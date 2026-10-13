"""
modules/typo.py
================================================================================
Typo Correction Module for the Sreyas Institute Chatbot.
Scans user questions against a curated domain dictionary of common misspellings
(e.g., 'pakcage' -> 'package', 'highes' -> 'highest', 'scholership' -> 'scholarship',
'admision' -> 'admission', 'placemnt' -> 'placement', 'clg' -> 'college').
================================================================================
"""

import re
from typing import List, Tuple

# Domain-specific dictionary of common typos mapped to canonical terms
TYPO_MAP = {
    # Admissions & Application
    "admision": "admission",
    "admisson": "admission",
    "admisn": "admission",
    "admition": "admission",
    "applcation": "application",
    "eligblity": "eligibility",
    "eligiblity": "eligibility",
    "councelling": "counselling",
    "counceling": "counselling",
    "counsellingg": "counselling",

    # Placements & Salary
    "placemnt": "placement",
    "placment": "placement",
    "palcement": "placement",
    "placeemnt": "placement",
    "pakcage": "package",
    "packge": "package",
    "pkg": "package",
    "highes": "highest",
    "highst": "highest",
    "salry": "salary",
    "recuriters": "recruiters",
    "recruitr": "recruiter",

    # Scholarships & Fees
    "scholrship": "scholarship",
    "scholorship": "scholarship",
    "scholership": "scholarship",
    "schlarship": "scholarship",
    "schalorship": "scholarship",
    "scholarshp": "scholarship",
    "scolarship": "scholarship",
    "freeship": "scholarship",
    "feess": "fees",
    "tution": "tuition",
    "wat": "what",

    # Courses & Branches
    "btech": "b.tech",
    "btch": "b.tech",
    "cours": "course",
    "brnch": "branch",
    "semister": "semester",
    "semstar": "semester",

    # College & Campus
    "collage": "college",
    "clg": "college",
    "colg": "college",
    "sreyass": "sreyas",
    "sreyash": "sreyas",

    # Facilities & Infrastructure
    "facilites": "facilities",
    "facilty": "facility",
    "libary": "library",
    "wifii": "wifi",
    "trasport": "transport",
    "transprot": "transport",
    "hostal": "hostel",

    # Exam & Authorities
    "eamcet": "eapcet",
    "eapcett": "eapcet",
}


class TypoCorrector:
    """Corrects known typos in incoming search queries."""

    @classmethod
    def correct(cls, text: str) -> Tuple[str, List[str]]:
        """
        Correct typos in text based on TYPO_MAP.

        Args:
            text: Input string.

        Returns:
            Tuple of (corrected_text, list_of_applied_corrections).
        """
        if not text:
            return text, []

        tokens = text.split()
        corrected_tokens = []
        corrections = []

        for token in tokens:
            # Strip punctuation for lookup
            clean_word = re.sub(r"[^\w\.]", "", token.lower())
            if clean_word in TYPO_MAP:
                canonical = TYPO_MAP[clean_word]
                # Preserve capitalization if original was capitalized
                replacement = canonical.capitalize() if token[0].isupper() else canonical
                corrected_tokens.append(replacement)
                corrections.append(f"{clean_word} -> {canonical}")
            else:
                corrected_tokens.append(token)

        corrected_text = " ".join(corrected_tokens)
        return corrected_text, corrections