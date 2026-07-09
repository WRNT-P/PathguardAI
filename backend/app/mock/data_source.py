"""Module 5 — Data source (swappable) + simulated generator.

Defines the seam between the model and where its training signal comes from:

    DataSource.decision_events() -> [DecisionEvent]   # ground-truth choices
    DataSource.raw_gps()         -> [gps dict]         # feeds Kalman + Module 1

``MockDataSource`` synthesizes ~90 days of simulated GPS with a *stochastic,
context-conditioned* pattern (time-of-day x day-of-week x weather), so that
which place is chosen genuinely depends on the situation — the thing Module 5
is meant to learn. ``RealDataSource`` (later) will yield the same shapes from
the DB + a real weather API, a drop-in swap.

SIMULATED DATA. Every number here is authored, not measured. Any accuracy
computed on it validates the *machinery*, not real-patient behavior.

Honesty properties baked into the generator (locked in design review):
  - probabilistic, never deterministic: per-context winner capped at ~0.65, so
    a perfect model still can't hit ~100% (100% would be a leakage alarm);
  - ~17.5% off-pattern events (context-free choices) — a realistic noise floor;
  - no single feature determines the label: the winner depends on the
    interaction of slot x weekend x weather (weather *flips* the winner on rainy
    days, *modulates* it otherwise, but is never a lone perfect predictor);
  - Home is a shared fallback across many contexts, so the majority-class
    baseline is a real competitor, not a strawman;
  - the generator emits only ground-truth (when / where-from / weather / chosen
    place) + raw GPS. It does NOT emit visit_frequency / avg_stay_time: those
    are derived later by clustering the *training window only*, so future visits
    physically cannot leak into a feature.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from .weather_provider import MockWeather, WeatherProvider

# Fixed calendar anchor so day-of-week and the whole series are reproducible and
# never depend on "today". 2025-01-01 is a Wednesday.
BASE_DATE = datetime(2025, 1, 1, 0, 0, 0)

# Two decision points per day: a morning errand slot and an evening leisure slot.
SLOTS: tuple[tuple[str, int], ...] = (("morning", 9), ("evening", 17))
SLOT_NAMES: tuple[str, ...] = tuple(name for name, _ in SLOTS)

# Five planted places around one Bangkok neighborhood, each >400 m from the
# others so DBSCAN (eps=50 m) resolves them as distinct clusters.
PLACES: dict[str, tuple[float, float]] = {
    "home":   (13.7500, 100.5000),
    "market": (13.7500, 100.5040),   # ~433 m east
    "park":   (13.7540, 100.5000),   # ~445 m north
    "temple": (13.7460, 100.4960),   # ~570 m southwest
    "clinic": (13.7520, 100.5040),   # ~495 m northeast
}
PLACE_KEYS: tuple[str, ...] = tuple(PLACES.keys())

# Fraction of events that ignore context entirely (patient does something
# unusual). This caps the achievable accuracy below 100% — the realistic floor.
OFF_PATTERN_RATE = 0.175


def _dist(winner: str, winner_p: float, home_p: float | None = None) -> dict[str, float]:
    """Build a per-context distribution: a winner, a Home fallback, rest spread.

    ``winner_p`` (<= 0.65) is the peak. If the winner *is* home, ``home_p`` is
    ignored. Remaining mass is spread uniformly over the other places.
    """
    probs = {k: 0.0 for k in PLACE_KEYS}
    probs[winner] = winner_p
    if winner != "home":
        probs["home"] = home_p if home_p is not None else 0.0
    rest = [k for k in PLACE_KEYS if probs[k] == 0.0]
    remainder = 1.0 - sum(probs.values())
    share = remainder / len(rest) if rest else 0.0
    for k in rest:
        probs[k] = share
    return probs


# Base P(place | context), context = (slot, is_weekend, weather).
# Winners never exceed 0.65. Note the interaction structure:
#   - "sunny" alone picks nothing: morning-sunny-weekday -> market,
#     morning-sunny-weekend -> temple, evening-sunny -> park;
#   - "morning" alone picks nothing: weekday -> market, weekend -> temple;
#   - rainy -> home regardless of slot/weekend (weather *flips* the winner);
#   - evening + hot -> home (weather *modulates*: too hot to go out).
_PATTERN: dict[tuple[str, bool, str], dict[str, float]] = {
    ("morning", False, "sunny"): _dist("market", 0.65, 0.20),
    ("morning", False, "hot"):   _dist("market", 0.55, 0.25),
    ("morning", False, "rainy"): _dist("home",   0.65),
    ("morning", True,  "sunny"): _dist("temple", 0.60, 0.20),
    ("morning", True,  "hot"):   _dist("temple", 0.55, 0.25),
    ("morning", True,  "rainy"): _dist("home",   0.65),
    ("evening", False, "sunny"): _dist("park",   0.60, 0.25),
    ("evening", False, "hot"):   _dist("home",   0.60),
    ("evening", False, "rainy"): _dist("home",   0.65),
    ("evening", True,  "sunny"): _dist("park",   0.60, 0.25),
    ("evening", True,  "hot"):   _dist("home",   0.60),
    ("evening", True,  "rainy"): _dist("home",   0.65),
}


@dataclass
class DecisionEvent:
    """One ground-truth situational choice. No place stats here by design."""
    day_index: int
    slot: str                 # "morning" | "evening"
    timestamp: datetime
    current_lat: float        # PRE-MOVE position (home) — never the chosen place
    current_lng: float
    is_weekend: bool
    weather_bucket: str       # "sunny" | "rainy" | "hot"
    chosen_place_key: str     # semantic label; harness maps it to a cluster id
    chosen_lat: float
    chosen_lng: float


class DataSource(ABC):
    """Contract shared by the mock now and the real DB/weather source later."""

    @abstractmethod
    def decision_events(self) -> list[DecisionEvent]:
        raise NotImplementedError

    @abstractmethod
    def raw_gps(self) -> list[dict]:
        """GPS dicts with keys latitude, longitude, speed, timestamp."""
        raise NotImplementedError


class MockDataSource(DataSource):
    def __init__(
        self,
        n_days: int = 90,
        seed: int = 42,
        weather: WeatherProvider | None = None,
        points_per_stop: int = 10,
        gps_noise_m: float = 6.0,
    ):
        self.n_days = n_days
        self.points_per_stop = points_per_stop
        self.gps_noise_m = gps_noise_m
        self._rng = np.random.RandomState(seed)
        self.weather = weather or MockWeather(n_days, SLOT_NAMES, seed=seed + 1)
        self._events: list[DecisionEvent] = []
        self._gps: list[dict] = []
        self._generate()

    # -- public API -----------------------------------------------------------

    def decision_events(self) -> list[DecisionEvent]:
        return self._events

    def raw_gps(self) -> list[dict]:
        return self._gps

    def effective_distribution(self, slot: str, is_weekend: bool, weather: str) -> dict[str, float]:
        """P(place | context) *after* off-pattern mixing.

        Exposed so the harness can compute the analytic oracle (majority floor,
        Bayes ceiling with/without weather) before any model is trained.
        p_eff = (1 - off) * base + off * uniform.
        """
        base = _PATTERN[(slot, is_weekend, weather)]
        u = 1.0 / len(PLACE_KEYS)
        return {k: (1 - OFF_PATTERN_RATE) * base[k] + OFF_PATTERN_RATE * u for k in PLACE_KEYS}

    # -- generation -----------------------------------------------------------

    def _choose(self, slot: str, is_weekend: bool, weather: str) -> str:
        """Sample a place: off-pattern -> uniform, else from the context pattern."""
        if self._rng.random_sample() < OFF_PATTERN_RATE:
            return str(self._rng.choice(PLACE_KEYS))
        base = _PATTERN[(slot, is_weekend, weather)]
        return str(self._rng.choice(PLACE_KEYS, p=[base[k] for k in PLACE_KEYS]))

    def _generate(self) -> None:
        home_lat, home_lng = PLACES["home"]
        for day in range(self.n_days):
            date = BASE_DATE + timedelta(days=day)
            is_weekend = date.weekday() >= 5

            picks: dict[str, str] = {}
            for slot, hour in SLOTS:
                weather = self.weather.bucket(day, slot)
                place = self._choose(slot, is_weekend, weather)
                picks[slot] = place
                plat, plng = PLACES[place]
                self._events.append(DecisionEvent(
                    day_index=day,
                    slot=slot,
                    timestamp=date.replace(hour=hour),
                    current_lat=home_lat,      # pre-move: patient is at home
                    current_lng=home_lng,
                    is_weekend=is_weekend,
                    weather_bucket=weather,
                    chosen_place_key=place,
                    chosen_lat=plat,
                    chosen_lng=plng,
                ))

            # Render a full day's GPS track: home -> morning place -> home ->
            # evening place -> home. Home accrues the most points (visited 3x/day)
            # which makes it the majority place, as intended.
            self._render_day(date, [
                ("home", 7),
                (picks["morning"], 9),
                ("home", 12),
                (picks["evening"], 17),
                ("home", 20),
            ])

    def _render_day(self, date: datetime, stops: list[tuple[str, int]]) -> None:
        noise_deg = self.gps_noise_m / 111_000
        for place_key, hour in stops:
            lat, lng = PLACES[place_key]
            t = date.replace(hour=hour)
            for _ in range(self.points_per_stop):
                self._gps.append({
                    "latitude": lat + self._rng.normal(0, noise_deg),
                    "longitude": lng + self._rng.normal(0, noise_deg),
                    # dwelling -> near-zero speed with a little jitter
                    "speed": max(0.0, self._rng.normal(0.3, 0.2)),
                    "timestamp": t,
                })
                t += timedelta(minutes=2)
