"""
SQLite layer — schema creation and all query helpers.
Uses sqlite_utils for convenience while exposing typed helper functions.
"""
import logging
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
    # Migration: add sort_order column if it doesn't exist
    try:
        db.execute("ALTER TABLE goals ADD COLUMN sort_order INTEGER DEFAULT 0")
        db.conn.commit()
    except Exception:
        pass  # Column already exists
    db.conn.commit()
    logger.debug("Schema ensured.")


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

    # Preserve existing sort_order on re-scan; use provided value or 0 for new goals
    existing = db.execute("SELECT sort_order FROM goals WHERE id = ?", [goal_id]).fetchone()
    sort_order = existing[0] if existing else goal_dict.get("sort_order", 0)

    db.conn.execute(
        """
        INSERT OR REPLACE INTO goals
            (id, name, status, horizon, due_date, parent_goal_id, depth, is_milestone,
             category, notify_before_days, created_date, file_path, last_scanned, notification_sent, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        "is_milestone", "category", "notify_before_days"
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


def get_recently_completed(limit: int = 5) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM goals WHERE status = 'Completed' ORDER BY last_scanned DESC LIMIT ?",
        [limit]
    ).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM goals LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]
