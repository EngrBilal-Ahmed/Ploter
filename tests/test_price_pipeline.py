import csv
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from psx_stock_tool.config import AppConfig
from psx_stock_tool.data import get_stock_data, load_stock_list, save_stock_list
from psx_stock_tool.database import initialize_database
from psx_stock_tool.ingestion import (
    CSV_COLUMNS,
    IngestionSummary,
    cache_api_prices,
    import_historical_csv,
    normalize_price_data,
)
from src.sync_psxdata import sync_psxdata


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SCHEMA = """
    CREATE TABLE stock_prices (
        symbol TEXT NOT NULL, date TEXT NOT NULL, open REAL, high REAL, low REAL,
        close REAL, volume REAL, fetched_at TEXT NOT NULL,
        PRIMARY KEY (symbol, date)
    )
"""


def historical_row(day="2025-01-02", symbol="ABC", close="11", **overrides):
    row = {
        "DATE": day, "SYMBOL": symbol, "LDCP": "10", "OPEN": "10", "HIGH": "15",
        "LOW": "9", "CLOSE": close, "CHANGE": "", "CHANGE (%)": "", "VOLUME": "300",
    }
    row.update(overrides)
    return row


def candles(*rows):
    return pd.DataFrame([
        {
            "date": day, "open": close, "high": close + 1,
            "low": close - 1, "close": close, "volume": 100,
        }
        for day, close in rows
    ])


class DatabaseTests(unittest.TestCase):
    def test_legacy_migration_preserves_prices_settings_and_timestamps(self):
        with closing(sqlite3.connect(":memory:")) as connection, connection:
            connection.execute(LEGACY_SCHEMA)
            connection.executemany(
                "INSERT INTO stock_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("ABC", "2025-01-02", 10, 11, 9, 10, 100, "original-time"),
                    ("ABC", "2025-01-03", 11, 12, 10, 11, 200, "original-time"),
                ],
            )
            connection.execute(
                "CREATE TABLE app_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL)"
            )
            connection.execute("INSERT INTO app_settings VALUES ('stock_list', 'ABC\\nSYS')")
            initialize_database(connection)
            first = connection.execute(
                "SELECT symbol, date, open, high, low, close, volume, fetched_at "
                "FROM stock_prices ORDER BY date LIMIT 1"
            ).fetchone()
            self.assertEqual(
                first, ("ABC", "2025-01-02", 10, 11, 9, 10, 100, "original-time")
            )
            self.assertEqual(connection.execute(
                "SELECT ldcp, change, change_pct, source FROM stock_prices "
                "WHERE date = '2025-01-03'"
            ).fetchone(), (10, 1, 10, "psxdata_api"))
            self.assertEqual(connection.execute(
                "SELECT setting_value FROM app_settings"
            ).fetchone()[0], "ABC\\nSYS")
            self.assertEqual(connection.execute(
                "SELECT COUNT(DISTINCT id) FROM stock_prices"
            ).fetchone()[0], 2)
            self.assertEqual(dict(
                (row[1], row[2]) for row in connection.execute("PRAGMA table_info(stock_prices)")
            )["volume"], "BIGINT")
            before = connection.execute("SELECT * FROM stock_prices ORDER BY id").fetchall()
            initialize_database(connection)
            self.assertEqual(
                connection.execute("SELECT * FROM stock_prices ORDER BY id").fetchall(), before
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO stock_prices "
                    "(symbol, date, source, updated_at, fetched_at) VALUES "
                    "('ABC', '2025-01-02', 'csv_historical', 'now', 'now')"
                )

    def test_failed_migration_rolls_back_ddl_and_rows(self):
        with closing(sqlite3.connect(":memory:")) as connection, connection:
            connection.execute(LEGACY_SCHEMA.replace(",\n        PRIMARY KEY (symbol, date)", ""))
            row = ("ABC", "2025-01-02", 10, 11, 9, 10, 100, "original-time")
            connection.executemany("INSERT INTO stock_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [row, row])
            with self.assertRaises(sqlite3.IntegrityError):
                initialize_database(connection)
            self.assertEqual(connection.execute("SELECT * FROM stock_prices").fetchall(), [row, row])
            self.assertNotIn(
                "id", [row[1] for row in connection.execute("PRAGMA table_info(stock_prices)")]
            )
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'stock_prices_migrating'"
            ).fetchone())

    def test_unknown_legacy_columns_are_not_discarded(self):
        with closing(sqlite3.connect(":memory:")) as connection, connection:
            connection.execute(LEGACY_SCHEMA)
            connection.execute("ALTER TABLE stock_prices ADD COLUMN custom_note TEXT")
            with self.assertRaisesRegex(sqlite3.DatabaseError, "Unrecognized"):
                initialize_database(connection)
            self.assertIn(
                "custom_note", [row[1] for row in connection.execute("PRAGMA table_info(stock_prices)")]
            )


class NormalizationTests(unittest.TestCase):
    def test_exact_example_mapping(self):
        frame = pd.DataFrame([{
            "DATE": "2017-01-02", "SYMBOL": " aabs ", "LDCP": "329.0",
            "OPEN": "338.88", "HIGH": "338.88", "LOW": "331.0", "CLOSE": "331.0",
            "CHANGE": "2.0", "CHANGE (%)": "0.60790273556231", "VOLUME": "300",
        }])
        row = normalize_price_data(frame, require_volume=True).iloc[0]
        self.assertEqual((row["symbol"], row["date"], row["volume"]), ("AABS", "2017-01-02", 300))
        self.assertEqual((row["ldcp"], row["open"], row["change"]), (329, 338.88, 2))
        self.assertAlmostEqual(row["change_pct"], 0.60790273556231)

    def test_numeric_commas_bigints_and_missing_derived_values(self):
        frame = pd.DataFrame([
            historical_row(
                OPEN="1, 000", HIGH="1,200", LOW="900", CLOSE="1,100", LDCP=" 1,000 ",
                VOLUME="9,223,372,036,854,775,807",
            ),
            historical_row(day="2025-01-03", VOLUME=""),
        ])
        cleaned = normalize_price_data(frame, require_volume=True)
        self.assertEqual(len(cleaned), 1)
        row = cleaned.iloc[0]
        self.assertEqual(row["volume"], 9223372036854775807)
        self.assertEqual((row["change"], row["change_pct"]), (100, 10))

    def test_zero_and_missing_ldcp_do_not_produce_infinite_percentages(self):
        frame = pd.DataFrame([
            historical_row(LDCP="0"),
            historical_row(day="2025-01-03", LDCP=""),
        ])
        cleaned = normalize_price_data(frame)
        self.assertEqual(cleaned.iloc[0]["change"], 11)
        self.assertTrue(cleaned["change_pct"].isna().all())
        self.assertTrue(pd.isna(cleaned.iloc[1]["change"]))

    def test_malformed_rows_and_last_valid_duplicate(self):
        rows = [
            historical_row(close="12"),
            historical_row(close="13"),
            historical_row(close="100"),
            historical_row(day="bad-date"),
            historical_row(symbol=" "),
            historical_row(day="2025-01-03", OPEN="bad"),
            historical_row(day="2025-01-04", HIGH="inf"),
        ]
        cleaned = normalize_price_data(pd.DataFrame(rows), require_volume=True)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned.iloc[0]["close"], 13)

    def test_invalid_volumes_are_rejected_without_float_rounding(self):
        values = ("-1", "3.5", "9223372036854775808", "infinity", "invalid", "300.0", "3e2")
        rows = [
            historical_row(day=f"2025-01-{index + 1:02d}", VOLUME=value)
            for index, value in enumerate(values)
        ]
        cleaned = normalize_price_data(pd.DataFrame(rows), require_volume=True)
        self.assertEqual(cleaned["volume"].tolist(), [300, 300])

    def test_api_missing_volume_is_retained_for_plotter_compatibility(self):
        frame = candles(("2025-01-02", 10)).drop(columns="volume")
        cleaned = normalize_price_data(frame, "abc")
        self.assertEqual(len(cleaned), 1)
        self.assertIsNone(cleaned.iloc[0]["volume"])

    def test_missing_required_columns_raise(self):
        with self.assertRaisesRegex(ValueError, "Missing price columns"):
            normalize_price_data(pd.DataFrame([{"close": 10}]))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / "prices.db")
        self.csv_file = Path(self.directory.name) / "history.csv"

    def write_csv(self, rows, columns=CSV_COLUMNS):
        with self.csv_file.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def query(self, sql, parameters=()):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            return connection.execute(sql, parameters).fetchall()

    def test_chunked_import_globally_deduplicates_and_upserts(self):
        self.write_csv([
            historical_row(),
            historical_row(day="2025-01-03", close="12"),
            historical_row(symbol=" abc ", close="13"),
            historical_row(day="bad"),
            historical_row(day="2025-01-04", VOLUME="9223372036854775807"),
        ])
        summary = import_historical_csv(self.csv_file, self.database, chunksize=2)
        self.assertEqual(summary, IngestionSummary(total_rows=5, inserted=3, skipped=2))
        self.assertEqual(self.query(
            "SELECT close, source FROM stock_prices WHERE date = '2025-01-02'"
        ), [(13, "csv_historical")])
        self.assertEqual(self.query(
            "SELECT volume, typeof(volume) FROM stock_prices WHERE date = '2025-01-04'"
        ), [(9223372036854775807, "integer")])
        ids = self.query("SELECT id FROM stock_prices ORDER BY date")
        second = import_historical_csv(self.csv_file, self.database, chunksize=1)
        self.assertEqual(second, IngestionSummary(total_rows=5, updated=3, skipped=2))
        self.assertEqual(ids, self.query("SELECT id FROM stock_prices ORDER BY date"))
        self.assertTrue(all(row[0] for row in self.query("SELECT updated_at FROM stock_prices")))

    def test_header_only_csv_and_bad_headers(self):
        self.write_csv([])
        self.assertEqual(import_historical_csv(self.csv_file, self.database), IngestionSummary())
        self.write_csv([], columns=("DATE", "SYMBOL"))
        with self.assertRaisesRegex(ValueError, "Missing CSV columns"):
            import_historical_csv(self.csv_file, self.database)
        self.assertEqual(self.query("SELECT COUNT(*) FROM stock_prices"), [(0,)])

    def test_default_chunk_size_across_fifty_thousand_rows(self):
        first_day = date(2017, 1, 2)
        rows = [
            historical_row(
                day=(first_day + timedelta(days=index // 100)).isoformat(),
                symbol=f"PSX{index % 100}",
            )
            for index in range(50001)
        ]
        rows.extend([
            historical_row(day=first_day.isoformat(), symbol="PSX0", close="14"),
            historical_row(day="malformed"),
        ])
        self.write_csv(rows)
        with patch("psx_stock_tool.ingestion.pd.read_csv", wraps=pd.read_csv) as read_csv:
            result = import_historical_csv(self.csv_file, self.database)
        self.assertEqual(read_csv.call_args.kwargs["chunksize"], 50000)
        self.assertEqual(result, IngestionSummary(total_rows=50003, inserted=50001, skipped=2))
        self.assertEqual(self.query(
            "SELECT close FROM stock_prices WHERE symbol = 'PSX0' AND date = '2017-01-02'"
        ), [(14,)])

    def test_structural_csv_error_does_not_partially_import(self):
        self.write_csv([historical_row()])
        import_historical_csv(self.csv_file, self.database)
        original = self.query("SELECT * FROM stock_prices")
        valid = "2025-02-02,XYZ,10,10,15,9,11,,,300\n"
        invalid = "2025-02-03,XYZ,10,10,15,9,11,,,300,extra\n"
        for rows in (valid + invalid, invalid + valid):
            self.csv_file.write_text(",".join(CSV_COLUMNS) + "\n" + rows, encoding="utf-8")
            for size in (1, 10):
                with self.subTest(first_row_invalid=rows.startswith(invalid), chunksize=size):
                    with self.assertRaises((ValueError, pd.errors.ParserError)):
                        import_historical_csv(self.csv_file, self.database, chunksize=size)
                    self.assertEqual(self.query("SELECT * FROM stock_prices"), original)

    def test_sync_starts_at_each_latest_date_and_uses_previous_trading_close(self):
        self.write_csv([
            historical_row(day="2025-01-02", close="10"),
            historical_row(day="2025-01-03", close="11"),
            historical_row(day="2025-01-02", symbol="XYZ", close="12"),
        ])
        import_historical_csv(self.csv_file, self.database)

        def provider(symbol, **kwargs):
            if symbol == "ABC":
                return candles(("2025-01-06", 12), ("2025-01-03", 11), ("2025-01-07", 13))
            return candles(("2025-01-02", 12), ("2025-01-06", 15))

        with patch("src.sync_psxdata.psxdata.stocks", side_effect=provider) as fetch:
            summary = sync_psxdata(self.database, end_date=date(2025, 1, 7))
        self.assertEqual(summary, IngestionSummary(total_rows=5, inserted=3, updated=2))
        self.assertEqual(fetch.call_args_list[0].kwargs["start"], date(2025, 1, 3))
        self.assertEqual(fetch.call_args_list[1].kwargs["start"], date(2025, 1, 2))
        self.assertFalse(fetch.call_args_list[0].kwargs["cache"])
        self.assertEqual(self.query(
            "SELECT ldcp, change, change_pct, source FROM stock_prices "
            "WHERE symbol = 'ABC' AND date = '2025-01-06'"
        ), [(11, 1, (1 / 11) * 100, "psxdata_api")])
        self.assertEqual(self.query(
            "SELECT ldcp, change FROM stock_prices WHERE symbol = 'ABC' AND date = '2025-01-07'"
        ), [(12, 1)])

    def test_sync_uses_today_as_default_and_is_repeatable(self):
        self.write_csv([historical_row(day=date.today().isoformat())])
        import_historical_csv(self.csv_file, self.database)
        with patch("src.sync_psxdata.psxdata.stocks", return_value=candles((date.today(), 12))) as fetch:
            for _ in range(2):
                result = sync_psxdata(self.database)
                self.assertEqual(result, IngestionSummary(total_rows=1, updated=1))
            self.assertEqual(fetch.call_args.kwargs["end"], date.today())
        self.assertEqual(self.query("SELECT COUNT(*) FROM stock_prices"), [(1,)])

    def test_new_symbol_empty_history_and_optional_start(self):
        with patch("src.sync_psxdata.psxdata.stocks", return_value=candles(("2025-01-03", 10))) as fetch:
            result = sync_psxdata(self.database, [" abc ", "ABC"], end_date=date(2025, 1, 3))
            self.assertEqual(fetch.call_count, 1)
            self.assertIsNone(fetch.call_args.kwargs["start"])
        self.assertEqual(result.inserted, 1)
        self.assertEqual(self.query("SELECT ldcp, change, change_pct FROM stock_prices"), [(None, None, None)])
        with patch("src.sync_psxdata.psxdata.stocks", return_value=pd.DataFrame()) as fetch:
            result = sync_psxdata(
                self.database, ["XYZ"], start_date=date(2025, 1, 1), end_date=date(2025, 1, 3)
            )
            self.assertEqual(result, IngestionSummary())
            self.assertEqual(fetch.call_args.kwargs["start"], date(2025, 1, 1))

    def test_sync_without_symbols_or_invalid_date_range_raises(self):
        with self.assertRaisesRegex(ValueError, "No symbols"):
            sync_psxdata(self.database)
        with self.assertRaisesRegex(ValueError, "start_date"):
            sync_psxdata(
                self.database, ["ABC"], start_date=date(2025, 2, 1), end_date=date(2025, 1, 1)
            )

    def test_sync_failure_propagates_and_keeps_existing_data(self):
        self.write_csv([historical_row()])
        import_historical_csv(self.csv_file, self.database)
        original = self.query("SELECT * FROM stock_prices")
        with patch("src.sync_psxdata.psxdata.stocks", side_effect=RuntimeError("provider unavailable")):
            with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                sync_psxdata(self.database, end_date=date(2025, 1, 3))
        self.assertEqual(self.query("SELECT * FROM stock_prices"), original)

    def test_api_corrections_and_csv_backfill_repair_next_api_ldcp(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            initialize_database(connection)
            cache_api_prices(
                connection, candles(("2025-01-02", 10), ("2025-01-06", 12)),
                "ABC", date(2025, 1, 2), date(2025, 1, 6),
            )
        self.write_csv([historical_row(day="2025-01-03", close="11", CHANGE="99", **{"CHANGE (%)": "88"})])
        import_historical_csv(self.csv_file, self.database)
        self.assertEqual(self.query(
            "SELECT ldcp, change FROM stock_prices WHERE date = '2025-01-06'"
        ), [(11, 1)])
        self.assertEqual(self.query(
            "SELECT change, change_pct FROM stock_prices WHERE date = '2025-01-03'"
        ), [(99, 88)])

    def test_api_filters_out_of_range_and_invalid_candles(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            initialize_database(connection)
            frame = candles(("2025-01-01", 10), ("2025-01-02", 10), ("2025-01-03", 11))
            frame.loc[2, "high"] = 1
            result = cache_api_prices(connection, frame, "ABC", date(2025, 1, 2), date(2025, 1, 3))
        self.assertEqual(result, IngestionSummary(total_rows=3, inserted=1, skipped=2))

    def test_zero_previous_close_is_not_a_division_error(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            initialize_database(connection)
            cache_api_prices(
                connection, candles(("2025-01-02", 0), ("2025-01-03", 10)),
                "ABC", date(2025, 1, 2), date(2025, 1, 3),
            )
        self.assertEqual(self.query(
            "SELECT ldcp, change, change_pct FROM stock_prices WHERE date = '2025-01-03'"
        ), [(0, 10, None)])

    def test_existing_plotter_reads_history_and_refreshes_same_schema(self):
        today = date.today()
        self.write_csv([
            historical_row(day=(today - timedelta(days=2)).isoformat(), close="10"),
            historical_row(day=(today - timedelta(days=1)).isoformat(), close="11"),
        ])
        import_historical_csv(self.csv_file, self.database)
        config = AppConfig(database_file=self.database, total_days=2, refresh_days=1)
        with patch("psx_stock_tool.data.psxdata.stocks", return_value=candles((today, 12))) as fetch:
            frame = get_stock_data(" abc ", config)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(list(frame.columns), ["date", "open", "high", "low", "close", "volume"])
        self.assertEqual(frame["close"].tolist(), [10, 11, 12])
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(frame["date"]))
        self.assertEqual(self.query(
            "SELECT ldcp, change, source FROM stock_prices WHERE date = ?", (today.isoformat(),)
        ), [(11, 1, "psxdata_api")])
        save_stock_list("ABC\nSYS", self.database)
        self.assertEqual(load_stock_list(self.database), "ABC\nSYS")

    def test_plotter_keeps_cached_fallback_on_network_failure(self):
        today = date.today().isoformat()
        self.write_csv([historical_row(day=today)])
        import_historical_csv(self.csv_file, self.database)
        with patch("psx_stock_tool.data.psxdata.stocks", side_effect=RuntimeError("offline")):
            frame = get_stock_data("ABC", AppConfig(database_file=self.database, total_days=1))
        self.assertEqual(frame["close"].tolist(), [11])

    def test_plotter_still_renders_selected_prices_and_intraday_range(self):
        import matplotlib.pyplot as plt
        from psx_stock_tool.plotting import plot_stock

        frame = candles(("2025-01-02", 10), ("2025-01-03", 11))
        frame["date"] = pd.to_datetime(frame["date"])
        config = AppConfig(price_graphs="LH", total_days=2)
        try:
            with patch("psx_stock_tool.plotting.get_stock_data", return_value=frame):
                plot_stock("ABC", config)
            figure = plt.gcf()
            self.assertEqual(len(figure.axes), 2)
            self.assertEqual([line.get_label() for line in figure.axes[0].lines], ["Low", "High"])
            self.assertEqual(len(figure.axes[1].patches), 2)
            figure.canvas.draw()
        finally:
            plt.close("all")

    def test_import_cli_and_sync_help(self):
        self.write_csv([historical_row()])
        imported = subprocess.run(
            [
                sys.executable, str(ROOT / "scripts" / "import_historical_csv.py"),
                "--file", str(self.csv_file), "--database", self.database, "--chunksize", "1",
            ],
            cwd=self.directory.name, capture_output=True, text=True,
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertIn("inserted: 1", imported.stdout)
        help_result = subprocess.run(
            [sys.executable, str(ROOT / "src" / "sync_psxdata.py"), "--help"],
            cwd=self.directory.name, capture_output=True, text=True,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--symbols", help_result.stdout)


if __name__ == "__main__":
    unittest.main()
