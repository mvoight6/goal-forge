"""
Thread-safe GF-XXXX unique ID generator.
Queries the database for the highest existing numeric suffix and increments by 1.
"""
import re
import threading
import logging

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _parse_numeric(goal_id: str) -> int:
    """Extract the numeric portion from a GF-XXXX id."""
    m = re.match(r"GF-(\d+)$", str(goal_id))
    return int(m.group(1)) if m else 0


def next_id(db_conn) -> str:
    """
    Generate the next GF- ID.

    Args:
        db_conn: sqlite3.Connection (or sqlite_utils.Database) to query for existing IDs.

    Returns:
        A string like "GF-0042".
    """
    with _lock:
        try:
            import sqlite3
            import sqlite_utils

            rows = []
            if isinstance(db_conn, sqlite_utils.Database):
                rows += db_conn.execute("SELECT id FROM goals").fetchall()
                try:
                    rows += db_conn.execute("SELECT id FROM ideas").fetchall()
                except Exception:
                    pass
            elif isinstance(db_conn, sqlite3.Connection):
                cursor = db_conn.cursor()
                cursor.execute("SELECT id FROM goals")
                rows += cursor.fetchall()
                try:
                    cursor.execute("SELECT id FROM ideas")
                    rows += cursor.fetchall()
                except Exception:
                    pass

            max_num = 0
            for row in rows:
                raw_id = row[0] if isinstance(row, (tuple, list)) else row
                max_num = max(max_num, _parse_numeric(raw_id))

            new_num = max_num + 1
            new_id = f"GF-{new_num:04d}"
            logger.debug("Generated ID: %s", new_id)
            return new_id

        except Exception as e:
            logger.warning("Could not query DB for ID generation: %s. Using timestamp fallback.", e)
            import time
            return f"GF-T{int(time.time())}"
