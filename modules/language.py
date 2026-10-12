"""
modules/language.py
================================================================================
All multilingual behaviour for the SREYAS Assistant lives here, split out
of rag.py so that file can stay focused on retrieval/ranking/confidence
(per the architecture review: "language translation inside rag.py is the
wrong place for it").

Two independent, single-purpose classes:

    Localizer        -- translates rag.py's OWN OUTPUT text (template
                         phrases, recognized "Label:" prefixes, AND
                         common knowledge-base value phrases) into
                         Telugu/Hindi. The knowledge base itself stays
                         English-only; Localizer translates rag.py's
                         generated wrapper text, known field labels, and
                         known value phrases that appear in answers.

    QueryTranslator   -- the actual fix for "asked ఫీజు వివరాలు, got an
                         English-language-detector miss": a fixed
                         domain-vocabulary dictionary that rewrites the
                         ~80 Telugu/Hindi words that matter for this
                         knowledge base (fee, hostel, admission,
                         scholarship, ...) into English BEFORE the query
                         ever reaches TextNormalizer / Tokenizer /
                         CategoryDetector / TypoCorrector in rag.py. None
                         of those files need to know any Telugu or Hindi
                         at all -- they only ever see English, exactly
                         like today.

HONESTY NOTE (both classes): these are dictionaries, not machine
translation. Localizer's LABEL_TRANSLATIONS covers ~30 of the knowledge
base's 100+ field labels; VALUE_TRANSLATIONS covers ~80 common value
phrases per language; QueryTranslator's vocabulary covers ~80 domain
words per language. Anything outside that set passes through unchanged
rather than showing a guessed, unverified translation -- an unlisted
Telugu word simply won't get translated to English (so it likely won't
match anything, same as today), and an unlisted label or value phrase in
an answer stays in English. All dictionaries live in
config/translations.json purely so a Telugu/Hindi speaker can extend them
without touching any Python.

For open-domain translation quality beyond a fixed vocabulary, swap
QueryTranslator.translate()'s implementation for a real MT API (Google
Cloud Translation, Bhashini, IndicTrans2) -- its call signature is the
integration point; nothing in rag.py needs to change either way.
================================================================================
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

SUPPORTED_LANGUAGES = frozenset({"en", "te", "hi"})

_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "translations.json"
)


def _load_translation_config(path: Optional[str] = None) -> Dict:
    """Loads config/translations.json. Missing or corrupt config degrades
    to empty dictionaries (everything falls back to English) rather than
    crashing the app -- a translation file problem should never take the
    chatbot down."""
    load_path = path or _DEFAULT_CONFIG_PATH
    try:
        with open(load_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


_CONFIG = _load_translation_config()


class Localizer:
    """Translates rag.py's own generated wrapper text into Telugu/Hindi.
    See module docstring for the accuracy-over-coverage design note."""

    SUPPORTED_LANGUAGES = SUPPORTED_LANGUAGES

    TEMPLATE_PHRASES: Dict[str, Dict[str, str]] = _CONFIG.get("template_phrases", {})
    LABEL_TRANSLATIONS: Dict[str, Dict[str, str]] = _CONFIG.get("label_translations", {})
    OVERRIDE_TRANSLATIONS: Dict[str, Dict[str, str]] = _CONFIG.get("override_translations", {})

    # ── Value-level phrase translations (multilingual bug fix) ─────────
    # Translates common English value phrases that appear in knowledge-base
    # answers (the text AFTER the "Label:" colon, and plain-text lines).
    # Loaded from config/translations.json → "value_translations".
    # Structure: { "te": { "English phrase": "Telugu" }, "hi": { ... } }
    _RAW_VALUE_TRANSLATIONS: Dict[str, Dict[str, str]] = _CONFIG.get("value_translations", {})

    # Pre-sort each language's phrases longest-first so multi-word entries
    # are substituted before their shorter sub-phrases, preventing partial
    # replacement conflicts (same strategy as QueryTranslator below).
    _SORTED_VALUE_PHRASES: Dict[str, List[str]] = {
        lang: sorted(phrases.keys(), key=len, reverse=True)
        for lang, phrases in _RAW_VALUE_TRANSLATIONS.items()
    }

    _LABEL_LINE_RE = re.compile(r"^(\s*(?:[•\-]\s*)?)([^:]{2,60}):\s*(.*)$")

    @classmethod
    def is_supported(cls, language: str) -> bool:
        return (language or "en").strip().lower() in cls.SUPPORTED_LANGUAGES

    @classmethod
    def phrase(cls, key: str, language: str) -> str:
        """Returns TEMPLATE_PHRASES[key] in `language`, or "" if absent
        (callers always have an English literal fallback, so this is only
        consulted for te/hi)."""
        language = (language or "en").strip().lower()
        return cls.TEMPLATE_PHRASES.get(key, {}).get(language, "")

    @classmethod
    def override(cls, category: str, language: str) -> Optional[str]:
        language = (language or "en").strip().lower()
        return cls.OVERRIDE_TRANSLATIONS.get((category or "").strip().upper(), {}).get(language)

    @classmethod
    def localized(cls, key: str, language: str, english_fallback: str = "") -> str:
        """Returns a template phrase in the requested language, falling back
        to `english_fallback` for English or when no translation exists."""
        language = (language or "en").strip().lower()
        if language == "en":
            return english_fallback
        return cls.phrase(key, language) or english_fallback

    @classmethod
    def translate_answer_body(cls, text: str, language: str) -> str:
        """Multi-pass translation of answer text into Telugu/Hindi.

        Step 1 — Value phrase pre-pass (longest-first):
                 Replace known English value phrases FIRST (before label
                 matching) so multi-word phrases embedded in any line are
                 caught early. This covers plain-text bullet lines like
                 '- Sreyas Institute offers 4-Year Full-Time B.Tech…' that
                 do NOT match the Label: Value pattern.

        Step 2 — Label translation:
                 Every recognized 'Label: Value' line's label is replaced
                 with its Telugu/Hindi equivalent from LABEL_TRANSLATIONS.

        Lines/phrases not in any dictionary pass through unchanged
        (safe no-op, consistent with the honesty design principle)."""
        language = (language or "en").strip().lower()
        if language not in ("te", "hi"):
            return text

        phrases_map = cls._RAW_VALUE_TRANSLATIONS.get(language, {})
        sorted_phrases = sorted(phrases_map.keys(), key=len, reverse=True)

        # ── Step 1: Full-text phrase substitution (longest-first) ─────
        result = text
        for eng_phrase in sorted_phrases:
            translated = phrases_map.get(eng_phrase)
            if translated and eng_phrase in result:
                result = result.replace(eng_phrase, translated)

        # ── Step 2: Translate Label: Value line labels ─────────────────
        out: List[str] = []
        for line in result.split("\n"):
            match = cls._LABEL_LINE_RE.match(line)
            if not match:
                out.append(line)
                continue
            prefix, label, value = match.groups()
            label_key = label.strip().lower()
            label_tr = cls.LABEL_TRANSLATIONS.get(label_key, {}).get(language)
            if label_tr:
                val_part = f" {value.strip()}" if value and value.strip() else ""
                out.append(f"{prefix}{label_tr}:{val_part}")
            else:
                out.append(line)

        return "\n".join(out)


class QueryTranslator:
    """Rewrites Telugu/Hindi domain words in a query to English, word by
    word, using the dictionary in config/translations.json's
    'query_translation' section. See module docstring for scope/limits."""

    VOCAB: Dict[str, Dict[str, str]] = _CONFIG.get("query_translation", {})

    # Sort each language's phrases longest-first so multi-word entries
    # (e.g. "ఫీజు వివరాలు" -> "fee structure") are substituted before their
    # shorter constituent words, instead of the multi-word phrase never
    # matching because a shorter piece already got replaced first.
    _sorted_phrases: Dict[str, List[str]] = {
        lang: sorted(words.keys(), key=len, reverse=True) for lang, words in VOCAB.items()
    }

    @classmethod
    def translate(cls, text: str, language: str) -> str:
        """Best-effort word/phrase substitution of known domain
        vocabulary into English. Returns `text` unchanged for English or
        an unsupported/unknown language, or if no vocabulary entries
        match (safe no-op, never raises)."""
        language = (language or "en").strip().lower()
        if language not in cls.VOCAB or not text:
            return text

        result = text
        word_map = cls.VOCAB[language]
        for phrase in cls._sorted_phrases.get(language, []):
            if phrase in result:
                result = result.replace(phrase, f" {word_map[phrase]} ")
        return re.sub(r"\s+", " ", result).strip()