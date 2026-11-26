from contextlib import closing
from datetime import date
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from psx_stock_tool.dashboard import chart_options
from psx_stock_tool.dashboard_sync import plan_fetch_start, sync_eligible_prices
from psx_stock_tool.database import initialize_database
from psx_stock_tool.eligibility import load_eligible_stocks
from psx_stock_tool.ingestion import cache_api_prices
from psx_stock_tool.surveillance import (
    SignalConfig, analyze_prices, load_dashboard_overview, load_symbol_analysis,
)


def prices(count=24):
    return pd.DataFrame({
        "date": pd.bdate_range("2025-01-01", periods=count),
        "ldcp": [10.0] * count, "open": [10.0] * count, "high": [10.2] * count,
        "low": [9.8] * count, "close": [10.0] * count, "volume": [100] * count,
    })


class AnalysisTests(unittest.TestCase):
    def test_limit_minimum_and_percentage(self):
        frame = prices(2)
        frame.loc[0, ["ldcp", "high", "close"]] = [5, 6, 6]
        frame.loc[1, ["ldcp", "high", "close"]] = [100, 110, 110]
        analyzed = analyze_prices(frame)
        self.assertEqual(analyzed["upper_limit"].tolist(), [6, 110])
        self.assertEqual(analyzed["lower_limit"].tolist(), [4, 90])
        self.assertTrue(analyzed["upper_close"].all())

    def test_prior_volume_baseline_and_no_future_leakage(self):
        frame = prices()
        frame.loc[20, ["volume", "close", "high"]] = [1000, 10.9, 10.9]
        before = analyze_prices(frame.iloc[:21])
        after = analyze_prices(frame)
        self.assertEqual(after.loc[20, "volume_average"], 100)
        self.assertEqual(after.loc[20, "volume_ratio"], 10)
        self.assertEqual(after.loc[20, "signal"], "Volume breakout")
        self.assertTrue(pd.isna(after.loc[19, "volume_ratio"]))
        pd.testing.assert_frame_equal(before, after.iloc[:21].reset_index(drop=True))

    def test_upper_streak_retreat_and_reversal(self):
        frame = prices()
        frame.loc[20, ["high", "close", "volume"]] = [11, 11, 500]
        frame.loc[21, ["ldcp", "high", "close"]] = [11, 12.1, 12.1]
        frame.loc[22, ["ldcp", "high", "close"]] = [12.1, 13.31, 12]
        analyzed = analyze_prices(frame)
        self.assertEqual(analyzed.loc[21, "upper_streak"], 2)
        self.assertTrue(analyzed.loc[22, "upper_retreat"])
        self.assertEqual(analyzed.loc[22, "signal"], "Reversal after upper run")
        self.assertEqual(analyzed.loc[22, "upper_streak"], 0)

    def test_streak_does_not_bridge_missing_market_sessions(self):
        frame = prices(3)
        frame.loc[:, "high"] = 11
        frame.loc[:, "close"] = 11
        full_calendar = frame["date"].tolist()
        analyzed = analyze_prices(frame.iloc[[0, 2]], market_dates=full_calendar)
        self.assertEqual(analyzed["upper_streak"].tolist(), [1, 1])

    def test_lower_close_and_invalid_reference(self):
        frame = prices(3)
        frame.loc[0, "ldcp"] = 0
        frame.loc[1, ["low", "close"]] = [9, 9]
        frame.loc[2, ["low", "close"]] = [9, 9]
        result = analyze_prices(frame)
        self.assertTrue(pd.isna(result.loc[0, "upper_limit"]))
        self.assertEqual(result.loc[2, "lower_streak"], 2)
        self.assertEqual(result.loc[2, "signal"], "Lower circuit close")

    def test_zero_volume_and_settings_validation(self):
        frame = prices()
        frame["volume"] = 0
        result = analyze_prices(frame)
        self.assertFalse(result["volume_spike"].any())
        self.assertTrue(result["volume_ratio"].isna().all())
        for kwargs in ({"volume_window": 0}, {"volume_multiple": 1}, {"limit_pct": float("nan")}):
            with self.assertRaises(ValueError):
                SignalConfig(**kwargs)

    def test_charts_are_json_safe_and_histograms_count_events(self):
        frame = prices()
        frame.loc[20, ["high", "close", "volume"]] = [11, 11, 1000]
        result = analyze_prices(frame)
        options = chart_options(result)
        json.dumps(options, allow_nan=False)
        self.assertEqual(sum(options[2]["series"][0]["data"]), 1)
        self.assertEqual(options[1]["series"][0]["data"][20]["itemStyle"]["color"], "#f97316")


class EligibleDashboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / "cache.db")
        self.eligible = Path(self.directory.name) / "eligible.xml"
        self.eligible.write_text(
            "<eligible_data><data>"
            "<data><symbol_code> abc </symbol_code><symbol_name>ABC Ltd</symbol_name></data>"
            "<data><symbol_code>DEF</symbol_code><symbol_name>DEF Ltd</symbol_name></data>"
            "</data></eligible_data>", encoding="utf-8",
        )
        with closing(sqlite3.connect(self.database)) as connection, connection:
            initialize_database(connection)
            for symbol in ("ABC", "NOT_ELIGIBLE"):
                frame = prices(3)
                frame = frame.iloc[[0, 2]]
                cache_api_prices(connection, frame, symbol, date(2025, 1, 1), date(2025, 1, 3))

    def query(self, query):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(query).fetchall()

    def test_xml_and_eligible_only_analysis(self):
        self.assertEqual(load_eligible_stocks(self.eligible), {"ABC": "ABC Ltd", "DEF": "DEF Ltd"})
        result = load_dashboard_overview(self.database, self.eligible)
        self.assertEqual([row["symbol"] for row in result["rows"]], ["ABC"])
        self.assertEqual(result["missing"], ["DEF"])
        self.assertEqual(result["rows"][0]["rank"], 1)
        self.assertIsNone(result["rows"][0]["upper_probability_pct"])
        self.assertEqual(result["prediction"]["state"], "unavailable")
        self.assertEqual(self.query(
            "SELECT COUNT(*) FROM stock_prices WHERE symbol = 'NOT_ELIGIBLE'"
        ), [(2,)])
        with self.assertRaisesRegex(ValueError, "not in"):
            load_symbol_analysis(self.database, "NOT_ELIGIBLE", self.eligible)

    def test_bad_eligibility_never_falls_back_to_all_stocks(self):
        self.eligible.write_text("<eligible_data/>", encoding="utf-8")
        with patch("psx_stock_tool.dashboard_sync.psxdata.stocks") as fetch:
            with self.assertRaises(ValueError):
                sync_eligible_prices(self.database, self.eligible)
            fetch.assert_not_called()

    def test_gap_plan_refresh_and_checked_empty_ranges(self):
        sessions = [date(2025, 1, day) for day in (1, 2, 3, 6)]
        stored = [sessions[0], sessions[2]]
        first = plan_fetch_start(stored, sessions, [], sessions[0], sessions[-1], 0)
        self.assertEqual(first, sessions[1])
        self.assertIsNone(plan_fetch_start(
            stored, sessions, [(sessions[0], sessions[-1])], sessions[0], sessions[-1], 0
        ))
        self.assertEqual(plan_fetch_start(
            stored, sessions, [(sessions[0], sessions[-1])], sessions[0], sessions[-1], 2
        ), date(2025, 1, 5))

    def test_only_eligible_are_fetched_and_empty_results_are_remembered(self):
        frame = prices(4)
        with patch("psx_stock_tool.dashboard_sync.psxdata.stocks",
                   side_effect=[frame, pd.DataFrame()]) as fetch:
            result = sync_eligible_prices(
                self.database, self.eligible, history_start=date(2025, 1, 1), end_date=date(2025, 1, 6)
            )
        self.assertFalse(result.failures)
        self.assertEqual(result.requested, 2)
        self.assertEqual([call.args[0] for call in fetch.call_args_list], ["ABC", "DEF"])
        self.assertEqual(fetch.call_args_list[0].kwargs["start"], date(2025, 1, 6))
        with patch("psx_stock_tool.dashboard_sync.psxdata.stocks") as fetch:
            result = sync_eligible_prices(
                self.database, self.eligible, history_start=date(2025, 1, 1), end_date=date(2025, 1, 6)
            )
            fetch.assert_not_called()
        self.assertEqual(result.unchanged, 2)

    def test_failed_symbols_are_reported_and_not_marked_checked(self):
        with patch("psx_stock_tool.dashboard_sync.psxdata.stocks",
                   side_effect=[RuntimeError("offline"), pd.DataFrame()]):
            with self.assertLogs("psx_stock_tool.dashboard_sync", level="ERROR"):
                result = sync_eligible_prices(
                    self.database, self.eligible, history_start=date(2025, 1, 1), end_date=date(2025, 1, 6)
                )
        self.assertEqual(result.failures, {"ABC": "offline"})
        self.assertEqual(self.query("SELECT DISTINCT symbol FROM dashboard_sync_ranges"), [("DEF",)])

    def test_internal_gap_is_found_from_another_eligible_market_history(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            cache_api_prices(
                connection, prices(3), "DEF", date(2025, 1, 1), date(2025, 1, 3)
            )
        with patch("psx_stock_tool.dashboard_sync.psxdata.stocks",
                   return_value=pd.DataFrame()) as fetch:
            sync_eligible_prices(
                self.database, self.eligible, history_start=date(2025, 1, 1), end_date=date(2025, 1, 3)
            )
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[0], "ABC")
        self.assertEqual(fetch.call_args.kwargs["start"], date(2025, 1, 2))

    def test_refresh_forces_previously_checked_recent_days(self):
        for _ in range(2):
            with patch("psx_stock_tool.dashboard_sync.psxdata.stocks",
                       return_value=pd.DataFrame()) as fetch:
                sync_eligible_prices(
                    self.database, self.eligible, history_start=date(2025, 1, 1),
                    end_date=date(2025, 1, 6), refresh_days=2,
                )
                self.assertEqual(fetch.call_count, 2)
                self.assertTrue(all(call.kwargs["start"] <= date(2025, 1, 5)
                                    for call in fetch.call_args_list))


class DashboardInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_probability_columns_diagnostics_and_filters_keep_global_ranks(self):
        from nicegui import ui
        from nicegui.testing import user_simulation
        from psx_stock_tool.dashboard import build_dashboard

        prediction = {
            "state": "experimental", "message": "Experimental calibrated ranking.",
            "as_of": "2025-02-03", "ranked_candidates": 2,
            "training_rows": 1000, "training_end": "2024-06-01",
            "calibration_rows": 200, "calibration_end": "2024-10-01",
            "validation_start": "2024-10-02", "validation_end": "2025-02-03",
            "validation_rows": 200, "validation_sessions": 20,
            "top_10_hit_pct": 45.0, "base_rate_pct": 3.0,
            "top_10_evaluable": 199, "top_10_picks": 200, "top_10_coverage_pct": 99.5,
            "brier": 0.02, "baseline_brier": 0.03,
        }
        overview = {
            "stocks": {"ABC": "ABC Ltd", "DEF": "DEF Ltd"}, "latest": "2025-02-03",
            "missing": [], "prediction": prediction, "rows": [
                {"symbol": "DEF", "name": "DEF Ltd", "rank": 1,
                 "upper_probability_pct": 30.12345, "ranking_status": "Experimental estimate",
                 "stale": False, "date": "2025-02-03", "priority": 2},
                {"symbol": "ABC", "name": "ABC Ltd", "rank": 2,
                 "upper_probability_pct": 10.5, "ranking_status": "Experimental estimate",
                 "stale": False, "date": "2025-02-03", "priority": 0},
            ],
        }

        async def page():
            await build_dashboard("unused.db", "unused.xml")

        with patch("psx_stock_tool.dashboard.load_dashboard_overview", return_value=overview), patch(
            "psx_stock_tool.dashboard.load_symbol_analysis", return_value=analyze_prices(prices())
        ):
            async with user_simulation() as user:
                ui.page("/")(page)
                await user.open("/")
                await user.should_see("Analysis loaded from SQLite.", retries=100)
                await user.should_see("Top-10 evaluable hit rate: 45.0%")
                await user.should_see("Pick coverage: 199/200")
                watch = min(user.find(ui.table).elements, key=lambda table: table.id)
                self.assertEqual([row["rank"] for row in watch.rows], [1, 2])
                self.assertEqual(watch.rows[0]["upper_probability_pct"], 30.123)
                user.find("Search symbol or company").type("ABC")
                self.assertEqual([row["rank"] for row in watch.rows], [2])
                user.find("Search symbol or company").clear()
                user.find("Signals only").click()
                self.assertEqual([row["rank"] for row in watch.rows], [1])
                prediction["top_10_hit_pct"] = None
                user.find("Reload analysis").click()
                await user.should_see("Top-10 evaluable hit rate: unavailable", retries=100)
                self.assertEqual([row["rank"] for row in watch.rows], [1])

    async def test_dashboard_loads_charts_searches_and_syncs_with_refresh(self):
        from nicegui import ui
        from nicegui.testing import user_simulation
        from psx_stock_tool.dashboard import build_dashboard
        from psx_stock_tool.dashboard_sync import DashboardSyncResult

        with TemporaryDirectory() as directory:
            database = str(Path(directory) / "prices.db")
            eligible = Path(directory) / "eligible.xml"
            eligible.write_text(
                "<eligible_data><data><symbol_code>ABC</symbol_code>"
                "<symbol_name>ABC Ltd</symbol_name></data></eligible_data>",
                encoding="utf-8",
            )
            with closing(sqlite3.connect(database)) as connection, connection:
                initialize_database(connection)
                cache_api_prices(
                    connection, prices(), "ABC", date(2025, 1, 1), date(2025, 12, 31)
                )

            async def page():
                await build_dashboard(database, eligible)

            async with user_simulation() as user:
                ui.page("/")(page)
                await user.open("/")
                await user.should_see("Analysis loaded from SQLite.", retries=100)
                await user.should_see("ABC | through", retries=100)
                charts = user.find(ui.echart).elements
                self.assertEqual(len(charts), 3)
                self.assertTrue(all(chart.options["series"] for chart in charts))
                user.find("Search symbol or company").type("NOT_FOUND")
                watch = min(user.find(ui.table).elements, key=lambda table: table.id)
                self.assertEqual(watch.rows, [])
                self.assertTrue({"rank", "upper_probability_pct", "ranking_status"}.issubset(
                    {column["field"] for column in watch.columns}
                ))
                user.find("Search symbol or company").clear()
                self.assertEqual(len(watch.rows), 1)
                self.assertEqual(watch.rows[0]["rank"], 1)
                self.assertIsNone(watch.rows[0]["upper_probability_pct"])
                await user.should_see("Need at least 120 usable historical sessions")
                user.find("Refresh past X calendar days").clear().type("7")
                with patch("psx_stock_tool.dashboard.sync_eligible_prices",
                           return_value=DashboardSyncResult(requested=1)) as sync:
                    user.find("Fetch all missing dates").click()
                    await user.should_see("Requested: 1;", retries=100)
                    self.assertEqual(sync.call_args.kwargs["refresh_days"], 7)
                    await user.should_see("Analysis loaded from SQLite.", retries=100)
                eligible.write_text("<eligible_data/>", encoding="utf-8")
                user.find("Reload analysis").click()
                await user.should_see("Analysis unavailable", retries=100)
                self.assertEqual(watch.rows, [])
                self.assertTrue(all(not chart.options for chart in charts))


if __name__ == "__main__":
    unittest.main()
