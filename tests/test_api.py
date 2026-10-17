"""HTTP contract tests for the local scoring service."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bank_campaign.api import create_app
from bank_campaign.data.schema import BASELINE_FEATURES
from bank_campaign.schemas import Candidate, ScoredRecord


def candidate_payload(row_id: int = 7) -> dict[str, object]:
    return {
        "row_id": row_id,
        "age": 42,
        "job": "admin.",
        "marital": "married",
        "education": "university.degree",
        "default": "no",
        "housing": "yes",
        "loan": "no",
        "previous": 1,
        "poutcome": "nonexistent",
    }


class FakePredictor:
    model_name = "fake-model"
    model_version = "comparison-v1:fake-model:0123456789ab"
    artifact_sha256 = "a" * 64
    features = BASELINE_FEATURES

    def __init__(self, probabilities: dict[int, float] | None = None) -> None:
        self.probabilities = probabilities or {}
        self.score_calls = 0

    def score(self, records: list[Candidate]) -> list[ScoredRecord]:
        self.score_calls += 1
        return [
            ScoredRecord(
                row_id=record.row_id,
                subscription_probability=self.probabilities.get(record.row_id, 0.25),
                model_version=self.model_version,
            )
            for record in records
        ]


def test_lifespan_loads_once_and_exposes_ready_model_information() -> None:
    predictor = FakePredictor()
    load_calls = 0

    def loader() -> FakePredictor:
        nonlocal load_calls
        load_calls += 1
        return predictor

    with TestClient(create_app(loader=loader)) as client:  # type: ignore[arg-type]
        assert client.get("/health").json() == {"status": "ready", "model_loaded": True}
        assert client.get("/health").status_code == 200
        info = client.get("/model-info")

    assert load_calls == 1
    assert info.status_code == 200
    assert info.json() == {
        "model_name": "fake-model",
        "model_version": predictor.model_version,
        "artifact_sha256": "a" * 64,
        "features": list(BASELINE_FEATURES),
        "calibrated": False,
        "limitations": [
            "Historical offline prototype; subscription probabilities are not causal campaign effects.",
            "Probabilities are uncalibrated and underestimate observed outcomes in the recorded evaluation.",
            "The fixed prior-history rule outperformed this model on the recorded holdout.",
        ],
    }


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/model-info", None),
        ("post", "/score", {"records": []}),
        ("post", "/campaign/select", {"records": [], "capacity": 0}),
    ],
)
def test_model_dependent_routes_return_503_when_startup_loading_fails(
    method: str, path: str, payload: dict[str, object] | None
) -> None:
    def unavailable() -> Any:
        raise ValueError("deliberately unavailable")

    with TestClient(create_app(loader=unavailable)) as client:
        assert client.get("/health").json() == {
            "status": "not_ready",
            "model_loaded": False,
        }
        response = client.request(method, path, json=payload)

    assert response.status_code == 503
    assert response.json() == {"detail": "Model is not ready."}


def invalid_record_cases() -> list[tuple[str, Callable[[dict[str, object]], None]]]:
    def remove_age(record: dict[str, object]) -> None:
        del record["age"]

    def add_target(record: dict[str, object]) -> None:
        record["y"] = "yes"

    def add_duration(record: dict[str, object]) -> None:
        record["duration"] = 42

    def invalid_category(record: dict[str, object]) -> None:
        record["job"] = "astronaut"

    def coerced_integer(record: dict[str, object]) -> None:
        record["age"] = "42"

    def non_finite_integer(record: dict[str, object]) -> None:
        record["age"] = float("nan")

    return [
        ("missing", remove_age),
        ("target", add_target),
        ("duration", add_duration),
        ("category", invalid_category),
        ("coercion", coerced_integer),
        ("nan", non_finite_integer),
    ]


@pytest.mark.parametrize(("_case", "mutate"), invalid_record_cases())
def test_score_rejects_invalid_records_without_echoing_input(
    _case: str, mutate: Callable[[dict[str, object]], None]
) -> None:
    record = candidate_payload()
    mutate(record)

    with TestClient(create_app(loader=FakePredictor)) as client:  # type: ignore[arg-type]
        if _case == "nan":
            response = client.post(
                "/score",
                content=json.dumps({"records": [record]}),
                headers={"content-type": "application/json"},
            )
        else:
            response = client.post("/score", json={"records": [record]})

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"detail"}
    assert body["detail"]
    assert all(set(error) == {"loc", "msg", "type"} for error in body["detail"])


def test_score_is_deterministic_and_preserves_request_order() -> None:
    predictor = FakePredictor({9: 0.8, 2: 0.8, 5: 0.1})
    payload = {"records": [candidate_payload(9), candidate_payload(2), candidate_payload(5)]}

    with TestClient(create_app(loader=lambda: predictor)) as client:  # type: ignore[arg-type]
        first = client.post("/score", json=payload)
        second = client.post("/score", json=payload)

    assert first.status_code == 200
    assert second.json() == first.json()
    assert [row["row_id"] for row in first.json()["records"]] == [9, 2, 5]
    assert [row["subscription_probability"] for row in first.json()["records"]] == [0.8, 0.8, 0.1]
    assert {row["model_version"] for row in first.json()["records"]} == {predictor.model_version}
    assert predictor.score_calls == 2


def test_campaign_capacity_is_deterministic_and_breaks_ties_by_row_id() -> None:
    predictor = FakePredictor({9: 0.8, 2: 0.8, 5: 0.1})
    payload = {
        "records": [candidate_payload(9), candidate_payload(5), candidate_payload(2)],
        "capacity": 2,
        "cost": 3,
        "value": 100,
    }

    with TestClient(create_app(loader=lambda: predictor)) as client:  # type: ignore[arg-type]
        response = client.post("/campaign/select", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert [row["row_id"] for row in body["selected"]] == [2, 9]
    assert [row["expected_net_value"] for row in body["selected"]] == [77.0, 77.0]
    assert body["summary"] == {
        "selected_count": 2,
        "expected_subscriptions": pytest.approx(1.6),
        "assumed_cost": pytest.approx(6.0),
        "assumed_net_value": pytest.approx(154.0),
    }
    assert "observed" not in body["summary"]


def test_positive_value_filters_strict_threshold_before_capacity() -> None:
    predictor = FakePredictor({1: 0.04, 2: 0.03, 3: 0.02})
    payload = {
        "records": [candidate_payload(1), candidate_payload(2), candidate_payload(3)],
        "capacity": 3,
        "cost": 3,
        "value": 100,
        "mode": "positive_value",
    }

    with TestClient(create_app(loader=lambda: predictor)) as client:  # type: ignore[arg-type]
        response = client.post("/campaign/select", json=payload)

    assert response.status_code == 200
    assert [row["row_id"] for row in response.json()["selected"]] == [1]
    assert response.json()["summary"]["selected_count"] == 1


@pytest.mark.parametrize("capacity", [0, 5])
def test_empty_campaign_is_valid_for_any_capacity(capacity: int) -> None:
    with TestClient(create_app(loader=FakePredictor)) as client:  # type: ignore[arg-type]
        response = client.post("/campaign/select", json={"records": [], "capacity": capacity})

    assert response.status_code == 200
    assert response.json()["selected"] == []
    assert response.json()["summary"] == {
        "selected_count": 0,
        "expected_subscriptions": 0.0,
        "assumed_cost": 0.0,
        "assumed_net_value": 0.0,
    }
