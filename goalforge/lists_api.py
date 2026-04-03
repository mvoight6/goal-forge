"""
Lists API — Google Keep-style lists with items, drag-reorder, reminders, and AI generation.
Existing ideas are migrated into lists automatically at startup (see database.py).
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from goalforge import database, id_generator
from goalforge.capture import _auth

logger = logging.getLogger(__name__)
router = APIRouter()

# Palette of named colours available in the UI
VALID_COLORS = {
    "red", "orange", "yellow", "green", "teal",
    "blue", "purple", "pink", "gray", "default",
}

# Recurrence values
VALID_RECURRENCES = {"daily", "weekly", "monthly", "annually"}


# ---------------------------------------------------------------------------
# Reminder helpers
# ---------------------------------------------------------------------------

def _compute_next_reminder(reminder_time: str, recurrence: Optional[str]) -> Optional[str]:
    """
    Given HH:MM and an optional recurrence string, return the ISO timestamp for
    the next time the reminder should fire.
    """
    if not reminder_time:
        return None
    try:
        h, m = map(int, reminder_time.split(":"))
    except ValueError:
        raise HTTPException(status_code=400, detail="reminder_time must be HH:MM")

    now = datetime.utcnow()
    candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)

    if recurrence in (None, ""):
        return candidate.isoformat()
    if recurrence == "daily":
        return candidate.isoformat()
    if recurrence == "weekly":
        # Keep same weekday — already computed for next occurrence ≥ now
        return candidate.isoformat()
    if recurrence == "monthly":
        return candidate.isoformat()
    if recurrence == "annually":
        return candidate.isoformat()
    raise HTTPException(status_code=400, detail=f"recurrence must be one of {sorted(VALID_RECURRENCES)}")


def _advance_reminder(lst: dict) -> Optional[str]:
    """Compute the next reminder_next_at after a reminder has fired."""
    recurrence = lst.get("reminder_recurrence")
    reminder_time = lst.get("reminder_time")
    if not recurrence or not reminder_time:
        return None  # one-time; clear it

    try:
        h, m = map(int, reminder_time.split(":"))
    except ValueError:
        return None

    prev = datetime.utcnow().replace(hour=h, minute=m, second=0, microsecond=0)
    if recurrence == "daily":
        nxt = prev + timedelta(days=1)
    elif recurrence == "weekly":
        nxt = prev + timedelta(weeks=1)
    elif recurrence == "monthly":
        # Advance by roughly one month (same day)
        month = prev.month + 1
        year = prev.year + (1 if month > 12 else 0)
        month = month if month <= 12 else 1
        try:
            nxt = prev.replace(year=year, month=month)
        except ValueError:
            # Day doesn't exist in target month (e.g. Jan 31 → Feb 28)
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            nxt = prev.replace(year=year, month=month, day=last_day)
    elif recurrence == "annually":
        try:
            nxt = prev.replace(year=prev.year + 1)
        except ValueError:
            nxt = prev.replace(year=prev.year + 1, day=28)
    else:
        return None

    return nxt.isoformat()


# ---------------------------------------------------------------------------
# List endpoints
# ---------------------------------------------------------------------------

@router.get("/lists")
def list_lists(include_items: bool = False, token=Depends(_auth)):
    lists = database.get_lists()
    if include_items:
        for lst in lists:
            lst["items"] = database.get_list_items(lst["id"])
    return lists


@router.post("/lists")
def create_list(body: dict, token=Depends(_auth)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    color = body.get("color") or None
    if color and color not in VALID_COLORS:
        raise HTTPException(status_code=400, detail=f"color must be one of {sorted(VALID_COLORS)}")
    db = database.get_db()
    new_id = id_generator.next_id(db)
    lst = database.create_list(new_id, name, color)

    # Set reminder if provided
    reminder_time = (body.get("reminder_time") or "").strip() or None
    recurrence = (body.get("reminder_recurrence") or "").strip() or None
    if reminder_time:
        if recurrence and recurrence not in VALID_RECURRENCES:
            raise HTTPException(status_code=400, detail=f"recurrence must be one of {sorted(VALID_RECURRENCES)}")
        next_at = _compute_next_reminder(reminder_time, recurrence)
        lst = database.update_list(new_id,
                                   reminder_time=reminder_time,
                                   reminder_recurrence=recurrence,
                                   reminder_next_at=next_at)
    return lst


@router.get("/lists/recent")
def recent_lists(n: int = 5, token=Depends(_auth)):
    return database.get_recent_lists(n)


@router.get("/lists/{list_id}")
def get_list(list_id: str, token=Depends(_auth)):
    lst = database.get_list(list_id)
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")
    lst["items"] = database.get_list_items(list_id)
    return lst


@router.put("/lists/order")
def reorder_lists(body: dict, token=Depends(_auth)):
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids must be an array")
    database.set_list_order(ids)
    return {"ok": True}


@router.put("/lists/{list_id}")
def update_list(list_id: str, body: dict, token=Depends(_auth)):
    lst = database.get_list(list_id)
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")

    updates = {}
    if "name" in body:
        name = (body["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        updates["name"] = name
    if "color" in body:
        color = body["color"] or None
        if color and color not in VALID_COLORS:
            raise HTTPException(status_code=400, detail=f"color must be one of {sorted(VALID_COLORS)}")
        updates["color"] = color
    if "reminder_time" in body or "reminder_recurrence" in body:
        reminder_time = (body.get("reminder_time") or "").strip() or None
        recurrence = (body.get("reminder_recurrence") or "").strip() or None
        if recurrence and recurrence not in VALID_RECURRENCES:
            raise HTTPException(status_code=400, detail=f"recurrence must be one of {sorted(VALID_RECURRENCES)}")
        updates["reminder_time"] = reminder_time
        updates["reminder_recurrence"] = recurrence
        if reminder_time:
            updates["reminder_next_at"] = _compute_next_reminder(reminder_time, recurrence)
        else:
            updates["reminder_next_at"] = None

    return database.update_list(list_id, **updates)


@router.delete("/lists/{list_id}")
def delete_list(list_id: str, confirmed: bool = False, token=Depends(_auth)):
    if not confirmed:
        raise HTTPException(status_code=400, detail="Pass ?confirmed=true to delete")
    lst = database.get_list(list_id)
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")
    database.delete_list(list_id)
    return {"ok": True, "deleted": list_id}


# ---------------------------------------------------------------------------
# List item endpoints
# ---------------------------------------------------------------------------

@router.get("/lists/{list_id}/items")
def get_items(list_id: str, token=Depends(_auth)):
    lst = database.get_list(list_id)
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")
    return database.get_list_items(list_id)


@router.post("/lists/{list_id}/items")
def create_item(list_id: str, body: dict, token=Depends(_auth)):
    lst = database.get_list(list_id)
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    indent = int(body.get("indent_level", 0))
    if indent not in (0, 1):
        raise HTTPException(status_code=400, detail="indent_level must be 0 or 1")
    db = database.get_db()
    new_id = id_generator.next_id(db)
    return database.create_list_item(new_id, list_id, content,
                                     note=body.get("note", ""),
                                     indent_level=indent)


@router.put("/lists/{list_id}/items/order")
def reorder_items(list_id: str, body: dict, token=Depends(_auth)):
    lst = database.get_list(list_id)
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids must be an array")
    database.reorder_list_items(list_id, ids)
    return {"ok": True}


@router.get("/lists/{list_id}/items/{item_id}")
def get_item(list_id: str, item_id: str, token=Depends(_auth)):
    item = database.get_list_item(item_id)
    if not item or item["list_id"] != list_id:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.put("/lists/{list_id}/items/{item_id}")
def update_item(list_id: str, item_id: str, body: dict, token=Depends(_auth)):
    item = database.get_list_item(item_id)
    if not item or item["list_id"] != list_id:
        raise HTTPException(status_code=404, detail="Item not found")

    updates = {}
    if "content" in body:
        content = (body["content"] or "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="content cannot be empty")
        updates["content"] = content
    if "checked" in body:
        updates["checked"] = 1 if body["checked"] else 0
    if "indent_level" in body:
        indent = int(body["indent_level"])
        if indent not in (0, 1):
            raise HTTPException(status_code=400, detail="indent_level must be 0 or 1")
        updates["indent_level"] = indent
    if "note" in body:
        updates["note"] = body["note"] or ""

    return database.update_list_item(item_id, **updates)


@router.delete("/lists/{list_id}/items/{item_id}")
def delete_item(list_id: str, item_id: str, token=Depends(_auth)):
    item = database.get_list_item(item_id)
    if not item or item["list_id"] != list_id:
        raise HTTPException(status_code=404, detail="Item not found")
    database.delete_list_item(item_id)
    return {"ok": True, "deleted": item_id}


@router.post("/lists/{list_id}/uncheck-all")
def uncheck_all(list_id: str, token=Depends(_auth)):
    lst = database.get_list(list_id)
    if not lst:
        raise HTTPException(status_code=404, detail="List not found")
    database.uncheck_all_list_items(list_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Graduate list item → strategic goal
# ---------------------------------------------------------------------------

@router.post("/lists/{list_id}/items/{item_id}/graduate")
def graduate_item(list_id: str, item_id: str, token=Depends(_auth)):
    """Promote a list item to a Backlog strategic goal."""
    from goalforge import id_generator as _idg

    item = database.get_list_item(item_id)
    if not item or item["list_id"] != list_id:
        raise HTTPException(status_code=404, detail="Item not found")

    db = database.get_db()
    new_goal_id = _idg.next_id(db)
    database.upsert_goal({
        "id": new_goal_id,
        "name": item["content"],
        "description": item.get("note") or "",
        "status": "Backlog",
        "horizon": "Yearly",
        "is_milestone": False,
        "notify_before_days": 3,
    })
    # Mark item as checked and append goal ID to note
    existing_note = item.get("note") or ""
    new_note = (existing_note + f"\n\nGraduated Goal: {new_goal_id}").strip()
    database.update_list_item(item_id, checked=1, note=new_note)

    logger.info("List item %s graduated to goal %s", item_id, new_goal_id)
    return {"item_id": item_id, "goal_id": new_goal_id, "goal": database.get_goal(new_goal_id)}


# ---------------------------------------------------------------------------
# AI generate list items
# ---------------------------------------------------------------------------

@router.post("/lists/generate")
def generate_list(body: dict, token=Depends(_auth)):
    """
    Use the configured LLM to generate a list of items from a text prompt.
    Body: {"prompt": "packing list for camping", "list_name": "optional"}
    Returns: {"items": ["item1", "item2", ...], "list_name": "suggested name"}
    Optionally pass "create": true to also create the list and items immediately.
    """
    prompt_text = (body.get("prompt") or "").strip()
    if not prompt_text:
        raise HTTPException(status_code=400, detail="prompt is required")

    from goalforge.llm.factory import get_provider
    provider = get_provider()

    system = (
        "You are a helpful assistant that generates concise, actionable list items. "
        "Respond ONLY with a JSON object in this exact format:\n"
        '{"list_name": "<short descriptive name>", "items": ["item 1", "item 2", ...]}\n'
        "Generate 5-15 items. No markdown, no extra text."
    )
    messages = [{"role": "user", "content": f"Create a list for: {prompt_text}"}]

    import json
    try:
        raw = provider.chat(system, messages)
        # Strip markdown fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(raw)
        items = [str(i).strip() for i in data.get("items", []) if str(i).strip()]
        list_name = body.get("list_name") or data.get("list_name") or "Generated List"
    except Exception as e:
        logger.error("AI list generation failed: %s", e)
        raise HTTPException(status_code=500, detail=f"AI generation failed: {e}")

    if not items:
        raise HTTPException(status_code=500, detail="AI returned no items")

    if body.get("create"):
        db = database.get_db()
        new_list_id = id_generator.next_id(db)
        database.create_list(new_list_id, list_name, color=body.get("color"))
        for idx, content in enumerate(items):
            item_id = id_generator.next_id(db)
            database.create_list_item(item_id, new_list_id, content)
        logger.info("AI generated list '%s' with %d items", list_name, len(items))
        lst = database.get_list(new_list_id)
        lst["items"] = database.get_list_items(new_list_id)
        return lst

    return {"list_name": list_name, "items": items}
