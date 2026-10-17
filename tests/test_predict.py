"""Trusted model loading and scoring tests."""

import json
import pickle
import platform
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from bank_campaign.data.schema import BASELINE_FEATURES
from bank_campaign.predict import load_predictor
from bank_campaign.schemas import Candidate


class BundleModel:
    """Small pickle-safe estimator double with the serving-time estimator contract."""

    classes_ = np.array([0, 1])
    feature_names_in_ = np.array(BASELINE_FEATURES)

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        probabilities = np.array([min(0.99, float(age) / 100) for age in frame["age"]])
        return np.column_stack((1 - probabilities, probabilities))


def candidate(row_id: int, age: int) -> Candidate:
    return Candidate(
        row_id=row_id,
        age=age,
        job="admin.",
        marital="married",
        education="university.degree",
        default="no",
        housing="yes",
        loan="no",
        previous=1,
        poutcome="nonexistent",
    )


def write_bundle(root: Path) -> dict[str, Any]:
    artifact_path = root / "artifacts/comparison-v1/selected_model.pkl"
    artifact_path.parent.mkdir(parents=True)
    artifact = pickle.dumps(BundleModel())
    artifact_path.write_bytes(artifact)

    manifest = {
        "run_id": "comparison-v1",
        "selected_model": "fake-model",
        "features": list(BASELINE_FEATURES),
        "versions": {
            "python": platform.python_version(),
            "pandas": version("pandas"),
            "sklearn": version("scikit-learn"),
            "lightgbm": version("lightgbm"),
        },
    }
    manifest_bytes = json.dumps(manifest).encode()
    reports = root / "reports"
    reports.mkdir()
    (reports / "comparison_manifest.json").write_bytes(manifest_bytes)

    freeze = {
        "run_id": "comparison-v1",
        "selected_model": "fake-model",
        "features": list(BASELINE_FEATURES),
        "comparison_manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "selected_model_path": "artifacts/comparison-v1/selected_model.pkl",
        "selected_model_sha256": sha256(artifact).hexdigest(),
        "selected_validation_predictions_sha256": "b" * 64,
    }
    (reports / "selection_freeze.json").write_text(json.dumps(freeze))
    return freeze


def test_load_predictor_verifies_bundle_and_scores_in_request_order(tmp_path: Path) -> None:
    freeze = write_bundle(tmp_path)

    predictor = load_predictor(tmp_path)
    scored = predictor.score([candidate(9, 80), candidate(2, 20)])

    assert predictor.model_name == "fake-model"
    assert (
        predictor.model_version
        == f"comparison-v1:fake-model:{freeze['selected_model_sha256'][:12]}"
    )
    assert predictor.artifact_sha256 == freeze["selected_model_sha256"]
    assert [record.row_id for record in scored] == [9, 2]
    assert [record.subscription_probability for record in scored] == pytest.approx([0.8, 0.2])


def test_artifact_hash_is_checked_before_unpickling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_bundle(tmp_path)
    (tmp_path / "artifacts/comparison-v1/selected_model.pkl").write_bytes(b"this is not a pickle")
    unpickle_called = False

    def forbidden_unpickle(_payload: bytes) -> object:
        nonlocal unpickle_called
        unpickle_called = True
        raise AssertionError("untrusted bytes reached pickle.loads")

    monkeypatch.setattr("bank_campaign.predict.pickle.loads", forbidden_unpickle)

    with pytest.raises(ValueError, match="trusted model bundle"):
        load_predictor(tmp_path)

    assert unpickle_called is False


def test_forged_restoration_cannot_authorize_changed_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze = write_bundle(tmp_path)
    changed_artifact = pickle.dumps(BundleModel()) + b"changed"
    (tmp_path / "artifacts/comparison-v1/selected_model.pkl").write_bytes(changed_artifact)
    (tmp_path / "artifacts/comparison-v1/restoration.json").write_text(
        json.dumps(
            {
                "selection_freeze_sha256": sha256(
                    (tmp_path / "reports/selection_freeze.json").read_bytes()
                ).hexdigest(),
                "restored_model_sha256": sha256(changed_artifact).hexdigest(),
                "verified_validation_predictions_sha256": freeze[
                    "selected_validation_predictions_sha256"
                ],
            }
        )
    )
    unpickle_called = False

    def forbidden_unpickle(_payload: bytes) -> object:
        nonlocal unpickle_called
        unpickle_called = True
        raise AssertionError("changed artifact reached pickle.loads")

    monkeypatch.setattr("bank_campaign.predict.pickle.loads", forbidden_unpickle)

    with pytest.raises(ValueError, match="artifact differs from the frozen hash"):
        load_predictor(tmp_path)

    assert unpickle_called is False


def test_changed_manifest_is_rejected_before_unpickling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_bundle(tmp_path)
    manifest_path = tmp_path / "reports/comparison_manifest.json"
    manifest_path.write_text(manifest_path.read_text() + " ")
    unpickle_called = False

    def forbidden_unpickle(_payload: bytes) -> object:
        nonlocal unpickle_called
        unpickle_called = True
        raise AssertionError("untrusted bytes reached pickle.loads")

    monkeypatch.setattr("bank_campaign.predict.pickle.loads", forbidden_unpickle)

    with pytest.raises(ValueError, match="metadata differs"):
        load_predictor(tmp_path)

    assert unpickle_called is False


def test_unrecorded_artifact_path_is_rejected_before_file_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze = write_bundle(tmp_path)
    freeze["selected_model_path"] = "../../untrusted.pkl"
    (tmp_path / "reports/selection_freeze.json").write_text(json.dumps(freeze))
    unpickle_called = False

    def forbidden_unpickle(_payload: bytes) -> object:
        nonlocal unpickle_called
        unpickle_called = True
        raise AssertionError("untrusted bytes reached pickle.loads")

    monkeypatch.setattr("bank_campaign.predict.pickle.loads", forbidden_unpickle)

    with pytest.raises(ValueError, match="recorded local model artifact"):
        load_predictor(tmp_path)

    assert unpickle_called is False


@pytest.mark.parametrize(
    "missing_path",
    [
        "reports/selection_freeze.json",
        "reports/comparison_manifest.json",
        "artifacts/comparison-v1/selected_model.pkl",
    ],
)
def test_missing_bundle_files_are_reported_as_invalid(tmp_path: Path, missing_path: str) -> None:
    write_bundle(tmp_path)
    (tmp_path / missing_path).unlink()

    with pytest.raises(ValueError, match="trusted model bundle"):
        load_predictor(tmp_path)


def test_corrupt_artifact_with_matching_hash_is_rejected(tmp_path: Path) -> None:
    freeze = write_bundle(tmp_path)
    artifact_path = tmp_path / "artifacts/comparison-v1/selected_model.pkl"
    artifact_path.write_bytes(b"not a pickle")
    freeze["selected_model_sha256"] = sha256(b"not a pickle").hexdigest()
    (tmp_path / "reports/selection_freeze.json").write_text(json.dumps(freeze))

    with pytest.raises(ValueError, match="trusted model bundle"):
        load_predictor(tmp_path)


def test_feature_contract_mismatch_is_rejected(tmp_path: Path) -> None:
    freeze = write_bundle(tmp_path)
    freeze["features"] = list(BASELINE_FEATURES[:-1])
    (tmp_path / "reports/selection_freeze.json").write_text(json.dumps(freeze))

    with pytest.raises(ValueError, match="feature/version contract"):
        load_predictor(tmp_path)
