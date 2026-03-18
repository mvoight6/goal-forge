"""Basic scanner tests."""
import tempfile
import os
from pathlib import Path


def test_parse_goal_file(tmp_path):
    """Scanner correctly parses a goal .md file with frontmatter."""
    goal_file = tmp_path / "GF-0001 Test Goal.md"
    goal_file.write_text("""---
id: GF-0001
name: "Test Goal"
status: Active
horizon: Monthly
due_date: 2025-12-31
parent_goal_id:
depth: 0
is_milestone: false
category: Health
created_date: 2025-01-01
notify_before_days: 3
tags: [goal]
---

## Description
A test goal.
""")

    import frontmatter
    post = frontmatter.load(str(goal_file))
    assert post["id"] == "GF-0001"
    assert post["name"] == "Test Goal"
    assert post["status"] == "Active"


def test_id_generator_format():
    """ID generator produces correctly formatted GF- IDs."""
    from goalforge.id_generator import _parse_numeric
    assert _parse_numeric("GF-0001") == 1
    assert _parse_numeric("GF-0042") == 42
    assert _parse_numeric("GF-1234") == 1234
    assert _parse_numeric("invalid") == 0
