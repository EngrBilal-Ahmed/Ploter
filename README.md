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
