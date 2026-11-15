# PSX Stock Tool

Small Python project for downloading PSX stock prices, caching them in SQLite, and plotting price/range graphs.

## Run

```powershell
python Numpy.py
```

`Numpy.py` is now only the entry point. The reusable code lives in `psx_stock_tool/`.

There are three independent runnable interfaces; they can run at the same time:

| Command | Default address | Purpose |
|---|---|---|
| `python Numpy.py` | `http://127.0.0.1:8080` | Original stock selector with Matplotlib price/range windows |
| `python PumpDashboard.py` | `http://127.0.0.1:8081` | Eligible-stock circuit and volume surveillance |
| `python NumpyDashboard.py` | `http://127.0.0.1:8082` | Browser-based OHLC, intraday range and volume with linked zoom |

## Use the GUI

Install dependencies first with `python -m pip install -r requirements.txt`, then run
`python Numpy.py`. The NiceGUI application opens in your default web browser on this computer and listens on port 8080 for devices on your local network. Paste PSX symbols into the stock-list box. The list accepts
commas, tabs, line breaks, and cells pasted directly from Excel. Click **Create
stock checkboxes**, choose the required symbols, then click **Plot selected stocks**.

On another device connected to the same network, open `http://<this-computer-ip>:8080`
in a browser. Allow Python through Windows Firewall on private networks if Windows
prompts you.

The GUI also lets you set chart history, recent download refresh days, and the
Open/High/Low/Close lines to display.

The stock-list text is saved automatically in `stock_data.db` when you create the
checkboxes or leave the stock-list field. It is restored the next time the app opens.

## Create a Windows EXE

NiceGUI is configured to open the local interface in the user's browser. From the
project folder, install PyInstaller and build the executable:

```powershell
python -m pip install pyinstaller
nicegui-pack --onefile --name PSXStockPlotter Numpy.py
```

The finished file is `dist\PSXStockPlotter.exe`.

## Use In Another Script

```python
from psx_stock_tool import AppConfig, get_stock_data, plot_stocks

config = AppConfig(stock_names=["GLAXO", "SYS"], total_days=90)
df = get_stock_data("GLAXO", config)
plot_stocks(config)
```

## Historical CSV import

The CSV importer and API sync use the same SQLite `stock_prices` table as the
plotter; the GUI, chart controls, and cached-data fallback are unchanged. Use
Python 3.10+ with SQLite 3.33+ and the existing dependencies in `requirements.txt`.
Run these commands from the project folder so they use the GUI's `stock_data.db`.

```powershell
python scripts\import_historical_csv.py --file "D:\PythonProjects\2017_2025.csv"
```

The required CSV header is:

```csv
DATE,SYMBOL,LDCP,OPEN,HIGH,LOW,CLOSE,CHANGE,CHANGE (%),VOLUME
2017-01-02,AABS,329.0,338.88,338.88,331.0,331.0,2.0,0.60790273556231,300
```

All columns map to lowercase names, with `CHANGE (%)` stored as `change_pct`.
Symbols are stripped and uppercased, dates become `YYYY-MM-DD`, numeric
whitespace/commas are removed, and volume is a nonnegative signed 64-bit integer.
Quote comma-containing numeric CSV fields, such as `"1,000"`.
Invalid dates, missing/invalid OHLC, inconsistent high/low ranges, and invalid or
missing historical volumes are skipped. Missing optional numeric values become
SQL `NULL`. Missing `change` and `change_pct` are computed from close/LDCP when
possible; supplied values are retained. A zero LDCP is retained, but a missing
percentage remains `NULL` rather than dividing by zero. Missing LDCP stays `NULL`.

Pandas reads 50,000 rows per chunk. SQLite disk-backed temporary staging
deduplicates across the entire file without holding all years in memory; the last
valid occurrence of each `(symbol, date)` wins. The final bulk upsert is atomic.
The summary counts unique inserted/updated keys; skipped includes malformed data
rows and duplicates. Updated counts include existing keys even if unchanged.
Malformed CSV structure or missing headers aborts the import with an error,
without partially changing prices.

Override the cache or chunk size if necessary:

```powershell
python scripts\import_historical_csv.py --file data\psx_2017_2025.csv --database stock_data.db --chunksize 50000
```

### Safe automatic migration

The first import, sync, or GUI database access upgrades the original cache
transactionally. Existing OHLCV rows, saved stock lists, and `fetched_at` values
are preserved. The table gains an integer primary-key `id`, `ldcp`, `change`,
`change_pct`, `source`, `updated_at`, BIGINT volume affinity, and a unique index on
`(symbol, date)`. Existing downloaded rows are marked `psxdata_api`; their derived
values are backfilled where a previous trading close exists. CSV rows are marked
`csv_historical`. No separate `daily_prices` table is needed.

Close the app and copy `stock_data.db` before the first migration if you want an
external backup. Migration failures roll back; unrecognized legacy columns are
not silently discarded. Re-running imports updates matching rows without
changing their IDs or creating duplicates.

## Ongoing psxdata synchronization

```powershell
python src\sync_psxdata.py
python src\sync_psxdata.py --symbols AABS GLAXO SYS
python src\sync_psxdata.py --symbols SYS --start 2017-01-01 --end 2025-12-31 --database stock_data.db
```

By default, sync visits every symbol already in the database, from its latest
cached date through today (inclusive). Re-fetching the latest candle captures
corrections or a previously incomplete session; the provider cache is bypassed.
For explicitly supplied symbols without history, `--start` sets the initial date;
without it, `psxdata` requests its earliest available history. `--start` does not
backfill earlier than an existing symbol's latest date; use CSV imports for
historical backfills. An empty cache requires `--symbols` or an initial CSV import.

API `ldcp` uses the immediately preceding trading row's close, not the preceding
calendar day. `change` and `change_pct` are computed automatically after merging
the fetched candles with cached history. The first candle without a predecessor
has `NULL` derived values; zero previous close yields a `NULL` percentage.
Backfills/corrections also repair subsequent API-derived values, without
overriding CSV-supplied changes. API candles without volume retain `NULL` volume,
preserving the plotter's existing OHLC behavior.

Each symbol commits atomically. A provider/database failure stops sync, is
reported with a nonzero CLI exit status, and leaves previously committed symbols
intact. Empty responses (for example, holidays) are valid and retain existing
history. The GUI's original download-failure fallback to cached prices remains.

There is no pump-and-dump detector in this workspace. A future detector can call
the service before querying `stock_prices`; synchronization errors propagate so
it need not proceed with stale data:

```python
from src.sync_psxdata import sync_psxdata

summary = sync_psxdata(database_file="stock_data.db")
print(summary)
# Run your detector against stock_prices after synchronization succeeds.
```

## Regression checks

```powershell
python -m unittest discover -s tests -v
```

## Eligible-stock circuit and volume dashboard

Run the new dashboard independently of the original plotter:

```powershell
python PumpDashboard.py
```

Open `http://127.0.0.1:8081`. `Numpy.py` and its plotting workflow are unchanged.
Optional arguments: `--port 8082`, `--database path\to\cache.db`,
`--eligible-file data\Eligible-Scrips-REG.xml`, and `--no-browser`.

The supplied eligibility file is **XML**, with `symbol_code` and `symbol_name`
entries. Only those symbols are fetched, written, and analyzed by this dashboard.
Existing non-eligible historical rows are retained but excluded. The original
plotter, CSV importer, and standalone sync retain their existing unrestricted
behavior. An unreadable/empty eligibility file reports an error, never an
all-symbol fallback.

The dashboard initially reads SQLite only. **Fetch all missing dates** uses each
eligible symbol's stored dates, observed eligible-market sessions, and successful
previous checks to find the earliest unchecked hole through today. A request may
overlap existing dates to repair an interior gap in one request per symbol.
Future weekday candidates are estimates, not an official exchange calendar;
empty responses do not create fake prices. Pre-listing/suspended/holiday ranges
successfully checked are recorded in `dashboard_sync_ranges` to avoid endless
retries. Failed or malformed requests remain retryable and errors are displayed;
successful symbols commit independently. Only one dashboard sync runs at a time
within a process.

Set **Earliest history date** (default 2017-01-01) to limit backfills. **Refresh past
X calendar days** forces recent ranges to be requested again, even if previously
checked; zero requests only unchecked missing sessions. New file eligibility is
read on each reload/sync. Refresh can recheck previously empty recent periods;
use an appropriately larger X for older provider corrections. A successful empty
request is a check, not proof of complete history. Market-wide missing historical
dates cannot be distinguished from holidays without an official trading calendar.

The watchlist displays the actual last candle date, stale-relative-to-market
flag, latest evidence, volume multiple, current streaks, and full-history maximum
upper/lower runs. Histories current to the newest cached market session sort
before older histories; dates remain visible and no stale signal is counted as
a signal for the newest session. Select a row/symbol for candlesticks with estimated limits and
touch markers, volume bars, monthly circuit event histograms, and recent events.
Chart range is relative to that symbol's latest stored candle, **not** a claim of
current data. Orange bars indicate elevated volume.

Initial screening rules are editable, explainable heuristics:

- Estimated move is `max(Rs 1, LDCP * 10%)`, rounded to paisa, with Rs 0.02
  matching tolerance. Missing/nonpositive LDCP falls back to the preceding stored
  close; without a valid reference no circuit is inferred.
  These are **indicative daily-candle classifications**, not
  exchange-verified locks. Actual historical PSX limits changed, and special
  scrips/corporate actions may require different rules. The selected rule applies
  uniformly to history; this version has no official historical rules calendar.
- Volume spikes default to 3 times the previous 20 observed sessions' mean,
  excluding the current candle. Missing/zero baselines are not assigned a ratio.
  Breakouts compare close against the preceding window's maximum high.
- Consecutive circuit closes reset across missing observed market sessions.
  An upper touch with a close away from the limit is flagged separately.
  Daily OHLC cannot determine whether a circuit actually locked or the order of
  intraday events. Historical eligibility is not reconstructed; the current XML
  universe is used throughout.

Labels such as **Volume breakout**, **Consecutive upper closes**, and **Reversal
after upper run** describe observed evidence. They are not validated forecasts,
buy/sell recommendations, proof of pump-and-dump manipulation, or probabilities.

## NumPy-style intraday range dashboard

```powershell
python NumpyDashboard.py
```

Open `http://127.0.0.1:8082`. This is a separate dashboard; the original
`Numpy.py` Matplotlib workflow and `PumpDashboard.py` remain available.
Optional arguments: `--port 8083`, `--database path\to\cache.db`, and `--no-browser`.

Paste symbols (including Excel cells) and click **Apply stock list**, choose the
stocks to compare, and click **Show cached charts**. Saved and pasted multiline
lists display inline, separated by commas; longer lists wrap and grow without an
internal scrollbar. The stock-list text is shared
with the original plotter through SQLite. The dashboard initially reads cached
data only and is not restricted to the PumpDashboard eligibility XML.
The comparison table shows each stock's latest session, latest/maximum/average
intraday range percentage, close and volume. Select a row or use the alphabetically
sorted **Stock to chart** dropdown to view that stock's history and daily-session
details. The dashboard uses the available browser width with small responsive
edge spacing, including on 1920 x 1080 displays and half-screen windows.

The price panel preserves selectable Open/High/Low/Close lines and minimum/maximum
annotations. The range histogram uses the original formula
`(High - Low) / Low * 100`, with percentage labels; a zero low produces no
percentage. The third panel shows daily volume (green for close >= open, red
otherwise); missing volume remains blank.

**All three panels share linked date zoom, pan, and hover guides.** Mouse-wheel
zoom over any panel, drag to pan, or adjust the bottom slider: price, range and
volume always show the same session/date interval. **Reset linked zoom** restores
the full displayed history. Price-line visibility can be changed without fetching.

**History (calendar days)** is measured back from each stock's latest cached
session, whose date is always displayed; this can be old data.
**Fetch / update selected** uses the original plotter's missing-history and
recent-refresh mechanism through today, with the configured history and refresh
days. Fetching runs off the UI thread, updates the shared SQLite cache, and reports
per-stock failures/empty requested periods while keeping available history
viewable. An empty provider response does not imply up-to-date cached data.

These are **daily OHLC-derived intraday ranges**, not live or minute-level
intraday feeds. Daily high/low data cannot establish the sequence of trades,
executable entry/exit prices, or achievable profit; the dashboard is a research
view, not a trading recommendation.
