"""
config.py
================================================================================
Production Configuration for Sreyas Institute of Engineering and Technology Chatbot.
Centralizes all settings, path definitions, authentication, theme config,
SMTP defaults, and system constants.
================================================================================
"""

import os
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

# ── Base Directory ────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load environment variables from .env file
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ── College Information ───────────────────────────────────────────────────────
COLLEGE_NAME       = "Sreyas Institute of Engineering and Technology"
COLLEGE_SHORT_NAME = "SREYAS"
COLLEGE_LOCATION   = "Nagole, Hyderabad"
COLLEGE_WEBSITE    = "https://www.sreyas.ac.in"
COLLEGE_PHONE      = "9246323444"
COLLEGE_EMAIL      = "info@sreyas.ac.in"
HOSTEL_AVAILABLE   = False

# ── Chatbot Identity & Themes ─────────────────────────────────────────────────
BOT_NAME             = "Sreyas Chatbot"
BOT_VERSION          = "2.5"
SUPPORTED_LANGUAGES  = ["English", "Telugu", "Hindi"]
THEME_CONFIG_FILE    = os.path.join(BASE_DIR, "config", "themes.json")

# ── Data File Paths ───────────────────────────────────────────────────────────
DATA_DIR             = os.path.join(BASE_DIR, "data")
KB_PATH              = os.path.join(DATA_DIR, "knowledge_base.txt")
ENQUIRY_FILE         = os.path.join(DATA_DIR, "enquiries.xlsx")
FEEDBACK_FILE        = os.path.join(DATA_DIR, "feedback.xlsx")
BACKUP_DIR           = os.path.join(DATA_DIR, "backups")
LOG_FOLDER           = os.path.join(DATA_DIR, "logs")

CHAT_LOG_FILE        = os.path.join(LOG_FOLDER, "chat.log")
ERROR_LOG_FILE       = os.path.join(LOG_FOLDER, "error.log")
AUDIT_LOG_FILE       = os.path.join(LOG_FOLDER, "audit.log")
SYNC_LOG_FILE        = os.path.join(LOG_FOLDER, "sync.log")

# Ensure required directories exist
for folder in (DATA_DIR, BACKUP_DIR, LOG_FOLDER):
    os.makedirs(folder, exist_ok=True)

# ── RAG Settings ──────────────────────────────────────────────────────────────
TOP_K_RESULTS       = 3
HIGH_CONFIDENCE     = 0.80
MEDIUM_CONFIDENCE   = 0.55
LOW_CONFIDENCE      = 0.40

# Scoring Weights
COSINE_WEIGHT       = 0.45
KEYWORD_WEIGHT      = 0.25
CATEGORY_WEIGHT     = 0.15
SYNONYM_WEIGHT      = 0.10
MEMORY_WEIGHT       = 0.05

# ── Memory & Spam ─────────────────────────────────────────────────────────────
MAX_MEMORY_MESSAGES      = 5
SESSION_TIMEOUT          = 30  # minutes
MAX_REQUESTS_PER_MINUTE  = 30
DUPLICATE_MESSAGE_LIMIT  = 3
COOLDOWN_SECONDS         = 60

# ── Generic SMTP Email Settings (Environment Configurable) ────────────────────
SMTP_HOST       = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT       = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER       = os.getenv("SMTP_USER", "")
SMTP_PASS       = os.getenv("SMTP_PASS", "")
SMTP_USE_TLS    = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", COLLEGE_EMAIL)

# ── Security & Admin Authentication ───────────────────────────────────────────
SECRET_KEY            = os.getenv("FLASK_SECRET_KEY", "SREYAS_CHATBOT_SECURE_KEY_2026_PRODUCTION")
ADMIN_SESSION_TIMEOUT = 1800  # 30 minutes in seconds

# Hashed credentials for multi-account admin support
DEFAULT_ADMIN_PASS = os.getenv("ADMIN_PASSWORD", "SreyasAdmin2026!")
ADMIN_USERS = {
    "admin": generate_password_hash(DEFAULT_ADMIN_PASS),
    "admissions": generate_password_hash("SreyasAdmissions2026!"),
}

# ── Knowledge Base Sync Schedule ──────────────────────────────────────────────
AUTO_SYNC_ENABLED = True
SYNC_HOUR         = 0   # 12:00 AM daily
SYNC_MINUTE       = 0

# ── Flask Server ──────────────────────────────────────────────────────────────
HOST  = os.getenv("HOST", "0.0.0.0")
PORT  = int(os.getenv("PORT", "5000"))
DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# ── Feature Flags ─────────────────────────────────────────────────────────────
ENABLE_GUARDRAILS       = True
ENABLE_SPAM_PROTECTION  = True
ENABLE_MEMORY           = True
ENABLE_LOGGING          = True