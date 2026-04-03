"""
SQLite layer — schema creation and all query helpers.
Uses sqlite_utils for convenience while exposing typed helper functions.
"""
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import sqlite_utils

from goalforge.config import config

logger = logging.getLogger(__name__)

_db: Optional[sqlite_utils.Database] = None


def get_db() -> sqlite_utils.Database:
    global _db
    if _db is None:
        configured = Path(config.database_path)
        # Fall back to local project dir if configured path isn't writable
        fallback = Path(__file__).parent.parent / "goals.db"
        db_path = configured
        try:
            configured.parent.mkdir(parents=True, exist_ok=True)
            configured.parent.stat()  # verify accessible
        except (PermissionError, OSError):
            logger.warning("Cannot write to '%s', using '%s' instead.", configured, fallback)
            db_path = fallback
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _db = sqlite_utils.Database(conn)
        _ensure_schema(_db)
        logger.info("Database: %s", db_path)
    return _db


def _ensure_schema(db: sqlite_utils.Database):
    db.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT,
            horizon TEXT,
            due_date DATE,
            parent_goal_id TEXT REFERENCES goals(id),
            depth INTEGER DEFAULT 0,
            is_milestone INTEGER DEFAULT 0,
            category TEXT,
            notify_before_days INTEGER DEFAULT 3,
            created_date DATE,
            file_path TEXT,
            last_scanned TIMESTAMP,
            notification_sent INTEGER DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS notification_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id TEXT,
            notification_type TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(goal_id, notification_type, sent_at)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS digest_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_type TEXT NOT NULL,
            period_key TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(notification_type, period_key)
        )
    """)
    # Migrations: add columns if they don't exist
    for col, definition in [
        ("sort_order", "INTEGER DEFAULT 0"),
        ("description", "TEXT DEFAULT ''"),
        ("tags", "TEXT DEFAULT '[]'"),
        ("updated_at", "TIMESTAMP"),
        ("progress_notes", "TEXT DEFAULT ''"),
    ]:
        try:
            db.execute(f"ALTER TABLE goals ADD COLUMN {col} {definition}")
            db.conn.commit()
        except Exception:
            pass  # Column already exists

    db.execute("""
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            mime_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Trigger updates updated_at on data changes, but NOT when updated_at itself changes
    # (scoping to specific columns prevents infinite recursion)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS goals_updated
        AFTER UPDATE OF name, status, horizon, due_date, parent_goal_id, depth,
            is_milestone, category, notify_before_days, description, tags, sort_order, progress_notes
        ON goals
        BEGIN
            UPDATE goals SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
        END
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS ideas (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            progress_notes TEXT DEFAULT '',
            status TEXT DEFAULT 'Incubating',
            priority TEXT DEFAULT 'Medium',
            category TEXT DEFAULT '',
            graduated_goal_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migrate category onto existing ideas tables that predate this column
    try:
        db.execute("ALTER TABLE ideas ADD COLUMN category TEXT DEFAULT ''")
        db.conn.commit()
    except Exception:
        pass  # Column already exists

    # Drop and recreate trigger so category changes also bump updated_at
    db.execute("DROP TRIGGER IF EXISTS ideas_updated")
    db.execute("""
        CREATE TRIGGER ideas_updated
        AFTER UPDATE OF name, description, progress_notes, status, priority, category, graduated_goal_id
        ON ideas
        BEGIN
            UPDATE ideas SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
        END
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            icon TEXT DEFAULT '🏷️',
            sort_order INTEGER DEFAULT 0
        )
    """)
    row = db.execute("SELECT COUNT(*) FROM categories").fetchone()
    if row[0] == 0:
        db.execute("INSERT INTO categories (name, icon, sort_order) VALUES (?, ?, ?)", ['Personal', '🏠', 0])
        db.execute("INSERT INTO categories (name, icon, sort_order) VALUES (?, ?, ?)", ['Work', '💼', 1])
        # Wipe user freeform categories but preserve the system 'Daily' marker
        db.execute("UPDATE goals SET category = NULL WHERE category != 'Daily'")
        db.execute("UPDATE ideas SET category = '' WHERE category IS NOT NULL")

    # Repair: restore 'Daily' on any daily goals whose category was previously wiped
    db.execute("""
        UPDATE goals SET category = 'Daily'
        WHERE category IS NULL AND (
            name LIKE 'Daily Goals ____-__-__'
            OR parent_goal_id IN (SELECT id FROM goals WHERE name LIKE 'Daily Goals ____-__-__')
        )
    """)

    # Lists & list items (Google Keep-style)
    db.execute("""
        CREATE TABLE IF NOT EXISTS lists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            color TEXT DEFAULT NULL,
            sort_order INTEGER DEFAULT 0,
            reminder_time TEXT DEFAULT NULL,
            reminder_recurrence TEXT DEFAULT NULL,
            reminder_next_at TIMESTAMP DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS list_items (
            id TEXT PRIMARY KEY,
            list_id TEXT NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
            content TEXT NOT NULL DEFAULT '',
            checked INTEGER DEFAULT 0,
            sort_order REAL DEFAULT 0,
            indent_level INTEGER DEFAULT 0,
            note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("DROP TRIGGER IF EXISTS lists_updated")
    db.execute("""
        CREATE TRIGGER lists_updated
        AFTER UPDATE OF name, color, sort_order, reminder_time, reminder_recurrence, reminder_next_at
        ON lists
        BEGIN
            UPDATE lists SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
        END
    """)
    db.execute("DROP TRIGGER IF EXISTS list_items_updated")
    db.execute("""
        CREATE TRIGGER list_items_updated
        AFTER UPDATE OF content, checked, sort_order, indent_level, note
        ON list_items
        BEGIN
            UPDATE list_items SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            UPDATE lists SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.list_id;
        END
    """)

    db.conn.commit()
    logger.debug("Schema ensured.")
    _migrate_ideas_to_lists(db)


def _compute_depth(db: sqlite_utils.Database, parent_goal_id: Optional[str]) -> int:
    if not parent_goal_id:
        return 0
    row = db.execute(
        "SELECT depth FROM goals WHERE id = ?", [parent_goal_id]
    ).fetchone()
    if row:
        return row[0] + 1
    return 1


def upsert_goal(goal_dict: dict):
    """Insert or update a goal by id. Auto-computes depth from parent chain."""
    db = get_db()
    goal_id = goal_dict.get("id")
    if not goal_id:
        raise ValueError("Goal dict must have an 'id' field")

    parent_id = goal_dict.get("parent_goal_id") or None
    depth = _compute_depth(db, parent_id)

    # Preserve existing sort_order, description, tags, progress_notes on update
    existing = db.execute("SELECT sort_order, description, tags, progress_notes FROM goals WHERE id = ?", [goal_id]).fetchone()
    sort_order = existing[0] if existing else goal_dict.get("sort_order", 0)
    description = goal_dict.get("description") if goal_dict.get("description") is not None else (existing[1] if existing else "")
    tags = goal_dict.get("tags") if goal_dict.get("tags") is not None else (existing[2] if existing else "[]")
    progress_notes = goal_dict.get("progress_notes") if goal_dict.get("progress_notes") is not None else (existing[3] if existing else "")

    db.conn.execute(
        """
        INSERT OR REPLACE INTO goals
            (id, name, status, horizon, due_date, parent_goal_id, depth, is_milestone,
             category, notify_before_days, created_date, file_path, last_scanned, notification_sent,
             sort_order, description, tags, progress_notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            goal_id,
            goal_dict.get("name", "Untitled"),
            goal_dict.get("status"),
            goal_dict.get("horizon"),
            str(goal_dict["due_date"]) if goal_dict.get("due_date") else None,
            parent_id,
            depth,
            1 if goal_dict.get("is_milestone") else 0,
            goal_dict.get("category"),
            goal_dict.get("notify_before_days", 3),
            str(goal_dict["created_date"]) if goal_dict.get("created_date") else None,
            goal_dict.get("file_path"),
            datetime.utcnow().isoformat(),
            goal_dict.get("notification_sent", 0),
            sort_order,
            description,
            tags,
            progress_notes,
        ],
    )
    db.conn.commit()
    logger.debug("Upserted goal %s (depth=%d)", goal_id, depth)


def get_goal(goal_id: str) -> Optional[dict]:
    db = get_db()
    row = db.execute("SELECT * FROM goals WHERE id = ?", [goal_id]).fetchone()
    if not row:
        return None
    cols = [d[0] for d in db.execute("SELECT * FROM goals LIMIT 0").description]
    return dict(zip(cols, row))


def get_children(goal_id: str, recursive: bool = False) -> list[dict]:
    db = get_db()
    if not recursive:
        rows = db.execute(
            "SELECT * FROM goals WHERE parent_goal_id = ? ORDER BY COALESCE(sort_order, 0) ASC, due_date ASC NULLS LAST",
            [goal_id]
        ).fetchall()
        cols = [d[0] for d in db.execute("SELECT * FROM goals LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]

    # Recursive: BFS
    result = []
    queue = [goal_id]
    seen = set()
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        children = get_children(current, recursive=False)
        for c in children:
            result.append(c)
            queue.append(c["id"])
    return result


def get_ancestors(goal_id: str) -> list[dict]:
    """Return parent chain from immediate parent up to root."""
    db = get_db()
    ancestors = []
    current_id = goal_id
    seen = set()
    while True:
        row = db.execute(
            "SELECT * FROM goals WHERE id = ?", [current_id]
        ).fetchone()
        if not row:
            break
        cols = [d[0] for d in db.execute("SELECT * FROM goals LIMIT 0").description]
        goal = dict(zip(cols, row))
        parent_id = goal.get("parent_goal_id")
        if not parent_id or parent_id in seen:
            break
        seen.add(parent_id)
        parent_row = db.execute(
            "SELECT * FROM goals WHERE id = ?", [parent_id]
        ).fetchone()
        if not parent_row:
            break
        ancestors.append(dict(zip(cols, parent_row)))
        current_id = parent_id
    ancestors.reverse()
    return ancestors


def get_root_goals() -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM goals WHERE parent_goal_id IS NULL ORDER BY due_date ASC NULLS LAST"
    ).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM goals LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


_DAILY_CHECKLIST_CLAUSE = (
    "name NOT LIKE 'Daily Goals ____-__-__' "
    "AND (parent_goal_id IS NULL OR parent_goal_id NOT IN "
    "(SELECT id FROM goals WHERE name LIKE 'Daily Goals ____-__-__'))"
)


def get_full_goals(filters: dict = None) -> list[dict]:
    """All goals where is_milestone = 0. Optional filter dict with keys: status, horizon, category."""
    db = get_db()
    where = ["is_milestone = 0"]
    params = []
    if filters:
        if filters.get("status"):
            where.append("status = ?")
            params.append(filters["status"])
        if filters.get("horizon"):
            where.append("horizon = ?")
            params.append(filters["horizon"])
        if filters.get("category"):
            where.append("category = ?")
            params.append(filters["category"])
        if filters.get("parent_goal_id") is not None:
            if filters["parent_goal_id"] == "":
                where.append("parent_goal_id IS NULL")
            else:
                where.append("parent_goal_id = ?")
                params.append(filters["parent_goal_id"])
        if filters.get("exclude_daily_checklist"):
            where.append(_DAILY_CHECKLIST_CLAUSE)
    sql = f"SELECT * FROM goals WHERE {' AND '.join(where)} ORDER BY due_date ASC NULLS LAST"
    rows = db.execute(sql, params).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM goals LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def get_all_goals(filters: dict = None) -> list[dict]:
    """All goals (milestones and full goals). Optional filter dict."""
    db = get_db()
    where = ["1=1"]
    params = []
    if filters:
        if filters.get("status"):
            where.append("status = ?")
            params.append(filters["status"])
        if filters.get("horizon"):
            where.append("horizon = ?")
            params.append(filters["horizon"])
        if filters.get("category"):
            where.append("category = ?")
            params.append(filters["category"])
        if filters.get("is_milestone") is not None:
            where.append("is_milestone = ?")
            params.append(1 if filters["is_milestone"] else 0)
        if filters.get("parent_goal_id") is not None:
            if filters["parent_goal_id"] == "":
                where.append("parent_goal_id IS NULL")
            else:
                where.append("parent_goal_id = ?")
                params.append(filters["parent_goal_id"])
        if filters.get("exclude_daily_checklist"):
            where.append(_DAILY_CHECKLIST_CLAUSE)
    sql = f"SELECT * FROM goals WHERE {' AND '.join(where)} ORDER BY due_date ASC NULLS LAST"
    rows = db.execute(sql, params).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM goals LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def get_goals_due_within(days: int) -> list[dict]:
    db = get_db()
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    today = date.today().isoformat()
    rows = db.execute(
        "SELECT * FROM goals WHERE due_date IS NOT NULL AND due_date >= ? AND due_date <= ? AND status != 'Completed'",
        [today, cutoff]
    ).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM goals LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def get_goals_overdue() -> list[dict]:
    db = get_db()
    today = date.today().isoformat()
    rows = db.execute(
        "SELECT * FROM goals WHERE due_date IS NOT NULL AND due_date < ? AND status != 'Completed'",
        [today]
    ).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM goals LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def get_goals_by_horizon(horizon: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM goals WHERE horizon = ? ORDER BY due_date ASC NULLS LAST",
        [horizon]
    ).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM goals LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def get_draft_captures() -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM goals WHERE status = 'Draft' ORDER BY created_date DESC"
    ).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM goals LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def mark_notification_sent(goal_id: Optional[str], notification_type: str):
    """Record that a notification was sent. goal_id=None for digest-style notifications."""
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO notification_log (goal_id, notification_type) VALUES (?, ?)",
        [goal_id, notification_type]
    )
    db.conn.commit()


def was_notification_sent_today(goal_id: Optional[str], notification_type: str) -> bool:
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM notification_log WHERE goal_id IS ? AND notification_type = ? AND date(sent_at) = date('now')",
        [goal_id, notification_type]
    ).fetchone()
    return row is not None


def was_digest_sent(notification_type: str, period_key: str) -> bool:
    """Check if a digest notification (weekly/monthly) was already sent for this period."""
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM digest_log WHERE notification_type = ? AND period_key = ?",
        [notification_type, period_key]
    ).fetchone()
    return row is not None


def mark_digest_sent(notification_type: str, period_key: str):
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO digest_log (notification_type, period_key) VALUES (?, ?)",
        [notification_type, period_key]
    )
    db.conn.commit()


def set_is_milestone(goal_id: str, is_milestone: bool):
    db = get_db()
    db.execute(
        "UPDATE goals SET is_milestone = ? WHERE id = ?",
        [1 if is_milestone else 0, goal_id]
    )
    db.conn.commit()


def set_parent(goal_id: str, new_parent_id: Optional[str]):
    """Update parent and recompute depth for goal and all descendants."""
    db = get_db()
    new_depth = _compute_depth(db, new_parent_id)
    db.execute(
        "UPDATE goals SET parent_goal_id = ?, depth = ? WHERE id = ?",
        [new_parent_id, new_depth, goal_id]
    )
    db.conn.commit()
    _recompute_descendant_depths(db, goal_id)


def _recompute_descendant_depths(db: sqlite_utils.Database, goal_id: str):
    children_rows = db.execute(
        "SELECT id FROM goals WHERE parent_goal_id = ?", [goal_id]
    ).fetchall()
    parent_row = db.execute("SELECT depth FROM goals WHERE id = ?", [goal_id]).fetchone()
    if not parent_row:
        return
    parent_depth = parent_row[0]
    for (child_id,) in children_rows:
        db.execute(
            "UPDATE goals SET depth = ? WHERE id = ?",
            [parent_depth + 1, child_id]
        )
        _recompute_descendant_depths(db, child_id)
    db.conn.commit()


def update_field(goal_id: str, field: str, value):
    """Update a single field on a goal row."""
    allowed = {
        "name", "status", "horizon", "due_date", "parent_goal_id",
        "is_milestone", "category", "notify_before_days", "description", "tags", "sort_order",
        "progress_notes",
    }
    if field not in allowed:
        raise ValueError(f"Field '{field}' is not updatable via this function.")
    db = get_db()
    db.execute(f"UPDATE goals SET {field} = ? WHERE id = ?", [value, goal_id])
    db.conn.commit()


def set_daily_order(item_ids: list):
    """Set sort_order for a list of goal IDs based on their position in the list."""
    db = get_db()
    for i, goal_id in enumerate(item_ids):
        db.execute("UPDATE goals SET sort_order = ? WHERE id = ?", [i, goal_id])
    db.conn.commit()


def delete_goal(goal_id: str):
    db = get_db()
    db.execute("DELETE FROM goals WHERE id = ?", [goal_id])
    db.conn.commit()


def search_goals(query: str) -> list[dict]:
    """Full-text search across goal name and description."""
    db = get_db()
    pattern = f"%{query}%"
    rows = db.execute(
        "SELECT * FROM goals WHERE name LIKE ? OR description LIKE ? ORDER BY updated_at DESC LIMIT 50",
        [pattern, pattern]
    ).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM goals LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def upsert_attachment(goal_id: str, filename: str, mime_type: str):
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO attachments (goal_id, filename, mime_type) VALUES (?, ?, ?)",
        [goal_id, filename, mime_type]
    )
    db.conn.commit()


def get_attachments(goal_id: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT id, goal_id, filename, mime_type, created_at FROM attachments WHERE goal_id = ? ORDER BY created_at ASC",
        [goal_id]
    ).fetchall()
    cols = ["id", "goal_id", "filename", "mime_type", "created_at"]
    return [dict(zip(cols, r)) for r in rows]


def get_recently_completed(limit: int = 5) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM goals WHERE status = 'Completed' ORDER BY updated_at DESC LIMIT ?",
        [limit]
    ).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM goals LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Ideas helpers
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = "CASE priority WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 WHEN 'Medium' THEN 3 WHEN 'Low' THEN 4 ELSE 5 END"


def get_idea(idea_id: str) -> Optional[dict]:
    db = get_db()
    row = db.execute("SELECT * FROM ideas WHERE id = ?", [idea_id]).fetchone()
    if not row:
        return None
    cols = [d[0] for d in db.execute("SELECT * FROM ideas LIMIT 0").description]
    return dict(zip(cols, row))


def upsert_idea(idea_dict: dict):
    """Insert or update an idea. Preserves created_at on update."""
    db = get_db()
    idea_id = idea_dict.get("id")
    if not idea_id:
        raise ValueError("Idea dict must have an 'id' field")
    db.conn.execute(
        """
        INSERT INTO ideas (id, name, description, progress_notes, status, priority, category, graduated_goal_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            progress_notes = excluded.progress_notes,
            status = excluded.status,
            priority = excluded.priority,
            category = excluded.category,
            graduated_goal_id = excluded.graduated_goal_id
        """,
        [
            idea_id,
            idea_dict.get("name", "Untitled"),
            idea_dict.get("description", ""),
            idea_dict.get("progress_notes", ""),
            idea_dict.get("status", "Incubating"),
            idea_dict.get("priority", "Medium"),
            idea_dict.get("category", ""),
            idea_dict.get("graduated_goal_id"),
        ],
    )
    db.conn.commit()


def get_ideas(status: str = None, priority: str = None, category: str = None) -> list[dict]:
    db = get_db()
    where = ["1=1"]
    params = []
    if status:
        where.append("status = ?")
        params.append(status)
    if priority:
        where.append("priority = ?")
        params.append(priority)
    if category:
        where.append("category = ?")
        params.append(category)
    sql = f"SELECT * FROM ideas WHERE {' AND '.join(where)} ORDER BY {_PRIORITY_ORDER} ASC, created_at DESC"
    rows = db.execute(sql, params).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM ideas LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def get_top_ideas(n: int = 5) -> list[dict]:
    """Top N non-graduated/archived ideas by priority then newest first."""
    db = get_db()
    rows = db.execute(
        f"SELECT * FROM ideas WHERE status NOT IN ('Graduated', 'Archived') ORDER BY {_PRIORITY_ORDER} ASC, created_at DESC LIMIT ?",
        [n]
    ).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM ideas LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def delete_idea(idea_id: str):
    db = get_db()
    db.execute("DELETE FROM ideas WHERE id = ?", [idea_id])
    db.conn.commit()


# ---------------------------------------------------------------------------
# Categories helpers
# ---------------------------------------------------------------------------

def get_categories() -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT id, name, icon, sort_order FROM categories ORDER BY sort_order ASC, id ASC"
    ).fetchall()
    return [{"id": r[0], "name": r[1], "icon": r[2], "sort_order": r[3]} for r in rows]


def get_category(cat_id: int) -> Optional[dict]:
    db = get_db()
    row = db.execute(
        "SELECT id, name, icon, sort_order FROM categories WHERE id = ?", [cat_id]
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "icon": row[2], "sort_order": row[3]}


def create_category(name: str, icon: str) -> Optional[dict]:
    db = get_db()
    try:
        db.conn.execute(
            "INSERT INTO categories (name, icon, sort_order) "
            "VALUES (?, ?, (SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories))",
            [name, icon]
        )
        db.conn.commit()
        row = db.conn.execute(
            "SELECT id, name, icon, sort_order FROM categories WHERE name = ?", [name]
        ).fetchone()
        return {"id": row[0], "name": row[1], "icon": row[2], "sort_order": row[3]}
    except Exception:
        return None  # Duplicate name


def update_category(cat_id: int, name: Optional[str], icon: Optional[str]):
    db = get_db()
    if name is not None:
        old = db.execute("SELECT name FROM categories WHERE id = ?", [cat_id]).fetchone()
        if old and old[0] != name:
            db.execute("UPDATE goals SET category = ? WHERE category = ?", [name, old[0]])
        db.execute("UPDATE categories SET name = ? WHERE id = ?", [name, cat_id])
    if icon is not None:
        db.execute("UPDATE categories SET icon = ? WHERE id = ?", [icon, cat_id])
    db.conn.commit()


def delete_category(cat_id: int):
    db = get_db()
    row = db.execute("SELECT name FROM categories WHERE id = ?", [cat_id]).fetchone()
    if row:
        db.execute("UPDATE goals SET category = NULL WHERE category = ?", [row[0]])
    db.execute("DELETE FROM categories WHERE id = ?", [cat_id])
    db.conn.commit()


def set_category_order(ids: list):
    db = get_db()
    for i, cat_id in enumerate(ids):
        db.execute("UPDATE categories SET sort_order = ? WHERE id = ?", [i, cat_id])
    db.conn.commit()


# ---------------------------------------------------------------------------
# Ideas → Lists migration (idempotent, runs once at startup)
# ---------------------------------------------------------------------------

def _format_idea_note(idea: dict) -> str:
    """Serialize all idea metadata into a human-readable note string."""
    parts = []
    if idea.get("description"):
        parts.append(f"Description:\n{idea['description']}")
    meta = []
    if idea.get("status"):
        meta.append(f"Status: {idea['status']}")
    if idea.get("priority"):
        meta.append(f"Priority: {idea['priority']}")
    if idea.get("category"):
        meta.append(f"Category: {idea['category']}")
    if meta:
        parts.append(" | ".join(meta))
    if idea.get("progress_notes"):
        parts.append(f"Progress Notes:\n{idea['progress_notes']}")
    if idea.get("graduated_goal_id"):
        parts.append(f"Graduated Goal: {idea['graduated_goal_id']}")
    return "\n\n".join(parts)


def _migrate_ideas_to_lists(db: sqlite_utils.Database):
    """Migrate existing ideas rows into the lists/list_items tables (one-time, idempotent)."""
    try:
        list_count = db.execute("SELECT COUNT(*) FROM lists").fetchone()[0]
        if list_count > 0:
            return  # Already migrated

        idea_rows = db.execute("SELECT * FROM ideas").fetchall()
        if not idea_rows:
            return  # Nothing to migrate

        idea_cols = [d[0] for d in db.execute("SELECT * FROM ideas LIMIT 0").description]
        ideas = [dict(zip(idea_cols, row)) for row in idea_rows]

        # Compute starting ID counter across all existing GF-XXXX rows
        all_ids: list[str] = []
        for tbl in ("goals", "ideas", "lists", "list_items"):
            try:
                all_ids.extend(r[0] for r in db.execute(f"SELECT id FROM {tbl}").fetchall())
            except Exception:
                pass
        max_num = 0
        for rid in all_ids:
            m = re.match(r"GF-(\d+)$", str(rid))
            if m:
                max_num = max(max_num, int(m.group(1)))
        counter = [max_num]

        def _next_id() -> str:
            counter[0] += 1
            return f"GF-{counter[0]:04d}"

        # Split ideas: archived/graduated → "Archived Ideas" list; others grouped by category
        archived = [i for i in ideas if i.get("status") in ("Graduated", "Archived")]
        active = [i for i in ideas if i.get("status") not in ("Graduated", "Archived")]

        # Group active ideas by category; uncategorised → "Ideas"
        from collections import defaultdict
        by_cat: dict[str, list] = defaultdict(list)
        for idea in active:
            cat = (idea.get("category") or "").strip() or "Ideas"
            by_cat[cat].append(idea)

        def _insert_list(name: str, ideas_subset: list, checked: int = 0):
            list_id = _next_id()
            db.conn.execute(
                "INSERT INTO lists (id, name, created_at, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                [list_id, name],
            )
            for idx, idea in enumerate(ideas_subset):
                item_id = _next_id()
                note = _format_idea_note(idea)
                db.conn.execute(
                    """INSERT INTO list_items
                       (id, list_id, content, checked, sort_order, note, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), COALESCE(?, CURRENT_TIMESTAMP))""",
                    [item_id, list_id, idea["name"], checked, float(idx), note,
                     idea.get("created_at"), idea.get("updated_at")],
                )

        for cat_name, cat_ideas in sorted(by_cat.items()):
            _insert_list(cat_name, cat_ideas, checked=0)

        if archived:
            _insert_list("Archived Ideas", archived, checked=1)

        db.conn.commit()
        logger.info(
            "Migrated %d ideas → %d list(s)",
            len(ideas),
            len(by_cat) + (1 if archived else 0),
        )
    except Exception as e:
        logger.error("Ideas→Lists migration failed: %s", e, exc_info=True)


# ---------------------------------------------------------------------------
# Lists helpers
# ---------------------------------------------------------------------------

_LIST_COLS = ["id", "name", "color", "sort_order", "reminder_time",
              "reminder_recurrence", "reminder_next_at", "created_at", "updated_at"]


def _row_to_list(row) -> dict:
    return dict(zip(_LIST_COLS, row))


def get_lists() -> list[dict]:
    """All lists ordered by sort_order, each augmented with item counts."""
    db = get_db()
    rows = db.execute(
        f"SELECT {', '.join(_LIST_COLS)} FROM lists ORDER BY sort_order ASC, created_at ASC"
    ).fetchall()
    result = []
    for row in rows:
        lst = _row_to_list(row)
        counts = db.execute(
            "SELECT COUNT(*), SUM(checked) FROM list_items WHERE list_id = ?",
            [lst["id"]],
        ).fetchone()
        lst["total_items"] = counts[0] or 0
        lst["checked_items"] = counts[1] or 0
        result.append(lst)
    return result


def get_recent_lists(n: int = 5) -> list[dict]:
    """Most recently updated lists (for dashboard widget)."""
    db = get_db()
    rows = db.execute(
        f"SELECT {', '.join(_LIST_COLS)} FROM lists ORDER BY updated_at DESC LIMIT ?", [n]
    ).fetchall()
    result = []
    for row in rows:
        lst = _row_to_list(row)
        counts = db.execute(
            "SELECT COUNT(*), SUM(checked) FROM list_items WHERE list_id = ?",
            [lst["id"]],
        ).fetchone()
        lst["total_items"] = counts[0] or 0
        lst["checked_items"] = counts[1] or 0
        # Grab first 3 unchecked items for preview
        preview_rows = db.execute(
            "SELECT content FROM list_items WHERE list_id = ? AND checked = 0 ORDER BY sort_order ASC LIMIT 3",
            [lst["id"]],
        ).fetchall()
        lst["preview_items"] = [r[0] for r in preview_rows]
        result.append(lst)
    return result


def get_list(list_id: str) -> Optional[dict]:
    db = get_db()
    row = db.execute(
        f"SELECT {', '.join(_LIST_COLS)} FROM lists WHERE id = ?", [list_id]
    ).fetchone()
    if not row:
        return None
    lst = _row_to_list(row)
    counts = db.execute(
        "SELECT COUNT(*), SUM(checked) FROM list_items WHERE list_id = ?", [list_id]
    ).fetchone()
    lst["total_items"] = counts[0] or 0
    lst["checked_items"] = counts[1] or 0
    return lst


def create_list(list_id: str, name: str, color: Optional[str] = None) -> dict:
    db = get_db()
    max_order = db.execute("SELECT COALESCE(MAX(sort_order), -1) FROM lists").fetchone()[0]
    db.conn.execute(
        "INSERT INTO lists (id, name, color, sort_order) VALUES (?, ?, ?, ?)",
        [list_id, name, color, max_order + 1],
    )
    db.conn.commit()
    return get_list(list_id)


def update_list(list_id: str, **kwargs) -> Optional[dict]:
    """Update any subset of list fields. Returns updated list or None."""
    db = get_db()
    allowed = {"name", "color", "sort_order", "reminder_time", "reminder_recurrence", "reminder_next_at"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_list(list_id)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    db.conn.execute(
        f"UPDATE lists SET {set_clause} WHERE id = ?",
        list(updates.values()) + [list_id],
    )
    db.conn.commit()
    return get_list(list_id)


def delete_list(list_id: str):
    db = get_db()
    db.conn.execute("DELETE FROM list_items WHERE list_id = ?", [list_id])
    db.conn.execute("DELETE FROM lists WHERE id = ?", [list_id])
    db.conn.commit()


def set_list_order(ids: list[str]):
    db = get_db()
    for i, lid in enumerate(ids):
        db.conn.execute("UPDATE lists SET sort_order = ? WHERE id = ?", [i, lid])
    db.conn.commit()


# ---------------------------------------------------------------------------
# List items helpers
# ---------------------------------------------------------------------------

_ITEM_COLS = ["id", "list_id", "content", "checked", "sort_order",
              "indent_level", "note", "created_at", "updated_at"]


def _row_to_item(row) -> dict:
    d = dict(zip(_ITEM_COLS, row))
    d["checked"] = bool(d["checked"])
    return d


def get_list_items(list_id: str) -> list[dict]:
    """Items for a list: unchecked first (by sort_order), then checked."""
    db = get_db()
    rows = db.execute(
        f"SELECT {', '.join(_ITEM_COLS)} FROM list_items WHERE list_id = ? ORDER BY checked ASC, sort_order ASC",
        [list_id],
    ).fetchall()
    return [_row_to_item(r) for r in rows]


def get_list_item(item_id: str) -> Optional[dict]:
    db = get_db()
    row = db.execute(
        f"SELECT {', '.join(_ITEM_COLS)} FROM list_items WHERE id = ?", [item_id]
    ).fetchone()
    return _row_to_item(row) if row else None


def create_list_item(item_id: str, list_id: str, content: str,
                     note: str = "", indent_level: int = 0) -> dict:
    db = get_db()
    # Place at end of unchecked items
    max_order = db.execute(
        "SELECT COALESCE(MAX(sort_order), -1) FROM list_items WHERE list_id = ? AND checked = 0",
        [list_id],
    ).fetchone()[0]
    db.conn.execute(
        "INSERT INTO list_items (id, list_id, content, note, indent_level, sort_order) VALUES (?, ?, ?, ?, ?, ?)",
        [item_id, list_id, content, note, indent_level, max_order + 1.0],
    )
    # Bump list updated_at manually (trigger only fires on UPDATE)
    db.conn.execute("UPDATE lists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", [list_id])
    db.conn.commit()
    return get_list_item(item_id)


def update_list_item(item_id: str, **kwargs) -> Optional[dict]:
    db = get_db()
    allowed = {"content", "checked", "sort_order", "indent_level", "note"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_list_item(item_id)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    db.conn.execute(
        f"UPDATE list_items SET {set_clause} WHERE id = ?",
        list(updates.values()) + [item_id],
    )
    db.conn.commit()
    return get_list_item(item_id)


def delete_list_item(item_id: str):
    db = get_db()
    item = get_list_item(item_id)
    db.conn.execute("DELETE FROM list_items WHERE id = ?", [item_id])
    if item:
        db.conn.execute("UPDATE lists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", [item["list_id"]])
    db.conn.commit()


def reorder_list_items(list_id: str, ordered_ids: list[str]):
    """Assign new sort_order values based on the provided ordered list of item IDs."""
    db = get_db()
    for idx, item_id in enumerate(ordered_ids):
        db.conn.execute(
            "UPDATE list_items SET sort_order = ? WHERE id = ? AND list_id = ?",
            [float(idx), item_id, list_id],
        )
    db.conn.execute("UPDATE lists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", [list_id])
    db.conn.commit()


def uncheck_all_list_items(list_id: str):
    db = get_db()
    db.conn.execute("UPDATE list_items SET checked = 0 WHERE list_id = ?", [list_id])
    db.conn.execute("UPDATE lists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", [list_id])
    db.conn.commit()


def get_lists_with_due_reminders() -> list[dict]:
    """Lists whose reminder_next_at is in the past (due to fire)."""
    db = get_db()
    rows = db.execute(
        f"SELECT {', '.join(_LIST_COLS)} FROM lists WHERE reminder_next_at IS NOT NULL AND reminder_next_at <= CURRENT_TIMESTAMP"
    ).fetchall()
    return [_row_to_list(r) for r in rows]


# ---------------------------------------------------------------------------
# Unified search (goals + lists + list items)
# ---------------------------------------------------------------------------

def search_all(query: str) -> dict:
    """Search goals, lists, and list items. Returns categorised results."""
    db = get_db()
    pattern = f"%{query}%"

    goal_rows = db.execute(
        "SELECT * FROM goals WHERE name LIKE ? OR description LIKE ? ORDER BY updated_at DESC LIMIT 30",
        [pattern, pattern],
    ).fetchall()
    goal_cols = [d[0] for d in db.execute("SELECT * FROM goals LIMIT 0").description]
    goals = [dict(zip(goal_cols, r)) for r in goal_rows]

    list_rows = db.execute(
        f"SELECT {', '.join(_LIST_COLS)} FROM lists WHERE name LIKE ? ORDER BY updated_at DESC LIMIT 20",
        [pattern],
    ).fetchall()
    lists_found = [_row_to_list(r) for r in list_rows]

    item_rows = db.execute(
        f"SELECT {', '.join(_ITEM_COLS)} FROM list_items WHERE content LIKE ? OR note LIKE ? ORDER BY updated_at DESC LIMIT 30",
        [pattern, pattern],
    ).fetchall()
    items_found = [_row_to_item(r) for r in item_rows]

    return {"goals": goals, "lists": lists_found, "list_items": items_found}
