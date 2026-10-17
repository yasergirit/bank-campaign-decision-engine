"""Local FastAPI scoring and campaign selection service."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import cast

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from bank_campaign.policy import campaign_summary, select_campaign
from bank_campaign.predict import Predictor, load_predictor
from bank_campaign.schemas import (
    CampaignRequest,
    CampaignResponse,
    CampaignSummary,
    HealthResponse,
    ModelInfo,
    ScoreRequest,
    ScoreResponse,
    SelectedRecord,
)

logger = logging.getLogger(__name__)


def create_app(loader: Callable[[], Predictor] = load_predictor) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.predictor = None
        try:
            application.state.predictor = loader()
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("Model startup unavailable: %s", exc)
        yield
        application.state.predictor = None

    application = FastAPI(
        title="Bank Campaign Decision Engine",
        version="0.1.0",
        lifespan=lifespan,
        description="Offline portfolio prototype: score pre-call records and select a capacity-constrained list. Probability and monetary estimates are uncalibrated scenarios.",
    )

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Avoid echoing unsafe/non-JSON values such as NaN or exception contexts.
        errors = [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    def ready_predictor() -> Predictor:
        predictor = cast(Predictor | None, application.state.predictor)
        if predictor is None:
            raise HTTPException(status_code=503, detail="Model is not ready.")
        return predictor

    @application.get(
        "/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}}
    )
    def health() -> HealthResponse | JSONResponse:
        if application.state.predictor is None:
            return JSONResponse(
                status_code=503, content={"status": "not_ready", "model_loaded": False}
            )
        return HealthResponse(status="ready", model_loaded=True)

    @application.get("/model-info", response_model=ModelInfo)
    def model_info() -> ModelInfo:
        predictor = ready_predictor()
        return ModelInfo(
            model_name=predictor.model_name,
            model_version=predictor.model_version,
            artifact_sha256=predictor.artifact_sha256,
            features=list(predictor.features),
            calibrated=False,
            limitations=[
                "Historical offline prototype; subscription probabilities are not causal campaign effects.",
                "Probabilities are uncalibrated and underestimate observed outcomes in the recorded evaluation.",
                "The fixed prior-history rule outperformed this model on the recorded holdout.",
            ],
        )

    def score_records(payload: ScoreRequest) -> ScoreResponse:
        predictor = ready_predictor()
        try:
            records = predictor.score(payload.records)
        except (ValueError, TypeError, RuntimeError) as exc:
            logger.error("Model inference failed: %s", exc)
            raise HTTPException(status_code=503, detail="Model inference is unavailable.") from exc
        return ScoreResponse(model_version=predictor.model_version, records=records)

    @application.post("/score", response_model=ScoreResponse)
    def score(payload: ScoreRequest) -> ScoreResponse:
        return score_records(payload)

    @application.post("/campaign/select", response_model=CampaignResponse)
    def select(payload: CampaignRequest) -> CampaignResponse:
        scored = score_records(payload)
        candidates = pd.DataFrame(
            [
                {
                    "row_id": record.row_id,
                    "subscription_probability": record.subscription_probability,
                }
                for record in scored.records
            ],
            columns=["row_id", "subscription_probability"],
        )
        chosen = select_campaign(
            candidates,
            capacity=payload.capacity,
            cost=payload.cost,
            value=payload.value,
            mode=payload.mode,
        )
        summary = CampaignSummary.model_validate(
            campaign_summary(chosen, cost=payload.cost, value=payload.value)
        )
        selected = [
            SelectedRecord.model_validate({**row, "model_version": scored.model_version})
            for row in chosen.to_dict(orient="records")
        ]
        return CampaignResponse(
            model_version=scored.model_version, selected=selected, summary=summary
        )

    return application


app = create_app()
