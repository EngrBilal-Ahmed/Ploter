from datetime import date, timedelta
import sqlite3

import pandas as pd
import psxdata


def initialize_database(connection):
    """Create the local price cache when the script runs for the first time."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_prices (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (symbol, date)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        )
        """
    )


def load_stock_list(database_file):
    """Return the stock-list text saved by the GUI, if there is one."""
    with sqlite3.connect(database_file) as connection:
        initialize_database(connection)
        row = connection.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key = ?",
            ("stock_list",),
        ).fetchone()
    return row[0] if row else None


def save_stock_list(stock_list, database_file):
    """Save the raw GUI stock-list text without changing its pasted format."""
    with sqlite3.connect(database_file) as connection:
        initialize_database(connection)
        connection.execute(
            """
            INSERT INTO app_settings (setting_key, setting_value)
            VALUES (?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value
            """,
            ("stock_list", stock_list),
        )
        connection.commit()


def clean_price_data(df, stock_name):
    """Return valid, chronologically ordered OHLC data from the provider."""
    if df.empty:
        return df

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    ohlc_columns = ["open", "high", "low", "close"]
    df[ohlc_columns] = df[ohlc_columns].apply(pd.to_numeric, errors="coerce")
    if "volume" not in df:
        df["volume"] = pd.NA
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

    valid_ohlc = (
        (df["high"] >= df["low"])
        & df["open"].between(df["low"], df["high"])
        & df["close"].between(df["low"], df["high"])
    )
    invalid_rows = len(df) - valid_ohlc.sum()
    if invalid_rows:
        print(f"{stock_name}: skipped {invalid_rows} invalid OHLC row(s).")

    return df.loc[valid_ohlc].dropna(subset=["date", *ohlc_columns]).sort_values("date")


def download_and_cache(connection, stock_name, start_date, end_date):
    """Download a period and replace cached records for the same stock and date."""
    if start_date > end_date:
        return

    try:
        downloaded = psxdata.stocks(stock_name, start=start_date, end=end_date)
    except Exception as error:
        print(f"{stock_name}: download failed ({error}); using cached data.")
        return

    downloaded = clean_price_data(downloaded, stock_name)
    if downloaded.empty:
        return

    downloaded["date"] = downloaded["date"].dt.strftime("%Y-%m-%d")
    downloaded["fetched_at"] = pd.Timestamp.now().isoformat()
    downloaded = downloaded.astype(object).where(pd.notna(downloaded), None)
    records = downloaded[
        ["date", "open", "high", "low", "close", "volume", "fetched_at"]
    ].itertuples(index=False, name=None)
    connection.executemany(
        """
        INSERT INTO stock_prices (symbol, date, open, high, low, close, volume, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            fetched_at = excluded.fetched_at
        """,
        ((stock_name, *record) for record in records),
    )
    connection.commit()


def get_stock_data(stock_name, config):
    """Read cached data and download only missing history plus recent values."""
    end_date = date.today()
    start_date = end_date - timedelta(days=config.total_days)

    with sqlite3.connect(config.database_file) as connection:
        initialize_database(connection)
        first_cached = connection.execute(
            "SELECT MIN(date) FROM stock_prices WHERE symbol = ?", (stock_name,)
        ).fetchone()[0]

        if first_cached is None:
            download_and_cache(connection, stock_name, start_date, end_date)
        else:
            first_cached_date = date.fromisoformat(first_cached)
            if start_date < first_cached_date:
                download_and_cache(
                    connection,
                    stock_name,
                    start_date,
                    first_cached_date - timedelta(days=1),
                )

            refresh_start = max(start_date, end_date - timedelta(days=config.refresh_days))
            download_and_cache(connection, stock_name, refresh_start, end_date)

        return pd.read_sql_query(
            """
            SELECT date, open, high, low, close, volume
            FROM stock_prices
            WHERE symbol = ? AND date BETWEEN ? AND ?
            ORDER BY date
            """,
            connection,
            params=(stock_name, start_date.isoformat(), end_date.isoformat()),
            parse_dates=["date"],
        )
