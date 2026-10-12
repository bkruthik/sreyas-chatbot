"""
modules/backup_service.py
================================================================================
Backup Service Module.
Performs timestamped file backups of enquiries.xlsx and feedback.xlsx
prior to exports or major administrative modifications.
================================================================================
"""

import os
import shutil
import logging
from datetime import datetime
from typing import Dict, Optional

import config

logger = logging.getLogger("sreyas_chatbot.backup")


class BackupService:
    """Manages creation and retention of data backups."""

    @classmethod
    def backup_file(cls, source_path: str) -> Optional[str]:
        """
        Create a timestamped backup copy of a specified file.

        Args:
            source_path: Path to the target file to back up.

        Returns:
            The file path of the created backup, or None if failed.
        """
        if not os.path.exists(source_path):
            return None

        try:
            os.makedirs(config.BACKUP_DIR, exist_ok=True)
            filename = os.path.basename(source_path)
            name, ext = os.path.splitext(filename)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_filename = f"{name}_backup_{timestamp}{ext}"
            backup_path = os.path.join(config.BACKUP_DIR, backup_filename)

            shutil.copy2(source_path, backup_path)
            logger.info("Backup created successfully: %s", backup_path)
            return backup_path
        except Exception as e:
            logger.error("Failed to create backup for %s: %s", source_path, e)
            return None

    @classmethod
    def backup_all(cls) -> Dict[str, Optional[str]]:
        """Perform backup of both enquiries and feedback workbooks."""
        return {
            "enquiries": cls.backup_file(config.ENQUIRY_FILE),
            "feedback": cls.backup_file(config.FEEDBACK_FILE),
        }
