"""
Goal operations for interactive mode.
All tools are called by the LLM during a chat session.
All write/delete operations go directly to the database.
"""
import logging
from datetime import date
from typing import Optional

from goalforge.config import config
from goalforge import database, id_generator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# READ OPERATIONS
# ---------------------------------------------------------------------------

def read_goal(id_or_name: str) -> dict:
    """Read a goal by ID or partial name match."""
    goal = database.get_goal(id_or_name)
    if not goal:
        all_goals = database.get_all_goals()
        matches = [g for g in all_goals if id_or_name.lower() in g.get("name", "").lower()]
        if not matches:
            raise ValueError(f"No goal found matching '{id_or_name}'")
        if len(matches) > 1:
            names = ", ".join(f"{g['id']}: {g['name']}" for g in matches[:5])
            raise ValueError(f"Multiple goals match '{id_or_name}': {names}")
        goal = matches[0]
    return goal


def list_goals(
    status: Optional[str] = None,
    horizon: Optional[str] = None,
    category: Optional[str] = None,
    is_milestone: Optional[bool] = None,
    parent_goal_id: Optional[str] = None,
) -> list[dict]:
    """List goals with optional filters."""
    filters = {}
    if status:
        filters["status"] = status
    if horizon:
        filters["horizon"] = horizon
    if category:
        filters["category"] = category
    if is_milestone is not None:
        filters["is_milestone"] = is_milestone
    if parent_goal_id is not None:
        filters["parent_goal_id"] = parent_goal_id
    return database.get_all_goals(filters)


def get_goal_tree(goal_id: str, max_depth: int = 5) -> dict:
    """Return a goal and its full child hierarchy up to max_depth."""
    goal = database.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")

    def _build_tree(g: dict, current_depth: int) -> dict:
        node = dict(g)
        if current_depth < max_depth:
            children = database.get_children(g["id"], recursive=False)
            node["children"] = [_build_tree(c, current_depth + 1) for c in children]
        else:
            node["children"] = []
        return node

    return _build_tree(goal, 0)


def get_ancestors(goal_id: str) -> list[dict]:
    """Return parent chain from root to immediate parent."""
    goal = database.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")
    return database.get_ancestors(goal_id)


def search_goals(query: str) -> list[dict]:
    """Search goals by name or description."""
    return database.search_goals(query)


# ---------------------------------------------------------------------------
# WRITE OPERATIONS
# ---------------------------------------------------------------------------

def update_goal_field(goal_id: str, field: str, value: str) -> dict:
    """Update a single field on a goal."""
    goal = database.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")
    database.update_field(goal_id, field, value)
    logger.info("Updated %s.%s = %r", goal_id, field, value)
    return database.get_goal(goal_id)


def promote_to_full_goal(goal_id: str) -> dict:
    """Promote a milestone to a full goal (is_milestone = false)."""
    from goalforge.planner import promote_to_full_goal as _promote
    _promote(goal_id)
    return database.get_goal(goal_id)


def demote_to_milestone(goal_id: str) -> dict:
    """Demote a full goal to a milestone (is_milestone = true)."""
    goal = database.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")
    database.set_is_milestone(goal_id, True)
    logger.info("Demoted %s to milestone", goal_id)
    return database.get_goal(goal_id)


def reparent_goal(goal_id: str, new_parent_id: Optional[str]) -> dict:
    """Move a goal to a different parent (or make it a root goal if new_parent_id is None)."""
    goal = database.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")
    if new_parent_id and not database.get_goal(new_parent_id):
        raise ValueError(f"New parent {new_parent_id} not found")
    database.set_parent(goal_id, new_parent_id)
    logger.info("Reparented %s to %s", goal_id, new_parent_id)
    return database.get_goal(goal_id)


def create_goal(
    name: str,
    description: str = "",
    horizon: str = "Monthly",
    due_date: Optional[str] = None,
    category: str = "",
    parent_goal_id: Optional[str] = None,
    is_milestone: bool = False,
) -> dict:
    """Create a new goal record in the database."""
    db = database.get_db()
    new_id = id_generator.next_id(db)

    depth = 0
    if parent_goal_id:
        parent = database.get_goal(parent_goal_id)
        depth = (parent.get("depth", 0) + 1) if parent else 1

    goal_dict = {
        "id": new_id,
        "name": name,
        "description": description,
        "status": "Backlog",
        "horizon": horizon,
        "due_date": due_date,
        "parent_goal_id": parent_goal_id,
        "depth": depth,
        "is_milestone": is_milestone,
        "category": category,
        "created_date": date.today().isoformat(),
        "notify_before_days": 3,
        "tags": '["goal"]',
    }
    database.upsert_goal(goal_dict)
    logger.info("Created goal %s: %s", new_id, name)
    return database.get_goal(new_id) or goal_dict


def delete_goal(goal_id: str, confirmed: bool = False) -> dict:
    """
    Delete a goal and its DB record.
    confirmed must be True — the interactive layer only sets this after user confirms.
    """
    if not confirmed:
        raise ValueError("Delete requires confirmed=True. Ask the user to confirm first.")

    goal = database.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")

    database.delete_goal(goal_id)
    logger.info("Deleted goal %s", goal_id)
    return {"deleted": goal_id, "name": goal.get("name")}


# ---------------------------------------------------------------------------
# LIST OPERATIONS (for LLM tool calling)
# ---------------------------------------------------------------------------

def list_lists_tool() -> list[dict]:
    """Return all lists with item counts."""
    return database.get_lists()


def read_list(list_id: str) -> dict:
    """Read a list and all its items by list ID."""
    lst = database.get_list(list_id)
    if not lst:
        raise ValueError(f"List {list_id} not found")
    lst["items"] = database.get_list_items(list_id)
    return lst


def create_list_item_tool(list_id: str, content: str, note: str = "") -> dict:
    """Add an item to an existing list."""
    lst = database.get_list(list_id)
    if not lst:
        raise ValueError(f"List {list_id} not found")
    db = database.get_db()
    new_id = id_generator.next_id(db)
    return database.create_list_item(new_id, list_id, content, note=note)


def update_list_item_tool(item_id: str, content: Optional[str] = None,
                          checked: Optional[bool] = None, note: Optional[str] = None) -> dict:
    """Update a list item's content, checked state, or note."""
    item = database.get_list_item(item_id)
    if not item:
        raise ValueError(f"List item {item_id} not found")
    updates = {}
    if content is not None:
        updates["content"] = content
    if checked is not None:
        updates["checked"] = 1 if checked else 0
    if note is not None:
        updates["note"] = note
    return database.update_list_item(item_id, **updates)


def graduate_list_item(item_id: str, confirmed: bool = False) -> dict:
    """
    Graduate a list item to a strategic goal (Backlog status).
    confirmed must be True — ask the user before setting this.
    """
    if not confirmed:
        raise ValueError("Graduate requires confirmed=True. Ask the user to confirm first.")
    item = database.get_list_item(item_id)
    if not item:
        raise ValueError(f"List item {item_id} not found")
    db = database.get_db()
    new_goal_id = id_generator.next_id(db)
    database.upsert_goal({
        "id": new_goal_id,
        "name": item["content"],
        "description": item.get("note") or "",
        "status": "Backlog",
        "horizon": "Yearly",
        "is_milestone": False,
        "notify_before_days": 3,
    })
    existing_note = item.get("note") or ""
    new_note = (existing_note + f"\n\nGraduated Goal: {new_goal_id}").strip()
    database.update_list_item(item_id, checked=1, note=new_note)
    logger.info("List item %s graduated to goal %s via chat", item_id, new_goal_id)
    return {"item_id": item_id, "goal_id": new_goal_id, "goal": database.get_goal(new_goal_id)}


def demote_goal_to_list(goal_id: str, list_id: Optional[str] = None) -> dict:
    """
    Convert a strategic goal to a list item, then delete the goal.
    If list_id is None, adds to a list named 'Ideas' (creating it if needed).
    """
    goal = database.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")

    # Resolve target list
    if list_id:
        lst = database.get_list(list_id)
        if not lst:
            raise ValueError(f"List {list_id} not found")
    else:
        # Find or create an "Ideas" list
        all_lists = database.get_lists()
        ideas_list = next((l for l in all_lists if l["name"].lower() == "ideas"), None)
        if ideas_list:
            list_id = ideas_list["id"]
        else:
            db = database.get_db()
            list_id = id_generator.next_id(db)
            database.create_list(list_id, "Ideas")

    db = database.get_db()
    new_item_id = id_generator.next_id(db)
    note_parts = []
    if goal.get("description"):
        note_parts.append(f"Description:\n{goal['description']}")
    if goal.get("progress_notes"):
        note_parts.append(f"Progress Notes:\n{goal['progress_notes']}")
    if goal.get("status"):
        note_parts.append(f"Was Goal Status: {goal['status']}")
    note = "\n\n".join(note_parts)

    database.create_list_item(new_item_id, list_id, goal["name"], note=note)
    database.delete_goal(goal_id)
    logger.info("Goal %s demoted to list item %s in list %s", goal_id, new_item_id, list_id)
    return {"goal_id": goal_id, "item_id": new_item_id, "item": database.get_list_item(new_item_id)}
