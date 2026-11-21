# Job Search Tracker

[![CI](https://github.com/SamGuercho/job-search-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/SamGuercho/job-search-tracker/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A local web app to run a job search: follow every application through its process, keep one
**master CV** and generate a **one-page CV tailored to each job** (Word + PDF), with an optional
**AI assistant** that can do it all for you from a job link.

Everything runs on your computer. Your data stays in files on your disk; nothing is sent anywhere
unless you add an OpenAI key (then only the text needed for AI features goes to OpenAI).

---

## Contents
1. [Quick start](#quick-start)
2. [First steps](#first-steps)
3. [What you can do](#what-you-can-do)
4. [Use it from Cursor / Claude (MCP)](#use-it-from-cursor--claude-mcp)
5. [Your data and privacy](#your-data-and-privacy)
6. [Configuration](#configuration)
7. [Troubleshooting](#troubleshooting)
8. [For developers](#for-developers)

---

## Quick start

**You need**
- **Python 3.11+** (`python3 --version`). macOS: `brew install python` or python.org. Windows: python.org.
- **For PDF export** (optional but recommended), one of:
  - **LibreOffice** (free, recommended): `brew install --cask libreoffice` on macOS, or libreoffice.org.
  - **Microsoft Word** (macOS/Windows). The first export asks permission to control Word: accept it.
  Without either, you still get the Word file; the one-page fit is then estimated.
- **For AI features** (optional): an OpenAI API key — see [Configuration](#configuration).

**Install and run (macOS / Linux)**
```bash
git clone <this repository's URL> job-search-tracker
cd job-search-tracker
./run.sh
```
Open **http://127.0.0.1:8000**. The first run creates a virtual environment, installs the dependencies
and creates your `.env` file. Stop the app with `Ctrl+C`; run `./run.sh` again to restart it.

**Windows** (PowerShell)
```powershell
git clone <this repository's URL> job-search-tracker
cd job-search-tracker
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python -m jobtracker serve --port 8000
```

**Or with Docker** (nothing else to install: LibreOffice and the CV font are in the image)
```bash
git clone <this repository's URL> job-search-tracker
cd job-search-tracker
cp .env.example .env        # optional: OpenAI key, password…
docker compose up --build
```
Open **http://127.0.0.1:8000**. Data stays in `./data` (shared with `./run.sh`), CVs go to `./output`
(or `HOST_OUTPUT_DIR` in `.env`). Linux hosts: the container runs as user id 10001, so give it write access
to `./data` and `./output` (e.g. `sudo chown -R 10001 data output`).

**Updating:** `git pull`, then `./run.sh` (new dependencies are installed automatically) or
`docker compose up --build`.

**On your phone:** the app is installable (home-screen icon, full screen), and reachable from anywhere through
[Tailscale](https://tailscale.com) (free private network between your devices, with https):
- **Always on (recommended): a home server.** Run it on a computer that stays on at home, with Docker and
  automatic updates: step-by-step guide in [`deploy/home-server/`](deploy/home-server/README.md) (Windows).
- **From your laptop**, while it's awake: set `JOBTRACKER_PASSWORD` in `.env`, run `./run.sh --tailscale`, and
  open the printed `https://….ts.net` address on your phone.
- **Friends** can use your home server with their own separate data: share the PC with them in Tailscale,
  nothing else to set up. See [Share with friends](deploy/home-server/README.md#11-share-with-friends).

Then *Install app* (Chrome on Android) or *Add to Home Screen* (Safari on iPhone).

---

## First steps

The app starts with **example data** (a fictional "Your Name" CV and two sample applications) so the
pages aren't empty. Replace it with yours:

1. **Your CV** → *Master CV* tab.
   - With an OpenAI key: **Import from CV file** (.docx / .pdf), review the result, choose *Replace*.
   - Without: fill in Profile, then add your experiences and their bullet points by hand.
   - Fill **Position (shown under your name)** and your **LinkedIn URL**.
2. **Delete the two sample applications** (open each from *Follow-up* → *Delete*).
3. **Choose where CVs are saved** (default: the `output/` folder of the app): set `JOBTRACKER_OUTPUT_DIR`
   in `.env`, e.g. a folder in iCloud Drive or Dropbox, then restart.
4. **Optional:** add your `OPENAI_API_KEY` in `.env` and restart to enable the assistant and AI features.

Then add your first application with **+ Add company** (paste the job description) or ask the assistant
"*Add this job and make my CV: <link or text>*".

---

## What you can do

### Follow-up (home page)
One row per application: status and current step, days since the last update (by you or them), location
(📍 opens Google Maps) and work model, salary, source, keywords, main qualities, contact, referee,
seniority. Sort by any column, search (including keywords), filter by status or work model, choose the
visible columns (**Columns**), and log a follow-up in one click. The chat bar sends a request to the assistant.

### Application page
- **Process graph** — default steps *Applied → Response received → First HR interview → Reference check*,
  plus a red **Rejected / Closed** node every step can lead to. Click a step to turn it Off / Planned / Done,
  date it, add notes, set its type; **+** on a link inserts a step there; **⑂ Branch** adds a parallel path.
  The application's **status is computed from the graph** (Not applied, Applied, In process, Offer, Rejected).
- **CV summary** — the paragraph at the top of the CV, tailorable per application, with an AI relevance check.
- **Details** — position, location/address, work model, job posting link, source, salary (yours and posted),
  contact, referee, qualities, notes, keywords, job description.
- **Job description** — saved as `Job_Description.md` in the company's folder, so you keep it when the posting
  disappears. Updated when you change it; older copies go to `archive/`.
- **Generated CVs**, map, follow-up log.

### CV builder (a CV for one job)
Pick the application or paste the job description → **Find matching keywords**: each requirement of the
ad is matched to the words **actually written in your CV** (same word, synonym, or AI), with the
requirements your CV doesn't cover listed as gaps. The preview shows which achievements are used and why;
**Create CV** writes `<Company>/<Your_Name>_CV.docx` and `.pdf`. Every achievement is used, each in its wording
that best fits the job, so a short job description still gets a full CV; only when it exceeds one page are
bullets cut, those matching no keyword first, then those repeating covered keywords.

### Master CV
Your whole career, as **achievements** (one fact each) with keywords. An achievement can have several
**wordings** (same facts, different keywords); a CV uses at most one wording per achievement, so nothing
is repeated. Keywords only count when they are written in the text (✓/✗ shown live).
**✨ Draft wordings with AI** proposes rewordings that never add facts or numbers; nothing is saved until
you click *Keep*. **📄 Create general CV** makes a CV for no job in particular (headhunters, networking),
saved in a `Master CV` folder. Also: education, skills, languages, synonyms, extra keywords to watch.

### Analytics
Applications sent, response rate, interviews (incl. technical), offers, days searching, time to a response,
funnel, where applications stop, applications per week, results by source and work model.

### Interview prep
On an application's page, **✨ Prepare interview** (premium accounts) has Codex research the company and write a
prep pack: likely interview process, topics, your angle, exercises with solutions and 10-point rubrics, questions
to ask (also saved as `Interview_Prep.md`). **Practice with the assistant** then quizzes you and grades your
answers with the rubrics. Codex runs with your ChatGPT plan, not API billing: setup in the home-server guide.

### Interview recordings
On an application's page, upload the audio of an interview (from your phone's recorder, Zoom/Meet…). You see the
estimated cost first (≈ $0.006 per minute to transcribe, a few cents for the debrief), then: transcription with
speaker labels, you pick which voice is yours, and a debrief: summary, what they said about the role, next
steps, every question with your answer graded /10 and what was missed, strengths, what to improve, and a
thank-you email (also saved as `Debrief_….md`). The graded answers go to the Practice page (as "interview"), and
the next prep builds on them. Needs `OPENAI_API_KEY` and ffmpeg (included in the Docker image).

### On your phone
- **Add to calendar:** give an interview step a date, time, duration and place (address or video link), then
  📅 *Add to calendar*: Google Calendar, or an `.ics` file for Apple Calendar / Outlook (reminders the day
  before and 1 hour before). 🔔 → *Subscribe to my interviews* gives a calendar of every scheduled interview that
  stays up to date (Apple Calendar, Outlook; Google Calendar can't reach a private Tailscale address).
- **Share a job (Android):** in LinkedIn or Chrome, *Share* → *Job Search*: the assistant adds the job and makes
  your CV. On iPhone, paste the link in the assistant.
- **Record an interview** in the app (🎙 on the application page): keep the screen on until you stop.
- **Notifications** (🔔 → *Turn on*): interview reminders, prep ready, debrief ready, follow-ups due. On iPhone
  they work in the app installed on the Home Screen (iOS 16.4+). The follow-ups due also show as a badge on the
  app icon (Android, desktop).

### Costs
The **Costs** page shows what the AI features cost: this month and last, per day, by feature, provider and model,
the typical cost of one use of each feature, and (owner, shared server) per person. Each Assistant answer shows
its cost too. These are estimates from the usage the providers report × list prices (`jobtracker/prices.py`);
your OpenAI / Anthropic bills are the reference. Codex preps are $0 (ChatGPT plan).

### Assistant (needs an OpenAI key)
A chat that uses the app's features for you: add a job from a link or pasted text (company, details, saved
description, tailored CV, gaps), update process steps ("I got an HR call for Monday 10am"), list follow-ups,
review your summary, interview you to grow your master CV, show analytics. Anything that **deletes or
overwrites data, or adds text to your CV, waits for your Approve / Reject**. Conversations are titled
"Company · stage" automatically.

---

## Use it from Cursor / Claude (MCP)

The app is also an **MCP server**: AI tools like Cursor, Claude Code, Claude Desktop or Codex get every feature as a
tool (52 of them: add an application, update its process, generate a CV, analytics…), with the same data as
the web app. Example prompts once connected: *"Add this job to my tracker and make my CV: <text>"*,
*"I had the HR interview with Nimbus today"*, *"Which applications need a follow-up?"*.

1. Install the app once (`./run.sh`, see Quick start), then print the configuration for your machine:
   ```bash
   .venv/bin/python -m jobtracker mcp-config
   ```
2. Add it to your tool:
   - **Cursor**: Settings → MCP → *Add new MCP server*, or paste the printed JSON into `~/.cursor/mcp.json`
     (merge it into `mcpServers` if the file already exists).
   - **Claude Code**: run the printed `claude mcp add …` command.
   - **Codex** (in the ChatGPT app or the CLI): run the printed `codex mcp add …` command. Codex uses your
     ChatGPT plan, not API billing: heavy work (interview prep with a top model and high reasoning) costs
     nothing extra there. Inside the ChatGPT app the CLI is at
     `/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex`.
   - **Claude Desktop**: Settings → Developer → *Edit Config*, paste the JSON, restart Claude Desktop.
3. The client starts the server itself when needed; the web app doesn't have to be running.

**Remote (the app on another machine, e.g. the home server):** the web app also serves the same tools over
HTTP at `/mcp`. Connect a client by URL instead of a command, through Tailscale:
```bash
codex mcp add job-search-tracker --url https://<server>.<tailnet>.ts.net/mcp
```
Each call runs as your Tailscale login, like the web app: friends sharing the server only see their own data.

Tools that delete or overwrite data, or add text to your CV, are flagged *destructive* and tell the AI to ask
you first; most clients also ask for your confirmation before any tool call. The AI-powered tools (matching,
drafts, reviews, import) use the `OPENAI_API_KEY` from `.env` (and Claude as fallback, see `ANTHROPIC_API_KEY`). Pair it with the instructions in
`skills/job-search/SKILL.md` (Claude Code: copy the folder to `~/.claude/skills/`).

---

## Your data and privacy

| What | Where | In git? |
|---|---|---|
| Master CV, applications, chats | `data/*.json` (or `JOBTRACKER_DATA_DIR`) | **No** (ignored) |
| Backups made by the app, uploaded CVs | `data/backups/`, `data/uploads/` | **No** |
| Generated CVs, saved job descriptions | `output/<Company>/` (or `JOBTRACKER_OUTPUT_DIR`) | **No** |
| OpenAI key, local paths | `.env` | **No** |
| Example data for new users | `data/seed/` | Yes (fictional) |

- Files are never overwritten silently: a regenerated CV or changed job description moves the previous one
  to `archive/`.
- **Sent to OpenAI** (only with a key, only when you use an AI feature): the text of the CV you import, your
  master CV and the job description when matching/reviewing/drafting, and your chat messages. Nothing else.
- To back up your data, copy the `data/` folder (or point `JOBTRACKER_DATA_DIR` to a synced folder).

---

## Configuration

All settings live in `.env` (created from `.env.example` on first run). Restart after a change.

| Setting | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | empty | Enables the assistant and AI features. Keys: platform.openai.com/api-keys. Usage is billed by OpenAI, typically cents per action. |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Model for import, matching, drafts, reviews. |
| `OPENAI_CHAT_MODEL` | = `OPENAI_MODEL` | Model for the assistant (a stronger one gives better conversations). |
| `ANTHROPIC_API_KEY` | empty | Fallback: when OpenAI fails on quota, billing, access or rate limits, the app switches to Claude and says so (a toast, or a note in the chat; a chat then stays on Claude). Used directly if there is no OpenAI key. Billed per token by Anthropic. |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | Claude model for the fallback. |
| `ANTHROPIC_EFFORT` | `medium` | Claude reasoning effort for the assistant: `low` … `max`. |
| `JOBTRACKER_PREMIUM` | empty | Tailscale logins allowed to use **✨ Prepare interview** (interview prep written by Codex with your ChatGPT plan, see the home-server guide). Single-user mode: always allowed. |
| `JOBTRACKER_PREP_MODEL` / `JOBTRACKER_PREP_EFFORT` | `gpt-6.2-astra` / `high` | Codex model and reasoning for interview preps. |
| `JOBTRACKER_TRANSCRIBE_MODEL` | `gpt-4o-transcribe-diarize` | Transcription with speaker labels. OpenAI shuts it down on 2027-02-26; then the fallback below is used and the debrief tells speakers apart from the content. |
| `JOBTRACKER_TRANSCRIBE_FALLBACK_MODEL` | `gpt-transcribe` | Text-only transcription when the model above fails. |
| `JOBTRACKER_MAX_RECORDING_MB` | `500` | Largest recording you can upload. |
| `JOBTRACKER_PRICES` | empty | A JSON file to override or add model prices for the Costs page (same shape as `jobtracker/prices.py`). |
| `JOBTRACKER_CODEX` | `codex` | The Codex command (installed in the Docker image; on a Mac, the one inside the ChatGPT app works too). |
| `JOBTRACKER_OUTPUT_DIR` | `./output` | Where company folders with CVs and job descriptions go. `~` allowed. |
| `JOBTRACKER_DATA_DIR` | `./data` | Where your data files live. |
| `JOBTRACKER_CV_FILENAME` | `{name}_CV` | CV file name; `{name}` = your name, `{company}` = the company. |
| `JOBTRACKER_PDF_ENGINE` | auto | `libreoffice`, `word` or `none`. |
| `JOBTRACKER_PASSWORD` | empty | When set, every page and API call requires signing in (signed, HTTP-only session cookie). |
| `JOBTRACKER_TAILSCALE_USERS` | off | Several users behind `tailscale serve` (on in the home-server deployment): each Tailscale login gets its own space, Tailscale decides who gets in, requests without Tailscale identity are refused (no password). Only with the app listening on 127.0.0.1. |
| `JOBTRACKER_OWNER` | from the CV | Who keeps the existing data: by default the login equal to the master CV's email, remembered in `<data>/owner.txt`. Set it only if your Tailscale login differs. |
| `JOBTRACKER_USERS_DIR` | `<data>/users` | Where the other users' data lives. |
| `JOBTRACKER_SESSION_DAYS` | `30` | How long a sign-in lasts. Changing the password signs everyone out. |
| `JOBTRACKER_HOST` / `PORT` | `127.0.0.1` / `8000` | Where the server listens (`0.0.0.0` to reach it from other devices: set a password). |
| `LOG_FORMAT` / `LOG_LEVEL` | `text` / `INFO` | `json` writes one JSON object per request (method, path, status, duration). |

The CV font is **STIX Two Text** (included in macOS). On Windows/Linux, install it from
github.com/stipub/stixfonts for the PDF to look identical; otherwise a similar serif font is used.

---

## Troubleshooting

- **No PDF, only Word** → install LibreOffice (recommended) or set `JOBTRACKER_PDF_ENGINE`. With Word on macOS,
  a "control Microsoft Word" permission dialog may be waiting: accept it, then restart the app.
- **"No AI key set"** → add `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`) to `.env` and restart.
- **Port 8000 already used** → `PORT=8001 ./run.sh`.
- **A job link can't be read** → some sites (LinkedIn…) block it: paste the job text instead.
- **Page looks outdated after an update** → reload the page.
- **Undo a deletion** → deletions in the app are final; keep a copy of `data/` (see
  [Your data and privacy](#your-data-and-privacy)). To restore one, stop the app and copy it back.

---

## For developers

### Architecture
```mermaid
flowchart LR
  subgraph Clients
    UI[Web UI / installable app]
    CLI[CLI]
    MCPC[Cursor · Claude Code · Claude Desktop]
    AS[In-app assistant]
  end
  UI -- "POST /api/actions/name" --> API[FastAPI · login · logs · /health]
  CLI --> REG
  MCPC -- "MCP (stdio)" --> MCPS[MCP server]
  API --> REG
  MCPS --> REG
  AS -- "OpenAI tool calls" --> REG
  API --> AS
  REG[Action registry · 58 typed actions] --> SVC[Services: applications · process graph · master CV · matching · CV builder · analytics]
  SVC --> DATA[(data/*.json)]
  SVC --> OUT[(CVs + job descriptions per company)]
  SVC --> LO[LibreOffice / Word → PDF]
  SVC -. "optional" .-> OAI[OpenAI]
```

```
jobtracker/
  models.py, process.py   data models; process graph (steps, links, derived status) 
  storage.py              atomic JSON persistence (swap for a database here)
  registry.py             @action decorator: one typed function = API endpoint + CLI command + AI tool
  actions.py              every operation of the app, as named actions
  services/               business logic (no HTTP): companies, master_cv, cv_builder, jd_match, wording,
                          summary, analytics, cv_import, assistant, llm, pdf, files, web
  cv_templates/           Word templates (elegant.py); add one and register it in __init__.py
  api.py                  FastAPI: POST /api/actions/<name>, GET /api/actions, /files/…, /health, the web UI
  users.py, auth.py       who is visiting (Tailscale identity, per-user folders), optional password
  logs.py                 request logging (text or JSON)
  __main__.py             CLI
  mcp_server.py           MCP server: one tool per action, generated from the registry
web/                      vanilla JavaScript UI, no build step (js/views/<page>.js)
skills/job-search/        SKILL.md: instructions for AI agents (e.g. Claude Code)
tests/                    unit tests (no network; OpenAI is stubbed)
```

### Actions = API = CLI = agent tools
Every feature is a function in `actions.py` decorated with `@action("group.verb")`. Its signature becomes the
JSON schema and its docstring the description, so it is automatically:
- an HTTP endpoint: `POST /api/actions/company.create` with a JSON body;
- a CLI command: `.venv/bin/python -m jobtracker company.create '{"name": "Acme"}'`;
- a tool of the in-app assistant (`@action(..., confirm=True)` makes it wait for the user's approval);
- an MCP tool (`process_set_state`…), flagged destructive when `confirm=True` and read-only when listed in
  `READ_ONLY` in `jobtracker/mcp_server.py`.

```bash
.venv/bin/python -m jobtracker help        # list actions
.venv/bin/python -m jobtracker actions     # JSON schemas (e.g. to expose them as MCP tools)
```
External agents: connect over MCP (above) or use the CLI, with `skills/job-search/SKILL.md` as their guide.

### Tests, lint, CI
```bash
.venv/bin/python -m unittest discover -t . -s tests
.venv/bin/pip install ruff && .venv/bin/ruff check .
```
Tests always run on throwaway folders: `tests/__init__.py` configures them before the app is imported and every
test checks it, so they can never touch your data, your output folder or `.env`. They never call OpenAI and
don't need a PDF engine.

**Deployment:** CI publishes `ghcr.io/samguercho/job-search-tracker` on every push to `main`;
[`deploy/home-server/`](deploy/home-server/README.md) runs it with Docker Compose behind Tailscale https, and a
scheduled task pulls each new image (continuous deployment to a home server), with backup and update scripts.

**CI** (`.github/workflows/ci.yml`, GitHub Actions): on every push and pull request, lint (ruff) and tests on
Python 3.11 and 3.13; on `main`, build the Docker image, start it and check `/health` (including the PDF engine),
then publish it to the GitHub Container Registry as `ghcr.io/<owner>/job-search-tracker:latest`.

### Docker image
`Dockerfile`: Python 3.13 slim + LibreOffice (PDF export) + STIX Two Text (pinned release) · non-root user ·
`/data` and `/output` volumes · health check on `/health` · JSON logs. `compose.yaml` runs it locally on the same
`./data` folder as `./run.sh`.

---

## License
[MIT](LICENSE): free to use, modify and share, keeping the copyright notice.
