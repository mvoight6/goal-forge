"""
AI goal planner — generates 3-5 child goals for any goal using the active LLM provider.
Creates child goals directly in the database.
"""
import json
import logging
from datetime import date
from typing import Optional

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


def _create_child_goal(child: dict, parent: dict) -> str:
    """Create a child goal record directly in the database. Returns the new goal ID."""
    db = database.get_db()
    new_id = id_generator.next_id(db)
    child["id"] = new_id

    goal_dict = {
        "id": new_id,
        "name": child["name"],
        "description": child.get("description", ""),
        "status": "Backlog",
        "horizon": child.get("horizon", ""),
        "due_date": child.get("due_date") or None,
        "parent_goal_id": parent["id"],
        "depth": parent.get("depth", 0) + 1,
        "is_milestone": child.get("is_milestone", False),
        "category": parent.get("category", ""),
        "created_date": date.today().isoformat(),
        "notify_before_days": child.get("notify_before_days", 3),
        "tags": '["goal"]',
    }
    database.upsert_goal(goal_dict)
    logger.info("Created child goal: %s %s", new_id, child["name"])
    return new_id


def promote_to_full_goal(goal_id: str):
    """Promote a milestone to a full goal (is_milestone = false)."""
    goal = database.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")

    database.set_is_milestone(goal_id, False)
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
            _create_child_goal(child, goal)
            created.append(database.get_goal(child["id"]))
        except Exception as e:
            logger.error("Failed to create child goal '%s': %s", child.get("name"), e)

    logger.info("Created %d child goals for %s", len(created), goal_id)
    return created
