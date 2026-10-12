"""Admin CRUD for contributor users (phone + PIN)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import bcrypt
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth_admin import AdminIdentity, require_admin
from app.auth_contributor import normalize_account_role
from app.config import settings
from app.db import get_database
from app.phone_norm import normalize_phone_e164
from app.services.demographics import (
    age_category_for_range,
    normalize_age_range,
    normalize_gender,
)
from app.services.language_pairs import get_pair_by_id

router = APIRouter(prefix="/api/admin/contributors", tags=["admin-contributors"])
COLLECTION = "contributor_users"

AccountRoleLiteral = Literal["contributor", "reviewer"]
GenderLiteral = Literal["male", "female", "other", "prefer_not_to_say"]
AgeRangeLiteral = Literal[
    "13-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+", "prefer_not_to_say"
]


class ContributorCreate(BaseModel):
    phone: str = Field(..., min_length=5)
    pin: str = Field(..., min_length=4)
    language_pair_id: str = Field(..., min_length=24, max_length=24)
    role: AccountRoleLiteral = "contributor"
    # Demographics (admin-set, optional). age_category is derived server-side.
    gender: GenderLiteral | None = None
    age_range: AgeRangeLiteral | None = None
    display_name: str | None = None
    guardian_consent: bool | None = None


class ContributorOut(BaseModel):
    id: str
    phone_e164: str
    language_pair_id: str
    disabled: bool
    role: AccountRoleLiteral = "contributor"
    gender: str | None = None
    age_range: str | None = None
    age_category: str | None = None
    display_name: str | None = None
    guardian_consent: bool | None = None


class PinResetBody(BaseModel):
    pin: str = Field(..., min_length=4)


class ContributorPatch(BaseModel):
    language_pair_id: str = Field(..., min_length=24, max_length=24)
    role: AccountRoleLiteral | None = None
    gender: GenderLiteral | None = None
    age_range: AgeRangeLiteral | None = None
    display_name: str | None = None
    guardian_consent: bool | None = None


def _demographic_fields(
    gender: object,
    age_range: object,
    display_name: object,
    guardian_consent: object,
) -> dict:
    """Build the demographic subset of a doc update; derives age_category."""
    out: dict = {}
    g = normalize_gender(gender)
    if g is not None:
        out["gender"] = g
    ar = normalize_age_range(age_range)
    if ar is not None:
        out["age_range"] = ar
        out["age_category"] = age_category_for_range(ar)
    if isinstance(display_name, str):
        out["display_name"] = display_name.strip() or None
    if isinstance(guardian_consent, bool):
        out["guardian_consent"] = guardian_consent
    return out


class ContributorKpiOut(BaseModel):
    id: str
    phone_e164: str
    language_pair_id: str
    pair_slug: str | None = None
    pair_display_name: str | None = None
    submissions_count: int = 0
    batches_completed_count: int = 0
    target_audio_seconds: float = 0.0


def _pin_ok(pin: str) -> bool:
    if not pin.isdigit():
        return False
    d, u = settings.contributor_pin_min_digits, settings.contributor_pin_max_digits
    return d <= len(pin) <= u


def _hash_pin(pin: str) -> bytes:
    return bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt())


def _out(doc: dict) -> ContributorOut:
    age_range = doc.get("age_range")
    return ContributorOut(
        id=str(doc["_id"]),
        phone_e164=str(doc.get("phone_e164", "")),
        language_pair_id=str(doc.get("language_pair_id", "")),
        disabled=bool(doc.get("disabled")),
        role=normalize_account_role(doc.get("role")),
        gender=doc.get("gender"),
        age_range=age_range,
        # Prefer stored category; derive if missing (legacy docs).
        age_category=doc.get("age_category") or age_category_for_range(age_range),
        display_name=doc.get("display_name"),
        guardian_consent=doc.get("guardian_consent"),
    )


@router.get("/kpi", response_model=list[ContributorKpiOut])
async def list_contributor_kpis(
    pair_id: str | None = Query(None, description="Optional ObjectId; scope KPIs to this pair"),
    _: AdminIdentity = Depends(require_admin),
):
    """Aggregated per-contributor metrics (submissions, batch completions, target audio sum)."""
    db = get_database()
    pair_oid: ObjectId | None = None
    if pair_id and pair_id.strip():
        try:
            pair_oid = ObjectId(pair_id.strip())
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid pair_id") from None

    sp_match: dict = {"submitted_by_user_id": {"$exists": True, "$ne": None}}
    if pair_oid is not None:
        sp_match["pair_id"] = pair_oid

    subs_by_user: dict[str, tuple[int, float]] = {}
    pipeline_sp = [
        {"$match": sp_match},
        {
            "$group": {
                "_id": "$submitted_by_user_id",
                "submissions_count": {"$sum": 1},
                "target_audio_seconds": {"$sum": {"$ifNull": ["$length_swahili", 0]}},
            }
        },
    ]
    async for row in db["speech_parallel"].aggregate(pipeline_sp):
        uid = row.get("_id")
        if uid is None:
            continue
        subs_by_user[str(uid)] = (
            int(row.get("submissions_count") or 0),
            float(row.get("target_audio_seconds") or 0),
        )

    bc_match: dict = {"actor_ref": {"$regex": r"^u:"}}
    if pair_oid is not None:
        bc_match["pair_id"] = pair_oid
    batches_by_user: dict[str, int] = {}
    async for row in db["batch_completions"].aggregate([
        {"$match": bc_match},
        {"$group": {"_id": "$actor_ref", "n": {"$sum": 1}}},
    ]):
        ref = row.get("_id")
        if not ref or not str(ref).startswith("u:"):
            continue
        uid_str = str(ref)[2:]
        batches_by_user[uid_str] = int(row.get("n") or 0)

    pairs_cache: dict[str, dict] = {}

    async def _pair_meta(pid: ObjectId) -> tuple[str | None, str | None]:
        key = str(pid)
        if key in pairs_cache:
            d = pairs_cache[key]
            return d.get("slug"), d.get("display_name")
        doc = await db["language_pairs"].find_one({"_id": pid})
        if not doc:
            pairs_cache[key] = {}
            return None, None
        pairs_cache[key] = {
            "slug": doc.get("slug"),
            "display_name": doc.get("display_name"),
        }
        return pairs_cache[key].get("slug"), pairs_cache[key].get("display_name")

    items: list[ContributorKpiOut] = []
    async for doc in db[COLLECTION].find({}).sort("phone_e164", 1):
        uid = str(doc["_id"])
        sub_n, sub_sec = subs_by_user.get(uid, (0, 0.0))
        batch_n = batches_by_user.get(uid, 0)
        lp = doc.get("language_pair_id")
        slug, disp = (None, None)
        if isinstance(lp, ObjectId):
            slug, disp = await _pair_meta(lp)
        items.append(
            ContributorKpiOut(
                id=uid,
                phone_e164=str(doc.get("phone_e164", "")),
                language_pair_id=str(lp) if lp is not None else "",
                pair_slug=slug,
                pair_display_name=disp,
                submissions_count=sub_n,
                batches_completed_count=batch_n,
                target_audio_seconds=sub_sec,
            )
        )
    return items


@router.patch("/{user_id}", response_model=ContributorOut)
async def patch_contributor(
    user_id: str,
    body: ContributorPatch,
    _: AdminIdentity = Depends(require_admin),
):
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user id") from None
    try:
        pid = ObjectId(body.language_pair_id.strip())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid language_pair_id") from None
    pair = await get_pair_by_id(pid)
    if not pair:
        raise HTTPException(status_code=404, detail="Language pair not found")
    updates: dict = {"language_pair_id": pid}
    if body.role is not None:
        updates["role"] = normalize_account_role(body.role)
    updates.update(
        _demographic_fields(
            body.gender, body.age_range, body.display_name, body.guardian_consent
        )
    )
    db = get_database()
    r = await db[COLLECTION].update_one(
        {"_id": oid},
        {"$set": updates},
    )
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    doc = await db[COLLECTION].find_one({"_id": oid})
    assert doc
    return _out(doc)


@router.get("", response_model=list[ContributorOut])
async def list_contributors(
    role: AccountRoleLiteral | None = Query(
        None, description="Optional filter: contributor or reviewer"
    ),
    _: AdminIdentity = Depends(require_admin),
):
    db = get_database()
    q: dict = {}
    if role == "reviewer":
        q["role"] = "reviewer"
    elif role == "contributor":
        # Include legacy docs with missing role as contributors.
        q["$or"] = [{"role": "contributor"}, {"role": {"$exists": False}}, {"role": None}]
    items: list[ContributorOut] = []
    async for doc in db[COLLECTION].find(q).sort("phone_e164", 1):
        items.append(_out(doc))
    return items


@router.post("", response_model=ContributorOut)
async def create_contributor(body: ContributorCreate, admin: AdminIdentity = Depends(require_admin)):
    phone = normalize_phone_e164(body.phone)
    if not phone:
        raise HTTPException(status_code=400, detail="Invalid phone")
    if not _pin_ok(body.pin.strip()):
        raise HTTPException(
            status_code=400,
            detail=f"PIN must be {settings.contributor_pin_min_digits}–{settings.contributor_pin_max_digits} digits",
        )
    try:
        pid = ObjectId(body.language_pair_id.strip())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid language_pair_id") from None
    pair = await get_pair_by_id(pid)
    if not pair:
        raise HTTPException(status_code=404, detail="Language pair not found")

    account_role = normalize_account_role(body.role)
    db = get_database()
    if await db[COLLECTION].find_one({"phone_e164": phone}):
        raise HTTPException(status_code=409, detail="Phone already registered")

    doc = {
        "phone_e164": phone,
        "pin_hash": _hash_pin(body.pin.strip()),
        "language_pair_id": pid,
        "role": account_role,
        "created_by_admin_email": admin.email,
        "disabled": False,
        "created_at": datetime.now(timezone.utc),
        "pin_updated_at": datetime.now(timezone.utc),
        **_demographic_fields(
            body.gender, body.age_range, body.display_name, body.guardian_consent
        ),
    }
    res = await db[COLLECTION].insert_one(doc)
    doc["_id"] = res.inserted_id
    return _out(doc)


@router.post("/{user_id}/reset-pin")
async def reset_pin(
    user_id: str,
    body: PinResetBody,
    _: AdminIdentity = Depends(require_admin),
):
    if not _pin_ok(body.pin.strip()):
        raise HTTPException(
            status_code=400,
            detail=f"PIN must be {settings.contributor_pin_min_digits}–{settings.contributor_pin_max_digits} digits",
        )
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user id") from None
    db = get_database()
    r = await db[COLLECTION].update_one(
        {"_id": oid},
        {
            "$set": {
                "pin_hash": _hash_pin(body.pin.strip()),
                "pin_updated_at": datetime.now(timezone.utc),
            }
        },
    )
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


@router.post("/{user_id}/disable")
async def disable_contributor(user_id: str, _: AdminIdentity = Depends(require_admin)):
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user id") from None
    db = get_database()
    r = await db[COLLECTION].update_one({"_id": oid}, {"$set": {"disabled": True}})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


@router.post("/{user_id}/enable")
async def enable_contributor(user_id: str, _: AdminIdentity = Depends(require_admin)):
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user id") from None
    db = get_database()
    r = await db[COLLECTION].update_one({"_id": oid}, {"$set": {"disabled": False}})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}
