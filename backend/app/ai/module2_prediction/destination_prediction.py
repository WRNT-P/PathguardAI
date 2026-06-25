"""Module 2.1 — Destination Prediction using LSTM.
Predicts patient's destination cluster_id from travel behavior + current GPS.

Reads Module 1's output (BehavioralProfile.known_places) and GPS history from DB.
Uses shared SequencePredictor from lstm_utils.
"""
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.lstm_utils import SequencePredictor
from app.ai.module2_prediction.cluster_matcher import find_nearest_cluster, get_familiarity
from app.db import crud
from app.db.models import BehavioralProfile


def _parse_known_places(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        places = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return places if isinstance(places, list) else []


class DestinationPredictor:
    def __init__(self, sequence_length: int = 3):
        self.sequence_length = sequence_length
        self.predictor: Optional[SequencePredictor] = None

    def _build_training_data(self, gps_records: list, known_places: list[dict]) -> tuple[list, list]:
        """Map GPS history → sequences of cluster_ids for LSTM training."""
        if len(gps_records) < self.sequence_length + 1:
            return [], []

        clusters = []
        timestamps = []
        for r in gps_records:
            cid = find_nearest_cluster(r.latitude, r.longitude, known_places)
            clusters.append(cid if cid is not None else -1)
            timestamps.append(r.recorded_at if hasattr(r, 'recorded_at') else r.get('recorded_at'))

        sequences, labels = [], []
        for i in range(self.sequence_length, len(clusters)):
            seq = clusters[i - self.sequence_length:i]
            label = clusters[i]
            if label == -1 or any(c == -1 for c in seq):
                continue
            current_cluster = seq[-1]
            recent = seq[:-1]
            dt = timestamps[i] if i < len(timestamps) else datetime.now(timezone.utc)
            hour = dt.hour + dt.minute / 60.0
            day = dt.weekday()
            familiarity = get_familiarity(known_places, label)
            feature_seq = self.predictor._build_sequence(recent, current_cluster, hour, day, familiarity)
            sequences.append(feature_seq)
            labels.append(label)

        return sequences, labels

    async def train_from_db(self, db: AsyncSession, patient_id: int, days: int = 30):
        """Read GPS history + behavioral profile → train LSTM → save model."""
        profile = await crud.get_behavioral_profile(db, patient_id)
        if not profile or not profile.known_places:
            return {"status": "skipped", "reason": "no behavioral profile"}

        known_places = _parse_known_places(profile.known_places)
        n_clusters = len(known_places)
        if n_clusters == 0:
            return {"status": "skipped", "reason": "no known places"}

        records = await crud.get_gps_history(db, patient_id, days=days)
        if len(records) < self.sequence_length + 1:
            return {"status": "skipped", "reason": f"only {len(records)} GPS records (need {self.sequence_length + 1})"}

        n_clusters_effective = max(n_clusters, max(p['cluster_id'] for p in known_places) + 1)
        self.predictor = SequencePredictor(sequence_length=self.sequence_length, n_clusters=n_clusters_effective)

        sequences, labels = self._build_training_data(records, known_places)
        if not sequences:
            return {"status": "skipped", "reason": "no valid training sequences"}

        self.predictor.train(sequences, labels, epochs=50)
        self.predictor.save(patient_id)
        return {"status": "trained", "sequences": len(sequences), "n_clusters": n_clusters_effective}

    def ensure_loaded(self, patient_id: int):
        """Load model from disk if not already in memory."""
        if self.predictor is not None:
            return True
        pred = SequencePredictor()
        if pred.load(patient_id):
            self.predictor = pred
            self.sequence_length = self.predictor.sequence_length
            return True
        return False

    async def predict(self, db: AsyncSession, patient_id: int,
                      current_lat: float, current_lng: float) -> dict:
        """Predict destination given current GPS coordinates."""
        profile = await crud.get_behavioral_profile(db, patient_id)
        if not profile or not profile.known_places:
            return {"status": "no_profile", "message": "No behavioral profile found. Run Module 1 first."}

        known_places = _parse_known_places(profile.known_places)
        current_cluster = find_nearest_cluster(current_lat, current_lng, known_places)

        if not self.ensure_loaded(patient_id):
            await self.train_from_db(db, patient_id)

        if self.predictor is None:
            return {"status": "unavailable", "message": "Could not train or load LSTM model."}

        records = await crud.get_gps_history(db, patient_id, days=1)
        recent_clusters = []
        for r in records[-self.sequence_length * 3:]:
            cid = find_nearest_cluster(r.latitude, r.longitude, known_places)
            if cid is not None:
                recent_clusters.append(cid)

        recent_clusters = recent_clusters[-(self.sequence_length - 1):]
        now = datetime.now(timezone.utc)
        hour = now.hour + now.minute / 60.0
        day = now.weekday()
        familiarity = get_familiarity(known_places, current_cluster if current_cluster is not None else 0)

        sequence = self.predictor._build_sequence(
            recent_clusters,
            current_cluster if current_cluster is not None else 0,
            hour, day, familiarity
        )

        predicted_id, confidence, top3 = self.predictor.predict(sequence)

        known_map = {p['cluster_id']: p for p in known_places}
        current_info = known_map.get(current_cluster, {}) if current_cluster is not None else {}
        predicted_info = known_map.get(predicted_id, {})

        return {
            "status": "ok",
            "patient_id": patient_id,
            "current_cluster_id": current_cluster,
            "current_latitude": current_info.get('latitude'),
            "current_longitude": current_info.get('longitude'),
            "predicted_destination_cluster_id": predicted_id,
            "predicted_destination_latitude": predicted_info.get('latitude'),
            "predicted_destination_longitude": predicted_info.get('longitude'),
            "confidence": round(confidence, 3),
            "confidence_pct": round(confidence * 100),
            "top_predictions": [
                {"cluster_id": cid, "probability": round(prob, 3)}
                for cid, prob in top3
            ],
        }
