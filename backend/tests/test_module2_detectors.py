"""Module 2 — wandering detection & stop/confusion classification.

Synthetic-trajectory tests (no DB). These mirror the standalone scripts
test_wandering_detection.py / test_stop_confusion.py but as pytest cases.
sklearn models are seeded inside the generators so results are deterministic.
"""
import numpy as np

from app.ai.module2_prediction.stop_confusion_classification import (
    StopConfusionClassifier,
)
from app.ai.module2_prediction.wandering_detection import WanderingDetector

LAT_DEG_PER_M = 1.0 / 111_000.0


def _lng_deg_per_m(lat):
    return 1.0 / (111_000.0 * np.cos(np.radians(lat)))


def straight_path(start_lat, start_lng, steps=30, step_m=10.0, bearing=0.0):
    np.random.seed(42)
    lat, lng, bear = start_lat, start_lng, bearing
    lng_m = _lng_deg_per_m(start_lat)
    out = []
    for _ in range(steps):
        speed = float(max(0.8, 1.4 + np.random.normal(0, 0.15)))
        bear += float(np.random.normal(0, 2.0))
        rad = np.radians(bear)
        out.append({"latitude": lat, "longitude": lng, "speed": speed})
        lat += step_m * np.cos(rad) * LAT_DEG_PER_M
        lng += step_m * np.sin(rad) * lng_m
    return out


def wandering_path(start_lat, start_lng, steps=30):
    np.random.seed(42)
    lat, lng = start_lat, start_lng
    lng_m = _lng_deg_per_m(start_lat)
    out = []
    for i in range(steps):
        rad = np.radians((i * 45) % 360 + np.random.normal(0, 5))
        dist = 5.0 + np.random.normal(0, 1)
        lat += dist * np.cos(rad) * LAT_DEG_PER_M
        lng += dist * np.sin(rad) * lng_m
        out.append({"latitude": lat, "longitude": lng, "speed": 0.8})
    return out


def _normal_history():
    history = []
    for b in (0, 90, 180, 270):
        history += straight_path(13.75, 100.50, steps=30, bearing=b)
    history += straight_path(13.75, 100.50, steps=30, bearing=45, step_m=5.0)
    history += straight_path(13.75, 100.50, steps=30, bearing=135, step_m=15.0)
    return history


# ── Wandering detection ───────────────────────────────────────────────────────

def test_wandering_detector_fits():
    detector = WanderingDetector(contamination=0.05, window_size=15)
    res = detector.fit(_normal_history())
    assert res.get("status") == "fitted"


def test_normal_walk_not_flagged_as_wandering():
    detector = WanderingDetector(contamination=0.05, window_size=15)
    detector.fit(_normal_history())
    res = detector.detect(straight_path(13.75, 100.50, steps=20, bearing=10))
    assert res["wandering_level"] == "normal"


def test_wandering_walk_is_detected():
    detector = WanderingDetector(contamination=0.05, window_size=15)
    detector.fit(_normal_history())
    res = detector.detect(wandering_path(13.75, 100.50, steps=20))
    assert res["wandering_detected"] is True


def test_rule_based_fallback_without_fit():
    detector = WanderingDetector(window_size=15)
    res = detector.detect(wandering_path(13.75, 100.50, steps=20))
    assert res["wandering_detected"] is True


# ── Stop / confusion classification ───────────────────────────────────────────

KNOWN_PLACES = [
    {"latitude": 13.7500, "longitude": 100.5000, "label": "home"},
    {"latitude": 13.7550, "longitude": 100.5050, "label": "market"},
]
PREDICTED_ROUTE = [
    (13.7500, 100.5000), (13.7510, 100.5010),
    (13.7520, 100.5020), (13.7530, 100.5030),
]


def _confusion_history(start_lat, start_lng, steps=10, curvy=False):
    np.random.seed(42)
    lat, lng = start_lat, start_lng
    lng_m = _lng_deg_per_m(start_lat)
    out = []
    for i in range(steps):
        bear = (i * 90) % 360 if curvy else 10.0
        rad = np.radians(bear)
        lat += 10.0 * np.cos(rad) * LAT_DEG_PER_M
        lng += 10.0 * np.sin(rad) * lng_m
        out.append({"latitude": lat, "longitude": lng, "speed": 0.5 if curvy else 1.4})
    return out


def test_confused_stop_classified_confused():
    clf = StopConfusionClassifier()
    res = clf.classify(
        recent_gps=_confusion_history(13.7900, 100.5900, curvy=True),
        stop_duration_seconds=900,
        current_lat=13.7900,
        current_lng=100.5900,
        predicted_route=PREDICTED_ROUTE,
        known_places=KNOWN_PLACES,
    )
    assert res["status"] == "confused"


def test_normal_stop_classified_normal():
    clf = StopConfusionClassifier()
    res = clf.classify(
        recent_gps=_confusion_history(13.7551, 100.5051, curvy=False),
        stop_duration_seconds=60,
        current_lat=13.7551,
        current_lng=100.5051,
        predicted_route=PREDICTED_ROUTE,
        known_places=KNOWN_PLACES,
    )
    assert res["status"] == "normal"


def test_rule_based_confusion_without_training():
    clf = StopConfusionClassifier()
    res = clf.classify(
        recent_gps=_confusion_history(13.7800, 100.5500, curvy=True),
        stop_duration_seconds=600,
        current_lat=13.7800,
        current_lng=100.5500,
        predicted_route=PREDICTED_ROUTE,
        known_places=KNOWN_PLACES,
    )
    assert res["status"] == "confused"
