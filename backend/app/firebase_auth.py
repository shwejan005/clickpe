"""Firebase Admin SDK helpers for authenticating dashboard sessions."""

from __future__ import annotations

import json
import os
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import auth, credentials

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "").strip()
SESSION_COOKIE_NAME = "clickpe_session"
SESSION_DURATION = timedelta(days=5)


@lru_cache(maxsize=1)
def firebase_app() -> firebase_admin.App:
    if not FIREBASE_PROJECT_ID:
        raise RuntimeError("FIREBASE_PROJECT_ID is not configured")
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    credential_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    credential_file = os.getenv("FIREBASE_SERVICE_ACCOUNT_FILE", "").strip()
    if credential_json:
        credential = credentials.Certificate(json.loads(credential_json))
    elif credential_file and Path(credential_file).is_file():
        credential = credentials.Certificate(credential_file)
    else:
        raise RuntimeError("Firebase Admin credentials are not configured")
    return firebase_admin.initialize_app(credential, {"projectId": FIREBASE_PROJECT_ID})


def verify_client_id_token(id_token: str) -> dict[str, Any]:
    decoded = auth.verify_id_token(id_token, app=firebase_app(), check_revoked=True)
    if decoded.get("aud") != FIREBASE_PROJECT_ID or decoded.get("iss") != f"https://securetoken.google.com/{FIREBASE_PROJECT_ID}":
        raise ValueError("Firebase token was issued for a different project")
    return decoded


def create_session_cookie(id_token: str) -> str:
    return auth.create_session_cookie(id_token, expires_in=SESSION_DURATION, app=firebase_app())


def verify_session_cookie(cookie: str) -> dict[str, Any]:
    return auth.verify_session_cookie(cookie, app=firebase_app(), check_revoked=True)


def firebase_auth_ready() -> bool:
    try:
        firebase_app()
        return True
    except Exception:
        return False
