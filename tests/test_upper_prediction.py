import json
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from psx_stock_tool.surveillance import SignalConfig, analyze_prices
from psx_stock_tool.upper_prediction import FEATURES, apply_upper_ranking, prediction_samples


def candles(count=80):
    return pd.DataFrame({
        "date": pd.bdate_range("2025-01-01", periods=count),
        "ldcp": 10.0, "open": 10.0, "high": 10.2, "low": 9.8,
        "close": 10.0, "volume": 100.0,
    })


def learning_samples():
    dates = pd.bdate_range("2025-01-01", periods=240)
    records = []
    for i, day in enumerate(dates):
        for j in range(12):
            upper = float((i + j) % 5 == 0)
            records.append({
                **dict.fromkeys(FEATURES, 0.0), "upper_close": upper,
                "date": day, "symbol": f"S{j:02}",
                "target_date": dates[i + 1] if i + 1 < len(dates) else pd.NaT,
                "target": upper if i + 1 < len(dates) else np.nan,
            })
    rows = [
        {"symbol": f"S{j:02}", "date": dates[-1].strftime("%Y-%m-%d"), "stale": False}
        for j in range(12)
    ]
    return rows, pd.DataFrame(records), dates


class PredictionFeatureTests(unittest.TestCase):
    def samples(self, frame, calendar=None, config=SignalConfig()):
        dates = list(frame["date"] if calendar is None else calendar)
        return prediction_samples(analyze_prices(frame, config, dates), "ABC", dates, config)

    def test_features_are_causal_and_input_is_unchanged(self):
        frame = candles()
        original = frame.copy(deep=True)
        before = self.samples(frame.iloc[:45])
        frame.loc[45:, ["high", "close", "volume"]] = [11, 11, 5000]
        after = self.samples(frame)
        columns = ["date", "symbol", *FEATURES]
        pd.testing.assert_frame_equal(
            before[columns], after.loc[after["date"].le(frame.loc[44, "date"]), columns]
            .reset_index(drop=True),
        )
        pd.testing.assert_frame_equal(frame.iloc[:45], original.iloc[:45])
        analyzed = analyze_prices(frame)
        snapshot = analyzed.copy(deep=True)
        prediction_samples(analyzed, "ABC", list(frame["date"]), SignalConfig())
        pd.testing.assert_frame_equal(analyzed, snapshot)

    def test_label_is_next_close_not_today_close_or_next_touch(self):
        frame = candles()
        frame.loc[21, ["high", "close"]] = [11, 11]
        frame.loc[22, ["high", "close"]] = [11, 10.5]
        result = self.samples(frame).set_index("date")
        self.assertEqual(result.loc[frame.loc[20, "date"], "target"], 1)
        self.assertEqual(result.loc[frame.loc[21, "date"], "target"], 0)
        self.assertTrue(pd.isna(result.iloc[-1]["target"]))
        self.assertEqual(result.loc[frame.loc[20, "date"], "target_date"], frame.loc[21, "date"])

    def test_missing_market_session_breaks_label_and_feature_baseline(self):
        frame = candles()
        calendar = list(frame["date"])
        result = self.samples(frame.drop(index=21), calendar).set_index("date")
        self.assertTrue(pd.isna(result.loc[frame.loc[20, "date"], "target"]))
        self.assertFalse(result.index.isin(frame.loc[22:41, "date"]).any())
        self.assertIn(frame.loc[42, "date"], result.index)

    def test_zero_missing_volume_and_rule_mismatch_are_not_negative_labels(self):
        for column, value in (("volume", 0), ("volume", np.nan), ("high", 12)):
            with self.subTest(column=column, value=value):
                frame = candles()
                frame.loc[21, column] = value
                result = self.samples(frame).set_index("date")
                self.assertTrue(pd.isna(result.loc[frame.loc[20, "date"], "target"]))
                self.assertNotIn(frame.loc[21, "date"], result.index)

    def test_flat_candle_and_configured_baseline(self):
        frame = candles()
        frame.loc[31, ["open", "high", "low", "close"]] = 11
        result = self.samples(frame, config=SignalConfig(volume_window=30))
        self.assertEqual(result.iloc[0]["date"], frame.loc[30, "date"])
        flat = result.loc[result["date"].eq(frame.loc[31, "date"])].iloc[0]
        self.assertEqual(flat["close_position"], 0.5)
        self.assertEqual(flat["upper_close"], 1)
        self.assertTrue(np.isfinite(result[list(FEATURES)]).all().all())


class ProbabilityRankingTests(unittest.TestCase):
    def test_real_model_ranks_by_probability_and_reports_temporal_holdout(self):
        rows, samples, dates = learning_samples()
        latest_features = samples.loc[samples["date"].eq(dates[-1])]
        bullish = set(latest_features.loc[latest_features["upper_close"].eq(1), "symbol"])
        report = apply_upper_ranking(rows, samples, dates, today=dates[-1] + pd.Timedelta(days=1))
        self.assertEqual(report["state"], "experimental")
        self.assertEqual([row["rank"] for row in rows], list(range(1, 13)))
        self.assertEqual(set(row["symbol"] for row in rows[:len(bullish)]), bullish)
        chances = [row["upper_probability_pct"] for row in rows]
        self.assertEqual(chances, sorted(chances, reverse=True))
        self.assertTrue(all(0 <= chance <= 100 for chance in chances))
        self.assertLess(report["brier"], report["baseline_brier"])
        self.assertGreater(report["top_10_hit_pct"], report["base_rate_pct"])
        self.assertLess(report["training_end"], report["calibration_end"])
        self.assertLess(report["calibration_end"], report["validation_start"])
        json.dumps({"rows": rows, "prediction": report}, allow_nan=False)
        for chance in set(chances):
            tied = [row["symbol"] for row in rows if row["upper_probability_pct"] == chance]
            self.assertEqual(tied, sorted(tied))

    def test_holdout_outcomes_do_not_train_or_calibrate_live_probabilities(self):
        rows, samples, dates = learning_samples()
        report = apply_upper_ranking(rows, samples, dates, today=dates[-1] + pd.Timedelta(days=1))
        original = {row["symbol"]: row["upper_probability_pct"] for row in rows}
        holdout = samples["date"].ge(pd.Timestamp(report["validation_start"]))
        samples.loc[holdout & samples["target"].notna(), "target"] = (
            1 - samples.loc[holdout & samples["target"].notna(), "target"]
        )
        changed = apply_upper_ranking(rows, samples, dates, today=dates[-1] + pd.Timedelta(days=1))
        self.assertEqual({row["symbol"]: row["upper_probability_pct"] for row in rows}, original)
        self.assertEqual(changed["state"], "weak")
        self.assertTrue(all(row["ranking_status"] == "Weak holdout evidence" for row in rows))

    def test_training_and_calibration_purge_boundary_outcomes_and_future_data(self):
        rows, samples, dates = learning_samples()
        extra = samples.iloc[:12].copy()
        extra["date"] = dates[-1] + pd.Timedelta(days=2)
        extra["target_date"] = dates[-1] + pd.Timedelta(days=3)
        samples = pd.concat([samples, extra], ignore_index=True)
        with patch("psx_stock_tool.upper_prediction.HistGradientBoostingClassifier") as factory:
            model = factory.return_value
            model.decision_function.side_effect = lambda data: (
                data["upper_close"].to_numpy() * 4 - 2
            )
            report = apply_upper_ranking(
                rows, samples, dates, today=dates[-1] + pd.Timedelta(days=1)
            )
            train = samples.loc[model.fit.call_args.args[0].index]
            calibration = samples.loc[model.decision_function.call_args_list[0].args[0].index]
            validation = samples.loc[model.decision_function.call_args_list[1].args[0].index]
        self.assertLess(train["target_date"].max(), calibration["date"].min())
        self.assertLess(calibration["target_date"].max(), validation["date"].min())
        self.assertTrue(validation["target_date"].le(dates[-1]).all())
        self.assertEqual(report["as_of"], dates[-1].strftime("%Y-%m-%d"))

    def test_holdout_picks_are_selected_before_filtering_unknown_outcomes(self):
        rows, samples, dates = learning_samples()
        missing = (
            samples["date"].ge(dates[int((len(dates) - 1) * 0.85)])
            & samples["date"].lt(dates[-1])
            & samples["upper_close"].eq(1)
        )
        samples.loc[missing, "target"] = np.nan
        report = apply_upper_ranking(rows, samples, dates, today=dates[-1] + pd.Timedelta(days=1))
        self.assertLess(report["top_10_coverage_pct"], 100)
        self.assertLess(report["top_10_evaluable"], report["top_10_picks"])
        self.assertEqual(report["state"], "weak")
        self.assertEqual(report["top_10_hit_pct"], 0)

    def test_stale_and_unusable_stocks_sort_last_without_invented_chances(self):
        rows, samples, dates = learning_samples()
        rows.extend([
            {"symbol": "OLD", "date": dates[-2].strftime("%Y-%m-%d"), "stale": True},
            {"symbol": "EMPTY", "date": dates[-1].strftime("%Y-%m-%d"), "stale": False},
        ])
        report = apply_upper_ranking(rows, samples, dates, today=dates[-1] + pd.Timedelta(days=1))
        self.assertEqual(report["ranked_candidates"], 12)
        self.assertEqual([row["symbol"] for row in rows[-2:]], ["EMPTY", "OLD"])
        self.assertTrue(all(row["upper_probability_pct"] is None for row in rows[-2:]))
        self.assertEqual([row["rank"] for row in rows], list(range(1, 15)))

    def test_incomplete_latest_session_is_not_ranked_and_does_not_fit(self):
        rows, samples, dates = learning_samples()
        with patch("psx_stock_tool.upper_prediction.HistGradientBoostingClassifier") as factory:
            report = apply_upper_ranking(rows, samples, dates, today=dates[-1])
            factory.assert_not_called()
        self.assertEqual(report["state"], "unavailable")
        self.assertTrue(all(row["upper_probability_pct"] is None for row in rows))
        self.assertTrue(all(row["ranking_status"] == "Session not completed" for row in rows))

    def test_short_or_single_class_history_reports_unavailable(self):
        for short in (True, False):
            with self.subTest(short=short):
                rows, samples, dates = learning_samples()
                if short:
                    samples = samples.loc[samples["date"].ge(dates[-80])]
                else:
                    samples.loc[samples["target"].notna(), "target"] = 0
                with patch("psx_stock_tool.upper_prediction.HistGradientBoostingClassifier") as factory:
                    report = apply_upper_ranking(
                        rows, samples, dates, today=dates[-1] + pd.Timedelta(days=1)
                    )
                    factory.assert_not_called()
                self.assertEqual(report["state"], "unavailable")
                self.assertTrue(all(row["upper_probability_pct"] is None for row in rows))
                self.assertEqual([row["rank"] for row in rows], list(range(1, 13)))

    def test_empty_cache_is_json_safe(self):
        rows = []
        report = apply_upper_ranking(rows, pd.DataFrame(), [])
        self.assertEqual(report["state"], "unavailable")
        json.dumps({"rows": rows, "prediction": report}, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
