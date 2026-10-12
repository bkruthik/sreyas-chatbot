"""
scripts/sync_knowledge_base.py
================================================================================
Production Knowledge-Base Synchronizer — SREYAS Assistant

ARCHITECTURE
------------
Official Website
      |
WebsiteFetcher  <-- retrying HTTP session, per-run page cache
      |
PageDiscovery   <-- discovers URLs from config + optional sitemap
      |
SectionExtractor <-- extracts facts per section using config-driven patterns
      |
Validator        <-- rejects malformed/invalid values before they touch the KB
      |
KnowledgeBaseUpdater <-- section-level merge, atomic write, backup
      |
RAGRebuilder     <-- calls rag_engine.rebuild_index() only when KB changed
      |
SyncStatistics   <-- structured result returned to scheduler.py / CLI

RUNNING
-------
    python scripts/sync_knowledge_base.py              # normal run
    python scripts/sync_knowledge_base.py --dry-run    # no file writes
    python scripts/sync_knowledge_base.py -v           # verbose logging

CRON / SCHEDULER
----------------
scheduler.py calls run_sync() automatically every N hours.

RELIABILITY
-----------
• Retries with exponential backoff on network errors.
• Atomic writes (temp file + os.replace).
• Timestamped backup before every real write.
• Malformed/invalid values are dropped — KB never corrupted.
• Unknown errors are caught per-section; remaining sections still run.
• If NOTHING changes → no write, no backup, no RAG rebuild.

CROSS-PLATFORM
--------------
No absolute paths. No hardcoded drive letters.
All paths resolved relative to __file__ via pathlib.
Works on Windows, Linux, macOS, Docker, Render, Railway, AWS, Azure, VPS.
================================================================================
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

# ---------------------------------------------------------------------------
# Optional dependencies — fallback to urllib and html.parser if missing
# ---------------------------------------------------------------------------
try:
    import requests
    from requests.adapters import HTTPAdapter
    from requests.exceptions import RequestException
    HAS_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.error
    HAS_REQUESTS = False
    class RequestException(Exception): pass

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    from html.parser import HTMLParser
    HAS_BS4 = False

    class SimpleHTMLParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []
        def handle_data(self, data):
            self.text.append(data)
        def get_text(self, separator="\n"):
            return separator.join(self.text)

    class DummySoup:
        def __init__(self, html):
            p = SimpleHTMLParser()
            p.feed(html)
            self._text = p.get_text()
        def get_text(self, separator="\n"):
            return self._text
        def select(self, selector):
            return []
        def __call__(self, tags):
            return []
        def decompose(self):
            pass
        def __str__(self):
            return self._text

    def BeautifulSoup(html, parser="html.parser"):
        return DummySoup(html)

# ---------------------------------------------------------------------------
# Portable paths
# ---------------------------------------------------------------------------
_SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT  = _SCRIPT_DIR.parent
DEFAULT_CONFIG = _SCRIPT_DIR / "kb_sync_config.json"
DEFAULT_KB    = PROJECT_ROOT / "data" / "knowledge_base.txt"
DEFAULT_BKDIR = PROJECT_ROOT / "data" / "backups"
DEFAULT_LOGDIR = PROJECT_ROOT / "data" / "logs"

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
log = logging.getLogger("sreyas.sync")


def _setup_logging(verbose: bool = False) -> None:
    if log.handlers:
        return
    level = logging.DEBUG if verbose else logging.INFO
    log.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [SYNC] %(levelname)s — %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    try:
        DEFAULT_LOGDIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(DEFAULT_LOGDIR / "kb_sync.log", encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        pass


# ===========================================================================
# CONFIGURATION LOADER
# ===========================================================================

class SyncConfig:
    """Loads and exposes kb_sync_config.json as typed attributes."""

    def __init__(self, config_path: Path = DEFAULT_CONFIG) -> None:
        if not config_path.is_file():
            raise FileNotFoundError(f"Sync config not found: {config_path}")
        with open(config_path, encoding="utf-8") as fh:
            raw: Dict[str, Any] = json.load(fh)

        self.base_url:        str  = raw["base_url"].rstrip("/")
        self.allowed_domains: List[str] = raw.get("allowed_domains", ["sreyas.ac.in"])
        self.http:            Dict[str, Any] = raw.get("http", {})
        self.backup:          Dict[str, Any] = raw.get("backup", {})
        self.sections:        List[Dict[str, Any]] = raw.get("sections", [])

    @property
    def timeout(self) -> int:
        return int(self.http.get("timeout_seconds", 15))

    @property
    def max_retries(self) -> int:
        return int(self.http.get("max_retries", 3))

    @property
    def backoff_base(self) -> float:
        return float(self.http.get("backoff_base", 1.6))

    @property
    def user_agent(self) -> str:
        return str(self.http.get(
            "user_agent",
            "Mozilla/5.0 (compatible; SreyasKBSync/2.0; +https://sreyas.ac.in)"
        ))

    @property
    def backup_enabled(self) -> bool:
        return bool(self.backup.get("enabled", True))

    @property
    def max_backups(self) -> int:
        return int(self.backup.get("max_backups", 10))

    @property
    def backup_dir(self) -> Path:
        return PROJECT_ROOT / self.backup.get("directory", "data/backups")

    def managed_sections(self) -> List[Dict[str, Any]]:
        return [s for s in self.sections if s.get("managed", False)]

    def is_url_allowed(self, url: str) -> bool:
        try:
            host = urlparse(url).hostname or ""
            return any(
                host == d or host.endswith("." + d)
                for d in self.allowed_domains
            )
        except Exception:
            return False


# ===========================================================================
# VALIDATOR
# ===========================================================================

class Validator:
    """Rejects obviously-broken or malicious scraped values."""

    _PHONE = re.compile(r"[6-9]\d{9}")
    _EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.IGNORECASE)
    _DIGIT = re.compile(r"\d")
    _LPA   = re.compile(r"\d[\d.]*\s*lpa", re.IGNORECASE)
    _PCT   = re.compile(r"\d{2,3}\s*%")
    _SAFE_TEXT = re.compile(r"[<>{}|\\^`\x00-\x1f]")  # chars that shouldn't appear in KB text

    @classmethod
    def is_safe_text(cls, value: Optional[str], min_len: int = 3) -> bool:
        if not value or len(value.strip()) < min_len:
            return False
        if cls._SAFE_TEXT.search(value):
            return False
        return True

    @classmethod
    def is_phone(cls, value: Optional[str]) -> bool:
        return bool(value) and bool(cls._PHONE.search(re.sub(r"\D", "", value or "")))

    @classmethod
    def is_email(cls, value: Optional[str]) -> bool:
        return bool(value) and bool(cls._EMAIL.search(value))

    @classmethod
    def is_fee(cls, value: Optional[str]) -> bool:
        return bool(value) and bool(cls._DIGIT.search(value))

    @classmethod
    def is_package(cls, value: Optional[str]) -> bool:
        return bool(value) and bool(cls._LPA.search(value))

    @classmethod
    def is_percentage(cls, value: Optional[str]) -> bool:
        return bool(value) and bool(cls._PCT.search(value))

    @classmethod
    def clean(cls, value: str) -> str:
        """Strip leading/trailing whitespace and collapse internal spaces."""
        return " ".join(value.split())


# ===========================================================================
# WEBSITE FETCHER
# ===========================================================================

class WebsiteFetcher:
    """
    Shared retrying HTTP session with per-run in-memory page cache.
    Every URL is fetched at most ONCE per sync run.
    """

    def __init__(self, cfg: SyncConfig) -> None:
        self.cfg = cfg
        self._cache: Dict[str, Optional[BeautifulSoup]] = {}
        self.pages_scanned = 0
        self.failed_pages  = 0
        self._failures: List[Tuple[str, str]] = []

        if HAS_REQUESTS:
            self.session = requests.Session()
            self.session.headers["User-Agent"] = cfg.user_agent
            adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8)
            self.session.mount("https://", adapter)
            self.session.mount("http://",  adapter)
        else:
            self.session = None

    def get_soup(self, path: str) -> Optional[BeautifulSoup]:
        """
        Fetch a page and return parsed BeautifulSoup, or None on failure.
        `path` may be an absolute URL or a relative path like '/admissions'.
        Result is cached for the life of this fetcher instance.
        """
        url = (path if path.startswith("http")
               else urljoin(self.cfg.base_url, path))

        if not self.cfg.is_url_allowed(url):
            log.warning("Blocked external URL: %s", url)
            return None

        if url in self._cache:
            return self._cache[url]

        soup = self._fetch(url)
        self._cache[url] = soup
        self.pages_scanned += 1
        if soup is None:
            self.failed_pages += 1
        return soup

    def _fetch(self, url: str) -> Optional[BeautifulSoup]:
        last_reason = "unknown"
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                log.debug("GET %s (attempt %d/%d)", url, attempt, self.cfg.max_retries)
                if HAS_REQUESTS and self.session:
                    resp = self.session.get(url, timeout=self.cfg.timeout, allow_redirects=True)
                    if resp.status_code == 200:
                        log.info("  [OK] %s", url)
                        return BeautifulSoup(resp.text, "html.parser")
                    else:
                        last_reason = f"http:{resp.status_code}"
                else:
                    req = urllib.request.Request(url, headers={"User-Agent": self.cfg.user_agent})
                    with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                        html = resp.read().decode("utf-8", errors="ignore")
                        log.info("  [OK] %s", url)
                        return BeautifulSoup(html, "html.parser")
            except Exception as exc:
                last_reason = str(exc)

            if attempt < self.cfg.max_retries:
                sleep_t = self.cfg.backoff_base ** attempt
                time.sleep(sleep_t)

        log.error("Failed: %s — %s", url, last_reason)
        self._failures.append((url, last_reason))
        return None

    @property
    def failures(self) -> List[Tuple[str, str]]:
        return list(self._failures)

    @staticmethod
    def visible_text(soup: BeautifulSoup) -> str:
        """Plain text with nav/header/footer/script/style removed."""
        clone = BeautifulSoup(str(soup), "html.parser")
        for tag in clone(["script", "style", "nav", "header", "footer", "noscript"]):
            tag.decompose()
        text = clone.get_text(separator="\n")
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    @staticmethod
    def tel_links(soup: BeautifulSoup) -> List[str]:
        return [a["href"].replace("tel:", "").strip()
                for a in soup.select('a[href^="tel:"]') if a.get("href")]

    @staticmethod
    def mailto_links(soup: BeautifulSoup) -> List[str]:
        return [a["href"].replace("mailto:", "").strip()
                for a in soup.select('a[href^="mailto:"]') if a.get("href")]


# ===========================================================================
# PAGE DISCOVERY
# ===========================================================================

class PageDiscovery:
    """
    Resolves the list of URLs to fetch for a section.
    Falls back to the homepage if a specific page 404s.
    Future extension: can read sitemap.xml here without touching any other class.
    """

    def __init__(self, fetcher: WebsiteFetcher, cfg: SyncConfig) -> None:
        self.fetcher = fetcher
        self.cfg     = cfg

    def urls_for_section(self, section_cfg: Dict[str, Any]) -> List[str]:
        """Return a de-duplicated list of absolute URLs for this section."""
        paths = section_cfg.get("urls", [])
        seen: set = set()
        result: List[str] = []
        for p in paths:
            url = p if p.startswith("http") else urljoin(self.cfg.base_url, p)
            if url not in seen:
                seen.add(url)
                result.append(url)
        return result


# ===========================================================================
# SECTION EXTRACTOR
# ===========================================================================

class SectionExtractor:
    """
    Extracts field values for one KB section using the patterns defined in
    kb_sync_config.json.

    Extraction strategy (in priority order):
    1. tel:/mailto: link scanning for phone/email fields.
    2. Regex patterns from config applied to visible page text.
    3. Skip field if no pattern matches or validation fails.

    Every extracted value is validated before being returned.
    """

    def __init__(self, fetcher: WebsiteFetcher, cfg: SyncConfig) -> None:
        self.fetcher = fetcher
        self.cfg     = cfg

    def extract(self, section_cfg: Dict[str, Any]) -> Dict[str, str]:
        """
        Return a dict of {field_label: value} for all fields that could
        be successfully scraped and validated.  Missing fields are simply
        absent from the dict — the caller merges with previous values.
        """
        urls     = section_cfg.get("urls", [])
        patterns = section_cfg.get("field_patterns", {})
        found: Dict[str, str] = {}

        soups: List[BeautifulSoup] = []
        for path in urls:
            soup = self.fetcher.get_soup(path)
            if soup:
                soups.append(soup)

        if not soups:
            return found

        # ── Phone / email from <a href="tel:..."> / <a href="mailto:..."> ──
        phones: List[str] = []
        emails: List[str] = []
        for soup in soups:
            phones.extend(self.fetcher.tel_links(soup))
            emails.extend(self.fetcher.mailto_links(soup))

        # ── Regex patterns from config ──
        texts = [self.fetcher.visible_text(s) for s in soups]

        for label, raw_patterns in patterns.items():
            # Normalize: config may store a single string or list of strings
            if isinstance(raw_patterns, str):
                raw_patterns = [raw_patterns]

            label_lower = label.lower()

            # Phone field
            if "phone" in label_lower:
                valid = [p for p in phones if Validator.is_phone(p)]
                if valid:
                    found[label] = Validator.clean(valid[0])
                continue

            # Email field
            if "email" in label_lower:
                valid = [e for e in emails if Validator.is_email(e)]
                if valid:
                    found[label] = Validator.clean(valid[0])
                continue

            # General regex search across all page texts
            for pattern_str in raw_patterns:
                try:
                    compiled = re.compile(pattern_str, re.IGNORECASE | re.DOTALL)
                except re.error as exc:
                    log.debug("Bad regex pattern '%s': %s", pattern_str, exc)
                    continue

                for text in texts:
                    m = compiled.search(text)
                    if m:
                        raw_val = (m.group(1) if m.lastindex and m.group(1) is not None else m.group(0)) or ""
                        value = Validator.clean(raw_val.strip())
                        if Validator.is_safe_text(value, min_len=3):
                            found[label] = value
                            break
                if label in found:
                    break

        return found


# ===========================================================================
# KNOWLEDGE BASE DOCUMENT
# ===========================================================================

class KnowledgeBaseDocument:
    """
    Safe, section-scoped read/write of data/knowledge_base.txt.

    Only the bytes of the targeted '== SECTION ==' block are modified.
    All other sections remain byte-for-byte identical.
    Writes are atomic: write to temp file in same directory → os.replace.
    """

    _SECTION_RE = re.compile(
        r"(==\s*{header}\s*==\n)(.*?)(?=\n==|\Z)", re.DOTALL
    )

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"knowledge_base.txt not found: {self.path}")
        self.original_text = self.path.read_text(encoding="utf-8")
        self.text = self.original_text

    @property
    def changed(self) -> bool:
        return self.text != self.original_text

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]

    def get_section(self, header: str) -> Optional[str]:
        pat = re.compile(
            self._SECTION_RE.pattern.format(header=re.escape(header)),
            re.DOTALL,
        )
        m = pat.search(self.text)
        return m.group(2).strip() if m else None

    def set_section(self, header: str, new_body: str) -> bool:
        pat = re.compile(
            self._SECTION_RE.pattern.format(header=re.escape(header)),
            re.DOTALL,
        )
        if not pat.search(self.text):
            log.warning("Section '%s' not found in KB — skipping.", header)
            return False
        self.text = pat.sub(
            lambda m: m.group(1) + new_body.strip() + "\n",
            self.text,
            count=1,
        )
        return True

    def save(self) -> None:
        """Atomic write — crash-safe."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".kb_sync_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(self.text)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


# ===========================================================================
# BACKUP MANAGER
# ===========================================================================

class BackupManager:
    """Creates timestamped backups and prunes old ones."""

    def __init__(self, backup_dir: Path, max_backups: int = 10) -> None:
        self.backup_dir  = Path(backup_dir)
        self.max_backups = max_backups

    def create(self, kb_path: Path) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y_%m_%d_%H%M%S")
        dest  = self.backup_dir / f"{kb_path.stem}_backup_{stamp}{kb_path.suffix}"
        shutil.copy2(str(kb_path), str(dest))
        log.info("Backup: %s", dest.name)
        self._prune(kb_path)
        return dest

    def _prune(self, kb_path: Path) -> None:
        pattern = f"{kb_path.stem}_backup_*{kb_path.suffix}"
        backups = sorted(
            self.backup_dir.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[self.max_backups:]:
            try:
                old.unlink()
                log.debug("Deleted old backup: %s", old.name)
            except OSError:
                pass


# ===========================================================================
# RAG REBUILDER
# ===========================================================================

class RAGRebuilder:
    """
    Calls rag_engine.rebuild_index() when the KB changes.
    Imports lazily so this script can run standalone without the full
    Flask stack being imported.
    """

    def __init__(self, kb_path: Path) -> None:
        self.kb_path = kb_path

    def rebuild(self, rag_engine: Any = None) -> bool:
        """
        Rebuild using a live engine reference (preferred — scheduler.py
        passes one in).  Falls back to importing RAGEngine directly if
        no reference is supplied (standalone / cron mode).
        """
        if rag_engine is not None:
            try:
                rag_engine.rebuild_index()
                log.info("RAG index rebuilt via live engine reference.")
                return True
            except Exception as exc:
                log.error("RAG rebuild (live) failed: %s", exc)
                return False

        # Standalone fallback
        modules_dir = str(PROJECT_ROOT / "modules")
        if modules_dir not in sys.path:
            sys.path.insert(0, modules_dir)
        try:
            from rag import RAGEngine  # type: ignore  # noqa: PLC0415
            engine = RAGEngine(knowledge_base_path=str(self.kb_path))
            engine.rebuild_index()
            log.info("RAG index rebuilt (standalone). Chunks: %d",
                     engine.get_stats().get("num_chunks", 0))
            return True
        except Exception as exc:
            log.error("RAG rebuild (standalone) failed: %s", exc)
            return False


# ===========================================================================
# SYNC STATISTICS
# ===========================================================================

@dataclass
class SyncStatistics:
    pages_scanned:         int  = 0
    sections_checked:      int  = 0
    sections_updated:      int  = 0
    sections_skipped:      int  = 0
    sections_protected:    int  = 0
    failed_pages:          int  = 0
    backup_created:        bool = False
    knowledge_base_changed:bool = False
    rag_rebuilt:           bool = False
    kb_hash:               str  = ""
    total_time:            float = 0.0
    updated_sections:      List[str] = field(default_factory=list)
    skipped_sections:      List[str] = field(default_factory=list)
    failed_page_urls:      List[str] = field(default_factory=list)
    errors:                List[str] = field(default_factory=list)
    success:               bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


# ===========================================================================
# KNOWLEDGE BASE UPDATER  (orchestrator)
# ===========================================================================

class KnowledgeBaseUpdater:
    """
    Runs one full sync pass.

    Responsibilities
    ----------------
    1. Load config.
    2. Fetch pages (WebsiteFetcher).
    3. Per managed section: extract → validate → merge → compare.
    4. If ANY section changed: backup → atomic save.
    5. If KB changed: rebuild RAG.
    6. Return SyncStatistics.
    """

    def __init__(
        self,
        kb_path:    Path = DEFAULT_KB,
        config_path:Path = DEFAULT_CONFIG,
        backup_dir: Path = DEFAULT_BKDIR,
        dry_run:    bool = False,
        rag_engine: Any  = None,
    ) -> None:
        self.kb_path    = Path(kb_path)
        self.dry_run    = dry_run
        self.rag_engine = rag_engine

        self.cfg     = SyncConfig(config_path)
        self.fetcher = WebsiteFetcher(self.cfg)
        self.extractor     = SectionExtractor(self.fetcher, self.cfg)
        self.backup_mgr    = BackupManager(Path(backup_dir), self.cfg.max_backups)
        self.rag_rebuilder = RAGRebuilder(self.kb_path)

    def run(self) -> Dict[str, Any]:
        """Execute one complete sync cycle. Returns a JSON-serialisable stats dict."""
        start = time.perf_counter()
        stats = SyncStatistics()

        log.info("=" * 60)
        log.info("Knowledge Base Sync Started")
        log.info("Target: %s", self.kb_path)
        if self.dry_run:
            log.info("[DRY-RUN MODE] No files will be written.")
        log.info("=" * 60)

        try:
            document = KnowledgeBaseDocument(self.kb_path)
        except FileNotFoundError as exc:
            log.error("%s", exc)
            stats.success = False
            stats.errors.append(str(exc))
            stats.total_time = time.perf_counter() - start
            return stats.to_dict()

        managed = self.cfg.managed_sections()
        protected = [s for s in self.cfg.sections if not s.get("managed", False)]
        stats.sections_protected = len(protected)

        for section_cfg in managed:
            self._sync_section(section_cfg, document, stats)

        stats.pages_scanned   = self.fetcher.pages_scanned
        stats.failed_pages    = self.fetcher.failed_pages
        stats.failed_page_urls = [u for u, _ in self.fetcher.failures]

        if document.changed:
            stats.knowledge_base_changed = True
            stats.kb_hash = document.hash

            if self.dry_run:
                log.info("[DRY-RUN] Would write KB and rebuild RAG.")
            else:
                if self.cfg.backup_enabled:
                    try:
                        self.backup_mgr.create(self.kb_path)
                        stats.backup_created = True
                    except OSError as exc:
                        log.error("Backup failed: %s", exc)
                        stats.errors.append(f"backup:{exc}")

                try:
                    document.save()
                    log.info("Knowledge base updated: %s", self.kb_path.name)
                except OSError as exc:
                    log.error("Save failed: %s", exc)
                    stats.success = False
                    stats.errors.append(f"save:{exc}")
                    stats.total_time = time.perf_counter() - start
                    return stats.to_dict()

                stats.rag_rebuilt = self.rag_rebuilder.rebuild(self.rag_engine)
        else:
            log.info("Knowledge base unchanged — nothing written.")

        stats.total_time = round(time.perf_counter() - start, 2)
        log.info(
            "Sync complete in %.2fs | updated=%d | skipped=%d | protected=%d | failed=%d",
            stats.total_time, stats.sections_updated,
            stats.sections_skipped, stats.sections_protected,
            stats.failed_pages,
        )
        log.info("=" * 60)
        return stats.to_dict()

    def _sync_section(
        self,
        section_cfg: Dict[str, Any],
        document:    KnowledgeBaseDocument,
        stats:       SyncStatistics,
    ) -> None:
        """Sync one section. All errors are caught so others still run."""
        header = section_cfg["name"]
        stats.sections_checked += 1

        previous_body = document.get_section(header)
        if previous_body is None:
            msg = f"Section '{header}' missing from KB."
            log.warning(msg)
            stats.errors.append(msg)
            stats.sections_skipped += 1
            stats.skipped_sections.append(header)
            return

        try:
            new_fields = self.extractor.extract(section_cfg)
        except Exception as exc:
            log.error("Extraction error in '%s': %s", header, exc)
            stats.errors.append(f"{header}: {exc}")
            stats.sections_skipped += 1
            stats.skipped_sections.append(header)
            return

        if not new_fields:
            log.debug("No new data for '%s' — keeping existing content.", header)
            stats.sections_skipped += 1
            stats.skipped_sections.append(header)
            return

        # Merge: overlay scraped fields into existing body text
        new_body = self._merge(previous_body, new_fields)

        if new_body.strip() == previous_body.strip():
            log.info("  [SAME] %s (unchanged)", header)
            stats.sections_skipped += 1
            stats.skipped_sections.append(header)
            return

        document.set_section(header, new_body)
        log.info("  [UPDATE] %s (updated %d field(s))", header, len(new_fields))
        stats.sections_updated += 1
        stats.updated_sections.append(header)

    @staticmethod
    def _merge(previous_body: str, new_fields: Dict[str, str]) -> str:
        """
        Overlay *new_fields* into *previous_body*.

        Strategy:
        • For each label that exists in previous_body as "Label: old_value",
          replace old_value with the new one.
        • For labels not yet present, append them at the end.
        • Lines not managed by the extractor are left untouched.
        """
        lines = previous_body.splitlines()
        managed_labels = set(new_fields.keys())
        updated_labels: set = set()
        result: List[str] = []

        for line in lines:
            colon = line.find(":")
            if colon == -1:
                result.append(line)
                continue
            label = line[:colon].strip()
            if label in managed_labels:
                result.append(f"{label}: {new_fields[label]}")
                updated_labels.add(label)
            else:
                result.append(line)

        # Append fields that didn't exist in the previous body
        for label, value in new_fields.items():
            if label not in updated_labels:
                result.append(f"{label}: {value}")

        return "\n".join(result)


# ===========================================================================
# PUBLIC API — called by scheduler.py
# ===========================================================================

def run_sync(
    kb_path:     Path = DEFAULT_KB,
    config_path: Path = DEFAULT_CONFIG,
    backup_dir:  Path = DEFAULT_BKDIR,
    dry_run:     bool = False,
    rag_engine:  Any  = None,
    verbose:     bool = False,
) -> Dict[str, Any]:
    """
    Run one full sync cycle. Never raises for ordinary failures — those
    are reflected in the returned stats dict instead.

    Parameters
    ----------
    kb_path:     Path to knowledge_base.txt.
    config_path: Path to kb_sync_config.json.
    backup_dir:  Directory for timestamped backups.
    dry_run:     If True, analyse but do not write anything.
    rag_engine:  Live RAGEngine instance (from scheduler.py).
                 Pass None to use standalone re-import fallback.
    verbose:     Enable DEBUG-level logging.
    """
    _setup_logging(verbose)
    updater = KnowledgeBaseUpdater(
        kb_path     = Path(kb_path),
        config_path = Path(config_path),
        backup_dir  = Path(backup_dir),
        dry_run     = dry_run,
        rag_engine  = rag_engine,
    )
    return updater.run()


# ===========================================================================
# CLI ENTRY POINT
# ===========================================================================

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync knowledge_base.txt with the live sreyas.ac.in website."
    )
    parser.add_argument(
        "--kb-path", type=Path, default=DEFAULT_KB,
        help="Path to knowledge_base.txt (default: data/knowledge_base.txt)"
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help="Path to kb_sync_config.json (default: scripts/kb_sync_config.json)"
    )
    parser.add_argument(
        "--backup-dir", type=Path, default=DEFAULT_BKDIR,
        help="Backup directory (default: data/backups)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Analyse without writing any files"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose (DEBUG) logging"
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args  = _parse_args(argv)
    stats = run_sync(
        kb_path     = args.kb_path,
        config_path = args.config,
        backup_dir  = args.backup_dir,
        dry_run     = args.dry_run,
        verbose     = args.verbose,
    )
    print(json.dumps(stats, indent=2, default=str))
    return 0 if stats.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())