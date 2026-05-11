"""Fill missing footer_totals keys from footer_text (e.g. '1237 TOTAL:')."""

from __future__ import annotations

import re
from typing import Any


def enrich_footer_totals(footer: dict[str, Any] | None) -> dict[str, Any] | None:
    if not footer:
        return footer
    out = dict(footer)
    if out.get("total_cartons") is None:
        txt = out.get("footer_text") or ""
        m = re.search(r"(\d+(?:\.\d+)?)", str(txt))
        if m:
            try:
                out["total_cartons"] = float(m.group(1))
            except ValueError:
                pass
    return out
