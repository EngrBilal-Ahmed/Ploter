"""Shared ECharts candlesticks and indicative circuit overlays."""

import pandas as pd


def circuit_price_series(frame):
    """Keep both dashboards' candles, limit estimates and touch markers identical."""
    days = frame["date"].dt.strftime("%Y-%m-%d").tolist()

    def values(column):
        return [None if pd.isna(value) else float(value) for value in frame[column]]

    return [
        {"name": "OHLC", "type": "candlestick",
         "data": frame[["open", "close", "low", "high"]].values.tolist(),
         "itemStyle": {"color": "#16a34a", "color0": "#dc2626",
                       "borderColor": "#16a34a", "borderColor0": "#dc2626"}},
        {"name": "Upper estimate", "type": "line", "data": values("upper_limit"),
         "showSymbol": False, "lineStyle": {"type": "dashed", "opacity": 0.5}},
        {"name": "Lower estimate", "type": "line", "data": values("lower_limit"),
         "showSymbol": False, "lineStyle": {"type": "dashed", "opacity": 0.5}},
        *[
            {"name": label, "type": "scatter", "symbol": "circle", "symbolSize": 10,
             "itemStyle": {"color": color},
             "data": [[day, float(row[price])] for day, (_, row) in zip(days, frame.iterrows())
                      if row[flag]]}
            for label, color, price, flag in (
                ("Upper touch", "#f59e0b", "high", "upper_touch"),
                ("Lower touch", "#7c3aed", "low", "lower_touch"),
            )
        ],
    ]
