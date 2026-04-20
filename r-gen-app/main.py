"""
main.py

FastAPI application entry point.

Responsibilities:
  - Serve the vanilla JS frontend from static/
  - Accept file uploads, parse them, and store session state in memory
  - Stream LLM-generated R code back to the browser

Session storage
---------------
Sessions are kept in a plain Python dict (_sessions) for simplicity.  This is
fine for a personal / single-user tool where the process is long-lived and
restarts are infrequent.

NOTE FOR PRODUCTION: replace _sessions with a Redis-backed store (e.g. via
redis-py or encode/databases).  The current in-memory dict is lost on every
restart and does not scale across multiple worker processes.
"""

import os
import shutil
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from codegen import stream_r_code
from file_parser import parse_file

# Load .env before anything reads os.environ (load_dotenv is a no-op when
# variables are already present, so this is safe in production too).
load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Directory where uploaded files are temporarily stored while a session is
# alive.  Created at startup if it does not exist.
TEMP_DIR = Path("/tmp/r-codegen")

# Accepted upload extensions (lower-case, with leading dot).
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}

# Maximum allowed upload size (50 MB).
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

# Session expiry (1 hour).
SESSION_MAX_AGE_SECS = 60 * 60  # 1 hour

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

# Read allowed origins from env; fall back to localhost for local development.
# In production set ALLOWED_ORIGINS=https://yourdomain.com in .env.
_raw_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:8000")
ALLOWED_ORIGINS: list[str] = [o.strip() for o in _raw_origins.split(",")]

# ---------------------------------------------------------------------------
# In-memory session store
#
# Each entry:
#   session_id (str) -> {
#       "summary":    dict   - full parse_file() output (sent to LLM)
#       "temp_path":  str    - absolute path to the saved temp file
#       "created_at": float  - unix timestamp for TTL eviction
#   }
#
# See the production note at the top of the file before deploying.
# ---------------------------------------------------------------------------

_sessions: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="r-gen-app")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    """Create the temp directory and purge stale files left from prior runs."""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_old_temp_files()

    # -- Startup diagnostics --------------------------------------------------
    # Confirm env vars loaded and surface obvious misconfigurations early.
    import codegen as _cg
    print(f"[startup] OLLAMA_BASE_URL : {_cg._BASE_URL}")
    print(f"[startup] OLLAMA_API_KEY  : {'configured' if _cg._API_KEY else 'missing'}")
    print(f"[startup] OLLAMA_MODEL    : {_cg._MODEL}")
    if "your-ollama" in _cg._BASE_URL or "your_" in _cg._API_KEY:
        print("[startup] WARNING: .env still contains placeholder values - update OLLAMA_BASE_URL and OLLAMA_API_KEY")


# ---------------------------------------------------------------------------
# Temp-file maintenance
# ---------------------------------------------------------------------------

# Files older than this are deleted on startup.
_TEMP_FILE_MAX_AGE_SECS = 60 * 60  # 1 hour


def _cleanup_old_temp_files() -> None:
    """
    Delete any files in TEMP_DIR whose mtime is older than _TEMP_FILE_MAX_AGE_SECS.

    Called once at startup.  In a long-running multi-user deployment you could
    also schedule this periodically (e.g. via APScheduler), but for a personal
    tool a single startup sweep is sufficient.
    """
    cutoff = time.time() - _TEMP_FILE_MAX_AGE_SECS
    for path in TEMP_DIR.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


def _evict_expired_sessions() -> None:
    """Remove sessions older than SESSION_MAX_AGE_SECS and delete their temp files."""
    cutoff = time.time() - SESSION_MAX_AGE_SECS
    expired = [sid for sid, data in _sessions.items() if data.get("created_at", 0) < cutoff]
    for sid in expired:
        session = _sessions.pop(sid, None)
        if session:
            Path(session["temp_path"]).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Routes - defined before the static mount so FastAPI sees them first.
# ---------------------------------------------------------------------------

@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness probe.  Returns {"status": "ok"}."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    session_id: str
    question: str


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@app.post("/upload", tags=["data"])
async def upload(file: UploadFile = File(...)) -> dict:
    """
    Accept a multipart file upload, parse it, and create a server-side session.

    Only .csv, .xlsx, and .xls files are accepted.  Any other type returns a
    422 before any data is written to disk.

    Returns a lightweight summary (shape + columns + filename) to the frontend.
    The full summary - including sample rows, numeric stats, and categorical
    value counts - is kept server-side and injected into the LLM prompt only.
    """
    # Evict expired sessions before creating a new one.
    _evict_expired_sessions()

    # --- Validate extension before touching disk ---
    original_name = file.filename or "upload"
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unsupported file type '{suffix}'. "
                "Please upload a .csv, .xlsx, or .xls file."
            ),
        )

    # --- Read and enforce size limit ---
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum allowed size is {MAX_UPLOAD_BYTES / 1024 / 1024:.0f} MB.",
        )

    # --- Save to temp directory with a unique name so concurrent uploads
    #     don't collide.  We keep the original extension so pandas can detect
    #     the format without sniffing the content.
    unique_id = uuid.uuid4().hex
    temp_path = TEMP_DIR / f"{unique_id}{suffix}"

    try:
        with temp_path.open("wb") as f:
            f.write(contents)
    finally:
        await file.close()

    # --- Parse the file ---
    try:
        summary = parse_file(str(temp_path))
        # Restore the original filename in the summary so the LLM sees the
        # real name rather than the UUID-prefixed temp name.
        summary["filename"] = original_name
    except ValueError as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse file: {exc}",
        )

    # --- Store full summary in session ---
    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "summary": summary,
        "temp_path": str(temp_path),
        "created_at": time.time(),
    }

    # --- Return lightweight summary to the frontend ---
    return {
        "session_id": session_id,
        "summary": {
            "filename": summary["filename"],
            "shape": summary["shape"],
            "columns": summary["columns"],
        },
    }


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------

@app.post("/generate", tags=["codegen"])
async def generate(body: GenerateRequest) -> StreamingResponse:
    """
    Stream LLM-generated R code for the given session and question.

    The full file summary (including sample rows and statistics) is retrieved
    from the session store and injected into the LLM system prompt so the
    model has complete context without the frontend ever seeing those fields.
    """
    session = _sessions.get(body.session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Session expired. Please re-upload your file.",
        )

    full_summary = session["summary"]
    print(f"[generate] session found - filename={full_summary.get('filename')} question={body.question!r}")

    async def _token_stream():
        """
        Wrap stream_r_code() so that any RuntimeError from codegen is
        converted to a plain-text error message in the stream rather than
        silently dropping the connection.  The frontend can detect a line
        starting with 'ERROR:' and surface it to the user.
        """
        chunk_count = 0
        try:
            async for chunk in stream_r_code(full_summary, body.question):
                chunk_count += 1
                if chunk_count == 1:
                    print(f"[generate] first chunk received ({len(chunk)} chars)")
                yield chunk
        except RuntimeError as exc:
            print(f"[generate] RuntimeError: {exc}")
            yield f"\n# ERROR: {exc}\n"
        except Exception as exc:
            print(f"[generate] unexpected error: {type(exc).__name__}: {exc}")
            yield f"\n# ERROR: unexpected server error - check logs\n"
        finally:
            print(f"[generate] stream complete - {chunk_count} chunks yielded")

    return StreamingResponse(_token_stream(), media_type="text/plain")


# ---------------------------------------------------------------------------
# Session cleanup
# ---------------------------------------------------------------------------

@app.delete("/session/{session_id}", tags=["data"])
async def delete_session(session_id: str) -> dict:
    """
    Remove the session from memory and delete the associated temp file.

    Safe to call multiple times - missing sessions and missing files are
    both treated as already-clean, not as errors.
    """
    session = _sessions.pop(session_id, None)
    if session:
        temp_path = Path(session["temp_path"])
        temp_path.unlink(missing_ok=True)

    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# Static frontend
#
# Mounted last so the explicit routes above take priority.  StaticFiles with
# html=True causes / to serve index.html automatically.
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static-assets")

# Serve index.html for the root path explicitly so that the /health and other
# API routes registered above are not shadowed by the static mount.
@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse("static/index.html")
