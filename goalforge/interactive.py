"""
Interactive chat mode — stateful LLM conversations with native tool calling.
Backs the PWA chat interface via POST /chat and DELETE /chat/{session_id}.
"""
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from goalforge.config import config
from goalforge.llm.factory import get_provider
from goalforge.llm.base import ToolCall
from datetime import date
from goalforge import vault_tools, daily_api

logger = logging.getLogger(__name__)
router = APIRouter()
bearer = HTTPBearer()

_sessions: dict[str, list[dict]] = {}

SYSTEM_PROMPT_TEMPLATE = """You are Joe MacMillan — visionary, driven, and genuinely invested in the person you're working with.

You've built things, lost things, and rebuilt them. You know that the gap between where someone is and where they want to be is real — but it's crossable. Your job is to help close that gap, one goal at a time.

You're direct without being harsh. You push when pushing helps, but you listen first. You believe the best ideas come from real conversation, not lectures. When someone shares a goal with you, you take it seriously — you help them shape it, plan it, and actually move on it.

Your voice: confident, warm when it counts, occasionally poetic about what's possible. You use short sentences when making a point. You don't pad your words. But you also know when to slow down and really hear someone.

Keep responses concise — they appear on a mobile screen.

## Goal System Structure

There are two distinct types of goals:

**Strategic Goals** — created with `create_goal`. These live in the Goals/ vault folder and represent longer-term objectives (Weekly, Monthly, Quarterly, Yearly, Life horizon). Use these for anything that is a real project or ambition.

**Daily Goals** — created with `create_daily_item`. These are simple checklist items for a specific day (like Google Keep). They live in the Daily/ vault folder. Use these when the user says things like "add to my daily goals", "add to today's list", "remind me to do X today/tomorrow", or anything that is a short task for a specific day.

Today's date is {today}. Tomorrow is {tomorrow}. Always use the correct ISO date (YYYY-MM-DD) based on these values."""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_goal",
            "description": "Read a goal's full details by ID (e.g. GF-0001) or partial name",
            "parameters": {
                "type": "object",
                "properties": {
                    "id_or_name": {"type": "string", "description": "Goal ID or partial name to search for"}
                },
                "required": ["id_or_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_goals",
            "description": "List goals, optionally filtered by status, horizon, category, is_milestone, or parent_goal_id",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["Draft", "Backlog", "Active", "Blocked", "Completed"]},
                    "horizon": {"type": "string", "enum": ["Daily", "Weekly", "Monthly", "Quarterly", "Yearly", "Life"]},
                    "category": {"type": "string"},
                    "is_milestone": {"type": "boolean"},
                    "parent_goal_id": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_goal_tree",
            "description": "Get a goal and its full child hierarchy",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {"type": "string"},
                    "max_depth": {"type": "integer", "default": 5},
                },
                "required": ["goal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ancestors",
            "description": "Get the parent chain up to root for a goal",
            "parameters": {
                "type": "object",
                "properties": {"goal_id": {"type": "string"}},
                "required": ["goal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_goal_field",
            "description": "Update a single field on a goal (name, status, horizon, due_date, category, notify_before_days)",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {"type": "string"},
                    "field": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["goal_id", "field", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "promote_to_full_goal",
            "description": "Promote a milestone to a full goal",
            "parameters": {
                "type": "object",
                "properties": {"goal_id": {"type": "string"}},
                "required": ["goal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "demote_to_milestone",
            "description": "Demote a full goal to a milestone",
            "parameters": {
                "type": "object",
                "properties": {"goal_id": {"type": "string"}},
                "required": ["goal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reparent_goal",
            "description": "Move a goal to a different parent, or make it a root goal by passing null for new_parent_id",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {"type": "string"},
                    "new_parent_id": {"type": ["string", "null"]},
                },
                "required": ["goal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_goal",
            "description": "Create a new goal file in the vault",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short, action-oriented goal name"},
                    "description": {"type": "string"},
                    "horizon": {"type": "string", "enum": ["Daily", "Weekly", "Monthly", "Quarterly", "Yearly", "Life"]},
                    "due_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                    "category": {"type": "string"},
                    "parent_goal_id": {"type": "string"},
                    "is_milestone": {"type": "boolean"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_goal",
            "description": "Delete a goal. You MUST ask the user to confirm before setting confirmed=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_id": {"type": "string"},
                    "confirmed": {"type": "boolean", "description": "Must be true — only set after user explicitly confirms"},
                },
                "required": ["goal_id", "confirmed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_daily_item",
            "description": "Add a checklist item to a specific day's Daily Goals list. Use this — NOT create_goal — when the user wants to add a task for today, tomorrow, or any specific day.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short description of the task"},
                    "date_str": {"type": "string", "description": "ISO date YYYY-MM-DD for the day this task belongs to"},
                },
                "required": ["name", "date_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_vault",
            "description": "Full-text search across all vault markdown files",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_note",
            "description": "Read any vault file by relative path",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_notes",
            "description": "List files in a vault folder",
            "parameters": {
                "type": "object",
                "properties": {"folder": {"type": "string"}},
                "required": ["folder"],
            },
        },
    },
]

# Map tool names to vault_tools functions
# Note: interactive.py uses goal_id as the param name; vault_tools uses id_or_name or goal_id depending on function
TOOL_DISPATCH = {
    "read_goal":           lambda a: vault_tools.read_goal(**a),
    "list_goals":          lambda a: vault_tools.list_goals(**a),
    "get_goal_tree":       lambda a: vault_tools.get_goal_tree(**a),
    "get_ancestors":       lambda a: vault_tools.get_ancestors(**a),
    "update_goal_field":   lambda a: vault_tools.update_goal_field(**_remap(a, "goal_id", "goal_id")),
    "promote_to_full_goal":lambda a: vault_tools.promote_to_full_goal(**_remap(a, "goal_id", "goal_id")),
    "demote_to_milestone": lambda a: vault_tools.demote_to_milestone(**_remap(a, "goal_id", "goal_id")),
    "reparent_goal":       lambda a: vault_tools.reparent_goal(**_remap(a, "goal_id", "goal_id")),
    "create_goal":         lambda a: vault_tools.create_goal(**a),
    "create_daily_item":   lambda a: daily_api.add_daily_item_for_date(**a),
    "delete_goal":         lambda a: vault_tools.delete_goal(**_remap(a, "goal_id", "goal_id")),
    "search_vault":        lambda a: vault_tools.search_vault(**a),
    "read_note":           lambda a: vault_tools.read_note(**a),
    "list_notes":          lambda a: vault_tools.list_notes(**a),
}


def _remap(args: dict, from_key: str, to_key: str) -> dict:
    """Rename a key in args dict if needed."""
    if from_key in args and to_key != from_key:
        args = dict(args)
        args[to_key] = args.pop(from_key)
    return args


def _execute_tool(tc: ToolCall) -> str:
    fn = TOOL_DISPATCH.get(tc.name)
    if not fn:
        return json.dumps({"error": f"Unknown tool: {tc.name}"})
    try:
        result = fn(tc.arguments)
        return json.dumps(result, default=str)
    except Exception as e:
        logger.warning("Tool '%s' error: %s", tc.name, e)
        return json.dumps({"error": str(e)})


def _auth(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if credentials.credentials != config.api.secret_token:
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials.credentials


def _build_system_prompt() -> str:
    today = date.today()
    from datetime import timedelta
    tomorrow = today + timedelta(days=1)
    return SYSTEM_PROMPT_TEMPLATE.format(
        today=today.isoformat(),
        tomorrow=tomorrow.isoformat(),
    )


def chat(session_id: str, message: str) -> dict:
    if session_id not in _sessions:
        _sessions[session_id] = []

    history = _sessions[session_id]
    history.append({"role": "user", "content": message})

    provider = get_provider()
    executed_tools = []
    reply = ""

    MAX_TOOL_ROUNDS = 10
    for _ in range(MAX_TOOL_ROUNDS):
        content, tool_calls = provider.chat_with_tools(
            system=_build_system_prompt(),
            messages=history,
            tools=TOOL_SCHEMAS,
        )

        if not tool_calls:
            reply = content
            history.append({"role": "assistant", "content": reply})
            break

        # Add assistant turn with tool calls
        history.append({
            "role": "assistant",
            "content": content or "",
            "tool_calls": [
                {
                    "id": tc.id or f"call_{tc.name}",
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in tool_calls
            ],
        })

        # Execute each tool and feed results back
        for tc in tool_calls:
            logger.info("Tool call: %s(%s)", tc.name, tc.arguments)
            result_str = _execute_tool(tc)
            executed_tools.append({
                "tool": tc.name,
                "args": tc.arguments,
                "result": json.loads(result_str),
            })
            history.append({
                "role": "tool",
                "tool_call_id": tc.id or f"call_{tc.name}",
                "content": result_str,
            })
    else:
        reply = "I ran into an issue completing that request. Try rephrasing or breaking it into smaller steps."
        history.append({"role": "assistant", "content": reply})

    return {"reply": reply, "tool_calls": executed_tools}


def clear_session(session_id: str):
    _sessions.pop(session_id, None)


# ---------------------------------------------------------------------------
# FastAPI endpoints
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: str = ""
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    tool_calls: list


@router.post("/chat", response_model=ChatResponse)
def post_chat(req: ChatRequest, token: str = Depends(_auth)):
    session_id = req.session_id or str(uuid.uuid4())
    try:
        result = chat(session_id, req.message)
        return ChatResponse(
            session_id=session_id,
            reply=result["reply"],
            tool_calls=result["tool_calls"],
        )
    except Exception as e:
        logger.error("Chat error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/chat/{session_id}")
def delete_chat(session_id: str, token: str = Depends(_auth)):
    clear_session(session_id)
    return {"session_id": session_id, "cleared": True}
