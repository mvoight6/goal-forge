"""
AI goal planner — generates 3-5 child goals for any goal using the active LLM provider.
Writes new .md files, updates the parent's Child Goals table, triggers a scanner run.
"""
import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import frontmatter

from goalforge.config import config
from goalforge import database, id_generator
from goalforge.llm.factory import get_provider

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a goal planning assistant. Given a goal and its context, generate 3-5 concrete child goals or milestones that will help achieve it.

You must respond with a valid JSON array. Each item must have these fields:
- name (string): Short, action-oriented name
- description (string): What specifically needs to happen
- due_date (string): ISO date YYYY-MM-DD — must be on or before the parent's due date
- notify_before_days (integer): 1-7
- is_milestone (boolean): true for simple checkpoints, false for goals that need their own planning
- horizon (string): Daily | Weekly | Monthly | Quarterly | Yearly | Life

Return ONLY the JSON array. No markdown, no explanation."""


def _build_prompt(goal: dict, ancestors: list[dict]) -> str:
    ancestor_names = " → ".join(a["name"] for a in ancestors) if ancestors else "None (root goal)"
    depth_note = {
        0: "This is a root goal — children can be broad sub-goals or milestones.",
        1: "This is a first-level child — children should be concrete tasks or milestones.",
    }.get(goal.get("depth", 0), "This is a deep child — children should be very specific, small tasks.")

    return f"""Goal: {goal['name']}
Description: {goal.get('description', 'No description provided.')}
Due date: {goal.get('due_date', 'Not set')}
Horizon: {goal.get('horizon', 'Not set')}
Depth: {goal.get('depth', 0)} ({depth_note})
Ancestor chain: {ancestor_names}
Today's date: {date.today().isoformat()}

Generate 3-5 child goals or milestones for this goal."""


def _goal_body(child: dict, parent: dict) -> str:
    parent_name = parent.get("name", "")
    parent_id = parent.get("id", "")
    return f"""## Summary
| Field       | Value                          |
|-------------|--------------------------------|
| Goal        | {child['name']}                |
| Due         | {child.get('due_date', '')}    |
| Horizon     | {child.get('horizon', '')}     |
| Status      | Backlog                        |
| Parent Goal | {parent_name} ({parent_id})    |

## Description
{child.get('description', '')}

## Child Goals
| ID | Name | Type | Due Date | Status |
|----|------|------|----------|--------|
"""


def _write_child_file(child: dict, parent: dict) -> Path:
    """Write a new .md file for a child goal. Returns the path."""
    db = database.get_db()
    new_id = id_generator.next_id(db)
    child["id"] = new_id

    goals_path = Path(config.vault_path) / config.goals_folder
    goals_path.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in child["name"])[:60]
    file_path = goals_path / f"{new_id} {safe_name}.md"

    post = frontmatter.Post(
        content=_goal_body(child, parent),
        id=new_id,
        name=child["name"],
        status="Backlog",
        horizon=child.get("horizon", ""),
        due_date=child.get("due_date", ""),
        parent_goal_id=parent["id"],
        depth=parent.get("depth", 0) + 1,
        is_milestone=child.get("is_milestone", False),
        category=parent.get("category", ""),
        created_date=date.today().isoformat(),
        notify_before_days=child.get("notify_before_days", 3),
        tags=["goal"],
    )

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))

    logger.info("Created child goal file: %s", file_path.name)
    return file_path


def _update_parent_child_table(parent: dict, children: list[dict]):
    """Rewrite the ## Child Goals section in the parent's .md file."""
    file_path = parent.get("file_path")
    if not file_path or not Path(file_path).exists():
        logger.warning("Parent file not found for Child Goals update: %s", file_path)
        return

    db_children = database.get_children(parent["id"], recursive=False)

    rows = ""
    for c in db_children:
        child_type = "Milestone" if c.get("is_milestone") else "Full Goal"
        rows += f"| {c['id']} | {c['name']} | {child_type} | {c.get('due_date', '')} | {c.get('status', '')} |\n"

    section = f"""## Child Goals
| ID | Name | Type | Due Date | Status |
|----|------|------|----------|--------|
{rows}"""

    post = frontmatter.load(file_path)
    content = post.content

    if "## Child Goals" in content:
        # Replace existing section up to next ## or EOF
        import re
        content = re.sub(
            r"## Child Goals\n.*?(?=\n## |\Z)",
            section + "\n",
            content,
            flags=re.DOTALL,
        )
    else:
        content = content.rstrip() + "\n\n" + section + "\n"

    post.content = content
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))
    logger.info("Updated Child Goals table in %s", Path(file_path).name)


def promote_to_full_goal(goal_id: str):
    """
    Promote a milestone to a full goal:
    - Set is_milestone: false in frontmatter and DB
    - Regenerate parent's Child Goals table
    """
    goal = database.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")

    # Update DB
    database.set_is_milestone(goal_id, False)

    # Update frontmatter
    file_path = goal.get("file_path")
    if file_path and Path(file_path).exists():
        post = frontmatter.load(file_path)
        post["is_milestone"] = False
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(frontmatter.dumps(post))

    # Regenerate parent's child table
    if goal.get("parent_goal_id"):
        parent = database.get_goal(goal["parent_goal_id"])
        if parent:
            _update_parent_child_table(parent, [])

    logger.info("Promoted %s to full goal", goal_id)


def plan_goal(goal_id: str) -> list[dict]:
    """
    Generate child goals for goal_id using the LLM.
    Returns the list of created child dicts with their assigned IDs.
    """
    goal = database.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")

    # Auto-promote milestones before planning
    if goal.get("is_milestone"):
        logger.info("Auto-promoting milestone %s before planning", goal_id)
        promote_to_full_goal(goal_id)
        goal = database.get_goal(goal_id)

    ancestors = database.get_ancestors(goal_id)
    prompt = _build_prompt(goal, ancestors)

    provider = get_provider()
    logger.info("Generating child goals for %s via LLM...", goal_id)
    raw = provider.chat(system=SYSTEM_PROMPT, messages=[{"role": "user", "content": prompt}], json_mode=True)

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            # Unwrap {"goals": [...]} or any single-key dict wrapping the array
            lists = [v for v in parsed.values() if isinstance(v, list)]
            if lists:
                parsed = lists[0]
            else:
                # Single goal object returned — wrap it
                parsed = [parsed]
        if not isinstance(parsed, list):
            raise ValueError("LLM response is not a JSON array")
        children = parsed
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("LLM returned invalid JSON: %s\nRaw: %s", e, raw)
        raise RuntimeError(f"LLM returned invalid JSON: {e}") from e

    created = []
    for child in children:
        try:
            file_path = _write_child_file(child, goal)
            child["file_path"] = str(file_path)

            # Upsert into DB
            db_record = {
                **child,
                "parent_goal_id": goal["id"],
                "depth": goal.get("depth", 0) + 1,
                "status": "Backlog",
                "created_date": date.today().isoformat(),
            }
            database.upsert_goal(db_record)
            created.append(child)
        except Exception as e:
            logger.error("Failed to create child goal '%s': %s", child.get("name"), e)

    # Update parent's Child Goals table
    _update_parent_child_table(goal, created)

    # Trigger a scan to pick up new files
    try:
        from goalforge import scanner
        scanner.run_scan()
    except Exception as e:
        logger.warning("Post-plan scan failed: %s", e)

    logger.info("Created %d child goals for %s", len(created), goal_id)
    return created
