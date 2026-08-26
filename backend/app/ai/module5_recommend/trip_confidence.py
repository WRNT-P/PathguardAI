"""Confidence for a place the patient has *never been* — what C-3 actually asks.

``score_place`` ranks the places already in a patient's profile, and every one of
its factors is measured relative to that profile: ``frequency`` is this place's
visits over the busiest place's visits, ``familiarity`` is its average stay over
the longest stay. Ask it about a destination the patient has never visited and
both come back 0 by construction, leaving only ``proximity`` (weight 0.35).

That is not a rounding problem, it is a ceiling. Measured on a real profile:

    a pinned place (home)          confidence 1.000
    a new place 100 m away         confidence 0.318
    a new place 2 km away          confidence 0.117
    a new place at distance zero   confidence 0.350   <- the maximum, always

Trip Approval (report §C-3) exists *because* a Level 2 patient's search box is
locked and they are requesting somewhere new (report line 514), so every request
a caregiver ever sees would carry a number that measures distance and calls it
safety, capped at 35% no matter how sensible the trip. A caregiver learns to
ignore a number like that in about a week, and then the screen is worse than not
having been built.

So familiarity is redefined for the unvisited case. Not *"how often do you go
here"* — which is 0, always, and says nothing — but **"how close is this to
somewhere you know"**. ``find_nearest_cluster`` already answers that and already
honours each pin's own ``radius_m``, so a market compound is not judged by a
house-sized 150 m.

Round one scores on **familiarity and danger zones only**, by decision on
2026-08-26. Time-of-day is deliberately out: it would need ``routine_patterns``,
which nothing writes, and a factor wired to a column no writer fills is how
``time_match`` ended up pinned at weight 0.0 in the first place.

**The danger zone is a veto, not a weight.** A caregiver drew that circle around
a canal or a highway; a familiar place that happens to sit inside it must not
average its way back up to "probably fine". Blending 0.5/0.5 would hand a
caregiver 50% confidence on a trip into water.
"""
from dataclasses import dataclass

from app.ai.module2_prediction.cluster_matcher import (
    find_nearest_cluster, get_familiarity, haversine_km,
)
from app.ai.module3_risk.risk_data_collection import is_in_danger_zone


@dataclass
class DestinationConfidence:
    """One answer to "should this trip be approved?", with its reasons intact.

    ``confidence`` is what C-3 shows; ``factors`` and ``nearest_place_name`` are
    what let the screen say *why*, which is the difference between a caregiver
    trusting the number and dismissing it.
    """
    status: str                       # "ok" | "no_profile"
    confidence: float                 # 0..1
    factors: dict                     # {"familiarity": float, "danger_zone": bool}
    nearest_place_name: str | None
    nearest_place_distance_m: float | None
    blocking_zone_name: str | None
    # Never pass rules off as ML — the same promise ScoredPlace.scorer makes.
    scorer: str = "rules"


def _blocking_zone(lat: float, lng: float, danger_zones: list) -> dict | None:
    """The zone this destination falls inside, if any.

    Delegates the actual test to Module 3 rather than re-deriving it. Two
    implementations of "is this point in a danger zone" is precisely the kind of
    drift that produces one answer on the risk screen and a different one on the
    approval screen for the same coordinates.
    """
    for zone in danger_zones:
        if is_in_danger_zone(lat, lng, [zone]):
            return zone
    return None


def _nearest_by_edge(lat: float, lng: float, known_places: list[dict],
                     default_radius_m: float) -> tuple[dict | None, float]:
    """Nearest known place measured to its *edge*, not its centre.

    Centre distance would make a wide place (a hospital compound) look further
    away than a narrow one whose boundary is actually further from the patient.
    """
    best, best_edge = None, float("inf")
    for place in known_places:
        centre_m = haversine_km(
            lat, lng, place["latitude"], place["longitude"]) * 1000.0
        radius_m = place.get("radius_m") or default_radius_m
        edge_m = max(0.0, centre_m - radius_m)
        if edge_m < best_edge:
            best, best_edge = place, edge_m
    return best, best_edge


def score_destination(
    latitude: float,
    longitude: float,
    known_places: list[dict],
    danger_zones: list,
    *,
    decay_ceiling_m: float,
    default_radius_m: float = 150.0,
) -> DestinationConfidence:
    """How confident are we that this destination is a reasonable trip?

    Args:
        latitude/longitude: where the patient is asking to go.
        known_places:       the patient's profile — caregiver pins in practice.
        danger_zones:       active zones in the shape
                            ``rule_repository.get_active_danger_zones`` returns.
        decay_ceiling_m:    distance beyond a known place's edge at which
                            familiarity reaches zero. **Required, not defaulted**
                            — the caller passes the rule KB's
                            ``route_deviation_ceiling_m`` so that this scale and
                            the risk score's agree, and so an admin editing the KB
                            moves both. Hardcoding it here is the mistake gotcha 3
                            already records for the risk weights.
        default_radius_m:   radius for pins written before ``radius_m`` existed,
                            matching ``find_nearest_cluster``'s own fallback.
    """
    # Veto first: a destination inside a zone the caregiver drew is refused
    # whether or not the patient knows the area well.
    zone = _blocking_zone(latitude, longitude, danger_zones)
    if zone is not None:
        return DestinationConfidence(
            status="ok",
            confidence=0.0,
            factors={"familiarity": 0.0, "danger_zone": True},
            nearest_place_name=None,
            nearest_place_distance_m=None,
            blocking_zone_name=zone.get("name"),
        )

    if not known_places:
        # Zero here would read as "we are confident this is a bad idea", which is
        # a claim we cannot make. The caregiver is told there is nothing to
        # compare against instead — the same honesty /api/risk shows with
        # status "partial" rather than a number it cannot stand behind.
        return DestinationConfidence(
            status="no_profile",
            confidence=0.0,
            factors={"familiarity": 0.0, "danger_zone": False},
            nearest_place_name=None,
            nearest_place_distance_m=None,
            blocking_zone_name=None,
        )

    nearest, edge_m = _nearest_by_edge(
        latitude, longitude, known_places, default_radius_m)

    # Inside some pin's own radius: the patient is asking to go somewhere they
    # already know, and its own relative familiarity is the answer.
    inside_id = find_nearest_cluster(latitude, longitude, known_places,
                                     max_distance_km=default_radius_m / 1000.0)
    if inside_id is not None:
        familiarity = get_familiarity(known_places, inside_id)
        matched = next(p for p in known_places if p["cluster_id"] == inside_id)
        edge_m = 0.0
    else:
        # Outside everything: start from how familiar the nearest place is and
        # fall off linearly to zero at the ceiling. Linear rather than a curve
        # because a caregiver has to be able to follow why the number moved.
        matched = nearest
        base = get_familiarity(known_places, nearest["cluster_id"])
        decay = max(0.0, 1.0 - edge_m / decay_ceiling_m) if decay_ceiling_m > 0 else 0.0
        familiarity = base * decay

    return DestinationConfidence(
        status="ok",
        confidence=round(familiarity, 4),
        factors={"familiarity": round(familiarity, 4), "danger_zone": False},
        nearest_place_name=matched.get("place_name") or matched.get("name"),
        nearest_place_distance_m=round(edge_m, 1),
        blocking_zone_name=None,
    )
