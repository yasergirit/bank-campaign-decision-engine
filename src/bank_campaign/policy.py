"""Campaign selection policies and decision-focused evaluation helpers."""

from __future__ import annotations

import math
from collections.abc import Iterable
from numbers import Integral, Number
from typing import cast

import pandas as pd

REQUIRED_COLUMNS = ("row_id", "subscription_probability")
POLICY_MODES = {"capacity", "positive_value"}


def _finite_nonnegative_number(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Number):
        raise ValueError(f"{name} must be a finite nonnegative number.")
    try:
        converted = float(cast(float, value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite nonnegative number.") from exc
    if not math.isfinite(converted) or converted < 0:
        raise ValueError(f"{name} must be a finite nonnegative number.")
    return converted


def _capacity(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError("capacity must be a nonnegative integer.")
    return int(value)


def _validate_candidates(candidates: object) -> tuple[pd.DataFrame, list[int], list[float]]:
    if not isinstance(candidates, pd.DataFrame):
        raise ValueError("candidates must be a pandas DataFrame.")
    for column in REQUIRED_COLUMNS:
        if candidates.columns.tolist().count(column) != 1:
            raise ValueError(f"candidates must contain exactly one {column!r} column.")

    row_ids: list[int] = []
    for value in candidates["row_id"]:
        if isinstance(value, bool) or not isinstance(value, Number):
            raise ValueError("row_id values must be nonnegative integers.")
        if isinstance(value, Integral):
            numeric_id = int(value)
        else:
            try:
                numeric = float(cast(float, value))
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("row_id values must be nonnegative integers.") from exc
            if not math.isfinite(numeric) or not numeric.is_integer():
                raise ValueError("row_id values must be nonnegative integers.")
            numeric_id = int(numeric)
        if numeric_id < 0:
            raise ValueError("row_id values must be nonnegative integers.")
        row_ids.append(numeric_id)
    if len(set(row_ids)) != len(row_ids):
        raise ValueError("row_id values must be unique.")

    probabilities: list[float] = []
    for value in candidates["subscription_probability"]:
        if isinstance(value, bool) or not isinstance(value, Number):
            raise ValueError("subscription_probability values must be finite numbers in [0, 1].")
        try:
            numeric = float(cast(float, value))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "subscription_probability values must be finite numbers in [0, 1]."
            ) from exc
        if not math.isfinite(numeric) or not 0 <= numeric <= 1:
            raise ValueError("subscription_probability values must be finite numbers in [0, 1].")
        probabilities.append(numeric)
    return candidates, row_ids, probabilities


def select_campaign(
    candidates: pd.DataFrame,
    *,
    capacity: int,
    cost: float = 3.0,
    value: float = 100.0,
    mode: str = "capacity",
) -> pd.DataFrame:
    """Select candidates by predicted probability under a stated decision policy.

    The policy ranks probabilities and does not estimate the causal effect of contact.
    """
    limit = _capacity(capacity)
    assumed_cost = _finite_nonnegative_number(cost, name="cost")
    assumed_value = _finite_nonnegative_number(value, name="value")
    if not isinstance(mode, str) or mode not in POLICY_MODES:
        raise ValueError(f"mode must be one of {sorted(POLICY_MODES)}.")
    frame, row_ids, probabilities = _validate_candidates(candidates)

    ranked = frame.copy(deep=True)
    # Use validated numeric values for deterministic numeric sorting even with object dtypes.
    ranked["row_id"] = row_ids
    ranked["subscription_probability"] = probabilities
    ranked["expected_net_value"] = [
        probability * assumed_value - assumed_cost for probability in probabilities
    ]
    ranked = ranked.sort_values(
        ["subscription_probability", "row_id"],
        ascending=[False, True],
        kind="mergesort",
    )
    if mode == "positive_value":
        ranked = ranked.loc[ranked["expected_net_value"] > 0]
    return ranked.head(limit).copy()


def campaign_summary(
    selected: pd.DataFrame,
    *,
    cost: float = 3.0,
    value: float = 100.0,
) -> dict[str, int | float]:
    """Summarize model-based expectations under explicit value assumptions."""
    validated = select_campaign(selected, capacity=len(selected), cost=cost, value=value)
    probabilities = validated["subscription_probability"].tolist()
    assumed_cost = float(cost)
    assumed_value = float(value)
    try:
        expected_subscriptions = math.fsum(probabilities)
        total_cost = math.fsum(assumed_cost for _ in probabilities)
        total_net_value = math.fsum(
            probability * assumed_value - assumed_cost for probability in probabilities
        )
    except OverflowError as exc:
        raise ValueError("Campaign summary totals must remain finite.") from exc
    if not all(
        math.isfinite(total) for total in (expected_subscriptions, total_cost, total_net_value)
    ):
        raise ValueError("Campaign summary totals must remain finite.")
    return {
        "selected_count": len(validated),
        "expected_subscriptions": expected_subscriptions,
        "assumed_cost": total_cost,
        "assumed_net_value": total_net_value,
    }


def campaign_metrics(
    y_true: Iterable[object], selected_positions: Iterable[object]
) -> dict[str, int | float | None]:
    """Evaluate a selected set using integer positions in the full label sequence."""
    labels = list(y_true)
    binary_labels: list[int] = []
    for label in labels:
        if isinstance(label, bool) or not isinstance(label, Number):
            raise ValueError("y_true must contain binary numeric labels 0/1.")
        try:
            numeric_label = float(cast(float, label))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("y_true must contain binary numeric labels 0/1.") from exc
        if not math.isfinite(numeric_label) or numeric_label not in (0, 1):
            raise ValueError("y_true must contain binary numeric labels 0/1.")
        binary_labels.append(int(numeric_label))

    positions: list[int] = []
    for position in selected_positions:
        if isinstance(position, bool) or not isinstance(position, Integral):
            raise ValueError("selected_positions must contain integer positions.")
        numeric_position = int(cast(int, position))
        if not 0 <= numeric_position < len(binary_labels):
            raise ValueError("selected_positions contains an out-of-range position.")
        positions.append(numeric_position)
    if len(set(positions)) != len(positions):
        raise ValueError("selected_positions must not contain duplicates.")

    selected_count = len(positions)
    observed_subscriptions = sum(binary_labels[position] for position in positions)
    precision = observed_subscriptions / selected_count if selected_count else None
    population_positives = sum(binary_labels)
    lift = None
    if precision is not None and population_positives:
        lift = precision / (population_positives / len(binary_labels))
    return {
        "selected_count": selected_count,
        "observed_subscriptions": observed_subscriptions,
        "precision": precision,
        "lift": lift,
    }
