"""
Daily Goals API — Google Keep-style daily checklist.
Endpoints for listing days, adding items, toggling complete, and moving items.
"""
import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from goalforge import database, id_generator
from goalforge.capture import _auth

logger = logging.getLogger(__name__)
router = APIRouter()


def _parent_name(date_str: str) -> str:
    return f"Daily Goals {date_str}"


def _get_daily_parent(date_str: str) -> Optional[dict]:
    """Find the daily parent goal for a given date."""
    name = _parent_name(date_str)
    all_goals = database.get_all_goals({"category": "Daily"})
    for g in all_goals:
        if g.get("name") == name and not g.get("parent_goal_id"):
            return g
    return None


def _create_daily_parent(date_str: str) -> dict:
    """Create the parent daily goal for a given date."""
    db = database.get_db()
    new_id = id_generator.next_id(db)
    name = _parent_name(date_str)

    goal_dict = {
        "id": new_id,
        "name": name,
        "status": "Active",
        "horizon": "Daily",
        "due_date": date_str,
        "parent_goal_id": None,
        "depth": 0,
        "is_milestone": False,
        "category": "Daily",
        "created_date": date.today().isoformat(),
        "notify_before_days": 0,
        "tags": '["goal","daily"]',
    }
    database.upsert_goal(goal_dict)
    return database.get_goal(new_id) or goal_dict


def _next_sort_order(parent_id: str) -> int:
    """Return sort_order for a new item appended to the end of a parent's children."""
    row = database.get_db().execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM goals WHERE parent_goal_id = ?", [parent_id]
    ).fetchone()
    return row[0] if row else 0


def _create_daily_item(name: str, date_str: str, parent_id: str) -> dict:
    """Create a single daily item under the given parent."""
    db = database.get_db()
    new_id = id_generator.next_id(db)
    sort_order = _next_sort_order(parent_id)

    goal_dict = {
        "id": new_id,
        "name": name,
        "status": "Active",
        "horizon": "Daily",
        "due_date": date_str,
        "parent_goal_id": parent_id,
        "depth": 1,
        "is_milestone": False,
        "category": "Daily",
        "created_date": date.today().isoformat(),
        "notify_before_days": 0,
        "sort_order": sort_order,
        "tags": '["goal","daily"]',
    }
    database.upsert_goal(goal_dict)
    return database.get_goal(new_id) or goal_dict


# ---------------------------------------------------------------------------
# Public helper (used by interactive/LLM tool dispatch)
# ---------------------------------------------------------------------------

def add_daily_item_for_date(name: str, date_str: str) -> dict:
    """Create a daily checklist item for the given date. Creates the parent day if needed."""
    parent = _get_daily_parent(date_str) or _create_daily_parent(date_str)
    return _create_daily_item(name, date_str, parent["id"])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/daily")
def get_daily(days: int = 7, token=Depends(_auth)):
    """Return the last `days` days plus any future days that have items, oldest first."""
    today = date.today()

    # Base range: last `days` days including today
    date_set = set()
    for i in range(days - 1, -1, -1):
        date_set.add((today - timedelta(days=i)).isoformat())

    # Include any future dates that already have a daily parent (e.g. moved items)
    all_parents = database.get_all_goals({"category": "Daily"})
    for g in all_parents:
        due = g.get("due_date")
        if due and due > today.isoformat() and not g.get("parent_goal_id"):
            date_set.add(due)

    result = []
    for d in sorted(date_set):
        parent = _get_daily_parent(d)
        items = database.get_children(parent["id"]) if parent else []
        result.append({"date": d, "parent": parent, "items": items})
    return result


@router.post("/daily/{date_str}/items")
def add_daily_item(date_str: str, body: dict, token=Depends(_auth)):
    """Add an item to a day. Creates the parent daily goal if it doesn't exist yet."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    parent = _get_daily_parent(date_str) or _create_daily_parent(date_str)
    return _create_daily_item(name, date_str, parent["id"])


@router.put("/daily/{date_str}/order")
def set_daily_order(date_str: str, body: dict, token=Depends(_auth)):
    """Persist the display order of items for a given day."""
    item_ids = body.get("item_ids", [])
    if not item_ids:
        raise HTTPException(status_code=400, detail="item_ids is required")
    database.set_daily_order(item_ids)
    return {"ok": True}


@router.post("/daily/items/{item_id}/move")
def move_daily_item(item_id: str, body: dict, token=Depends(_auth)):
    """Move a daily item to a different date."""
    to_date = (body.get("to_date") or "").strip()
    if not to_date:
        raise HTTPException(status_code=400, detail="to_date is required")

    item = database.get_goal(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    target_parent = _get_daily_parent(to_date) or _create_daily_parent(to_date)

    database.set_parent(item_id, target_parent["id"])
    database.update_field(item_id, "due_date", to_date)
    return database.get_goal(item_id)
