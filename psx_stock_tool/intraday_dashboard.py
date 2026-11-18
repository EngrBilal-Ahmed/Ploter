"""NiceGUI dashboard preserving the original NumPy plotter's daily-range view."""

import math

from nicegui import run, ui
import pandas as pd

from .config import AppConfig, PRICE_GRAPH_OPTIONS
from .data import get_stock_data, load_stock_list, save_stock_list
from .gui import DEFAULT_STOCKS, parse_stock_symbols
from .intraday import intraday_chart_options, load_intraday_history, session_rows
from .surveillance import SignalConfig


def _columns(names):
    return [
        {"name": key, "field": key, "label": label, "sortable": True, "align": "left"}
        for key, label in names
    ]


async def build_intraday_dashboard(database_file):
    saved = await run.io_bound(load_stock_list, database_file)
    stocks = parse_stock_symbols(saved if saved is not None else DEFAULT_STOCKS)
    stock_text = ", ".join(stocks)
    circuit_defaults = SignalConfig()
    active = {"frames": {}, "busy": False, "circuit_config": circuit_defaults}
    ui.colors(primary="#2563eb", secondary="#0f766e", accent="#f97316")
    ui.add_head_html(
        "<style>body{background:#f1f5f9}"
        ".q-card{border:1px solid #e2e8f0;box-shadow:none}"
        "@supports(field-sizing:content){.intraday-stock-list textarea{"
        "field-sizing:content;height:auto!important}}</style>"
    )

    with ui.column().classes("w-full min-w-0 p-2 md:p-3 gap-4"):
        with ui.row().classes("items-center gap-3"):
            ui.icon("query_stats", size="36px").classes("text-primary")
            ui.label("PSX Intraday Range Dashboard").classes("text-3xl font-bold text-slate-800")
        ui.label(
            "Switch between the original OHLC lines and circuit candlesticks, with daily range and aligned volume. "
            "Daily candles are not live ticks or minute-by-minute trades."
        ).classes("text-slate-600")
        ui.label(f"Shared SQLite cache: {database_file}").classes("text-xs text-slate-500")

        with ui.card().classes("w-full rounded-xl p-5 gap-3"):
            ui.label("Stocks and graph settings").classes("text-lg font-semibold")
            stock_input = ui.textarea(
                "Stock list", value=stock_text,
                placeholder="Paste comma-separated symbols or cells from Excel",
            ).props("rows=1 autogrow").classes("intraday-stock-list w-full")
            apply_stocks = ui.button("Apply stock list", icon="checklist").props("outline")
            selected = ui.select(
                stocks, value=stocks.copy(), multiple=True, with_input=True,
                label="Stocks to compare",
            ).props("use-chips").classes("w-full")
            with ui.row().classes("items-start gap-5"):
                history_days = ui.number(
                    "History (calendar days)", value=120, min=1, step=1, format="%.0f",
                ).classes("w-48")
                refresh_days = ui.number(
                    "Refresh recent days", value=5, min=0, step=1, format="%.0f",
                ).classes("w-48")
                with ui.column().classes("gap-1"):
                    ui.label("Price chart").classes("text-sm text-slate-600")
                    price_view = ui.radio(
                        {"line": "Line graph", "candlestick": "OHLC candlesticks"}, value="line",
                    ).props("inline")
                with ui.column().classes("gap-1") as line_settings:
                    ui.label("Price lines").classes("text-sm text-slate-600")
                    with ui.row().classes("gap-2"):
                        price_lines = {
                            key: ui.checkbox(label, value=key in "LH")
                            for key, label in (("O", "Open"), ("H", "High"), ("L", "Low"), ("C", "Close"))
                        }
            with ui.row().classes("gap-3"):
                cached_button = ui.button("Show cached charts", icon="show_chart")
                fetch_button = ui.button("Fetch / update selected", icon="sync").props("outline")
            ui.label(
                "Cached charts end at each stock's latest stored session, not necessarily today. "
                "Fetch uses Numpy.py's missing-history and recent-refresh workflow through today. "
                "No automatic API requests."
            ).classes("text-sm text-slate-500")

        with ui.expansion(
            "Candlestick circuit settings and limitations", icon="tune",
        ).classes("w-full bg-white rounded") as circuit_settings:
            with ui.row().classes("items-end gap-4"):
                limit_pct = ui.number(
                    "Circuit (%)", value=circuit_defaults.limit_pct, min=0.1, max=99, step=0.5,
                )
                min_move = ui.number(
                    "Minimum move (Rs)", value=circuit_defaults.min_move, min=0.01, step=0.01,
                )
                apply_circuits = ui.button("Apply circuit settings", icon="tune")
            ui.label(
                "Same indicative rules as PumpDashboard: LDCP +/- max(minimum move, LDCP x circuit %), "
                "within Rs 0.02. Dashed lines are upper/lower estimates; orange/purple circles mark "
                "upper/lower touches. Missing/nonpositive LDCP falls back to the preceding cached close; "
                "without a usable reference no limit is inferred. The selected rule applies to all displayed "
                "history, not historical PSX rule changes or special scrip rules. Corporate actions may "
                "distort estimates. Daily candles cannot confirm an order-book lock or trade sequence."
            ).classes("text-sm text-amber-800")
        circuit_settings.set_visibility(False)

        status = ui.label("Loading cached history...").classes("text-slate-700")
        errors = ui.textarea("Fetch errors / empty responses").props("readonly").classes("w-full")
        errors.set_visibility(False)
        watch = ui.table(
            columns=_columns([
                ("symbol", "Symbol"), ("date", "Last stored session"), ("sessions", "Sessions"),
                ("close", "Close (Rs.)"), ("intraday_range_percent", "Last range %"),
                ("max_range", "Max range %"), ("average_range", "Average range %"),
                ("volume", "Last volume"),
            ]), rows=[], row_key="symbol", selection="single", pagination=10,
        ).classes("w-full")
        with ui.row().classes("w-full items-end gap-4"):
            selection = ui.select([], label="Stock to chart", with_input=True).classes("w-64")
            reset_zoom = ui.button("Reset linked zoom", icon="zoom_out_map").props("outline")
        detail = ui.label("No stock selected.").classes("text-lg font-semibold")
        ui.label(
            "Scroll over any panel to zoom; drag to pan, or use the bottom slider. "
            "Price, intraday range and volume always share the same date range and hover guide."
        ).classes("text-sm text-slate-600")
        with ui.card().classes("w-full p-0"):
            chart = ui.echart({}).classes("w-full").style("height: 900px")
        ui.label(
            "Intraday range (%) = (High - Low) / Low x 100, as in Numpy.py. "
            "A zero low has no defined percentage. Missing volume stays blank, not zero. "
            "The range does not establish trade order, achievable profit, or a buy/sell signal."
        ).classes("text-sm text-amber-800")
        ui.label("Daily session details (newest first)").classes("text-lg font-semibold")
        sessions = ui.table(
            columns=_columns([
                ("date", "Date"), ("open", "Open"), ("high", "High"), ("low", "Low"),
                ("close", "Close"), ("daily_range", "High - Low (Rs.)"),
                ("intraday_range_percent", "Intraday range %"), ("volume", "Volume"),
            ]), rows=[], row_key="date", pagination=15,
        ).classes("w-full")

    controls = [
        stock_input, apply_stocks, selected, history_days, refresh_days,
        cached_button, fetch_button, selection, price_view, limit_pct, min_move,
        apply_circuits, *price_lines.values(),
    ]

    def set_busy(busy):
        active["busy"] = busy
        for control in controls:
            control.set_enabled(not busy)

    def normalize_stock_list():
        symbols = parse_stock_symbols(stock_input.value or "")
        stock_input.set_value(", ".join(symbols))
        return symbols

    def flatten_stock_list(event):
        text = event.value or ""
        if any(delimiter in text for delimiter in ("\r", "\n", "\t")):
            symbols = normalize_stock_list()
            if symbols and text.endswith(("\r", "\n", "\t")):
                stock_input.set_value(stock_input.value + ", ")
            stock_input.update()  # Input events disable automatic value loopback.

    def read_config():
        symbols = list(selected.value or [])
        if not symbols:
            raise ValueError("Select at least one stock.")
        history, refresh = float(history_days.value), float(refresh_days.value)
        if not history.is_integer() or history < 1:
            raise ValueError("History must be a whole number of at least 1.")
        if not refresh.is_integer() or refresh < 0:
            raise ValueError("Refresh days must be a whole number of at least 0.")
        config = AppConfig(
            stock_names=symbols, total_days=int(history), refresh_days=int(refresh),
            price_graphs="".join(key for key, checkbox in price_lines.items() if checkbox.value),
            database_file=database_file,
        )
        circuit_config = (
            SignalConfig(limit_pct=float(limit_pct.value), min_move=float(min_move.value))
            if price_view.value == "candlestick" else active["circuit_config"]
        )
        return config, circuit_config

    def rounded_rows(rows):
        return [
            {key: round(value, 2) if isinstance(value, float) else value for key, value in row.items()}
            for row in rows
        ]

    def show_symbol(*, preserve_zoom=False):
        symbol = selection.value
        frame = active["frames"].get(symbol)
        previous_zoom = chart.options.get("dataZoom", [])
        zoom_values = {
            key: previous_zoom[0][key] for key in ("start", "end")
        } if preserve_zoom and previous_zoom else {}
        chart.options.clear()
        line_settings.set_visibility(price_view.value == "line")
        circuit_settings.set_visibility(price_view.value == "candlestick")
        if frame is None:
            detail.set_text("No cached history for the selected stocks. Fetch data or change the stock list.")
            sessions.rows = []
            watch.selected = []
        else:
            graphs = [
                PRICE_GRAPH_OPTIONS[key]
                for key, checkbox in price_lines.items() if checkbox.value
            ]
            chart.options.update(intraday_chart_options(frame, graphs, price_view=price_view.value))
            for zoom in chart.options["dataZoom"]:
                zoom.update(zoom_values)
            latest = frame.iloc[-1]
            detail.set_text(
                f"{symbol} | {frame.iloc[0]['date']:%Y-%m-%d} to {latest['date']:%Y-%m-%d} | "
                f"{len(frame)} sessions | Close Rs. {latest['close']:.2f} | cached daily data"
            )
            sessions.rows = rounded_rows(session_rows(frame.iloc[::-1]))
            watch.selected = [row for row in watch.rows if row["symbol"] == symbol]
        chart.update()
        sessions.update()
        watch.update()

    async def read_cache(config, circuit_config):
        frames = await run.io_bound(
            load_intraday_history, database_file, config.stock_names, config.total_days, circuit_config,
        )
        active["frames"] = frames
        active["circuit_config"] = circuit_config
        rows = []
        for symbol, frame in frames.items():
            latest = session_rows(frame.tail(1))[0]
            rows.append({
                "symbol": symbol, "sessions": len(frame), **latest,
                **{
                    key: float(value) if pd.notna(value) and math.isfinite(value) else None
                    for key, value in (
                        ("max_range", frame["intraday_range_percent"].max()),
                        ("average_range", frame["intraday_range_percent"].mean()),
                    )
                },
            })
        watch.rows = rounded_rows(rows)
        selection.options = sorted(frames)
        selection.set_value(
            selection.value if selection.value in frames else next(iter(selection.options), None)
        )
        selection.update()
        show_symbol()
        missing = [symbol for symbol in config.stock_names if symbol not in frames]
        return (
            f"Loaded cached history for {len(frames)} stock(s)."
            + (f" No stored data: {', '.join(missing)}." if missing else "")
            + " Check the last stored session dates before trading."
        )

    def clear_view():
        active["frames"] = {}
        watch.rows = []
        selection.options = []
        selection.set_value(None)
        selection.update()
        show_symbol()

    async def load_selected():
        if active["busy"]:
            return
        try:
            config, circuit_config = read_config()
        except (TypeError, ValueError) as error:
            ui.notify(str(error), type="negative")
            return
        set_busy(True)
        status.set_text("Reading SQLite; no API requests...")
        try:
            status.set_text(await read_cache(config, circuit_config))
        except Exception as error:
            clear_view()
            status.set_text(f"Could not load cached history: {error}")
            ui.notify(str(error), type="negative")
        finally:
            set_busy(False)

    async def fetch_selected():
        if active["busy"]:
            return
        try:
            config, circuit_config = read_config()
        except (TypeError, ValueError) as error:
            ui.notify(str(error), type="negative")
            return
        set_busy(True)
        errors.set_visibility(False)
        failures = []
        try:
            for index, symbol in enumerate(config.stock_names, 1):
                status.set_text(f"Fetching {symbol} ({index}/{len(config.stock_names)})...")
                try:
                    frame = await run.io_bound(
                        get_stock_data, symbol, config, raise_on_download_error=True,
                    )
                    if frame.empty:
                        failures.append(f"{symbol}: no candles in the requested period; older cache may be shown.")
                except Exception as error:
                    failures.append(f"{symbol}: {error}")
            if failures:
                errors.set_value("\n".join(failures))
                errors.set_visibility(True)
                ui.notify("Some data could not be fetched; see errors and cached session dates.", type="warning")
            message = await read_cache(config, circuit_config)
            status.set_text(
                (f"Fetch finished with {len(failures)} error(s) / empty result(s). "
                 if failures else "Fetch finished. ") + message
            )
        except Exception as error:
            clear_view()
            status.set_text(f"Could not reload history after fetching: {error}")
            ui.notify(str(error), type="negative")
        finally:
            set_busy(False)

    async def apply_stock_list():
        if active["busy"]:
            return
        set_busy(True)
        try:
            symbols = normalize_stock_list()
            await run.io_bound(save_stock_list, stock_input.value or "", database_file)
            selected.set_options(symbols, value=symbols.copy())
            if symbols:
                status.set_text(await read_cache(*read_config()))
            else:
                clear_view()
                status.set_text("Enter at least one stock symbol and apply the stock list.")
        except Exception as error:
            clear_view()
            status.set_text(f"Could not apply stock list: {error}")
            ui.notify(str(error), type="negative")
        finally:
            set_busy(False)

    stock_input.on_value_change(flatten_stock_list)
    stock_input.on("blur", normalize_stock_list)
    selection.on_value_change(show_symbol)
    price_view.on_value_change(lambda: show_symbol(preserve_zoom=True))

    def remember_zoom(event):
        payload = (event.args.get("batch") or [event.args])[0]
        for zoom in chart.options.get("dataZoom", []):
            zoom.update({key: payload[key] for key in ("start", "end") if key in payload})

    chart.on("chart:datazoom", remember_zoom, args=["start", "end", "batch"])
    watch.on(
        "selection",
        lambda event: selection.set_value(event.args["rows"][0]["symbol"])
        if event.args.get("rows") and event.args.get("added") else None,
    )
    for checkbox in price_lines.values():
        checkbox.on_value_change(show_symbol)
    reset_zoom.on_click(lambda: chart.run_chart_method(
        "dispatchAction", {"type": "dataZoom", "start": 0, "end": 100},
    ))
    apply_stocks.on_click(apply_stock_list)
    cached_button.on_click(load_selected)
    fetch_button.on_click(fetch_selected)
    apply_circuits.on_click(load_selected)
    if stocks:
        await load_selected()
    else:
        status.set_text("Enter at least one stock symbol and apply the stock list.")


def main(database_file, *, port=8082, show=True):
    async def page():
        await build_intraday_dashboard(database_file)

    ui.run(page, title="PSX Intraday Range Dashboard", host="127.0.0.1",
           port=port, reload=False, show=show)
