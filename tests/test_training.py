"""Integration checks on synthetic data; do not use the real held-out labels."""

import json
import pickle
from pathlib import Path

import pandas as pd
import pytest

from bank_campaign.data.split import freeze_splits
from bank_campaign.features import select_features
from bank_campaign.train import train_baselines


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "age": [20 + i for i in range(40)],
            "job": ["admin."] * 40,
            "marital": ["single"] * 40,
            "education": ["high.school"] * 40,
            "default": ["no"] * 40,
            "housing": ["yes"] * 40,
            "loan": ["no"] * 40,
            "previous": [i % 3 for i in range(40)],
            "poutcome": ["nonexistent"] * 40,
            "duration": [100 + i for i in range(40)],
            "y": ["yes" if i % 3 == 0 else "no" for i in range(40)],
        }
    )


def test_frozen_assignments_refuse_changes_and_reuse_identical_data(tmp_path: Path) -> None:
    arguments = {
        "csv_sha256": "synthetic",
        "assignment_path": tmp_path / "assignments.csv",
        "manifest_path": tmp_path / "manifest.json",
    }
    frame = _frame()
    first, manifest = freeze_splits(frame, **arguments)
    second, repeated = freeze_splits(frame, **arguments)
    pd.testing.assert_frame_equal(first, second)
    assert manifest == repeated
    (tmp_path / "assignments.csv").write_text("tampered")
    with pytest.raises(ValueError, match="Frozen split"):
        freeze_splits(frame, **arguments)


def test_training_does_not_depend_on_test_labels_and_saved_pipeline_reloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bank_campaign.train as training_module

    frame = _frame()
    monkeypatch.setattr(
        training_module,
        "verify_manifest",
        lambda _: {
            "files": {"bank-additional-full.csv": {"sha256": "synthetic"}},
        },
    )
    monkeypatch.setattr(training_module, "validate_dataset", lambda *args, **kwargs: frame.copy())
    first = train_baselines(
        report_dir=tmp_path / "first/reports",
        processed_dir=tmp_path / "first/processed",
        artifact_dir=tmp_path / "first/artifacts",
    )
    # Change only the original final 20% labels; validation metrics must not change.
    frame.loc[32:, "y"] = frame.loc[32:, "y"].map({"yes": "no", "no": "yes"})
    second = train_baselines(
        report_dir=tmp_path / "second/reports",
        processed_dir=tmp_path / "second/processed",
        artifact_dir=tmp_path / "second/artifacts",
    )
    assert first["results"] == second["results"]
    assert first["test_evaluated"] is False
    assert second["test_evaluated"] is False
    with (tmp_path / "first/artifacts/logistic_regression.pkl").open("rb") as handle:
        saved_pipeline = pickle.load(handle)
    saved = pd.read_csv(tmp_path / "first/artifacts/validation_predictions.csv")
    probabilities = saved_pipeline.predict_proba(select_features(frame.iloc[24:32]))[:, 1]
    assert probabilities == pytest.approx(saved["logistic_regression"].to_numpy())
    metadata = json.loads((tmp_path / "first/artifacts/metadata.json").read_text())
    assert metadata["split_assignment_sha256"]
    assert metadata["source_code_sha256"]


def test_clean_checkout_reconstructs_assignments_only_after_manifest_matches(
    tmp_path: Path,
) -> None:
    arguments = {
        "csv_sha256": "synthetic",
        "assignment_path": tmp_path / "processed/assignments.csv",
        "manifest_path": tmp_path / "reports/manifest.json",
    }
    original, _ = freeze_splits(_frame(), **arguments)
    assignment_path = arguments["assignment_path"]
    expected_bytes = assignment_path.read_bytes()
    assignment_path.unlink()
    restored, _ = freeze_splits(_frame(), **arguments)
    pd.testing.assert_frame_equal(original, restored)
    assert assignment_path.read_bytes() == expected_bytes
    assignment_path.unlink()
    with pytest.raises(ValueError, match="manifest differs"):
        freeze_splits(_frame(), **{**arguments, "csv_sha256": "different source"})
    assert not assignment_path.exists()
