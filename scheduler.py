"""
scheduler.py
================================================================================
Background Knowledge-Base Synchronizer for SREYAS Assistant.

HOW IT WORKS
------------
This module starts an APScheduler BackgroundScheduler when imported.
The scheduler runs sync_knowledge_base.run_sync() on a configurable
interval (default: every 6 hours).

If the knowledge base changes, rag.py's RAGEngine is rebuilt automatically
WITHOUT restarting Flask.

HOW TO ACTIVATE
---------------
In app.py, add ONE line after the RAGEngine is created:

    import scheduler          # <-- add this line
    scheduler.init(rag_engine)

That is the only change needed in app.py.

If app.py cannot be touched, the scheduler can be started as a
separate process (see bottom of this file).

CROSS-PLATFORM
--------------
Works on Windows, Linux, macOS, Docker, Render, Railway, AWS, Azure,
DigitalOcean — no platform-specific code anywhere.

No absolute paths. No localhost assumptions.
================================================================================
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    print("[scheduler] APScheduler not installed. Using built-in threading scheduler fallback.")

# ---------------------------------------------------------------------------
# Portable project root — works from any working directory
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
log = logging.getLogger("sreyas.scheduler")
if not log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter(
        "%(asctime)s [SCHEDULER] %(levelname)s — %(message)s"
    ))
    log.addHandler(_h)
    log.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Sync interval — configurable via environment variable
# SREYAS_SYNC_HOURS=6   → sync every 6 hours  (default)
# SREYAS_SYNC_HOURS=1   → sync every 1 hour   (for testing)
# ---------------------------------------------------------------------------
_SYNC_HOURS: float = float(os.environ.get("SREYAS_SYNC_HOURS", "24"))

# ---------------------------------------------------------------------------
# Built-in Threading Fallback Scheduler (used if APScheduler is missing)
# ---------------------------------------------------------------------------
class BuiltinThreadScheduler:
    """Simple 24-hour background thread timer when APScheduler is not installed."""

    def __init__(self, job_func, interval_hours: float) -> None:
        self.job_func = job_func
        self.interval_seconds = interval_hours * 3600.0
        self.running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sreyas-sync-timer")
        self._thread.start()

    def _loop(self) -> None:
        while self.running:
            time.sleep(self.interval_seconds)
            if self.running:
                try:
                    self.job_func()
                except Exception as e:
                    log.error("BuiltinThreadScheduler job error: %s", e)

    def shutdown(self, wait: bool = False) -> None:
        self.running = False

    def get_job(self, job_id: str) -> Any:
        return None

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_scheduler: Any = None
_rag_engine: Any = None          # the live RAGEngine instance from app.py
_lock = threading.Lock()          # prevents concurrent syncs
_last_sync_result: dict = {}
_sync_count: int = 0


def _run_sync_job() -> None:
    """
    The job executed on the configured interval.
    """
    global _last_sync_result, _sync_count

    if not _lock.acquire(blocking=False):
        log.warning("Previous sync still running — skipping this interval.")
        return

    try:
        log.info("Starting scheduled knowledge-base sync ...")
        start = time.perf_counter()

        scripts_dir = str(PROJECT_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        try:
            from sync_knowledge_base import run_sync  # type: ignore
        except ImportError as exc:
            log.error(
                "Could not import sync_knowledge_base: %s. "
                "Make sure scripts/sync_knowledge_base.py exists.", exc
            )
            return

        kb_path = PROJECT_ROOT / "data" / "knowledge_base.txt"
        stats   = run_sync(kb_path=kb_path)

        elapsed = round(time.perf_counter() - start, 2)
        _sync_count += 1
        _last_sync_result = {
            **stats,
            "duration_seconds": elapsed,
            "run_number": _sync_count,
            "last_sync_time": datetime.now().strftime("%d %b %Y, %I:%M %p")
        }

        if stats.get("knowledge_base_changed") and _rag_engine is not None:
            log.info("Knowledge base changed — rebuilding RAG index ...")
            try:
                _rag_engine.rebuild_index()
                log.info("RAG index rebuilt successfully.")
            except Exception as rebuild_exc:  # noqa: BLE001
                log.error("RAG rebuild failed: %s", rebuild_exc)
                _last_sync_result["rag_rebuild_error"] = str(rebuild_exc)
        else:
            log.info("Knowledge base unchanged — RAG index kept as-is.")

        log.info(
            "Sync complete in %.2fs | pages: %s | updated: %s | failed: %s",
            elapsed,
            stats.get("pages_scanned", 0),
            stats.get("sections_updated", 0),
            stats.get("failed_pages", 0),
        )

        _record_sync_history(_last_sync_result)

    except Exception as exc:  # noqa: BLE001
        log.error("Unexpected error during sync job: %s", exc)
        _last_sync_result = {
            "success": False,
            "error": str(exc),
            "last_sync_time": datetime.now().strftime("%d %b %Y, %I:%M %p")
        }
        _record_sync_history(_last_sync_result)

    finally:
        _lock.release()


def _record_sync_history(entry: dict) -> None:
    """Append the sync entry to persistent data/sync_history.json (keeps last 20)."""
    try:
        import json
        history_file = PROJECT_ROOT / "data" / "sync_history.json"
        history = []
        if history_file.exists():
            try:
                with open(history_file, "r", encoding="utf-8") as fh:
                    history = json.load(fh)
                if not isinstance(history, list):
                    history = []
            except Exception:
                history = []
        history.insert(0, {
            "timestamp": datetime.now().strftime("%d %b %Y, %I:%M %p"),
            "success": entry.get("success", True),
            "pages_scanned": entry.get("pages_scanned", 0),
            "sections_updated": entry.get("sections_updated", 0),
            "kb_changed": entry.get("knowledge_base_changed", False),
            "rag_rebuilt": entry.get("rag_rebuilt", False),
            "duration": entry.get("duration_seconds", 0),
            "error": entry.get("error", None) or entry.get("rag_rebuild_error", None)
        })
        history = history[:20]
        history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(history_file, "w", encoding="utf-8") as fh:
            json.dump(history, fh, indent=2)
    except Exception as e:
        log.warning("Could not persist sync history: %s", e)


def get_sync_history() -> list:
    """Return the persistent sync history from data/sync_history.json."""
    import json
    history_file = PROJECT_ROOT / "data" / "sync_history.json"
    if not history_file.exists():
        return []
    try:
        with open(history_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def init(rag_engine: Any = None, sync_hours: float = _SYNC_HOURS) -> None:
    """
    Start the background scheduler (24-hour cycle by default).
    """
    global _scheduler, _rag_engine, _SYNC_HOURS

    _SYNC_HOURS = sync_hours
    _rag_engine = rag_engine

    if _scheduler is not None and getattr(_scheduler, "running", False):
        log.warning("Scheduler already running — init() called twice.")
        return

    if HAS_APSCHEDULER:
        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(
            func     = _run_sync_job,
            trigger  = IntervalTrigger(hours=sync_hours),
            id       = "kb_sync",
            name     = "Knowledge Base Sync",
            max_instances = 1,
            replace_existing = True,
            misfire_grace_time = 300,
        )
        _scheduler.start()
        scheduler_type = "APScheduler (BackgroundScheduler)"
    else:
        _scheduler = BuiltinThreadScheduler(_run_sync_job, sync_hours)
        _scheduler.start()
        scheduler_type = "Built-in Threading Scheduler Fallback"

    log.info(
        "Scheduler initialized using %s — running every %.1f hour(s) (24-Hour Cycle). RAG engine %s.",
        scheduler_type,
        sync_hours,
        "connected (auto-rebuild enabled)"
        if rag_engine is not None
        else "NOT connected (sync-only mode)",
    )


def run_now() -> dict:
    """
    Trigger an immediate sync outside the regular schedule.
    """
    log.info("Manual sync triggered via run_now().")
    _run_sync_job()
    return _last_sync_result


def get_status() -> dict:
    """
    Return the current scheduler status.
    """
    running = _scheduler is not None and getattr(_scheduler, "running", False)
    next_run = None
    if running and HAS_APSCHEDULER:
        job = _scheduler.get_job("kb_sync")
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()
    elif running:
        next_run = f"In {_SYNC_HOURS} hours"

    return {
        "scheduler_running":  running,
        "scheduler_type":     "APScheduler" if HAS_APSCHEDULER else "BuiltinThreadScheduler",
        "sync_interval_hours": _SYNC_HOURS,
        "sync_count":          _sync_count,
        "next_run":            next_run,
        "last_sync":           _last_sync_result,
    }


def shutdown() -> None:
    """
    Gracefully stop the scheduler.
    Flask calls this automatically on shutdown via atexit if you register it.
    """
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("Scheduler stopped.")
    _scheduler = None


# ---------------------------------------------------------------------------
# If run as a standalone process (without Flask), the scheduler keeps
# running until interrupted.  Useful for Docker side-car containers or
# dedicated sync servers.
#
# Usage:
#   python scheduler.py
#   SREYAS_SYNC_HOURS=1 python scheduler.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    log.info("Running scheduler as standalone process.")
    log.info("Sync interval: %.1f hour(s).  Press Ctrl+C to stop.", _SYNC_HOURS)

    # In standalone mode there is no live RAGEngine to rebuild, but the
    # KB file is still kept up to date for the next Flask restart.
    init(rag_engine=None, sync_hours=_SYNC_HOURS)

    # Run one immediate sync on startup so the KB is current right away.
    log.info("Running initial sync on startup ...")
    _run_sync_job()

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Interrupted — shutting down.")
        shutdown()