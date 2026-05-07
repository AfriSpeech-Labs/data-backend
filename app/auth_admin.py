"""Admin JWT (httpOnly cookie) verification for protected /api/admin/* routes."""
from datetime import datetime, timezone, timedelta

import jwt
from fastapi import Cookie, Depends, HTTPException
from pydantic import BaseModel

from app.config import settings


class AdminIdentity(BaseModel):
    email: str


def _require_secret() -> str:
    secret = (settings.admin_jwt_secret or "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Admin auth is not configured (set ADMIN_JWT_SECRET).",
        )
    return secret


def create_admin_token(email: str) -> str:
    secret = _require_secret()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=max(1, settings.admin_token_expire_hours))
    payload = {
        "sub": email,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_admin_token(token: str) -> AdminIdentity:
    secret = _require_secret()
    try:
        data = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid session")
    email = data.get("sub")
    if not email or not isinstance(email, str):
        raise HTTPException(status_code=401, detail="Invalid session")
    return AdminIdentity(email=email)


async def require_admin(
    admin_session: str | None = Cookie(None, alias=settings.admin_cookie_name),
) -> AdminIdentity:
    if not admin_session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return decode_admin_token(admin_session)
