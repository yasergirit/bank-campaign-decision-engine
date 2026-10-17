from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from bank_campaign.data.schema import BASELINE_FEATURES
from bank_campaign.data.split import build_assignments
from bank_campaign.evaluate import probability_metrics
from bank_campaign.features import build_pipeline, select_features

NUMERIC_FEATURES = ("age", "previous")
CATEGORICAL_FEATURES = tuple(
    feature for feature in BASELINE_FEATURES if feature not in NUMERIC_FEATURES
)


def _assignment_frame(size: int = 20) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "age": [25 + row for row in range(size)],
            "job": ["admin." if row % 2 == 0 else "technician" for row in range(size)],
            "duration": [100 + row for row in range(size)],
            "y": ["yes" if row % 3 == 0 else "no" for row in range(size)],
        },
        index=[1000 + 7 * row for row in range(size)],
    )


def _baseline_frame(*, jobs: list[str], ages: list[int], previous: list[int]) -> pd.DataFrame:
    size = len(jobs)
    return pd.DataFrame(
        {
            "age": ages,
            "job": jobs,
            "marital": ["married", "single"] * (size // 2) + ["divorced"] * (size % 2),
            "education": ["university.degree"] * size,
            "default": ["no"] * size,
            "housing": ["yes", "no"] * (size // 2) + ["unknown"] * (size % 2),
            "loan": ["no"] * size,
            "previous": previous,
            "poutcome": ["nonexistent", "failure"] * (size // 2) + ["success"] * (size % 2),
            "duration": [999] * size,
            "y": ["yes", "no"] * (size // 2) + ["no"] * (size % 2),
        }
    )


def _find_step(transformer: object, expected_type: type[object]) -> object:
    if isinstance(transformer, expected_type):
        return transformer
    if isinstance(transformer, Pipeline):
        for step in transformer.named_steps.values():
            if isinstance(step, expected_type):
                return step
    raise AssertionError(f"No {expected_type.__name__} found in transformer")


def test_build_assignments_preserves_row_positions_and_fixed_split_boundaries() -> None:
    assignments = build_assignments(_assignment_frame())

    assert list(assignments.columns) == ["row_id", "split", "included", "duplicate_of"]
    assert assignments["row_id"].tolist() == list(range(20))
    assert assignments["split"].tolist() == ["train"] * 12 + ["validation"] * 4 + ["test"] * 4
    assert assignments["included"].tolist() == [True] * 20
    assert assignments["duplicate_of"].tolist() == [-1] * 20
    assert pd.api.types.is_integer_dtype(assignments["row_id"])
    assert pd.api.types.is_integer_dtype(assignments["duplicate_of"])


def test_build_assignments_accounts_for_every_row_in_disjoint_splits() -> None:
    assignments = build_assignments(_assignment_frame())

    assert len(assignments) == 20
    assert assignments["row_id"].is_unique
    assert set(assignments["split"]) == {"train", "validation", "test"}
    split_ids = {
        name: set(group["row_id"]) for name, group in assignments.groupby("split", observed=True)
    }
    assert split_ids["train"].isdisjoint(split_ids["validation"])
    assert split_ids["train"].isdisjoint(split_ids["test"])
    assert split_ids["validation"].isdisjoint(split_ids["test"])
    assert set().union(*split_ids.values()) == set(range(20))


def test_build_assignments_excludes_only_later_duplicate_even_across_splits() -> None:
    frame = _assignment_frame()
    frame.iloc[17] = frame.iloc[2]
    frame.iloc[17, frame.columns.get_loc("y")] = "yes"

    assignments = build_assignments(frame).set_index("row_id")

    assert assignments.loc[2, ["included", "duplicate_of"]].tolist() == [True, -1]
    assert assignments.loc[17, "split"] == "test"
    assert bool(assignments.loc[17, "included"]) is False
    assert assignments.loc[17, "duplicate_of"] == 2


def test_duplicate_detection_uses_all_predictors_not_only_baseline_features() -> None:
    frame = _assignment_frame()
    first_index, second_index = frame.index[:2]
    frame.loc[second_index, ["age", "job"]] = frame.loc[first_index, ["age", "job"]]
    frame.loc[second_index, "duration"] = frame.loc[first_index, "duration"] + 1

    assignments = build_assignments(frame).set_index("row_id")

    assert bool(assignments.loc[1, "included"]) is True
    assert assignments.loc[1, "duplicate_of"] == -1


def test_build_assignments_does_not_use_target_for_duplicate_or_split_logic() -> None:
    frame = _assignment_frame()
    before = build_assignments(frame)
    frame.loc[16:, "y"] = frame.loc[16:, "y"].map({"yes": "no", "no": "yes"})

    after = build_assignments(frame)

    pd.testing.assert_frame_equal(before, after)


@pytest.mark.parametrize("size", [0, 1, 4])
def test_build_assignments_rejects_frames_too_small_for_three_blocks(size: int) -> None:
    with pytest.raises(ValueError):
        build_assignments(_assignment_frame(size))


def test_build_assignments_requires_target_column() -> None:
    with pytest.raises(ValueError):
        build_assignments(_assignment_frame().drop(columns="y"))


def test_select_features_returns_only_ordered_baseline_columns() -> None:
    frame = _baseline_frame(jobs=["admin.", "technician"], ages=[30, 50], previous=[0, 2])

    selected = select_features(frame)

    assert tuple(selected.columns) == BASELINE_FEATURES
    assert "duration" not in selected
    assert "y" not in selected
    pd.testing.assert_series_equal(selected["age"], frame["age"])


def test_select_features_rejects_missing_required_feature() -> None:
    frame = _baseline_frame(jobs=["admin."], ages=[30], previous=[0]).drop(columns="housing")

    with pytest.raises(ValueError):
        select_features(frame)


def test_build_pipeline_has_explicit_preprocessing_and_logistic_model() -> None:
    pipeline = build_pipeline()

    assert isinstance(pipeline, Pipeline)
    assert list(pipeline.named_steps) == ["preprocess", "model"]
    assert isinstance(pipeline.named_steps["preprocess"], ColumnTransformer)
    model = pipeline.named_steps["model"]
    assert isinstance(model, LogisticRegression)
    assert model.class_weight is None
    assert model.random_state == 42
    assert model.max_iter >= 1_000

    feature_sets = {
        name: tuple(columns) for name, _, columns in pipeline["preprocess"].transformers
    }
    assert set(feature_sets.values()) == {NUMERIC_FEATURES, CATEGORICAL_FEATURES}


def test_pipeline_learns_scaling_and_categories_from_training_rows_only() -> None:
    train = _baseline_frame(
        jobs=["admin.", "technician", "admin.", "technician"],
        ages=[20, 30, 40, 50],
        previous=[0, 0, 2, 2],
    )
    validation = _baseline_frame(jobs=["student"], ages=[1_000], previous=[100])
    pipeline = build_pipeline()

    pipeline.fit(select_features(train), np.array([0, 1, 0, 1]))

    preprocess = pipeline.named_steps["preprocess"]
    assert isinstance(preprocess, ColumnTransformer)
    fitted_by_columns = {
        tuple(columns): transformer for _, transformer, columns in preprocess.transformers_
    }
    scaler = _find_step(fitted_by_columns[NUMERIC_FEATURES], StandardScaler)
    encoder = _find_step(fitted_by_columns[CATEGORICAL_FEATURES], OneHotEncoder)
    assert isinstance(scaler, StandardScaler)
    assert isinstance(encoder, OneHotEncoder)
    np.testing.assert_allclose(scaler.mean_, [35.0, 1.0])
    job_position = CATEGORICAL_FEATURES.index("job")
    assert set(encoder.categories_[job_position]) == {"admin.", "technician"}
    assert "student" not in encoder.categories_[job_position]
    assert encoder.handle_unknown == "ignore"

    probabilities = pipeline.predict_proba(select_features(validation))
    assert probabilities.shape == (1, 2)
    assert np.isfinite(probabilities).all()


def test_probability_metrics_computes_probability_focused_scores() -> None:
    metrics = probability_metrics([0, 1, 0, 1], [0.25, 0.25, 0.25, 0.25])

    assert set(metrics) == {"average_precision", "roc_auc", "brier_score"}
    assert metrics["average_precision"] == pytest.approx(0.5)
    assert metrics["roc_auc"] == pytest.approx(0.5)
    assert metrics["brier_score"] == pytest.approx(0.3125)


@pytest.mark.parametrize("label", [0, 1])
def test_probability_metrics_handles_single_class_without_undefined_scores(label: int) -> None:
    metrics = probability_metrics([label, label], [0.2, 0.8])

    assert metrics["average_precision"] is None
    assert metrics["roc_auc"] is None
    assert metrics["brier_score"] == pytest.approx(0.34)


@pytest.mark.parametrize(
    ("y_true", "probabilities"),
    [([], []), ([0, 1], [0.5]), ([0], [0.5, 0.7])],
)
def test_probability_metrics_rejects_empty_or_mismatched_inputs(
    y_true: list[int], probabilities: list[float]
) -> None:
    with pytest.raises(ValueError):
        probability_metrics(y_true, probabilities)


def test_probability_metrics_rejects_nonbinary_targets() -> None:
    with pytest.raises(ValueError):
        probability_metrics([0, 2], [0.2, 0.8])


@pytest.mark.parametrize(
    "probabilities",
    [[-0.01, 0.5], [0.5, 1.01], [float("nan"), 0.5], [float("inf"), 0.5]],
)
def test_probability_metrics_rejects_invalid_probabilities(
    probabilities: list[float],
) -> None:
    with pytest.raises(ValueError):
        probability_metrics([0, 1], probabilities)
