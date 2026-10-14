from multiprocessing import freeze_support

from psx_stock_tool.cli import main


if __name__ == "__main__":
    freeze_support()
    main()
