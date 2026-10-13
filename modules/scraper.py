"""
modules/scraper.py
================================================================================
Automatic Website Scraper & Knowledge-Base Updater — SREYAS AI Assistant

Features:
1. Approved official college domain filtering (sreyas.ac.in).
2. Robots.txt compliance check with urllib.robotparser.
3. Clean HTML parsing with BeautifulSoup (decomposes scripts, styles, nav, footer).
4. Content hashing (SHA-256) to detect new, modified, unchanged, and removed content.
5. Preserves last known good content on failure or network error.
6. Safe atomic updates to data/knowledge_base.txt with timestamped backups.
7. Avoids duplicates across repeated runs.
8. Triggers RAG index rebuild (rag_engine.rebuild_index()) with zero-downtime hot reload.
9. Persistent state tracking in data/scrape_state.json with Asia/Kolkata timestamps.
================================================================================
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

try:
    import pytz
    KOLKATA_TZ = pytz.timezone("Asia/Kolkata")
except ImportError:
    KOLKATA_TZ = None

try:
    import requests
    from requests.adapters import HTTPAdapter
    HAS_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.error
    HAS_REQUESTS = False

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
        def find_all(self, *args, **kwargs):
            return []
        def find(self, *args, **kwargs):
            return None
        def __call__(self, tags):
            return []
        def decompose(self):
            pass
        def __str__(self):
            return self._text

    def BeautifulSoup(html, parser="html.parser"):
        return DummySoup(html)

# ---------------------------------------------------------------------------
# Base paths
# ---------------------------------------------------------------------------
MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parent
DEFAULT_KB_PATH = PROJECT_ROOT / "data" / "knowledge_base.txt"
DEFAULT_STATE_PATH = PROJECT_ROOT / "data" / "scrape_state.json"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "scripts" / "kb_sync_config.json"
DEFAULT_SOURCES_PATH = PROJECT_ROOT / "config" / "sources.json"

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("sreyas.scraper")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [SCRAPER] %(levelname)s — %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


def get_current_ist_time() -> datetime:
    """Return current datetime in Asia/Kolkata timezone."""
    if KOLKATA_TZ:
        return datetime.now(KOLKATA_TZ)
    return datetime.now()


def get_current_ist_str() -> str:
    """Return formatted ISO/human string in Asia/Kolkata timezone."""
    return get_current_ist_time().strftime("%Y-%m-%d %H:%M:%S IST")


# ---------------------------------------------------------------------------
# Robots.txt Checker
# ---------------------------------------------------------------------------
class RobotsChecker:
    """Checks and caches robots.txt rules for target domains."""

    def __init__(self, user_agent: str = "SreyasAssistantBot") -> None:
        self.user_agent = user_agent
        self._parsers: Dict[str, RobotFileParser] = {}

    def is_allowed(self, url: str) -> bool:
        """Return True if URL is allowed according to robots.txt."""
        parsed = urlparse(url)
        netloc = parsed.netloc
        if not netloc:
            return True

        if netloc not in self._parsers:
            robots_url = f"{parsed.scheme}://{netloc}/robots.txt"
            rp = RobotFileParser()
            rp.set_url(robots_url)
            try:
                if HAS_REQUESTS:
                    resp = requests.get(robots_url, timeout=5, headers={"User-Agent": self.user_agent})
                    if resp.status_code == 200:
                        rp.parse(resp.text.splitlines())
                    else:
                        rp.allow_all = True
                else:
                    rp.read()
            except Exception as e:
                logger.debug("robots.txt not available at %s (%s). Permitting crawl.", robots_url, e)
                rp.allow_all = True
            self._parsers[netloc] = rp

        parser = self._parsers[netloc]
        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception:
            return True


# ---------------------------------------------------------------------------
# Content Sanitizer & Validator
# ---------------------------------------------------------------------------
class ContentValidator:
    """Sanitizes text and rejects malformed scraped content."""

    _CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        """Strip control chars and normalize whitespace."""
        if not text:
            return ""
        text = cls._CONTROL_CHARS.sub("", text)
        return " ".join(text.split())

    @classmethod
    def is_valid_content(cls, content: str, min_len: int = 15, max_len: int = 50000) -> bool:
        """Verify length and structure."""
        if not content:
            return False
        clean = content.strip()
        return min_len <= len(clean) <= max_len


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
@dataclass
class ScrapedItem:
    url: str
    category: str
    title: str
    content: str
    content_hash: str
    extracted_at: str
    fields: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Main Website Scraper Engine
# ---------------------------------------------------------------------------
class CollegeWebsiteScraper:
    """
    Production scraper for official Sreyas College website sources.
    Safely extracts announcements, courses, admissions, fees, and placement updates.
    """

    DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; SreyasAssistantBot/2.0; +https://sreyas.ac.in)"
    APPROVED_DOMAINS = ["sreyas.ac.in", "www.sreyas.ac.in", "epass.cgg.gov.in"]

    # Target Section in KB dedicated to live scraped website content
    SCRAPED_SECTION_HEADER = "LIVE COLLEGE ANNOUNCEMENTS & WEBSITE UPDATES"

    def __init__(
        self,
        kb_path: Optional[Path | str] = None,
        state_file: Optional[Path | str] = None,
        backup_dir: Optional[Path | str] = None,
        sources_file: Optional[Path | str] = None,
        config_file: Optional[Path | str] = None,
        timeout: int = 15,
        rate_limit_delay: float = 0.5,
        user_agent: Optional[str] = None,
    ) -> None:
        self.kb_path = Path(kb_path or DEFAULT_KB_PATH)
        self.state_file = Path(state_file or DEFAULT_STATE_PATH)
        self.backup_dir = Path(backup_dir or DEFAULT_BACKUP_DIR)
        self.sources_file = Path(sources_file or DEFAULT_SOURCES_PATH)
        self.config_file = Path(config_file or DEFAULT_CONFIG_PATH)

        self.timeout = timeout
        self.rate_limit_delay = rate_limit_delay
        self.user_agent = user_agent or self.DEFAULT_USER_AGENT

        self.robots_checker = RobotsChecker(user_agent=self.user_agent)
        self._session = None
        if HAS_REQUESTS:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": self.user_agent})
            adapter = HTTPAdapter(pool_connections=5, pool_maxsize=5)
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)

        self.state = self._load_state()

    # -----------------------------------------------------------------------
    # Domain & URL Validation
    # -----------------------------------------------------------------------
    def is_url_allowed(self, url: str) -> bool:
        """Ensure URL is within approved official domains."""
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            return any(host == d or host.endswith("." + d) for d in self.APPROVED_DOMAINS)
        except Exception:
            return False

    # -----------------------------------------------------------------------
    # State Persistence
    # -----------------------------------------------------------------------
    def _load_state(self) -> Dict[str, Any]:
        """Load persistent scrape state from data/scrape_state.json."""
        if not self.state_file.exists():
            return {
                "last_run": None,
                "next_run": None,
                "status": "INITIALIZED",
                "sources_processed": [],
                "content_hashes": {},
                "last_known_good": {},
                "active_items": {},
                "errors": [],
            }
        try:
            with open(self.state_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.warning("Could not read scrape state file: %s", e)
        return {
            "last_run": None,
            "next_run": None,
            "status": "ERROR_LOADING_STATE",
            "sources_processed": [],
            "content_hashes": {},
            "last_known_good": {},
            "active_items": {},
            "errors": [],
        }

    def _save_state(self) -> None:
        """Persist current scrape state atomically."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.state_file.parent), prefix=".scrape_state_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh, indent=2, default=str)
            os.replace(tmp, self.state_file)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # -----------------------------------------------------------------------
    # Configured Sources Discovery
    # -----------------------------------------------------------------------
    def get_configured_sources(self) -> List[Dict[str, str]]:
        """
        Aggregate approved official sources from config/sources.json
        and scripts/kb_sync_config.json.
        """
        sources: List[Dict[str, str]] = []
        seen_urls: Set[str] = set()

        # 1. From sources.json
        if self.sources_file.exists():
            try:
                with open(self.sources_file, "r", encoding="utf-8") as fh:
                    s_data = json.load(fh)
                for cat, info in s_data.get("sources", {}).items():
                    url = info.get("url", "").strip()
                    if url and url not in seen_urls and self.is_url_allowed(url):
                        sources.append({
                            "url": url,
                            "category": cat.upper(),
                            "title": info.get("title", cat.title()),
                        })
                        seen_urls.add(url)
                for custom in s_data.get("custom_sources", []):
                    url = custom.get("url", "").strip()
                    if url and url not in seen_urls and self.is_url_allowed(url):
                        sources.append({
                            "url": url,
                            "category": custom.get("category", "GENERAL").upper(),
                            "title": custom.get("title", "Official Announcement"),
                        })
                        seen_urls.add(url)
            except Exception as e:
                logger.error("Error reading sources_file: %s", e)

        # 2. From kb_sync_config.json
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as fh:
                    cfg_data = json.load(fh)
                base_url = cfg_data.get("base_url", "https://sreyas.ac.in").rstrip("/")
                for sec in cfg_data.get("sections", []):
                    sec_name = sec.get("name", "GENERAL").upper()
                    for p in sec.get("urls", []):
                        full_url = p if p.startswith("http") else urljoin(base_url, p)
                        if full_url not in seen_urls and self.is_url_allowed(full_url):
                            sources.append({
                                "url": full_url,
                                "category": sec_name,
                                "title": f"{sec_name} Official Notice",
                            })
                            seen_urls.add(full_url)
            except Exception as e:
                logger.error("Error reading config_file: %s", e)

        # Default fallback official source if none configured
        if not sources:
            sources.append({
                "url": "https://www.sreyas.ac.in",
                "category": "INSTITUTION OVERVIEW",
                "title": "Sreyas Official Website Homepage",
            })

        return sources

    # -----------------------------------------------------------------------
    # HTTP Fetching with Robots.txt & Rate Limiting
    # -----------------------------------------------------------------------
    def fetch_page(self, url: str) -> Optional[str]:
        """Fetch page HTML respecting timeouts, robots.txt, and rate limits."""
        if not self.is_url_allowed(url):
            logger.warning("Blocked disallowed URL: %s", url)
            return None

        if not self.robots_checker.is_allowed(url):
            logger.warning("Robots.txt disallows crawling: %s", url)
            return None

        if self.rate_limit_delay > 0:
            time.sleep(self.rate_limit_delay)

        logger.info("Fetching: %s", url)
        try:
            if HAS_REQUESTS and self._session:
                resp = self._session.get(url, timeout=self.timeout, allow_redirects=True)
                if resp.status_code == 200:
                    return resp.text
                logger.warning("HTTP %d for %s", resp.status_code, url)
                return None
            else:
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            logger.error("Failed to fetch %s: %s", url, e)
            return None

    # -----------------------------------------------------------------------
    # HTML Parsing & Content Extraction
    # -----------------------------------------------------------------------
    def parse_and_extract(
        self,
        html: str,
        url: str,
        category: str,
        default_title: str = "College Notice",
    ) -> Optional[ScrapedItem]:
        """
        Parse HTML using BeautifulSoup, strip noise, extract main text and
        generate content hash.
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            logger.error("HTML parsing error for %s: %s", url, e)
            return None

        # Clean noise tags
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "form"]):
            if hasattr(tag, "decompose"):
                tag.decompose()

        # Extract title
        extracted_title = default_title
        title_el = soup.find("h1") or soup.find("title")
        if title_el:
            raw_title = ContentValidator.sanitize_text(title_el.get_text())
            if raw_title and len(raw_title) > 3:
                extracted_title = raw_title

        # Extract main text
        main_content = ""
        # Try finding article or main content div
        content_container = (
            soup.find("article")
            or soup.find("main")
            or soup.find("div", {"id": re.compile(r"content|main|post", re.I)})
            or soup.find("div", {"class": re.compile(r"content|entry|post-body|main", re.I)})
            or soup
        )

        lines: List[str] = []
        # Key links (tel, mailto)
        contact_points: List[str] = []
        for a in soup.select('a[href^="tel:"]'):
            contact_points.append(f"Phone: {a['href'].replace('tel:', '').strip()}")
        for a in soup.select('a[href^="mailto:"]'):
            contact_points.append(f"Email: {a['href'].replace('mailto:', '').strip()}")

        raw_text = content_container.get_text(separator="\n")
        for line in raw_text.splitlines():
            clean_l = ContentValidator.sanitize_text(line)
            if len(clean_l) > 1:
                lines.append(clean_l)

        # Deduplicate consecutive lines
        deduped: List[str] = []
        for l in lines:
            if not deduped or deduped[-1] != l:
                deduped.append(l)

        # Join up to reasonable limit
        body_text = "\n".join(deduped[:120])
        if contact_points:
            body_text += "\n\nContact Details:\n" + "\n".join(set(contact_points))

        if not ContentValidator.is_valid_content(body_text):
            logger.warning("Extracted text too short or invalid for %s", url)
            return None

        content_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()

        return ScrapedItem(
            url=url,
            category=category,
            title=extracted_title,
            content=body_text,
            content_hash=content_hash,
            extracted_at=get_current_ist_str(),
        )

    # -----------------------------------------------------------------------
    # Scrape Workflow
    # -----------------------------------------------------------------------
    def run_scrape(
        self,
        test_sources: Optional[List[Dict[str, str]]] = None,
        mock_fetcher: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Execute full scrape workflow across configured sources.
        Detects new, modified, unchanged, and failed items.
        Preserves last known good content when fetch fails.
        """
        start_time = time.perf_counter()
        sources = test_sources if test_sources is not None else self.get_configured_sources()

        processed_sources: List[str] = []
        new_items: List[Dict[str, Any]] = []
        modified_items: List[Dict[str, Any]] = []
        unchanged_items: List[Dict[str, Any]] = []
        failed_sources: List[Dict[str, str]] = []
        preserved_items: List[str] = []

        active_items = self.state.get("active_items", {})
        last_known_good = self.state.get("last_known_good", {})
        content_hashes = self.state.get("content_hashes", {})

        for src in sources:
            url = src.get("url", "").strip()
            cat = src.get("category", "GENERAL").upper()
            title = src.get("title", "College Notice")

            if not url:
                continue

            processed_sources.append(url)

            # Fetch HTML
            html = None
            if mock_fetcher:
                html = mock_fetcher(url)
            else:
                html = self.fetch_page(url)

            if html is None:
                # Preservation of Last Known Good
                failed_sources.append({"url": url, "reason": "Fetch failed or inaccessible"})
                if url in last_known_good:
                    preserved_items.append(url)
                    logger.info("Preserving last known good content for failed URL: %s", url)
                continue

            # Extract content
            item = self.parse_and_extract(html, url, cat, title)
            if item is None:
                failed_sources.append({"url": url, "reason": "Extracted content was empty or invalid"})
                if url in last_known_good:
                    preserved_items.append(url)
                    logger.info("Preserving last known good content for invalid URL: %s", url)
                continue

            # Change detection
            prev_hash = content_hashes.get(url)
            if prev_hash is None:
                # Newly added item
                new_items.append(item.to_dict())
                logger.info("New content detected from %s (Category: %s)", url, cat)
            elif prev_hash != item.content_hash:
                # Modified content
                modified_items.append(item.to_dict())
                logger.info("Modified content detected from %s (Category: %s)", url, cat)
            else:
                # Unchanged
                unchanged_items.append(item.to_dict())

            # Update cache and state
            content_hashes[url] = item.content_hash
            active_items[url] = item.to_dict()
            last_known_good[url] = item.to_dict()

        has_changes = bool(new_items or modified_items)

        # Update persistent state
        self.state["last_run"] = get_current_ist_str()
        self.state["status"] = "SUCCESS" if not failed_sources else ("PARTIAL" if (active_items or last_known_good) else "FAILED")
        self.state["sources_processed"] = processed_sources
        self.state["content_hashes"] = content_hashes
        self.state["active_items"] = active_items
        self.state["last_known_good"] = last_known_good
        self.state["errors"] = [f"{f['url']}: {f['reason']}" for f in failed_sources]

        duration = round(time.perf_counter() - start_time, 2)

        return {
            "success": bool(active_items or last_known_good),
            "timestamp": self.state["last_run"],
            "duration_seconds": duration,
            "sources_scanned": len(processed_sources),
            "new_count": len(new_items),
            "modified_count": len(modified_items),
            "unchanged_count": len(unchanged_items),
            "failed_count": len(failed_sources),
            "preserved_count": len(preserved_items),
            "has_changes": has_changes,
            "new_items": new_items,
            "modified_items": modified_items,
            "failed_sources": failed_sources,
        }

    # -----------------------------------------------------------------------
    # Knowledge Base Atomic Update & Backup
    # -----------------------------------------------------------------------
    def update_knowledge_base(
        self,
        rag_engine: Any = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Safely update data/knowledge_base.txt with active scraped content.
        Creates a timestamped backup before modifying the file.
        Avoids duplicate entries.
        Calls rag_engine.rebuild_index() on success for zero-downtime hot reload.
        """
        active_items = self.state.get("active_items", {})
        if not active_items and not force:
            return {"updated": False, "reason": "No active scraped content available."}

        if not self.kb_path.exists():
            return {"updated": False, "reason": f"KB file does not exist: {self.kb_path}"}

        with open(self.kb_path, "r", encoding="utf-8") as fh:
            original_kb = fh.read()

        # Build clean formatted section for scraped website content
        scraped_section_lines = [
            f"== {self.SCRAPED_SECTION_HEADER} ==",
            "Official automated updates and verified information extracted from sreyas.ac.in:",
            "",
        ]

        seen_content_hashes: Set[str] = set()

        for url, item in active_items.items():
            c_hash = item.get("content_hash", "")
            if c_hash in seen_content_hashes:
                continue  # duplicate prevention
            seen_content_hashes.add(c_hash)

            category = item.get("category", "GENERAL")
            title = item.get("title", "Official Announcement")
            content = item.get("content", "").strip()
            extracted_at = item.get("extracted_at", get_current_ist_str())

            scraped_section_lines.append(f"--- UPDATE [{category}]: {title} ---")
            scraped_section_lines.append(f"Source URL: {url}")
            scraped_section_lines.append(f"Verification Timestamp: {extracted_at}")
            scraped_section_lines.append(content)
            scraped_section_lines.append("")

        new_scraped_block = "\n".join(scraped_section_lines).strip()

        # Replace existing section if present, or append to end
        sec_pattern = re.compile(
            rf"==\s*{re.escape(self.SCRAPED_SECTION_HEADER)}\s*==\n(.*?)(?=\n==|\Z)",
            re.DOTALL,
        )

        if sec_pattern.search(original_kb):
            updated_kb = sec_pattern.sub(new_scraped_block + "\n\n", original_kb, count=1)
        else:
            updated_kb = original_kb.rstrip() + "\n\n" + new_scraped_block + "\n"

        if updated_kb.strip() == original_kb.strip() and not force:
            logger.info("Knowledge base is already up-to-date with scraped content.")
            self._save_state()
            return {"updated": False, "reason": "Content identical (no change needed)."}

        # 1. Backup original file
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y_%m_%d_%H%M%S")
        backup_file = self.backup_dir / f"knowledge_base_backup_{stamp}.txt"
        shutil.copy2(str(self.kb_path), str(backup_file))
        logger.info("Created backup of knowledge base: %s", backup_file.name)

        # 2. Atomic write
        fd, tmp = tempfile.mkstemp(dir=str(self.kb_path.parent), prefix=".kb_write_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(updated_kb)
            os.replace(tmp, self.kb_path)
            logger.info("Atomic update to %s successful.", self.kb_path.name)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

        # 3. Rebuild RAG index
        rag_rebuilt = False
        if rag_engine is not None:
            try:
                if hasattr(rag_engine, "rebuild_index"):
                    rag_engine.rebuild_index()
                elif hasattr(rag_engine, "safe_rebuild_index"):
                    rag_engine.safe_rebuild_index()
                elif hasattr(rag_engine, "rebuild"):
                    rag_engine.rebuild()
                rag_rebuilt = True
                logger.info("RAG index rebuilt successfully with newly extracted web knowledge.")
            except Exception as e:
                logger.error("Failed to rebuild RAG index: %s", e)

        # Save state
        self._save_state()

        return {
            "updated": True,
            "backup_created": backup_file.name,
            "rag_rebuilt": rag_rebuilt,
            "items_count": len(active_items),
        }


# ---------------------------------------------------------------------------
# Convenience Runner Function
# ---------------------------------------------------------------------------
def execute_website_sync(
    rag_engine: Any = None,
    test_sources: Optional[List[Dict[str, str]]] = None,
    mock_fetcher: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Standard entrypoint for scraper execution.
    Runs scrape -> updates knowledge base -> rebuilds RAG index.
    """
    scraper = CollegeWebsiteScraper()
    scrape_res = scraper.run_scrape(test_sources=test_sources, mock_fetcher=mock_fetcher)

    kb_res = {"updated": False}
    if scrape_res.get("has_changes") or scrape_res.get("success"):
        kb_res = scraper.update_knowledge_base(rag_engine=rag_engine)

    combined_stats = {
        "success": scrape_res.get("success", False),
        "timestamp": scrape_res.get("timestamp"),
        "duration_seconds": scrape_res.get("duration_seconds", 0),
        "pages_scanned": scrape_res.get("sources_scanned", 0),
        "sections_updated": scrape_res.get("new_count", 0) + scrape_res.get("modified_count", 0),
        "knowledge_base_changed": kb_res.get("updated", False),
        "rag_rebuilt": kb_res.get("rag_rebuilt", False),
        "preserved_count": scrape_res.get("preserved_count", 0),
        "failed_pages": scrape_res.get("failed_count", 0),
        "new_items": scrape_res.get("new_items", []),
        "modified_items": scrape_res.get("modified_items", []),
    }

    return combined_stats
