"""
Obsidian Dashboard.md generator.
Writes/overwrites Goals/Dashboard.md with Dataview-compatible blocks.
Called after every scanner run.
"""
import logging
from datetime import date
from pathlib import Path

from goalforge.config import config
from goalforge import database

logger = logging.getLogger(__name__)


def generate():
    vault_path = Path(config.vault_path)
    goals_folder = config.goals_folder
    dashboard_path = vault_path / goals_folder / "Dashboard.md"

    inbox_count = len(database.get_draft_captures())
    today = date.today().strftime("%Y-%m-%d")

    content = f"""---
created: {today}
tags: [dashboard]
---

# 🏆 Goal Forge Dashboard
*Auto-generated · Last updated: {today}*

---

## 🌳 Active Root Goals
```dataview
TABLE name, due_date, status, horizon, category
FROM "{goals_folder}"
WHERE depth = 0 AND status = "Active"
SORT due_date ASC
```

---

## 🔥 Due This Week
```dataview
TABLE name, due_date, status, parent_goal_id
FROM "{goals_folder}"
WHERE due_date <= date(today) + dur(7 days)
  AND due_date >= date(today)
  AND status != "Completed"
SORT due_date ASC
```

---

## 🚨 Overdue
```dataview
TABLE name, due_date, status, parent_goal_id
FROM "{goals_folder}"
WHERE due_date < date(today)
  AND status != "Completed"
SORT due_date ASC
```

---

## ✅ Recently Completed
```dataview
TABLE name, due_date, horizon
FROM "{goals_folder}"
WHERE status = "Completed"
SORT file.mtime DESC
LIMIT 5
```

---

## 🧩 Active Sub-Goals
```dataview
TABLE name, due_date, parent_goal_id, is_milestone
FROM "{goals_folder}"
WHERE depth > 0 AND status = "Active" AND is_milestone = false
SORT due_date ASC
```

---

## 📥 Inbox
> **{inbox_count}** captured idea{'s' if inbox_count != 1 else ''} awaiting review

```dataview
TABLE name, created_date
FROM "{config.inbox_folder}"
WHERE status = "Draft"
SORT created_date DESC
```
"""

    dashboard_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard_path.write_text(content, encoding="utf-8")
    logger.info("Dashboard written to %s", dashboard_path)
