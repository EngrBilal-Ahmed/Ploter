"""Incrementally synchronize PSX candles into the plotter's SQLite cache."""

import argparse
from contextlib import closing
from datetime import date
from pathlib import Path
import sqlite3
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psxdata

from psx_stock_tool.config import AppConfig
from psx_stock_tool.database import initialize_database
from psx_stock_tool.ingestion import IngestionSummary, cache_api_prices


def sync_psxdata(database_file="stock_data.db", symbols=None, *, start_date=None, end_date=None):
    """Sync cached/explicit symbols; raise on failure so callers can stop analysis."""
    end_date = date.today() if end_date is None else end_date
    if start_date is not None and start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    summary = IngestionSummary()
    with closing(sqlite3.connect(database_file)) as connection:
        initialize_database(connection)
        latest_dates = dict(connection.execute(
            "SELECT symbol, MAX(date) FROM stock_prices GROUP BY symbol"
        ))
        selected = latest_dates if symbols is None else symbols
        selected = list(dict.fromkeys(symbol.strip().upper() for symbol in selected))
        if not selected or any(not symbol for symbol in selected):
            raise ValueError("No symbols to synchronize; import CSV data or supply --symbols")
        for symbol in selected:
            latest = latest_dates.get(symbol)
            # Refresh the latest candle as well, including a possibly incomplete session.
            fetch_start = date.fromisoformat(latest) if latest is not None else start_date
            if start_date is not None and fetch_start is not None:
                fetch_start = max(start_date, fetch_start)
            if fetch_start is not None and fetch_start > end_date:
                continue
            print(f"{symbol}: fetching {fetch_start or 'earliest available'} through {end_date}")
            downloaded = psxdata.stocks(
                symbol, start=fetch_start, end=end_date, cache=False
            )
            with connection:
                result = cache_api_prices(
                    connection, downloaded, symbol, fetch_start or date.min, end_date
                )
            summary.total_rows += result.total_rows
            summary.inserted += result.inserted
            summary.updated += result.updated
            summary.skipped += result.skipped
            print(f"{symbol}: {result}")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=AppConfig().database_file, help="SQLite cache path")
    parser.add_argument("--symbols", nargs="+", help="Symbols (default: all cached symbols)")
    parser.add_argument("--start", type=date.fromisoformat, help="Initial history date (YYYY-MM-DD)")
    parser.add_argument("--end", type=date.fromisoformat, help="Last date (default: today)")
    args = parser.parse_args(argv)
    try:
        summary = sync_psxdata(
            args.database, args.symbols, start_date=args.start, end_date=args.end
        )
    except Exception as error:
        parser.exit(1, f"PSX synchronization failed: {error}\n")
    print(summary)


if __name__ == "__main__":
    main()
