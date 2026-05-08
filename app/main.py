import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings


def _cors_allow_origins() -> list[str]:
    raw = (settings.cors_origins or "").strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if "*" in parts:
        raise RuntimeError(
            "CORS_ORIGINS cannot include '*' while admin auth uses cookies (credentials). "
            "Set CORS_ORIGINS to your admin site origin(s), comma-separated, e.g. "
            "https://elegant-youthfulness-production-8e95.up.railway.app"
        )
    if parts:
        return parts
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"):
        raise RuntimeError(
            "CORS_ORIGINS is unset. Set it on Railway to your admin web URL(s), e.g. "
            "https://your-admin-service.up.railway.app (comma-separated if multiple)."
        )
    # Local dev: admin-web 5174, contributor web 5173 (see vite configs)
    return [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ]
from app.db import connect_db, close_db
from app.docs_otp import DocsOTPMiddleware, get_verify_router, init_docs_otp
from app.routes.admin_audio import router as admin_audio_router
from app.routes.admin_auth import router as admin_auth_router
from app.routes.admin_contributors import router as admin_contributors_router
from app.routes.admin_datasets import router as admin_datasets_router
from app.routes.admin_language_pairs import router as admin_language_pairs_router
from app.routes.admin_synth_pairs import router as admin_synth_pairs_router
from app.routes.admin_text_corpus import router as admin_text_corpus_router
from app.routes.audio import router as audio_router
from app.routes.batches import router as batches_router
from app.routes.contributor_auth import router as contributor_auth_router

app = FastAPI(title="Speech Parallel Data API", version="1.0.0")

if settings.docs_otp_enabled and settings.docs_otp_secret:
    app.add_middleware(DocsOTPMiddleware)
    app.include_router(get_verify_router())

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(audio_router)
app.include_router(batches_router)
app.include_router(contributor_auth_router)
app.include_router(admin_auth_router)
app.include_router(admin_language_pairs_router)
app.include_router(admin_contributors_router)
app.include_router(admin_audio_router)
app.include_router(admin_datasets_router)
app.include_router(admin_synth_pairs_router)
app.include_router(admin_text_corpus_router)


@app.on_event("startup")
async def startup():
    await connect_db()
    if settings.docs_otp_enabled and settings.docs_otp_secret:
        init_docs_otp()


@app.on_event("shutdown")
async def shutdown():
    await close_db()


@app.get("/health")
def health():
    return {"status": "ok"}
