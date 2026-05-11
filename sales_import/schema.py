"""JSON Schema + prompts for Reducto Extract (sales packing list / invoice grid)."""

SYSTEM_PROMPT = """You are extracting rows from a bilingual (English + Chinese) Yiwu-style delivery note / packing list PDF.

MAIN TABLE (all pages): columns include NO., DEL NO., CUS NO. (sometimes green-highlighted), ITEM NO., PHOTO, DES.,
CTN, QTY, T.QTY, U/P, AMOUNT, L/W/H (cm), CBM (per carton), G.W. (per carton kg), TTL CBM (line total, often orange),
TTL KGS (line total, often orange), CODE, REK, W.H., UNIT, NAME/品名, MATERIAL/材质.

Line items (line_items):
- One entry per real product row. Do NOT include the final TOTAL/合计 summary row as a line item.
- Merged / repeated cells: DEL NO. and CUS NO. may be merged vertically. For every product row, repeat the DEL NO.
  that applies to that block until the PDF shows a new one. If CUS NO. appears only on the first row of a group
  (green highlight), copy that same customer_item_ref to each following product row in that group until a new CUS NO.
  appears.
- Orange styling on TTL CBM / TTL KGS does not change field names — still map to total_cbm and total_weight_kg per row.
- Numbers must be JSON numbers, not strings. Omit fields you truly cannot read — do not guess.

FOOTER TOTALS (footer_totals) — critical:
- The document often has a **TOTAL** (or 合计) **row at the bottom of the main table** (typically on the last page),
  summing CTN, T.QTY, AMOUNT, TTL CBM, TTL KGS for the **entire shipment**. Put those printed values in footer_totals.
- Do **NOT** use the short **red header** snippets (e.g. 三仓/刀叉勺/东阳/秒账: 件数/m³) as footer_totals — those are
  partial/warehouse summaries, not the table grand total.
- Extract footer_totals **exactly as printed** in that TOTAL row; do not recompute from line items."""


# Top-level schema must include `line_items` — mapper expects this key.
SALES_EXTRACT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "line_items": {
            "type": "array",
            "description": (
                "All product line rows from the main delivery table on every page, top to bottom. "
                "Exclude header-only rows and the final TOTAL/合计 aggregate row."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "line_no": {
                        "type": "integer",
                        "description": "Integer from column NO. / 编号",
                    },
                    "source_page": {
                        "type": "integer",
                        "description": "1-based PDF page number where this row appears",
                    },
                    "delivery_no": {
                        "type": "string",
                        "description": (
                            "DEL NO. / 送货单号. If the cell is vertically merged across several rows, "
                            "use the same value for each product row in that block."
                        ),
                    },
                    "customer_item_ref": {
                        "type": "string",
                        "description": (
                            "CUS NO. / 客户货号 (often green). If only the first row of a group shows CUS NO., "
                            "copy it to every following row in that group until a new CUS NO. appears."
                        ),
                    },
                    "item_code": {
                        "type": "string",
                        "description": "ITEM NO. / 产品货号 — primary SKU",
                    },
                    "photo_url": {
                        "type": "string",
                        "description": "Image URL if the API provides one; else omit",
                    },
                    "description": {
                        "type": "string",
                        "description": "DES. / 描述 — English description",
                    },
                    "total_cartons": {
                        "type": "number",
                        "description": "CTN / 总箱数",
                    },
                    "qty_per_carton": {
                        "type": "number",
                        "description": "QTY / 每箱数量 — pieces per carton; with CTN and T.QTY must satisfy T.QTY = CTN × QTY",
                    },
                    "total_quantity": {
                        "type": "number",
                        "description": (
                            "T.QTY / 总数量 — line total units; equals CTN × QTY when all quantity columns are readable"
                        ),
                    },
                    "unit_price_rmb": {
                        "type": "number",
                        "description": "U/P / 单价 (RMB)",
                    },
                    "total_amount_rmb": {
                        "type": "number",
                        "description": "AMOUNT / 金额 (RMB)",
                    },
                    "dim_l_cm": {"type": "number", "description": "L / 外箱长 cm"},
                    "dim_w_cm": {"type": "number", "description": "W / 外箱宽 cm"},
                    "dim_h_cm": {"type": "number", "description": "H / 外箱高 cm"},
                    "unit_cbm": {"type": "number", "description": "CBM / 体积 per carton"},
                    "unit_weight_kg": {
                        "type": "number",
                        "description": "G.W. / 重量 per carton, kg",
                    },
                    "total_cbm": {"type": "number", "description": "TTL CBM / 总体积"},
                    "total_weight_kg": {"type": "number", "description": "TTL KGS / 总重量 kg"},
                    "barcode": {"type": "string", "description": "CODE / 条形码 if present"},
                    "remarks": {"type": "string", "description": "REK / 备注"},
                    "warehouse": {"type": "string", "description": "W.H. / 仓库"},
                    "unit": {"type": "string", "description": "UNIT / 单位 e.g. pcs, SET"},
                    "product_name_local": {
                        "type": "string",
                        "description": "NAME / 品名 in Chinese",
                    },
                    "material": {
                        "type": "string",
                        "description": "MATERIAL / 材质 in Chinese",
                    },
                },
                "required": ["item_code"],
            },
        },
        "footer_totals": {
            "type": "object",
            "description": (
                "The **bottom TOTAL / 合计 row of the main product table** (usually on the last page): "
                "grand totals for CTN, T.QTY, AMOUNT (RMB), TTL CBM, TTL KGS for the full document. "
                "Not red header warehouse snippets; not a single line item."
            ),
            "properties": {
                "total_cartons": {
                    "type": "number",
                    "description": "Total cartons (CTN) for the whole document if printed",
                },
                "total_quantity": {
                    "type": "number",
                    "description": "Grand total T.QTY / 总数量 for all lines",
                },
                "total_cbm": {
                    "type": "number",
                    "description": "Grand total TTL CBM / 立方 for all lines",
                },
                "total_weight_kg": {
                    "type": "number",
                    "description": "Grand total TTL KGS / 总重量 for all lines",
                },
                "total_amount_rmb": {
                    "type": "number",
                    "description": "Grand total AMOUNT / 金额 RMB for all lines",
                },
                "footer_text": {
                    "type": "string",
                    "description": "Verbatim footer/summary label if helpful for auditing",
                },
            },
        },
    },
    "required": ["line_items"],
}


def parsing_options() -> dict:
    """Parse options tuned for messy multi-page bilingual tables."""
    return {
        "formatting": {
            "merge_tables": True,
            "table_output_format": "html",
        },
        "settings": {
            "ocr_system": "standard",
        },
    }


def extract_settings() -> dict:
    import os

    deep = os.environ.get("REDUCTO_DEEP_EXTRACT", "").strip().lower() in ("1", "true", "yes")
    return {
        "deep_extract": deep,
    }
