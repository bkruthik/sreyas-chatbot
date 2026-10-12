====================================================
  SREYAS ASSISTANT — Setup & Run Instructions
====================================================

STEP 1 — Install Python libraries (only once):
  pip install -r requirements.txt

STEP 2 — Start the server:
  python app.py

STEP 3 — Open in browser:
  http://localhost:5000

====================================================
  FEATURES
====================================================
✅ RAG Engine        — Searches knowledge_base.txt to answer questions
✅ NeMo Guardrails   — Blocks negative / off-topic responses
✅ Smart Enquiry Form — Appears when bot can't answer, saves to Excel
✅ Excel Export       — enquiries.xlsx auto-created in this folder
✅ Student/Parent Mode — Different questions shown for each
✅ 3 Languages        — English, Telugu, Hindi
✅ Embeddable Widget  — Single floating button, works on any website

====================================================
  FILES
====================================================
app.py              — Python Flask backend (RAG + Guardrails + API)
index.html          — Frontend chatbot UI
knowledge_base.txt  — College data (edit this to update answers)
requirements.txt    — Python packages needed
enquiries.xlsx      — Auto-created when someone submits the form
README.txt          — This file

====================================================
  TO UPDATE COLLEGE DATA
====================================================
Just edit knowledge_base.txt — no code changes needed.
The RAG engine will automatically use the updated data.

====================================================
  TO ADD TO COLLEGE WEBSITE
====================================================
Option A — Replace their chatbot:
  Copy the <button id="sr-launcher"> and <div id="sr-panel">
  blocks from index.html into the college website pages.
  Change BACKEND = "http://localhost:5000" to the server URL.

Option B — Run as separate page:
  Deploy this entire folder on any Python hosting server.

====================================================
  VIEW ENQUIRIES
====================================================
Open enquiries.xlsx in Excel to see all collected leads.
Or visit: http://localhost:5000/api/enquiries

====================================================
