"""
=========================================================
SREYAS ASSISTANT — NeMo-inspired Guardrails Module
=========================================================
Handles input safety, prompt injection detection, abuse filtering,
off-topic query redirection, and factual positive redirection
for negative queries.
"""

import re
try:
    from modules.language import Localizer
except ImportError:
    from language import Localizer   # type: ignore


class Guardrails:

    # Off-topic pattern filtering
    OFF_TOPIC_PATTERNS = [
        r"\b(ipl|movie|movies|film|actor|actress|hero|heroine)\b",
        r"\b(recipe|food|restaurant|cook|cooking)\b",
        r"\b(bitcoin|crypto|share market|stock market)\b",
        r"\b(politics|election|minister|prime minister)\b"
    ]

    # Abusive words patterns
    ABUSE_PATTERNS = [
        r"\b(idiot|stupid|dumb|hate|loser)\b",
        r"\b(fuck|shit|bitch|asshole)\b"
    ]

    # Prompt injection patterns
    PROMPT_PATTERNS = [
        r"ignore previous",
        r"ignore instructions",
        r"forget instructions",
        r"system prompt",
        r"developer mode",
        r"act as",
        r"jailbreak",
        r"reveal prompt",
        r"show hidden prompt"
    ]

    # Negative college query patterns
    NEGATIVE_PATTERNS = [
        r"\bworst college\b",
        r"\bavoid\b",
        r"\bscam\b",
        r"\bfraud\b",
        r"\bdon.?t join\b",
        r"\bbad college\b",
        r"\bpoor placements\b",
        r"\bno placements\b",
        r"\bfake college\b"
    ]

    # Rich factual positive redirect for negative queries
    FACTUAL_POSITIVE_REDIRECT = (
        "Sreyas Institute of Engineering and Technology is a premier UGC Autonomous institution in Hyderabad:\n\n"
        "⭐ **Accreditation**: NAAC 'A' Grade Accredited & NBA Accredited (ECE Branch)\n"
        "🏆 **Rankings**: #93 in Times Engineering Survey 2025 | 5-Star Rating from IIT Bombay\n"
        "💼 **Placements 2025-26**: Highest Package **Rs 21.18 LPA** (Atomic Works) with 558+ Placement Offers\n"
        "🏢 **Top Recruiters**: Capgemini, Accenture, TCS, Wipro, Infosys, Hexaware\n"
        "🌐 **Official Portal**: www.sreyas.ac.in\n\n"
        "If you have specific academic or admission questions, I'm glad to assist! 😊"
    )

    OFF_TOPIC_REDIRECT = (
        "I am SREYAS Assistant, specialized in providing official information about "
        "Sreyas Institute of Engineering and Technology (admissions, courses, fees, placements, campus facilities).\n\n"
        "How can I help you with Sreyas Institute today? 😊"
    )

    @classmethod
    def check_input(cls, message: str, language: str = "en") -> dict:
        text = message.lower().strip()

        # 1. Prompt Injection
        for pattern in cls.PROMPT_PATTERNS:
            if re.search(pattern, text):
                return {
                    "safe": False,
                    "reason": "Prompt Injection",
                    "message": Localizer.localized("ood_refusal", language, cls.OFF_TOPIC_REDIRECT)
                }

        # 2. Abusive Language
        for pattern in cls.ABUSE_PATTERNS:
            if re.search(pattern, text):
                _abuse_te = "దయచేసి మర్యాదగా మాట్లాడండి. Sreyas Institute గురించి మీకు సహాయం చేయడానికి నేను సంతోషంగా ఉన్నాను. 😊"
                _abuse_hi = "कृपया सम्मानजनक भाषा का उपयोग करें। मुझे आपकी Sreyas Institute के बारे में सहायता करने में खुशी होगी। 😊"
                _abuse_map = {"te": _abuse_te, "hi": _abuse_hi}
                return {
                    "safe": False,
                    "reason": "Abusive Language",
                    "message": _abuse_map.get(language, "Please use respectful language. I am happy to assist you with any questions about Sreyas Institute. 😊")
                }

        # 3. Negative Queries -> Factual Positive Redirect
        for pattern in cls.NEGATIVE_PATTERNS:
            if re.search(pattern, text):
                return {
                    "safe": False,
                    "reason": "Negative Query",
                    "message": cls.FACTUAL_POSITIVE_REDIRECT
                }

        # 4. Off-Topic
        for pattern in cls.OFF_TOPIC_PATTERNS:
            if re.search(pattern, text):
                return {
                    "safe": False,
                    "reason": "Off Topic",
                    "message": Localizer.localized("ood_refusal", language, cls.OFF_TOPIC_REDIRECT)
                }

        return {
            "safe": True,
            "reason": "Safe",
            "message": ""
        }

    @classmethod
    def check_output(cls, answer: str) -> str:
        answer_lower = answer.lower()
        for pattern in cls.NEGATIVE_PATTERNS:
            if re.search(pattern, answer_lower):
                return cls.FACTUAL_POSITIVE_REDIRECT
        return answer