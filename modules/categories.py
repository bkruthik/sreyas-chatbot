"""
modules/categories.py
================================================================================
Category Detector Module for the Sreyas Institute Chatbot.
Classifies user queries into 11 domain categories with keyword boundary matching.
================================================================================
"""

import re
from typing import Dict, List, Tuple

CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "ADMISSIONS": [
        "admission", "admissions", "apply", "application", "join", "seat", "seats",
        "management", "quota", "eapcet", "eamcet", "ecet", "eligibility", "eligible",
        "counselling", "counseling", "cutoff", "cut-off", "rank", "ranks", "process"
    ],
    "FEES": [
        "fee", "fees", "tuition", "payment", "cost", "annual fee", "per year",
        "structure", "breakdown", "scholarship amount", "installment"
    ],
    "PLACEMENTS": [
        "placement", "placements", "package", "packages", "salary", "lpa", "ctc",
        "company", "companies", "recruiters", "job", "jobs", "internship", "career",
        "highest package", "average package", "top recruiters", "training",
        "guaranteed job", "guarantee job", "job guarantee", "placement guarantee", "guaranteed"
    ],
    "COURSES": [
        "course", "courses", "branch", "branches", "department", "departments",
        "cse", "ece", "aiml", "ai & ml", "ai ml", "btech", "b.tech", "undergraduate", "programme",
        "computer science", "electronics", "artificial intelligence", "machine learning",
        "tell me about cse", "tell me about ece", "tell me about ai"
    ],
    "FACILITIES": [
        "library", "wifi", "wi-fi", "laboratory", "lab", "labs", "canteen",
        "cafeteria", "sports", "gym", "campus", "facility", "facilities", "infrastructure",
        "smart classroom", "smart classrooms", "classroom", "classrooms", "seminar hall", "auditorium"
    ],
    "HOSTEL": [
        "hostel", "hostels", "hostel facility", "hostel facilities", "hostel accommodation", "pg", "accommodation", "room", "stay", "boarding"
    ],
    "TRANSPORT": [
        "bus", "buses", "transport", "route", "routes", "pickup", "drop", "travel"
    ],
    "EXAMS": [
        "exam", "exams", "examination", "attendance", "result", "results",
        "marks", "semester", "internal", "external", "mid", "pattern"
    ],
    "SCHOLARSHIPS": [
        "scholarship", "scholarships", "reimbursement", "freeship", "financial aid", "loan",
        "fee reimbursement", "government fee reimbursement", "government scholarship",
        "scholarship facility", "scholarship facilities"
    ],
    "FACULTY": [
        "faculty", "teacher", "teachers", "lecturer", "professor", "professors",
        "hod", "principal", "staff"
    ],
    "CONTACT": [
        "phone", "email", "address", "location", "contact", "call", "helpline",
        "map", "direction", "reach"
    ]
}


class CategoryDetector:
    """Classifies search queries into domain categories."""

    @classmethod
    def detect(cls, text: str) -> Dict[str, float | str]:
        """
        Classify text into a category.

        Args:
            text: Query string.

        Returns:
            Dict containing 'category' (str) and 'confidence' (float).
        """
        if not text or not text.strip():
            return {"category": "GENERAL", "confidence": 0.0}

        text_lower = text.lower()
        scores: Dict[str, int] = {}

        for category, keywords in CATEGORY_KEYWORDS.items():
            count = 0
            for kw in keywords:
                # Use word boundary or exact match
                if " " in kw:
                    if kw in text_lower:
                        count += 2  # Give higher weight to multi-word phrases
                else:
                    if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                        count += 1
            if count > 0:
                scores[category] = count

        if not scores:
            return {"category": "GENERAL", "confidence": 0.0}

        best_category = max(scores, key=lambda k: scores[k])
        highest_score = scores[best_category]
        confidence = min(1.0, round(highest_score / 3.0, 2))

        return {"category": best_category, "confidence": confidence}