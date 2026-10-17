#!/usr/bin/env python3
"""Verify the local serving stack with synthetic, label-free requests."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from bank_campaign.dashboard import selected_csv
from bank_campaign.schemas import CampaignResponse

ROOT = Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}.")
    return value


def verify_local_bundle() -> str:
    """Require the exact frozen artifact before starting local containers."""
    freeze = _read_json(ROOT / "reports/selection_freeze.json")
    relative_path = Path(str(freeze["selected_model_path"]))
    if relative_path != Path("artifacts/comparison-v1/selected_model.pkl"):
        raise RuntimeError("The selection freeze points to an unsupported model path.")
    artifact_path = ROOT / relative_path
    if not artifact_path.is_file():
        raise RuntimeError(
            f"Missing local model artifact: {artifact_path}. Restore the trusted frozen artifact."
        )
    actual = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    expected = str(freeze["selected_model_sha256"])
    if actual != expected:
        raise RuntimeError("The local model artifact does not match the frozen SHA-256.")
    return actual


def _request_json(
    method: str, url: str, *, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object from {url}.")
    return value


def _wait_for_json(url: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _request_json("GET", url)
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"Service did not become ready at {url}: {last_error}")


def _wait_for_text(url: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.read().decode("utf-8")
        except (OSError, UnicodeError, urllib.error.HTTPError) as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"Service did not become ready at {url}: {last_error}")


def verify_http(api_url: str, ui_url: str, timeout: float, expected_sha: str) -> None:
    health = _wait_for_json(f"{api_url}/health", timeout)
    if health != {"status": "ready", "model_loaded": True}:
        raise RuntimeError(f"Unexpected API health response: {health}")

    model_info = _request_json("GET", f"{api_url}/model-info")
    if model_info.get("artifact_sha256") != expected_sha:
        raise RuntimeError("The running API loaded a different model artifact.")

    score_payload = _read_json(ROOT / "examples/score_request.json")
    first_score = _request_json("POST", f"{api_url}/score", payload=score_payload)
    second_score = _request_json("POST", f"{api_url}/score", payload=score_payload)
    if first_score != second_score:
        raise RuntimeError("Repeated scoring was not deterministic.")
    if len(first_score.get("records", [])) != len(score_payload["records"]):
        raise RuntimeError("Scoring did not preserve the synthetic request cardinality.")

    campaign_payload = {
        "records": score_payload["records"],
        "capacity": 2,
        "cost": 3.0,
        "value": 100.0,
        "mode": "capacity",
    }
    campaign_data = _request_json("POST", f"{api_url}/campaign/select", payload=campaign_payload)
    campaign = CampaignResponse.model_validate(campaign_data)
    if campaign.summary.selected_count != len(campaign.selected):
        raise RuntimeError("Campaign summary count differs from the selected records.")
    if len(campaign.selected) > campaign_payload["capacity"]:
        raise RuntimeError("Campaign selection exceeded capacity.")

    zero_data = _request_json(
        "POST",
        f"{api_url}/campaign/select",
        payload={**campaign_payload, "capacity": 0},
    )
    if zero_data.get("selected") != [] or zero_data.get("summary", {}).get("selected_count") != 0:
        raise RuntimeError("Zero capacity did not produce an empty selection.")

    positive_data = _request_json(
        "POST",
        f"{api_url}/campaign/select",
        payload={
            **campaign_payload,
            "mode": "positive_value",
            "cost": 1_000_000_000_000.0,
            "value": 0.0,
        },
    )
    if positive_data.get("selected") != []:
        raise RuntimeError("Positive-value mode selected a negative-value record.")

    csv_payload = selected_csv(campaign)
    exported = list(csv.DictReader(io.StringIO(csv_payload.decode("utf-8-sig"))))
    if [int(row["row_id"]) for row in exported] != [row.row_id for row in campaign.selected]:
        raise RuntimeError("CSV export changed the API selection order.")

    ui_health = _wait_for_text(f"{ui_url}/_stcore/health", timeout).strip()
    if ui_health != "ok":
        raise RuntimeError(f"Unexpected Streamlit health response: {ui_health!r}")

    print(
        json.dumps(
            {
                "status": "ok",
                "artifact_sha256": expected_sha,
                "model_version": first_score.get("model_version"),
                "synthetic_records_scored": len(first_score["records"]),
                "selected_records": campaign.summary.selected_count,
                "csv_rows": len(exported),
                "ui_health": ui_health,
            },
            indent=2,
            sort_keys=True,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:18000")
    parser.add_argument("--ui-url", default="http://127.0.0.1:18502")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    expected_sha = verify_local_bundle()
    if args.preflight_only:
        print(f"Trusted local artifact verified: {expected_sha}")
        return
    verify_http(args.api_url.rstrip("/"), args.ui_url.rstrip("/"), args.timeout, expected_sha)


if __name__ == "__main__":
    main()
