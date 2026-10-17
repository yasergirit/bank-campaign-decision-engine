"""Freeze source-order partitions and exclude later identical predictor records."""

import argparse
import json
from hashlib import sha256
from pathlib import Path

import pandas as pd

from bank_campaign.data.download import CSV_NAME, verify_manifest
from bank_campaign.data.validate import validate_dataset

POLICY = "source_order_60_20_20_first_predictor_occurrence_v1"


def build_assignments(frame: pd.DataFrame) -> pd.DataFrame:
    """Assign every source position; never use labels to detect duplicates."""
    if len(frame) < 5 or "y" not in frame or len(frame.columns) < 2:
        raise ValueError("Splitting requires at least five rows, predictors and a y column.")
    n = len(frame)
    first_boundary, second_boundary = int(n * 0.6), int(n * 0.8)
    predictors = frame.drop(columns="y")
    first_seen: dict[str, int] = {}
    rows = []
    for row_id, values in enumerate(predictors.itertuples(index=False, name=None)):
        fingerprint = sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()
        duplicate_of = first_seen.get(fingerprint, -1)
        first_seen.setdefault(fingerprint, row_id)
        partition = (
            "train"
            if row_id < first_boundary
            else "validation"
            if row_id < second_boundary
            else "test"
        )
        rows.append((row_id, partition, duplicate_of == -1, duplicate_of))
    return pd.DataFrame(rows, columns=["row_id", "split", "included", "duplicate_of"])


def freeze_splits(
    frame: pd.DataFrame,
    *,
    csv_sha256: str,
    assignment_path: Path,
    manifest_path: Path,
) -> tuple[pd.DataFrame, dict]:
    """Create assignments once, or verify existing files without silently replacing them."""
    assignments = build_assignments(frame)
    content = assignments.to_csv(index=False, lineterminator="\n").encode()
    counts = {}
    for name in ("train", "validation", "test"):
        subset = assignments.loc[assignments["split"] == name]
        counts[name] = {
            "assigned": len(subset),
            "retained": int(subset["included"].sum()),
            "excluded_duplicates": int((~subset["included"]).sum()),
        }
    manifest = {
        "policy": POLICY,
        "source_csv_sha256": csv_sha256,
        "row_id_definition": "zero-based original CSV record position, excluding header",
        "duplicate_key": [column for column in frame.columns if column != "y"],
        "duplicate_policy": "retain earliest identical predictor row; exclude later occurrences",
        "assignment_sha256": sha256(content).hexdigest(),
        "counts": counts,
        "test_status": "reserved; not evaluated in Stage 3",
        "limitations": "Source order, not exact dates; customer identity is unavailable.",
    }
    manifest_content = json.dumps(manifest, indent=2) + "\n"
    if manifest_path.exists():
        if manifest_path.read_text() != manifest_content:
            raise ValueError("Frozen split manifest differs from data/policy; do not overwrite it.")
        if assignment_path.exists():
            if assignment_path.read_bytes() != content:
                raise ValueError("Frozen split assignments differ; do not overwrite them.")
        else:
            # A clean checkout retains the versioned manifest but not local assignments.
            assignment_path.parent.mkdir(parents=True, exist_ok=True)
            assignment_path.write_bytes(content)
    elif assignment_path.exists():
        raise ValueError("Frozen split manifest is missing; inspect before restoring it.")
    else:
        assignment_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        assignment_path.write_bytes(content)
        manifest_path.write_text(manifest_content)
    return assignments, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    parser.add_argument(
        "--assignments", type=Path, default=Path("data/processed/split_assignments.csv")
    )
    parser.add_argument("--manifest", type=Path, default=Path("reports/split_manifest.json"))
    args = parser.parse_args()
    try:
        acquisition = verify_manifest(args.data_dir)
        frame = validate_dataset(args.data_dir / CSV_NAME, expected_rows=41188)
        _, manifest = freeze_splits(
            frame,
            csv_sha256=acquisition["files"][CSV_NAME]["sha256"],
            assignment_path=args.assignments,
            manifest_path=args.manifest,
        )
    except ValueError as exc:
        parser.exit(1, f"Split failed: {exc}\n")
    print(json.dumps(manifest["counts"], indent=2))
    print("Test set reserved; no test-performance metrics calculated.")


if __name__ == "__main__":
    main()
