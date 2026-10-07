"""Normalize sales.csv into a clean, single-line import-friendly CSV."""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

SRC = Path(r"C:\Users\user\Desktop\test1\sales.raw.csv")
OUT = Path(r"C:\Users\user\Desktop\test1\sales.csv")
# Prefer untouched original backup; fall back to current sales.csv once.
if not SRC.exists():
    SRC = Path(r"C:\Users\user\Desktop\test1\sales.csv")

HEADER = [
    "MARKS",
    "SHOP#",
    "ITEM NO.",
    "DESCRIPTION OF GOODS",
    "PRODUCT NAME",
    "PACKING",
    "T.CTN",
    "T.QTY",
    "H",
    "W",
    "L",
    "UNIT CBM",
    "T.CBM",
    "UNIT WEIGHT",
    "T.WEIGHT",
    "U.PRICE (RMB)",
    "T.AMOUNT",
    "BOX .NO",
]


def num(s: str) -> str:
    if s is None:
        return ""
    t = str(s).strip()
    if not t or t.upper() in {"#NAME?", "N/A", "-", "NONE"}:
        return ""
    t = t.replace(",", "").replace("，", "")
    t = re.sub(r"[¥￥$]", "", t)
    t = re.sub(r"(?i)\s*(CTNS?|PCS|CBM|KGS?|RMB|USD|KG)\s*$", "", t).strip()
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    return m.group(0) if m else ""


def clean_text(s: str) -> str:
    if s is None:
        return ""
    t = str(s).replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\n", " ").strip()
    t = re.sub(r"\s+", " ", t)
    if t.upper() == "#NAME?":
        return ""
    return t


def flatten_marks(s: str) -> str:
    t = clean_text(s)
    m = re.match(r"^(MMB-\d+(?:-\d+)?)", t, re.I)
    if m:
        return m.group(1).upper()
    return t


def is_blank_row(cols: list[str]) -> bool:
    return not any(c.strip() for c in cols)


def looks_like_data(cols: list[str]) -> bool:
    if len(cols) < 9:
        return False
    marks = flatten_marks(cols[0])
    return bool(re.match(r"^MMB-\d+", marks, re.I))


def main() -> None:
    raw = SRC.read_text(encoding="utf-8-sig", errors="replace")
    raw_backup = Path(r"C:\Users\user\Desktop\test1\sales.raw.csv")
    if not raw_backup.exists():
        raw_backup.write_text(raw, encoding="utf-8")

    rows = list(csv.reader(io.StringIO(raw)))

    client_line = clean_text(rows[0][0]) if rows else ""
    client_m = re.search(
        r"CLIENT DETAILS\s*:\s*(.+?)\s+CONTAINER NO\s*:\s*(\S+)",
        client_line,
        re.I,
    )
    client = client_m.group(1).strip() if client_m else ""
    container = client_m.group(2).strip() if client_m else ""

    totals = {
        "TOTAL WEIGHT": "",
        "TOTAL CBM": "",
        "TOTAL CARTON": "",
        "TOTAL COST RMB": "",
        "TOTAL COST USD": "",
    }
    for row in rows:
        joined = ",".join(row)
        upper = joined.upper()
        if upper.startswith("TOTAL WEIGHT"):
            totals["TOTAL WEIGHT"] = num(row[4] if len(row) > 4 else "")
        elif upper.startswith("TOTAL CBM"):
            totals["TOTAL CBM"] = num(row[4] if len(row) > 4 else "")
        elif upper.startswith("TOTAL CARTON"):
            totals["TOTAL CARTON"] = num(row[4] if len(row) > 4 else "")
        elif upper.startswith("TOTAL COST"):
            rmb = usd = ""
            for c in row:
                if ("￥" in c or "¥" in c) and not rmb:
                    rmb = num(c)
                if "$" in c and not usd:
                    usd = num(c)
            totals["TOTAL COST RMB"] = rmb
            totals["TOTAL COST USD"] = usd

    shipped: list[list[str]] = []
    left: list[list[str]] = []
    section = "shipped"

    for row in rows[1:]:
        if is_blank_row(row):
            continue
        joined = " ".join(c.strip() for c in row if c.strip()).upper()
        if "GOODS LEFT" in joined:
            section = "left"
            continue
        if joined.startswith("NEW ORDERS") or joined.startswith("MARKS"):
            continue
        if any(
            joined.startswith(b)
            for b in (
                "TOTAL WEIGHT",
                "TOTAL CBM",
                "TOTAL CARTON",
                "TOTAL COST",
                "GOODS BALANCE",
                "CREDIT SUPPORT",
                "PIVOC",
                "YIWU",
                "TOTAL BALANCE",
                "BALANCE PAYMENT",
                "IF OUTSTANDING",
                "CLIENT DETAILS",
                "COMPUTED FROM",
            )
        ):
            continue
        if not flatten_marks(row[0] if row else "") and (
            "CTNS" in joined or "CBM" in joined
        ):
            continue
        if not looks_like_data(row):
            continue

        marks = flatten_marks(row[0])
        shop = clean_text(row[1] if len(row) > 1 else "")
        item_no = clean_text(row[2] if len(row) > 2 else "")
        desc = clean_text(row[4] if len(row) > 4 else "")
        product = clean_text(row[5] if len(row) > 5 else "").replace("，", ", ")
        packing = clean_text(row[6] if len(row) > 6 else "")
        pm = re.search(r"(\d+)\s*pcs\s*/\s*ctn", packing, re.I)
        packing_out = f"{pm.group(1)}pcs/ctn" if pm else packing

        out = [
            marks,
            shop,
            item_no,
            desc,
            product,
            packing_out,
            num(row[7] if len(row) > 7 else ""),
            num(row[8] if len(row) > 8 else ""),
            num(row[9] if len(row) > 9 else ""),
            num(row[10] if len(row) > 10 else ""),
            num(row[11] if len(row) > 11 else ""),
            num(row[12] if len(row) > 12 else ""),
            num(row[13] if len(row) > 13 else ""),
            num(row[14] if len(row) > 14 else ""),
            num(row[15] if len(row) > 15 else ""),
            num(row[16] if len(row) > 16 else ""),
            num(row[17] if len(row) > 17 else ""),
            clean_text(row[18] if len(row) > 18 else ""),
        ]
        (left if section == "left" else shipped).append(out)

    def sums(rs: list[list[str]]) -> tuple[float, float, float, float, float]:
        ctn = qty = cbm = kg = amt = 0.0
        for r in rs:
            ctn += float(r[6] or 0)
            qty += float(r[7] or 0)
            cbm += float(r[12] or 0)
            kg += float(r[14] or 0)
            amt += float(r[16] or 0)
        return ctn, qty, cbm, kg, amt

    s_ctn, s_qty, s_cbm, s_kg, s_amt = sums(shipped)
    l_ctn, l_qty, l_cbm, l_kg, l_amt = sums(left)

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([f"CLIENT DETAILS: {client}", f"CONTAINER NO: {container}"])
    w.writerow(HEADER)
    w.writerow(["NEW ORDERS"])
    for r in shipped:
        w.writerow(r)
    if left:
        w.writerow(["GOODS LEFT IN SANCARGO"])
        for r in left:
            w.writerow(r)
    w.writerow([])
    # Document footer — label in col0, value in col1 (not mistaken for ITEM NO.)
    w.writerow(["TOTAL WEIGHT", totals["TOTAL WEIGHT"], "KGS"])
    w.writerow(["TOTAL CBM", totals["TOTAL CBM"], "CBM"])
    w.writerow(["TOTAL CARTON", totals["TOTAL CARTON"], "CTN"])
    w.writerow(
        [
            "TOTAL COST",
            totals["TOTAL COST RMB"],
            "RMB",
            totals["TOTAL COST USD"],
            "USD",
        ]
    )

    OUT.write_text(buf.getvalue(), encoding="utf-8-sig")

    meta = Path(r"C:\Users\user\Desktop\test1\sales.clean_notes.txt")
    meta.write_text(
        "\n".join(
            [
                "Cleaned from sales.raw.csv",
                f"shipped_rows={len(shipped)} ctn={s_ctn:.0f} qty={s_qty:.0f} cbm={s_cbm:.3f} kg={s_kg:.1f} rmb={s_amt:.2f}",
                f"left_rows={len(left)} ctn={l_ctn:.0f} qty={l_qty:.0f} cbm={l_cbm:.3f} kg={l_kg:.1f} rmb={l_amt:.2f}",
                f"all_rows={len(shipped)+len(left)} ctn={s_ctn+l_ctn:.0f} qty={s_qty+l_qty:.0f} cbm={s_cbm+l_cbm:.3f} kg={s_kg+l_kg:.1f} rmb={s_amt+l_amt:.2f}",
                f"footer_declared={totals}",
                "Note: line-item carton/weight sums exceed footer TOTAL CARTON/WEIGHT;",
                "amount matches footer (~356754). Prefer line sums for re-import checks.",
                "Fixes: flattened multiline cells, removed #NAME?, stripped units/currency,",
                "single header row, bare numeric T.CTN/T.QTY/CBM/WEIGHT/PRICE/AMOUNT.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print("wrote", OUT)
    print("notes", meta)
    print("shipped", len(shipped), s_ctn, s_qty, round(s_amt, 2))
    print("left", len(left), l_ctn, l_qty, round(l_amt, 2))
    print("footer", totals)


if __name__ == "__main__":
    main()
