// Master CV editor (name and contacts are the Profile page's): page layout (section order), experiences and projects + achievements (with alternative
// wordings), education, skills. Bullets, experiences and projects save automatically when a field loses focus.
import { call, fileUrl } from "../api.js";
import { attempt, confirmSheet, field, h, joinList, keywordPattern, mount, promptSheet, readForm, sendPdfButton, splitList } from "../ui.js";
import { openImport } from "./import.js";


export async function renderMasterCv(root) {
  const importance = () => call("cv.keywords.importance").catch(() => null);
  let [cv, vocabulary, info, asked, fonts] = await Promise.all([call("cv.master.get"), call("cv.keywords.list"), call("app.info"), importance(),
    call("cv.font.list").catch(() => null)]);
  const reload = async () => {
    [cv, vocabulary, asked] = await Promise.all([call("cv.master.get"), call("cv.keywords.list"), importance()]);
    render();
  };

  // ------------------------------------------------------- titles & summaries
  // Several titles, each with its own summary; each job's CV uses the one closest to the job's title.
  const headlinesCard = () => {
    const save = async (id, changes, msg) => {
      if (await attempt(() => call("cv.headline.update", { id, ...changes }), msg)) reload();
    };
    const row = (v) => {
      const title = h("input", { value: v.title, "aria-label": "Title", dir: "auto" });
      const summary = h("textarea", { rows: 3, "aria-label": "Summary", dir: "auto" }, v.summary);
      const hints = h("input", { value: joinList(v.keywords), placeholder: "Hint words (optional), e.g. machine learning, experimentation" });
      return h("div", { class: `headline-row ${v.default ? "is-default" : ""}` },
        h("div", { class: "row" }, title,
          v.default ? h("span", { class: "pill applied", title: "Used when no title fits a job better" }, "Default")
            : h("button", { class: "ghost small", onclick: () => save(v.id, { make_default: true }, `“${v.title}” is now the default`) }, "Make default"),
          cv.headlines.length > 1 && h("button", {
            class: "ghost icon danger", title: "Delete this title", onclick: async () => {
              if (await confirmSheet("Applications that pinned it go back to the closest title.", { title: `Delete “${v.title}”?`, ok: "Delete", danger: true })
                && await attempt(() => call("cv.headline.delete", { id: v.id }), "Deleted")) reload();
            },
          }, "✕")),
        summary, hints,
        h("div", { class: "row" }, h("span", { class: "spacer" }),
          h("button", { class: "small", onclick: () => save(v.id, { title: title.value, summary: summary.value, keywords: splitList(hints.value) }, "Saved") }, "Save")));
    };
    return h("div", { class: "card" },
      h("div", { class: "row" }, h("span", { class: "spacer" }),
        h("button", {
          onclick: async () => {
            const title = await promptSheet("Its summary can then be written for that title.", "", { title: "Add a title", ok: "Add", placeholder: "e.g. Data Scientist" });
            if (title && await attempt(() => call("cv.headline.add", { title, summary: "" }), "Title added: write its summary")) reload();
          },
        }, "+ Add a title")),
      h("p", { class: "small muted" }, "The line under your name and the paragraph below it, in several versions (Data Scientist, AI Engineer…). "
        + "Each job's CV uses the title closest to the job's title (then the hint words); the default when none fits. "
        + "You can still adjust them for one application on its page."),
      cv.headlines.map(row));
  };

  // ------------------------------------------------------------ page layout
  // A miniature page: the sections as blocks in their order on the CV. Drag a block (mouse or finger) or use its
  // arrows; the order saves on drop. The header (name, title, contacts) always stays on top.
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const SECTIONS = {
    summary: ["Summary", () => (cv.profile.summary ? 1 + Math.ceil(cv.profile.summary.length / 130) : 0),
      () => (cv.profile.summary ? "" : "no summary")],
    skills: ["Skills", () => { const n = cv.skills.reduce((t, g) => t + g.items.length, 0); return n ? 1 + Math.ceil(n / 12) : 0; },
      () => `${cv.skills.reduce((t, g) => t + g.items.length, 0)} skills`],
    experience: ["Professional Experience", () => cv.experiences.reduce((t, e) => t + 1 + e.bullets.length, 0),
      () => `${plural(cv.experiences.length, "role")} · ${plural(cv.experiences.reduce((t, e) => t + e.bullets.length, 0), "achievement")}`],
    projects: ["Projects", () => cv.projects.reduce((t, p) => t + 1 + p.bullets.length, 0),
      () => `${plural(cv.projects.length, "project")} · ${plural(cv.projects.reduce((t, p) => t + p.bullets.length, 0), "achievement")}`],
    education: ["Education", () => cv.education.reduce((t, e) => t + 1 + (e.details ? 1 : 0), 0),
      () => `${cv.education.length} ${cv.education.length === 1 ? "entry" : "entries"}`],
    languages: ["Languages", () => (cv.languages.length ? 1 : 0), () => cv.languages.join(" · ")],
  };

  const layoutCard = () => {
    let order = [...cv.sections];
    const page = h("div", { class: "mini-page", role: "list", "aria-label": "CV sections, top to bottom" });
    const status = h("span", { class: "small muted", role: "status" });

    const save = async (next) => {
      if (next.join() === order.join()) return draw();
      const previous = order;
      order = next;
      draw();
      status.textContent = "Saving…";
      const saved = await attempt(() => call("cv.sections.set", { sections: next }));
      status.textContent = saved ? "Order saved: every new CV follows it." : "";
      order = saved || previous;
      if (saved) { cv.sections = saved; placeSections(); }
      draw();
    };
    const shift = (i, by) => {
      const next = [...order];
      next.splice(i + by, 0, ...next.splice(i, 1));
      save(next);
    };

    // Pointer events cover mouse, pen and touch: the block follows the pointer between its neighbours. Listened on
    // the document, since moving the block in the page drops a pointer capture. A press that doesn't move is a click:
    // it opens that section of the page below.
    const drag = (block) => (e) => {
      if (e.button > 0 || e.target.closest("button")) return;
      e.preventDefault();
      let moved = false;
      const move = (ev) => {
        if (ev.pointerId !== e.pointerId) return;
        if (!moved && Math.hypot(ev.clientX - e.clientX, ev.clientY - e.clientY) < 6) return;
        moved = true;
        block.classList.add("dragging");
        const others = [...page.querySelectorAll(".mini-block")].filter((b) => b !== block);
        const before = others.find((b) => { const r = b.getBoundingClientRect(); return ev.clientY < r.top + r.height / 2; });
        if (before) { if (block.nextElementSibling !== before) page.insertBefore(block, before); }
        else if (page.lastElementChild !== block) page.append(block);
      };
      const drop = (ev) => {
        if (ev.pointerId !== e.pointerId) return;
        for (const [type, fn] of listeners) document.removeEventListener(type, fn);
        block.classList.remove("dragging");
        if (moved) save([...page.querySelectorAll(".mini-block")].map((b) => b.dataset.section));
        else if (ev.type === "pointerup") openSection(block.dataset.section);
      };
      const listeners = [["pointermove", move], ["pointerup", drop], ["pointercancel", drop]];
      for (const [type, fn] of listeners) document.addEventListener(type, fn);
    };

    const blockFor = (name, i) => {
      const [label, lines, detail] = SECTIONS[name];
      const n = lines();
      const block = h("div", {
        class: `mini-block ${n ? "" : "is-empty"}`, role: "listitem", "data-section": name, tabindex: 0,
        "aria-label": `${label}: open this section`,
        title: n ? "Click to edit this section, drag to move it" : "Empty: not printed until it has content. Click to fill it in",
        onkeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openSection(name); } },
      },
        h("div", { class: "mini-grip", "aria-hidden": "true" }, "⋮⋮"),
        h("div", { class: "mini-body" },
          h("div", { class: "mini-title" }, label),
          h("div", { class: "mini-lines", "aria-hidden": "true" },
            Array.from({ length: Math.min(n, 8) }, (_, k) => h("i", { style: `width:${k % 3 === 2 ? 62 : 92}%` }))),
          h("div", { class: "mini-detail" }, n ? detail() : "empty: not printed")),
        h("div", { class: "mini-arrows" },
          h("button", { class: "ghost icon", disabled: i === 0, "aria-label": `Move ${label} up`, onclick: () => shift(i, -1) }, "↑"),
          h("button", { class: "ghost icon", disabled: i === order.length - 1, "aria-label": `Move ${label} down`, onclick: () => shift(i, 1) }, "↓")));
      block.addEventListener("pointerdown", drag(block));
      return block;
    };

    // The header is the Profile page's (name, contacts, links): it opens there.
    const header = h("a", { class: "mini-header", href: "#/profile", title: "Your name, contacts and links: edit them in your Profile" },
      h("div", { class: "mini-name" }, cv.profile.name || "Your name"), h("i"), h("i", { class: "short" }),
      h("div", { class: "mini-detail" }, "Name & contacts: in your Profile ↗"));
    const draw = () => mount(page, order.map(blockFor));
    draw();
    const sheet = h("div", { class: "mini-sheet", style: `--cv-font: "${cv.font}", serif` }, header, page);

    // The font of every CV: only fonts installed where the PDFs are made are offered. Each option shows itself.
    const fontPicker = () => {
      if (!fonts || !fonts.fonts.length) return null;
      const select = h("select", { "aria-label": "Font of your CVs", style: `font-family:"${cv.font}", serif; width:auto` },
        fonts.fonts.map((f) => h("option", { value: f.name, selected: f.name === cv.font, style: `font-family:"${f.name}", ${f.kind}` },
          f.name + (f.name === fonts.default ? " (default)" : ""))));
      select.addEventListener("change", async () => {
        const saved = await attempt(() => call("cv.font.set", { font: select.value }), `Font: ${select.value}. Every new CV uses it`);
        if (saved) cv.font = saved;
        select.value = cv.font;
        select.style.fontFamily = `"${cv.font}", serif`;
        sheet.style.setProperty("--cv-font", `"${cv.font}", serif`);
      });
      return h("label", { class: "row small", style: "gap:8px; margin:4px 0 8px" }, h("span", { class: "muted" }, "Font"), select);
    };
    return h("div", { class: "card layout-card" },
      h("div", { class: "layout-side" },
        h("h2", {}, "Page layout"),
        h("p", { class: "small muted" }, "Your CV's sections in their order on the page. Click a section to edit it below; "
          + "drag it (or use its arrows) to move it: every CV you create follows this order. Empty sections are left out. "
          + "Your name, contacts and links at the top come from your ", h("a", { href: "#/profile" }, "Profile"), "."),
        fontPicker(),
        h("div", { class: "row" },
          h("button", { class: "ghost small", onclick: () => setAll(true) }, "Expand all"),
          h("button", { class: "ghost small", onclick: () => setAll(false) }, "Collapse all")),
        status),
      sheet);
  };

  // ------------------------------------------------- experiences and projects
  // A project is an experience without an employer: same achievements, wordings and keywords.
  const KINDS = {
    experience: {
      fields: [["company", "Company"], ["role", "Role"], ["location", "Location"], ["start", "Start"], ["end", "End"]],
      title: (o) => `${o.role || "Role"} · ${o.company}`, name: (o) => o.company,
      list: () => cv.experiences, action: "cv.experience", saved: "Experience saved", deleted: "Experience deleted",
    },
    project: {
      fields: [["name", "Project name"], ["role", "Role or context"], ["link", "Link"], ["start", "Start"], ["end", "End"]],
      title: (o) => `${o.name}${o.role ? ` · ${o.role}` : ""}`, name: (o) => o.name,
      list: () => cv.projects, action: "cv.project", saved: "Project saved", deleted: "Project deleted",
    },
  };

  const experience = (exp, i) => ownerCard("experience", exp, i);
  const project = (pr, i) => ownerCard("project", pr, i);

  const ownerCard = (kind, exp, i) => {
    const k = KINDS[kind];
    const header = h("div", { class: "form-grid" },
      k.fields.map(([name, label]) => field(label, h("input", {
        name, value: exp[name], ...(name === "link" ? { inputmode: "url", placeholder: "github.com/you/project (optional)" } : {}),
        ...(name === "role" && kind === "project" ? { placeholder: "Personal, Master's thesis, Freelance… (optional)" } : {}),
      }))));
    header.addEventListener("change", () =>
      attempt(() => call(`${k.action}.update`, { id: exp.id, fields: readForm(header) }), k.saved));

    return h("div", { class: "card exp-card" },
      h("div", { class: "row", style: "margin-bottom:10px" },
        h("h2", { style: "margin:0" }, k.title(exp)),
        h("span", { class: "spacer" }),
        h("button", { class: "icon", disabled: i === 0, title: "Move up", onclick: () => move(kind, exp.id, -1) }, "↑"),
        h("button", { class: "icon", disabled: i === k.list().length - 1, title: "Move down", onclick: () => move(kind, exp.id, 1) }, "↓"),
        h("button", {
          class: "danger icon", onclick: async () => {
            if (await confirmSheet(`Its ${exp.bullets.length} achievements are deleted too.`, { title: `Delete ${k.name(exp)}?`, ok: "Delete", danger: true }) &&
              await attempt(() => call(`${k.action}.delete`, { id: exp.id }), k.deleted)) reload();
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
          if (await confirmSheet("All its wordings are deleted too.", { title: "Delete this achievement?", ok: "Delete", danger: true }) && await attempt(() => call("cv.bullet.delete", { id: b.id }), "Achievement deleted")) reload();
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
          if (await confirmSheet("", { title: "Delete this wording?", ok: "Delete", danger: true }) && await attempt(() => call("cv.variant.delete", { id: v.id }), "Wording deleted")) reload();
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
  const move = async (kind, id, offset) => { if (await attempt(() => call(`${KINDS[kind].action}.move`, { id, offset }))) reload(); };

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

  const newProject = () => {
    const form = h("form", { class: "row" },
      h("input", { name: "name", placeholder: "Project name", required: true, style: "max-width:240px" }),
      h("input", { name: "role", placeholder: "Role or context (optional)", style: "max-width:240px" }),
      h("button", { type: "submit" }, "+ Add project"));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (await attempt(() => call("cv.project.add", readForm(form)), "Project added")) reload();
    });
    return h("div", { class: "card" },
      h("p", { class: "small muted", style: "margin-top:0" }, "Important work done outside a company: personal, academic, freelance, open source. "
        + "Their achievements are matched to jobs, reworded and trimmed exactly like experience ones."),
      form);
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
      rows,
      h("div", { class: "row", style: "margin-top:12px" },
        h("button", { onclick: () => { items.push(structuredClone(blank)); draw(); } }, "+ Add"),
        h("span", { class: "spacer" }),
        h("button", { class: "primary", onclick: () => attempt(() => call(saveAction, { [key]: items }), `${title} saved`) }, `Save ${title.toLowerCase()}`)));
  };

  // Education: header fields on one row; details below, one item per line, printed under the degree or on its line.
  const educationEditor = () => {
    const items = cv.education;
    const rows = h("div", { class: "stack" });
    const input = (item, k, label, flex = 1, max = "") => h("input", {
      placeholder: label, value: item[k] || "", style: `flex:${flex}; min-width:0${max ? `; max-width:${max}` : ""}`,
      oninput: (e) => (item[k] = e.target.value),
    });
    const draw = () => mount(rows, items.map((item, i) => h("div", { class: "edu-row stack-tight" },
      h("div", { class: "row" },
        input(item, "degree", "Degree", 2), input(item, "school", "School", 2), input(item, "location", "Location", 1, "160px"),
        input(item, "start", "Start", 1, "90px"), input(item, "end", "End", 1, "90px"),
        h("button", { class: "ghost icon danger", title: "Remove", onclick: () => { items.splice(i, 1); draw(); } }, "✕")),
      h("div", { class: "row", style: "align-items:flex-start" },
        h("textarea", {
          rows: Math.max(2, (item.details || "").split("\n").length), style: "flex:1",
          placeholder: "Details (optional), one per line: Thesis: …, GPA 92/100, Dean's list…",
          oninput: (e) => (item.details = e.target.value),
        }, item.details || ""),
        h("select", {
          "aria-label": "Where the details go", style: "width:auto",
          onchange: (e) => (item.details_inline = e.target.value === "inline"),
        },
          h("option", { value: "below", selected: !item.details_inline }, "Each on its own line"),
          h("option", { value: "inline", selected: !!item.details_inline }, "On the degree's line"))))));
    draw();
    return h("div", { class: "card" },
      h("p", { class: "small muted", style: "margin-top:0" }, "Details: one item per line. Each prints on its own line under the degree, "
        + "or all of them on the degree's line (“Degree, School • GPA 92 • Thesis: …”) when they fit before the dates."),
      rows,
      h("div", { class: "row", style: "margin-top:12px" },
        h("button", { onclick: () => { items.push({ school: "", degree: "", location: "", start: "", end: "", details: "", details_inline: false }); draw(); } }, "+ Add"),
        h("span", { class: "spacer" }),
        h("button", { class: "primary", onclick: () => attempt(() => call("cv.education.set", { items }), "Education saved") }, "Save education")));
  };

  const languages = () => {
    const input = h("input", { value: joinList(cv.languages), placeholder: "French (Native), English (Fluent)" });
    return h("div", { class: "card" },
      h("div", { class: "row" }, input,
        h("button", { class: "primary", onclick: () => attempt(() => call("cv.languages.set", { languages: splitList(input.value) }), "Languages saved") }, "Save")));
  };

  const synonyms = () => {
    const input = h("textarea", { rows: 8, style: "font-family:ui-monospace,monospace;font-size:12px" },
      cv.synonyms.map((g) => g.join(", ")).join("\n"));
    return h("div", { class: "card" },
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
      h("p", { class: "small muted" }, "Words you track in job descriptions even if no bullet uses them yet: they show up as gaps to work on."),
      h("div", { class: "row" }, input,
        h("button", { class: "primary", onclick: () => attempt(() => call("cv.keywords.set_extra", { keywords: splitList(input.value) }), "Keywords saved") }, "Save")));
  };

  // General CV (headhunters, networking): one page, not tailored to a job. Full master CV: every achievement and
  // project, nothing cut, several pages: to proofread the master CV. Outside render() so they survive re-renders.
  const generalResult = h("div");
  const cvButton = (action, label, primary, done, cutNote) => {
    const btn = h("button", {
      class: primary ? "primary" : "", onclick: async () => {
        btn.disabled = true;
        btn.textContent = "Creating Word + PDF…";
        const r = await attempt(() => call(action));
        btn.disabled = false;
        btn.textContent = label;
        if (!r) return;
        mount(generalResult, h("div", { class: `card result ${r.warnings.length ? "warn" : ""}` },
          h("h3", {}, done),
          h("div", { class: "row" },
            r.pdf && h("a", { class: "btn primary", href: fileUrl(r.pdf), target: "_blank" }, "Open PDF"),
            r.pdf && sendPdfButton(fileUrl(r.pdf)),
            r.docx && h("a", { class: "btn", href: fileUrl(r.docx) }, "Download Word"),
            h("span", { class: "small muted" }, r.pages ? plural(r.pages, "page") : "")),
          h("p", { class: "small muted" }, `Saved in ${r.folder}`),
          cutNote && r.removed.length > 0 && h("p", { class: "small" }, `${r.removed.length} lower-priority bullet(s) left out to fit one page. Raise a bullet's priority or pin it to keep it.`),
          r.warnings.map((w) => h("p", { class: "small", style: "color:var(--amber)" }, `⚠ ${w}`))));
      },
    }, label);
    return btn;
  };
  const generalBtn = cvButton("cv.generate_general", "📄 Create general CV", true, "General CV created (one page)", true);
  generalBtn.title = "One page for no job in particular (headhunters, networking): the highest-priority achievements";
  const fullBtn = cvButton("cv.generate_full", "📚 Full master CV", false, "Full master CV created: every achievement, nothing cut", false);
  fullBtn.title = "Every achievement and project, nothing cut, as many pages as it takes: to proofread your master CV";

  // The keywords the user's applications asked for most: the strong ones keep projects' achievements on a CV.
  const askedKeywords = () => {
    if (!asked || !asked.keywords.length) {
      return h("div", { class: "card" }, h("p", { class: "small muted", style: "margin:0" },
        "Nothing yet: once your applications have job descriptions matched (or CVs created), the keywords they ask for most show up here."));
    }
    const chip = (r) => h("span", {
      class: `chip ${r.strong ? "on" : ""} ${r.in_cv ? "" : "bad"}`,
      title: `${r.applications} of ${asked.applications} applications asked for it${r.required ? ` (${r.required} required it)` : ""}`
        + (r.in_cv ? "" : " · not written in your master CV"),
    }, `${r.keyword} · ${r.applications}`);
    return h("div", { class: "card stack-tight" },
      h("p", { class: "small muted", style: "margin:0" }, `Across ${plural(asked.applications, "application")}, how many jobs asked for each keyword. `
        + "Highlighted = strong: a project's achievement that proves one your professional experience doesn't stays on your CVs. "
        + "Red = asked for but written nowhere in your master CV (worth an achievement, if you truly have it)."),
      h("div", { class: "chips" }, asked.keywords.map(chip)));
  };

  // ------------------------------------------------------ collapsible sections
  // Every big section folds; which ones are open is remembered on this device (and survives re-renders).
  const OPEN_KEY = "ezri.masterCv.open";
  const opened = new Set(JSON.parse(localStorage.getItem(OPEN_KEY) || "null") || []);
  const remember = () => localStorage.setItem(OPEN_KEY, JSON.stringify([...opened]));
  const fold = (id, title, meta, ...body) => {
    const el = h("details", { class: "fold", id: `cv-${id}`, open: opened.has(id) },
      h("summary", { class: "fold-head" },
        h("span", { class: "fold-chevron", "aria-hidden": "true" }, "›"),
        h("h2", {}, title),
        meta && h("span", { class: "small muted fold-meta" }, meta)),
      h("div", { class: "fold-body stack" }, body));
    el.addEventListener("toggle", () => { el.open ? opened.add(id) : opened.delete(id); remember(); });
    return el;
  };
  const setAll = (open) => root.querySelectorAll("details.fold").forEach((d) => { d.open = open; });
  const openSection = (name) => {
    const el = root.querySelector(`#cv-${name}`);
    if (!el) return;
    el.open = true;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // The CV's sections, in their order on the page (the layout's order); each opens from its block.
  const sectionFolds = () => ({
    summary: fold("summary", "Titles & summaries", plural(cv.headlines.length, "title"), headlinesCard()),
    skills: fold("skills", "Skills", plural(cv.skills.reduce((t, g) => t + g.items.length, 0), "skill"),
      listEditor("Skills", cv.skills, [["name", "Group"], ["items", "Items, comma separated", true]],
        { name: "", items: [] }, "cv.skills.set", "groups")),
    experience: fold("experience", "Professional Experience", SECTIONS.experience[2](),
      newExperience(), cv.experiences.map(experience)),
    projects: fold("projects", "Projects", SECTIONS.projects[2](), newProject(), cv.projects.map(project)),
    education: fold("education", "Education", `${cv.education.length} ${cv.education.length === 1 ? "entry" : "entries"}`,
      educationEditor()),
    languages: fold("languages", "Languages", cv.languages.join(" · ") || "none yet", languages()),
  });
  const sectionsBox = h("div", { class: "stack" });
  const placeSections = () => cv.sections.forEach((name) => { const el = sectionsBox.querySelector(`#cv-${name}`); if (el) sectionsBox.append(el); });

  function render() {
    mount(sectionsBox, Object.values(sectionFolds()));
    const bullets = [...cv.experiences, ...cv.projects].reduce((n, e) => n + e.bullets.length, 0);
    mount(root,
      h("div", { class: "page-head" },
        h("div", {}, h("h1", {}, "Master CV"),
          h("div", { class: "sub muted" }, `${cv.experiences.length} experiences · ${cv.projects.length} project${cv.projects.length === 1 ? "" : "s"} · ${bullets} bullet points. Tag each bullet with the keywords it proves.`)),
        h("div", { class: "row" },
          h("button", { onclick: () => openImport(reload) }, "Import from CV file"),
          generalBtn,
          fullBtn,
          h("a", { class: "btn", href: "#/cv" }, "CV for a specific job"))),
      h("div", { class: "stack" },
        generalResult,
        layoutCard(),
        sectionsBox,
        fold("asked", "Keywords your applications ask for", asked && asked.keywords.length
          ? `${plural(asked.keywords.filter((r) => r.strong).length, "strong keyword")}` : "", askedKeywords()),
        fold("keywords", "Extra keywords to watch", plural(cv.extra_keywords.length, "keyword"), extraKeywords()),
        fold("synonyms", "Synonyms", plural(cv.synonyms.length, "group"), synonyms())));
    placeSections();
  }
  render();
}
