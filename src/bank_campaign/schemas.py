"""Strict request and response contracts for the scoring API."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from bank_campaign.data.schema import CATEGORICAL_VALUES

MAX_SAFE_INTEGER = 2**53 - 1
MAX_BATCH_SIZE = 10_000
MAX_MONEY_VALUE = 1_000_000_000_000.0

RowId = Annotated[StrictInt, Field(ge=0, le=MAX_SAFE_INTEGER)]
Age = Annotated[StrictInt, Field(ge=1, le=120)]
Count = Annotated[StrictInt, Field(ge=0, le=MAX_SAFE_INTEGER)]
Money = Annotated[float, Field(strict=True, ge=0, le=MAX_MONEY_VALUE)]
Probability = Annotated[float, Field(strict=True, ge=0, le=1)]
FiniteFloat = Annotated[float, Field(strict=True)]


class ApiModel(BaseModel):
    """Shared API behavior: reject coercion, unknown fields, and non-finite numbers."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Candidate(ApiModel):
    """Pre-call customer features accepted by the released model."""

    row_id: RowId
    age: Age
    job: str
    marital: str
    education: str
    default: str
    housing: str
    loan: str
    previous: Count
    poutcome: str

    @field_validator("job", "marital", "education", "default", "housing", "loan", "poutcome")
    @classmethod
    def validate_official_category(cls, value: str, info: object) -> str:
        """Accept only categories declared by the source-data contract."""
        field_name = getattr(info, "field_name", None)
        if field_name not in CATEGORICAL_VALUES:
            raise ValueError("Categorical field is missing from the data contract")
        if value not in CATEGORICAL_VALUES[field_name]:
            allowed = ", ".join(sorted(CATEGORICAL_VALUES[field_name]))
            raise ValueError(f"Unknown {field_name!r} category; expected one of: {allowed}")
        return value


class ScoreRequest(ApiModel):
    """A bounded batch of candidates to score."""

    records: Annotated[list[Candidate], Field(min_length=0, max_length=MAX_BATCH_SIZE)]

    @model_validator(mode="after")
    def validate_unique_row_ids(self) -> Self:
        row_ids = [record.row_id for record in self.records]
        if len(row_ids) != len(set(row_ids)):
            raise ValueError("record row_id values must be unique within a request")
        return self


class CampaignRequest(ScoreRequest):
    """Scoring records plus the assumptions used by the decision policy."""

    capacity: Annotated[StrictInt, Field(ge=0, le=MAX_BATCH_SIZE)]
    cost: Money = 3.0
    value: Money = 100.0
    mode: Literal["capacity", "positive_value"] = "capacity"


class ScoredRecord(ApiModel):
    row_id: int
    subscription_probability: Probability
    model_version: str


class ScoreResponse(ApiModel):
    model_version: str
    records: list[ScoredRecord]


class SelectedRecord(ScoredRecord):
    expected_net_value: FiniteFloat


class CampaignSummary(ApiModel):
    selected_count: Annotated[int, Field(ge=0)]
    expected_subscriptions: Annotated[float, Field(strict=True, ge=0)]
    assumed_cost: Annotated[float, Field(strict=True, ge=0)]
    assumed_net_value: FiniteFloat


class CampaignResponse(ApiModel):
    model_version: str
    selected: list[SelectedRecord]
    summary: CampaignSummary
    interpretation: str = (
        "Expected values use model probabilities and user assumptions; they are not observed "
        "conversions or achieved profit."
    )


class HealthResponse(ApiModel):
    status: Literal["ready", "not_ready"]
    model_loaded: bool


class ModelInfo(ApiModel):
    model_name: str
    model_version: str
    artifact_sha256: str
    features: list[str]
    calibrated: bool = False
    limitations: list[str]
