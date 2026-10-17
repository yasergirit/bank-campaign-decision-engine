"""Strict raw-data checks; preserve documented unknown and sentinel values."""

import argparse
import csv
import math
from pathlib import Path

import pandas as pd

from bank_campaign.data.schema import (
    CATEGORICAL_VALUES,
    COLUMNS,
    FLOAT_COLUMNS,
    INTEGER_COLUMNS,
)


def validate_dataset(path: Path, *, expected_rows: int | None = None) -> pd.DataFrame:
    """Return typed data or fail with an actionable error; never silently repair rows."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, delimiter=";", strict=True)
            if tuple(next(reader, ())) != COLUMNS:
                raise ValueError("CSV header must exactly match bank-additional-full schema/order.")
            for line, row in enumerate(reader, start=2):
                if len(row) != len(COLUMNS):
                    raise ValueError(
                        f"CSV row {line} has {len(row)} fields; expected {len(COLUMNS)}."
                    )
        frame = pd.read_csv(path, sep=";", dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except (OSError, UnicodeError, csv.Error, pd.errors.ParserError) as exc:
        raise ValueError(f"Cannot read dataset {path}: {exc}") from exc
    if frame.empty:
        raise ValueError("Dataset is empty.")
    if expected_rows is not None and len(frame) != expected_rows:
        raise ValueError(f"Expected {expected_rows} rows; got {len(frame)}.")
    if frame.isna().any().any() or frame.map(lambda value: not str(value).strip()).any().any():
        raise ValueError("Dataset contains empty/missing cells.")
    for column, allowed in CATEGORICAL_VALUES.items():
        invalid = sorted(set(frame[column]) - allowed)
        if invalid:
            raise ValueError(f"Invalid category in {column}: {invalid[:5]}")
    for column in INTEGER_COLUMNS + FLOAT_COLUMNS:
        try:
            numeric = pd.to_numeric(frame[column], errors="raise")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid numeric value in {column}.") from exc
        if not numeric.map(math.isfinite).all():
            raise ValueError(f"Non-finite numeric value in {column}.")
        if column in INTEGER_COLUMNS:
            if not (numeric % 1 == 0).all() or (numeric.abs() > 2**53).any():
                raise ValueError(f"Expected safely representable integers in {column}.")
            frame[column] = numeric.astype("int64")
        else:
            frame[column] = numeric.astype("float64")
    # Project plausibility rules, not inferred universal domain limits.
    if not frame["age"].between(1, 120).all():
        raise ValueError("age must be between 1 and 120.")
    for column, minimum in (("duration", 0), ("campaign", 1), ("pdays", 0), ("previous", 0)):
        if (frame[column] < minimum).any():
            raise ValueError(f"{column} must be >= {minimum}.")
    for column in ("cons.price.idx", "euribor3m", "nr.employed"):
        if (frame[column] <= 0).any():
            raise ValueError(f"{column} must be positive for this dataset version.")
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=Path("data/raw/bank-additional-full.csv"))
    args = parser.parse_args()
    try:
        frame = validate_dataset(args.path, expected_rows=41188)
    except ValueError as exc:
        parser.exit(1, f"Validation failed: {exc}\n")
    print(f"Validated {len(frame):,} records and {len(frame.columns)} columns.")


if __name__ == "__main__":
    main()
