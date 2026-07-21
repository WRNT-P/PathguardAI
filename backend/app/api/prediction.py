"""Module 2 API — predict patient's destination from current GPS + behavioral profile."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.ai.module2_prediction.destination_prediction import DestinationPredictor
from app.models.prediction import PredictionResponse, TopPrediction

router = APIRouter()

_predictors: dict[int, DestinationPredictor] = {}


def _get_predictor(patient_id: int) -> DestinationPredictor:
    if patient_id not in _predictors:
        _predictors[patient_id] = DestinationPredictor()
    return _predictors[patient_id]


@router.get("/api/predict-destination/{patient_id}", response_model=PredictionResponse)
async def predict_destination(
    patient_id: int,
    lat: float = Query(..., ge=-90, le=90, description="Current latitude"),
    lng: float = Query(..., ge=-180, le=180, description="Current longitude"),
    db: AsyncSession = Depends(get_db),
) -> PredictionResponse:

    predictor = _get_predictor(patient_id)
    result = await predictor.predict(db, patient_id, lat, lng)

    top = [TopPrediction(cluster_id=cid, probability=prob)
           for cid, prob in result.get("top_predictions", [])]

    return PredictionResponse(
        status=result.get("status", "unavailable"),
        patient_id=patient_id,
        message=result.get("message", ""),
        current_cluster_id=result.get("current_cluster_id"),
        current_latitude=result.get("current_latitude"),
        current_longitude=result.get("current_longitude"),
        predicted_destination_cluster_id=result.get("predicted_destination_cluster_id"),
        predicted_destination_latitude=result.get("predicted_destination_latitude"),
        predicted_destination_longitude=result.get("predicted_destination_longitude"),
        confidence=result.get("confidence"),
        confidence_pct=result.get("confidence_pct"),
        top_predictions=top,
    )


@router.post("/api/predict-destination/{patient_id}/train")
async def train_prediction_model(
    patient_id: int,
    days: int = Query(30, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
) -> dict:
    predictor = _get_predictor(patient_id)
    return await predictor.train_from_db(db, patient_id, days=days)
