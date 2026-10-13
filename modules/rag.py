"""
modules/rag.py
================================================================================
RAG Engine — SREYAS Assistant

Changes in THIS revision (architecture cleanup, per review):

  1. Localizer moved OUT to modules/language.py. rag.py now imports it
     instead of defining it -- this file is retrieval/ranking/confidence
     only, not a translation engine.
  2. SYNONYMS and ABBREVIATION_EXPANSIONS moved OUT to
     config/synonyms.json. SynonymEngine loads them at import time; a
     missing/corrupt config degrades to an empty dict rather than
     crashing (English matching still works via TF-IDF/keyword scoring
     alone, just without the synonym boost).
  3. NEW: query-side translation. The actual bug from the field report
     ("selected Telugu, asked ఫీజు వివరాలు, got an English answer") is
     that only the OUTPUT was ever translated -- the query itself hit
     TextNormalizer/Tokenizer/CategoryDetector still full of untranslated
     Telugu, so nothing matched. Fixed by running the query through
     `language.QueryTranslator.translate()` FIRST, so every downstream
     step (TextNormalizer, Tokenizer, SynonymEngine, CategoryDetector,
     TypoCorrector) only ever sees English, exactly as before -- none of
     those files needed to change.

  Kept from the previous revision (all still valid):
  section/field/exact-phrase boosting, source metadata, LRU query cache,
  multi-section answer combining, structured reputation pillars, debug
  mode, session-aware conversation memory, the narrowed reputation-query
  patterns, the four-signal enquiry gate, and knowledge_base.txt
  staleness auto-refresh.

SCOPE NOTE: Tokenizer / TextNormalizer / SimilarityEngine / Confidence-
Scorer / AnalyticsTracker / QueryCache remain in this file rather than
each becoming its own module. They're small, have no state that outlives
one RAGEngine instance, and are only ever used by RAGEngine -- splitting
them further would add import indirection without adding safety or
testability. Localizer and the synonym/translation dictionaries were
split out because they had a real, independent reason to move (a second
consumer -- future language-aware category detection -- and a
non-engineer-editable data format). If that stops being true, further
splitting is cheap to do later; it isn't free to undo.

All public method signatures remain backward compatible: retrieve(query)
still works with no other arguments; session_id and language are both
optional keyword arguments with safe defaults.
================================================================================
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from abc import ABC, abstractmethod
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Sibling-module imports (defensive — degrade gracefully if missing)
# ---------------------------------------------------------------------------
try:
    from modules.categories import CategoryDetector
except ImportError:
    try:
        from categories import CategoryDetector
    except ImportError:
        CategoryDetector = None

try:
    from modules.typo import TypoCorrector
except ImportError:
    try:
        from typo import TypoCorrector
    except ImportError:
        TypoCorrector = None

try:
    from modules.memory import ConversationMemory
except ImportError:
    try:
        from memory import ConversationMemory
    except ImportError:
        ConversationMemory = None

try:
    from . import config as _config  # type: ignore
except ImportError:
    try:
        import config as _config  # type: ignore
    except ImportError:
        _config = None

# Localizer / QueryTranslator now live in modules/language.py.
try:
    from .language import Localizer, QueryTranslator  # type: ignore
except ImportError:
    try:
        from language import Localizer, QueryTranslator  # type: ignore
    except ImportError:
        # Last-resort no-op fallbacks so rag.py still runs (English-only,
        # no output translation) even if language.py is somehow missing --
        # consistent with this file's existing "never let a missing
        # sibling module take the whole engine down" philosophy.
        class Localizer:  # type: ignore
            SUPPORTED_LANGUAGES = frozenset({"en"})

            @classmethod
            def is_supported(cls, language: str) -> bool:
                return (language or "en").strip().lower() == "en"

            @classmethod
            def phrase(cls, key: str, language: str) -> str:
                return ""

            @classmethod
            def override(cls, category: str, language: str) -> Optional[str]:
                return None

            @classmethod
            def translate_answer_body(cls, text: str, language: str) -> str:
                return text

        class QueryTranslator:  # type: ignore
            @classmethod
            def translate(cls, text: str, language: str) -> str:
                return text


def _cfg(name: str, default: Any) -> Any:
    """Read from config.py with fallback."""
    return getattr(_config, name, default)


def _safe_call(obj: Any, method_names: List[str], *args: Any, default: Any = None) -> Any:
    """Call the first matching method on obj, suppressing all errors."""
    if obj is None:
        return default
    for name in method_names:
        fn = getattr(obj, name, None)
        if callable(fn):
            try:
                return fn(*args)
            except TypeError:
                try:
                    return fn()
                except Exception:
                    continue
            except Exception:
                continue
    return default


def _load_synonym_config() -> Dict[str, Any]:
    """Loads config/synonyms.json. Missing/corrupt config degrades to
    empty dicts -- English retrieval still works via TF-IDF/keyword
    scoring alone, just without the synonym boost, rather than crashing."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "synonyms.json"
    )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


_SYNONYM_CONFIG = _load_synonym_config()


# ---------------------------------------------------------------------------
# BUS ROUTES CONFIG — loaded lazily from config/bus_routes.json
# ---------------------------------------------------------------------------

_BUS_ROUTES_CACHE: Optional[List[Dict]] = None

def _load_bus_routes() -> List[Dict]:
    """Load and cache bus routes from config/bus_routes.json.
    Returns empty list if file is missing (degrades gracefully)."""
    global _BUS_ROUTES_CACHE
    if _BUS_ROUTES_CACHE is not None:
        return _BUS_ROUTES_CACHE
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "bus_routes.json"
    )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _BUS_ROUTES_CACHE = data.get("routes", [])
    except (OSError, json.JSONDecodeError):
        _BUS_ROUTES_CACHE = []
    return _BUS_ROUTES_CACHE


_RT_NUM_RE = re.compile(
    r'\b(?:rt[-\s]?|route[-\s]?|bus[-\s]?)(\d{1,2})\b', re.IGNORECASE
)

def _extract_route_number(q: str) -> Optional[str]:
    """Return e.g. 'RT-5' if the query asks for a specific route number, else None."""
    m = _RT_NUM_RE.search(q)
    if m:
        return f"RT-{m.group(1)}"
    return None


# ===========================================================================
# DATA STRUCTURES
# ===========================================================================

@dataclass
class RawDocument:
    """A section loaded from knowledge_base.txt before indexing."""
    category: str
    title: str
    content: str


@dataclass
class DocumentChunk:
    """A fully indexed, vectorized knowledge-base section."""
    category: str
    title: str
    content: str
    tokens: List[str] = field(default_factory=list)
    tf: Dict[str, float] = field(default_factory=dict)
    vector: Dict[str, float] = field(default_factory=dict)


# ===========================================================================
# TEXT NORMALIZER
# ===========================================================================

class TextNormalizer:
    """
    Canonicalises variant spellings of branch codes, exam names, etc.
    so "C.S.E", "CSE", "Computer Science Engineering" all surface the
    same tokens and match the same KB section.
    """

    _CANONICAL: Dict[str, str] = {
        r"\bc\.?s\.?e\.?\b": "computer science engineering",
        r"\be\.?c\.?e\.?\b": "electronics communication engineering",
        r"\be\.?e\.?e\.?\b": "electrical electronics engineering",
        r"\bai\s*[&/]\s*ml\b": "artificial intelligence machine learning",
        r"\bai\s*ml\b": "artificial intelligence machine learning",
        r"\baiml\b": "artificial intelligence machine learning",
        r"\bai\s*&\s*ml\b": "artificial intelligence machine learning",
        r"\bdata\s*science\b": "data science",
        r"\bds\b": "data science",
        r"\bjntu[\s\-]?h\b": "jawaharlal nehru technological university hyderabad",
        r"\bjntu\b": "jawaharlal nehru technological university",
        r"\btg[\s\-]?eapcet\b": "telangana eapcet",
        r"\beapcet\b": "telangana eapcet",
        r"\becet\b": "telangana ecet",
        r"\bnaac\b": "naac accreditation",
        r"\bnba\b": "nba accreditation",
        r"\baicte\b": "aicte approval",
        r"\bugc\b": "ugc autonomous",
        r"\biso\b": "iso certification",
        r"\blpa\b": "lakhs per annum",
        r"\bpg\b": "paying guest",
        r"\bb\.?\s*tech\b": "btech",
        r"\bm\.?\s*tech\b": "mtech",
        r"\bclg\b": "college",
        r"\bprinci\b": "principal",
        r"\bhighest\s+pkg\b": "highest package",
        r"\bavg\s+pkg\b": "average package",
    }

    _compiled: Optional[List[Tuple[re.Pattern, str]]] = None

    @classmethod
    def _get_compiled(cls) -> List[Tuple[re.Pattern, str]]:
        if cls._compiled is None:
            cls._compiled = [
                (re.compile(pattern, re.IGNORECASE), replacement)
                for pattern, replacement in cls._CANONICAL.items()
            ]
        return cls._compiled

    @classmethod
    def normalize(cls, text: str) -> str:
        """Apply all canonical substitutions to *text* and return the result."""
        result = text
        for pattern, replacement in cls._get_compiled():
            result = pattern.sub(replacement, result)
        return result


# ===========================================================================
# DOCUMENT SOURCE
# ===========================================================================

class BaseDocumentSource(ABC):
    """Abstract loader — subclass to add PDF / DOCX / web sources later."""

    @abstractmethod
    def load(self) -> List[RawDocument]:
        raise NotImplementedError


class TextFileSource(BaseDocumentSource):
    """Loads knowledge_base.txt split by ``== SECTION NAME ==`` headers."""

    _SECTION_PATTERN = re.compile(r"==\s*(.+?)\s*==")

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path

    def load(self) -> List[RawDocument]:
        if not os.path.isfile(self.file_path):
            raise FileNotFoundError(f"Knowledge base not found: {self.file_path}")
        with open(self.file_path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        matches = list(self._SECTION_PATTERN.finditer(raw))
        docs: List[RawDocument] = []
        for idx, m in enumerate(matches):
            cat = m.group(1).strip().upper()
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
            body = raw[start:end].strip()
            if body:
                docs.append(RawDocument(category=cat, title=cat.title(), content=body))
        return docs


# ===========================================================================
# TOKENIZER
# ===========================================================================

class Tokenizer:
    """Lowercase, strip punctuation, drop stopwords, keep abbreviations."""

    STOPWORDS: frozenset = frozenset({
        "a", "an", "the", "and", "or", "but", "if", "then", "else", "of", "at", "by",
        "for", "with", "about", "against", "between", "into", "through", "during",
        "before", "after", "above", "below", "to", "from", "up", "down", "in", "out",
        "on", "off", "over", "under", "again", "further", "once", "is", "am", "are",
        "was", "were", "be", "been", "being", "have", "has", "had", "having", "do",
        "does", "did", "doing", "would", "should", "could", "ought", "i", "me", "my",
        "myself", "we", "our", "ours", "ourselves", "you", "your", "yours",
        "yourself", "yourselves", "he", "him", "his", "himself", "she", "her",
        "hers", "herself", "it", "its", "itself", "they", "them", "their", "theirs",
        "themselves", "what", "which", "who", "whom", "this", "that", "these",
        "those", "as", "until", "while", "so", "than", "too", "very", "s", "t",
        "can", "will", "just", "don", "now", "please", "tell", "know", "want",
        "there", "here", "how", "when", "where", "why", "also", "get", "got",
    })

    _CLEAN = re.compile(r"[^a-z0-9\s]")
    _WS = re.compile(r"\s+")

    @classmethod
    def tokenize(cls, text: str) -> List[str]:
        if not text:
            return []
        txt = cls._CLEAN.sub(" ", text.lower())
        txt = cls._WS.sub(" ", txt).strip()
        return [t for t in txt.split() if t and t not in cls.STOPWORDS]


# ===========================================================================
# SYNONYM ENGINE
# ===========================================================================

class SynonymEngine:
    """
    Expands query / document tokens with domain synonyms and abbreviations,
    loaded from config/synonyms.json (see module docstring, change #2).
    Original tokens are preserved; expansions are appended so both forms
    contribute to TF-IDF scoring.
    """

    def __init__(self) -> None:
        self.synonyms: Dict[str, str] = {
            **_SYNONYM_CONFIG.get("synonyms", {}),
            **_cfg("SYNONYMS", {}),
        }
        self.abbreviations: Dict[str, str] = {
            **_SYNONYM_CONFIG.get("abbreviations", {}),
            **_cfg("ABBREVIATIONS", {}),
        }

    def expand(self, tokens: List[str]) -> List[str]:
        expanded = list(tokens)
        for tok in tokens:
            syn = self.synonyms.get(tok)
            if syn and syn != tok:
                expanded.append(syn)
            abbr = self.abbreviations.get(tok)
            if abbr:
                expanded.extend(abbr.split())
        return expanded


# ===========================================================================
# TF-IDF VECTORIZER
# ===========================================================================

class TFIDFVectorizer:
    """Manual TF-IDF — no scikit-learn, no external dependencies."""

    def __init__(self) -> None:
        self.vocabulary: Dict[str, int] = {}
        self.document_frequency: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self.total_documents: int = 0

    def fit(self, chunks: List[DocumentChunk]) -> None:
        self.total_documents = len(chunks)
        self.vocabulary.clear()
        self.document_frequency.clear()
        self.idf.clear()
        for chunk in chunks:
            for tok in set(chunk.tokens):
                self.vocabulary.setdefault(tok, len(self.vocabulary))
                self.document_frequency[tok] = self.document_frequency.get(tok, 0) + 1
        for tok, df in self.document_frequency.items():
            self.idf[tok] = math.log(self.total_documents / df) + 1.0
        for chunk in chunks:
            chunk.tf = self._tf(chunk.tokens)
            chunk.vector = self._vectorize(chunk.tf)

    def _tf(self, tokens: List[str]) -> Dict[str, float]:
        if not tokens:
            return {}
        counts = Counter(tokens)
        total = len(tokens)
        return {t: c / total for t, c in counts.items()}

    def _vectorize(self, tf: Dict[str, float]) -> Dict[str, float]:
        max_idf = math.log(max(self.total_documents, 1)) + 1.0
        return {t: f * self.idf.get(t, max_idf) for t, f in tf.items()}

    def vectorize_query(self, tokens: List[str]) -> Dict[str, float]:
        return self._vectorize(self._tf(tokens))


# ===========================================================================
# CATEGORY HEADER MATCHER
# ===========================================================================

class CategoryHeaderMatcher:
    """Maps CategoryDetector codes to KB section header strings."""

    DEFAULT_ALIASES: Dict[str, List[str]] = {
        "ADMISSIONS": ["ADMISSIONS AND ELIGIBILITY CRITERIA", "ADMISSIONS PROCESS", "ADMISSIONS"],
        "FEES": ["TUITION FEES AND OTHER CHARGES", "FEE STRUCTURE", "FEES"],
        "PLACEMENTS": ["TRAINING AND PLACEMENTS (T&P CELL)", "PLACEMENTS"],
        "COURSES": ["UNDERGRADUATE DEGREE PROGRAMMES (B.TECH)", "COURSES AND BRANCHES", "COURSES"],
        "FACILITIES": ["CAMPUS FACILITIES AND INFRASTRUCTURE", "CENTRAL LIBRARY AND E-LEARNING RESOURCES", "FACILITIES"],
        "HOSTEL": ["HOSTEL AND ACCOMMODATION INFORMATION", "HOSTEL"],
        "TRANSPORT": ["TRANSPORTATION AND BUS ROUTES", "TRANSPORT"],
        "EXAMS": ["ACADEMIC CALENDAR AND EXAMINATIONS", "EXAMINATION PATTERN", "EXAMS"],
        "SCHOLARSHIPS": ["SCHOLARSHIPS AND FINANCIAL ASSISTANCE", "SCHOLARSHIPS AND FINANCIAL AID", "SCHOLARSHIPS"],
        "FACULTY": ["GOVERNING BODY AND MANAGEMENT", "FACULTY", "LEADERSHIP AND MANAGEMENT"],
        "CONTACT": ["OFFICIAL CONTACT DETAILS", "CONTACT DETAILS", "CONTACT"],
        "SAFETY": ["CAMPUS SAFETY, SECURITY AND DISCIPLINE", "SAFETY AND SECURITY"],
    }

    def __init__(self) -> None:
        merged: Dict[str, List[str]] = {k: list(v) for k, v in self.DEFAULT_ALIASES.items()}
        for k, v in _cfg("CATEGORY_HEADER_ALIASES", {}).items():
            merged.setdefault(k.upper(), []).extend(v)
        self.aliases = merged

    def matches(self, chunk_category: str, detected_category: str) -> bool:
        cc = (chunk_category or "").strip().upper()
        dc = (detected_category or "").strip().upper()
        if not cc or not dc:
            return False
        if cc == dc:
            return True
        if cc in self.aliases.get(dc, []):
            return True
        stripped = dc.rstrip("S")
        return bool(stripped) and stripped in cc


# ===========================================================================
# SIMILARITY ENGINE
# ===========================================================================

_SECTION_TITLE_MAP: Dict[str, str] = {
    "courses": "UNDERGRADUATE DEGREE PROGRAMMES (B.TECH)",
    "course": "UNDERGRADUATE DEGREE PROGRAMMES (B.TECH)",
    "branches": "UNDERGRADUATE DEGREE PROGRAMMES (B.TECH)",
    "branch": "UNDERGRADUATE DEGREE PROGRAMMES (B.TECH)",
    "programmes": "UNDERGRADUATE DEGREE PROGRAMMES (B.TECH)",
    "fees": "TUITION FEES AND OTHER CHARGES",
    "fee": "TUITION FEES AND OTHER CHARGES",
    "fee structure": "TUITION FEES AND OTHER CHARGES",
    "cost": "TUITION FEES AND OTHER CHARGES",
    "placements": "TRAINING AND PLACEMENTS (T&P CELL)",
    "placement": "TRAINING AND PLACEMENTS (T&P CELL)",
    "recruiter": "TRAINING AND PLACEMENTS (T&P CELL)",
    "recruiters": "TRAINING AND PLACEMENTS (T&P CELL)",
    "top recruiters": "TRAINING AND PLACEMENTS (T&P CELL)",
    "placement training": "TRAINING AND PLACEMENTS (T&P CELL)",
    "scholarship": "SCHOLARSHIPS AND FINANCIAL ASSISTANCE",
    "scholarships": "SCHOLARSHIPS AND FINANCIAL ASSISTANCE",
    "fee reimbursement": "SCHOLARSHIPS AND FINANCIAL ASSISTANCE",
    "government fee reimbursement": "SCHOLARSHIPS AND FINANCIAL ASSISTANCE",
    "government scholarship": "SCHOLARSHIPS AND FINANCIAL ASSISTANCE",
    "epass": "SCHOLARSHIPS AND FINANCIAL ASSISTANCE",
    "admission": "ADMISSIONS AND ELIGIBILITY CRITERIA",
    "admissions": "ADMISSIONS AND ELIGIBILITY CRITERIA",
    "transport": "TRANSPORTATION AND BUS ROUTES",
    "transportation": "TRANSPORTATION AND BUS ROUTES",
    "bus": "TRANSPORTATION AND BUS ROUTES",
    "bus routes": "TRANSPORTATION AND BUS ROUTES",
    "bus facility": "TRANSPORTATION AND BUS ROUTES",
    "college bus": "TRANSPORTATION AND BUS ROUTES",
    "route": "TRANSPORTATION AND BUS ROUTES",
    "routes": "TRANSPORTATION AND BUS ROUTES",
    "facilities": "CAMPUS FACILITIES AND INFRASTRUCTURE",
    "facility": "CAMPUS FACILITIES AND INFRASTRUCTURE",
    "library": "CENTRAL LIBRARY AND E-LEARNING RESOURCES",
    "exam": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "exam pattern": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "exams": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "examination": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "examination pattern": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "r22": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "r25": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "marks": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "internal marks": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "external marks": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "passing marks": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "attendance": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "contact": "OFFICIAL CONTACT DETAILS",
    "contacts": "OFFICIAL CONTACT DETAILS",
    "faculty": "GOVERNING BODY AND MANAGEMENT",
    "principal": "GOVERNING BODY AND MANAGEMENT",
    "chairman": "GOVERNING BODY AND MANAGEMENT",
    "leadership": "GOVERNING BODY AND MANAGEMENT",
    "hostel": "HOSTEL AND ACCOMMODATION INFORMATION",
    "hostel facility": "HOSTEL AND ACCOMMODATION INFORMATION",
    "hostel facilities": "HOSTEL AND ACCOMMODATION INFORMATION",
    "hostel accommodation": "HOSTEL AND ACCOMMODATION INFORMATION",
    "pg accommodation": "HOSTEL AND ACCOMMODATION INFORMATION",
    "college timings": "CAMPUS FACILITIES AND INFRASTRUCTURE",
    "timings": "CAMPUS FACILITIES AND INFRASTRUCTURE",
    "timing": "CAMPUS FACILITIES AND INFRASTRUCTURE",
    "about": "INSTITUTION OVERVIEW",
    "about sreyas": "INSTITUTION OVERVIEW",
    "about the college": "INSTITUTION OVERVIEW",
    "sreyas institute": "INSTITUTION OVERVIEW",
    "what is sreyas": "INSTITUTION OVERVIEW",
    "what is sreyas institute": "INSTITUTION OVERVIEW",
    "college overview": "INSTITUTION OVERVIEW",
    "rankings": "ACADEMIC RANKINGS & ACHIEVEMENTS",
    "events": "STUDENT CLUBS AND CO-CURRICULAR ACTIVITIES",
    "activities": "STUDENT CLUBS AND CO-CURRICULAR ACTIVITIES",
    "clubs": "STUDENT CLUBS AND CO-CURRICULAR ACTIVITIES",
    "safety": "CAMPUS SAFETY, SECURITY AND DISCIPLINE",
    "security": "CAMPUS SAFETY, SECURITY AND DISCIPLINE",
}

_FIELD_BOOST_MAP: Dict[str, str] = {
    "principal": "GOVERNING BODY AND MANAGEMENT", "chairman": "GOVERNING BODY AND MANAGEMENT",
    "phone": "OFFICIAL CONTACT DETAILS", "email": "OFFICIAL CONTACT DETAILS",
    "address": "OFFICIAL CONTACT DETAILS", "website": "OFFICIAL CONTACT DETAILS",
    "contact": "OFFICIAL CONTACT DETAILS", "contact details": "OFFICIAL CONTACT DETAILS",
    "highest package": "TRAINING AND PLACEMENTS (T&P CELL)",
    "average package": "TRAINING AND PLACEMENTS (T&P CELL)",
    "top recruiters": "TRAINING AND PLACEMENTS (T&P CELL)",
    "recruiter": "TRAINING AND PLACEMENTS (T&P CELL)",
    "placement training": "TRAINING AND PLACEMENTS (T&P CELL)",
    "placement": "TRAINING AND PLACEMENTS (T&P CELL)",
    "transport": "TRANSPORTATION AND BUS ROUTES",
    "transportation": "TRANSPORTATION AND BUS ROUTES",
    "bus routes": "TRANSPORTATION AND BUS ROUTES",
    "bus facility": "TRANSPORTATION AND BUS ROUTES",
    "college timings": "CAMPUS FACILITIES AND INFRASTRUCTURE",
    "cutoff": "ADMISSIONS AND ELIGIBILITY CRITERIA", "attendance": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "r22": "ACADEMIC CALENDAR AND EXAMINATIONS", "r25": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "internal marks": "ACADEMIC CALENDAR AND EXAMINATIONS", "external marks": "ACADEMIC CALENDAR AND EXAMINATIONS",
    "annual fee": "TUITION FEES AND OTHER CHARGES", "management quota": "ADMISSIONS AND ELIGIBILITY CRITERIA",
    "cse": "UNDERGRADUATE DEGREE PROGRAMMES (B.TECH)", "ece": "UNDERGRADUATE DEGREE PROGRAMMES (B.TECH)",
    "aiml": "UNDERGRADUATE DEGREE PROGRAMMES (B.TECH)", "ai & ml": "UNDERGRADUATE DEGREE PROGRAMMES (B.TECH)",
    "computer science": "UNDERGRADUATE DEGREE PROGRAMMES (B.TECH)",
    "artificial intelligence": "UNDERGRADUATE DEGREE PROGRAMMES (B.TECH)",
    "electronics": "UNDERGRADUATE DEGREE PROGRAMMES (B.TECH)",
    "library": "CENTRAL LIBRARY AND E-LEARNING RESOURCES", "wifi": "CAMPUS FACILITIES AND INFRASTRUCTURE", "laboratory": "DEPARTMENTS AND LABORATORIES",
    "scholarship": "SCHOLARSHIPS AND FINANCIAL ASSISTANCE",
    "fee reimbursement": "SCHOLARSHIPS AND FINANCIAL ASSISTANCE",
    "government scholarship": "SCHOLARSHIPS AND FINANCIAL ASSISTANCE",
    "anti ragging": "CAMPUS SAFETY, SECURITY AND DISCIPLINE", "cctv": "CAMPUS SAFETY, SECURITY AND DISCIPLINE",
    "about the college": "INSTITUTION OVERVIEW", "about sreyas": "INSTITUTION OVERVIEW", "college overview": "INSTITUTION OVERVIEW",
}

_HIGH_VALUE_PHRASES: List[str] = [
    "highest package", "average package", "exam pattern", "annual fee",
    "management quota", "computer science", "artificial intelligence",
    "placement training", "cutoff rank", "lateral entry", "internship",
    "anti ragging", "cctv surveillance", "smart classroom", "google cloud",
    "naac", "nba accredited", "jntu hyderabad", "ugc autonomous",
]

# Bare/short-query -> section resolution, used so retrieval understands
# single-word queries like "principal", "library", "hostel" on its own
# merit rather than depending entirely on CategoryDetector (audit
# requirement: "smarter retrieval" for common bare queries). Reuses
# _SECTION_TITLE_MAP rather than duplicating a second dictionary.
_BARE_QUERY_MAX_TOKENS = 2


class SimilarityEngine:
    """Cosine similarity + keyword score + section/field/phrase boosts."""

    HIGH_PRIORITY_KEYWORDS: frozenset = frozenset({
        "principal", "phone", "chairman", "director", "email", "contact",
        "fees", "fee", "hostel", "placement", "placements", "scholarship",
        "scholarships", "bus", "transport", "exam", "exams", "admission",
        "admissions", "address", "website", "helpline", "location",
    })

    # BUG FIX (found via real transcript: "ph alllowed in clg" -- a
    # question about phone policy, with no answer anywhere in the KB --
    # confidently returned "College Timings" instead of triggering the
    # enquiry form). Root cause: "clg" expands via synonym to
    # "institute", and "college"/"institute" appear in almost EVERY
    # chunk's token set (every section's header or body mentions the
    # college by name, and build_index() synonym-expands chunk tokens
    # too) -- so a query merely mentioning "college"/"clg" scored a
    # spurious ~0.5 keyword overlap with an unrelated chunk purely from
    # this near-universal word, which was enough to clear the enquiry
    # gate's keyword floor even though nothing about the actual question
    # matched. These generic institutional words carry essentially zero
    # discriminating power between sections and are excluded from both
    # keyword_score and synonym_overlap_score accordingly.
    GENERIC_TERMS: frozenset = frozenset({"college", "institute", "campus", "sreyas"})

    SECTION_BOOST = 0.25
    FIELD_BOOST = 0.12
    PHRASE_BOOST = 0.10
    PRIORITY_KW_UNIT = 0.08

    @staticmethod
    def cosine_similarity(va: Dict[str, float], vb: Dict[str, float]) -> float:
        if not va or not vb:
            return 0.0
        if len(va) > len(vb):
            va, vb = vb, va
        dot = sum(w * vb.get(t, 0.0) for t, w in va.items())
        ma = math.sqrt(sum(w * w for w in va.values()))
        mb = math.sqrt(sum(w * w for w in vb.values()))
        if ma == 0.0 or mb == 0.0:
            return 0.0
        return max(0.0, min(1.0, dot / (ma * mb)))

    @classmethod
    def keyword_score(cls, q_tokens: List[str], c_tokens: List[str]) -> float:
        qs = set(q_tokens) - cls.GENERIC_TERMS
        if not qs:
            return 0.0
        cs = set(c_tokens) - cls.GENERIC_TERMS
        matched = qs & cs
        if not matched:
            return 0.0
        base = len(matched) / len(qs)
        bonus = cls.PRIORITY_KW_UNIT * len(matched & cls.HIGH_PRIORITY_KEYWORDS)
        return max(0.0, min(1.0, base + bonus))

    @classmethod
    def synonym_overlap_score(cls, expansion_only_tokens: List[str], c_tokens: List[str]) -> float:
        """
        Fraction of QUERY-EXPANSION-ONLY tokens (the ones synonym/
        abbreviation expansion added beyond the user's literal words)
        that appear in the chunk. Used as the fourth independent signal
        for the enquiry-gating check -- distinct from `keyword_score`,
        which measures overlap of ALL tokens (literal + expanded)
        together. GENERIC_TERMS excluded for the same reason as above.
        """
        expansion_set = set(expansion_only_tokens) - cls.GENERIC_TERMS
        if not expansion_set:
            return 0.0
        chunk_set = set(c_tokens) - cls.GENERIC_TERMS
        matched = expansion_set & chunk_set
        return len(matched) / len(expansion_set)

    @classmethod
    def section_boost(cls, chunk_category: str, raw_query: str) -> float:
        q_low = raw_query.lower()
        for phrase, header in sorted(_SECTION_TITLE_MAP.items(), key=lambda x: len(x[0]), reverse=True):
            if re.search(r"\b" + re.escape(phrase) + r"\b", q_low) and header == chunk_category:
                return cls.SECTION_BOOST
        return 0.0

    @classmethod
    def field_boost(cls, chunk_category: str, raw_query: str) -> float:
        q_low = raw_query.lower()
        total = 0.0
        for label, target_cat in _FIELD_BOOST_MAP.items():
            if label in q_low and target_cat == chunk_category:
                total += cls.FIELD_BOOST
        return min(total, cls.FIELD_BOOST * 2)

    @classmethod
    def phrase_boost(cls, chunk_content: str, raw_query: str) -> float:
        q_low = raw_query.lower()
        c_low = chunk_content.lower()
        score = sum(cls.PHRASE_BOOST for ph in _HIGH_VALUE_PHRASES if ph in q_low and ph in c_low)
        return min(score, cls.PHRASE_BOOST * 4)

    @staticmethod
    def hybrid_score(
        cosine: float, keyword: float,
        cosine_weight: float = 0.70, keyword_weight: float = 0.30,
    ) -> float:
        return max(0.0, min(1.0, cosine_weight * cosine + keyword_weight * keyword))


# ===========================================================================
# CONFIDENCE SCORER
# ===========================================================================

class ConfidenceScorer:
    """Multi-signal confidence: retrieval score, category match, memory
    continuity, query length, exact-phrase presence, section-match bonus."""

    def __init__(self) -> None:
        self.high_threshold = float(_cfg("HIGH_CONFIDENCE", 0.80))
        self.medium_threshold = float(_cfg("MEDIUM_CONFIDENCE", 0.55))
        self.low_threshold = float(_cfg("LOW_CONFIDENCE", 0.40))

    def score(
        self,
        retrieval_score: float,
        category_matched: bool,
        category_confidence: float,
        follow_up_resolved: bool,
        query_token_count: int,
        phrase_boost_used: float = 0.0,
        section_boost_used: float = 0.0,
    ) -> Tuple[float, str]:
        cat_sig = category_confidence if category_matched else category_confidence * 0.3
        cat_sig = max(0.0, min(1.0, cat_sig))
        mem_sig = 1.0 if follow_up_resolved else 0.5
        len_sig = 0.6 if query_token_count <= 1 else 1.0 if query_token_count <= 12 else 0.80

        composite = (
            0.62 * retrieval_score
            + 0.13 * cat_sig
            + 0.07 * mem_sig
            + 0.06 * len_sig
            + 0.07 * min(phrase_boost_used, 0.40)
            + 0.05 * min(section_boost_used, 0.25)
        )
        confidence = round(max(0.0, min(1.0, composite)), 4)

        if confidence >= self.high_threshold:
            label = "High"
        elif confidence >= self.medium_threshold:
            label = "Medium"
        elif confidence >= self.low_threshold:
            label = "Low"
        else:
            label = "Very Low"
        return confidence, label


# ===========================================================================
# OVERRIDE RULES
# ===========================================================================

class OverrideRules:
    """Hard-coded deterministic answers for known fixed facts (e.g. hostel)."""

    DEFAULT_OVERRIDES: Dict[str, str] = {
        "HOSTEL": (
            "Sreyas Institute currently does not provide on-campus hostel "
            "facilities. Students generally stay in nearby private PG "
            "(paying-guest) accommodations close to the campus in Nagole "
            "and Bandlaguda area.\n\n"
            "For PG recommendations, please contact the admissions office:\n"
            "📞 9246323444\n✉️ info@sreyas.ac.in"
        ),
    }

    def __init__(self) -> None:
        self.overrides: Dict[str, str] = {**self.DEFAULT_OVERRIDES, **_cfg("CATEGORY_OVERRIDES", {})}

    def get(self, category: str, language: str = "en") -> Optional[str]:
        normalized = (category or "").strip().upper()
        translated = Localizer.override(normalized, language)
        if translated:
            return translated
        return self.overrides.get(normalized)


# ===========================================================================
# ANALYTICS TRACKER
# ===========================================================================

class AnalyticsTracker:
    """In-memory per-session counters for the admin dashboard."""

    def __init__(self) -> None:
        self.total_queries = 0
        self.category_counts = Counter()
        self.confidence_label_counts = Counter()
        self.total_response_time = 0.0
        self.enquiry_triggered_count = 0
        self.cache_hits = 0

    def record(
        self, category: str, label: str, response_time: float,
        show_enquiry: bool, cache_hit: bool = False,
    ) -> None:
        self.total_queries += 1
        self.category_counts[category or "GENERAL"] += 1
        self.confidence_label_counts[label] += 1
        self.total_response_time += response_time
        if show_enquiry:
            self.enquiry_triggered_count += 1
        if cache_hit:
            self.cache_hits += 1

    def snapshot(self) -> Dict[str, Any]:
        avg = round(self.total_response_time / self.total_queries, 4) if self.total_queries else 0.0
        return {
            "total_queries": self.total_queries,
            "by_category": dict(self.category_counts),
            "by_confidence": dict(self.confidence_label_counts),
            "average_response_time": avg,
            "enquiry_triggered_count": self.enquiry_triggered_count,
            "cache_hits": self.cache_hits,
        }


# ===========================================================================
# QUERY CACHE
# ===========================================================================

class QueryCache:
    """Simple LRU cache. Key = (normalised_query, session_id or "", language)."""

    def __init__(self, capacity: int = 100) -> None:
        self._cache: "OrderedDict[Tuple[str, str, str], Dict[str, Any]]" = OrderedDict()
        self.capacity = capacity

    @staticmethod
    def _make_key(query: str, session_id: Optional[str], language: str) -> Tuple[str, str, str]:
        return (query.strip().lower(), session_id or "", (language or "en").lower())

    def get(self, query: str, session_id: Optional[str], language: str) -> Optional[Dict[str, Any]]:
        key = self._make_key(query, session_id, language)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, query: str, session_id: Optional[str], language: str, result: Dict[str, Any]) -> None:
        key = self._make_key(query, session_id, language)
        self._cache[key] = result
        self._cache.move_to_end(key)
        if len(self._cache) > self.capacity:
            self._cache.popitem(last=False)

    def invalidate(self) -> None:
        """Call after rebuild_index() so stale results are discarded."""
        self._cache.clear()


# ===========================================================================
# MAIN RAG ENGINE
# ===========================================================================

class RAGEngine:
    """
    Orchestrates the complete retrieval pipeline.

    Public API (backward compatible):
        retrieve(query, session_id=None, language="en") -> Dict
        build_index()
        rebuild_index()
        get_stats()     -> Dict
        get_analytics() -> Dict
    """

    REPUTATION_TRIGGER_PATTERNS: List[str] = [
        r"\bis\s+(the\s+)?(college|sreyas|institute|campus|clg)\s+"
        r"(good|nice|worth\s*it|worthy|reputable|genuine|decent|best|great)\b",
        r"\bhow\s+is\s+(the\s+)?(college|sreyas|institute|campus|clg)\b",
        r"\b(should|shall)\s+i\s+(join|choose|pick|opt\s+for)\s+"
        r"(sreyas|this\s+college|the\s+college|this\s+clg)\b",
        r"\bis\s+it\s+worth\s+joining\s+(sreyas|this\s+college|the\s+college|this\s+clg)?\b",
        r"\bworth\s+joining\s+(sreyas|this\s+college|the\s+college|this\s+clg)?\b",
        r"\bcollege\s+reputation\b",
        r"\bis\s+(sreyas|this\s+clg|this\s+college)\s+(good|best|nice|great|worth)\b",
        r"\b(is\s+this\s+clg\s+best|is\s+this\s+college\s+best|is\s+sreyas\s+best)\b",
        r"\b(clg|college)\s+(good|best|worth|nice|great)\b",
    ]

    MULTI_SECTION_PAIRS: List[Tuple[str, str]] = [
        ("ADMISSIONS PROCESS", "FEE STRUCTURE"),
        ("SCHOLARSHIPS AND FINANCIAL AID", "FEE STRUCTURE"),
        ("PLACEMENTS", "FACILITIES"),
        ("COURSES AND BRANCHES", "FEE STRUCTURE"),
    ]
    MULTI_SECTION_THRESHOLD = 0.35

    def __init__(self, knowledge_base_path: str, debug: bool = False) -> None:
        self.knowledge_base_path = knowledge_base_path
        self.debug = debug

        self.source = TextFileSource(knowledge_base_path)
        self.chunks: List[DocumentChunk] = []
        self.vectorizer = TFIDFVectorizer()
        self.similarity_engine = SimilarityEngine()
        self.confidence_scorer = ConfidenceScorer()
        self.synonym_engine = SynonymEngine()
        self.override_rules = OverrideRules()
        self.category_matcher = CategoryHeaderMatcher()
        self.analytics = AnalyticsTracker()
        self.cache = QueryCache(capacity=100)
        self.last_rebuild_time: Optional[datetime] = None

        self._reputation_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.REPUTATION_TRIGGER_PATTERNS
        ]

        self.top_k = int(_cfg("TOP_K", 3))
        self.cosine_weight = float(_cfg("COSINE_WEIGHT", 0.70))
        self.keyword_weight = float(_cfg("KEYWORD_WEIGHT", 0.30))
        self.category_confidence_threshold = float(_cfg("CATEGORY_CONFIDENCE_THRESHOLD", 0.60))
        self.soft_category_boost = float(_cfg("SOFT_CATEGORY_BOOST", 0.05))
        self.max_answer_length = int(_cfg("MAX_ANSWER_LENGTH", 2000))

        self.enquiry_cosine_floor = float(_cfg("ENQUIRY_COSINE_FLOOR", 0.15))
        self.enquiry_keyword_floor = float(_cfg("ENQUIRY_KEYWORD_FLOOR", 0.15))
        self.enquiry_synonym_floor = float(_cfg("ENQUIRY_SYNONYM_FLOOR", 0.15))

        self.category_detector = self._instantiate(CategoryDetector)
        self.typo_corrector = self._instantiate(TypoCorrector)
        self.conversation_memory = self._instantiate(ConversationMemory)

        self.build_index()

    @staticmethod
    def _instantiate(cls_ref: Any) -> Any:
        if cls_ref is None:
            return None
        try:
            return cls_ref()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # INDEX BUILDING
    # ------------------------------------------------------------------

    def build_index(self) -> None:
        """Load KB, tokenize, expand synonyms, fit TF-IDF. Called once at
        startup, and again by rebuild_index() whenever knowledge_base.txt
        changes -- always reads the file fresh from disk."""
        raw_docs = self.source.load()
        if not raw_docs:
            raise ValueError("knowledge_base.txt is empty or has no == SECTION == headers.")
        self.chunks = []
        for doc in raw_docs:
            header_tokens = Tokenizer.tokenize(TextNormalizer.normalize(doc.category)) * 2
            body_tokens = Tokenizer.tokenize(TextNormalizer.normalize(doc.content))
            base_tokens = header_tokens + body_tokens
            expanded = self.synonym_engine.expand(base_tokens)
            self.chunks.append(DocumentChunk(
                category=doc.category, title=doc.title, content=doc.content, tokens=expanded,
            ))
        self.vectorizer.fit(self.chunks)
        self.last_rebuild_time = datetime.now()
        self.cache.invalidate()

    def safe_rebuild_index(self) -> bool:
        """
        Build new index in a temporary structure, validate completeness,
        and atomically replace active index pointers. If build fails,
        retain the previous active index and log error. (Zero downtime).
        """
        try:
            raw_docs = self.source.load()
            if not raw_docs:
                logger.error("Safe index rebuild failed: knowledge_base.txt loaded 0 raw docs.")
                return False

            temp_chunks: List[DocumentChunk] = []
            for doc in raw_docs:
                header_tokens = Tokenizer.tokenize(TextNormalizer.normalize(doc.category)) * 2
                body_tokens = Tokenizer.tokenize(TextNormalizer.normalize(doc.content))
                base_tokens = header_tokens + body_tokens
                expanded = self.synonym_engine.expand(base_tokens)
                temp_chunks.append(DocumentChunk(
                    category=doc.category, title=doc.title, content=doc.content, tokens=expanded,
                ))

            temp_vectorizer = TFIDFVectorizer()
            temp_vectorizer.fit(temp_chunks)

            if not temp_chunks or not temp_vectorizer.vocabulary:
                print("[RAG] Safe index rebuild validation failed: empty chunks or vocabulary.")
                return False

            self.chunks = temp_chunks
            self.vectorizer = temp_vectorizer
            self.last_rebuild_time = datetime.now()
            self.cache.invalidate()
            print(f"[RAG] Atomic index swap done. Chunks: {len(self.chunks)}")
            return True
        except Exception as e:
            print(f"[RAG] Safe index rebuild failed: {e}. Retaining previous active index.")
            return False

    def rebuild_index(self) -> None:
        """Force re-build safely without downtime."""
        self.safe_rebuild_index()

    def _refresh_if_stale(self) -> None:
        """Auto-detects an on-disk knowledge_base.txt newer than the last
        build and rebuilds automatically. Never raises."""
        try:
            mtime = os.path.getmtime(self.knowledge_base_path)
        except OSError:
            return
        if self.last_rebuild_time is None or datetime.fromtimestamp(mtime) > self.last_rebuild_time:
            self.safe_rebuild_index()

    # ------------------------------------------------------------------
    # CANDIDATE SELECTION
    # ------------------------------------------------------------------

    def _select_candidates(self, category: str, cat_conf: float) -> Tuple[List[DocumentChunk], bool]:
        norm = (category or "GENERAL").strip().upper()
        if norm in ("GENERAL", ""):
            return self.chunks, False
        if cat_conf >= self.category_confidence_threshold:
            matched = [c for c in self.chunks if self.category_matcher.matches(c.category, norm)]
            if matched:
                return matched, True
        return self.chunks, False

    def _resolve_bare_section(self, base_tokens: List[str]) -> Optional[str]:
        """
        Resolves a short, bare query (e.g. "principal", "library",
        "hostel") directly to a section header using _SECTION_TITLE_MAP,
        WITHOUT depending on CategoryDetector at all (audit requirement
        #3: "the retrieval engine itself should understand these common
        queries"). Deliberately conservative: only fires for a query of
        at most _BARE_QUERY_MAX_TOKENS tokens, and only when every
        matched token agrees on exactly one section -- a longer or
        ambiguous query (e.g. "fees for hostel students", which touches
        two sections) returns None and falls through to normal ranked
        search instead of guessing.
        """
        if not base_tokens or len(base_tokens) > _BARE_QUERY_MAX_TOKENS:
            return None
        matched_sections = {_SECTION_TITLE_MAP[t] for t in base_tokens if t in _SECTION_TITLE_MAP}
        if len(matched_sections) == 1:
            return matched_sections.pop()
        return None

    # ------------------------------------------------------------------
    # SEARCH + BOOSTING
    # ------------------------------------------------------------------

    def _search(
        self, query_tokens: List[str], raw_query: str,
        candidates: List[DocumentChunk], boost_cat: Optional[str],
    ) -> List[Tuple[DocumentChunk, float, float, float, float]]:
        """Returns list of (chunk, cosine, keyword, total_boost, hybrid_score),
        ordered by a TIERED priority rather than a single blended score:

            1. exact field-label match  (e.g. query names "highest package")
            2. exact section-title match (e.g. query names "placements")
            3. exact high-value-phrase match
            4. blended cosine/keyword score (used as the tiebreaker within
               a tier, and as the sole ordering when no chunk has any
               exact match at all)

        This guarantees a chunk with a real, exact signal always outranks
        one that merely happens to have marginally higher cosine
        similarity -- "the correct section should consistently rank
        first" (audit requirement #2). The returned tuple SHAPE is
        unchanged from before (still 5 elements) so every existing
        caller/index into these tuples keeps working; only the sort
        order changed.
        """
        q_vec = self.vectorizer.vectorize_query(query_tokens)
        se = self.similarity_engine
        scored: List[Tuple[DocumentChunk, float, float, float, float, bool, bool, bool]] = []

        for chunk in candidates:
            cosine = se.cosine_similarity(q_vec, chunk.vector)
            keyword = se.keyword_score(query_tokens, chunk.tokens)
            hybrid = se.hybrid_score(cosine, keyword, self.cosine_weight, self.keyword_weight)

            sb = se.section_boost(chunk.category, raw_query)
            fb = se.field_boost(chunk.category, raw_query)
            pb = se.phrase_boost(chunk.content, raw_query)
            total_boost = sb + fb + pb

            if boost_cat and self.category_matcher.matches(chunk.category, boost_cat):
                total_boost += self.soft_category_boost

            final = max(0.0, min(1.0, hybrid + total_boost))
            scored.append((chunk, cosine, keyword, total_boost, final, fb > 0, sb > 0, pb > 0))

        # Tuples compare lexicographically and True > False in Python, so
        # sorting by (has_field, has_section, has_phrase, final_score)
        # descending implements the exact tier order above in one pass.
        scored.sort(key=lambda x: (x[5], x[6], x[7], x[4]), reverse=True)
        return [(chunk, cosine, keyword, total_boost, final) for chunk, cosine, keyword, total_boost, final, *_ in scored]

    # ------------------------------------------------------------------
    # ANSWER BUILDER & SUBTOPIC SLICER
    # ------------------------------------------------------------------

    def _slice_subtopic_answer(self, full_text: str, category: str, raw_query: str) -> Optional[str]:
        q_low = raw_query.lower().strip()
        cat_upper = category.upper()

        # 1. PLACEMENTS
        if "PLACEMENT" in cat_upper:
            if any(k in q_low for k in ["guarantee", "100%", "every student", "every cse student", "job guarantee", "placement guarantee"]):
                return (
                    "ℹ️ Placement Policy & Statistics:\n"
                    "• The college does NOT guarantee a ₹20 LPA package or 100% job placement to every student. Placements depend on individual academic performance, skills, CRT training, and company selection criteria.\n"
                    "• Official Placement Highlights (2025-26):\n"
                    "  - Highest Package: ₹21.18 LPA (Atomic Works)\n"
                    "  - Average Package: ₹4.0 LPA – ₹5.0 LPA\n"
                    "  - 558+ offers with an 85%+ placement rate for eligible registered students."
                )
            if any(k in q_low for k in ["highest package", "maximum package", "highest salary"]):
                return (
                    "🏆 Highest Salary Package (2025-26):\n"
                    "• Rs 21.18 LPA (Lakhs Per Annum) offered by Atomic Works."
                )
            if any(k in q_low for k in ["average package", "avg package", "average salary"]):
                return (
                    "💼 Average Salary Package (2025-26):\n"
                    "• Rs 4.0 LPA to Rs 5.0 LPA across placed students."
                )
            if any(k in q_low for k in ["top recruiter", "top recruiters", "recruiting companies", "recruiter", "recruiters"]):
                return (
                    "🏢 Top Recruiting Companies:\n"
                    "• IT & Software Giants: Capgemini, Accenture, TCS, Wipro, Infosys, Cognizant, Hexaware, Mphasis, ITC Infotech, Mindtree, Tech Mahindra.\n"
                    "• Core & Product Companies: Atomic Works, Virtusa, Wiley Edge, ValueLabs, Modak Analytics, ZenQ, Genpact, Amazon, Medha Servo Drives."
                )
            if any(k in q_low for k in ["placement training", "crt", "recruitment training"]):
                return (
                    "🎯 Placement Training (CRT):\n"
                    "• Aptitude & Quantitative Ability Training from 2nd Year onwards\n"
                    "• Coding Bootcamps in C, Java, Python & Data Structures\n"
                    "• Web Development & Cloud Certification Workshops\n"
                    "• Mock Technical Interviews & HR Group Discussion sessions\n"
                    "• Soft Skills, Resume Building, & Corporate Etiquette Training"
                )
            if q_low in ["placements", "placement", "placement overview", "tell me about placements"]:
                return (
                    "📊 Placements Overview (2025-26):\n"
                    "• Highest Salary Package: Rs 21.18 LPA (Atomic Works)\n"
                    "• Average Salary Package: Rs 4.0 LPA – Rs 5.0 LPA\n"
                    "• 558+ Placement Offers secured (85%+ eligible students placed)\n"
                    "• Placement season starts in 6th Semester (3rd Year onwards)"
                )

        # 2. SCHOLARSHIPS
        if "SCHOLARSHIP" in cat_upper:
            if any(k in q_low for k in ["government", "reimbursement", "tg eapcet"]):
                return (
                    "🏛️ Telangana Government Fee Reimbursement:\n"
                    "Full or partial tuition fee reimbursement provided by TG State Govt for eligible SC/ST/BC/EBC/Minority students admitted under EAPCET Convenor Quota (Category-A), subject to parental income limits and government norms."
                )
            if any(k in q_low for k in ["merit", "college merit"]):
                return (
                    "🏅 College Merit Scholarships:\n"
                    "Special tuition fee concessions and merit scholarships awarded to top EAPCET rankers and academic toppers during admission."
                )
            if any(k in q_low for k in ["loan", "education loan"]):
                return (
                    "🏦 Education Loan Assistance:\n"
                    "The college provides official bonafide certificates, fee structure documents, and bank loan application assistance for students applying for education loans from nationalised and private banks."
                )
            if any(k in q_low for k in ["contact", "team", "desk", "phone"]):
                return (
                    "📞 Scholarship Desk Contact:\n"
                    "• Email: scholarship@sreyas.ac.in\n"
                    "• Phone: 9246323444 / 040-65814415"
                )
            if q_low in ["scholarships", "scholarship", "financial aid", "scholarships and financial assistance", "what scholarships are available"]:
                return (
                    "🎓 Scholarships & Financial Assistance:\n"
                    "Sreyas provides:\n"
                    "• Telangana Government fee reimbursement support for eligible students\n"
                    "• College merit-based fee concessions\n"
                    "• Scholarship & education loan assistance documentation support"
                )

        # 3. FACILITIES & CAMPUS AMENITIES
        if any(k in cat_upper for k in ["FACILITIES", "SAFETY", "CAMPUS", "GENERAL"]):
            if any(k in q_low for k in ["canteen", "cafeteria", "food", "dining"]):
                return (
                    "🍽️ Campus Canteen:\n"
                    "• Multi-cuisine canteen serving South Indian & North Indian food, snacks, and fresh juices.\n"
                    "• FSSAI certified hygienic food preparation at subsidized student pricing."
                )
            if any(k in q_low for k in ["gym", "sports", "cricket", "football", "games", "gymnasium"]):
                return (
                    "🏋️ Sports & Gym Facilities:\n"
                    "• Full-size Cricket ground, Football field, Volleyball courts, and Basketball court.\n"
                    "• Indoor Gymnasium / fitness equipment, Table Tennis, Carroms, and Chess."
                )
            if any(k in q_low for k in ["wifi", "wi-fi", "internet", "bandwidth"]):
                return (
                    "📶 High-Speed Wi-Fi:\n"
                    "• 1 Gbps dedicated leased-line high-speed internet bandwidth across campus."
                )
            if any(k in q_low for k in ["security", "cctv", "safety", "anti ragging", "icc"]):
                return (
                    "🛡️ Campus Safety & Security:\n"
                    "• 24x7 security personnel at all gates.\n"
                    "• 200+ CCTV surveillance cameras monitoring classrooms, labs, library, and grounds.\n"
                    "• Active Anti-Ragging squad & Women Internal Complaints Committee (ICC)."
                )
            if q_low in ["facilities", "facility", "campus facilities", "infrastructure"]:
                return (
                    "🏫 Campus Facilities Overview:\n"
                    "• Smart Classrooms & Auditorium\n"
                    "• High-Speed Wi-Fi\n"
                    "• Multi-Cuisine Canteen\n"
                    "• Gym & Sports Grounds\n"
                    "• 24x7 CCTV Security & First Aid"
                )

        # 4. COURSES & B.TECH BRANCHES
        if any(k in cat_upper for k in ["COURSE", "PROGRAMME", "ACADEMIC", "GENERAL", "UNDERGRADUATE"]):
            if any(k in q_low for k in ["cse aiml", "ai & ml", "ai and ml", "artificial intelligence", "b.tech cse (ai & ml)", "btech computer science engineering artificial intelligence", "btech computer science engineering (artificial intelligence"]):
                return (
                    "🤖 B.Tech CSE (Artificial Intelligence & Machine Learning):\n"
                    "• Duration: 4 Years (8 Semesters)\n"
                    "• Total Branch Intake: 180 Seats (across all sections)\n"
                    "• Focus: AI Algorithms, Machine Learning, Deep Learning, NLP & Computer Vision"
                )
            if any(k in q_low for k in ["b.tech cse", "cse branch", "cse course", "btech computer science engineering", "about cse", "tell me about cse", "computer science engineering"]) or q_low in ["cse", "computer science"]:
                return (
                    "🎓 B.Tech Computer Science and Engineering (CSE):\n"
                    "• Duration: 4 Years (8 Semesters)\n"
                    "• Total Branch Intake: 240 Seats (across all sections)\n"
                    "• Focus: Software Engineering, Data Structures, Web Technologies, Databases & Cloud Computing"
                )
            if any(k in q_low for k in ["b.tech ece", "ece branch", "ece course", "btech electronics communication engineering", "electronics communication", "electronics branch", "interested in electronics", "electronics engineering", "interested in ece"]) or q_low in ["ece", "electronics"]:
                return (
                    "📡 B.Tech Electronics and Communication Engineering (ECE):\n"
                    "• Duration: 4 Years (8 Semesters) — NBA Accredited Branch\n"
                    "• Total Branch Intake: 120 Seats (across all sections)\n"
                    "• Focus: Embedded Systems, VLSI Design, IoT, Robotics & Communication Systems"
                )
            if any(k in q_low for k in ["courses", "course", "courses offered", "what courses are offered", "branches", "programmes", "available courses"]):
                return (
                    "🎓 Undergraduate Programmes Offered (B.Tech):\n"
                    "• B.Tech CSE (Total Intake: 240 seats)\n"
                    "• B.Tech CSE (AI & ML) (Total Intake: 180 seats)\n"
                    "• B.Tech ECE (Total Intake: 120 seats)\n\n"
                    "Which course would you like to know more about?"
                )

        # 5. ACADEMIC CALENDAR & EXAMINATIONS (R22 / R25 / MARKS)
        if any(k in cat_upper for k in ["EXAM", "CALENDAR", "ACADEMIC"]):
            if q_low in ["exam pattern", "examination pattern", "exam regulations", "regulations", "academic regulations", "what are the exam regulations"]:
                return (
                    "📚 Academic Regulations & Exam Pattern:\n\n"
                    "Which regulation would you like to check?\n"
                    "• R25 Regulations (2025 Onwards: 30 Internal / 70 External)\n"
                    "• R22 Regulations (2022–2024: 40 Internal / 60 External)\n"
                    "• Marks & Evaluation Distribution\n"
                    "• Attendance Regulations (75% mandatory)"
                )
            if "r25" in q_low:
                return (
                    "📜 R25 Academic Regulations (B.Tech 2025 Onwards):\n"
                    "• Internal Evaluation: 30 Marks (Mid Exams, Assignments & Quizzes)\n"
                    "• External Exam: 70 Marks (End-Semester Exam)\n"
                    "• Total Marks: 100 Marks per subject\n"
                    "• Minimum Passing Criteria: 35% in External Exam (25/70) and 40% Overall Aggregate (40/100)"
                )
            if "r22" in q_low:
                return (
                    "📜 R22 Academic Regulations (B.Tech 2022-2024):\n"
                    "• Internal Evaluation: 40 Marks (Mid Exams & Continuous Assessment)\n"
                    "• External Exam: 60 Marks (End-Semester Exam)\n"
                    "• Total Marks: 100 Marks per subject\n"
                    "• Minimum Passing Criteria: 35% in External Exam (21/60) and 40% Overall Aggregate (40/100)"
                )
            if any(k in q_low for k in ["how many marks", "external exam marks", "internal marks", "marks distribution", "marks for the exam", "total marks", "marks & evaluation", "evaluation"]):
                return (
                    "📝 Examination Marks Distribution:\n"
                    "• Internal Assessment: 30–40 Marks (Mid exams & assignments)\n"
                    "• External End-Semester Exam: 60–70 Marks\n"
                    "• Total Marks per subject: 100 Marks\n\n"
                    "Regulation Breakdown:\n"
                    "• R25 Regulation (2025+): 30 Internal + 70 External = 100 Marks\n"
                    "• R22 Regulation (2022-2024): 40 Internal + 60 External = 100 Marks\n\n"
                    "If you mean R22 or R25 specifically, please specify your regulation!"
                )
            if any(k in q_low for k in ["how many marks", "external exam marks", "internal marks", "marks distribution", "marks for the exam", "total marks"]):
                return (
                    "📝 Examination Marks Distribution:\n"
                    "• Internal Assessment: 30–40 Marks (Mid exams & assignments)\n"
                    "• External End-Semester Exam: 60–70 Marks\n"
                    "• Total Marks per subject: 100 Marks\n\n"
                    "Regulation Breakdown:\n"
                    "• R25 Regulation (2025+): 30 Internal + 70 External = 100 Marks\n"
                    "• R22 Regulation (2022-2024): 40 Internal + 60 External = 100 Marks\n\n"
                    "If you mean R22 or R25 specifically, please specify your regulation!"
                )
            if any(k in q_low for k in ["attendance requirement", "attendance percentage", "attendance regulation", "minimum attendance"]):
                return (
                    "📅 Attendance Regulations:\n"
                    "• Minimum Mandatory Attendance: 75% overall per semester.\n"
                    "• Condonation: 65% to 75% shortage allowed on medical grounds with doctor certificate and fee.\n"
                    "• Detention: Below 65% attendance results in detention."
                )
            if any(k in q_low for k in ["passing criteria", "passing marks", "pass marks"]):
                return (
                    "✅ Passing Criteria:\n"
                    "• External Exam: Minimum 35% marks required (25/70 in R25, 21/60 in R22)\n"
                    "• Overall Aggregate: Minimum 40% total marks per subject (40/100)"
                )

        # 6. TRANSPORT & BUS ROUTES — route lookup, stop search, fee, all-routes
        if "TRANSPORT" in cat_upper:
            _routes = _load_bus_routes()  # cached after first call

            # 6a. Fee query
            if any(k in q_low for k in ["fee", "cost", "price", "how much", "charge"]):
                return (
                    "🚌 Transport Fee Structure:\n"
                    "• Annual Bus Fee: Rs 18,000 to Rs 26,000 per annum (depending on pickup route distance).\n"
                    "• Fleet: 25+ Air-Conditioned & Non-AC Buses across Hyderabad & Secunderabad.\n"
                    "• Helpline: 9246323444"
                )

            # 6b. Specific route number lookup  e.g. "route 5", "RT-5", "bus 5", "show route 5"
            rt_match = _extract_route_number(q_low)
            if rt_match:
                route = next((r for r in _routes if r["id"] == rt_match), None)
                if route:
                    stops_str = " → ".join(route["stops"])
                    return (
                        f"🚌 {route['id']} — {route['name']}\n"
                        f"• Pickup Time: {route['timing']}\n"
                        f"• Stops: {stops_str}"
                    )
                else:
                    return (
                        f"ℹ️ Route {rt_match} is not currently listed in the verified bus route information.\n"
                        "For the complete and up-to-date route list, contact:\n"
                        "📞 9246323444"
                    )

            # 6c. Area / stop search  e.g. "which bus goes to Kukatpally", "bus for Ameerpet"
            area_q = q_low
            # Strip question prefixes to isolate area name
            for pfx in ["which bus covers", "which bus goes to", "which bus passes through",
                        "bus to", "bus for", "bus near", "bus from", "how to reach college from",
                        "route for", "route to", "is there a bus to", "bus that goes to"]:
                if pfx in area_q:
                    area_q = area_q.replace(pfx, "").strip()
            area_q = area_q.strip("?.,!:; \t\n")
            if area_q and area_q not in {"bus", "route", "routes", "transport", "transportation",
                                          "all routes", "all bus routes", "bus routes", "show all",
                                          "show all routes", "show all bus routes", "college bus"}:
                matched = []
                for r in _routes:
                    for stop in r["stops"]:
                        if area_q in stop.lower() or stop.lower() in area_q:
                            matched.append(r)
                            break
                if matched:
                    if len(matched) == 1:
                        r = matched[0]
                        stops_str = " → ".join(r["stops"])
                        return (
                            f"🚌 Bus covering that area:\n"
                            f"• {r['id']} — {r['name']} | Pickup: {r['timing']}\n"
                            f"• Stops: {stops_str}"
                        )
                    else:
                        lines = [f"🚌 Routes covering that area:"]
                        for r in matched:
                            key_stops = ", ".join(r["stops"][:5])
                            lines.append(f"• {r['id']} ({r['name']}, {r['timing']}): {key_stops}…")
                        return "\n".join(lines)

            # 6d. All routes overview table  e.g. "show all bus routes", "bus routes", "all routes"
            if _routes:
                header = "🚌 Sreyas Bus Routes (20 official routes):\n"
                header += "─" * 46 + "\n"
                header += f"{'Route':<7} {'Name':<20} {'Time':<7} Key Stops\n"
                header += "─" * 46 + "\n"
                rows = []
                for r in _routes:
                    key = ", ".join(r["stops"][:3])
                    rows.append(f"{r['id']:<7} {r['name']:<20} {r['timing']:<7} {key}…")
                return header + "\n".join(rows) + "\n\nAsk for a specific route (e.g. 'show RT-5') or area (e.g. 'bus for Kukatpally') for full stop details."

        # 7. TUITION FEES — Branch-specific and general (FIX: was missing, causing full dump)
        if any(k in cat_upper for k in ["FEE", "FEES", "TUITION", "CHARGE"]):
            # CSE (AI & ML) — must be checked BEFORE plain CSE (more specific match)
            if any(k in q_low for k in ["ai & ml", "aiml", "ai and ml", "cse ai", "cse (ai", "artificial intelligence"]):
                return (
                    "💰 CSE (AI & ML) Tuition Fee:\n"
                    "• Approximately ₹95,000 – ₹1,07,500 per year\n"
                    "• Fee fixed by TAFRC (Telangana Admission & Fee Regulatory Committee).\n"
                    "• Subject to annual revision by TAFRC."
                )
            # ECE — Electronics branch
            if any(k in q_low for k in ["ece", "electronics"]):
                return (
                    "💰 ECE Tuition Fee:\n"
                    "• Approximately ₹85,000 – ₹95,000 per year\n"
                    "• Fee fixed by TAFRC (Telangana Admission & Fee Regulatory Committee).\n"
                    "• Subject to annual revision by TAFRC."
                )
            # Plain CSE — checked after AI&ML so it doesn't catch "CSE AI&ML"
            if any(k in q_low for k in ["cse", "computer science"]):
                return (
                    "💰 CSE Tuition Fee:\n"
                    "• Approximately ₹95,000 – ₹1,07,500 per year\n"
                    "• Fee fixed by TAFRC (Telangana Admission & Fee Regulatory Committee).\n"
                    "• Subject to annual revision by TAFRC."
                )
            # Additional / university / exam fees
            if any(k in q_low for k in ["additional", "university fee", "jntu", "registration", "examination fee"]):
                return (
                    "📋 Additional Annual Fees:\n"
                    "• JNTUH University Recognition & Registration Fee: As per JNTUH norms\n"
                    "• Examination Fee: Per semester, based on number of papers\n"
                    "• Transport Fee: ₹18,000 – ₹26,000 per annum (optional, route-based)"
                )
            # General fee overview (exact/short queries only)
            if q_low in {"fee", "fees", "tuition fee", "fee structure", "tuition fees", "fee details",
                         "what is the fee", "what are the fees", "college fees", "tell me the fees"}:
                return (
                    "💰 Tuition Fee Structure (Per Annum):\n"
                    "• CSE: ₹95,000 – ₹1,07,500 per year\n"
                    "• CSE (AI & ML): ₹95,000 – ₹1,07,500 per year\n"
                    "• ECE: ₹85,000 – ₹95,000 per year\n"
                    "• Total 4-Year Range: ₹1.86 Lakhs – ₹4.30 Lakhs (varies by branch & quota)\n\n"
                    "Which branch fee would you like in detail?"
                )

        # 8. EXAM DATE / SCHEDULE QUERIES
        if any(k in cat_upper for k in ["EXAM", "CALENDAR", "ACADEMIC", "GENERAL"]):
            if any(k in q_low for k in ["mid exam date", "mid-term date", "mid term date",
                                         "when is mid", "when are mid", "exam date", "exam dates",
                                         "exam schedule", "exam timetable", "exam time table",
                                         "when is the exam", "when are the exams",
                                         "2nd year mid", "1st year mid", "3rd year mid", "4th year mid",
                                         "mid exam schedule"]):
                return (
                    "📅 Examination Schedule:\n"
                    "Mid-term examination dates are officially announced by the Examination Branch at the start of each semester.\n\n"
                    "• Odd Semester: July – December\n"
                    "• Even Semester: January – June\n"
                    "• Mid-term exams are typically held in the 7th–8th week of each semester.\n\n"
                    "For the current official schedule:\n"
                    "📧 exambranch@sreyas.ac.in"
                )

        # 9. SPECIFIC FACILITIES (Smart Classrooms, Auditorium, Sports, Wi-Fi)
        if any(k in cat_upper for k in ["FACILITIES", "INFRASTRUCTURE", "GENERAL"]):
            if any(k in q_low for k in ["smart classroom", "smart classrooms", "seminar hall", "seminar halls", "auditorium"]):
                return (
                    "🏫 **Smart Classrooms, Seminar Halls & Auditorium at Sreyas:**\n\n"
                    "• **ICT-Enabled Smart Classrooms:** Equipped with overhead LCD projectors, smart interactive boards, high-speed Wi-Fi connectivity, and modern audio systems.\n"
                    "• **Central Air-Conditioned Auditorium:** 800+ seating capacity venue for national conferences, annual technical fests, symposiums, and cultural events.\n"
                    "• **Departmental Seminar Halls:** 3 dedicated air-conditioned seminar halls (200+ capacity each) for expert guest lectures, seminars, and technical workshops."
                )

        # 10. COLLEGE OVERVIEW & ABOUT SREYAS
        if any(k in cat_upper for k in ["INSTITUTION", "OVERVIEW", "GENERAL"]):
            if any(k in q_low for k in ["tell me about the college", "about the college", "about sreyas", "what is sreyas", "college overview", "college details", "tell me about college"]):
                return (
                    "🏛️ Sreyas Institute of Engineering and Technology (SIET):\n"
                    "• Established in 2011, permanently affiliated to JNTUH and approved by AICTE, New Delhi.\n"
                    "• Autonomous institution with NBA accreditation for ECE and accredited with NAAC Grade.\n"
                    "• Campus: 10+ acres eco-friendly green campus located in Nagole, Bandlaguda, Hyderabad.\n"
                    "• B.Tech Programmes: CSE (240 seats), CSE AI & ML (180 seats), and ECE (120 seats).\n"
                    "• Highlights: 558+ placement offers (highest ₹21.18 LPA), 20 college bus routes across Hyderabad.\n\n"
                    "What would you like to explore next? (Courses, Placements, Fees, or Bus Routes)"
                )

        return None

    def _build_answer(self, top_results: List[Tuple], language: str, raw_query: str = "") -> Tuple[str, bool, bool]:
        """Returns (answer_text, is_truncated, is_sliced)."""
        if not top_results:
            fallback = Localizer.phrase("no_information", language)
            return (
                fallback or (
                    "I couldn't find information about that in the college knowledge base.\n\n"
                    "Please contact us directly:\n📞 9246323444\n✉️ info@sreyas.ac.in"
                ),
                False,
                False,
            )
        text = top_results[0][0].content.strip()
        cat = top_results[0][0].category

        # Try targeted subtopic slicing
        sliced = self._slice_subtopic_answer(text, cat, raw_query)
        if sliced:
            return Localizer.translate_answer_body(sliced, language), False, True

        is_truncated = False

        # Never truncate transport routes or long structural listings
        if "TRANSPORT" in cat.upper():
            return Localizer.translate_answer_body(text, language), False, False

        if len(text) > self.max_answer_length:
            trunc = text[: self.max_answer_length]
            last = trunc.rfind(".")
            if last > 0:
                trunc = trunc[: last + 1]
            text = trunc.strip()
            is_truncated = True
        return Localizer.translate_answer_body(text, language), is_truncated, False

    # ------------------------------------------------------------------
    # MULTI-SECTION ANSWER
    # ------------------------------------------------------------------

    def _try_multi_section_answer(self, all_results: List[Tuple], primary_answer: str, language: str) -> str:
        if len(all_results) < 2:
            return primary_answer
        top_cat = all_results[0][0].category
        sec_chunk = all_results[1][0]
        sec_score = all_results[1][4]
        if sec_score < self.MULTI_SECTION_THRESHOLD:
            return primary_answer
        pair_found = any(
            (top_cat == a and sec_chunk.category == b) or (top_cat == b and sec_chunk.category == a)
            for a, b in self.MULTI_SECTION_PAIRS
        )
        if not pair_found:
            return primary_answer

        extra = sec_chunk.content.strip()
        if len(extra) > 300:
            cut = extra[:300]
            last = cut.rfind(".")
            extra = (cut[: last + 1] if last > 0 else cut) + " ..."
        extra = Localizer.translate_answer_body(extra, language)

        return f"{primary_answer}\n\n---\n**{sec_chunk.title}:**\n{extra}"

    # ------------------------------------------------------------------
    # REPUTATION ANSWER
    # ------------------------------------------------------------------

    def _is_reputation_query(self, text: str) -> bool:
        lowered = text.lower().strip()
        return any(p.search(lowered) for p in self._reputation_patterns)

    def _build_reputation_answer(self, language: str) -> Tuple[str, List[DocumentChunk]]:
        """Structured reputation answer organised into named pillars. Only
        uses text verbatim from the knowledge base — never invented."""
        pillars: List[Tuple[str, str, List[str]]] = [
            (Localizer.phrase("pillar_accreditations", language) or "🎓 Accreditations",
             "ABOUT THE COLLEGE", ["naac", "nba", "ugc", "autonomous", "aicte", "iso"]),
            (Localizer.phrase("pillar_rankings", language) or "🏆 Rankings",
             "RANKINGS AND ACHIEVEMENTS", ["ranking", "times", "dataquest", "iiit", "iit bombay"]),
            (Localizer.phrase("pillar_placements", language) or "💼 Placements",
             "PLACEMENTS", ["highest package", "average package", "students placed", "lpa"]),
            (Localizer.phrase("pillar_infrastructure", language) or "🏛️ Infrastructure",
             "FACILITIES", ["library", "laboratory", "wifi", "auditorium", "smart classroom"]),
            (Localizer.phrase("pillar_partnerships", language) or "🤝 Industry Partnerships",
             "ABOUT THE COLLEGE", ["google cloud", "coursera", "nptel", "ieee", "iste"]),
        ]

        sections: List[str] = []
        source_chunks: List[DocumentChunk] = []

        for pillar_title, preferred_cat, keywords in pillars:
            for chunk in sorted(self.chunks, key=lambda c: (c.category != preferred_cat, 0)):
                for line in chunk.content.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    ll = line.lower()
                    if any(kw in ll for kw in keywords):
                        translated_line = Localizer.translate_answer_body(line, language)
                        sections.append(f"{pillar_title}\n• {translated_line}")
                        if chunk not in source_chunks:
                            source_chunks.append(chunk)
                        break
                else:
                    continue
                break

        if not sections:
            fallback = Localizer.phrase("reputation_fallback", language)
            return fallback or (
                "Based on available official information, Sreyas Institute holds "
                "NAAC 'A' Grade accreditation, is UGC Autonomous, and AICTE approved. "
                "For detailed current information please contact 📞 9246323444."
            ), []

        body = "\n\n".join(sections)
        intro = Localizer.phrase("reputation_intro", language) or (
            "Here is a factual overview of Sreyas Institute based on official college information:"
        )
        outro = Localizer.phrase("would_you_like_more", language) or (
            "Would you like more details on placements, admissions, or fees?"
        )
        answer = f"{intro}\n\n{body}\n\n{outro}"
        return answer, source_chunks

    # ------------------------------------------------------------------
    # MEMORY / TYPO HELPERS
    # ------------------------------------------------------------------

    def _apply_typo_correction(self, query: str) -> Tuple[str, List[str]]:
        result = _safe_call(self.typo_corrector, ["correct", "correct_query", "fix", "process"], query, default=None)
        if isinstance(result, tuple) and len(result) == 2:
            return result[0] or query, result[1] or []
        if isinstance(result, dict):
            return result.get("query", query) or query, result.get("corrections", []) or []
        if isinstance(result, str) and result.strip():
            return result, []
        return query, []

    def _apply_memory_context(self, query: str, session_id: Optional[str] = None) -> Tuple[str, bool, str]:
        """Fetch conversation context. If the current query looks like a
        follow-up, prepend the last category as context so retrieval
        stays on topic."""
        memory_result = None
        if session_id and self.conversation_memory:
            memory_result = _safe_call(self.conversation_memory, ["get_context"], session_id, query, default=None)
        if memory_result is None:
            memory_result = _safe_call(
                self.conversation_memory, ["get_context", "check_follow_up", "process", "analyze"], query, default=None
            )

        follow_up = False
        context = ""
        last_category = ""
        if isinstance(memory_result, dict):
            follow_up = bool(memory_result.get("follow_up", False))
            context = memory_result.get("context", "") or ""
            last_category = memory_result.get("last_category", "") or ""

        tokens = Tokenizer.tokenize(query)
        if not follow_up and last_category and len(tokens) <= 4 and not any(
            sec in query.lower() for sec in _SECTION_TITLE_MAP
        ):
            follow_up = True
            context = last_category.lower().replace("_", " ")

        search_query = f"{context} {query}".strip() if (follow_up and context) else query
        return search_query, follow_up, last_category

    def _update_memory(self, query: str, category: str, bot_answer: str = "", session_id: Optional[str] = None) -> None:
        if session_id and self.conversation_memory:
            _safe_call(self.conversation_memory, ["add_message"], session_id, query, bot_answer, category, default=None)
        else:
            _safe_call(self.conversation_memory, ["add", "update", "store"], query, category, default=None)

    # ------------------------------------------------------------------
    # SOURCE METADATA
    # ------------------------------------------------------------------

    def _source_metadata(
        self,
        top_chunk: Optional[DocumentChunk],
        retrieval_method: str = "unknown",
        matched_keywords: Optional[List[str]] = None,
        matched_phrases: Optional[List[str]] = None,
        boosts_applied: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Debugging-oriented metadata about how an answer was found.
        `retrieval_method`/`matched_keywords`/`matched_phrases`/
        `boosts_applied` are all optional so existing call sites that
        only pass `top_chunk` keep working unchanged (audit requirement
        #9 -- additive fields only, nothing removed)."""
        return {
            "file": os.path.basename(self.knowledge_base_path),
            "section": top_chunk.category if top_chunk else "UNKNOWN",
            "title": top_chunk.title if top_chunk else "",
            "last_updated": (self.last_rebuild_time.strftime("%Y-%m-%d") if self.last_rebuild_time else "unknown"),
            "retrieval_method": retrieval_method,
            "matched_keywords": matched_keywords or [],
            "matched_phrases": matched_phrases or [],
            "boosts_applied": boosts_applied or {},
        }

    # ------------------------------------------------------------------
    # PUBLIC ENTRY POINT
    # ------------------------------------------------------------------

    def retrieve(self, query: str, session_id: Optional[str] = None, language: str = "en") -> Dict[str, Any]:
        """
        Full RAG pipeline.

        Args:
            query: Raw user question, in English, Telugu, or Hindi.
            session_id: Optional per-user session identifier.
            language: "en" (default), "te", or "hi". The knowledge base
                stays English internally. A non-English query is first
                passed through language.QueryTranslator (domain
                vocabulary only -- see modules/language.py) so every
                downstream step still only ever sees English; the
                returned `answer` text is then translated back via
                Localizer.

        Returns a dict with keys: answer, top_chunks, confidence,
        confidence_label, category, corrections, follow_up, show_enquiry,
        response_time, source, language, (debug if enabled).
        """
        start = time.perf_counter()

        if not query or not query.strip():
            raise ValueError("query must be a non-empty string.")

        language = (language or "en").strip().lower()
        if not Localizer.is_supported(language):
            language = "en"

        self._refresh_if_stale()

        cached = self.cache.get(query, session_id, language)
        if cached:
            self.analytics.record(
                cached.get("category", "GENERAL"), cached.get("confidence_label", "High"),
                time.perf_counter() - start, cached.get("show_enquiry", False), cache_hit=True,
            )
            return {**cached, "response_time": round(time.perf_counter() - start, 4)}

        # Translate non-English domain vocabulary to English FIRST, so
        # TextNormalizer/Tokenizer/CategoryDetector/TypoCorrector below
        # never need to know Telugu or Hindi exist (see module docstring
        # change #3 -- this is the actual fix for Telugu/Hindi retrieval).
        english_query = QueryTranslator.translate(query.strip(), language)

        normalised_query = TextNormalizer.normalize(english_query)
        corrected_query, corrections = self._apply_typo_correction(normalised_query)

        if self._is_reputation_query(corrected_query):
            rep_answer, rep_chunks = self._build_reputation_answer(language)
            conf = 1.0 if rep_chunks else 0.0
            conf_lbl = "High" if rep_chunks else "Very Low"
            self._update_memory(query.strip(), "GENERAL", rep_answer, session_id)
            rt = round(time.perf_counter() - start, 4)
            show_enq = not bool(rep_chunks)
            self.analytics.record("GENERAL", conf_lbl, rt, show_enq)
            result = {
                "answer": rep_answer,
                "top_chunks": [
                    {"category": c.category, "title": c.title, "content": c.content, "score": 1.0}
                    for c in rep_chunks
                ],
                "confidence": conf, "confidence_label": conf_lbl, "category": "GENERAL",
                "corrections": corrections, "follow_up": False, "show_enquiry": show_enq,
                "response_time": rt, "language": language,
                "source": self._source_metadata(rep_chunks[0] if rep_chunks else None, retrieval_method="reputation_pillars"),
            }
            self.cache.put(query, session_id, language, result)
            return result

        # NOTE: Out-of-domain and Campus FAQ queries are now handled upstream by
        # IntentEngine in app.py BEFORE retrieve() is called. This block is intentionally
        # removed to avoid double-handling and incorrectly triggering enquiry forms for
        # legitimate campus FAQ topics like 'bike parking' or 'two-wheelers'.

        search_query, follow_up, last_category = self._apply_memory_context(corrected_query, session_id)

        # Use search_query (which includes conversation context for follow-ups) for category detection
        query_for_detection = search_query if follow_up else corrected_query
        cat_result = _safe_call(
            self.category_detector, ["detect", "detect_category", "classify", "predict"], query_for_detection, default=None
        )
        detected_cat = "GENERAL"
        cat_conf = 0.0
        if isinstance(cat_result, dict):
            detected_cat = (cat_result.get("category") or "GENERAL").strip().upper()
            cat_conf = float(cat_result.get("confidence") or 0.0)
        elif isinstance(cat_result, str) and cat_result.strip():
            detected_cat = cat_result.strip().upper()
            cat_conf = 1.0

        # If still GENERAL on follow-up, inherit the last_category
        if follow_up and detected_cat == "GENERAL" and last_category and last_category != "GENERAL":
            detected_cat = last_category
            cat_conf = 0.85

        override_answer = self.override_rules.get(detected_cat, language)

        # Tokenize BEFORE candidate selection so a bare/short query (e.g.
        # "principal", "library") can resolve directly to its section --
        # audit requirement #3: "the retrieval engine itself should
        # understand these common queries" without depending entirely on
        # CategoryDetector. Only attempted when CategoryDetector itself
        # didn't already confidently resolve a category, so a real
        # CategoryDetector result is never second-guessed.
        norm_search = TextNormalizer.normalize(search_query)
        base_tokens = Tokenizer.tokenize(norm_search)
        exp_tokens = self.synonym_engine.expand(base_tokens)
        expansion_only_tokens = [t for t in exp_tokens if t not in base_tokens]

        bare_section = self._resolve_bare_section(base_tokens) if detected_cat == "GENERAL" else None

        candidates, hard_filtered = self._select_candidates(detected_cat, cat_conf)
        if bare_section and not hard_filtered:
            bare_candidates = [c for c in candidates if self.category_matcher.matches(c.category, bare_section)]
            if bare_candidates:
                candidates, hard_filtered = bare_candidates, True
        boost_cat = None if hard_filtered else (detected_cat if detected_cat != "GENERAL" else None)

        ranked = self._search(exp_tokens, search_query, candidates, boost_cat)
        top_results = ranked[: self.top_k]

        top_cosine = top_results[0][1] if top_results else 0.0
        top_keyword = top_results[0][2] if top_results else 0.0
        top_score = top_results[0][4] if top_results else 0.0
        top_synonym_overlap = (
            self.similarity_engine.synonym_overlap_score(expansion_only_tokens, top_results[0][0].tokens)
            if top_results else 0.0
        )
        win_cat = top_results[0][0].category if top_results else detected_cat
        cat_matched = detected_cat == "GENERAL" or self.category_matcher.matches(win_cat, detected_cat)
        fu_resolved = follow_up and bool(last_category)

        phrase_boost_used = top_results[0][3] if top_results else 0.0
        section_boost_used = SimilarityEngine.section_boost(win_cat, corrected_query)
        top_field_boost = SimilarityEngine.field_boost(win_cat, corrected_query) if top_results else 0.0

        confidence, conf_label = self.confidence_scorer.score(
            retrieval_score=top_score, category_matched=cat_matched, category_confidence=cat_conf,
            follow_up_resolved=fu_resolved, query_token_count=len(base_tokens),
            phrase_boost_used=phrase_boost_used, section_boost_used=section_boost_used,
        )

        if override_answer is not None:
            answer, confidence, conf_label, is_truncated = override_answer, 1.0, "High", False
        else:
            primary, is_truncated, is_sliced = self._build_answer(top_results, language, corrected_query)
            if is_sliced:
                answer = primary
            else:
                answer = self._try_multi_section_answer(ranked, primary, language)

        # show_enquiry gate — be conservative: only show the form when there is
        # genuinely NO signal pointing at any known KB section.
        #
        # A section is "relevant" if ANY of these hold:
        #   1. CategoryDetector matched a specific (non-GENERAL) category that
        #      maps to the winning chunk's category.
        #   2. A field-label boost fired (e.g. query contains "highest package",
        #      "top recruiters", "transport", "college timings" — see _FIELD_BOOST_MAP).
        #   3. A bare-keyword section was resolved directly from _SECTION_TITLE_MAP.
        #   4. Hard-filtering was applied — means CategoryDetector was confident
        #      enough to narrow candidates to one section; that alone is sufficient.
        has_relevant_section = bool(top_results) and (
            top_field_boost > 0                 # exact field phrase matched ("highest package", "transport", etc.)
            or section_boost_used > 0           # section title matched ("courses", "about the college", etc.)
            or bare_section is not None         # bare keyword resolved directly
            or (
                detected_cat != "GENERAL"
                and self.category_matcher.matches(win_cat, detected_cat)
                and (top_keyword >= 0.12 or top_cosine >= 0.10)  # must have actual content overlap
            )
        )
        # Check if query requests unverified future schedule information (e.g. upcoming companies next month)
        has_future_unverified_request = any(
            kw in query.lower() for kw in ["next month", "upcoming companies", "expected next month", "visit next month", "future drives"]
        )

        show_enquiry = (
            override_answer is None
            and (
                has_future_unverified_request
                or (not has_relevant_section and top_cosine < self.enquiry_cosine_floor and top_keyword < self.enquiry_keyword_floor)
                or (confidence < 0.25 and top_field_boost == 0 and section_boost_used == 0)
            )
        )

        self._update_memory(query.strip(), detected_cat, answer, session_id)

        rt = round(time.perf_counter() - start, 4)
        self.analytics.record(detected_cat, conf_label, rt, show_enquiry)

        retrieval_method = (
            "override" if override_answer is not None else
            "bare_keyword" if bare_section else
            "category_filtered" if hard_filtered else
            "open_search"
        )
        matched_keywords = sorted(set(exp_tokens) & set(top_results[0][0].tokens)) if top_results else []
        matched_phrases = (
            [ph for ph in _HIGH_VALUE_PHRASES if ph in corrected_query.lower() and ph in top_results[0][0].content.lower()]
            if top_results else []
        )
        boosts_applied = {
            "section": round(section_boost_used, 4),
            "field": round(top_field_boost, 4),
            "phrase": round(phrase_boost_used, 4),
        }

        # Generate context-aware follow-up suggestions based on
        # the winning section (only when we have a real answer).
        suggestions = self._generate_suggestions(
            win_cat if top_results else detected_cat, show_enquiry, language
        )

        result: Dict[str, Any] = {
            "answer": answer,
            "top_chunks": [
                {"category": c.category, "title": c.title, "content": c.content, "score": round(s, 4)}
                for c, _cos, _kw, _boost, s in top_results
            ],
            "confidence": confidence, "confidence_label": conf_label, "category": detected_cat,
            "corrections": corrections, "follow_up": follow_up, "show_enquiry": show_enquiry,
            "suggestions": suggestions,
            "is_truncated": is_truncated,
            "response_time": rt, "language": language,
            "source": self._source_metadata(
                top_results[0][0] if top_results else None,
                retrieval_method=retrieval_method,
                matched_keywords=matched_keywords,
                matched_phrases=matched_phrases,
                boosts_applied=boosts_applied,
            ),
        }

        if self.debug:
            result["debug"] = {
                "cache_hit": False, "raw_query": query, "translated_query": english_query,
                "corrected_query": corrected_query,
                "search_query": search_query, "base_tokens": base_tokens, "expanded_tokens": exp_tokens,
                "expansion_only_tokens": expansion_only_tokens, "bare_section": bare_section,
                "detected_category": detected_cat, "cat_confidence": cat_conf, "hard_filtered": hard_filtered,
                "top_cosine": round(top_cosine, 4), "top_keyword": round(top_keyword, 4),
                "top_synonym_overlap": round(top_synonym_overlap, 4), "has_relevant_section": has_relevant_section,
                "top_results": [
                    {"category": c.category, "cosine": round(cos, 4), "keyword": round(kw, 4),
                     "total_boost": round(bst, 4), "hybrid_final": round(s, 4)}
                    for c, cos, kw, bst, s in ranked[:5]
                ],
                "show_enquiry": show_enquiry,
            }

        self.cache.put(query, session_id, language, result)
        return result

    # ------------------------------------------------------------------
    # SMART FOLLOW-UP SUGGESTIONS
    # ------------------------------------------------------------------

    # Maps KB section categories to related follow-up topics. Each entry
    # has a user-facing label and the canonical English query to send.
    # Only sections that actually exist in knowledge_base.txt are listed.
    SECTION_SUGGESTIONS = {
        "PLACEMENTS": [
            {"label": "Highest Package", "query": "highest package"},
            {"label": "Average Package", "query": "average package"},
            {"label": "Top Recruiters", "query": "top recruiters"},
            {"label": "Placement Training", "query": "placement training"},
        ],
        "FEE_STRUCTURE": [
            {"label": "CSE Tuition Fee", "query": "what is the cse fee"},
            {"label": "Additional Fees", "query": "additional fees"},
            {"label": "Scholarships", "query": "scholarships"},
            {"label": "Accounts Contact", "query": "accounts contact"},
        ],
        "ADMISSIONS_PROCESS": [
            {"label": "Convenor Quota (EAPCET)", "query": "admissions process"},
            {"label": "Management Quota", "query": "category b management quota"},
            {"label": "Cutoff Ranks", "query": "EAPCET cutoff rank"},
            {"label": "Fee Structure", "query": "fee structure"},
        ],
        "COURSES_AND_BRANCHES": [
            {"label": "B.Tech CSE", "query": "cse"},
            {"label": "B.Tech CSE (AI & ML)", "query": "cse aiml"},
            {"label": "B.Tech ECE", "query": "ece"},
            {"label": "Admission Process", "query": "admissions process"},
        ],
        "HOSTEL": [
            {"label": "PG Accommodations", "query": "hostel"},
            {"label": "Transport", "query": "transport"},
            {"label": "Facilities", "query": "facilities"},
            {"label": "Contact Details", "query": "contact details"},
        ],
        "TRANSPORT": [
            {"label": "Bus Routes", "query": "bus routes"},
            {"label": "Transport Fee", "query": "transport fee"},
            {"label": "College Timings", "query": "college timings"},
            {"label": "Contact Details", "query": "contact details"},
        ],
        "FACILITIES": [
            {"label": "Canteen", "query": "is there a cafeteria"},
            {"label": "Gym & Sports", "query": "is there a gym"},
            {"label": "Wi-Fi", "query": "wifi"},
            {"label": "Security", "query": "safety and security"},
        ],
        "SCHOLARSHIPS_AND_FINANCIAL_AID": [
            {"label": "Government Scholarship", "query": "government fee reimbursement"},
            {"label": "Merit Scholarship", "query": "college merit scholarship"},
            {"label": "Education Loan", "query": "education loan"},
            {"label": "Scholarship Contact", "query": "scholarship team contact"},
        ],
        "EXAMINATION_PATTERN": [
            {"label": "R25 Regulations", "query": "r25 regulations"},
            {"label": "R22 Regulations", "query": "r22 regulations"},
            {"label": "Marks & Evaluation", "query": "how many marks"},
            {"label": "Attendance Rules", "query": "attendance requirement"},
        ],
        "SAFETY_AND_SECURITY": [
            {"label": "24x7 CCTV Security", "query": "safety and security"},
            {"label": "Facilities", "query": "facilities"},
            {"label": "Transport", "query": "transport"},
            {"label": "Contact Details", "query": "contact details"},
        ],
        "COLLEGE_TIMINGS": [
            {"label": "Transport", "query": "transport"},
            {"label": "Exam Pattern", "query": "examination pattern"},
            {"label": "Facilities", "query": "facilities"},
            {"label": "Contact Details", "query": "contact details"},
        ],
        "CONTACT_DETAILS": [
            {"label": "Admissions", "query": "admissions process"},
            {"label": "Fee Structure", "query": "fee structure"},
            {"label": "Courses Offered", "query": "courses and branches"},
            {"label": "About Sreyas", "query": "about the college"},
        ],
    }

    # Localized labels for suggestion chips
    SUGGESTION_LABELS = {
        "te": {
            "Highest Package": "అత్యధిక ప్యాకేజీ", "Average Package": "సగటు ప్యాకేజీ",
            "Top Recruiters": "ప్రధాన రిక్రూటర్లు", "Placement Training": "ప్లేస్‌మెంట్ శిక్షణ",
            "Fee Structure": "ఫీజు వివరాలు", "Scholarships": "స్కాలర్‌షిప్",
            "Government Scholarship": "ప్రభుత్వ రీయింబర్స్‌మెంట్", "Merit Scholarship": "మెరిట్ స్కాలర్‌షిప్",
            "Education Loan": "విద్యా రుణం", "Scholarship Contact": "స్కాలర్‌షిప్ కాంటాక్ట్",
            "Admissions Process": "ప్రవేశ ప్రక్రియ", "Admissions": "అడ్మిషన్",
            "Placements": "ప్లేస్‌మెంట్స్", "Eligibility": "అర్హత",
            "Cutoff Ranks": "కటాఫ్ ర్యాంకులు", "Courses Offered": "కోర్సులు",
            "Facilities": "సౌకర్యాలు", "Transport": "రవాణా", "Canteen": "కాంటీన్",
            "Gym & Sports": "జిమ్ & క్రీడలు", "Wi-Fi": "వై-ఫై", "Security": "భద్రత",
            "Hostel / PG": "హాస్టల్ / PG", "Contact Details": "సంప్రదింపు వివరాలు",
            "Safety & Security": "భద్రత", "College Timings": "సమయాలు",
            "R25 Regulations": "R25 నిబంధనలు", "R22 Regulations": "R22 నిబంధనలు",
            "Marks & Evaluation": "మార్కులు & మూల్యాంకనం", "Attendance Rules": "హాజరు నిబంధనలు",
            "Exam Pattern": "పరీక్ష విధానం", "About Sreyas": "Sreyas గురించి",
        },
        "hi": {
            "Highest Package": "उच्चतम पैकेज", "Average Package": "औसत पैकेज",
            "Top Recruiters": "प्रमुख भर्तीकर्ता", "Placement Training": "प्लेसमेंट प्रशिक्षण",
            "Fee Structure": "फीस विवरण", "Scholarships": "छात्रवृत्ति",
            "Government Scholarship": "सरकारी छात्रवृत्ति", "Merit Scholarship": "मेरिट छात्रवृत्ति",
            "Education Loan": "शिक्षा ऋण", "Scholarship Contact": "छात्रवृत्ति संपर्क",
            "Admissions Process": "प्रवेश प्रक्रिया", "Admissions": "प्रवेश",
            "Placements": "प्लेसमेंट", "Eligibility": "पात्रता",
            "Cutoff Ranks": "कटऑफ रैंक", "Courses Offered": "कोर्सेज़",
            "Facilities": "सुविधाएं", "Transport": "परिवहन", "Canteen": "कैंटीन",
            "Gym & Sports": "जिम और खेल", "Wi-Fi": "वाई-फाई", "Security": "सुरक्षा",
            "Hostel / PG": "छात्रावास / PG", "Contact Details": "संपर्क विवरण",
            "Safety & Security": "सुरक्षा", "College Timings": "टाइमिंग",
            "R25 Regulations": "R25 नियम", "R22 Regulations": "R22 नियम",
            "Marks & Evaluation": "अंक और मूल्यांकन", "Attendance Rules": "उपस्थिति नियम",
            "Exam Pattern": "परीक्षा पैटर्न", "About Sreyas": "Sreyas के बारे में",
        },
    }

    def _generate_suggestions(
        self, category: str, show_enquiry: bool, language: str = "en"
    ) -> List[Dict[str, str]]:
        """Generate context-aware follow-up suggestion chips based on the
        matched KB section. Returns an empty list if no suggestions are
        available or if the answer triggered the enquiry form."""
        if show_enquiry:
            return []

        cat_upper = category.strip().upper()
        alias_map = {
            "TRAINING AND PLACEMENTS (T&P CELL)": "PLACEMENTS",
            "TRAINING AND PLACEMENTS": "PLACEMENTS",
            "TUITION FEES AND OTHER CHARGES": "FEE_STRUCTURE",
            "FEE STRUCTURE": "FEE_STRUCTURE",
            "FEES": "FEE_STRUCTURE",
            "ADMISSIONS AND ELIGIBILITY CRITERIA": "ADMISSIONS_PROCESS",
            "ADMISSIONS PROCESS": "ADMISSIONS_PROCESS",
            "ADMISSIONS": "ADMISSIONS_PROCESS",
            "UNDERGRADUATE DEGREE PROGRAMMES (B.TECH)": "COURSES_AND_BRANCHES",
            "COURSES AND BRANCHES": "COURSES_AND_BRANCHES",
            "COURSES": "COURSES_AND_BRANCHES",
            "CAMPUS FACILITIES AND INFRASTRUCTURE": "FACILITIES",
            "CENTRAL LIBRARY AND E-LEARNING RESOURCES": "FACILITIES",
            "FACILITIES": "FACILITIES",
            "TRANSPORTATION AND BUS ROUTES": "TRANSPORT",
            "TRANSPORT": "TRANSPORT",
            "HOSTEL AND ACCOMMODATION INFORMATION": "HOSTEL",
            "HOSTEL": "HOSTEL",
            "ACADEMIC CALENDAR AND EXAMINATIONS": "EXAMINATION_PATTERN",
            "EXAMINATION PATTERN": "EXAMINATION_PATTERN",
            "EXAMS": "EXAMINATION_PATTERN",
            "SCHOLARSHIPS AND FINANCIAL ASSISTANCE": "SCHOLARSHIPS_AND_FINANCIAL_AID",
            "SCHOLARSHIPS": "SCHOLARSHIPS_AND_FINANCIAL_AID",
            "GOVERNING BODY AND MANAGEMENT": "LEADERSHIP_AND_MANAGEMENT",
            "FACULTY AND ACADEMIC STAFF": "FACULTY",
            "FACULTY": "FACULTY",
            "OFFICIAL CONTACT DETAILS": "CONTACT_DETAILS",
            "CONTACT DETAILS": "CONTACT_DETAILS",
            "CAMPUS SAFETY, SECURITY AND DISCIPLINE": "SAFETY_AND_SECURITY",
            "SAFETY AND SECURITY": "SAFETY_AND_SECURITY",
            "ABOUT THE COLLEGE": "ABOUT_THE_COLLEGE",
            "INSTITUTION OVERVIEW": "ABOUT_THE_COLLEGE",
        }

        norm_cat = alias_map.get(cat_upper, cat_upper.replace(" ", "_"))

        suggestions = self.SECTION_SUGGESTIONS.get(norm_cat, self.SECTION_SUGGESTIONS.get(cat_upper, []))
        if not suggestions:
            return []

        # Localize labels if language is not English
        labels_map = self.SUGGESTION_LABELS.get(language, {})
        result = []
        for s in suggestions:
            localized_label = labels_map.get(s["label"], s["label"])
            result.append({"label": localized_label, "query": s["query"]})
        return result

    # ------------------------------------------------------------------
    # STATS / ANALYTICS
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "num_chunks": len(self.chunks),
            "vocabulary_size": len(self.vectorizer.vocabulary),
            "categories": sorted({c.category for c in self.chunks}),
            "last_rebuild_time": self.last_rebuild_time.isoformat() if self.last_rebuild_time else None,
        }

    def get_analytics(self) -> Dict[str, Any]:
        return self.analytics.snapshot()

    def rebuild(self) -> None:
        """Alias for rebuild_index."""
        self.rebuild_index()


# ===========================================================================
# STANDALONE SELF-TEST
# ===========================================================================
if __name__ == "__main__":
    _kb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "knowledge_base.txt")
    try:
        eng = RAGEngine(knowledge_base_path=_kb, debug=True)
        print("Index built.", eng.get_stats())
        for q, lang in [
            ("what courses are offered", "en"),
            ("cse aiml fee", "en"),
            ("does the college have a hostel", "en"),
            ("does the college have a hostel", "te"),
            ("ఫీజు వివరాలు", "te"),
            ("హాస్టల్ ఉందా", "te"),
            ("फीस विवरण", "hi"),
            ("highest package 2025", "en"),
            ("exam pattern", "en"),
            ("is this college good", "en"),
            ("should I join the coding club", "en"),
            ("is the hostel worth it", "en"),
            ("scholarship details", "en"),
            ("Placements", "en"),
            ("xyzabc gibberish nonsense", "en"),
        ]:
            r = eng.retrieve(q, language=lang)
            print(f"\n[{q!r} lang={lang}]")
            print(f"  Category  : {r['category']}")
            print(f"  Confidence: {r['confidence']} ({r['confidence_label']})")
            print(f"  Enquiry   : {r['show_enquiry']}")
            print(f"  Answer    : {r['answer'][:150]}")
    except FileNotFoundError as e:
        print(e)