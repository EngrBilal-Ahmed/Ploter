"""Run the independent NumPy-style intraday range and volume dashboard."""

import argparse
from multiprocessing import freeze_support
from pathlib import Path

from psx_stock_tool.intraday_dashboard import main


if __name__ == "__main__":
    freeze_support()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(Path(__file__).resolve().with_name("stock_data.db")))
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    main(args.database, port=args.port, show=not args.no_browser)
