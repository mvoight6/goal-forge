"""
Goal Forge — Entry point.
Starts APScheduler + FastAPI + serves PWA static files.
"""
import os
import sys
from pathlib import Path

# Allow running from project root without installing
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from goalforge.logs_api import setup_logging
setup_logging()  # Must be first so all subsequent imports get configured loggers

import logging
from goalforge.scheduler import start_scheduler, stop_scheduler
from goalforge import capture, interactive, scheduler, config_api, logs_api, daily_api, lists_api, categories_api

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Goal Forge", version="1.0.0")

# Register routers
app.include_router(capture.router)
app.include_router(interactive.router)
app.include_router(scheduler.router)
app.include_router(config_api.router)
app.include_router(logs_api.router)
app.include_router(daily_api.router)
app.include_router(lists_api.router)
app.include_router(categories_api.router)

# Serve PWA static files
PWA_DIR = Path(__file__).parent / "pwa"

@app.get("/")
def serve_index():
    return FileResponse(PWA_DIR / "index.html")

@app.get("/style.css")
def serve_css():
    return FileResponse(PWA_DIR / "style.css")

@app.get("/app.js")
def serve_js():
    return FileResponse(PWA_DIR / "app.js")

@app.get("/manifest.json")
def serve_manifest():
    return FileResponse(PWA_DIR / "manifest.json")

@app.get("/service-worker.js")
def serve_sw():
    return FileResponse(PWA_DIR / "service-worker.js")

# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    logger.info("Goal Forge starting up…")
    start_scheduler()


@app.on_event("shutdown")
async def on_shutdown():
    stop_scheduler()
    logger.info("Goal Forge shut down.")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    from goalforge.config import config

    host = config.api.host or "0.0.0.0"
    port = int(config.api.port or 8742)

    # CLI commands: python main.py plan GF-0001
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "plan" and len(sys.argv) > 2:
            goal_id = sys.argv[2]
            from goalforge.planner import plan_goal
            children = plan_goal(goal_id)
            print(f"Created {len(children)} child goals:")
            for c in children:
                print(f"  [{c['id']}] {c['name']}")
            sys.exit(0)
        else:
            print(f"Unknown command: {cmd}")
            print("Usage: python main.py plan <goal_id>")
            sys.exit(1)

    logger.info("Starting server on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_config=None)
