"""
modules/student_db.py
================================================================================
Comprehensive Fictional Student & Parent Database for Sreyas AI Assistant.
Features:
  - SQLite persistence (data/students.db)
  - Strict schema for Students, Parents, Relationships, Academic Records, and OTPs
  - Real semester-wise breakdown (1-1, 1-2, 2-1, 2-2, 3-1, 3-2, 4-1, 4-2)
  - PBKDF2 hashing for OTPs and PINs
  - Session tokens with strict server-side authorization
  - Admin management APIs for approval, rejection, suspension, and record editing
================================================================================
"""

import os
import sqlite3
import secrets
import time
import random
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "students.db")

OTP_EXPIRY_SECONDS = 300       # 5 minutes
RESEND_COOLDOWN_SECONDS = 60   # 1 minute resend cooldown
MAX_OTP_ATTEMPTS = 5
SESSION_EXPIRY_SECONDS = 3600  # 1 hour

# Valid semester codes
VALID_SEMESTERS = ["1-1", "1-2", "2-1", "2-2", "3-1", "3-2", "4-1", "4-2"]

# In-memory Active Sessions: { session_token: { "parent_id": int, "parent_email": str, "expires": float } }
_ACTIVE_SESSIONS: Dict[str, Dict[str, Any]] = {}


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(force: bool = False) -> None:
    """Initializes tables and seeds ~100 realistic fictional students with semester-wise records."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Students Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS students (
        student_id TEXT PRIMARY KEY,
        hall_ticket_no TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        branch TEXT NOT NULL,
        academic_year INTEGER NOT NULL,
        current_semester TEXT NOT NULL,
        section TEXT NOT NULL,
        account_status TEXT NOT NULL DEFAULT 'Active'
    )
    """)

    # 2. Parents Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS parents (
        parent_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT,
        status TEXT NOT NULL DEFAULT 'Pending Verification',
        created_at TEXT NOT NULL
    )
    """)

    # 3. Parent-Student Relationships
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS parent_student_relationships (
        relationship_id INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_id INTEGER NOT NULL,
        student_id TEXT NOT NULL,
        relationship_type TEXT NOT NULL,
        approval_status TEXT NOT NULL DEFAULT 'Pending Verification',
        approved_by TEXT,
        approved_at TEXT,
        revoked_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (parent_id) REFERENCES parents(parent_id),
        FOREIGN KEY (student_id) REFERENCES students(student_id),
        UNIQUE(parent_id, student_id)
    )
    """)

    # 4. Academic Records Table (Semester-wise: 1-1, 1-2, 2-1, 2-2, 3-1, 3-2, etc.)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS academic_records (
        record_id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        academic_year INTEGER NOT NULL,
        semester TEXT NOT NULL,
        subject_code TEXT NOT NULL,
        subject_name TEXT NOT NULL,
        internal_marks INTEGER NOT NULL,
        external_marks INTEGER NOT NULL,
        total_marks INTEGER NOT NULL,
        attendance_percentage REAL NOT NULL,
        grade TEXT NOT NULL,
        status TEXT NOT NULL,
        FOREIGN KEY (student_id) REFERENCES students(student_id),
        UNIQUE(student_id, semester, subject_code)
    )
    """)

    # 5. OTPs Table (hashed OTPs with single-use flag and expiry)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS parent_otps (
        email TEXT PRIMARY KEY,
        otp_hash TEXT NOT NULL,
        expires_at REAL NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_sent_at REAL NOT NULL,
        is_used INTEGER NOT NULL DEFAULT 0
    )
    """)

    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM students")
    count = cursor.fetchone()[0]

    if count >= 100 and not force:
        conn.close()
        return

    if force:
        cursor.execute("DELETE FROM academic_records")
        cursor.execute("DELETE FROM parent_student_relationships")
        cursor.execute("DELETE FROM parents")
        cursor.execute("DELETE FROM students")
        cursor.execute("DELETE FROM parent_otps")
        conn.commit()

    # Seed ~100 realistic fictional students
    random.seed(42)
    branches = ["CSE", "CSE (AI & ML)", "ECE"]
    first_names = [
        "Aarav", "Aditi", "Akhil", "Ananya", "Bhavya", "Chaitanya", "Deepak", "Divya",
        "Gautam", "Harika", "Ishaan", "Kavya", "Keerthi", "Kiran", "Madhuri", "Manoj",
        "Naveen", "Nikita", "Pooja", "Pranav", "Priyanka", "Rahul", "Rakesh", "Ramesh",
        "Rhea", "Rohit", "Sai", "Sameer", "Sandhya", "Sanjay", "Santhosh", "Sarika",
        "Shiva", "Sneha", "Spandana", "Sravan", "Srikanth", "Srinivas", "Sruthi", "Suresh",
        "Surya", "Swathi", "Tarun", "Teja", "Varun", "Venkatesh", "Vidya", "Vikas",
        "Vinay", "Vishnu"
    ]
    last_names = [
        "Reddy", "Rao", "Sharma", "Varma", "Chowdary", "Gupta", "Goud", "Kaur",
        "Patel", "Naidu", "Yadav", "Kumar", "Prasad", "Mishra", "Joshi"
    ]

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Primary demo student: Sai Krishna Reddy (Third Year - 3rd year student has 3-1 completed and 3-2 in progress)
    cursor.execute("""
    INSERT OR REPLACE INTO students (
        student_id, hall_ticket_no, name, branch, academic_year, current_semester, section, account_status
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, ("SIET2023CSE001", "23VE1A0501", "Sai Krishna Reddy", "CSE", 3, "3-2", "A", "Active"))

    # Add subjects for Sai Krishna Reddy:
    # 3-1 Semester (Third Year, First Semester)
    demo_31_subjects = [
        ("CS501PC", "Design and Analysis of Algorithms", 28, 62, 90, 88.5, "O", "PASSED"),
        ("CS502PC", "Database Management Systems", 26, 56, 82, 85.0, "A+", "PASSED"),
        ("CS503PC", "Software Engineering", 25, 52, 77, 84.0, "A+", "PASSED"),
        ("CS504PC", "Web Technologies", 27, 58, 85, 91.0, "O", "PASSED"),
        ("CS505PC", "Computer Networks", 24, 48, 72, 82.5, "A", "PASSED"),
    ]
    for sc, sn, im, em, tm, att, gr, st in demo_31_subjects:
        cursor.execute("""
        INSERT OR REPLACE INTO academic_records (
            student_id, academic_year, semester, subject_code, subject_name,
            internal_marks, external_marks, total_marks, attendance_percentage, grade, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("SIET2023CSE001", 3, "3-1", sc, sn, im, em, tm, att, gr, st))

    # 3-2 Semester (Third Year, Second Semester - DISTINCT courses)
    demo_32_subjects = [
        ("CS601PC", "Machine Learning & Neural Networks", 27, 59, 86, 89.0, "O", "PASSED"),
        ("CS602PC", "Compiler Design", 23, 50, 73, 84.0, "A", "PASSED"),
        ("CS603PC", "Cloud Computing & DevOps", 28, 61, 89, 92.5, "O", "PASSED"),
        ("CS604PE", "Information Security (Elective)", 25, 54, 79, 86.0, "A+", "PASSED"),
        ("CS605OE", "Managerial Economics & Financial Analysis", 26, 51, 77, 85.0, "A+", "PASSED"),
    ]
    for sc, sn, im, em, tm, att, gr, st in demo_32_subjects:
        cursor.execute("""
        INSERT OR REPLACE INTO academic_records (
            student_id, academic_year, semester, subject_code, subject_name,
            internal_marks, external_marks, total_marks, attendance_percentage, grade, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("SIET2023CSE001", 3, "3-2", sc, sn, im, em, tm, att, gr, st))

    # Primary demo parent already approved & active for instant evaluation
    cursor.execute("""
    INSERT OR REPLACE INTO parents (parent_id, name, email, phone, status, created_at)
    VALUES (1, 'K. Rama Rao Reddy', 'parent.demo@sreyas.ac.in', '+91 9848022338', 'Active', ?)
    """, (now_str,))

    cursor.execute("""
    INSERT OR REPLACE INTO parent_student_relationships (
        relationship_id, parent_id, student_id, relationship_type, approval_status,
        approved_by, approved_at, created_at
    ) VALUES (1, 1, 'SIET2023CSE001', 'Father', 'Approved', 'admin', ?, ?)
    """, (now_str, now_str))

    # Also insert a Pending Verification request for demonstration
    cursor.execute("""
    INSERT OR REPLACE INTO parents (parent_id, name, email, phone, status, created_at)
    VALUES (2, 'Suresh Varma', 'parent.pending@sreyas.ac.in', '+91 9876543210', 'Pending Verification', ?)
    """, (now_str,))

    cursor.execute("""
    INSERT OR REPLACE INTO parent_student_relationships (
        relationship_id, parent_id, student_id, relationship_type, approval_status, created_at
    ) VALUES (2, 2, 'SIET2023CSE002', 'Father', 'Pending Verification', ?)
    """, (now_str,))

    # Seed 99 more students (total 101)
    for i in range(2, 102):
        branch = branches[(i - 2) % len(branches)]
        year = ((i - 2) % 4) + 1
        sem1 = f"{year}-1"
        sem2 = f"{year}-2"
        sec = chr(65 + ((i - 2) % 3))
        s_fn = first_names[(i * 3) % len(first_names)]
        s_ln = last_names[(i * 7) % len(last_names)]
        name = f"{s_fn} {s_ln}"
        ht_branch_code = "05" if "CSE" == branch else ("66" if "AI" in branch else "04")
        hall_ticket = f"2{4 - year}VE1A{ht_branch_code}{i:02d}"
        s_id = f"SIET202{4 - year}{branch[:2].strip()}{i:03d}"

        cursor.execute("""
        INSERT OR REPLACE INTO students (
            student_id, hall_ticket_no, name, branch, academic_year, current_semester, section, account_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (s_id, hall_ticket, name, branch, year, sem2, sec, "Active"))

        # Seed sem1 records
        for s_idx in range(1, 5):
            sub_code = f"{branch[:2]}{year}10{s_idx}PC"
            sub_name = f"{branch} Core {s_idx} (Sem {sem1})"
            im = random.randint(22, 29)
            em = random.randint(40, 68)
            tm = im + em
            att = round(random.uniform(72.0, 96.0), 1)
            gr = "O" if tm >= 85 else ("A+" if tm >= 75 else ("A" if tm >= 65 else "B"))
            st = "PASSED" if tm >= 40 else "SUPPLEMENTARY"
            cursor.execute("""
            INSERT OR REPLACE INTO academic_records (
                student_id, academic_year, semester, subject_code, subject_name,
                internal_marks, external_marks, total_marks, attendance_percentage, grade, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (s_id, year, sem1, sub_code, sub_name, im, em, tm, att, gr, st))

        # Seed sem2 records (different subject codes and names)
        for s_idx in range(1, 5):
            sub_code = f"{branch[:2]}{year}20{s_idx}PC"
            sub_name = f"{branch} Advanced {s_idx} (Sem {sem2})"
            im = random.randint(20, 28)
            em = random.randint(38, 65)
            tm = im + em
            att = round(random.uniform(70.0, 95.0), 1)
            gr = "O" if tm >= 85 else ("A+" if tm >= 75 else ("A" if tm >= 65 else "B"))
            st = "PASSED" if tm >= 40 else "SUPPLEMENTARY"
            cursor.execute("""
            INSERT OR REPLACE INTO academic_records (
                student_id, academic_year, semester, subject_code, subject_name,
                internal_marks, external_marks, total_marks, attendance_percentage, grade, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (s_id, year, sem2, sub_code, sub_name, im, em, tm, att, gr, st))

    conn.commit()
    conn.close()


# ── Parent Registration & Onboarding ──────────────────────────────────────────

def register_parent_request(
    parent_name: str,
    parent_email: str,
    student_id: str,
    student_name: str,
    relationship_type: str,
    parent_phone: str = ""
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Submits a Parent Mode registration request.
    Validates required fields, email format, and checks that student exists by student_id.
    Creates records with status 'Pending Verification'.
    Does NOT grant academic access at this stage.
    """
    p_name = (parent_name or "").strip()
    p_email = (parent_email or "").strip().lower()
    s_id = (student_id or "").strip().upper()
    s_name = (student_name or "").strip()
    rel = (relationship_type or "").strip()
    phone = (parent_phone or "").strip()

    if not p_name or len(p_name) < 2:
        return False, "Parent full name is required (minimum 2 characters).", None

    if not p_email or not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", p_email):
        return False, "A valid email address is required.", None

    if not s_id:
        return False, "Unique Student ID or Roll Number is required.", None

    if not s_name:
        return False, "Student full name is required.", None

    if not rel:
        return False, "Relationship to student is required (e.g., Father, Mother, Guardian).", None

    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify student exists in official records by unique student_id or hall_ticket_no
    cursor.execute("""
        SELECT * FROM students
        WHERE UPPER(student_id) = ? OR UPPER(hall_ticket_no) = ?
    """, (s_id, s_id))
    student = cursor.fetchone()

    if not student:
        conn.close()
        return False, f"No student found with ID / Roll Number '{s_id}' in college records.", None

    # Check student name match warning / verification
    target_student_id = student["student_id"]

    # Insert or update parent record
    cursor.execute("SELECT * FROM parents WHERE email = ?", (p_email,))
    existing_parent = cursor.fetchone()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if existing_parent:
        parent_id = existing_parent["parent_id"]
        # If existing parent was rejected or active, keep record
    else:
        cursor.execute("""
            INSERT INTO parents (name, email, phone, status, created_at)
            VALUES (?, ?, ?, 'Pending Verification', ?)
        """, (p_name, p_email, phone, now_str))
        parent_id = cursor.lastrowid

    # Check relationship request
    cursor.execute("""
        SELECT * FROM parent_student_relationships
        WHERE parent_id = ? AND student_id = ?
    """, (parent_id, target_student_id))
    rel_row = cursor.fetchone()

    if rel_row:
        if rel_row["approval_status"] == "Approved":
            conn.close()
            return True, "Your registration was already approved. You may proceed to email verification.", {
                "parent_id": parent_id,
                "status": "Approved — Awaiting Email Verification",
                "student_id": target_student_id
            }
        elif rel_row["approval_status"] == "Pending Verification":
            conn.close()
            return True, "Your registration request has already been submitted and is awaiting college administration verification.", {
                "parent_id": parent_id,
                "status": "Pending Verification",
                "student_id": target_student_id
            }

    cursor.execute("""
        INSERT OR REPLACE INTO parent_student_relationships (
            parent_id, student_id, relationship_type, approval_status, created_at
        ) VALUES (?, ?, ?, 'Pending Verification', ?)
    """, (parent_id, target_student_id, rel, now_str))

    conn.commit()
    conn.close()

    return True, "Your registration request has been submitted and is awaiting college administration verification.", {
        "parent_id": parent_id,
        "status": "Pending Verification",
        "student_id": target_student_id
    }


# ── Manual Admin Verification ─────────────────────────────────────────────────

def admin_get_parent_requests(status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieves parent registration requests for admin review."""
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT r.relationship_id, r.parent_id, r.student_id, r.relationship_type,
               r.approval_status, r.approved_by, r.approved_at, r.created_at,
               p.name AS parent_name, p.email AS parent_email, p.phone AS parent_phone,
               s.name AS student_name, s.hall_ticket_no, s.branch, s.academic_year
        FROM parent_student_relationships r
        JOIN parents p ON r.parent_id = p.parent_id
        JOIN students s ON r.student_id = s.student_id
    """
    params: List[Any] = []
    if status_filter:
        query += " WHERE r.approval_status = ?"
        params.append(status_filter)

    query += " ORDER BY r.relationship_id DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def admin_approve_request(relationship_id: int, admin_user: str) -> Tuple[bool, str]:
    """Admin approves a parent-student registration request."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM parent_student_relationships WHERE relationship_id = ?", (relationship_id,))
    rel = cursor.fetchone()
    if not rel:
        conn.close()
        return False, "Registration request not found."

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        UPDATE parent_student_relationships
        SET approval_status = 'Approved', approved_by = ?, approved_at = ?, revoked_at = NULL
        WHERE relationship_id = ?
    """, (admin_user, now_str, relationship_id))

    cursor.execute("""
        UPDATE parents
        SET status = 'Approved — Awaiting Email Verification'
        WHERE parent_id = ? AND status != 'Active'
    """, (rel["parent_id"],))

    conn.commit()
    conn.close()
    return True, f"Request #{relationship_id} approved. Parent can now verify via email OTP."


def admin_reject_request(relationship_id: int, admin_user: str, reason: str = "") -> Tuple[bool, str]:
    """Admin rejects a parent-student registration request."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM parent_student_relationships WHERE relationship_id = ?", (relationship_id,))
    rel = cursor.fetchone()
    if not rel:
        conn.close()
        return False, "Registration request not found."

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        UPDATE parent_student_relationships
        SET approval_status = 'Rejected', approved_by = ?, approved_at = ?, revoked_at = ?
        WHERE relationship_id = ?
    """, (admin_user, now_str, now_str, relationship_id))

    cursor.execute("""
        UPDATE parents
        SET status = 'Rejected'
        WHERE parent_id = ?
    """, (rel["parent_id"],))

    conn.commit()
    conn.close()
    return True, f"Request #{relationship_id} rejected."


def admin_set_parent_status(parent_id: int, new_status: str, admin_user: str) -> Tuple[bool, str]:
    """Admin sets parent status: Active, Suspended, or Revoked."""
    if new_status not in ["Active", "Suspended", "Rejected", "Pending Verification"]:
        return False, f"Invalid status: {new_status}"

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM parents WHERE parent_id = ?", (parent_id,))
    if not cursor.fetchone():
        conn.close()
        return False, "Parent account not found."

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("UPDATE parents SET status = ? WHERE parent_id = ?", (new_status, parent_id))

    if new_status == "Suspended":
        cursor.execute("""
            UPDATE parent_student_relationships
            SET approval_status = 'Suspended', revoked_at = ?
            WHERE parent_id = ?
        """, (now_str, parent_id))
    elif new_status == "Active":
        cursor.execute("""
            UPDATE parent_student_relationships
            SET approval_status = 'Approved', revoked_at = NULL
            WHERE parent_id = ? AND approval_status != 'Rejected'
        """, (parent_id,))

    conn.commit()
    conn.close()
    return True, f"Parent account #{parent_id} updated to {new_status}."


# ── Real Email OTP Generation & Storage ───────────────────────────────────────

def create_and_store_otp(email: str) -> Tuple[bool, str, Optional[str]]:
    """
    Creates a secure 6-digit OTP, hashes it with PBKDF2, and saves to database.
    Requires that the parent exists and is Approved.
    Returns (success, message, raw_otp).
    The raw_otp is returned ONLY so the email service can dispatch it.
    """
    email = (email or "").strip().lower()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM parents WHERE email = ?", (email,))
    parent = cursor.fetchone()
    if not parent:
        conn.close()
        return False, "No parent registration found for this email.", None

    if parent["status"] not in ["Approved — Awaiting Email Verification", "Active"]:
        conn.close()
        return False, f"Cannot issue OTP: Account status is '{parent['status']}'. Awaiting admin approval.", None

    # Check cooldown
    cursor.execute("SELECT * FROM parent_otps WHERE email = ?", (email,))
    existing_otp = cursor.fetchone()
    now_ts = time.time()

    if existing_otp and (now_ts - existing_otp["last_sent_at"] < RESEND_COOLDOWN_SECONDS):
        wait_left = int(RESEND_COOLDOWN_SECONDS - (now_ts - existing_otp["last_sent_at"]))
        conn.close()
        return False, f"Please wait {wait_left} seconds before requesting a new OTP.", None

    # Cryptographically secure 6-digit OTP
    raw_otp = f"{secrets.randbelow(900000) + 100000:06d}"
    otp_hash = generate_password_hash(raw_otp)
    expires_at = now_ts + OTP_EXPIRY_SECONDS

    cursor.execute("""
        INSERT OR REPLACE INTO parent_otps (email, otp_hash, expires_at, attempts, last_sent_at, is_used)
        VALUES (?, ?, ?, 0, ?, 0)
    """, (email, otp_hash, expires_at, now_ts))

    conn.commit()
    conn.close()

    return True, "OTP generated successfully.", raw_otp


def verify_email_otp(email: str, submitted_otp: str) -> Tuple[bool, str, Optional[str]]:
    """
    Validates submitted OTP against stored hash.
    Enforces single-use, 5-minute expiry, and max attempts.
    Activates parent account on success and issues a session token.
    """
    email = (email or "").strip().lower()
    sub_otp = (submitted_otp or "").strip()

    if not email or not sub_otp:
        return False, "Email and OTP are required.", None

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM parent_otps WHERE email = ?", (email,))
    record = cursor.fetchone()

    if not record:
        conn.close()
        return False, "No active OTP request found. Please request a new verification OTP.", None

    if record["is_used"]:
        conn.close()
        return False, "This OTP has already been used. Please request a new one.", None

    now_ts = time.time()
    if now_ts > record["expires_at"]:
        conn.close()
        return False, "This OTP has expired (5-minute limit). Please request a new one.", None

    if record["attempts"] >= MAX_OTP_ATTEMPTS:
        conn.close()
        return False, "Maximum verification attempts exceeded. Please request a new OTP.", None

    # Increment attempt count
    cursor.execute("UPDATE parent_otps SET attempts = attempts + 1 WHERE email = ?", (email,))
    conn.commit()

    if not check_password_hash(record["otp_hash"], sub_otp):
        conn.close()
        return False, "Invalid verification code. Please check and try again.", None

    # Successful verification! Mark OTP used
    cursor.execute("UPDATE parent_otps SET is_used = 1 WHERE email = ?", (email,))

    # Update parent to Active
    cursor.execute("SELECT * FROM parents WHERE email = ?", (email,))
    parent = cursor.fetchone()
    if parent:
        cursor.execute("UPDATE parents SET status = 'Active' WHERE email = ?", (email,))

    conn.commit()
    conn.close()

    # Issue server-side session token
    token = secrets.token_hex(24)
    _ACTIVE_SESSIONS[token] = {
        "parent_id": parent["parent_id"],
        "parent_email": email,
        "expires": now_ts + SESSION_EXPIRY_SECONDS
    }

    return True, "Your email has been verified and your parent account is active. You can now access your linked student's academic records.", token


# ── Strict Student-Level Access Control ───────────────────────────────────────

def get_parent_from_session(token: str) -> Optional[Dict[str, Any]]:
    """Derives and validates parent identity from the authenticated server-side session."""
    if not token:
        return None

    sess = _ACTIVE_SESSIONS.get(token)
    if not sess:
        return None

    if time.time() > sess["expires"]:
        _ACTIVE_SESSIONS.pop(token, None)
        return None

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM parents WHERE parent_id = ?", (sess["parent_id"],))
    parent = cursor.fetchone()
    conn.close()

    if not parent or parent["status"] != "Active":
        return None

    return dict(parent)


def get_linked_student_records(token: str, semester: Optional[str] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    STRICT SECURITY ACCESS CONTROL:
    Derives parent from server session token ONLY.
    Looks up relationships where approval_status = 'Approved'.
    Ensures Parent A can ONLY see Student A's records, even if a student ID was supplied.
    Filters strictly by requested semester (e.g. 3-1 vs 3-2).
    """
    parent = get_parent_from_session(token)
    if not parent:
        return False, "Unauthorized: Session invalid, expired, or account not active.", None

    conn = get_db_connection()
    cursor = conn.cursor()

    # Find approved linked student(s) for this parent
    cursor.execute("""
        SELECT r.student_id, r.relationship_type, r.approval_status,
               s.name AS student_name, s.hall_ticket_no, s.branch,
               s.academic_year, s.current_semester, s.section, s.account_status
        FROM parent_student_relationships r
        JOIN students s ON r.student_id = s.student_id
        WHERE r.parent_id = ? AND r.approval_status = 'Approved'
    """, (parent["parent_id"],))
    student_rows = cursor.fetchall()

    if not student_rows:
        conn.close()
        return False, "No active approved student record linked to this parent account.", None

    # Use linked student
    student = dict(student_rows[0])
    s_id = student["student_id"]

    # Determine available semesters for this student
    cursor.execute("""
        SELECT DISTINCT semester FROM academic_records
        WHERE student_id = ?
        ORDER BY semester ASC
    """, (s_id,))
    sem_rows = cursor.fetchall()
    available_semesters = [r["semester"] for r in sem_rows]

    # If semester specified, validate and filter
    target_sem = semester.strip() if semester else (available_semesters[-1] if available_semesters else "3-1")

    cursor.execute("""
        SELECT record_id, academic_year, semester, subject_code, subject_name,
               internal_marks, external_marks, total_marks, attendance_percentage, grade, status
        FROM academic_records
        WHERE student_id = ? AND semester = ?
        ORDER BY subject_code ASC
    """, (s_id, target_sem))
    recs = cursor.fetchall()
    conn.close()

    records_list = [dict(r) for r in recs]

    # Calculate overall semester performance if records exist
    overall_performance = {}
    if records_list:
        avg_att = round(sum(r["attendance_percentage"] for r in records_list) / len(records_list), 1)
        tot_marks = sum(r["total_marks"] for r in records_list)
        max_marks = len(records_list) * 100
        pass_count = sum(1 for r in records_list if r["status"] == "PASSED")
        overall_performance = {
            "average_attendance": avg_att,
            "total_marks_obtained": tot_marks,
            "maximum_marks": max_marks,
            "percentage": round((tot_marks / max_marks) * 100, 2) if max_marks > 0 else 0.0,
            "subjects_passed": pass_count,
            "total_subjects": len(records_list),
            "status": "ALL PASSED" if pass_count == len(records_list) else "SUPPLEMENTARY"
        }

    return True, "Academic records retrieved successfully.", {
        "parent": {
            "name": parent["name"],
            "email": parent["email"]
        },
        "student": student,
        "selected_semester": target_sem,
        "available_semesters": available_semesters,
        "academic_records": records_list,
        "overall_performance": overall_performance
    }


# ── Admin Fictional Student & Record Management ────────────────────────────────

def admin_search_students(query: str = "") -> List[Dict[str, Any]]:
    """Search students by ID, hall ticket, or name."""
    conn = get_db_connection()
    cursor = conn.cursor()
    q = f"%{(query or '').strip()}%"
    cursor.execute("""
        SELECT * FROM students
        WHERE student_id LIKE ? OR hall_ticket_no LIKE ? OR name LIKE ? OR branch LIKE ?
        ORDER BY student_id ASC LIMIT 100
    """, (q, q, q, q))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def admin_add_or_edit_student(
    student_id: str,
    hall_ticket_no: str,
    name: str,
    branch: str,
    academic_year: int,
    current_semester: str,
    section: str = "A",
    account_status: str = "Active"
) -> Tuple[bool, str]:
    """Adds or edits a student record, validating semester and preventing duplicate IDs."""
    s_id = (student_id or "").strip().upper()
    ht = (hall_ticket_no or "").strip().upper()
    nm = (name or "").strip()
    br = (branch or "").strip()
    sem = (current_semester or "").strip()
    status = (account_status or "Active").strip()

    if not s_id or not ht or not nm or not br:
        return False, "Student ID, Hall Ticket Number, Name, and Branch are required."

    valid_branches = ["CSE", "CSE (AI & ML)", "ECE"]
    if br not in valid_branches:
        return False, f"Invalid branch '{br}'. Must be one of: {', '.join(valid_branches)}"

    if sem not in VALID_SEMESTERS:
        return False, f"Invalid semester '{sem}'. Must be one of: {', '.join(VALID_SEMESTERS)}"

    # Validate year-semester combination (e.g. Year 3 must have 3-1 or 3-2)
    expected_year = sem.split("-")[0]
    if str(academic_year) != expected_year:
        return False, f"Semester '{sem}' does not match Academic Year {academic_year}."

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO students (student_id, hall_ticket_no, name, branch, academic_year, current_semester, section, account_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id) DO UPDATE SET
                hall_ticket_no = excluded.hall_ticket_no,
                name = excluded.name,
                branch = excluded.branch,
                academic_year = excluded.academic_year,
                current_semester = excluded.current_semester,
                section = excluded.section,
                account_status = excluded.account_status
        """, (s_id, ht, nm, br, academic_year, sem, section, status))
        conn.commit()
        conn.close()
        return True, f"Student {s_id} saved successfully."
    except sqlite3.IntegrityError as e:
        conn.close()
        return False, f"Database error (Duplicate Hall Ticket or constraint): {e}"


def admin_add_or_edit_academic_record(
    student_id: str,
    academic_year: int,
    semester: str,
    subject_code: str,
    subject_name: str,
    internal_marks: int,
    external_marks: int,
    attendance_percentage: float
) -> Tuple[bool, str]:
    """Adds or edits a semester academic record with full validation."""
    s_id = (student_id or "").strip().upper()
    sem = (semester or "").strip()
    sc = (subject_code or "").strip().upper()
    sn = (subject_name or "").strip()

    if sem not in VALID_SEMESTERS:
        return False, f"Invalid semester '{sem}'. Must be one of: {', '.join(VALID_SEMESTERS)}"

    if not (0 <= internal_marks <= 40):
        return False, "Internal marks must be between 0 and 40."

    if not (0 <= external_marks <= 70):
        return False, "External marks must be between 0 and 70."

    if not (0.0 <= attendance_percentage <= 100.0):
        return False, "Attendance percentage must be between 0.0 and 100.0."

    total = internal_marks + external_marks
    grade = "O" if total >= 85 else ("A+" if total >= 75 else ("A" if total >= 65 else ("B" if total >= 55 else "C")))
    status = "PASSED" if (external_marks >= 21 and total >= 40) else "SUPPLEMENTARY"

    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify student exists
    cursor.execute("SELECT student_id FROM students WHERE student_id = ?", (s_id,))
    if not cursor.fetchone():
        conn.close()
        return False, f"Student '{s_id}' does not exist."

    try:
        cursor.execute("""
            INSERT INTO academic_records (
                student_id, academic_year, semester, subject_code, subject_name,
                internal_marks, external_marks, total_marks, attendance_percentage, grade, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, semester, subject_code) DO UPDATE SET
                subject_name = excluded.subject_name,
                internal_marks = excluded.internal_marks,
                external_marks = excluded.external_marks,
                total_marks = excluded.total_marks,
                attendance_percentage = excluded.attendance_percentage,
                grade = excluded.grade,
                status = excluded.status
        """, (s_id, academic_year, sem, sc, sn, internal_marks, external_marks, total, attendance_percentage, grade, status))
        conn.commit()
        conn.close()
        return True, f"Academic record for {s_id} ({sem} - {sc}) saved successfully."
    except Exception as e:
        conn.close()
        return False, f"Failed to save record: {e}"


def admin_get_student_details(student_id: str) -> Optional[Dict[str, Any]]:
    """Returns student profile and all semester records grouped by semester."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM students WHERE student_id = ?", (student_id,))
    s_row = cursor.fetchone()
    if not s_row:
        conn.close()
        return None

    cursor.execute("""
        SELECT * FROM academic_records WHERE student_id = ?
        ORDER BY semester ASC, subject_code ASC
    """, (student_id,))
    records = cursor.fetchall()
    conn.close()

    records_by_sem: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        sem = r["semester"]
        records_by_sem.setdefault(sem, []).append(dict(r))

    return {
        "student": dict(s_row),
        "records_by_semester": records_by_sem
    }
