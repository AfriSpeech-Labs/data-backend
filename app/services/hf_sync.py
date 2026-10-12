"""Chunked HuggingFace dataset sync for the speech_parallel collection.

Pushes submitted records (metadata + audio) to a HuggingFace *dataset* repo in
bounded batches so no single call exceeds the serverless function timeout.

Paging/idempotency model:
- Records are walked by ``_id`` ascending. The caller passes the last ``_id``
  it saw as ``cursor``; we return the next cursor to continue.
- Only records with ``status == "submitted"`` and both source+target audio are
  pushed. Already-synced records (``hf_synced_at`` set) are skipped unless
  ``resync=True``.
- Each batch commits its audio files plus a per-record metadata JSONL shard
  (``data/metadata/<id>.jsonl``). Per-record shards avoid read-modify-write of a
  single growing metadata file, which would be slow and race-prone on serverless.
- Synced records are stamped with ``hf_synced_at`` so repeated full syncs are
  incremental.

Requires ``HF_TOKEN`` (write access to the target repo) and ``huggingface_hub``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from bson import ObjectId

from app.config import settings
from app.db import get_database
from app.services.export_zip_paths import zip_paths_for_pair
from app.services.language_pairs import get_default_pair_document, get_pair_by_id
from app.services.speech_parallel_read import (
    load_source_wav_bytes,
    load_target_wav_bytes,
)

COLLECTION = "speech_parallel"

# Server-side cap on how many records a single call may process. Keep small
# enough that audio download + one HF commit fits inside the function timeout.
MAX_BATCH_LIMIT = 25
DEFAULT_BATCH_LIMIT = 10


class HfSyncConfigError(RuntimeError):
    """Raised for missing token / repo id (caller maps to 4xx)."""


@dataclass
class HfSyncResult:
    repo_id: str
    pushed: int = 0
    skipped: int = 0
    next_cursor: str | None = None
    done: bool = True
    pushed_ids: list[str] = field(default_factory=list)
    dry_run: bool = False


def _coll():
    return get_database()[COLLECTION]


def _text_coll():
    return get_database()[settings.parallel_text_corpus_collection]


def _clamp_limit(limit: int | None) -> int:
    if not limit or limit < 1:
        return DEFAULT_BATCH_LIMIT
    return min(limit, MAX_BATCH_LIMIT)


def _build_query(
    pair_oid: ObjectId | None,
    cursor: str | None,
    resync: bool,
) -> dict:
    q: dict = {"status": "submitted"}
    if pair_oid is not None:
        q["pair_id"] = pair_oid
    if not resync:
        q["hf_synced_at"] = {"$exists": False}
    if cursor:
        try:
            q["_id"] = {"$gt": ObjectId(cursor)}
        except Exception as e:
            raise HfSyncConfigError(f"Invalid cursor: {cursor!r}") from e
    return q


async def _resolve_pair_for_doc(doc: dict, pair_cache: dict) -> dict | None:
    pid = doc.get("pair_id")
    key = str(pid) if pid is not None else "__default__"
    if key in pair_cache:
        return pair_cache[key]
    pair = None
    if pid is not None:
        pair = await get_pair_by_id(pid)
    if pair is None:
        pair = await get_default_pair_document()
    pair_cache[key] = pair
    return pair


async def _speaker_demographics(doc: dict, cache: dict) -> dict:
    """Look up the submitting contributor's demographics (cached per batch).

    Returns {speaker_id, gender, age_range, age_category}. speaker_id is the
    contributor's id (stable voice key for TTS/TTST). Missing contributor →
    speaker_id present, demographics null.
    """
    from app.services.demographics import age_category_for_range

    sid = doc.get("submitted_by_user_id")
    speaker_id = str(sid) if sid is not None else None
    out = {
        "speaker_id": speaker_id,
        "gender": None,
        "age_range": None,
        "age_category": None,
    }
    if sid is None:
        return out
    if speaker_id in cache:
        return {**out, **cache[speaker_id]}
    cu = await get_database()["contributor_users"].find_one({"_id": sid})
    demo = {"gender": None, "age_range": None, "age_category": None}
    if cu:
        ar = cu.get("age_range")
        demo = {
            "gender": cu.get("gender"),
            "age_range": ar,
            "age_category": cu.get("age_category") or age_category_for_range(ar),
        }
    cache[speaker_id] = demo
    return {**out, **demo}


async def _metadata_record(
    doc: dict,
    id_str: str,
    pair: dict | None,
    src_path: str,
    tgt_path: str,
    speaker_cache: dict,
) -> dict:
    """ASR/TTS/TTST-ready metadata.

    ``transcript`` is the spoken target-language text (ASR label / TTS input);
    ``translation_en`` is the optional English (TTST input). Audio quality +
    speaker demographics support TTS/TTST voice modeling.
    """
    demo = await _speaker_demographics(doc, speaker_cache)
    return {
        "id": id_str,
        "pair_slug": pair.get("slug") if pair else "",
        "language": (pair.get("audio_folder_target") if pair else None),
        "audio": tgt_path,
        "source_audio": src_path,
        # Spoken target-language transcript = ASR ground truth / TTS input.
        "transcript": doc.get("text_swahili") or "",
        # Optional English translation for speech-translation (TTST).
        "translation_en": doc.get("text_english") or "",
        # Speaker + demographics (TTS/TTST voice mapping).
        "speaker_id": demo["speaker_id"],
        "gender": demo["gender"],
        "age_range": demo["age_range"],
        "age_category": demo["age_category"],
        # Measured audio quality.
        "sample_rate": doc.get("sample_rate"),
        "duration": doc.get("duration", doc.get("length_swahili")),
        "snr": doc.get("snr"),
        "status": doc.get("status", "submitted"),
    }


async def sync_batch(
    *,
    repo_id: str,
    pair_oid: ObjectId | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    resync: bool = False,
    dry_run: bool = False,
    _hf_api=None,
) -> HfSyncResult:
    """Push one bounded batch of submitted records to a HF dataset repo.

    ``_hf_api`` is an injection point for tests (an object exposing
    ``create_repo`` and ``create_commit``). In production we build a real
    ``huggingface_hub.HfApi``.
    """
    repo_id = (repo_id or "").strip()
    if not repo_id:
        raise HfSyncConfigError("repo_id is required (owner/name).")
    if "/" not in repo_id:
        raise HfSyncConfigError("repo_id must be 'owner/name'.")

    token = (settings.hf_token or "").strip()
    if not dry_run and not token:
        raise HfSyncConfigError(
            "HF_TOKEN is not set. Configure it in the environment to push to HuggingFace."
        )

    limit = _clamp_limit(limit)
    query = _build_query(pair_oid, cursor, resync)

    # Fetch one extra to decide whether more remain after this batch.
    docs: list[dict] = []
    async for doc in _coll().find(query).sort("_id", 1).limit(limit + 1):
        docs.append(doc)
    has_more = len(docs) > limit
    batch = docs[:limit]

    result = HfSyncResult(repo_id=repo_id, dry_run=dry_run)
    if not batch:
        result.done = True
        result.next_cursor = cursor
        return result

    # Lazily import huggingface_hub so the module imports without the dep in tests.
    operations = []
    pair_cache: dict = {}
    speaker_cache: dict = {}
    pushed_ids: list[str] = []

    if _hf_api is None and not dry_run:
        from huggingface_hub import HfApi

        _hf_api = HfApi(token=token)
        _hf_api.create_repo(
            repo_id=repo_id, repo_type="dataset", private=True, exist_ok=True
        )

    from huggingface_hub import CommitOperationAdd  # type: ignore

    for doc in batch:
        id_str = str(doc["_id"])
        pair = await _resolve_pair_for_doc(doc, pair_cache)
        if pair:
            src_path, tgt_path = zip_paths_for_pair(pair, id_str)
        else:
            src_path = f"data/audio/source/{id_str}_src.wav"
            tgt_path = f"data/audio/target/{id_str}_tgt.wav"

        # ASR/TTS/TTST require the target (spoken) recording. Source (English)
        # audio is optional and only present for speech-to-speech style items.
        tgt_bytes = await load_target_wav_bytes(doc)
        if not tgt_bytes:
            result.skipped += 1
            continue
        src_bytes = await load_source_wav_bytes(doc)

        meta = await _metadata_record(
            doc, id_str, pair, src_path if src_bytes else "", tgt_path, speaker_cache
        )
        meta_path = f"data/metadata/{id_str}.jsonl"

        if not dry_run:
            operations.append(
                CommitOperationAdd(path_in_repo=tgt_path, path_or_fileobj=tgt_bytes)
            )
            if src_bytes:
                operations.append(
                    CommitOperationAdd(path_in_repo=src_path, path_or_fileobj=src_bytes)
                )
            operations.append(
                CommitOperationAdd(
                    path_in_repo=meta_path,
                    path_or_fileobj=(json.dumps(meta) + "\n").encode("utf-8"),
                )
            )
        pushed_ids.append(id_str)

    if not dry_run and operations:
        _hf_api.create_commit(
            repo_id=repo_id,
            repo_type="dataset",
            operations=operations,
            commit_message=f"Sync {len(pushed_ids)} speech records",
        )
        now = datetime.now(timezone.utc)
        await _coll().update_many(
            {"_id": {"$in": [ObjectId(i) for i in pushed_ids]}},
            {"$set": {"hf_synced_at": now, "hf_repo_id": repo_id}},
        )

    result.pushed = len(pushed_ids)
    result.pushed_ids = pushed_ids
    result.next_cursor = str(batch[-1]["_id"])
    result.done = not has_more
    return result


def _build_text_query(
    pair_oid: ObjectId | None,
    cursor: str | None,
    resync: bool,
) -> dict:
    """Any complete text row (both sides filled): pre-completed corpus pairs AND
    submitted translation tasks. Completeness = text_source and text_target present."""
    q: dict = {
        "text_source": {"$nin": [None, ""]},
        "text_target": {"$nin": [None, ""]},
    }
    if pair_oid is not None:
        q["language_pair_id"] = pair_oid
    if not resync:
        q["hf_text_synced_at"] = {"$exists": False}
    if cursor:
        try:
            q["_id"] = {"$gt": ObjectId(cursor)}
        except Exception as e:
            raise HfSyncConfigError(f"Invalid cursor: {cursor!r}") from e
    return q


def _text_metadata_record(doc: dict, id_str: str, pair: dict | None) -> dict:
    return {
        "id": id_str,
        "pair_slug": pair.get("slug") if pair else "",
        "source_language": doc.get("source_language", ""),
        "target_language": doc.get("target_language", ""),
        "text_source": doc.get("text_source") or "",
        "text_target": doc.get("text_target") or "",
        # Provenance: pre-completed corpus pair vs contributor-translated task.
        "kind": "translation" if doc.get("task_type") == "translation" else "corpus",
        "source": doc.get("source"),
        "submitted_at": (
            doc["submitted_at"].isoformat()
            if doc.get("submitted_at") is not None and hasattr(doc["submitted_at"], "isoformat")
            else doc.get("submitted_at")
        ),
    }


async def sync_text_batch(
    *,
    repo_id: str,
    pair_oid: ObjectId | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    resync: bool = False,
    dry_run: bool = False,
    _hf_api=None,
) -> HfSyncResult:
    """Push one bounded batch of submitted text-to-text records to a HF dataset repo.

    Pure text (no audio / no R2). Each record is committed as
    ``data/text/<id>.jsonl``; synced records are stamped ``hf_text_synced_at``.
    ``_hf_api`` is a test injection point (see sync_batch).
    """
    repo_id = (repo_id or "").strip()
    if not repo_id:
        raise HfSyncConfigError("repo_id is required (owner/name).")
    if "/" not in repo_id:
        raise HfSyncConfigError("repo_id must be 'owner/name'.")

    token = (settings.hf_token or "").strip()
    if not dry_run and not token:
        raise HfSyncConfigError(
            "HF_TOKEN is not set. Configure it in the environment to push to HuggingFace."
        )

    limit = _clamp_limit(limit)
    query = _build_text_query(pair_oid, cursor, resync)

    docs: list[dict] = []
    async for doc in _text_coll().find(query).sort("_id", 1).limit(limit + 1):
        docs.append(doc)
    has_more = len(docs) > limit
    batch = docs[:limit]

    result = HfSyncResult(repo_id=repo_id, dry_run=dry_run)
    if not batch:
        result.done = True
        result.next_cursor = cursor
        return result

    operations = []
    pair_cache: dict = {}
    pushed_ids: list[str] = []

    if _hf_api is None and not dry_run:
        from huggingface_hub import HfApi

        _hf_api = HfApi(token=token)
        _hf_api.create_repo(
            repo_id=repo_id, repo_type="dataset", private=True, exist_ok=True
        )

    from huggingface_hub import CommitOperationAdd  # type: ignore

    for doc in batch:
        id_str = str(doc["_id"])
        pid = doc.get("language_pair_id")
        key = str(pid) if pid is not None else "__default__"
        if key not in pair_cache:
            p = await get_pair_by_id(pid) if pid is not None else None
            if p is None:
                p = await get_default_pair_document()
            pair_cache[key] = p
        pair = pair_cache[key]

        meta = _text_metadata_record(doc, id_str, pair)
        meta_path = f"data/text/{id_str}.jsonl"

        if not dry_run:
            operations.append(
                CommitOperationAdd(
                    path_in_repo=meta_path,
                    path_or_fileobj=(json.dumps(meta) + "\n").encode("utf-8"),
                )
            )
        pushed_ids.append(id_str)

    if not dry_run and operations:
        _hf_api.create_commit(
            repo_id=repo_id,
            repo_type="dataset",
            operations=operations,
            commit_message=f"Sync {len(pushed_ids)} text records",
        )
        now = datetime.now(timezone.utc)
        await _text_coll().update_many(
            {"_id": {"$in": [ObjectId(i) for i in pushed_ids]}},
            {"$set": {"hf_text_synced_at": now, "hf_text_repo_id": repo_id}},
        )

    result.pushed = len(pushed_ids)
    result.pushed_ids = pushed_ids
    result.next_cursor = str(batch[-1]["_id"])
    result.done = not has_more
    return result
