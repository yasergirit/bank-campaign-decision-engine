from __future__ import annotations

import fcntl
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

import bank_campaign.campaign as campaign
import bank_campaign.comparison as comparison
import bank_campaign.holdout as holdout
from bank_campaign.data.download import CSV_NAME


def _synthetic_frame(size: int = 50) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "age": [25 + row for row in range(size)],
            "job": ["admin." if row % 2 else "technician" for row in range(size)],
            "marital": ["single" if row % 3 else "married" for row in range(size)],
            "education": ["university.degree" if row % 2 else "high.school" for row in range(size)],
            "default": ["no"] * size,
            "housing": ["yes" if row % 2 else "no" for row in range(size)],
            "loan": ["yes" if row % 5 == 0 else "no" for row in range(size)],
            "previous": [row % 4 for row in range(size)],
            "poutcome": ["success" if row % 7 == 0 else "nonexistent" for row in range(size)],
            "duration": [100 + row for row in range(size)],
            "y": ["yes" if row % 3 == 0 else "no" for row in range(size)],
        }
    )


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        (
            [
                {"model": "a", "average_precision": 0.6, "brier_score": 0.3},
                {"model": "b", "average_precision": 0.7, "brier_score": 0.4},
            ],
            "b",
        ),
        (
            [
                {"model": "a", "average_precision": 0.7, "brier_score": 0.3},
                {"model": "b", "average_precision": 0.7, "brier_score": 0.2},
            ],
            "b",
        ),
        (
            [
                {"model": "z", "average_precision": 0.7, "brier_score": 0.2},
                {"model": "a", "average_precision": 0.7, "brier_score": 0.2},
            ],
            "a",
        ),
    ],
)
def test_choose_model_ap_then_brier_then_name(results: list[dict], expected: str) -> None:
    assert comparison.choose_model(results) == expected


@pytest.mark.parametrize(
    "results",
    [[], [{"model": "a", "average_precision": None, "brier_score": 0.2}]],
)
def test_choose_model_requires_defined_results(results: list[dict]) -> None:
    with pytest.raises(ValueError):
        comparison.choose_model(results)


@pytest.mark.parametrize(
    "metrics",
    [
        {"average_precision": float("nan"), "brier_score": 0.2},
        {"average_precision": 0.7, "brier_score": float("inf")},
    ],
)
def test_choose_model_rejects_nonfinite_metrics(metrics: dict) -> None:
    with pytest.raises(ValueError, match="finite"):
        comparison.choose_model([{"model": "candidate", **metrics}])


def test_config_rejects_candidate_name_collisions_and_zero_repetitions() -> None:
    config = json.loads(comparison.CONFIG.read_text())
    config["lightgbm_candidates"] = [
        {"name": "lightgbm_same", "num_leaves": 3, "n_estimators": 5},
        {"name": "lightgbm_same", "num_leaves": 7, "n_estimators": 5},
    ]
    with pytest.raises(ValueError, match="unique"):
        comparison.validate_config(config)

    config["lightgbm_candidates"] = [{"name": "lightgbm_one", "num_leaves": 3, "n_estimators": 5}]
    config["random_repetitions"] = 0
    with pytest.raises(ValueError, match="1..10000"):
        comparison.validate_config(config)


def test_random_benchmark_is_reproducible() -> None:
    labels = [0, 1, 0, 1, 1, 0, 0, 1]

    first = comparison.random_benchmark(labels, capacity=3, repetitions=25, seed=42)
    second = comparison.random_benchmark(labels, capacity=3, repetitions=25, seed=42)

    assert first == second
    assert first["selected_count"] == 3
    assert first["repetitions"] == 25


def test_business_rule_positions_do_not_depend_on_target() -> None:
    frame = pd.DataFrame(
        {
            "previous": [1, 3, 2, 0],
            "poutcome": ["failure", "success", "success", "nonexistent"],
            "y": ["no", "no", "yes", "yes"],
        },
        index=[30, 20, 10, 40],
    )
    changed_targets = frame.assign(y=["yes", "yes", "no", "no"])

    assert comparison.business_rule_positions(frame, 3) == [1, 2, 0]
    assert comparison.business_rule_positions(changed_targets, 3) == [1, 2, 0]


def _configure_isolated_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    frame = _synthetic_frame()
    config = json.loads(comparison.CONFIG.read_text())
    config.update(
        {
            "random_repetitions": 10,
            "capacities": [2, 5],
            "scenario": {"capacity": 5, "cost": 3.0, "value": 100.0},
            "lightgbm_candidates": [{"name": "lightgbm_tiny", "num_leaves": 3, "n_estimators": 5}],
        }
    )
    config["lightgbm_common"].update({"min_child_samples": 5})
    config_path = tmp_path / "comparison.json"
    config_path.write_text(json.dumps(config))
    reports = tmp_path / "reports"
    artifacts = tmp_path / "artifacts"
    source_marker = tmp_path / "frozen_source.txt"
    source_marker.write_text("frozen source v1")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(comparison, "CONFIG", config_path)
    monkeypatch.setattr(comparison, "REPORTS", reports)
    monkeypatch.setattr(comparison, "ARTIFACTS", artifacts)
    monkeypatch.setattr(comparison, "SOURCE_FILES", (str(source_marker),))
    monkeypatch.setattr(holdout, "REPORTS", reports)
    monkeypatch.setattr(holdout, "ARTIFACTS", artifacts)
    monkeypatch.setattr(holdout, "FREEZE", reports / "selection_freeze.json")
    monkeypatch.setattr(holdout, "TEST_REPORT", reports / "test_metrics.json")
    monkeypatch.setattr(campaign, "REPORTS", reports)
    monkeypatch.setattr(campaign, "ARTIFACTS", artifacts)

    manifest = {"files": {CSV_NAME: {"sha256": "synthetic-source"}}}
    monkeypatch.setattr(comparison, "verify_manifest", lambda _: manifest)
    monkeypatch.setattr(holdout, "verify_manifest", lambda _: manifest)
    monkeypatch.setattr(comparison, "validate_dataset", lambda *args, **kwargs: frame.copy())
    monkeypatch.setattr(holdout, "validate_dataset", lambda *args, **kwargs: frame.copy())
    return source_marker, artifacts / "selected_model.pkl"


def test_comparison_freeze_single_holdout_and_cache_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_marker, model_path = _configure_isolated_run(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="Freeze model and scenario first"):
        holdout.evaluate_holdout()

    comparison_metadata = comparison.run_comparison()
    assert comparison_metadata["test_evaluated"] is False
    assert comparison_metadata["validation_records"] == 10
    assert Path(comparison_metadata["selected_model_path"]) == model_path
    assert not holdout.FREEZE.exists()
    assert not holdout.TEST_REPORT.exists()
    monkeypatch.setattr(sys, "argv", ["bank-campaign"])
    with pytest.raises(SystemExit) as exit_info:
        campaign.main()
    assert exit_info.value.code == 1

    frozen = holdout.freeze_selection()
    assert frozen["test_was_evaluated_at_freeze"] is False
    assert (
        frozen["selected_validation_predictions_sha256"]
        == comparison_metadata["selected_validation_predictions_sha256"]
    )
    assert holdout.FREEZE.exists()
    with pytest.raises(ValueError, match="already frozen"):
        comparison.run_comparison()

    selected_validation_path = holdout.ARTIFACTS / "selected_validation_predictions.csv"
    model_path.unlink()
    selected_validation_path.unlink()
    with monkeypatch.context() as restore_context:
        restore_context.setattr(
            holdout,
            "probability_metrics",
            lambda *args, **kwargs: pytest.fail("restore must not calculate test metrics"),
        )
        restore_context.setattr(
            holdout,
            "selection_analysis",
            lambda *args, **kwargs: pytest.fail("restore must not evaluate a campaign"),
        )
        holdout.restore_artifacts()
    assert model_path.exists()
    assert (
        comparison.file_hash(selected_validation_path)
        == frozen["selected_validation_predictions_sha256"]
    )
    restored_validation = pd.read_csv(selected_validation_path)
    assert restored_validation["row_id"].tolist() == list(range(30, 40))
    ledger = json.loads((holdout.ARTIFACTS / "restoration.json").read_text())
    assert ledger["selection_freeze_sha256"] == comparison.file_hash(holdout.FREEZE)
    assert ledger["restored_model_sha256"] == comparison.file_hash(model_path)
    assert (
        ledger["verified_validation_predictions_sha256"]
        == frozen["selected_validation_predictions_sha256"]
    )
    holdout.verify_frozen_inputs(frozen)

    lock_path = holdout.ARTIFACTS / "holdout.lock"
    with lock_path.open("a") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="Another held-out evaluation is active"):
            holdout.evaluate_holdout()
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    result = holdout.evaluate_holdout()
    assert result["evaluation_split"] == "test"
    assert result["test_records"] == 10
    assert result["selected_model"] == comparison_metadata["selected_model"]
    assert holdout.TEST_REPORT.exists()
    test_report_md = holdout.REPORTS / "test_report.md"
    assert test_report_md.exists()
    prediction_cache = holdout.ARTIFACTS / "test_prediction_cache.json"
    attempt_marker = holdout.ARTIFACTS / "test_attempt.json"
    completion_ledger = holdout.REPORTS / "test_release.json"
    assert prediction_cache.exists()
    assert attempt_marker.exists()
    assert completion_ledger.exists()
    assert not prediction_cache.with_suffix(".json.tmp").exists()
    assert not holdout.TEST_REPORT.with_suffix(".json.tmp").exists()
    release = json.loads(completion_ledger.read_text())
    original_completion_ledger = completion_ledger.read_bytes()
    assert release == {
        "selection_freeze_sha256": comparison.file_hash(holdout.FREEZE),
        "test_metrics_sha256": comparison.file_hash(holdout.TEST_REPORT),
        "prediction_cache_sha256": comparison.file_hash(prediction_cache),
    }

    monkeypatch.setattr(
        holdout.pickle,
        "load",
        lambda _: pytest.fail("checkpoint recovery must not reload or predict with the model"),
    )
    original_test_metrics = holdout.TEST_REPORT.read_bytes()
    corrupted_metrics = json.loads(original_test_metrics)
    corrupted_metrics["model_metrics"]["brier_score"] = 0.999
    assert corrupted_metrics["selection_freeze_sha256"] == result["selection_freeze_sha256"]
    holdout.TEST_REPORT.write_text(json.dumps(corrupted_metrics, indent=2) + "\n")
    with pytest.raises(ValueError, match="Cached test metrics differ from the recorded release"):
        holdout.evaluate_holdout()
    holdout.TEST_REPORT.write_bytes(original_test_metrics)

    original_prediction_cache = prediction_cache.read_bytes()
    corrupted_predictions = json.loads(original_prediction_cache)
    corrupted_predictions["probabilities"][0] = 0.999
    prediction_cache.write_text(json.dumps(corrupted_predictions, indent=2) + "\n")
    with pytest.raises(
        ValueError, match="Cached test predictions differ from the recorded release"
    ):
        holdout.evaluate_holdout()
    prediction_cache.write_bytes(original_prediction_cache)

    holdout.TEST_REPORT.unlink()
    test_report_md.unlink()
    assert holdout.evaluate_holdout() == result
    assert holdout.TEST_REPORT.exists()
    assert test_report_md.exists()

    test_report_md.unlink()
    assert holdout.evaluate_holdout() == result
    assert test_report_md.exists()

    holdout.TEST_REPORT.unlink()
    prediction_cache.unlink()
    completion_ledger.unlink()
    with pytest.raises(ValueError, match="Interrupted test attempt"):
        holdout.evaluate_holdout()

    completion_ledger.write_bytes(original_completion_ledger)
    attempt_marker.unlink()
    with pytest.raises(ValueError, match="already released; restore recorded metrics"):
        holdout.evaluate_holdout()

    source_marker.write_text("tampered source")
    with pytest.raises(ValueError, match="Frozen code/config/lockfile changed"):
        holdout.evaluate_holdout()
    source_marker.write_text("frozen source v1")

    model_path.write_bytes(model_path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="Frozen model artifact differs"):
        holdout.evaluate_holdout()
