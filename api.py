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

import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sales_import.pipeline import _ensure_dotenv, run_import

_ALLOWED_SUFFIXES = frozenset({".pdf", ".csv", ".xlsx", ".xlsm"})

app = FastAPI(
    title="Sales packing-list import",
    version="1.0.0",
    description="Upload PDF (Reducto), CSV, or Excel; inserts documents, document_items, document_totals when dry_run=false.",
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
    return {"status": "ok"}


@app.post("/import", dependencies=[Depends(verify_import_api_key)])
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
    _ensure_dotenv()
    name = file.filename or "upload"
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix!r}. Allowed: {sorted(_ALLOWED_SUFFIXES)}",
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

        return run_import(tmp_path, write_db=not dry_run, excel_sheet=sheet)
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    import uvicorn

    _ensure_dotenv()
    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8000"))
    uvicorn.run("api:app", host=host, port=port, reload=False)
