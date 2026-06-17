# pathguard/backend/app/ai/module5_recommend/user_context_analysis.py
"""Module 5.1 — User Context Analysis.

Assembles the inputs Module 5 needs into a single ``UserContext`` value:
the patient's known places (from Module 1's behavioral profile) plus the
"current" location and time used for scoring.

Pure: no DB access. The router gathers the raw inputs (profile row, current
lat/lng) and hands them here, which keeps this unit-testable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class UserContext:
    patient_id: int
    current_lat: float | None
    current_lng: float | None
    now: datetime
    known_places: list[dict] = field(default_factory=list)

    @property
    def has_profile(self) -> bool:
        return bool(self.known_places)

    @property
    def has_location(self) -> bool:
        return self.current_lat is not None and self.current_lng is not None


def _parse_known_places(raw: str | None) -> list[dict]:
    """Decode the ``known_places`` JSON column into a list, tolerating bad data.

    Returns ``[]`` for None/empty/malformed input so the caller never has to
    distinguish "no profile" from "broken profile" — both mean no places.
    """
    if not raw:
        return []
    try:
        places = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return places if isinstance(places, list) else []


def build_user_context(
    profile,
    current_lat: float | None,
    current_lng: float | None,
    now: datetime,
) -> UserContext:
    """Build a ``UserContext`` from a BehavioralProfile row (or None) + location.

    ``profile`` is the ORM object returned by ``crud.get_behavioral_profile``
    (or None when Module 1 hasn't trained this patient yet).
    """
    raw_places = getattr(profile, "known_places", None) if profile else None
    return UserContext(
        patient_id=profile.patient_id if profile else 0,
        current_lat=current_lat,
        current_lng=current_lng,
        now=now,
        known_places=_parse_known_places(raw_places),
    )
