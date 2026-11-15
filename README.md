# PSX Stock Tool

Small Python project for downloading PSX stock prices, caching them in SQLite, and plotting price/range graphs.

## Run

```powershell
python Numpy.py
```

`Numpy.py` is now only the entry point. The reusable code lives in `psx_stock_tool/`.

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
