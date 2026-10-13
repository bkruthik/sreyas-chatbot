# SREYAS AI Assistant 🎓

Official Virtual Assistant for **Sreyas Institute of Engineering and Technology (SIET)**, Nagole, Hyderabad.

An enterprise-ready AI chatbot delivering verified campus information, admissions guidance, fee details, bus-route navigation, placement analytics, and exam regulations across **English**, **Telugu**, and **Hindi**.

---

## 🌟 Key Features

* **Retrieval-Augmented Generation (RAG)**: Fast, deterministic TF-IDF subtopic retrieval over indexed campus knowledge base with zero external cloud LLM dependencies.
* **Automatic Midnight Website Scraper**: Runs automatically every day at **12:00 AM (Midnight) Asia/Kolkata** to fetch official campus updates from `sreyas.ac.in`, extract new announcements, detect content changes, and safely update the knowledge base.
* **Zero-Downtime Hot Index Reload**: Automatically triggers `rag_engine.rebuild_index()` when knowledge base changes occur, making updated website notices immediately queryable without restarting the application server.
* **Multi-Provider SMTP Email Notifications**: Structured, asynchronous dispatch with specific SMTP error categorization supporting Gmail, Outlook/Hotmail, Yahoo, and college domains.
* **NeMo-Inspired Guardrails**: Content moderation, prompt injection detection, and positive factual redirection.
* **Trilingual Multilingual Engine**: Native support for **English**, **Telugu**, and **Hindi** with automated character normalization and technical term preservation.
* **Interactive UI**: Responsive floating widget with bus-route sequence cards, interactive stop timelines, timestamps, verified Google Maps directions, and suggestion chips.
* **Lead & Admission Management**: In-app general enquiry and B.Tech admission forms persisting directly to Excel and SQLite without external database dependencies.
* **Admin Analytics Dashboard**: Secure web interface (`/admin`) with real-time stats, enquiry workflow manager, query charts, and sync controls.

---

## 📁 System Architecture

```
sreyas-chatbot/
├── app.py                         # Flask application entry point & API endpoints
├── config.py                      # Production configuration & security settings
├── scheduler.py                   # Automatic midnight APScheduler daemon (Asia/Kolkata)
├── requirements.txt               # Production Python dependencies
├── Dockerfile                     # Production container image specification
├── docker-compose.yml             # Container service, volume mount & health check
├── .env.example                   # Environment configuration template
├── config/                        # Configuration datasets
│   ├── bus_routes.json            # 20 verified college bus routes and stops
│   ├── facts.json                 # Fast structured facts lookup
│   ├── sources.json               # Institutional source mapping & approved URLs
│   ├── synonyms.json              # Academic domain synonyms & normalizer
│   ├── themes.json                # Dynamic UI themes & event styling
│   └── translations.json          # Trilingual translation dictionaries
├── data/                          # Persistent storage & knowledge assets
│   ├── knowledge_base.txt         # Complete institutional knowledge base
│   ├── enquiries.xlsx             # Enquiry leads storage (Excel)
│   ├── feedback.xlsx              # Feedback ratings storage (Excel)
│   ├── students.db                # SQLite database for parent & student portal
│   ├── sync_history.json          # Persistent sync execution audit history
│   └── backups/                   # Timestamped atomic backups of knowledge base
├── modules/                       # Core system engines
│   ├── scraper.py                 # College website scraper, change detector & KB updater
│   ├── rag.py                     # RAG retrieval, TF-IDF vectorizer & subtopic slicer
│   ├── intent_engine.py           # Pre-RAG classification & FAQ dispatcher
│   ├── guardrails.py              # Input safety & content moderation
│   ├── language.py                # Multilingual translator & localizer
│   ├── enquiry.py                 # Thread-safe Excel enquiry manager
│   ├── feedback.py                # User feedback collector
│   ├── email_service.py           # Multi-provider SMTP mailer with retry classification
│   ├── memory.py                  # Contextual conversation memory
│   └── logger.py                  # Structured logging & audit trails
├── scripts/                       # Management & testing scripts
│   ├── sync_knowledge_base.py     # Section-level pattern synchronizer
│   ├── test_scraper_scheduler.py  # Automated scraper & midnight scheduler verification suite
│   └── verify_production.py       # 31-point comprehensive regression suite
└── static/                        # Frontend user interface
    ├── index.html                 # Client chatbot widget
    ├── admin.html                 # Admin analytics dashboard
    └── 404.html                   # Custom branded error page
```

---

## 🕒 Automatic Midnight Website Scraping

The assistant includes an automated web feature extractor that tracks official Sreyas web pages:

1. **Schedule**: Configured with APScheduler `CronTrigger` to execute daily at **12:00 AM (Midnight)** in the **`Asia/Kolkata` (IST)** timezone (`hour=0, minute=0`).
2. **Robots.txt & Etiquette**: Automatically checks `robots.txt` via `urllib.robotparser`, respects timeouts, and rate limits requests.
3. **Change Detection**: Computes SHA-256 hashes of extracted web content to detect new, modified, and unchanged announcements.
4. **Failure Preservation**: If a website endpoint is down or returns invalid content, the last known good content is preserved. The knowledge base is never corrupted or cleared on network errors.
5. **Atomic Updates & Backups**: Changes to `data/knowledge_base.txt` are written atomically with a pre-write timestamped backup stored in `data/backups/`.
6. **Index Hot-Reload**: On successful KB updates, `rag_engine.rebuild_index()` is invoked automatically, updating the TF-IDF chunk vector space in memory with zero server downtime.
7. **Single-Scheduler Process Lock**: Uses kernel-level file locking (`data/.scheduler_process.lock`) ensuring only one worker instance triggers the midnight job in multi-process/multi-worker deployments.

---

## 🐳 Docker Deployment (Production / College Lab Systems)

The project includes container specifications designed to execute independently on lab computers or servers without local Python installation requirements.

### Prerequisites
* Docker and Docker Compose v2.0+

### 1. Configure Environment (Optional)
```bash
cp .env.example .env
# Edit .env with your SMTP credentials and admin password
```

### 2. Start the Container
```bash
docker compose up -d --build
```

### 3. Verify Health & Running Status
```bash
# Check container status
docker ps

# Check API health and midnight scheduler state
curl http://localhost:5000/api/health
```
Example health check response:
```json
{
  "college": "Sreyas Institute of Engineering and Technology",
  "rag_chunks": 22,
  "scheduler": {
    "is_primary_worker": true,
    "next_run": "2026-10-14T00:00:00+05:30",
    "schedule": "12:00 AM (Midnight) Daily",
    "scheduler_running": true,
    "scheduler_type": "APScheduler",
    "timezone": "Asia/Kolkata"
  },
  "status": "running",
  "vocabulary_size": 1358
}
```

### 4. Persistent Storage & Container Recreation
The `./data` directory is bind-mounted to `/app/data` inside the container:
* Enquiries (`enquiries.xlsx`), feedback ratings (`feedback.xlsx`), student DB (`students.db`), and backups (`backups/`) survive `docker compose down` and container recreation.
* To safely recreate the container without losing data:
  ```bash
  docker compose up -d --force-recreate
  ```

---

## 🚀 Local Development Setup

### 1. Prerequisites
* Python 3.11+
* `pip` package manager

### 2. Set Up Virtual Environment
```bash
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Run the Server
```bash
python app.py
```
Open your browser and navigate to:
* **Chatbot Widget**: [http://localhost:5000](http://localhost:5000)
* **Admin Dashboard**: [http://localhost:5000/admin](http://localhost:5000/admin)
* **API Health Check**: [http://localhost:5000/api/health](http://localhost:5000/api/health)

---

## ⚙️ Environment Variables & External Services

| Variable | Default | Purpose |
|---|---|---|
| `HOST` | `0.0.0.0` | Server bind interface |
| `PORT` | `5000` | Server listen port |
| `FLASK_DEBUG` | `false` | Enable or disable Flask debug mode |
| `FLASK_SECRET_KEY` | *(Built-in default)* | Session encryption key |
| `ADMIN_PASSWORD` | `SreyasAdmin2026!` | Admin dashboard login password |
| `SMTP_HOST` | `smtp.gmail.com` | Outgoing SMTP mail server |
| `SMTP_PORT` | `587` | Outgoing SMTP port (587 TLS or 465 SSL) |
| `SMTP_USER` | `""` | SMTP sender username / address |
| `SMTP_PASS` | `""` | SMTP sender password or App Password |
| `SMTP_USE_TLS` | `true` | Enable STARTTLS connection encryption |
| `SMTP_FROM_EMAIL` | `info@sreyas.ac.in` | From header email address |
| `AUTO_SYNC_ENABLED`| `true` | Enable midnight knowledge sync daemon |

---

## 🧪 Testing & Verification

### 1. Full Production Regression Test Suite (31 Tests)
```bash
python scripts/verify_production.py
```
Expected output:
```text
Regression Tests : 31/31 PASSED
========================================
PRODUCTION READY
========================================
```

### 2. Scraper & Midnight Scheduler Test Suite (8 Tests)
```bash
python scripts/test_scraper_scheduler.py
```
Verifies HTML parsing, change detection, duplicate prevention, failure recovery, RAG reload, midnight IST scheduling, and concurrency controls.
Expected output:
```text
Ran 8 tests in 0.260s
OK
```

---

## 🛡️ Security & Privacy Notice

* All student database records (`students.db`), local enquiry sheets, and scrape caches are excluded from Git via `.gitignore`.
* Production credentials and API keys are environment-configurable (`.env`) and never committed to source control.
* Clean storage templates are provided out-of-the-box.
