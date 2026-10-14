from datetime import date, timedelta

import psxdata
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import mplcursors


# =========================================================
# SETTINGS
# =========================================================

StockName = "FFC"
TotalDays = 40


# =========================================================
# GET CURRENT QUOTE
# =========================================================

quote = psxdata.quote(StockName)

print("Current Quote:")
print(quote)


# =========================================================
# GET HISTORICAL DATA
# =========================================================

df = psxdata.stocks(
    StockName,
    start=date.today() - timedelta(days=TotalDays),
    end=date.today()
)

print("\nOriginal Data:")
print(df)


# =========================================================
# PREPARE DATA
# =========================================================

df["date"] = pd.to_datetime(df["date"])

# Convert columns to numeric
for column in [
    "open",
    "high",
    "low",
    "close",
    "volume"
]:
    df[column] = pd.to_numeric(
        df[column],
        errors="coerce"
    )


# Remove rows with missing data
df = df.dropna(
    subset=[
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]
)


# Sort by date
df = df.sort_values("date")


# Reset index
df = df.reset_index(drop=True)


print("\nData Used For Graph:")
print(df)


# =========================================================
# CREATE TRADING-DAY INDEX
# =========================================================
#
# IMPORTANT:
#
# We DO NOT use the real calendar date as X.
#
# Instead:
#
# 0 = first trading day
# 1 = second trading day
# 2 = third trading day
# ...
#
# This removes weekend/holiday gaps completely.
# =========================================================

df["x"] = range(len(df))


# =========================================================
# CREATE FIGURE
# =========================================================

fig, (ax1, ax2) = plt.subplots(
    2,
    1,
    figsize=(15, 9),
    sharex=True,
    gridspec_kw={
        "height_ratios": [4, 1],
        "hspace": 0.05
    }
)


# Set window title
try:
    fig.canvas.manager.set_window_title(
        StockName + " Candlestick Chart"
    )
except Exception:
    pass


# =========================================================
# CANDLE SETTINGS
# =========================================================

candle_width = 0.65


# =========================================================
# CANDLESTICK CHART
# =========================================================

for i, row in df.iterrows():

    x = row["x"]

    open_price = row["open"]
    high_price = row["high"]
    low_price = row["low"]
    close_price = row["close"]


    # -----------------------------------------------------
    # CANDLE COLOR
    # -----------------------------------------------------

    if close_price >= open_price:
        color = "green"
    else:
        color = "red"


    # -----------------------------------------------------
    # HIGH -> LOW WICK
    # -----------------------------------------------------

    ax1.plot(
        [x, x],
        [low_price, high_price],
        color=color,
        linewidth=1.3,
        zorder=2
    )


    # -----------------------------------------------------
    # OPEN -> CLOSE BODY
    # -----------------------------------------------------

    body_bottom = min(
        open_price,
        close_price
    )

    body_height = abs(
        close_price - open_price
    )


    # Handle same Open and Close
    if body_height == 0:
        body_height = 0.01


    candle = Rectangle(
        (
            x - candle_width / 2,
            body_bottom
        ),
        candle_width,
        body_height,
        facecolor=color,
        edgecolor=color,
        alpha=0.85,
        zorder=3
    )

    ax1.add_patch(candle)


# =========================================================
# PRICE CHART SETTINGS
# =========================================================

ax1.set_ylabel(
    "Price (Rs.)",
    fontsize=11
)


ax1.set_title(
    f"{StockName} - Candlestick + Volume",
    fontsize=16,
    fontweight="bold"
)


ax1.grid(
    True,
    linestyle="--",
    alpha=0.3
)


# =========================================================
# MINIMUM / MAXIMUM VALUES
# =========================================================

for column, label, color in [
    ("open", "Open", "blue"),
    ("high", "High", "darkgreen"),
    ("low", "Low", "darkred"),
    ("close", "Close", "purple")
]:

    # -----------------------------------------------------
    # MINIMUM
    # -----------------------------------------------------

    min_idx = df[column].idxmin()

    min_x = df.loc[
        min_idx,
        "x"
    ]

    min_value = df.loc[
        min_idx,
        column
    ]


    # -----------------------------------------------------
    # MAXIMUM
    # -----------------------------------------------------

    max_idx = df[column].idxmax()

    max_x = df.loc[
        max_idx,
        "x"
    ]

    max_value = df.loc[
        max_idx,
        column
    ]


    # -----------------------------------------------------
    # MINIMUM LABEL
    # -----------------------------------------------------

    ax1.annotate(
        f"{label} Min: {min_value:.2f}",
        xy=(
            min_x,
            min_value
        ),
        xytext=(0, -20),
        textcoords="offset points",
        ha="center",
        fontsize=8,
        color=color,
        arrowprops=dict(
            arrowstyle="->",
            color=color,
            alpha=0.6
        )
    )


    # -----------------------------------------------------
    # MAXIMUM LABEL
    # -----------------------------------------------------

    ax1.annotate(
        f"{label} Max: {max_value:.2f}",
        xy=(
            max_x,
            max_value
        ),
        xytext=(0, 20),
        textcoords="offset points",
        ha="center",
        fontsize=8,
        color=color,
        arrowprops=dict(
            arrowstyle="->",
            color=color,
            alpha=0.6
        )
    )


# =========================================================
# VOLUME CHART
# =========================================================

for i, row in df.iterrows():

    x = row["x"]


    # Green volume if price increased
    if row["close"] >= row["open"]:
        volume_color = "green"
    else:
        volume_color = "red"


    ax2.bar(
        x,
        row["volume"],
        width=candle_width,
        color=volume_color,
        alpha=0.6
    )


# =========================================================
# VOLUME AXIS
# =========================================================

ax2.set_ylabel(
    "Volume",
    fontsize=11
)

ax2.set_xlabel(
    "Date",
    fontsize=11
)

ax2.grid(
    True,
    linestyle="--",
    alpha=0.3
)


# =========================================================
# X-AXIS
# =========================================================

# Use ONLY trading-day indexes
ax2.set_xticks(
    df["x"]
)


# Use actual dates as labels
ax2.set_xticklabels(
    df["date"].dt.strftime("%d %b"),
    rotation=90
)


# =========================================================
# X-AXIS LIMIT
# =========================================================

ax1.set_xlim(
    -0.8,
    len(df) - 0.2
)


# =========================================================
# INTERACTIVE HOVER
# =========================================================

hover_points = ax1.scatter(
    df["x"],
    df["close"],
    s=30,
    alpha=0
)


cursor = mplcursors.cursor(
    hover_points,
    hover=True
)


@cursor.connect("add")
def on_add(sel):

    index = sel.index

    row = df.iloc[index]


    sel.annotation.set_text(
        f"Date: {row['date'].strftime('%d %b %Y')}\n"
        f"Open:  Rs. {row['open']:.2f}\n"
        f"High:  Rs. {row['high']:.2f}\n"
        f"Low:   Rs. {row['low']:.2f}\n"
        f"Close: Rs. {row['close']:.2f}\n"
        f"Volume: {row['volume']:,.0f}"
    )


# =========================================================
# LAYOUT
# =========================================================

plt.tight_layout()

plt.show()
