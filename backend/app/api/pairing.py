# pathguard/backend/app/api/pairing.py
"""Getting the patient's phone signed in without asking the patient to sign up.

Everything else in this backend assumes the caller already has a Firebase
account: ``/api/register`` takes a uid the app has just obtained, and
``services/auth.py`` turns a bearer token into ``users.id``. That assumption
holds for a caregiver. It does not hold for the person the app is *for* — asking
someone with dementia to hold an email address and a password is asking them to
do the one thing the illness takes away first.

So the direction is reversed here. The caregiver creates the patient, **the
server picks that patient's Firebase uid before the patient's phone has ever
been touched**, and hands back a short code. The phone trades the code for a
Firebase custom token, signs in with it, and from that moment is an ordinary
authenticated client. ``services/auth.py`` gains no second code path and its
tests keep covering the only one that exists.

Why the server invents the uid rather than making ``users.firebase_uid``
nullable: a nullable identity column would have to be checked at every place
that resolves a caller, forever, for a state that lasts minutes. Choosing the
uid up front keeps the column ``NOT NULL UNIQUE`` and confines the whole
question to this file. ``create_custom_token`` will mint a token for a uid that
has no Firebase account yet; signing in with it is what creates the account.

**This endpoint pair is why ``AUTH_ENABLED`` can be turned on at all.** Without
it, flipping the flag would 403 the patient's device on every ``POST /api/gps``
— which is not a login problem, it is the whole data path going dark.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.database import get_db
from app.services.auth import Caller, current_caller, verify_patient_access

logger = logging.getLogger(__name__)

router = APIRouter()

# No 0/O/1/I/L/U — a caregiver reads this off one screen and types it into
# another, and the pairs above are the ones people transcribe wrongly. U is out
# because it turns into V in handwriting often enough to matter.
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LENGTH = 8
_CODE_TTL = timedelta(hours=24)

# Distinct codes attempted before giving up. Collisions are vanishing at 30^8,
# so more than a couple of retries means something is wrong with the RNG rather
# than that we were unlucky.
_MAX_CODE_ATTEMPTS = 5

# One message for "no such code", "expired" and "already used" alike. Which of
# the three it was goes to the log, not to the caller — telling an outsider that
# a code exists but has expired confirms that the code exists.
_BAD_CODE = "invalid, expired, or already-used pairing code"


def generate_code() -> str:
    """A fresh code in storage form (no separator, upper case)."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


def normalise(code: str) -> str:
    """Storage form of whatever the caregiver typed.

    People add the separator they saw on screen, use lower case, or paste a
    trailing space. None of those should be a failed pairing.
    """
    return "".join(ch for ch in code.upper() if ch.isalnum())


def format_code(code: str) -> str:
    """Display form — grouped in fours, which is how it gets read aloud."""
    return f"{code[:4]}-{code[4:]}" if len(code) == _CODE_LENGTH else code


def _as_utc(when: datetime) -> datetime:
    """Timestamps come back naive from SQLite and aware from Postgres.

    Comparing the two raises, so every expiry check has to go through here. It
    was written inline in ``/api/pair`` and copied by the invite check below;
    one function means the tests cannot pass on SQLite while a comparison
    against Neon behaves differently.
    """
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when


def _mint_custom_token(firebase_uid: str) -> str:
    """Firebase custom token for a uid that need not exist yet.

    Imported inside the function for the same reason ``auth.py`` does it: the
    module must import cleanly in a test run where Firebase was never
    initialised.
    """
    from firebase_admin import auth as firebase_auth

    token = firebase_auth.create_custom_token(firebase_uid)
    return token.decode() if isinstance(token, bytes) else token


class PatientIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255,
                      description="ชื่อผู้ป่วยที่ผู้ดูแลกรอก")
    severity_level: int | None = Field(
        None, ge=1, le=2,
        description="1 = ระยะต้น, 2 = ระยะกลาง (ตามรายงาน)")
    caregiver_id: int | None = Field(
        None,
        description="ส่งเมื่อ AUTH_ENABLED=false เท่านั้น; เปิด auth แล้วยึดจาก token")


class PatientOut(BaseModel):
    patient_id: int
    name: str
    severity_level: int | None
    caregiver_id: int
    pairing_code: str = Field(..., description="รูปแบบที่ผู้ดูแลอ่าน เช่น 7K2M-P9QX")
    expires_at: datetime


class PairIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)


class PairOut(BaseModel):
    patient_id: int
    firebase_custom_token: str = Field(
        ..., description="ส่งเข้า signInWithCustomToken() แล้วใช้ ID token ที่ได้ต่อ")
    # The phone builds a different interface for level 1 and level 2, and pairing
    # is the one moment it is guaranteed to talk to us — after this it re-signs-in
    # from a cached token and never calls /api/pair again. Sending the stage here
    # means the very first screen it draws is the right one.
    severity_level: int | None = Field(
        None, description="1 = ระยะต้น, 2 = ระยะกลาง; null ถ้าผู้ดูแลไม่ได้ระบุ")


class PatientProfileOut(BaseModel):
    patient_id: int
    name: str
    severity_level: int | None


async def _issue_code(db: AsyncSession, patient_id: int) -> tuple[str, datetime]:
    expires_at = datetime.now(timezone.utc) + _CODE_TTL
    for _ in range(_MAX_CODE_ATTEMPTS):
        code = generate_code()
        if await crud.get_pairing_code(db, code) is None:
            await crud.create_pairing_code(db, code, patient_id, expires_at)
            return code, expires_at
    raise HTTPException(                                   # pragma: no cover
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="could not allocate a unique pairing code",
    )


@router.post(
    "/api/patients",
    response_model=PatientOut,
    status_code=status.HTTP_201_CREATED,
    summary="ผู้ดูแลสร้างผู้ป่วยใหม่ และรับรหัสจับคู่เครื่อง",
)
async def create_patient(
    payload: PatientIn,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(current_caller),
) -> PatientOut:
    # With auth on the caregiver is whoever holds the token. A body field would
    # let any signed-in account attach a patient to somebody else's account and
    # then read that patient's live position for the life of the row.
    if caller.authenticated:
        caregiver_id = caller.user_id
    elif payload.caregiver_id is not None:
        caregiver_id = payload.caregiver_id
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="caregiver_id is required while AUTH_ENABLED is false",
        )

    caregiver = await crud.get_user(db, caregiver_id)
    if caregiver is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"caregiver {caregiver_id} not found")
    # Existing was not enough. Until 2026-08-30 any users.id passed here was
    # accepted, so a patient's id worked and the new patient was linked to a
    # person who has dementia rather than to a caregiver — a 201 and a pairing
    # code, with nobody who can be alerted at the other end. Found because the
    # app side had to hardcode an id while their caregiver login was unbuilt,
    # and picked one that belongs to a seed patient.
    #
    # With AUTH_ENABLED on this is also an authorization check: caregiver_id is
    # then the caller's own id, so it stops a paired patient device from
    # creating patients under itself.
    if caregiver.role != "caregiver":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"user {caregiver_id} has role '{caregiver.role}', "
                   "not 'caregiver'",
        )

    # The uid the patient's Firebase account will be created with when the phone
    # redeems the code. Random rather than derived from users.id so it carries no
    # information and cannot be guessed from a patient_id seen elsewhere.
    firebase_uid = f"pathguard:{secrets.token_hex(16)}"
    patient = await crud.create_user(
        db,
        firebase_uid=firebase_uid,
        name=payload.name,
        role="patient",
        caregiver_id=caregiver_id,
        severity_level=payload.severity_level,
    )
    code, expires_at = await _issue_code(db, patient.id)
    logger.info("patient %s created by caregiver %s, pairing code issued",
                patient.id, caregiver_id)
    return PatientOut(
        patient_id=patient.id,
        name=patient.name,
        severity_level=patient.severity_level,
        caregiver_id=caregiver_id,
        pairing_code=format_code(code),
        expires_at=expires_at,
    )


@router.post(
    "/api/pair",
    response_model=PairOut,
    summary="เครื่องผู้ป่วยแลกรหัสจับคู่เป็น Firebase custom token",
)
async def pair_device(
    payload: PairIn,
    db: AsyncSession = Depends(get_db),
) -> PairOut:
    """Unauthenticated by design — the code *is* the credential, once.

    It is the only route in the system that has to work before the caller holds
    any token, which is exactly why it is single-use and short-lived.
    """
    code = normalise(payload.code)
    row = await crud.get_pairing_code(db, code)

    if row is None:
        logger.info("pairing rejected: no such code")
        raise HTTPException(status.HTTP_404_NOT_FOUND, _BAD_CODE)
    if row.used_at is not None:
        logger.info("pairing rejected: code for patient %s already used at %s",
                    row.patient_id, row.used_at)
        raise HTTPException(status.HTTP_404_NOT_FOUND, _BAD_CODE)
    # SQLite hands back naive datetimes; normalise to UTC rather than letting a
    # tz-naive row raise TypeError and turn a pairing attempt into a 500.
    expires_at = _as_utc(row.expires_at)
    if expires_at <= datetime.now(timezone.utc):
        logger.info("pairing rejected: code for patient %s expired at %s",
                    row.patient_id, expires_at)
        raise HTTPException(status.HTTP_404_NOT_FOUND, _BAD_CODE)

    patient = await crud.get_user(db, row.patient_id)
    if patient is None:                                    # pragma: no cover
        logger.warning("pairing code %s points at missing patient %s",
                       row.id, row.patient_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, _BAD_CODE)

    token = _mint_custom_token(patient.firebase_uid)
    # Spent only once the token exists. Minting can fail (Firebase unreachable,
    # credentials wrong); burning the code first would leave the caregiver with a
    # patient they can never pair and nothing to explain why.
    await crud.mark_pairing_code_used(db, row)
    logger.info("device paired to patient %s", patient.id)
    return PairOut(patient_id=patient.id, firebase_custom_token=token,
                   severity_level=patient.severity_level)


@router.get(
    "/api/patients/{patient_id}",
    response_model=PatientProfileOut,
    summary="ใครคือผู้ป่วยรายนี้ และอยู่ระยะไหน",
)
async def get_patient(
    patient_id: int,
    db: AsyncSession = Depends(get_db),
    _: Caller = Depends(verify_patient_access),
) -> PatientProfileOut:
    """Read back the two facts the caregiver stated when they created the patient.

    Until this existed, ``severity_level`` left the backend through exactly one
    door — the ``POST /api/patients`` response — and that is a *caregiver* call
    made on a *caregiver's* phone. The patient's device gets ``patient_id`` and a
    token out of ``/api/pair``, which it calls once per device ever; from then on
    it re-signs-in from a cached token and never speaks to either endpoint again.
    So the phone the app is actually for could never learn its own stage, and the
    level 1 / level 2 interfaces built on top of that had nothing to switch on.

    ``PairOut`` now carries the stage too, which covers a fresh install. This
    covers everything after it: a reinstall, a stage the caregiver changed, and
    the caregiver's own screens, which had no way to read a patient's name or
    stage back either.
    """
    patient = await crud.get_user(db, patient_id)
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown patient_id {patient_id}",
        )
    return PatientProfileOut(
        patient_id=patient.id,
        name=patient.name,
        severity_level=patient.severity_level,
    )


# ── A second caregiver for the same patient (2026-08-28) ─────────────────────
#
# The report wants an alert to reach every caregiver, ranked by distance. The
# schema can hold them since patient_caregivers landed, but nothing could put a
# second person in it: POST /api/patients links whoever created the patient and
# that was the only writer. This is the door.
#
# It reuses this module's code machinery — the alphabet with the ambiguous
# letters removed, the eight-character length, the one-message-for-three-failures
# rule — because a family reads these codes aloud the same way. It does NOT
# reuse pairing_codes. See the CaregiverInvite docstring: one code space would
# let a code meant to set up the patient's phone be redeemed for a caregiver's
# view of that patient instead.

_BAD_INVITE = "invalid, expired, or already-used invite code"


class CaregiverInviteOut(BaseModel):
    patient_id: int
    invite_code: str
    expires_at: datetime


class RedeemInviteIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    caregiver_id: int | None = Field(
        None,
        description="ต้องส่งเฉพาะตอน AUTH_ENABLED=false; เปิด auth แล้วยึดจาก token")


class RedeemInviteOut(BaseModel):
    patient_id: int
    patient_name: str
    caregiver_id: int
    already_linked: bool


@router.post(
    "/api/patients/{patient_id}/caregiver-invites",
    response_model=CaregiverInviteOut,
    status_code=status.HTTP_201_CREATED,
    summary="ผู้ดูแลออกรหัสเชิญผู้ดูแลอีกคนมาดูผู้ป่วยคนเดียวกัน",
)
async def create_caregiver_invite(
    patient_id: int,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(verify_patient_access),
) -> CaregiverInviteOut:
    """Issue a code that adds whoever redeems it as a caregiver of this patient.

    ``verify_patient_access`` lets the patient through as well, which is right
    for reading their own data and wrong here: a patient with dementia handing
    out access to their own live position is exactly the situation the caregiver
    exists for. So the caller must additionally be one of the caregivers.

    Any caregiver of the patient may invite, not only the primary. Every link
    grants the same access already, so restricting it would stop nothing — a
    caregiver who wanted to could share their own sign-in — while making the
    common case (the person holding the phone is the second son, not the first)
    fail for no gain.
    """
    if caller.authenticated:
        if caller.user_id not in await crud.get_caregiver_ids(db, patient_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="only a caregiver of this patient may invite another",
            )

    expires_at = datetime.now(timezone.utc) + _CODE_TTL
    for _ in range(_MAX_CODE_ATTEMPTS):
        code = generate_code()
        if await crud.get_caregiver_invite(db, code) is None:
            await crud.create_caregiver_invite(
                db, code, patient_id, caller.user_id, expires_at)
            logger.info("caregiver invite for patient %s issued by %s",
                        patient_id, caller.user_id)
            return CaregiverInviteOut(
                patient_id=patient_id,
                invite_code=format_code(code),
                expires_at=expires_at,
            )
    raise HTTPException(                                   # pragma: no cover
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="could not allocate a unique invite code",
    )


@router.post(
    "/api/caregivers/redeem-invite",
    response_model=RedeemInviteOut,
    summary="ผู้ดูแลคนที่ 2 กรอกรหัสเชิญ เพื่อเข้าถึงผู้ป่วยคนนั้น",
)
async def redeem_caregiver_invite(
    payload: RedeemInviteIn,
    db: AsyncSession = Depends(get_db),
    caller: Caller = Depends(current_caller),
) -> RedeemInviteOut:
    """Trade an invite code for a link to that patient.

    Unlike ``POST /api/pair`` this mints no token and creates no account. The
    second caregiver is an ordinary registered user already — they signed in
    with Firebase and called ``/api/register`` like the first one. All that is
    missing is the row saying which patient they may see, and that is all this
    writes.
    """
    if caller.authenticated:
        caregiver_id = caller.user_id
    elif payload.caregiver_id is not None:
        caregiver_id = payload.caregiver_id
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="caregiver_id is required while AUTH_ENABLED is false",
        )

    invite = await crud.get_caregiver_invite(db, normalise(payload.code))
    now = datetime.now(timezone.utc)
    # One message for all three failures, as with pairing codes: telling an
    # outsider that a code exists but has expired confirms that it exists.
    if invite is None:
        reason = "no such code"
    elif invite.used_at is not None:
        reason = "already used"
    elif _as_utc(invite.expires_at) <= now:
        reason = "expired"
    else:
        reason = None
    if reason is not None:
        logger.info("caregiver invite rejected (%s)", reason)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=_BAD_INVITE)

    user = await crud.get_user(db, caregiver_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user {caregiver_id} not found — call /api/register first")
    # A patient device redeeming this would give the patient a caregiver's view
    # of themselves — and, more to the point, of whoever else the code was meant
    # for. The role is the only thing separating the two kinds of account.
    if user.role != "caregiver":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"user {caregiver_id} is a {user.role}, not a caregiver")

    patient = await crud.get_user(db, invite.patient_id)
    link = await crud.link_caregiver(db, invite.patient_id, caregiver_id)
    # Spent either way. A code that stays live because the holder was already
    # linked is a code that can be passed on to somebody who is not.
    await crud.mark_caregiver_invite_used(db, invite, now)
    logger.info("caregiver %s linked to patient %s by invite",
                caregiver_id, invite.patient_id)

    return RedeemInviteOut(
        patient_id=invite.patient_id,
        patient_name=patient.name,
        caregiver_id=caregiver_id,
        already_linked=link is None,
    )
