"""Minimal, dependency-free auth: PBKDF2 password hashes + HMAC-signed bearer tokens."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session as DB

from . import storage
from .db import User, get_db

SECRET = storage.secret_key()
TOKEN_TTL = 60 * 60 * 24 * 30
ITERATIONS = 240_000


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITERATIONS)
    return f"pbkdf2${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
        test = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(test.hex(), dk)
    except ValueError:
        return False


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(payload: dict, ttl: int = TOKEN_TTL) -> str:
    body = _b64(json.dumps({**payload, "exp": int(time.time()) + ttl}).encode())
    sig = _b64(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def unsign(token: str) -> dict:
    try:
        body, sig = token.split(".")
    except ValueError:
        raise HTTPException(401, "Malformed token") from None
    good = _b64(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(good, sig):
        raise HTTPException(401, "Bad token signature")
    data = json.loads(_unb64(body))
    if data.get("exp", 0) < time.time():
        raise HTTPException(401, "Token expired")
    return data


def current_user(authorization: str = Header(default=""), db: DB = Depends(get_db)) -> User:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not signed in")
    data = unsign(authorization.split(" ", 1)[1])
    user = db.get(User, data.get("uid"))
    if not user:
        raise HTTPException(401, "User not found")
    return user
