"""
Quick-capture endpoint — accepts text + images via multipart/form-data.
Saves images to the data/attachments folder, creates a draft capture record in the database.
Partial failures return HTTP 207 with per-image status.
"""
import logging
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from goalforge.config import config
from goalforge import database, id_generator

logger = logging.getLogger(__name__)
router = APIRouter()
bearer = HTTPBearer()

MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "image/heif": ".heic",
}


def _auth(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if credentials.credentials != config.api.secret_token:
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials.credentials


def _allowed_types() -> list[str]:
    types = config.capture.allowed_image_types
    if isinstance(types, list):
        return [t.lower() for t in types]
    return ["jpg", "jpeg", "png", "webp", "gif", "heic"]


def _max_bytes() -> int:
    mb = config.capture.max_image_size_mb
    try:
        return int(mb) * 1024 * 1024
    except (TypeError, ValueError):
        return 20 * 1024 * 1024


def _validate_image(file: UploadFile) -> tuple[bool, str, str]:
    """
    Returns (ok, ext, error_message).
    ext is the normalised extension to use when saving.
    """
    # Check MIME type
    mime = (file.content_type or "").lower()
    if mime in MIME_TO_EXT:
        ext = MIME_TO_EXT[mime]
    else:
        # Fall back to filename extension
        suffix = Path(file.filename or "").suffix.lower().lstrip(".")
        allowed = _allowed_types()
        if suffix not in allowed:
            return False, "", f"Unsupported file type '{file.content_type or suffix}'. Accepted: {', '.join(allowed)}"
        ext = f".{suffix}"

    return True, ext, ""


def _attachments_path() -> Path:
    """Return the data/attachments directory, derived from the database location."""
    path = Path(config.database_path).parent / "attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/capture")
async def capture(
    title: str = Form(...),
    description: Optional[str] = Form(""),
    images: list[UploadFile] = File(default=[]),
    token: str = Depends(_auth),
):
    db = database.get_db()
    goal_id = id_generator.next_id(db)

    attachments_path = _attachments_path()
    max_bytes = _max_bytes()
    saved_images: list[str] = []
    image_results: list[dict] = []
    any_failed = False

    for n, file in enumerate(images, start=1):
        ok, ext, error = _validate_image(file)
        if not ok:
            image_results.append({"filename": file.filename, "status": "rejected", "error": error})
            any_failed = True
            continue

        filename = f"{goal_id}_{n}{ext}"
        dest = attachments_path / filename

        try:
            data = await file.read()
            if len(data) > max_bytes:
                mb_limit = max_bytes // (1024 * 1024)
                image_results.append({
                    "filename": file.filename,
                    "status": "rejected",
                    "error": f"File exceeds {mb_limit}MB limit ({len(data) // (1024*1024)}MB)",
                })
                any_failed = True
                continue

            dest.write_bytes(data)
            saved_images.append(filename)
            database.upsert_attachment(goal_id, filename, file.content_type or "")
            image_results.append({"filename": file.filename, "saved_as": filename, "status": "saved"})
            logger.info("Saved image %s", filename)

        except Exception as e:
            logger.error("Failed to save image %s: %s", file.filename, e)
            image_results.append({"filename": file.filename, "status": "error", "error": str(e)})
            any_failed = True

    # Upsert into DB as a Draft capture
    database.upsert_goal({
        "id": goal_id,
        "name": title,
        "status": "Draft",
        "description": description or "",
        "tags": '["capture"]',
        "created_date": date.today().isoformat(),
    })

    result = {
        "id": goal_id,
        "status": "captured",
        "images_saved": saved_images,
        "image_results": image_results,
    }

    # 207 Multi-Status if any image failed
    if any_failed and saved_images:
        return JSONResponse(status_code=207, content=result)

    return result


@router.get("/goals")
def list_goals_api(
    status: Optional[str] = None,
    horizon: Optional[str] = None,
    is_milestone: Optional[bool] = None,
    full_only: bool = False,
    exclude_daily_checklist: bool = True,
    token: str = Depends(_auth),
):
    """List goals with optional filtering. Daily checklist items are excluded by default."""
    filters = {}
    if status:
        filters["status"] = status
    if horizon:
        filters["horizon"] = horizon
    if is_milestone is not None:
        filters["is_milestone"] = is_milestone
    if exclude_daily_checklist:
        filters["exclude_daily_checklist"] = True

    if full_only:
        return database.get_full_goals(filters)
    return database.get_all_goals(filters)


@router.get("/goals/{goal_id}")
def get_goal_api(goal_id: str, token: str = Depends(_auth)):
    goal = database.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
    children = database.get_children(goal_id, recursive=False)
    ancestors = database.get_ancestors(goal_id)
    goal["children"] = children
    goal["ancestors"] = ancestors
    return goal


@router.post("/goals/{goal_id}/plan")
def plan_goal_api(goal_id: str, token: str = Depends(_auth)):
    """Trigger AI planning for a goal. Returns list of created child goals."""
    from goalforge.planner import plan_goal
    try:
        children = plan_goal(goal_id)
        return {"goal_id": goal_id, "created": children}
    except Exception as e:
        logger.error("Plan failed for %s: %s", goal_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/goals/{goal_id}")
def update_goal_api(goal_id: str, updates: dict, token: str = Depends(_auth)):
    """Update one or more fields on a goal — writes through to the vault file."""
    from goalforge.vault_tools import update_goal_field
    for field, value in updates.items():
        try:
            update_goal_field(goal_id, field, value)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return database.get_goal(goal_id)


@router.delete("/goals/{goal_id}")
def delete_goal_api(goal_id: str, token: str = Depends(_auth)):
    """Delete a goal file and its DB record."""
    from goalforge.vault_tools import delete_goal
    try:
        return delete_goal(goal_id, confirmed=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/inbox")
def list_inbox(token: str = Depends(_auth)):
    """List all Draft captures."""
    return database.get_draft_captures()


@router.post("/goals/create")
def create_goal_api(body: dict, token: str = Depends(_auth)):
    """Create a new goal via vault_tools."""
    from goalforge.vault_tools import create_goal
    try:
        return create_goal(
            name=body["name"],
            description=body.get("description", ""),
            horizon=body.get("horizon", "Monthly"),
            due_date=body.get("due_date"),
            category=body.get("category", ""),
            parent_goal_id=body.get("parent_goal_id"),
            is_milestone=body.get("is_milestone", False),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/goals/{goal_id}/promote")
def promote_goal_api(goal_id: str, token: str = Depends(_auth)):
    from goalforge.vault_tools import promote_to_full_goal
    try:
        return promote_to_full_goal(goal_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/goals/{goal_id}/demote")
def demote_goal_api(goal_id: str, token: str = Depends(_auth)):
    from goalforge.vault_tools import demote_to_milestone
    try:
        return demote_to_milestone(goal_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/goals/{goal_id}/demote-to-idea")
def demote_goal_to_idea_api(goal_id: str, token: str = Depends(_auth)):
    """Convert a strategic goal to an idea, then delete the goal."""
    from goalforge.vault_tools import demote_goal_to_idea
    try:
        return demote_goal_to_idea(goal_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/search")
def search_api(q: str, token: str = Depends(_auth)):
    """Search goals by name or description."""
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="q parameter is required")
    return database.search_goals(q.strip())


@router.get("/goals/{goal_id}/attachments")
def get_goal_attachments(goal_id: str, token: str = Depends(_auth)):
    """List attachments for a goal."""
    goal = database.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
    return database.get_attachments(goal_id)


@router.get("/attachments/{filename}")
def serve_attachment(filename: str, token: str = Depends(_auth)):
    """Serve an attachment file."""
    path = _attachments_path() / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")
    return FileResponse(path)
