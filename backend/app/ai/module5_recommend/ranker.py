"""Module 5 — Learned pointwise ranker.

A binary classifier over (context x candidate-place) rows; ranking a patient's
known places = sorting them by P(chosen | context, place). Two model kinds:

    "histgbt"  — HistGradientBoostingClassifier, the deliverable. Shallow and
                 regularized (tiny data); captures the slot x weekend x weather
                 interactions the pattern is built from. Handles NaN natively
                 (missing location -> NaN distance at inference).
    "logistic" — plain LogisticRegression, the diagnostic rung. Deliberately
                 gets NO hand-crafted interaction terms: if it matched the
                 trees, the pattern wasn't interaction-dependent after all.

Model selection honors the temporal design: HistGBT's iteration count is chosen
on the temporal TAIL of the training window (fit on days < tail, validate on the
tail), then the model is refit on the full training window. The test window is
never touched during selection.

Unknown weather at inference is marginalized: predict once per bucket and
average — the model is never fed a fabricated "sunny".

Persistence: ``save``/``load`` round-trip the fitted model + frozen place stats
+ provenance (data origin, metrics) as one pickle, mirroring Module 2's
per-patient artifact convention (``ranker_patient_{id}.pkl``).
"""
from __future__ import annotations

import math
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

from .featurize import (
    BUCKETS, FEATURE_NAMES, WEATHER_FEATURES, PlaceStatsNorm, pair_row, rows_for_event,
)

MODELS_DIR = Path(__file__).resolve().parents[2] / "ai" / "models"

_HISTGBT_FIXED = dict(
    max_depth=3, max_leaf_nodes=8, learning_rate=0.1,
    l2_regularization=1.0, early_stopping=False, random_state=0,
)
_ITER_GRID = (25, 50, 100, 200)
_TAIL_DAYS = 9  # temporal validation tail inside the train window


def _weather_cols(use_weather: bool) -> list[int]:
    """Column indices to keep: all, or all minus the weather one-hots."""
    if use_weather:
        return list(range(len(FEATURE_NAMES)))
    return [i for i, n in enumerate(FEATURE_NAMES) if n not in WEATHER_FEATURES]


class Module5Ranker:
    def __init__(self, kind: str = "histgbt", use_weather: bool = True):
        assert kind in ("histgbt", "logistic")
        self.kind = kind
        self.use_weather = use_weather
        self._cols = _weather_cols(use_weather)
        self.model = None
        self.norm: PlaceStatsNorm | None = None
        self.places: list[dict] = []          # frozen train-window clusters
        self.provenance: dict = {}

    # -- training --------------------------------------------------------------

    def fit(self, train_events: list, frozen_places: list[dict],
            label_fn, provenance: dict | None = None) -> "Module5Ranker":
        """Fit on decision events; ``label_fn(event) -> chosen cluster_id``.

        ``frozen_places`` MUST be train-window-only clusters (leakage guard —
        see evaluation.freeze_place_stats).
        """
        self.places = frozen_places
        self.norm = PlaceStatsNorm.from_places(frozen_places)

        X, y = self._matrix(train_events, label_fn)

        if self.kind == "logistic":
            self.model = LogisticRegression(max_iter=1000)
            self.model.fit(X, y)
        else:
            best_iter = self._select_iterations(train_events, label_fn)
            self.model = HistGradientBoostingClassifier(
                max_iter=best_iter, **_HISTGBT_FIXED)
            self.model.fit(X, y)

        self.provenance = {
            "kind": self.kind,
            "use_weather": self.use_weather,
            "n_train_events": len(train_events),
            "trained_at": datetime.utcnow().isoformat(),
            **(provenance or {}),
        }
        return self

    def _select_iterations(self, train_events: list, label_fn) -> int:
        """Pick max_iter on the temporal tail of the train window (never test)."""
        max_day = max(e.day_index for e in train_events)
        tail_start = max_day - _TAIL_DAYS + 1
        early = [e for e in train_events if e.day_index < tail_start]
        tail = [e for e in train_events if e.day_index >= tail_start]
        if not early or not tail:
            return 100

        X_early, y_early = self._matrix(early, label_fn)
        best_iter, best_acc = _ITER_GRID[0], -1.0
        for n_iter in _ITER_GRID:
            m = HistGradientBoostingClassifier(max_iter=n_iter, **_HISTGBT_FIXED)
            m.fit(X_early, y_early)
            acc = self._event_top1(m, tail, label_fn)
            if acc > best_acc:
                best_iter, best_acc = n_iter, acc
        return best_iter

    def _matrix(self, events: list, label_fn):
        X, y = [], []
        for e in events:
            rows, labels, _ = rows_for_event(e, self.places, self.norm, label_fn(e))
            X.extend(rows)
            y.extend(labels)
        return np.array(X)[:, self._cols], np.array(y)

    def _event_top1(self, model, events: list, label_fn) -> float:
        hits = 0
        for e in events:
            rows, _, cids = rows_for_event(e, self.places, self.norm)
            proba = model.predict_proba(np.array(rows)[:, self._cols])[:, 1]
            if cids[int(np.argmax(proba))] == label_fn(e):
                hits += 1
        return hits / len(events) if events else 0.0

    # -- inference ---------------------------------------------------------------

    def rank_event(self, event) -> list[tuple[int, float]]:
        """Rank frozen places for a DecisionEvent-shaped context (evaluation path)."""
        rows, _, cids = rows_for_event(event, self.places, self.norm)
        proba = self.model.predict_proba(np.array(rows)[:, self._cols])[:, 1]
        return sorted(zip(cids, proba.tolist()), key=lambda t: -t[1])

    def score_places(
        self,
        places: list[dict],
        now: datetime,
        current_lat: float | None,
        current_lng: float | None,
        weather_bucket: str | None = None,
    ) -> dict[int, float]:
        """P(chosen) per cluster_id for arbitrary places/context (API path).

        Unknown weather -> marginalize (average over buckets). Missing location
        -> NaN distance (HistGBT handles NaN natively; logistic does not, so the
        integrated deliverable is histgbt).
        """
        norm = PlaceStatsNorm.from_places(places)
        buckets = [weather_bucket] if weather_bucket else list(BUCKETS)
        lat = current_lat if current_lat is not None else math.nan
        lng = current_lng if current_lng is not None else math.nan

        scores: dict[int, float] = {}
        for p in places:
            probs = []
            for b in buckets:
                row = pair_row(
                    slot_morning=(now.hour < 12),
                    is_weekend=(now.weekday() >= 5),
                    weather_bucket=b,
                    current_lat=lat, current_lng=lng,
                    place=p, norm=norm,
                )
                x = np.array([row])[:, self._cols]
                probs.append(float(self.model.predict_proba(x)[0, 1]))
            scores[int(p["cluster_id"])] = float(np.mean(probs))
        return scores

    # -- persistence --------------------------------------------------------------

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "kind": self.kind,
                "use_weather": self.use_weather,
                "model": self.model,
                "norm": self.norm,
                "places": self.places,
                "provenance": self.provenance,
            }, f)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Module5Ranker":
        with open(path, "rb") as f:
            blob = pickle.load(f)
        r = cls(kind=blob["kind"], use_weather=blob["use_weather"])
        r.model = blob["model"]
        r.norm = blob["norm"]
        r.places = blob["places"]
        r.provenance = blob["provenance"]
        return r


def ranker_path(patient_id: int) -> Path:
    return MODELS_DIR / f"ranker_patient_{patient_id}.pkl"


def load_ranker(patient_id: int) -> Module5Ranker | None:
    """Per-patient model if one has been trained and persisted, else None
    (caller falls back to the rule-based scorer, flagged as such)."""
    path = ranker_path(patient_id)
    if not path.exists():
        return None
    try:
        return Module5Ranker.load(path)
    except Exception:
        return None
