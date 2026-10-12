"""Dependency-free verification of app.services.hf_sync.sync_batch.

Runs without pytest / MongoDB / HuggingFace by injecting fakes. Exercises:
- query selection (submitted-only, not-yet-synced, pair scope, cursor > _id)
- batch bounding + has_more / next_cursor
- skip of records missing audio
- dry-run (no HF calls, no DB writes)
- real-ish push path (fake HF api) + hf_synced_at stamping

Usage (from backend/):  python scripts/check_hf_sync.py
Exits non-zero on failure.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bson import ObjectId  # noqa: E402


def oid(n: int) -> ObjectId:
    return ObjectId(f"{n:024x}")


class FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class FakeCollection:
    def __init__(self, docs):
        # store copies so we can observe updates
        self.docs = {d["_id"]: dict(d) for d in docs}
        self.update_calls = []

    def _match(self, q):
        out = []
        for d in self.docs.values():
            ok = True
            for k, v in q.items():
                if k == "_id" and isinstance(v, dict) and "$gt" in v:
                    if not d["_id"] > v["$gt"]:
                        ok = False
                        break
                elif isinstance(v, dict) and "$exists" in v:
                    if (k in d) != v["$exists"]:
                        ok = False
                        break
                elif isinstance(v, dict) and "$nin" in v:
                    if d.get(k) in v["$nin"]:
                        ok = False
                        break
                else:
                    if d.get(k) != v:
                        ok = False
                        break
            if ok:
                out.append(d)
        out.sort(key=lambda d: d["_id"])
        return out

    def find(self, q):
        return FakeCursor([dict(d) for d in self._match(q)])

    async def update_many(self, q, update):
        self.update_calls.append((q, update))
        ids = q["_id"]["$in"]
        for i in ids:
            self.docs[i].update(update["$set"])
        return None


class FakeHfApi:
    def __init__(self):
        self.commits = []
        self.repos = []

    def create_repo(self, **kw):
        self.repos.append(kw)

    def create_commit(self, **kw):
        self.commits.append(kw)


# ---- Monkeypatch collaborators -------------------------------------------------
import app.services.hf_sync as hf  # noqa: E402

FAKE_PAIR = {
    "slug": "swahili-english",
    "audio_folder_source": "english",
    "audio_folder_target": "swahili",
    "file_suffix_source": "en",
    "file_suffix_target": "sw",
}


async def _fake_default_pair():
    return FAKE_PAIR


async def _fake_pair_by_id(_pid):
    return FAKE_PAIR


async def _fake_src(doc):
    return b"SRC" if doc.get("_has_src", True) else None


async def _fake_tgt(doc):
    return b"TGT" if doc.get("_has_tgt", True) else None


# Fake contributor_users collection for speaker-demographics lookup in sync_batch.
class _FakeContributors:
    def __init__(self, by_id):
        self._by_id = by_id

    async def find_one(self, q):
        return self._by_id.get(q.get("_id"))


class _FakeDb:
    def __init__(self, contributors):
        self._contributors = _FakeContributors(contributors)

    def __getitem__(self, name):
        if name == "contributor_users":
            return self._contributors
        raise KeyError(name)


def install_fakes(coll, contributors=None):
    hf.get_default_pair_document = _fake_default_pair
    hf.get_pair_by_id = _fake_pair_by_id
    hf.load_source_wav_bytes = _fake_src
    hf.load_target_wav_bytes = _fake_tgt
    hf._coll = lambda: coll  # type: ignore
    _db = _FakeDb(contributors or {})
    hf.get_database = lambda: _db  # type: ignore
    hf.settings.hf_token = "fake-token"


def install_text_fakes(coll):
    hf.get_default_pair_document = _fake_default_pair
    hf.get_pair_by_id = _fake_pair_by_id
    hf._text_coll = lambda: coll  # type: ignore
    hf.settings.hf_token = "fake-token"


def assert_eq(a, b, msg):
    if a != b:
        raise AssertionError(f"{msg}: expected {b!r}, got {a!r}")


async def main():
    failures = 0

    def check(name, fn):
        nonlocal failures
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {name}: {e}")

    # Dataset: 4 submitted (1 missing target), 1 pending, 1 already synced.
    docs = [
        {"_id": oid(1), "status": "submitted", "pair_id": oid(100),
         "text_english": "a", "text_swahili": "aa", "length_english": 1.0, "length_swahili": 1.1},
        {"_id": oid(2), "status": "submitted", "pair_id": oid(100),
         "text_english": "b", "text_swahili": "bb", "_has_tgt": False},  # skipped
        {"_id": oid(3), "status": "pending", "pair_id": oid(100)},  # excluded
        {"_id": oid(4), "status": "submitted", "pair_id": oid(100),
         "text_english": "d", "text_swahili": "dd",
         "submitted_by_user_id": oid(200), "sample_rate": 24000, "duration": 2.0,
         "snr": 30.0, "_has_src": False},  # target-only (contributor ASR style)
        {"_id": oid(5), "status": "submitted", "pair_id": oid(100),
         "text_english": "e", "text_swahili": "ee"},
        {"_id": oid(6), "status": "submitted", "pair_id": oid(100),
         "hf_synced_at": "already", "text_english": "f", "text_swahili": "ff"},
    ]

    # --- Test 1: dry run, limit 2, first page ---
    coll = FakeCollection(docs)
    install_fakes(coll)
    r = await hf.sync_batch(repo_id="acme/ds", pair_oid=oid(100), limit=2, dry_run=True)
    check("dry_run selects submitted+unsynced, honors limit",
          lambda: assert_eq((r.pushed, r.skipped), (1, 1), "pushed/skipped"))
    # ids 1 (pushed) and 2 (skipped) consumed; more remain (4,5) -> not done
    check("dry_run reports not done when more remain",
          lambda: assert_eq(r.done, False, "done"))
    check("dry_run advances cursor to last _id in batch",
          lambda: assert_eq(r.next_cursor, str(oid(2)), "next_cursor"))
    check("dry_run makes no DB writes",
          lambda: assert_eq(len(coll.update_calls), 0, "update_calls"))

    # --- Test 2: continue from cursor, real push via fake HF api ---
    coll = FakeCollection(docs)
    contributors = {
        oid(200): {"gender": "female", "age_range": "25-34", "age_category": "adult_early"},
    }
    install_fakes(coll, contributors)
    api = FakeHfApi()
    r2 = await hf.sync_batch(
        repo_id="acme/ds", pair_oid=oid(100), cursor=str(oid(2)), limit=10, _hf_api=api
    )
    # Remaining submitted+unsynced after _id>2: ids 4 and 5 (6 is already synced).
    check("resumed page pushes remaining unsynced records",
          lambda: assert_eq(r2.pushed, 2, "pushed"))
    check("resumed page is done when exhausted",
          lambda: assert_eq(r2.done, True, "done"))
    check("one commit issued for the batch",
          lambda: assert_eq(len(api.commits), 1, "commits"))
    # id4 target-only (tgt+meta=2) + id5 with source (tgt+src+meta=3) = 5 ops
    check("target-only record omits source op",
          lambda: assert_eq(len(api.commits[0]["operations"]), 5, "operations"))
    check("synced records stamped in DB",
          lambda: assert_eq(coll.docs[oid(4)].get("hf_repo_id"), "acme/ds", "hf_repo_id stamp"))
    check("already-synced record not re-pushed",
          lambda: assert_eq(oid(6) in [ObjectId(i) for i in r2.pushed_ids], False, "skip synced"))

    # Inspect the emitted metadata for ASR/TTS/TTST fields on id4.
    def _meta_for(ops, meta_suffix):
        import json as _json
        for op in ops:
            p = getattr(op, "path_in_repo", None) or getattr(op, "path", "")
            if p.endswith(meta_suffix):
                body = getattr(op, "path_or_fileobj", b"")
                if hasattr(body, "read"):
                    body = body.read()
                return _json.loads(bytes(body).decode("utf-8"))
        return None

    meta4 = _meta_for(api.commits[0]["operations"], f"{str(oid(4))}.jsonl")
    check("metadata emits transcript (target text)",
          lambda: assert_eq(meta4 and meta4.get("transcript"), "dd", "transcript"))
    check("metadata emits translation_en",
          lambda: assert_eq(meta4 and meta4.get("translation_en"), "d", "translation_en"))
    check("metadata emits speaker_id",
          lambda: assert_eq(meta4 and meta4.get("speaker_id"), str(oid(200)), "speaker_id"))
    check("metadata emits demographics",
          lambda: assert_eq(
              meta4 and (meta4.get("gender"), meta4.get("age_category")),
              ("female", "adult_early"), "demographics"))
    check("metadata emits quality metrics",
          lambda: assert_eq(
              meta4 and (meta4.get("sample_rate"), meta4.get("snr")),
              (24000, 30.0), "quality"))

    # --- Test 3: resync re-pushes everything submitted (incl. already synced) ---
    coll = FakeCollection(docs)
    install_fakes(coll)
    r3 = await hf.sync_batch(repo_id="acme/ds", pair_oid=oid(100), limit=50,
                             resync=True, dry_run=True)
    # submitted with audio: ids 1,4,5,6 => 4 pushed; id 2 skipped (no target)
    check("resync includes previously synced records",
          lambda: assert_eq((r3.pushed, r3.skipped), (4, 1), "pushed/skipped"))

    # --- Test 4: missing token on real push is rejected ---
    coll = FakeCollection(docs)
    install_fakes(coll)
    hf.settings.hf_token = ""
    try:
        await hf.sync_batch(repo_id="acme/ds", limit=1)
        check("missing token rejected", lambda: (_ for _ in ()).throw(AssertionError("no error")))
    except hf.HfSyncConfigError:
        check("missing token rejected", lambda: None)

    # ===== Text-to-text sync (parallel_text_corpus) =====
    # Completeness = both text_source and text_target present, regardless of
    # task_type/status. Covers pre-completed corpus pairs AND translation tasks.
    text_docs = [
        # submitted translation task (complete)
        {"_id": oid(11), "task_type": "translation", "status": "submitted",
         "language_pair_id": oid(100), "text_source": "hi", "text_target": "mbote",
         "source_language": "english", "target_language": "kom"},
        # pre-completed corpus pair: NO task_type, NO status — must be INCLUDED now
        {"_id": oid(12), "language_pair_id": oid(100), "source": "bulk_csv",
         "text_source": "bye", "text_target": "kwaheri"},
        # incomplete (empty target) -> excluded
        {"_id": oid(13), "task_type": "translation", "status": "submitted",
         "language_pair_id": oid(100), "text_source": "x", "text_target": ""},
        # pending translation but BOTH texts present -> now INCLUDED (completeness, not status)
        {"_id": oid(14), "task_type": "translation", "status": "pending",
         "language_pair_id": oid(100), "text_source": "y", "text_target": "yy"},
        # already synced -> excluded unless resync
        {"_id": oid(15), "language_pair_id": oid(100), "source": "bulk_csv",
         "text_source": "z", "text_target": "zz", "hf_text_synced_at": "already"},
    ]

    # --- Test 5: text dry run selects all complete, unsynced rows (any kind) ---
    tcoll = FakeCollection(text_docs)
    install_text_fakes(tcoll)
    t1 = await hf.sync_text_batch(repo_id="acme/txt", pair_oid=oid(100), limit=50, dry_run=True)
    # complete + unsynced: 11, 12, 14 => 3 (13 incomplete, 15 already synced)
    check("text dry_run selects all complete unsynced rows (corpus + translation)",
          lambda: assert_eq((t1.pushed, t1.skipped), (3, 0), "pushed/skipped"))
    check("text dry_run makes no DB writes",
          lambda: assert_eq(len(tcoll.update_calls), 0, "update_calls"))

    # --- Test 6: text real push via fake HF api, with kind provenance ---
    tcoll = FakeCollection(text_docs)
    install_text_fakes(tcoll)
    tapi = FakeHfApi()
    t2 = await hf.sync_text_batch(repo_id="acme/txt", pair_oid=oid(100), limit=10, _hf_api=tapi)
    check("text push pushes all complete unsynced rows",
          lambda: assert_eq(t2.pushed, 3, "pushed"))
    check("text push done when exhausted",
          lambda: assert_eq(t2.done, True, "done"))
    check("text commit carries one op per record",
          lambda: assert_eq(len(tapi.commits[0]["operations"]), 3, "operations"))
    check("text synced records stamped with hf_text_repo_id",
          lambda: assert_eq(tcoll.docs[oid(12)].get("hf_text_repo_id"), "acme/txt", "stamp"))
    check("text already-synced record skipped",
          lambda: assert_eq(oid(15) in [ObjectId(i) for i in t2.pushed_ids], False, "skip synced"))
    meta12 = _meta_for(tapi.commits[0]["operations"], f"{str(oid(12))}.jsonl")
    check("corpus row tagged kind=corpus",
          lambda: assert_eq(meta12 and meta12.get("kind"), "corpus", "kind"))
    meta11 = _meta_for(tapi.commits[0]["operations"], f"{str(oid(11))}.jsonl")
    check("translation row tagged kind=translation",
          lambda: assert_eq(meta11 and meta11.get("kind"), "translation", "kind"))

    # --- Test 7: text resync includes already-synced ---
    tcoll = FakeCollection(text_docs)
    install_text_fakes(tcoll)
    t3 = await hf.sync_text_batch(repo_id="acme/txt", pair_oid=oid(100), limit=50,
                                  resync=True, dry_run=True)
    # complete rows regardless of sync stamp: 11,12,14,15 => 4 (13 excluded: empty target)
    check("text resync includes previously synced",
          lambda: assert_eq(t3.pushed, 4, "pushed"))

    print()
    if failures:
        print(f"{failures} check(s) FAILED")
        sys.exit(1)
    print("All hf_sync checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
