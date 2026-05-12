"""Section banners, yellow bar subtotals, and document footer rows (container manifests).

Aligned with kato/inventory detection ideas — capture only; DB insert stays the sales pipeline."""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import quote

ManifestSection = Literal["shipped", "left_in_warehouse", "repacked"]

SECTION_LABEL_PREFIX = "section_label:"


def encode_section_label_remark(label: str) -> str:
    t = re.sub(r"\s+", " ", (label or "").strip())
    if not t:
        return ""
    return f"{SECTION_LABEL_PREFIX}{quote(t, safe='')}"


def append_section_label(remarks: str | None, banner_label: str | None) -> str | None:
    tag = encode_section_label_remark(banner_label or "")
    if not tag:
        return remarks
    base = (remarks or "").strip()
    if base and tag in base:
        return base or None
    if base:
        return f"{base}; {tag}"[:2000]
    return tag[:2000]


def should_persist_section_label(banner_label: str | None) -> bool:
    """Do not tag every line with NEW ORDER — it is the default table block only."""
    if not banner_label or not str(banner_label).strip():
        return False
    return not is_new_order_section_header(banner_label)


def is_new_order_section_header(raw: str) -> bool:
    joined = re.sub(r"\s+", " ", (raw or "").strip())
    if not joined or not re.search(r"\bNEW\s+ORDERS?\b", joined, re.I):
        return False
    remainder = re.sub(r"\bNEW\s+ORDERS?\b", "", joined, flags=re.I)
    remainder = re.sub(r"[\s.:—\-_]", "", remainder).strip()
    return len(remainder) == 0


def is_before_goods_header(raw: str) -> bool:
    u = re.sub(r"\s+", " ", (raw or "").strip().upper())
    return bool(u) and bool(re.search(r"\bBEFORE\s+GOODS\b", u))


def is_goods_left_header(raw: str) -> bool:
    u = (raw or "").upper()
    compact = re.sub(r"\s+", "", u)
    if "GOODSLEFTINSANCARGO" in compact:
        return True
    if re.search(r"\bGOODS\s+LEFT\b", raw or "", re.I) and re.search(r"\bSANCARGO\b", raw or "", re.I):
        return True
    if re.search(r"\bLEFT\s+IN\s+WAREHOUSE\b", u, re.I):
        return True
    if re.search(r"\bGOODS\s+LEFT\s+IN\s+WAREHOUSE\b", u, re.I):
        return True
    return False


def is_stuffed_container_header(raw: str) -> bool:
    u = (raw or "").upper()
    normalized = re.sub(r"\s+", " ", u).strip()
    compact = re.sub(r"[^A-Z0-9]", "", normalized)
    return (
        "GOODS STUFFED INTO THIS CONTAINER" in u
        or "GOODS STUFFED INTO THIS CO" in u
        or "STUFFED INTO THIS CONTAINER" in u
        or "STUFFED INTO THIS CO" in u
        or "GOODSHASBEENSTUFFEDINTOTHISCO" in compact
        or "GOODSHASBEENSTUFFEDINTOTHISCONTAINER" in compact
    )


def is_repackaged_section_header(raw: str) -> bool:
    if not is_stuffed_container_header(raw):
        return False
    u = (raw or "").upper()
    return bool(
        re.search(r"REPACKAGED\s+GOODS", u)
        or re.search(r"REPACKED\s+GOODS", u)
        or re.search(r"\bREPACK(?:ED|AGED)?\b", u)
    )


def classify_section_banner(row_text: str) -> tuple[ManifestSection | None, str | None]:
    """
    If row is a known section delimiter, return (new_section, label_for_remarks).
    Otherwise (None, None).
    """
    t = re.sub(r"\s+", " ", (row_text or "").strip())
    if not t:
        return None, None
    if is_new_order_section_header(t):
        return "shipped", t
    if is_before_goods_header(t):
        return "left_in_warehouse", t
    if is_goods_left_header(t):
        return "left_in_warehouse", t
    if is_repackaged_section_header(t):
        return "repacked", t
    if is_stuffed_container_header(t):
        return "shipped", t
    return None, None


def is_likely_yellow_subtotal_row(row_text: str) -> bool:
    """Rolled CTN+CBM+KGS+¥ bar without a real product line (kato-style)."""
    joined = re.sub(r"\s+", " ", (row_text or "").strip())
    if not joined:
        return False
    if re.search(r"\bSUB\s*TOTAL\b|\bSECTION\s+TOTAL\b", joined, re.I):
        return True
    if re.search(r"\d+\s*pcs/ctn", joined, re.I):
        return False
    has_ctn = bool(re.search(r"\b\d+\s*CTNS?\b", joined, re.I) or re.search(r"\b\d+CTNS\b", joined, re.I))
    has_cbm = bool(re.search(r"\b\d+\.?\d*\s*CBM\b", joined, re.I) or re.search(r"\d+\.?\d*CBM\b", joined, re.I))
    has_money = bool(re.search(r"[¥￥]\s*[\d,]+", joined))
    has_kgs = bool(
        re.search(r"\b\d+\.?\d*\s*KGS?\b", joined, re.I)
        or re.search(r"\d+\.?\d*KGS?\b", joined, re.I)
        or re.search(r"\d+\.?\d*GS\b", joined, re.I)
    )
    signals = sum(1 for x in (has_ctn, has_cbm, has_money, has_kgs) if x)
    if signals < 3:
        return False
    alpha_chunks = re.sub(r"[\d.,¥￥\s/:%\-]", " ", joined)
    words = [w for w in alpha_chunks.split() if re.match(r"^[A-Za-z]{3,}", w)]
    if len(words) >= 5:
        return False
    return True


def extract_yellow_subtotal_metrics(row_text: str) -> dict[str, float | None]:
    joined = re.sub(r"\s+", " ", (row_text or "").replace(",", ""))
    out: dict[str, float | None] = {
        "total_cartons": None,
        "total_cbm": None,
        "total_weight_kg": None,
        "total_amount_rmb": None,
    }

    def _f(m: re.Match[str] | None, g: int = 1) -> float | None:
        if not m:
            return None
        try:
            return float(m.group(g).replace(",", ""))
        except (ValueError, IndexError):
            return None

    out["total_cartons"] = _f(re.search(r"(\d+(?:\.\d+)?)\s*CTNS?\b", joined, re.I))
    out["total_cbm"] = _f(re.search(r"(\d+(?:\.\d+)?)\s*CBM\b", joined, re.I))
    out["total_weight_kg"] = _f(re.search(r"(\d+(?:\.\d+)?)\s*KGS?\b", joined, re.I))
    m = re.search(r"[¥￥]\s*([\d,]+(?:\.\d+)?)", joined)
    out["total_amount_rmb"] = _f(m, 1) if m else None
    return out


def is_document_footer_row(row_text: str) -> bool:
    combined = re.sub(r"\s+", " ", (row_text or "").strip()).upper()
    if not combined:
        return False
    if re.search(r"\bTOTAL\s+(WEIGHT|CBM|CARTON|COST|BALANCE)\b", combined):
        return True
    if re.search(r"\b(GOODS\s+BALANCE|CREDIT\s+SUPPORT|PIVOC|EXCHANGE\s+RATE)\b", combined):
        return True
    if re.search(r"YIWU.{0,12}MOMBASA.{0,12}FREIGHT", row_text or "", re.I):
        return True
    if re.search(r"\bPAYMENT\b", combined) and "USD" in combined and not re.search(r"pcs/ctn", combined, re.I):
        return True
    if re.search(r"BALANCE\s+PAYMENT\s+TERMS", combined):
        return True
    if re.search(r"IF\s+OUTSTANDING\s+BALANCE", combined):
        return True
    if re.search(r"PAYMENT\s+DELAY\s+SURCHARGE", combined):
        return True
    if re.search(r"VESSEL\s+ARRIVAL\s+MOMBASA", combined):
        return True
    if re.search(r"REDUCE\s+DETAILS", combined):
        return True
    if re.search(r"REDUCE\s+\d+\s*CTN", combined):
        return True
    if re.search(r"\bCURRENT\s+BALANCE\b", combined):
        return True
    return False


def merge_footer_from_line(footer: dict[str, Any], row_text: str) -> None:
    """Merge one footer / payment line into cumulative footer_totals (in-place)."""
    flat = re.sub(r"\s+", " ", (row_text or "").strip())
    uc = flat.upper()

    def _f(pat: str, flags: int = 0, group: int = 1) -> float | None:
        m = re.search(pat, flat, flags)
        if not m:
            return None
        try:
            return float(m.group(group).replace(",", ""))
        except (ValueError, IndexError):
            return None

    # Sparse CSV/Excel: "TOTAL WEIGHT ,,, 13027 , KGS , ..."
    if re.search(r"\bTOTAL\s+WEIGHT\b", uc):
        v = _f(r"TOTAL\s+WEIGHT.*?([\d,]+(?:\.\d+)?)\s*KGS", re.I)
        if v is None:
            v = _f(r"TOTAL\s+WEIGHT\s*[:\s,]*([\d,]+(?:\.\d+)?)", re.I)
        if v is not None:
            footer["total_weight_kg"] = v
    if re.search(r"\bTOTAL\s+CBM\b", uc):
        v = _f(r"TOTAL\s+CBM.*?([\d.,]+)\s*CBM", re.I)
        if v is None:
            v = _f(r"TOTAL\s+CBM\s*[:\s,]*([\d.,]+)", re.I)
        if v is not None:
            footer["total_cbm"] = v
    if re.search(r"\bTOTAL\s+CARTONS?\b", uc) or re.search(r"\bTOTAL\s+CARTON\b", uc):
        v = _f(r"TOTAL\s+CARTONS?.*?([\d,]+)\s*CTN", re.I)
        if v is None:
            v = _f(r"TOTAL\s+CARTON.*?([\d,]+)\s*CTN", re.I)
        if v is None:
            v = _f(r"(?:TOTAL\s+CARTONS?|TOTAL\s+CARTON)\s*[:\s,]*([\d,]+)", re.I)
        if v is not None:
            footer["total_cartons"] = v
    if re.search(r"\bTOTAL\s+COST\b", uc):
        v = _f(r"TOTAL\s+COST.{0,160}?[¥￥\uffe5]\s*([\d,]+(?:\.\d+)?)", re.I)
        if v is None:
            v = _f(r"TOTAL\s+COST[^\d¥￥\uffe5]{0,80}?[¥￥\uffe5]\s*([\d,]+(?:\.\d+)?)", re.I)
        if v is None:
            v = _f(r"[¥￥\uffe5]\s*([\d,]+(?:\.\d+)?)")
        if v is not None:
            footer["total_amount_rmb"] = v

    parts: list[str] = []
    if footer.get("total_cartons") is not None:
        parts.append(str(footer["total_cartons"]))
    if footer.get("total_quantity") is not None:
        parts.append(str(footer["total_quantity"]))
    if footer.get("total_amount_rmb") is not None:
        parts.append(str(footer["total_amount_rmb"]))
    if footer.get("total_cbm") is not None:
        parts.append(str(footer["total_cbm"]))
    if footer.get("total_weight_kg") is not None:
        parts.append(str(footer["total_weight_kg"]))
    if parts:
        footer["footer_text"] = "TOTAL: " + " ".join(parts)


def finalize_footer_totals(footer: dict[str, Any]) -> dict[str, Any] | None:
    if not footer:
        return None
    if all(footer.get(k) in (None, "", []) for k in ("total_cartons", "total_cbm", "total_weight_kg", "total_amount_rmb", "total_quantity")):
        return None
    return footer
