"""Import historical PSX CSV data into the plotter's existing SQLite cache."""

import argparse
from pathlib import Path
import sqlite3
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from psx_stock_tool.config import AppConfig
from psx_stock_tool.ingestion import import_historical_csv


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path, help="Historical PSX CSV file")
    parser.add_argument("--database", default=AppConfig().database_file, help="SQLite cache path")
    parser.add_argument("--chunksize", default=50000, type=int, help="Rows per pandas chunk")
    args = parser.parse_args(argv)
    try:
        summary = import_historical_csv(
            args.file, args.database, chunksize=args.chunksize
        )
    except (OSError, sqlite3.Error, ValueError, pd.errors.ParserError) as error:
        parser.exit(1, f"CSV import failed: {error}\n")
    print(summary)


if __name__ == "__main__":
    main()
