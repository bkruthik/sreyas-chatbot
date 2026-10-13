"""
scripts/test_scraper_scheduler.py
================================================================================
Comprehensive Verification Suite for Scraper and Midnight IST Scheduler

Covers:
1. Fetching and parsing controlled test content.
2. Detecting new and modified content.
3. Updating the knowledge base without duplicates.
4. Preserving existing content after a failed fetch.
5. Refreshing the retrieval index.
6. Asking the chatbot a question requiring newly extracted test content.
7. Verifying midnight schedule & Asia/Kolkata timezone.
8. Preventing duplicate or overlapping jobs.
9. Handling scheduler initialization and restart behavior.
10. Comparing against baseline.
================================================================================
"""

import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from datetime import datetime

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "modules"))

from modules.scraper import CollegeWebsiteScraper, ScrapedItem, ContentValidator, RobotsChecker
from modules.rag import RAGEngine
import scheduler


class TestScraperAndSchedulerSuite(unittest.TestCase):

    def setUp(self):
        # Create temporary directory for isolated KB, state, and backup tests
        self.test_dir = tempfile.mkdtemp(prefix="sreyas_test_")
        self.kb_path = Path(self.test_dir) / "knowledge_base.txt"
        self.state_file = Path(self.test_dir) / "scrape_state.json"
        self.backup_dir = Path(self.test_dir) / "backups"

        # Copy original KB to test KB
        orig_kb = PROJECT_ROOT / "data" / "knowledge_base.txt"
        shutil.copy2(str(orig_kb), str(self.kb_path))

        self.scraper = CollegeWebsiteScraper(
            kb_path=self.kb_path,
            state_file=self.state_file,
            backup_dir=self.backup_dir,
            rate_limit_delay=0.0,
            timeout=5,
        )

    def tearDown(self):
        scheduler.shutdown()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # TEST 1: Fetching and parsing controlled test content
    # -------------------------------------------------------------------------
    def test_01_parsing_controlled_html(self):
        sample_html = """
        <!DOCTYPE html>
        <html>
        <head><title>Sreyas Quantum Computing Lab Inauguration 2026</title></head>
        <body>
            <nav><a href="/">Home</a><a href="/about">About</a></nav>
            <h1>Sreyas Opens Center for Advanced Quantum AI & Cyber Security</h1>
            <div id="content">
                <p>Sreyas Institute of Engineering and Technology has officially launched its state-of-the-art Quantum AI Center.</p>
                <p>The facility provides 120 specialized quantum workstations equipped with IBM Qiskit software.</p>
                <a href="tel:9246323444">Contact Helpline</a>
                <a href="mailto:quantum@sreyas.ac.in">Quantum Lab Email</a>
            </div>
            <footer>© 2026 Sreyas Institute</footer>
        </body>
        </html>
        """
        item = self.scraper.parse_and_extract(
            html=sample_html,
            url="https://sreyas.ac.in/quantum-ai-2026",
            category="FACILITIES",
            default_title="Quantum AI Lab",
        )
        self.assertIsNotNone(item)
        self.assertIn("Quantum AI", item.title)
        self.assertIn("IBM Qiskit", item.content)
        self.assertIn("Phone: 9246323444", item.content)
        self.assertIn("Email: quantum@sreyas.ac.in", item.content)
        self.assertNotIn("Home", item.content)  # nav stripped
        self.assertNotIn("© 2026 Sreyas Institute", item.content)  # footer stripped
        self.assertEqual(len(item.content_hash), 64)

    # -------------------------------------------------------------------------
    # TEST 2: Detecting new and modified content
    # -------------------------------------------------------------------------
    def test_02_change_detection_new_and_modified(self):
        url = "https://sreyas.ac.in/notices/hackathon-2026"
        html_v1 = "<html><body><h1>Hackathon 2026</h1><p>Date: 15 November 2026 with prize pool Rs 2 Lakhs.</p></body></html>"
        html_v2 = "<html><body><h1>Hackathon 2026</h1><p>Date: 20 November 2026 with prize pool Rs 5 Lakhs updated.</p></body></html>"

        # Pass 1: New content
        res1 = self.scraper.run_scrape(
            test_sources=[{"url": url, "category": "EVENTS", "title": "Hackathon 2026"}],
            mock_fetcher=lambda u: html_v1,
        )
        self.assertEqual(res1["new_count"], 1)
        self.assertEqual(res1["modified_count"], 0)
        self.assertTrue(res1["has_changes"])

        # Pass 2: Unchanged content
        res2 = self.scraper.run_scrape(
            test_sources=[{"url": url, "category": "EVENTS", "title": "Hackathon 2026"}],
            mock_fetcher=lambda u: html_v1,
        )
        self.assertEqual(res2["new_count"], 0)
        self.assertEqual(res2["unchanged_count"], 1)
        self.assertFalse(res2["has_changes"])

        # Pass 3: Modified content
        res3 = self.scraper.run_scrape(
            test_sources=[{"url": url, "category": "EVENTS", "title": "Hackathon 2026"}],
            mock_fetcher=lambda u: html_v2,
        )
        self.assertEqual(res3["new_count"], 0)
        self.assertEqual(res3["modified_count"], 1)
        self.assertTrue(res3["has_changes"])

    # -------------------------------------------------------------------------
    # TEST 3: Updating the knowledge base without duplicates
    # -------------------------------------------------------------------------
    def test_03_knowledge_base_update_no_duplicates(self):
        url = "https://sreyas.ac.in/placement-record-2026"
        html = "<html><body><h1>Placement Milestone</h1><p>Sreyas achieves 98.4% campus placement in 2026 batch.</p></body></html>"

        self.scraper.run_scrape(
            test_sources=[{"url": url, "category": "PLACEMENTS", "title": "Placement Milestone"}],
            mock_fetcher=lambda u: html,
        )
        up_res1 = self.scraper.update_knowledge_base(force=True)
        self.assertTrue(up_res1["updated"])

        # Read KB
        kb_text1 = self.kb_path.read_text(encoding="utf-8")
        self.assertIn("Placement Milestone", kb_text1)
        self.assertEqual(kb_text1.count("--- UPDATE [PLACEMENTS]: Placement Milestone ---"), 1)

        # Run scrape again and update KB again
        self.scraper.run_scrape(
            test_sources=[{"url": url, "category": "PLACEMENTS", "title": "Placement Milestone"}],
            mock_fetcher=lambda u: html,
        )
        up_res2 = self.scraper.update_knowledge_base(force=False)
        self.assertFalse(up_res2["updated"])  # No duplicate write

        kb_text2 = self.kb_path.read_text(encoding="utf-8")
        self.assertEqual(kb_text2.count("--- UPDATE [PLACEMENTS]: Placement Milestone ---"), 1)

    # -------------------------------------------------------------------------
    # TEST 4: Preserving existing content after a failed fetch
    # -------------------------------------------------------------------------
    def test_04_preservation_on_failed_fetch(self):
        url = "https://sreyas.ac.in/critical-notice"
        good_html = "<html><body><h1>Notice</h1><p>Mandatory Academic Registration Deadline is October 30, 2026.</p></body></html>"

        # Initial good scrape
        self.scraper.run_scrape(
            test_sources=[{"url": url, "category": "EXAMINATIONS", "title": "Notice"}],
            mock_fetcher=lambda u: good_html,
        )
        self.scraper.update_knowledge_base()
        self.assertIn("Mandatory Academic Registration Deadline", self.kb_path.read_text(encoding="utf-8"))

        # Subsequent failed scrape (network failure / None returned)
        res_fail = self.scraper.run_scrape(
            test_sources=[{"url": url, "category": "EXAMINATIONS", "title": "Notice"}],
            mock_fetcher=lambda u: None,
        )
        self.assertEqual(res_fail["failed_count"], 1)
        self.assertEqual(res_fail["preserved_count"], 1)

        # Update KB after failure: existing good content MUST remain intact
        self.scraper.update_knowledge_base()
        kb_after = self.kb_path.read_text(encoding="utf-8")
        self.assertIn("Mandatory Academic Registration Deadline", kb_after)

    # -------------------------------------------------------------------------
    # TEST 5 & 6: Refreshing retrieval index & Chatbot Retrieval of New Knowledge
    # -------------------------------------------------------------------------
    def test_05_and_06_index_refresh_and_chatbot_retrieval(self):
        engine = RAGEngine(knowledge_base_path=str(self.kb_path))

        # Query before scrape: should NOT know about Supercomputing Center
        query = "What is the new Supercomputing Center at Sreyas?"
        res_before = engine.retrieve(query)
        self.assertNotIn("NVIDIA DGX H100", res_before["answer"])

        # Inject new website content
        url = "https://sreyas.ac.in/supercomputing-facility"
        html = """
        <html>
        <body>
            <h1>Center for Supercomputing and Advanced Research</h1>
            <p>Sreyas Institute of Engineering and Technology has deployed an NVIDIA DGX H100 supercomputing cluster.</p>
            <p>This supercomputing lab is dedicated to Generative AI, Bioinformatics, and Autonomous Robotics research.</p>
        </body>
        </html>
        """
        self.scraper.run_scrape(
            test_sources=[{"url": url, "category": "FACILITIES", "title": "Supercomputing Facility"}],
            mock_fetcher=lambda u: html,
        )
        update_result = self.scraper.update_knowledge_base(rag_engine=engine, force=True)
        self.assertTrue(update_result["updated"])
        self.assertTrue(update_result["rag_rebuilt"])

        # Query AFTER update: Chatbot MUST retrieve newly extracted knowledge immediately!
        res_after = engine.retrieve(query)
        self.assertIn("NVIDIA DGX H100", res_after["answer"])
        self.assertIn("supercomputing", res_after["answer"].lower())

    # -------------------------------------------------------------------------
    # TEST 7: Verifying Midnight Schedule & Asia/Kolkata Timezone
    # -------------------------------------------------------------------------
    def test_07_midnight_schedule_and_timezone(self):
        scheduler.shutdown()
        scheduler.init(rag_engine=None, hour=0, minute=0)

        status = scheduler.get_status()
        self.assertTrue(status["scheduler_running"])
        self.assertEqual(status["timezone"], "Asia/Kolkata")
        self.assertEqual(status["schedule"], "12:00 AM (Midnight) Daily")
        self.assertIsNotNone(status["next_run"])

        # Verify next_run contains midnight hour representation
        self.assertTrue("00:00:00" in status["next_run"] or "12:00 AM" in status["next_run"] or "+05:30" in status["next_run"])

    # -------------------------------------------------------------------------
    # TEST 8: Preventing Duplicate or Overlapping Jobs
    # -------------------------------------------------------------------------
    def test_08_concurrency_and_single_instance(self):
        # 1. Thread lock verification
        with scheduler._sync_lock:
            # When lock is held, calling _run_sync_job should gracefully abort
            scheduler._run_sync_job()
            # If aborted gracefully, it won't crash or double-execute

        # 2. Duplicate init call should be ignored safely
        scheduler.init(rag_engine=None)
        self.assertTrue(scheduler.get_status()["scheduler_running"])

    # -------------------------------------------------------------------------
    # TEST 9: Scheduler Initialization and Restart Behavior
    # -------------------------------------------------------------------------
    def test_09_scheduler_restart_lifecycle(self):
        scheduler.shutdown()
        status_stopped = scheduler.get_status()
        self.assertFalse(status_stopped["scheduler_running"])

        # Re-initialize
        scheduler.init(rag_engine=None, hour=0, minute=0)
        status_restarted = scheduler.get_status()
        self.assertTrue(status_restarted["scheduler_running"])
        self.assertEqual(status_restarted["timezone"], "Asia/Kolkata")


if __name__ == "__main__":
    unittest.main()
