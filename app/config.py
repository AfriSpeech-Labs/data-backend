"""Application configuration from environment."""
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongodb_uri: str = "mongodb://localhost:27017"
    database_name: str = "speech_parallel"
    gridfs_bucket: str = "audio"
    # Comma-separated exact origins (no "*"). Wildcard is invalid with cookie credentials.
    cors_origins: str = ""
    # Docs OTP: when True, /docs and /redoc require a 6-digit code (logged at startup).
    docs_otp_enabled: bool = False
    docs_otp_secret: str = ""
    docs_otp_ttl_minutes: int = 60
    # Admin dashboard: JWT in httpOnly cookie; set a strong secret in production.
    admin_jwt_secret: str = ""
    admin_token_expire_hours: int = 24
    admin_cookie_name: str = "admin_session"
    admin_cookie_secure: bool = False
    # Cross-origin admin UI + API (different hosts): use "none" with admin_cookie_secure=true.
    admin_cookie_samesite: str = "lax"
    # Bidirectional TTS synth module (parallel_synth_pairs + separate GridFS bucket)
    synth_pairs_collection: str = "parallel_synth_pairs"
    synth_pairs_gridfs_bucket: str = "audio_synth"
    # Parallel text-only corpus (e.g. English–Kom), separate from speech_parallel audio docs.
    parallel_text_corpus_collection: str = "parallel_text_corpus"
    # Swahili engine: only "edge" is implemented; "mms" reserved for future worker
    synth_sw_engine: str = "edge"
    synth_augment_enabled: bool = False
    # Cloudflare R2 (S3-compatible). Leave endpoint empty to use MongoDB GridFS only.
    r2_endpoint_url: str = ""
    r2_bucket_name: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    # Default pair slug for legacy session-only clients (X-Session-ID) and unscoped imports.
    default_language_pair_slug: str = "swahili-english"
    # Contributor auth (JWT httpOnly cookie or Bearer); PIN set by admin.
    contributor_jwt_secret: str = ""
    contributor_cookie_name: str = "contributor_session"
    contributor_token_expire_hours: int = 168
    contributor_cookie_secure: bool = False
    contributor_cookie_samesite: str = "lax"
    contributor_pin_min_digits: int = 4
    contributor_pin_max_digits: int = 8

    class Config:
        env_file = ".env"
        extra = "ignore"

    @field_validator("admin_cookie_samesite", "contributor_cookie_samesite", mode="before")
    @classmethod
    def normalize_cookie_samesite(cls, v: object) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "lax"
        s = str(v).lower().strip()
        if s not in ("lax", "strict", "none"):
            raise ValueError(
                "cookie samesite must be lax, strict, or none "
                f"(got {v!r})"
            )
        return s

    @model_validator(mode="after")
    def samesite_none_requires_secure(self):
        if self.admin_cookie_samesite == "none" and not self.admin_cookie_secure:
            raise ValueError(
                "ADMIN_COOKIE_SAMESITE=none requires ADMIN_COOKIE_SECURE=true"
            )
        return self

    @model_validator(mode="after")
    def contributor_samesite_none_requires_secure(self):
        if self.contributor_cookie_samesite == "none" and not self.contributor_cookie_secure:
            raise ValueError(
                "CONTRIBUTOR_COOKIE_SAMESITE=none requires CONTRIBUTOR_COOKIE_SECURE=true"
            )
        return self


settings = Settings()
