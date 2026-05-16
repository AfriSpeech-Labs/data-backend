"""Delete a language pair and optionally all scoped MongoDB + R2 data."""
from __future__ import annotations

import logging
from typing import Any

from bson import ObjectId

from app.config import settings
from app.db import get_database, get_gridfs, get_gridfs_synth
from app.r2_storage import delete_objects_by_prefix, r2_enabled
from app.services.language_pairs import COLLECTION as PAIRS_COLLECTION

logger = logging.getLogger(__name__)

SPEECH_COLLECTION = "speech_parallel"
CONTRIBUTOR_COLLECTION = "contributor_users"


def _text_corpus_coll():
    return get_database()[settings.parallel_text_corpus_collection]


def _synth_coll():
    return get_database()[settings.synth_pairs_collection]


async def count_contributors_for_pair(pair_oid: ObjectId) -> int:
    return await get_database()[CONTRIBUTOR_COLLECTION].count_documents(
        {"language_pair_id": pair_oid}
    )


async def count_pair_data(pair_oid: ObjectId) -> dict[str, int]:
    db = get_database()
    return {
        "speech_parallel": await db[SPEECH_COLLECTION].count_documents({"pair_id": pair_oid}),
        "batch_claims": await db["batch_claims"].count_documents({"pair_id": pair_oid}),
        "batch_completions": await db["batch_completions"].count_documents(
            {"pair_id": pair_oid}
        ),
        "parallel_text_corpus": await _text_corpus_coll().count_documents(
            {"language_pair_id": pair_oid}
        ),
        "text_batch_claims": await db["text_batch_claims"].count_documents(
            {"pair_id": pair_oid}
        ),
        "text_batch_completions": await db["text_batch_completions"].count_documents(
            {"pair_id": pair_oid}
        ),
        "parallel_synth_pairs": await _synth_coll().count_documents(
            {"language_pair_id": pair_oid}
        ),
    }


def total_data_count(counts: dict[str, int]) -> int:
    return sum(int(v) for v in counts.values())


async def _delete_gridfs_ids(gridfs, file_ids: set[Any]) -> int:
    n = 0
    for fid in file_ids:
        if fid is None:
            continue
        try:
            await gridfs.delete(fid)
            n += 1
        except Exception as e:
            logger.debug("gridfs delete %s: %s", fid, e)
    return n


async def _collect_audio_gridfs_ids(pair_oid: ObjectId) -> set[Any]:
    ids: set[Any] = set()
    coll = get_database()[SPEECH_COLLECTION]
    async for doc in coll.find(
        {"pair_id": pair_oid},
        {"audio_english": 1, "audio_swahili": 1},
    ):
        for field in ("audio_english", "audio_swahili"):
            fid = doc.get(field)
            if fid is not None:
                ids.add(fid)
    return ids


async def _collect_synth_gridfs_ids(pair_oid: ObjectId) -> set[Any]:
    ids: set[Any] = set()
    coll = _synth_coll()
    async for doc in coll.find(
        {"language_pair_id": pair_oid},
        {"audio_english": 1, "audio_swahili": 1},
    ):
        for field in ("audio_english", "audio_swahili"):
            fid = doc.get(field)
            if fid is not None:
                ids.add(fid)
    return ids


async def clear_text_corpus_for_pair_oid(pair_oid: ObjectId) -> dict[str, int]:
    db = get_database()
    corpus = await _text_corpus_coll().delete_many({"language_pair_id": pair_oid})
    claims = await db["text_batch_claims"].delete_many({"pair_id": pair_oid})
    completions = await db["text_batch_completions"].delete_many({"pair_id": pair_oid})
    return {
        "parallel_text_corpus": corpus.deleted_count,
        "text_batch_claims": claims.deleted_count,
        "text_batch_completions": completions.deleted_count,
    }


async def clear_pair_mongo(pair_oid: ObjectId) -> dict[str, int]:
    db = get_database()
    out: dict[str, int] = {
        "speech_parallel": 0,
        "batch_claims": 0,
        "batch_completions": 0,
        "parallel_text_corpus": 0,
        "text_batch_claims": 0,
        "text_batch_completions": 0,
        "parallel_synth_pairs": 0,
        "gridfs_files": 0,
        "r2_objects": 0,
    }

    speech_ids = await _collect_audio_gridfs_ids(pair_oid)
    synth_ids = await _collect_synth_gridfs_ids(pair_oid)

    out["gridfs_files"] += await _delete_gridfs_ids(get_gridfs(), speech_ids)
    out["gridfs_files"] += await _delete_gridfs_ids(get_gridfs_synth(), synth_ids)

    sp = await db[SPEECH_COLLECTION].delete_many({"pair_id": pair_oid})
    out["speech_parallel"] = sp.deleted_count

    bc = await db["batch_claims"].delete_many({"pair_id": pair_oid})
    out["batch_claims"] = bc.deleted_count

    bcomp = await db["batch_completions"].delete_many({"pair_id": pair_oid})
    out["batch_completions"] = bcomp.deleted_count

    text_part = await clear_text_corpus_for_pair_oid(pair_oid)
    out.update(text_part)

    synth = await _synth_coll().delete_many({"language_pair_id": pair_oid})
    out["parallel_synth_pairs"] = synth.deleted_count

    return out


async def clear_pair_r2_prefix(slug: str) -> int:
    if not r2_enabled():
        return 0
    s = (slug or "").strip()
    if not s:
        return 0
    prefix = s if s.endswith("/") else f"{s}/"
    try:
        return await delete_objects_by_prefix(prefix)
    except Exception as e:
        logger.warning("R2 prefix delete failed for %s: %s", prefix, e)
        return 0


async def delete_language_pair_document(pair_oid: ObjectId) -> int:
    res = await get_database()[PAIRS_COLLECTION].delete_one({"_id": pair_oid})
    return res.deleted_count
