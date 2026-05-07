#!/usr/bin/env python3
"""Chunked POST of parallel EN/SW CSV to admin synth-pairs bulk endpoint (long timeout per chunk).

Requires admin cookie or use from trusted network with curl -b.

Example (after login, copy cookie admin_session=... from browser):

  python scripts/import_synth_pairs_csv.py --url http://127.0.0.1:8000 \\
    --cookie 'admin_session=YOUR_JWT' --file data.csv --chunk-rows 50

Or pass ADMIN_SESSION env var instead of --cookie.
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
    path: str, chunk_size: int, english_col: str, swahili_col: str
) -> Iterator[list[dict[str, str]]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise SystemExit("CSV has no header")
        en_key = english_col.strip().lower()
        sw_key = swahili_col.strip().lower()
        lower_map = {h.strip().lower(): h for h in reader.fieldnames if h}
        if en_key not in lower_map or sw_key not in lower_map:
            raise SystemExit(f"Missing columns {english_col} / {swahili_col}")
        col_en = lower_map[en_key]
        col_sw = lower_map[sw_key]
        buf: list[dict[str, str]] = []
        for row in reader:
            en = (row.get(col_en) or "").strip()
            sw = (row.get(col_sw) or "").strip()
            if not en and not sw:
                continue
            buf.append({english_col: en, swahili_col: sw})
            if len(buf) >= chunk_size:
                yield buf
                buf = []
        if buf:
            yield buf


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000", help="API base (no trailing slash)")
    p.add_argument("--file", required=True, help="UTF-8 CSV path")
    p.add_argument(
        "--chunk-rows",
        type=int,
        default=50,
        help="Rows per POST chunk (must be 1–200; API max_rows cap)",
    )
    p.add_argument("--english-column", default="english")
    p.add_argument("--swahili-column", default="swahili")
    p.add_argument("--cookie", default="", help="Cookie header value e.g. admin_session=...")
    p.add_argument("--timeout", type=float, default=900.0)
    args = p.parse_args()
    if not (1 <= args.chunk_rows <= 200):
        print("Error: --chunk-rows must be between 1 and 200", file=sys.stderr)
        return 1
    cookie = args.cookie or os.environ.get("ADMIN_SESSION", "")
    if not cookie:
        print("Set --cookie or ADMIN_SESSION with admin_session=...", file=sys.stderr)
        return 1
    base = args.url.rstrip("/")
    url = f"{base}/api/admin/synth-pairs/bulk-csv"
    params = {
        "max_rows": args.chunk_rows,
        "english_column": args.english_column,
        "swahili_column": args.swahili_column,
    }
    headers = {"Cookie": cookie} if "=" in cookie else {"Cookie": f"admin_session={cookie}"}

    total_ok = 0
    total_err = 0
    for chunk in _chunked_rows(
        args.file, args.chunk_rows, args.english_column, args.swahili_column
    ):
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=[args.english_column, args.swahili_column])
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
