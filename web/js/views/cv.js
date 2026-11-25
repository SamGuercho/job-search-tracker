// CV builder: match a job description (or type keywords), preview, generate.
import { call, fileUrl } from "../api.js";
import { attempt, debounce, field, h, keywordPattern, matchPanel, mount, sendPdfButton, toast } from "../ui.js";

export async function renderCvBuilder(root, params) {
  const [companies, vocabulary, info] = await Promise.all([call("company.list"), call("cv.keywords.list"), call("app.info")]);
  const preselected = companies.find((c) => c.id === params.get("company"));
  const state = {
    companyId: preselected?.id || "",
    companyName: "",
    keywords: [...(preselected?.keywords || [])],
    template: info.templates[0]?.name,
    preview: null,
    suggestions: {},   // typed keyword not in the CV -> closest CV terms
    required: [],      // must-have keywords from the job match (weigh double)
  };

  // -------------------------------------------------------------- keywords
  const chipBox = h("div", { class: "chips", style: "min-height:30px" });
  const isMissing = (k) => state.preview?.unmatched_keywords.some((u) => u.toLowerCase() === k.toLowerCase());
  const drawChips = () => mount(chipBox, state.keywords.length
    ? state.keywords.map((k) => h("span", { class: `chip ${isMissing(k) ? "unknown" : "on"}`, title: isMissing(k) ? "Not written in any bullet of your CV" : "" },
      k,
      isMissing(k) && (state.suggestions[k] || []).map((s) => h("button", {
        class: "fix", title: "Replace with this term from your CV",
        onclick: () => setKeywords(state.keywords.map((x) => (x === k ? s : x))),
      }, `→ ${s}?`)),
      h("button", { "aria-label": `Remove ${k}`, onclick: () => setKeywords(state.keywords.filter((x) => x !== k)) }, "×")))
    : h("span", { class: "muted small" }, "No keyword yet: match a job description above, or type one."));

  const setKeywords = (list) => {
    state.keywords = [...new Map(list.map((k) => [k.toLowerCase(), k])).values()];
    drawChips();
    refreshPreview();
  };

  const kwInput = h("input", {
    placeholder: "e.g. SQL, Leadership…", list: "kw-vocab",
    onkeydown: (e) => {
      if (e.key !== "Enter" && e.key !== ",") return;
      e.preventDefault();
      const words = kwInput.value.split(",").map((s) => s.trim()).filter(Boolean);
      if (words.length) setKeywords([...state.keywords, ...words]);
      kwInput.value = "";
    },
  });
  const jd = h("textarea", { rows: 7, placeholder: "Paste the job description here…" }, preselected?.job_description || "");

  // Job description -> requirements mapped to the wording of your CV, plus gaps.
  const analysisBox = h("div");
  const matchBtn = h("button", { class: "primary", onclick: () => matchJob() }, "Find matching keywords");
  const matchJob = async () => {
    if (!jd.value.trim()) return toast("Paste a job description first", true);
    matchBtn.disabled = true;
    matchBtn.textContent = info.llm_available ? "Reading the job description with AI…" : "Matching…";
    const r = await attempt(() => call("cv.keywords.match_job", { text: jd.value }));
    matchBtn.disabled = false;
    matchBtn.textContent = "Find matching keywords";
    if (!r) return;
    state.required = r.required || [];
    setKeywords([...state.keywords, ...r.keywords]);
    drawAnalysis(r);
  };

  const SOURCE = { exact: "same word", synonym: "synonym", ai: "AI" };
  const drawAnalysis = (r) => mount(analysisBox, h("div", { class: "analysis stack-tight" },
    r.ai_error && h("p", { class: "small", style: "color:var(--amber)" }, `AI step skipped: ${r.ai_error}`),
    !r.ai_used && !r.ai_error && h("p", { class: "small muted" }, "Exact words and synonyms only (add an OpenAI key for smarter matching)."),
    h("div", { class: "small muted" }, `Covered by your CV (${r.matches.length})`),
    r.matches.map((m) => h("div", { class: "req" },
      h("span", { class: "req-term" }, m.must_have ? h("b", {}, m.jd_term) : m.jd_term),
      h("span", { class: "muted" }, "→"),
      h("span", { class: "chips" }, m.keywords.map((k) => h("span", { class: "chip on" }, k))),
      h("span", { class: "small muted" }, `${m.bullets} bullet${m.bullets === 1 ? "" : "s"}${m.in_skills ? " + skills" : ""} · ${SOURCE[m.source]}`))),
    r.gaps.length > 0 && [
      h("div", { class: "small", style: "color:var(--red); margin-top:8px" }, `Not in your CV (${r.gaps.length}): reword a true bullet to include them, or skip`),
      h("div", { class: "chips" }, r.gaps.map((g) => h("span", { class: "chip bad", title: g.must_have ? "Required" : "" }, g.must_have ? `${g.jd_term} (required)` : g.jd_term)))],
  ));

  // --------------------------------------------------------------- preview
  const previewBox = h("div");
  const refreshPreview = debounce(async () => {
    if (!state.keywords.length) { state.preview = null; return drawPreview(); }
    state.preview = await attempt(() => call("cv.preview", { keywords: state.keywords, template: state.template, required: state.required }));
    drawPreview();
    // Typed keywords not in the CV: look up the closest CV terms (typos, other spellings)
    const missing = (state.preview?.unmatched_keywords || []).filter((k) => !(k in state.suggestions));
    await Promise.all(missing.map(async (k) => (state.suggestions[k] = await call("cv.keywords.suggest", { word: k }).catch(() => []))));
    drawChips();
  }, 250);

  const highlight = (text, words) => {
    if (!words.length) return text;
    const sources = [...words].sort((a, b) => b.length - a.length).map((w) => keywordPattern(w).source);
    const re = new RegExp(sources.join("|"), "gi");
    const parts = [];
    let last = 0;
    for (const m of text.matchAll(re)) {
      parts.push(text.slice(last, m.index), h("mark", {}, m[0]));
      last = m.index + m[0].length;
    }
    parts.push(text.slice(last));
    return parts;
  };

  const drawPreview = () => {
    const p = state.preview;
    if (!p) return mount(previewBox, h("p", { class: "muted" }, "Add keywords to see which bullet points will be used."));
    const all = p.experiences.flatMap((e) => e.bullets);
    const count = (f) => all.filter(f).length;
    mount(previewBox,
      h("div", { class: "legend", style: "margin-bottom:10px" },
        h("span", { class: "l-in" }, `brings new keywords (${count((b) => !b.fallback && !b.redundant)})`),
        h("span", { class: "l-rd" }, `only repeats covered keywords (${count((b) => b.redundant)})`),
        h("span", { class: "l-fb" }, `no job keyword, fills the page (${count((b) => b.fallback)})`),
        h("span", { class: "l-cut" }, `cut to fit 1 page (${p.removed.length})`)),
      p.unmatched_keywords.length > 0 && h("p", { class: "small", style: "color:var(--amber)" },
        `Not written in any bullet: ${p.unmatched_keywords.join(", ")}. Rewrite a bullet in the Master CV to include it.`),
      p.experiences.map((e) => h("div", { class: "preview-exp" },
        h("h3", {}, `${e.role} · ${e.company}`),
        e.bullets.map((b) => h("div", { class: `bullet ${b.fallback ? "fallback" : ""} ${b.redundant ? "redundant" : ""}`, title: b.matched.length ? `Matches: ${b.matched.join(", ")}` : "" },
          b.group && h("div", { class: "small", style: "color:var(--accent); font-style:italic" }, b.group),
          b.lead && h("b", {}, `${b.lead} `), highlight(b.text, b.matched), b.pinned ? " 📌" : "",
          (b.matched.length > 0 || b.wordings > 1) && h("div", { class: "small muted" },
            [b.matched.length && `matches: ${b.matched.join(", ")}`,
             b.wordings > 1 && `wording ${b.variant_id ? "variant" : "main"} of ${b.wordings} (best for this job)`].filter(Boolean).join(" · ")))),
        p.removed.filter((b) => b.experience_id === e.id).map((b) => h("div", { class: "bullet cut" }, b.text)))),
    );
  };

  // -------------------------------------------------------------- generate
  const resultBox = h("div");
  const generateBtn = h("button", { class: "primary", style: "width:100%; justify-content:center; padding:11px", onclick: generate }, "Create CV");

  async function generate() {
    if (!state.keywords.length) return toast("Add at least one keyword", true);
    if (!state.companyId && !state.companyName.trim()) return toast("Choose a company or type a name", true);
    generateBtn.disabled = true;
    generateBtn.textContent = "Generating Word + PDF…";
    const r = await attempt(() => call("cv.generate", {
      keywords: state.keywords, template: state.template, required: state.required,
      ...(state.companyId ? { company_id: state.companyId } : { company_name: state.companyName.trim() }),
    }));
    generateBtn.disabled = false;
    generateBtn.textContent = "Create CV";
    if (!r) return;
    mount(resultBox, h("div", { class: `card result ${r.warnings.length ? "warn" : ""}` },
      h("h3", {}, `CV created for ${r.company}`),
      h("div", { class: "row" },
        r.docx && h("a", { class: "btn", href: fileUrl(r.docx) }, "Download Word"),
        r.pdf && h("a", { class: "btn", href: fileUrl(r.pdf), target: "_blank" }, "Open PDF"),
        r.pdf && sendPdfButton(fileUrl(r.pdf)),
        h("span", { class: "small muted" }, r.pages ? `${r.pages} page` : `~${Math.round(r.estimated_pages * 100)}% of a page (estimated)`)),
      h("p", { class: "small muted" }, `Saved in ${r.folder}`),
      r.removed.length > 0 && h("p", { class: "small" }, `${r.removed.length} bullet(s) removed to stay on one page.`),
      matchPanel(r.match),
      r.warnings.map((w) => h("p", { class: "small", style: "color:var(--amber)" }, `⚠ ${w}`))));
    toast("CV generated");
  }

  // ---------------------------------------------------------------- layout
  const companyName = h("input", { placeholder: "Company name (creates the output folder)", style: state.companyId ? "display:none" : "", oninput: (e) => (state.companyName = e.target.value) });
  const companySelect = h("select", {
    onchange: (e) => {
      state.companyId = e.target.value;
      companyName.style.display = state.companyId ? "none" : "";
      const c = companies.find((x) => x.id === state.companyId);
      if (c) {
        jd.value = c.job_description || jd.value;
        if (c.keywords.length) setKeywords([...state.keywords, ...c.keywords]);
      }
    },
  }, h("option", { value: "" }, "Other company (not tracked)…"),
    companies.map((c) => h("option", { value: c.id, selected: c.id === state.companyId }, `${c.name}${c.position ? ` — ${c.position}` : ""}`)));

  mount(root,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "CV builder"), h("div", { class: "sub muted" }, "Keywords select bullet points from your master CV; the result is trimmed to one page.")),
      h("a", { class: "btn", href: "#/cv/master" }, "Edit master CV")),
    h("datalist", { id: "kw-vocab" }, vocabulary.map((k) => h("option", { value: k }))),
    h("div", { class: "grid cols-2" },
      h("div", { class: "stack" },
        h("div", { class: "card stack" },
          h("h2", {}, "1. Target"),
          field("Company", companySelect), companyName,
          info.templates.length > 1 && field("Template", h("select", { onchange: (e) => { state.template = e.target.value; refreshPreview(); } },
            info.templates.map((t) => h("option", { value: t.name }, t.label))))),
        h("div", { class: "card stack" },
          h("h2", {}, "2. Keywords"),
          field("Job description", jd),
          h("div", { class: "row" }, matchBtn,
            h("span", { class: "spacer" }), h("button", { class: "ghost small", onclick: () => setKeywords([]) }, "Clear keywords")),
          analysisBox,
          field("Keywords used for this CV (add more by typing, Enter to add)", kwInput), chipBox),
        h("div", { class: "stack" }, generateBtn, resultBox)),
      h("div", { class: "card" }, h("h2", {}, "Preview"), previewBox)));

  drawChips();
  drawPreview();
  if (state.keywords.length) refreshPreview();
}
