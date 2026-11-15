import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import mplcursors
import pandas as pd

from .data import calculate_intraday_ranges, get_stock_data


def maximize_figure(fig):
    """Open the graph maximized on supported Matplotlib GUI backends."""
    window = getattr(fig.canvas.manager, "window", None)
    if window is None:
        return

    try:
        window.showMaximized()
    except AttributeError:
        try:
            window.state("zoomed")
        except AttributeError:
            pass


def plot_stock(stock_name, config):
    """Fetch and plot price data plus the intraday percentage range."""
    df = get_stock_data(stock_name, config)

    if df.empty:
        print(f"No valid price data was returned for {stock_name}.")
        return

    fig, (ax_price, ax_range) = plt.subplots(
        2,
        1,
        figsize=(14, 9),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    fig.canvas.manager.set_window_title(f"{stock_name} Graph")
    maximize_figure(fig)

    price_graphs = config.selected_price_graphs()
    for column, label in price_graphs:
        ax_price.plot(df["date"], df[column], marker="o", label=label)

        min_idx = df[column].idxmin()
        max_idx = df[column].idxmax()
        ax_price.annotate(
            f"{label} Min: {df.loc[min_idx, column]:.2f}",
            (df.loc[min_idx, "date"], df.loc[min_idx, column]),
            xytext=(0, -15),
            textcoords="offset points",
            ha="center",
        )
        ax_price.annotate(
            f"{label} Max: {df.loc[max_idx, column]:.2f}",
            (df.loc[max_idx, "date"], df.loc[max_idx, column]),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
        )

    ax_price.set_ylabel("Price (Rs.)")
    ax_price.grid(True)
    if price_graphs:
        ax_price.legend(loc="upper right")

    cursor = mplcursors.cursor(ax_price.lines, hover=True) if ax_price.lines else None

    if cursor is not None:

        @cursor.connect("add")
        def on_add(selection):
            _, value = selection.target
            selection.annotation.set_text(f"Value: {value:.2f}")

    df = calculate_intraday_ranges(df)
    min_range_percent = df["intraday_range_percent"].min()
    max_range_percent = df["intraday_range_percent"].max()
    range_bars = ax_range.bar(
        df["date"],
        df["intraday_range_percent"],
        width=0.8,
        color="steelblue",
        label=f"Range (min {min_range_percent:.2f}%, max {max_range_percent:.2f}%)",
    )
    ax_range.bar_label(
        range_bars,
        labels=[
            f"{range_percent:.2f}%" #\n{day_range:.1f}"
            for range_percent, day_range in zip(
                df["intraday_range_percent"], df["daily_range"]
            )
        ],
        padding=3,
        fontsize=8,
    )
    ax_range.set_xlabel("Date")
    ax_range.set_ylabel("Intraday Range")
    ax_range.grid(True, axis="y")
    ax_range.legend(loc="upper right")

    frequency = "1D" if config.total_days <= 120 else "ME"
    date_ticks = pd.date_range(df["date"].min(), df["date"].max(), freq=frequency)
    ax_range.set_xticks(date_ticks)
    ax_range.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax_range.tick_params(axis="x", rotation=90)

    selected_labels = ", ".join(label for _, label in price_graphs) or "No Price Lines"
    fig.suptitle(f"{stock_name} - {selected_labels} & Daily Range")
    fig.tight_layout()


def plot_stocks(config):
    """Create one graph for each configured stock and show all windows."""
    for stock_name in config.stock_names:
        plot_stock(stock_name, config)

    plt.show()
