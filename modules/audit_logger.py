"""
modules/audit_logger.py
================================================================================
Audit Logging Module for Admin Actions.
Records all login attempts, status changes, lead deletions, and data exports.
================================================================================
"""

import logging
import os
from datetime import datetime

import config

logger = logging.getLogger("sreyas_chatbot.audit")
logger.setLevel(logging.INFO)

# Ensure log directory exists
os.makedirs(config.LOG_FOLDER, exist_ok=True)

# Add FileHandler for audit.log if not present
_file_handler_exists = any(
    isinstance(h, logging.FileHandler) and h.baseFilename == os.path.abspath(config.AUDIT_LOG_FILE)
    for h in logger.handlers
)

if not _file_handler_exists:
    try:
        fh = logging.FileHandler(config.AUDIT_LOG_FILE, encoding="utf-8")
        fh.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(fh)
    except Exception as e:
        print(f"Warning: Could not set up audit log file: {e}")


class AuditLogger:
    """Logs all administrative actions with timestamp, user, IP, and action details."""

    @classmethod
    def log_action(cls, username: str, action: str, details: str = "", ip_address: str = "127.0.0.1") -> None:
        """
        Record an administrative action.

        Args:
            username: Username of the logged-in administrator.
            action: Action tag (e.g. LOGIN, LOGOUT, UPDATE_STATUS, DELETE, EXPORT).
            details: Extra details (e.g. Reference ID, export format).
            ip_address: Client IP address.
        """
        log_msg = f"[{ip_address}] USER:{username} ACTION:{action} | {details}"
        logger.info(log_msg)

    @classmethod
    def get_recent_logs(cls, limit: int = 50) -> list:
        """Read and return recent audit logs as structured dicts for the admin UI."""
        import re as _re
        if not os.path.exists(config.AUDIT_LOG_FILE):
            return []
        try:
            with open(config.AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            raw_lines = [line.strip() for line in lines[-limit:] if line.strip()]
        except Exception:
            return []

        # Parse log lines of format:
        # [2026-08-16 19:09:27] [127.0.0.1] USER:admin ACTION:LOGIN_SUCCESS | details
        _PATTERN = _re.compile(
            r"\[(?P<timestamp>[^\]]+)\]\s+\[(?P<ip>[^\]]+)\]\s+USER:(?P<user>\S+)\s+ACTION:(?P<action>\S+)\s*\|\s*(?P<details>.*)"
        )
        result = []
        for line in raw_lines:
            m = _PATTERN.match(line)
            if m:
                result.append({
                    "timestamp": m.group("timestamp"),
                    "ip": m.group("ip"),
                    "admin_user": m.group("user"),
                    "action": m.group("action"),
                    "details": m.group("details").strip(),
                    "raw": line,
                })
            else:
                # Fallback: return raw line with safe defaults so UI never shows "undefined"
                result.append({
                    "timestamp": "",
                    "ip": "",
                    "admin_user": "system",
                    "action": "LOG",
                    "details": line,
                    "raw": line,
                })
        return result

