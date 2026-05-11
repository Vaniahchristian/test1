from __future__ import annotations

import copy
import os
import re
from typing import Any

# Max cartons on a single line for this doc style (tunable).
_MAX_LINE_CARTONS = float(os.environ.get("LINE_MAX_CARTONS", "600"))
_MAX_LINE_QPC = float(os.environ.get("LINE_MAX_QTY_PER_CARTON", "5000"))
# Cartons above this are almost always OCR column bleed (e.g. 252); factorize from T.QTY.
_MAX_SANE_CTN = float(os.environ.get("LINE_MAX_SANE_CTN", "120"))
# Prefer swapping CTN vs QPC when OCR puts a small pack count in QPC and a multiple in CTN.
_SWAP_OCR_PACK = os.environ.get("LINE_SWAP_OCR_PACK_COLUMNS", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Common QPC values — slight scoring bonus when choosing factor pairs.
_COMMON_QPC = frozenset({5, 6, 8, 9, 10, 12, 15, 16, 18, 20, 24, 30, 36, 48, 72, 96})
# When CTN and QPC are both missing, factorize only for larger T.QTY (avoids 1×20 on small lines).
_FACTOR_BOTH_MISSING_MIN_TQ = float(os.environ.get("LINE_FACTORIZE_BOTH_MISSING_MIN_TQ", "24"))
# If U/P×T.QTY would exceed this, the "unit price" may be the printed line amount and T.QTY junk (e.g. ZYB226).
_MAX_SANE_LINE_AMOUNT_RMB = float(os.environ.get("LINE_MAX_SANE_LINE_AMOUNT_RMB", "60000"))
_REINT_UP_AS_AMT_MIN = float(os.environ.get("LINE_REINTERPRET_AS_AMOUNT_UP_MIN", "30"))
_REINT_UP_AS_AMT_MAX = float(os.environ.get("LINE_REINTERPRET_AS_AMOUNT_UP_MAX", "20000"))
_REINT_BIG_QTY_THRESHOLD = float(os.environ.get("LINE_REINTERPRET_BIG_QTY_THRESHOLD", "1500"))
_REINT_MIN_UNIT_PRICE_FOR_BIG_QTY = float(
    os.environ.get("LINE_REINTERPRET_MIN_UNIT_PRICE_FOR_BIG_QTY", "120")
)
# total_kg / T.QTY below this with non-trivial total kg ⇒ T.QTY almost certainly wrong (column bleed).
_REINT_IMPLAUSIBLE_KG_PER_QTY = float(
    os.environ.get("LINE_REINTERPRET_IMPLAUSIBLE_KG_PER_QTY", "0.04")
)


def _f(v: Any) -> float | None:
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


def _merge_forward_fill(lines: list[dict[str, Any]]) -> None:
    """Fill vertically merged DEL NO / CUS NO from previous row (in-place)."""
    last_del: str | None = None
    last_cus: str | None = None
    for row in lines:
        d = (row.get("delivery_no") or "").strip() if row.get("delivery_no") else ""
        if d:
            last_del = d
        elif last_del:
            row["delivery_no"] = last_del
        c = (row.get("customer_item_ref") or "").strip() if row.get("customer_item_ref") else ""
        if c:
            last_cus = c
        elif last_cus:
            row["customer_item_ref"] = last_cus


def _flags(row: dict[str, Any]) -> list[str]:
    if "_normalize_flags" not in row:
        row["_normalize_flags"] = []
    return row["_normalize_flags"]


def _clean_stripped_string_fields(row: dict[str, Any], fl: list[str]) -> None:
    """Strip table noise (pipes, control chars) from short text columns."""
    for key in ("item_code", "material", "product_name_local", "description"):
        v = row.get(key)
        if not isinstance(v, str):
            continue
        s = "".join(ch for ch in v.strip() if ord(ch) >= 32)
        s = s.replace("|", "").strip()
        if s != v.strip():
            fl.append(f"cleaned_field:{key}")
        row[key] = s or None


def _score_factor_pair(ctn: int, qpc: int, tq_line: int | None = None) -> float:
    if ctn < 1 or qpc < 1:
        return 1e9
    s = 0.0
    if ctn > 80:
        s += (ctn - 80) * 2.5
    if ctn > 0:
        ratio = qpc / ctn
        if ratio > 5:
            s += (ratio - 5) ** 1.35
        s += ratio * 0.08
    if ctn > qpc * 4:
        s += (ctn - qpc * 4) * 0.4
    if qpc < 4:
        s += (4 - qpc) * 2.0
    if qpc > 200:
        s += (qpc - 200) * 0.5
    if qpc in _COMMON_QPC:
        s -= 2.0
    if qpc in (12, 24, 36):
        s -= 0.12
    if tq_line is not None and tq_line >= 96 and qpc == 24:
        s -= 0.42
    if ctn <= qpc:
        s -= 0.95
    return s


def _factorize_repair_ctn_qpc(row: dict[str, Any], fl: list[str]) -> None:
    """Replace absurd CTN×QPC with best divisor pair of T.QTY (handles DH4344, 1966-08, etc.)."""
    tq = _f(row.get("total_quantity"))
    if tq is None or tq <= 0:
        return
    if abs(tq - round(tq)) > 0.05:
        return
    ti = int(round(tq))
    ctn = _f(row.get("total_cartons"))
    qpc = _f(row.get("qty_per_carton"))

    absurd = False
    if ctn is not None and ctn > _MAX_SANE_CTN:
        absurd = True
    elif qpc is not None and qpc > 0 and (qpc < 0.8 or qpc > _MAX_LINE_QPC):
        absurd = True
    elif (
        qpc is not None
        and 0 < qpc < 100
        and abs(qpc - round(qpc)) > 0.08
    ):
        # e.g. 2.1818 from misread "24" — product may still nearly match T.QTY
        absurd = True
    elif ctn is not None and qpc is not None and ctn > 0 and qpc > 0:
        prod = ctn * qpc
        if prod > 0 and abs(prod - tq) / tq > 0.06:
            absurd = True
    elif (
        (ctn is None or ctn <= 0)
        and (qpc is None or qpc <= 0)
        and tq is not None
        and tq >= _FACTOR_BOTH_MISSING_MIN_TQ
    ):
        absurd = True
    if not absurd:
        return

    best: tuple[int, int] | None = None
    best_score = 1e18
    for a in range(1, min(ti, int(_MAX_SANE_CTN) + 1)):
        if ti % a != 0:
            continue
        b = ti // a
        if b > _MAX_LINE_QPC or b < 1:
            continue
        for ctn_g, qpc_g in ((a, b), (b, a)):
            if ctn_g > _MAX_SANE_CTN or qpc_g > _MAX_LINE_QPC or ctn_g < 1 or qpc_g < 1:
                continue
            sc = _score_factor_pair(int(ctn_g), int(qpc_g), ti)
            if sc < best_score:
                best_score = sc
                best = (int(ctn_g), int(qpc_g))
    if best:
        row["total_cartons"] = best[0]
        row["qty_per_carton"] = best[1]
        fl.append("ctn_qpc_repaired_by_tqty_factorization")


def _maybe_swap_small_qpc_big_ctn(row: dict[str, Any], fl: list[str]) -> None:
    """When OCR puts 12 in CTN and 5 in QPC but PDF is 5×12 (KP007 style)."""
    if not _SWAP_OCR_PACK:
        return
    # Excel/CSV are structured — small QPC (e.g. 2 pcs/carton) is often intentional; do not swap.
    src = (row.get("_parse_source") or "").strip().lower()
    if src in ("csv", "excel"):
        return
    ctn = _f(row.get("total_cartons"))
    qpc = _f(row.get("qty_per_carton"))
    tq = _f(row.get("total_quantity"))
    if ctn is None or qpc is None or tq is None:
        return
    if ctn <= 0 or qpc <= 0:
        return
    if abs(ctn * qpc - tq) / tq > 0.02:
        return
    if qpc > 5:
        return
    if ctn < 10 or ctn > 48:
        return
    if qpc >= ctn:
        return
    row["total_cartons"] = int(qpc)
    row["qty_per_carton"] = int(ctn)
    fl.append("swapped_ctn_qpc_ocr_small_pack")


def _infer_total_quantity_from_amount(row: dict[str, Any], fl: list[str]) -> None:
    tq = _f(row.get("total_quantity"))
    if tq is not None and tq > 0:
        return
    amt = _f(row.get("total_amount_rmb"))
    up = _f(row.get("unit_price_rmb"))
    if amt is None or up is None or up <= 0:
        return
    guess = amt / up
    if guess <= 0:
        return
    row["total_quantity"] = int(round(guess)) if abs(guess - round(guess)) < 0.06 else round(guess, 4)
    fl.append("total_quantity_inferred_from_amount_div_unit")


def _maybe_reinterpret_unit_price_as_line_amount(row: dict[str, Any], fl: list[str]) -> None:
    """When OCR leaves amount empty but U/P×T.QTY is huge, U/P may be the line total (ZYB226-style)."""
    up = _f(row.get("unit_price_rmb"))
    amt = _f(row.get("total_amount_rmb"))
    tq = _f(row.get("total_quantity"))
    tkg = _f(row.get("total_weight_kg"))
    if amt is not None or up is None or tq is None or tq <= 0 or up <= 0:
        return
    if not (_REINT_UP_AS_AMT_MIN <= up <= _REINT_UP_AS_AMT_MAX):
        return
    prod = up * tq
    if prod <= _MAX_SANE_LINE_AMOUNT_RMB:
        return
    density = tkg / tq if tkg is not None and tkg > 0 else None
    implausible_weight = (
        density is not None
        and tkg is not None
        and tkg >= 2.5
        and density < _REINT_IMPLAUSIBLE_KG_PER_QTY
    )
    big_qty_suspect_unit_col = (
        up > _REINT_MIN_UNIT_PRICE_FOR_BIG_QTY and tq >= _REINT_BIG_QTY_THRESHOLD
    )
    if not (implausible_weight or big_qty_suspect_unit_col):
        return
    row["total_amount_rmb"] = round(up, 2)
    row["unit_price_rmb"] = None
    row["total_quantity"] = None
    row["total_cartons"] = None
    row["qty_per_carton"] = None
    row["total_cbm"] = None
    fl.append("unit_price_reinterpreted_as_line_amount_cleared_pack")


def _prefer_unit_price_matching_amount(row: dict[str, Any], fl: list[str]) -> None:
    """Trust line AMOUNT from the PDF over doubled/wrong U/P when inconsistent (e.g. ZYB227)."""
    amt = _f(row.get("total_amount_rmb"))
    tq = _f(row.get("total_quantity"))
    up = _f(row.get("unit_price_rmb"))
    if amt is None or tq is None or tq <= 0:
        return
    implied = amt / tq
    if implied <= 0 or implied > 1e6:
        return
    if up is None:
        row["unit_price_rmb"] = round(implied, 4)
        fl.append("unit_price_inferred_from_amount_div_qty")
        return
    rel_amt_err = abs(up * tq - amt) / max(amt, 0.01)
    if rel_amt_err <= 0.02:
        return
    if abs(implied - up) / max(up, 0.01) > 0.06:
        row["unit_price_rmb"] = round(implied, 4)
        fl.append("unit_price_repaired_to_match_line_amount")


def _clean_warehouse_and_unit_row(row: dict[str, Any], fl: list[str]) -> None:
    wh_raw = row.get("warehouse")
    un_raw = row.get("unit")
    wh = wh_raw if isinstance(wh_raw, str) else ""
    un = un_raw if isinstance(un_raw, str) else ""

    w = "".join(ch for ch in wh.strip() if ord(ch) >= 32)
    w = re.sub(r"[\u0000-\u001f].*$", "", w)
    w = w.replace("|", " ").strip()
    w = re.sub(r"\s+", " ", w)

    if re.search(r"ed3$", w, re.I) or w.lower() in ("ed3", "12ed3"):
        w = "刀叉勺"
        fl.append("warehouse_garbage_mapped_to_cutlery_zone")

    unit_keywords = {
        "PCS": "PCS",
        "SET": "SET",
        "DCS": "DCS",
        "PS": "PS",
        "pcs": "pcs",
        "set": "set",
    }
    if w.upper() in ("IPCS",):
        w = "浙江仓"
        if not un.strip():
            row["unit"] = "PCS"
            fl.append("unit_from_warehouse_ipcs_bleed")
        fl.append("warehouse_ipcs_corrected")

    if w in unit_keywords or w.upper() in ("PCS", "SET", "DCS", "PS"):
        if not un.strip():
            row["unit"] = unit_keywords.get(w, w.upper() if w.upper() in ("PCS", "SET", "DCS") else w)
            fl.append("unit_from_warehouse_unit_bleed")
        w = ""
    elif w.upper() in ("SET",) and len(w) <= 5:
        if not un.strip():
            row["unit"] = "SET"
        w = ""
    elif w in ("I", "II", "III", "【"):
        w = "浙江仓"
        fl.append("warehouse_truncated_i_to_zhejiang")
    elif w == "江":
        w = "浙江仓"
    elif w == "3" or w == "三仓":
        w = "3仓"

    for pref in ("浙江仓", "东阳仓", "浦江仓"):
        if w.startswith(pref) and w != pref:
            tail = w[len(pref) :].strip()
            if tail in ("8", "") or tail.upper() in ("PCS", "SET"):
                if tail.upper() in ("PCS", "SET") and not un.strip():
                    row["unit"] = tail.upper()
                    fl.append("unit_suffix_moved_from_warehouse")
                w = pref

    upper_w = w.upper()
    for prov in ("浙江", "东阳", "浦江"):
        if upper_w.startswith(prov) and upper_w.endswith("PCS"):
            base = w[: -3].strip()
            if not un.strip():
                row["unit"] = "PCS"
            w = base if base.endswith("仓") else base
            fl.append("warehouse_stripped_trailing_pcs")
            break

    row["warehouse"] = w if w else None


def _forward_fill_warehouse(lines: list[dict[str, Any]]) -> None:
    last: str | None = None
    for row in lines:
        w = row.get("warehouse")
        if isinstance(w, str) and w.strip():
            last = w.strip()
        elif last:
            row["warehouse"] = last
            fl = _flags(row)
            fl.append("warehouse_forward_filled")


def _normalize_one_line(row: dict[str, Any]) -> None:
    """Repair numeric consistency for packing-list math (in-place)."""
    fl = _flags(row)
    _clean_stripped_string_fields(row, fl)

    _infer_total_quantity_from_amount(row, fl)

    qpc_chk = _f(row.get("qty_per_carton"))
    if qpc_chk is not None and qpc_chk > _MAX_LINE_QPC:
        row["qty_per_carton"] = None
        fl.append("nulled_insane_qty_per_carton")
    ctn = _f(row.get("total_cartons"))
    qpc = _f(row.get("qty_per_carton"))
    tq = _f(row.get("total_quantity"))
    up = _f(row.get("unit_price_rmb"))
    amt = _f(row.get("total_amount_rmb"))
    ucbm = _f(row.get("unit_cbm"))
    tcbm = _f(row.get("total_cbm"))
    ukg = _f(row.get("unit_weight_kg"))
    tkg = _f(row.get("total_weight_kg"))

    # --- Swap CTN vs QTY-per-carton when OCR clearly swapped (huge CTN) ---
    if (
        ctn is not None
        and qpc is not None
        and ctn > _MAX_LINE_CARTONS
        and qpc <= _MAX_LINE_CARTONS
        and qpc >= 1
        and ctn <= _MAX_LINE_QPC
    ):
        row["total_cartons"] = qpc
        row["qty_per_carton"] = ctn
        fl.append("swapped_carton_qty_per_carton")
        ctn, qpc = qpc, ctn

    # Cap absurd cartons (after swap)
    if ctn is not None and ctn > _MAX_LINE_CARTONS:
        row["total_cartons"] = None
        fl.append("nulled_oversized_cartons")
        ctn = None

    _factorize_repair_ctn_qpc(row, fl)
    ctn = _f(row.get("total_cartons"))
    qpc = _f(row.get("qty_per_carton"))
    tq = _f(row.get("total_quantity"))

    _maybe_swap_small_qpc_big_ctn(row, fl)
    ctn = _f(row.get("total_cartons"))
    qpc = _f(row.get("qty_per_carton"))

    # --- Infer QPC from T.QTY / CTN when empty or OCR garbage ---
    if ctn is not None and ctn > 0 and tq is not None and tq > 0:
        ratio = tq / ctn
        qpc_bad = (
            qpc is None
            or qpc == 0
            or qpc > _MAX_LINE_QPC
            or (
                qpc is not None
                and ratio > 0
                and ratio <= _MAX_LINE_QPC
                and abs(qpc - ratio) / max(ratio, 1e-9) > 0.12
            )
        )
        if qpc_bad and 0 < ratio <= _MAX_LINE_QPC:
            row["qty_per_carton"] = (
                int(round(ratio)) if abs(ratio - round(ratio)) < 0.05 else round(ratio, 4)
            )
            fl.append("qty_per_carton_inferred_from_tqty_ctn")
            qpc = _f(row.get("qty_per_carton"))

    # --- Infer CTN from T.QTY / QPC when cartons still missing ---
    if (ctn is None or ctn == 0) and qpc is not None and qpc > 0 and tq is not None and tq > 0:
        ratio_ctn = tq / qpc
        if 0 < ratio_ctn <= _MAX_LINE_CARTONS and abs(ratio_ctn - round(ratio_ctn)) < 0.051:
            row["total_cartons"] = int(round(ratio_ctn))
            fl.append("total_cartons_inferred_from_tqty_qpc")
            ctn = _f(row.get("total_cartons"))

    # --- Second pass factorization after infers (e.g. partial repair) ---
    _factorize_repair_ctn_qpc(row, fl)
    ctn = _f(row.get("total_cartons"))
    qpc = _f(row.get("qty_per_carton"))
    tq = _f(row.get("total_quantity"))

    # --- T.QTY = CTN * QPC ---
    if ctn is not None and qpc is not None:
        expected_tq = ctn * qpc
        if tq is None or (expected_tq > 0 and abs(tq - expected_tq) / expected_tq > 0.02):
            row["total_quantity"] = round(expected_tq, 6) if expected_tq % 1 else int(expected_tq)
            if tq is not None and abs(tq - expected_tq) / max(expected_tq, 1) > 0.02:
                fl.append("total_quantity_repaired_ctn_x_qpc")
            tq = _f(row.get("total_quantity"))

    # --- Repair unit CBM when merged columns put G.W. (kg) or other noise in m³ ---
    ucbm = _f(row.get("unit_cbm"))
    L = _f(row.get("dim_l_cm"))
    W = _f(row.get("dim_w_cm"))
    H = _f(row.get("dim_h_cm"))
    der_cbm = None
    if L and W and H and 3 < L < 400 and 3 < W < 400 and 3 < H < 400:
        der_cbm = round(L * W * H / 1e6, 4)
    if ucbm is not None and ucbm > 2.0:
        if der_cbm is not None:
            row["unit_cbm"] = der_cbm
            fl.append("unit_cbm_repaired_from_dims_absurd")
        else:
            row["unit_cbm"] = None
            fl.append("nulled_absurd_unit_cbm")
        ucbm = _f(row.get("unit_cbm"))
    elif ucbm is not None and der_cbm is not None and ucbm > 0.15 and der_cbm > 1e-9:
        if ucbm / der_cbm > 12:
            row["unit_cbm"] = der_cbm
            fl.append("unit_cbm_repaired_dims_ratio")
        ucbm = _f(row.get("unit_cbm"))

    tcbm = _f(row.get("total_cbm"))
    ctn = _f(row.get("total_cartons"))

    # --- TTL CBM = unit CBM * CTN ---
    if (tcbm is None or tcbm == 0) and ucbm is not None and ctn is not None and ctn > 0:
        row["total_cbm"] = round(ucbm * ctn, 4)
        fl.append("total_cbm_derived")
        tcbm = _f(row.get("total_cbm"))

    # --- Coherent TTL CBM vs unit CBM × CTN (column slip inflated line TTL) ---
    ucbm = _f(row.get("unit_cbm"))
    tcbm = _f(row.get("total_cbm"))
    ctn = _f(row.get("total_cartons"))
    if (
        tcbm is not None
        and ucbm is not None
        and ctn is not None
        and ctn > 0
        and ucbm > 0
        and tcbm > ucbm * ctn * 2.5 + 0.05
    ):
        row["total_cbm"] = round(ucbm * ctn, 4)
        fl.append("total_cbm_repaired_coherent_ucbm_x_ctn")
        tcbm = _f(row.get("total_cbm"))

    # --- Drop weights that are clearly a leaked m³ fragment (kg/m³ impossibly low) ---
    ucbm_u = _f(row.get("unit_cbm"))
    ukg_u = _f(row.get("unit_weight_kg"))
    if (
        ucbm_u is not None
        and ucbm_u > 0.005
        and ukg_u is not None
        and ukg_u > 0
        and (ukg_u / ucbm_u) < 15
    ):
        row["unit_weight_kg"] = None
        row["total_weight_kg"] = None
        fl.append("nulled_implausible_weight_vs_cbm")
    ukg = _f(row.get("unit_weight_kg"))
    tkg = _f(row.get("total_weight_kg"))
    ctn = _f(row.get("total_cartons"))

    # --- TTL KGS = unit weight * CTN ---
    if (tkg is None or tkg == 0) and ukg is not None and ctn is not None and ctn > 0:
        row["total_weight_kg"] = round(ukg * ctn, 4)
        fl.append("total_weight_kg_derived")
        tkg = _f(row.get("total_weight_kg"))

    # --- Prefer U/P consistent with printed line amount (do not overwrite PDF amount) ---
    tq = _f(row.get("total_quantity"))
    _prefer_unit_price_matching_amount(row, fl)
    _maybe_reinterpret_unit_price_as_line_amount(row, fl)

    up = _f(row.get("unit_price_rmb"))
    amt = _f(row.get("total_amount_rmb"))
    if amt is None and up is not None and tq is not None and tq > 0:
        row["total_amount_rmb"] = round(up * tq, 2)
        fl.append("total_amount_derived_unit_x_qty")

    _clean_warehouse_and_unit_row(row, fl)


def resync_total_quantity_from_ctn_qpc(rows: list[dict[str, Any]]) -> None:
    """After footer scaling of cartons, keep T.QTY = CTN × QTY (in-place)."""
    for row in rows:
        ctn = _f(row.get("total_cartons"))
        qpc = _f(row.get("qty_per_carton"))
        if ctn is None or qpc is None or ctn <= 0 or qpc <= 0:
            continue
        expected = ctn * qpc
        new_tq = int(expected) if abs(expected - round(expected)) < 1e-6 else round(expected, 6)
        old_tq = _f(row.get("total_quantity"))
        if old_tq is None or abs(old_tq - new_tq) > 1e-4:
            row["total_quantity"] = new_tq
            fl = _flags(row)
            if "total_quantity_resynced_ctn_x_qpc" not in fl:
                fl.append("total_quantity_resynced_ctn_x_qpc")


def normalize_line_items(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return new list with merged-cell fill and numeric repairs; sets _normalize_flags per row."""
    out = [copy.deepcopy(x) for x in lines]
    _merge_forward_fill(out)
    for row in out:
        _normalize_one_line(row)
    _forward_fill_warehouse(out)
    return out
