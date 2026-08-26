# pathguard/backend/app/ai/module1_behavior/routine_patterns.py
"""When is this patient usually where — the writer ``routine_patterns`` never had.

``behavioral_profiles.routine_patterns`` has existed since the schema was
written and nothing has ever filled it, so Module 5 has been running on three of
its four factors: ``recommendation_generation.WEIGHTS`` pinned ``time_match`` at
0.0 and ``score_place`` hardcoded the value to zero.

**This is not Module 1's clustering, and it does not reopen it.** DBSCAN over raw
GPS fixes was measured to invent 142-156 nameless "places", the largest spanning
1.5 km, and that is why production uses caregiver pins instead (see L3-0). The
job here is the other half of the question and a much safer one: the places are
already named by a human, and all that is learned is *when* the patient is at
each of them. Nothing here can invent a place, move one, or name one.

Two things are deliberate:

**The denominator is every fix in the hour, not just the matched ones.** A
patient who is out walking for most of 3 p.m. should get a low probability for
every place, not a confident one for whichever place they passed. Dividing by
matched-only would report "100% at the market at 3 p.m." on the strength of one
fix in the car park.

**Local hours, computed in one place.** ``recorded_at`` is UTC; a Thai family's
"he goes to the temple in the morning" is not. ``local_hour`` below is used by
both the builder and ``score_place``, because if the two ever disagreed by an
offset the lookup would miss every hour and ``time_match`` would read 0.0
everywhere — with no error, and looking exactly like a patient with no routine.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

from app.ai.module2_prediction.cluster_matcher import find_nearest_cluster

# Asia/Bangkok. The pilot is one Thai family; a per-patient timezone is a column
# nobody has asked for yet. Both readers of an hour go through local_hour(), so
# changing this changes them together.
LOCAL_UTC_OFFSET_HOURS = 7

# Below this many fixes in an hour bucket there is not enough evidence to claim
# anything about that hour, and one stray fix would otherwise read as a habit.
MIN_SAMPLES_PER_HOUR = 5

# A place the patient is at less than a tenth of an hour's fixes is not a
# routine; keeping it only inflates the JSON and the noise floor.
MIN_PROBABILITY = 0.1


def local_hour(moment: datetime) -> int:
    """Hour of day 0-23 in the patient's local time.

    Naive datetimes are read as UTC — SQLite hands timestamps back without a
    tzinfo, the same assumption ``api/gps.py`` makes.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int((moment.astimezone(timezone.utc).hour + LOCAL_UTC_OFFSET_HOURS) % 24)


def build_routine_patterns(
    fixes: list[tuple[float, float, datetime]],
    known_places: list[dict],
    *,
    min_samples_per_hour: int = MIN_SAMPLES_PER_HOUR,
    min_probability: float = MIN_PROBABILITY,
) -> list[dict]:
    """``[{hour, cluster_id, probability}]`` — where the patient tends to be, by hour.

    ``fixes`` is ``(latitude, longitude, recorded_at)``. ``known_places`` is the
    decoded ``known_places`` column; each place is matched against its own
    ``radius_m`` by ``find_nearest_cluster``.

    Returns ``[]`` for no fixes or no places — there is nothing to say, and an
    empty list is what keeps ``time_match`` out of the blend downstream.
    """
    if not fixes or not known_places:
        return []

    totals: dict[int, int] = defaultdict(int)
    hits: dict[tuple[int, int], int] = defaultdict(int)

    for lat, lng, recorded_at in fixes:
        hour = local_hour(recorded_at)
        totals[hour] += 1
        cluster_id = find_nearest_cluster(lat, lng, known_places)
        if cluster_id is not None:
            hits[(hour, cluster_id)] += 1

    patterns = []
    for (hour, cluster_id), count in hits.items():
        total = totals[hour]
        if total < min_samples_per_hour:
            continue
        probability = count / total
        if probability < min_probability:
            continue
        patterns.append({
            "hour": hour,
            "cluster_id": cluster_id,
            "probability": round(probability, 4),
            # Kept so a caregiver-facing screen, or the next person reading this
            # column, can tell "3 of 4 fixes" from "300 of 400".
            "samples": total,
        })

    patterns.sort(key=lambda p: (p["hour"], -p["probability"]))
    return patterns


def decode(raw: str | None) -> list[dict]:
    """Read the routine_patterns JSON column, tolerating anything unusable.

    Mirrors ``known_places.decode``: a broken profile and an untrained one both
    mean "no routine", and no caller should have to tell them apart.
    """
    if not raw:
        return []
    try:
        patterns = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(patterns, list):
        return []
    return [p for p in patterns if isinstance(p, dict)]


def probability_at(patterns: list[dict], hour: int, cluster_id: int) -> float:
    """How often this patient is at this place at this hour, 0.0 if never seen."""
    for pattern in patterns:
        if pattern.get("hour") == hour and pattern.get("cluster_id") == cluster_id:
            return float(pattern.get("probability", 0.0))
    return 0.0
