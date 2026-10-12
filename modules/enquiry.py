"""
modules/enquiry.py
================================================================================
Enquiry Management Module for the Sreyas Institute of Engineering and
Technology (SIET) College Chatbot.

This module manages every student / parent enquiry raised through the
chatbot (typically triggered by `app.py` whenever `rag.py` returns
`show_enquiry=True`, meaning the chatbot could not confidently answer the
question and the person should be followed up with by college staff).

No database is used. All enquiries are persisted directly into an Excel
workbook (`enquiries.xlsx`) via `openpyxl`, so college staff can open the
file directly in Microsoft Excel / LibreOffice / Google Sheets without any
extra tooling.

Responsibilities:
    1.  Auto-create `enquiries.xlsx` (with the correct sheet + header row)
        the first time it is needed.
    2.  Generate unique, sequential, never-duplicated enquiry IDs
        (ENQ0001, ENQ0002, ...).
    3.  Validate submitted enquiry data (name, phone, email, question).
    4.  Detect and silently reject duplicate submissions (same email + same
        question within a 24-hour window).
    5.  Save new enquiries (`save_enquiry`).
    6.  Search enquiries by ID / name / email / phone / category / status
        (`search`).
    7.  Filter enquiries by date / category / mode / status (`filter`).
    8.  Update enquiry status, assignment and remarks (`update_status`).
    9.  Soft-delete enquiries by marking them "Closed" instead of removing
        rows (`close_enquiry`).
    10. Produce summary statistics for the admin dashboard (`get_summary`).
    11. Export the current enquiry list to CSV without touching/losing the
        underlying Excel workbook (`export_csv`).
    12. Be thread-safe (Flask can serve multiple simultaneous requests) via
        a single `threading.Lock` guarding every read-modify-write cycle
        against the workbook file.
    13. Never crash the chatbot: missing files, corrupt workbooks, invalid
        values and OS-level permission errors are all handled gracefully
        and reported back as structured error responses / log entries
        instead of raised exceptions bubbling into `app.py`.

Author: AI Engineering Team - SIET Chatbot Project
================================================================================
"""

from __future__ import annotations

import csv
import logging
import os
import re
import shutil
import threading
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.worksheet import Worksheet

# ==============================================================================
# LOGGING
# ------------------------------------------------------------------------------
# A dedicated module-level logger. `app.py` / `logger.py` can attach their
# own handlers; this module never calls `print()` and never lets a logging
# failure interrupt enquiry processing.
# ==============================================================================
logger = logging.getLogger("sreyas_chatbot.enquiry")
if not logger.handlers:
    # Provide a sane default handler only if the host application hasn't
    # already configured logging, so standalone testing still shows output.
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] enquiry: %(message)s")
    )
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


# ==============================================================================
# CONSTANTS
# ==============================================================================

SHEET_NAME: str = "Enquiries"

COLUMNS: List[str] = [
    "Enquiry ID",
    "Date",
    "Time",
    "Name",
    "Phone",
    "Email",
    "Mode",
    "Category",
    "Confidence",
    "Question",
    "Status",
    "Assigned To",
    "Remarks",
    "Parent Email",
]

VALID_STATUSES: List[str] = ["Pending", "In Progress", "Reviewed", "Closed"]

DUPLICATE_WINDOW_HOURS: int = 24

NAME_MIN_LENGTH: int = 2
NAME_MAX_LENGTH: int = 100

QUESTION_MIN_LENGTH: int = 5
QUESTION_MAX_LENGTH: int = 1000

_PHONE_PATTERN = re.compile(r"^[0-9]{10}$")
_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
_ENQUIRY_ID_PATTERN = re.compile(r"^ENQ(\d{4,})$")

_DATE_FORMAT = "%Y-%m-%d"
_TIME_FORMAT = "%H:%M:%S"

_ADMISSION_ID_PATTERN = re.compile(r"^SIET(\d{4,})$")

ADMISSION_SHEET_NAME: str = "Admission_Enquiries"

ADMISSION_COLUMNS: List[str] = [
    "Reference ID",
    "Date",
    "Time",
    "Student Name",
    "Student Phone",
    "Parent Phone",
    "Email",
    "City",
    "Interested Course",
    "Qualification",
    "Percentage",
    "Entrance Exam Rank",
    "Notes",
    "Status",
    "Assigned To",
    "Remarks",
]


# ==============================================================================
# RESULT DATACLASSES
# ------------------------------------------------------------------------------
# Lightweight, serializable result objects used internally. Public methods
# always return plain dictionaries (via `.to_dict()`), keeping the public
# contract JSON-friendly for Flask.
# ==============================================================================

@dataclass
class OperationResult:
    """A generic success/failure result with a human-readable message."""

    success: bool
    message: str
    data: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert this result into a plain dictionary for JSON responses."""
        payload: Dict[str, Any] = {"success": self.success, "message": self.message}
        if self.data is not None:
            payload.update(self.data if isinstance(self.data, dict) else {"data": self.data})
        return payload


# ==============================================================================
# ENQUIRY MANAGER
# ==============================================================================

class EnquiryManager:
    """
    Manages the full lifecycle of student / parent enquiries, persisted to
    an Excel workbook via `openpyxl`.

    A single `EnquiryManager` instance is intended to be created once (e.g.
    at Flask app startup) and reused for every request; all public methods
    are thread-safe.
    """

    def __init__(self, excel_path: str = os.path.join("data", "enquiries.xlsx")) -> None:
        """
        Args:
            excel_path: Path to the enquiries Excel workbook. Created
                automatically (including parent directories) if it does
                not already exist.
        """
        self.excel_path: str = excel_path
        self._lock = threading.Lock()
        self._ensure_workbook_exists()

    # --------------------------------------------------------------------
    # WORKBOOK LIFECYCLE
    # --------------------------------------------------------------------

    def _ensure_workbook_exists(self) -> None:
        """
        Create the Excel workbook (with header row + basic formatting) if
        it does not already exist. Safe to call multiple times.
        """
        try:
            directory = os.path.dirname(self.excel_path)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)

            if not os.path.isfile(self.excel_path):
                self._create_new_workbook()
                logger.info("Created new enquiries workbook at '%s'.", self.excel_path)
        except PermissionError as error:
            logger.error("Permission denied while creating workbook: %s", error)
        except OSError as error:
            logger.error("OS error while creating workbook: %s", error)

    def _create_new_workbook(self) -> None:
        """Build a brand-new workbook with the correct sheet name, header
        row, bold header styling and sensible column widths."""
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = SHEET_NAME
        worksheet.append(COLUMNS)

        try:
            for cell in worksheet[1]:
                cell.font = Font(bold=True)
            column_widths = [12, 12, 10, 22, 14, 28, 10, 16, 12, 50, 12, 16, 30]
            for index, width in enumerate(column_widths, start=1):
                worksheet.column_dimensions[worksheet.cell(row=1, column=index).column_letter].width = width
        except Exception as style_error:  # noqa: BLE001 - styling must never break saving.
            logger.warning("Could not apply header styling: %s", style_error)

        workbook.save(self.excel_path)

    def _backup_corrupt_file(self) -> str:
        """
        Rename an unreadable/corrupt workbook out of the way (with a
        timestamped suffix) so a fresh workbook can be created without
        losing whatever bytes were on disk.

        Returns:
            The path the corrupt file was moved to.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{self.excel_path}.corrupt_{timestamp}.bak"
        try:
            shutil.move(self.excel_path, backup_path)
            logger.error(
                "Enquiries workbook was corrupt/unreadable. Backed up to '%s' "
                "and a fresh workbook will be created.",
                backup_path,
            )
        except OSError as error:
            logger.error("Failed to back up corrupt workbook: %s", error)
        return backup_path

    def _load_workbook_safely(self) -> Optional[Workbook]:
        """
        Load the workbook from disk, handling missing files and corrupt
        files gracefully. Never raises -- returns None on unrecoverable
        failure so callers can degrade gracefully instead of crashing.

        Returns:
            The loaded `Workbook`, or None if it could not be loaded even
            after attempting recovery.
        """
        if not os.path.isfile(self.excel_path):
            self._ensure_workbook_exists()

        try:
            return load_workbook(self.excel_path)
        except (InvalidFileException, KeyError, OSError, zipfile.BadZipFile) as error:
            logger.error("Failed to load enquiries workbook: %s", error)
            self._backup_corrupt_file()
            try:
                self._create_new_workbook()
                return load_workbook(self.excel_path)
            except Exception as recovery_error:  # noqa: BLE001
                logger.critical(
                    "Could not recover enquiries workbook after corruption: %s",
                    recovery_error,
                )
                return None
        except Exception as error:  # noqa: BLE001 - absolute last-resort safety net.
            logger.error("Unexpected error loading enquiries workbook: %s", error)
            return None

    def _get_worksheet(self, workbook: Workbook) -> Worksheet:
        """
        Return the Enquiries worksheet from a workbook, creating it (with
        headers) if a workbook exists but the expected sheet is missing
        for some reason (e.g. someone renamed the sheet manually).

        Args:
            workbook: An already-loaded `Workbook`.

        Returns:
            The `Enquiries` worksheet.
        """
        if SHEET_NAME in workbook.sheetnames:
            return workbook[SHEET_NAME]

        worksheet = workbook.create_sheet(SHEET_NAME)
        worksheet.append(COLUMNS)
        return worksheet

    def _save_workbook_safely(self, workbook: Workbook) -> bool:
        """
        Save the workbook to disk, retrying briefly on transient
        permission errors (e.g. the file is momentarily open in Excel on
        Windows) before giving up gracefully.

        Args:
            workbook: The `Workbook` to persist.

        Returns:
            True if the save succeeded, False otherwise.
        """
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                workbook.save(self.excel_path)
                return True
            except PermissionError as error:
                logger.warning(
                    "Permission denied saving workbook (attempt %d/%d): %s. "
                    "The file may be open in another program.",
                    attempt, attempts, error,
                )
            except OSError as error:
                logger.error("OS error saving workbook (attempt %d/%d): %s", attempt, attempts, error)
        logger.critical("Failed to save enquiries workbook after %d attempts.", attempts)
        return False

    # --------------------------------------------------------------------
    # ENQUIRY ID GENERATION
    # --------------------------------------------------------------------

    def _generate_next_enquiry_id(self, worksheet: Worksheet) -> str:
        """
        Scan all existing enquiry IDs in the worksheet and generate the
        next sequential ID (ENQ0001, ENQ0002, ...), guaranteeing no
        duplicates even if rows were manually edited or deleted out of
        order.

        Args:
            worksheet: The Enquiries worksheet.

        Returns:
            The next available enquiry ID string.
        """
        highest_sequence = 0
        for row in worksheet.iter_rows(min_row=2, values_only=True):
            if not row or row[0] is None:
                continue
            match = _ENQUIRY_ID_PATTERN.match(str(row[0]).strip())
            if match:
                sequence_number = int(match.group(1))
                highest_sequence = max(highest_sequence, sequence_number)

        return f"ENQ{highest_sequence + 1:04d}"

    # --------------------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------------------

    @staticmethod
    def validate_name(name: str) -> Optional[str]:
        """
        Validate a submitted name.

        Args:
            name: The name to validate.

        Returns:
            An error message string if invalid, otherwise None.
        """
        if not name or not name.strip():
            return "Name is required."
        cleaned = name.strip()
        if len(cleaned) < NAME_MIN_LENGTH:
            return f"Name must be at least {NAME_MIN_LENGTH} characters long."
        if len(cleaned) > NAME_MAX_LENGTH:
            return f"Name must not exceed {NAME_MAX_LENGTH} characters."
        return None

    @staticmethod
    def validate_phone(phone: str) -> Optional[str]:
        """
        Validate a submitted phone number. Must be exactly 10 digits, with
        no letters, spaces, or symbols (country codes should be stripped
        by the caller/frontend before submission).

        Args:
            phone: The phone number to validate.

        Returns:
            An error message string if invalid, otherwise None.
        """
        if not phone or not phone.strip():
            return "Phone number is required."
        cleaned = phone.strip()
        if not _PHONE_PATTERN.match(cleaned):
            return "Phone number must be exactly 10 digits."
        return None

    @staticmethod
    def validate_email(email: str) -> Optional[str]:
        """
        Validate a submitted email address using a standard email regex.

        Args:
            email: The email address to validate.

        Returns:
            An error message string if invalid, otherwise None.
        """
        if not email or not email.strip():
            return "Email address is required."
        cleaned = email.strip()
        if not _EMAIL_PATTERN.match(cleaned):
            return "Please provide a valid email address."
        return None

    @staticmethod
    def validate_question(question: str) -> Optional[str]:
        """
        Validate a submitted question/enquiry text.

        Args:
            question: The question text to validate.

        Returns:
            An error message string if invalid, otherwise None.
        """
        if not question or not question.strip():
            return "Question is required."
        cleaned = question.strip()
        if len(cleaned) < QUESTION_MIN_LENGTH:
            return f"Question must be at least {QUESTION_MIN_LENGTH} characters long."
        if len(cleaned) > QUESTION_MAX_LENGTH:
            return f"Question must not exceed {QUESTION_MAX_LENGTH} characters."
        return None

    def _validate_enquiry_fields(
        self, name: str, phone: str, email: str, question: str
    ) -> List[str]:
        """
        Run all field validators and collect every error found (rather
        than stopping at the first one), so the frontend can display all
        problems to the user at once.

        Returns:
            A list of human-readable error messages (empty if valid).
        """
        errors: List[str] = []
        for validator, value in (
            (self.validate_name, name),
            (self.validate_phone, phone),
            (self.validate_email, email),
            (self.validate_question, question),
        ):
            error = validator(value)
            if error:
                errors.append(error)
        return errors

    # --------------------------------------------------------------------
    # DUPLICATE DETECTION
    # --------------------------------------------------------------------

    def _is_duplicate(self, worksheet: Worksheet, email: str, question: str) -> bool:
        """
        Check whether an enquiry with the same email AND same question
        text was already submitted within the last `DUPLICATE_WINDOW_HOURS`
        hours.

        Args:
            worksheet: The Enquiries worksheet.
            email: The submitted email address.
            question: The submitted question text.

        Returns:
            True if a duplicate was found, False otherwise.
        """
        normalized_email = email.strip().lower()
        normalized_question = " ".join(question.strip().lower().split())
        cutoff = datetime.now() - timedelta(hours=DUPLICATE_WINDOW_HOURS)

        for row in worksheet.iter_rows(min_row=2, values_only=True):
            if not row or len(row) < len(COLUMNS):
                continue

            row_email = str(row[5] or "").strip().lower()
            row_question = " ".join(str(row[9] or "").strip().lower().split())

            if row_email != normalized_email or row_question != normalized_question:
                continue

            row_datetime = self._parse_row_datetime(row[1], row[2])
            if row_datetime is not None and row_datetime >= cutoff:
                return True

        return False

    @staticmethod
    def _parse_row_datetime(date_value: Any, time_value: Any) -> Optional[datetime]:
        """
        Parse a row's Date + Time cell values (which may be strings or
        native `datetime`/`date`/`time` objects depending on how Excel
        stored them) back into a single `datetime` for comparisons.

        Args:
            date_value: The raw "Date" cell value.
            time_value: The raw "Time" cell value.

        Returns:
            A combined `datetime`, or None if parsing failed.
        """
        try:
            if isinstance(date_value, datetime):
                date_part = date_value.date()
            elif hasattr(date_value, "year") and hasattr(date_value, "month"):
                date_part = date_value  # a `date` object
            else:
                date_part = datetime.strptime(str(date_value).strip(), _DATE_FORMAT).date()

            if isinstance(time_value, datetime):
                time_part = time_value.time()
            elif hasattr(time_value, "hour") and hasattr(time_value, "minute"):
                time_part = time_value  # a `time` object
            else:
                time_part = datetime.strptime(str(time_value).strip(), _TIME_FORMAT).time()

            return datetime.combine(date_part, time_part)
        except (ValueError, TypeError):
            return None

    # --------------------------------------------------------------------
    # SAVE ENQUIRY
    # --------------------------------------------------------------------

    def save_enquiry(
        self,
        name: str,
        phone: str,
        email: str,
        mode: str,
        category: str,
        confidence: float,
        question: str,
        parent_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate and persist a new enquiry to the Excel workbook.

        Automatically fills in Date, Time, Status ("Pending"), and leaves
        "Assigned To" / "Remarks" empty for staff to fill in later.

        Args:
            name: Enquirer's full name.
            phone: Enquirer's 10-digit phone number.
            email: Enquirer's email address.
            mode: How the enquiry was raised (e.g. "Chatbot", "WhatsApp",
                "Phone", "Walk-in").
            category: The chatbot-detected category (e.g. "ADMISSIONS").
            confidence: The chatbot's confidence score for the original
                question (0.0 - 1.0).
            question: The original question that could not be confidently
                answered.

        Returns:
            {
                "success": bool,
                "message": str,
                "enquiry_id": Optional[str],
            }
        """
        validation_errors = self._validate_enquiry_fields(name, phone, email, question)
        if validation_errors:
            return {
                "success": False,
                "message": " ".join(validation_errors),
                "enquiry_id": None,
            }

        with self._lock:
            workbook = self._load_workbook_safely()
            if workbook is None:
                return {
                    "success": False,
                    "message": "Could not access the enquiries storage file. Please try again shortly.",
                    "enquiry_id": None,
                }

            try:
                worksheet = self._get_worksheet(workbook)

                if self._is_duplicate(worksheet, email, question):
                    logger.info(
                        "Duplicate enquiry rejected for email='%s'.", email.strip().lower()
                    )
                    return {
                        "success": False,
                        "message": "Duplicate enquiry already exists.",
                        "enquiry_id": None,
                    }

                enquiry_id = self._generate_next_enquiry_id(worksheet)
                now = datetime.now()

                row = [
                    enquiry_id,
                    now.strftime(_DATE_FORMAT),
                    now.strftime(_TIME_FORMAT),
                    name.strip(),
                    phone.strip(),
                    email.strip(),
                    (mode or "Chatbot").strip(),
                    (category or "GENERAL").strip().upper(),
                    round(float(confidence or 0.0), 4),
                    question.strip(),
                    "Pending",
                    "",
                    "",
                    parent_email.strip() if parent_email else "",
                ]
                worksheet.append(row)

                if not self._save_workbook_safely(workbook):
                    return {
                        "success": False,
                        "message": "Enquiry could not be saved due to a file access error. Please try again.",
                        "enquiry_id": None,
                    }

                logger.info("Saved new enquiry '%s' for '%s'.", enquiry_id, email.strip())
                return {
                    "success": True,
                    "message": "Enquiry submitted successfully.",
                    "enquiry_id": enquiry_id,
                }

            except (ValueError, TypeError) as error:
                logger.error("Invalid data while saving enquiry: %s", error)
                return {
                    "success": False,
                    "message": "Invalid enquiry data provided.",
                    "enquiry_id": None,
                }
            except Exception as error:  # noqa: BLE001 - guarantee we never crash the caller.
                logger.error("Unexpected error while saving enquiry: %s", error)
                return {
                    "success": False,
                    "message": "An unexpected error occurred while saving your enquiry.",
                    "enquiry_id": None,
                }
            finally:
                workbook.close()

    # --------------------------------------------------------------------
    # ROW <-> DICT HELPERS
    # --------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: tuple) -> Dict[str, Any]:
        """Convert a raw worksheet row tuple into a labeled dictionary."""
        values = list(row) + [""] * (len(COLUMNS) - len(row))
        return {column: values[index] for index, column in enumerate(COLUMNS)}

    def _iter_records(self, worksheet: Worksheet) -> List[Dict[str, Any]]:
        """Return every data row (excluding the header) as a list of dicts."""
        records: List[Dict[str, Any]] = []
        for row in worksheet.iter_rows(min_row=2, values_only=True):
            if not row or row[0] in (None, ""):
                continue
            records.append(self._row_to_dict(row))
        return records

    # --------------------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------------------

    def search(
        self,
        enquiry_id: Optional[str] = None,
        name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search enquiries by any combination of ID, name, email, phone,
        category or status. All text criteria are matched case-insensitively
        as substrings (except `enquiry_id`, which is matched exactly), and
        all supplied criteria must match (logical AND).

        Args:
            enquiry_id: Exact enquiry ID to match (e.g. "ENQ0001").
            name: Substring to search for within the Name column.
            email: Substring to search for within the Email column.
            phone: Substring to search for within the Phone column.
            category: Category to match (case-insensitive, exact).
            status: Status to match (case-insensitive, exact).

        Returns:
            A list of matching enquiry dictionaries (empty list on no
            matches or on any read failure).
        """
        with self._lock:
            workbook = self._load_workbook_safely()
            if workbook is None:
                return []

            try:
                worksheet = self._get_worksheet(workbook)
                records = self._iter_records(worksheet)
            except Exception as error:  # noqa: BLE001
                logger.error("Unexpected error while searching enquiries: %s", error)
                return []
            finally:
                workbook.close()

        results: List[Dict[str, Any]] = []
        for record in records:
            if enquiry_id and str(record["Enquiry ID"]).strip().upper() != enquiry_id.strip().upper():
                continue
            if name and name.strip().lower() not in str(record["Name"]).lower():
                continue
            if email and email.strip().lower() not in str(record["Email"]).lower():
                continue
            if phone and phone.strip() not in str(record["Phone"]):
                continue
            if category and str(record["Category"]).strip().upper() != category.strip().upper():
                continue
            if status and str(record["Status"]).strip().upper() != status.strip().upper():
                continue
            results.append(record)

        return results

    # --------------------------------------------------------------------
    # FILTER
    # --------------------------------------------------------------------

    def filter(
        self,
        date: Optional[str] = None,
        category: Optional[str] = None,
        mode: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Filter enquiries by date, category, mode and/or status. All
        supplied criteria must match (logical AND).

        Args:
            date: Date string in "YYYY-MM-DD" format to filter on.
            category: Category to match (case-insensitive, exact).
            mode: Mode to match (case-insensitive, exact).
            status: Status to match (case-insensitive, exact).

        Returns:
            A list of matching enquiry dictionaries (empty list on no
            matches or on any read failure).
        """
        with self._lock:
            workbook = self._load_workbook_safely()
            if workbook is None:
                return []

            try:
                worksheet = self._get_worksheet(workbook)
                records = self._iter_records(worksheet)
            except Exception as error:  # noqa: BLE001
                logger.error("Unexpected error while filtering enquiries: %s", error)
                return []
            finally:
                workbook.close()

        results: List[Dict[str, Any]] = []
        for record in records:
            if date and str(record["Date"]).strip() != date.strip():
                continue
            if category and str(record["Category"]).strip().upper() != category.strip().upper():
                continue
            if mode and str(record["Mode"]).strip().upper() != mode.strip().upper():
                continue
            if status and str(record["Status"]).strip().upper() != status.strip().upper():
                continue
            results.append(record)

        return results

    # --------------------------------------------------------------------
    # UPDATE STATUS
    # --------------------------------------------------------------------

    def update_status(
        self,
        enquiry_id: str,
        status: str,
        assigned_to: Optional[str] = None,
        remarks: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update the status (and optionally the assignee / remarks) of an
        existing enquiry.

        Args:
            enquiry_id: The enquiry ID to update (e.g. "ENQ0001").
            status: New status. Must be one of "Pending", "Contacted",
                "Resolved", "Closed".
            assigned_to: Optional staff member name to assign the enquiry
                to. Leave None to keep the existing value unchanged.
            remarks: Optional remarks/notes to attach. Leave None to keep
                the existing value unchanged.

        Returns:
            {"success": bool, "message": str}
        """
        if not enquiry_id or not enquiry_id.strip():
            return {"success": False, "message": "Enquiry ID is required."}

        normalized_status = (status or "").strip().title()
        if normalized_status not in VALID_STATUSES:
            return {
                "success": False,
                "message": f"Status must be one of: {', '.join(VALID_STATUSES)}.",
            }

        with self._lock:
            workbook = self._load_workbook_safely()
            if workbook is None:
                return {"success": False, "message": "Could not access the enquiries storage file."}

            try:
                worksheet = self._get_worksheet(workbook)
                target_row_index = self._find_row_index_by_id(worksheet, enquiry_id)

                if target_row_index is None:
                    return {"success": False, "message": f"Enquiry '{enquiry_id}' not found."}

                status_column = COLUMNS.index("Status") + 1
                assigned_column = COLUMNS.index("Assigned To") + 1
                remarks_column = COLUMNS.index("Remarks") + 1

                worksheet.cell(row=target_row_index, column=status_column, value=normalized_status)
                if assigned_to is not None:
                    worksheet.cell(row=target_row_index, column=assigned_column, value=assigned_to.strip())
                if remarks is not None:
                    worksheet.cell(row=target_row_index, column=remarks_column, value=remarks.strip())

                if not self._save_workbook_safely(workbook):
                    return {"success": False, "message": "Status update could not be saved due to a file access error."}

                logger.info("Updated enquiry '%s' to status '%s'.", enquiry_id, normalized_status)
                return {"success": True, "message": f"Enquiry '{enquiry_id}' updated to '{normalized_status}'."}

            except Exception as error:  # noqa: BLE001
                logger.error("Unexpected error while updating enquiry status: %s", error)
                return {"success": False, "message": "An unexpected error occurred while updating the enquiry."}
            finally:
                workbook.close()

    def _find_row_index_by_id(self, worksheet: Worksheet, enquiry_id: str) -> Optional[int]:
        """
        Locate the 1-based worksheet row number for a given enquiry ID.

        Args:
            worksheet: The Enquiries worksheet.
            enquiry_id: The enquiry ID to locate.

        Returns:
            The row index, or None if not found.
        """
        target = enquiry_id.strip().upper()
        for row_index, row in enumerate(
            worksheet.iter_rows(min_row=2, max_col=1, values_only=True), start=2
        ):
            if row and row[0] is not None and str(row[0]).strip().upper() == target:
                return row_index
        return None

    # --------------------------------------------------------------------
    # SOFT DELETE
    # --------------------------------------------------------------------

    def close_enquiry(self, enquiry_id: str, remarks: Optional[str] = None) -> Dict[str, Any]:
        """
        "Delete" an enquiry without ever removing data -- marks its status
        as "Closed" instead, preserving the full audit trail in the Excel
        workbook.

        Args:
            enquiry_id: The enquiry ID to close.
            remarks: Optional closing remarks to record.

        Returns:
            {"success": bool, "message": str}
        """
        return self.update_status(enquiry_id, status="Closed", remarks=remarks)

    # --------------------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------------------

    def get_summary(self) -> Dict[str, Any]:
        """
        Compute summary statistics across all enquiries, for the admin
        dashboard.

        Returns:
            {
                "total_enquiries": int,
                "pending": int,
                "contacted": int,
                "resolved": int,
                "closed": int,
                "today_enquiries": int,
            }
        """
        with self._lock:
            workbook = self._load_workbook_safely()
            if workbook is None:
                return {
                    "total_enquiries": 0,
                    "pending": 0,
                    "contacted": 0,
                    "resolved": 0,
                    "closed": 0,
                    "today_enquiries": 0,
                }

            try:
                worksheet = self._get_worksheet(workbook)
                records = self._iter_records(worksheet)
            except Exception as error:  # noqa: BLE001
                logger.error("Unexpected error while computing enquiry summary: %s", error)
                return {
                    "total_enquiries": 0,
                    "pending": 0,
                    "contacted": 0,
                    "resolved": 0,
                    "closed": 0,
                    "today_enquiries": 0,
                }
            finally:
                workbook.close()

        today_string = datetime.now().strftime(_DATE_FORMAT)
        status_counts = {status: 0 for status in VALID_STATUSES}
        today_count = 0

        for record in records:
            record_status = str(record["Status"]).strip().title()
            if record_status in status_counts:
                status_counts[record_status] += 1
            if str(record["Date"]).strip() == today_string:
                today_count += 1

        return {
            "total_enquiries": len(records),
            "pending": status_counts.get("Pending", 0),
            "in_progress": status_counts.get("In Progress", 0),
            "reviewed": status_counts.get("Reviewed", 0),
            "closed": status_counts.get("Closed", 0),
            # Legacy aliases so existing admin dashboard keys still work
            "contacted": status_counts.get("In Progress", 0),
            "resolved": status_counts.get("Reviewed", 0),
            "today_enquiries": today_count,
        }

    # --------------------------------------------------------------------
    # EXPORT
    # --------------------------------------------------------------------

    def export_csv(self, output_path: str = os.path.join("data", "enquiries_export.csv")) -> Dict[str, Any]:
        """
        Export all current enquiries to a CSV file, leaving the source
        Excel workbook completely untouched.

        Args:
            output_path: Destination path for the exported CSV file.

        Returns:
            {"success": bool, "message": str, "path": Optional[str]}
        """
        with self._lock:
            workbook = self._load_workbook_safely()
            if workbook is None:
                return {"success": False, "message": "Could not access the enquiries storage file.", "path": None}

            try:
                worksheet = self._get_worksheet(workbook)
                records = self._iter_records(worksheet)
            except Exception as error:  # noqa: BLE001
                logger.error("Unexpected error while reading enquiries for export: %s", error)
                return {"success": False, "message": "Could not read enquiries for export.", "path": None}
            finally:
                workbook.close()

        try:
            directory = os.path.dirname(output_path)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)

            with open(output_path, "w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=COLUMNS)
                writer.writeheader()
                for record in records:
                    writer.writerow(record)

            logger.info("Exported %d enquiries to CSV at '%s'.", len(records), output_path)
            return {
                "success": True,
                "message": f"Exported {len(records)} enquiries successfully.",
                "path": output_path,
            }
        except PermissionError as error:
            logger.error("Permission denied while exporting CSV: %s", error)
            return {"success": False, "message": "Permission denied while writing the export file.", "path": None}
        except OSError as error:
            logger.error("OS error while exporting CSV: %s", error)
            return {"success": False, "message": "A file system error occurred during export.", "path": None}


# ==============================================================================
# ADMISSION ENQUIRY MANAGER
# ==============================================================================

class AdmissionEnquiryManager:
    """
    Manages admission-specific enquiries with detailed student information,
    stored in a separate 'Admission_Enquiries' sheet within the same Excel
    workbook used by EnquiryManager.

    Fields: Student Name, Student Phone, Parent Phone, Email, City,
    Interested Course, Qualification, Percentage, Entrance Exam Rank
    (optional), Notes (optional).

    Generates unique reference IDs: SIET0001, SIET0002, ...
    """

    VALID_COURSES = [
        "CSE - Computer Science and Engineering",
        "CSE (AI & ML) - Artificial Intelligence and Machine Learning",
        "ECE - Electronics and Communication Engineering",
        "Not Sure / Other",
    ]

    def __init__(self, excel_path: str = os.path.join("data", "enquiries.xlsx")) -> None:
        self.excel_path: str = excel_path
        self._lock = threading.Lock()
        self._ensure_sheet_exists()

    def _ensure_sheet_exists(self) -> None:
        """Create the Admission_Enquiries sheet if it doesn't exist."""
        try:
            directory = os.path.dirname(self.excel_path)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)

            if not os.path.isfile(self.excel_path):
                # Workbook doesn't exist yet -- EnquiryManager will create it.
                # We add our sheet on first save_admission_enquiry call.
                return

            wb = load_workbook(self.excel_path)
            if ADMISSION_SHEET_NAME not in wb.sheetnames:
                ws = wb.create_sheet(ADMISSION_SHEET_NAME)
                ws.append(ADMISSION_COLUMNS)
                try:
                    for cell in ws[1]:
                        cell.font = Font(bold=True)
                    col_widths = [14, 12, 10, 22, 14, 14, 28, 16, 38, 20, 12, 18, 40, 12, 16, 30]
                    for idx, w in enumerate(col_widths, start=1):
                        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = w
                except Exception:  # noqa: BLE001
                    pass
                wb.save(self.excel_path)
                logger.info("Created '%s' sheet in workbook.", ADMISSION_SHEET_NAME)
            wb.close()
        except Exception as e:  # noqa: BLE001
            logger.error("Error ensuring admission sheet: %s", e)

    def _load_workbook_safely(self) -> Optional[Workbook]:
        """Load the workbook safely, same approach as EnquiryManager."""
        if not os.path.isfile(self.excel_path):
            return None
        try:
            return load_workbook(self.excel_path)
        except (InvalidFileException, KeyError, OSError, zipfile.BadZipFile) as e:
            logger.error("Failed to load workbook for admission enquiry: %s", e)
            return None
        except Exception as e:  # noqa: BLE001
            logger.error("Unexpected error loading workbook: %s", e)
            return None

    def _get_worksheet(self, workbook: Workbook) -> Worksheet:
        """Return the Admission_Enquiries sheet, creating it if missing."""
        if ADMISSION_SHEET_NAME in workbook.sheetnames:
            return workbook[ADMISSION_SHEET_NAME]
        ws = workbook.create_sheet(ADMISSION_SHEET_NAME)
        ws.append(ADMISSION_COLUMNS)
        return ws

    def _generate_next_id(self, worksheet: Worksheet) -> str:
        """Generate the next SIET#### reference ID."""
        highest = 0
        for row in worksheet.iter_rows(min_row=2, values_only=True):
            if not row or row[0] is None:
                continue
            match = _ADMISSION_ID_PATTERN.match(str(row[0]).strip())
            if match:
                highest = max(highest, int(match.group(1)))
        return f"SIET{highest + 1:04d}"

    def save_admission_enquiry(
        self,
        student_name: str,
        student_phone: str,
        parent_phone: str,
        email: str,
        course: str,
        percentage: str,
        parent_name: str = "",
        entrance_rank: str = "",
        quota: str = "",
        city: str = "Hyderabad",
        qualification: str = "12th / Intermediate",
        notes: str = "",
    ) -> Dict[str, Any]:
        """Validates and saves a new B.Tech admission application."""
        errors: List[str] = []

        if not student_name or len(student_name.strip()) < 3:
            errors.append("Student name must be at least 3 characters.")
        if not student_phone or not re.match(r"^[6-9]\d{9}$", student_phone.strip()):
            errors.append("Student phone must be a valid 10-digit number starting with 6-9.")
        if not parent_phone or not re.match(r"^[6-9]\d{9}$", parent_phone.strip()):
            errors.append("Parent phone must be a valid 10-digit number starting with 6-9.")
        if not email or not _EMAIL_PATTERN.match(email.strip()):
            errors.append("Please provide a valid email address.")
        if not course or not course.strip():
            errors.append("Please select a preferred branch.")
        if not percentage or not percentage.strip():
            errors.append("Percentage / 12th marks is required.")

        if errors:
            return {"success": False, "message": " ".join(errors), "reference_id": None}

        with self._lock:
            workbook = self._load_workbook_safely()
            if workbook is None:
                # Workbook doesn't exist yet -- create it
                try:
                    wb = Workbook()
                    ws = wb.active
                    ws.title = SHEET_NAME  # main enquiries sheet
                    ws.append(COLUMNS)
                    ws2 = wb.create_sheet(ADMISSION_SHEET_NAME)
                    ws2.append(ADMISSION_COLUMNS)
                    wb.save(self.excel_path)
                    wb.close()
                    workbook = load_workbook(self.excel_path)
                except Exception as e:  # noqa: BLE001
                    logger.error("Could not create workbook for admission enquiry: %s", e)
                    return {
                        "success": False,
                        "message": "Could not access the storage file. Please try again.",
                        "reference_id": None,
                    }

            try:
                worksheet = self._get_worksheet(workbook)
                ref_id = self._generate_next_id(worksheet)
                now = datetime.now()

                row = [
                    ref_id,
                    now.strftime(_DATE_FORMAT),
                    now.strftime(_TIME_FORMAT),
                    student_name.strip(),
                    student_phone.strip(),
                    parent_phone.strip(),
                    (email or "").strip(),
                    city.strip(),
                    course.strip(),
                    qualification.strip(),
                    percentage.strip(),
                    (entrance_rank or "").strip(),
                    (notes or "").strip(),
                    "Pending",
                    "",
                    "",
                ]
                worksheet.append(row)

                # Save
                attempts = 3
                saved = False
                for attempt in range(1, attempts + 1):
                    try:
                        workbook.save(self.excel_path)
                        saved = True
                        break
                    except PermissionError:
                        logger.warning(
                            "Permission denied saving admission enquiry (attempt %d/%d).",
                            attempt, attempts,
                        )
                    except OSError as e:
                        logger.error("OS error saving admission enquiry: %s", e)

                if not saved:
                    return {
                        "success": False,
                        "message": "Could not save admission enquiry due to file access error.",
                        "reference_id": None,
                    }

                logger.info("Saved admission enquiry '%s' for '%s'.", ref_id, student_name.strip())
                return {
                    "success": True,
                    "message": "Admission enquiry submitted successfully.",
                    "reference_id": ref_id,
                }

            except Exception as e:  # noqa: BLE001
                logger.error("Unexpected error saving admission enquiry: %s", e)
                return {
                    "success": False,
                    "message": "An unexpected error occurred.",
                    "reference_id": None,
                }
            finally:
                workbook.close()


# ==============================================================================
# STANDALONE MANUAL TEST (only runs when this file is executed directly,
# never when imported as part of the `modules` package by app.py).
# ==============================================================================
if __name__ == "__main__":
    manager = EnquiryManager(excel_path=os.path.join("data", "enquiries.xlsx"))

    print("Summary before:", manager.get_summary())

    save_result = manager.save_enquiry(
        name="Ravi Kumar",
        phone="9876543210",
        email="ravi.kumar@example.com",
        mode="Chatbot",
        category="FEES",
        confidence=0.42,
        question="What is the exact fee structure for CSE with all breakdowns?",
    )
    print("Save result:", save_result)

    duplicate_result = manager.save_enquiry(
        name="Ravi Kumar",
        phone="9876543210",
        email="ravi.kumar@example.com",
        mode="Chatbot",
        category="FEES",
        confidence=0.42,
        question="What is the exact fee structure for CSE with all breakdowns?",
    )
    print("Duplicate result:", duplicate_result)

    invalid_result = manager.save_enquiry(
        name="A",
        phone="12345",
        email="not-an-email",
        mode="Chatbot",
        category="FEES",
        confidence=0.42,
        question="Hi",
    )
    print("Invalid result:", invalid_result)

    if save_result.get("enquiry_id"):
        update_result = manager.update_status(
            save_result["enquiry_id"], status="Contacted", assigned_to="Admissions Desk"
        )
        print("Update result:", update_result)

    print("Search by email:", manager.search(email="ravi.kumar"))
    print("Filter by category FEES:", manager.filter(category="FEES"))
    print("Summary after:", manager.get_summary())
    print("Export:", manager.export_csv(os.path.join("data", "enquiries_export.csv")))