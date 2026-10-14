"""
app.py
================================================================================
SREYAS Assistant — Flask Backend (Production Ready)
Works on Windows, Linux, Ubuntu, Docker, any college server.
No hardcoded paths. No paid external LLM API dependencies.

Run:
    pip install flask flask-cors openpyxl werkzeug python-dotenv
    python app.py
================================================================================
"""

from __future__ import annotations

import os
import sys
import re
import time
import uuid
import json
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
from werkzeug.security import check_password_hash

# ── Path setup — works from any working directory ─────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
MODULES_DIR = os.path.join(BASE_DIR, "modules")

for p in (BASE_DIR, MODULES_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import config
import scheduler
from modules.audit_logger  import AuditLogger
from modules.backup_service import BackupService
from modules.feedback       import FeedbackManager
from modules.email_service  import EmailService

# ── Import core modules ───────────────────────────────────────────────────────
try:
    from modules.rag        import RAGEngine
    from modules.guardrails import Guardrails
    from modules.memory     import ConversationMemory
    from modules.spam       import SpamDetector
    from modules.logger     import ChatLogger
    from modules.enquiry    import EnquiryManager, AdmissionEnquiryManager
    from modules.intent_engine import IntentEngine
    from modules.language   import Localizer
    from modules.student_db import (
        init_db as init_student_db,
        register_parent_request,
        admin_get_parent_requests,
        admin_approve_request,
        admin_reject_request,
        admin_set_parent_status,
        create_and_store_otp,
        verify_email_otp,
        get_linked_student_records,
        admin_search_students,
        admin_add_or_edit_student,
        admin_add_or_edit_academic_record,
        admin_get_student_details,
    )
except ImportError:
    from rag        import RAGEngine         # type: ignore
    from guardrails import Guardrails        # type: ignore
    from memory     import ConversationMemory # type: ignore
    from spam       import SpamDetector      # type: ignore
    from logger     import ChatLogger        # type: ignore
    from enquiry    import EnquiryManager, AdmissionEnquiryManager    # type: ignore
    from language   import Localizer          # type: ignore
    from student_db import (                 # type: ignore
        init_db as init_student_db,
        register_parent_request,
        admin_get_parent_requests,
        admin_approve_request,
        admin_reject_request,
        admin_set_parent_status,
        create_and_store_otp,
        verify_email_otp,
        get_linked_student_records,
        admin_search_students,
        admin_add_or_edit_student,
        admin_add_or_edit_academic_record,
        admin_get_student_details,
    )

# ── File paths ────────────────────────────────────────────────────────────────
STATIC_DIR = os.path.join(BASE_DIR, "static")
FACTS_FILE = os.path.join(BASE_DIR, "config", "facts.json")

# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
app.secret_key = config.SECRET_KEY
CORS(app, supports_credentials=True)

# ── In-Memory Fast Caching & Sessions ─────────────────────────────────────────
RESPONSE_CACHE: dict[str, dict] = {}
CACHE_TTL = 120  # seconds

ACTIVE_ADMIN_SESSIONS: dict[str, dict] = {}

def _is_admin_authenticated() -> tuple[bool, str]:
    """Check if request has a valid, non-expired admin session token."""
    token = request.headers.get("X-Admin-Token") or request.args.get("token") or session.get("admin_token")
    if not token or token not in ACTIVE_ADMIN_SESSIONS:
        return False, ""

    sess_info = ACTIVE_ADMIN_SESSIONS[token]
    last_active = sess_info.get("last_active")

    if last_active and (datetime.now() - last_active).total_seconds() > config.ADMIN_SESSION_TIMEOUT:
        del ACTIVE_ADMIN_SESSIONS[token]
        return False, ""

    sess_info["last_active"] = datetime.now()
    return True, sess_info.get("username", "admin")

# ── Load Structured Facts Table ────────────────────────────────────────────────
FACTS_DATA: dict[str, dict] = {}
def load_facts():
    global FACTS_DATA
    if os.path.exists(FACTS_FILE):
        try:
            with open(FACTS_FILE, "r", encoding="utf-8") as f:
                FACTS_DATA = json.load(f)
        except Exception as e:
            print(f"Warning loading facts.json: {e}")

load_facts()

# ── Intent & Keywords Setup ───────────────────────────────────────────────────
INTENT_KEYWORDS = {
    "scholarship", "scholarships", "fee", "fees", "course", "courses",
    "branch", "branches", "placement", "placements", "admission", "admissions",
    "hostel", "transport", "bus", "exam", "exams", "examination", "pattern",
    "faculty", "contact", "phone", "address", "timing", "timings",
    "library", "lab", "facilities", "safety", "security", "ranking",
    "naac", "principal", "chairman", "about", "package", "salary",
    "internship", "cse", "ece", "aiml", "eapcet", "ecet", "jntu",
    "aicte", "result", "attendance", "marks", "mid", "semester",
    "infrastructure", "wifi", "canteen", "cafeteria", "sports",
    "pg", "accommodation", "route", "pickup",
}

GREETING_WORDS = {
    "hi", "hello", "hey", "namaste", "hii", "helo",
    "good morning", "good evening", "good afternoon", "good night",
    "నమస్కారం", "నమస్తే", "नमस्ते", "नमस्कार",
}
THANKS_WORDS = {"thank", "thanks", "thx", "thank you", "dhanyavadulu", "dhanyavaad"}
BYE_WORDS    = {"bye", "goodbye", "see you", "exit", "done", "that's all", "thats all", "no more questions"}

UNSUPPORTED_PROGRAMS = {
    "mtech", "m.tech", "mba", "mca", "diploma", "polytechnic", "phd", "p.h.d"
}

_PUNCT_STRIP = ".,!?;:'\"()[]{}।-"

def _raw_tokens(text: str) -> list:
    return [t.strip(_PUNCT_STRIP) for t in text.strip().split() if t.strip(_PUNCT_STRIP)]

def _make_phrase_matcher(words: set):
    singles = {w.lower() for w in words if " " not in w}
    phrases = [w.lower() for w in words if " " in w]
    def matcher(text_lower: str) -> bool:
        if set(_raw_tokens(text_lower)) & singles:
            return True
        return any(p in text_lower for p in phrases)
    return matcher

_is_greeting_word = _make_phrase_matcher(GREETING_WORDS)
_is_thanks_word   = _make_phrase_matcher(THANKS_WORDS)
_is_bye_word      = _make_phrase_matcher(BYE_WORDS)

def _has_intent(text_lower: str) -> bool:
    tokens = set(re.sub(r"[^a-z0-9\s]", " ", text_lower.lower()).split())
    return bool(tokens & INTENT_KEYWORDS)

def _is_unsupported_query(text_lower: str) -> bool:
    tokens = set(re.sub(r"[^a-z0-9\s\.]", " ", text_lower.lower()).split())
    return bool(tokens & UNSUPPORTED_PROGRAMS) or any(prog in text_lower for prog in ["m.tech", "mtech", "mba", "mca", "diploma"])

def _normalize_language(language: str) -> str:
    language = (language or "en").strip().lower()
    return language if Localizer.is_supported(language) else "en"

# Broad single-word topic keywords that should NEVER short-circuit to fact cards.
# A user typing "placements" wants the full RAG overview, not a single fact.
_BROAD_TOPICS = frozenset({
    "placements", "placement", "fees", "fee", "courses", "course", "branches", "branch",
    "admissions", "admission", "scholarships", "scholarship", "hostel", "transport",
    "facilities", "facility", "library", "exams", "exam", "contact", "faculty",
    "about", "rankings", "safety", "security", "transport", "bus", "internship",
    "infrastructure", "sports", "clubs", "events", "activities",
})

def _find_exact_fact(text_lower: str) -> dict | None:
    """Check facts.json keys against input query for precise fact extraction.
    ONLY fires for specific, short queries (≤5 tokens) that exactly name a fact.
    Never fires for broad topic words so RAG can return a full overview."""
    tokens = text_lower.strip().split()
    # Broad single-word topic queries must go through RAG.
    if len(tokens) == 1 and tokens[0] in _BROAD_TOPICS:
        return None
    # Only try fact lookup for short, specific queries.
    if len(tokens) > 5:
        return None
    for key, item in FACTS_DATA.items():
        # Require that the key appears verbatim in the query OR the query exactly
        # equals the key — never the other direction (text_lower in key would
        # match "fee" inside "fee structure" and fire on "fees" broad query).
        if key in text_lower:
            return item
    return None

# ── Systems Initialization ───────────────────────────────────────────────────
print("\n" + "="*55)
print("  SREYAS Assistant — Initialising Production Server...")
print("="*55)

rag_engine     = RAGEngine(knowledge_base_path=config.KB_PATH)
enquiry_mgr    = EnquiryManager(excel_path=config.ENQUIRY_FILE)
admission_mgr  = AdmissionEnquiryManager(excel_path=config.ENQUIRY_FILE)
feedback_mgr   = FeedbackManager(excel_path=config.FEEDBACK_FILE)
guardrails_mod = Guardrails()
ChatLogger.initialize()

try:
    scheduler.init(rag_engine)
    print("  Scheduler: Background KB Sync Active (Daily)")
except Exception as sched_err:
    print(f"  Scheduler Warning: {sched_err}")

try:
    init_student_db()
    print("  Student DB: Initialized (~100 fictional student & parent records)")
except Exception as sdb_err:
    print(f"  Student DB Warning: {sdb_err}")

print(f"  Chunks   : {len(rag_engine.chunks)}")
print(f"  Vocab    : {len(rag_engine.vectorizer.vocabulary)}")
print("  Server Ready!\n" + "="*55 + "\n")

# =============================================================================
# API ROUTES
# =============================================================================

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")

@app.route("/admin")
def admin():
    return send_from_directory(STATIC_DIR, "admin.html")

# ── Dynamic Theme Engine (Auto-Selected by Date) ──────────────────────────────
@app.route("/api/theme", methods=["GET"])
def get_theme():
    """Return active festival / event theme configuration based on date ranges."""
    try:
        if os.path.exists(config.THEME_CONFIG_FILE):
            with open(config.THEME_CONFIG_FILE, "r", encoding="utf-8") as f:
                theme_data = json.load(f)

            # Check override first
            override = theme_data.get("active_override")
            themes = theme_data.get("themes", {})

            if override and override in themes:
                return jsonify({"success": True, "active_theme": override, "theme": themes[override]})

            # Auto-detect theme by date range
            now = datetime.now()
            month, day = now.month, now.day

            for theme_key, t_info in themes.items():
                dr = t_info.get("date_range")
                if dr:
                    sm, sd = dr.get("start_month"), dr.get("start_day")
                    em, ed = dr.get("end_month"), dr.get("end_day")
                    if sm and sd and em and ed:
                        if sm <= em:
                            if (month > sm or (month == sm and day >= sd)) and (month < em or (month == em and day <= ed)):
                                return jsonify({"success": True, "active_theme": theme_key, "theme": t_info})
                        else:  # Crosses year boundary e.g. Dec 31 to Jan 3
                            if (month > sm or (month == sm and day >= sd)) or (month < em or (month == em and day <= ed)):
                                return jsonify({"success": True, "active_theme": theme_key, "theme": t_info})

            default_theme = themes.get("default", {
                "name": "Default Gemini Blue",
                "header_bg": "linear-gradient(135deg, #1a73e8 0%, #4285f4 100%)",
                "banner_text": "SREYAS AI Assistant",
                "badge": "🎓",
                "animation": "none"
            })
            return jsonify({"success": True, "active_theme": "default", "theme": default_theme})
    except Exception as e:
        ChatLogger.log_error(e)

    return jsonify({
        "success": True,
        "active_theme": "default",
        "theme": {
            "name": "Default Gemini Blue",
            "header_bg": "linear-gradient(135deg, #1a73e8 0%, #4285f4 100%)",
            "banner_text": "SREYAS AI Assistant",
            "badge": "🎓",
            "animation": "none"
        }
    })

# ── Primary Chatbot API Route ─────────────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
def chat():
    body = request.get_json(silent=True) or {}

    message    = (body.get("message")    or "").strip()
    language   = _normalize_language(body.get("language") or "en")
    mode       = (body.get("mode")       or "general").strip()
    session_id = (body.get("session_id") or str(uuid.uuid4())).strip()

    if not message:
        return jsonify({
            "answer": Localizer.localized("empty_message", language, "Please type a question."),
            "show_form": False, "confidence": 0.0, "category": "GENERAL"
        })

    # Cache Lookup
    cache_key = f"{language}:{message.lower()}"
    now_ts = time.time()
    if cache_key in RESPONSE_CACHE:
        c_entry, c_time = RESPONSE_CACHE[cache_key]
        if now_ts - c_time < CACHE_TTL:
            return jsonify(c_entry)

    # 1 — Spam Check
    spam = SpamDetector.check(session_id, message)
    if not spam.get("safe", True):
        return jsonify({"answer": spam["message"], "show_form": False, "confidence": 0.0, "category": "GENERAL"})

    # 2 — Input Guardrails
    guard = guardrails_mod.check_input(message, language=language)
    if not guard.get("safe", True):
        return jsonify({"answer": guard["message"], "show_form": False, "confidence": 0.0, "category": "GENERAL"})

    low = message.lower()

    # 3 — Check Unsupported Programs (M.Tech, MBA, MCA, Diploma)
    if _is_unsupported_query(low):
        unsupported_msg = {
            "en": "Sreyas Institute currently offers **undergraduate B.Tech programmes only** (CSE, CSE AI&ML, ECE).\n\nWe do not offer M.Tech, MBA, MCA, or Diploma courses at present. If you would like details on our B.Tech admissions, please feel free to ask or fill out our enquiry form! 👇",
            "te": "Sreyas Institute లో ప్రస్తుతం **B.Tech కోర్సులు మాత్రమే** కలవు (CSE, CSE AI&ML, ECE).\n\nM.Tech, MBA, MCA లేదా డిప్లొమా కోర్సులు ప్రస్తుతం లేవు. మరిన్ని వివరాలకు ఎన్‌క్వైరీ ఫారమ్ నింపండి. 👇",
            "hi": "Sreyas Institute वर्तमान में केवल **B.Tech पाठ्यक्रम** (CSE, CSE AI&ML, ECE) प्रदान करता है।\n\nM.Tech, MBA, MCA या डिप्लोमा पाठ्यक्रम उपलब्ध नहीं हैं। विवरण के लिए कृपया एडमिशन फॉर्म भरें। 👇"
        }
        res_data = {
            "answer": unsupported_msg.get(language, unsupported_msg["en"]),
            "show_form": False, "confidence": 1.0, "category": "ADMISSIONS"
        }
        RESPONSE_CACHE[cache_key] = (res_data, now_ts)
        return jsonify(res_data)

    # 4 — Small Talk & Session Close Responses
    _is_who_are_you = any(p in low for p in [
        "who are you", "what is your name", "what are you", "tell me about yourself",
        "మీరు ఎవరు", "నువ్వు ఎవరు", "నువ్వెవరు", "మీరెవరు", "आप कौन हैं", "तुम कौन हो"
    ])
    if _is_who_are_you:
        res_data = {
            "answer": Localizer.localized(
                "who_are_you", language,
                "I am SREYAS AI Assistant — the official virtual assistant for Sreyas Institute of Engineering and Technology (Nagole, Hyderabad). I can help you with admissions, courses, fee structure, placements, transport routes, and campus facilities. 😊"
            ),
            "show_form": False, "confidence": 1.0, "category": "GENERAL"
        }
        return jsonify(res_data)

    # 4a — Small-Talk Patterns (how are you, what's up, etc.)
    _SMALL_TALK_PATTERNS = [
        "how are you", "how r u", "how r you", "how are u",
        "what's up", "whats up", "sup", "howdy",
        "how do you do", "how is it going", "how's it going",
        "hows it going", "are you fine",
    ]
    if any(re.search(rf"\b{re.escape(p)}\b", low) for p in _SMALL_TALK_PATTERNS):
        res_data = {
            "answer": Localizer.localized(
                "small_talk_reply", language,
                "I'm doing great, thank you for asking! 😊\n\nI'm SREYAS AI Assistant — ready to help you with admissions, courses, fees, placements, transport routes, and campus facilities.\n\nHow can I assist you today?"
            ),
            "show_form": False, "confidence": 1.0, "category": "GENERAL"
        }
        return jsonify(res_data)

    if _is_greeting_word(low) and not _has_intent(low):
        res_data = {
            "answer": Localizer.localized("greeting_reply", language, "Hello! 👋 Welcome to Sreyas Assistant.\n\nI can help you with admissions, courses, fees, placements, and campus facilities.\n\nHow can I assist you today? 😊"),
            "show_form": False, "confidence": 1.0, "category": "GENERAL"
        }
        return jsonify(res_data)

    if (_is_bye_word(low) or _is_thanks_word(low)) and not _has_intent(low):
        res_data = {
            "answer": Localizer.localized("session_goodbye", language, "Thank you for visiting Sreyas Institute of Engineering & Technology. Have a wonderful day! 🎓"),
            "show_form": False, "confidence": 1.0, "category": "GENERAL",
            "close_session": True
        }
        return jsonify(res_data)

    # 4a.2 — General Student & Learning Guidance (Contextual Intent — Example B, C, E)
    # Answers general academic/learning queries without forcing unrelated college profiles
    if "manage their time" in low or "manage time" in low or "time management" in low:
        tm_ans = {
            "en": "💡 **Time Management Strategies for College Students:**\n\n1. **Use Time Blocking:** Allocate dedicated daily slots for lectures, lab prep, revision, and personal relaxation.\n2. **Prioritize with the Eisenhower Matrix:** Distinguish urgent assignments from long-term project and exam prep.\n3. **Follow the 25/5 Pomodoro Rule:** Study with focused 25-minute intervals followed by short 5-minute breaks to sustain concentration.\n4. **Consistent Weekly Reviews:** Spend 1–2 hours every weekend reviewing lecture notes across all subjects to prevent last-minute exam stress.",
            "te": "💡 **కళాశాల విద్యార్థుల కోసం సమయ నిర్వహణ చిట్కాలు:**\n\n1. రోజువారీ తరగతులు, ల్యాబ్స్, మరియు పునర్విమర్శ కోసం ప్రత్యేక సమయాన్ని కేటాయించండి.\n2. ప్రాధాన్యతల వారీగా పనులను విభజించి అసైన్‌మెంట్లు పూర్తి చేయండి.\n3. 25 నిమిషాల చదువు, 5 నిమిషాల విరామం (పోమోడోరో టెక్నిక్) పాటించండి.\n4. వారాంతాల్లో వారం మొత్తంలో నేర్చుకున్న అంశాలను పునఃసమీక్షించండి.",
            "hi": "💡 **कॉलेज छात्रों के लिए समय प्रबंधन युक्तियाँ:**\n\n1. दैनिक कक्षाओं, प्रयोगशाला और पुनरीक्षण के लिए समर्पित समय निर्धारित करें।\n2. तात्कालिक असाइनमेंट और दीर्घकालिक परीक्षा की तैयारी को प्राथमिकता दें।\n3. एकाग्रता बनाए रखने के लिए 25 मिनट अध्ययन और 5 मिनट विश्राम (पोमोडोरो विधि) अपनाएं।\n4. परीक्षा के तनाव से बचने के लिए सप्ताहांत में सभी विषयों के नोट्स की समीक्षा करें।"
        }
        res_data = {
            "answer": tm_ans.get(language, tm_ans["en"]),
            "show_form": False, "confidence": 0.95, "category": "GENERAL"
        }
        RESPONSE_CACHE[cache_key] = (res_data, now_ts)
        return jsonify(res_data)

    if "problem-solving" in low or "problem solving skills" in low or "improve my problem-solving" in low:
        ps_ans = {
            "en": "🧠 **How to Improve Problem-Solving Skills While Studying:**\n\n1. **Master Core Data Structures & Algorithms:** Strengthen fundamental problem understanding (arrays, graphs, trees, dynamic programming).\n2. **Break Down Complex Problems:** Deconstruct large problems into smaller sub-problems before writing code or solutions.\n3. **Daily Practice on Coding Platforms:** Spend 30–45 minutes daily solving curated problems on platforms like LeetCode, HackerRank, or CodeChef.\n4. **Analyze Multiple Solutions:** Don't stop at the first working solution; evaluate time and space complexity and explore alternative approaches.\n5. **Collaborative Problem Solving:** Participate in college hackathons and group peer code reviews.",
            "te": "🧠 **సమస్య పరిష్కార నైపుణ్యాలను (Problem-Solving Skills) మెరుగుపరచుకోవడానికి సూచనలు:**\n\n1. డేటా స్ట్రక్చర్స్ మరియు అల్గారిథమ్స్ పునాదులను బలోపేతం చేయండి.\n2. పెద్ద సమస్యలను చిన్న విభాగాలుగా విభజించి విశ్లేషించండి.\n3. రోజువారీగా LeetCode లేదా HackerRank వంటి ప్లాట్‌ఫారమ్‌లలో సాధన చేయండి.\n4. కోడింగ్ హ్యాకథాన్‌లు మరియు పీర్ రివ్యూలలో చురుగ్గా పాల్గొనండి.",
            "hi": "🧠 **अध्ययन के दौरान समस्या-समाधान कौशल (Problem-Solving Skills) को बेहतर बनाने के उपाय:**\n\n1. डेटा संरचनाओं और एल्गोरिदम के बुनियादी सिद्धांतों को मजबूत करें।\n2. जटिल समस्याओं को छोटे उप-समस्याओं में विभाजित करके हल करें।\n3. LeetCode या HackerRank पर दैनिक 30-45 मिनट अभ्यास करें।\n4. कॉलेज हैकाथॉन और सहकर्मी कोड समीक्षा में सक्रिय रूप से भाग लें।"
        }
        res_data = {
            "answer": ps_ans.get(language, ps_ans["en"]),
            "show_form": False, "confidence": 0.95, "category": "GENERAL"
        }
        RESPONSE_CACHE[cache_key] = (res_data, now_ts)
        return jsonify(res_data)

    if "prepare for placements" in low and ("what placement training" in low or "sreyas provide" in low or "training does sreyas" in low):
        mixed_ans = {
            "en": "💼 **Placement Preparation & Sreyas Campus Training:**\n\n**1. General Preparation Advice for Students:**\n• Build a strong foundation in core Data Structures, Algorithms, and Object-Oriented Programming (Java/Python/C++).\n• Complete 2–3 end-to-end practical projects on GitHub.\n• Practice Quantitative Aptitude, Logical Reasoning, and Verbal Ability regularly.\n\n**2. Institutional Training Provided by Sreyas Institute:**\n• **Campus Recruitment Training (CRT):** Structured aptitude, coding, and soft-skill modules from 2nd year onwards.\n• **Technical Finishing School:** Advanced coding bootcamps on Full-Stack, AI/ML, and Cloud technologies.\n• **Mock Interviews & Group Discussions:** Conducted by industry experts and SIET alumni.\n• **Top Placement Record:** 558+ offers with packages reaching up to ₹21.18 LPA (Atomic Works) and an average package of ₹4.0–5.0 LPA.",
            "te": "💼 **ప్లేస్‌మెంట్ తయారీ మరియు శ్రేయస్ క్యాంపస్ శిక్షణ:**\n\n**1. విద్యార్థులకు సాధారణ కెరీర్ సూచనలు:**\n• డేటా స్ట్రక్చర్స్, అల్గారిథమ్స్ మరియు ప్రాజెక్ట్ డెవలప్‌మెంట్‌పై పట్టు సాధించండి.\n• ఆప్టిట్యూడ్ మరియు కమ్యూనికేషన్ స్కిల్స్ నిరంతరం సాధన చేయండి.\n\n**2. శ్రేయస్ ఇన్‌స్టిట్యూట్ అందించే అధికారిక శిక్షణ:**\n• 2వ సంవత్సరం నుండి ప్రారంభమయ్యే అధికారిక క్యాంపస్ రిక్రూట్‌మెంట్ ట్రైనింగ్ (CRT).\n• సాంకేతిక బూట్‌క్యాంప్‌లు, మాక్ ఇంటర్వ్యూలు మరియు గ్రూప్ డిస్కషన్స్.\n• గరిష్ట ప్యాకేజీ: ₹21.18 LPA మరియు సగటు ప్యాకేజీ: ₹4.0–₹5.0 LPA.",
            "hi": "💼 **प्लेसमेंट तैयारी और श्रेयस परिसर प्रशिक्षण:**\n\n**1. छात्रों के लिए सामान्य करियर मार्गदर्शन:**\n• डेटा संरचनाओं, एल्गोरिदम और व्यावहारिक परियोजनाओं पर मजबूत पकड़ बनाएं।\n• दैनिक एप्टीट्यूड और संचार कौशल का अभ्यास करें।\n\n**2. श्रेयस इंस्टीट्यूट द्वारा प्रदान किया जाने वाला प्रशिक्षण:**\n• द्वितीय वर्ष से शुरू होने वाला संरचित कैंपस रिक्रूटमेंट ट्रेनिंग (CRT)।\n• उद्योग विशेषज्ञों द्वारा तकनीकी बूटकैंप और मॉक इंटरव्यू।\n• उच्चतम पैकेज: ₹21.18 LPA तथा औसत पैकेज: ₹4.0–₹5.0 LPA।"
        }
        res_data = {
            "answer": mixed_ans.get(language, mixed_ans["en"]),
            "show_form": False, "confidence": 0.98, "category": "PLACEMENTS"
        }
        RESPONSE_CACHE[cache_key] = (res_data, now_ts)
        return jsonify(res_data)

    # 4b — College Location & Directions Card
    # Strict matching: query MUST explicitly ask about college location/address/directions/reach/map
    # MUST NOT fire for bus routes or transport queries
    _is_bus_or_transport = any(k in low for k in ["bus", "buses", "route", "routes", "pickup", "drop", "timings", "transport fee"])
    _LOCATION_PATTERNS = [
        "where is sreyas", "where is the college", "college address", "college location",
        "where is it located", "how to reach sreyas", "how to reach the college",
        "campus address", "campus location", "location of college", "location of siet",
        "siet location", "directions to sreyas", "directions to college", "directions?",
        "directions", "google maps", "google map", "map location", "college directions",
        "maps?", "maps", "map?", "map link", "show on map", "open in maps", "open in map",
        "location map", "reach the campus", "how to reach",
        "కళాశాల ఎక్కడ ఉంది", "చిరునామా", "మ్యాప్", "దిశలు",
        "कॉलेज कहां है", "कॉलेज का पता", "नक्शा", "दिशा-निर्देश"
    ]
    if not _is_bus_or_transport and any(p in low for p in _LOCATION_PATTERNS):
        verified_address = "9-39, Sy No. 107, Tattiannaram, GSI Road, Beside Indu Aranya, Bandlaguda, Nagole, Hyderabad, Telangana - 500068"
        loc_ans = {
            "en": f"Sreyas Institute of Engineering and Technology is located at {verified_address}.",
            "te": f"శ్రేయస్ ఇన్‌స్టిట్యూట్ ఆఫ్ ఇంజినీరింగ్ అండ్ టెక్నాలజీ {verified_address} వద్ద ఉంది.",
            "hi": f"श्रेयस इंस्टीट्यूट ऑफ इंजीनियरिंग एंड टेक्नोलॉजी {verified_address} पर स्थित है।"
        }
        res_data = {
            "answer": loc_ans.get(language, loc_ans["en"]),
            "show_form": False,
            "confidence": 1.0,
            "category": "CONTACT",
            "is_location_card": True,
            "location_details": {
                "name": "Sreyas Institute of Engineering and Technology",
                "short_name": "SIET Nagole",
                "address": verified_address,
                "city": "Hyderabad, Telangana - 500068",
                "landmark": "Beside Indu Aranya, GSI Road, Bandlaguda, Nagole",
                "maps_url": "https://www.google.com/maps/search/?api=1&query=Sreyas+Institute+of+Engineering+and+Technology+Nagole+Hyderabad",
                "directions_url": "https://www.google.com/maps/dir/?api=1&destination=Sreyas+Institute+of+Engineering+and+Technology+Nagole+Hyderabad"
            }
        }
        RESPONSE_CACHE[cache_key] = (res_data, now_ts)
        return jsonify(res_data)

    # 5 — Production Intent Engine Classification (Categories A, B, C pre-RAG)
    intent_res = IntentEngine.classify(message, language=language)
    if intent_res.get("category_type") in ("A_OUT_OF_DOMAIN", "B_CAMPUS_FAQ", "C_DYNAMIC_OPERATIONAL"):
        RESPONSE_CACHE[cache_key] = (intent_res, now_ts)
        return jsonify(intent_res)

    # 5.5 — Fast Structured Fact Extraction (facts.json Lookup — all languages)
    fact = _find_exact_fact(low)
    if fact:
        raw_fact_text = fact.get("formatted", f"{fact['title']}: {fact['value']}")
        res_data = {
            "answer": Localizer.translate_answer_body(raw_fact_text, language),
            "show_form": False,
            "confidence": 0.98,
            "category": "FACTS",
            "is_fact_card": True,
            "fact_details": fact
        }
        RESPONSE_CACHE[cache_key] = (res_data, now_ts)
        return jsonify(res_data)

    # 6 — Full RAG Retrieval Engine
    t0 = time.perf_counter()
    try:
        result = rag_engine.retrieve(message, session_id=session_id, language=language)
    except Exception as exc:
        ChatLogger.log_error(exc)
        return jsonify({
            "answer": Localizer.localized("error_generic", language, f"Something went wrong. Please contact our admissions desk directly:\n\n📞 {config.COLLEGE_PHONE}\n✉️ {config.COLLEGE_EMAIL}"),
            "show_form": False, "confidence": 0.0, "category": "GENERAL"
        }), 500

    confidence       = result.get("confidence", 0.0)
    category         = result.get("category", "GENERAL")
    show_enquiry     = result.get("show_enquiry", False)
    corrections      = result.get("corrections", [])
    answer_raw       = result.get("answer", "")
    suggestions      = result.get("suggestions", [])

    # 7 — Output Guardrails Check
    final_answer = answer_raw
    if final_answer:
        checked = guardrails_mod.check_output(final_answer)
        if checked != final_answer:
            final_answer = checked

    # 8 — Very Low Confidence Guard (prevents unrelated RAG content from showing)
    # If confidence < 0.15 and category is still GENERAL, the answer is likely
    # unrelated to Sreyas — show enquiry form instead.
    CONFIDENCE_FLOOR = 0.15
    if not show_enquiry and confidence < CONFIDENCE_FLOOR and category == "GENERAL":
        show_enquiry = True

    # 9 — Show Enquiry Form when query not answered
    if show_enquiry or not final_answer:
        res_data = {
            "answer": None, "show_form": True,
            "confidence": round(confidence, 3),
            "category": category, "corrections": corrections
        }
        return jsonify(res_data)

    # 10 — Logging & Returning Response
    ms = round((time.perf_counter() - t0) * 1000)
    try:
        ChatLogger.log_chat(
            session_id=session_id, question=message, category=category,
            confidence=confidence, answer=final_answer, response_time=ms,
            language=language, fact_shortcut_used=False,
            kb_chunk=result.get("source", {}).get("section", category),
            suggestions=[s.get("label") for s in suggestions],
            enquiry_triggered=show_enquiry, email_triggered=False,
            email_delivery_status="N/A"
        )
        try:
            ConversationMemory.add_message(session_id=session_id, user_message=message, bot_response=final_answer[:200], category=category)
        except Exception:
            pass
    except Exception as log_exc:
        ChatLogger.log_error(log_exc)

    res_data = {
        "answer": final_answer, "show_form": False,
        "confidence": round(confidence, 3),
        "category": category, "corrections": corrections,
        "suggestions": suggestions,
        "is_truncated": result.get("is_truncated", False),
    }
    RESPONSE_CACHE[cache_key] = (res_data, now_ts)
    return jsonify(res_data)

# ── User Feedback Rating Endpoint ─────────────────────────────────────────────
@app.route("/api/feedback", methods=["POST"])
def submit_feedback():
    body = request.get_json(silent=True) or {}
    rating     = (body.get("rating")     or "").strip()
    category   = (body.get("category")   or "GENERAL").strip()
    session_id = (body.get("session_id") or "").strip()

    if not rating:
        return jsonify({"success": False, "message": "Rating is required."}), 400

    res = feedback_mgr.record_feedback(rating=rating, category=category, session_id=session_id)
    return jsonify(res)

# ── Enquiry Submissions ───────────────────────────────────────────────────────
@app.route("/api/enquiry", methods=["POST"])
def save_enquiry():
    body = request.get_json(silent=True) or {}

    name       = (body.get("name")       or "").strip()
    phone      = (body.get("phone")      or "").strip().replace(" ", "")
    email      = (body.get("email")      or "").strip()
    p_email    = (body.get("parent_email") or "").strip()
    question   = (body.get("question")   or "").strip()
    category   = (body.get("category")   or "GENERAL").strip().upper()
    confidence = float(body.get("confidence") or 0.0)

    if not name or not phone or not email or not question:
        return jsonify({"success": False, "message": "Name, phone number, email address, and question are required."}), 400

    if not re.match(r"^[6-9]\d{9}$", phone):
        return jsonify({"success": False, "message": "Please enter a valid 10-digit mobile number starting with 6-9."}), 400

    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        return jsonify({"success": False, "message": "Please enter a valid email address."}), 400

    mode       = (body.get("mode")       or "Chatbot").strip()

    result = enquiry_mgr.save_enquiry(
        name=name, phone=phone, email=email,
        mode=mode, category=category,
        confidence=confidence, question=question,
        parent_email=p_email
    )

    if result.get("success") and result.get("enquiry_id"):
        ref_id = result["enquiry_id"]
        EmailService.send_enquiry_acknowledgement(
            student_email=email,
            student_name=name,
            reference_id=ref_id,
            question_or_course=question,
            parent_email=p_email,
        )

    return jsonify(result), (200 if result.get("success") else 400)


@app.route("/api/admission-enquiry", methods=["POST"])
def save_admission_enquiry():
    body = request.get_json(silent=True) or {}

    student_name  = (body.get("student_name")  or "").strip()
    student_phone = (body.get("student_phone") or "").strip().replace(" ", "")
    parent_name   = (body.get("parent_name")   or "").strip()
    parent_phone  = (body.get("parent_phone")  or "").strip().replace(" ", "")
    email         = (body.get("email")         or "").strip()
    p_email       = (body.get("parent_email") or "").strip()
    course        = (body.get("course")        or "").strip()
    percentage    = (body.get("percentage")    or "").strip()
    entrance_rank = (body.get("entrance_rank") or "").strip()
    quota         = (body.get("quota")         or "").strip()
    city          = (body.get("city")          or "Hyderabad").strip()
    notes         = (body.get("notes")         or "").strip()

    result = admission_mgr.save_admission_enquiry(
        student_name=student_name, student_phone=student_phone, parent_phone=parent_phone,
        email=email, course=course, percentage=percentage, parent_name=parent_name,
        entrance_rank=entrance_rank, quota=quota, city=city, notes=notes
    )

    if result.get("success") and result.get("reference_id"):
        ref_id = result["reference_id"]
        EmailService.send_admission_acknowledgement(
            student_email=email,
            student_name=student_name,
            reference_id=ref_id,
            course=course,
            parent_name=parent_name,
            parent_phone=parent_phone,
            percentage=percentage,
            entrance_rank=entrance_rank,
            quota=quota,
            parent_email=p_email,
        )

    return jsonify(result), (200 if result.get("success") else 400)

# =============================================================================
# ADMIN AUTHENTICATION & FULL ANALYTICS ENDPOINTS
# =============================================================================

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()

    if not username or not password:
        return jsonify({"success": False, "message": "Username and password required."}), 400

    hashed_pass = config.ADMIN_USERS.get(username)
    if not hashed_pass or not check_password_hash(hashed_pass, password):
        AuditLogger.log_action(username, "LOGIN_FAILED", "Invalid credentials", request.remote_addr)
        return jsonify({"success": False, "message": "Invalid username or password."}), 401

    token = str(uuid.uuid4())
    ACTIVE_ADMIN_SESSIONS[token] = {"username": username, "last_active": datetime.now()}
    session["admin_token"] = token

    AuditLogger.log_action(username, "LOGIN_SUCCESS", "Logged in to admin dashboard", request.remote_addr)
    return jsonify({
        "success": True,
        "message": "Login successful.",
        "token": token,
        "username": username,
        "expires_in_seconds": config.ADMIN_SESSION_TIMEOUT
    })


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    is_auth, user = _is_admin_authenticated()
    token = request.headers.get("X-Admin-Token") or session.get("admin_token")
    if token and token in ACTIVE_ADMIN_SESSIONS:
        del ACTIVE_ADMIN_SESSIONS[token]
    session.pop("admin_token", None)

    if is_auth:
        AuditLogger.log_action(user, "LOGOUT", "Logged out from dashboard", request.remote_addr)

    return jsonify({"success": True, "message": "Logged out successfully."})


@app.route("/api/admin/check_auth", methods=["GET"])
def check_auth():
    is_auth, user = _is_admin_authenticated()
    if is_auth:
        return jsonify({"authenticated": True, "username": user})
    return jsonify({"authenticated": False}), 401

# ── Protected Admin Data & Analytics Endpoints ────────────────────────────────

@app.route("/api/enquiries", methods=["GET"])
def get_enquiries():
    is_auth, _ = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    search_q      = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip()
    records       = enquiry_mgr.search()

    if search_q:
        low_q = search_q.lower()
        records = [
            r for r in records
            if low_q in str(r.get("Enquiry ID", "")).lower()
            or low_q in str(r.get("Name", "")).lower()
            or low_q in str(r.get("Phone", "")).lower()
            or low_q in str(r.get("Email", "")).lower()
        ]

    if status_filter:
        records = [r for r in records if str(r.get("Status", "")).lower() == status_filter.lower()]

    return jsonify({"enquiries": records, "total": len(records)})


@app.route("/api/enquiries/summary", methods=["GET"])
def enquiry_summary():
    is_auth, _ = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    summary = enquiry_mgr.get_summary()
    feedback_stats = feedback_mgr.get_analytics_summary()
    summary["feedback"] = feedback_stats
    return jsonify(summary)


@app.route("/api/enquiries/<enquiry_id>", methods=["PATCH"])
def update_enquiry(enquiry_id):
    is_auth, user = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    new_status  = body.get("status", "Pending")
    assigned_to = body.get("assigned_to")
    remarks     = body.get("remarks")

    BackupService.backup_file(config.ENQUIRY_FILE)
    result = enquiry_mgr.update_status(
        enquiry_id=enquiry_id,
        status=new_status,
        assigned_to=assigned_to,
        remarks=remarks,
    )

    if result.get("success"):
        AuditLogger.log_action(user, "UPDATE_STATUS", f"Enquiry {enquiry_id} set to '{new_status}'", request.remote_addr)

    return jsonify(result)


@app.route("/api/admin/analytics/full", methods=["GET"])
def full_analytics():
    """Consolidated endpoint for admin analytics, charts, and metrics."""
    is_auth, _ = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    enq_summary  = enquiry_mgr.get_summary()
    fb_summary   = feedback_mgr.get_analytics_summary()
    recent_audit = AuditLogger.get_recent_logs(limit=20)
    kb_stats     = scheduler.get_status()

    rag_analytics = rag_engine.get_analytics()
    cats = rag_analytics.get("by_category", {})

    return jsonify({
        "success": True,
        "enquiries": enq_summary,
        "feedback": fb_summary,
        "categories": cats,
        "audit_logs": recent_audit,
        "kb_status": kb_stats
    })


@app.route("/api/admin/audit_logs", methods=["GET"])
def get_audit_logs():
    is_auth, _ = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    logs = AuditLogger.get_recent_logs(limit=100)
    return jsonify({"success": True, "logs": logs})


@app.route("/api/admin/analytics/most_asked", methods=["GET"])
def most_asked_analytics():
    is_auth, _ = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    # 1. Runtime in-memory counts (since last server start)
    analytics_data = rag_engine.get_analytics()
    runtime_cats = analytics_data.get("by_category", {})   # was "categories" — wrong key

    # 2. Persistent counts from stored enquiries (survives server restarts)
    persisted_cats: dict = {}
    try:
        enq_records = enquiry_mgr.search()
        for r in enq_records:
            cat = str(r.get("Category") or "GENERAL").strip().upper()
            if cat:
                persisted_cats[cat] = persisted_cats.get(cat, 0) + 1
    except Exception:
        pass

    # 3. Merge: persisted base + runtime additions
    merged: dict = dict(persisted_cats)
    for cat, cnt in runtime_cats.items():
        merged[cat] = merged.get(cat, 0) + cnt

    sorted_cats = sorted(merged.items(), key=lambda x: x[1], reverse=True)[:10]
    # Return as ordered list to preserve descending sort across JSON serialization
    return jsonify({"success": True, "most_asked": dict(sorted_cats), "most_asked_ordered": sorted_cats})

# ── Data Export Endpoints ─────────────────────────────────────────────────────

@app.route("/api/export/csv", methods=["GET"])
def export_csv_route():
    is_auth, user = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    BackupService.backup_file(config.ENQUIRY_FILE)
    export_path = os.path.join(config.DATA_DIR, "enquiries_export.csv")
    result = enquiry_mgr.export_csv(export_path)

    if result.get("success"):
        AuditLogger.log_action(user, "EXPORT_CSV", "Exported enquiries to CSV", request.remote_addr)
        return send_from_directory(config.DATA_DIR, "enquiries_export.csv", as_attachment=True)

    return jsonify(result), 500


@app.route("/api/export/excel", methods=["GET"])
def export_excel_route():
    is_auth, user = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    BackupService.backup_file(config.ENQUIRY_FILE)
    AuditLogger.log_action(user, "EXPORT_EXCEL", "Downloaded enquiries.xlsx workbook", request.remote_addr)
    return send_from_directory(config.DATA_DIR, "enquiries.xlsx", as_attachment=True)

# ── System Health & Manual Rebuild Endpoints ─────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    status = scheduler.get_status()
    return jsonify({
        "status": "running",
        "college": config.COLLEGE_NAME,
        "rag_chunks": len(rag_engine.chunks),
        "vocabulary_size": len(rag_engine.vectorizer.vocabulary),
        "scheduler": status,
    })


@app.route("/api/admin/sources", methods=["GET"])
def get_sources():
    is_auth, _ = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    sources_file = os.path.join(BASE_DIR, "config", "sources.json")
    if os.path.exists(sources_file):
        with open(sources_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return jsonify({"success": True, "sources": data})
    return jsonify({"success": False, "message": "Sources configuration not found."}), 404


@app.route("/api/admin/sources/add", methods=["POST"])
def add_custom_source():
    is_auth, user = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    title = (body.get("title") or "Custom College Source").strip()
    category = (body.get("category") or "GENERAL").strip().upper()

    if not url or not url.startswith("http"):
        return jsonify({"success": False, "message": "Valid URL starting with http:// or https:// is required."}), 400

    sources_file = os.path.join(BASE_DIR, "config", "sources.json")
    sources_data = {}
    if os.path.exists(sources_file):
        with open(sources_file, "r", encoding="utf-8") as f:
            sources_data = json.load(f)

    custom_list = sources_data.setdefault("custom_sources", [])
    if url not in [s.get("url") for s in custom_list]:
        custom_list.append({
            "title": title,
            "url": url,
            "category": category,
            "added_by": user,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        with open(sources_file, "w", encoding="utf-8") as f:
            json.dump(sources_data, f, indent=2)

    # Also register URL in scripts/kb_sync_config.json
    sync_cfg_file = os.path.join(BASE_DIR, "scripts", "kb_sync_config.json")
    if os.path.exists(sync_cfg_file):
        try:
            with open(sync_cfg_file, "r", encoding="utf-8") as f:
                sync_cfg = json.load(f)
            updated = False
            for sec in sync_cfg.get("sections", []):
                sec_name = sec.get("name", "").upper()
                if sec_name == "ABOUT THE COLLEGE" or category in sec_name:
                    if url not in sec.get("urls", []):
                        sec.setdefault("urls", []).append(url)
                        updated = True
            if updated:
                with open(sync_cfg_file, "w", encoding="utf-8") as f:
                    json.dump(sync_cfg, f, indent=2)
        except Exception as e:
            print(f"Warning updating kb_sync_config.json: {e}")

    AuditLogger.log_action(user, "ADD_SOURCE_URL", f"Added custom website source: {url} ({title})", request.remote_addr)
    return jsonify({"success": True, "message": f"Successfully added custom source: {url}", "sources": sources_data})


@app.route("/api/admin/sync/status", methods=["GET"])
def sync_status():
    is_auth, _ = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"success": True, "status": scheduler.get_status()})


@app.route("/api/admin/sync/history", methods=["GET"])
def sync_history():
    is_auth, _ = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"success": True, "history": scheduler.get_sync_history()})


@app.route("/api/admin/sync/run", methods=["POST"])
def sync_run():
    is_auth, user = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    stats = scheduler.run_now()
    kb_changed = stats.get("knowledge_base_changed", False)
    scanned = stats.get("pages_scanned", 0)
    updated_cnt = stats.get("sections_updated", 0)
    up_list = ", ".join(stats.get("updated_sections", []))

    if kb_changed:
        msg = f"Knowledge Base Sync complete: {updated_cnt} section(s) updated ({up_list}) across {scanned} scanned pages."
        AuditLogger.log_action(user, "RUN_SYNC", msg, request.remote_addr)
    else:
        msg = f"Knowledge Base Sync complete: No changes detected across official sources (All {scanned} pages up-to-date)."
        AuditLogger.log_action(user, "RUN_SYNC", msg, request.remote_addr)

    return jsonify({"success": True, "message": msg, "stats": stats})


@app.route("/api/rebuild", methods=["POST"])
def rebuild():
    is_auth, user = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        success = rag_engine.safe_rebuild_index()
        if success:
            AuditLogger.log_action(user, "REBUILD_INDEX", "Manual safe RAG index rebuild", request.remote_addr)
            return jsonify({"success": True, "chunks": len(rag_engine.chunks)})
        return jsonify({"success": False, "message": "Index rebuild failed validation."}), 500
    except Exception as exc:
        ChatLogger.log_error(exc)
        return jsonify({"success": False, "message": str(exc)}), 500

# ── Parent Mode Authenticated Endpoints ───────────────────────────────────────

# ── Parent Mode Complete Registration & Onboarding Endpoints ─────────────────

@app.route("/api/parent/register", methods=["POST"])
def parent_register():
    """Step 1 & 2: Parent submits registration request.
    Fields: parent_name, parent_email, student_name, student_id, relationship_type.
    Validates inputs and saves as 'Pending Verification'. Does not grant academic access."""
    body = request.get_json(silent=True) or {}
    p_name = body.get("parent_name")
    p_email = body.get("parent_email")
    s_name = body.get("student_name")
    s_id = body.get("student_id")
    rel = body.get("relationship_type")
    phone = body.get("parent_phone", "")

    success, msg, data = register_parent_request(
        parent_name=p_name,
        parent_email=p_email,
        student_id=s_id,
        student_name=s_name,
        relationship_type=rel,
        parent_phone=phone
    )

    if not success:
        return jsonify({"success": False, "message": msg}), 400

    return jsonify({"success": True, "message": msg, "data": data})


@app.route("/api/parent/request-otp", methods=["POST"])
def parent_request_otp():
    """Step 4: After admin approval, parent requests email OTP.
    Generates OTP, stores hash, and dispatches real email via EmailService.
    If SMTP is not configured, honestly reports error without fake activation."""
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()

    if not email:
        return jsonify({"success": False, "message": "Parent email is required."}), 400

    success, msg, raw_otp = create_and_store_otp(email)
    if not success:
        return jsonify({"success": False, "message": msg}), 400

    # Dispatch via EmailService
    sent, send_msg = EmailService.send_parent_otp(
        parent_email=email,
        parent_name="Parent",
        otp=raw_otp
    )

    if not sent:
        return jsonify({
            "success": False,
            "message": send_msg,
            "smtp_configured": EmailService.is_smtp_configured()
        }), 503

    return jsonify({
        "success": True,
        "message": "Verification OTP has been dispatched to your registered email address.",
        "smtp_configured": True
    })


@app.route("/api/parent/verify-otp", methods=["POST"])
def parent_verify_otp():
    """Step 5: Verifies single-use OTP against stored hash, activates account, and issues session token."""
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    otp = (body.get("otp") or "").strip()

    if not email or not otp:
        return jsonify({"success": False, "message": "Email and OTP are required."}), 400

    success, msg, token = verify_email_otp(email, otp)
    if not success:
        return jsonify({"success": False, "message": msg}), 401

    return jsonify({
        "success": True,
        "message": msg,
        "token": token
    })


@app.route("/api/parent/academic-records", methods=["GET"])
def parent_academic_records():
    """Step 6: Strictly authorized endpoint.
    Retrieves ONLY the linked student's records for this authenticated parent session.
    Supports semester filtering (e.g. ?semester=3-1 or ?semester=3-2)."""
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    elif "token" in request.args:
        token = request.args.get("token", "").strip()

    if not token:
        return jsonify({"success": False, "message": "Authorization token required."}), 401

    sem = request.args.get("semester", None)
    success, msg, data = get_linked_student_records(token, semester=sem)

    if not success:
        return jsonify({"success": False, "message": msg}), 403

    return jsonify({"success": True, "data": data})


# Backward compatible alias for parent dashboard
@app.route("/api/parent/dashboard", methods=["GET"])
def parent_dashboard_legacy():
    return parent_academic_records()


# ── Admin Parent & Student Management Endpoints (Strict Admin Auth) ───────────

@app.route("/api/admin/parent-requests", methods=["GET"])
def admin_parent_requests_list():
    is_auth, user = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    status_filter = request.args.get("status")
    requests_list = admin_get_parent_requests(status_filter)
    return jsonify({"success": True, "requests": requests_list})


@app.route("/api/admin/parent-requests/<int:req_id>/approve", methods=["POST"])
def admin_approve_parent_request(req_id: int):
    is_auth, user = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    success, msg = admin_approve_request(req_id, admin_user=user)
    if success:
        AuditLogger.log_action(user, "APPROVE_PARENT", msg, request.remote_addr)
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/api/admin/parent-requests/<int:req_id>/reject", methods=["POST"])
def admin_reject_parent_request(req_id: int):
    is_auth, user = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    reason = body.get("reason", "")
    success, msg = admin_reject_request(req_id, admin_user=user, reason=reason)
    if success:
        AuditLogger.log_action(user, "REJECT_PARENT", msg, request.remote_addr)
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/api/admin/parents/<int:parent_id>/status", methods=["POST"])
def admin_update_parent_status(parent_id: int):
    is_auth, user = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    new_status = (body.get("status") or "").strip()
    success, msg = admin_set_parent_status(parent_id, new_status, admin_user=user)
    if success:
        AuditLogger.log_action(user, "UPDATE_PARENT_STATUS", msg, request.remote_addr)
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/api/admin/students", methods=["GET", "POST"])
def admin_students_endpoint():
    is_auth, user = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    if request.method == "GET":
        q = request.args.get("q", "")
        students = admin_search_students(q)
        return jsonify({"success": True, "students": students})

    # POST: Add or edit student
    body = request.get_json(silent=True) or {}
    success, msg = admin_add_or_edit_student(
        student_id=body.get("student_id"),
        hall_ticket_no=body.get("hall_ticket_no"),
        name=body.get("name"),
        branch=body.get("branch"),
        academic_year=int(body.get("academic_year") or 1),
        current_semester=body.get("current_semester"),
        section=body.get("section", "A"),
        account_status=body.get("account_status", "Active")
    )
    if success:
        AuditLogger.log_action(user, "SAVE_STUDENT", msg, request.remote_addr)
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/api/admin/students/<student_id>", methods=["GET"])
def admin_student_details_endpoint(student_id: str):
    is_auth, user = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    data = admin_get_student_details(student_id)
    if not data:
        return jsonify({"success": False, "message": "Student not found."}), 404
    return jsonify({"success": True, "data": data})


@app.route("/api/admin/students/<student_id>/academic-records", methods=["POST"])
def admin_add_academic_record_endpoint(student_id: str):
    is_auth, user = _is_admin_authenticated()
    if not is_auth:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    success, msg = admin_add_or_edit_academic_record(
        student_id=student_id,
        academic_year=int(body.get("academic_year") or 3),
        semester=body.get("semester"),
        subject_code=body.get("subject_code"),
        subject_name=body.get("subject_name"),
        internal_marks=int(body.get("internal_marks") or 0),
        external_marks=int(body.get("external_marks") or 0),
        attendance_percentage=float(body.get("attendance_percentage") or 0.0)
    )
    if success:
        AuditLogger.log_action(user, "SAVE_ACADEMIC_RECORD", msg, request.remote_addr)
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400

# ── Custom Production Error Handlers ─────────────────────────────────────────

@app.errorhandler(404)
def not_found_error(error):
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "message": "API endpoint not found"}), 404
    if os.path.exists(os.path.join(STATIC_DIR, "404.html")):
        return send_from_directory(STATIC_DIR, "404.html"), 404
    return jsonify({"success": False, "message": "Page not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    ChatLogger.log_error(error)
    return jsonify({"success": False, "message": "An internal server error occurred"}), 500

@app.errorhandler(401)
def unauthorized_error(error):
    return jsonify({"success": False, "message": "Unauthorized access"}), 401

# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    print(f"  Browser  : http://localhost:{config.PORT}")
    print(f"  Admin    : http://localhost:{config.PORT}/admin")
    print(f"  Health   : http://localhost:{config.PORT}/api/health\n")
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
