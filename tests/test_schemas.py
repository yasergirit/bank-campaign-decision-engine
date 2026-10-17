"""Request and response contract tests for the HTTP API."""

import math

import pytest
from pydantic import ValidationError

from bank_campaign.schemas import (
    MAX_BATCH_SIZE,
    MAX_SAFE_INTEGER,
    CampaignRequest,
    CampaignResponse,
    Candidate,
    HealthResponse,
    ModelInfo,
    ScoreRequest,
    ScoreResponse,
)


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


def test_candidate_accepts_unknown_and_official_categories_unseen_in_training() -> None:
    unknown = candidate_payload()
    unknown.update(
        job="unknown",
        marital="unknown",
        education="unknown",
        default="unknown",
        housing="unknown",
        loan="unknown",
    )
    assert Candidate.model_validate(unknown).job == "unknown"

    official = candidate_payload()
    official.update(job="housemaid", education="illiterate", default="yes")
    parsed = Candidate.model_validate(official)
    assert (parsed.job, parsed.education, parsed.default) == ("housemaid", "illiterate", "yes")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("row_id", True),
        ("row_id", 1.0),
        ("row_id", "1"),
        ("age", False),
        ("age", 42.0),
        ("age", "42"),
        ("previous", True),
        ("previous", 1.0),
        ("previous", "1"),
    ],
)
def test_candidate_rejects_coerced_integer_inputs(field: str, value: object) -> None:
    payload = candidate_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        Candidate.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("row_id", -1),
        ("row_id", MAX_SAFE_INTEGER + 1),
        ("age", 0),
        ("age", 121),
        ("previous", -1),
        ("previous", MAX_SAFE_INTEGER + 1),
    ],
)
def test_candidate_rejects_out_of_range_integers(field: str, value: int) -> None:
    payload = candidate_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        Candidate.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job", "astronaut"),
        ("marital", "widowed"),
        ("education", "doctorate"),
        ("default", "maybe"),
        ("housing", "true"),
        ("loan", "false"),
        ("poutcome", "unknown"),
    ],
)
def test_candidate_rejects_out_of_vocabulary_categories(field: str, value: str) -> None:
    payload = candidate_payload()
    payload[field] = value
    with pytest.raises(ValidationError, match="category"):
        Candidate.model_validate(payload)


@pytest.mark.parametrize("field", ["row_id", "age", "job", "previous", "poutcome"])
def test_candidate_requires_every_field(field: str) -> None:
    payload = candidate_payload()
    del payload[field]
    with pytest.raises(ValidationError):
        Candidate.model_validate(payload)


@pytest.mark.parametrize("field", ["y", "duration", "unexpected"])
def test_candidate_rejects_target_leakage_and_extra_fields(field: str) -> None:
    payload = candidate_payload()
    payload[field] = "yes" if field == "y" else 120
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Candidate.model_validate(payload)


def test_score_request_accepts_empty_batch_and_rejects_duplicate_ids() -> None:
    assert ScoreRequest(records=[]).records == []
    duplicate = [candidate_payload(1), candidate_payload(1)]
    with pytest.raises(ValidationError, match="unique"):
        ScoreRequest.model_validate({"records": duplicate})


def test_score_request_accepts_maximum_batch_and_rejects_one_more() -> None:
    records = [candidate_payload(row_id) for row_id in range(MAX_BATCH_SIZE)]
    assert len(ScoreRequest.model_validate({"records": records}).records) == MAX_BATCH_SIZE
    records.append(candidate_payload(MAX_BATCH_SIZE))
    with pytest.raises(ValidationError):
        ScoreRequest.model_validate({"records": records})


def test_request_rejects_top_level_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScoreRequest.model_validate({"records": [], "duration": 1})


@pytest.mark.parametrize("capacity", [True, 1.0, "1", -1, MAX_BATCH_SIZE + 1])
def test_campaign_request_rejects_invalid_capacity(capacity: object) -> None:
    with pytest.raises(ValidationError):
        CampaignRequest.model_validate({"records": [], "capacity": capacity})


@pytest.mark.parametrize("capacity", [0, MAX_BATCH_SIZE])
def test_campaign_request_accepts_capacity_boundaries(capacity: int) -> None:
    request = CampaignRequest.model_validate({"records": [], "capacity": capacity})
    assert request.capacity == capacity
    assert request.cost == 3.0
    assert request.value == 100.0
    assert request.mode == "capacity"


@pytest.mark.parametrize("field", ["cost", "value"])
@pytest.mark.parametrize(
    "invalid_value",
    [True, "3", -1, math.nan, math.inf, -math.inf, 1_000_000_000_001],
)
def test_campaign_request_rejects_invalid_money_values(field: str, invalid_value: object) -> None:
    payload: dict[str, object] = {"records": [], "capacity": 0, field: invalid_value}
    with pytest.raises(ValidationError):
        CampaignRequest.model_validate(payload)


def test_campaign_request_accepts_json_integer_for_float_fields() -> None:
    request = CampaignRequest.model_validate(
        {"records": [], "capacity": 0, "cost": 3, "value": 100}
    )
    assert request.cost == 3.0
    assert request.value == 100.0


def test_response_models_validate_bounds_and_forbid_extras() -> None:
    valid = ScoreResponse.model_validate(
        {
            "model_version": "stage4",
            "records": [
                {
                    "row_id": 1,
                    "subscription_probability": 0.25,
                    "model_version": "stage4",
                }
            ],
        }
    )
    assert valid.records[0].subscription_probability == 0.25

    with pytest.raises(ValidationError):
        ScoreResponse.model_validate(
            {
                "model_version": "stage4",
                "records": [
                    {
                        "row_id": 1,
                        "subscription_probability": 1.01,
                        "model_version": "stage4",
                    }
                ],
            }
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        HealthResponse.model_validate({"status": "ready", "model_loaded": True, "debug": True})


def test_campaign_response_has_plain_language_interpretation_default() -> None:
    response = CampaignResponse.model_validate(
        {
            "model_version": "stage4",
            "selected": [],
            "summary": {
                "selected_count": 0,
                "expected_subscriptions": 0.0,
                "assumed_cost": 0.0,
                "assumed_net_value": 0.0,
            },
        }
    )
    assert "not observed conversions or achieved profit" in response.interpretation


def test_model_info_defaults_to_uncalibrated_and_rejects_extra_fields() -> None:
    info = ModelInfo.model_validate(
        {
            "model_name": "lightgbm",
            "model_version": "stage4",
            "artifact_sha256": "a" * 64,
            "features": ["age", "job"],
            "limitations": ["Limited temporal generalization"],
        }
    )
    assert info.calibrated is False

    payload = info.model_dump()
    payload["duration"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ModelInfo.model_validate(payload)
