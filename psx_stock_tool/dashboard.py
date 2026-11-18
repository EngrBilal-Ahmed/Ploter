"""Separate NiceGUI dashboard for eligible-stock circuit and volume surveillance."""

from datetime import date
from queue import Empty, SimpleQueue

from nicegui import run, ui
import pandas as pd

from .charting import circuit_price_series
from .dashboard_sync import sync_eligible_prices
from .surveillance import SignalConfig, load_dashboard_overview, load_symbol_analysis


def chart_options(frame):
    """Build JSON-safe daily prices, volume bars and monthly circuit histograms."""
    days = frame["date"].dt.strftime("%Y-%m-%d").tolist()

    def values(column):
        return [None if pd.isna(value) else float(value) for value in frame[column]]

    common = {
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 65, "right": 30, "bottom": 75},
        "xAxis": {"type": "category", "data": days},
        "yAxis": {"type": "value", "scale": True},
        "dataZoom": [{"type": "inside"}, {"type": "slider"}],
    }
    price_series = circuit_price_series(frame)
    prices = {
        **common, "legend": {"data": [series["name"] for series in price_series]},
        "series": price_series,
    }
    volume = {
        **common, "legend": {"data": ["Volume", "Prior-session average"]},
        "series": [
            {"name": "Volume", "type": "bar", "data": [
                {"value": value, "itemStyle": {"color": "#f97316" if spike else "#64748b"}}
                for value, spike in zip(values("volume"), frame["volume_spike"])
            ]},
            {"name": "Prior-session average", "type": "line", "showSymbol": False,
             "data": values("volume_average")},
        ],
    }
    monthly = frame.set_index("date")[
        ["upper_close", "lower_close", "upper_retreat", "lower_rebound"]
    ].resample("MS").sum()
    histogram = {
        "tooltip": {"trigger": "axis"}, "legend": {},
        "grid": {"left": 50, "right": 20, "bottom": 65},
        "xAxis": {"type": "category", "data": monthly.index.strftime("%Y-%m").tolist()},
        "yAxis": {"type": "value", "minInterval": 1},
        "dataZoom": [{"type": "slider"}],
        "series": [
            {"name": name, "type": "bar", "data": monthly[column].astype(int).tolist()}
            for name, column in (
                ("Upper circuit closes", "upper_close"), ("Lower circuit closes", "lower_close"),
                ("Upper touch / close away", "upper_retreat"),
                ("Lower touch / close away", "lower_rebound"),
            )
        ],
    }
    return prices, volume, histogram


def _columns(names):
    return [
        {"name": key, "field": key, "label": label, "sortable": True, "align": "left"}
        for key, label in names
    ]


def _whole_number(value, label, minimum):
    number = float(value)
    if not number.is_integer() or number < minimum:
        raise ValueError(f"{label} must be a whole number of at least {minimum}")
    return int(number)


async def build_dashboard(database_file, eligible_file):
    ui.colors(primary="#2563eb")
    ui.add_head_html("<style>body{background:#f1f5f9}.q-card{border:1px solid #e2e8f0;box-shadow:none}</style>")
    active = {"config": SignalConfig(), "overview": None, "generation": 0}
    messages = SimpleQueue()

    with ui.column().classes("w-full max-w-screen-2xl mx-auto p-5 gap-4"):
        ui.label("PSX Circuit & Volume Watch").classes("text-3xl font-bold text-slate-800")
        ui.label(
            "Research signals, not proof of manipulation or predictions. Daily candles cannot "
            "confirm an order-book lock or the intraday sequence of a circuit break."
        ).classes("text-amber-800")
        ui.label(f"Eligibility: {eligible_file} | Cache: {database_file}").classes("text-xs text-slate-500")
        with ui.card().classes("w-full"):
            ui.label("Data synchronization").classes("text-lg font-semibold")
            with ui.row().classes("items-end gap-4"):
                history_start = ui.input("Earliest history date", value="2017-01-01").props("type=date")
                refresh_days = ui.number("Refresh past X calendar days", value=0, min=0, step=1, format="%.0f")
                fetch_button = ui.button("Fetch all missing dates", icon="sync")
                reload_button = ui.button("Reload analysis", icon="refresh").props("outline")
            ui.label(
                "Only XML-eligible stocks are downloaded and analyzed. Old non-eligible rows are retained. "
                "A missing interior session can trigger an overlapping range upsert; successful empty "
                "ranges are remembered. Use refresh days to recheck recent data."
            ).classes("text-sm text-slate-500")
            status = ui.label("Loading cached history; no automatic API requests.")
            fetch_status = ui.label("").classes("text-sm")
            errors = ui.textarea("Synchronization errors").props("readonly").classes("w-full")
            errors.set_visibility(False)
        with ui.expansion("Screening settings and limitations", icon="tune").classes("w-full bg-white rounded"):
            with ui.row().classes("gap-4"):
                limit_pct = ui.number("Circuit (%)", value=10, min=0.1, max=99, step=0.5)
                min_move = ui.number("Minimum move (Rs)", value=1, min=0.01, step=0.01)
                volume_window = ui.number("Prior volume sessions", value=20, min=2, step=1, format="%.0f")
                volume_multiple = ui.number("Volume spike multiple", value=3, min=1.1, step=0.1)
                apply_button = ui.button("Apply screening settings")
            ui.label(
                "Indicative limits = LDCP +/- max(minimum move, LDCP x circuit %), within Rs 0.02. "
                "The selected rule applies to the whole displayed history, NOT historical PSX rule changes "
                "or special scrip rules. Corporate actions/adjusted prices may distort comparisons. "
                "Volume uses only preceding sessions (no look-ahead); a complete baseline is required. "
                "Streaks reset across missing observed market sessions. Labels use daily evidence only."
            ).classes("text-sm text-slate-600")
        stats = ui.label("").classes("text-base font-semibold")
        missing = ui.label("").classes("text-sm text-slate-600")
        with ui.row().classes("w-full items-center gap-4"):
            search = ui.input("Search symbol or company").classes("w-80")
            signals_only = ui.switch("Signals only", value=False)
        watch = ui.table(
            columns=_columns([
                ("symbol", "Symbol"), ("name", "Company"), ("date", "Last candle"),
                ("stale", "Behind market data"), ("signal", "Latest evidence"), ("close", "Close"),
                ("change_pct", "Change %"), ("volume_ratio", "Volume x avg"),
                ("upper_streak", "Upper run now"), ("max_upper_run", "Max upper run"),
                ("max_lower_run", "Max lower run"), ("upper_closes", "Upper closes"),
                ("lower_closes", "Lower closes"), ("sessions", "Stored sessions"),
            ]), rows=[], row_key="symbol", selection="single", pagination=15,
        ).classes("w-full")
        ui.label("Select a row or choose a symbol below. Blank volume ratios mean insufficient/zero baseline.")
        with ui.row().classes("items-end"):
            selection = ui.select({}, label="Eligible stock", with_input=True).classes("w-96")
            chart_days = ui.number("Chart calendar days", value=365, min=1, step=1, format="%.0f")
            chart_button = ui.button("Show history")
        detail = ui.label("No stock selected.").classes("text-lg font-semibold")
        price_chart = ui.echart({}).classes("w-full h-96")
        ui.label("Volume histogram (orange = elevated versus preceding-session baseline)")
        volume_chart = ui.echart({}).classes("w-full h-64")
        ui.label("Monthly circuit-close and touch/retreat counts")
        history_chart = ui.echart({}).classes("w-full h-64")
        ui.label("Recent circuit / volume events").classes("text-lg font-semibold")
        events = ui.table(
            columns=_columns([
                ("date", "Date"), ("signal", "Evidence"), ("close", "Close"),
                ("volume_ratio", "Volume x avg"), ("upper_streak", "Upper run"),
                ("lower_streak", "Lower run"), ("upper_retreat", "Upper touch / close away"),
                ("lower_rebound", "Lower touch / close away"),
            ]), rows=[], row_key="date", pagination=10,
        ).classes("w-full")

    controls = [fetch_button, reload_button, apply_button, history_start, refresh_days,
                limit_pct, min_move, volume_window, volume_multiple]

    def set_busy(busy):
        for control in controls:
            control.set_enabled(not busy)

    def drain_progress():
        while True:
            try:
                fetch_status.set_text(messages.get_nowait())
            except Empty:
                break

    ui.timer(0.25, drain_progress)

    def update_watch():
        overview = active["overview"]
        if overview is None:
            return
        query = (search.value or "").strip().upper()
        watch.rows = [
            {key: round(value, 3) if isinstance(value, float) else value for key, value in row.items()}
            for row in overview["rows"]
            if (not signals_only.value or row["priority"] > 0)
            and (query in row["symbol"] or query in row["name"].upper())
        ]
        watch.update()

    def clear_details(message):
        detail.set_text(message)
        for chart in (price_chart, volume_chart, history_chart):
            chart.options.clear()
            chart.update()
        events.rows = []
        events.update()

    async def show_symbol():
        symbol = selection.value
        if not symbol:
            return
        active["generation"] += 1
        generation = active["generation"]
        try:
            days = _whole_number(chart_days.value, "Chart days", 1)
            frame = await run.io_bound(
                load_symbol_analysis, database_file, symbol, eligible_file, active["config"]
            )
            if generation != active["generation"]:
                return
            if frame.empty:
                raise ValueError(f"No stored history for {symbol}; fetch missing data first")
            latest = frame["date"].max()
            shown = frame.loc[frame["date"] >= latest - pd.Timedelta(days=days - 1)]
            for chart, options in zip((price_chart, volume_chart, history_chart), chart_options(shown)):
                chart.options.clear()
                chart.options.update(options)
                chart.update()
            detail.set_text(
                f"{symbol} | through {latest:%Y-%m-%d} | {frame.iloc[-1]['signal']} | "
                f"max upper/lower runs: {frame['upper_streak'].max()}/{frame['lower_streak'].max()}"
            )
            flagged = shown.loc[
                shown["upper_touch"] | shown["lower_touch"] | shown["volume_spike"]
                | shown["priority"].gt(0)
            ].tail(200).sort_values("date", ascending=False).copy()
            flagged["date"] = flagged["date"].dt.strftime("%Y-%m-%d")
            flagged = flagged[["date", "signal", "close", "volume_ratio", "upper_streak",
                               "lower_streak", "upper_retreat", "lower_rebound"]]
            events.rows = flagged.astype(object).where(pd.notna(flagged), None).to_dict("records")
            events.update()
        except Exception as error:
            clear_details(f"Could not load {symbol}: {error}")
            ui.notify(str(error), type="negative")

    async def reload_analysis():
        set_busy(True)
        status.set_text("Analyzing eligible cached history...")
        try:
            config = SignalConfig(
                limit_pct=float(limit_pct.value), min_move=float(min_move.value),
                volume_window=_whole_number(volume_window.value, "Volume sessions", 2),
                volume_multiple=float(volume_multiple.value),
            )
            overview = await run.io_bound(load_dashboard_overview, database_file, eligible_file, config)
            active.update(config=config, overview=overview)
            update_watch()
            stats.set_text(
                f"{len(overview['stocks'])} eligible | {len(overview['rows'])} with history | "
                f"{sum(row['priority'] > 0 and not row['stale'] for row in overview['rows'])} "
                f"signals on newest market session | "
                f"{sum(row['stale'] for row in overview['rows'])} older histories | "
                f"Newest cached eligible session: {overview['latest'] or 'none'}"
            )
            missing.set_text(
                f"{len(overview['missing'])} eligible stocks without stored history: "
                + ", ".join(overview["missing"][:25])
                + (" ..." if len(overview["missing"]) > 25 else "")
            )
            selection.options = {row["symbol"]: f"{row['symbol']} - {row['name']}" for row in overview["rows"]}
            if selection.value not in selection.options:
                selection.set_value(next(iter(selection.options), None))
            selection.update()
            await show_symbol()
            status.set_text("Analysis loaded from SQLite. See each row's last candle date before using a signal.")
        except Exception as error:
            active["overview"] = None
            active["generation"] += 1
            watch.rows = []
            watch.update()
            selection.options = {}
            selection.set_value(None)
            selection.update()
            stats.set_text("Analysis unavailable")
            missing.set_text("")
            clear_details("Reload valid eligible history and settings to view charts.")
            status.set_text(f"Analysis failed: {error}")
            ui.notify(str(error), type="negative")
        finally:
            set_busy(False)

    async def fetch_missing():
        set_busy(True)
        errors.set_visibility(False)
        status.set_text("Synchronizing eligible stocks; this may take time on the first run.")
        try:
            refresh = _whole_number(refresh_days.value, "Refresh days", 0)
            result = await run.io_bound(
                sync_eligible_prices, database_file, eligible_file,
                history_start=date.fromisoformat(history_start.value),
                refresh_days=refresh, progress=messages.put,
            )
            drain_progress()
            fetch_status.set_text(
                f"Requested: {result.requested}; no request needed: {result.unchanged}; "
                f"failed: {len(result.failures)}. {result.prices}"
            )
            if result.failures:
                errors.set_value("\n".join(f"{symbol}: {error}" for symbol, error in result.failures.items()))
                errors.set_visibility(True)
                ui.notify("Partial sync: some stocks failed. See synchronization errors.", type="warning")
            ui.timer(0.1, reload_analysis, once=True)
        except Exception as error:
            status.set_text(f"Synchronization failed: {error}")
            ui.notify(str(error), type="negative")
        finally:
            set_busy(False)

    search.on_value_change(update_watch)
    signals_only.on_value_change(update_watch)
    watch.on("selection", lambda event: selection.set_value(event.args["rows"][0]["symbol"])
             if event.args.get("rows") and event.args.get("added") else None)
    selection.on_value_change(show_symbol)
    chart_button.on_click(show_symbol)
    reload_button.on_click(reload_analysis)
    apply_button.on_click(reload_analysis)
    fetch_button.on_click(fetch_missing)
    await reload_analysis()


def main(database_file, eligible_file, *, port=8081, show=True):
    async def page():
        await build_dashboard(database_file, eligible_file)

    ui.run(page, title="PSX Circuit & Volume Watch", host="127.0.0.1", port=port,
           reload=False, show=show)
