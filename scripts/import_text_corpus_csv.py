#!/usr/bin/env python3
"""Chunked POST of parallel text CSV to admin text-corpus bulk endpoint.

Requires admin cookie (same as synth import).

Example:

  python scripts/import_text_corpus_csv.py --url https://api.example.com \\
    --pair-id 674abc... --cookie 'admin_session=JWT' --file data.csv --chunk-rows 50

Or set ADMIN_SESSION env var.
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


def _chunked_rows(
    path: str, chunk_size: int, source_col: str, target_col: str
) -> Iterator[list[dict[str, str]]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise SystemExit("CSV has no header")
        sk = source_col.strip().lower()
        tk = target_col.strip().lower()
        lower_map = {h.strip().lower(): h for h in reader.fieldnames if h}
        if sk not in lower_map or tk not in lower_map:
            raise SystemExit(f"Missing columns {source_col} / {target_col}")
        col_s = lower_map[sk]
        col_t = lower_map[tk]
        buf: list[dict[str, str]] = []
        for row in reader:
            s = (row.get(col_s) or "").strip()
            t = (row.get(col_t) or "").strip()
            if not s and not t:
                continue
            buf.append({source_col: s, target_col: t})
            if len(buf) >= chunk_size:
                yield buf
                buf = []
        if buf:
            yield buf


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000", help="API base (no trailing slash)")
    p.add_argument("--pair-id", required=True, help="language_pairs ObjectId hex")
    p.add_argument("--file", required=True, help="UTF-8 CSV path")
    p.add_argument("--chunk-rows", type=int, default=50, help="Rows per POST (1–200)")
    p.add_argument("--source-column", default="english")
    p.add_argument("--target-column", default="kom")
    p.add_argument("--cookie", default="", help="Cookie e.g. admin_session=...")
    p.add_argument("--timeout", type=float, default=120.0)
    args = p.parse_args()
    if not (1 <= args.chunk_rows <= 200):
        print("Error: --chunk-rows must be between 1 and 200", file=sys.stderr)
        return 1
    cookie = args.cookie or os.environ.get("ADMIN_SESSION", "")
    if not cookie:
        print("Set --cookie or ADMIN_SESSION with admin_session=...", file=sys.stderr)
        return 1
    base = args.url.rstrip("/")
    url = f"{base}/api/admin/text-corpus/bulk-csv"
    params = {
        "pair_id": args.pair_id.strip(),
        "max_rows": args.chunk_rows,
        "source_column": args.source_column,
        "target_column": args.target_column,
    }
    headers = {"Cookie": cookie} if "=" in cookie else {"Cookie": f"admin_session={cookie}"}

    total_ok = 0
    total_err = 0
    for chunk in _chunked_rows(
        args.file, args.chunk_rows, args.source_column, args.target_column
    ):
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=[args.source_column, args.target_column])
        w.writeheader()
        for row in chunk:
            w.writerow(row)
        body = out.getvalue().encode("utf-8")
        files = {"file": ("chunk.csv", body, "text/csv")}
        with httpx.Client(timeout=args.timeout) as client:
            r = client.post(url, params=params, files=files, headers=headers)
        if r.status_code != 200:
            print(f"HTTP {r.status_code}: {r.text[:500]}", file=sys.stderr)
            return 1
        data = r.json()
        total_ok += len(data.get("created_ids", []))
        total_err += len(data.get("errors", []))
        print(f"chunk: created={len(data['created_ids'])} errors={len(data['errors'])}")
    print(f"done: created_ids total={total_ok} error rows total={total_err}")
    return 0


if __name__ == "__main__":
    if _BACKEND not in sys.path:
        sys.path.insert(0, _BACKEND)
    raise SystemExit(main())
