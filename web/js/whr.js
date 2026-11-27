// The What · How · Result way of writing an achievement (jobtracker/guides/bullets.md): three short fields, each
// with a "?" that shows how to fill it, then AI drafts of the sentence (cv.bullet.compose). Used on the Master CV
// page (per achievement, and for a new one) and in the CV builder's "Create a bullet" for a job's gaps.
import { call } from "./api.js";
import { draftCard, diffLegend } from "./diff.js";
import { attempt, h, mount } from "./ui.js";

const FIELDS = [
  {
    name: "what", label: "What", required: true,
    placeholder: "What did you do? Start with a verb",
    tip: "An action verb + the thing you did. Not your duty, the deed.",
    example: "Redesigned the mobile checkout flow",
  },
  {
    name: "how", label: "How",
    placeholder: "How did you do it well? (by …-ing)",
    tip: "Methods, tools, scale: what made it work. Think “by …-ing”, several steps separated by commas.",
    example: "interviewing 12 customers about where they dropped off, A/B testing 4 payment-page layouts",
  },
  {
    name: "result", label: "Result",
    placeholder: "What did it achieve? A number, or the goal",
    tip: "A measurable result (%, €, time saved, volume, users). No number? The goal it served, e.g. “so support could answer without escalating”.",
    example: "cut cart abandonment by 18%",
  },
];
const SENTENCE = "Redesigned the mobile checkout flow by interviewing 12 customers about where they dropped off and A/B testing 4 payment-page layouts, cutting cart abandonment by 18%.";

/** The three fields. read() -> {what, how, result}; insert(text) types into the last focused field. */
export function whrFields(parts = {}, { keywords = [] } = {}) {
  let focused = null;
  const inputs = {};
  const el = h("div", { class: "stack-tight" },
    FIELDS.map((f) => {
      const tip = h("div", { class: "whr-tip", hidden: true },
        f.tip, h("br"), h("b", {}, "e.g. "), `“${f.example}”`,
        f.name === "result" && [h("br"), h("b", {}, "Whole sentence: "), SENTENCE]);
      const input = inputs[f.name] = h("textarea", {
        name: f.name, rows: 2, placeholder: f.placeholder, "aria-label": f.label, onfocus: () => (focused = input),
      }, parts?.[f.name] || "");
      return h("div", { class: "whr-field" },
        h("label", {}, f.label, f.required ? "" : h("span", { class: "small muted", style: "font-weight:400" }, "(optional)"),
          h("button", {
            type: "button", class: "whr-help", "aria-label": `How to fill ${f.label}`, "aria-expanded": "false",
            onclick: (e) => { tip.hidden = !tip.hidden; e.target.setAttribute("aria-expanded", String(!tip.hidden)); },
          }, "?")),
        tip, input);
    }),
    keywords.length > 0 && h("div", { class: "whr-chips row small" },
      h("span", { class: "muted" }, "The job's words (tap to add where true):"),
      keywords.map((k) => h("button", {
        type: "button", class: "chip on", onclick: () => {
          const t = focused || inputs.how;
          t.value = `${t.value.trimEnd()}${t.value.trim() ? ", " : ""}${k}`;
          t.focus();
        },
      }, `+ ${k}`))));
  const read = () => Object.fromEntries(FIELDS.map((f) => [f.name, inputs[f.name].value.trim()]));
  return { el, read };
}

/**
 * Fields + "Write it" + drafts.
 *   parts, lead, keywords, ownerId (or a function returning it): what to start from
 *   original: the current text (drafts then show what changes), or "" for a new achievement
 *   actions(parts) -> [{label, primary?, onclick(draft)}] for each draft
 */
export function whrPanel({ parts = null, lead = "", keywords = [], ownerId = "", original = "", actions, onClose, intro }) {
  const fields = whrFields(parts || {}, { keywords });
  const out = h("div", { class: "stack-tight" });
  const go = h("button", {
    class: "primary small", onclick: async () => {
      const p = fields.read();
      if (!p.what) return fields.el.querySelector("[name=what]").focus();
      go.disabled = true;
      go.textContent = "Writing…";
      const owner = typeof ownerId === "function" ? ownerId() : ownerId;
      const r = await attempt(() => call("cv.bullet.compose", { ...p, lead, keywords, owner_id: owner }));
      go.disabled = false;
      go.textContent = "Write again";
      if (!r) return;
      mount(out,
        r.drafts.length > 0 && diffLegend(),
        r.drafts.map((d) => draftCard(original, d, { keywords, actions: actions(r.parts) })),
        !r.drafts.length && h("p", { class: "small muted" }, "No usable draft: add a detail to How or Result, then write again."),
        r.rejected.length > 0 && h("details", { class: "small muted" }, h("summary", {}, `${r.rejected.length} draft(s) rejected automatically`),
          r.rejected.map((x) => h("div", {}, `“${x.text}”: ${x.reason}`))));
    },
  }, "✍ Write it");
  return h("div", { class: "whr" },
    h("div", { class: "small muted" }, intro || "Fill what you did, how, and what it achieved: the AI turns it into one strong sentence, using only your words and numbers. Tap ? for an example."),
    fields.el,
    h("div", { class: "row" }, go, onClose && h("button", { class: "ghost small", onclick: onClose }, "Close")),
    out);
}
