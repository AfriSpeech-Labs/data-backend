#!/usr/bin/env python3
"""Download the admin full synthetic dataset ZIP (uncapped) via GET /api/admin/datasets/synthetic/download.

Uses the same auth conventions as import_synth_pairs_csv.py: --cookie or ADMIN_SESSION env.
If --cookie / ADMIN_SESSION is set, it takes precedence over --login-email / --login-password.

Examples:

  python scripts/download_admin_synth_zip.py --url http://127.0.0.1:8000 \\
    --cookie 'admin_session=YOUR_JWT' -o synthetic_approved.zip

  set ADMIN_SESSION=admin_session=YOUR_JWT
  python scripts/download_admin_synth_zip.py --url https://speech-backend-12c1f.up.railway.app

  Or add to backend/.env (not committed): ADMIN_SESSION=... or ADMIN_LOGIN_EMAIL / ADMIN_LOGIN_PASSWORD.

  python scripts/download_admin_synth_zip.py --url http://127.0.0.1:8000 \\
    --login-email admin@example.com --login-password 'secret' -o out.zip

Local JWTs only validate when ADMIN_JWT_SECRET matches and data comes from your local MongoDB.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

# Loaded into os.environ (if not already set) from backend/.env — same keys you can set in the shell.
_DOTENV_KEYS = ("ADMIN_SESSION", "ADMIN_LOGIN_EMAIL", "ADMIN_LOGIN_PASSWORD")


def _merge_env_from_backend_dotenv() -> None:
    """Set ADMIN_* script keys from backend/.env so `python scripts/...` picks them up like the API does."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key not in _DOTENV_KEYS or os.environ.get(key):
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ[key] = val


def _cookie_header(raw: str) -> dict[str, str]:
    c = raw.strip()
    if not c:
        return {}
    if "=" in c:
        return {"Cookie": c}
    return {"Cookie": f"admin_session={c}"}


def main() -> int:
    _merge_env_from_backend_dotenv()

    p = argparse.ArgumentParser(description="Download admin synthetic dataset ZIP")
    p.add_argument("--url", default="http://127.0.0.1:8000", help="API base (no trailing slash)")
    p.add_argument(
        "-o",
        "--out",
        default="synthetic_export.zip",
        help="Output .zip path",
    )
    p.add_argument(
        "--status",
        choices=("all", "pending", "approved", "rejected"),
        default="approved",
        help="validation_status filter (default: approved)",
    )
    p.add_argument("--cookie", default="", help="Cookie header value e.g. admin_session=...")
    p.add_argument(
        "--login-email",
        default="",
        help="If set with password, POST /login first (or env ADMIN_LOGIN_EMAIL)",
    )
    p.add_argument(
        "--login-password",
        default="",
        help="Admin password (or env ADMIN_LOGIN_PASSWORD)",
    )
    p.add_argument("--timeout", type=float, default=3600.0, help="HTTP timeout in seconds (default 3600)")
    args = p.parse_args()

    base = args.url.rstrip("/")
    download_url = f"{base}/api/admin/datasets/synthetic/download"
    cookie_raw = (args.cookie or os.environ.get("ADMIN_SESSION", "")).strip()
    login_email = (args.login_email or os.environ.get("ADMIN_LOGIN_EMAIL", "")).strip()
    login_password = args.login_password or os.environ.get("ADMIN_LOGIN_PASSWORD", "")
    use_login = bool(login_email and login_password)
    if not cookie_raw and not use_login:
        print(
            "No admin credentials. Use one of:\n"
            "  --cookie 'admin_session=...'  or  set ADMIN_SESSION  (PowerShell: $env:ADMIN_SESSION='...')\n"
            "  --login-email + --login-password  or  set ADMIN_LOGIN_EMAIL and ADMIN_LOGIN_PASSWORD\n"
            "Optional: put those keys in backend/.env (this script loads them if not already in the environment).",
            file=sys.stderr,
        )
        return 1
    if cookie_raw and use_login:
        use_login = False

    timeout = httpx.Timeout(args.timeout, connect=60.0)

    with httpx.Client(timeout=timeout) as client:
        headers: dict[str, str] = {}
        if cookie_raw:
            headers = _cookie_header(cookie_raw)
        elif use_login:
            lr = client.post(
                f"{base}/api/admin/auth/login",
                json={"email": login_email, "password": login_password},
            )
            if lr.status_code != 200:
                print(f"Login HTTP {lr.status_code}: {lr.text[:500]}", file=sys.stderr)
                return 1

        with client.stream(
            "GET",
            download_url,
            params={"status": args.status},
            headers=headers or None,
        ) as r:
            if r.status_code != 200:
                body = r.read().decode("utf-8", errors="replace")[:800]
                print(f"HTTP {r.status_code}: {body}", file=sys.stderr)
                return 1
            total = 0
            with open(args.out, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
                    total += len(chunk)

    print(f"wrote {args.out} ({total} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
