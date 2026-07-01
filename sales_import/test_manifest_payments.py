"""Self-check: footer USD payment lines from container manifest CSV footers."""

from __future__ import annotations

from pathlib import Path

from sales_import.container_manifest_import import load_container_manifest_lines_from_csv
from sales_import.manifest_sections import parse_manifest_payment_line


def test_ms7_footer_payments() -> None:
    path = Path("MS-7  20260526.csv")
    if not path.is_file():
        return
    _, footer, _, _, payments = load_container_manifest_lines_from_csv(path)
    assert footer is not None
    assert footer.get("total_amount_usd") == 27902.0
    notes = {p["note"] for p in payments}
    assert "GOODS BALANCE" in notes
    assert "YIWU-MOMBASA FREIGHT" in notes
    assert "TOTAL BALANCE" in notes
    freight = next(p for p in payments if p["note"] == "YIWU-MOMBASA FREIGHT")
    assert freight["amount_usd"] == 6500.0
    assert freight["payment_type"] == "freight"


def test_parse_goods_balance_line() -> None:
    row = "GOODS BALANCE,,,,,,\"$27,902\",USD"
    p = parse_manifest_payment_line(row)
    assert p is not None
    assert p["amount_usd"] == 27902.0
    assert p["note"] == "GOODS BALANCE"


if __name__ == "__main__":
    test_parse_goods_balance_line()
    test_ms7_footer_payments()
    print("OK")
