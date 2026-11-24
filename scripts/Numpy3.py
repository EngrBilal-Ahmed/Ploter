from datetime import date, timedelta
import psxdata
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import mplcursors

StockName = "EFERT"
TotalDays = 365
quote = psxdata.quote(StockName)
print(quote)

df = psxdata.stocks(
    StockName,
    start=date.today() - timedelta(days=TotalDays),
    end=date.today()
)

print(df)

df["date"] = pd.to_datetime(df["date"])
df = df.sort_values("date")
fig, ax1 = plt.subplots(figsize=(14, 7))
fig.canvas.manager.set_window_title(StockName + " Graph")

# Price
ax1.plot(df["date"], df["open"], marker="o", label="Open")
ax1.plot(df["date"], df["high"], marker="o", label="High")
ax1.plot(df["date"], df["low"], marker="o", label="Low")
ax1.plot(df["date"], df["close"], marker="o", label="Close")

# Show minimum and maximum values for Open, High, Low, Close
for column, label in [
    ("open", "Open"),
    ("high", "High"),
    ("low", "Low"),
    ("close", "Close")
]:
    min_idx = df[column].idxmin()
    max_idx = df[column].idxmax()

    # Minimum
    ax1.annotate(
        f"{label} Min: {df.loc[min_idx, column]:.2f}",
        (df.loc[min_idx, "date"], df.loc[min_idx, column]),
        xytext=(0, -15),
        textcoords="offset points",
        ha="center"
    )

    # Maximum
    ax1.annotate(
        f"{label} Max: {df.loc[max_idx, column]:.2f}",
        (df.loc[max_idx, "date"], df.loc[max_idx, column]),
        xytext=(0, 10),
        textcoords="offset points",
        ha="center"
    )

ax1.set_xlabel("Date")
ax1.set_ylabel("Price (Rs.)")
ax1.tick_params(axis="x", rotation=90)
ax1.set_xticks(pd.date_range(df["date"].min(), df["date"].max(), freq="1D"))
ax1.grid(True)
ax1.autoscale(axis="y")
ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
cursor = mplcursors.cursor(ax1.lines, hover=True)

@cursor.connect("add")
def on_add(sel):
    x, y = sel.target
    sel.annotation.set_text(f"Value: {y:.2f}")
# ax1.set_yticks(range(20, 35, 1))   # Price grid every Rs. 1

# Volume
# ax2 = ax1.twinx()
#
# ax2.plot(
#     df["date"],
#     df["volume"].astype(float),
#     label="Volume",
#     linestyle="--"
# )
#
# ax2.set_ylabel("Volume")
#
# Combined legend
lines1, labels1 = ax1.get_legend_handles_labels()
#lines2, labels2 = ax2.get_legend_handles_labels()

ax1.legend(
    lines1,
    labels1,
    loc="upper right"
)
#     lines1 + lines2,
#     labels1 + labels2,
#     loc="upper left"
# )

plt.title(StockName + " - Open, High, Low, Close & Volume")
plt.tight_layout()
plt.show()