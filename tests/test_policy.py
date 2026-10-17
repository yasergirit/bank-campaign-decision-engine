from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from bank_campaign.policy import campaign_metrics, campaign_summary, select_campaign


def _candidates() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "row_id": [9, 2, 4, 1],
            "subscription_probability": [0.8, 0.8, 0.2, 0.5],
            "segment": ["a", "b", "c", "d"],
            "y": [1, 0, 0, 1],
        },
        index=[90, 20, 40, 10],
    )


def test_capacity_policy_is_deterministic_and_preserves_extra_columns() -> None:
    original = _candidates()
    before = original.copy(deep=True)

    selected = select_campaign(original.sample(frac=1, random_state=7), capacity=3)

    assert selected["row_id"].tolist() == [2, 9, 1]
    assert list(selected.columns) == [*original.columns, "expected_net_value"]
    assert selected["segment"].tolist() == ["b", "a", "d"]
    assert selected["y"].tolist() == [0, 1, 1]
    assert selected["expected_net_value"].tolist() == pytest.approx([77, 77, 47])
    pd.testing.assert_frame_equal(original, before)


@pytest.mark.parametrize(("capacity", "expected"), [(0, []), (99, [2, 9, 1, 4])])
def test_capacity_boundaries(capacity: int, expected: list[int]) -> None:
    selected = select_campaign(_candidates(), capacity=capacity)

    assert selected["row_id"].tolist() == expected


def test_empty_frame_is_valid_and_keeps_schema() -> None:
    empty = pd.DataFrame(
        {
            "row_id": pd.Series(dtype=object),
            "subscription_probability": pd.Series(dtype=object),
            "segment": pd.Series(dtype=object),
        }
    )

    selected = select_campaign(empty, capacity=5)

    assert selected.empty
    assert list(selected.columns) == [*empty.columns, "expected_net_value"]
    assert selected["expected_net_value"].dtype == "float64"


def test_positive_value_uses_strict_threshold_before_capacity() -> None:
    candidates = pd.DataFrame({"row_id": [0, 1, 2], "subscription_probability": [0.04, 0.03, 0.02]})

    selected = select_campaign(candidates, capacity=3, cost=3.0, value=100.0, mode="positive_value")

    assert selected["row_id"].tolist() == [0]


@pytest.mark.parametrize("capacity", [True, -1, 1.0, "1"])
def test_invalid_capacity_is_rejected(capacity: object) -> None:
    with pytest.raises(ValueError):
        select_campaign(_candidates(), capacity=capacity)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["cost", "value"])
@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "3"])
def test_invalid_value_assumption_is_rejected(name: str, value: object) -> None:
    kwargs = {name: value}
    with pytest.raises(ValueError):
        select_campaign(_candidates(), capacity=1, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("mode", ["", "value", 1])
def test_invalid_mode_is_rejected(mode: object) -> None:
    with pytest.raises(ValueError):
        select_campaign(_candidates(), capacity=1, mode=mode)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("column", "values"),
    [
        ("row_id", [0, 0]),
        ("row_id", [0, None]),
        ("row_id", [0, 1.5]),
        ("row_id", [0, -1]),
        ("row_id", [0, "1"]),
        ("row_id", [0, True]),
        ("subscription_probability", [0.1, None]),
        ("subscription_probability", [0.1, float("nan")]),
        ("subscription_probability", [0.1, float("inf")]),
        ("subscription_probability", [0.1, -0.01]),
        ("subscription_probability", [0.1, 1.01]),
        ("subscription_probability", [0.1, "0.2"]),
        ("subscription_probability", [0.1, True]),
    ],
)
def test_invalid_candidate_values_are_rejected(column: str, values: list[object]) -> None:
    frame = pd.DataFrame({"row_id": [0, 1], "subscription_probability": [0.1, 0.2]})
    frame[column] = values

    with pytest.raises(ValueError):
        select_campaign(frame, capacity=1)


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame({"row_id": [0]}),
        pd.DataFrame({"subscription_probability": [0.5]}),
    ],
)
def test_required_columns_are_enforced(frame: pd.DataFrame) -> None:
    with pytest.raises(ValueError):
        select_campaign(frame, capacity=1)


def test_campaign_summary_uses_stable_numeric_totals() -> None:
    selected = pd.DataFrame({"row_id": [0, 1, 2], "subscription_probability": [0.1, 0.2, 0.3]})

    summary = campaign_summary(selected, cost=2.0, value=10.0)

    assert summary == {
        "selected_count": 3,
        "expected_subscriptions": pytest.approx(0.6),
        "assumed_cost": pytest.approx(6.0),
        "assumed_net_value": pytest.approx(0.0),
    }
    assert campaign_summary(selected.iloc[:0]) == {
        "selected_count": 0,
        "expected_subscriptions": 0.0,
        "assumed_cost": 0.0,
        "assumed_net_value": 0.0,
    }


def test_campaign_metrics_matches_hand_calculation() -> None:
    metrics = campaign_metrics([0, 1, 0, 1, 1], [1, 3])

    assert metrics == {
        "selected_count": 2,
        "observed_subscriptions": 2,
        "precision": pytest.approx(1.0),
        "lift": pytest.approx(5 / 3),
    }


def test_campaign_metrics_handles_undefined_values() -> None:
    assert campaign_metrics([0, 1], []) == {
        "selected_count": 0,
        "observed_subscriptions": 0,
        "precision": None,
        "lift": None,
    }
    assert campaign_metrics([0, 0], [0]) == {
        "selected_count": 1,
        "observed_subscriptions": 0,
        "precision": 0.0,
        "lift": None,
    }


@pytest.mark.parametrize("positions", [[0, 0], [-1], [2], [0.0], [True]])
def test_campaign_metrics_rejects_invalid_positions(positions: list[object]) -> None:
    with pytest.raises(ValueError):
        campaign_metrics([0, 1], positions)


@pytest.mark.parametrize("labels", [[0, 2], [0, "1"], [0, None], [0, True], [0, math.nan]])
def test_campaign_metrics_rejects_nonbinary_labels(labels: list[object]) -> None:
    with pytest.raises(ValueError):
        campaign_metrics(labels, [])


def test_numpy_integer_positions_are_supported() -> None:
    assert campaign_metrics(np.array([0, 1]), [np.int64(1)])["precision"] == 1.0
