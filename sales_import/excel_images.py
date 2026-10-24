"""Extract floating pictures from .xlsx and map them to product marks (column A).

ponytail: parse drawing XML in the zip (openpyxl often leaves _images empty after .xls→.xlsx).
"""

from __future__ import annotations

import hashlib
import logging
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

_NS = {
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}

_PHOTO_COL = 3
_MIN_WIDTH_EMU = 50_000  # ~ignore 1-px junk (~57k EMU ≈ 1px at 96dpi is wrong scale; use low bar)


def _marks_key(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).replace("\r", "\n").strip()
    if not text:
        return None
    first = text.split("\n", 1)[0].strip()
    first = re.sub(r"\s+SANCARGO\s*$", "", first, flags=re.I).strip()
    return first or None


def _sheet_drawing_targets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """Return [(sheet_xml_path, drawing_xml_path), ...] for worksheets that have drawings."""
    out: list[tuple[str, str]] = []
    for name in zf.namelist():
        if not re.match(r"xl/worksheets/_rels/sheet\d+\.xml\.rels$", name):
            continue
        sheet_xml = name.replace("/_rels/", "/").replace(".rels", "")
        root = ET.fromstring(zf.read(name))
        for rel in root.findall("rel:Relationship", _NS):
            if "drawing" not in (rel.get("Type") or "").lower():
                continue
            target = rel.get("Target") or ""
            # Target is often ../drawings/drawing1.xml
            drawing = "xl/" + target.lstrip("./").replace("../", "")
            if drawing.startswith("xl/xl/"):
                drawing = drawing[3:]
            if not drawing.startswith("xl/"):
                drawing = "xl/drawings/" + Path(target).name
            out.append((sheet_xml, drawing))
    return out


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for si in root.findall("main:si", _NS):
        texts = [t.text or "" for t in si.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")]
        strings.append("".join(texts))
    return strings


def _col_row_from_ref(ref: str) -> tuple[int, int] | None:
    m = re.match(r"^([A-Z]+)(\d+)$", ref.upper())
    if not m:
        return None
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return col - 1, int(m.group(2)) - 1  # 0-based


def _sheet_col_a_marks(zf: zipfile.ZipFile, sheet_xml: str, shared: list[str]) -> dict[int, str]:
    """Map 0-based row → marks text from column A."""
    root = ET.fromstring(zf.read(sheet_xml))
    marks: dict[int, str] = {}
    for c in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
        ref = c.get("r") or ""
        parsed = _col_row_from_ref(ref)
        if not parsed or parsed[0] != 0:
            continue
        row0 = parsed[1]
        v = c.find("main:v", _NS)
        if v is None or v.text is None:
            continue
        if c.get("t") == "s":
            try:
                raw = shared[int(v.text)]
            except (ValueError, IndexError):
                continue
        else:
            raw = v.text
        key = _marks_key(raw)
        if key:
            marks[row0] = key
    return marks


def _drawing_placements(zf: zipfile.ZipFile, drawing_xml: str) -> list[tuple[int, int, bytes, str]]:
    """Return [(col0, row0, bytes, ext), ...] for pictures in a drawing part."""
    if drawing_xml not in zf.namelist():
        return []
    rels_path = str(Path(drawing_xml).parent / "_rels" / (Path(drawing_xml).name + ".rels")).replace("\\", "/")
    # Path may produce xl/drawings/_rels/drawing1.xml.rels
    rid_to_media: dict[str, str] = {}
    if rels_path in zf.namelist():
        rel_root = ET.fromstring(zf.read(rels_path))
        for rel in rel_root.findall("rel:Relationship", _NS):
            rid = rel.get("Id") or ""
            target = rel.get("Target") or ""
            media = "xl/" + target.lstrip("./").replace("../", "")
            if media.startswith("xl/xl/"):
                media = media[3:]
            rid_to_media[rid] = media

    root = ET.fromstring(zf.read(drawing_xml))
    placements: list[tuple[int, int, bytes, str]] = []
    for anchor in list(root.findall("xdr:twoCellAnchor", _NS)) + list(root.findall("xdr:oneCellAnchor", _NS)):
        frm = anchor.find("xdr:from", _NS)
        if frm is None:
            continue
        col_el = frm.find("xdr:col", _NS)
        row_el = frm.find("xdr:row", _NS)
        if col_el is None or row_el is None or col_el.text is None or row_el.text is None:
            continue
        col0, row0 = int(col_el.text), int(row_el.text)
        if col0 != _PHOTO_COL:
            continue
        # Skip tiny widths when to-col is same and cx tiny
        ext = anchor.find("xdr:ext", _NS)
        if ext is not None:
            try:
                if int(ext.get("cx") or "0") < _MIN_WIDTH_EMU:
                    continue
            except ValueError:
                pass
        blip = anchor.find(".//a:blip", _NS)
        if blip is None:
            continue
        rid = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
        if not rid or rid not in rid_to_media:
            continue
        media_path = rid_to_media[rid]
        if media_path not in zf.namelist():
            continue
        data = zf.read(media_path)
        if len(data) < 100:
            continue
        suffix = Path(media_path).suffix.lower().lstrip(".") or "jpeg"
        if suffix == "jpg":
            suffix = "jpeg"
        placements.append((col0, row0, data, suffix))
    return placements


def extract_images_by_marks(path: Path) -> dict[str, list[tuple[bytes, str]]]:
    """Return {marks: [(image_bytes, ext), ...]} across all sheets."""
    path = path.expanduser().resolve()
    out: dict[str, list[tuple[bytes, str]]] = {}
    with zipfile.ZipFile(path) as zf:
        shared = _shared_strings(zf)
        for sheet_xml, drawing_xml in _sheet_drawing_targets(zf):
            marks_by_row = _sheet_col_a_marks(zf, sheet_xml, shared)
            for _col, row0, data, ext in _drawing_placements(zf, drawing_xml):
                marks = marks_by_row.get(row0)
                if not marks:
                    continue
                u = marks.upper()
                if u.startswith("TOTAL ") or u in ("NEW ORDERS", "TOTAL WEIGHT", "TOTAL CARTON", "TOTAL CBM"):
                    continue
                out.setdefault(marks, []).append((data, ext))
    return out


def upload_image_bytes(
    sb: Any,
    *,
    data: bytes,
    ext: str,
    document_id: str,
    marks: str,
    slot: int,
) -> str | None:
    digest = hashlib.sha256(data).hexdigest()[:16]
    safe_marks = re.sub(r"[^\w\-]+", "_", marks)[:40]
    path = f"manifest/{document_id}/{safe_marks}_{slot}_{digest}.{ext}"
    content_type = "image/jpeg" if ext in ("jpeg", "jpg") else f"image/{ext}"
    try:
        sb.storage.from_("product-images").upload(
            path,
            data,
            {"content-type": content_type, "upsert": "true"},
        )
    except Exception as e:  # noqa: BLE001
        if "already exists" not in str(e).lower() and "Duplicate" not in str(e):
            logger.warning("upload failed %s: %s", path, e)
            return None
    try:
        return sb.storage.from_("product-images").get_public_url(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("public url failed %s: %s", path, e)
        return None


def attach_photos_to_lines(
    lines: list[dict[str, Any]],
    images_by_marks: dict[str, list[tuple[bytes, str]]],
    sb: Any,
    document_id: str,
) -> int:
    attached = 0
    url_cache: dict[str, str] = {}
    for line in lines:
        marks = _marks_key(line.get("marks"))
        if not marks or marks not in images_by_marks:
            continue
        blobs = images_by_marks[marks]
        urls: list[str] = []
        for i, (data, ext) in enumerate(blobs[:2]):
            digest = hashlib.sha256(data).hexdigest()
            if digest in url_cache:
                urls.append(url_cache[digest])
                continue
            url = upload_image_bytes(
                sb, data=data, ext=ext, document_id=document_id, marks=marks, slot=i
            )
            if url:
                url_cache[digest] = url
                urls.append(url)
        if urls:
            line["photo_url"] = urls[0]
            if len(urls) > 1:
                line["photo2_url"] = urls[1]
            attached += 1
    return attached
