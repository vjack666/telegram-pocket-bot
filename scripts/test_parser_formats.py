import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.signals.parser import SignalParser


def main() -> None:
    parser = SignalParser(default_amount=1.0)
    samples = [
        "EUR/JPY OTC BUY 5 MIN $1.26",
        "EURJPY-OTC \U0001f53c M5 $2.5",
        "SENAL\nPAR: EURJPY OTC\nDIRECCION: CALL\nEXPIRACION: 5M\nMONTO: 1.26",
        "EURJPY OTC \u2b07\ufe0f M 5",
    ]

    for text in samples:
        parsed = parser.parse(text)
        print("---")
        print(text)
        if parsed is None:
            print("PARSED = None")
            continue
        print(
            "PARSED =",
            parsed.asset,
            parsed.side,
            f"exp={parsed.expiry_minutes}m",
            f"amount={parsed.amount:.2f}",
        )


if __name__ == "__main__":
    main()
