"""Contributor/reviewer login (phone + PIN), cookie session."""
from __future__ import annotations

import re

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from bson import ObjectId

from app.auth_contributor import (
    AccountRole,
    ContributorIdentity,
    create_contributor_token,
    normalize_account_role,
    require_account,
    require_contributor_secret,
)
from app.config import settings
from app.db import get_database
from app.login_rate_limit import rate_limit_allow, rate_limit_reset
from app.phone_norm import normalize_phone_e164

router = APIRouter(prefix="/api/contributor/auth", tags=["contributor-auth"])
COLLECTION = "contributor_users"


class LoginBody(BaseModel):
    phone: str = Field(..., min_length=3)
    pin: str = Field(..., min_length=1)


class MeResponse(BaseModel):
    user_id: str
    pair_id: str
    phone_e164: str
    role: AccountRole = "contributor"


def _validate_pin_format(pin: str) -> bool:
    d = settings.contributor_pin_min_digits
    u = settings.contributor_pin_max_digits
    if not pin.isdigit() or not (d <= len(pin) <= u):
        return False
    return True


@router.post("/login")
async def contributor_login(body: LoginBody):
    require_contributor_secret()
    phone = normalize_phone_e164(body.phone)
    if not phone:
        raise HTTPException(status_code=400, detail="Invalid phone")
    if not _validate_pin_format(body.pin.strip()):
        raise HTTPException(
            status_code=400,
            detail=f"PIN must be {settings.contributor_pin_min_digits}–{settings.contributor_pin_max_digits} digits",
        )
    if not rate_limit_allow(f"login:{phone}"):
        raise HTTPException(status_code=429, detail="Too many attempts; try later")

    db = get_database()
    doc = await db[COLLECTION].find_one({"phone_e164": phone})
    if not doc or doc.get("disabled"):
        raise HTTPException(status_code=401, detail="Invalid phone or PIN")
    ph = doc.get("pin_hash")
    if isinstance(ph, str):
        ph = ph.encode("utf-8")
    try:
        ok = bcrypt.checkpw(body.pin.strip().encode("utf-8"), ph)
    except ValueError:
        ok = False
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid phone or PIN")

    rate_limit_reset(f"login:{phone}")
    uid = str(doc["_id"])
    pair_id = str(doc.get("language_pair_id") or "")
    if not pair_id or not re.match(r"^[a-f\d]{24}$", pair_id, re.I):
        raise HTTPException(status_code=503, detail="Contributor has no language pair assigned")
    role = normalize_account_role(doc.get("role"))
    token = create_contributor_token(uid, pair_id, role=role)
    max_age = max(60, int(settings.contributor_token_expire_hours * 3600))
    resp = JSONResponse(
        content={
            "user_id": uid,
            "pair_id": pair_id,
            "phone_e164": phone,
            "role": role,
        }
    )
    resp.set_cookie(
        key=settings.contributor_cookie_name,
        value=token,
        httponly=True,
        max_age=max_age,
        samesite=settings.contributor_cookie_samesite,  # type: ignore[arg-type]
        secure=settings.contributor_cookie_secure,
        path="/",
    )
    return resp


@router.post("/logout")
async def contributor_logout():
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(
        key=settings.contributor_cookie_name,
        path="/",
        samesite=settings.contributor_cookie_samesite,  # type: ignore[arg-type]
        secure=settings.contributor_cookie_secure,
    )
    return resp


@router.get("/me", response_model=MeResponse)
async def contributor_me(identity: ContributorIdentity = Depends(require_account)):
    db = get_database()
    doc = await db[COLLECTION].find_one({"_id": ObjectId(identity.sub)})
    if not doc:
        raise HTTPException(status_code=401, detail="User not found")
    return MeResponse(
        user_id=str(doc["_id"]),
        pair_id=str(doc.get("language_pair_id")),
        phone_e164=str(doc.get("phone_e164", "")),
        role=normalize_account_role(doc.get("role")),
    )
