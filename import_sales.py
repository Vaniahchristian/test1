#!/usr/bin/env python3
"""CLI: python import_sales.py file.pdf|file.xlsx|file.csv [--dry-run] [--sheet NAME]

Container manifest (marks / T.CTN / T.QTY columns):
  python import_sales.py --manifest "manifest.csv" [--dry-run] [--sheet Sheet1]
"""

from sales_import.pipeline import main_cli

if __name__ == "__main__":
    main_cli()
