"""Admin login/logout/me — cookie-based session."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import bcrypt
from app.auth_admin import AdminIdentity, create_admin_token, require_admin
from app.config import settings
from app.db import get_database

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])
ADMIN_USERS = "admin_users"


class LoginBody(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


@router.post("/login")
async def admin_login(body: LoginBody):
    secret = (settings.admin_jwt_secret or "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Admin auth is not configured (set ADMIN_JWT_SECRET).",
        )
    db = get_database()
    doc = await db[ADMIN_USERS].find_one({"email": body.email.lower().strip()})
    if not doc or not doc.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    pw_hash = doc["password_hash"]
    if isinstance(pw_hash, str):
        pw_hash = pw_hash.encode("utf-8")
    try:
        ok = bcrypt.checkpw(body.password.encode("utf-8"), pw_hash)
    except ValueError:
        ok = False
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    email = str(doc["email"])
    token = create_admin_token(email)
    max_age = max(60, int(settings.admin_token_expire_hours * 3600))
    resp = JSONResponse(content={"email": email})
    resp.set_cookie(
        key=settings.admin_cookie_name,
        value=token,
        httponly=True,
        max_age=max_age,
        samesite=settings.admin_cookie_samesite,  # type: ignore[arg-type]
        secure=settings.admin_cookie_secure,
        path="/",
    )
    return resp


@router.post("/logout")
async def admin_logout():
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(
        key=settings.admin_cookie_name,
        path="/",
        samesite=settings.admin_cookie_samesite,  # type: ignore[arg-type]
        secure=settings.admin_cookie_secure,
    )
    return resp


@router.get("/me", response_model=AdminIdentity)
async def admin_me(identity: AdminIdentity = Depends(require_admin)):
    return identity
