"""Module 5 — Weather provider (swappable).

Weather is the feature that makes Module 5 distinct from Module 2 (which has
none). It is a *decision-context* attribute, not a per-GPS-fix attribute, so it
lives here behind a small contract rather than on the ``GPSData`` rows.

``MockWeather`` synthesizes a reproducible, Bangkok-flavored weather series for
the simulated data. ``RealWeather`` (later) will pull historical weather from an
API keyed by (lat, lng, time) and return the *same* three coarse buckets — a
drop-in swap, no downstream change.

Buckets are deliberately coarse (sunny / rainy / hot): 30-90 days of data can't
support a continuous temperature feature without overfitting.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

BUCKETS = ("sunny", "rainy", "hot")


class WeatherProvider(ABC):
    """Contract: given a day index and a time slot, return one coarse bucket."""

    @abstractmethod
    def bucket(self, day_index: int, slot: str) -> str:
        """Return 'sunny' | 'rainy' | 'hot' for the given day/slot."""
        raise NotImplementedError


class MockWeather(WeatherProvider):
    """Reproducible simulated weather.

    All buckets are sampled once in ``__init__`` into a fixed table keyed by
    (day_index, slot), so a given ``seed`` always yields the same series
    regardless of call order — essential for a deterministic test suite.

    Bangkok bias: mornings skew sunny/hot; evenings carry the afternoon rain, so
    'rainy' is weighted toward the evening slot. This gives every bucket real
    support in both the train and test windows.
    """

    # P(bucket) per slot — mornings drier, evenings rainier (Bangkok-ish).
    _SLOT_PROBS = {
        "morning": {"sunny": 0.50, "hot": 0.40, "rainy": 0.10},
        "evening": {"sunny": 0.35, "hot": 0.25, "rainy": 0.40},
    }

    def __init__(self, n_days: int, slots: tuple[str, ...], seed: int = 7):
        rng = np.random.RandomState(seed)
        self._table: dict[tuple[int, str], str] = {}
        for day in range(n_days):
            for slot in slots:
                probs = self._SLOT_PROBS[slot]
                weights = [probs[b] for b in BUCKETS]
                self._table[(day, slot)] = str(rng.choice(BUCKETS, p=weights))

    def bucket(self, day_index: int, slot: str) -> str:
        return self._table[(day_index, slot)]
