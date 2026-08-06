"""Contributor/reviewer JWT (cookie or Bearer) for protected routes."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Literal

import jwt
from fastapi import Cookie, Depends, HTTPException, Header
from pydantic import BaseModel

from app.config import settings

AccountRole = Literal["contributor", "reviewer"]


class ContributorIdentity(BaseModel):
    sub: str  # contributor_users._id hex
    pair_id: str
    role: AccountRole = "contributor"


def _contributor_secret() -> str:
    s = (settings.contributor_jwt_secret or "").strip()
    if s:
        return s
    # Dev fallback: reuse admin secret so one env var works locally.
    return (settings.admin_jwt_secret or "").strip()


def require_contributor_secret() -> str:
    secret = _contributor_secret()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Contributor auth is not configured (set CONTRIBUTOR_JWT_SECRET or ADMIN_JWT_SECRET).",
        )
    return secret


def normalize_account_role(raw: object) -> AccountRole:
    if raw == "reviewer":
        return "reviewer"
    return "contributor"


def create_contributor_token(
    user_id: str,
    pair_id: str,
    role: AccountRole = "contributor",
) -> str:
    secret = require_contributor_secret()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=max(1, settings.contributor_token_expire_hours))
    payload = {
        "sub": user_id,
        "pair_id": pair_id,
        "role": normalize_account_role(role),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_account_token(token: str) -> ContributorIdentity:
    """Decode JWT for contributor or reviewer accounts."""
    secret = require_contributor_secret()
    try:
        data = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid session")
    role_raw = data.get("role", "contributor")
    if role_raw not in ("contributor", "reviewer"):
        raise HTTPException(status_code=401, detail="Invalid session")
    sub = data.get("sub")
    pair_id = data.get("pair_id")
    if not sub or not isinstance(sub, str) or not pair_id or not isinstance(pair_id, str):
        raise HTTPException(status_code=401, detail="Invalid session")
    return ContributorIdentity(
        sub=sub,
        pair_id=pair_id,
        role=normalize_account_role(role_raw),
    )


def decode_contributor_token(token: str) -> ContributorIdentity:
    """Decode JWT and require role=contributor (recording/translation accounts)."""
    identity = decode_account_token(token)
    if identity.role != "contributor":
        raise HTTPException(status_code=403, detail="Contributor role required")
    return identity


def _extract_token(
    authorization: str | None,
    contributor_session: str | None,
) -> str | None:
    token = contributor_session
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    return token or None


async def require_account(
    authorization: str | None = Header(None),
    contributor_session: str | None = Cookie(None, alias=settings.contributor_cookie_name),
) -> ContributorIdentity:
    """Any logged-in contributor_users account (contributor or reviewer)."""
    token = _extract_token(authorization, contributor_session)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return decode_account_token(token)


async def require_contributor(
    authorization: str | None = Header(None),
    contributor_session: str | None = Cookie(None, alias=settings.contributor_cookie_name),
) -> ContributorIdentity:
    token = _extract_token(authorization, contributor_session)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return decode_contributor_token(token)


async def require_reviewer(
    authorization: str | None = Header(None),
    contributor_session: str | None = Cookie(None, alias=settings.contributor_cookie_name),
) -> ContributorIdentity:
    token = _extract_token(authorization, contributor_session)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    identity = decode_account_token(token)
    if identity.role != "reviewer":
        raise HTTPException(status_code=403, detail="Reviewer role required")
    return identity


async def optional_contributor(
    authorization: str | None = Header(None),
    contributor_session: str | None = Cookie(None, alias=settings.contributor_cookie_name),
) -> ContributorIdentity | None:
    """Return contributor identity only; reviewer cookies are ignored (anonymous)."""
    token = _extract_token(authorization, contributor_session)
    if not token:
        return None
    try:
        return decode_contributor_token(token)
    except HTTPException:
        return None


def assert_contributor_doc_pair(
    doc: dict,
    contrib: ContributorIdentity | None,
) -> None:
    """If logged in as a contributor, the speech item must belong to their assigned pair."""
    if contrib is None:
        return
    raw = doc.get("pair_id")
    if raw is None:
        raise HTTPException(
            status_code=403,
            detail="This clip is not available for your account",
        )
    if str(raw) != contrib.pair_id:
        raise HTTPException(
            status_code=403,
            detail="This clip belongs to a different language pair than your assignment",
        )
