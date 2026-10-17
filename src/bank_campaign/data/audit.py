"""Generate a reproducible dataset audit without fitting a model."""

import argparse
import json
from pathlib import Path

import pandas as pd

from bank_campaign.data.download import CSV_NAME, SOURCE_PAGE, verify_manifest
from bank_campaign.data.schema import (
    BASELINE_FEATURES,
    CATEGORICAL_VALUES,
    FEATURE_DECISIONS,
    FLOAT_COLUMNS,
    INTEGER_COLUMNS,
)
from bank_campaign.data.validate import validate_dataset


def build_audit(directory: Path, report: Path) -> dict:
    manifest = verify_manifest(directory)
    frame = validate_dataset(directory / CSV_NAME, expected_rows=41188)
    labels = {str(k): int(v) for k, v in frame["y"].value_counts().items()}
    unknowns = {
        column: int((frame[column] == "unknown").sum())
        for column in CATEGORICAL_VALUES
        if "unknown" in CATEGORICAL_VALUES[column]
    }
    numeric = INTEGER_COLUMNS + FLOAT_COLUMNS
    full_duplicates = int(frame.duplicated().sum())
    feature_duplicates = int(frame.duplicated(subset=list(BASELINE_FEATURES)).sum())
    # Inspect boundary overlap without evaluating held-out model performance.
    n = len(frame)
    blocks = (
        frame.iloc[: int(n * 0.6)],
        frame.iloc[int(n * 0.6) : int(n * 0.8)],
        frame.iloc[int(n * 0.8) :],
    )
    hashes = [
        set(pd.util.hash_pandas_object(b[list(BASELINE_FEATURES)], index=False)) for b in blocks
    ]
    overlap = {
        "train_validation": len(hashes[0] & hashes[1]),
        "train_test": len(hashes[0] & hashes[2]),
        "validation_test": len(hashes[1] & hashes[2]),
    }
    summary = {
        "csv_sha256": manifest["files"][CSV_NAME]["sha256"],
        "source_url": manifest["source_url"],
        "rows": n,
        "columns": len(frame.columns),
        "target_counts": labels,
        "positive_rate": labels.get("yes", 0) / n,
        "empty_cells": int(frame.isna().sum().sum()),
        "unknown_counts": unknowns,
        "never_previously_contacted": int((frame["pdays"] == 999).sum()),
        "full_duplicate_rows_beyond_first": full_duplicates,
        "baseline_duplicate_rows_beyond_first": feature_duplicates,
        "provisional_split_sizes": [len(block) for block in blocks],
        "baseline_unique_profile_overlap": overlap,
        "baseline_features": list(BASELINE_FEATURES),
    }
    lines = [
        "# Data audit — Bank Marketing (additional full)",
        "",
        "Generated from local validated data. No model has been trained.",
        "",
        "## Provenance",
        "",
        f"Source: [UCI Bank Marketing]({SOURCE_PAGE}).",
        "Selected variant: `bank-additional-full.csv`; older bank-full and the 10% sample are not used.",
        "UCI licenses the dataset under CC BY 4.0. Citation: Moro, S., Rita, P., & Cortez, P. (2014),",
        "Bank Marketing, UCI Machine Learning Repository, DOI: 10.24432/C5K306.",
        "The accompanying data dictionary additionally requests the 2014 Decision Support Systems paper citation",
        "(doi:10.1016/j.dss.2014.03.001).",
        "",
        f"CSV SHA-256: `{summary['csv_sha256']}`.",
        f"Acquired at UTC: `{manifest['retrieved_at_utc']}`.",
        "Hashes detect changes relative to this acquisition; UCI does not provide a verified publisher hash here.",
        "",
        "## Observed data quality",
        "",
        f"- Records: **{n:,}**; columns: **{len(frame.columns)}** (20 source inputs plus target).",
        f"- Target: yes={labels.get('yes', 0):,}, no={labels.get('no', 0):,}; positive rate={summary['positive_rate']:.2%}.",
        "- No blank or null cells, invalid categories, non-finite values, or schema violations passed validation.",
        "- This does not mean complete information: the dictionary defines `unknown` as missing categorical information.",
        f"- `pdays=999`: {summary['never_previously_contacted']:,} records; documented sentinel, not 999 actual days.",
        f"- Exact full-row duplicates beyond first occurrences: **{full_duplicates}**. Retained for now; no unverified customer deduplication.",
        f"- Repeated nine-feature profiles beyond first occurrences: **{feature_duplicates:,}**. These are not verified duplicate customers.",
        "- Numeric bounds are project plausibility rules (including age 1–120 and positive rates for this historical version), not universal banking rules.",
        "",
        "### Unknown categories",
        "",
        "| Column | Count | Share |",
        "|---|---:|---:|",
    ]
    for column, count in unknowns.items():
        lines.append(f"| {column} | {count:,} | {count / n:.2%} |")
    lines += [
        "",
        "Unknown values will remain explicit categories in the initial baseline, not dropped rows.",
        "",
        "### Numeric ranges",
        "",
        "| Column | Minimum | Maximum |",
        "|---|---:|---:|",
    ]
    for column in numeric:
        lines.append(f"| {column} | {frame[column].min():g} | {frame[column].max():g} |")
    lines += [
        "",
        "## Feature availability decisions",
        "",
        "This is a conservative design policy, not proof of point-in-time availability.",
        "Included client/history fields assume a CRM snapshot known before selection; no snapshot timestamps exist to prove it.",
        "Unknown categories are retained. Excluded time/context variables can only be reconsidered with an explicit availability contract.",
        "",
        "| Column | Decision | Reason |",
        "|---|---|---|",
    ]
    for column, (decision, reason) in FEATURE_DECISIONS.items():
        lines.append(f"| `{column}` | {decision} | {reason} |")
    lines += [
        "",
        "## Ordering and evaluation risks",
        "",
        "The official data dictionary states date ordering from May 2008 to November 2010.",
        "That ordering is source documentation, not independently reconstructed chronology: year and exact date are absent.",
        f"A provisional contiguous 60/20/20 division gives {summary['provisional_split_sizes']} records.",
        "No split files or models are created in this stage. Stage 3 must freeze row assignments and inspect duplicate leakage.",
        "",
        "| Pair | Shared unique baseline profiles |",
        "|---|---:|",
    ]
    for pair, count in overlap.items():
        lines.append(f"| {pair} | {count:,} |")
    lines += [
        "",
        "Shared profiles do not establish shared identity: different customers can share these attributes.",
        "There is no customer ID, so customer-disjoint evaluation cannot be guaranteed.",
        "A future row_id is only a stable technical record key. Do not remove all matching profiles as duplicate people.",
        "The whole-dataset label rate above is a data audit, not model selection or held-out performance.",
        "The held-out labels must not be used for model, feature, or policy tuning.",
        "",
        "## Next stage",
        "",
        "Freeze row-level splits, handle exact duplicate boundary overlap, and build a training-only preprocessing pipeline.",
        "Compare a dummy baseline and logistic regression using the nine approved features.",
        "Model metrics and causal/financial impact are not available at this stage.",
        "",
    ]
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    report.with_suffix(".json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--report", type=Path, default=Path("reports/data_audit.md"))
    args = parser.parse_args()
    try:
        summary = build_audit(args.data_dir, args.report)
    except ValueError as exc:
        parser.exit(1, f"Audit failed: {exc}\n")
    print(f"Audit written: {args.report}")
    print(f"Records: {summary['rows']:,}; positive rate: {summary['positive_rate']:.2%}")
    print(f"Approved baseline features: {len(BASELINE_FEATURES)}")


if __name__ == "__main__":
    main()
