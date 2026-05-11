#!/usr/bin/env python3
"""CLI entry: python import_sales.py path/to/sales.pdf [--dry-run]"""

from sales_import.pipeline import main_cli

if __name__ == "__main__":
    main_cli()
