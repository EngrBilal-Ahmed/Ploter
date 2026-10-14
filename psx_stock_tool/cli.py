from .config import AppConfig
from .plotting import plot_stocks


def main():
    config = AppConfig(
        # Add or remove PSX symbols here. One two-panel graph is created for each stock.
        stock_names= ["AVN", "BNL", "UNITY"
            # "KEL", "PAEL", "PREMA", "FCCL", "GATM", "LOADS", "GGL", "CNERGY"
    # "SSGC",
    # "UNITY",
    # "AVN",
    # "BNL",
    # "SNGP",
    # "BWCL",
    # "FABL",
    # "PAEL",
    # "NML",
    # "LUCK",
    # "NATF",
    # "GLAXO",
    # "SEARL",
    # "LOTCHEM",
    # "EPQL",
    # "GGL",
    # "DGKC",
    # "MLCF",
    # "PIBTL",
    # "MEBL",
    # "SYS",
    # "NCPL",
    # "SELECT",
    # "SLM",
    # "PSO",
    # "LCI",
    # "FATIMA",
    # "EFERT",
    # "ENGROH",
    # "OGDC",
    # "FFC"
],
        # "EFERT",
        # "SYS",
        # "BWCL",
        # "FABL",
        # "HUBC",
        # "AVN",
        # "PSO",
        # "LUCK",
        # "SSGC",
        # "LOTCHEM",
        # "NATF",
        # "SELECT",
        # "MEBL",
        # "ENGROH",
        # "BNL",
        # "NML",
        # "GGL",
        # "LCI",
        # "OGDC",
        # "PAEL",
        # "BOP",
        # "SNGP",
        # "UNITY",
        # "MLCF",
        # "NCPL",
        # "EPQL",
        total_days=120,
        price_graphs="LH",  # O=open, H=high, L=low, C=close.
        refresh_days=5,
        database_file="stock_data.db",
    )
    plot_stocks(config)


if __name__ == "__main__":
    main()
