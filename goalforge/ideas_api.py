"""
Ideas API — capture and cultivate ideas before they become strategic goals.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from goalforge import database, id_generator
from goalforge.capture import _auth

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_STATUSES = {'Incubating', 'Active', 'Graduated', 'Archived'}
VALID_PRIORITIES = {'Critical', 'High', 'Medium', 'Low'}


@router.get("/ideas/top")
def top_ideas(n: int = 5, token=Depends(_auth)):
    """Top N active ideas sorted by priority then newest first."""
    return database.get_top_ideas(n)


@router.get("/ideas")
def list_ideas(status: str = None, priority: str = None, category: str = None, token=Depends(_auth)):
    return database.get_ideas(status=status, priority=priority, category=category)


@router.post("/ideas")
def create_idea(body: dict, token=Depends(_auth)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    status = body.get("status", "Incubating")
    priority = body.get("priority", "Medium")
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(VALID_STATUSES)}")
    if priority not in VALID_PRIORITIES:
        raise HTTPException(status_code=400, detail=f"priority must be one of {sorted(VALID_PRIORITIES)}")

    db = database.get_db()
    new_id = id_generator.next_id(db)
    database.upsert_idea({
        "id": new_id,
        "name": name,
        "description": body.get("description", ""),
        "progress_notes": body.get("progress_notes", ""),
        "status": status,
        "priority": priority,
        "category": body.get("category", ""),
    })
    return database.get_idea(new_id)


@router.get("/ideas/{idea_id}")
def get_idea(idea_id: str, token=Depends(_auth)):
    idea = database.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    return idea


@router.put("/ideas/{idea_id}")
def update_idea(idea_id: str, body: dict, token=Depends(_auth)):
    idea = database.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    if "status" in body and body["status"] not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(VALID_STATUSES)}")
    if "priority" in body and body["priority"] not in VALID_PRIORITIES:
        raise HTTPException(status_code=400, detail=f"priority must be one of {sorted(VALID_PRIORITIES)}")

    updatable = {"name", "description", "progress_notes", "status", "priority", "category"}
    updated = {**idea, **{k: v for k, v in body.items() if k in updatable}}
    database.upsert_idea(updated)
    return database.get_idea(idea_id)


@router.delete("/ideas/{idea_id}")
def delete_idea(idea_id: str, confirmed: bool = False, token=Depends(_auth)):
    if not confirmed:
        raise HTTPException(status_code=400, detail="Pass ?confirmed=true to delete")
    idea = database.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    database.delete_idea(idea_id)
    return {"ok": True, "deleted": idea_id}


@router.post("/ideas/{idea_id}/graduate")
def graduate_idea(idea_id: str, token=Depends(_auth)):
    """Promote an idea to a strategic goal (Backlog status). Marks idea as Graduated."""
    idea = database.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    db = database.get_db()
    new_goal_id = id_generator.next_id(db)
    database.upsert_goal({
        "id": new_goal_id,
        "name": idea["name"],
        "description": idea.get("description") or "",
        "progress_notes": idea.get("progress_notes") or "",
        "status": "Backlog",
        "horizon": "Yearly",
        "is_milestone": False,
        "notify_before_days": 3,
    })

    database.upsert_idea({**idea, "status": "Graduated", "graduated_goal_id": new_goal_id})
    logger.info("Idea %s graduated to goal %s", idea_id, new_goal_id)
    return {"idea_id": idea_id, "goal_id": new_goal_id, "goal": database.get_goal(new_goal_id)}
