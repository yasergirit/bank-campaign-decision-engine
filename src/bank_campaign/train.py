"""Train fixed baselines and report validation performance without evaluating test."""

import argparse
import json
import pickle
import platform
import warnings
from hashlib import sha256
from pathlib import Path

import pandas as pd
import sklearn
from sklearn.dummy import DummyClassifier
from sklearn.exceptions import ConvergenceWarning

from bank_campaign.data.download import CSV_NAME, verify_manifest
from bank_campaign.data.schema import BASELINE_FEATURES
from bank_campaign.data.split import freeze_splits
from bank_campaign.data.validate import validate_dataset
from bank_campaign.evaluate import probability_metrics
from bank_campaign.features import build_pipeline, select_features


def train_baselines(
    data_dir: Path = Path("data/raw"),
    processed_dir: Path = Path("data/processed"),
    report_dir: Path = Path("reports"),
    artifact_dir: Path = Path("artifacts/baseline-v1"),
) -> dict:
    acquisition = verify_manifest(data_dir)
    frame = validate_dataset(data_dir / CSV_NAME, expected_rows=41188)
    assignments, split_manifest = freeze_splits(
        frame,
        csv_sha256=acquisition["files"][CSV_NAME]["sha256"],
        assignment_path=processed_dir / "split_assignments.csv",
        manifest_path=report_dir / "split_manifest.json",
    )

    def partition(name: str) -> pd.DataFrame:
        ids = assignments.loc[(assignments["split"] == name) & assignments["included"], "row_id"]
        return frame.iloc[ids.to_list()]

    training, validation = partition("train"), partition("validation")
    x_train, x_validation = select_features(training), select_features(validation)
    y_train = training["y"].map({"no": 0, "yes": 1}).astype(int)
    y_validation = validation["y"].map({"no": 0, "yes": 1}).astype(int)
    if y_train.nunique() != 2 or y_validation.nunique() != 2:
        raise ValueError("Both training and validation must contain both target classes.")
    models = {
        "dummy_prior": DummyClassifier(strategy="prior"),
        "logistic_regression": build_pipeline(),
    }
    results, predictions = [], pd.DataFrame({"row_id": validation.index.to_list()})
    for name, model in models.items():
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(x_train, y_train)
        positive_index = list(model.classes_).index(1)
        probabilities = model.predict_proba(x_validation)[:, positive_index]
        metrics = probability_metrics(y_validation, probabilities)
        results.append({"model": name, "evaluation_split": "validation", **metrics})
        predictions[name] = probabilities
    logistic = models["logistic_regression"]
    metadata = {
        "run_id": "baseline-v1",
        "features": list(BASELINE_FEATURES),
        "source_csv_sha256": acquisition["files"][CSV_NAME]["sha256"],
        "split_assignment_sha256": split_manifest["assignment_sha256"],
        "uv_lock_sha256": sha256(Path("uv.lock").read_bytes()).hexdigest(),
        "source_code_sha256": {
            name: sha256(Path(name).read_bytes()).hexdigest()
            for name in (
                "src/bank_campaign/data/split.py",
                "src/bank_campaign/data/schema.py",
                "src/bank_campaign/features.py",
                "src/bank_campaign/evaluate.py",
                "src/bank_campaign/train.py",
            )
        },
        "versions": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "sklearn": sklearn.__version__,
        },
        "train_records": len(training),
        "validation_records": len(validation),
        "train_positive_rate": float(y_train.mean()),
        "validation_positive_rate": float(y_validation.mean()),
        "logistic_config": logistic.named_steps["model"].get_params(),
        "results": results,
        "test_evaluated": False,
        "calibration": "none; fixed unweighted logistic regression baseline",
    }
    artifact_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    with (artifact_dir / "logistic_regression.pkl").open("wb") as handle:
        pickle.dump(logistic, handle)
    predictions.to_csv(artifact_dir / "validation_predictions.csv", index=False)
    pd.DataFrame(results).to_csv(report_dir / "baseline_validation.csv", index=False)
    (artifact_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, allow_nan=False) + "\n"
    )
    (report_dir / "baseline_validation.json").write_text(
        json.dumps(metadata, indent=2, allow_nan=False) + "\n"
    )
    write_report(metadata, report_dir / "baseline_report.md", split_manifest)
    return metadata


def write_report(metadata: dict, destination: Path, split_manifest: dict) -> None:
    lines = [
        "# Stage 3 — fixed baseline validation",
        "",
        "This report contains validation results only. The test partition has not been scored.",
        "",
        "## Split policy",
        "",
        "Original source positions are assigned 60/20/20 without shuffling.",
        "Later identical source-predictor rows are excluded, retaining their first occurrence.",
        "Duplicate detection uses all 20 predictors and does not inspect y.",
        "Shared nine-feature profiles alone are not treated as duplicate people.",
        "",
        "| Partition | Assigned | Retained | Excluded repeated records |",
        "|---|---:|---:|---:|",
    ]
    for name, counts in split_manifest["counts"].items():
        lines.append(
            f"| {name} | {counts['assigned']:,} | {counts['retained']:,} | {counts['excluded_duplicates']} |"
        )
    lines += [
        "",
        "Source ordering is documented by UCI, but exact dates and customer IDs are absent.",
        "Therefore this is not a proven customer-disjoint or precisely dated out-of-time evaluation.",
        "",
        "## Fixed baseline specification",
        "",
        f"Features: {', '.join(metadata['features'])}.",
        "Numeric age/previous are standardized; categorical fields are one-hot encoded.",
        "Unknown remains an explicit observed category. Unseen validation categories are ignored by the encoder.",
        "Scaling and encoding are fit only on the training partition, inside a Pipeline.",
        "Logistic regression uses C=1, lbfgs, max_iter=1000, random_state=42 and no class weighting.",
        "No hyperparameter search or probability calibration has been performed.",
        "DummyClassifier(strategy=prior) predicts the training subscription prevalence for every record.",
        "",
        f"Training positive rate: {metadata['train_positive_rate']:.2%}; validation positive rate: {metadata['validation_positive_rate']:.2%}.",
        "These can differ because the split follows historical source order.",
        "",
        "## Validation results",
        "",
        "| Model | Average Precision ↑ | ROC-AUC ↑ | Brier score ↓ |",
        "|---|---:|---:|---:|",
    ]
    for result in metadata["results"]:
        lines.append(
            f"| {result['model']} | {result['average_precision']:.6f} | {result['roc_auc']:.6f} | {result['brier_score']:.6f} |"
        )
    lines += [
        "",
        "Average Precision uses sklearn's non-interpolated AP definition, not trapezoidal PR-AUC.",
        "A constant-score baseline has AP equal to validation prevalence and ROC-AUC 0.5.",
        "Brier measures squared probability error; it is not a standalone calibration certificate.",
        "Improvements on validation do not establish test performance, causal uplift, or actual profit.",
        "",
        "## Reproduction and artifacts",
        "",
        "```bash",
        "uv sync --locked --inexact",
        "python -m bank_campaign.data.split",
        "python -m bank_campaign.train",
        "```",
        "",
        "Run from the repository root. Frozen assignments are checked before reuse; changes cause an error.",
        "Baseline-v1 artifacts are replaced when repeating this fixed experiment; future variants need separate run IDs.",
        "The trusted local pickle is stored in artifacts/baseline-v1/logistic_regression.pkl.",
        "Metadata records package versions, model parameters, source hash, split hash and lockfile hash.",
        "Validation predictions and model artifacts stay outside Git; aggregate reports are versionable.",
        "No model promotion decision has been made. Capacity decisions and further comparison follow in Stage 4.",
        "",
    ]
    destination.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    try:
        metadata = train_baselines(data_dir=args.data_dir)
    except (ValueError, OSError, ConvergenceWarning) as exc:
        parser.exit(1, f"Training failed: {exc}\n")
    print(pd.DataFrame(metadata["results"]).to_string(index=False))
    print("Test set not evaluated. Report: reports/baseline_report.md")


if __name__ == "__main__":
    main()
