// Costs: what the AI features cost, from the usage each provider reported × list prices (estimates; the
// providers' bills are the reference). Codex preps run on the ChatGPT plan: $0 here.
import { call } from "../api.js";
import { h, mount, usd } from "../ui.js";

const FEATURES = {
  "chat.send": "Assistant messages", "chat.resolve": "Assistant (after an approval)", "cv.import.parse": "CV import",
  "cv.keywords.match_job": "Job description matching", "workflow.new_application": "New application", "cv.summary.review": "CV summary review",
  "cv.variant.draft": "Wording drafts", "recording.transcribe": "Recording transcription", "recording.analyze": "Recording debrief",
  "recording.reanalyze": "Recording debrief (again)", "prep.start": "Interview prep (Codex)", "other": "Other",
};
const PROVIDERS = { openai: "OpenAI (API credits)", anthropic: "Anthropic (Claude fallback)", codex: "Codex (ChatGPT plan)" };
const SVG = "http://www.w3.org/2000/svg";

export async function renderCosts(root) {
  const [r, averages] = await Promise.all([call("usage.report", { days: 30 }), call("usage.averages")]);
  const tile = (label, value, note) => h("div", { class: "kpi" },
    h("div", { class: "kpi-label" }, label), h("div", { class: "kpi-value" }, value), note && h("div", { class: "kpi-note" }, note));
  const table = (head, rows) => h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("thead", {}, h("tr", {}, head.map((x) => h("th", {}, x)))), h("tbody", {}, rows)));
  const usage = (g) => [g.input_tokens || g.output_tokens ? `${(g.input_tokens / 1000).toFixed(1)}k in · ${(g.output_tokens / 1000).toFixed(1)}k out` : "",
    g.seconds ? `${Math.round(g.seconds / 60)} min` : ""].filter(Boolean).join(" · ");

  mount(root,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Costs"),
        h("div", { class: "sub muted" }, `Estimated from the usage each provider reports × list prices (checked ${r.prices_checked_on}). Your bills are the reference: `,
          h("a", { href: "https://platform.openai.com/usage", target: "_blank" }, "OpenAI usage ↗"), " · ",
          h("a", { href: "https://platform.claude.com/usage", target: "_blank" }, "Anthropic usage ↗")))),
    h("div", { class: "kpis" },
      tile("This month", usd(r.this_month_usd), r.unpriced_calls ? `+ ${r.unpriced_calls} calls with no known price` : "API usage"),
      tile("Last month", usd(r.last_month_usd)),
      tile("Interview preps this month", String(r.plan_runs_this_month), "$0: covered by your ChatGPT plan"),
      averages["chat.send"] && tile("Assistant message", `≈ ${usd(averages["chat.send"].average_usd)}`, `average over ${averages["chat.send"].uses}`)),
    h("div", { class: "card" }, h("h2", {}, "Last 30 days"), dailyChart(r.per_day)),
    h("div", { class: "grid cols-2" },
      h("div", { class: "card" }, h("h2", {}, "This month by feature"),
        r.by_feature.length ? table(["Feature", "Calls", "Usage", "Cost"], r.by_feature.map((g) => h("tr", {},
          h("td", {}, FEATURES[g.name] || g.name), h("td", {}, g.calls), h("td", { class: "small muted" }, usage(g)), h("td", {}, usd(g.cost_usd)))))
          : h("p", { class: "muted" }, "No AI usage this month yet.")),
      h("div", { class: "card" }, h("h2", {}, "This month by provider and model"),
        r.by_model.length ? table(["Model", "Calls", "Usage", "Cost"], r.by_model.map((g) => h("tr", {},
          h("td", {}, `${PROVIDERS[g.name.split("/")[0]] || g.name.split("/")[0]} · ${g.name.split("/").slice(1).join("/")}`), h("td", {}, g.calls),
          h("td", { class: "small muted" }, usage(g)), h("td", {}, usd(g.cost_usd)))))
          : h("p", { class: "muted" }, "—"))),
    Object.keys(averages).length > 0 && h("div", { class: "card" }, h("h2", {}, "Typical cost per use"),
      h("p", { class: "small muted" }, "From your own history: what one use of each feature cost on average (one use can make several AI calls)."),
      table(["Feature", "Uses", "Average", "Total"], Object.values(averages).map((a) => h("tr", {},
        h("td", {}, FEATURES[a.feature] || a.feature), h("td", {}, a.uses), h("td", {}, usd(a.average_usd)), h("td", {}, usd(a.total_usd)))))),
    r.people && h("div", { class: "card" }, h("h2", {}, "By person"),
      h("p", { class: "small muted" }, "Everyone's AI usage runs on your keys."),
      table(["Person", "This month", "Last month"], r.people.map((p) => h("tr", {}, h("td", {}, p.user), h("td", {}, usd(p.this_month_usd)), h("td", {}, usd(p.last_month_usd)))))),
    h("div", { class: "card" }, h("h2", {}, "Latest AI calls"),
      r.recent.length ? table(["When", "Feature", "Model", "Usage", "Cost"], r.recent.map((e) => h("tr", {},
        h("td", { class: "small" }, e.at.replace("T", " ").slice(0, 16)), h("td", {}, FEATURES[e.feature] || e.feature || "—"),
        h("td", { class: "small" }, `${e.provider} · ${e.model}`),
        h("td", { class: "small muted" }, usage({ input_tokens: e.input_tokens + e.cached_tokens + e.cache_write_tokens, output_tokens: e.output_tokens, seconds: e.seconds })),
        h("td", {}, e.covered_by_plan ? "$0 (plan)" : usd(e.cost_usd)))))
        : h("p", { class: "muted" }, "Nothing yet.")));
}

// One bar per day, single hue; hover a bar for the day and amount (the tables above hold the same totals).
function dailyChart(days) {
  const W = 720, H = 140, L = 44, B = 20, T = 8;
  const max = Math.max(...days.map((d) => d.cost_usd), 0.01);
  const step = (W - L) / days.length, bar = Math.min(18, step - 2);
  const y = (v) => T + (1 - v / max) * (H - T - B);
  const el = (tag, attrs, ...kids) => {
    const n = document.createElementNS(SVG, tag);
    Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
    kids.forEach((k) => n.append(k));
    return n;
  };
  const title = (text) => { const t = document.createElementNS(SVG, "title"); t.textContent = text; return t; };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "cost-chart", role: "img", "aria-label": "Cost per day, last 30 days" });
  [0, max / 2, max].forEach((v) => {
    svg.append(el("line", { x1: L, x2: W, y1: y(v), y2: y(v), class: "grid" }));
    svg.append(el("text", { x: L - 6, y: y(v) + 4, class: "axis", "text-anchor": "end" }, usd(v)));
  });
  days.forEach((d, i) => {
    const x = L + i * step + (step - bar) / 2;
    const top = y(d.cost_usd), height = Math.max(H - B - top, d.cost_usd > 0 ? 2 : 0);
    const g = el("g", { class: "bar" }, title(`${d.date}: ${usd(d.cost_usd)}`),
      el("rect", { x: L + i * step, y: T, width: step, height: H - T - B, class: "hit" }),
      el("rect", { x, y: H - B - height, width: bar, height, rx: Math.min(4, bar / 2), class: "fill" }));
    svg.append(g);
    if (i % 5 === 0 || i === days.length - 1) svg.append(el("text", { x: x + bar / 2, y: H - 4, class: "axis", "text-anchor": "middle" }, d.date.slice(5)));
  });
  return svg;
}
