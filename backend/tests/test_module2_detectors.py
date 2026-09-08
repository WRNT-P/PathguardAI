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

# cluster_id and visit_frequency are what a real behavioral_profile always
# carries (places.py renumbers pins from 0; place_clustering emits both), and
# the classifier now reads familiarity through the same radius-aware helper
# Module 3 uses, which needs them.
KNOWN_PLACES = [
    {"cluster_id": 0, "latitude": 13.7500, "longitude": 100.5000,
     "label": "home", "visit_frequency": 100},
    {"cluster_id": 1, "latitude": 13.7550, "longitude": 100.5050,
     "label": "market", "visit_frequency": 40},
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


# ── Resting at a familiar place must not read as confusion ───────────────────
#
# Measured on live patient 44 (2026-09-08): confusion came back 1.000 — the
# min(score, 1.0) ceiling — for a patient sitting still at home, contributing
# the full 20.0 points of the 0.20-weighted C factor. Two hardcoded constants
# did it, and both are fixed below the tests.

HOME_PIN = [{
    "cluster_id": 0, "latitude": 13.7500, "longitude": 100.5000,
    "visit_frequency": 100, "radius_m": 400,     # a wide pin, as a real home may be
}]

# 279 m due north of the pin — outside the classifier's old hardcoded 100 m,
# comfortably inside the pin's own 400 m radius. This is patient 44's real gap.
INSIDE_WIDE_PIN_LAT = 13.7500 + 279.0 * LAT_DEG_PER_M
INSIDE_WIDE_PIN_LNG = 100.5000


def _stationary(lat, lng, points=10):
    """A phone that has not moved: identical fixes, zero speed."""
    return [{"latitude": lat, "longitude": lng, "speed": 0.0} for _ in range(points)]


def test_classifier_honours_each_pins_own_radius():
    """The familiarity feature must use the pin's radius_m, not a hardcoded 100 m.

    Module 3 reads familiarity through find_nearest_cluster, which honours
    radius_m. The classifier had its own `if min_dist <= 0.1` rule, so the same
    patient at the same spot was "at home" to the risk formula and "nowhere
    familiar" to the confusion classifier at the same instant.
    """
    clf = StopConfusionClassifier()
    feat = clf.extract_features(
        recent_gps=_stationary(INSIDE_WIDE_PIN_LAT, INSIDE_WIDE_PIN_LNG),
        stop_duration_seconds=10_800,
        current_lat=INSIDE_WIDE_PIN_LAT,
        current_lng=INSIDE_WIDE_PIN_LNG,
        predicted_route=None,
        known_places=HOME_PIN,
    )
    assert feat[3] > 0.0, (
        "279 m from a 400 m pin is inside it — familiarity must not be 0", feat[3])


def test_no_predicted_route_does_not_max_the_deviation_term():
    """No route to deviate from is not evidence of deviating.

    The old fallback wrote 300.0 m, and _rule_based_score divides by 250.0, so
    the term hit its 0.15 ceiling on every patient the route predictor could not
    fit — which is the ordinary case, not the exception.
    """
    clf = StopConfusionClassifier()
    feat = clf.extract_features(
        recent_gps=_stationary(INSIDE_WIDE_PIN_LAT, INSIDE_WIDE_PIN_LNG),
        stop_duration_seconds=10_800,
        current_lat=INSIDE_WIDE_PIN_LAT,
        current_lng=INSIDE_WIDE_PIN_LNG,
        predicted_route=None,
        known_places=HOME_PIN,
    )
    assert feat[4] < 250.0, (
        "a missing route must not contribute a full-ceiling deviation", feat[4])


def test_a_long_still_stop_at_home_is_a_normal_stop():
    """The headline case: three hours asleep at home scores near zero.

    The module docstring already promises this ("Normal Stop ... อยู่บ้าน"); it
    was the implementation that disagreed.
    """
    clf = StopConfusionClassifier()
    res = clf.classify(
        recent_gps=_stationary(INSIDE_WIDE_PIN_LAT, INSIDE_WIDE_PIN_LNG),
        stop_duration_seconds=10_800,
        current_lat=INSIDE_WIDE_PIN_LAT,
        current_lng=INSIDE_WIDE_PIN_LNG,
        predicted_route=None,
        known_places=HOME_PIN,
    )
    assert res["status"] == "normal"
    assert res["confidence_score"] <= 0.2, res


def test_the_same_long_still_stop_somewhere_unknown_stays_confused():
    """Guard against fixing the false positive by deleting the detection.

    Identical duration and stillness, only the place changes — 5 km from every
    pin. This must still clear the 0.60 threshold.
    """
    clf = StopConfusionClassifier()
    far_lat = 13.7500 + 5_000.0 * LAT_DEG_PER_M
    res = clf.classify(
        recent_gps=_stationary(far_lat, 100.5000),
        stop_duration_seconds=10_800,
        current_lat=far_lat,
        current_lng=100.5000,
        predicted_route=None,
        known_places=HOME_PIN,
    )
    assert res["status"] == "confused", res
