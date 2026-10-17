"""Bounded validation comparison and campaign analysis; never score the test split."""

import argparse
import json
import math
import pickle
import platform
import random
import warnings
from hashlib import sha256
from pathlib import Path

import lightgbm
import pandas as pd
import sklearn
from lightgbm import LGBMClassifier
from sklearn.base import clone
from sklearn.dummy import DummyClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.pipeline import Pipeline

from bank_campaign.data.download import CSV_NAME, verify_manifest
from bank_campaign.data.schema import BASELINE_FEATURES
from bank_campaign.data.split import freeze_splits
from bank_campaign.data.validate import validate_dataset
from bank_campaign.evaluate import probability_metrics
from bank_campaign.features import build_pipeline, select_features
from bank_campaign.policy import campaign_metrics, campaign_summary, select_campaign

CONFIG = Path("configs/comparison.json")
ARTIFACTS = Path("artifacts/comparison-v1")
REPORTS = Path("reports")
SOURCE_FILES = (
    "src/bank_campaign/data/download.py",
    "src/bank_campaign/data/validate.py",
    "src/bank_campaign/data/schema.py",
    "src/bank_campaign/data/split.py",
    "src/bank_campaign/features.py",
    "src/bank_campaign/evaluate.py",
    "src/bank_campaign/policy.py",
    "src/bank_campaign/comparison.py",
    "src/bank_campaign/holdout.py",
    "src/bank_campaign/campaign.py",
    "configs/comparison.json",
    "uv.lock",
)


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def validate_config(config: dict) -> None:
    if (
        config.get("run_id") != "comparison-v1"
        or config.get("selection_rule")
        != "validation_average_precision_desc_then_brier_asc_then_name"
    ):
        raise ValueError("Unsupported run ID or model selection rule.")
    candidates = config.get("lightgbm_candidates", [])
    names = [candidate.get("name") for candidate in candidates]
    if (
        not 1 <= len(candidates) <= 3
        or len(set(names)) != len(names)
        or any(not isinstance(name, str) or not name.startswith("lightgbm_") for name in names)
    ):
        raise ValueError("Require 1–3 unique lightgbm_ candidate names, excluding baseline names.")
    for candidate in candidates:
        if set(candidate) != {"name", "num_leaves", "n_estimators"}:
            raise ValueError("Unsupported candidate parameters.")
        for parameter in ("num_leaves", "n_estimators"):
            value = candidate[parameter]
            if isinstance(value, bool) or not isinstance(value, int) or value < 2 or value > 1000:
                raise ValueError(f"Invalid candidate {parameter}.")
    if config.get("business_rule") != "prior_success_desc_then_previous_desc_then_row_id_asc":
        raise ValueError("Unsupported history rule.")
    for key in ("random_repetitions", "seed"):
        if isinstance(config.get(key), bool) or not isinstance(config.get(key), int):
            raise ValueError(f"{key} must be integer.")
    if not 1 <= config["random_repetitions"] <= 10000:
        raise ValueError("random_repetitions must be in 1..10000.")
    capacities = config.get("capacities", [])
    if (
        not capacities
        or any(isinstance(k, bool) or not isinstance(k, int) or k < 0 for k in capacities)
        or len(set(capacities)) != len(capacities)
    ):
        raise ValueError("Capacities must be distinct nonnegative integers.")
    scenario = config.get("scenario", {})
    if set(scenario) != {"capacity", "cost", "value"}:
        raise ValueError("Scenario requires capacity, cost and value.")
    select_campaign(pd.DataFrame(columns=["row_id", "subscription_probability"]), **scenario)
    common = config.get("lightgbm_common", {})
    required = {
        "learning_rate",
        "min_child_samples",
        "reg_lambda",
        "random_state",
        "n_jobs",
        "deterministic",
        "force_col_wise",
        "verbosity",
    }
    if set(common) != required:
        raise ValueError("Unsupported or missing common LightGBM parameters.")
    for key in ("learning_rate", "reg_lambda"):
        value = common[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or (key == "learning_rate" and value == 0)
        ):
            raise ValueError(f"Invalid LightGBM {key}.")
    if (
        isinstance(common["min_child_samples"], bool)
        or not isinstance(common["min_child_samples"], int)
        or common["min_child_samples"] < 1
        or common["random_state"] != config["seed"]
        or common["n_jobs"] != 1
        or common["deterministic"] is not True
        or common["force_col_wise"] is not True
        or common["verbosity"] != -1
    ):
        raise ValueError("LightGBM training must retain the declared deterministic configuration.")


def build_candidates(config: dict) -> dict:
    validate_config(config)
    candidates = {
        "dummy_prior": DummyClassifier(strategy="prior"),
        "logistic_regression": build_pipeline(),
    }
    for specification in config["lightgbm_candidates"]:
        parameters = {key: value for key, value in specification.items() if key != "name"}
        candidates[specification["name"]] = Pipeline(
            [
                ("preprocess", clone(build_pipeline().named_steps["preprocess"])),
                ("model", LGBMClassifier(**config["lightgbm_common"], **parameters)),
            ]
        )
    return candidates


def choose_model(results: list[dict]) -> str:
    if not results or any(
        row["average_precision"] is None or row["brier_score"] is None for row in results
    ):
        raise ValueError("Model selection requires defined validation AP and Brier metrics.")
    if any(
        not math.isfinite(row[key])
        for row in results
        for key in ("average_precision", "brier_score")
    ):
        raise ValueError("Model selection metrics must be finite.")
    return min(
        results, key=lambda row: (-row["average_precision"], row["brier_score"], row["model"])
    )["model"]


def random_benchmark(labels, *, capacity: int, repetitions: int, seed: int) -> dict:
    """Monte Carlo selection percentiles, not a model uncertainty confidence interval."""
    labels = list(labels)
    rng = random.Random(seed)
    results = [
        campaign_metrics(labels, rng.sample(range(len(labels)), min(capacity, len(labels))))
        for _ in range(repetitions)
    ]
    observed = pd.Series([result["observed_subscriptions"] for result in results], dtype=float)
    return {
        "capacity": capacity,
        "selected_count": min(capacity, len(labels)),
        "repetitions": repetitions,
        "seed": seed,
        "observed_mean": float(observed.mean()),
        "observed_p05": float(observed.quantile(0.05)),
        "observed_p95": float(observed.quantile(0.95)),
    }


def business_rule_positions(frame: pd.DataFrame, capacity: int) -> list[int]:
    ordered = pd.DataFrame(
        {
            "position": range(len(frame)),
            "row_id": frame.index.to_list(),
            "prior_success": (frame["poutcome"] == "success").to_numpy(),
            "previous": frame["previous"].to_numpy(),
        }
    ).sort_values(["prior_success", "previous", "row_id"], ascending=[False, False, True])
    return ordered.head(capacity)["position"].to_list()


def selection_analysis(
    frame: pd.DataFrame, probabilities, scenario: dict, *, mode: str
) -> tuple[dict, pd.DataFrame]:
    candidates = pd.DataFrame(
        {"row_id": frame.index.to_list(), "subscription_probability": probabilities}
    )
    selected = select_campaign(candidates, **scenario, mode=mode)
    positions = {row_id: position for position, row_id in enumerate(frame.index)}
    selected_positions = [positions[row_id] for row_id in selected["row_id"]]
    labels = frame["y"].map({"no": 0, "yes": 1}).to_list()
    metrics = campaign_metrics(labels, selected_positions)
    summary = campaign_summary(selected, cost=scenario["cost"], value=scenario["value"])
    return {"mode": mode, "capacity": scenario["capacity"], **summary, **metrics}, selected


def calibration_bins(labels, probabilities) -> list[dict]:
    frame = pd.DataFrame({"label": list(labels), "probability": probabilities})
    # Fixed bins avoid looking at test labels or adapting a calibration model.
    frame["bin"] = pd.cut(
        frame["probability"], bins=[0, 0.05, 0.1, 0.2, 0.4, 0.6, 0.8, 1], include_lowest=True
    )
    results = []
    for interval, group in frame.groupby("bin", observed=True):
        results.append(
            {
                "bin": str(interval),
                "count": len(group),
                "mean_probability": float(group["probability"].mean()),
                "observed_rate": float(group["label"].mean()),
            }
        )
    return results


def run_comparison() -> dict:
    config = json.loads(CONFIG.read_text())
    validate_config(config)
    if (REPORTS / "selection_freeze.json").exists():
        raise ValueError(
            "This comparison is already frozen. Reuse its results; do not retune after the test release."
        )
    acquisition = verify_manifest(Path("data/raw"))
    frame = validate_dataset(Path("data/raw") / CSV_NAME, expected_rows=41188)
    assignments, split_manifest = freeze_splits(
        frame,
        csv_sha256=acquisition["files"][CSV_NAME]["sha256"],
        assignment_path=Path("data/processed/split_assignments.csv"),
        manifest_path=REPORTS / "split_manifest.json",
    )

    def partition(name: str) -> pd.DataFrame:
        ids = assignments.loc[(assignments["split"] == name) & assignments["included"], "row_id"]
        return frame.iloc[ids.to_list()]

    training, validation = partition("train"), partition("validation")
    x_train, x_validation = select_features(training), select_features(validation)
    y_train, y_validation = (
        part["y"].map({"no": 0, "yes": 1}).astype(int) for part in (training, validation)
    )
    if y_train.nunique() != 2 or y_validation.nunique() != 2:
        raise ValueError("Both classes are required in training and validation.")
    models = build_candidates(config)
    results, probabilities_by_model = [], {}
    for name, model in models.items():
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(x_train, y_train)
        probabilities = model.predict_proba(x_validation)[:, list(model.classes_).index(1)]
        probabilities_by_model[name] = probabilities
        results.append(
            {
                "model": name,
                "evaluation_split": "validation",
                **probability_metrics(y_validation, probabilities),
            }
        )
    winner = choose_model(results)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    selected_path = ARTIFACTS / "selected_model.pkl"
    with selected_path.open("wb") as handle:
        pickle.dump(models[winner], handle)
    prediction_frame = pd.DataFrame(
        {"row_id": validation.index.to_list(), **probabilities_by_model}
    )
    prediction_frame.to_csv(ARTIFACTS / "validation_predictions.csv", index=False)
    pd.DataFrame(
        {
            "row_id": validation.index.to_list(),
            "subscription_probability": probabilities_by_model[winner],
        }
    ).to_csv(ARTIFACTS / "selected_validation_predictions.csv", index=False, float_format="%.12f")
    campaign_results, random_results, rule_results = [], [], []
    for capacity in config["capacities"]:
        scenario = {**config["scenario"], "capacity": capacity}
        for name, probabilities in probabilities_by_model.items():
            result, _ = selection_analysis(validation, probabilities, scenario, mode="capacity")
            campaign_results.append({"model": name, **result})
        random_results.append(
            random_benchmark(
                y_validation,
                capacity=capacity,
                repetitions=config["random_repetitions"],
                seed=config["seed"],
            )
        )
        rule_results.append(
            {
                "capacity": capacity,
                **campaign_metrics(y_validation, business_rule_positions(validation, capacity)),
            }
        )
    scenario_results = []
    for mode in ("capacity", "positive_value"):
        result, selected = selection_analysis(
            validation, probabilities_by_model[winner], config["scenario"], mode=mode
        )
        scenario_results.append(result)
        selected.to_csv(ARTIFACTS / f"validation_selection_{mode}.csv", index=False)
    calibration = calibration_bins(y_validation, probabilities_by_model[winner])
    pd.DataFrame(results).to_csv(REPORTS / "model_comparison.csv", index=False)
    pd.DataFrame(campaign_results).to_csv(REPORTS / "campaign_validation.csv", index=False)
    pd.DataFrame(random_results).to_csv(REPORTS / "random_validation.csv", index=False)
    pd.DataFrame(rule_results).to_csv(REPORTS / "business_rule_validation.csv", index=False)
    pd.DataFrame(calibration).to_csv(REPORTS / "calibration_validation.csv", index=False)
    metadata = {
        "run_id": config["run_id"],
        "selected_model": winner,
        "selection_rule": config["selection_rule"],
        "features": list(BASELINE_FEATURES),
        "training_records": len(training),
        "validation_records": len(validation),
        "training_positive_rate": float(y_train.mean()),
        "validation_positive_rate": float(y_validation.mean()),
        "config": config,
        "model_parameters": {
            name: model.get_params(deep=False)
            if name == "dummy_prior"
            else model.named_steps["model"].get_params()
            for name, model in models.items()
        },
        "versions": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "sklearn": sklearn.__version__,
            "lightgbm": lightgbm.__version__,
        },
        "source_csv_sha256": acquisition["files"][CSV_NAME]["sha256"],
        "split_assignment_sha256": split_manifest["assignment_sha256"],
        "source_code_sha256": {name: file_hash(Path(name)) for name in SOURCE_FILES},
        "selected_model_path": str(selected_path),
        "selected_model_sha256": file_hash(selected_path),
        "validation_predictions_sha256": file_hash(ARTIFACTS / "validation_predictions.csv"),
        "selected_validation_predictions_sha256": file_hash(
            ARTIFACTS / "selected_validation_predictions.csv"
        ),
        "results": results,
        "scenario_results": scenario_results,
        "random_results": random_results,
        "business_rule_results": rule_results,
        "calibration": "No calibration fitted; validation diagnostic bins reported, not used to change probabilities.",
        "test_evaluated": False,
    }
    content = json.dumps(metadata, indent=2, allow_nan=False) + "\n"
    (REPORTS / "comparison_manifest.json").write_text(content)
    (ARTIFACTS / "metadata.json").write_text(content)
    write_comparison_report(metadata, REPORTS / "comparison_report.md")
    return metadata


def write_comparison_report(metadata: dict, destination: Path) -> None:
    lines = [
        "# Stage 4 — validation model and campaign comparison",
        "",
        f"Selected model: **{metadata['selected_model']}**.",
        "Selection uses validation Average Precision descending, then Brier ascending, then name.",
        "Candidate configurations, capacity points and the history rule were declared before fitting in configs/comparison.json.",
        "Models fit only training data and use the same nine approved fields. No test predictions were made in comparison.",
        "",
        "## Validation probabilities",
        "",
        "| Model | AP ↑ | ROC-AUC ↑ | Brier ↓ |",
        "|---|---:|---:|---:|",
    ]
    for result in metadata["results"]:
        lines.append(
            f"| {result['model']} | {result['average_precision']:.6f} | {result['roc_auc']:.6f} | {result['brier_score']:.6f} |"
        )
    lines += [
        "",
        "## Selected-model scenario",
        "",
        f"Assumptions: {metadata['config']['scenario']}.",
        "These are user-defined units, not actual banking revenue/cost or causal uplift.",
        "",
        "| Mode | Selected | Historical subscriptions | Precision | Lift | Expected subscriptions | Assumed net value |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in metadata["scenario_results"]:
        precision = "undefined" if row["precision"] is None else f"{row['precision']:.4f}"
        lift = "undefined" if row["lift"] is None else f"{row['lift']:.3f}"
        lines.append(
            f"| {row['mode']} | {row['selected_count']} | {row['observed_subscriptions']} | {precision} | {lift} | {row['expected_subscriptions']:.2f} | {row['assumed_net_value']:.2f} |"
        )
    lines += [
        "",
        "Capacity mode takes at most K by score; positive_value additionally requires p*V-C>0.",
        "Ties break by numeric row_id. Selection itself never reads target labels.",
        "Observed subscriptions are historical outcomes, not conversions caused by this selection.",
        "",
        "## Random and fixed-history benchmarks",
        "",
        "Random selection repeats 200 times per capacity with seed 42. The 5th/95th percentiles describe selection variability, not confidence bounds on model performance.",
        "The fixed rule ranks prior-campaign success, then previous-contact count, then row_id. It is a transparent comparison, not a validated banking policy.",
        "",
        "| Capacity | Random observed mean | Random p05 | Random p95 | Fixed-rule observed |",
        "|---|---:|---:|---:|---:|",
    ]
    for random_row, rule_row in zip(
        metadata["random_results"], metadata["business_rule_results"], strict=True
    ):
        lines.append(
            f"| {random_row['capacity']} | {random_row['observed_mean']:.2f} | {random_row['observed_p05']:.1f} | {random_row['observed_p95']:.1f} | {rule_row['observed_subscriptions']} |"
        )
    lines += [
        "",
        "## Probability and evaluation limits",
        "",
        "See calibration_validation.csv for fixed probability-bin diagnostics. No calibrator was fitted in this bounded experiment.",
        "Ranking selection optimizes validation AP, not precision at a specific K, financial return or probability calibration. Treat monetary estimates as exploratory scenarios.",
        "The AP winner is not the precision@500 winner; see campaign_validation.csv. No top-500-optimal claim is made.",
        "The constant-score dummy uses row_id to break ties and is not a randomized campaign baseline; use the repeated random benchmark for that comparison.",
        "The historical training/validation prevalence shift remains; no fit uses validation or test labels.",
        "Chronological source order is documented, but exact dates/customer-disjointness cannot be verified.",
        "The final selection freeze must be written before a single held-out evaluation. That report is a separate final check; it must not drive retuning.",
        "",
    ]
    destination.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        metadata = run_comparison()
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Comparison failed: {exc}\n")
    print(pd.DataFrame(metadata["results"]).to_string(index=False))
    print(f"Selected on validation: {metadata['selected_model']}. Test not evaluated.")


if __name__ == "__main__":
    main()
