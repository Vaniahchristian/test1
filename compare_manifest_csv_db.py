#!/usr/bin/env python3
"""Compare manifest CSV parse + normalization to rows in document_items.

Usage:
  python compare_manifest_csv_db.py "MS-3 20260407 .final.csv"
  python compare_manifest_csv_db.py "path.csv" --document-id <uuid>

Requires .env with SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY for DB comparison.
Without credentials, prints CSV trace + expected rows only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_CMP_FIELDS = (
    "line_no",
    "marks",
    "section",
    "total_cartons",
    "total_quantity",
    "total_cbm",
    "total_weight_kg",
    "total_amount_rmb",
    "warehouse",
    "shop",
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv_path", type=Path)
    ap.add_argument("--document-id", help="documents.id (latest match by filename if omitted)")
    args = ap.parse_args()
    path = args.csv_path.expanduser().resolve()
    if not path.is_file():
        print(f"Not found: {path}", file=sys.stderr)
        return 1

    from sales_import.container_manifest_import import load_container_manifest_lines_from_csv
    from sales_import.line_normalize import normalize_line_items
    from sales_import.map_rows import extraction_row_to_document_item

    trace: list[dict[str, Any]] = []
    raw_lines, footer, _subs, banners, _payments = load_container_manifest_lines_from_csv(path, trace_events=trace)
    norm_lines = normalize_line_items(list(raw_lines))

    fake_doc = "00000000-0000-0000-0000-000000000001"
    expected: list[dict[str, Any]] = []
    for i, raw in enumerate(norm_lines, start=1):
        expected.append(
            extraction_row_to_document_item(raw, document_id=fake_doc, db_line_no=i)
        )

    print(f"=== CSV: {path.name} ===")
    print(f"Parsed line items: {len(norm_lines)} | Footer cartons: {footer.get('total_cartons') if footer else None}")
    print()

    unrecognized = [b for b in banners if not b.get("recognized")]
    if unrecognized:
        print(f"=== Section banners the classifier didn't recognize ({len(unrecognized)}) ===")
        for b in unrecognized:
            print(f"  {b['text']!r} (kept section={b['section']!r})")
        print()

    trace_path = path.with_suffix(".compare_trace.json")
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Full parser trace written to: {trace_path}")
    print()

    item_events = [ev for ev in trace if ev.get("event") == "item"]
    print(f"=== Item rows mapped from CSV ({len(item_events)} lines) csv_line -> db line_no / CTN ===")
    for ev in item_events:
        cl = ev.get("csv_line")
        idx = ev.get("item_index")
        print(
            f"  CSV line {cl}: item_index={idx} line_no={idx + 1} "
            f"marks={ev.get('marks')!r} total_cartons={ev.get('total_cartons')} section={ev.get('section')}"
        )
    print()

    print("=== Parser trace sample (first 40 events) ===")
    for ev in trace[:40]:
        print(json.dumps(ev, ensure_ascii=False))
    if len(trace) > 40:
        print(f"... ({len(trace) - 40} more in JSON file)")
    print()

    # Shipped null cartons from expected
    issues = [
        (i, r)
        for i, r in enumerate(expected, start=1)
        if (r.get("section") == "shipped" and r.get("total_cartons") is None)
    ]
    if issues:
        print("=== Shipped rows with total_cartons NULL (often empty T.CTN in export) ===")
        for line_no, r in issues:
            print(f"  line_no={line_no} marks={r.get('marks')!r} qty={r.get('total_quantity')}")
        print()

    db_rows: list[dict[str, Any]] | None = None
    doc_id = args.document_id
    try:
        from sales_import.supabase_client import get_supabase

        sb = get_supabase()
        if not doc_id:
            q = (
                sb.table("documents")
                .select("id")
                .eq("source_file_name", path.name)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if q.data:
                doc_id = q.data[0]["id"]
        if doc_id:
            ins = (
                sb.table("document_items")
                .select(",".join(_CMP_FIELDS))
                .eq("document_id", doc_id)
                .order("line_no")
                .execute()
            )
            db_rows = ins.data or []
            print(f"=== DB document_id={doc_id} document_items: {len(db_rows)} rows ===")
        else:
            print("=== DB: no document found for this filename (import once or pass --document-id) ===")
    except RuntimeError as e:
        print(f"=== DB skipped ({e}) ===")

    if db_rows is not None:
        if len(db_rows) != len(expected):
            print(f"COUNT MISMATCH: db={len(db_rows)} expected={len(expected)}")
        n = min(len(db_rows), len(expected))
        mismatches = 0
        for i in range(n):
            a, b = db_rows[i], expected[i]
            diffs: list[str] = []
            for k in _CMP_FIELDS:
                av, bv = a.get(k), b.get(k)
                if av != bv and not _float_equal(av, bv):
                    diffs.append(f"{k}: db={av!r} csv={bv!r}")
            if diffs:
                mismatches += 1
                print(f"--- line_no {a.get('line_no')} ---")
                for d in diffs:
                    print(f"  {d}")
        if mismatches == 0 and len(db_rows) == len(expected):
            print("All compared fields match for every line_no.")
        elif mismatches:
            print(f"Total lines with field differences: {mismatches}")
        if len(db_rows) != len(expected):
            print("Fix count mismatch before trusting row-by-row alignment.")

    return 0


def _float_equal(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return abs(fa - fb) <= 1e-6 * max(1.0, abs(fa), abs(fb))


if __name__ == "__main__":
    raise SystemExit(main())
