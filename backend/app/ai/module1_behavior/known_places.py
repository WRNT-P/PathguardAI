"""The one place that decides what ``behavioral_profiles.known_places`` may hold.

Two writers feed that column — the caregiver's pins (``api/places.py``) and
Module 1's nightly clustering (``behavior_pipeline.analyze_behavior``) — and
until now neither knew the other existed. Both problems that creates are silent.

**The column is overwritten, not merged.** ``places.py`` already keeps whatever
Module 1 learned when the caregiver re-pins; Module 1 did not do the reverse, so
scheduling the nightly job would erase every pin that night.

**The two writers use different units.** A pin carries the rank scale
(``daily_live`` = 100 … ``rare`` = 3) and stay time in *seconds*; clustering
emits a raw count of GPS fixes — measured as high as 2,978 in a 30-day window —
and stay time in *minutes*. Every consumer normalizes relatively:
``cluster_matcher.get_familiarity`` divides by ``max(visit_frequency)`` across
the whole list, and Module 5 divides by ``max(avg_stay_time)``. Drop unscaled
learned rows in beside pins and the pins collapse — a caregiver's home pin goes
from familiarity 1.000 to 0.034, so the patient reads as 97% unfamiliar while
sitting in their own living room.

So learned places are rescaled onto the caregiver's axes before they are allowed
into the list, and they are capped below the top rank: only a human gets to
assert "this is where they live". The algorithm's best guess is "most days".
"""
from __future__ import annotations

import json

# The caregiver-facing scale. api/places.py maps its visit_rank enum through
# this; keeping the numbers here means the learned side cannot drift from it.
VISIT_FREQUENCY: dict[str, int] = {
    "daily_live": 100,   # lives here / sleeps here
    "most_days": 40,
    "weekly": 10,
    "rare": 3,
}

# A learned place tops out at "most_days". Not a tuning knob — a policy: the
# caregiver's explicit "she lives here" must outrank anything DBSCAN inferred,
# because when the two disagree the human is the one who has been in the house.
LEARNED_VISIT_CEILING = float(VISIT_FREQUENCY["most_days"])

# place_clustering.py reports avg_stay_time in minutes; pins store seconds.
_MINUTES_TO_SECONDS = 60.0


def decode(raw: str | None) -> list[dict]:
    """Read the known_places JSON column, tolerating anything unusable."""
    if not raw:
        return []
    try:
        places = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(places, list):
        return []
    return [p for p in places if isinstance(p, dict)]


def renumber(places: list[dict]) -> list[dict]:
    """Re-hand cluster_ids as 0, 1, 2 … in list order.

    ``route_prediction.py:111`` sizes its transition matrix with
    ``max(cluster_id) + 1``, so ids have to stay contiguous and small — a single
    place numbered 1000 would allocate a 1001x1001 matrix.
    """
    return [{**place, "cluster_id": i} for i, place in enumerate(places)]


def normalize_learned(learned: list[dict]) -> list[dict]:
    """Put clustered places on the caregiver's scales so the two can coexist.

    Relative order among the learned places is preserved — that is all any
    consumer reads — while the absolute numbers are brought onto the pin axes.
    """
    if not learned:
        return []

    max_freq = max((p.get("visit_frequency", 0) or 0) for p in learned)
    out = []
    for place in learned:
        freq = place.get("visit_frequency", 0) or 0
        scaled = (LEARNED_VISIT_CEILING * freq / max_freq) if max_freq else 0.0
        stay_min = place.get("avg_stay_time", 0.0) or 0.0
        out.append({
            **place,
            # Round up, never to zero: a place worth clustering is worth more
            # than "never been here", which is what a 0 would claim.
            "visit_frequency": max(1, round(scaled)),
            "avg_stay_time": round(stay_min * _MINUTES_TO_SECONDS, 1),
            "source": "learned",
        })
    return out


def merge_learned(existing: list[dict], learned: list[dict]) -> list[dict]:
    """Fresh clustering results + the caregiver's pins, pins winning.

    The mirror of what ``api/places.py`` does when the caregiver re-pins: that
    side keeps the learned entries, this side keeps the manual ones. Neither
    writer may delete the other's rows.
    """
    manual = [p for p in existing if p.get("source") == "manual"]
    return renumber(manual + normalize_learned(learned))
