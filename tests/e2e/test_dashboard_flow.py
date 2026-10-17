"""UI-to-HTTP-contract scenarios, isolated from trained models and source labels."""

import io
import json
from pathlib import Path

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from bank_campaign import dashboard
from bank_campaign.api import create_app
from bank_campaign.data.schema import BASELINE_FEATURES
from bank_campaign.schemas import Candidate, ScoredRecord

ROOT = Path(__file__).resolve().parents[2]
APP = str(ROOT / "app/streamlit_app.py")
HEADER = "row_id,age,job,marital,education,default,housing,loan,previous,poutcome\n"
ROW = "9,80,admin.,married,university.degree,no,yes,no,1,nonexistent\n"


class FixturePredictor:
    model_name = "fixture"
    model_version = "fixture-v1"
    artifact_sha256 = "a" * 64
    features = BASELINE_FEATURES

    def score(self, records: list[Candidate]) -> list[ScoredRecord]:
        return [
            ScoredRecord(
                row_id=row.row_id,
                subscription_probability=row.age / 100,
                model_version=self.model_version,
            )
            for row in records
        ]


@pytest.fixture
def ui(monkeypatch: pytest.MonkeyPatch):
    real_client = dashboard.ApiClient
    calls = []
    with TestClient(create_app(loader=FixturePredictor)) as server:

        def dispatch(request: httpx.Request) -> httpx.Response:
            calls.append(
                (request.url.path, json.loads(request.content) if request.content else None)
            )
            reply = server.request(
                request.method,
                request.url.path,
                content=request.content,
                headers={"Content-Type": "application/json"},
            )
            return httpx.Response(reply.status_code, content=reply.content)

        def client_factory(base_url: str, timeout: float = 30):
            return real_client(
                client=httpx.Client(base_url=base_url, transport=httpx.MockTransport(dispatch))
            )

        monkeypatch.setattr(dashboard, "ApiClient", client_factory)
        at = AppTest.from_file(APP).run()
        assert not at.exception
        yield at, calls


def response(at: AppTest) -> dict:
    assert not at.exception
    return at.session_state["campaign_result"]


def test_sample_selection_uses_api_and_exact_download(ui) -> None:
    at, calls = ui
    at.button(key="select_campaign").click().run()
    result = response(at)
    assert [r["row_id"] for r in result["selected"]] == [2, 1]
    assert calls[-1][0] == "/campaign/select"
    assert calls[-1][1]["capacity"] == 2
    displayed = at.dataframe[-1].value
    pd.testing.assert_frame_equal(displayed, pd.DataFrame(result["selected"]))
    # Download bytes are built from exactly the same typed response, without rounding.
    from bank_campaign.schemas import CampaignResponse

    exported = dashboard.selected_csv(CampaignResponse.model_validate(result))
    csv_rows = pd.read_csv(io.BytesIO(exported)).to_dict(orient="records")
    assert [row["row_id"] for row in csv_rows] == [2, 1]
    for actual, expected in zip(csv_rows, result["selected"], strict=True):
        assert actual["subscription_probability"] == pytest.approx(
            expected["subscription_probability"]
        )
        assert actual["expected_net_value"] == pytest.approx(expected["expected_net_value"])
    assert at.get("download_button")[-1].key == "selection_download"
    assert not any("precision" in table.value.columns for table in at.dataframe)


def test_settings_invalidate_results_and_change_selection(ui) -> None:
    at, _ = ui
    at.button(key="select_campaign").click().run()
    at.number_input(key="capacity").set_value(1).run()
    assert all(b.key != "selection_download" for b in at.get("download_button"))
    at.button(key="select_campaign").click().run()
    assert [r["row_id"] for r in response(at)["selected"]] == [2]
    at.radio(key="mode").set_value("Yalnız pozitif beklenen değer").run()
    at.number_input(key="cost").set_value(60.0).run()
    assert all(b.key != "selection_download" for b in at.get("download_button"))
    at.button(key="select_campaign").click().run()
    assert response(at)["selected"] == []
    assert response(at)["summary"]["assumed_net_value"] == 0
    at.number_input(key="value").set_value(200.0).run()
    at.button(key="select_campaign").click().run()
    assert [r["row_id"] for r in response(at)["selected"]] == [2]


def test_csv_upload_scoring_and_source_change(ui) -> None:
    at, calls = ui
    at.button(key="select_campaign").click().run()
    at.radio(key="source").set_value("CSV yükle").run()
    assert at.button(key="select_campaign").disabled
    assert all(b.key != "selection_download" for b in at.get("download_button"))
    at.file_uploader(key="candidate_upload").set_value(
        ("candidates.csv", (HEADER + ROW).encode(), "text/csv")
    ).run()
    at.button(key="select_campaign").click().run()
    assert [r["row_id"] for r in response(at)["selected"]] == [9]
    assert calls[-1][1]["records"][0]["age"] == 80
    at.file_uploader(key="candidate_upload").clear().run()
    assert at.button(key="select_campaign").disabled
    assert all(b.key != "selection_download" for b in at.get("download_button"))


@pytest.mark.parametrize(
    "contents",
    [
        b"bad,columns\n1,2\n",
        (HEADER + ROW + ROW).encode(),
        (HEADER.rstrip() + ",y\n" + ROW.rstrip() + ",yes\n").encode(),
        b"x" * (dashboard.MAX_UPLOAD_BYTES + 1),
    ],
)
def test_bad_csv_shows_error_without_traceback(ui, contents: bytes) -> None:
    at, _ = ui
    at.radio(key="source").set_value("CSV yükle").run()
    at.file_uploader(key="candidate_upload").set_value(("bad.csv", contents, "text/csv")).run()
    assert not at.exception
    assert at.error
    assert at.button(key="select_campaign").disabled


def test_api_failure_is_actionable_and_history_independent(
    ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    at, _ = ui

    def unavailable(self, payload):
        raise dashboard.ApiError("Karar API bağlantısı yok; servisi başlatın.")

    # API failure replaces a previously successful result, preventing stale export.
    at.button(key="select_campaign").click().run()

    class OfflineClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        select = unavailable

    monkeypatch.setattr(dashboard, "ApiClient", OfflineClient)
    at.button(key="select_campaign").click().run()
    assert not at.exception
    assert at.error and "servisi başlatın" in at.error[0].value
    assert all(b.key != "selection_download" for b in at.get("download_button"))
    at.radio(key="page").set_value("Tarihsel değerlendirme").run()
    assert not at.exception
    assert at.title[0].value == "Tarihsel değerlendirme"
    assert at.dataframe[-1].value["Tarihsel abonelik"].tolist() == [231.0, 154.065, 372.0]


def test_container_docs_link_uses_browser_reachable_address(
    ui, monkeypatch: pytest.MonkeyPatch
) -> None:
    at, calls = ui
    monkeypatch.setenv("BANK_CAMPAIGN_PUBLIC_API_URL", "http://127.0.0.1:18000")
    monkeypatch.setenv("BANK_CAMPAIGN_API_URL", "http://api:8000")
    at.run()
    assert not at.exception
    assert at.get("link_button")[0].proto.url == "http://127.0.0.1:18000/docs"
    at.button(key="select_campaign").click().run()
    assert response(at)["summary"]["selected_count"] == 2
    assert calls[-1][0] == "/campaign/select"
