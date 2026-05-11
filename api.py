"""HTTP API for sales import: POST a PDF/CSV/Excel file, persist to Supabase.

Run (from project root):
  pip install -r requirements.txt
  python api.py
  # or: uvicorn api:app --host 0.0.0.0 --port 8000

Environment (see .env.example):
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY — required for DB writes.
  REDUCTO_API_KEY — required only for .pdf imports.
  IMPORT_API_KEY — optional; if set, clients must send the same value in
    header X-API-Key: <key> or Authorization: Bearer <key>.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Annotated

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sales_import.pipeline import _ensure_dotenv, run_import

_ALLOWED_SUFFIXES = frozenset({".pdf", ".csv", ".xlsx", ".xlsm"})

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

_bearer = HTTPBearer(auto_error=False)


def verify_import_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    _ensure_dotenv()
    expected = os.environ.get("IMPORT_API_KEY", "").strip()
    if not expected:
        return
    provided = (x_api_key or "").strip()
    if not provided and credentials is not None:
        provided = (credentials.credentials or "").strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


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


if __name__ == "__main__":
    import uvicorn

    _ensure_dotenv()
    host = os.environ.get("API_HOST", "0.0.0.0")
    # Railway / Render set PORT; local dev can use API_PORT.
    port = int(os.environ.get("PORT", os.environ.get("API_PORT", "8000")))
    uvicorn.run("api:app", host=host, port=port, reload=False)
