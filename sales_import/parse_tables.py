"""Convert Reducto /parse Table block HTML into line_items + footer_totals (same shape as Extract)."""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

from bs4 import BeautifulSoup


def _to_float(cell: str | None) -> float | None:
    if cell is None:
        return None
    s = str(cell).strip().replace(",", "")
    if not s or s in ("[ ]", "—", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _first_number_in_cell(s: str) -> float | None:
    """Use first numeric token (handles '10<br/>10' → 10)."""
    for part in re.split(r"[\s\n/]+", str(s).strip()):
        v = _to_float(part)
        if v is not None:
            return v
    return _to_float(s)


def _clean_text(s: str | None) -> str | None:
    if s is None:
        return None
    t = re.sub(r"\s+", " ", str(s)).strip()
    return t or None


def _fetch_parse_result_url(url: str, timeout: float = 180.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "sales-import/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("Parse URL payload is not a JSON object")
    return data


def _result_to_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if isinstance(result, dict):
        return result
    return dict(result)


def parse_response_to_html_tables(parse_response: Any) -> list[str]:
    """Pull HTML fragments from Table blocks; resolve URL result if needed."""
    data = parse_response.model_dump() if hasattr(parse_response, "model_dump") else dict(parse_response)
    result = data.get("result")
    if result is None:
        return []
    rd = _result_to_dict(result)
    if rd.get("type") == "url":
        url = rd.get("url")
        if not url:
            raise RuntimeError("Parse returned URL result but url is missing")
        rd = _fetch_parse_result_url(str(url))
    if rd.get("type") != "full":
        raise RuntimeError(f"Unexpected parse result type after resolve: {rd.get('type')!r}")

    out: list[str] = []
    for ch in rd.get("chunks") or []:
        chunk = ch if isinstance(ch, dict) else _result_to_dict(ch)
        for block in chunk.get("blocks") or []:
            b = block if isinstance(block, dict) else _result_to_dict(block)
            if b.get("type") != "Table":
                continue
            html = (b.get("content") or "").strip()
            if html:
                out.append(html)
    return out


def _is_header_row(cells: list[str]) -> bool:
    j = " ".join(cells).upper()
    if "DEL NO" in j or "送货单号" in j:
        return True
    if cells and str(cells[0]).strip().upper() in ("NO.", "NO"):
        return True
    return False


def _is_total_row(cells: list[str]) -> bool:
    j = " ".join(cells).upper()
    return "TOTAL" in j or "合计" in " ".join(cells)


def _is_junk_short_table(rows: list[list[str]]) -> bool:
    """Drop small legend / key tables (unit, 品名) at document end."""
    if not rows:
        return True
    maxw = max(len(r) for r in rows)
    if maxw <= 6 and len(rows) <= 15:
        return True
    return False


def _try_footer_from_row(cells: list[str]) -> dict[str, Any] | None:
    if not _is_total_row(cells):
        return None
    nums: list[float] = []
    for c in cells:
        for m in re.finditer(r"\d+(?:\.\d+)?", str(c).replace(",", "")):
            nums.append(float(m.group(0)))
    if len(nums) < 5:
        return None
    return {
        "total_cartons": nums[0],
        "total_quantity": nums[1],
        "total_amount_rmb": nums[2],
        "total_cbm": nums[3],
        "total_weight_kg": nums[4],
        "footer_text": " ".join(cells)[:800],
    }


def _is_data_row(cells: list[str]) -> bool:
    """Main grid rows: numeric NO., or delivery-no pattern, or wide numeric product row."""
    if len(cells) < 4:
        return False
    c0 = cells[0].strip()
    c1 = cells[1].strip() if len(cells) > 1 else ""
    if c0.isdigit():
        return True
    if re.match(r"^(26C405-|C25-)", c1):
        return True
    if len(cells) >= 11:
        c3 = cells[3].strip()
        if c3 and c3 not in ("[ ]",) and (
            _to_float(cells[8]) is not None or _to_float(cells[10]) is not None
        ):
            return bool(c1 or c3)
    return False


def _split_merged_dimension_cell(cell: str) -> list[str]:
    """Split '38 0.08' (H + m³) or '39.5 24.5' (L + W cm) when OCR merges adjacent columns."""
    s = (cell or "").strip()
    if not s or s.startswith("[") or len(s) > 60:
        return [cell]
    m = re.match(r"^(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)$", re.sub(r"\[\s*\]", "", s).strip())
    if not m:
        return [cell]
    a, b = float(m.group(1)), float(m.group(2))
    if 8 <= a <= 240 and 0.003 <= b <= 3.0:
        return [str(a), str(b)]
    if 8 <= a <= 240 and 8 <= b <= 240:
        a_out = int(a) if abs(a - round(a)) < 0.02 else a
        b_out = int(b) if abs(b - round(b)) < 0.02 else b
        return [str(a_out), str(b_out)]
    return [cell]


def expand_pack_row_cells(cells: list[str]) -> list[str]:
    """Re-split merged L/W and H+unit-CBM in columns 11–13 (0-based after NO…AMOUNT)."""
    if len(cells) < 12:
        return cells
    head = list(cells[:11])
    mid: list[str] = []
    for idx in range(11, min(14, len(cells))):
        for part in _split_merged_dimension_cell(cells[idx]):
            mid.append(part)
    tail = list(cells[14:]) if len(cells) > 14 else []
    return head + mid + tail


def _table_to_rows(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = []
        for cell in tr.find_all(["td", "th"]):
            cells.append(cell.get_text(separator=" ", strip=True))
        if cells:
            rows.append(cells)
    return rows


def _use_narrow_volume_tail(cells: list[str]) -> bool:
    """Merged HTML often drops the per-carton CBM column — G.W. lands in slot 14."""
    if len(cells) < 18:
        return False
    if len(cells) == 23:
        return True
    v14 = _first_number_in_cell(cells[14])
    v15 = _first_number_in_cell(cells[15]) if len(cells) > 15 else None
    if v14 is None:
        return False
    if v14 > 2.5 and v15 is not None and v15 < 2.0:
        return True
    return False


def _looks_like_packaging_unit(s: str) -> bool:
    t = s.strip().lower()
    if t in ("pcs", "set", "ps", "dcs", "pc", "件", "套"):
        return True
    if len(s) <= 5 and s.isascii() and s.replace(".", "").isalpha():
        return True
    return False


def _maybe_shift_missing_unit_column(row: dict[str, Any], cells: list[str], bi: int) -> None:
    """When merge_tables drops the UNIT cell, 品名 sits in the unit slot."""
    u = (row.get("unit") or "").strip()
    if not u or _looks_like_packaging_unit(u):
        return
    mat = _clean_text(cells[bi + 4]) if len(cells) > bi + 4 else None
    fl = _flags_cell_list(row)
    row["product_name_local"] = u
    row["material"] = mat
    row["unit"] = None
    fl.append("parse_shifted_missing_unit_column")


def _flags_cell_list(row: dict[str, Any]) -> list[str]:
    if "_normalize_flags" not in row:
        row["_normalize_flags"] = []
    return row["_normalize_flags"]


def _derive_unit_cbm_from_dims(row: dict[str, Any]) -> None:
    if row.get("unit_cbm") is not None:
        return
    L = _to_float(row.get("dim_l_cm"))
    W = _to_float(row.get("dim_w_cm"))
    H = _to_float(row.get("dim_h_cm"))
    if L and W and H and 3 < L < 400 and 3 < W < 400 and 3 < H < 400:
        row["unit_cbm"] = round(L * W * H / 1e6, 4)


def _cells_to_line_dict(cells: list[str], last_del: str | None, last_cus: str | None) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Map wide packing-list row to extract-shaped dict; update forward-fill refs."""
    if len(cells) < 11:
        return None, last_del, last_cus

    p = cells + [""] * 28
    line_no = _to_float(p[0])
    delivery = _clean_text(p[1])
    cus = _clean_text(p[2])
    item_code = _clean_text(p[3])

    if delivery:
        last_del = delivery
    elif last_del:
        delivery = last_del

    if cus:
        last_cus = cus
    elif last_cus:
        cus = last_cus

    if not item_code or item_code in ("[ ]",):
        return None, last_del, last_cus

    narrow_vol = _use_narrow_volume_tail(cells)
    if narrow_vol:
        u_cbm = None
        u_kg = _first_number_in_cell(p[14])
        t_cbm = _first_number_in_cell(p[15])
        t_kg = _first_number_in_cell(p[16])
        bi = 17
    else:
        u_cbm = _first_number_in_cell(p[14])
        u_kg = _first_number_in_cell(p[15])
        t_cbm = _first_number_in_cell(p[16])
        t_kg = _first_number_in_cell(p[17])
        bi = 18

    row: dict[str, Any] = {
        "line_no": int(line_no) if line_no is not None and abs(line_no - round(line_no)) < 0.01 else None,
        "delivery_no": delivery,
        "customer_item_ref": cus,
        "item_code": item_code,
        "description": _clean_text(p[5]),
        "total_cartons": _first_number_in_cell(p[6]),
        "qty_per_carton": _first_number_in_cell(p[7]),
        "total_quantity": _first_number_in_cell(p[8]),
        "unit_price_rmb": _first_number_in_cell(p[9]),
        "total_amount_rmb": _first_number_in_cell(p[10]),
        "dim_l_cm": _first_number_in_cell(p[11]),
        "dim_w_cm": _first_number_in_cell(p[12]),
        "dim_h_cm": _first_number_in_cell(p[13]),
        "unit_cbm": u_cbm,
        "unit_weight_kg": u_kg,
        "total_cbm": t_cbm,
        "total_weight_kg": t_kg,
        "barcode": _clean_text(p[bi]),
        "remarks": _clean_text(p[bi + 1]),
        "warehouse": _clean_text(p[bi + 2]),
        "unit": _clean_text(p[bi + 3]),
        "product_name_local": _clean_text(p[bi + 4]),
        "material": _clean_text(p[bi + 5]),
    }
    row["_parse_source"] = "html_table"
    if narrow_vol:
        _flags_cell_list(row).append("parse_narrow_volume_columns")
    _maybe_shift_missing_unit_column(row, cells, bi)
    _derive_unit_cbm_from_dims(row)
    return row, last_del, last_cus


def parse_sales_document_from_html_tables(html_tables: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Scan all tables: product rows + optional TOTAL row."""
    lines: list[dict[str, Any]] = []
    footer: dict[str, Any] | None = None
    last_del: str | None = None
    last_cus: str | None = None

    for html in html_tables:
        rows = _table_to_rows(html)
        if _is_junk_short_table(rows):
            continue
        header_gone = False
        for cells in rows:
            if not header_gone and _is_header_row(cells):
                continue
            header_gone = True

            foot = _try_footer_from_row(cells)
            if foot:
                footer = foot
                continue

            if _is_total_row(cells):
                continue

            if not _is_data_row(cells):
                continue

            cells = expand_pack_row_cells(cells)
            item, last_del, last_cus = _cells_to_line_dict(cells, last_del, last_cus)
            if item:
                lines.append(item)

    return lines, footer
