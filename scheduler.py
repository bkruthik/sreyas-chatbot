"""
scheduler.py
================================================================================
Automatic Midnight Knowledge-Base & Website Scraper Scheduler — SREYAS Assistant

SCHEDULE CONFIGURATION:
- Time: 12:00 AM (Midnight) every day
- Timezone: Asia/Kolkata (IST)
- Engine: APScheduler BackgroundScheduler with CronTrigger
- Concurrency Protection: Thread lock + process file lock to prevent overlapping runs
- Single Worker Guard: Only one worker instance executes the scheduler in multi-worker setups
- Hot Reload: Automatically rebuilds RAGEngine index on changes without restarting Flask
================================================================================
"""

from __future__ import annotations

import atexit
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import pytz
    KOLKATA_TZ = pytz.timezone("Asia/Kolkata")
except ImportError:
    KOLKATA_TZ = None

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    print("[scheduler] APScheduler not installed. Using built-in threading scheduler fallback.")

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
LOCK_FILE = DATA_DIR / ".scheduler_process.lock"

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
# Timezone helper
# ---------------------------------------------------------------------------
def now_ist() -> datetime:
    """Current time in Asia/Kolkata."""
    if KOLKATA_TZ:
        return datetime.now(KOLKATA_TZ)
    return datetime.now()


def now_ist_str() -> str:
    """Formatted IST time string."""
    return now_ist().strftime("%d %b %Y, %I:%M %p IST")


# ---------------------------------------------------------------------------
# Built-in Midnight Fallback Scheduler (used if APScheduler is missing)
# ---------------------------------------------------------------------------
class BuiltinMidnightScheduler:
    """
    Fallback thread timer that calculates seconds until 12:00 AM Asia/Kolkata
    and wakes up precisely at midnight IST.
    """

    def __init__(self, job_func, target_hour: int = 0, target_minute: int = 0) -> None:
        self.job_func = job_func
        self.target_hour = target_hour
        self.target_minute = target_minute
        self.running = False
        self._thread: Optional[threading.Thread] = None

    def _seconds_until_midnight_ist(self) -> float:
        current = now_ist()
        # Next target time
        target = current.replace(hour=self.target_hour, minute=self.target_minute, second=0, microsecond=0)
        if target <= current:
            target += timedelta(days=1)
        diff = (target - current).total_seconds()
        return max(diff, 1.0)

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sreyas-midnight-timer")
        self._thread.start()

    def _loop(self) -> None:
        while self.running:
            sleep_sec = self._seconds_until_midnight_ist()
            log.info("Builtin timer sleeping %.1f seconds until next 12:00 AM IST cycle...", sleep_sec)
            
            # Sleep in intervals so shutdown is responsive
            slept = 0.0
            while self.running and slept < sleep_sec:
                step = min(30.0, sleep_sec - slept)
                time.sleep(step)
                slept += step

            if self.running:
                try:
                    self.job_func()
                except Exception as e:
                    log.error("BuiltinMidnightScheduler job error: %s", e)

    def shutdown(self, wait: bool = False) -> None:
        self.running = False

    def get_job(self, job_id: str) -> Any:
        return None


# ---------------------------------------------------------------------------
# Module-level state & single-process lock
# ---------------------------------------------------------------------------
_scheduler: Any = None
_rag_engine: Any = None
_sync_lock = threading.Lock()     # thread-level lock against concurrent jobs
_last_sync_result: dict = {}
_sync_count: int = 0
_is_primary_worker: bool = True


_lock_fh: Any = None


def _acquire_process_lock() -> bool:
    """
    Ensure only one worker process runs the scheduler in multi-worker
    environments (e.g. Gunicorn, Docker multi-core, Flask reloader).
    Uses OS kernel-level non-blocking file locking.
    """
    global _lock_fh
    if os.environ.get("WERKZEUG_RUN_MAIN") == "false":
        return False

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _lock_fh = open(LOCK_FILE, "a+", encoding="utf-8")
        if sys.platform == "win32":
            import msvcrt
            _lock_fh.seek(0)
            msvcrt.locking(_lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        _lock_fh.seek(0)
        _lock_fh.truncate()
        _lock_fh.write(str(os.getpid()))
        _lock_fh.flush()
        return True
    except (IOError, OSError, BlockingIOError):
        log.info("Another process holds scheduler lock — running in secondary mode.")
        if _lock_fh:
            try:
                _lock_fh.close()
            except Exception:
                pass
            _lock_fh = None
        return False
    except Exception as e:
        log.warning("Lock check error: %s. Defaulting to active.", e)
        return True


def _release_process_lock() -> None:
    global _lock_fh
    if _lock_fh:
        try:
            if sys.platform == "win32":
                import msvcrt
                _lock_fh.seek(0)
                msvcrt.locking(_lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            _lock_fh.close()
        except Exception:
            pass
        _lock_fh = None
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


atexit.register(_release_process_lock)


# ---------------------------------------------------------------------------
# The Scheduled Sync Job
# ---------------------------------------------------------------------------
def _run_sync_job() -> None:
    """
    Executes the automatic scraping and knowledge-base synchronization.
    Runs at 12:00 AM Asia/Kolkata.
    """
    global _last_sync_result, _sync_count

    if not _sync_lock.acquire(blocking=False):
        log.warning("Previous sync still running — skipping this trigger.")
        return

    try:
        log.info("=" * 65)
        log.info("STARTING AUTOMATIC MIDNIGHT SYNC (Asia/Kolkata: %s)", now_ist_str())
        log.info("=" * 65)
        start = time.perf_counter()

        # 1. Run Website Scraper & Feature Extractor
        scraper_stats: Dict[str, Any] = {}
        try:
            from modules.scraper import execute_website_sync
            scraper_stats = execute_website_sync(rag_engine=_rag_engine)
            log.info("Website Scraper finished: %s pages scanned, KB changed: %s",
                     scraper_stats.get("pages_scanned", 0),
                     scraper_stats.get("knowledge_base_changed", False))
        except Exception as scraper_err:
            log.error("Scraper module error: %s", scraper_err)
            scraper_stats = {"success": False, "error": str(scraper_err)}

        # 2. Run Config-based Section Pattern Synchronizer
        sync_stats: Dict[str, Any] = {}
        try:
            scripts_dir = str(PROJECT_ROOT / "scripts")
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            from sync_knowledge_base import run_sync
            kb_path = PROJECT_ROOT / "data" / "knowledge_base.txt"
            sync_stats = run_sync(kb_path=kb_path, rag_engine=_rag_engine)
            log.info("KB Pattern Synchronizer finished: %s sections updated, KB changed: %s",
                     sync_stats.get("sections_updated", 0),
                     sync_stats.get("knowledge_base_changed", False))
        except Exception as sync_err:
            log.error("Sync script error: %s", sync_err)
            sync_stats = {"success": False, "error": str(sync_err)}

        # Combine metrics
        elapsed = round(time.perf_counter() - start, 2)
        _sync_count += 1

        kb_changed = bool(scraper_stats.get("knowledge_base_changed") or sync_stats.get("knowledge_base_changed"))
        total_pages = scraper_stats.get("pages_scanned", 0) + sync_stats.get("pages_scanned", 0)
        total_updated = scraper_stats.get("sections_updated", 0) + sync_stats.get("sections_updated", 0)
        failed_count = scraper_stats.get("failed_pages", 0) + sync_stats.get("failed_pages", 0)

        # Trigger RAG index rebuild if KB changed and RAG engine connected
        rag_rebuilt = False
        if kb_changed and _rag_engine is not None:
            try:
                if hasattr(_rag_engine, "rebuild_index"):
                    _rag_engine.rebuild_index()
                elif hasattr(_rag_engine, "safe_rebuild_index"):
                    _rag_engine.safe_rebuild_index()
                elif hasattr(_rag_engine, "rebuild"):
                    _rag_engine.rebuild()
                rag_rebuilt = True
                log.info("RAG index rebuilt successfully with updated content.")
            except Exception as rebuild_exc:
                log.error("RAG rebuild failed: %s", rebuild_exc)

        _last_sync_result = {
            "success": True,
            "duration_seconds": elapsed,
            "run_number": _sync_count,
            "last_sync_time": now_ist_str(),
            "timestamp": now_ist().isoformat(),
            "timezone": "Asia/Kolkata",
            "pages_scanned": total_pages,
            "sections_updated": total_updated,
            "failed_pages": failed_count,
            "knowledge_base_changed": kb_changed,
            "rag_rebuilt": rag_rebuilt,
            "scraper_details": scraper_stats,
            "pattern_sync_details": sync_stats,
        }

        log.info(
            "Midnight Sync complete in %.2fs | pages: %s | updated: %s | failed: %s | kb_changed: %s",
            elapsed, total_pages, total_updated, failed_count, kb_changed
        )

        _record_sync_history(_last_sync_result)

    except Exception as exc:
        log.error("Unexpected error during scheduled sync job: %s", exc)
        _last_sync_result = {
            "success": False,
            "error": str(exc),
            "last_sync_time": now_ist_str(),
            "timezone": "Asia/Kolkata",
        }
        _record_sync_history(_last_sync_result)

    finally:
        _sync_lock.release()


def _record_sync_history(entry: dict) -> None:
    """Append sync entry to persistent data/sync_history.json (keeps last 25)."""
    try:
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
            "timestamp": entry.get("last_sync_time", now_ist_str()),
            "iso_time": entry.get("timestamp", now_ist().isoformat()),
            "timezone": "Asia/Kolkata",
            "success": entry.get("success", True),
            "pages_scanned": entry.get("pages_scanned", 0),
            "sections_updated": entry.get("sections_updated", 0),
            "kb_changed": entry.get("knowledge_base_changed", False),
            "rag_rebuilt": entry.get("rag_rebuilt", False),
            "duration": entry.get("duration_seconds", 0),
            "error": entry.get("error", None),
        })
        history = history[:25]
        history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(history_file, "w", encoding="utf-8") as fh:
            json.dump(history, fh, indent=2)
    except Exception as e:
        log.warning("Could not persist sync history: %s", e)


def get_sync_history() -> list:
    """Return persistent sync history from data/sync_history.json."""
    history_file = PROJECT_ROOT / "data" / "sync_history.json"
    if not history_file.exists():
        return []
    try:
        with open(history_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Public Lifecycle API
# ---------------------------------------------------------------------------
def init(
    rag_engine: Any = None,
    hour: int = 0,
    minute: int = 0,
    interval_hours: Optional[float] = None,
) -> None:
    """
    Initialize and start the background scheduler.
    Default: Daily at 12:00 AM midnight in Asia/Kolkata timezone.
    """
    global _scheduler, _rag_engine, _is_primary_worker

    _rag_engine = rag_engine

    if _scheduler is not None and getattr(_scheduler, "running", False):
        log.warning("Scheduler already running — ignoring duplicate init() call.")
        return

    _is_primary_worker = _acquire_process_lock()
    if not _is_primary_worker:
        log.info("Scheduler skipped in secondary worker process (single scheduler active).")
        return

    if HAS_APSCHEDULER:
        _scheduler = BackgroundScheduler(daemon=True)
        if interval_hours is not None:
            trigger = IntervalTrigger(hours=interval_hours)
            schedule_desc = f"Every {interval_hours} hour(s)"
        else:
            trigger = CronTrigger(
                hour=hour,
                minute=minute,
                timezone=KOLKATA_TZ or "Asia/Kolkata",
            )
            schedule_desc = f"Daily at {hour:02d}:{minute:02d} AM (Midnight) Asia/Kolkata"

        _scheduler.add_job(
            func=_run_sync_job,
            trigger=trigger,
            id="kb_sync",
            name="Sreyas Midnight Website & KB Sync",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
            misfire_grace_time=3600,
        )
        _scheduler.start()
        scheduler_type = "APScheduler (BackgroundScheduler)"
    else:
        _scheduler = BuiltinMidnightScheduler(_run_sync_job, target_hour=hour, target_minute=minute)
        _scheduler.start()
        scheduler_type = "Built-in Midnight Threading Fallback"
        schedule_desc = f"Daily at {hour:02d}:{minute:02d} AM Asia/Kolkata"

    log.info(
        "Scheduler initialized using %s — Schedule: %s. RAG engine %s.",
        scheduler_type,
        schedule_desc,
        "connected (auto-rebuild enabled)" if rag_engine is not None else "NOT connected",
    )


def run_now() -> dict:
    """Trigger an immediate sync outside the regular schedule."""
    log.info("Manual sync triggered via run_now().")
    _run_sync_job()
    return _last_sync_result


def get_status() -> dict:
    """Return scheduler health and timing status."""
    running = _scheduler is not None and getattr(_scheduler, "running", False)
    next_run = None

    if running and HAS_APSCHEDULER:
        job = _scheduler.get_job("kb_sync")
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()
    elif running:
        next_run = "Tomorrow at 12:00 AM Asia/Kolkata"

    return {
        "scheduler_running": running,
        "scheduler_type": "APScheduler" if HAS_APSCHEDULER else "BuiltinMidnightScheduler",
        "schedule": "12:00 AM (Midnight) Daily",
        "timezone": "Asia/Kolkata",
        "sync_count": _sync_count,
        "next_run": next_run,
        "is_primary_worker": _is_primary_worker,
        "last_sync": _last_sync_result,
    }


def shutdown() -> None:
    """Gracefully stop the scheduler."""
    global _scheduler
    if _scheduler and getattr(_scheduler, "running", False):
        _scheduler.shutdown(wait=False)
        log.info("Scheduler stopped.")
    _scheduler = None
    _release_process_lock()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log.info("Running standalone scheduler test at 12:00 AM Asia/Kolkata schedule.")
    init(rag_engine=None, hour=0, minute=0)
    print("Scheduler Status:", json.dumps(get_status(), indent=2, default=str))
    print("Executing immediate run_now() verification...")
    res = run_now()
    print("Run result:", json.dumps(res, indent=2, default=str))
    shutdown()