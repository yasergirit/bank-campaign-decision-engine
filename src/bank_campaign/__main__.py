"""Run a setup check with: python -m bank_campaign."""

import sys
from importlib.metadata import version


def main() -> None:
    print(f"Bank Campaign Decision Engine {version('bank-campaign-decision-engine')}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Interpreter: {sys.executable}")
    print(f"Virtual environment active: {sys.prefix != sys.base_prefix}")
    print("Setup check complete. Baseline command: python -m bank_campaign.train")


if __name__ == "__main__":
    main()
