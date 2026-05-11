from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from datetime import datetime, timezone

from dotenv import load_dotenv
from reducto import Reducto

from sales_import.excel_import import load_sales_lines_from_csv, load_sales_lines_from_xlsx
from sales_import.footer_align import align_rows_to_footer
from sales_import.footer_enrich import enrich_footer_totals
from sales_import.line_normalize import normalize_line_items
from sales_import.map_rows import extraction_row_to_document_item, merge_line_items_from_result_parts
from sales_import.parse_tables import parse_response_to_html_tables, parse_sales_document_from_html_tables
from sales_import.schema import (
    SALES_EXTRACT_SCHEMA,
    SYSTEM_PROMPT,
    extract_settings,
    parsing_options,
)
from sales_import.supabase_client import get_supabase
from sales_import.totals_reconcile import (
    merge_footer_totals_from_parts,
    reconcile_totals,
    refresh_totals_for_document,
    sum_item_rows,
    upsert_document_totals_row,
)


env_loaded = False


def _ensure_dotenv() -> None:
    """Load .env once before reading IMPORT_* so mode matches extract_sales_pdf."""
    global env_loaded
    if not env_loaded:
        load_dotenv()
        env_loaded = True


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


def _import_reducto_mode() -> str:
    return os.environ.get("IMPORT_REDUCTO_MODE", "extract").strip().lower()


def extract_sales_pdf(
    path: Path,
) -> tuple[Any, list[dict[str, Any]], dict[str, Any] | None, int | None]:
    """Call Reducto. Fourth return is parse HTML table count when mode=parse, else None."""
    _ensure_dotenv()
    api_key = os.environ.get("REDUCTO_API_KEY")
    if not api_key:
        raise RuntimeError("Set REDUCTO_API_KEY in .env")

    client = Reducto(
        api_key=api_key,
        timeout=_timeout_seconds(),
    )
    upload = client.upload(file=path)

    if _import_reducto_mode() == "parse":
        po = parsing_options()
        parse_response = client.parse.run(
            input=upload.file_id,
            formatting=po.get("formatting") or {},
            settings={**(po.get("settings") or {}), **extract_settings()},
        )
        html_tables = parse_response_to_html_tables(parse_response)
        lines, footer = parse_sales_document_from_html_tables(html_tables)
        return parse_response, lines, footer, len(html_tables)

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
    return extract_response, lines, footer, None


def _serialize_extract_for_jsonb(extract_response: Any) -> dict[str, Any]:
    if hasattr(extract_response, "model_dump"):
        return extract_response.model_dump()
    return {"raw": str(extract_response)}


def _attach_dry_run_totals_summary(
    summary: dict[str, Any],
    raw_lines: list[dict[str, Any]],
    footer_totals: dict[str, Any] | None,
) -> None:
    """Set totals_match / totals_diff using SUM(lines) vs footer (no DB write)."""
    computed, skipped = sum_item_rows(raw_lines)
    match, diff = reconcile_totals(
        pdf_footer=footer_totals,
        computed=computed,
        sanity_skipped=skipped,
    )
    summary["totals_match"] = match
    summary["totals_diff"] = diff if diff else {}
    summary["computed_line_totals"] = {k: v for k, v in computed.items() if v is not None}
    sk = {k: v for k, v in skipped.items() if v}
    if sk:
        summary["computed_sanity_skipped_cells"] = sk


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_local_tabular_import(
    path: Path,
    *,
    write_db: bool,
    raw_lines: list[dict[str, Any]],
    footer_totals: dict[str, Any] | None,
    serializable: dict[str, Any],
    import_reducto_mode: str,
    parser_ver: str,
    pipe_ver: str,
    model_nm: str,
) -> dict[str, Any]:
    """Shared path for Excel / CSV: normalize, footer align, optional Supabase insert."""
    footer_totals = enrich_footer_totals(footer_totals)
    raw_lines = normalize_line_items(raw_lines)
    footer_align_meta = align_rows_to_footer(raw_lines, footer_totals)

    summary: dict[str, Any] = {
        "file": str(path),
        "import_reducto_mode": import_reducto_mode,
        "line_count": len(raw_lines),
        "parse_html_table_count": None,
        "footer_totals": footer_totals,
        "footer_align": footer_align_meta,
        "document_id": None,
        "items_inserted": 0,
        "totals_match": None,
        "totals_diff": None,
        "extract_summary": None,
    }

    if not write_db:
        summary["raw_lines_sample"] = raw_lines[:3]
        _attach_dry_run_totals_summary(summary, raw_lines, footer_totals)
        return summary

    sb = get_supabase()
    doc_row = {
        "document_type": "sales_order",
        "source_file_name": path.name,
        "source_file_path": str(path),
        "extraction_status": "review_needed",
        "parser_version": parser_ver,
        "import_pipeline_version": pipe_ver,
        "model_name": model_nm,
        "raw_extraction": serializable,
        "normalized_payload": {
            "line_items": raw_lines,
            "footer_totals": footer_totals,
            "footer_align": footer_align_meta,
        },
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


def run_import_csv(
    path: Path,
    *,
    write_db: bool = True,
) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    raw_lines, footer_totals = load_sales_lines_from_csv(path)
    serializable: dict[str, Any] = {
        "source": "csv",
        "path": str(path),
    }
    return _run_local_tabular_import(
        path,
        write_db=write_db,
        raw_lines=raw_lines,
        footer_totals=footer_totals,
        serializable=serializable,
        import_reducto_mode="csv",
        parser_ver="csv-stdlib-v1",
        pipe_ver="v1-csv-import",
        model_nm="csv-upload",
    )


def run_import_excel(
    path: Path,
    *,
    write_db: bool = True,
    sheet_name: str | None = None,
) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    raw_lines, footer_totals = load_sales_lines_from_xlsx(path, sheet_name=sheet_name)
    serializable: dict[str, Any] = {
        "source": "excel",
        "path": str(path),
        "sheet": sheet_name or (os.environ.get("EXCEL_SHEET", "").strip() or None),
    }
    return _run_local_tabular_import(
        path,
        write_db=write_db,
        raw_lines=raw_lines,
        footer_totals=footer_totals,
        serializable=serializable,
        import_reducto_mode="excel",
        parser_ver="excel-openpyxl-v1",
        pipe_ver="v1-excel-import",
        model_nm="excel-upload",
    )


def run_import(
    path: Path,
    *,
    write_db: bool = True,
    excel_sheet: str | None = None,
) -> dict[str, Any]:
    _ensure_dotenv()
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    suf = path.suffix.lower()
    if suf in (".xlsx", ".xlsm"):
        return run_import_excel(path, write_db=write_db, sheet_name=excel_sheet)
    if suf == ".csv":
        return run_import_csv(path, write_db=write_db)

    mode = _import_reducto_mode()
    extract_response, raw_lines, footer_totals, parse_table_count = extract_sales_pdf(path)
    footer_totals = enrich_footer_totals(footer_totals)
    raw_lines = normalize_line_items(raw_lines)
    footer_align_meta = align_rows_to_footer(raw_lines, footer_totals)
    serializable = _serialize_extract_for_jsonb(extract_response)

    summary: dict[str, Any] = {
        "file": str(path),
        "import_reducto_mode": mode,
        "line_count": len(raw_lines),
        "parse_html_table_count": parse_table_count,
        "footer_totals": footer_totals,
        "footer_align": footer_align_meta,
        "document_id": None,
        "items_inserted": 0,
        "totals_match": None,
        "totals_diff": None,
    }

    if not write_db:
        summary["raw_lines_sample"] = raw_lines[:3]
        summary["extract_summary"] = serializable.get("usage")
        _attach_dry_run_totals_summary(summary, raw_lines, footer_totals)
        return summary

    sb = get_supabase()
    parser_ver = (
        "reducto-parse-html-v1"
        if mode == "parse"
        else "reducto-python-v7-cbm-dim-repair"
    )
    pipe_ver = (
        "v7-reducto-parse-html"
        if mode == "parse"
        else "v7-extract-cbm-dim-repair"
    )
    model_nm = "reducto-parse" if mode == "parse" else "reducto-extract"
    doc_row = {
        "document_type": "sales_order",
        "source_file_name": path.name,
        "source_file_path": str(path),
        "extraction_status": "review_needed",
        "parser_version": parser_ver,
        "import_pipeline_version": pipe_ver,
        "model_name": model_nm,
        "raw_extraction": serializable,
        "normalized_payload": {
            "line_items": raw_lines,
            "footer_totals": footer_totals,
            "footer_align": footer_align_meta,
        },
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

    p = argparse.ArgumentParser(
        description="Import sales packing list from PDF (Reducto), Excel (.xlsx), or CSV into Supabase"
    )
    p.add_argument(
        "pdf",
        type=Path,
        nargs="?",
        help="Path to sales PDF, Excel .xlsx / .xlsm, or .csv",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write to Supabase (PDF: still calls Reducto; Excel: local read only)",
    )
    p.add_argument(
        "--parse",
        action="store_true",
        help="Use Reducto /parse (HTML tables) instead of /extract; or set IMPORT_REDUCTO_MODE=parse",
    )
    p.add_argument(
        "--sheet",
        metavar="NAME",
        default=None,
        help="Excel worksheet name (default: first sheet, or EXCEL_SHEET in .env)",
    )
    p.add_argument(
        "--reconcile-document",
        metavar="UUID",
        help="Re-sum document_items vs footer_totals in raw_extraction; upsert document_totals",
    )
    args = p.parse_args()
    _ensure_dotenv()
    if args.parse:
        os.environ["IMPORT_REDUCTO_MODE"] = "parse"
    if args.reconcile_document:
        _ensure_dotenv()
        sb = get_supabase()
        out = refresh_totals_for_document(sb, args.reconcile_document)
        print(json.dumps(out, indent=2, default=str))
        return
    if not args.pdf:
        p.error("pdf path is required unless you pass --reconcile-document UUID")
    out = run_import(args.pdf, write_db=not args.dry_run, excel_sheet=args.sheet)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main_cli()
