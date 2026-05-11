from __future__ import annotations

import re
from typing import Any

# Columns we send to public.document_items (no id — DB default).
_DOCUMENT_ITEM_KEYS = frozenset(
    {
        "document_id",
        "line_no",
        "source_item_no",
        "item_code",
        "description",
        "shop",
        "packaging",
        "qty_per_carton",
        "total_cartons",
        "total_quantity",
        "unit_price_rmb",
        "total_amount_rmb",
        "dim_l_cm",
        "dim_w_cm",
        "dim_h_cm",
        "unit_cbm",
        "total_cbm",
        "unit_weight_kg",
        "total_weight_kg",
        "warehouse",
        "barcode",
        "remarks",
        "extraction_confidence",
        "source_page",
        "validation_flags",
        "photo",
        "photo2",
        "code",
        "model_source",
        "section",
        "delivery_no",
        "customer_item_ref",
        "unit",
        "product_name_local",
        "material",
        "source_cells",
    }
)


def _optional_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _optional_int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    s = str(v).strip()
    m = re.match(r"^(\d+)", s)
    if m:
        return int(m.group(1))
    try:
        return int(float(s.replace(",", "")))
    except ValueError:
        return None


def _pdf_row_label_for_source(raw: dict[str, Any]) -> str | None:
    """NO. / 编号 from the PDF (may repeat each page); stored in source_item_no."""
    for key in ("line_no", "no", "seq", "row_no"):
        v = raw.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def extraction_row_to_document_item(
    raw: dict[str, Any],
    *,
    document_id: str,
    db_line_no: int,
) -> dict[str, Any]:
    # DB enforces UNIQUE(document_id, line_no). Extraction often restarts NO. on each page.
    line_no = db_line_no
    source_item_no = _pdf_row_label_for_source(raw)

    row: dict[str, Any] = {
        "document_id": document_id,
        "line_no": line_no,
        "item_code": (raw.get("item_code") or "").strip() or None,
        "description": (raw.get("description") or "").strip() or None,
        "delivery_no": (raw.get("delivery_no") or "").strip() or None,
        "customer_item_ref": (raw.get("customer_item_ref") or "").strip() or None,
        "photo": (raw.get("photo_url") or "").strip() or None,
        "total_cartons": _optional_float(raw.get("total_cartons")),
        "qty_per_carton": _optional_float(raw.get("qty_per_carton")),
        "total_quantity": _optional_float(raw.get("total_quantity")),
        "unit_price_rmb": _optional_float(raw.get("unit_price_rmb")),
        "total_amount_rmb": _optional_float(raw.get("total_amount_rmb")),
        "dim_l_cm": _optional_float(raw.get("dim_l_cm")),
        "dim_w_cm": _optional_float(raw.get("dim_w_cm")),
        "dim_h_cm": _optional_float(raw.get("dim_h_cm")),
        "unit_cbm": _optional_float(raw.get("unit_cbm")),
        "unit_weight_kg": _optional_float(raw.get("unit_weight_kg")),
        "total_cbm": _optional_float(raw.get("total_cbm")),
        "total_weight_kg": _optional_float(raw.get("total_weight_kg")),
        "barcode": (raw.get("barcode") or "").strip() or None,
        "remarks": (raw.get("remarks") or "").strip() or None,
        "warehouse": (raw.get("warehouse") or "").strip() or None,
        "unit": (raw.get("unit") or "").strip() or None,
        "product_name_local": (raw.get("product_name_local") or "").strip() or None,
        "material": (raw.get("material") or "").strip() or None,
        "source_page": _optional_int(raw.get("source_page")),
        "extraction_confidence": _optional_int(raw.get("extraction_confidence")) or 0,
        "validation_flags": raw.get("validation_flags") if isinstance(raw.get("validation_flags"), list) else [],
        "model_source": "reducto",
        "section": "shipped",
        "source_cells": raw.get("source_cells") if isinstance(raw.get("source_cells"), dict) else {},
    }
    if source_item_no is not None:
        row["source_item_no"] = source_item_no

    return {k: v for k, v in row.items() if k in _DOCUMENT_ITEM_KEYS}


def merge_line_items_from_result_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collect line_items arrays from each extract chunk."""
    out: list[dict[str, Any]] = []
    for part in parts:
        items = part.get("line_items")
        if not items:
            continue
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    out.append(it)
    return out
