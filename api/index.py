"""Vercel serverless ASGI entrypoint.

Vercel's Python runtime discovers an ASGI/WSGI callable named ``app`` in files
under ``api/``. We import the existing FastAPI application unchanged and expose
it here, so the whole app runs as a single serverless function. All routing is
handled by FastAPI; ``vercel.json`` rewrites every path to this function.

Local parity:
    uvicorn api.index:app --reload --host 0.0.0.0 --port 8000
"""

from app.main import app  # noqa: F401  (re-exported as the ASGI handler)

__all__ = ["app"]
