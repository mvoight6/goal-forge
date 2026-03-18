"""
APScheduler job scheduler — all 8 recurring jobs + manual trigger endpoint.
All schedule config is read at job execution time so PWA config changes take effect
without a server restart.
"""
import logging
import threading
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from goalforge.config import config

logger = logging.getLogger(__name__)
router = APIRouter()
bearer = HTTPBearer()

_scheduler: Optional[BackgroundScheduler] = None

# Track last-run times per job
_last_run: dict[str, Optional[datetime]] = {
    "scan": None,
    "check_due_dates": None,
    "daily_morning_briefing": None,
    "weekly_digest": None,
    "end_of_week_summary": None,
    "inbox_review": None,
    "beginning_of_month": None,
    "end_of_month": None,
}


def _record_run(job_name: str):
    _last_run[job_name] = datetime.utcnow()


def _parse_time(time_str: str) -> tuple[int, int]:
    """Parse 'HH:MM' string into (hour, minute) integers."""
    parts = str(time_str).split(":")
    return int(parts[0]), int(parts[1])


# ---------------------------------------------------------------------------
# Job wrappers — read config at execution time
# ---------------------------------------------------------------------------

def _job_scan():
    _record_run("scan")
    from goalforge import scanner
    scanner.run_scan()


def _job_check_due_dates():
    _record_run("check_due_dates")
    from goalforge import notifier
    notifier.check_due_dates()


def _job_daily_morning_briefing():
    _record_run("daily_morning_briefing")
    from goalforge import notifier
    notifier.send_daily_morning_briefing()


def _job_weekly_digest():
    _record_run("weekly_digest")
    from goalforge import notifier
    notifier.send_weekly_digest()


def _job_end_of_week_summary():
    _record_run("end_of_week_summary")
    from goalforge import notifier
    notifier.send_end_of_week_summary()


def _job_inbox_review():
    _record_run("inbox_review")
    from goalforge import notifier
    notifier.send_inbox_review_prompt()


def _job_beginning_of_month():
    _record_run("beginning_of_month")
    from goalforge import notifier
    notifier.send_beginning_of_month()


def _job_end_of_month():
    _record_run("end_of_month")
    from goalforge import notifier
    notifier.send_end_of_month()


JOB_MAP = {
    "scan": _job_scan,
    "check_due_dates": _job_check_due_dates,
    "daily_morning_briefing": _job_daily_morning_briefing,
    "weekly_digest": _job_weekly_digest,
    "end_of_week_summary": _job_end_of_week_summary,
    "inbox_review": _job_inbox_review,
    "beginning_of_month": _job_beginning_of_month,
    "end_of_month": _job_end_of_month,
}


def start_scheduler():
    global _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")

    scan_minutes = int(config.scheduler.scan_interval_minutes or 15)
    _scheduler.add_job(_job_scan, IntervalTrigger(minutes=scan_minutes), id="scan", replace_existing=True)

    # Daily due-date check at 08:00
    _scheduler.add_job(_job_check_due_dates, CronTrigger(hour=8, minute=0), id="check_due_dates", replace_existing=True)

    # Daily morning briefing
    bh, bm = _parse_time(config.notifications.daily_morning_briefing.time or "07:00")
    _scheduler.add_job(_job_daily_morning_briefing, CronTrigger(hour=bh, minute=bm), id="daily_morning_briefing", replace_existing=True)

    # Weekly digest — Monday
    wh, wm = _parse_time(config.notifications.weekly_digest.time or "07:30")
    _scheduler.add_job(_job_weekly_digest, CronTrigger(day_of_week="mon", hour=wh, minute=wm), id="weekly_digest", replace_existing=True)

    # End of week summary — Friday
    ewh, ewm = _parse_time(config.notifications.end_of_week_summary.time or "17:00")
    _scheduler.add_job(_job_end_of_week_summary, CronTrigger(day_of_week="fri", hour=ewh, minute=ewm), id="end_of_week_summary", replace_existing=True)

    # Inbox review — Sunday
    irh, irm = _parse_time(config.notifications.inbox_review.time or "09:00")
    _scheduler.add_job(_job_inbox_review, CronTrigger(day_of_week="sun", hour=irh, minute=irm), id="inbox_review", replace_existing=True)

    # Beginning of month — 1st
    bomh, bomm = _parse_time(config.notifications.beginning_of_month.time or "08:00")
    _scheduler.add_job(_job_beginning_of_month, CronTrigger(day=1, hour=bomh, minute=bomm), id="beginning_of_month", replace_existing=True)

    # End of month — last day
    eomh, eomm = _parse_time(config.notifications.end_of_month.time or "17:00")
    _scheduler.add_job(_job_end_of_month, CronTrigger(day="last", hour=eomh, minute=eomm), id="end_of_month", replace_existing=True)

    _scheduler.start()
    logger.info("Scheduler started with %d jobs", len(_scheduler.get_jobs()))


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def get_jobs_status() -> list[dict]:
    """Return status info for all jobs."""
    if not _scheduler:
        return []
    jobs = []
    for job_id, fn in JOB_MAP.items():
        apsjob = _scheduler.get_job(job_id)
        next_run = None
        if apsjob and apsjob.next_run_time:
            next_run = apsjob.next_run_time.isoformat()

        last = _last_run.get(job_id)

        # Check if job is disabled in config
        disabled = False
        if job_id not in ("scan", "check_due_dates"):
            try:
                n_cfg = config.notifications
                type_cfg = getattr(n_cfg, job_id, None)
                if type_cfg and not type_cfg.enabled:
                    disabled = True
            except Exception:
                pass

        jobs.append({
            "id": job_id,
            "last_run": last.isoformat() if last else None,
            "next_run": next_run,
            "status": "disabled" if disabled else "ok",
        })
    return jobs


# ---------------------------------------------------------------------------
# FastAPI endpoints
# ---------------------------------------------------------------------------

def _auth(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if credentials.credentials != config.api.secret_token:
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials.credentials


@router.post("/jobs/run/{job_name}")
def run_job_now(job_name: str, token: str = Depends(_auth)):
    if job_name not in JOB_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown job '{job_name}'. Valid: {', '.join(JOB_MAP)}")

    def _run():
        try:
            JOB_MAP[job_name]()
        except Exception as e:
            logger.error("Manual job '%s' failed: %s", job_name, e)

    thread = threading.Thread(target=_run, daemon=True, name=f"manual-{job_name}")
    thread.start()
    return {"job": job_name, "status": "triggered"}


@router.get("/jobs")
def get_jobs(token: str = Depends(_auth)):
    return get_jobs_status()
