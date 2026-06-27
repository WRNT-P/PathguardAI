"""Module 2 — Destination Prediction: predict patient's next place from behavior."""
from .cluster_matcher import find_nearest_cluster, get_familiarity, haversine_km

__all__ = [
    "DestinationPredictor",
    "find_nearest_cluster",
    "get_familiarity",
    "haversine_km",
]


def __getattr__(name):
    # Lazy import so consumers that only need the sklearn/numpy detectors
    # (wandering / confusion / route) don't pull in TensorFlow via the LSTM chain.
    if name == "DestinationPredictor":
        from .destination_prediction import DestinationPredictor
        return DestinationPredictor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
