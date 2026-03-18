"""
Vault operations for interactive mode.
All tools are called by the LLM during a chat session. Not exposed directly to users.
All write/delete operations are logged to vault_changes.log.
All paths are validated to stay within vault root.
"""
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

import frontmatter

from goalforge.config import config
from goalforge import database, id_generator

logger = logging.getLogger(__name__)

# Separate logger for vault changes audit trail
changes_logger = logging.getLogger("vault_changes")


def _vault_root() -> Path:
    return Path(config.vault_path)


def _goals_path() -> Path:
    return _vault_root() / config.goals_folder


def _validate_path(path: Path) -> Path:
    """Raise ValueError if path escapes vault root (path traversal guard)."""
    try:
        path.resolve().relative_to(_vault_root().resolve())
        return path
    except ValueError:
        raise ValueError(f"Path '{path}' is outside the vault root — rejected.")


def _log_change(operation: str, path: str, field: str = None, old_value=None, new_value=None):
    msg = f"op={operation} path={path}"
    if field:
        msg += f" field={field} old={old_value!r} new={new_value!r}"
    changes_logger.info(msg)


# ---------------------------------------------------------------------------
# READ OPERATIONS
# ---------------------------------------------------------------------------

def read_goal(id_or_name: str) -> dict:
    """Read a goal by ID or partial name match."""
    # Try exact ID match first
    goal = database.get_goal(id_or_name)
    if not goal:
        # Search by name (case-insensitive partial match)
        all_goals = database.get_all_goals()
        matches = [g for g in all_goals if id_or_name.lower() in g.get("name", "").lower()]
        if not matches:
            raise ValueError(f"No goal found matching '{id_or_name}'")
        if len(matches) > 1:
            names = ", ".join(f"{g['id']}: {g['name']}" for g in matches[:5])
            raise ValueError(f"Multiple goals match '{id_or_name}': {names}")
        goal = matches[0]

    # Enrich with file body
    file_path = goal.get("file_path")
    if file_path and Path(file_path).exists():
        post = frontmatter.load(file_path)
        goal["body"] = post.content
    else:
        goal["body"] = ""

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


# ---------------------------------------------------------------------------
# WRITE OPERATIONS
# ---------------------------------------------------------------------------

def update_goal_field(goal_id: str, field: str, value: str) -> dict:
    """Update a single frontmatter field on a goal."""
    goal = database.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")

    old_value = goal.get(field)

    # Update DB
    database.update_field(goal_id, field, value)

    # Update frontmatter file
    file_path = goal.get("file_path")
    if file_path and Path(file_path).exists():
        _validate_path(Path(file_path))
        post = frontmatter.load(file_path)
        post[field] = value
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(frontmatter.dumps(post))

    _log_change("update_field", file_path or goal_id, field=field, old_value=old_value, new_value=value)
    return database.get_goal(goal_id)


def promote_to_full_goal(goal_id: str) -> dict:
    """Promote a milestone to a full goal (is_milestone = false)."""
    from goalforge.planner import promote_to_full_goal as _promote
    _promote(goal_id)
    _log_change("promote_to_full_goal", goal_id)
    return database.get_goal(goal_id)


def demote_to_milestone(goal_id: str) -> dict:
    """Demote a full goal to a milestone (is_milestone = true)."""
    goal = database.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")

    database.set_is_milestone(goal_id, True)

    file_path = goal.get("file_path")
    if file_path and Path(file_path).exists():
        _validate_path(Path(file_path))
        post = frontmatter.load(file_path)
        post["is_milestone"] = True
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(frontmatter.dumps(post))

    _log_change("demote_to_milestone", file_path or goal_id)
    return database.get_goal(goal_id)


def reparent_goal(goal_id: str, new_parent_id: Optional[str]) -> dict:
    """
    Move a goal to a different parent (or make it a root goal if new_parent_id is None).
    Recomputes depth for the moved goal and all descendants.
    """
    goal = database.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")
    if new_parent_id and not database.get_goal(new_parent_id):
        raise ValueError(f"New parent {new_parent_id} not found")

    old_parent = goal.get("parent_goal_id")
    database.set_parent(goal_id, new_parent_id)

    # Update frontmatter
    file_path = goal.get("file_path")
    if file_path and Path(file_path).exists():
        _validate_path(Path(file_path))
        post = frontmatter.load(file_path)
        post["parent_goal_id"] = new_parent_id or ""
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(frontmatter.dumps(post))

    _log_change("reparent_goal", file_path or goal_id,
                field="parent_goal_id", old_value=old_parent, new_value=new_parent_id)
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
    """Create a new goal file and DB record."""
    db = database.get_db()
    new_id = id_generator.next_id(db)

    goals_path = _goals_path()
    _validate_path(goals_path)
    goals_path.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)[:60]
    file_path = goals_path / f"{new_id} {safe_name}.md"

    depth = 0
    if parent_goal_id:
        parent = database.get_goal(parent_goal_id)
        depth = (parent.get("depth", 0) + 1) if parent else 1

    body = f"""## Summary
| Field       | Value                   |
|-------------|-------------------------|
| Goal        | {name}                  |
| Due         | {due_date or ''}        |
| Horizon     | {horizon}               |
| Status      | Backlog                 |

## Description
{description}

## Child Goals
| ID | Name | Type | Due Date | Status |
|----|------|------|----------|--------|
"""

    post = frontmatter.Post(
        content=body,
        id=new_id,
        name=name,
        status="Backlog",
        horizon=horizon,
        due_date=due_date or "",
        parent_goal_id=parent_goal_id or "",
        depth=depth,
        is_milestone=is_milestone,
        category=category,
        created_date=date.today().isoformat(),
        notify_before_days=3,
        tags=["goal"],
    )

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))

    goal_dict = {
        "id": new_id,
        "name": name,
        "status": "Backlog",
        "horizon": horizon,
        "due_date": due_date,
        "parent_goal_id": parent_goal_id,
        "depth": depth,
        "is_milestone": is_milestone,
        "category": category,
        "created_date": date.today().isoformat(),
        "notify_before_days": 3,
        "file_path": str(file_path),
    }
    database.upsert_goal(goal_dict)
    _log_change("create_goal", str(file_path), field="name", new_value=name)
    return database.get_goal(new_id) or goal_dict


def delete_goal(goal_id: str, confirmed: bool = False) -> dict:
    """
    Delete a goal file and DB record.
    confirmed must be True — the interactive layer only sets this after user confirms.
    """
    if not confirmed:
        raise ValueError("Delete requires confirmed=True. Ask the user to confirm first.")

    goal = database.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")

    file_path = goal.get("file_path")
    if file_path and Path(file_path).exists():
        _validate_path(Path(file_path))
        Path(file_path).unlink()
        _log_change("delete_goal", file_path)

    database.delete_goal(goal_id)
    logger.info("Deleted goal %s", goal_id)
    return {"deleted": goal_id, "name": goal.get("name")}


# ---------------------------------------------------------------------------
# NOTE OPERATIONS (general vault files)
# ---------------------------------------------------------------------------

def read_note(path: str) -> str:
    """Read any vault file by relative path."""
    full_path = _vault_root() / path
    _validate_path(full_path)
    if not full_path.exists():
        raise FileNotFoundError(f"Note not found: {path}")
    return full_path.read_text(encoding="utf-8")


def list_notes(folder: str) -> list[str]:
    """List files in a vault folder. Returns relative paths."""
    full_path = _vault_root() / folder
    _validate_path(full_path)
    if not full_path.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")
    return [str(p.relative_to(_vault_root())) for p in full_path.rglob("*") if p.is_file()]


def search_vault(query: str) -> list[dict]:
    """Full-text search across all vault .md files."""
    results = []
    query_lower = query.lower()
    for md_file in _vault_root().rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8")
            if query_lower in text.lower():
                # Find matching lines
                lines = [
                    {"line": i + 1, "text": line.strip()}
                    for i, line in enumerate(text.splitlines())
                    if query_lower in line.lower()
                ]
                results.append({
                    "path": str(md_file.relative_to(_vault_root())),
                    "matches": lines[:5],
                })
        except Exception:
            pass
    return results
