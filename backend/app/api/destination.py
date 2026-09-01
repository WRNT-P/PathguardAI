# pathguard/backend/app/api/destination.py
"""Module 2 — where is this patient likely heading next, without TensorFlow.

The report gives Module 2 a destination predictor. The one that was built is an
LSTM (``app/ai/module2_prediction/destination_prediction.py``) and the 4-week
plan cut it (section 08), so until now there was nothing behind that claim and
``/api/predict-destination`` answered 404.

**This is not the LSTM and does not pretend to be.** It reads the Markov
transition matrix that ``RoutePredictor`` already fits on every risk score
(``module3_risk/risk_data_collection.py:145``) and returns the top rows of it.
``transition_matrix[current]`` is, by construction, a probability distribution
over *the next place* — counted from how often this patient has actually moved
from here to there — so exposing it is a real prediction with a real method
behind it, not a mock.

**Why it is a separate file from ``prediction.py``.** That module imports the
LSTM chain at module scope, so importing it at all raises ImportError without
TensorFlow installed, which would stop the entire application from booting. The
two are alternatives for the same path, never both mounted.

**The honesty fields are the point, not decoration.** With four pins and almost
no history the matrix is close to uniform, and a bare "25%" would read as a
model's judgement when it is 1 divided by 4. ``scorer``, ``history_status`` and
``transitions_observed`` are how the caller can tell those two apart — the same
job ``Scorer: rule-based fallback`` does in ``/api/recommendation``. This project
has already shipped one number that measured distance and was displayed as
safety (the C-3 confidence ceiling); the cost of that was a rewrite.
"""
import json
import logging

import numpy as np
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.module2_prediction.route_prediction import RoutePredictor, _nearest_cluster
from app.db import crud
from app.db.database import get_db
from app.services.auth import Caller, verify_patient_access

logger = logging.getLogger(__name__)

router = APIRouter()

# How many places to offer. Three, matching /api/recommendation — a caregiver
# reading a fourth and fifth guess is reading noise at this data volume.
_TOP_N = 3

# Days of history the transition matrix is counted from. Same window Module 1
# and the risk scorer read, so the two cannot disagree about what "recently"
# means.
_HISTORY_DAYS = 30

# Below this many observed moves, the distribution is reported as "sparse".
# ⚠️ A judgement call, not a measurement — there is no experiment behind it yet,
# and it is here to be raised once real pilot data exists. It is deliberately
# generous: a patient with 20 recorded moves between pinned places still has a
# matrix dominated by whichever handful of trips happened to be captured.
_SPARSE_TRANSITIONS = 20


class DestinationCandidate(BaseModel):
    rank: int
    cluster_id: int
    # None for a place Module 1 clustered rather than a caregiver pinned: the
    # clusterer emits no name and only a human can give one. The UI hides those
    # tiles rather than printing "unknown" — same rule as /api/recommendation.
    place_name: str | None = None
    latitude: float
    longitude: float
    probability: float = Field(..., description="0.0-1.0, จาก transition matrix")
    probability_pct: int = Field(..., description="0-100, ปัดแล้ว")


class DestinationResponse(BaseModel):
    patient_id: int
    status: str = Field(
        ...,
        description=(
            "ok | no_profile | no_location | unknown_current_place — "
            "ทั้งสามอันหลังคืน predictions ว่าง และ message บอกว่าต้องทำอะไรต่อ"
        ),
    )
    # Never "lstm" from this module. If the LSTM is ever restored it gets its own
    # value, so a stored response can always be traced to what produced it.
    scorer: str = Field("markov", description='วิธีที่ใช้จริง — "markov" เสมอจาก endpoint นี้')
    history_status: str = Field(
        "unknown",
        description=(
            'ok | sparse | none — "none" คือยังไม่เคยเห็นคนไข้ย้ายออกจากที่ที่ยืนอยู่เลย '
            "ตัวเลขที่ได้จึงเป็นการหารเท่ากัน ไม่ใช่คำทำนาย"
        ),
    )
    transitions_observed: int = Field(
        0, description="จำนวนการย้ายที่นับได้ทั้งหมดใน 30 วัน — ตรวจสอบได้ว่าทำไมแบน"
    )
    current_cluster_id: int | None = None
    current_place_name: str | None = None
    message: str
    predictions: list[DestinationCandidate] = []


def _known_places(profile) -> list[dict]:
    """``behavioral_profiles.known_places`` as a list, or empty."""
    if profile is None or not profile.known_places:
        return []
    raw = profile.known_places
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return raw if isinstance(raw, list) else []


def _empty(patient_id: int, status: str, message: str) -> DestinationResponse:
    return DestinationResponse(
        patient_id=patient_id, status=status, message=message, predictions=[]
    )


@router.get(
    "/api/predict-destination/{patient_id}",
    response_model=DestinationResponse,
    summary="ทำนายว่าคนไข้น่าจะไปที่ไหนต่อ (Markov — ไม่ใช่ LSTM ที่ถูกตัด)",
)
async def predict_destination(
    patient_id: int,
    lat: float | None = Query(None, ge=-90, le=90,
                              description="ทับตำแหน่งปัจจุบัน; ไม่ส่ง = ใช้จุด GPS ล่าสุด"),
    lng: float | None = Query(None, ge=-180, le=180),
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(verify_patient_access),
) -> DestinationResponse:
    """Top 3 next places, read off this patient's own transition matrix.

    Read-only, unlike ``/api/risk`` and ``/api/search-area`` — nothing is written
    and nothing is pushed. It is still not a polling endpoint: the matrix is
    refitted from 30 days of GPS on every call, the same fit-per-request the risk
    scorer does, and the answer only changes when the patient changes place.
    """
    profile = await crud.get_behavioral_profile(db, patient_id)
    places = _known_places(profile)
    if not places:
        return _empty(
            patient_id, "no_profile",
            "ยังไม่มีหมุดสถานที่ — ให้ผู้ดูแลปักหมุดผ่าน POST /api/patients/{id}/places ก่อน",
        )

    if lat is None or lng is None:
        latest = await crud.get_latest_gps(db, patient_id)
        if latest is None:
            return _empty(patient_id, "no_location",
                          "ยังไม่มีพิกัดของคนไข้เลย และไม่ได้ส่ง lat/lng มาด้วย")
        lat, lng = latest.latitude, latest.longitude

    # Which known place is the patient standing in. Each pin is tested against
    # its own radius_m — see _nearest_cluster.
    current = _nearest_cluster(lat, lng, places)
    if current is None:
        # A Markov chain has to start from a state. Standing between known
        # places is a real, common situation and the honest answer is "the chain
        # cannot say", not a guess dressed up as one — the wandering case is
        # Module 3's job (/api/risk), which does not need a current cluster.
        return _empty(
            patient_id, "unknown_current_place",
            "ตอนนี้คนไข้ไม่ได้อยู่ในรัศมีของหมุดไหนเลย จึงยังไม่มีจุดตั้งต้นให้ทำนายต่อ "
            "— ถ้ากำลังกังวลว่าหลงทาง ให้ใช้ GET /api/risk/{id} แทน",
        )

    gps_30d = await crud.get_gps_history(db, patient_id, days=_HISTORY_DAYS)
    predictor = RoutePredictor()
    fit = predictor.fit(gps_30d, places)
    if predictor.transition_matrix is None:
        return _empty(
            patient_id, "no_location",
            f"ยังไม่มีประวัติ GPS ใน {_HISTORY_DAYS} วันล่าสุดให้เรียนรู้ ({fit.get('reason', '')})",
        )

    by_id = {p["cluster_id"]: p for p in places}
    n = predictor.n_clusters
    row = predictor.transition_matrix[current].astype(float).copy()

    # A row with no observed departures is filled with 1/n by fit() — including
    # the diagonal, which would otherwise predict "they will go where they
    # already are". Detect that state before zeroing anything, because after the
    # renormalisation below the two cases look identical.
    never_left_here = bool(np.allclose(row, 1.0 / n)) if n else True

    row[current] = 0.0
    total = row.sum()
    if total <= 0:
        return _empty(
            patient_id, "unknown_current_place",
            "หมุดนี้เป็นหมุดเดียวที่มี จึงไม่มีที่อื่นให้ทำนาย",
        )
    row = row / total

    observed = int(fit.get("n_transitions", 0))
    if never_left_here:
        history_status = "none"
        note = ("ยังไม่เคยเห็นคนไข้ย้ายออกจากที่นี่เลย ตัวเลขข้างล่างคือการหารเท่ากัน "
                "ไม่ใช่คำทำนาย — อย่าแสดงเป็นเปอร์เซ็นต์ความมั่นใจ")
    elif observed < _SPARSE_TRANSITIONS:
        history_status = "sparse"
        note = (f"นับการย้ายได้ {observed} ครั้งใน {_HISTORY_DAYS} วัน "
                f"(ต่ำกว่า {_SPARSE_TRANSITIONS}) ตัวเลขยังไม่น่าเชื่อถือ")
    else:
        history_status = "ok"
        note = f"จาก {observed} การย้ายที่บันทึกได้ใน {_HISTORY_DAYS} วัน"

    order = np.argsort(row)[::-1][:_TOP_N]
    predictions = []
    for rank, cid in enumerate(order, start=1):
        cid = int(cid)
        place = by_id.get(cid)
        if place is None or row[cid] <= 0.0:
            # A cluster_id the matrix is sized for but no pin describes — it has
            # no coordinates to send, so it cannot be a destination on a map.
            continue
        predictions.append(DestinationCandidate(
            rank=rank,
            cluster_id=cid,
            place_name=place.get("place_name"),
            latitude=float(place["latitude"]),
            longitude=float(place["longitude"]),
            probability=round(float(row[cid]), 4),
            probability_pct=round(float(row[cid]) * 100),
        ))

    here = by_id.get(current, {})
    return DestinationResponse(
        patient_id=patient_id,
        status="ok",
        scorer="markov",
        history_status=history_status,
        transitions_observed=observed,
        current_cluster_id=current,
        current_place_name=here.get("place_name"),
        message=f"Markov transition จาก '{here.get('place_name') or current}' — {note}",
        predictions=predictions,
    )
