"""Eligible-only, user-triggered synchronization for the surveillance dashboard."""

from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, timedelta
import logging
import sqlite3
import threading

import pandas as pd
import psxdata

from .database import initialize_database
from .eligibility import DEFAULT_ELIGIBLE_FILE, load_eligible_stocks, register_eligible_stocks
from .ingestion import IngestionSummary, cache_api_prices


_SYNC_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


@dataclass
class DashboardSyncResult:
    prices: IngestionSummary = field(default_factory=IngestionSummary)
    requested: int = 0
    unchanged: int = 0
    failures: dict[str, str] = field(default_factory=dict)


def plan_fetch_start(stored_dates, market_dates, checked_ranges, start, end, refresh_days):
    """Find the earliest unchecked hole or forced refresh; one request per symbol."""
    if start > end or refresh_days < 0:
        raise ValueError("Invalid history range or negative refresh days")
    stored = set(stored_dates)
    missing = [
        day for day in sorted(market_dates)
        if start <= day <= end and day not in stored
        and not any(first <= day <= last for first, last in checked_ranges)
    ]
    earliest = missing[0] if missing else None
    if refresh_days:
        refresh_start = max(start, end - timedelta(days=refresh_days - 1))
        earliest = min(earliest, refresh_start) if earliest else refresh_start
    return earliest


def sync_eligible_prices(
    database_file="stock_data.db", eligible_file=DEFAULT_ELIGIBLE_FILE, *,
    history_start=date(2017, 1, 1), end_date=None, refresh_days=0, progress=None,
):
    """Fetch only eligible symbols; keep successful range checks and report failures."""
    end_date = date.today() if end_date is None else end_date
    if history_start > end_date or refresh_days < 0:
        raise ValueError("Invalid history range or negative refresh days")
    stocks = load_eligible_stocks(eligible_file)
    if not _SYNC_LOCK.acquire(blocking=False):
        raise RuntimeError("A dashboard synchronization is already running")
    result = DashboardSyncResult()
    try:
        with closing(sqlite3.connect(database_file, timeout=30)) as connection:
            initialize_database(connection)
            register_eligible_stocks(connection, stocks)
            connection.execute(
                """CREATE TABLE IF NOT EXISTS dashboard_sync_ranges (
                    symbol TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (symbol, start_date, end_date)
                )"""
            )
            connection.commit()
            market_dates = {
                date.fromisoformat(row[0]) for row in connection.execute(
                    """SELECT DISTINCT date FROM stock_prices
                       JOIN eligible_symbols USING (symbol) WHERE date BETWEEN ? AND ?""",
                    (history_start.isoformat(), end_date.isoformat()),
                )
            }
            # Existing market dates distinguish historical sessions from holidays.
            # Beyond the known market history, weekdays are only request candidates.
            tail_start = max(market_dates) + timedelta(days=1) if market_dates else history_start
            market_dates.update(day.date() for day in pd.bdate_range(tail_start, end_date))
            for index, symbol in enumerate(stocks, 1):
                if progress:
                    progress(f"{index}/{len(stocks)}: checking {symbol}")
                stored = [
                    date.fromisoformat(row[0]) for row in connection.execute(
                        "SELECT date FROM stock_prices WHERE symbol = ?", (symbol,)
                    )
                ]
                checked = [
                    (date.fromisoformat(a), date.fromisoformat(b))
                    for a, b in connection.execute(
                        "SELECT start_date, end_date FROM dashboard_sync_ranges WHERE symbol = ?",
                        (symbol,),
                    )
                ]
                first = plan_fetch_start(
                    stored, market_dates, checked, history_start, end_date, refresh_days
                )
                if first is None:
                    result.unchanged += 1
                    continue
                result.requested += 1
                if progress:
                    progress(f"{index}/{len(stocks)}: {symbol}, {first} through {end_date}")
                try:
                    downloaded = psxdata.stocks(symbol, start=first, end=end_date, cache=False)
                    with connection:
                        summary = cache_api_prices(connection, downloaded, symbol, first, end_date)
                        # A successful request is not proof every session had a trade.
                        # Keep the check so suspended/pre-listing periods are not retried forever.
                        if summary.skipped == 0:
                            connection.execute(
                                """INSERT INTO dashboard_sync_ranges (symbol, start_date, end_date)
                                   VALUES (?, ?, ?)
                                   ON CONFLICT(symbol, start_date, end_date)
                                   DO UPDATE SET checked_at = CURRENT_TIMESTAMP""",
                                (symbol, first.isoformat(), end_date.isoformat()),
                            )
                    result.prices.total_rows += summary.total_rows
                    result.prices.inserted += summary.inserted
                    result.prices.updated += summary.updated
                    result.prices.skipped += summary.skipped
                except Exception as error:
                    logger.exception("Dashboard synchronization failed for %s", symbol)
                    result.failures[symbol] = str(error)
    finally:
        _SYNC_LOCK.release()
    return result
