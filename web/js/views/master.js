// Master CV editor: profile, experiences + achievements (with alternative wordings), education, skills.
// Bullets and experiences save automatically when a field loses focus.
import { call, fileUrl } from "../api.js";
import { attempt, field, h, joinList, keywordPattern, mount, readForm, splitList } from "../ui.js";
import { openImport } from "./import.js";

const PROFILE_FIELDS = [
  ["name", "Name"], ["title", "Position (shown under your name)", "e.g. Senior Data Scientist · NLP & LLM Systems"],
  ["email", "Email"], ["phone", "Phone"], ["location", "Location"], ["linkedin", "LinkedIn URL"], ["website", "Website"],
];

export async function renderMasterCv(root) {
  let [cv, vocabulary, info] = await Promise.all([call("cv.master.get"), call("cv.keywords.list"), call("app.info")]);
  const reload = async () => {
    [cv, vocabulary] = await Promise.all([call("cv.master.get"), call("cv.keywords.list")]);
    render();
  };

  // ---------------------------------------------------------------- profile
  const profile = () => {
    const p = cv.profile;
    const form = h("form", { class: "form-grid" },
      PROFILE_FIELDS.map(([k, label, placeholder]) =>
        field(label, h("input", { name: k, value: p[k], placeholder: placeholder || "" }))),
      field("Summary", h("textarea", { name: "summary", rows: 3 }, p.summary), "wide"),
      h("div", { class: "row wide" }, h("span", { class: "spacer" }), h("button", { class: "primary", type: "submit" }, "Save profile")));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      await attempt(() => call("cv.profile.update", { fields: readForm(form) }), "Profile saved");
    });
    return h("div", { class: "card" }, h("h2", {}, "Profile"), form);
  };

  // ------------------------------------------------------------ experiences
  const experience = (exp, i) => {
    const header = h("div", { class: "form-grid" },
      ["company", "role", "location", "start", "end"].map((k) => field(k[0].toUpperCase() + k.slice(1), h("input", { name: k, value: exp[k] }))));
    header.addEventListener("change", () =>
      attempt(() => call("cv.experience.update", { id: exp.id, fields: readForm(header) }), "Experience saved"));

    return h("div", { class: "card exp-card" },
      h("div", { class: "row", style: "margin-bottom:10px" },
        h("h2", { style: "margin:0" }, `${exp.role || "Role"} · ${exp.company}`),
        h("span", { class: "spacer" }),
        h("button", { class: "icon", disabled: i === 0, title: "Move up", onclick: () => move(exp.id, -1) }, "↑"),
        h("button", { class: "icon", disabled: i === cv.experiences.length - 1, title: "Move down", onclick: () => move(exp.id, 1) }, "↓"),
        h("button", {
          class: "danger icon", onclick: async () => {
            if (confirm(`Delete ${exp.company} and its ${exp.bullets.length} bullets?`) &&
              await attempt(() => call("cv.experience.delete", { id: exp.id }), "Experience deleted")) reload();
          },
        }, "Delete")),
      header,
      h("div", { class: "small muted", style: "margin:14px 0 4px" }, "Achievements (same sub-heading = grouped together) · keywords: words written in the text (✓ in text, ✗ missing, + known keyword found) · priority (higher = kept longer) · pinned (always included). Extra wordings of an achievement say the same facts with other keywords: a CV uses one of them."),
      exp.bullets.map(bulletRow),
      h("button", { style: "margin-top:8px", onclick: () => addBullet(exp.id) }, "+ Add achievement"));
  };

  // Keywords only count when written in the text: mark each ✓/✗ and suggest known keywords the text contains.
  const keywordStatus = (container) => {
    const status = h("div", { class: "chips small" });
    const draw = () => {
      const form = readForm(container);
      const text = `${form.lead} ${form.text}`;
      const tags = form.keywords;
      const tagged = new Set(tags.map((k) => k.toLowerCase()));
      const suggestions = vocabulary.filter((k) => !tagged.has(k.toLowerCase()) && keywordPattern(k).test(text));
      mount(status,
        tags.map((k) => keywordPattern(k).test(text)
          ? h("span", { class: "chip ok", title: "Written in the text" }, `✓ ${k}`)
          : h("span", { class: "chip bad", title: "Not written in the text: it will never match. Rewrite or remove it." }, `✗ ${k}`)),
        suggestions.map((k) => h("button", {
          class: "chip suggest", title: "In the text: click to add as a keyword",
          onclick: () => {
            container.querySelector("[name=keywords]").value = [...tags, k].join(", ");
            container.dispatchEvent(new Event("change"));
          },
        }, `+ ${k}`)));
    };
    return { status, draw };
  };

  const keywordsField = (keywords, status) => h("div", { class: "stack-tight" },
    h("textarea", { name: "keywords", "data-list": true, "aria-label": "Keywords", placeholder: "keywords written in the text, comma separated" }, joinList(keywords)),
    status);

  // One achievement = main wording (with group, priority, pin) + optional extra wordings.
  const bulletRow = (b) => {
    const row = h("div", { class: "bullet-row" });
    const { status, draw } = keywordStatus(row);
    mount(row,
      h("div", { class: "stack-tight" },
        h("div", { class: "row" },
          h("input", { name: "group", value: b.group, placeholder: "Project sub-heading (optional)", style: "flex:1" }),
          h("input", { name: "lead", value: b.lead, placeholder: "Bold lead, e.g. Copilot: (optional)", style: "flex:1" })),
        h("textarea", { name: "text", "aria-label": "Achievement text" }, b.text)),
      keywordsField(b.keywords, status),
      h("input", { name: "priority", type: "number", value: b.priority, title: "Priority", "aria-label": "Priority" }),
      h("label", { class: "check", title: "Always include" }, h("input", { name: "pinned", type: "checkbox", checked: b.pinned }), "📌"),
      h("button", {
        class: "ghost icon danger", title: "Delete achievement (and its wordings)", onclick: async () => {
          if (confirm("Delete this achievement and all its wordings?") && await attempt(() => call("cv.bullet.delete", { id: b.id }), "Achievement deleted")) reload();
        },
      }, "✕"));
    row.addEventListener("change", () => { draw(); attempt(() => call("cv.bullet.update", { id: b.id, fields: readForm(row) }), "Saved"); });
    row.addEventListener("input", draw);
    draw();

    const drafts = h("div");
    return h("div", { class: "achievement" },
      row,
      b.variants.length > 0 && h("div", { class: "wordings" }, b.variants.map((v, i) => variantRow(v, i + 2))),
      h("div", { class: "row wording-actions" },
        h("button", { class: "ghost small", onclick: () => addVariant(b) }, "+ Add wording"),
        h("button", {
          class: "ghost small", disabled: !info.llm_available, title: info.llm_available ? "" : "Needs OPENAI_API_KEY in .env",
          onclick: () => mount(drafts, draftPanel(b, drafts)),
        }, "✨ Draft wordings with AI"),
        b.variants.length > 0 && h("span", { class: "small muted" }, `${b.variants.length + 1} wordings · one is used per CV`)),
      drafts);
  };

  const variantRow = (v, n) => {
    const row = h("div", { class: "variant-row" });
    const { status, draw } = keywordStatus(row);
    mount(row,
      h("span", { class: "small muted", style: "padding-top:8px" }, `#${n}`),
      h("div", { class: "stack-tight" },
        h("input", { name: "lead", value: v.lead, placeholder: "Bold lead (optional)" }),
        h("textarea", { name: "text", "aria-label": "Wording text" }, v.text)),
      keywordsField(v.keywords, status),
      h("button", {
        class: "ghost icon danger", title: "Delete this wording", onclick: async () => {
          if (confirm("Delete this wording?") && await attempt(() => call("cv.variant.delete", { id: v.id }), "Wording deleted")) reload();
        },
      }, "✕"));
    row.addEventListener("change", () => { draw(); attempt(() => call("cv.variant.update", { id: v.id, fields: readForm(row) }), "Saved"); });
    row.addEventListener("input", draw);
    draw();
    return row;
  };

  const addVariant = async (b) => {
    if (await attempt(() => call("cv.variant.add", { bullet_id: b.id, text: b.text }), "Wording added: edit it to emphasise other keywords")) reload();
  };

  // AI drafts: nothing is saved until "Keep" is clicked.
  const draftPanel = (b, container) => {
    const kw = h("input", { placeholder: "Keywords to work in, comma separated (e.g. LLM, observability)" });
    const facts = h("input", { placeholder: "Optional true details to use (e.g. 'team of 4', 'took 3 months')" });
    const out = h("div", { class: "stack-tight" });
    const go = h("button", {
      class: "primary small", onclick: async () => {
        go.disabled = true;
        go.textContent = "Drafting…";
        const r = await attempt(() => call("cv.variant.draft", { bullet_id: b.id, keywords: splitList(kw.value), facts: facts.value }));
        go.disabled = false;
        go.textContent = "Draft again";
        if (!r) return;
        mount(out,
          r.drafts.map((d) => h("div", { class: "draft" },
            h("div", {}, d.lead && h("b", {}, `${d.lead} `), d.text),
            h("div", { class: "row" },
              h("div", { class: "chips small" }, d.keywords.map((k) => h("span", { class: `chip ${d.covers.includes(k) ? "on" : ""}` }, k))),
              h("span", { class: "spacer" }),
              h("button", {
                class: "small", onclick: async () => {
                  if (await attempt(() => call("cv.variant.add", { bullet_id: b.id, text: d.text, lead: d.lead, keywords: d.keywords }), "Wording kept")) reload();
                },
              }, "Keep")))),
          !r.drafts.length && h("p", { class: "small muted" }, "No usable draft: try other keywords or add true details."),
          r.rejected.length > 0 && h("details", { class: "small muted" }, h("summary", {}, `${r.rejected.length} draft(s) rejected automatically`),
            r.rejected.map((x) => h("div", {}, `“${x.text}” — ${x.reason}`))));
      },
    }, "Draft");
    return h("div", { class: "draft-panel stack-tight" },
      h("div", { class: "small muted" }, "The AI only rewords the facts already written (or the details you add). Drafts adding numbers are rejected automatically. Read before keeping."),
      h("div", { class: "row" }, kw, go, h("button", { class: "ghost small", onclick: () => mount(container) }, "Close")),
      facts, out);
  };

  const addBullet = async (experienceId) => {
    const b = await attempt(() => call("cv.bullet.add", { experience_id: experienceId, text: "New bullet point: describe an achievement with a number." }));
    if (b) await reload();
  };
  const move = async (id, offset) => { if (await attempt(() => call("cv.experience.move", { id, offset }))) reload(); };

  const newExperience = () => {
    const form = h("form", { class: "row" },
      h("input", { name: "company", placeholder: "Company", required: true, style: "max-width:240px" }),
      h("input", { name: "role", placeholder: "Role", style: "max-width:240px" }),
      h("button", { type: "submit" }, "+ Add experience"));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (await attempt(() => call("cv.experience.add", { ...readForm(form), position: 0 }), "Experience added")) reload();
    });
    return h("div", { class: "card" }, form);
  };

  // ------------------------------------------------------- education/skills
  const listEditor = (title, items, columns, blank, saveAction, key) => {
    const rows = h("div", { class: "stack" });
    const draw = () => mount(rows, items.map((item, i) => h("div", { class: "row", "data-row": i },
      columns.map(([k, label, isList]) => h("input", {
        placeholder: label, value: isList ? joinList(item[k]) : item[k],
        style: isList ? "flex:3" : "flex:1",
        oninput: (e) => (item[k] = isList ? splitList(e.target.value) : e.target.value),
      })),
      h("button", { class: "ghost icon danger", onclick: () => { items.splice(i, 1); draw(); } }, "✕"))));
    draw();
    return h("div", { class: "card" },
      h("h2", {}, title), rows,
      h("div", { class: "row", style: "margin-top:12px" },
        h("button", { onclick: () => { items.push(structuredClone(blank)); draw(); } }, "+ Add"),
        h("span", { class: "spacer" }),
        h("button", { class: "primary", onclick: () => attempt(() => call(saveAction, { [key]: items }), `${title} saved`) }, `Save ${title.toLowerCase()}`)));
  };

  const languages = () => {
    const input = h("input", { value: joinList(cv.languages), placeholder: "French (Native), English (Fluent)" });
    return h("div", { class: "card" },
      h("h2", {}, "Languages"),
      h("div", { class: "row" }, input,
        h("button", { class: "primary", onclick: () => attempt(() => call("cv.languages.set", { languages: splitList(input.value) }), "Languages saved") }, "Save")));
  };

  const synonyms = () => {
    const input = h("textarea", { rows: 8, style: "font-family:ui-monospace,monospace;font-size:12px" },
      cv.synonyms.map((g) => g.join(", ")).join("\n"));
    return h("div", { class: "card" },
      h("h2", {}, "Synonyms"),
      h("p", { class: "small muted" }, "One group per line, comma separated. When a job description uses one term of a line, bullets containing any other term of that line are selected (e.g. the ad says \"large language model\", your bullet says \"LLM\")."),
      input,
      h("div", { class: "row", style: "margin-top:8px" }, h("span", { class: "spacer" }),
        h("button", {
          class: "primary", onclick: () => attempt(() => call("cv.synonyms.set", {
            groups: input.value.split("\n").map(splitList).filter((g) => g.length > 1),
          }), "Synonyms saved"),
        }, "Save synonyms")));
  };

  const extraKeywords = () => {
    const input = h("input", { value: joinList(cv.extra_keywords) });
    return h("div", { class: "card" },
      h("h2", {}, "Extra keywords to watch"),
      h("p", { class: "small muted" }, "Words you track in job descriptions even if no bullet uses them yet: they show up as gaps to work on."),
      h("div", { class: "row" }, input,
        h("button", { class: "primary", onclick: () => attempt(() => call("cv.keywords.set_extra", { keywords: splitList(input.value) }), "Keywords saved") }, "Save")));
  };

  // General CV (headhunters, networking): not tailored to a job. Lives outside render() so it survives re-renders.
  const generalResult = h("div");
  const generalBtn = h("button", {
    class: "primary", onclick: async () => {
      generalBtn.disabled = true;
      generalBtn.textContent = "Creating Word + PDF…";
      const r = await attempt(() => call("cv.generate_general"));
      generalBtn.disabled = false;
      generalBtn.textContent = "📄 Create general CV";
      if (!r) return;
      mount(generalResult, h("div", { class: `card result ${r.warnings.length ? "warn" : ""}` },
        h("h3", {}, "General CV created"),
        h("div", { class: "row" },
          r.pdf && h("a", { class: "btn primary", href: fileUrl(r.pdf), target: "_blank" }, "Open PDF"),
          r.docx && h("a", { class: "btn", href: fileUrl(r.docx) }, "Download Word"),
          h("span", { class: "small muted" }, r.pages ? `${r.pages} page` : "")),
        h("p", { class: "small muted" }, `Saved in ${r.folder}`),
        r.removed.length > 0 && h("p", { class: "small" }, `${r.removed.length} lower-priority bullet(s) left out to fit one page. Raise a bullet's priority or pin it to keep it.`),
        r.warnings.map((w) => h("p", { class: "small", style: "color:var(--amber)" }, `⚠ ${w}`))));
    },
  }, "📄 Create general CV");

  function render() {
    const bullets = cv.experiences.reduce((n, e) => n + e.bullets.length, 0);
    mount(root,
      h("div", { class: "page-head" },
        h("div", {}, h("h1", {}, "Master CV"),
          h("div", { class: "sub muted" }, `${cv.experiences.length} experiences · ${bullets} bullet points. Tag each bullet with the keywords it proves.`)),
        h("div", { class: "row" },
          h("button", { onclick: () => openImport(reload) }, "Import from CV file"),
          generalBtn,
          h("a", { class: "btn", href: "#/cv" }, "CV for a specific job"))),
      h("div", { class: "stack" },
        generalResult,
        profile(),
        newExperience(),
        cv.experiences.map(experience),
        h("div", { class: "grid cols-2" },
          listEditor("Education", cv.education,
            [["degree", "Degree"], ["school", "School"], ["location", "Location"], ["start", "Start"], ["end", "End"], ["details", "Details"]],
            { school: "", degree: "", location: "", start: "", end: "", details: "" }, "cv.education.set", "items"),
          listEditor("Skills", cv.skills, [["name", "Group"], ["items", "Items, comma separated", true]],
            { name: "", items: [] }, "cv.skills.set", "groups")),
        h("div", { class: "grid cols-2" }, languages(), extraKeywords()),
        synonyms()));
  }
  render();
}
