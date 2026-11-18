// Analytics: headline numbers, funnel, where applications stop, weekly activity, breakdowns.
// Single-series charts in one hue (no legend needed); every value is also in a table.
import { call } from "../api.js";
import { formatDate, h, mount } from "../ui.js";

export async function renderAnalytics(root) {
  const a = await call("analytics.summary");
  const t = a.totals, r = a.rates, time = a.timing;
  const pct = (v) => (v === null || v === undefined ? "—" : `${Math.round(v)}%`);
  const days = (v) => (v === null || v === undefined ? "—" : `${v} d`);

  const tile = (label, value, note) => h("div", { class: "kpi" },
    h("div", { class: "kpi-label" }, label), h("div", { class: "kpi-value" }, value), note && h("div", { class: "kpi-note" }, note));

  mount(root,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Analytics"),
        h("div", { class: "sub muted" }, time.search_started
          ? `Search started ${formatDate(time.search_started)} · ${t.applied} applications sent · ${t.not_applied} not sent yet`
          : "Mark “Applied” on your applications to start the statistics."))),
    h("div", { class: "kpis" },
      tile("Applications sent", t.applied, `${t.active} still active`),
      tile("Response rate", pct(r.response_rate), `${t.responses} got a response`),
      tile("Interviews", t.interviews, `${t.technical_interviews} technical`),
      tile("Offers", t.offers, r.offer_rate !== null ? `${pct(r.offer_rate)} of applications` : ""),
      tile("Days searching", time.days_searching ?? "—", time.days_to_first_offer !== null ? `first offer after ${time.days_to_first_offer} d` : "no offer yet"),
      tile("Time to a response", days(time.days_to_response.median), basedOn(time.days_to_response.n, "median"))),
    h("div", { class: "grid cols-2" },
      h("div", { class: "card" },
        h("h2", {}, "Funnel"),
        h("p", { class: "small muted" }, "Applications that reached each stage; % is the share that made it from the previous stage."),
        hbars(a.funnel.map((f) => ({ label: f.label, value: f.count, note: f.from_previous !== undefined ? pct(f.from_previous) : "" })))),
      h("div", { class: "card" },
        h("h2", {}, "Where it stops"),
        h("p", { class: "small muted" }, `Last step reached by the ${t.closed} closed application${t.closed === 1 ? "" : "s"}.`),
        a.stopped_at.length ? hbars(a.stopped_at.map((s) => ({ label: s.stage, value: s.count })))
          : h("p", { class: "muted" }, "No closed application yet."))),
    h("div", { class: "grid cols-2" },
      h("div", { class: "card" },
        h("h2", {}, "Applications per week"),
        a.weekly_applications.length ? columns(a.weekly_applications) : h("p", { class: "muted" }, "No dated applications yet.")),
      h("div", { class: "card" },
        h("h2", {}, "Timing"),
        table(["Measure", "Median", "Average", "Based on"], [
          ["Applied → first response", days(time.days_to_response.median), days(time.days_to_response.average), time.days_to_response.n],
          ["Applied → offer", days(time.days_to_offer.median), days(time.days_to_offer.average), time.days_to_offer.n],
          ["Applied → closed", days(time.days_to_close.median), days(time.days_to_close.average), time.days_to_close.n],
        ]),
        h("p", { class: "small muted" }, "Only steps with a date count. Marking a step Done fills in today's date."))),
    h("div", { class: "grid cols-2" },
      breakdown("By source", a.by_source),
      breakdown("By work model", a.by_work_model)),
    h("div", { class: "card" },
      h("h2", {}, "All applications"),
      table(["Company", "Position", "Status", "Stage", "Applied", "Days to response", "Interviews", "Stopped at"],
        a.applications.map((x) => [h("a", { href: `#/company/${x.id}` }, x.name), x.position, x.status, x.stage,
          x.applied_date ? formatDate(x.applied_date) : "—", x.days_to_response ?? "—", x.interviews, x.stopped_at || "—"]))));
}

const basedOn = (n, what) => (n ? `${what} of ${n} application${n === 1 ? "" : "s"}` : "needs dated steps");

// ---------------------------------------------------------------- tooltip
const tip = h("div", { class: "viz-tip", role: "tooltip" });
document.body.append(tip);
function hover(el, value, label) {
  const show = (x, y) => {
    mount(tip, h("b", {}, value), h("span", {}, label));
    tip.style.display = "block";
    tip.style.left = `${Math.min(x + 12, innerWidth - tip.offsetWidth - 8)}px`;
    tip.style.top = `${y - tip.offsetHeight - 10}px`;
  };
  el.tabIndex = 0;
  el.addEventListener("pointermove", (e) => show(e.clientX, e.clientY));
  el.addEventListener("focus", () => { const b = el.getBoundingClientRect(); show(b.left + b.width / 2, b.top); });
  for (const ev of ["pointerleave", "blur"]) el.addEventListener(ev, () => (tip.style.display = "none"));
  return el;
}

// ------------------------------------------------------------------ charts
// Horizontal bars: one hue, value at the tip, the whole row is the hit target.
function hbars(rows) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return h("div", { class: "hbars" }, rows.map((r) => hover(h("div", { class: "hbar-row" },
    h("span", { class: "hbar-label" }, r.label),
    h("span", { class: "hbar-track" },
      h("span", { class: "hbar", style: `width:${(100 * r.value) / max}%` }),
      h("span", { class: "hbar-value" }, String(r.value), r.note && h("span", { class: "muted" }, ` · ${r.note}`)))),
    `${r.value}${r.note ? ` (${r.note})` : ""}`, r.label)));
}

// Columns over time: label only the peak; tooltip carries each week.
function columns(weeks) {
  const max = Math.max(1, ...weeks.map((w) => w.count));
  const peak = weeks.findIndex((w) => w.count === max);
  return h("div", { class: "cols" },
    h("div", { class: "cols-plot" }, weeks.map((w, i) => hover(h("div", { class: "col-slot" },
      i === peak && h("span", { class: "col-value" }, String(w.count)),
      h("span", { class: "col", style: `height:${(100 * w.count) / max}%` })), `${w.count} application${w.count === 1 ? "" : "s"}`, weekLabel(w.week)))),
    h("div", { class: "cols-axis" }, weeks.map((w, i) => h("span", {}, i === 0 || i === weeks.length - 1 ? weekLabel(w.week) : ""))));
}

const weekLabel = (iso) => { const [y, w] = iso.split("-W"); return `W${Number(w)} ${y}`; };

function breakdown(title, rows) {
  return h("div", { class: "card" }, h("h2", {}, title),
    rows.length ? table(["", "Applied", "Responses", "Interviews", "Offers", "Response rate"],
      rows.map((x) => [x.group, x.applied, x.responses, x.interviews, x.offers, x.response_rate === null ? "—" : `${Math.round(x.response_rate)}%`]))
      : h("p", { class: "muted" }, "No application sent yet."),
    h("p", { class: "small muted" }, "Fill “Source” and “Work model” on each application to compare them."));
}

function table(headers, rows) {
  return h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("thead", {}, h("tr", {}, headers.map((x) => h("th", {}, x)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c, i) => h("td", { class: i && typeof c === "number" ? "num" : "" }, c)))))));
}
