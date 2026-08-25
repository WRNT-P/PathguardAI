"""Who is calling, and whose patient may they ask about.

Every endpoint in this backend has been open since the day it was written. Guess
that a patient id is 1, 2 or 3 — they are sequential integers — and you can read
a dementia patient's live position from anywhere on the internet the moment the
tunnel is up. That was a stated, accepted risk while the alert chain was being
built (plan D5); this is the part that closes it.

Two questions, deliberately separate:

* **Authentication** — is this request from a real signed-in account? A Firebase
  ID token in ``Authorization: Bearer <token>``, verified against Firebase, then
  mapped through ``users.firebase_uid`` to the internal int id every other table
  is keyed on.
* **Authorization** — may that account see *this* patient? Only the patient
  themselves, or the caregiver named in ``users.caregiver_id``. Caregiver A
  asking about caregiver B's patient is a 403, not a 404: pretending the patient
  does not exist would be a lie the caller can disprove by watching timings.

``AUTH_ENABLED`` (env, default **off**) is the switch. Off, every request is let
through as an unauthenticated caller and the server says so loudly at startup —
which is the state plan D5 chose while the pilot runs watched on a laptop with
an unshared URL. On, the rules above apply everywhere.

Leaving it off by default is a deliberate, uncomfortable choice: the dashboard in
``scripts/demo_server.py`` is a plain HTML page with no Firebase sign-in, and it
is the caregiver's documented fallback for receiving alerts (plan D6). Turning
auth on without giving that page a token silently removes a safety net around a
person with dementia. So the switch exists, everything behind it is written and
tested, and flipping it is a decision with a named consequence rather than a
default nobody reviewed.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.database import get_db

logger = logging.getLogger(__name__)


def _read_flag() -> bool:
    return os.getenv("AUTH_ENABLED", "false").strip().lower() in ("1", "true", "yes")


# Read once at import. Tests monkeypatch this attribute; nothing else writes it.
AUTH_ENABLED = _read_flag()


@dataclass(frozen=True)
class Caller:
    """The identity behind one request."""
    user_id: int | None
    firebase_uid: str | None
    authenticated: bool

    @property
    def is_anonymous(self) -> bool:
        return not self.authenticated


ANONYMOUS = Caller(user_id=None, firebase_uid=None, authenticated=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_firebase_token(token: str) -> str:
    """Return the Firebase uid for a valid ID token, or raise 401.

    Imported lazily: the whole module has to stay importable on a machine with
    no ``serviceAccountKey.json``, which is how the test suite and every AI
    developer runs it.
    """
    from firebase_admin import auth as firebase_auth

    try:
        return firebase_auth.verify_id_token(token)["uid"]
    except Exception as exc:                                   # noqa: BLE001
        # Expired, malformed, wrong project, or Firebase never initialised —
        # all of them mean the same thing to the caller, and spelling out which
        # only helps someone probing.
        logger.info("ID token rejected: %s", exc)
        raise _unauthorized("invalid or expired ID token") from exc


async def current_caller(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Caller:
    """FastAPI dependency: resolve the bearer token to an internal user."""
    if not AUTH_ENABLED:
        return ANONYMOUS

    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("missing bearer token")

    uid = verify_firebase_token(authorization.split(" ", 1)[1].strip())
    user_id = await crud.get_user_id_by_firebase_uid(db, uid)
    if user_id is None:
        # A valid Firebase account with no row here has signed in but never
        # registered; that is a real state and the app knows how to fix it.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="signed in but not registered — call /api/register first",
        )
    return Caller(user_id=user_id, firebase_uid=uid, authenticated=True)


async def verified_uid(
    authorization: str | None = Header(default=None),
) -> str | None:
    """The caller's Firebase uid, or None when auth is off.

    For ``/api/register`` only: that endpoint is how a signed-in account gets a
    ``users`` row, so it cannot demand one already exist the way
    ``current_caller`` does.
    """
    if not AUTH_ENABLED:
        return None
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("missing bearer token")
    return verify_firebase_token(authorization.split(" ", 1)[1].strip())


async def assert_may_access_patient(
    db: AsyncSession, caller: Caller, patient_id: int,
) -> None:
    """403 unless the caller is the patient or that patient's caregiver."""
    if caller.is_anonymous:          # AUTH_ENABLED off — nothing to check
        return
    if caller.user_id == patient_id:
        return
    if await crud.get_caregiver_id(db, patient_id) == caller.user_id:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"not your patient (patient_id {patient_id})",
    )


async def verify_patient_access(
    patient_id: int,
    caller: Caller = Depends(current_caller),
    db: AsyncSession = Depends(get_db),
) -> Caller:
    """Dependency for any route with ``{patient_id}`` in its path.

    FastAPI fills ``patient_id`` from the path parameter the route already
    declares, so guarding a route costs one ``Depends`` and no plumbing.
    """
    await assert_may_access_patient(db, caller, patient_id)
    return caller


def log_startup_state() -> None:
    """Say out loud which mode this process is in — nobody should discover it."""
    if AUTH_ENABLED:
        logger.info("AUTH_ENABLED — Firebase ID token required on patient routes")
    else:
        logger.warning(
            "AUTH_ENABLED is off — every endpoint is open and patient ids are "
            "sequential integers. Acceptable only while the URL is unshared "
            "(plan D5). Set AUTH_ENABLED=true before any real-patient data."
        )
