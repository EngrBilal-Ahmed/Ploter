"""The dashboard's explicit eligible-scrip universe."""

from pathlib import Path
import xml.etree.ElementTree as ET


DEFAULT_ELIGIBLE_FILE = Path(__file__).resolve().parents[1] / "data" / "Eligible-Scrips-REG.xml"


def load_eligible_stocks(file=DEFAULT_ELIGIBLE_FILE):
    root = ET.parse(file).getroot()
    stocks = {}
    for item in root.iter():
        code = item.findtext("symbol_code")
        if code is None:
            continue
        symbol = code.strip().upper()
        name = (item.findtext("symbol_name") or symbol).strip()
        if not symbol:
            raise ValueError("The eligibility file contains a blank symbol")
        if symbol in stocks and stocks[symbol] != name:
            raise ValueError(f"Conflicting eligible-stock names for {symbol}")
        stocks[symbol] = name
    if not stocks:
        raise ValueError("No symbol_code entries found in the eligibility XML")
    return stocks


def register_eligible_stocks(connection, stocks):
    connection.execute("CREATE TEMP TABLE eligible_symbols (symbol TEXT PRIMARY KEY)")
    connection.executemany("INSERT INTO eligible_symbols VALUES (?)", ((s,) for s in stocks))
