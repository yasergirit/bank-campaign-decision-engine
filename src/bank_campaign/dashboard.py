"""Helpers shared by the local Streamlit dashboard.

This module deliberately stays independent from training, database, and model-artifact code.
The dashboard talks to the scoring service only through its documented HTTP contract.
"""

from __future__ import annotations

import csv
import io
from types import TracebackType
from typing import Self

import httpx
from pydantic import ValidationError

from bank_campaign.schemas import (
    MAX_BATCH_SIZE,
    CampaignRequest,
    CampaignResponse,
    Candidate,
    HealthResponse,
    ModelInfo,
    ScoreRequest,
)

MAX_UPLOAD_BYTES = 2 * 1024 * 1024


class ApiError(RuntimeError):
    """A safe, user-facing failure raised while calling the local scoring API."""


def _parse_integer(value: str, field: str, row_number: int) -> int:
    if not value or not value.isascii() or not value.isdigit():
        raise ValueError(
            f"CSV satırı {row_number}: {field} alanı yalnızca tam sayı rakamları içermelidir."
        )
    return int(value)


def parse_candidate_csv(payload: bytes) -> list[dict[str, object]]:
    """Parse and strictly validate an uploaded candidate CSV file."""
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("CSV dosyası 2 MiB boyut sınırını aşıyor.")
    if not payload:
        raise ValueError("CSV dosyası boş; en azından başlık satırı bulunmalıdır.")

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV dosyası UTF-8 kodlamasında olmalıdır.") from exc

    expected_columns = list(Candidate.model_fields)
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(reader)
    except StopIteration as exc:
        raise ValueError("CSV dosyası boş; en azından başlık satırı bulunmalıdır.") from exc
    except csv.Error as exc:
        raise ValueError(
            "CSV biçimi okunamadı; virgülle ayrılmış geçerli bir dosya kullanın."
        ) from exc

    if len(header) != len(set(header)):
        raise ValueError("CSV başlığında yinelenen sütun adları bulunuyor.")
    if len(header) != len(expected_columns) or set(header) != set(expected_columns):
        raise ValueError(
            "CSV sütunları müşteri şemasıyla tam olarak eşleşmelidir: "
            + ", ".join(expected_columns)
        )

    records: list[dict[str, object]] = []
    try:
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(header):
                raise ValueError(f"CSV satırı {row_number}: sütun sayısı başlıkla eşleşmiyor.")
            if len(records) >= MAX_BATCH_SIZE:
                raise ValueError(f"CSV en fazla {MAX_BATCH_SIZE} müşteri satırı içerebilir.")

            record: dict[str, object] = dict(zip(header, row, strict=True))
            for field in ("row_id", "age", "previous"):
                record[field] = _parse_integer(str(record[field]), field, row_number)

            try:
                candidate = Candidate.model_validate(record)
            except ValidationError as exc:
                raise ValueError(
                    f"CSV satırı {row_number} geçersiz; alanları ve kategori değerlerini kontrol edin."
                ) from exc
            records.append(candidate.model_dump())
    except csv.Error as exc:
        raise ValueError(
            "CSV biçimi okunamadı; virgülle ayrılmış geçerli bir dosya kullanın."
        ) from exc

    try:
        validated = ScoreRequest.model_validate({"records": records})
    except ValidationError as exc:
        raise ValueError("CSV içindeki row_id değerleri benzersiz olmalıdır.") from exc
    return [record.model_dump() for record in validated.records]


class ApiClient:
    """Small typed client for the local decision-engine API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request(self, method: str, path: str, *, json: object | None = None) -> httpx.Response:
        try:
            return self._client.request(method, path, json=json)
        except httpx.RequestError as exc:
            raise ApiError(
                "Karar API'sine bağlanılamadı; yerel servisin çalıştığını kontrol edin."
            ) from exc

    @staticmethod
    def _validate_response(response: httpx.Response, model: type[HealthResponse]) -> HealthResponse:
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ApiError("Karar API'si beklenmeyen bir yanıt döndürdü.") from exc

    def health(self) -> HealthResponse:
        response = self._request("GET", "/health")
        if response.status_code not in {200, 503}:
            raise ApiError(f"Karar API'si sağlık isteğini reddetti (HTTP {response.status_code}).")
        return self._validate_response(response, HealthResponse)

    def model_info(self) -> ModelInfo:
        response = self._request("GET", "/model-info")
        if not response.is_success:
            raise ApiError(f"Model bilgisi alınamadı (HTTP {response.status_code}).")
        try:
            return ModelInfo.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ApiError("Karar API'si geçersiz model bilgisi döndürdü.") from exc

    def select(self, payload: CampaignRequest) -> CampaignResponse:
        response = self._request("POST", "/campaign/select", json=payload.model_dump(mode="json"))
        if not response.is_success:
            raise ApiError(f"Kampanya seçimi başarısız oldu (HTTP {response.status_code}).")
        try:
            return CampaignResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ApiError("Karar API'si geçersiz kampanya sonucu döndürdü.") from exc


def _spreadsheet_safe(value: str) -> str:
    if value.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + value
    return value


def selected_csv(response: CampaignResponse) -> bytes:
    """Export selected rows without rounding probabilities or expected values."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["row_id", "subscription_probability", "model_version", "expected_net_value"])
    for record in response.selected:
        writer.writerow(
            [
                record.row_id,
                record.subscription_probability,
                _spreadsheet_safe(record.model_version),
                record.expected_net_value,
            ]
        )
    return output.getvalue().encode("utf-8-sig")
