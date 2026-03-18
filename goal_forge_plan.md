# Goal Forge — Claude Code Build Plan

## Overview

Goal Forge is a self-hosted goal tracking system that bridges your Obsidian vault
(synced via Syncthing) with a Python automation engine running on your home server.
It provides AI-powered milestone planning, Android push notifications via ntfy.sh,
a quick-capture API, an interactive LLM mode with vault read/write access, and
Obsidian dashboard generation. The Android interface is a PWA hosted on the server
and accessible anywhere via Tailscale.

**Stack:**
- Python 3.11+ on home server (Linux assumed)
- Obsidian vault path accessible on the server via Syncthing mount
- ntfy.sh for Android push notifications (self-hosted or ntfy.sh cloud)
- Pluggable LLM provider system (Claude API, OpenRouter, Ollama, vLLM) — configured in config.yaml
- SQLite for internal goal database
- FastAPI for all backend endpoints (capture, chat, vault tools)
- APScheduler for cron-style job scheduling (no separate cron needed)
- PWA (Progressive Web App) served by FastAPI for Android interface
- Tailscale for secure remote access without exposing ports publicly

---

## Project Structure

```
goal-forge/
├── config.yaml                  # All user config (vault path, ntfy topic, LLM provider, etc.)
├── main.py                      # Entry point — starts scheduler + API server + serves PWA
├── requirements.txt
├── goalforge/
│   ├── __init__.py
│   ├── scanner.py               # Reads .md files, parses YAML frontmatter
│   ├── database.py              # SQLite schema + query helpers
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py              # Abstract LLMProvider base class
│   │   ├── anthropic.py         # Claude API provider
│   │   ├── openrouter.py        # OpenRouter API provider
│   │   ├── ollama.py            # Ollama local provider
│   │   ├── vllm.py              # vLLM local provider
│   │   └── factory.py           # Reads config and returns correct provider instance
│   ├── planner.py               # Child goal generation (uses LLM provider)
│   ├── interactive.py           # Interactive chat mode with vault tool access
│   ├── vault_tools.py           # Vault read/write/create/delete operations for LLM use
│   ├── notifier.py              # Push (ntfy.sh) + email (SMTP) notifications, all 8 types
│   ├── scheduler.py             # APScheduler jobs + /jobs/run manual trigger endpoint
│   ├── config_api.py            # GET/PUT /config and PUT /config/raw endpoints
│   ├── logs_api.py              # GET /logs, /logs/{file}, /logs/{file}/tail endpoints
│   ├── capture.py               # Quick-capture endpoint with image handling
│   ├── dashboard.py             # Generates Dashboard.md in vault
│   └── id_generator.py          # GF- unique ID logic
├── pwa/                         # Progressive Web App frontend
│   ├── index.html               # App shell (installable PWA)
│   ├── manifest.json            # PWA manifest (name, icons, theme)
│   ├── service-worker.js        # Offline caching
│   ├── app.js                   # Main JS — handles all views and API calls
│   └── style.css                # Mobile-first styles
├── templates/
│   ├── goal_template.md         # Blank goal file template
│   ├── dashboard_template.md    # Dashboard.md dataview template
│   └── email/                   # Jinja2 HTML email templates
│       ├── base.html            # Shared email wrapper with inline styles
│       ├── daily_briefing.html
│       ├── end_of_week.html
│       ├── beginning_of_month.html
│       └── end_of_month.html
└── tests/
    └── test_scanner.py
```

---

## Phase 1 — Data Schema (Obsidian)

### Vault Folder
All goal files live in: `<vault_root>/Goals/`
Inbox captures land in: `<vault_root>/Goals/_inbox/`
Images attached to captures are saved in: `<vault_root>/Goals/_inbox/attachments/`

Image filenames follow the pattern `{goal_id}_{n}{ext}` (e.g. `GF-0042_1.jpg`, `GF-0042_2.png`)
so all images for a capture are clearly grouped by ID.

### Unified Goal Schema — Goals and Milestones are the Same Entity

Every goal file uses an identical schema regardless of whether it is a top-level goal
or a child milestone. The only structural difference is whether `parent_goal_id` is
populated. This means any milestone can be promoted to a full goal (or demoted back)
simply by clearing or setting that field — no migration, no file restructuring needed.

A goal with no `parent_goal_id` is a **root goal**.
A goal with a `parent_goal_id` is a **child goal** (formerly called a milestone).
A child goal can itself have children — the hierarchy is unlimited in depth.

### Goal File Frontmatter (YAML)

```yaml
---
id: GF-0001
name: "Run a 5K"
status: Active            # Draft | Backlog | Active | Completed | Blocked
horizon: Monthly          # Daily | Weekly | Monthly | Quarterly | Yearly | Life
due_date: 2025-09-01
parent_goal_id:           # Empty = root goal. Set to e.g. GF-0001 to make this a child.
depth: 0                  # 0 = root, 1 = first-level child, 2 = grandchild, etc. (auto-computed)
is_milestone: false       # true = treat as a simple milestone; false = treat as a full goal
                          # When is_milestone is false, this goal gets its own full planning,
                          # notifications, and can have its own children.
category: Health
created_date: 2025-07-01
notify_before_days: 3
tags: [goal, health]
---
```

**`is_milestone` flag explained:**
- `is_milestone: true` — lightweight child; shown as a row in the parent's Milestones table;
  does NOT appear as a standalone card in the Goals list view of the PWA.
- `is_milestone: false` — full goal; appears in the Goals list as its own card;
  can have its own children, its own plan, its own notifications.
- Any child goal can be promoted at any time by setting `is_milestone: false`.
- Any full goal can be demoted by setting `is_milestone: true`.

### Goal File Body

```markdown
## Summary
| Field         | Value            |
|---------------|------------------|
| Goal          | Run a 5K         |
| Due           | 2025-09-01       |
| Horizon       | Monthly          |
| Status        | Active           |
| Parent Goal   | —                |   ← shows parent name + ID if child goal

## Description
(Free-form goal description here — sent to the LLM for planning)

## Child Goals
(Auto-maintained — includes both milestones and full sub-goals)
| ID       | Name                  | Type      | Due Date   | Status  |
|----------|-----------------------|-----------|------------|---------|
| GF-0002  | Week 1: Walk/run 2km  | Milestone | 2025-07-15 | Backlog |
| GF-0005  | Register for race     | Full Goal | 2025-08-01 | Active  |
```

The section header is **Child Goals** (not "Milestones") to reflect that it contains
both milestone-type and full-goal-type children.

### Quick Capture File
Captured notes land in: `<vault_root>/Goals/_inbox/`
Each capture is a minimal `.md` file with status `Draft` pending review.
If images were attached, the file embeds them inline AND lists them as an attachment
block at the bottom:

```markdown
---
id: GF-0042
status: Draft
created_date: 2025-08-10
tags: [capture]
---

## Note
Great idea for a new hiking goal — maybe plan a trip to the UP.

## Images
![[GF-0042_1.jpg]]
![[GF-0042_2.jpg]]

## Attachments
- [[Goals/_inbox/attachments/GF-0042_1.jpg]]
- [[Goals/_inbox/attachments/GF-0042_2.jpg]]
```

The `![[...]]` syntax renders inline in Obsidian. The `[[...]]` links in Attachments
are standard Obsidian wikilinks for navigation and backlink tracking.

---

## Phase 2 — Python Components

### 2.1 `config.yaml`
Claude Code should generate this file and prompt the user to fill it in before first run:

```yaml
vault_path: "/mnt/syncthing/obsidian/MyVault"
goals_folder: "Goals"
inbox_folder: "Goals/_inbox"
attachments_folder: "Goals/_inbox/attachments"
database_path: "/opt/goal-forge/goals.db"
log_path: "/opt/goal-forge/logs"          # Directory for all log files

# LLM Provider — choose one: anthropic | openrouter | ollama | vllm
llm:
  provider: "vllm"
  anthropic:
    api_key: "sk-ant-..."
    model: "claude-opus-4-5"
  openrouter:
    api_key: "sk-or-..."
    model: "mistralai/mistral-7b-instruct"
  ollama:
    base_url: "http://localhost:11434"
    model: "llama3"
  vllm:
    base_url: "http://localhost:8000"
    model: "mistralai/Mistral-7B-Instruct-v0.2"

ntfy:
  server: "https://ntfy.sh"
  topic: "goalforge-yourname"

email:
  smtp_host: "smtp.gmail.com"
  smtp_port: 587
  smtp_user: "you@gmail.com"
  smtp_password: "your-app-password"       # Use a Gmail App Password, not your account password
  from_address: "you@gmail.com"
  to_address: "you@gmail.com"              # Who receives Goal Forge emails

api:
  host: "0.0.0.0"
  port: 8742
  secret_token: "changeme"

# Per-notification settings: enabled (on/off) and channel (push | email | both)
notifications:
  due_soon:
    enabled: true
    channel: push                          # push | email | both
    days_before: 3
  goal_overdue:
    enabled: true
    channel: push
  daily_morning_briefing:
    enabled: true
    channel: push
    time: "07:00"
  weekly_digest:
    enabled: true
    channel: push
    day: "Monday"
    time: "07:30"
  end_of_week_summary:
    enabled: true
    channel: email
    day: "Friday"
    time: "17:00"
  inbox_review:
    enabled: true
    channel: push
    day: "Sunday"
    time: "09:00"
  beginning_of_month:
    enabled: true
    channel: email
    day_of_month: 1
    time: "08:00"
  end_of_month:
    enabled: true
    channel: email
    day_of_month: 28                       # Close enough to month-end; handler checks last day
    time: "17:00"

scheduler:
  scan_interval_minutes: 15

capture:
  max_image_size_mb: 20
  allowed_image_types: [jpg, jpeg, png, webp, gif, heic]
```

**Important:** Each notification block has two independent controls:
- `enabled` — master on/off switch; if false, the job is skipped entirely
- `channel` — delivery method: `push` (ntfy.sh), `email` (SMTP), or `both`

Both are editable from the PWA Config screen and take effect immediately (no restart needed)
because the scheduler reads config at job execution time, not at startup.

---

### 2.2 `scanner.py` — Vault Scanner

**Responsibility:** Walk the Goals folder, parse every `.md` file's YAML frontmatter,
and upsert records into the SQLite database.

**Key behaviors:**
- Use the `python-frontmatter` library to parse files
- Skip files in `_inbox/` (handled separately)
- Compare `id` field to detect new vs. updated goals
- If a file has no `id`, generate one via `id_generator.py` and write it back to the file
- Log any files with missing required fields as warnings, do not crash

**Trigger:** Runs on startup and every 15 minutes via scheduler.

---

### 2.3 `database.py` — SQLite Layer

**Schema — `goals` table:**

```sql
CREATE TABLE goals (
    id TEXT PRIMARY KEY,           -- GF-0001
    name TEXT NOT NULL,
    status TEXT,
    horizon TEXT,
    due_date DATE,
    parent_goal_id TEXT REFERENCES goals(id),
    depth INTEGER DEFAULT 0,       -- 0 = root, auto-computed on upsert
    is_milestone INTEGER DEFAULT 0, -- 0 = full goal, 1 = milestone
    category TEXT,
    notify_before_days INTEGER DEFAULT 3,
    created_date DATE,
    file_path TEXT,
    last_scanned TIMESTAMP,
    notification_sent INTEGER DEFAULT 0
);
```

**Key functions to implement:**
- `upsert_goal(goal_dict)` — insert or update by id; auto-compute `depth` by walking parent chain
- `get_children(goal_id: str, recursive: bool = False)` — direct children or full subtree
- `get_ancestors(goal_id: str)` — full parent chain up to root (for breadcrumb display)
- `get_root_goals()` — all goals where `parent_goal_id` is null
- `get_full_goals()` — all goals where `is_milestone = 0` (for Goals list view)
- `get_goals_due_within(days: int)` — for notification checks; includes both milestones and full goals
- `get_goals_by_horizon(horizon: str)` — for weekly digest
- `get_draft_captures()` — returns inbox items
- `mark_notification_sent(goal_id, notification_type)` — prevents duplicate alerts
- `promote_to_full_goal(goal_id: str)` — sets `is_milestone = 0`, updates file frontmatter
- `demote_to_milestone(goal_id: str)` — sets `is_milestone = 1`, updates file frontmatter

---

### 2.4 `id_generator.py` — Unique ID System

**Format:** `GF-XXXX` where XXXX is a zero-padded integer (GF-0001, GF-0042, etc.)

**Behavior:**
- Query the database for the highest existing numeric ID
- Increment by 1 for each new goal
- Thread-safe (use a lock if the API and scanner could generate IDs concurrently)

---

### 2.5 `llm/` — Pluggable LLM Provider System

**Responsibility:** Abstract all LLM calls behind a common interface so the rest of
the codebase never needs to know which provider is active.

**`base.py` — Abstract base class:**
```python
from abc import ABC, abstractmethod

class LLMProvider(ABC):
    @abstractmethod
    def chat(self, system: str, messages: list[dict], json_mode: bool = False) -> str:
        """Send a chat request. Returns the assistant's reply as a string."""
        pass
```

**`factory.py` — Provider loader:**
Reads `config.yaml` and returns the correct provider instance. This is the only
place in the codebase that references the config `llm.provider` field.

```python
def get_provider() -> LLMProvider:
    match config.llm.provider:
        case "anthropic":  return AnthropicProvider(config.llm.anthropic)
        case "openrouter": return OpenRouterProvider(config.llm.openrouter)
        case "ollama":     return OllamaProvider(config.llm.ollama)
        case "vllm":       return VLLMProvider(config.llm.vllm)
```

**Provider implementation notes:**

| Provider | Library/Method | JSON mode |
|----------|---------------|-----------|
| `anthropic` | `anthropic` SDK | Prompt instruction |
| `openrouter` | `httpx` POST to `https://openrouter.ai/api/v1/chat/completions` | `response_format: {type: "json_object"}` |
| `ollama` | `httpx` POST to `http://localhost:11434/api/chat` | `format: "json"` in request body |
| `vllm` | `httpx` POST to vLLM OpenAI-compatible endpoint `/v1/chat/completions` | `response_format: {type: "json_object"}` |

All providers must handle errors gracefully and raise a common `LLMError` exception
so callers don't need provider-specific error handling.

---

### 2.6 `planner.py` — AI Goal Planner

**Responsibility:** Given any goal (root or child), use the active LLM provider to
generate 3–5 child goals, write them as new `.md` files, and link them to the parent.
Both milestones and full sub-goals are generated as proper goal files — the only
difference is the `is_milestone` flag in their frontmatter.

**Input to LLM prompt:**
- Goal name
- Goal description
- Due date
- Horizon
- Depth in hierarchy (so the LLM can calibrate scope — a depth-2 child should have
  narrower, more concrete children than a depth-0 root goal)
- Ancestry chain (names of all ancestors, so the LLM has full context)

**Expected LLM output (JSON):**
```json
[
  {
    "name": "Register for a local 5K race",
    "description": "Find and register for a race 8 weeks out",
    "due_date": "2025-08-01",
    "notify_before_days": 3,
    "is_milestone": false,
    "horizon": "Monthly"
  },
  {
    "name": "Complete week 1 walk/run intervals",
    "description": "Walk 2 min / run 1 min, repeat 6x, three times this week",
    "due_date": "2025-07-15",
    "notify_before_days": 1,
    "is_milestone": true,
    "horizon": "Weekly"
  }
]
```

The LLM decides `is_milestone` per child based on complexity. The user can override
this after the fact via the PWA or interactive chat.

**Key behaviors:**
- Uses `llm_factory.get_provider().chat(..., json_mode=True)`
- Each returned child becomes a new `.md` file with `parent_goal_id` set to the parent
- `depth` is set to `parent.depth + 1` automatically
- After writing files, triggers a scanner run
- Updates the parent's `## Child Goals` table in its `.md` file
- Callable on any goal at any depth: `python main.py plan GF-0005`
- If called on a milestone (`is_milestone: true`), automatically promotes it to a
  full goal (`is_milestone: false`) before planning, since milestones with children
  are by definition full goals

**Promotion helper:**
```python
def promote_to_full_goal(goal_id: str):
    """
    Promotes a milestone to a full goal.
    - Sets is_milestone: false in frontmatter
    - Updates database
    - Regenerates parent's Child Goals table
    - Does NOT change parent_goal_id — it remains a child of its parent
    """
```

---

### 2.7 `vault_tools.py` — Vault Operations for Interactive Mode

**Responsibility:** Expose a set of named tools the LLM can invoke during interactive
mode to read and modify the Obsidian vault. These are NOT exposed directly to the user
— they are called by the LLM as tool calls during a conversation.

**Tools to implement:**

| Tool name | Description | Parameters |
|-----------|-------------|------------|
| `read_goal` | Read a goal file by ID or name | `id_or_name: str` |
| `list_goals` | List goals, optionally filtered | `status`, `horizon`, `category`, `is_milestone`, `parent_goal_id` (all optional) |
| `get_goal_tree` | Return a goal and its full child hierarchy | `id: str`, `max_depth: int` (optional) |
| `get_ancestors` | Return the parent chain up to root for a goal | `id: str` |
| `update_goal_field` | Update a single frontmatter field | `id: str`, `field: str`, `value: str` |
| `promote_to_full_goal` | Promote a milestone to a full goal (`is_milestone: false`) | `id: str` |
| `demote_to_milestone` | Demote a full goal to a milestone (`is_milestone: true`) | `id: str` |
| `reparent_goal` | Move a goal to a different parent (or make it a root goal) | `id: str`, `new_parent_id: str \| null` |
| `create_goal` | Create a new goal file | `name`, `description`, `horizon`, `due_date`, `category`, `parent_goal_id` (optional), `is_milestone` (optional) |
| `delete_goal` | Delete a goal file by ID | `id: str`, `confirmed: bool` |
| `read_note` | Read any vault file by relative path | `path: str` |
| `list_notes` | List files in a vault folder | `folder: str` |
| `search_vault` | Full-text search across all vault files | `query: str` |

**Safety rules:**
- `delete_goal` requires confirmation flag (`confirmed: bool`) — the LLM must explicitly
  pass `confirmed=True`, which the interactive layer only sets after user confirms in the PWA
- All write operations log what changed (file path, field, old value, new value) to a
  `vault_changes.log` file for auditability
- Paths are always validated to be within the configured vault root (no path traversal)

---

### 2.8 `interactive.py` — Interactive Chat Mode

**Responsibility:** Manage a stateful conversation with the LLM that has access to
vault tools. This is the backend for the PWA chat interface.

**How it works:**
- Maintains conversation history in memory (per session) and optionally persists to DB
- On each user message, calls the LLM with the full conversation history + tool definitions
- If the LLM returns a tool call, executes it via `vault_tools.py` and feeds the result
  back into the conversation before the next LLM turn
- Supports multi-turn tool use (LLM can call multiple tools in sequence before responding)
- Returns the final text response to the PWA

**FastAPI endpoints for interactive mode:**
```
POST /chat
Authorization: Bearer <secret_token>
{ "session_id": "abc123", "message": "What goals do I have due this week?" }

→ { "reply": "You have 3 goals due this week: ...", "tool_calls": [...] }

DELETE /chat/{session_id}   # Clear conversation history
```

**System prompt for interactive mode:**
The system prompt should tell the LLM:
- It is a personal goal coach with access to the user's Obsidian vault
- Available tools and what they do
- To always confirm before deleting anything
- To be concise since responses appear on a mobile screen

---

### 2.9 `notifier.py` — Notification Engine

**Responsibility:** Send notifications via ntfy.sh (push) and/or SMTP (email)
depending on each notification type's `channel` setting in config. Also generates
LLM-assisted content for the richer digest/summary notifications.

**Delivery abstraction:**
```python
def deliver(notification_type: str, title: str, body: str, html_body: str = None):
    """
    Reads config.notifications[notification_type].channel and routes accordingly.
    channel = 'push'  → send_push(title, body)
    channel = 'email' → send_email(title, body, html_body)
    channel = 'both'  → both
    If enabled = false, returns immediately without sending.
    """
```

**Push sender (ntfy.sh):**
```python
def send_push(title: str, body: str, priority: str = "default", tags: list = None):
    httpx.post(
        f"{config.ntfy.server}/{config.ntfy.topic}",
        headers={
            "Title": title,
            "Priority": priority,
            "Tags": ",".join(tags or []),
        },
        content=body,
    )
```

**Email sender (SMTP/Gmail):**
```python
def send_email(subject: str, body_text: str, body_html: str = None):
    # Use Python stdlib smtplib + email.mime
    # Connect to smtp.gmail.com:587, STARTTLS, login with app password
    # Send multipart/alternative with both text and HTML parts if html provided
```

**All notification types:**

| Type | Trigger | Channel default | Content |
|------|---------|----------------|---------|
| `due_soon` | Daily check: goal due within `days_before` days | push | "⏰ [GF-0001] Run a 5K is due in 2 days" |
| `goal_overdue` | Daily check: goal past due, not Completed | push | "🚨 [GF-0003] Overdue: Learn Spanish" |
| `daily_morning_briefing` | Every morning at configured time | push | LLM-generated: what to focus on today to move goals forward |
| `weekly_digest` | Monday morning | push | List of active goals due this week |
| `end_of_week_summary` | Friday afternoon | email | LLM-generated: celebrate wins, reflect on the week's progress |
| `inbox_review` | Sunday morning | push | "📥 You have N captured ideas to review" |
| `beginning_of_month` | 1st of month | email | LLM-generated: upcoming month's goals and milestones plan |
| `end_of_month` | Last day of month | email | LLM-generated: congratulate and summarize the month's accomplishments |

**LLM-generated notification content:**
The four richer notification types (`daily_morning_briefing`, `end_of_week_summary`,
`beginning_of_month`, `end_of_month`) use the active LLM provider to generate their
content. Each calls `get_provider().chat()` with a context-rich prompt:

- **Daily morning briefing prompt inputs:** today's date, all active goals + their
  child goals, any items due today or within 3 days, any overdue items
- **End of week summary prompt inputs:** week's date range, all goals completed or
  progressed this week (status changes logged in DB), any milestones hit
- **Beginning of month prompt inputs:** upcoming month name, all active goals with
  due dates in the month, child goals/milestones scheduled for the month
- **End of month prompt inputs:** month just ended, all goals/milestones completed
  during the month, goals that slipped and are now overdue

For email notifications, ask the LLM to format the output as clean HTML
(a simple styled email body — no external CSS frameworks, inline styles only)
so it renders well in Gmail. Also generate a plain-text fallback.

**Deduplication:** `notification_sent` in the DB tracks last-sent timestamp per
goal per type; prevents re-firing `due_soon` and `goal_overdue` for unchanged goals.
Digest/summary notifications deduplicate by checking whether they already fired
on the same calendar day/week/month.

---

### 2.10 `scheduler.py` — Job Scheduler

Use **APScheduler** (`pip install apscheduler`) to run all jobs in-process.
No system cron required. All schedule times and enabled states are read from
config at job execution time so changes from the PWA Config screen take effect
without a restart.

**Jobs:**

```python
# Every N minutes (configurable)
scheduler.add_job(scanner.run_scan, 'interval',
                  minutes=config.scheduler.scan_interval_minutes)

# Daily due-soon + overdue check
scheduler.add_job(notifier.check_due_dates, 'cron', hour=8, minute=0)

# Daily morning briefing
scheduler.add_job(notifier.send_daily_morning_briefing, 'cron',
                  hour=config.notifications.daily_morning_briefing.time.hour,
                  minute=config.notifications.daily_morning_briefing.time.minute)

# Weekly digest — Monday morning
scheduler.add_job(notifier.send_weekly_digest, 'cron',
                  day_of_week='mon',
                  hour=config.notifications.weekly_digest.time.hour,
                  minute=config.notifications.weekly_digest.time.minute)

# End of week summary — Friday afternoon
scheduler.add_job(notifier.send_end_of_week_summary, 'cron',
                  day_of_week='fri',
                  hour=config.notifications.end_of_week_summary.time.hour,
                  minute=config.notifications.end_of_week_summary.time.minute)

# Inbox review — Sunday morning
scheduler.add_job(notifier.send_inbox_review_prompt, 'cron',
                  day_of_week='sun',
                  hour=config.notifications.inbox_review.time.hour,
                  minute=config.notifications.inbox_review.time.minute)

# Beginning of month
scheduler.add_job(notifier.send_beginning_of_month, 'cron',
                  day=1,
                  hour=config.notifications.beginning_of_month.time.hour,
                  minute=config.notifications.beginning_of_month.time.minute)

# End of month — last day of month
scheduler.add_job(notifier.send_end_of_month, 'cron',
                  day='last',
                  hour=config.notifications.end_of_month.time.hour,
                  minute=config.notifications.end_of_month.time.minute)
```

**Manual trigger endpoint** (used by PWA Jobs screen):
```
POST /jobs/run/{job_name}
Authorization: Bearer <secret_token>

job_name options:
  scan | check_due_dates | daily_morning_briefing | weekly_digest |
  end_of_week_summary | inbox_review | beginning_of_month | end_of_month
```
Each job runs immediately in a background thread and returns
`{"job": "weekly_digest", "status": "triggered"}` without waiting for completion.

---

### 2.11 `capture.py` — Quick Capture FastAPI Endpoint

**Responsibility:** Accept a text note plus zero or more images from the PWA,
save images to the vault attachments folder, and write a `.md` capture file that
embeds and lists them.

**Endpoints:**

```
POST /capture
Authorization: Bearer <secret_token>
Content-Type: multipart/form-data

Fields:
  title       (required)  string
  description (optional)  string
  images      (optional)  one or more image files (JPEG, PNG, WEBP, GIF, HEIC)
```

Using `multipart/form-data` (not JSON) is required here because binary file data
cannot be sent in a JSON body. The PWA must use a `FormData` object for this request.

**Behavior:**
1. Validate bearer token
2. Generate a GF- ID via `id_generator.py`
3. For each uploaded image file:
   - Validate MIME type (accept: `image/jpeg`, `image/png`, `image/webp`, `image/gif`, `image/heic`)
   - Reject files over a configurable max size (default: 20MB, set in `config.yaml`)
   - Save to `<attachments_folder>/<goal_id>_<n><ext>` preserving original extension
4. Write the `.md` capture file to `_inbox/` with embedded `![[...]]` links and
   `[[...]]` attachment wikilinks (see schema above)
5. Return:
```json
{
  "id": "GF-0042",
  "status": "captured",
  "images_saved": ["GF-0042_1.jpg", "GF-0042_2.png"]
}
```

**Config additions for capture:**
```yaml
capture:
  max_image_size_mb: 20
  allowed_image_types: [jpg, jpeg, png, webp, gif, heic]
```

**Error handling:**
- Unsupported file type → 400 with clear message listing accepted types
- File exceeds size limit → 400 with size limit in message
- Partial failure (some images saved, one failed) → save what succeeded,
  return 207 Multi-Status with per-image results so the PWA can show the user
  exactly what was and wasn't saved

Note: Quick capture is accessible directly from the PWA capture view —
no separate app needed.

---

### 2.12 `dashboard.py` — Obsidian Dashboard Generator

**Responsibility:** Write/overwrite `Goals/Dashboard.md` in the vault
with a Dataview-compatible summary of all goals.

**Dashboard sections:**
1. **Active Root Goals** — top-level goals only (`depth = 0`), by horizon
2. **Due This Week** — all goals at any depth with due_date within 7 days
3. **Overdue** — goals past due_date, status not Completed, any depth
4. **Recently Completed** — last 5 completed goals at any depth
5. **In Progress Sub-Goals** — active children of active root goals (depth = 1, is_milestone = false)
6. **Inbox** — count of Draft captures awaiting review

**Example Dataview blocks:**

````markdown
## 🌳 Active Root Goals
```dataview
TABLE name, due_date, status, horizon
FROM "Goals"
WHERE depth = 0 AND status = "Active"
SORT due_date ASC
```

## 🔥 Due This Week
```dataview
TABLE name, due_date, status, parent_goal_id
FROM "Goals"
WHERE due_date <= date(today) + dur(7 days)
  AND status != "Completed"
SORT due_date ASC
```

## 🧩 Active Sub-Goals
```dataview
TABLE name, due_date, parent_goal_id, is_milestone
FROM "Goals"
WHERE depth > 0 AND status = "Active" AND is_milestone = false
SORT due_date ASC
```
````

**Trigger:** Regenerate dashboard after every scanner run.

---

## Phase 3 — PWA (Android Interface)

The PWA is a mobile-first web app served directly by FastAPI from the `pwa/` folder.
Users install it to their Android home screen via Chrome's "Add to Home Screen" prompt.
It connects to the Goal Forge backend over Tailscale.

### Views to implement

| View | Description |
|------|-------------|
| **Dashboard** | Summary cards: root goals due this week, overdue count, inbox count |
| **Goals list** | Browsable/filterable list of full goals (`is_milestone: false`) by horizon/status; root goals shown with child count badge |
| **Goal detail** | Full goal view showing: summary, description, ancestor breadcrumb trail, child goals tree (both milestones and full sub-goals), status controls, Promote/Demote button, Plan button |
| **Chat** | Text input + response area for interactive LLM mode |
| **Quick Capture** | Text form + image attachment UI; supports camera capture and gallery picker; posts to `/capture` as multipart form |
| **Inbox** | List of Draft captures with option to promote to full goal or discard |
| **Config** | Edit all system config options; dual-mode: friendly form view and raw YAML editor |
| **Jobs** | List all scheduled jobs with last-run time; trigger any job manually with one tap |
| **Logs** | Browse and tail Goal Forge log files |

### Goal Detail — Child Goals Tree

The goal detail view renders children in a two-section layout:

- **Milestones** — children where `is_milestone: true`; shown as a simple checklist
  with a "Promote to Full Goal" button on each row
- **Sub-Goals** — children where `is_milestone: false`; shown as cards with their own
  status, due date, and a "View" button that navigates into that sub-goal's detail view
  (which may itself have children)

Breadcrumb trail at the top of every goal detail view (e.g. `Run a 5K → Race Prep → Register`),
each crumb tappable to navigate up the hierarchy.

### Config Screen

The Config screen lets you edit `config.yaml` from the app without SSH access.
It has two tabs:

**Form tab** — human-readable grouped sections:
- *General* — vault path, database path, log path
- *LLM* — provider dropdown (anthropic / openrouter / ollama / vllm), model name,
  API key or base URL depending on selection
- *Push Notifications (ntfy)* — server URL, topic
- *Email (SMTP)* — host, port, username, password (masked), from/to addresses
- *Notifications* — one row per notification type with two controls each:
  - Toggle switch: enabled on/off
  - Channel selector: push / email / both
  - (For time-based ones) time picker and day selector
- *Capture* — max image size, allowed types
- *Scheduler* — scan interval

**YAML tab** — raw `config.yaml` content in a scrollable monospace text area,
fully editable. A "Validate" button checks YAML syntax before saving.

Both tabs read from and write to the same `config.yaml` file via:
```
GET  /config          → returns current config.yaml as JSON + raw YAML string
PUT  /config          → accepts updated config as JSON (form save)
PUT  /config/raw      → accepts raw YAML string (YAML editor save); validates before writing
```
Changes take effect immediately — no server restart needed.
Sensitive fields (API keys, SMTP password) are masked in the form view (show/hide toggle).

---

### Jobs Screen

Lists all scheduled jobs with status and controls:

| Job | Last Run | Next Run | Status | Action |
|-----|----------|----------|--------|--------|
| Vault Scan | 2 min ago | 13 min | ✅ OK | ▶ Run Now |
| Due Date Check | 4h ago | Tomorrow 8am | ✅ OK | ▶ Run Now |
| Daily Briefing | Today 7am | Tomorrow 7am | ✅ OK | ▶ Run Now |
| Weekly Digest | Mon 7:30am | Next Mon | ✅ OK | ▶ Run Now |
| End of Week Summary | Fri 5pm | Next Fri | ✅ OK | ▶ Run Now |
| Inbox Review | Sun 9am | Next Sun | ✅ OK | ▶ Run Now |
| Beginning of Month | — | Jun 1 | ✅ OK | ▶ Run Now |
| End of Month | — | May 31 | ✅ OK | ▶ Run Now |

Tapping **▶ Run Now** calls `POST /jobs/run/{job_name}` and shows a spinner
followed by a success/error toast. Jobs that are disabled in config show a
greyed-out row with a note "Disabled in config" instead of the Run button.

---

### Logs Screen

Displays log files from the server's `log_path` directory.

**Backend endpoints:**
```
GET /logs                     → list of log files with name, size, last modified
GET /logs/{filename}          → full log file content (paginated, newest-first)
GET /logs/{filename}/tail     → last N lines (default 100); used for live tail
```

**PWA log viewer:**
- File picker at top: dropdown of available log files
  (e.g. `goalforge.log`, `vault_changes.log`, `scanner.log`, `notifier.log`)
- Log content displayed in a scrollable monospace box, newest entries at top
- **Tail mode toggle** — when on, polls `GET /logs/{filename}/tail` every 5 seconds
  and updates the display; useful for watching a triggered job run in real time
- **Search/filter** — text input that filters displayed lines client-side
- **Log level filter** — buttons to show All / INFO / WARNING / ERROR only

**Logging requirements for the backend** (instruct Claude Code):
All modules must use Python's `logging` library with a shared configuration that:
- Writes to rotating file handlers in `log_path/` (one file per module, max 5MB, keep 3 backups)
- Also writes to a combined `goalforge.log` for the overall app log
- `vault_changes.log` gets its own dedicated handler in `vault_tools.py`
- Log format: `YYYY-MM-DD HH:MM:SS | LEVEL | module | message`

---

### Quick Capture — Image Attachment UI

The Quick Capture view must include two image input controls alongside the text fields:

**Camera button** — triggers the device camera directly:
```html
<input type="file" accept="image/*" capture="environment" id="camera-input">
```
`capture="environment"` opens the rear camera by default on Android.

**Gallery button** — opens the device photo picker:
```html
<input type="file" accept="image/*" multiple id="gallery-input">
```
`multiple` allows selecting more than one image at a time.

**Preview strip** — after selecting/capturing, show thumbnail previews of all
queued images in a horizontal scrollable strip below the text fields. Each thumbnail
has an ✕ button to remove it before submitting.

**Submission** — the capture form must submit as `multipart/form-data` using the
browser `FormData` API (not JSON). Example:

```javascript
const formData = new FormData();
formData.append("title", titleInput.value);
formData.append("description", descInput.value);
selectedImages.forEach(file => formData.append("images", file));

fetch("/capture", {
  method: "POST",
  headers: { "Authorization": `Bearer ${token}` },
  body: formData   // Do NOT set Content-Type manually — browser sets multipart boundary
});
```

**Post-submit feedback** — show which images saved successfully and flag any that
were rejected (wrong type or too large), using the 207 response from the backend.

- `manifest.json` with app name, icons, `display: standalone`, `theme_color`
- `service-worker.js` for offline shell caching (app loads even without network)
- Authentication: all API calls include the bearer token (stored in `localStorage`)
- The chat view sends messages to `POST /chat` and displays the streamed or complete reply
- Tool call results (e.g. "I updated your goal status to Active") should be shown
  as a subtle inline note below the LLM reply so the user knows what changed
- Delete actions in chat must show a confirmation dialog in the PWA before sending
  `confirmed: true` to the backend

### Tailscale setup note (include in README)
Install Tailscale on both the home server and your Android phone. The PWA URL will be
something like `http://100.x.x.x:8742` (Tailscale IP). For a cleaner URL, enable
MagicDNS in your Tailscale account (e.g. `http://goalforge:8742`).

---

## Phase 4 — Deployment on Home Server

### `main.py` — Entry Point
Starts both the FastAPI server and APScheduler in the same process:

```python
import uvicorn
from goalforge.scheduler import start_scheduler
from goalforge.capture import app  # FastAPI app

if __name__ == "__main__":
    start_scheduler()
    uvicorn.run(app, host="0.0.0.0", port=8742)
```

### Systemd Service
Claude Code should generate a `goal-forge.service` file:

```ini
[Unit]
Description=Goal Forge
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/goal-forge/main.py
WorkingDirectory=/opt/goal-forge
Restart=always
User=youruser
EnvironmentFile=/opt/goal-forge/.env

[Install]
WantedBy=multi-user.target
```

### `requirements.txt`
```
python-frontmatter
anthropic
fastapi
uvicorn
apscheduler
httpx
pyyaml
sqlite-utils
python-multipart     # Required by FastAPI for multipart/form-data file uploads
aiosmtplib           # Async SMTP for sending email notifications
jinja2               # HTML email templating for rich digest/summary emails
```

---

## Build Order for Claude Code

Ask Claude Code to implement in this sequence to avoid dependency issues:

1. **Project scaffold** — folder structure, `config.yaml`, `requirements.txt`
2. **`id_generator.py`** — needed by everything else
3. **`database.py`** — schema + helper functions
4. **`scanner.py`** — reads vault, populates DB; test with a sample `.md` file
5. **`llm/`** — base class, all four providers, factory; test each provider standalone
6. **`planner.py`** — child goal generation using LLM factory
7. **`vault_tools.py`** — all vault read/write operations
8. **`interactive.py`** — chat session manager + `/chat` endpoint
9. **`notifier.py`** — push + email delivery, all 8 notification types, LLM content generation
10. **`scheduler.py`** — all jobs wired up; `/jobs/run/{job_name}` manual trigger endpoint
11. **`config_api.py`** — `GET/PUT /config` and `PUT /config/raw` endpoints
12. **`logs_api.py`** — `GET /logs`, `GET /logs/{filename}`, `GET /logs/{filename}/tail` endpoints; configure rotating log handlers for all modules
13. **`capture.py`** — quick-capture endpoint with image handling
14. **`dashboard.py`** — generate Dashboard.md
15. **`pwa/`** — full PWA frontend: Dashboard, Goals, Goal Detail, Chat, Quick Capture, Inbox, Config, Jobs, Logs
16. **`main.py`** — tie everything together, serve PWA static files
17. **`goal-forge.service`** — systemd unit file
18. **`README.md`** — setup instructions including Tailscale + Gmail App Password config

---

## Prompt to Give Claude Code

Paste this to start the build session:

---

> I want to build a self-hosted Python project called **Goal Forge**. It reads Obsidian
> Markdown goal files from a local folder (synced via Syncthing), tracks them in SQLite,
> generates AI-powered child goals using a pluggable LLM provider system, sends
> push notifications via ntfy.sh and email via SMTP/Gmail, supports an interactive chat
> mode with full vault read/write access, and serves a mobile PWA as the Android interface.
>
> Please refer to the attached plan document for the full spec including file structure,
> YAML frontmatter schema, SQLite schema, all module responsibilities, the LLM provider
> abstraction layer, vault tools, interactive chat mode, all notification types, PWA views
> (including Config, Jobs, and Logs screens), scheduler job definitions, and the systemd
> service file.
>
> Build in this order: id_generator → database → scanner → llm providers → planner →
> vault_tools → interactive → notifier → scheduler → config_api → logs_api → capture →
> dashboard → PWA → main → systemd service → README.
>
> Key constraints:
> - Each notification type has independent `enabled` (on/off) and `channel` (push/email/both) settings in config
> - Notification settings are read at job execution time so PWA config changes take effect without restart
> - Push uses ntfy.sh HTTP API; email uses aiosmtplib with Gmail SMTP (App Password)
> - The 4 rich notifications (daily briefing, end of week, beginning of month, end of month) use the LLM to generate content; email versions use inline-styled HTML + plain text fallback via Jinja2
> - Manual job trigger endpoint: `POST /jobs/run/{job_name}` — runs in background thread, returns immediately
> - Config API: `GET/PUT /config` (JSON form data) and `PUT /config/raw` (raw YAML with validation)
> - Logs API serves files from `log_path`; `/tail` endpoint returns last N lines for live tail polling
> - All modules use Python rotating file log handlers (5MB max, 3 backups); combined `goalforge.log` + per-module files + dedicated `vault_changes.log`
> - Log format: `YYYY-MM-DD HH:MM:SS | LEVEL | module | message`
> - PWA Config screen has two tabs: friendly form view and raw YAML editor with validation
> - PWA Jobs screen shows last-run, next-run, status for all jobs with one-tap manual trigger
> - PWA Logs screen has file picker, tail mode (5s poll), text search, and log level filter
> - Quick capture accepts images via multipart/form-data; use `capture="environment"` for camera and `multiple` for gallery
> - Images saved to `Goals/_inbox/attachments/` as `{goal_id}_{n}{ext}`; originals kept as-is (no resizing)
> - Capture `.md` files embed images with `![[...]]` AND list them with `[[...]]` wikilinks
> - Partial image upload failures return HTTP 207 with per-image status
> - Goals and milestones use an identical schema — the only difference is `is_milestone` flag
> - Any child goal can be promoted to a full goal or demoted to a milestone at any time
> - `depth` is auto-computed on upsert by walking the parent chain; never set manually
> - The planner works on any goal at any depth and passes ancestry context to the LLM
> - Planning a milestone automatically promotes it to a full goal first
> - `reparent_goal` in vault_tools must recompute `depth` for the moved goal and all its descendants
> - The PWA Goal detail view shows a breadcrumb trail and separates children into Milestones vs Sub-Goals sections
> - All config lives in `config.yaml`, no hardcoded values
> - LLM provider is set once in config — supported providers: anthropic, openrouter, ollama, vllm
> - All four providers implement the same `LLMProvider` base class with a `chat()` method
> - vault_tools.py must validate all paths stay within vault root (no path traversal)
> - All vault write/delete operations must be logged to `vault_changes.log`
> - Delete in interactive mode requires `confirmed=True` from the PWA confirmation dialog
> - The PWA must be a proper installable PWA with manifest.json and service worker
> - Access is secured via Tailscale; README must include Tailscale setup steps and Gmail App Password instructions
> - Use `python-frontmatter`, `anthropic`, `fastapi`, `uvicorn`, `apscheduler`, `httpx`, `pyyaml`, `sqlite-utils`, `python-multipart`, `aiosmtplib`, `jinja2`
> - Target Python 3.11+ on Linux (home server)
> - Generate a working `goal-forge.service` systemd unit file

---

## Open Questions to Resolve Before Starting - Answers below each question

1. **Vault path on your server** — what is the Syncthing-mounted path to your Obsidian vault?
this is the root of my vault: /home/matt/14TBShare/Obsidian Vault/Main Vault

2. **vLLM endpoint and model** — what URL and model name is your vLLM instance running?
vLLM endpoint: http://localhost:8000/v1
Model: cyankiwi/Qwen3.5-9B-AWQ-4bit
API Key: local

3. **ntfy.sh topic name** — pick something unique and not guessable
MattsGoalTopic

4. **Self-host ntfy or use ntfy.sh cloud?** — cloud is easiest; self-hosted adds privacy
Self-host

5. **Gmail address for notifications** — the `smtp_user` and `to_address` in config; you'll need to generate a Gmail App Password at myaccount.google.com → Security → App Passwords (requires 2FA to be enabled)
matt@voight.net

6. **Home server OS** — Linux assumed; if it's a NAS (Synology/QNAP) let Claude Code know
Yes, Linux

7. **Tailscale already set up?** — if not, that's a prerequisite before first use of the PWA remotely
No, no yet
