"""Protect /docs and /redoc with a 6-digit OTP. Code is logged at startup."""
import hashlib
import hmac
import logging
import secrets
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.config import settings

logger = logging.getLogger(__name__)

DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}
COOKIE_NAME = "docs_access"

_current_otp: str = ""
_otp_expires_at: float = 0.0


def init_docs_otp() -> None:
    """Generate 6-digit OTP and print it so it appears in deploy logs. Call at startup when docs_otp_enabled."""
    global _current_otp, _otp_expires_at
    if not settings.docs_otp_enabled:
        return
    if not settings.docs_otp_secret:
        print("Docs OTP: SKIPPED - set DOCS_OTP_SECRET in your environment to enable docs protection.")
        logger.warning("Docs OTP enabled but DOCS_OTP_SECRET is not set; docs remain public.")
        return
    ttl_sec = settings.docs_otp_ttl_minutes * 60
    _current_otp = "".join(secrets.choice("0123456789") for _ in range(6))
    _otp_expires_at = time.time() + ttl_sec
    msg = f"Docs OTP: {_current_otp} (valid for {settings.docs_otp_ttl_minutes} min)"
    print(msg)
    logger.info("%s", msg)


def _is_otp_valid(otp: str) -> bool:
    if not otp or not _current_otp:
        return False
    if time.time() > _otp_expires_at:
        return False
    return secrets.compare_digest(otp.strip(), _current_otp)


def _make_cookie_value() -> tuple[str, int]:
    ttl_sec = settings.docs_otp_ttl_minutes * 60
    expiry_ts = int(time.time()) + ttl_sec
    payload = str(expiry_ts)
    sig = hmac.new(
        settings.docs_otp_secret.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{sig}", ttl_sec


def _is_cookie_valid(value: str) -> bool:
    if not value or "." not in value:
        return False
    parts = value.split(".", 1)
    try:
        expiry_ts = int(parts[0])
    except ValueError:
        return False
    if time.time() > expiry_ts:
        return False
    payload = parts[0]
    sig = hmac.new(
        settings.docs_otp_secret.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(parts[1], sig)


GATE_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Enter OTP</title></head>
<body style="font-family:sans-serif;max-width:320px;margin:80px auto;padding:20px;">
  <h2>Enter OTP</h2>
  <p>Enter the 6-digit code to access the API docs.</p>
  <form method="post" action="/docs-otp-verify" style="display:flex;flex-direction:column;gap:12px;">
    <input type="text" name="otp" placeholder="000000" maxlength="6" pattern="[0-9]{6}" autocomplete="one-time-code" style="padding:10px;font-size:16px;" required />
    <input type="hidden" name="next_url" value="/docs" />
    <button type="submit" style="padding:10px;">Submit</button>
  </form>
  <p style="color:#666;font-size:14px;">Get the code from the server logs when the app starts.</p>
</body>
</html>
"""


class DocsOTPMiddleware(BaseHTTPMiddleware):
    """When docs_otp_enabled, require valid OTP or cookie for /docs, /redoc, /openapi.json."""

    async def dispatch(self, request: Request, call_next):
        if not settings.docs_otp_enabled or not settings.docs_otp_secret:
            return await call_next(request)

        path = request.scope.get("path", "")
        if path not in DOCS_PATHS:
            return await call_next(request)

        cookie_value = request.cookies.get(COOKIE_NAME)
        if cookie_value and _is_cookie_valid(cookie_value):
            return await call_next(request)

        otp_param = request.query_params.get("otp")
        if otp_param and _is_otp_valid(otp_param):
            value, max_age = _make_cookie_value()
            response = RedirectResponse(url=path, status_code=302)
            response.set_cookie(
                COOKIE_NAME,
                value,
                max_age=max_age,
                path="/",
                httponly=True,
                samesite="lax",
            )
            return response

        if path == "/openapi.json":
            return Response(status_code=403, content='{"detail":"OTP required"}', media_type="application/json")

        html = GATE_HTML.replace('name="next_url" value="/docs"', f'name="next_url" value="{path}"')
        return HTMLResponse(html)


def get_verify_router():
    """Router with POST /docs-otp-verify. Include in app when docs_otp_enabled."""
    from fastapi import APIRouter, Form
    from fastapi.responses import HTMLResponse, RedirectResponse

    router = APIRouter()

    @router.post("/docs-otp-verify")
    async def docs_otp_verify(otp: str = Form(""), next_url: str = Form("/docs")):
        if not _is_otp_valid(otp):
            return HTMLResponse(
                """<!DOCTYPE html><html><body style="font-family:sans-serif;margin:40px;">
                <h2>Invalid or expired OTP</h2>
                <p><a href="/docs">Try again</a></p>
                </body></html>""",
                status_code=401,
            )
        value, max_age = _make_cookie_value()
        target = next_url if next_url in ("/docs", "/redoc") else "/docs"
        response = RedirectResponse(url=target, status_code=302)
        response.set_cookie(
            COOKIE_NAME,
            value,
            max_age=max_age,
            path="/",
            httponly=True,
            samesite="lax",
        )
        return response

    return router
