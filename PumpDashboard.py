"""Run the independent eligible-stock surveillance dashboard."""

import argparse
from multiprocessing import freeze_support
from pathlib import Path

from psx_stock_tool.dashboard import main
from psx_stock_tool.eligibility import DEFAULT_ELIGIBLE_FILE


if __name__ == "__main__":
    freeze_support()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(Path(__file__).resolve().with_name("stock_data.db")))
    parser.add_argument("--eligible-file", type=Path, default=DEFAULT_ELIGIBLE_FILE)
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    main(args.database, args.eligible_file, port=args.port, show=not args.no_browser)
