"""
Vault scanner — walks the Goals folder, parses YAML frontmatter, upserts to DB.
Skips _inbox/ (handled by capture). Auto-assigns GF- IDs to files missing them.
"""
import logging
from pathlib import Path
from typing import Optional

import frontmatter

from goalforge.config import config
from goalforge import database, id_generator

logger = logging.getLogger(__name__)


def _vault_goals_path() -> Path:
    return Path(config.vault_path) / config.goals_folder


def _vault_daily_path() -> Path:
    return Path(config.vault_path) / config.daily_folder


def _inbox_path() -> Path:
    return Path(config.vault_path) / config.inbox_folder


def _is_in_inbox(path: Path) -> bool:
    try:
        path.relative_to(_inbox_path())
        return True
    except ValueError:
        return False


def _assign_id_to_file(path: Path) -> Optional[str]:
    """Generate a GF- ID and write it into the file's frontmatter. Returns the new ID."""
    try:
        db = database.get_db()
        new_id = id_generator.next_id(db)
        post = frontmatter.load(str(path))
        post["id"] = new_id
        with open(path, "w", encoding="utf-8") as f:
            f.write(frontmatter.dumps(post))
        logger.info("Assigned ID %s to %s", new_id, path.name)
        return new_id
    except Exception as e:
        logger.warning("Could not assign ID to %s: %s", path, e)
        return None


REQUIRED_FIELDS = {"id", "name"}


def _parse_goal_file(path: Path) -> Optional[dict]:
    """Parse a .md file and return a dict suitable for database.upsert_goal."""
    try:
        post = frontmatter.load(str(path))
        meta = dict(post.metadata)

        # Auto-assign ID if missing
        if not meta.get("id"):
            new_id = _assign_id_to_file(path)
            if not new_id:
                return None
            meta["id"] = new_id

        # Warn on missing required fields but don't crash
        for field in REQUIRED_FIELDS:
            if not meta.get(field):
                logger.warning("File %s missing required field '%s' — skipping", path.name, field)
                return None

        meta["file_path"] = str(path)
        return meta

    except Exception as e:
        logger.warning("Error parsing %s: %s", path, e)
        return None


def _scan_folder(folder_path: Path, scanned: list, skipped: list):
    """Scan a single folder of .md goal files and upsert to DB."""
    if not folder_path.exists():
        return
    for path in folder_path.rglob("*.md"):
        if _is_in_inbox(path):
            continue
        if path.name == "Dashboard.md":
            continue
        goal = _parse_goal_file(path)
        if goal is None:
            skipped.append(path)
            continue
        try:
            database.upsert_goal(goal)
            scanned.append(path)
        except Exception as e:
            logger.warning("Failed to upsert goal from %s: %s", path.name, e)
            skipped.append(path)


def run_scan():
    """Main scan entry point. Called by scheduler and on startup."""
    goals_path = _vault_goals_path()
    if not goals_path.exists():
        logger.warning("Goals folder does not exist: %s", goals_path)
        return

    scanned: list = []
    skipped: list = []

    _scan_folder(goals_path, scanned, skipped)
    _scan_folder(_vault_daily_path(), scanned, skipped)

    logger.info("Scan complete: %d goals upserted, %d skipped", len(scanned), len(skipped))

    # Regenerate dashboard after scan
    try:
        from goalforge import dashboard
        dashboard.generate()
    except Exception as e:
        logger.warning("Dashboard generation failed after scan: %s", e)
