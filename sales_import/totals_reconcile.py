from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from supabase import Client

# Keys shared by PDF footer and SUM(document_items.*)
TOTAL_KEYS = (
    "total_cartons",
    "total_quantity",
    "total_cbm",
    "total_weight_kg",
    "total_amount_rmb",
)

# Ignore per-line outliers when summing (mis-OCR / column bleed). Tune via env if needed.
_MAX_PLAUSIBLE_PER_LINE: dict[str, float] = {
    "total_cartons": float(os.environ.get("TOTALS_MAX_LINE_CARTONS", "20000")),
    "total_quantity": float(os.environ.get("TOTALS_MAX_LINE_QTY", "200000")),
    "total_cbm": float(os.environ.get("TOTALS_MAX_LINE_CBM", "100")),
    "total_weight_kg": float(os.environ.get("TOTALS_MAX_LINE_KG", "50000")),
    "total_amount_rmb": float(os.environ.get("TOTALS_MAX_LINE_RMB", "1e7")),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def merge_footer_totals_from_parts(parts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Merge non-null fields from `footer_totals` across extract chunks."""
    merged: dict[str, Any] = {}
    for p in parts:
        ft = p.get("footer_totals")
        if not isinstance(ft, dict):
            continue
        for k, v in ft.items():
            if v is None or v == "":
                continue
            merged[k] = v
    return merged or None


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def sum_item_rows(
    item_rows: list[dict[str, Any]],
) -> tuple[dict[str, float | None], dict[str, int]]:
    """Sum numeric columns; skip per-cell values outside plausible ranges (OCR bleed)."""
    out: dict[str, float | None] = {k: None for k in TOTAL_KEYS}
    skipped: dict[str, int] = {k: 0 for k in TOTAL_KEYS}
    for key in TOTAL_KEYS:
        cap = _MAX_PLAUSIBLE_PER_LINE.get(key, float("inf"))
        s = 0.0
        n = 0
        for row in item_rows:
            fv = _to_float(row.get(key))
            if fv is None:
                continue
            if fv < 0 or abs(fv) > cap:
                skipped[key] += 1
                continue
            s += fv
            n += 1
        out[key] = s if n else None
    return out, skipped


def _tolerance_for(key: str) -> tuple[float, float]:
    """Absolute tolerance; relative (fraction of PDF value) for large numbers."""
    env_abs = {
        "total_cartons": float(os.environ.get("TOTALS_TOL_CARTONS", "1")),
        "total_quantity": float(os.environ.get("TOTALS_TOL_QTY", "2")),
        "total_cbm": float(os.environ.get("TOTALS_TOL_CBM", "0.05")),
        "total_weight_kg": float(os.environ.get("TOTALS_TOL_KG", "5")),
        "total_amount_rmb": float(os.environ.get("TOTALS_TOL_RMB", "5")),
    }
    rtol = float(os.environ.get("TOTALS_REL_TOL", "0.002"))  # 0.2%
    abs_tol = env_abs.get(key, 1.0)
    return abs_tol, rtol


def _within_tol(pdf_v: float, computed_v: float, key: str) -> bool:
    abs_tol, rtol = _tolerance_for(key)
    delta = abs(computed_v - pdf_v)
    if delta <= abs_tol:
        return True
    if pdf_v != 0 and delta / abs(pdf_v) <= rtol:
        return True
    return False


def reconcile_totals(
    *,
    pdf_footer: dict[str, Any] | None,
    computed: dict[str, float | None],
    sanity_skipped: dict[str, int] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """
    Compare PDF footer numbers to summed line items.
    If pdf_footer is missing or empty, totals_match is False.
    """
    diff: dict[str, Any] = {}
    if sanity_skipped and any(v > 0 for v in sanity_skipped.values()):
        diff["_sanity_skipped_line_cells"] = {k: v for k, v in sanity_skipped.items() if v > 0}
    if not pdf_footer:
        base = {
            "_note": "no_pdf_footer_totals_extracted",
            "computed": {k: v for k, v in computed.items() if v is not None},
        }
        if diff:
            base.update(diff)
        return False, base

    match = True
    compared_any = False
    for key in TOTAL_KEYS:
        pdf_raw = pdf_footer.get(key)
        pdf_v = _to_float(pdf_raw)
        comp_v = computed.get(key)
        if pdf_v is None:
            continue
        compared_any = True
        if comp_v is None:
            diff[key] = {"pdf": pdf_v, "computed": None, "issue": "computed_sum_missing"}
            match = False
            continue
        if not _within_tol(pdf_v, comp_v, key):
            diff[key] = {"pdf": pdf_v, "computed": comp_v, "delta": round(comp_v - pdf_v, 6)}
            match = False

    if not compared_any:
        out_diff: dict[str, Any] = {
            "_note": "pdf_footer_had_no_numeric_totals",
            "footer_keys": list(pdf_footer.keys()),
            "computed": {k: v for k, v in computed.items() if v is not None},
        }
        out_diff.update(diff)
        return False, out_diff

    return match, diff if diff else {}


def upsert_document_totals_row(
    sb: Client,
    *,
    document_id: str,
    pdf_footer: dict[str, Any] | None,
    item_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    computed, skipped = sum_item_rows(item_rows)
    totals_match, totals_diff = reconcile_totals(
        pdf_footer=pdf_footer,
        computed=computed,
        sanity_skipped=skipped,
    )

    row: dict[str, Any] = {
        "document_id": document_id,
        "total_cartons": _to_float(pdf_footer.get("total_cartons")) if pdf_footer else None,
        "total_quantity": _to_float(pdf_footer.get("total_quantity")) if pdf_footer else None,
        "total_cbm": _to_float(pdf_footer.get("total_cbm")) if pdf_footer else None,
        "total_weight_kg": _to_float(pdf_footer.get("total_weight_kg")) if pdf_footer else None,
        "total_amount_rmb": _to_float(pdf_footer.get("total_amount_rmb")) if pdf_footer else None,
        "computed_cartons": computed["total_cartons"],
        "computed_quantity": computed["total_quantity"],
        "computed_cbm": computed["total_cbm"],
        "computed_weight_kg": computed["total_weight_kg"],
        "computed_amount_rmb": computed["total_amount_rmb"],
        "totals_match": totals_match,
        "totals_diff": totals_diff if totals_diff else {},
        "updated_at": _now_iso(),
    }
    sb.table("document_totals").upsert(row, on_conflict="document_id").execute()
    return {
        "totals_match": totals_match,
        "totals_diff": totals_diff,
        "computed": computed,
        "sanity_skipped_cells": {k: v for k, v in skipped.items() if v > 0},
    }


def refresh_totals_for_document(sb: Client, document_id: str) -> dict[str, Any]:
    """Re-sum document_items and re-read footer_totals from documents.raw_extraction."""
    doc = (
        sb.table("documents")
        .select("raw_extraction,normalized_payload")
        .eq("id", document_id)
        .limit(1)
        .execute()
    )
    if not doc.data:
        raise ValueError(f"document not found: {document_id}")
    raw = (doc.data[0] or {}).get("raw_extraction") or {}
    norm = (doc.data[0] or {}).get("normalized_payload") or {}
    if isinstance(norm, str):
        norm = {}
    parts: list[dict[str, Any]] = []
    res = raw.get("result")
    if isinstance(res, list):
        for chunk in res:
            if isinstance(chunk, dict):
                parts.append(chunk)
    elif isinstance(res, dict):
        parts.append(res)

    footer = merge_footer_totals_from_parts(parts)
    if not footer and isinstance(norm, dict):
        ft = norm.get("footer_totals")
        if isinstance(ft, dict) and ft:
            footer = ft
    items = (
        sb.table("document_items")
        .select(",".join(TOTAL_KEYS))
        .eq("document_id", document_id)
        .execute()
    )
    rows = items.data or []
    out = upsert_document_totals_row(sb, document_id=document_id, pdf_footer=footer, item_rows=rows)
    sb.table("documents").update(
        {
            "validation_flags": {
                "totals_match": out["totals_match"],
                "totals_reconciled_at": _now_iso(),
            },
            "updated_at": _now_iso(),
        }
    ).eq("id", document_id).execute()
    return out
