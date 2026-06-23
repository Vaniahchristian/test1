"""Self-check: run directly (`python -m sales_import.test_section_banners`).

Covers the bug fixed in MS-4 / MS-T-2: section-heading rows with wording the
classifier hasn't seen before must never be parsed as a fake product line.
"""

from __future__ import annotations

from sales_import.container_manifest_import import _is_lone_text_row
from sales_import.manifest_sections import classify_section_banner


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


if __name__ == "__main__":
    test_classify_section_banner()
    test_is_lone_text_row()
    print("OK")
