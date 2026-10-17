"""Tests for dashboard CSV and local API helper behavior."""

import csv
import io

import httpx
import pytest

from bank_campaign.dashboard import (
    MAX_UPLOAD_BYTES,
    ApiClient,
    ApiError,
    parse_candidate_csv,
    selected_csv,
)
from bank_campaign.schemas import MAX_BATCH_SIZE, CampaignRequest, CampaignResponse

HEADER = "row_id,age,job,marital,education,default,housing,loan,previous,poutcome\n"
ROW = "7,42,admin.,married,university.degree,no,yes,no,1,nonexistent\n"


def test_parse_candidate_csv_accepts_utf8_bom_and_header_only() -> None:
    parsed = parse_candidate_csv(("\ufeff" + HEADER + ROW).encode())
    assert parsed == [
        {
            "row_id": 7,
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
    ]
    assert parse_candidate_csv(HEADER.encode()) == []


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "boş"),
        (b"\xff", "UTF-8"),
        (("row_id,row_id," + HEADER.removeprefix("row_id,")).encode(), "yinelenen"),
        ((HEADER.rstrip("\n") + ",duration\n" + ROW.rstrip("\n") + ",10\n").encode(), "tam"),
        ((HEADER.rstrip("\n") + ",y\n" + ROW.rstrip("\n") + ",yes\n").encode(), "tam"),
    ],
)
def test_parse_candidate_csv_rejects_invalid_files(payload: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_candidate_csv(payload)


@pytest.mark.parametrize("value", ["7.0", " 7", "+7", "٧", "true", ""])
def test_parse_candidate_csv_rejects_non_digit_integer_fields(value: str) -> None:
    row = ROW.replace("7,42", f"{value},42", 1)
    with pytest.raises(ValueError, match="tam sayı"):
        parse_candidate_csv((HEADER + row).encode())


def test_parse_candidate_csv_rejects_invalid_category_without_echoing_value() -> None:
    secret_value = "=HYPERLINK(very-secret)"
    row = ROW.replace("admin.", secret_value)
    with pytest.raises(ValueError) as caught:
        parse_candidate_csv((HEADER + row).encode())
    assert secret_value not in str(caught.value)


def test_parse_candidate_csv_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="benzersiz"):
        parse_candidate_csv((HEADER + ROW + ROW).encode())


def test_parse_candidate_csv_enforces_file_and_batch_limits() -> None:
    with pytest.raises(ValueError, match="2 MiB"):
        parse_candidate_csv(b"x" * (MAX_UPLOAD_BYTES + 1))

    rows = [ROW.replace("7,42", f"{row_id},42", 1) for row_id in range(MAX_BATCH_SIZE + 1)]
    with pytest.raises(ValueError, match=str(MAX_BATCH_SIZE)):
        parse_candidate_csv((HEADER + "".join(rows)).encode())


def _response(status: int, data: object, request: httpx.Request) -> httpx.Response:
    return httpx.Response(status, json=data, request=request)


def test_api_client_parses_typed_responses_and_sends_campaign_payload() -> None:
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        if request.url.path == "/health":
            return _response(200, {"status": "ready", "model_loaded": True}, request)
        if request.url.path == "/model-info":
            return _response(
                200,
                {
                    "model_name": "small",
                    "model_version": "v1",
                    "artifact_sha256": "a" * 64,
                    "features": ["age"],
                    "calibrated": False,
                    "limitations": ["prototype"],
                },
                request,
            )
        return _response(
            200,
            {
                "model_version": "v1",
                "selected": [],
                "summary": {
                    "selected_count": 0,
                    "expected_subscriptions": 0.0,
                    "assumed_cost": 0.0,
                    "assumed_net_value": 0.0,
                },
                "interpretation": "scenario",
            },
            request,
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="http://test")
    client = ApiClient(client=http_client)

    assert client.health().status == "ready"
    assert client.model_info().model_version == "v1"
    request = CampaignRequest(records=[], capacity=0)
    assert client.select(request).summary.selected_count == 0
    assert seen_request is not None
    assert seen_request.url.path == "/campaign/select"
    assert b'"capacity":0' in seen_request.content


def test_api_client_accepts_structured_not_ready_health_response() -> None:
    transport = httpx.MockTransport(
        lambda request: _response(503, {"status": "not_ready", "model_loaded": False}, request)
    )
    client = ApiClient(client=httpx.Client(transport=transport, base_url="http://test"))
    assert client.health().status == "not_ready"


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("secret", request=request)),
        lambda request: httpx.Response(500, text="secret", request=request),
        lambda request: httpx.Response(200, text="not-json", request=request),
        lambda request: _response(200, {"status": "ready"}, request),
    ],
)
def test_api_client_wraps_transport_status_and_invalid_response_errors(handler: object) -> None:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    client = ApiClient(client=httpx.Client(transport=transport, base_url="http://test"))
    with pytest.raises(ApiError) as caught:
        client.health()
    assert "secret" not in str(caught.value)
    assert "not-json" not in str(caught.value)


def test_selected_csv_preserves_order_and_float_round_trip_and_neutralizes_formulas() -> None:
    response = CampaignResponse.model_validate(
        {
            "model_version": "v1",
            "selected": [
                {
                    "row_id": 9,
                    "subscription_probability": 0.1234567890123456,
                    "model_version": "=dangerous",
                    "expected_net_value": 9.34567890123456,
                },
                {
                    "row_id": 2,
                    "subscription_probability": 0.8,
                    "model_version": "v1",
                    "expected_net_value": 77.0,
                },
            ],
            "summary": {
                "selected_count": 2,
                "expected_subscriptions": 0.9234567890123456,
                "assumed_cost": 6.0,
                "assumed_net_value": 86.34567890123456,
            },
        }
    )

    payload = selected_csv(response)
    assert payload.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    assert [int(row["row_id"]) for row in rows] == [9, 2]
    assert float(rows[0]["subscription_probability"]) == 0.1234567890123456
    assert float(rows[0]["expected_net_value"]) == 9.34567890123456
    assert rows[0]["model_version"] == "'=dangerous"
    assert rows[1]["model_version"] == "v1"
