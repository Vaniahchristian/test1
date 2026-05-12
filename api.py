"""HTTP API for sales import: POST a PDF/CSV/Excel file, persist to Supabase.

Run (from project root):
  pip install -r requirements.txt
  python api.py
  # or: uvicorn api:app --host 0.0.0.0 --port 8000

Environment (see .env.example):
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY — required for DB writes.
  REDUCTO_API_KEY — required only for .pdf imports.

Authentication: POST /import and POST /import/manifest require **no** API key or Bearer
token. Both behave the same from a client perspective.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from sales_import.pipeline import _ensure_dotenv, run_import, run_import_container_manifest

_ALLOWED_SUFFIXES = frozenset({".pdf", ".csv", ".xlsx", ".xlsm"})
_MANIFEST_SUFFIXES = frozenset({".csv", ".xlsx", ".xlsm"})

app = FastAPI(
    title="Sales packing-list import",
    version="1.0.0",
    description="Upload PDF (Reducto), CSV, or Excel; inserts documents, document_items, document_totals when dry_run=false.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    logger.info("Health check requested")
    return {"status": "ok"}


@app.post("/import")
def import_document(
    file: UploadFile = File(..., description="Sales PDF, .csv, .xlsx, or .xlsm"),
    dry_run: bool = Query(
        False,
        description="If true, parse only; no Supabase writes (PDF still calls Reducto).",
    ),
    sheet: str | None = Query(
        None,
        description="Excel worksheet name (optional; else EXCEL_SHEET or first sheet).",
    ),
) -> dict:
    logger.info(f"Import started - filename: {file.filename}, dry_run: {dry_run}, sheet: {sheet}")
    _ensure_dotenv()
    name = file.filename or "upload"
    suffix = Path(name).suffix.lower()
    logger.info(f"File suffix detected: {suffix}")

    tmp_path: Path | None = None
    try:
        logger.info("Reading file data...")
        data = file.file.read()
        logger.info(f"File size: {len(data)} bytes")
        if not data:
            logger.error("Empty file received")
            raise HTTPException(status_code=400, detail="Empty file")
        fd, raw = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        tmp_path = Path(raw)
        tmp_path.write_bytes(data)
        logger.info(f"Temp file created: {tmp_path}")

        logger.info("Calling run_import...")
        result = run_import(tmp_path, write_db=not dry_run, excel_sheet=sheet)
        logger.info(f"Import successful - lines: {result.get('line_count', 0)}, items_inserted: {result.get('items_inserted', 0)}")
        return result
    except HTTPException as e:
        logger.error(f"HTTPException: {e.status_code} - {e.detail}")
        raise
    except FileNotFoundError as e:
        logger.error(f"FileNotFoundError: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.error(f"RuntimeError: {e}")
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Unexpected error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
            logger.info(f"Temp file cleaned up: {tmp_path}")


@app.post("/import/manifest")
def import_container_manifest(
    file: UploadFile = File(..., description="Container manifest .csv, .xlsx, or .xlsm"),
    dry_run: bool = Query(
        False,
        description="If true, parse only; no Supabase writes.",
    ),
    sheet: str | None = Query(
        None,
        description="Excel worksheet name (optional; else EXCEL_SHEET or first sheet).",
    ),
) -> dict:
    logger.info(
        f"Manifest import - filename: {file.filename}, dry_run: {dry_run}, sheet: {sheet}"
    )
    _ensure_dotenv()
    name = file.filename or "upload"
    suffix = Path(name).suffix.lower()
    if suffix not in _MANIFEST_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Container manifest import accepts {sorted(_MANIFEST_SUFFIXES)} only; got {suffix!r}",
        )

    tmp_path: Path | None = None
    try:
        data = file.file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Empty file")
        fd, raw = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        tmp_path = Path(raw)
        tmp_path.write_bytes(data)

        result = run_import_container_manifest(
            tmp_path, write_db=not dry_run, sheet_name=sheet
        )
        logger.info(
            f"Manifest import done - lines: {result.get('line_count', 0)}, "
            f"items_inserted: {result.get('items_inserted', 0)}"
        )
        return result
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Manifest import error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    import uvicorn

    _ensure_dotenv()
    host = os.environ.get("API_HOST", "0.0.0.0")
    # Railway / Render set PORT; local dev can use API_PORT.
    port = int(os.environ.get("PORT", os.environ.get("API_PORT", "8000")))
    uvicorn.run("api:app", host=host, port=port, reload=False)
