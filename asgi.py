"""Vercel ASGI entrypoint.

Vercel's Python runtime auto-detects FastAPI (from requirements.txt) and loads
the top-level ``app`` variable from a recognized root entrypoint filename
(app.py / index.py / server.py / main.py / wsgi.py / asgi.py). It then routes
every request to the app — no rewrites needed. We expose the existing FastAPI
application unchanged.

Local parity:
    uvicorn asgi:app --reload --host 0.0.0.0 --port 8000
"""

from app.main import app  # noqa: F401  (re-exported as the ASGI handler)

__all__ = ["app"]
