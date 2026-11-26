// "Import from CV file" modal: upload -> LLM draft preview -> merge or replace.
import { call, upload } from "../api.js";
import { attempt, chips, confirmSheet, h, modal, mount } from "../ui.js";

export async function openImport(onDone) {
  const info = await call("app.info");
  const body = h("div", { class: "stack" });
  const close = modal("Import from an existing CV", body);
  let draft = null;

  const fileInput = h("input", { type: "file", accept: ".docx,.pdf,.txt,.md" });
  const analyse = h("button", { class: "primary", onclick: run }, "Analyse CV");
  const status = h("div", { class: "small muted" });
  const preview = h("div");

  async function run() {
    const file = fileInput.files[0];
    if (!file) return (status.textContent = "Choose a file first.");
    analyse.disabled = true;
    status.textContent = "Reading the CV with OpenAI… (10–40 s)";
    const r = await attempt(() => upload("/api/cv/import", file));
    analyse.disabled = false;
    status.textContent = "";
    if (!r) return;
    draft = r.draft;
    showDraft(r.stats);
  }

  function showDraft(stats) {
    const p = draft.profile;
    const mode = h("select", { style: "width:auto" },
      h("option", { value: "merge" }, "Merge: add what's new, keep my current master CV"),
      h("option", { value: "replace" }, "Replace: overwrite profile, experiences, projects, education, skills"));
    mount(preview,
      h("div", { class: "card", style: "background:var(--surface-2)" },
        h("h3", {}, `${p.name || "(no name)"} · ${p.title || ""}`),
        h("p", { class: "small muted" }, `${stats.experiences} experiences · ${stats.projects || 0} projects · ${stats.bullets} bullets · ${stats.education} education · ${stats.skills} skills. Keywords were suggested by the AI: you can edit them after import.`),
        draft.experiences.map((e) => h("div", { class: "preview-exp" },
          h("h3", {}, `${e.role} · ${e.company}`, h("span", { class: "small muted" }, `  ${[e.start, e.end].filter(Boolean).join(" – ")}`)),
          e.bullets.map((b) => h("div", { class: "bullet" }, b.group && h("div", { class: "small muted" }, b.group),
            b.lead && h("b", {}, `${b.lead} `), b.text, chips(b.keywords, "on"))))),
        (draft.projects || []).map((pr) => h("div", { class: "preview-exp" },
          h("h3", {}, `Project · ${pr.name}${pr.role ? ` (${pr.role})` : ""}`, h("span", { class: "small muted" }, `  ${[pr.start, pr.end].filter(Boolean).join(" – ")}`)),
          pr.bullets.map((b) => h("div", { class: "bullet" }, b.group && h("div", { class: "small muted" }, b.group),
            b.lead && h("b", {}, `${b.lead} `), b.text, chips(b.keywords, "on"))))),
        draft.education.length > 0 && h("p", { class: "small" }, h("b", {}, "Education: "), draft.education.map((e) => `${e.degree} (${e.school})`).join("; ")),
        draft.skills.map((g) => h("p", { class: "small" }, h("b", {}, `${g.name}: `), g.items.join(", "))),
        draft.languages.length > 0 && h("p", { class: "small" }, h("b", {}, "Languages: "), draft.languages.join(" | "))),
      h("div", { class: "row" }, mode, h("span", { class: "spacer" }),
        h("button", {
          class: "primary", onclick: async () => {
            if (mode.value === "replace" && !(await confirmSheet("Your current master CV is replaced by this import.",
              { title: "Replace your master CV?", ok: "Replace", danger: true }))) return;
            if (await attempt(() => call("cv.import.apply", { draft, mode: mode.value }), "CV imported")) { close(); onDone(); }
          },
        }, "Apply import")));
  }

  mount(body,
    !info.llm_available && h("div", { class: "card result warn" },
      h("b", {}, "OpenAI key missing. "),
      "Copy ", h("code", {}, ".env.example"), " to ", h("code", {}, ".env"), ", set ", h("code", {}, "OPENAI_API_KEY"), ", then restart the server."),
    h("p", { class: "muted", style: "margin:0" }, "Upload your current CV (.docx or .pdf). It is structured into profile, experiences, projects, bullet points with keywords, education and skills. You review the result before anything is saved."),
    h("div", { class: "row" }, fileInput, analyse), status, preview);
}
