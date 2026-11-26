"""Experimental, temporally evaluated next-session upper-close probabilities."""

from datetime import date

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits


FEATURES = (
    "circuit_progress", "close_position", "body_pct", "range_pct", "log_price",
    "log_volume", "volume_ratio", "momentum_5", "momentum_20", "volatility_20",
    "upper_close", "upper_retreat", "lower_close", "breakout", "upper_streak",
    "lower_streak", "recent_upper_rate",
)


def _valid_candles(frame, config):
    ohlc = frame[["open", "high", "low", "close"]]
    return (
        pd.Series(np.isfinite(ohlc).all(axis=1), index=frame.index)
        & ohlc.gt(0).all(axis=1)
        & frame["high"].ge(ohlc[["open", "close"]].max(axis=1))
        & frame["low"].le(ohlc[["open", "close"]].min(axis=1))
        & frame["high"].ge(frame["low"])
        & frame["volume"].gt(0) & np.isfinite(frame["volume"])
        & frame["high"].le(frame["upper_limit"] + config.tolerance)
        & frame["low"].ge(frame["lower_limit"] - config.tolerance)
    )


def prediction_samples(frame, symbol, market_dates, config):
    """Use only information available at each close; unknown outcomes stay missing."""
    positions = pd.Series(
        range(len(market_dates)), index=pd.to_datetime(market_dates)
    )
    adjacent = frame["date"].map(positions).diff().eq(1)
    reference = frame["ldcp"].where(frame["ldcp"].gt(0), frame["close"].shift(1))
    reference = reference.where(reference.gt(0))
    span = frame["high"] - frame["low"]
    result = pd.DataFrame({
        "symbol": symbol, "date": frame["date"],
        "circuit_progress": (frame["close"] - reference) / (frame["upper_limit"] - reference),
        "close_position": ((frame["close"] - frame["low"]) / span.where(span.gt(0))).where(
            span.ne(0), 0.5
        ),
        "body_pct": (frame["close"] - frame["open"]) / reference * 100,
        "range_pct": span / reference * 100,
        "log_price": np.log1p(frame["close"].clip(lower=0)),
        "log_volume": np.log1p(frame["volume"].clip(lower=0)),
        "volume_ratio": frame["volume_ratio"].clip(upper=20),
        "momentum_5": frame["close"] / frame["close"].shift(5) - 1,
        "momentum_20": frame["close"] / frame["close"].shift(20) - 1,
        "volatility_20": frame["change_pct"].rolling(20, min_periods=20).std(),
        "recent_upper_rate": frame["upper_close"].rolling(20, min_periods=20).mean(),
    })
    for feature in ("upper_close", "upper_retreat", "lower_close", "breakout",
                    "upper_streak", "lower_streak"):
        result[feature] = frame[feature].astype(float)
    valid = _valid_candles(frame, config)
    result["target_date"] = frame["date"].shift(-1)
    result["target"] = frame["upper_close"].shift(-1).astype(float).where(
        adjacent.shift(-1, fill_value=False) & valid.shift(-1, fill_value=False)
    )
    lookback = max(20, config.volume_window)
    usable = (
        valid & adjacent.rolling(lookback, min_periods=lookback).sum().eq(lookback)
        & np.isfinite(result[list(FEATURES)]).all(axis=1)
    )
    return result.loc[usable].reset_index(drop=True)


def _finish_ranks(rows):
    rows.sort(key=lambda row: (
        row["upper_probability_pct"] is None, row["stale"],
        -(row["upper_probability_pct"] or 0), row["symbol"],
    ))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank


def _has_support(samples, minimum_rows, minimum_class):
    positives = int(samples["target"].sum())
    return (
        len(samples) >= minimum_rows
        and positives >= minimum_class
        and len(samples) - positives >= minimum_class
    )


def apply_upper_ranking(rows, samples, market_dates, today=None):
    """Rank all rows; unavailable estimates sort last and are never invented."""
    today = pd.Timestamp(today or date.today())
    latest = pd.Timestamp(market_dates[-1]) if len(market_dates) else None
    current = samples.loc[samples["date"].eq(latest)] if not samples.empty else samples
    available = set(current["symbol"]) if not current.empty else set()
    candidates = []
    for row in rows:
        row["upper_probability_pct"] = None
        if row["stale"]:
            row["ranking_status"] = "Older than newest cached session"
        elif pd.Timestamp(row["date"]) >= today:
            row["ranking_status"] = "Session not completed"
        elif row["symbol"] not in available:
            row["ranking_status"] = "Insufficient/invalid history or traded volume"
        else:
            row["ranking_status"] = "Insufficient model history"
            candidates.append(row)

    report = {"state": "unavailable", "message": "No current completed-session candidates."}
    if not candidates:
        _finish_ranks(rows)
        return report

    recent_dates = pd.to_datetime(market_dates)
    recent_dates = recent_dates[recent_dates <= latest][-504:]
    historical_candidates = samples.loc[
        samples["date"].ge(recent_dates[0]) & samples["date"].lt(latest)
    ].sort_values(["date", "symbol"]).copy()
    historical_candidates.loc[
        historical_candidates["target_date"].gt(latest)
        | historical_candidates["target_date"].ge(today), "target"
    ] = np.nan
    history = historical_candidates.loc[historical_candidates["target"].notna()]
    dates = history["date"].drop_duplicates().sort_values().tolist()
    if len(dates) < 120:
        report["message"] = "Need at least 120 usable historical sessions for probability ranking."
        _finish_ranks(rows)
        return report

    calibration_start = dates[int(len(dates) * 0.70)]
    validation_start = dates[int(len(dates) * 0.85)]
    # Purge outcomes on each later block's first feature date, across ALL symbols.
    train = history.loc[history["target_date"].lt(calibration_start)]
    calibration = history.loc[
        history["date"].ge(calibration_start) & history["target_date"].lt(validation_start)
    ]
    validation = history.loc[history["date"].ge(validation_start)]
    validation_candidates = historical_candidates.loc[
        historical_candidates["date"].ge(validation_start)
    ]
    if not (_has_support(train, 1000, 30) and _has_support(calibration, 200, 10)
            and len(validation) >= 200):
        report["message"] = (
            "Insufficient training/calibration outcomes: need 1,000 training rows with "
            "30 of each outcome, 200 calibration rows with 10 of each, and 200 holdout rows."
        )
        _finish_ranks(rows)
        return report

    model = HistGradientBoostingClassifier(
        learning_rate=0.07, max_iter=120, max_leaf_nodes=15, min_samples_leaf=50,
        l2_regularization=5, max_bins=128, early_stopping=False, random_state=0,
    )
    calibrator = LogisticRegression(C=1.0, random_state=0)
    candidate_symbols = {row["symbol"] for row in candidates}
    current = current.loc[current["symbol"].isin(candidate_symbols)].copy()
    with threadpool_limits(limits=2):
        model.fit(train[list(FEATURES)], train["target"])
        calibrator.fit(
            model.decision_function(calibration[list(FEATURES)]).reshape(-1, 1),
            calibration["target"],
        )

        def probabilities(data):
            return calibrator.predict_proba(
                model.decision_function(data[list(FEATURES)]).reshape(-1, 1)
            )[:, 1]

        tested = probabilities(validation_candidates)
        predicted = probabilities(current)

    if not (np.isfinite(tested).all() and np.isfinite(predicted).all()):
        raise ValueError("Upper-close model produced non-finite probabilities")
    outcomes = validation["target"].to_numpy()
    known = validation_candidates["target"].notna().to_numpy()
    brier = float(np.mean((tested[known] - outcomes) ** 2))
    baseline_brier = float(np.mean((calibration["target"].mean() - outcomes) ** 2))
    evaluated = validation_candidates[["date", "symbol", "target"]].copy()
    evaluated["probability"] = tested
    # Select picks before observing whether tomorrow's outcome is available.
    top = evaluated.sort_values(
        ["date", "probability", "symbol"], ascending=[True, False, True]
    ).groupby("date", sort=False).head(10)
    top_known = top.loc[top["target"].notna()]
    top_hit = float(top_known["target"].mean()) if len(top_known) else None
    base_rate = float(outcomes.mean())
    weak = brier >= baseline_brier or top_hit is None or top_hit <= base_rate
    estimates = dict(zip(current["symbol"], predicted))
    for row in candidates:
        row["upper_probability_pct"] = float(estimates[row["symbol"]] * 100)
        row["ranking_status"] = "Weak holdout evidence" if weak else "Experimental estimate"

    report = {
        "state": "weak" if weak else "experimental",
        "message": "Weak holdout evidence; ranking is not demonstrated to beat baseline."
                   if weak else "Experimental calibrated gradient-boosted ranking.",
        "as_of": latest.strftime("%Y-%m-%d"),
        "training_rows": len(train), "calibration_rows": len(calibration),
        "validation_rows": len(validation),
        "validation_sessions": int(validation_candidates["date"].nunique()),
        "training_end": train["target_date"].max().strftime("%Y-%m-%d"),
        "calibration_end": calibration["target_date"].max().strftime("%Y-%m-%d"),
        "validation_start": validation["date"].min().strftime("%Y-%m-%d"),
        "validation_end": validation["target_date"].max().strftime("%Y-%m-%d"),
        "brier": brier, "baseline_brier": baseline_brier,
        "top_10_hit_pct": top_hit * 100 if top_hit is not None else None,
        "top_10_coverage_pct": len(top_known) / len(top) * 100,
        "top_10_evaluable": len(top_known), "top_10_picks": len(top),
        "base_rate_pct": base_rate * 100,
        "ranked_candidates": len(candidates),
    }
    _finish_ranks(rows)
    return report
