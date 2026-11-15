"""Normalization and chunked historical imports for the shared price cache."""

from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import sqlite3

import pandas as pd

from .database import (
    PRICE_COLUMNS,
    initialize_database,
    stage_price_rows,
    upsert_staged_prices,
)


CSV_COLUMNS = (
    "DATE", "SYMBOL", "LDCP", "OPEN", "HIGH", "LOW", "CLOSE",
    "CHANGE", "CHANGE (%)", "VOLUME",
)
CSV_MAPPING = dict(zip(CSV_COLUMNS, (
    "date", "symbol", "ldcp", "open", "high", "low", "close",
    "change", "change_pct", "volume",
)))
_MISSING_VALUES = ("", "NA", "N/A", "NAN", "NONE", "NULL", "-", "--")


@dataclass
class IngestionSummary:
    total_rows: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0

    def __str__(self):
        return (
            f"Total rows processed: {self.total_rows}; inserted: {self.inserted}; "
            f"updated: {self.updated}; skipped: {self.skipped}"
        )


def _numeric_text(values):
    return values.astype("string").str.replace(r"[,\s]", "", regex=True)


def _volume(value):
    if pd.isna(value):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    if (
        not number.is_finite()
        or number < 0
        or number > 9223372036854775807
        or number != number.to_integral_value()
    ):
        return None
    return int(number)


def normalize_price_data(frame, symbol=None, *, require_volume=False):
    """Normalize CSV/API prices; reject invalid dates, OHLC, and volume values."""
    if frame.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)

    frame = frame.rename(columns=CSV_MAPPING).copy()
    if symbol is not None:
        frame["symbol"] = symbol
    required = {"symbol", "date", "open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing price columns: {', '.join(sorted(missing))}")

    frame["symbol"] = frame["symbol"].astype("string").str.strip().str.upper()
    frame["date"] = pd.to_datetime(
        frame["date"], format="mixed", errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    for column in ("ldcp", "open", "high", "low", "close", "change", "change_pct"):
        values = frame[column] if column in frame else pd.Series(pd.NA, index=frame.index)
        numbers = pd.to_numeric(_numeric_text(values), errors="coerce").astype(float)
        frame[column] = numbers.mask(numbers.isin([float("inf"), float("-inf")]))

    volumes = frame["volume"] if "volume" in frame else pd.Series(pd.NA, index=frame.index)
    volume_text = _numeric_text(volumes)
    missing_volume = volume_text.isna() | volume_text.str.upper().isin(_MISSING_VALUES)
    # Construct as object to avoid rounding BIGINT values through float/NaN.
    frame["volume"] = pd.Series(
        [_volume(value) for value in volume_text], index=frame.index, dtype=object
    )
    valid_volume = frame["volume"].notna()
    if not require_volume:
        valid_volume |= missing_volume

    valid = (
        frame["symbol"].notna()
        & frame["symbol"].ne("")
        & frame["date"].notna()
        & (frame["high"] >= frame["low"])
        & frame["open"].between(frame["low"], frame["high"])
        & frame["close"].between(frame["low"], frame["high"])
        & valid_volume
    )
    frame = frame.loc[valid].copy()
    difference = frame["close"] - frame["ldcp"]
    frame["change"] = frame["change"].fillna(difference)
    percentage = (difference / frame["ldcp"].where(frame["ldcp"].ne(0))) * 100
    frame["change_pct"] = frame["change_pct"].fillna(percentage)
    return (
        frame.loc[:, list(PRICE_COLUMNS)]
        .drop_duplicates(["symbol", "date"], keep="last")
        .sort_values(["symbol", "date"], kind="stable")
    )


def stage_price_frame(connection, frame, source):
    values = frame.astype(object).where(pd.notna(frame), None)
    stage_price_rows(
        connection,
        values.loc[:, list(PRICE_COLUMNS)].itertuples(index=False, name=None),
        source,
    )


def cache_api_prices(connection, downloaded, symbol, start_date, end_date):
    """Persist only valid, requested candles; calculate changes in the database."""
    cleaned = normalize_price_data(downloaded, symbol)
    cleaned = cleaned.loc[
        cleaned["date"].between(start_date.isoformat(), end_date.isoformat())
    ]
    summary = IngestionSummary(total_rows=len(downloaded))
    stage_price_frame(connection, cleaned, "psxdata_api")
    summary.inserted, summary.updated = upsert_staged_prices(connection)
    summary.skipped = summary.total_rows - summary.inserted - summary.updated
    return summary


def import_historical_csv(file, database_file="stock_data.db", *, chunksize=50000):
    """Stage CSV chunks and commit one globally deduplicated, all-or-nothing import."""
    if chunksize < 1:
        raise ValueError("chunksize must be at least 1")
    summary = IngestionSummary()
    with closing(sqlite3.connect(database_file)) as connection:
        initialize_database(connection)
        connection.execute("PRAGMA temp_store = FILE")
        with connection:
            with pd.read_csv(
                file, chunksize=chunksize, dtype="string", keep_default_na=False,
                # The C parser can truncate extra fields at a chunk boundary.
                engine="python",
            ) as chunks:
                for chunk in chunks:
                    if not isinstance(chunk.index, pd.RangeIndex):
                        raise ValueError("Malformed CSV: rows contain more fields than the header")
                    missing = set(CSV_COLUMNS) - set(chunk.columns)
                    if missing:
                        raise ValueError(f"Missing CSV columns: {', '.join(sorted(missing))}")
                    summary.total_rows += len(chunk)
                    cleaned = normalize_price_data(chunk, require_volume=True)
                    stage_price_frame(connection, cleaned, "csv_historical")
            summary.inserted, summary.updated = upsert_staged_prices(connection)
    summary.skipped = summary.total_rows - summary.inserted - summary.updated
    return summary
