"""GPS smoothing via a 2D constant-velocity Kalman filter, one per patient.

Raw phone GPS jitters by several metres even when the patient is still. The
filter tracks position + velocity and fuses each new reading with its own
prediction, producing a smoother track for the live map and the AI modules.

State is held in memory per patient, so consecutive points refine each other.
Call ``reset`` after a long gap or GPS loss to avoid the filter "snapping"
across a discontinuity.
"""
import numpy as np
from filterpy.kalman import KalmanFilter

# one filter per patient_id, keyed so consecutive readings build on each other
_filters: dict[int, KalmanFilter] = {}


def _new_filter(latitude: float, longitude: float) -> KalmanFilter:
    kf = KalmanFilter(dim_x=4, dim_z=2)  # state [lat, lon, v_lat, v_lon], measure [lat, lon]
    kf.x = np.array([latitude, longitude, 0.0, 0.0])
    kf.F = np.array(  # constant-velocity transition (unit time step)
        [
            [1, 0, 1, 0],
            [0, 1, 0, 1],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ],
        dtype=float,
    )
    kf.H = np.array(  # we only observe position, not velocity
        [
            [1, 0, 0, 0],
            [0, 1, 0, 0],
        ],
        dtype=float,
    )
    kf.P *= 1000.0          # large initial uncertainty
    kf.R = np.eye(2) * 1e-4  # measurement noise — GPS jitter (~degrees²)
    kf.Q = np.eye(4) * 1e-6  # process noise — how much real motion can vary
    return kf


def smooth(patient_id: int, latitude: float, longitude: float) -> tuple[float, float]:
    """Return the Kalman-smoothed (lat, lon) for a patient's next raw reading.

    The first reading for a patient is returned unchanged (nothing to fuse yet).
    """
    kf = _filters.get(patient_id)
    if kf is None:
        _filters[patient_id] = _new_filter(latitude, longitude)
        return latitude, longitude

    kf.predict()
    kf.update([latitude, longitude])
    return float(kf.x[0]), float(kf.x[1])


def reset(patient_id: int) -> None:
    """Drop a patient's filter state (after GPS loss or a large time gap)."""
    _filters.pop(patient_id, None)
