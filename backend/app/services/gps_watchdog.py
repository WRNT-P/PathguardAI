"""Notice a patient whose phone has stopped reporting, and say so.

Until this existed, nothing in the system could. ``gps_loss`` alerts were
written in exactly two places and neither can fire on silence:

  * ``api/risk.py`` writes one, but ``evaluate_risk`` only ever runs from GPS
    ingest (``api/gps.py``) or from a hand-called ``GET /api/risk``. On the
    ingest path the gap it measures is always ~0, because the reading it
    measures against is the one that just arrived. No ingest, no run.
  * ``api/search_area.py`` writes one, but only after a caregiver has already
    opened the search — that is, after a human worked it out unaided.

So the case the report describes — the patient's phone dies and the family is
told — had no code path at all. This is it.

The other half of the cycle already works: ``risk.py`` resolves every open
``gps_loss`` row the moment a fresh point lands (``_resolve_stale``), so this
module only ever has to open them.

Design:
  * **Throttled by the alert, not by a timer.** One unresolved ``gps_loss`` row
    per patient is enough; the push cooldown in ``notification.py`` handles
    re-notifying. Re-raising every scan would fill the caregiver's timeline
    with one row a minute for as long as the phone stays off.
  * **A patient who has never reported is not missing.** ``detect_gps_gap``
    treats "no reading" as lost by design (unknown ⇒ caution), which is right
    inside a search and wrong here: a freshly registered patient whose phone is
    not paired yet would page their family. They are skipped explicitly.
  * **Never fatal.** A scan that raises must not take the application with it,
    and the threshold comes from the rule KB so it can be changed live
    (``PATCH /api/admin/rules``) without a restart — which is also how a demo
    shortens the 10-minute gap to one minute.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.module3_risk import detect_gps_gap
from app.db import crud, rule_repository
from app.db.database import AsyncSessionLocal
from app.services.notification import notify_alert

logger = logging.getLogger(__name__)

# How often to look. Independent of ``gps_gap_seconds`` (how long a silence has
# to last to count): this is only the granularity of noticing, so a scan a
# minute detects a 10-minute gap within 10–11 minutes. Cheap — one indexed
# latest-GPS read per patient, on a table of tens of patients.
WATCHDOG_INTERVAL_S = 60.0


def _as_reading(point) -> dict:
    """The GPS row as a plain dict, with the timestamp made timezone-aware.

    ``detect_gps_gap`` accepts either shape, but it cannot subtract a naive
    timestamp from an aware ``now``: it catches the TypeError and answers
    ``gps_lost=True``, because unknown means caution once a search is under way.
    Here that bias is exactly wrong — it would page every family in the system,
    including the ones whose phone is reporting normally every minute.

    Postgres stores ``recorded_at`` as ``timestamptz`` and hands back an aware
    datetime, so this only bites under SQLite; ``gps.py`` normalises the same
    way, for the same reason, before comparing risk-score timestamps.

    A dict rather than mutating the row: ``latest`` is attached to the session,
    so assigning to it would queue an UPDATE on the next commit.
    """
    recorded_at = point.recorded_at
    if recorded_at is not None and recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    return {
        "latitude": point.latitude,
        "longitude": point.longitude,
        "recorded_at": recorded_at,
    }


async def scan_once(db: AsyncSession, now: datetime | None = None) -> dict:
    """Check every patient for a GPS gap and raise an alert for new ones.

    Returns a summary (``{"checked", "lost", "raised"}``) rather than nothing,
    so a test can assert on it and the loop can log something meaningful.
    ``now`` is injectable so a test does not have to wait ten minutes.
    """
    now = now or datetime.now(timezone.utc)
    threshold_s = await rule_repository.get_threshold(
        db, rule_repository.GPS_GAP_SECONDS)
    cooldown_s = await rule_repository.get_threshold(
        db, rule_repository.PUSH_COOLDOWN_SECONDS)

    patient_ids = await crud.get_all_patient_ids(db)
    checked = lost = raised = 0

    for patient_id in patient_ids:
        latest = await crud.get_latest_gps(db, patient_id)
        if latest is None:
            # Never reported a position. Not missing — not set up. Passing this
            # to detect_gps_gap would return gps_lost=True and page a family
            # about a phone that has not been paired yet.
            continue

        checked += 1
        gap = detect_gps_gap(last_reading=_as_reading(latest), now=now,
                             threshold_s=threshold_s)
        if not gap["gps_lost"]:
            continue

        lost += 1
        if await crud.get_unresolved_alerts_by_type(db, patient_id, "gps_loss"):
            # Already told them. risk.py closes this row when GPS returns.
            continue

        last_known = gap["last_known"] or {}
        alert = await crud.save_alert(
            db,
            patient_id,
            alert_type="gps_loss",
            severity="high",
            message=(
                f"สัญญาณ GPS ขาดหาย ({gap['gap_seconds']} วินาที) — "
                "ส่งตำแหน่งล่าสุดที่ทราบให้แล้ว"
            ),
            latitude=last_known.get("latitude"),
            longitude=last_known.get("longitude"),
        )
        await notify_alert(db, alert, cooldown_s)
        raised += 1

    return {"checked": checked, "lost": lost, "raised": raised}


async def run_forever(interval_s: float = WATCHDOG_INTERVAL_S) -> None:
    """Scan on a loop until cancelled. Started from ``main.py``'s lifespan.

    Sleeps first: at startup every patient's last reading is as old as the
    process is new, and a restart is not evidence about anybody's phone.

    Owns its own session per scan rather than borrowing one — ``get_db`` is a
    request dependency and there is no request here.
    """
    logger.info("GPS watchdog started (every %.0fs)", interval_s)
    while True:
        try:
            await asyncio.sleep(interval_s)
            async with AsyncSessionLocal() as session:
                try:
                    summary = await scan_once(session)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise
            if summary["raised"]:
                logger.info(
                    "GPS watchdog: %s patient(s) went quiet, %s alert(s) raised",
                    summary["lost"], summary["raised"],
                )
        except asyncio.CancelledError:
            logger.info("GPS watchdog stopped")
            raise
        except Exception:  # noqa: BLE001 — a bad scan must not end the loop
            logger.exception("GPS watchdog scan failed; continuing")
