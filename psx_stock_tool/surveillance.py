"""Explainable daily-candle screening, not a prediction of market manipulation."""

from contextlib import closing
from dataclasses import dataclass
import math
import sqlite3

import pandas as pd

from .database import initialize_database
from .eligibility import DEFAULT_ELIGIBLE_FILE, load_eligible_stocks, register_eligible_stocks
from .upper_prediction import apply_upper_ranking, prediction_samples


@dataclass(frozen=True)
class SignalConfig:
    limit_pct: float = 10.0
    min_move: float = 1.0
    volume_window: int = 20
    volume_multiple: float = 3.0
    tolerance: float = 0.02

    def __post_init__(self):
        if (
            not all(math.isfinite(x) for x in (
                self.limit_pct, self.min_move, self.volume_multiple, self.tolerance
            ))
            or not 0 < self.limit_pct < 100 or self.min_move <= 0
            or not isinstance(self.volume_window, int) or self.volume_window < 2
            or self.volume_multiple <= 1 or self.tolerance < 0
        ):
            raise ValueError("Invalid circuit or volume settings")


def analyze_circuits(prices, config=SignalConfig()):
    """Calculate indicative limits and touches from each candle and prior close."""
    frame = prices.sort_values("date").drop_duplicates("date", keep="last").copy()
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"])
    for column in ("open", "high", "low", "close", "volume", "ldcp"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    reference = frame["ldcp"].where(frame["ldcp"].gt(0), frame["close"].shift(1))
    reference = reference.where(reference.gt(0))
    move = (reference * config.limit_pct / 100).clip(lower=config.min_move)
    frame["upper_limit"] = (reference + move).round(2)
    frame["lower_limit"] = (reference - move).clip(lower=0).round(2)
    for side, price in (("upper", "high"), ("lower", "low")):
        limit = frame[f"{side}_limit"]
        frame[f"{side}_touch"] = (
            (frame[price] - limit).abs().le(config.tolerance + 1e-9) & limit.gt(0)
        )
        frame[f"{side}_close"] = (
            (frame["close"] - limit).abs().le(config.tolerance + 1e-9)
            & frame[f"{side}_touch"]
        )
    frame["upper_retreat"] = frame["upper_touch"] & ~frame["upper_close"]
    frame["lower_rebound"] = frame["lower_touch"] & ~frame["lower_close"]
    frame["change_pct"] = ((frame["close"] - reference) / reference) * 100
    return frame.reset_index(drop=True)


def analyze_prices(prices, config=SignalConfig(), market_dates=None):
    """Calculate features using only the current candle and preceding observations."""
    frame = analyze_circuits(prices, config)
    if frame.empty:
        return frame
    frame["volume_average"] = frame["volume"].shift(1).rolling(
        config.volume_window, min_periods=config.volume_window
    ).mean()
    frame["volume_ratio"] = frame["volume"] / frame["volume_average"].where(
        frame["volume_average"].gt(0)
    )
    frame["volume_spike"] = frame["volume_ratio"].ge(config.volume_multiple)
    frame["breakout"] = frame["close"].gt(
        frame["high"].shift(1).rolling(
            config.volume_window, min_periods=config.volume_window
        ).max()
    )
    if market_dates is None:
        calendar = pd.bdate_range(frame["date"].min(), frame["date"].max())
    else:
        calendar = pd.DatetimeIndex(pd.to_datetime(sorted(market_dates)))
    positions = pd.Series(range(len(calendar)), index=calendar)
    adjacent = frame["date"].map(positions).diff().eq(1)
    for side in ("upper", "lower"):
        active = frame[f"{side}_close"]
        groups = (~active | ~adjacent).cumsum()
        frame[f"{side}_streak"] = active.astype(int).groupby(groups).cumsum()
    frame["signal"] = "Observe"
    frame["priority"] = 0
    conditions = [
        (frame["volume_spike"], "Elevated volume", 1),
        (frame["volume_spike"] & frame["breakout"], "Volume breakout", 2),
        (frame["upper_close"] & frame["volume_spike"], "Upper close + volume", 3),
        (frame["upper_streak"].ge(2), "Consecutive upper closes", 4),
        (frame["upper_retreat"], "Upper touch / close away", 5),
        (frame["lower_rebound"], "Lower touch / close away", 5),
        (frame["lower_close"], "Lower circuit close", 6),
        (frame["upper_streak"].shift(1).ge(2) & frame["change_pct"].lt(0) & adjacent,
         "Reversal after upper run", 7),
    ]
    for condition, label, priority in conditions:
        frame.loc[condition, ["signal", "priority"]] = [label, priority]
    return frame.reset_index(drop=True)


def _read_prices(connection, symbol):
    return pd.read_sql_query(
        """SELECT date, ldcp, open, high, low, close, volume FROM stock_prices
           WHERE symbol = ? ORDER BY date""",
        connection, params=(symbol,),
    )


def load_dashboard_overview(database_file, eligible_file=DEFAULT_ELIGIBLE_FILE, config=SignalConfig()):
    stocks = load_eligible_stocks(eligible_file)
    rows = []
    samples = []
    with closing(sqlite3.connect(database_file, timeout=30)) as connection:
        initialize_database(connection)
        register_eligible_stocks(connection, stocks)
        market_dates = [row[0] for row in connection.execute(
            "SELECT DISTINCT date FROM stock_prices JOIN eligible_symbols USING (symbol) ORDER BY date"
        )]
        prediction_start = pd.Timestamp(market_dates[-504:][0]) if market_dates else None
        available = {row[0] for row in connection.execute(
            "SELECT DISTINCT symbol FROM stock_prices JOIN eligible_symbols USING (symbol)"
        )}
        for symbol in sorted(available):
            frame = analyze_prices(_read_prices(connection, symbol), config, market_dates)
            symbol_samples = prediction_samples(frame, symbol, market_dates, config)
            samples.append(symbol_samples.loc[symbol_samples["date"].ge(prediction_start)])
            latest = frame.iloc[-1]
            row = {
                "symbol": symbol, "name": stocks[symbol], "date": latest["date"].strftime("%Y-%m-%d"),
                "signal": latest["signal"], "priority": int(latest["priority"]),
                "close": latest["close"], "change_pct": latest["change_pct"],
                "volume": latest["volume"], "volume_ratio": latest["volume_ratio"],
                "upper_streak": int(latest["upper_streak"]), "lower_streak": int(latest["lower_streak"]),
                "max_upper_run": int(frame["upper_streak"].max()),
                "max_lower_run": int(frame["lower_streak"].max()),
                "upper_closes": int(frame["upper_close"].sum()),
                "lower_closes": int(frame["lower_close"].sum()),
                "retreats": int(frame["upper_retreat"].sum()), "sessions": len(frame),
                "start": frame["date"].iloc[0].strftime("%Y-%m-%d"),
                "stale": latest["date"].strftime("%Y-%m-%d") < market_dates[-1],
            }
            rows.append(row)
    prediction = apply_upper_ranking(
        rows, pd.concat(samples, ignore_index=True) if samples else pd.DataFrame(), market_dates
    )
    clean_rows = pd.DataFrame(rows).astype(object)
    rows = clean_rows.where(pd.notna(clean_rows), None).to_dict("records")
    return {"stocks": stocks, "rows": rows, "latest": market_dates[-1] if market_dates else None,
            "missing": sorted(set(stocks) - available), "prediction": prediction}


def load_symbol_analysis(database_file, symbol, eligible_file=DEFAULT_ELIGIBLE_FILE, config=SignalConfig()):
    stocks = load_eligible_stocks(eligible_file)
    if symbol not in stocks:
        raise ValueError(f"{symbol} is not in the eligible-stock list")
    with closing(sqlite3.connect(database_file, timeout=30)) as connection:
        initialize_database(connection)
        register_eligible_stocks(connection, stocks)
        calendar = [row[0] for row in connection.execute(
            "SELECT DISTINCT date FROM stock_prices JOIN eligible_symbols USING (symbol) ORDER BY date"
        )]
        return analyze_prices(_read_prices(connection, symbol), config, calendar)
