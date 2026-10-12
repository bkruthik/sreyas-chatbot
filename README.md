# SREYAS AI Assistant 🎓

Official Virtual Assistant for **Sreyas Institute of Engineering and Technology (SIET)**, Nagole, Hyderabad.

An enterprise-ready AI chatbot delivering verified campus information, admissions guidance, fee details, bus-route navigation, placement analytics, and exam regulations across **English**, **Telugu**, and **Hindi**.

---

## 🌟 Key Features

* **Retrieval-Augmented Generation (RAG)**: Fast, deterministic subtopic retrieval over indexed campus knowledge base.
* **NeMo-Inspired Guardrails**: Content moderation, prompt injection detection, and positive factual redirection.
* **Multilingual Engine**: Native support for **English**, **Telugu**, and **Hindi** with automated character normalization and technical term preservation.
* **Interactive UI**: Responsive floating widget with bus-route sequence cards, interactive stop timelines, timestamps, and suggestion chips.
* **Lead & Admission Management**: In-app general enquiry and B.Tech admission forms persisting directly to Excel without external database dependencies.
* **Admin Analytics Dashboard**: Secure web interface (`/admin`) with real-time stats, enquiry workflow manager, query charts, and sync controls.
* **Autonomous Knowledge Sync**: Scheduled background synchronizer updating campus information automatically from official portals.

---

## 🚀 Quick Start (Local Execution)

### Prerequisites
* Python 3.9+ (Python 3.11 recommended)
* `pip` package manager

### 1. Clone the Repository
```bash
git clone https://github.com/bkruthik/sreyas-chatbot.git
cd sreyas-chatbot
```

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

### 4. Configure Environment (Optional)
```bash
cp .env.example .env
# Edit .env with custom SMTP or admin credentials if needed
```

### 5. Run the Server
```bash
python app.py
```
Open your browser and navigate to:
* **Chatbot Widget**: [http://localhost:5000](http://localhost:5000)
* **Admin Dashboard**: [http://localhost:5000/admin](http://localhost:5000/admin)
* **API Health Check**: [http://localhost:5000/api/health](http://localhost:5000/api/health)

---

## 🐳 Docker Deployment (Project Lab Systems)

The project includes pre-configured Docker definitions for lab computers or container environments without local Python installation requirements.

### Running with Docker Compose (Recommended)
```bash
docker compose up -d --build
```
* **Persistent Storage**: Data directory (`./data`) is mounted into the container to preserve enquiries and feedback across container restarts.

### Running with Docker CLI
```bash
# Build image
docker build -t sreyas-ai-assistant .

# Run container with persistent volume
docker run -d -p 5000:5000 -v $(pwd)/data:/app/data --name sreyas_assistant sreyas-ai-assistant
```

---

## 📁 Project Architecture

```
sreyas-chatbot/
├── app.py                     # Flask application entry point & API endpoints
├── config.py                  # Production configuration & security settings
├── scheduler.py               # Background task scheduler & sync daemon
├── requirements.txt           # Python dependencies
├── Dockerfile                 # Container image specification
├── docker-compose.yml         # Container service & storage configuration
├── config/                    # Configuration datasets
│   ├── bus_routes.json        # 20 verified college bus routes and stops
│   ├── facts.json             # Fast structured facts lookup
│   ├── sources.json           # Institutional source mapping
│   ├── synonyms.json          # Academic domain synonyms & normalizer
│   ├── themes.json            # Dynamic UI themes & event styling
│   └── translations.json      # Trilingual translation dictionaries
├── data/                      # Persistent storage & knowledge assets
│   ├── knowledge_base.txt     # Complete institutional knowledge base
│   ├── enquiries.xlsx         # Enquiry leads storage (auto-generated)
│   ├── feedback.xlsx          # Feedback ratings storage (auto-generated)
│   └── sync_history.json      # Sync audit tracking
├── modules/                   # Core system engines
│   ├── rag.py                 # RAG retrieval & subtopic slicer
│   ├── intent_engine.py       # Pre-RAG classification & FAQ dispatcher
│   ├── guardrails.py          # Input safety & content moderation
│   ├── language.py            # Multilingual translator & localizer
│   ├── enquiry.py             # Thread-safe Excel enquiry manager
│   ├── feedback.py            # User feedback collector
│   ├── email_service.py       # Notification mailer
│   ├── memory.py              # Contextual conversation memory
│   └── logger.py              # Structured logging & audit trails
├── scripts/                   # Management & testing scripts
│   ├── sync_knowledge_base.py # Autonomous web synchronizer
│   └── verify_production.py   # 31-point comprehensive regression suite
└── static/                    # Frontend user interface
    ├── index.html             # Client chatbot widget
    ├── admin.html             # Admin analytics dashboard
    └── 404.html               # Custom branded error page
```

---

## 🧪 Testing & Verification

Run the full 31-point test suite covering multilingual rendering, enquiry workflows, transport routes, and guardrails:

```bash
python scripts/verify_production.py
```

Expected output:
```
Regression Tests : 31/31 PASSED
PRODUCTION READY
```

---

## 🛡️ Security & Privacy Notice

* All student enquiry records and user logs are excluded from Git tracking via `.gitignore`.
* Production credentials and API keys are environment-configurable (`.env`) and never committed to source control.
* Clean storage templates (`enquiries.xlsx` and `feedback.xlsx`) are provided out-of-the-box.
