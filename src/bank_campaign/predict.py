"""Load the trusted frozen model once and score the approved pre-call fields."""

import json
import math
import pickle
import platform
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pandas as pd

from bank_campaign.data.schema import BASELINE_FEATURES
from bank_campaign.features import select_features
from bank_campaign.schemas import Candidate, ScoredRecord

ROOT = Path(__file__).resolve().parents[2]


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


@dataclass
class Predictor:
    model: Any
    model_name: str
    model_version: str
    artifact_sha256: str
    features: tuple[str, ...]

    def score(self, records: list[Candidate]) -> list[ScoredRecord]:
        if not records:
            return []
        frame = pd.DataFrame([record.model_dump() for record in records])
        matrix = self.model.predict_proba(select_features(frame))
        if getattr(matrix, "shape", None) != (len(records), 2):
            raise ValueError("Model returned an invalid probability shape.")
        probabilities = matrix[:, list(self.model.classes_).index(1)]
        if any(not math.isfinite(float(p)) or not 0 <= float(p) <= 1 for p in probabilities):
            raise ValueError("Model returned invalid probabilities.")
        return [
            ScoredRecord(
                row_id=record.row_id,
                subscription_probability=float(probability),
                model_version=self.model_version,
            )
            for record, probability in zip(records, probabilities, strict=True)
        ]


def load_predictor(root: Path = ROOT) -> Predictor:
    """Verify bytes/provenance before unpickling; no training, raw data or SQL required."""
    try:
        freeze_bytes = (root / "reports/selection_freeze.json").read_bytes()
        freeze = json.loads(freeze_bytes)
        metadata_bytes = (root / "reports/comparison_manifest.json").read_bytes()
        metadata = json.loads(metadata_bytes)
        if _digest(metadata_bytes) != freeze["comparison_manifest_sha256"]:
            raise ValueError("Comparison metadata differs from the frozen model.")
        if (
            tuple(freeze["features"]) != BASELINE_FEATURES
            or tuple(metadata["features"]) != BASELINE_FEATURES
            or freeze["selected_model"] != metadata["selected_model"]
            or freeze["run_id"] != metadata["run_id"]
        ):
            raise ValueError("Frozen model metadata violates the feature/version contract.")
        for package, key in (
            ("scikit-learn", "sklearn"),
            ("pandas", "pandas"),
            ("lightgbm", "lightgbm"),
        ):
            if version(package) != metadata["versions"][key]:
                raise ValueError(
                    f"Serving {package} version differs from the recorded model environment."
                )
        if (
            platform.python_version().split(".")[:2]
            != metadata["versions"]["python"].split(".")[:2]
        ):
            raise ValueError(
                "Serving Python major/minor differs from the recorded model environment."
            )
        expected_path = Path("artifacts/comparison-v1/selected_model.pkl")
        if Path(freeze["selected_model_path"]) != expected_path:
            raise ValueError("Only the recorded local model artifact is supported.")
        artifact = (root / expected_path).read_bytes()
        artifact_sha = _digest(artifact)
        if artifact_sha != freeze["selected_model_sha256"]:
            raise ValueError("The trusted model bundle artifact differs from the frozen hash.")
        # Deserialize the same bytes that were hashed, rather than reopening a mutable path.
        model = pickle.loads(artifact)
        if (
            not callable(getattr(model, "predict_proba", None))
            or list(model.classes_) != [0, 1]
            or tuple(model.feature_names_in_) != BASELINE_FEATURES
        ):
            raise ValueError("Loaded estimator violates the binary probability/feature contract.")
        return Predictor(
            model=model,
            model_name=freeze["selected_model"],
            model_version=f"{freeze['run_id']}:{freeze['selected_model']}:{freeze['selected_model_sha256'][:12]}",
            artifact_sha256=artifact_sha,
            features=BASELINE_FEATURES,
        )
    except (
        OSError,
        KeyError,
        TypeError,
        AttributeError,
        ImportError,
        EOFError,
        pickle.UnpicklingError,
        PackageNotFoundError,
    ) as exc:
        raise ValueError("The trusted model bundle is missing, incompatible, or invalid.") from exc
