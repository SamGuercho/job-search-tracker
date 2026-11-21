// Practice: the training profile built from graded answers (what to review, scores by topic, history).
import { call } from "../api.js";
import { attempt, confirmSheet, h, mount, promptSheet } from "../ui.js";

const SVG = "http://www.w3.org/2000/svg";
// Minimal SVG element builder (h() makes HTML elements): attributes, on* listeners, nested children.
function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) if (c !== null && c !== undefined && c !== false) el.append(c);
  return el;
}

export async function renderPractice(root) {
  const [p, log] = await Promise.all([call("practice.profile"), call("practice.history")]);
  const rerender = () => renderPractice(root);
  const t = p.totals;
  const chat = (text) => `#/chat?q=${encodeURIComponent(text)}`;
  const score = (s) => h("span", { class: `score ${s >= 7 ? "good" : "low"}` }, `${s}/10`);
  const table = (...rows) => h("div", { class: "table-wrap" }, h("table", { class: "data" }, ...rows));
  const tile = (label, value, note) => h("div", { class: "kpi" },
    h("div", { class: "kpi-label" }, label), h("div", { class: "kpi-value" }, value ?? "—"), note && h("div", { class: "kpi-note" }, note));

  // ------------------------------------------------ progress per capability
  // Small multiples, one per capability: every answer (grey dot), the average of the last 3 (accent line), the
  // target (labelled hairline). One y-scale 0–10 everywhere, so the cards compare. Hover/focus a dot for details.
  const tip = h("div", { class: "chart-tip", role: "tooltip", hidden: true });
  const showTip = (evt, pt) => {
    tip.replaceChildren(h("b", {}, `${pt.score}/10`), ` · ${pt.exercise}`, h("div", { class: "muted" }, `${pt.at.slice(0, 10)} · average then ${pt.moving}/10`));
    tip.hidden = false;
    const r = evt.target.getBoundingClientRect();
    tip.style.left = `${Math.max(140, Math.min(r.left + r.width / 2, document.documentElement.clientWidth - 140))}px`;
    tip.style.top = `${r.top - 8}px`;
  };
  const hideTip = () => { tip.hidden = true; };

  const chart = (c) => {
    const W = 320, H = 120, L = 22, R = 34, T = 8, B = 18;
    const n = c.points.length;
    const x = (i) => L + (n <= 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (n - 1));
    const y = (v) => T + (1 - v / 10) * (H - T - B);
    const grid = [0, 5, 10].map((v) => [
      svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }),
      svg("text", { x: L - 6, y: y(v) + 4, class: "axis", "text-anchor": "end" }, String(v))]);
    const target = c.target && [
      svg("line", { x1: L, x2: W - R, y1: y(c.target), y2: y(c.target), class: "target" }),
      svg("text", { x: W - R + 4, y: y(c.target) + 4, class: "axis" }, `target ${c.target}`)];
    const line = n > 1 && svg("polyline", { class: "trend", points: c.points.map((pt, i) => `${x(i)},${y(pt.moving)}`).join(" ") });
    const dots = c.points.map((pt, i) => svg("g", {
      class: "pt", tabindex: 0, "aria-label": `${pt.score} out of 10, ${pt.exercise}, ${pt.at.slice(0, 10)}`,
      onpointerenter: (e) => showTip(e, pt), onpointerleave: hideTip, onfocus: (e) => showTip(e, pt), onblur: hideTip,
    }, svg("circle", { cx: x(i), cy: y(pt.score), r: 10, class: "hit" }), svg("circle", { cx: x(i), cy: y(pt.score), r: 4, class: "dot" })));
    const axisNote = svg("text", { x: L, y: H - 3, class: "axis" }, n ? `${n} answer${n > 1 ? "s" : ""}, oldest → newest` : "not practised yet");
    return svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "cap-chart", role: "img", "aria-label": `${c.capability} scores over time` },
      grid, target, line, dots, axisNote);
  };

  const targetPicker = (c) => h("label", { class: "small muted" }, "Target ",
    h("select", {
      onchange: async (e) => {
        const value = Number(e.target.value) || null;
        if (await attempt(() => call("practice.set_target", { capability: c.capability, target: value }), "Target saved")) rerender();
      },
    }, h("option", { value: "" }, "none"), [5, 6, 7, 8, 9, 10].map((v) => h("option", { value: v, selected: c.target === v }, `${v}/10`))));

  // Rename, or merge into an existing capability (e.g. "Stats" into "Statistics")
  const renameButton = (c) => h("button", {
    class: "ghost icon", title: "Rename, or merge into another capability", onclick: async () => {
      const others = p.capabilities.map((x) => x.capability).filter((x) => x !== c.capability && x !== "Unsorted");
      const to = await promptSheet(`To merge it into another capability, type that one's name (suggestions below).`, c.capability,
        { title: `Rename “${c.capability}”`, ok: "Rename", suggestions: others });
      if (!to || to === c.capability) return;
      const into = others.find((x) => x.toLowerCase() === to.toLowerCase());
      if (into && !(await confirmSheet(`Its ${c.attempts} answer${c.attempts === 1 ? "" : "s"} move to “${into}”. This can't be undone.`,
        { title: `Merge “${c.capability}” into “${into}”?`, ok: "Merge", danger: true }))) return;
      if (await attempt(() => call("practice.rename_capability", { capability: c.capability, to }), into ? "Merged" : "Renamed")) rerender();
    },
  }, "✎");

  const capabilityCard = (c) => h("div", { class: "card cap-card" },
    h("div", { class: "row" }, h("h3", { style: "margin:0" }, c.capability), renameButton(c), h("span", { class: "spacer" }), targetPicker(c)),
    h("div", { class: "row cap-stats" },
      h("span", { class: "cap-now" }, c.now !== null ? `${c.now}/10` : "—"),
      h("span", { class: "small muted" }, c.now !== null ? "now (last 3)" : ""),
      c.change !== null && h("span", { class: `small ${c.change > 0 ? "up" : c.change < 0 ? "down" : "muted"}` },
        c.change > 0 ? `▲ +${c.change} since start` : c.change < 0 ? `▼ ${c.change} since start` : "= since start"),
      c.gap !== null && c.gap > 0 && h("span", { class: "small muted" }, `${c.gap} to target`),
      c.gap !== null && c.gap <= 0 && h("span", { class: "small up" }, "✓ target reached")),
    chart(c));

  const unsorted = p.capabilities.find((c) => c.capability === "Unsorted");
  const capabilities = () => p.capabilities.length > 0 && [
    h("div", { class: "row", style: "margin-top:16px" },
      h("h2", { style: "margin:0" }, "Progress by capability"), h("span", { class: "spacer" }),
      p.most_improved && h("span", { class: "small" }, "Most improved: ", h("b", {}, p.most_improved)),
      p.needs_work.length > 0 && h("span", { class: "small" }, " · Needs work: ", h("b", {}, p.needs_work.join(", ")))),
    h("p", { class: "small muted" }, "● each answer   ━ average of the last 3   ─ your target. Hover a dot for details."),
    unsorted && h("div", { class: "result warn card" }, `${unsorted.attempts} answer${unsorted.attempts > 1 ? "s are" : " is"} not sorted into a capability yet. `,
      h("button", { onclick: async () => { if (await attempt(() => call("practice.classify"), "Sorted")) rerender(); } }, "Sort them")),
    h("div", { class: "cap-grid" }, p.capabilities.filter((c) => c.capability !== "Unsorted").map(capabilityCard)),
    tip];

  if (!t.attempts && !p.capabilities.length) {
    return mount(root,
      h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Practice"))),
      h("div", { class: "card empty" },
        h("h2", {}, "No practice yet"),
        h("p", { class: "muted" }, "Open an application's Interview prep and tap “Practice with the assistant”. Each graded answer is recorded here: your scores by topic, and what to review.")));
  }

  mount(root,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Practice"), h("div", { class: "sub muted" }, "Built from the answers the assistant graded.")),
      h("a", { class: "btn primary", href: chat("Quiz me on my weak spots: pick from my practice profile's to-review list.") }, "Quiz me on my weak spots")),
    h("div", { class: "kpis" },
      tile("Answers graded", t.attempts),
      tile("Exercises practised", t.exercises),
      tile("Average score", t.average !== null ? `${t.average}/10` : "—"),
      tile("To review", p.to_review.length, "last score below 7")),
    capabilities(),
    h("div", { class: "grid cols-2" },
      h("div", { class: "card" },
        h("h2", {}, "To review"),
        p.to_review.length
          ? p.to_review.map((e) => h("div", { class: "review-row" },
            h("div", { class: "row" }, score(e.last_score), h("b", {}, e.exercise), h("span", { class: "small muted" }, ` · ${e.company} · ${e.topic || e.kind}`)),
            e.missed.length > 0 && h("div", { class: "small muted" }, `Missed: ${e.missed.join("; ")}`),
            h("a", { class: "small", href: chat(`Let's practice "${e.exercise}" again (${e.company} interview prep). Give me the exercise, then grade my answer with the rubric.`) }, "Practice again →")))
          : h("p", { class: "muted" }, "Nothing to review: every exercise's last score is 7 or more.")),
      h("div", { class: "card" },
        h("h2", {}, "By topic"),
        table(
          h("thead", {}, h("tr", {}, h("th", {}, "Topic"), h("th", {}, "Answers"), h("th", {}, "Average"), h("th", {}, "Last"), h("th", {}, "Trend"))),
          h("tbody", {}, p.topics.map((x) => h("tr", {},
            h("td", {}, x.topic), h("td", {}, x.attempts), h("td", {}, score(x.average)), h("td", {}, `${x.last_score}/10`),
            h("td", {}, x.trend > 0 ? `▲ +${x.trend}` : x.trend < 0 ? `▼ ${x.trend}` : "—"))))))),
    h("div", { class: "card" },
      h("h2", {}, "History"),
      table(
        h("thead", {}, h("tr", {}, h("th", {}, "Date"), h("th", {}, "Exercise"), h("th", {}, "Application"), h("th", {}, "Score"), h("th", {}, "Feedback"))),
        h("tbody", {}, log.slice(0, 50).map((a) => h("tr", {},
          h("td", { class: "small" }, a.at.slice(0, 10)), h("td", {}, a.exercise),
          h("td", {}, h("a", { href: `#/company/${a.company_id}` }, a.company)), h("td", {}, score(a.score)),
          h("td", { class: "small muted" }, a.feedback)))))));
}
