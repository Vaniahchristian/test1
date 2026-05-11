"""JSON Schema + prompts for Reducto Extract (sales packing list / invoice grid)."""

SYSTEM_PROMPT = """You are extracting rows from a bilingual (English + Chinese) sales or packing list PDF.
The main grid has columns such as: NO., DEL NO., CUS NO., ITEM NO., PHOTO, description, cartons, qty per carton,
total qty, unit price (RMB), line amount (RMB), L/W/H cm, CBM, gross weight, TTL CBM, TTL KGS, barcode/code, remarks,
warehouse, unit, Chinese product name, Chinese material.
Return one line_items entry per product row on all pages; skip header rows and summary lines that are not products.
Numbers must be numeric (not strings). Omit a field if absent — do not guess.

Also locate the document-level summary/footer (often the last row(s) of the table or a TOTAL/合计 line) that states
grand totals for the whole shipment: total cartons, total quantity (T.QTY), total TTL CBM, total TTL KGS, and total
amount RMB. Put those in footer_totals. These must match the sum of line AMOUNT, TTL CBM, TTL KGS, T.QTY, and CTN
columns across all line items — extract the printed footer values exactly as shown, not recomputed."""

# Top-level schema must include `line_items` — mapper expects this key.
SALES_EXTRACT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "line_items": {
            "type": "array",
            "description": (
                "All product line rows from the delivery/packing table across every page, "
                "in document order."
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
                        "description": "DEL NO. / 送货单号",
                    },
                    "customer_item_ref": {
                        "type": "string",
                        "description": "CUS NO. / 客户货号",
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
                        "description": "QTY / 每箱数量",
                    },
                    "total_quantity": {
                        "type": "number",
                        "description": "T.QTY / 总数量",
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
                "Grand totals printed in the PDF for the entire document (footer row, TOTAL, or 合计): "
                "must be document-level sums, not a single line item."
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
    return {
        "deep_extract": False,
    }
