# PSX Stock Tool

Small Python project for downloading PSX stock prices, caching them in SQLite, and plotting price/range graphs.

## Run

```powershell
python Numpy.py
```

`Numpy.py` is now only the entry point. The reusable code lives in `psx_stock_tool/`.

## Change Settings

Edit `psx_stock_tool/cli.py`:

- `stock_names`: PSX symbols to graph
- `total_days`: chart history length
- `price_graphs`: any mix of `O`, `H`, `L`, `C`
- `refresh_days`: recent days to re-download
- `database_file`: SQLite cache path

## Use In Another Script

```python
from psx_stock_tool import AppConfig, get_stock_data, plot_stocks

config = AppConfig(stock_names=["GLAXO", "SYS"], total_days=90)
df = get_stock_data("GLAXO", config)
plot_stocks(config)
```
