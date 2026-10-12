# Backend (FastAPI) — Speech Parallel Data API

This folder contains the FastAPI backend for the Speech Parallel Data App.

- Stores metadata in MongoDB and audio either in **MongoDB GridFS** (default) or **Cloudflare R2** (optional).
- Provides **public/contributor** APIs for listing English clips, submitting Swahili audio, and batch-of-25 workflows.
- Provides **admin** APIs for login, dataset exports (capped + full), synthetic TTS pair QA, **parallel text corpus** (text-only lines per language pair), language-pair management, and contributor-user management.

If you’re looking for the full monorepo overview (web/admin-web/mobile/scripts), see the project root `[README.md](../README.md)`.

> **Deployment:** The API runs on **Vercel** (serverless) via `api/index.py` +
> `vercel.json`. See [Deploy to Vercel](#deploy-to-vercel). The MMS (torch) TTS
> stack was removed; all server-side TTS synthesis is **offline-only** now (see
> [Offline TTS workflow](#offline-tts-workflow)). Railway/Docker files remain in
> the repo marked **LEGACY**.

## Requirements

- Python 3.12 (matches the Vercel runtime; 3.10+ works locally)
- MongoDB (local or Atlas)
- Cloudflare R2 for audio bytes in production (`REQUIRE_R2=true`)

## Quickstart (local)

From `backend/`:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

Create a local env file:

```bash
copy .env.example .env
```

Provision the database once (indexes, default pair, legacy backfill):

```bash
# Windows PowerShell: $env:PYTHONPATH="."
set PYTHONPATH=.
python scripts/init_db.py
```

Run the API (either entrypoint works locally):

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# or match the Vercel entrypoint:
uvicorn api.index:app --reload --host 0.0.0.0 --port 8000
```

- Health check: `GET /health` → `{"status":"ok"}`
- API docs: `GET /docs`

> **Note on DB init:** The app no longer creates indexes or seeds data on
> startup (that was removed for fast serverless cold starts). Run
> `scripts/init_db.py` once per database — it is idempotent and safe to re-run.

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

When enabled, `/docs`, `/redoc`, and `/openapi.json` require a short-lived OTP. A code is printed in server logs on startup, and a **new** code is issued (and logged) each time an unauthenticated user opens or refreshes `/docs` or `/redoc`. After you enter a valid OTP, a cookie keeps access for `DOCS_OTP_TTL_MINUTES` without re-prompting.

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
- When `REQUIRE_R2=true` (production / Vercel), R2 is **required** — the backend fails fast if R2 is not configured, and MongoDB is used for **metadata/users** only. Leave `REQUIRE_R2=false` for local GridFS-only dev.
- Migration scripts:
  - Parallel dataset (collection `speech_parallel`): `scripts/migrate_gridfs_to_r2.py`
  - Synthetic pairs (collection `parallel_synth_pairs` by default): `scripts/migrate_synth_gridfs_to_r2.py`

### Language pairs

The backend supports multiple language pairs; many endpoints accept an optional `pair_id` query param.

- `DEFAULT_LANGUAGE_PAIR_SLUG` (default `swahili-english`)

### Parallel text corpus (e.g. English–Kom)

Text-only parallel lines live in MongoDB collection **`parallel_text_corpus`** (override with `PARALLEL_TEXT_CORPUS_COLLECTION`). They are separate from **`speech_parallel`** (audio workflow).

1. **Create a pair** (admin cookie): `POST /api/admin/language-pairs` with e.g. slug `english-kom`, `display_name` `English–Kom`, and sensible `audio_folder_*` / `file_suffix_*` if you may record audio later (`english` / `kom`, suffixes `en` / `km`).
2. Note the returned **`id`** — use it as **`pair_id`** for imports.

**CSV example** (column names are configurable via query params; defaults `english` and `kom`):

```text
english,kom
Hello.,Mbha.
```

**Admin API** (requires admin cookie):

- `POST /api/admin/text-corpus/bulk-csv` — multipart `file`; query: **`pair_id`** (required), `max_rows` (default 50, max 200), `source_column`, `target_column`
- `GET /api/admin/text-corpus?pair_id=...&page=1&limit=20` — optional `q=` substring search on source/target
- `GET /api/admin/text-corpus/stats` — global totals and per-pair counts; optional `pair_id` for one pair’s total only

**Chunked CLI** (same auth as synth import):

```bash
python scripts/import_text_corpus_csv.py --url https://your-api.example.com ^
  --pair-id YOUR_LANGUAGE_PAIR_OBJECT_ID ^
  --file path/to/corpus.csv ^
  --chunk-rows 50 ^
  --source-column english ^
  --target-column kom
```

Set `ADMIN_SESSION` or `--cookie` with `admin_session=...`.

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
- `POST /api/audio/english/speak` (**501** — offline TTS only)
- `POST /api/audio/parallel/bulk-csv` (**501** — offline TTS only)

Batch-of-25 flow:

- `GET /api/batches` (preview a few random unclaimed batches)
- `POST /api/batches/{batch_index}/claim`
- `GET /api/batches/current`
- `GET /api/batches/my-overview`
- `GET /api/batches/{batch_index}/items`

Text translation batch-of-25 flow:

- `GET /api/text-batches/stats` (translation task totals for contributor’s pair)
- `GET /api/text-batches` (preview unclaimed batches)
- `POST /api/text-batches/{batch_index}/claim`
- `GET /api/text-batches/current`
- `GET /api/text-batches/my-overview`
- `GET /api/text-batches/{batch_index}/items`
- `POST /api/text-corpus/{id}/translation` and `PUT` (submit English for a task)

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
- `POST /api/admin/datasets/hf-sync` (chunked HuggingFace dataset sync; see [HuggingFace dataset sync](#huggingface-dataset-sync))

Synthetic TTS pairs (QA + export; **pair-aware multi-language TTS**):

- `GET /api/admin/synth-pairs` (list; optional `pair_id`)
- `GET /api/admin/synth-pairs/stats`
- `POST /api/admin/synth-pairs/bulk-csv` — **501** on this deployment (offline TTS only); use `scripts/import_synth_pairs_csv.py`
- `PATCH /api/admin/synth-pairs/{id}/validation`
- `GET /api/admin/synth-pairs/{id}/detail`
- `GET /api/admin/synth-pairs/{id}/english|swahili` (stream source/target audio)
- `GET /api/admin/synth-pairs/export` (ZIP export; capped)

**TTS engines (per language pair) — offline only:**

Server-side synthesis endpoints return **501** on the serverless deployment.
Generate audio **offline** with the CLI (see [Offline TTS workflow](#offline-tts-workflow)); the pair's TTS config still drives which engine/voice the CLI uses.

- **English source** (`audio_folder_source: english`): **Edge TTS** with random **male/female** voices (8-voice default pool; override via `tts_source_voices` on the pair).
- **Target**: **Edge** when Azure has voices (e.g. Swahili). **MMS was removed** — pairs configured for the `mms` engine raise a clear "unsupported" error on the server; generate MMS audio with a locally-installed toolchain if you still need it.
- Configure via `POST/PATCH /api/admin/language-pairs` optional fields: `tts_target_engine`, `tts_target_voices`, and the new `hf_repo_id` (HuggingFace dataset target for this pair).

**Bulk CSV query params:** `pair_id`, `source_column`, `target_column` (legacy aliases: `english_column`, `swahili_column`). Column defaults come from the pair’s folder names.

**Chunked CLI (full script upload):**

```bash
# Dry-run: count rows/chunks only
python scripts/import_synth_pairs_csv.py --dry-run ^
  --pair-id YOUR_LANGUAGE_PAIR_OBJECT_ID ^
  --file path/to/full_script.csv ^
  --source-column english ^
  --target-column twi

# Upload entire file (50 rows per request; increase --timeout for MMS)
python scripts/import_synth_pairs_csv.py --url https://your-api.example.com ^
  --pair-id YOUR_LANGUAGE_PAIR_OBJECT_ID ^
  --file path/to/full_script.csv ^
  --source-column english ^
  --target-column twi ^
  --chunk-rows 50
```

Admin UI (pair workspace → Synth): use **Upload full file** for CSV or Excel — same chunked flow in the browser.

**MMS removed:** the `torch`/`transformers` stack is no longer bundled. The synth bulk-CSV endpoint returns 501; run the offline CLI instead.

Parallel text corpus (text-only; admin):

- `POST /api/admin/text-corpus/bulk-csv` (`pair_id` required)
- `GET /api/admin/text-corpus` (`pair_id` required)
- `GET /api/admin/text-corpus/stats`
- `GET /api/admin/text-corpus/translation-stats` (`pair_id` required)

Admin management:

- `GET/POST/PATCH /api/admin/language-pairs` (CRUD + `GET /summary`)
- `GET/POST/PATCH /api/admin/contributors` (CRUD, reset PIN, enable/disable, KPI list; `role`: `contributor` | `reviewer`)
- Reviewer synth QA (phone+PIN accounts with `role=reviewer`):
  - `GET/PATCH /api/reviewer/synth-pairs` (list/stats/validation; pair-scoped)
  - `GET /api/reviewer/synth-pairs/{id}/english|swahili`

### Reviewer login shows Home instead of Synth QA

The contributor web app routes by `role` from login/`/me`. If you still see Home / Record / Translate:

1. Admin → Contributors: Role column must be **`reviewer`** (edit + Save if needed).
2. Contributor app gear → **Server URL** must point at a backend that includes reviewer auth (local `http://127.0.0.1:8000` or redeployed Railway). Default production host without the new code omits `role` → SPA treats you as contributor.
3. **Sign out**, then sign in again.
4. DevTools → Network → `login` response must include `"role":"reviewer"`.

## Deploy to Vercel

The API runs as a single ASGI function on Vercel:

- `[api/index.py](api/index.py)` imports the FastAPI `app` and exposes it as the handler.
- `[vercel.json](vercel.json)` rewrites **all** paths to that function (Python 3.12, 60s max duration).
- `[.vercelignore](.vercelignore)` keeps the bundle small (no venv, scripts, legacy Docker files, or TTS toolchain).

### One-time setup

1. **Provision the database** (from your machine, against the production Mongo):

   ```bash
   set PYTHONPATH=.   # PowerShell: $env:PYTHONPATH="."
   python scripts/init_db.py
   ```

2. **Create the Vercel project** with **Root Directory = `backend`**.

3. **Set environment variables** in the Vercel project (Settings → Environment Variables):

   | Variable | Required | Notes |
   | --- | --- | --- |
   | `MONGODB_URI` | yes | Atlas `mongodb+srv://...` recommended |
   | `DATABASE_NAME` | yes | e.g. `speech_parallel` |
   | `REQUIRE_R2` | yes | `true` — enforces R2 + CORS in prod |
   | `CORS_ORIGINS` | yes | comma-separated exact frontend origins (no `*`) |
   | `ADMIN_JWT_SECRET` | yes | long random secret |
   | `ADMIN_COOKIE_SECURE` | yes | `true` (HTTPS) |
   | `ADMIN_COOKIE_SAMESITE` | yes | `none` (cross-site admin UI) |
   | `R2_ENDPOINT_URL` / `R2_BUCKET_NAME` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | yes | audio bytes |
   | `HF_TOKEN` | for HF sync | write token for the dataset repo(s) |
   | `CONTRIBUTOR_COOKIE_SECURE` / `CONTRIBUTOR_COOKIE_SAMESITE` | if contributor cookies | `true` / `none` |
   | `CONTRIBUTOR_JWT_SECRET` | recommended | separate from admin |

4. **Deploy** (git push or `vercel --prod`).

### Serverless constraints

- Functions are **stateless** with short timeouts (10s hobby / 60s Pro default; `vercel.json` sets 60s). No persistent DB connections — the Motor client is cached per warm instance.
- **No ffmpeg / no ML stack.** Server-side TTS endpoints return **501**; see the offline workflow below.
- Large ZIP exports and HF sync are **bounded**: HF sync is chunked (`POST /api/admin/datasets/hf-sync`), and heavy full-dataset work should use the CLI scripts.

### Post-deploy smoke tests

```bash
curl -sS https://<your-app>.vercel.app/health
curl -I  https://<your-app>.vercel.app/docs
curl -i  https://<your-app>.vercel.app/api/admin/auth/login ^
  -H "content-type: application/json" ^
  -d "{\"email\":\"you@example.com\",\"password\":\"your-password\"}"
curl -sS "https://<your-app>.vercel.app/api/audio?page=1&limit=20"
```

## Offline TTS workflow

Server-side synthesis moved off the API. Generate audio on an admin machine and
upload the WAVs via the surviving insert endpoints.

1. Install the toolchain locally (needs system **ffmpeg**):

   ```bash
   pip install -r requirements.txt -r requirements-tts.txt
   # ffmpeg: choco install ffmpeg (Windows) / apt-get install ffmpeg / brew install ffmpeg
   ```

2. Run an import script against the deployed API (they synthesize locally and
   upload pre-generated WAVs):

   ```bash
   python scripts/import_synth_pairs_csv.py --url https://<your-app>.vercel.app ^
     --pair-id YOUR_PAIR_ID --file script.csv --source-column english --target-column swahili
   ```

Endpoints that now return **501** (offline-only): `POST /api/audio/english/speak`,
`POST /api/audio/parallel/bulk-csv`, `POST /api/admin/synth-pairs/bulk-csv`.
Surviving upload endpoints: `POST /api/audio/english/with-text`, `POST /api/audio/english/bulk`.

## HuggingFace dataset sync

Push data to HuggingFace **dataset** repos in bounded batches that fit the
serverless timeout. Two datasets are supported, each with its own repo:

| `dataset` | Source collection | Records pushed | Per-pair repo field |
| --- | --- | --- | --- |
| `speech` (default) | `speech_parallel` | `status == "submitted"` with source+target audio | `hf_repo_id` |
| `text` | `parallel_text_corpus` | `task_type == "translation"`, `status == "submitted"`, non-empty `text_source` **and** `text_target` | `hf_text_repo_id` |

- Set `HF_TOKEN` (write access) in the environment.
- Set the target repo(s) per language pair via `hf_repo_id` and/or
  `hf_text_repo_id` (`owner/name`) on `POST/PATCH /api/admin/language-pairs`, or
  pass `repo_id` per request. When `repo_id` is omitted it falls back to the
  pair's field for the chosen `dataset`.
- Audio and text are **separate repos** and page independently (each keeps its
  own cursor and sync stamp: `hf_synced_at` / `hf_text_synced_at`).

Call repeatedly, feeding `next_cursor` back as `cursor`, until `done` is true:

```bash
# Speech (default). dry_run reports without pushing. Admin cookie required.
curl -i https://<your-app>.vercel.app/api/admin/datasets/hf-sync ^
  -H "content-type: application/json" ^
  --cookie "admin_session=..." ^
  -d "{\"dataset\":\"speech\",\"pair_id\":\"YOUR_PAIR_ID\",\"limit\":10,\"dry_run\":true}"

# Text-to-text. Repeat with the returned next_cursor until {"done": true}.
curl -s https://<your-app>.vercel.app/api/admin/datasets/hf-sync ^
  -H "content-type: application/json" ^
  --cookie "admin_session=..." ^
  -d "{\"dataset\":\"text\",\"pair_id\":\"YOUR_PAIR_ID\",\"limit\":10,\"cursor\":\"<next_cursor>\"}"
```

Request fields: `dataset` (`speech`|`text`), `repo_id`, `pair_id`, `cursor`,
`limit`, `resync`, `dry_run`. Response: `repo_id`, `pushed`, `skipped`,
`next_cursor`, `done`, `pushed_ids`, `dry_run`. Synced records are stamped and
skipped on later runs unless `resync=true`. Speech commits the paired WAVs plus
a per-record shard (`data/metadata/<id>.jsonl`); text commits a per-record shard
(`data/text/<id>.jsonl`).

Logic is verified by `scripts/check_hf_sync.py` (no external services needed):

```bash
python scripts/check_hf_sync.py
```

## Legacy: Docker / Railway (not used for the live API)

> These files are kept for self-hosting but the live API deploys to Vercel.

Local container dev (API + MongoDB):

```bash
docker compose up --build   # API on :8000, MongoDB on :27017
```

`[docker-compose.yml](docker-compose.yml)` sets `CORS_ORIGINS: "*"`, which the app
rejects when credentials are on — override with exact origins. The
`[Dockerfile](Dockerfile)` installs ffmpeg and the optional TTS toolchain (MMS/torch
removed). Railway config lives in `[railway.toml](railway.toml)` / `[Procfile](Procfile)`
with Root Directory `backend` and `REQUIRE_R2=true`.

### Migration (move existing audio from GridFS to R2)

Run once (from `backend/` with venv active) after setting `MONGODB_URI` and all `R2_*`:

```bash
set PYTHONPATH=.
python scripts/migrate_gridfs_to_r2.py
python scripts/migrate_synth_gridfs_to_r2.py
```

## Troubleshooting

- **Admin login returns 503**: `ADMIN_JWT_SECRET` is not set.
- **Browser requests fail with CORS + cookies**: ensure `CORS_ORIGINS` is an exact origin list (not `*`), and cookie `SameSite`/`Secure` are correct for your deployment topology.
- **App fails to start with "R2 is required"**: `REQUIRE_R2=true` but R2 vars are missing. Set all `R2_*`, or use `REQUIRE_R2=false` for local GridFS dev.
- **TTS endpoints return 501**: expected — synthesis is offline-only. Use `scripts/import_*_csv.py` and upload WAVs via `/api/audio/english/with-text`.
- **HF sync returns 400 "HF_TOKEN is not set"**: set `HF_TOKEN` with write access, or call with `dry_run=true` to preview.
- **HF sync "repo_id is required"**: pass `repo_id` in the body or set `hf_repo_id` on the language pair.
- **No data after deploy / missing default pair**: run `python scripts/init_db.py` once against the production database (startup no longer seeds).
- **`python-dotenv could not parse` locally**: your `.env` contains non `KEY=value` lines (e.g. pasted shell commands). Remove them — `.env.example` shows the valid format.
