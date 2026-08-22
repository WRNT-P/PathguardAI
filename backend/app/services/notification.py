"""FCM push to the caregiver — the last link in the alert chain.

Everything upstream of this file works and has for days: GPS reaches Neon,
risk scores itself on ingest, and an emergency writes an ``alerts`` row. Until
now that row was where the chain stopped — nothing left the server, so the only
way to learn a patient had wandered was to be watching the dashboard.

**Cooldown lives here, not in risk.py.** ``alerts`` is an audit trail: a row per
scoring round the condition holds, which at the 60 s ingest throttle is a row a
minute for as long as a patient stays in a danger zone. That is correct as a
record and catastrophic as a push schedule — a caregiver notified every minute
mutes the app, and then the one alert that mattered arrives silently. So the
alert table keeps its behaviour untouched and the rate limit sits at the send
boundary, keyed on ``push_notifications``: DB state, so it survives a restart
and stays correct if this ever runs on more than one worker.

The cooldown is per (patient, alert_type) — a ``geofence`` alert must never be
suppressed because a ``gps_loss`` push went out ninety seconds ago.
"""
from __future__ import annotations

import asyncio
import logging

from firebase_admin import messaging
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.models import Alert

logger = logging.getLogger(__name__)

_TITLES = {
    "emergency": "PathGuard — ต้องการความช่วยเหลือ",
    "geofence": "PathGuard — เข้าเขตอันตราย",
    "gps_loss": "PathGuard — สัญญาณ GPS หาย",
    "gps_lost": "PathGuard — สัญญาณ GPS หาย",
    "wandering": "PathGuard — พบการเดินหลง",
}


def _send_one(token: str, title: str, body: str, data: dict[str, str]) -> None:
    """Blocking FCM send for a single device. Raises on failure."""
    messaging.send(
        messaging.Message(
            token=token,
            notification=messaging.Notification(title=title, body=body),
            data=data,
            android=messaging.AndroidConfig(priority="high"),
        )
    )


async def notify_alert(db: AsyncSession, alert: Alert, cooldown_s: float) -> dict:
    """Push one alert to the patient's caregiver, subject to the cooldown.

    Never raises. Every caller sits downstream of a stored GPS position and a
    saved risk score, and neither may be lost because Firebase was unreachable —
    so failure is a returned status (``sent`` / ``cooldown`` / ``no_caregiver`` /
    ``failed`` / ``error``), not an exception the request has to survive.
    """
    try:
        return await _notify_alert(db, alert, cooldown_s)
    except Exception:
        logger.exception("push notification failed for alert %s", alert.id)
        return {"status": "error", "recipients": 0}


async def _notify_alert(db: AsyncSession, alert: Alert, cooldown_s: float) -> dict:
    since = await crud.seconds_since_last_push(db, alert.patient_id, alert.alert_type)
    if since is not None and since < cooldown_s:
        logger.info(
            "push suppressed: patient=%s type=%s last sent %.0fs ago (cooldown %.0fs)",
            alert.patient_id, alert.alert_type, since, cooldown_s,
        )
        return {"status": "cooldown", "recipients": 0, "seconds_since": since}

    tokens = await crud.get_caregiver_tokens(db, alert.patient_id)
    if not tokens:
        logger.warning(
            "no caregiver device for patient=%s — alert %s raised but not pushed",
            alert.patient_id, alert.id,
        )
        return {"status": "no_caregiver", "recipients": 0}

    # The app opens the map on tap, so the payload has to carry where to look.
    # FCM data values must be strings.
    data = {
        "alert_id": str(alert.id),
        "patient_id": str(alert.patient_id),
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "latitude": "" if alert.latitude is None else str(alert.latitude),
        "longitude": "" if alert.longitude is None else str(alert.longitude),
    }
    title = _TITLES.get(alert.alert_type, "PathGuard")

    delivered = 0
    for token in tokens:
        try:
            # firebase-admin is synchronous HTTP; off-thread so a slow FCM call
            # can't stall the event loop mid-GPS-ingest.
            await asyncio.to_thread(_send_one, token, title, alert.message, data)
            delivered += 1
        except messaging.UnregisteredError:
            # App uninstalled or token rotated — drop it, or every future push
            # fails on it forever.
            logger.info("dropping unregistered device token for patient=%s",
                        alert.patient_id)
            await crud.delete_device_token(db, token)
        except Exception:
            logger.exception("FCM send failed for patient=%s", alert.patient_id)

    if delivered == 0:
        return {"status": "failed", "recipients": 0}

    await crud.record_push(db, alert.patient_id, alert.id, alert.alert_type, delivered)
    return {"status": "sent", "recipients": delivered}
