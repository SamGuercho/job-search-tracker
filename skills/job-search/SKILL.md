---
name: job-search
description: Track job applications and generate tailored one-page CVs with the local Job Search Tracker. Use when the user shares a job description or job link, wants to add/update a company in their job search, log a follow-up, record an interview step, or create a CV for a position.
---

# Job Search Tracker

Every operation is a named **action** with a JSON input. Run it either way:

- CLI (from the project root): `.venv/bin/python -m jobtracker <action> '<json>'`
  (or `@file.json`, or `-` to read stdin). Output: `{"ok": true, "result": ...}`.
- HTTP (when the server runs): `POST http://127.0.0.1:8000/api/actions/<action>` with the JSON body.

`.venv/bin/python -m jobtracker actions` (or `GET /api/actions`) prints every action
with its full JSON schema. Prefer it over guessing field names.

## Typical flows

### New job description → tracked company + tailored CV (one call)
1. Read the job ad. Pick out: company name, position, location, website, seniority,
   the 3–6 main qualities required, contact/recruiter if mentioned.
2. Call `workflow.new_application`:
   ```json
   {"name": "Acme", "position": "Senior Data Analyst", "location": "Paris (hybrid)",
    "website": "https://acme.com", "seniority": "Senior",
    "qualities": ["SQL", "Experimentation", "Stakeholder management"],
    "job_description": "<full text of the ad>",
    "keywords": ["A/B Testing"], "status": "Wishlist"}
   ```
   It creates the company (or updates it if the name already exists), extracts the
   known keywords from the description, merges your `keywords`, and writes
   `<Company>/<Name>_CV.docx` (+ `.pdf`) in the output folder.
3. Report to the user: the files, `removed` bullets (cut to fit one page),
   `unmatched_keywords` (requirements no bullet proves: suggest new bullets), and `warnings`.

### Choosing keywords well
- A keyword selects a bullet only if it is literally written in the bullet (plural/hyphen variants OK).
  The point is that the CV ends up containing the job description's words.
- `cv.keywords.list` returns the vocabulary the master CV knows; `cv.keywords.missing` lists tags not in their text.
- If a requirement is in `unmatched_keywords`, suggest rewriting a true bullet to contain that word; never invent.
- `cv.keywords.match_job {"text": <job description>}` is the way to pick keywords: it maps each job
  requirement to the words written in the CV (exact, synonym groups, AI) and lists `gaps`. Pass its
  `keywords` to cv.generate and tell the user about the gaps. `cv.keywords.suggest {"word"}` fixes typos.
- Synonym groups (`cv.synonyms.set`) teach it that two wordings mean the same thing.
- `cv.preview {"keywords": [...]}` is a dry run: check it before `cv.generate`.

### Follow-up and process
- `company.list` → each company has `days_since_update` and `last_update_by`.
  Companies with 14+ days and status Applied/Interviewing need a follow-up.
- `company.log_update {"id", "by": "me"|"them", "note"}` when an email is sent/received.
- Find an application: `application.find {"query": "nimbus"}` (name and/or position).
- Process graph (`company` = id or name, `step` = id or name): `process.get` (outline with ids),
  `process.set_state {"company": "Nimbus", "step": "Applied", "state": "done"}` (todo/planned/done),
  `process.add_step` (with `after`/`before`, both = between), `process.add_branch {"from_step", "join"}`,
  `process.close {"reason"}` / `process.reopen`, `process.update_step`, `process.delete_step`.
- Status (Not applied, Applied, In process, Offer, Rejected) is derived from the graph: never set it.
  A new application is "Not applied" until the user says they applied.

### Growing the master CV
- `cv.master.get` to read it; `cv.bullet.add {"experience_id", "text", "keywords", "priority"}`.
- Only add bullets that reflect the user's real experience. Ask before inventing facts.
- Each bullet is an achievement with optional extra wordings (variants). A CV uses one wording per achievement,
  so to target new keywords add a wording (`cv.variant.draft` then `cv.variant.add`), never a duplicate bullet.
- For a job, pass `required` (from `cv.keywords.match_job`) to cv.generate so must-haves weigh double.
- Import an existing CV: `cv.import.parse {"file_path": "/path/cv.pdf"}` returns a draft (not saved);
  show the user a summary, then `cv.import.apply {"draft": <draft>, "mode": "merge"|"replace"}`.
  Needs OPENAI_API_KEY in `.env`. Default to `merge`; only `replace` if the user asks.

- Read a job link: `job.fetch_url {"url"}` (some sites block it: ask for the pasted text).

- When adding a job also pass job_url, work_model, salary_posted, source_type/source_detail if known;
  ask for the salary expectation instead of guessing. The description is saved to Job_Description.md.
- Search progress: `analytics.summary`.
- CV summary for a job: `cv.summary.review {"company"}`, then (with the user's OK) `company.set_cv_summary`
  for that application only; regenerate the CV after.

## Rules
- Actions with `"confirm": true` in the manifest delete or overwrite data: ask the user before calling them.
- Never invent experience or numbers for the user's CV.
- Dates are `YYYY-MM-DD`. Update actions only change the `fields` you pass.
- Generated files live in `<JOBTRACKER_OUTPUT_DIR>/<Company>/` (see the `folder` field of cv.generate);
  a previous CV there is moved to `archive/`, never overwritten. Data lives in `data/*.json`.
