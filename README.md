# Backend (FastAPI) — Speech Parallel Data API

This folder contains the FastAPI backend for the Speech Parallel Data App.

- Stores metadata in MongoDB and audio either in **MongoDB GridFS** (default) or **Cloudflare R2** (optional).
- Provides **public/contributor** APIs for listing English clips, submitting Swahili audio, and batch-of-25 workflows.
- Provides **admin** APIs for login, dataset exports (capped + full), synthetic TTS pair QA, language-pair management, and contributor-user management.

If you’re looking for the full monorepo overview (web/admin-web/mobile/scripts), see the project root `[README.md](../README.md)`.

## Requirements

- Python 3.10+
- MongoDB (local, Railway, or Atlas)

## Quickstart (local)

From `backend/`:

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
# source venv/bin/activate

pip install -r requirements.txt
```

Create a local env file:

```bash
copy .env.example .env
```

Run the API:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Health check: `GET /health`
- API docs: `GET /docs`

## Configuration (environment variables)

Settings are defined in `[app/config.py](app/config.py)` (loaded from `.env` by default).

### MongoDB

- `MONGODB_URI` (default `mongodb://localhost:27017`)
- `DATABASE_NAME` (default `speech_parallel`)

### CORS (important for browsers + cookies)

The API enables `allow_credentials=True` (cookies), so **you must use exact origins** (no wildcard).

- `CORS_ORIGINS`: comma-separated list of origins, for example:
  - `http://localhost:5173` (contributor web dev server)
  - `http://localhost:5174` (admin-web dev server)
  - Your deployed frontend origins (HTTPS)

If `CORS_ORIGINS` contains `*`, the API will raise on startup (see `[app/main.py](app/main.py)`).

### Admin auth (cookie session)

Admin routes are protected by an httpOnly cookie (default name `admin_session`).

- `ADMIN_JWT_SECRET` (**required** to enable admin login)
- `ADMIN_TOKEN_EXPIRE_HOURS` (default 24)
- `ADMIN_COOKIE_SECURE` (set `true` on HTTPS production)
- `ADMIN_COOKIE_SAMESITE`
  - Use `lax` when admin UI and API are same-site.
  - Use `none` when admin UI and API are on different hosts, but then you must set `ADMIN_COOKIE_SECURE=true` (enforced in `[app/config.py](app/config.py)`).

Create/update an admin user (upsert by email):

```bash
python scripts/create_admin_user.py --email you@example.com --password "your-secret"
```

### Contributor auth (optional; phone + PIN → cookie session)

Contributor login can set an httpOnly cookie (default `contributor_session`) and/or accept `Authorization: Bearer ...`.

- `CONTRIBUTOR_JWT_SECRET` (optional; if empty, dev fallback uses `ADMIN_JWT_SECRET`)
- `CONTRIBUTOR_TOKEN_EXPIRE_HOURS` (default 168)
- `CONTRIBUTOR_COOKIE_SECURE` / `CONTRIBUTOR_COOKIE_SAMESITE`
- `CONTRIBUTOR_PIN_MIN_DIGITS` / `CONTRIBUTOR_PIN_MAX_DIGITS`

### Docs OTP (optional)

When enabled, `/docs`, `/redoc`, and `/openapi.json` require a short-lived OTP that is printed in server logs on startup.

- `DOCS_OTP_ENABLED` (default false)
- `DOCS_OTP_SECRET` (required for gating to actually activate)
- `DOCS_OTP_TTL_MINUTES` (default 60)

### Cloudflare R2 (optional S3-compatible storage)

When configured, audio can be written to R2 instead of GridFS.

- `R2_ENDPOINT_URL`
- `R2_BUCKET_NAME`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`

Notes:

- R2 code uses `aioboto3` at runtime (imported only when R2 is used).
- There is a helper script to migrate existing GridFS audio to R2: `scripts/migrate_gridfs_to_r2.py`.

### Language pairs

The backend supports multiple language pairs; many endpoints accept an optional `pair_id` query param.

- `DEFAULT_LANGUAGE_PAIR_SLUG` (default `swahili-english`)

## Key API routes

The API is assembled in `[app/main.py](app/main.py)`.

### Public / contributor

- `GET /api/audio` (paginated list)
- `GET /api/audio/{id}/english` (stream English WAV)
- `GET /api/audio/{id}/swahili` (stream submitted Swahili WAV)
- `POST /api/audio/{id}/swahili` (submit Swahili WAV)
- `PUT /api/audio/{id}/swahili` (replace Swahili WAV / resubmit)
- `GET /api/audio/stats` and `GET /api/audio/stats/durations`
- `GET /api/audio/export` (ZIP export; capped)
- `POST /api/audio/english/bulk` (bulk upload WAVs)
- `POST /api/audio/english/with-text` (upload one WAV + optional text)
- `POST /api/audio/english/speak` (backend TTS → WAV + doc)
- `POST /api/audio/parallel/bulk-csv` (CSV import; per-row TTS; `max_rows` cap)

Batch-of-25 flow:

- `GET /api/batches` (preview a few random unclaimed batches)
- `POST /api/batches/{batch_index}/claim`
- `GET /api/batches/current`
- `GET /api/batches/my-overview`
- `GET /api/batches/{batch_index}/items`

Contributor login (optional):

- `POST /api/contributor/auth/login`
- `POST /api/contributor/auth/logout`
- `GET /api/contributor/auth/me`

### Admin

Admin auth:

- `POST /api/admin/auth/login`
- `POST /api/admin/auth/logout`
- `GET /api/admin/auth/me`

Parallel audio (admin view/export; requires admin cookie):

- `GET /api/admin/audio` (paginated list)
- `GET /api/admin/audio/{id}/english|swahili` (stream)
- `GET /api/admin/audio/stats` and `GET /api/admin/audio/stats/durations`
- `GET /api/admin/audio/export` (ZIP export; capped)

Full dataset downloads (uncapped; can be large/memory-heavy):

- `GET /api/admin/datasets/parallel/download?status=all|pending|submitted`
- `GET /api/admin/datasets/synthetic/download?status=all|pending|approved|rejected`

Synthetic TTS pairs (QA + export):

- `GET /api/admin/synth-pairs` (list)
- `GET /api/admin/synth-pairs/stats`
- `POST /api/admin/synth-pairs/bulk-csv` (bidirectional TTS per row; `max_rows` cap)
- `PATCH /api/admin/synth-pairs/{id}/validation`
- `GET /api/admin/synth-pairs/{id}/detail`
- `GET /api/admin/synth-pairs/{id}/english|swahili` (stream)
- `GET /api/admin/synth-pairs/export` (ZIP export; capped)

Admin management:

- `GET/POST/PATCH /api/admin/language-pairs` (CRUD + `GET /summary`)
- `GET/POST/PATCH /api/admin/contributors` (CRUD, reset PIN, enable/disable, KPI list)

## Docker (API + MongoDB)

From `backend/`:

```bash
docker compose up --build
```

- API: `http://localhost:8000`
- MongoDB: `mongodb://localhost:27017`

Important: the current `[docker-compose.yml](docker-compose.yml)` sets `CORS_ORIGINS: "*"`, but the app rejects `*` when credentials are enabled.
For local use, set `CORS_ORIGINS` to exact origins (e.g. `http://localhost:5173,http://localhost:5174`) via compose overrides or your environment.

## Deployment notes (Railway)

- Railway should run the `Procfile` command from `[Procfile](Procfile)` (uvicorn on `$PORT`).
- Set `CORS_ORIGINS` in Railway service variables to every deployed frontend origin (comma-separated).
- If admin UI and API are on different hosts over HTTPS, you typically need:
  - `ADMIN_COOKIE_SAMESITE=none`
  - `ADMIN_COOKIE_SECURE=true`

## Troubleshooting

- **Admin login returns 503**: `ADMIN_JWT_SECRET` is not set.
- **Browser requests fail with CORS + cookies**: ensure `CORS_ORIGINS` is an exact origin list (not `*`), and cookie `SameSite`/`Secure` are correct for your deployment topology.
- **OpenAPI “Failed to fetch” on CSV imports**: bulk CSV endpoints do per-row TTS and can run for minutes; browsers/proxies may time out. Use smaller `max_rows` in `/docs`, or use scripts / curl with a long timeout.

