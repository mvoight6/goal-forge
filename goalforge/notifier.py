"""
Notification engine — push (ntfy.sh) and email (SMTP/Gmail) delivery.
All 8 notification types with deduplication.
LLM-generated content for the 4 rich notification types.
"""
import asyncio
import logging
import smtplib
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import httpx
from jinja2 import Environment, FileSystemLoader

from goalforge.config import config
from goalforge import database
from goalforge.llm.factory import get_provider

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "email"
_jinja = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)


# ---------------------------------------------------------------------------
# Delivery primitives
# ---------------------------------------------------------------------------

def send_push(title: str, body: str, priority: str = "default", tags: list = None):
    """Send a push notification via ntfy.sh."""
    ntfy_cfg = config.ntfy
    url = f"{ntfy_cfg.server}/{ntfy_cfg.topic}"
    try:
        httpx.post(
            url,
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": ",".join(tags or []),
            },
            content=body.encode("utf-8"),
            timeout=10,
        )
        logger.info("Push sent: %s", title)
    except Exception as e:
        logger.error("Push notification failed: %s", e)


def send_email(subject: str, body_text: str, body_html: Optional[str] = None):
    """Send an email via SMTP (Gmail). Uses synchronous smtplib."""
    email_cfg = config.email
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_cfg.from_address
    msg["To"] = email_cfg.to_address

    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(email_cfg.smtp_host, int(email_cfg.smtp_port)) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(email_cfg.smtp_user, email_cfg.smtp_password)
            smtp.sendmail(email_cfg.from_address, email_cfg.to_address, msg.as_string())
        logger.info("Email sent: %s", subject)
    except Exception as e:
        logger.error("Email send failed: %s", e)


def deliver(notification_type: str, title: str, body: str, html_body: Optional[str] = None):
    """
    Route a notification based on config channel setting for the given type.
    Returns immediately if the type is disabled.
    """
    n_cfg = config.notifications
    type_cfg = getattr(n_cfg, notification_type, None)
    if not type_cfg:
        logger.warning("No config for notification type: %s", notification_type)
        return
    if not type_cfg.enabled:
        logger.debug("Notification '%s' is disabled — skipping", notification_type)
        return

    channel = type_cfg.channel or "push"
    if channel in ("push", "both"):
        send_push(title, body)
    if channel in ("email", "both"):
        send_email(title, body, html_body)


def _render_email(template_name: str, **context) -> str:
    tpl = _jinja.get_template(template_name)
    return tpl.render(port=config.api.port, **context)


# ---------------------------------------------------------------------------
# LLM-generated content helpers
# ---------------------------------------------------------------------------

def _llm_generate(prompt: str) -> str:
    try:
        provider = get_provider()
        return provider.chat(
            system="You are Joe MacMillan — visionary, direct, and demanding in the best way. You write short, punchy goal coaching content that challenges the reader to move faster and think bigger. No fluff. Every sentence earns its place.",
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.error("LLM generation failed: %s", e)
        return "(AI content unavailable)"


# ---------------------------------------------------------------------------
# Individual notification handlers
# ---------------------------------------------------------------------------

def check_due_dates():
    """Daily check: due_soon and goal_overdue notifications."""
    # Due soon
    due_soon_cfg = config.notifications.due_soon
    if due_soon_cfg.enabled:
        days = int(due_soon_cfg.days_before or 3)
        goals = database.get_goals_due_within(days)
        for g in goals:
            if not database.was_notification_sent_today(g["id"], "due_soon"):
                days_left = (
                    datetime.strptime(str(g["due_date"]), "%Y-%m-%d").date() - date.today()
                ).days
                title = f"⏰ [{g['id']}] Due in {days_left}d: {g['name']}"
                body = f"{g['name']} is due in {days_left} day(s) ({g['due_date']}). Status: {g.get('status', 'Unknown')}"
                deliver("due_soon", title, body)
                database.mark_notification_sent(g["id"], "due_soon")

    # Overdue
    overdue_cfg = config.notifications.goal_overdue
    if overdue_cfg.enabled:
        goals = database.get_goals_overdue()
        for g in goals:
            if not database.was_notification_sent_today(g["id"], "goal_overdue"):
                title = f"🚨 [{g['id']}] Overdue: {g['name']}"
                body = f"{g['name']} was due {g['due_date']} and is not yet completed."
                deliver("goal_overdue", title, body)
                database.mark_notification_sent(g["id"], "goal_overdue")


def send_daily_morning_briefing():
    """Daily morning briefing — LLM-generated focus summary."""
    period_key = date.today().isoformat()
    if database.was_digest_sent("daily_morning_briefing", period_key):
        return

    active_goals = database.get_all_goals({"status": "Active"})
    due_today = database.get_goals_due_within(0)
    due_soon = database.get_goals_due_within(3)
    overdue = database.get_goals_overdue()

    goal_summary = "\n".join(f"- [{g['id']}] {g['name']} (due {g.get('due_date', 'N/A')})" for g in active_goals[:20])
    overdue_str = "\n".join(f"- [{g['id']}] {g['name']}" for g in overdue[:5])

    prompt = f"""Today is {date.today().strftime('%A, %B %d, %Y')}.

Active goals:
{goal_summary or 'None'}

Overdue:
{overdue_str or 'None'}

Write a short (3-5 sentence) morning briefing that tells the user what to focus on today to move their goals forward. Be motivating and specific."""

    text = _llm_generate(prompt)
    title = f"☀️ Goal Forge — {date.today().strftime('%A')} Briefing"

    html = _render_email(
        "daily_briefing.html",
        subject=title,
        subtitle=date.today().strftime("%B %d, %Y"),
        briefing_text=text,
        due_today=due_today,
        overdue=overdue,
    )

    deliver("daily_morning_briefing", title, text, html)
    database.mark_digest_sent("daily_morning_briefing", period_key)


def send_weekly_digest():
    """Monday morning — push digest of active goals due this week."""
    today = date.today()
    week_key = today.strftime("%Y-W%W")
    if database.was_digest_sent("weekly_digest", week_key):
        return

    week_end = today + timedelta(days=6)
    goals = database.get_goals_due_within(7)

    if not goals:
        body = "No goals due this week. Good time to plan ahead!"
    else:
        lines = [f"• [{g['id']}] {g['name']} (due {g.get('due_date', '?')})" for g in goals]
        body = "Goals due this week:\n" + "\n".join(lines)

    deliver("weekly_digest", f"📋 Weekly Digest — {today.strftime('%b %d')}", body)
    database.mark_digest_sent("weekly_digest", week_key)


def send_end_of_week_summary():
    """Friday afternoon — LLM-generated week reflection email."""
    today = date.today()
    week_key = today.strftime("%Y-W%W-eow")
    if database.was_digest_sent("end_of_week_summary", week_key):
        return

    week_start = today - timedelta(days=today.weekday())
    week_end = today
    completed = database.get_recently_completed(10)
    active = database.get_all_goals({"status": "Active"})

    completed_str = "\n".join(f"- {g['name']}" for g in completed[:10])
    active_str = "\n".join(f"- {g['name']} (due {g.get('due_date', '?')})" for g in active[:15])

    prompt = f"""Week of {week_start.strftime('%B %d')} to {week_end.strftime('%B %d, %Y')}.

Completed:
{completed_str or 'None'}

Still active:
{active_str or 'None'}

Write a warm end-of-week reflection (3-4 sentences). Celebrate wins, acknowledge what's still in progress, and close on an encouraging note."""

    text = _llm_generate(prompt)
    title = f"🎉 Week in Review — {week_start.strftime('%b %d')} to {week_end.strftime('%b %d')}"

    html = _render_email(
        "end_of_week.html",
        subject=title,
        subtitle="Weekly Summary",
        week_start=week_start.strftime("%B %d"),
        week_end=week_end.strftime("%B %d, %Y"),
        summary_text=text,
        completed=completed,
        active=active[:10],
    )

    deliver("end_of_week_summary", title, text, html)
    database.mark_digest_sent("end_of_week_summary", week_key)


def send_inbox_review_prompt():
    """Sunday morning — push reminder to review inbox captures."""
    period_key = date.today().isoformat()
    if database.was_digest_sent("inbox_review", period_key):
        return

    drafts = database.get_draft_captures()
    count = len(drafts)
    if count == 0:
        return  # Nothing to review

    title = "📥 Inbox Review"
    body = f"You have {count} captured idea{'s' if count != 1 else ''} waiting for review. Take a moment to process your inbox."

    deliver("inbox_review", title, body)
    database.mark_digest_sent("inbox_review", period_key)


def send_beginning_of_month():
    """1st of month — LLM-generated monthly plan email."""
    today = date.today()
    period_key = today.strftime("%Y-%m-bom")
    if database.was_digest_sent("beginning_of_month", period_key):
        return

    month_name = today.strftime("%B %Y")
    # Goals due this month
    month_end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    days_in_month = (month_end - today).days
    month_goals = database.get_goals_due_within(days_in_month)
    active_goals = database.get_all_goals({"status": "Active"})

    goals_str = "\n".join(f"- [{g['id']}] {g['name']} (due {g.get('due_date', '?')})" for g in month_goals[:20])
    active_str = "\n".join(f"- {g['name']}" for g in active_goals[:15])

    prompt = f"""It's the beginning of {month_name}.

Goals due this month:
{goals_str or 'None'}

All active goals:
{active_str or 'None'}

Write a motivating 3-4 sentence monthly plan introduction. Highlight the key goals to focus on this month and set an energetic tone."""

    text = _llm_generate(prompt)
    title = f"🗓️ {month_name} — Goal Plan"

    html = _render_email(
        "beginning_of_month.html",
        subject=title,
        subtitle=f"Beginning of {month_name}",
        month_name=month_name,
        plan_text=text,
        month_goals=month_goals,
    )

    deliver("beginning_of_month", title, text, html)
    database.mark_digest_sent("beginning_of_month", period_key)


def send_end_of_month():
    """Last day of month — LLM-generated monthly summary email."""
    today = date.today()
    period_key = today.strftime("%Y-%m-eom")
    if database.was_digest_sent("end_of_month", period_key):
        return

    month_name = today.strftime("%B %Y")
    completed = database.get_recently_completed(20)
    overdue = database.get_goals_overdue()

    completed_str = "\n".join(f"- {g['name']}" for g in completed[:15])
    slipped_str = "\n".join(f"- {g['name']} (was due {g.get('due_date', '?')})" for g in overdue[:10])

    prompt = f"""It's the end of {month_name}.

Completed this month:
{completed_str or 'None'}

Goals that slipped / are overdue:
{slipped_str or 'None'}

Write a warm, congratulatory end-of-month summary (3-4 sentences). Celebrate accomplishments, briefly acknowledge what slipped without judgment, and express optimism for next month."""

    text = _llm_generate(prompt)
    title = f"🏁 {month_name} — Month Complete"

    html = _render_email(
        "end_of_month.html",
        subject=title,
        subtitle=f"End of {month_name}",
        month_name=month_name,
        summary_text=text,
        completed=completed,
        slipped=overdue,
    )

    deliver("end_of_month", title, text, html)
    database.mark_digest_sent("end_of_month", period_key)
