"""Section banners, yellow bar subtotals, and document footer rows (container manifests).

Aligned with kato/inventory detection ideas — capture only; DB insert stays the sales pipeline."""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import quote

ManifestSection = Literal["shipped", "left_in_warehouse", "repacked", "other"]

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
    # Compound banners like "NEW ORDER-MS-4 GOODS LEFT BEHIND" are ambiguous —
    # let the unclassified-banner fallback capture those instead of guessing.
    if re.search(r"\bGOODS\b|\bLEFT\b", joined, re.I):
        return False
    remainder = re.sub(r"\bNEW\s+ORDERS?\b", "", joined, flags=re.I)
    remainder = re.sub(r"[\s.:—\-_/]", "", remainder).strip()
    # Allow a trailing order/lot code, e.g. "NEW ORDER-MS-4" -> remainder "MS4".
    return remainder == "" or bool(re.fullmatch(r"[A-Za-z0-9]+", remainder))


def classify_left_behind_now_status(raw: str) -> ManifestSection | None:
    """Generic "LEFT BEHIND GOODS FROM <ref> NOW/NOT LOADED" banners.

    Container refs vary per shipment (37-T-1, MS-3, MS-T-2, ...) so this matches on the
    NOW/NOT LOADED qualifier rather than an exact phrase list. NOW LOADED means those
    goods are loaded onto *this* shipment (shipped); NOT LOADED means still in the
    warehouse.
    """
    u = (raw or "").upper()
    if not re.search(r"\bLEFT\s+BEHIND\b", u) and not re.search(r"\bGOODS\s+LEFT\b", u):
        return None
    if re.search(r"\bNOT\s+LOADED\b", u):
        return "left_in_warehouse"
    if re.search(r"\bLOADED\b", u):
        return "shipped"
    return None


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


def looks_like_section_banner(raw: str) -> bool:
    """Heading-shaped text vs. a stray cell (a bare number, a single word, ...).

    Mirrors `isUnknownSectionBanner` in kato/inventory/lib/sections.ts (same
    classification problem on the PDF-extraction side of this project): a real
    banner is alpha-heavy, has no product/measurement tokens, and is a short
    phrase rather than a single token or a long sentence.
    """
    text = re.sub(r"\s+", " ", (raw or "").strip())
    if not text:
        return False
    upper = text.upper()
    if not re.search(r"[A-Z一-鿿]", upper):
        return False
    if re.search(r"\b(PCS/CTN|CTNS?|CBM|KGS?|USD|RMB)\b", upper):
        return False
    word_count = len(upper.split())
    return 2 <= word_count <= 18


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
    loaded_status = classify_left_behind_now_status(t)
    if loaded_status is not None:
        return loaded_status, t
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
        usd = extract_usd_amount(flat, anchor="TOTAL COST")
        if usd is not None:
            footer["total_amount_usd"] = usd
    if re.search(r"\bEXCHANGE\s+RATE\b", uc):
        rate = _f(r"EXCHANGE\s+RATE\s*[:\s,]*([\d.]+)", re.I)
        if rate is None:
            rate = _f(r"([\d.]+)\s*RMB\s*/\s*USD", re.I)
        if rate is not None:
            footer["exchange_rate"] = rate

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


def extract_usd_amount(text: str, *, anchor: str | None = None) -> float | None:
    """First positive USD amount in text (optionally after an anchor phrase)."""
    flat = re.sub(r"\s+", " ", (text or "").strip())
    if not flat:
        return None
    window = flat
    if anchor:
        idx = flat.upper().find(anchor.upper())
        if idx < 0:
            return None
        window = flat[idx : idx + 160]
    for pat in (
        r"\$\s*([\d,]+(?:\.\d+)?)",
        r"USD\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d+)?)",
    ):
        m = re.search(pat, window, re.I)
        if not m:
            continue
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if v > 0:
            return v
    return None


def _payment_label_from_line(flat: str) -> str:
    """Short label for document_payments.note from a footer/payment CSV row."""
    uc = flat.upper()
    if re.search(r"YIWU.{0,12}MOMBASA.{0,12}FREIGHT", flat, re.I):
        return "YIWU-MOMBASA FREIGHT"
    if re.search(r"CREDIT\s+SUPPORT", uc):
        return "CREDIT SUPPORT TO MOMBASA"
    if re.search(r"\bTOTAL\s+BALANCE\b", uc):
        return "TOTAL BALANCE"
    if re.search(r"GOODS\s+BALANCE", uc):
        return "GOODS BALANCE"
    if re.search(r"CHANGE\s+CONSIGNEE", uc):
        m = re.match(r"^([^,$\d]+)", flat)
        return (m.group(1).strip() if m else flat)[:80]
    if re.search(r"\bPIVOC\b", uc):
        return "PIVOC"
    if re.search(r"REPACK", uc):
        return "REPACKING"
    if re.search(r"\bPAYMENT\b", uc):
        return "PAYMENT"
    m = re.match(r"^([A-Za-z][A-Za-z0-9\s./\-]{2,60})", flat)
    return (m.group(1).strip() if m else flat)[:80]


def parse_manifest_payment_line(row_text: str) -> dict[str, Any] | None:
    """
    One USD payment/fee line from a container-manifest footer block.
    Returns {amount_usd, payment_type, note, payment_date?} or None.
    """
    flat = re.sub(r"\s+", " ", (row_text or "").strip())
    if not flat:
        return None
    uc = flat.upper()
    if re.search(r"\bTOTAL\s+(WEIGHT|CBM|CARTON|COST)\b", uc):
        return None
    if re.search(
        r"IF\s+OUTSTANDING\s+BALANCE|PAYMENT\s+DELAY\s+SURCHARGE|"
        r"BALANCE\s+PAYMENT\s+TERMS|VESSEL\s+ARRIVAL|REDUCE\s+DETAILS|REDUCE\s+\d+\s*CTN",
        uc,
    ):
        return None

    amount = extract_usd_amount(flat)
    if amount is None:
        return None

    note = _payment_label_from_line(flat)
    payment_type = "other"
    if re.search(r"YIWU.{0,12}MOMBASA.{0,12}FREIGHT", flat, re.I):
        payment_type = "freight"
    elif re.search(r"CREDIT\s+SUPPORT", uc):
        payment_type = "credit"
    elif re.search(r"\bTOTAL\s+BALANCE\b", uc):
        payment_type = "balance"
    elif re.search(r"\bPAYMENT\b", uc) and re.search(
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b|\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b", flat
    ):
        payment_type = "deposit"

    payment_date: str | None = None
    dm = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", flat)
    if dm:
        payment_date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
    else:
        dm2 = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", flat)
        if dm2:
            payment_date = (
                f"{dm2.group(3)}-{int(dm2.group(2)):02d}-{int(dm2.group(1)):02d}"
            )

    return {
        "amount_usd": amount,
        "payment_type": payment_type,
        "note": note,
        "payment_date": payment_date,
    }


def append_manifest_payment(
    payments: list[dict[str, Any]], row_text: str
) -> None:
    """Parse one footer row; append if new (dedupe by note + amount)."""
    parsed = parse_manifest_payment_line(row_text)
    if not parsed:
        return
    key = (parsed.get("note"), parsed.get("amount_usd"))
    if any((p.get("note"), p.get("amount_usd")) == key for p in payments):
        return
    payments.append(parsed)


def finalize_footer_totals(footer: dict[str, Any]) -> dict[str, Any] | None:
    if not footer:
        return None
    if all(footer.get(k) in (None, "", []) for k in ("total_cartons", "total_cbm", "total_weight_kg", "total_amount_rmb", "total_quantity")):
        return None
    return footer
