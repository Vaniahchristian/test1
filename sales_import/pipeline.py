from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from reducto import Reducto

from sales_import.map_rows import extraction_row_to_document_item, merge_line_items_from_result_parts
from sales_import.schema import (
    SALES_EXTRACT_SCHEMA,
    SYSTEM_PROMPT,
    extract_settings,
    parsing_options,
)
from sales_import.supabase_client import get_supabase
from sales_import.totals_reconcile import (
    merge_footer_totals_from_parts,
    refresh_totals_for_document,
    upsert_document_totals_row,
)


def _result_chunks_to_dicts(result: Any) -> list[dict[str, Any]]:
    """Normalize Reducto `result` (list or single dict / object)."""
    if result is None:
        return []
    if isinstance(result, list):
        chunks = result
    else:
        chunks = [result]
    out: list[dict[str, Any]] = []
    for c in chunks:
        if hasattr(c, "model_dump"):
            out.append(c.model_dump())
        elif isinstance(c, dict):
            out.append(c)
        else:
            out.append(dict(c))  # type: ignore[arg-type]
    return out


def _timeout_seconds() -> float:
    ms = int(os.environ.get("IMPORT_EXTRACT_TIMEOUT_MS", "420000"))
    return max(30.0, ms / 1000.0)


def extract_sales_pdf(path: Path) -> tuple[Any, list[dict[str, Any]], dict[str, Any] | None]:
    load_dotenv()
    api_key = os.environ.get("REDUCTO_API_KEY")
    if not api_key:
        raise RuntimeError("Set REDUCTO_API_KEY in .env")

    client = Reducto(
        api_key=api_key,
        timeout=_timeout_seconds(),
    )
    upload = client.upload(file=path)
    extract_response = client.extract.run(
        input=upload.file_id,
        instructions={
            "schema": SALES_EXTRACT_SCHEMA,
            "system_prompt": SYSTEM_PROMPT,
        },
        parsing=parsing_options(),
        settings=extract_settings(),
    )

    parts = _result_chunks_to_dicts(extract_response.result)
    lines = merge_line_items_from_result_parts(parts)
    footer = merge_footer_totals_from_parts(parts)
    return extract_response, lines, footer


def _serialize_extract_for_jsonb(extract_response: Any) -> dict[str, Any]:
    if hasattr(extract_response, "model_dump"):
        return extract_response.model_dump()
    return {"raw": str(extract_response)}


from datetime import datetime, timezone

def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_import(
    path: Path,
    *,
    write_db: bool = True,
) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    extract_response, raw_lines, footer_totals = extract_sales_pdf(path)
    serializable = _serialize_extract_for_jsonb(extract_response)

    summary: dict[str, Any] = {
        "file": str(path),
        "line_count": len(raw_lines),
        "footer_totals": footer_totals,
        "document_id": None,
        "items_inserted": 0,
        "totals_match": None,
        "totals_diff": None,
    }

    if not write_db:
        summary["raw_lines_sample"] = raw_lines[:3]
        summary["extract_summary"] = serializable.get("usage")
        return summary

    sb = get_supabase()
    doc_row = {
        "document_type": "sales_order",
        "source_file_name": path.name,
        "source_file_path": str(path),
        "extraction_status": "review_needed",
        "parser_version": "reducto-python-v1",
        "model_name": "reducto-extract",
        "raw_extraction": serializable,
        "normalized_payload": {"line_items": raw_lines, "footer_totals": footer_totals},
    }
    doc_ins = sb.table("documents").insert(doc_row).execute()
    if not doc_ins.data:
        raise RuntimeError(f"documents insert failed: {doc_ins}")
    document_id = doc_ins.data[0]["id"]

    item_rows: list[dict[str, Any]] = []
    for i, raw in enumerate(raw_lines, start=1):
        item_rows.append(
            extraction_row_to_document_item(
                raw,
                document_id=document_id,
                db_line_no=i,
            )
        )

    if item_rows:
        ins = sb.table("document_items").insert(item_rows).execute()
        summary["items_inserted"] = len(ins.data or item_rows)
    else:
        summary["items_inserted"] = 0

    summary["document_id"] = document_id

    reconciled = upsert_document_totals_row(
        sb,
        document_id=document_id,
        pdf_footer=footer_totals,
        item_rows=item_rows,
    )
    summary["totals_match"] = reconciled["totals_match"]
    summary["totals_diff"] = reconciled["totals_diff"]

    sb.table("documents").update(
        {
            "validation_flags": {
                "totals_match": reconciled["totals_match"],
                "totals_reconciled_at": _utc_iso(),
            },
            "updated_at": _utc_iso(),
        }
    ).eq("id", document_id).execute()

    return summary


def main_cli() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Reducto sales PDF -> Supabase documents + document_items")
    p.add_argument("pdf", type=Path, nargs="?", help="Path to sales/packing PDF")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Call Reducto only; do not write to Supabase",
    )
    p.add_argument(
        "--reconcile-document",
        metavar="UUID",
        help="Re-sum document_items vs footer_totals in raw_extraction; upsert document_totals",
    )
    args = p.parse_args()
    if args.reconcile_document:
        sb = get_supabase()
        out = refresh_totals_for_document(sb, args.reconcile_document)
        print(json.dumps(out, indent=2, default=str))
        return
    if not args.pdf:
        p.error("pdf path is required unless you pass --reconcile-document UUID")
    out = run_import(args.pdf, write_db=not args.dry_run)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main_cli()
