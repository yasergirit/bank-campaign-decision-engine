"""Create a campaign list from scored records using explicit capacity/value assumptions."""

import argparse
import json
from pathlib import Path

import pandas as pd

from bank_campaign.comparison import ARTIFACTS, REPORTS, file_hash
from bank_campaign.policy import campaign_summary, select_campaign


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        help="Scored CSV with row_id and subscription_probability; default: validation demo",
    )
    parser.add_argument("--capacity", type=int, default=500)
    parser.add_argument("--cost", type=float, default=3.0)
    parser.add_argument("--value", type=float, default=100.0)
    parser.add_argument("--mode", choices=["capacity", "positive_value"], default="capacity")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/campaign-demo"))
    args = parser.parse_args()
    try:
        if args.input is None:
            metadata = json.loads((REPORTS / "comparison_manifest.json").read_text())
            predictions_path = ARTIFACTS / "selected_validation_predictions.csv"
            if file_hash(predictions_path) != metadata["selected_validation_predictions_sha256"]:
                raise ValueError("Validation prediction file differs from the recorded experiment.")
            freeze_path = REPORTS / "selection_freeze.json"
            if not freeze_path.exists():
                raise ValueError("Freeze the selected experiment before using its validation demo.")
            frozen = json.loads(freeze_path.read_text())
            if (
                file_hash(predictions_path) != frozen["selected_validation_predictions_sha256"]
                or file_hash(REPORTS / "comparison_manifest.json")
                != frozen["comparison_manifest_sha256"]
                or metadata["selected_model"] != frozen["selected_model"]
            ):
                raise ValueError("Predictions/model metadata differ from the frozen decision.")
            candidates = pd.read_csv(predictions_path)
            source = f"historical validation demo ({metadata['selected_model']}); record IDs are not customer IDs"
        else:
            candidates = pd.read_csv(args.input)
            source = f"user-supplied scored records: {args.input}"
        selected = select_campaign(
            candidates, capacity=args.capacity, cost=args.cost, value=args.value, mode=args.mode
        )
        summary = {
            "source": source,
            "mode": args.mode,
            "capacity": args.capacity,
            "cost_per_record": args.cost,
            "assumed_value_per_subscription": args.value,
            **campaign_summary(selected, cost=args.cost, value=args.value),
            "interpretation": "Model-based assumptions, not observed conversions, causal uplift, or actual profit.",
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        selected.to_csv(args.output_dir / "selected.csv", index=False)
        (args.output_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, allow_nan=False) + "\n"
        )
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"Campaign selection failed: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Selection saved to {args.output_dir / 'selected.csv'}")


if __name__ == "__main__":
    main()
