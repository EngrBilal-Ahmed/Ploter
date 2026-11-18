"""Cached daily OHLC and high/low-range views for the intraday dashboard."""

from contextlib import closing
from datetime import date, timedelta
import math
import sqlite3

import pandas as pd

from .charting import circuit_price_series
from .data import calculate_intraday_ranges
from .database import initialize_database
from .surveillance import SignalConfig, analyze_circuits


def load_intraday_history(database_file, symbols, days, circuit_config=SignalConfig()):
    """Read each symbol's last calendar-day window without downloading prices."""
    if not isinstance(days, int) or days < 1:
        raise ValueError("History days must be a whole number of at least 1")
    frames = {}
    with closing(sqlite3.connect(database_file)) as connection, connection:
        initialize_database(connection)
        for symbol in symbols:
            latest = connection.execute(
                "SELECT MAX(date) FROM stock_prices WHERE symbol = ? AND date <= ?",
                (symbol, date.today().isoformat()),
            ).fetchone()[0]
            if latest is None:
                continue
            start = date.fromisoformat(latest) - timedelta(days=days - 1)
            frame = pd.read_sql_query(
                """
                SELECT date, ldcp, open, high, low, close, volume
                FROM stock_prices
                WHERE symbol = ? AND date BETWEEN COALESCE(
                    (SELECT MAX(date) FROM stock_prices WHERE symbol = ? AND date < ?), ?
                ) AND ?
                ORDER BY date
                """,
                connection,
                params=(symbol, symbol, start.isoformat(), start.isoformat(), latest),
                parse_dates=["date"],
            )
            frame = analyze_circuits(frame, circuit_config)
            frames[symbol] = calculate_intraday_ranges(
                frame.loc[frame["date"] >= pd.Timestamp(start)].reset_index(drop=True)
            )
    return frames


def _number(value):
    return float(value) if pd.notna(value) and math.isfinite(value) else None


def session_rows(frame):
    """Make daily-session records safe for NiceGUI tables and JSON."""
    return [
        {"date": row["date"].strftime("%Y-%m-%d"), **{
            column: _number(row[column])
            for column in ("open", "high", "low", "close", "daily_range",
                           "intraday_range_percent", "volume")
        }}
        for _, row in frame.iterrows()
    ]


def intraday_chart_options(frame, price_graphs, *, price_view="line"):
    """Link price, daily range and volume through both shared zoom controls."""
    if price_view not in ("line", "candlestick"):
        raise ValueError("Price view must be line or candlestick")
    days = frame["date"].dt.strftime("%Y-%m-%d").tolist()

    def values(column):
        return [_number(value) for value in frame[column]]

    series = []
    for column, label in (price_graphs if price_view == "line" else []):
        series.append({
            "name": label, "type": "line", "xAxisIndex": 0, "yAxisIndex": 0,
            "data": values(column), "symbol": "circle", "symbolSize": 5,
            "markPoint": {
                "symbol": "circle", "symbolSize": 8,
                "label": {"show": True, "position": "top", "formatter": "{b}: {c}"},
                "data": [
                    {"name": f"{label} {name}",
                     "coord": [frame.loc[index, "date"].strftime("%Y-%m-%d"),
                               _number(frame.loc[index, column])],
                     "value": round(float(frame.loc[index, column]), 2),
                     "label": {"position": "bottom" if name == "min" else "top"}}
                    for name, index in (
                        ("min", frame[column].idxmin()), ("max", frame[column].idxmax())
                    )
                ] if not frame.empty else [],
            },
        })
    if price_view == "candlestick":
        series = [
            {**item, "xAxisIndex": 0, "yAxisIndex": 0}
            for item in circuit_price_series(frame)
        ]
    price_legend = [item["name"] for item in series]
    series.extend([
        {
            "name": "Intraday range (%)", "type": "bar", "xAxisIndex": 1, "yAxisIndex": 1,
            "data": values("intraday_range_percent"), "itemStyle": {"color": "#3b82f6"},
            "label": {
                "show": True, "position": "top", "fontSize": 10,
                ":formatter": "p => p.value == null ? '' : p.value.toFixed(2) + '%'",
            },
            "labelLayout": {"hideOverlap": True},
            "tooltip": {
                ":valueFormatter": "value => value == null ? '-' : value.toFixed(2) + '%'",
            },
        },
        {
            "name": "Volume", "type": "bar", "xAxisIndex": 2, "yAxisIndex": 2,
            "data": [
                {"value": _number(row["volume"]),
                 "itemStyle": {"color": "#16a34a" if row["close"] >= row["open"] else "#dc2626"}}
                for _, row in frame.iterrows()
            ],
        },
    ])
    return {
        "animation": False,
        "title": [
            {"text": "OHLC candlesticks & estimated circuits (Rs.)"
             if price_view == "candlestick" else "OHLC price (Rs.)", "left": 65, "top": 8},
            {"text": "Intraday high/low range (%)", "left": 65, "top": 385},
            {"text": "Daily traded volume", "left": 65, "top": 615},
        ],
        "legend": {"data": price_legend, "top": 35},
        "tooltip": {"trigger": "axis", "confine": True, "axisPointer": {"type": "cross"}},
        "axisPointer": {"link": [{"xAxisIndex": "all"}]},
        "grid": [
            {"left": 80, "right": 40, "top": 80, "height": 260},
            {"left": 80, "right": 40, "top": 430, "height": 140},
            {"left": 80, "right": 40, "top": 660, "bottom": 90},
        ],
        "xAxis": [
            {"type": "category", "gridIndex": index, "data": days, "boundaryGap": True}
            for index in range(3)
        ],
        "yAxis": [
            {"type": "value", "gridIndex": 0, "scale": True},
            {"type": "value", "gridIndex": 1, "min": 0, "axisLabel": {"formatter": "{value}%"}},
            {"type": "value", "gridIndex": 2, "min": 0},
        ],
        "dataZoom": [
            {"type": "inside", "xAxisIndex": [0, 1, 2], "filterMode": "none",
             "start": 0, "end": 100},
            {"type": "slider", "xAxisIndex": [0, 1, 2], "filterMode": "none",
             "start": 0, "end": 100, "bottom": 15, "height": 28},
        ],
        "series": series,
    }
