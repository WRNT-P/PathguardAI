"""Create a caregiver who can actually sign in — Firebase account and users row.

**Why a script and not the register endpoint.** ``POST /api/register`` takes a
``firebase_uid`` the app has already obtained by signing in. That is the right
shape for the app and useless here: on 2026-08-30 the caregiver side of the
Flutter app had no Firebase sign-in at all, so nobody could obtain a uid, so
``users`` held four seed patients and **not one caregiver** — and the app was
sending a hardcoded ``caregiver_id`` that belonged to a patient.

So this goes the other way round, the same direction ``POST /api/patients``
goes for a patient: create the Firebase account first with the Admin SDK, then
write the row against the uid it produced. The caregiver signs in with the email
and password afterwards and lands on the same ``users.id`` — no second account,
no orphan row.

Idempotent in both halves: re-running finds the existing Firebase user and the
existing row rather than making another.

**The password is printed to this terminal and written nowhere.** This repo is
public. Send it over chat, not in a file.

    python -m scripts.create_caregiver --email caregiver1@gmail.test --name "ผู้ดูแล"

Pass ``--api-key`` (the Firebase Web API key, which is in the app's
``firebase_options.dart`` and is not a secret) to also *prove* the account
works, by signing in the way the phone will:

    python -m scripts.create_caregiver --email ... --name ... --api-key AIza...

That last step is the one worth running. The Admin SDK will happily create a
user even when Email/Password is disabled in the Firebase console; the failure
only shows up on the phone, as ``operation-not-allowed``, on the day somebody
is standing there trying to log in.
"""
import argparse
import asyncio
import secrets
import string
import sys
import urllib.error
import urllib.request
import json

import firebase_admin
from firebase_admin import auth as fb_auth

from app.db.database import AsyncSessionLocal, init_firebase
from app.db import crud

_SIGN_IN_URL = ("https://identitytoolkit.googleapis.com/v1/"
                "accounts:signInWithPassword?key=")


def _make_password() -> str:
    # Readable enough to be typed off a chat message onto a phone keyboard, and
    # long enough that being in a chat log is not the weak point.
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(16))


def _firebase_user(email: str, password: str) -> tuple[str, bool]:
    """Return (uid, created). Reuses an existing account for this email."""
    try:
        existing = fb_auth.get_user_by_email(email)
        return existing.uid, False
    except fb_auth.UserNotFoundError:
        user = fb_auth.create_user(email=email, password=password,
                                   display_name=email.split("@")[0])
        return user.uid, True
    except fb_auth.ConfigurationNotFoundError:
        # Firebase Authentication has never been switched on for this project.
        # Worth its own message because the raw error says only
        # "No auth provider found for the given identifier", which reads like a
        # problem with the email address rather than with the whole service —
        # and because **the patient pairing flow is broken by the same thing**:
        # POST /api/pair mints its custom token offline and returns 200, and the
        # app's signInWithCustomToken is what actually fails. So this looks like
        # an app bug from the app side and like a working endpoint from ours.
        for line in (
            "FAIL  Firebase Authentication is not enabled on this project.",
            "      Console -> Authentication -> Get started, then enable the",
            "      Email/Password provider. Nothing here can turn it on.",
            "      Until it is on, signInWithCustomToken fails too, so device",
            "      pairing cannot work no matter what this backend returns.",
        ):
            print(line)
        raise SystemExit(1)


def _verify_sign_in(api_key: str, email: str, password: str) -> str | None:
    """Sign in exactly as the phone does. Returns an error string, or None."""
    payload = json.dumps({"email": email, "password": password,
                          "returnSecureToken": True}).encode()
    req = urllib.request.Request(_SIGN_IN_URL + api_key, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read())
        return None if body.get("idToken") else "no idToken in the response"
    except urllib.error.HTTPError as exc:
        detail = json.loads(exc.read()).get("error", {}).get("message", "?")
        return detail
    except Exception as exc:                                # pragma: no cover
        return f"{type(exc).__name__}: {exc}"


async def main() -> int:
    # Same cp1252 trap scripts/delete_patient.py fell into: this file's help
    # text and its --name argument are Thai, and a Windows console kills the
    # process on the first print rather than on anything it did.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):          # not a real console
            pass

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", required=True, help="ชื่อที่ผู้ป่วยจะเห็นบนหน้าจอ SOS")
    ap.add_argument("--phone", default=None, help="เบอร์ที่ปุ่มโทรจะกด")
    ap.add_argument("--password", default=None,
                    help="ตั้งเอง (ค่าเริ่มต้น: สุ่มให้ แล้วพิมพ์ออกจอ)")
    ap.add_argument("--api-key", default=None,
                    help="Firebase Web API key — ใส่เพื่อทดสอบ sign-in จริง")
    args = ap.parse_args()

    password = args.password or _make_password()

    init_firebase()
    uid, created = _firebase_user(args.email, password)
    print(f"{'created' if created else 'reused '} Firebase user  uid={uid}")
    if not created and args.password is None:
        # The account already existed, so the password we just generated is not
        # its password. Saying otherwise would send somebody a string that
        # cannot work.
        password = None

    async with AsyncSessionLocal() as db:
        user_id = await crud.get_user_id_by_firebase_uid(db, uid)
        if user_id is None:
            user = await crud.create_user(
                db, firebase_uid=uid, name=args.name, role="caregiver",
                phone=args.phone)
            await db.commit()
            user_id, verb = user.id, "created"
        else:
            verb = "reused "
        print(f"{verb} users row        id={user_id}  role=caregiver")

    if args.api_key:
        if password is None:
            print("SKIP    sign-in check   (account pre-existed, password unknown)")
        else:
            err = _verify_sign_in(args.api_key, args.email, password)
            if err:
                print(f"FAIL    sign-in check   {err}")
                if err == "OPERATION_NOT_ALLOWED":
                    print("        → enable Email/Password in Firebase console:")
                    print("          Authentication → Sign-in method → Email/Password")
                return 1
            print("OK      sign-in check   the phone can log in with this")

    print()
    print(f"  caregiver_id  {user_id}      ← this replaces the hardcoded id in the app")
    print(f"  email         {args.email}")
    print(f"  password      {password if password else '(unchanged, not shown)'}")
    print()
    print("  Send the password over chat. Do NOT put it in a file in this repo —")
    print("  the repo is public.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
