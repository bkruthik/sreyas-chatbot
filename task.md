**# Sreyas Chatbot — Production Upgrade Tasks**

## Phase 1 — Config & Backend
- [x] Create `config/facts.json` — configurable fact lookup table
- [x] Expand `config/themes.json` — add date ranges, animations, greetings, chip sets
- [x] Update `app.py` — date-based theme auto-select from themes.json
- [x] Update `app.py` — facts.json-based precision lookup (pre-RAG)
- [x] Update `app.py` — unsupported programs block (M.Tech, MBA, MCA, Diploma)
- [x] Update `app.py` — negative query factual redirect
- [x] Update `app.py` — missing admin endpoints (summary, analytics, most_asked, audit_logs)
- [x] Update `app.py` — simple TTL response cache
- [x] Update `app.py` — conversation analytics logging enhancements
- [x] Update `app.py` — custom 404 error page route
- [x] Update `modules/guardrails.py` — rich factual negative redirect
- [x] Expand `data/knowledge_base.txt` — from 153 to 330+ lines

## Phase 2 — Frontend
- [x] Rewrite `static/index.html` — Gemini-style complete clean rewrite
  - [x] Premium typography (Google Sans/Inter from Google Fonts)
  - [x] CSS design tokens (custom properties)
  - [x] Glassmorphism landing card with quick-action chips
  - [x] Full theme engine (header + background + animation + greeting + chips)
  - [x] Theme animations (snow for Christmas, flag for Independence Day, confetti for New Year)
  - [x] Three-dot Gemini-style typing indicator
  - [x] Smooth fade+slide message bubble animations
  - [x] Formatted answer cards for precise facts (divider, icon, value)
  - [x] Better suggestion chips (ripple, hover-lift, shadow)
  - [x] Professional enquiry success screen (Reference ID, email confirmation)
  - [x] Auto-open on first visit
  - [x] Responsive mobile design
- [x] Rewrite `static/admin.html` — full dashboard with charts
  - [x] Chart.js for pie/bar/line charts
  - [x] Today's leads count
  - [x] Conversation analytics panel
  - [x] Language usage chart
  - [x] Feedback ratio display
  - [x] Most asked categories
  - [x] Pending/Reviewed/Closed counts
- [x] Create `static/404.html` — custom branded error page

## Phase 3 — Cleanup
- [x] Remove stray `enquiries.xlsx` from root
- [x] Update `.gitignore` — add `__pycache__/`, `*.pyc`, `venv/`
- [x] Verify no test files remain
- [x] Final server restart and verification
