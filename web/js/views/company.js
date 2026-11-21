// Company page: details, follow-up counter, process graph, generated CVs.
import { call, fileUrl } from "../api.js";
import { attempt, chips, confirmSheet, daysBadge, field, formatDate, h, joinList, mapsUrl, markdown, mount, readForm, toast } from "../ui.js";
import { processCard } from "./process-graph.js";
import { recordingsCard } from "./recordings.js";

export async function renderCompany(root, id) {
  const [company, info, master] = await Promise.all([call("company.get", { id }), call("app.info"), call("cv.master.get")]);
  let prep = await call("prep.get", { company: id });
  let practiceLog = await call("practice.history", { company: id });  // newest first
  let prepLanguage = "English";
  const recordings = recordingsCard(company, info);
  let review = null;   // last AI review of the summary, kept across re-renders

  const save = async (fields, msg = "Saved") => {
    const updated = await attempt(() => call("company.update", { id, fields }), msg);
    if (updated) Object.assign(company, updated);
    return updated;
  };

  // ---------------------------------------------------------------- header
  const header = () => h("div", { class: "page-head" },
    h("div", {},
      h("a", { href: "#/", class: "small" }, "← Follow-up"),
      h("h1", {}, company.name),
      h("div", { class: "sub muted" }, company.position,
        (company.location || company.address) && [" · ", h("a", { href: mapsUrl(company), target: "_blank" }, "📍 ", company.location || company.address)],
        company.work_model && [" · ", company.work_model],
        company.website && [" · ", h("a", { href: company.website, target: "_blank" }, "Website ↗")],
        company.job_url && [" · ", h("a", { href: company.job_url, target: "_blank" }, "Job posting ↗")])),
    h("div", { class: "row" },
      h("a", { class: "btn primary", href: `#/cv?company=${id}` }, "Create tailored CV"),
      h("button", {
        class: "danger", onclick: async () => {
          if (!(await confirmSheet("This cannot be undone.", { title: `Delete ${company.name}?`, ok: "Delete", danger: true }))) return;
          if (await attempt(() => call("company.delete", { id }), "Company deleted")) location.hash = "#/";
        },
      }, "Delete")));

  // -------------------------------------------------------------- follow-up
  const followUp = () => h("div", { class: "card" },
    h("h2", {}, "Follow-up"),
    h("div", { class: "row", style: "gap:16px" },
      daysBadge(company.days_since_update, company.last_update_by),
      h("div", { class: "muted small" }, `Last update ${formatDate(company.last_update)}`),
      h("span", { class: "spacer" }),
      h("button", { onclick: () => logUpdate("me") }, "I followed up today"),
      h("button", { onclick: () => logUpdate("them") }, "They replied today")),
    h("div", { class: "row", style: "margin-top:10px" },
      h("input", { id: "update-note", placeholder: "Optional note added to the log (e.g. 'Sent thank-you email')" })));

  const logUpdate = async (by) => {
    const note = root.querySelector("#update-note").value;
    const updated = await attempt(() => call("company.log_update", { id, by, note }), "Update logged");
    if (updated) { Object.assign(company, updated); render(); }
  };

  // --------------------------------------------------------- process graph
  // The graph keeps its own state (selection, forms); it re-renders the page after each change.
  const process = () => processCard(company, { stepTypes: info.step_types, onChange: async () => { await reload(); render(); } });

  // ---------------------------------------------------------------- details
  const select = (name, options, value) => h("select", { name },
    h("option", { value: "" }, "Unknown"), options.map((o) => h("option", { value: o, selected: o === value }, o)));

  const details = () => {
    const c = company.contact;
    const form = h("form", { class: "form-grid" },
      field("Position", h("input", { name: "position", value: company.position })),
      field("Seniority", h("input", { name: "seniority", value: company.seniority })),
      field("Location", h("input", { name: "location", value: company.location })),
      field("Work model", select("work_model", info.work_models, company.work_model)),
      field("Exact address (for the map)", h("input", { name: "address", value: company.address, placeholder: "Optional" })),
      field("Website", h("input", { name: "website", type: "url", value: company.website })),
      field("Job posting link", h("input", { name: "job_url", type: "url", value: company.job_url })),
      field("Source", select("source_type", info.source_types, company.source_type)),
      field("Source detail", h("input", { name: "source_detail", value: company.source_detail, placeholder: "LinkedIn, Dana from TalentCo…" })),
      field("My salary expectation", h("input", { name: "salary_expectation", value: company.salary_expectation, placeholder: "e.g. 38k ILS/month" })),
      field("Salary posted", h("input", { name: "salary_posted", value: company.salary_posted, placeholder: "Range in the ad, if any" })),
      field("Contact name", h("input", { name: "contact.name", value: c.name })),
      field("Contact role", h("input", { name: "contact.role", value: c.role })),
      field("Contact email", h("input", { name: "contact.email", type: "email", value: c.email })),
      field("Contact phone", h("input", { name: "contact.phone", value: c.phone })),
      field("Contact LinkedIn", h("input", { name: "contact.linkedin", value: c.linkedin })),
      field("Referee", h("input", { name: "referee", value: company.referee })),
      field("Main qualities required (comma separated)", h("input", { name: "qualities", "data-list": true, value: joinList(company.qualities) }), "wide"),
      field("Notes", h("textarea", { name: "notes", rows: 6 }, company.notes), "wide"),
      field("CV keywords (comma separated)", h("input", { name: "keywords", "data-list": true, value: joinList(company.keywords) }), "wide"),
      field("Job description", h("textarea", { name: "job_description", rows: 6 }, company.job_description), "wide"),
      h("div", { class: "row wide" },
        h("button", { type: "button", onclick: extract }, "Match job description with my CV"),
        h("span", { class: "spacer" }), h("button", { type: "submit", class: "primary" }, "Save details")));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (await save(readForm(form), "Details saved")) render();
    });
    return h("div", { class: "card" }, h("h2", {}, "Details"), form);
  };

  async function extract() {
    const text = root.querySelector("[name=job_description]").value;
    if (!text.trim()) return toast("Paste the job description first", true);
    toast("Matching the job description with your CV…");
    const r = await attempt(() => call("cv.keywords.match_job", { text }));
    if (!r) return;
    const found = r.keywords;
    const input = root.querySelector("[name=keywords]");
    const merged = [...new Set([...input.value.split(",").map((s) => s.trim()).filter(Boolean), ...found])];
    input.value = merged.join(", ");
    toast(`${found.length} keywords from your CV match${r.gaps.length ? `; not in your CV: ${r.gaps.map((g) => g.jd_term).join(", ")}` : ""}. Save to keep them.`);
  }

  // -------------------------------------------------------------- CV summary
  // The application's own summary if it has one, else the master CV summary.
  const summaryCard = () => {
    const tailored = !!company.cv_summary;
    const text = h("textarea", { rows: 5 }, company.cv_summary || master.profile.summary);
    const checkBtn = h("button", {
      disabled: !info.llm_available || !company.job_description,
      title: !company.job_description ? "Add the job description first" : !info.llm_available ? "Needs OPENAI_API_KEY" : "",
      onclick: async () => {
        checkBtn.disabled = true;
        checkBtn.textContent = "Checking…";
        review = await attempt(() => call("cv.summary.review", { company: id, summary: text.value }));
        render();
      },
    }, "✨ Check relevance with AI");
    return h("div", { class: "card" },
      h("div", { class: "row" }, h("h2", { style: "margin:0" }, "CV summary"), h("span", { class: "spacer" }),
        h("span", { class: `pill ${tailored ? "applied" : "not-applied"}` }, tailored ? "Tailored for this application" : "Master CV summary")),
      h("p", { class: "small muted" }, "The paragraph at the top of this application's CV. Saving here changes this application only."),
      text,
      h("div", { class: "row", style: "margin-top:8px" },
        checkBtn,
        h("span", { class: "spacer" }),
        tailored && h("button", { class: "ghost", onclick: () => saveSummary("", "Back to the master summary") }, "Use master summary"),
        h("button", { class: "primary", onclick: () => saveSummary(text.value, "Summary saved for this application") }, "Save for this application")),
      review && reviewPanel(review, text));
  };

  const saveSummary = async (value, msg) => {
    if (await attempt(() => call("company.set_cv_summary", { company: id, summary: value }), msg)) {
      await reload();
      render();
      toast("Regenerate the CV to apply it");
    }
  };

  const VERDICT = { relevant: "offer", "partly relevant": "in-process", "not relevant": "rejected" };
  const reviewPanel = (r, text) => h("div", { class: "review stack-tight" },
    h("div", { class: "row" },
      h("span", { class: `pill ${VERDICT[r.verdict]}` }, `${r.verdict} · ${r.score}/100`),
      h("span", { class: "small muted" }, "for the summary as written above"),
      h("span", { class: "spacer" }), h("button", { class: "ghost small", onclick: () => { review = null; render(); } }, "Close")),
    r.strengths.length > 0 && h("div", {}, h("div", { class: "small muted" }, "Already fits"), chips(r.strengths, "ok")),
    r.missing_but_in_cv.length > 0 && h("div", {}, h("div", { class: "small muted" }, "The job wants it, your CV has it, the summary doesn't say it"),
      h("ul", { class: "small" }, r.missing_but_in_cv.map((m) => h("li", { dir: "auto" }, h("b", {}, m.requirement), m.evidence ? ` — ${m.evidence}` : "")))),
    r.gaps.length > 0 && h("div", {}, h("div", { class: "small muted" }, "Not in your CV (don't claim it)"), chips(r.gaps, "bad")),
    r.warnings.map((w) => h("p", { class: "small", style: "color:var(--amber)" }, `⚠ ${w}`)),
    r.suggestion && h("div", {},
      h("div", { class: "small muted" }, "Suggested summary (only facts from your CV)"),
      h("blockquote", {}, r.suggestion),
      h("p", { class: "small muted" }, r.explanation),
      h("button", { onclick: () => { text.value = r.suggestion; text.focus(); toast("Suggestion copied above: edit it if needed, then save"); } }, "Use this suggestion")));

  // ------------------------------------------------- saved job description
  const jobDescription = () => h("div", { class: "card" },
    h("h2", {}, "Job description"),
    company.job_description_file
      ? h("div", { class: "stack-tight" },
        h("div", { class: "row" },
          h("a", { class: "btn", href: fileUrl(company.job_description_file), target: "_blank" }, "📄 Open saved copy"),
          h("span", { class: "small muted" }, `Saved ${formatDate(company.job_description_saved_at) || ""} in ${company.job_description_file}`)),
        h("p", { class: "small muted", style: "margin:0" }, "Kept even if the company removes the posting. Updated automatically when you change the description (older copies go to archive/)."))
      : h("p", { class: "muted" }, company.job_description ? "Not saved yet." : "Paste the job description in Details: a copy is saved in the company folder."),
    company.job_description && h("details", { style: "margin-top:8px" }, h("summary", { class: "small" }, "Read it here"),
      h("div", { class: "jd-text" }, company.job_description)));

  // ---------------------------------------------------------------------- map
  const map = () => (company.location || company.address) && h("div", { class: "card", style: "padding:0; overflow:hidden" },
    h("iframe", {
      class: "map", referrerpolicy: "no-referrer-when-downgrade", title: "Map",
      // the embedded map shows the address, or the city (Google picks a sensible zoom); the link below
      // searches company + city, which finds the office itself when Google knows it
      src: `https://www.google.com/maps?q=${encodeURIComponent(company.address || company.location)}&output=embed`,
    }),
    h("div", { class: "row", style: "padding:8px 12px" },
      h("span", { class: "small muted" }, company.address || company.location), h("span", { class: "spacer" }),
      h("a", { href: mapsUrl(company), target: "_blank", class: "small" }, "Open in Google Maps ↗")));

  // -------------------------------------------------------------- documents
  const documents = () => h("div", { class: "card" },
    h("h2", {}, "Generated CVs"),
    company.documents.length
      ? company.documents.map((d) => h("div", { class: "row", style: "padding:6px 0; border-top:1px solid var(--border)" },
        h("span", { class: "small muted" }, d.created_at.replace("T", " ")),
        h("span", { class: "spacer" }),
        d.docx && h("a", { class: "btn", href: fileUrl(d.docx) }, "Word"),
        d.pdf && h("a", { class: "btn", href: fileUrl(d.pdf), target: "_blank" }, "PDF")))
      : h("p", { class: "muted" }, "No CV generated yet."));

  // --------------------------------------------------------- interview prep
  // Written by a strong model (Codex on the server for premium accounts, or any AI assistant through MCP).
  const prepCard = () => {
    const p = prep;
    const run = p?.run || (p && p.status !== "done" ? p : null);  // a newer run still running, or failed
    const running = run?.status === "running";
    const start = info.premium && h("button", {
      class: p?.status === "done" ? "" : "primary", disabled: running, onclick: async () => {
        if (p?.status === "done" && !(await confirmSheet("A new version is written; the current one stays available in the version list.",
          { title: "Prepare again?", ok: "Prepare" }))) return;
        const started = await attempt(() => call("prep.start", { company: id, language: prepLanguage }), "Preparation started: it takes a few minutes");
        if (started) { prep = await call("prep.get", { company: id }); render(); poll(); }  // keeps the current prep visible
      },
    }, running ? "Preparing…" : p?.status === "done" ? "Prepare again" : "✨ Prepare interview");
    const language = info.premium && !running && h("select", {
      title: "Language of the prep (e.g. Hebrew for an HR round in Hebrew)", onchange: (e) => { prepLanguage = e.target.value; },
    }, [["English", "English"], ["Hebrew", "Hebrew · עברית"], ["French", "French"]].map(([v, label]) => h("option", { value: v, selected: v === prepLanguage }, label)));
    return h("div", { class: "card prep" },
      h("div", { class: "row" }, h("h2", { style: "margin:0" }, "Interview prep"), h("span", { class: "spacer" }), language, start),
      info.premium && !running && h("p", { class: "small muted", style: "margin:4px 0 0; text-align:right" }, "Covered by your ChatGPT plan: no API cost."),
      !p && h("p", { class: "muted" }, info.premium
        ? "Research on the company, the likely interview process, and exercises with solutions and grading rubrics. Takes a few minutes."
        : "No prep yet. Ask an AI assistant connected to the tracker (MCP) to write one with prep.brief and prep.save."),
      running && h("p", {}, `⏳ Being prepared by ${run.by} since ${run.requested_at.slice(11, 16)}. This takes a few minutes: you can leave this page.`),
      run?.status === "error" && h("div", { class: "result warn card" }, "The last preparation failed: ", run.error),
      p?.status === "done" && prepBody(p));
  };

  // Practice scores per exercise title (from the log, newest first)
  const scores = () => {
    const out = {};
    for (const a of practiceLog) (out[a.exercise] ??= []).push(a.score);
    return out;
  };
  const scoreBadge = (list) => list && h("span", { class: `score ${list[0] >= 7 ? "good" : "low"}`, title: `Scores, newest first: ${list.join(", ")}` },
    `${list[0]}/10${list.length > 1 ? ` · ${list.length} tries` : ""}`);

  const versionPicker = (p) => p.versions.length > 1 && h("label", { class: "small muted row" }, "Version ",
    h("select", {
      onchange: async (e) => { prep = await call("prep.get", { company: id, version: e.target.value }); render(); },
    }, [...p.versions].reverse().map((v, i) => h("option", { value: v.id, selected: v.id === p.id },
      `${(v.finished_at || "").replace("T", " ").slice(0, 16)}${i === 0 ? " (latest)" : ""}`))));

  const section = (title, content, open = false) => h("details", { open }, h("summary", {}, title), h("div", { class: "md" }, content));

  const prepBody = (p) => {
    const r = p.result;
    const byTitle = scores();
    const latest = !p.versions.length || p.id === p.versions[p.versions.length - 1].id;
    const practice = (i, e) => h("a", {
      class: "btn", href: `#/chat?q=${encodeURIComponent(`Let's practice exercise ${i} ("${e.title}") of my ${company.name} interview prep. Give me the exercise, then grade my answer with the rubric.`)}`,
    }, "Practice with the assistant");
    return h("div", { class: "stack-tight" },
      h("div", { class: "row small muted" }, `By ${p.by} · ${(p.finished_at || "").replace("T", " ").slice(0, 16)}`,
        latest && p.file && [" · ", h("a", { href: fileUrl(p.file), target: "_blank" }, "📄 Interview_Prep.md")],
        h("span", { class: "spacer" }), versionPicker(p)),
      section("The company", markdown(r.company_summary), true),
      section("Likely interview process", markdown(r.interview_process)),
      section("Focus topics", h("ul", {}, r.focus_topics.map((t) => h("li", { dir: "auto" }, t)))),
      section("Your angle", markdown(r.candidate_angle)),
      h("h3", {}, `Exercises (${r.exercises.length})`),
      r.exercises.map((e, n) => h("details", { class: "exercise" },
        h("summary", {}, h("b", {}, `${n + 1}. ${e.title}`), h("span", { class: "small muted" }, ` · ${e.kind} · ${e.difficulty} `),
          scoreBadge(byTitle[e.title])),
        h("div", { class: "md" },
          h("p", { class: "small muted" }, h("i", {}, "Why they'd ask it: "), e.why),
          markdown(e.prompt),
          h("div", { class: "row" }, practice(n + 1, e)),
          h("details", {}, h("summary", { class: "small" }, "Solution, rubric and strong answer"),
            markdown(e.solution),
            h("p", { class: "md-h" }, "Rubric (10 points)"),
            h("ul", {}, e.rubric.map((pt) => h("li", { dir: "auto" }, `${pt.point} (${pt.points})`))),
            h("p", { class: "md-h" }, "Strong answer"), markdown(e.strong_answer),
            h("p", { class: "md-h" }, "Common mistakes"), h("ul", {}, e.common_mistakes.map((m) => h("li", { dir: "auto" }, m))))))),
      practiceLog.length > 0 && section(`Your practice (${practiceLog.length})`, h("ul", {}, practiceLog.slice(0, 15).map((a) => h("li", {},
        h("b", {}, `${a.score}/10`), ` · ${a.exercise} · ${a.at.slice(0, 10)}`,
        a.missed.length > 0 && h("span", { class: "small muted" }, ` · missed: ${a.missed.join("; ")}`))))),
      section("Questions to ask them", h("ul", {}, r.questions_to_ask.map((q) => h("li", { dir: "auto" }, q)))),
      section("Sources", h("ul", {}, r.sources.map((s) => h("li", { dir: "auto" }, /^https?:\/\//.test(s) ? h("a", { href: s, target: "_blank" }, s) : s)))));
  };

  // While a prep is being written, check every 15 s (stops when you leave the page).
  const here = location.hash;
  const poll = () => setTimeout(async () => {
    const isRunning = () => (prep?.run || prep)?.status === "running";
    if (location.hash !== here || !isRunning()) return;
    prep = await call("prep.get", { company: id }).catch(() => prep);
    render();
    if (isRunning()) poll();
  }, 15000);

  const reload = async () => Object.assign(company, await call("company.get", { id }));

  function render() {
    mount(root, header(),
      h("div", { class: "stack" },
        process(),
        prepCard(),
        recordings,
        h("div", { class: "grid cols-main" },
          h("div", { class: "stack" }, summaryCard(), details()),
          h("div", { class: "stack" }, followUp(), jobDescription(), documents(), map()))));
  }
  render();
  if ((prep?.run || prep)?.status === "running") poll();
}
