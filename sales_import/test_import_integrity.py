"""Regression: Excel/CSV manifests must keep every product row and match section bars.

Run: python -m sales_import.test_import_integrity
"""

from __future__ import annotations

from pathlib import Path

from sales_import.container_manifest_import import (
    load_container_manifest_lines_from_csv,
    load_container_manifest_lines_from_xlsx,
)
from sales_import.totals_reconcile import (
    pick_manifest_expected_totals,
    reconcile_totals,
    sum_item_rows,
    sum_section_subtotals,
)

ROOT = Path(__file__).resolve().parents[1]


def _assert_integrity(label: str, lines, footer, subs) -> None:
    computed = sum_item_rows(lines)
    yellow = sum_section_subtotals(subs)
    expected, source = pick_manifest_expected_totals(
        pdf_footer=footer, section_subtotals=subs, computed=computed
    )
    match, diff = reconcile_totals(pdf_footer=expected, computed=computed)
    null_ctn = sum(1 for r in lines if r.get("total_cartons") is None)

    assert lines, f"{label}: no lines imported"
    # Blank T.CTN in the spreadsheet (merged cells) is OK if yellow bars still match line sums.
    assert match, f"{label}: totals_match failed ({source}): {diff} null_ctn={null_ctn}"

    # Each yellow section bar must equal the sum of its lines (client's section total).
    by_sec_ctn: dict[str, float] = {}
    for r in lines:
        sec = (r.get("section") or "shipped").strip().lower()
        by_sec_ctn[sec] = by_sec_ctn.get(sec, 0.0) + float(r.get("total_cartons") or 0)
    yellow_by: dict[str, float] = {}
    for s in subs or []:
        sec = (s.get("section") or "").strip().lower() or "?"
        if s.get("total_cartons") is not None:
            yellow_by[sec] = yellow_by.get(sec, 0.0) + float(s["total_cartons"])
    for sec, yc in yellow_by.items():
        lc = by_sec_ctn.get(sec, 0.0)
        assert abs(lc - yc) <= 1.0, f"{label}: section {sec} lines={lc} yellow={yc}"

    print(
        f"OK {label}: lines={len(lines)} ctn={computed.get('total_cartons')} "
        f"source={source} yellow_ctn={yellow.get('total_cartons')}"
    )


def test_sales_xlsx_all_rows_and_totals() -> None:
    path = ROOT / "_sales_photos.xlsx"
    if not path.is_file():
        print("SKIP sales_xlsx (no fixture)")
        return
    lines, footer, subs, _b, _p = load_container_manifest_lines_from_xlsx(path)
    assert len(lines) >= 254, f"sales_xlsx dropped rows: {len(lines)}"
    _assert_integrity("sales_xlsx", lines, footer, subs)


def test_km1_xlsx_includes_sheet2() -> None:
    path = ROOT / "_km1_photos.xlsx"
    if not path.is_file():
        print("SKIP km1_xlsx (no fixture)")
        return
    lines, footer, subs, banners, _p = load_container_manifest_lines_from_xlsx(path)
    marks = { (r.get("marks") or "").split("\n")[0].strip() for r in lines }
    assert any(m.startswith("KM-129") for m in marks), "KM-1 Sheet2 (KM-129*) was dropped"
    assert len(lines) >= 190, f"km1_xlsx too few rows: {len(lines)}"
    _assert_integrity("km1_xlsx", lines, footer, subs)


def test_sales_csv_same_line_count() -> None:
    path = ROOT / "sales.csv"
    if not path.is_file():
        print("SKIP sales_csv (no fixture)")
        return
    lines, footer, subs, _b, _p = load_container_manifest_lines_from_csv(path)
    assert len(lines) >= 254, f"sales_csv dropped rows: {len(lines)}"
    # CSV exports often omit yellow bars — still require no null CTN.
    null_ctn = sum(1 for r in lines if r.get("total_cartons") is None)
    assert null_ctn == 0, f"sales_csv: {null_ctn} null cartons"
    print(f"OK sales_csv: lines={len(lines)}")


def test_packing_carry_forward_display_only() -> None:
    """Blank PACKING inherits prior text for display; blank T.CTN stays blank (no double-count)."""
    from sales_import.container_manifest_import import _header_indices, _process_manifest_data_rows

    header = [
        "MARKS 唛头", "SHOP#", "ITEM NO.", "DESCRIPTION", "", "", "PACKING", "T.CTN", "T.QTY",
        "H", "W", "L", "U.CBM", "T.T.CBM", "G.W", "T.T.KGS", "U.PRICE", "AMOUNT",
    ]
    idx = _header_indices(header)
    rows = [
        ["A-1", "shop", "1", "Item one", "", "", "4", "10", "40", "", "", "", "", "", "", "", "", ""],
        ["A-2", "shop", "2", "Item two", "", "", "", "", "80", "", "", "", "", "", "", "", "", ""],
        # Explicit packing on this row may fill blank T.CTN from qty.
        ["A-3", "shop", "3", "Item three", "", "", "4", "", "80", "", "", "", "", "", "", "", "", ""],
    ]
    lines, _, _, _, _ = _process_manifest_data_rows(rows, dict(idx))
    assert len(lines) == 3
    assert lines[1]["packaging"] == "4"
    assert lines[1]["total_cartons"] is None  # carried packing must not invent CTN
    assert lines[2]["total_cartons"] == 20.0  # explicit packing cell + qty
    print("OK packing_carry_forward")


if __name__ == "__main__":
    test_packing_carry_forward_display_only()
    test_sales_xlsx_all_rows_and_totals()
    test_km1_xlsx_includes_sheet2()
    test_sales_csv_same_line_count()
    print("ALL OK")
