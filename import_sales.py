#!/usr/bin/env python3
"""CLI: python import_sales.py file.pdf|file.xlsx|file.csv [--dry-run] [--sheet NAME]"""

from sales_import.pipeline import main_cli

if __name__ == "__main__":
    main_cli()
