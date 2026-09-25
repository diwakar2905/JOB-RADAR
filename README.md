# 🎯 Job Radar

> **Personal, locally-run autonomous agent that finds hiring startups matching your target profile, ranks them by fit (0–100), and provides direct 1-click application links.**

Job Radar monitors public ATS feeds (Greenhouse, Lever, Ashby), Hacker News ("Who is hiring?"), Y Combinator startups, and web searches (Tavily). It deduplicates openings, filters dealbreakers, researches company context, scores candidate fit with citation URLs, and presents a local review queue in Streamlit.

---

## 🏗 Architecture & Data Flow

```text
[Windows Task Scheduler / CLI] 
       │ (every 4–6 hrs with lockfile & cursors)
       ▼
 [Discovery Sources] ──► [ATS: Greenhouse, Lever, Ashby]
                     ──► [Hacker News Algolia API]
                     ──► [Tavily Web Search]
                     ──► [YC Public Directory]
       │
       ▼
 [Normalize & Dedupe] ──► SHA-256 hash(domain:title:location)
       │
       ▼
 [Filter Dealbreakers] ──► Drops unpaid, outside India on-site, 5+ yrs exp, avoided companies
       │
       ▼
 [Company Research]  ──► Stage, funding, founders, product (14-day SQLite cache)
       │
       ▼
 [Fit Scoring 0-100] ──► Claude API / Ollama / Heuristic with mandatory citation URLs
       │
       ▼
 [SQLite Database]   ──► db.sqlite (companies, openings, matches, runs, cursors)
       │
       ▼
 [Streamlit Dashboard] ──► Ranked review queue, 1-click apply, company cards, status tracking
```

---

## ⚡ Quickstart

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Your Profile & Targeting
- **`profile.json`**: Never committed (personal data). Copy the starter template and edit it, or let
  the CLI build one for you:
  ```bash
  cp profile.example.json profile.json   # then edit by hand, or:
  python -m radar profile --resume data/resume.pdf --github diwakarmishra
  ```
- **`config.yaml`**: Set target roles, locations (`India`, `remote`), seniority (`intern`, `fresher`, `junior`), company watchlist, and dealbreakers.
- **`.env`**: Add your optional API keys (`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `OLLAMA_HOST`). Job Radar includes a fallback scoring engine that works even without paid API keys!

### 3. Run the Discovery Pipeline
```bash
# Check config, keys, Ollama, and DB health first:
python -m radar doctor

# Run full discovery pipeline across all sources:
python -m radar run
# (equivalently: python run.py)

# Or run specific sources:
python -m radar run --source ats --limit 10
python -m radar run --source hn --limit 5
python -m radar run --dry-run   # no DB writes, no LLM scoring spend

# Rebuild profile.json from resume/GitHub/site:
python -m radar profile --resume data/resume.pdf --github diwakarmishra
```

### 4. Launch the Local Dashboard
```bash
streamlit run app.py
```
Open **`http://localhost:8501`** in your browser.

---

## 🖥 Automated Background Scheduling

Job Radar can run unattended every `schedule_hours` (default 6), catching up on
whatever it missed after the laptop was asleep or off.

**Windows** (PowerShell as Administrator):
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_scheduler.ps1
powershell -ExecutionPolicy Bypass -File scripts\setup_scheduler.ps1 -Uninstall
```

**macOS** (launchd):
```bash
scripts/install_schedule_mac.sh
scripts/install_schedule_mac.sh --uninstall
```

**Linux** (cron):
```bash
scripts/install_schedule_linux.sh
scripts/install_schedule_linux.sh --uninstall
```

- **Overlap Prevention**: Uses a single-instance lock file (`job_radar.lock`).
- **Gap & Sleep Tolerance**: Tracks progress via `source_cursors` in SQLite; missed runs catch up seamlessly.
- **Non-blocking Desktop Alerts**: Sends a notification when high-fit matches (>= 80) are found.

---

## 🧪 Testing

Run the test suite:
```bash
python -m pytest tests/test_radar.py -v
```

---

## 🛡️ Built-in Guardrails
1. **Public Data Only**: Zero LinkedIn or logged-in scraping; relies on official public APIs.
2. **Strict Citation Requirement**: Every fit score claim carries a verified source URL.
3. **Monthly Spend Cap**: Tracks API costs in SQLite and stops external calls before exceeding `$5.00/month`.
4. **Link Health Verification**: Validates application links via HTTP HEAD requests and flags broken/dead URLs with warning badges.
