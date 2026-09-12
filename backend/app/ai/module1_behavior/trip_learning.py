# pathguard/backend/app/ai/module1_behavior/trip_learning.py
"""Which places a patient has proved they go to on purpose.

Module 1's clustering answers "where does this patient stop a lot", which is
not the same question as "where does this patient go". A patient who gets lost
and stands in one spot for twenty minutes, twice, looks exactly like a patient
who visits a coffee shop twice — and treating the first as a familiar place is
how a monitoring system learns to stay quiet in the one place it should not.

The difference is intent, and intent is not in the GPS track. It is in the app:
the patient picked a destination, the app walked them there, and the phone
reported arriving. So learning starts from completed navigated trips
(``trip_arrived``), and the GPS history is used to answer the questions arrival
alone cannot:

  did they STAY?   an arrival followed by two minutes on the spot is passing
                   through, or a mis-tap corrected immediately.
  do they RETURN?  once is an errand. Twice, on different days, is a routine.

The clustering itself is the same DBSCAN the rest of Module 1 uses, over the
same Kalman-smoothed coordinates — only what is fed to it changes.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
from sklearn.cluster import DBSCAN

from app.ai.module1_behavior.place_clustering import EARTH_RADIUS_M, _haversine_m
from app.ai.module1_behavior.routine_patterns import LOCAL_UTC_OFFSET_HOURS

# How close to the arrival point still counts as "at the place". Wider than the
# 20 m the app uses to declare arrival, because a patient sitting inside a
# building drifts further than that on GPS alone.
STAY_RADIUS_M = 100.0

# Minimum time on the spot after arriving. Below this they walked in and out,
# which says nothing about the place being part of their life.
#
# ⚠️ DEMO VALUE — 15 * 60 is the real one. Restore it after the presentation.
MIN_DWELL_S = 5 * 60

# Two arrivals, on two different days. Two in one afternoon is one errand seen
# twice; the calendar is what separates a routine from an event.
#
# ⚠️ MIN_DISTINCT_DAYS is a DEMO VALUE too — 2 is the real one, and dropping it
# to 1 means two trips in the same afternoon now teach a place. Restore both
# together: at 5 minutes and 1 day, an errand looks like a routine.
MIN_VISITS = 2
MIN_DISTINCT_DAYS = 1

# How long the patient has to be away from the arrival point before the stay is
# treated as over. Below this, an outlying fix is GPS noise rather than leaving.
LEFT_AFTER_S = 5 * 60

# Same 50 m DBSCAN neighbourhood the place clustering uses, so "the same place"
# means the same thing on both paths.
CLUSTER_EPS_M = 50.0


def _coords(row) -> tuple[float | None, float | None]:
    """Smoothed position when the Kalman filter produced one, raw otherwise."""
    lat = getattr(row, "smooth_latitude", None)
    lng = getattr(row, "smooth_longitude", None)
    if lat is None or lng is None:
        lat, lng = getattr(row, "latitude", None), getattr(row, "longitude", None)
    return lat, lng


def dwell_seconds(gps_rows: list, lat: float, lng: float, arrived_at: datetime,
                  *, radius_m: float = STAY_RADIUS_M,
                  leave_after_s: float = LEFT_AFTER_S) -> float:
    """How long the patient stayed within ``radius_m`` of the arrival point.

    Measured forward from the arrival, up to the point they are gone: a fix
    outside the circle does not end the stay by itself, but ``leave_after_s``
    with nothing inside it does.

    The tolerance is not generosity. A consumer GPS fix jumps a hundred metres
    on its own indoors, and a single stray reading ending a twenty-minute visit
    would teach the system that nobody ever stays anywhere. Leaving is a
    sustained absence, not one bad sample.
    """
    last_inside: datetime | None = None
    for row in gps_rows:
        recorded_at = getattr(row, "recorded_at", None)
        if recorded_at is None:
            continue
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        if recorded_at < arrived_at:
            continue
        row_lat, row_lng = _coords(row)
        if row_lat is None or row_lng is None:
            continue
        if _haversine_m(lat, lng, row_lat, row_lng) <= radius_m:
            last_inside = recorded_at
            continue
        since_inside = (recorded_at - (last_inside or arrived_at)).total_seconds()
        if since_inside > leave_after_s:
            break
    if last_inside is None:
        return 0.0
    return (last_inside - arrived_at).total_seconds()


def qualifying_visits(arrivals: list[dict], gps_rows: list, *,
                      min_dwell_s: float = MIN_DWELL_S) -> list[dict]:
    """Arrivals the patient actually stayed at, with their dwell time attached.

    ``arrivals`` is ``[{latitude, longitude, destination_name, arrived_at}]``
    (one per ``trip_arrived`` alert). ``gps_rows`` is the patient's history,
    oldest first.
    """
    visits = []
    for arrival in arrivals:
        lat, lng = arrival.get("latitude"), arrival.get("longitude")
        arrived_at = arrival.get("arrived_at")
        if lat is None or lng is None or arrived_at is None:
            continue
        if arrived_at.tzinfo is None:
            arrived_at = arrived_at.replace(tzinfo=timezone.utc)
        dwell = dwell_seconds(gps_rows, lat, lng, arrived_at)
        if dwell < min_dwell_s:
            continue
        visits.append({
            "latitude": lat,
            "longitude": lng,
            "destination_name": arrival.get("destination_name"),
            "arrived_at": arrived_at,
            "dwell_s": dwell,
        })
    return visits


def _local_day(moment: datetime) -> str:
    """Calendar day in the patient's local time.

    Local, not UTC: in Bangkok a 9 p.m. trip and the next morning's fall on the
    same UTC date, and counting them as one day would hide a real routine.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(timezone.utc) + timedelta(hours=LOCAL_UTC_OFFSET_HOURS)
    return local.date().isoformat()


def learn_places_from_trips(visits: list[dict], *,
                            min_visits: int = MIN_VISITS,
                            min_distinct_days: int = MIN_DISTINCT_DAYS) -> list[dict]:
    """Cluster qualifying visits into places the patient demonstrably goes to.

    Returns ``[{place_name, latitude, longitude, visit_frequency,
    avg_stay_time, distinct_days}]`` — ``avg_stay_time`` in minutes, matching
    what ``place_clustering.cluster_places`` emits so both paths pour into the
    same merge.
    """
    if len(visits) < min_visits:
        return []

    coords = np.radians([[v["latitude"], v["longitude"]] for v in visits])
    labels = DBSCAN(
        eps=CLUSTER_EPS_M / EARTH_RADIUS_M,
        min_samples=min_visits,
        metric="haversine",
    ).fit(coords).labels_

    places = []
    for cluster_id in sorted(set(labels)):
        if cluster_id == -1:  # visited once and never again
            continue
        members = [v for v, label in zip(visits, labels) if label == cluster_id]
        days = {_local_day(v["arrived_at"]) for v in members}
        if len(days) < min_distinct_days:
            continue
        # The name the patient themselves chose, most often. They picked it out
        # of search or off a caregiver's pin, so it is already a human name —
        # this path never produces the nameless clusters the GPS one does.
        named = [v["destination_name"] for v in members if v.get("destination_name")]
        places.append({
            "source": "learned_trip",
            "place_name": Counter(named).most_common(1)[0][0] if named else None,
            "latitude": float(sum(v["latitude"] for v in members) / len(members)),
            "longitude": float(sum(v["longitude"] for v in members) / len(members)),
            "visit_frequency": len(members),
            "avg_stay_time": round(
                sum(v["dwell_s"] for v in members) / len(members) / 60.0, 1),
            "distinct_days": len(days),
        })
    return places


def summarise(places: list[dict]) -> str:
    """One line per learned place, for logs and the demo script."""
    if not places:
        return "no places learned from completed trips yet"
    return "; ".join(
        f"{p['place_name'] or 'unnamed'} ({p['visit_frequency']} visits, "
        f"{p['distinct_days']} days, {p['avg_stay_time']:.0f} min avg)"
        for p in places
    )


__all__ = [
    "MIN_DISTINCT_DAYS",
    "MIN_DWELL_S",
    "MIN_VISITS",
    "STAY_RADIUS_M",
    "dwell_seconds",
    "learn_places_from_trips",
    "qualifying_visits",
    "summarise",
]
