"""Probability metrics for validation; deliberately no held-out test CLI."""

import math

from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score


def probability_metrics(y_true, probabilities) -> dict[str, float | None]:
    """AP is non-interpolated average precision, not trapezoidal PR area."""
    labels, scores = list(y_true), list(probabilities)
    if not labels or len(labels) != len(scores):
        raise ValueError("Labels and probabilities must be nonempty and equal length.")
    if any(label not in (0, 1) for label in labels):
        raise ValueError("Expected binary labels 0/1.")
    try:
        valid = all(math.isfinite(score) and 0 <= score <= 1 for score in scores)
    except (TypeError, ValueError) as exc:
        raise ValueError("Expected finite numeric probabilities in [0, 1].") from exc
    if not valid:
        raise ValueError("Expected finite probabilities in [0, 1].")
    both_classes = len(set(labels)) == 2
    return {
        "average_precision": float(average_precision_score(labels, scores))
        if both_classes
        else None,
        "roc_auc": float(roc_auc_score(labels, scores)) if both_classes else None,
        "brier_score": float(brier_score_loss(labels, scores)),
    }
