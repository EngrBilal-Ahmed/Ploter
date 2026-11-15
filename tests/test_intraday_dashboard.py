from contextlib import closing
from datetime import date
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from psx_stock_tool.config import AppConfig
from psx_stock_tool.data import (
    calculate_intraday_ranges, get_stock_data, load_stock_list, save_stock_list,
)
from psx_stock_tool.database import initialize_database
from psx_stock_tool.ingestion import cache_api_prices
from psx_stock_tool.intraday import (
    intraday_chart_options, load_intraday_history, session_rows,
)


ROOT = Path(__file__).resolve().parents[1]


def prices():
    return pd.DataFrame({
        "date": pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"]),
        "open": [10.0, 11.0, 12.0], "high": [12.0, 13.0, 14.0],
        "low": [10.0, 10.0, 10.0], "close": [11.0, 10.0, 13.0],
        "volume": [100, None, 300],
    })


class IntradayDataTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / "prices.db")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            initialize_database(connection)
            cache_api_prices(
                connection, prices(), "ABC", date(2025, 1, 1), date(2025, 1, 31),
            )

    def test_formula_matches_original_without_mutating_prices(self):
        frame = prices()
        result = calculate_intraday_ranges(frame)
        self.assertEqual(result["daily_range"].tolist(), [2, 3, 4])
        self.assertEqual(result["intraday_range_percent"].tolist(), [20, 30, 40])
        self.assertEqual(result["gain_per_rupee"].tolist(), [0.2, 0.3, 0.4])
        self.assertNotIn("daily_range", frame)
        frame.loc[0, "low"] = 0
        self.assertTrue(pd.isna(calculate_intraday_ranges(frame).iloc[0]["intraday_range_percent"]))

    def test_cache_window_ends_at_last_stored_date_and_never_fetches(self):
        with patch("psx_stock_tool.data.psxdata.stocks") as fetch:
            frames = load_intraday_history(self.database, ["ABC", "MISSING"], 4)
            fetch.assert_not_called()
        self.assertEqual(list(frames), ["ABC"])
        self.assertEqual(
            frames["ABC"]["date"].dt.strftime("%Y-%m-%d").tolist(),
            ["2025-01-03", "2025-01-06"],
        )
        self.assertTrue(pd.isna(frames["ABC"].iloc[0]["volume"]))
        for days in (0, -1, 1.5):
            with self.assertRaises(ValueError):
                load_intraday_history(self.database, ["ABC"], days)

    def test_shared_axes_and_zoom_preserve_each_plot_and_json_safety(self):
        frame = calculate_intraday_ranges(prices())
        frame.loc[0, "intraday_range_percent"] = float("nan")
        options = intraday_chart_options(frame, [("low", "Low"), ("high", "High")])
        json.dumps(options, allow_nan=False)
        days = ["2025-01-02", "2025-01-03", "2025-01-06"]
        for index, axis in enumerate(options["xAxis"]):
            self.assertEqual(axis["data"], days)
            self.assertEqual(axis["gridIndex"], index)
            self.assertEqual(options["grid"][index]["left"], 80)
            self.assertEqual(options["grid"][index]["right"], 40)
        self.assertEqual(options["axisPointer"]["link"], [{"xAxisIndex": "all"}])
        self.assertEqual([zoom["type"] for zoom in options["dataZoom"]], ["inside", "slider"])
        for zoom in options["dataZoom"]:
            self.assertEqual(zoom["xAxisIndex"], [0, 1, 2])
            self.assertEqual((zoom["start"], zoom["end"]), (0, 100))
        low, high, ranges, volume = options["series"]
        self.assertEqual((low["name"], high["name"]), ("Low", "High"))
        self.assertEqual(high["markPoint"]["data"][1]["coord"], ["2025-01-06", 14])
        self.assertEqual(ranges["data"], [None, 30, 40])
        self.assertEqual((ranges["xAxisIndex"], ranges["yAxisIndex"]), (1, 1))
        self.assertEqual((volume["xAxisIndex"], volume["yAxisIndex"]), (2, 2))
        self.assertEqual(volume["data"][0]["itemStyle"]["color"], "#16a34a")
        self.assertEqual(volume["data"][1]["itemStyle"]["color"], "#dc2626")
        self.assertIsNone(volume["data"][1]["value"])
        self.assertIsNone(session_rows(frame)[1]["volume"])
        json.dumps(session_rows(frame), allow_nan=False)
        self.assertEqual(
            [series["name"] for series in intraday_chart_options(frame, [])["series"]],
            ["Intraday range (%)", "Volume"],
        )

    def test_strict_fetch_raises_and_default_keeps_legacy_cache_fallback(self):
        today = date.today()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            current = prices().iloc[:1].copy()
            current["date"] = pd.to_datetime([today])
            cache_api_prices(connection, current, "ABC", today, today)
        config = AppConfig(database_file=self.database, total_days=1)
        with patch("psx_stock_tool.data.psxdata.stocks", side_effect=RuntimeError("offline")):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                get_stock_data("ABC", config, raise_on_download_error=True)
            self.assertFalse(get_stock_data("ABC", config).empty)

    def test_independent_launcher_help(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "NumpyDashboard.py"), "--help"],
            cwd=self.directory.name, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ("--database", "--port", "--no-browser"):
            self.assertIn(option, result.stdout)


class IntradayInteractionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # NiceGUI's global reset requires its test-mode storage path under unittest too.
        test_mode = patch("nicegui.helpers.is_pytest", return_value=True)
        test_mode.start()
        self.addCleanup(test_mode.stop)

    async def test_stock_lists_display_inline_and_chart_choices_are_alphabetical(self):
        from nicegui import ui
        from nicegui.testing import user_simulation
        from psx_stock_tool.intraday_dashboard import build_intraday_dashboard

        with TemporaryDirectory() as directory:
            database = str(Path(directory) / "prices.db")
            save_stock_list("def\nabc\tDEF", database)
            with closing(sqlite3.connect(database)) as connection, connection:
                for symbol in ("DEF", "ABC"):
                    cache_api_prices(
                        connection, prices(), symbol, date(2025, 1, 1), date(2025, 1, 31),
                    )

            async def page():
                await build_intraday_dashboard(database)

            async with user_simulation() as user:
                ui.page("/")(page)
                await user.open("/")
                await user.should_see("Loaded cached history for 2 stock(s).", retries=100)
                stock_input = next(iter(user.find("Stock list").elements))
                selection = next(iter(user.find("Stock to chart").elements))
                self.assertEqual(stock_input.value, "DEF, ABC")
                self.assertEqual(stock_input.props["rows"], "1")
                self.assertTrue(stock_input.props["autogrow"])
                self.assertEqual(selection.options, ["ABC", "DEF"])
                self.assertEqual(selection.value, "ABC")
                user.find("Stock to chart").trigger(
                    "update:modelValue", {"value": 1, "label": "DEF"},
                )
                user.find("Stock list").trigger(
                    "update:value", "def,\r\nabc\tABC\nMISSING\n",
                )
                self.assertEqual(stock_input.value, "DEF, ABC, MISSING, ")
                user.find("Stock list").trigger("blur")
                self.assertEqual(stock_input.value, "DEF, ABC, MISSING")
                user.find("Apply stock list").click()
                await user.should_see("No stored data: MISSING.", retries=100)
                self.assertEqual(load_stock_list(database), "DEF, ABC, MISSING")
                self.assertEqual(selection.options, ["ABC", "DEF"])
                self.assertEqual(selection.value, "DEF")
                user.find("Stock list").trigger("update:value", "abc, def,abc")
                user.find("Stock list").trigger("blur")
                self.assertEqual(stock_input.value, "ABC, DEF")

    async def test_cached_chart_stock_switch_settings_and_empty_history(self):
        from nicegui import ui
        from nicegui.testing import user_simulation
        from psx_stock_tool.intraday_dashboard import build_intraday_dashboard

        with TemporaryDirectory() as directory:
            database = str(Path(directory) / "prices.db")
            save_stock_list("ABC\nDEF", database)
            with closing(sqlite3.connect(database)) as connection, connection:
                for symbol in ("ABC", "DEF"):
                    cache_api_prices(
                        connection, prices(), symbol, date(2025, 1, 1), date(2025, 1, 31),
                    )

            async def page():
                await build_intraday_dashboard(database)

            async with user_simulation() as user:
                ui.page("/")(page)
                with patch("psx_stock_tool.data.psxdata.stocks") as fetch:
                    await user.open("/")
                    await user.should_see("Loaded cached history for 2 stock(s).", retries=100)
                    fetch.assert_not_called()
                await user.should_see("ABC | 2025-01-02 to 2025-01-06")
                chart = next(iter(user.find(ui.echart).elements))
                self.assertEqual(
                    [series["name"] for series in chart.options["series"]],
                    ["High", "Low", "Intraday range (%)", "Volume"],
                )
                user.find("Stock to chart").trigger(
                    "update:modelValue", {"value": 1, "label": "DEF"},
                )
                await user.should_see("DEF | 2025-01-02 to 2025-01-06")
                user.find("High").click()
                self.assertEqual(
                    [series["name"] for series in chart.options["series"]],
                    ["Low", "Intraday range (%)", "Volume"],
                )
                user.find("History (calendar days)").clear().type("1")
                user.find("Show cached charts").click()
                await user.should_see("DEF | 2025-01-06 to 2025-01-06", retries=100)
                self.assertEqual(chart.options["xAxis"][0]["data"], ["2025-01-06"])
                user.find("History (calendar days)").clear().type("1.5")
                user.find("Show cached charts").click()
                await user.should_see("History must be a whole number of at least 1.", retries=100)
                user.find("History (calendar days)").clear().type("120")
                user.find("Stock list").clear().type("MISSING")
                user.find("Apply stock list").click()
                await user.should_see("No stored data: MISSING.", retries=100)
                self.assertEqual(chart.options, {})
                self.assertEqual(load_stock_list(database), "MISSING")
                self.assertTrue(all(not table.rows for table in user.find(ui.table).elements))

    async def test_fetch_reports_failures_keeps_cache_and_processes_other_stocks(self):
        from nicegui import ui
        from nicegui.testing import user_simulation
        from psx_stock_tool.intraday_dashboard import build_intraday_dashboard

        with TemporaryDirectory() as directory:
            database = str(Path(directory) / "prices.db")
            save_stock_list("ABC, DEF", database)
            with closing(sqlite3.connect(database)) as connection, connection:
                cache_api_prices(
                    connection, prices(), "ABC", date(2025, 1, 1), date(2025, 1, 31),
                )

            async def page():
                await build_intraday_dashboard(database)

            async with user_simulation() as user:
                ui.page("/")(page)
                await user.open("/")
                await user.should_see("Loaded cached history for 1 stock(s).", retries=100)
                user.find("Refresh recent days").clear().type("7")
                with patch(
                    "psx_stock_tool.intraday_dashboard.get_stock_data",
                    side_effect=[RuntimeError("offline"), pd.DataFrame()],
                ) as fetch:
                    user.find("Fetch / update selected").click()
                    await user.should_see("Fetch finished with 2 error(s) / empty result(s).", retries=100)
                self.assertEqual([call.args[0] for call in fetch.call_args_list], ["ABC", "DEF"])
                self.assertEqual(fetch.call_args.args[1].refresh_days, 7)
                self.assertTrue(fetch.call_args.kwargs["raise_on_download_error"])
                error_box = next(
                    element for element in user.find(ui.textarea).elements
                    if element.props["label"] == "Fetch errors / empty responses"
                )
                self.assertTrue(error_box.visible)
                self.assertIn("ABC: offline", error_box.value)
                self.assertIn("DEF: no candles", error_box.value)
                await user.should_see("ABC | 2025-01-02 to 2025-01-06")
                chart = next(iter(user.find(ui.echart).elements))
                self.assertEqual(len(chart.options["xAxis"][0]["data"]), 3)
                current = prices().tail(1).copy()
                current["date"] = pd.to_datetime([date.today()])
                with patch("psx_stock_tool.data.psxdata.stocks", return_value=current) as provider:
                    user.find("Fetch / update selected").click()
                    await user.should_see(
                        "Fetch finished. Loaded cached history for 2 stock(s).", retries=100,
                    )
                self.assertEqual(provider.call_count, 2)
                self.assertFalse(error_box.visible)
                self.assertEqual(
                    chart.options["xAxis"][0]["data"], [date.today().isoformat()],
                )


if __name__ == "__main__":
    unittest.main()
