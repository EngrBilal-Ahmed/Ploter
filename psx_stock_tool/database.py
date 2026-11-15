"""SQLite schema migration and bulk storage shared by all price writers."""

from datetime import datetime, timezone
import sqlite3


PRICE_COLUMNS = (
    "symbol", "date", "ldcp", "open", "high", "low", "close",
    "change", "change_pct", "volume",
)
STORAGE_COLUMNS = (*PRICE_COLUMNS, "source", "updated_at", "fetched_at")
_COLUMN_NAMES = ", ".join(STORAGE_COLUMNS)
_PRICE_SCHEMA = """
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    ldcp REAL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    change REAL,
    change_pct REAL,
    volume BIGINT,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL
"""


def _refresh_api_changes(connection, *, staged_only=True):
    """Recalculate API candles against the final, merged trading history."""
    affected_symbols = (
        "WHERE symbol IN (SELECT symbol FROM incoming_stock_prices)"
        if staged_only else ""
    )
    connection.execute(
        f"""
        WITH previous_prices AS (
            SELECT id, close,
                   LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS ldcp
            FROM stock_prices
            {affected_symbols}
        ), changes AS (
            SELECT id, ldcp, close - ldcp AS change,
                   ((close - ldcp) / NULLIF(ldcp, 0)) * 100 AS change_pct
            FROM previous_prices
        )
        UPDATE stock_prices
        SET ldcp = changes.ldcp,
            change = changes.change,
            change_pct = changes.change_pct,
            updated_at = ?
        FROM changes
        WHERE stock_prices.id = changes.id
          AND stock_prices.source = 'psxdata_api'
          AND (stock_prices.ldcp IS NOT changes.ldcp
               OR stock_prices.change IS NOT changes.change
               OR stock_prices.change_pct IS NOT changes.change_pct)
        """,
        (datetime.now(timezone.utc).isoformat(),),
    )


def initialize_database(connection):
    """Atomically upgrade the original cache without discarding prices/settings."""
    connection.execute("SAVEPOINT initialize_prices")
    try:
        columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(stock_prices)")
        }
        expected = {"id", *STORAGE_COLUMNS}
        if not columns:
            connection.execute(f"CREATE TABLE stock_prices ({_PRICE_SCHEMA})")
        elif (
            not expected.issubset(columns)
            or columns["id"][5] != 1
            or columns["volume"][2].upper() not in ("INTEGER", "BIGINT")
        ):
            required = {"symbol", "date", "open", "high", "low", "close", "volume"}
            if not required.issubset(columns) or set(columns) - expected:
                raise sqlite3.DatabaseError(
                    "Unrecognized stock_prices schema; migration stopped without "
                    "discarding existing columns."
                )
            schema_objects = connection.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE tbl_name = 'stock_prices' AND type IN ('index', 'trigger')
                  AND sql IS NOT NULL
                """
            ).fetchall()
            connection.execute(f"CREATE TABLE stock_prices_migrating ({_PRICE_SCHEMA})")
            expressions = []
            for column in ("id", *STORAGE_COLUMNS):
                if column in columns:
                    expressions.append(column)
                elif column == "id":
                    expressions.append("rowid")
                elif column == "source":
                    expressions.append("'psxdata_api'")
                elif column in ("updated_at", "fetched_at"):
                    timestamp = next(
                        (name for name in ("fetched_at", "updated_at") if name in columns),
                        "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
                    )
                    expressions.append(timestamp)
                else:
                    expressions.append("NULL")
            connection.execute(
                f"INSERT INTO stock_prices_migrating (id, {_COLUMN_NAMES}) "
                f"SELECT {', '.join(expressions)} FROM stock_prices"
            )
            connection.execute("DROP TABLE stock_prices")
            connection.execute("ALTER TABLE stock_prices_migrating RENAME TO stock_prices")
            for (sql,) in schema_objects:
                connection.execute(sql)
            _refresh_api_changes(connection, staged_only=False)
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_prices_symbol_date
            ON stock_prices (symbol, date)
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
    except BaseException:
        connection.execute("ROLLBACK TO initialize_prices")
        connection.execute("RELEASE initialize_prices")
        raise
    connection.execute("RELEASE initialize_prices")


def stage_price_rows(connection, records, source):
    """Stage rows on disk; the last occurrence of a symbol/date wins."""
    connection.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS incoming_stock_prices (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            ldcp REAL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            change REAL,
            change_pct REAL,
            volume BIGINT,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (symbol, date)
        )
        """
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    assignments = ", ".join(
        f"{column} = excluded.{column}" for column in STORAGE_COLUMNS[2:]
    )
    placeholders = ", ".join("?" for _ in STORAGE_COLUMNS)
    connection.executemany(
        f"""
        INSERT INTO incoming_stock_prices ({_COLUMN_NAMES})
        VALUES ({placeholders})
        ON CONFLICT(symbol, date) DO UPDATE SET {assignments}
        """,
        ((*record, source, timestamp, timestamp) for record in records),
    )


def upsert_staged_prices(connection):
    """Merge a staged import atomically and return unique inserted/updated counts."""
    total = connection.execute("SELECT COUNT(*) FROM incoming_stock_prices").fetchone()[0]
    updated = connection.execute(
        """
        SELECT COUNT(*) FROM incoming_stock_prices incoming
        JOIN stock_prices existing USING (symbol, date)
        """
    ).fetchone()[0]
    assignments = ", ".join(
        f"{column} = excluded.{column}" for column in STORAGE_COLUMNS[2:]
    )
    # WHERE disambiguates SQLite's INSERT ... SELECT ... ON CONFLICT grammar.
    connection.execute(
        f"""
        INSERT INTO stock_prices ({_COLUMN_NAMES})
        SELECT {_COLUMN_NAMES} FROM incoming_stock_prices WHERE 1
        ON CONFLICT(symbol, date) DO UPDATE SET {assignments}
        """
    )
    _refresh_api_changes(connection)
    connection.execute("DELETE FROM incoming_stock_prices")
    return total - updated, updated
