"""Modern NiceGUI interface for choosing PSX stocks and graph settings."""

from __future__ import annotations

import re

from nicegui import ui

from .config import AppConfig
from .data import load_stock_list, save_stock_list
from .plotting import plot_stocks


DEFAULT_STOCKS = "AVN, BNL, UNITY"


def parse_stock_symbols(text: str) -> list[str]:
    """Parse symbols pasted from a comma list, TSV data, or an Excel sheet."""
    symbols = []
    seen = set()
    for value in re.split(r"[,\t\r\n]+", text):
        symbol = value.strip().upper()
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
    return symbols


def build_page() -> None:
    """Create the stock-selection page."""
    selected_stocks: dict[str, ui.checkbox] = {}
    database_file = AppConfig().database_file
    saved_stock_list = load_stock_list(database_file)

    ui.colors(primary="#2563eb", secondary="#0f766e", accent="#f97316")
    ui.add_head_html("""
        <style>
            body { background: #f8fafc; }
            .page-shell { max-width: 1080px; margin: 0 auto; }
            .stock-card { border: 1px solid #e2e8f0; box-shadow: 0 10px 30px rgba(15, 23, 42, .07); }
            .stock-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(145px, 1fr)); gap: 8px; }
        </style>
    """)

    with ui.column().classes("page-shell w-full gap-5 p-4 md:p-8"):
        with ui.row().classes("items-center gap-3"):
            ui.icon("show_chart", size="36px").classes("text-primary")
            with ui.column().classes("gap-0"):
                ui.label("PSX Stock Plotter").classes("text-3xl font-bold text-slate-800")

        with ui.card().classes("stock-card w-full rounded-xl p-5"):
            ui.label("Stocks List").classes("text-lg font-semibold")
            stock_input = ui.textarea(
                value=saved_stock_list if saved_stock_list is not None else DEFAULT_STOCKS,
                placeholder="e.g. OGDC, FFC, SYS",
            ).classes("w-full mt-1")

            with ui.row().classes("items-center gap-3 mt-1"):
                stock_count = ui.label("3 stocks ready").classes("text-slate-500")

                with ui.row().classes("ml-auto gap-2"):
                    create_button = ui.button("Create stock checkboxes", icon="checklist").props("unelevated")

        with ui.card().classes("stock-card w-full rounded-xl p-5"):
            with ui.row().classes("w-full items-center"):
                ui.label("Stocks to plot").classes("text-lg font-semibold")
                with ui.row().classes("ml-auto gap-2"):
                    select_all_button = ui.button("Select all", icon="done_all").props("flat")
                    uncheck_all_button = ui.button("Uncheck all", icon="remove_done").props("flat")
            ui.separator().classes("my-3")
            checkboxes = ui.element("div").classes("stock-grid w-full max-h-64 overflow-y-auto pr-2")

        with ui.card().classes("stock-card w-full rounded-xl p-5"):
            ui.label("Graph settings").classes("text-lg font-semibold mb-3")
            with ui.row().classes("w-full items-start gap-5"):
                history_days = ui.number("History (days)", value=120, min=1, step=1, format="%.0f").classes("w-40")
                refresh_days = ui.number("Refresh recent days", value=5, min=0, step=1, format="%.0f").classes("w-48")
                with ui.column().classes("gap-1"):
                    ui.label("Price lines").classes("text-sm text-slate-600")
                    with ui.row().classes("gap-2"):
                        price_lines = {key: ui.checkbox(label, value=key in "LH") for key, label in (("O", "Open"), ("H", "High"), ("L", "Low"), ("C", "Close"))}

        status = ui.label("Choose stocks and click Plot selected stocks.").classes("text-slate-500")

        def create_stock_checkboxes() -> None:
            save_stock_list(stock_input.value or "", database_file)
            symbols = parse_stock_symbols(stock_input.value or "")
            selected_stocks.clear()
            checkboxes.clear()
            with checkboxes:
                for symbol in symbols:
                    selected_stocks[symbol] = ui.checkbox(symbol, value=True)
            stock_count.set_text(f"{len(symbols)} stock(s) ready" if symbols else "No stocks found")
            status.set_text("Choose stocks and click Plot selected stocks." if symbols else "Enter at least one stock symbol.")

        def select_all() -> None:
            for checkbox in selected_stocks.values():
                checkbox.set_value(True)

        def uncheck_all() -> None:
            for checkbox in selected_stocks.values():
                checkbox.set_value(False)

        def plot_selected() -> None:
            symbols = [symbol for symbol, checkbox in selected_stocks.items() if checkbox.value]
            if not symbols:
                ui.notify("Select at least one stock to plot.", type="warning")
                return
            try:
                total_days = int(history_days.value)
                recent_days = int(refresh_days.value)
                if total_days < 1 or recent_days < 0:
                    raise ValueError
            except (TypeError, ValueError):
                ui.notify("History must be at least 1 and refresh days cannot be negative.", type="negative")
                return

            config = AppConfig(
                stock_names=symbols,
                total_days=total_days,
                price_graphs="".join(key for key, checkbox in price_lines.items() if checkbox.value),
                refresh_days=recent_days,
            )
            status.set_text("Downloading data and opening graph windows...")
            try:
                plot_stocks(config)
                status.set_text("Graph windows closed.")
            except Exception as error:
                status.set_text("Could not create the graphs.")
                ui.notify(f"Plotting error: {error}", type="negative")

        create_button.on("click", create_stock_checkboxes)
        stock_input.on("blur", lambda: save_stock_list(stock_input.value or "", database_file))
        select_all_button.on("click", select_all)
        uncheck_all_button.on("click", uncheck_all)
        ui.button("Plot selected stocks", icon="show_chart", on_click=plot_selected).props("unelevated").classes("self-end")

        create_stock_checkboxes()


def main() -> None:
    ui.run(
        build_page,
        title="PSX Stock Plotter",
        favicon="📈",
        host="0.0.0.0",
        port=8080,
        reload=False,
    )
