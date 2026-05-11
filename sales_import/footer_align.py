"""Optional proportional scaling of line columns toward PDF footer totals.

Default is **off**: line items stay **PDF-exact** after extraction + `line_normalize` only.
Totals are validated (not rewritten) via `totals_reconcile` → `document_totals`.

Opt-in scaling: set **FOOTER_ALIGN_PROPORTIONAL=1** (legacy; distorts per-line CTN/QTY/U/P).
"""

from __future__ import annotations

import os
from typing import Any

from sales_import.line_normalize import resync_total_quantity_from_ctn_qpc


def _proportional_enabled() -> bool:
    return os.environ.get("FOOTER_ALIGN_PROPORTIONAL", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _fit_ctn_qty_enabled() -> bool:
    return os.environ.get("FOOTER_ALIGN_FIT_CTN_QTY", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _fit_max_rel_gap() -> float:
    return float(os.environ.get("FOOTER_ALIGN_FIT_MAX_REL_GAP", "0.22"))

# Do NOT scale total_quantity by default: it is T.QTY and must stay CTN×QTY for display.
# Scaling it broke per-line QTY vs the PDF. Opt-in: FOOTER_ALIGN_SCALE_TOTAL_QUANTITY=1
def _keys_to_scale() -> tuple[str, ...]:
    if os.environ.get("FOOTER_ALIGN_SCALE_TOTAL_QUANTITY", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return (
            "total_quantity",
            "total_cartons",
            "total_cbm",
            "total_weight_kg",
            "total_amount_rmb",
        )
    return (
        "total_cartons",
        "total_cbm",
        "total_weight_kg",
        "total_amount_rmb",
    )


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sum_positive(rows: list[dict[str, Any]], key: str) -> float:
    return sum(x for x in (_f(r.get(key)) for r in rows) if x is not None and x > 0)


def fit_ctn_qty_rows_to_footer(
    rows: list[dict[str, Any]],
    footer: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Scale **total_cartons** and **qty_per_carton** on each line so that, after
    resync, SUM(CTN) and SUM(T.QTY) match **footer_totals**, while keeping
    T.QTY = CTN × QTY per row.

    Use when HTML parse systematically under-reads CTN (e.g. merged cells) but
    footer totals are trusted. Does **not** change amounts, CBM, or weight.

    Opt-in: **FOOTER_ALIGN_FIT_CTN_QTY=1**
    """
    if not footer:
        return {"applied": False, "reason": "no_footer"}
    tgt_ctn = _f(footer.get("total_cartons"))
    tgt_tq = _f(footer.get("total_quantity"))
    if tgt_ctn is None or tgt_ctn <= 0 or tgt_tq is None or tgt_tq <= 0:
        return {"applied": False, "reason": "footer_missing_ctn_or_qty"}

    s_ctn = 0.0
    s_tq = 0.0
    n_fit = 0
    for r in rows:
        c = _f(r.get("total_cartons"))
        q = _f(r.get("qty_per_carton"))
        t = _f(r.get("total_quantity"))
        if c is not None and c > 0 and q is not None and q > 0:
            s_ctn += c
            if t is not None and t > 0:
                s_tq += t
            else:
                s_tq += c * q
            n_fit += 1

    if s_ctn <= 0 or s_tq <= 0 or n_fit < 1:
        return {"applied": False, "reason": "no_positive_ctn_qty_rows", "rows_considered": n_fit}

    rel_ctn = abs(s_ctn - tgt_ctn) / tgt_ctn
    if rel_ctn > _fit_max_rel_gap():
        return {
            "applied": False,
            "reason": "ctn_gap_exceeds_FOOTER_ALIGN_FIT_MAX_REL_GAP",
            "sum_cartons": s_ctn,
            "target_cartons": tgt_ctn,
            "relative_gap": round(rel_ctn, 4),
        }

    alpha = tgt_ctn / s_ctn
    denom = alpha * s_tq
    if denom <= 0:
        return {"applied": False, "reason": "zero_denom_for_beta"}
    beta = tgt_tq / denom

    for r in rows:
        c = _f(r.get("total_cartons"))
        q = _f(r.get("qty_per_carton"))
        if c is None or c <= 0 or q is None or q <= 0:
            continue
        r["total_cartons"] = round(c * alpha, 4)
        r["qty_per_carton"] = round(q * beta, 4)
        r["total_quantity"] = round(c * alpha * q * beta, 4)
        fl = r.setdefault("_normalize_flags", [])
        if isinstance(fl, list) and "footer_fit_ctn_qty" not in fl:
            fl.append("footer_fit_ctn_qty")

    resync_total_quantity_from_ctn_qpc(rows)

    s_ctn2 = _sum_positive(rows, "total_cartons")
    s_tq2 = _sum_positive(rows, "total_quantity")
    return {
        "applied": True,
        "alpha": round(alpha, 6),
        "beta": round(beta, 6),
        "rows_scaled": n_fit,
        "sum_cartons_before": round(s_ctn, 4),
        "sum_quantity_before": round(s_tq, 4),
        "sum_cartons_after": round(s_ctn2, 4),
        "sum_quantity_after": round(s_tq2, 4),
        "targets": {"total_cartons": tgt_ctn, "total_quantity": tgt_tq},
    }


def _scale_column(rows: list[dict[str, Any]], key: str, target: float) -> bool:
    cur = _sum_positive(rows, key)
    if cur <= 0 or target <= 0:
        return False
    # Optional safety: skip scale if gap is huge (disabled by default for noisy OCR)
    max_gap = float(os.environ.get("FOOTER_ALIGN_MAX_REL_GAP", "999"))
    if max_gap < 100 and abs(cur - target) / target > max_gap:
        return False
    factor = target / cur
    for r in rows:
        v = _f(r.get(key))
        if v is None or v <= 0:
            continue
        if key == "total_amount_rmb":
            r[key] = round(v * factor, 2)
        elif key in ("total_cbm", "total_weight_kg"):
            r[key] = round(v * factor, 4)
        else:
            r[key] = round(v * factor, 6)
        fl = r.setdefault("_normalize_flags", [])
        if isinstance(fl, list):
            tag = f"footer_scaled:{key}"
            if tag not in fl:
                fl.append(tag)
    return True


def _rederive_unit_price(rows: list[dict[str, Any]]) -> None:
    for r in rows:
        fl = r.get("_normalize_flags")
        if not isinstance(fl, list) or not any(
            isinstance(x, str) and x.startswith("footer_scaled:") for x in fl
        ):
            continue
        amt = _f(r.get("total_amount_rmb"))
        tq = _f(r.get("total_quantity"))
        if amt is None or tq is None or tq <= 0:
            continue
        r["unit_price_rmb"] = round(amt / tq, 4)
        if "implied_unit_price_after_footer" not in fl:
            fl.append("implied_unit_price_after_footer")


def align_rows_to_footer(
    rows: list[dict[str, Any]],
    footer: dict[str, Any] | None,
) -> dict[str, Any]:
    if _fit_ctn_qty_enabled():
        fit_meta = fit_ctn_qty_rows_to_footer(rows, footer)
        if fit_meta.get("applied"):
            return {
                "scaled": ["total_cartons", "qty_per_carton", "total_quantity"],
                "proportional_scaling": False,
                "footer_fit_ctn_qty": True,
                "note": "CTN and T.QTY scaled to match PDF footer; per-row T.QTY = CTN × QTY. Amounts/CBM/kg unchanged.",
                **fit_meta,
            }
        return {
            "scaled": [],
            "proportional_scaling": False,
            "footer_fit_ctn_qty": False,
            "note": "FOOTER_ALIGN_FIT_CTN_QTY=1 but fit was skipped; see fit_skip_reason.",
            "fit_skip_reason": fit_meta,
        }

    if not _proportional_enabled():
        return {
            "scaled": [],
            "proportional_scaling": False,
            "note": "Line sums = source cells; no footer scaling. Set FOOTER_ALIGN_FIT_CTN_QTY=1 for CTN+T.QTY fit, or FOOTER_ALIGN_PROPORTIONAL=1 for legacy column scale.",
        }
    if not footer:
        return {"scaled": [], "proportional_scaling": True}

    targets: dict[str, float | None] = {
        "total_quantity": _f(footer.get("total_quantity")),
        "total_cartons": _f(footer.get("total_cartons")),
        "total_cbm": _f(footer.get("total_cbm")),
        "total_weight_kg": _f(footer.get("total_weight_kg")),
        "total_amount_rmb": _f(footer.get("total_amount_rmb")),
    }

    scaled: list[str] = []
    for k in _keys_to_scale():
        t = targets.get(k)
        if t is not None and t > 0 and _scale_column(rows, k, t):
            scaled.append(k)

    # Footer may scale CTN; keep line T.QTY consistent before amount → unit price.
    resync_total_quantity_from_ctn_qpc(rows)
    _rederive_unit_price(rows)
    return {
        "scaled": scaled,
        "proportional_scaling": True,
        "targets_applied": {k: v for k, v in targets.items() if v},
    }
