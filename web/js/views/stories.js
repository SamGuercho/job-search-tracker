// Stories: the user's true stories for "tell me about a time…" questions, in the STAR shape (Situation, Task,
// Action, Result, lesson). Written once, reused in every interview: preps point their story questions here,
// practice grades the telling. The coverage card shows the common questions with no story yet.
import { call } from "../api.js";
import { attempt, chatHref, confirmSheet, field, h, modal, mount } from "../ui.js";

const PARTS = [
  ["situation", "Situation", "The context, in one or two sentences."],
  ["task", "Task", "What was at stake, and what was your responsibility?"],
  ["action", "Action", "What did YOU do? Say “I”, not “we”."],
  ["result", "Result", "What happened? Numbers if you have them."],
  ["lesson", "Lesson", "What you learned, or would do differently (optional)."],
];

export async function renderStories(root) {
  const [bank, cv] = await Promise.all([call("story.list"), call("cv.master.get").catch(() => null)]);
  const rerender = () => renderStories(root);
  const score = (s) => h("span", { class: `score ${s >= 7 ? "good" : "low"}` }, `${s}/10`);
  const write = (competency) => chatHref(competency
    ? `Help me write a story for "${competency}" questions (${bank.coverage.find((r) => r.competency === competency)?.question || ""}). Ask me one question at a time, then save it in my story bank.`
    : "Help me write a new story for my story bank. Ask me one question at a time, then save it.");

  // ------------------------------------------------------------- editor (new story or edit)
  const edit = (story = null) => {
    const s = story || { title: "", competencies: [], achievements: [] };
    const inputs = {};
    inputs.title = h("input", { value: s.title, placeholder: "e.g. Elasticsearch migration at Cybersixgill" });
    for (const [key, , hint] of PARTS) inputs[key] = h("textarea", { rows: key === "action" ? 4 : 2, placeholder: hint }, s[key] || "");
    const picked = new Set(s.competencies);
    const extra = h("input", { placeholder: "Other, comma separated", value: s.competencies.filter((c) => !bank.competencies.includes(c)).join(", ") });
    const competencyPicker = h("div", { class: "chips" }, bank.competencies.map((c) => {
      const paint = () => {
        chip.className = `chip ${picked.has(c) ? "on" : ""}`;
        chip.setAttribute("aria-pressed", String(picked.has(c)));
        chip.textContent = picked.has(c) ? `✓ ${c}` : c;
      };
      const chip = h("button", { type: "button", onclick: () => { picked.has(c) ? picked.delete(c) : picked.add(c); paint(); } });
      paint();
      return chip;
    }));
    const linked = new Set(s.achievements);
    const achievementPicker = cv && cv.experiences.some((e) => e.bullets.length) && h("details", { open: linked.size > 0 },
      h("summary", { class: "small" }, "Achievements of your master CV this story tells"),
      h("div", { class: "stack-tight", style: "max-height:260px;overflow:auto;margin-top:6px" }, cv.experiences.filter((e) => e.bullets.length).map((e) => [
        h("div", { class: "small muted", style: "margin-top:8px" }, h("b", {}, e.company), e.role ? ` · ${e.role}` : ""),
        e.bullets.map((b) => h("label", { class: "row small", style: "align-items:flex-start;gap:8px" },
          h("input", { type: "checkbox", checked: linked.has(b.id), onchange: (ev) => (ev.target.checked ? linked.add(b.id) : linked.delete(b.id)) }),
          h("span", { dir: "auto" }, b.lead ? [h("b", {}, b.lead), " "] : "", b.text)))])));
    const save = async (ev) => {
      ev.preventDefault();
      const body = Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value]));
      body.competencies = [...picked, ...extra.value.split(",").map((x) => x.trim()).filter(Boolean)];
      body.achievements = [...linked];
      const done = await attempt(() => (story ? call("story.update", { story: story.id, ...body }) : call("story.add", body)), story ? "Story saved" : "Story added");
      if (done) { close(); rerender(); }
    };
    const form = h("form", { class: "stack-tight", onsubmit: save },
      field("Title", inputs.title),
      PARTS.map(([key, label]) => field(label, inputs[key])),
      h("div", { class: "small muted", style: "margin-top:10px" }, "Questions it answers"), competencyPicker, extra,
      achievementPicker,
      h("div", { class: "sheet-actions" }, h("button", { type: "button", onclick: () => close() }, "Cancel"),
        h("button", { type: "submit", class: "primary" }, story ? "Save" : "Add story")));
    const close = modal(story ? "Edit story" : "New story", form);
    setTimeout(() => inputs.title.focus(), 50);
  };

  const remove = async (s) => {
    if (!(await confirmSheet(`“${s.title}” leaves your story bank. Its practice scores stay in Practice.`, { title: "Delete this story?", ok: "Delete", danger: true }))) return;
    if (await attempt(() => call("story.delete", { story: s.id }), "Story deleted")) rerender();
  };

  // ------------------------------------------------------------------- coverage
  const coverage = h("div", { class: "card" },
    h("div", { class: "row" }, h("h2", { style: "margin:0" }, "Questions your stories answer"), h("span", { class: "spacer" }),
      h("span", { class: "small muted" }, `${bank.coverage.filter((r) => r.question && r.stories.length).length} of ${bank.competencies.length} covered`)),
    h("p", { class: "small muted" }, "Interviewers ask these most. One story usually answers two or three: aim for 8 to 10 stories that cover them all. Tap a missing one to write it with the assistant."),
    h("div", { class: "chips" }, bank.coverage.map((r) => r.stories.length
      ? h("span", { class: "chip ok", title: `${r.question}\nAnswered by: ${r.stories.join(", ")}` }, `✓ ${r.competency}${r.stories.length > 1 ? ` · ${r.stories.length}` : ""}`)
      : h("a", { class: "chip unknown", href: write(r.competency), title: r.question }, `+ ${r.competency}`))));

  // --------------------------------------------------------------------- a story
  const storyCard = (s) => h("div", { class: "card stack-tight" },
    h("div", { class: "row" }, h("h3", { style: "margin:0", dir: "auto" }, s.title),
      s.practice.last_score !== null && score(s.practice.last_score),
      h("span", { class: "spacer" }),
      h("a", { class: "btn small", href: chatHref(`Practice my story "${s.title}": ask me a question it answers, as an interviewer would, then grade how I tell it and record it.`) }, "Practice"),
      h("button", { class: "ghost small", onclick: () => edit(s) }, "Edit"),
      h("button", { class: "ghost icon", title: "Delete", "aria-label": `Delete ${s.title}`, onclick: () => remove(s) }, "🗑")),
    s.competencies.length > 0 && h("div", { class: "chips" }, s.competencies.map((c) => h("span", { class: "chip on" }, c))),
    h("div", { class: "md" }, PARTS.filter(([key]) => s[key]).map(([key, label]) => h("p", { dir: "auto" }, h("b", {}, `${label}: `), s[key]))),
    s.missing.length > 0 && h("div", { class: "small", style: "color:var(--amber)" },
      `Missing: ${s.missing.map((m) => PARTS.find(([k]) => k === m)[1]).join(", ")}. `,
      h("a", { href: chatHref(`Help me complete my story "${s.title}": it has no ${s.missing.join(", ")} yet. Ask me one question at a time.`) }, "Complete it with the assistant →")),
    s.practice.last_missed.length > 0 && h("div", { class: "small muted" }, `Last practice missed: ${s.practice.last_missed.join("; ")}`),
    s.achievement_texts.length > 0 && h("details", {}, h("summary", { class: "small muted" }, `From your CV (${s.achievement_texts.length})`),
      h("ul", { class: "small" }, s.achievement_texts.map((a) => h("li", { dir: "auto" }, h("span", { class: "muted" }, `${a.experience} · `), a.text)))));

  const head = h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, "Stories"),
      h("div", { class: "sub muted" }, "Your true stories for “Tell me about a time…” questions. Write each one once, then reuse it in every interview.")),
    h("div", { class: "row" }, h("button", { onclick: () => edit() }, "+ New story"),
      h("a", { class: "btn primary", href: write() }, "✨ Write one with the assistant")));

  if (!bank.stories.length) {
    return mount(root, head, coverage,
      h("div", { class: "card empty" },
        h("h2", {}, "No stories yet"),
        h("p", { class: "muted" }, "Behavioral questions have no right answer: the interviewer wants a true story from your past, told well: the situation, what you did, and the result. Start with the project you're most proud of."),
        h("a", { class: "btn primary", href: write("Proud achievement") }, "Write my first story")));
  }
  mount(root, head, coverage, h("div", { class: "grid cols-2", style: "margin-top:16px" }, bank.stories.map(storyCard)));
}
