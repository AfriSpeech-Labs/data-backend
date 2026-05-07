#!/usr/bin/env python3
"""Create an admin user in MongoDB (password stored as bcrypt hash).

Run from repo root with backend venv active and PYTHONPATH including backend:

  cd backend
  venv\\Scripts\\activate   # Windows
  python scripts/create_admin_user.py --email you@example.com --password "your-secret"

Or pass password via env (avoid shell history):

  set ADMIN_PASSWORD=...
  python scripts/create_admin_user.py --email you@example.com
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

# Allow running as `python scripts/create_admin_user.py` from backend/
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

import bcrypt
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings

ADMIN_USERS = "admin_users"


async def main_async() -> int:
    p = argparse.ArgumentParser(description="Create admin user for the admin dashboard")
    p.add_argument("--email", required=True, help="Login email (stored lowercase)")
    p.add_argument(
        "--password",
        default="",
        help="Plain password (omit to use ADMIN_PASSWORD env var)",
    )
    args = p.parse_args()
    password = args.password or os.environ.get("ADMIN_PASSWORD", "")
    if not password:
        print("Error: provide --password or set ADMIN_PASSWORD", file=sys.stderr)
        return 1
    email = args.email.strip().lower()
    if not email:
        print("Error: empty email", file=sys.stderr)
        return 1

    client = AsyncIOMotorClient(settings.mongodb_uri)
    try:
        db = client[settings.database_name]
        await db[ADMIN_USERS].create_index("email", unique=True)
        pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        now = datetime.now(timezone.utc)
        result = await db[ADMIN_USERS].update_one(
            {"email": email},
            {
                "$set": {
                    "email": email,
                    "password_hash": pw_hash,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        if result.upserted_id is not None:
            print(f"Created admin user: {email}")
        else:
            print(f"Updated admin user: {email}")
        return 0
    finally:
        client.close()


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
