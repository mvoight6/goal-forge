# ⚒️ Goal Forge

Self-hosted goal tracking system that bridges your Obsidian vault with AI-powered planning, push notifications, and a mobile PWA interface.

## Features

- **Obsidian-native** — goals live as Markdown files with YAML frontmatter, synced via Syncthing
- **Daily Goals** — Google Keep-style daily checklist with reordering, move-to-tomorrow, and quick-add
- **AI Planning** — generate child goals/milestones using your local vLLM, Ollama, Claude, or OpenRouter
- **Interactive Chat** — LLM-powered coach with full vault read/write access and daily goal awareness
- **Push Notifications** — self-hosted ntfy push + Gmail SMTP email for 8 notification types
- **Mobile PWA** — installable on Android via Chrome; accessible over HTTPS via Caddy reverse proxy

---

## Prerequisites

- Python 3.11+ on Linux
- Obsidian vault accessible on the server (Syncthing or local mount)
- [ntfy](https://ntfy.sh) for push notifications (self-hosted or ntfy.sh cloud)
- Gmail account with App Password (for email notifications)
- A domain name + Caddy for HTTPS remote access (optional but recommended)

---

## Installation

### 1. Clone and install dependencies

```bash
git clone https://github.com/mvoight6/goal-forge /opt/goal-forge
cd /opt/goal-forge
pip3 install -r requirements.txt
```

### 2. Configure

Copy the example config and edit it:

```bash
cp config.yaml.example config.yaml
```

Required changes:

```yaml
vault_path: "/path/to/your/obsidian/vault"
api:
  secret_token: "your-secret-token-here"   # CHANGE THIS
email:
  smtp_password: "your-gmail-app-password"
```

### 3. Vault folder structure

Goal Forge will create these automatically, but you can pre-create them:

```
<vault_root>/Goals/
<vault_root>/Goals/_inbox/
<vault_root>/Goals/_inbox/attachments/
<vault_root>/Goals/Daily/
```

### 4. Create the database directory

```bash
sudo mkdir -p /opt/goal-forge/logs
sudo chown $USER:$USER /opt/goal-forge /opt/goal-forge/logs
```

### 5. Test it

```bash
cd /opt/goal-forge
python3 main.py
```

Open `http://localhost:8742` in a browser and enter your `secret_token` to log in.

---

## Systemd Service

To run Goal Forge as a background service that starts on boot:

```bash
sudo cp goal-forge.service /etc/systemd/system/
# Edit the service file if your code lives somewhere other than /opt/goal-forge
sudo systemctl daemon-reload
sudo systemctl enable goal-forge
sudo systemctl start goal-forge
sudo systemctl status goal-forge
```

View logs:
```bash
journalctl -u goal-forge -f
```

---

## HTTPS Remote Access (Caddy + Let's Encrypt)

To access Goal Forge securely from outside your home network, use Caddy as a reverse proxy with automatic HTTPS.

### 1. Install Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

### 2. Configure Caddyfile

Edit `/etc/caddy/Caddyfile`:

```
goals.yourdomain.com {
    reverse_proxy localhost:8742
}

notify.yourdomain.com {
    reverse_proxy localhost:2586
}
```

```bash
sudo systemctl reload caddy
```

### 3. Port forward on your router

Forward TCP ports **80** and **443** to your server's local IP. Caddy handles Let's Encrypt certificates automatically.

### 4. Install the PWA on Android

1. Open `https://goals.yourdomain.com` in Chrome
2. Tap **⋮ menu → Add to Home screen**
3. Goal Forge installs as a standalone app

---

## Self-hosted ntfy Setup

ntfy provides push notifications to your Android device.

### Install

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://archive.heckel.io/apt/pubkey.txt | sudo gpg --dearmor -o /etc/apt/keyrings/archive.heckel.io.gpg
sudo sh -c "echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/archive.heckel.io.gpg] https://archive.heckel.io/apt debian main' > /etc/apt/sources.list.d/archive.heckel.io.list"
sudo apt update && sudo apt install ntfy
```

### Configure

Edit `/etc/ntfy/server.yml`:

```yaml
listen-http: ":2586"
base-url: "https://notify.yourdomain.com"
```

```bash
sudo systemctl enable ntfy --now
```

### Update config.yaml

```yaml
ntfy:
  server: "https://notify.yourdomain.com"
  topic: "YourTopic"
```

### Subscribe on Android

Install the [ntfy app](https://play.google.com/store/apps/details?id=io.heckel.ntfy), tap **+**, choose **Use another server**, and enter your ntfy URL and topic.

---

## Gmail App Password Setup

Required for email notifications.

1. Go to [myaccount.google.com](https://myaccount.google.com) → **Security**
2. Ensure **2-Step Verification** is enabled
3. Search for **"App Passwords"**
4. Create a password for "Goal Forge" and copy it into `config.yaml`:
   ```yaml
   email:
     smtp_password: "abcdefghijklmnop"
   ```

---

## Using Goal Forge

### PWA Views

| View | Description |
|------|-------------|
| **Dashboard** | Overview — Today's Goals, due this week, overdue, active goals |
| **Daily** | Daily checklist — last 7 days + any future days with items |
| **Goals** | Full goal hierarchy — collapsible tree, filter by status/horizon |
| **Capture** | Quick capture — text + images saved as Draft goals |
| **More** | Chat, Inbox, Config, Jobs, Logs |

### Daily Goals

Daily Goals are a simple day-by-day checklist (like Google Keep), separate from your strategic goal hierarchy.

- Items are stored in `Goals/Daily/` in your vault
- Each day has a parent goal (`Daily Goals YYYY-MM-DD`) with items as children
- Check items off as you complete them — status persists through restarts
- Reorder items within a day using the ▲/▼ buttons
- Move incomplete items to tomorrow with the **→ Tmrw** button
- Ask the chat AI to add daily items: *"Add 'call dentist' to today's list"*

### Goal File Format

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
├── main.py                # Entry point
├── config.yaml.example    # Config template (copy to config.yaml)
├── requirements.txt
├── goal-forge.service     # Systemd unit
├── goalforge/
│   ├── config.py          # Config loader (hot-reload)
│   ├── database.py        # SQLite layer
│   ├── scanner.py         # Vault scanner (Goals/ + Goals/Daily/)
│   ├── planner.py         # AI goal planning
│   ├── notifier.py        # Push + email notifications
│   ├── scheduler.py       # APScheduler jobs
│   ├── capture.py         # Quick capture + goals API
│   ├── daily_api.py       # Daily Goals API
│   ├── interactive.py     # Chat mode + LLM tool calling
│   ├── vault_tools.py     # Vault read/write for LLM
│   ├── dashboard.py       # Dashboard.md generator
│   ├── config_api.py      # Config endpoints
│   ├── logs_api.py        # Log endpoints
│   ├── id_generator.py    # GF- ID generation
│   └── llm/               # LLM provider abstraction
├── pwa/                   # Mobile PWA (vanilla JS)
└── templates/email/       # Jinja2 email templates
```

---

## Notification Types

| Type | When | Default Channel |
|------|------|----------------|
| Due Soon | Daily at 8am | Push |
| Overdue | Daily at 8am | Push |
| Daily Briefing | Configurable time | Push |
| Weekly Digest | Monday morning | Push |
| End of Week Summary | Friday afternoon | Email |
| Inbox Review | Sunday morning | Push |
| Beginning of Month | 1st of month | Email |
| End of Month | Last day of month | Email |

All types can be enabled/disabled and routed to push, email, or both from the PWA Config screen.

---

## Troubleshooting

**App won't connect remotely:**
- Verify Caddy is running: `sudo systemctl status caddy`
- Check DNS resolves to your public IP: `nslookup goals.yourdomain.com`
- Ensure ports 80 and 443 are forwarded on your router

**Notifications not arriving:**
- Test ntfy: `curl https://notify.yourdomain.com/YourTopic -d "test"`
- Verify the ntfy app is subscribed to the correct server URL and topic
- Check logs: `journalctl -u ntfy`

**LLM not responding:**
- Verify vLLM is running: `curl http://localhost:8000/v1/models`
- Check the model name in config matches what vLLM is serving

**Goals not appearing after adding .md files:**
- Trigger a manual scan from the PWA Jobs screen, or run `python3 main.py scan`
- Ensure files have a `name:` field in frontmatter (required)

**Checkboxes resetting after restart:**
- Ensure you're on the latest version — earlier versions didn't write status back to vault files
