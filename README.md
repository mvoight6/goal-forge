# ⚒️ Goal Forge

Self-hosted goal tracking system that bridges your Obsidian vault with AI-powered planning, push notifications, and a mobile PWA interface.

## Features

- **Obsidian-native** — goals live as Markdown files with YAML frontmatter, synced via Syncthing
- **AI Planning** — generate child goals/milestones using your local vLLM, Ollama, Claude, or OpenRouter
- **Push Notifications** — ntfy.sh push + Gmail SMTP email for 8 notification types
- **Interactive Chat** — LLM-powered coach with full vault read/write access
- **Mobile PWA** — installable on Android via Chrome; works over Tailscale

---

## Prerequisites

- Python 3.11+ on Linux
- Obsidian vault accessible on the server (Syncthing or local)
- [ntfy](https://ntfy.sh) running locally (or ntfy.sh cloud)
- Gmail account with App Password (for email notifications)
- [Tailscale](https://tailscale.com) on server + Android (for remote access)

---

## Installation

### 1. Clone and install dependencies

```bash
git clone <your-repo-url> /opt/goal-forge
cd /opt/goal-forge
pip3 install -r requirements.txt
```

### 2. Configure

Edit `config.yaml` — the file is pre-populated with sensible defaults. Required changes:

```yaml
vault_path: "/home/matt/14TBShare/Obsidian Vault/Main Vault"  # already set
api:
  secret_token: "your-secret-token-here"   # CHANGE THIS
email:
  smtp_password: "your-gmail-app-password"  # see below
```

### 3. Create the Obsidian Goals folder

Make sure this folder exists in your vault:
```
<vault_root>/Goals/
<vault_root>/Goals/_inbox/
<vault_root>/Goals/_inbox/attachments/
```

Or Goal Forge will create them automatically on first scan.

### 4. Create the database directory

```bash
sudo mkdir -p /opt/goal-forge/logs
sudo chown matt:matt /opt/goal-forge /opt/goal-forge/logs
```

### 5. Test it

```bash
cd /opt/goal-forge
python3 main.py
```

Open `http://localhost:8742` in a browser. Enter your `secret_token` to log in.

---

## Systemd Service

To run Goal Forge as a background service:

```bash
sudo cp goal-forge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable goal-forge
sudo systemctl start goal-forge
sudo systemctl status goal-forge
```

View logs:
```bash
journalctl -u goal-forge -f
# or
tail -f /opt/goal-forge/logs/goalforge.log
```

---

## Gmail App Password Setup

Required for email notifications. App Passwords bypass 2FA safely.

1. Go to [myaccount.google.com](https://myaccount.google.com) → **Security**
2. Ensure **2-Step Verification** is enabled
3. Search for **"App Passwords"** in the search bar
4. Select app: **Mail**, device: **Other** → name it "Goal Forge"
5. Copy the 16-character password into `config.yaml`:
   ```yaml
   email:
     smtp_password: "abcd efgh ijkl mnop"  # paste without spaces
   ```

---

## Self-hosted ntfy Setup

ntfy is the push notification server. Run it on the same machine as Goal Forge:

### Using Docker (easiest)

```bash
docker run -p 80:80 -v /opt/ntfy:/etc/ntfy binwiederhier/ntfy serve
```

### Or install directly

```bash
sudo apt install ntfy
sudo systemctl enable ntfy --now
```

Then update `config.yaml`:
```yaml
ntfy:
  server: "http://localhost:80"
  topic: "MattsGoalTopic"
```

Subscribe to notifications on your Android: install the [ntfy app](https://play.google.com/store/apps/details?id=io.heckel.ntfy) and subscribe to `http://<tailscale-ip>/MattsGoalTopic`.

---

## Tailscale Setup

Tailscale creates a private network between your server and Android — no port forwarding needed.

### On the server

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

### On Android

1. Install [Tailscale](https://play.google.com/store/apps/details?id=com.tailscale.ipn.android) from the Play Store
2. Sign in with the same account used on the server
3. Both devices should appear in your Tailscale admin panel

### Find your Tailscale IP

```bash
tailscale ip -4
# e.g. 100.64.1.23
```

The PWA URL will be: `http://100.64.1.23:8742`

**For a cleaner URL** — enable MagicDNS in [Tailscale admin](https://login.tailscale.com/admin/dns) and use:
`http://goalforge:8742`

### Install the PWA on Android

1. Open `http://<tailscale-ip>:8742` in Chrome
2. Tap the **⋮ menu → Add to Home screen**
3. Goal Forge installs as a standalone app

---

## Using Goal Forge

### Goal file format

Place `.md` files in `<vault_root>/Goals/` with this frontmatter:

```yaml
---
id: GF-0001
name: "Run a 5K"
status: Active
horizon: Monthly
due_date: 2025-09-01
parent_goal_id:
depth: 0
is_milestone: false
category: Health
created_date: 2025-07-01
notify_before_days: 3
tags: [goal]
---
```

Goals without an `id` are assigned one automatically on the next scan.

### AI Planning

From the PWA Goal Detail screen, tap **🤖 Plan with AI** to generate child goals.
Or from the command line:

```bash
python3 main.py plan GF-0001
```

### Manual vault scan

```bash
python3 main.py scan
```

### LLM Providers

Change `config.yaml` → `llm.provider` to switch providers:

| Provider | Config key | Notes |
|----------|-----------|-------|
| vLLM (default) | `vllm` | Local; set `base_url` and `model` |
| Ollama | `ollama` | Local; `http://localhost:11434` |
| Claude API | `anthropic` | Requires `api_key` |
| OpenRouter | `openrouter` | Requires `api_key` |

---

## Project Structure

```
goal-forge/
├── main.py               # Entry point
├── config.yaml           # All configuration
├── requirements.txt
├── goal-forge.service    # Systemd unit
├── goalforge/
│   ├── config.py         # Config loader
│   ├── database.py       # SQLite layer
│   ├── scanner.py        # Vault scanner
│   ├── planner.py        # AI goal planning
│   ├── notifier.py       # Push + email notifications
│   ├── scheduler.py      # APScheduler jobs
│   ├── capture.py        # Quick capture + goals API
│   ├── interactive.py    # Chat mode
│   ├── vault_tools.py    # Vault read/write for LLM
│   ├── dashboard.py      # Dashboard.md generator
│   ├── config_api.py     # Config endpoints
│   ├── logs_api.py       # Log endpoints
│   ├── id_generator.py   # GF- ID generation
│   └── llm/              # LLM provider abstraction
├── pwa/                  # Mobile PWA
└── templates/email/      # Jinja2 email templates
```

---

## Notification Types

| Type | When | Channel |
|------|------|---------|
| Due Soon | Daily at 8am | Push |
| Overdue | Daily at 8am | Push |
| Daily Briefing | Configurable time | Push |
| Weekly Digest | Monday morning | Push |
| End of Week Summary | Friday afternoon | Email |
| Inbox Review | Sunday morning | Push |
| Beginning of Month | 1st of month | Email |
| End of Month | Last day of month | Email |

All types can be enabled/disabled and channel (push/email/both) configured from the PWA Config screen.

---

## Troubleshooting

**App won't connect from Android:**
- Ensure Tailscale is running on both devices and both are signed in to the same account
- Check `tailscale status` on the server — both devices should show as "connected"
- Try pinging the server from Android: `ping <tailscale-ip>` in a terminal app

**Notifications not arriving:**
- Check ntfy is running: `curl http://localhost:80/MattsGoalTopic/json`
- Verify ntfy app on Android is subscribed to the correct topic and server URL
- Check `/opt/goal-forge/logs/goalforge_notifier.log`

**LLM not responding:**
- Verify vLLM is running: `curl http://localhost:8000/v1/models`
- Check the model name in config matches what vLLM is serving
- Check `/opt/goal-forge/logs/goalforge_llm_vllm.log`

**Goals not appearing after adding .md files:**
- Trigger a manual scan from the PWA Jobs screen, or run `python3 main.py scan`
- Ensure files have `name:` in frontmatter (required field)
