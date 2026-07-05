#!/usr/bin/env python3
"""Upload a full parallel script (CSV) to synth-pairs bulk endpoint with chunked TTS.

Each chunk runs bidirectional TTS on the server (Edge English + pair target engine).
Use for large files: rows are POSTed in batches of up to 200 (default 50).

Requires admin cookie or ADMIN_SESSION env var.

Example — English–Twi full script:

  set ADMIN_SESSION=admin_session=YOUR_JWT
  python scripts/import_synth_pairs_csv.py ^
    --url http://127.0.0.1:8000 ^
    --pair-id 674a1b2c3d4e5f6789012345 ^
    --file C:\\data\\english_twi_full.csv ^
    --source-column english ^
    --target-column twi ^
    --chunk-rows 50

Example — English–Swahili (legacy column names still work):

  python scripts/import_synth_pairs_csv.py --url http://127.0.0.1:8000 ^
    --pair-id YOUR_PAIR_ID --file parallel.csv ^
    --english-column english --swahili-column swahili
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from typing import Iterator

import httpx

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _count_rows(path: str, source_col: str, target_col: str) -> int:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise SystemExit("CSV has no header")
        src_key = source_col.strip().lower()
        tgt_key = target_col.strip().lower()
        lower_map = {h.strip().lower(): h for h in reader.fieldnames if h}
        if src_key not in lower_map or tgt_key not in lower_map:
            raise SystemExit(
                f"Missing columns {source_col!r} / {target_col!r}. "
                f"Found: {list(reader.fieldnames)}"
            )
        col_src = lower_map[src_key]
        col_tgt = lower_map[tgt_key]
        n = 0
        for row in reader:
            src = (row.get(col_src) or "").strip()
            tgt = (row.get(col_tgt) or "").strip()
            if src or tgt:
                n += 1
        return n


def _chunked_rows(
    path: str,
    chunk_size: int,
    source_col: str,
    target_col: str,
) -> Iterator[list[dict[str, str]]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise SystemExit("CSV has no header")
        src_key = source_col.strip().lower()
        tgt_key = target_col.strip().lower()
        lower_map = {h.strip().lower(): h for h in reader.fieldnames if h}
        if src_key not in lower_map or tgt_key not in lower_map:
            raise SystemExit(f"Missing columns {source_col} / {target_col}")
        col_src = lower_map[src_key]
        col_tgt = lower_map[tgt_key]
        buf: list[dict[str, str]] = []
        for row in reader:
            src = (row.get(col_src) or "").strip()
            tgt = (row.get(col_tgt) or "").strip()
            if not src and not tgt:
                continue
            buf.append({source_col: src, target_col: tgt})
            if len(buf) >= chunk_size:
                yield buf
                buf = []
        if buf:
            yield buf


def main() -> int:
    p = argparse.ArgumentParser(
        description="Upload full parallel CSV to synth-pairs (chunked TTS import).",
    )
    p.add_argument("--url", default="http://127.0.0.1:8000", help="API base (no trailing slash)")
    p.add_argument("--file", required=True, help="UTF-8 CSV path (full script)")
    p.add_argument(
        "--pair-id",
        required=True,
        help="Language pair ObjectId (drives TTS engines/voices)",
    )
    p.add_argument(
        "--chunk-rows",
        type=int,
        default=50,
        help="Rows per POST chunk (1–200; API max_rows cap)",
    )
    p.add_argument("--source-column", default="english")
    p.add_argument("--target-column", default="swahili")
    p.add_argument("--english-column", default="", help="Legacy alias for --source-column")
    p.add_argument("--swahili-column", default="", help="Legacy alias for --target-column")
    p.add_argument("--cookie", default="", help="Cookie header value e.g. admin_session=...")
    p.add_argument(
        "--timeout",
        type=float,
        default=900.0,
        help="HTTP timeout seconds per chunk (TTS can be slow; default 900)",
    )
    p.add_argument(
        "--continue-on-chunk-error",
        action="store_true",
        help="Keep uploading remaining chunks if one chunk returns HTTP error",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows and chunks only; do not POST",
    )
    args = p.parse_args()
    source_col = args.source_column or args.english_column or "english"
    target_col = args.target_column or args.swahili_column or "swahili"
    if not (1 <= args.chunk_rows <= 200):
        print("Error: --chunk-rows must be between 1 and 200", file=sys.stderr)
        return 1
    if not os.path.isfile(args.file):
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        return 1

    total_rows = _count_rows(args.file, source_col, target_col)
    n_chunks = (total_rows + args.chunk_rows - 1) // args.chunk_rows if total_rows else 0
    print(
        f"File: {args.file}\n"
        f"Pair: {args.pair_id.strip()}\n"
        f"Columns: {source_col} / {target_col}\n"
        f"Rows (non-empty): {total_rows}\n"
        f"Chunks: {n_chunks} x up to {args.chunk_rows} rows"
    )
    if total_rows == 0:
        print("Nothing to upload.", file=sys.stderr)
        return 1
    if args.dry_run:
        print("Dry run — no requests sent.")
        return 0

    cookie = args.cookie or os.environ.get("ADMIN_SESSION", "")
    if not cookie:
        print("Set --cookie or ADMIN_SESSION with admin_session=...", file=sys.stderr)
        return 1
    base = args.url.rstrip("/")
    url = f"{base}/api/admin/synth-pairs/bulk-csv"
    headers = {"Cookie": cookie} if "=" in cookie else {"Cookie": f"admin_session={cookie}"}

    total_ok = 0
    total_err = 0
    chunk_idx = 0
    for chunk in _chunked_rows(args.file, args.chunk_rows, source_col, target_col):
        chunk_idx += 1
        params = {
            "pair_id": args.pair_id.strip(),
            "max_rows": args.chunk_rows,
            "source_column": source_col,
            "target_column": target_col,
        }
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=[source_col, target_col])
        w.writeheader()
        for row in chunk:
            w.writerow(row)
        body = out.getvalue().encode("utf-8")
        files = {"file": ("chunk.csv", body, "text/csv")}
        print(f"Posting chunk {chunk_idx}/{n_chunks} ({len(chunk)} rows)…", flush=True)
        with httpx.Client(timeout=args.timeout) as client:
            r = client.post(url, params=params, files=files, headers=headers)
        if r.status_code != 200:
            print(f"Chunk {chunk_idx} HTTP {r.status_code}: {r.text[:500]}", file=sys.stderr)
            if args.continue_on_chunk_error:
                continue
            return 1
        data = r.json()
        created = len(data.get("created_ids", []))
        error_items = data.get("errors") or []
        errs = len(error_items)
        total_ok += created
        total_err += errs
        print(f"  chunk {chunk_idx}: created={created} errors={errs}")
        if error_items:
            seen: set[str] = set()
            shown = 0
            for item in error_items:
                detail = item.get("detail") if isinstance(item, dict) else str(item)
                row = item.get("row") if isinstance(item, dict) else "?"
                if not detail or detail in seen:
                    continue
                seen.add(detail)
                print(f"    row {row}: {detail}")
                shown += 1
                if shown >= 5:
                    remaining = len({e.get("detail") for e in error_items if isinstance(e, dict)}) - shown
                    if remaining > 0:
                        print(f"    … and {remaining} more unique error(s)")
                    break
    print(f"Done: created_ids total={total_ok} error rows total={total_err}")
    return 0 if total_ok > 0 or total_err == 0 else 1


if __name__ == "__main__":
    if _BACKEND not in sys.path:
        sys.path.insert(0, _BACKEND)
    raise SystemExit(main())
