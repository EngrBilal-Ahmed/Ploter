from dataclasses import dataclass, field


PRICE_GRAPH_OPTIONS = {
    "O": ("open", "Open"),
    "H": ("high", "High"),
    "L": ("low", "Low"),
    "C": ("close", "Close"),
}


@dataclass
class AppConfig:
    """User-editable settings for the stock graph workflow."""

    stock_names: list[str] = field(default_factory=lambda: ["GLAXO"])
    total_days: int = 120
    price_graphs: str = "CLOH"
    refresh_days: int = 1
    database_file: str = "stock_data.db"

    def selected_price_graphs(self):
        """Return configured price columns in the requested order."""
        selected = []
        invalid_letters = []

        for letter in self.price_graphs.upper():
            if letter in PRICE_GRAPH_OPTIONS:
                selected.append(PRICE_GRAPH_OPTIONS[letter])
            elif not letter.isspace():
                invalid_letters.append(letter)

        if invalid_letters:
            print(
                "Ignored unknown PriceGraphs letter(s): "
                + ", ".join(sorted(set(invalid_letters)))
            )

        if not selected:
            print("PriceGraphs is empty or invalid, so price lines are hidden.")

        return selected
