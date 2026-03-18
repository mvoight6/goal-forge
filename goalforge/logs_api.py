"""
Logs API + rotating log handler configuration for all modules.
GET /logs, GET /logs/{filename}, GET /logs/{filename}/tail
Log format: YYYY-MM-DD HH:MM:SS | LEVEL | module | message
"""
import logging
import logging.handlers
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from goalforge.config import config

logger = logging.getLogger(__name__)
router = APIRouter()
bearer = HTTPBearer()


# ---------------------------------------------------------------------------
# Logging setup — called once from main.py at startup
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_MODULE_LOGGERS = [
    "goalforge.scanner",
    "goalforge.database",
    "goalforge.planner",
    "goalforge.notifier",
    "goalforge.interactive",
    "goalforge.vault_tools",
    "goalforge.scheduler",
    "goalforge.capture",
    "goalforge.dashboard",
    "goalforge.config_api",
    "goalforge.logs_api",
    "goalforge.llm.anthropic",
    "goalforge.llm.openrouter",
    "goalforge.llm.ollama",
    "goalforge.llm.vllm",
    "goalforge.llm.factory",
]


def setup_logging():
    configured = Path(config.log_path)
    # Try configured path; fall back to ./logs next to project root if not writable
    for candidate in [configured, Path(__file__).parent.parent / "logs"]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            # Test writability
            test = candidate / ".write_test"
            test.touch()
            test.unlink()
            log_dir = candidate
            break
        except (PermissionError, OSError):
            continue
    else:
        # Last resort: use a temp directory
        import tempfile
        log_dir = Path(tempfile.mkdtemp(prefix="goalforge_logs_"))

    if log_dir != configured:
        print(f"[Goal Forge] WARNING: Cannot write to '{configured}', using '{log_dir}' for logs instead.")

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    def _rotating(filename: str, log_name: Optional[str] = None) -> logging.handlers.RotatingFileHandler:
        h = logging.handlers.RotatingFileHandler(
            log_dir / filename,
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8",
        )
        h.setFormatter(formatter)
        return h

    # Root / combined log
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    combined_handler = _rotating("goalforge.log")
    root.addHandler(combined_handler)

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    # Per-module rotating files
    for mod in _MODULE_LOGGERS:
        file_name = mod.replace(".", "_") + ".log"
        mod_handler = _rotating(file_name)
        mod_logger = logging.getLogger(mod)
        mod_logger.addHandler(mod_handler)

    # Dedicated vault_changes.log
    vc_handler = _rotating("vault_changes.log")
    vc_handler.setLevel(logging.INFO)
    vc_logger = logging.getLogger("vault_changes")
    vc_logger.addHandler(vc_handler)
    vc_logger.propagate = False  # Don't duplicate in combined log

    logger.info("Logging configured. Log dir: %s", log_dir)
    # Store the resolved dir so the API endpoints can find it
    global _resolved_log_dir
    _resolved_log_dir = log_dir


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

_resolved_log_dir: Path = Path("logs")  # overwritten by setup_logging()


def _auth(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if credentials.credentials != config.api.secret_token:
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials.credentials


def _log_dir() -> Path:
    return _resolved_log_dir


def _safe_log_path(filename: str) -> Path:
    log_dir = _log_dir()
    path = (log_dir / filename).resolve()
    # Security: must stay within log_path
    try:
        path.relative_to(log_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Log file not found: {filename}")
    return path


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/logs")
def list_logs(token: str = Depends(_auth)):
    """List all log files with name, size, last modified."""
    log_dir = _log_dir()
    if not log_dir.exists():
        return []
    files = []
    for p in sorted(log_dir.glob("*.log")):
        stat = p.stat()
        files.append({
            "name": p.name,
            "size_bytes": stat.st_size,
            "last_modified": stat.st_mtime,
        })
    return files


@router.get("/logs/{filename}")
def get_log(
    filename: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(500, ge=1, le=5000),
    token: str = Depends(_auth),
):
    """Return paginated log content, newest lines first."""
    path = _safe_log_path(filename)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    lines.reverse()  # newest first

    total = len(lines)
    start = (page - 1) * per_page
    end = start + per_page
    page_lines = lines[start:end]

    return {
        "filename": filename,
        "total_lines": total,
        "page": page,
        "per_page": per_page,
        "lines": page_lines,
    }


@router.get("/logs/{filename}/tail")
def tail_log(
    filename: str,
    n: int = Query(100, ge=1, le=2000),
    token: str = Depends(_auth),
):
    """Return last N lines of a log file (for live tail polling)."""
    path = _safe_log_path(filename)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {
        "filename": filename,
        "lines": lines[-n:],
    }
