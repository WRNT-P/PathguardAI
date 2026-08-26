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
from app.services.auth import Caller, current_caller

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
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
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
    return PairOut(patient_id=patient.id, firebase_custom_token=token)
