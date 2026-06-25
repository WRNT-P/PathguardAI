from typing import Literal
from pydantic import BaseModel


class TopPrediction(BaseModel):
    cluster_id: int
    probability: float


class PredictionResponse(BaseModel):
    status: Literal["ok", "no_profile", "unavailable"]
    patient_id: int
    message: str = ""
    current_cluster_id: int | None = None
    current_latitude: float | None = None
    current_longitude: float | None = None
    predicted_destination_cluster_id: int | None = None
    predicted_destination_latitude: float | None = None
    predicted_destination_longitude: float | None = None
    confidence: float | None = None
    confidence_pct: int | None = None
    top_predictions: list[TopPrediction] = []
