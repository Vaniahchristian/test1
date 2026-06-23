"""Self-check: run directly (`python -m sales_import.test_section_banners`).

Covers the bug fixed in MS-4 / MS-T-2: section-heading rows with wording the
classifier hasn't seen before must never be parsed as a fake product line.
"""

from __future__ import annotations

from sales_import.container_manifest_import import (
    _header_indices,
    _is_aggregate_row,
    _is_lone_text_row,
    _process_manifest_data_rows,
)
from sales_import.manifest_sections import classify_section_banner, looks_like_section_banner

_MANIFEST_HEADER = [
    "MARKS", "SHOP#", "ITEM NO.", "DESCRIPTION OF GOODS", "", "",
    "PACKING", "T.CTN", "T.QTY", "H", "W", "L", "UNIT CBM", "T.CBM",
    "UNIT WEIGHT", "T.WEIGHT", "U.PRICE (RMB)", "T.AMOUNT", "Stuffed with",
]


def test_classify_section_banner() -> None:
    assert classify_section_banner("LEFT BEHIND GOODS FROM 37-T-1 NOW LOADED") == (
        "shipped",
        "LEFT BEHIND GOODS FROM 37-T-1 NOW LOADED",
    )
    assert classify_section_banner("LEFT BEHIND GOODS FROM MS-3 NOT LOADED") == (
        "left_in_warehouse",
        "LEFT BEHIND GOODS FROM MS-3 NOT LOADED",
    )
    assert classify_section_banner("NEW ORDER-MS-4") == ("shipped", "NEW ORDER-MS-4")
    assert classify_section_banner("NEW ORDER") == ("shipped", "NEW ORDER")
    assert classify_section_banner("GOODS LEFT IN SANCARGO") == (
        "left_in_warehouse",
        "GOODS LEFT IN SANCARGO",
    )
    # Compound/ambiguous wording: capture verbatim, don't guess.
    assert classify_section_banner("NEW ORDER-MS-4 GOODS LEFT BEHIND") == (None, None)
    # Ordinary product row text must never look like a section banner.
    assert classify_section_banner("MS-301-24 SANCARGO Cake Showcase 3CTNS") == (None, None)


def test_is_lone_text_row() -> None:
    assert _is_lone_text_row(["NEW ORDER-MS-4"] + [""] * 18) is True
    assert _is_lone_text_row(["25"] + [""] * 18) is True
    assert (
        _is_lone_text_row(["MS-402-2 SANCARGO", "新南方", "2", "", "Food Display", *([""] * 14)])
        is False
    )
    assert _is_lone_text_row(["", "", ""]) is False


def test_is_aggregate_row_without_carton_count() -> None:
    idx = _header_indices(_MANIFEST_HEADER)
    # MS-4 row 241: leftover CBM/weight correction, no marks/item/desc, no T.CTN.
    cbm_weight_only_row = [
        "", "", "", "", "", "", "", "", "", "", "", "", "", "3.5016CBM", "", "452.0KGS", "", "", "",
    ]
    assert _is_aggregate_row(cbm_weight_only_row, idx) is True
    # A real product row (has description) must never be treated as an aggregate row.
    real_row = [
        "MS-402-2 SANCARGO", "新南方", "2", "", "Food Display Showcase", "66cm",
        "", "", "50pcs", "", "", "", "", "", "", "0.0KGS", "￥600.00", "￥30,000.00", "",
    ]
    assert _is_aggregate_row(real_row, idx) is False


def test_looks_like_section_banner() -> None:
    assert looks_like_section_banner("NEW ORDER-MS-4 GOODS LEFT BEHIND") is True
    # Stray cell, not a heading: a bare number, or a single word.
    assert looks_like_section_banner("25") is False
    assert looks_like_section_banner("REMARKS") is False
    # Data fragment, not a heading.
    assert looks_like_section_banner("452.0KGS") is False
    assert looks_like_section_banner("") is False


def test_unclassified_banner_gets_other_section() -> None:
    idx = _header_indices(_MANIFEST_HEADER)
    item_row = [
        "MS-402-2 SANCARGO", "新南方", "2", "", "Food Display Showcase", "66cm",
        "", "", "50pcs", "", "", "", "", "", "", "0.0KGS", "￥600.00", "￥30,000.00", "",
    ]
    banner_row = ["NEW ORDER-MS-4 GOODS LEFT BEHIND"] + [""] * 18
    stray_row = ["25"] + [""] * 18

    # A real (if unrecognized) heading -> its own "other" bucket, not a guess.
    lines, _, _, banners = _process_manifest_data_rows([banner_row, item_row], dict(idx))
    assert lines[0]["section"] == "other"
    assert banners[-1] == {
        "text": "NEW ORDER-MS-4 GOODS LEFT BEHIND",
        "section": "other",
        "recognized": False,
    }

    # A stray cell that doesn't look like a heading must not flip the active section.
    lines2, _, _, _ = _process_manifest_data_rows([stray_row, item_row], dict(idx))
    assert lines2[0]["section"] == "shipped"


if __name__ == "__main__":
    test_classify_section_banner()
    test_is_lone_text_row()
    test_is_aggregate_row_without_carton_count()
    test_looks_like_section_banner()
    test_unclassified_banner_gets_other_section()
    print("OK")
