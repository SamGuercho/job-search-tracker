// Ezri monitor: dashboard, conversations, the trace of one turn (waterfall + metrics), and the review queue of flags.
const root = document.getElementById("root");
const tip = document.getElementById("tooltip");
const USER_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"];
const KIND_COLORS = { turn: "var(--neutral)", llm_call: "var(--series-1)", tool: "var(--series-2)", history: "var(--series-3)" };
const KIND_NAMES = { turn: "Turn", llm_call: "LLM call", tool: "Tool", history: "History" };
let filters = { days: 30, user: "*" };
let users = [];
let timer = null;

// ------------------------------------------------------------------ helpers
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) if (c !== null && c !== undefined && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}
// Replace an element's children; nested arrays are flattened, empty values skipped.
function mount(el, ...children) {
  el.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false)
    .map((c) => (c instanceof Node ? c : document.createTextNode(String(c)))));
}
function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const c of children.flat(Infinity)) if (c) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}
async function get(path, init) {
  const res = await fetch(path, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `${path}: ${res.status}`);
  return body;
}
const send = (method, path, data) => get(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
const ms = (v) => (v === null || v === undefined ? "–" : v >= 10000 ? `${(v / 1000).toFixed(1)} s` : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`);
const usd = (v) => (v === null || v === undefined ? "–" : v === 0 ? "$0" : v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`);
const num = (v) => (v === null || v === undefined ? "–" : Number(v).toLocaleString());
const when = (ns) => new Date(ns / 1e6).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const who = (u) => u || "(local)";
const STATUS = { completed: "good", ok: "good", done: "good", waiting_approval: "warning", pending_approval: "warning",
  running: "warning", max_steps: "warning", max_tokens: "warning", incomplete: "warning", refusal: "critical", error: "critical" };
const STATUS_ICON = { good: "✓", warning: "◐", critical: "✕" };
const chip = (status) => { const s = STATUS[status] || ""; return h("span", { class: `chip ${s}` }, `${STATUS_ICON[s] || ""} ${status || "?"}`.trim()); };
const qs = () => `days=${filters.days}&user=${encodeURIComponent(filters.user)}`;

function showTip(e, content) {
  mount(tip, content);
  tip.hidden = false;
  const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8);
  const y = Math.min(e.clientY + 14, innerHeight - tip.offsetHeight - 8);
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
}
const hideTip = () => { tip.hidden = true; };

function filterBar(onChange) {
  const days = h("select", { "aria-label": "Period", onchange: (e) => { filters.days = Number(e.target.value); onChange(); } },
    [7, 30, 90, 365].map((d) => h("option", { value: d, selected: d === filters.days }, `Last ${d} days`)));
  const user = h("select", { "aria-label": "User", onchange: (e) => { filters.user = e.target.value; onChange(); } },
    h("option", { value: "*" }, "Everyone"), users.map((u) => h("option", { value: u, selected: u === filters.user }, who(u))));
  return h("div", { class: "filters" }, days, user);
}

// ------------------------------------------------------------------ dashboard
async function dashboard() {
  const data = await get(`/api/overview?${qs()}`);
  users = data.users;
  const t = data.totals;
  const L = data.latency;
  const tile = (label, value, sub, cls = "") => h("div", { class: `tile ${cls}` }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), sub && h("div", { class: "sub" }, sub));
  mount(root, 
    h("h1", {}, "Dashboard"),
    h("div", { class: "muted small" }, "Every number is computed from the raw start/end timestamps of each turn's events."),
    filterBar(route),
    !t.turns ? h("div", { class: "card empty" }, "No turns in this period yet. Send a message in the app's chat.") : [
      h("div", { class: "tiles" },
        tile("Turns", num(t.turns), `${num(t.conversations)} conversations`),
        tile("Turn time · p50", ms(L.turn_wall_ms.p50), `p95 ${ms(L.turn_wall_ms.p95)}`),
        tile("First token · p50", ms(L.ttft_ms.p50), `p95 ${ms(L.ttft_ms.p95)}`),
        tile("Cost", usd(t.cost_usd), `${usd(t.cost_usd / t.turns)} per turn`),
        tile("Tokens", num(t.input_tokens + t.output_tokens), `${num(t.output_tokens)} generated`),
        tile("LLM calls", num(t.llm_calls), `${(t.llm_calls / t.turns).toFixed(1)} per turn`),
        tile("Tool calls", num(t.tool_calls), `${num(t.tool_errors)} failed`, t.tool_errors ? "warn" : ""),
        tile("Approvals", `${num(t.approved)} / ${num(t.approved + t.rejected)}`, "approved / answered"),
        tile("Errors", num(t.errors), `${num(t.fallbacks)} switches to Claude`, t.errors ? "bad" : ""),
        tile("Timing checks", t.inconsistent_turns ? `${num(t.inconsistent_turns)} ✕` : "✓ all", "turns whose totals don't match their timestamps", t.inconsistent_turns ? "bad" : ""),
        reviewTile(data.review), feedbackTile(data.review)),
      h("div", { class: "grid2" },
        h("div", { class: "card" }, h("h2", {}, "Turns per day"), perDayChart(data.per_day)),
        h("div", { class: "card" }, h("h2", {}, "Latency"), latencyTable(L),
          h("div", { class: "small muted", style: { marginTop: "8px" } },
            "Tools · wall = time the turn waited for tools (parallel tools counted once). Tools · work = their durations added up. Work above wall means tools ran in parallel."))),
      h("div", { class: "grid2" },
        h("div", { class: "card" }, h("h2", {}, "By person"), table(["Person", "Turns", "Turn p50", "Turn p95", "Tokens out", "Cost"],
          data.by_user.map((u) => [who(u.user), num(u.turns), ms(u.wall.p50), ms(u.wall.p95), num(u.output_tokens), usd(u.cost_usd)]), [1, 2, 3, 4, 5])),
        h("div", { class: "card" }, h("h2", {}, "How turns ended"), table(["Status", "Turns", "Share"],
          Object.entries(data.statuses).sort((a, b) => b[1] - a[1]).map(([s, n]) => [chip(s), num(n), `${Math.round((100 * n) / t.turns)}%`]), [1, 2]))),
      h("div", { class: "card" }, h("h2", {}, "By model"), table(["Model", "Calls", "p50", "p95", "Input", "Cached", "Output", "Cost"],
        data.by_model.map((m) => [m.model, num(m.calls), ms(m.latency.p50), ms(m.latency.p95), num(m.input_tokens), num(m.cached_tokens), num(m.output_tokens), usd(m.cost_usd)]), [1, 2, 3, 4, 5, 6, 7])),
      h("div", { class: "card" }, h("h2", {}, "By tool"), table(["Tool", "Calls", "Failed", "Waited approval", "p50", "p95", "Result tokens p50 (est.)"],
        data.by_tool.map((r) => [r.action, num(r.calls), num(r.errors), num(r.pending), ms(r.latency.p50), ms(r.latency.p95), num(r.result_tokens.p50 && Math.round(r.result_tokens.p50))]), [1, 2, 3, 4, 5, 6])),
    ]);
}

function reviewTile(r) {
  const f = r?.flags || {};
  const todo = (f.open || 0) + (f.in_case || 0);
  return h("a", { class: `tile link ${todo ? "warn" : ""}`, href: "#/review" }, h("div", { class: "label" }, "To review"),
    h("div", { class: "value" }, num(todo)), h("div", { class: "sub" }, `${num(f.suggested || 0)} suggested · ${num(f.fixed || 0)} fixed`));
}
function feedbackTile(r) {
  const fb = r?.feedback || { up: 0, down: 0 };
  return h("div", { class: `tile ${fb.down ? "warn" : ""}` }, h("div", { class: "label" }, "Feedback"),
    h("div", { class: "value" }, `👍 ${num(fb.up)} · 👎 ${num(fb.down)}`), h("div", { class: "sub" }, "replies rated in the chat"));
}

function table(headers, rows, numeric = [], onRow = null) {
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, headers.map((x, i) => h("th", { class: numeric.includes(i) ? "num" : "" }, x)))),
    h("tbody", {}, rows.length ? rows.map((r, j) => h("tr", { class: onRow ? "link" : "", onclick: onRow ? () => onRow(j) : null },
      r.map((c, i) => h("td", { class: numeric.includes(i) ? "num" : "" }, c)))) : h("tr", {}, h("td", { colspan: headers.length, class: "muted" }, "Nothing yet")))));
}

function latencyTable(L) {
  const rows = [["Turn (wall)", L.turn_wall_ms], ["Time to first token", L.ttft_ms], ["One LLM call", L.llm_call_ms],
    ["One tool call", L.tool_ms], ["Tools · wall, per turn", L.tools_wall_per_turn_ms], ["Tools · work, per turn", L.tools_sum_per_turn_ms]];
  return table(["", "n", "p50", "p95", "max"], rows.map(([n, d]) => [n, num(d.n), ms(d.p50), ms(d.p95), ms(d.max)]), [1, 2, 3, 4]);
}

function perDayChart(days) {
  const names = [...new Set(days.flatMap((d) => Object.keys(d.by_user)))].sort();
  const color = (u) => USER_COLORS[users.indexOf(u === "(local)" ? "" : u) % USER_COLORS.length] || USER_COLORS[names.indexOf(u) % USER_COLORS.length];
  const W = 640, H = 220, L = 32, B = 22, T = 8;
  const max = Math.max(1, ...days.map((d) => Object.values(d.by_user).reduce((a, b) => a + b, 0)));
  const step = max <= 5 ? 1 : Math.ceil(max / 4);
  const top = Math.ceil(max / step) * step;
  const y = (v) => T + (H - T - B) * (1 - v / top);
  const bw = (W - L) / days.length;
  const g = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", role: "img", "aria-label": "Turns per day, stacked by person" });
  for (let v = 0; v <= top; v += step) {
    g.append(svg("line", { x1: L, x2: W, y1: y(v), y2: y(v), class: "gridline" }), svg("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end" }, v));
  }
  days.forEach((d, i) => {
    let acc = 0;
    const x = L + i * bw + 1;
    const w = Math.max(1, bw - 2);
    const segs = names.filter((u) => d.by_user[u]);
    segs.forEach((u, k) => {
      const v = d.by_user[u];
      const y0 = y(acc), y1 = y(acc + v);
      acc += v;
      const topSeg = k === segs.length - 1;
      const r = topSeg ? Math.min(4, w / 2) : 0;
      const hgt = y0 - y1 - (k > 0 ? 2 : 0);  // 2px surface gap between stacked segments
      g.append(svg("path", { d: roundedTop(x, y1, w, Math.max(1, hgt), r), fill: color(u) }));
    });
    if ((days.length - 1 - i) % Math.ceil(days.length / 8) === 0) {  // counted from today: the last day always has its label
      const last = i === days.length - 1;
      g.append(svg("text", { x: last ? W : x + w / 2, y: H - 6, "text-anchor": last ? "end" : "middle" }, new Date(d.date).toLocaleDateString(undefined, { day: "numeric", month: "short" })));
    }
    const hit = svg("rect", { x: L + i * bw, y: T, width: bw, height: H - T - B, fill: "transparent" });
    hit.addEventListener("mousemove", (e) => showTip(e, [h("b", {}, new Date(d.date).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })),
      ...(names.length ? names.map((u) => h("div", {}, h("i", { style: { display: "inline-block", width: "8px", height: "8px", background: color(u), marginRight: "6px", borderRadius: "2px" } }), `${u}: ${d.by_user[u] || 0}`)) : [h("div", {}, "0")])]));
    hit.addEventListener("mouseleave", hideTip);
    g.append(hit);
  });
  return [names.length > 1 && h("div", { class: "legend" }, names.map((u) => h("span", {}, h("i", { style: { background: color(u) } }), u))), g];
}
function roundedTop(x, y, w, hgt, r) {
  return `M${x},${y + hgt} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + hgt} Z`;
}

// ------------------------------------------------------------------ conversations
async function conversationList() {
  const [list, data] = await Promise.all([get(`/api/conversations?${qs()}`), users.length ? null : get(`/api/overview?${qs()}`)]);
  if (data) users = data.users;
  mount(root, h("h1", {}, "Conversations"), filterBar(route),
    h("div", { class: "card" }, table(["Conversation", "Person", "Turns", "Tools", "Cost", "Problems", "Last activity"],
      list.map((c) => [h("a", { href: `#/c/${c.conversation_id}` }, c.title), who(c.user_id), num(c.turns), num(c.tool_calls), usd(c.cost_usd),
        c.running ? chip("running") : c.problems ? h("span", { class: "chip critical" }, `✕ ${c.problems}`) : "", when(c.last_ns)]),
      [2, 3, 4], (i) => { location.hash = `#/c/${list[i].conversation_id}`; })));
}

async function conversationPage(id) {
  const c = await get(`/api/conversations/${encodeURIComponent(id)}`);
  mount(root, 
    h("div", { class: "crumbs small" }, h("a", { href: "#/conversations" }, "← Conversations")),
    h("h1", {}, c.title), h("div", { class: "muted small" }, `${who(c.user_id)} · ${c.turns.length} turns`),
    h("div", { class: "card", style: { marginTop: "14px" } }, table(["#", "Message", "Status", "Turn", "First token", "LLM calls", "Tools", "Tokens out", "Cost", "Timing", "Flags"],
      c.turns.map((t) => [t.turn_index, h("span", { title: t.user_text }, (t.user_text || "").slice(0, 90) || h("span", { class: "muted" }, t.trigger)),
        chip(t.status), ms(t.wall_ms), ms(t.ttft_ms), num(t.llm_calls), num(t.tool_calls), num(t.output_tokens), usd(t.cost_usd),
        t.consistent ? h("span", { class: "chip good" }, "✓") : h("span", { class: "chip critical" }, "✕ check"),
        t.flags ? h("span", { class: "chip warning" }, `⚑ ${t.flags}`) : ""]),
      [3, 4, 5, 6, 7, 8], (i) => { location.hash = `#/t/${c.turns[i].turn_id}`; })));
}

// ------------------------------------------------------------------ one turn
let selected = null;

async function turnPage(turnId) {
  const [data] = await Promise.all([get(`/api/turns/${encodeURIComponent(turnId)}`), loadCatalog()]);
  const s = data.summary || {};
  const total = Math.max(1, ...data.spans.map((x) => x.end_ms ?? x.start_ms), data.running ? (Date.now() * 1e6 - data.origin_ns) / 1e6 : 0);
  const byId = Object.fromEntries(data.spans.map((x) => [x.span_id, x]));
  if (!selected || !byId[selected]) selected = data.spans[0]?.span_id;
  const idx = data.turn_indexes.indexOf(data.turn_index);
  const nav = (i) => data.turn_indexes[i] !== undefined && h("a", { href: `#/t/${data.conversation_id}:${data.turn_indexes[i]}` }, i < idx ? "← previous turn" : "next turn →");

  const detail = h("div", { class: "detail" });
  const drawDetail = () => mount(detail, spanDetail(byId[selected], data));
  const flagged = new Set(data.flags.filter((f) => f.span_id && !["dismissed", "archived"].includes(f.status)).map((f) => f.span_id));
  const rows = data.spans.map((x) => waterfallRow(x, byId, total, flagged.has(x.span_id), () => {
    selected = x.span_id;
    for (const r of root.querySelectorAll(".wf-row")) r.classList.toggle("selected", r.dataset.span === selected);
    drawDetail();
  }));
  drawDetail();
  mount(root, 
    h("div", { class: "crumbs small" }, h("a", { href: "#/conversations" }, "Conversations"), "›", h("a", { href: `#/c/${data.conversation_id}` }, data.title), "›", `Turn ${data.turn_index}`,
      h("span", { style: { flex: 1 } }), nav(idx - 1), nav(idx + 1)),
    h("div", { class: "title-row" },
      h("h1", {}, data.running && h("span", { class: "live-dot", title: "Running: updates live" }), `Turn ${data.turn_index} · `, chip(s.status)),
      h("button", { class: "flag-btn", onclick: () => flagDialog({ turn_id: data.turn_id }, "turn", () => route(true)) }, "⚑ Flag this turn")),
    h("div", { class: "muted small" }, `${who(s.user_id)} · ${s.trigger === "approval" ? "after an approval" : "user message"} · ${new Date(data.origin_ns / 1e6).toLocaleString()}`),
    h("div", { class: "turn-layout", style: { marginTop: "14px" } },
      h("div", {},
        h("div", { class: "card" },
          h("div", { class: "legend" }, Object.entries(KIND_NAMES).map(([k, n]) => h("span", {}, h("i", { style: { background: KIND_COLORS[k] } }), n)),
            h("span", {}, h("i", { style: { background: "var(--text)", width: "2px", opacity: ".55" } }), "event (first token, text, tool requested)")),
          h("div", { class: "waterfall" }, axis(total), rows)),
        h("div", { class: "card" }, detail)),
      metricsPanel(data)));
  for (const r of root.querySelectorAll(".wf-row")) r.classList.toggle("selected", r.dataset.span === selected);
  if (data.running) timer = setTimeout(() => route(true), 1500);
}

function spanLabel(x) {
  if (x.kind === "llm_call") return `${x.start.provider} · ${x.start.model} · step ${x.start.step}`;
  if (x.kind === "tool") return x.start.action + (x.start.approved ? " (approved)" : "");
  if (x.kind === "history") return x.start.source;
  if (x.kind === "turn") return x.start.trigger === "approval" ? "approval" : "user message";
  return x.kind;
}

function depth(x, byId) {
  let d = 0, p = byId[x.parent_span_id];
  while (p) { d++; p = byId[p.parent_span_id]; }
  return d;
}

function axis(total) {
  const step = niceStep(total / (innerWidth < 600 ? 2.5 : 6));  // fewer labels on a phone
  const marks = [];
  for (let v = 0; v <= total + 1e-9; v += step) marks.push(v);
  return h("div", { class: "wf-axis" }, h("div", {}),
    h("div", { class: "wf-axis-track" }, h("div", { class: "wf-inner" }, marks.map((v) => h("span", { style: { left: `${(100 * v) / total}%` } }, ms(v))))));
}
function niceStep(raw) {
  const p = 10 ** Math.floor(Math.log10(Math.max(raw, 1e-3)));
  return [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw) || raw;
}

function waterfallRow(x, byId, total, isFlagged, onSelect) {
  const end = x.end_ms ?? total;
  const left = (100 * x.start_ms) / total;
  const width = (100 * (end - x.start_ms)) / total;
  const ticks = (x.events || []).filter((e) => ["llm_call.first_token", "llm.text", "llm.thinking", "tool_call.requested", "approval.requested"].includes(e.type));
  const bar = h("div", { class: `wf-bar ${x.end_ms === null ? "running" : ""} ${x.status === "error" ? "error" : ""}`, style: { left: `${left}%`, width: `${width}%`, background: KIND_COLORS[x.kind] || "var(--neutral)" } });
  const row = h("div", { class: "wf-row", "data-span": x.span_id, onclick: onSelect },
    h("div", { class: "wf-label", style: { paddingLeft: `${depth(x, byId) * 14}px` }, title: spanLabel(x) },
      isFlagged && h("span", { class: "flag-mark", title: "Flagged" }, "⚑ "), h("span", { class: "kind" }, `${KIND_NAMES[x.kind] || x.kind} `), spanLabel(x)),
    h("div", { class: "wf-track" }, h("div", { class: "wf-inner" }, bar,
      ticks.map((e) => h("div", { class: "wf-tick", style: { left: `${(100 * (e.at_ms || 0)) / total}%` } })),
      h("span", { class: "wf-dur", style: { left: `calc(${left + width}% + 6px)` } }, x.end_ms === null ? "running…" : ms(x.duration_ms)))));
  row.addEventListener("mousemove", (e) => showTip(e, [h("b", {}, `${KIND_NAMES[x.kind] || x.kind}: ${spanLabel(x)}`),
    h("div", {}, `starts at ${ms(x.start_ms)} · ${x.end_ms === null ? "running" : `lasts ${ms(x.duration_ms)}`}`),
    x.status && h("div", {}, `status: ${x.status}`),
    x.end?.usage && h("div", {}, `tokens: ${num((x.end.usage.input_tokens || 0) + (x.end.usage.cached_tokens || 0))} in · ${num(x.end.usage.output_tokens)} out`),
    x.end?.result_tokens !== undefined && h("div", {}, `result: ~${num(x.end.result_tokens)} tokens (est.)`)]));
  row.addEventListener("mouseleave", hideTip);
  return row;
}

function spanDetail(x, data) {
  if (!x) return h("div", { class: "muted" }, "Select a row.");
  const evs = data.events.filter((e) => e.span_id === x.span_id && !e.type.endsWith(".started") && !e.type.endsWith(".ended"));
  const json = (v) => h("pre", {}, JSON.stringify(v, null, 2));
  const body = [];
  if (x.kind === "llm_call") {
    const u = x.end.usage || {};
    body.push(h("dl", { class: "kv" },
      h("dt", {}, "Status"), h("dd", {}, chip(x.status)), h("dt", {}, "Stop reason"), h("dd", {}, x.end.stop_reason || "–"),
      h("dt", {}, "Duration"), h("dd", {}, ms(x.duration_ms)),
      h("dt", {}, "First token after"), h("dd", {}, ms(evs.find((e) => e.type === "llm_call.first_token") ? evs.find((e) => e.type === "llm_call.first_token").at_ms - x.start_ms : null)),
      h("dt", {}, "Input (uncached)"), h("dd", {}, num(u.input_tokens)), h("dt", {}, "Input (cached)"), h("dd", {}, num(u.cached_tokens)),
      h("dt", {}, "Written to cache"), h("dd", {}, num(u.cache_write_tokens)), h("dt", {}, "Output"), h("dd", {}, num(u.output_tokens)),
      x.end.reasoning_tokens !== undefined && x.end.reasoning_tokens !== null && [h("dt", {}, "of which reasoning"), h("dd", {}, num(x.end.reasoning_tokens))],
      h("dt", {}, "Cost"), h("dd", {}, usd(x.end.cost_usd)), h("dt", {}, "LLM call id"), h("dd", { class: "small" }, x.llm_call_id)));
    for (const e of evs) {
      if (e.type === "llm.thinking") body.push(h("h2", { style: { marginTop: "14px" } }, `Thinking · ${num(e.payload.chars)} chars, ${e.payload.deltas} chunks`), h("div", { class: "text" }, e.payload.text || "(not shown by the provider)"));
      if (e.type === "llm.text") body.push(h("h2", { style: { marginTop: "14px" } }, `Text · ${num(e.payload.chars)} chars, ~${num(e.payload.tokens_estimate)} tokens, streamed in ${e.payload.deltas} chunks over ${ms((e.payload.last_delta_ns - e.payload.first_delta_ns) / 1e6)}`), h("div", { class: "text", dir: "auto" }, e.payload.text));
      if (e.type === "tool_call.requested") body.push(h("h2", { style: { marginTop: "14px" } }, `Tool requested · ${e.payload.action}`), json(e.payload.args));
    }
  } else if (x.kind === "tool") {
    body.push(h("dl", { class: "kv" },
      h("dt", {}, "Status"), h("dd", {}, chip(x.status)), h("dt", {}, "Duration"), h("dd", {}, ms(x.duration_ms)),
      h("dt", {}, "Result size"), h("dd", {}, `${num(x.end.result_chars)} chars · ~${num(x.end.result_tokens)} tokens`),
      x.end.truncated && [h("dt", {}, "Truncated"), h("dd", {}, "yes (the model saw the start)")]),
      x.end.error && h("div", { class: "chip critical", style: { margin: "10px 0" } }, `✕ ${x.end.error}`),
      h("h2", { style: { marginTop: "14px" } }, "Arguments"), json(x.start.args),
      h("h2", {}, "Result (what the model got)"), h("pre", {}, prettyJson(x.end.result)));
    for (const e of evs) if (e.type === "approval.requested") body.push(h("div", { class: "chip warning" }, "◐ waited for the user's approval"));
  } else {
    const turnEvents = x.kind === "turn" ? data.events.filter((e) => e.span_id === x.span_id && !e.type.startsWith("turn.")) : evs;
    body.push(h("dl", { class: "kv" }, h("dt", {}, "Status"), h("dd", {}, chip(x.status || (x.end_ms === null ? "running" : ""))), h("dt", {}, "Duration"), h("dd", {}, ms(x.duration_ms))),
      x.start.text && [h("h2", { style: { marginTop: "14px" } }, "User message"), h("div", { class: "text", dir: "auto" }, x.start.text)],
      h("h2", { style: { marginTop: "14px" } }, "Started with"), json(x.start), h("h2", {}, "Ended with"), json(x.end),
      turnEvents.length > 0 && [h("h2", {}, "Events"), table(["At", "Event", ""], turnEvents.map((e) => [ms(e.at_ms), e.type, h("code", { class: "small" }, JSON.stringify(e.payload).slice(0, 140))]), [0])]);
  }
  const canFlag = x.kind === "llm_call" || x.kind === "tool";
  return [h("div", { class: "title-row" }, h("h2", {}, `${KIND_NAMES[x.kind] || x.kind}: ${spanLabel(x)}`),
    canFlag && h("button", { class: "flag-btn small", onclick: () => flagDialog({ turn_id: data.turn_id, span_id: x.span_id, llm_call_id: x.llm_call_id }, x.kind, () => route(true)) },
      `⚑ Flag this ${x.kind === "tool" ? "tool call" : "LLM call"}`)), body];
}
function prettyJson(text) {
  try { return JSON.stringify(JSON.parse(text), null, 2); } catch { return text || ""; }
}

function metricsPanel(data) {
  const s = data.summary || {};
  const kv = (pairs) => h("dl", { class: "kv" }, pairs.filter(Boolean).map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
  return h("div", {},
    h("div", { class: "card" }, h("h2", {}, "This turn"), kv([
      ["Wall time", ms(s.wall_ms)], ["First token", ms(s.ttft_ms)], ["LLM calls", `${num(s.llm_calls)} · ${ms(s.llm_ms)}`],
      ["Tools · wall", ms(s.tool_wall_ms)], ["Tools · work", ms(s.tool_sum_ms)], ["Tool calls", `${num(s.tool_calls)} (${num(s.tool_errors)} failed)`],
      ["Input tokens", `${num(s.input_tokens)} + ${num(s.cached_tokens)} cached`], ["Output tokens", num(s.output_tokens)], ["Cost", usd(s.cost_usd)],
      ["Model", s.model ? `${s.provider}/${s.model}` : "–"]])),
    h("div", { class: "card" }, h("h2", {}, "Steps"), table(["#", "LLM", "Tools", "Wall", "Work", "×"],
      (s.steps || []).map((st) => [st.step, ms(st.llm_ms), num(st.tools), ms(st.tool_wall_ms), ms(st.tool_sum_ms), st.parallelism ? `${st.parallelism}×` : "–"]), [1, 2, 3, 4, 5]),
    h("div", { class: "small muted", style: { marginTop: "6px" } }, "× = tool work ÷ tool wall: above 1, tools ran in parallel.")),
    h("div", { class: "card" }, h("h2", {}, "Timing checks"),
      (s.checks || []).map((c) => h("div", { class: "check" }, h("span", { class: c.ok ? "ok" : "ko" }, c.ok ? "✓" : "✕"), h("div", {}, c.check, h("div", { class: "small muted" }, c.detail))))),
    h("div", { class: "card" }, h("h2", {}, "Flags"),
      data.flags.length ? data.flags.map((f) => flagItem(f, () => route(true), { showTurn: false }))
        : h("div", { class: "small muted" }, "Nothing flagged. Use ⚑ on the turn, an LLM call or a tool call.")),
    h("div", { class: "card" }, h("h2", {}, "Evaluations"),
      data.scores.length ? table(["Evaluator", "Metric", "Value", ""], data.scores.map((x) => [x.evaluator, x.metric, x.value ?? x.label ?? "–", x.label && x.value !== null ? x.label : ""]), [2])
        : h("div", { class: "small muted" }, "No evaluations yet. Scores from LLM-as-a-judge or smaller models will show here (turn_scores table).")));
}

// ------------------------------------------------------------------ flags and the review queue
let catalog = null;
const loadCatalog = async () => (catalog ||= await get("/api/flags/catalog"));
const SOURCE = { user: "👎 user", owner: "owner", detector: "detector" };
const FLAG_STATUS = { suggested: "warning", open: "critical", in_case: "warning", fixed: "good", archived: "", dismissed: "" };
const flagStatus = (s) => h("span", { class: `chip ${FLAG_STATUS[s] || ""}` }, s.replace("_", " "));

async function refreshReviewCount() {
  try {
    const flags = await get("/api/flags?status=suggested,open,in_case");
    const el = document.getElementById("review-count");
    const todo = flags.filter((f) => f.status !== "suggested").length;
    el.textContent = todo || flags.length;
    el.className = `nav-count ${todo ? "todo" : ""}`;
    el.hidden = !flags.length;
  } catch { /* the badge is optional */ }
}

// The flag form: category (only those that apply to this level), and what went wrong
async function flagDialog(target, level, onDone) {
  const cat = await loadCatalog();
  const dialog = document.getElementById("dialog");
  const options = cat.categories.filter((c) => c.levels.includes(level));
  const groups = [...new Set(options.map((c) => c.group))];
  const select = h("select", { required: true, "aria-label": "What went wrong" }, h("option", { value: "" }, "Choose…"),
    groups.map((g) => h("optgroup", { label: g }, options.filter((c) => c.group === g).map((c) => h("option", { value: c.id }, c.label)))));
  const note = h("textarea", { rows: 4, placeholder: "What happened, and what should it have done?" });
  const error = h("div", { class: "small", style: { color: "var(--critical)" } });
  const what = { turn: "this turn", llm_call: "this LLM call", tool: "this tool call" }[level];
  mount(dialog, h("form", { method: "dialog", onsubmit: async (e) => {
    e.preventDefault();
    try {
      await send("POST", "/api/flags", { ...target, category: select.value, note: note.value });
      dialog.close();
      onDone?.();
    } catch (err) { error.textContent = err.message; }
  } },
    h("h2", {}, `Flag ${what}`), h("label", {}, "What went wrong", select), h("label", {}, "Note", note), error,
    h("div", { class: "dialog-actions" }, h("button", { type: "button", onclick: () => dialog.close() }, "Cancel"), h("button", { type: "submit", class: "primary" }, "Flag"))));
  dialog.showModal();
  select.focus();
}

async function setFlag(f, status, onDone) {
  try { await send("PATCH", `/api/flags/${f.id}`, { status }); onDone?.(); } catch (e) { alert(e.message); }
}

function flagItem(f, onDone, { showTurn = true } = {}) {
  const label = (catalog?.categories.find((c) => c.id === f.category) || {}).label || f.label || f.category;
  const actions = f.status === "suggested" ? [h("button", { class: "small primary", onclick: () => setFlag(f, "open", onDone) }, "Confirm"), h("button", { class: "small", onclick: () => setFlag(f, "dismissed", onDone) }, "Dismiss")]
    : ["open", "in_case"].includes(f.status) ? [h("button", { class: "small", onclick: () => setFlag(f, "dismissed", onDone) }, "Dismiss")]
    : f.status === "dismissed" ? [h("button", { class: "small", onclick: () => setFlag(f, "open", onDone) }, "Reopen")] : [];
  return h("div", { class: "flag" },
    h("div", { class: "flag-head" }, h("b", {}, label), flagStatus(f.status), h("span", { class: "chip" }, SOURCE[f.source] || f.source),
      f.span_label && h("span", { class: "small muted" }, `on ${f.span_label}`), h("span", { style: { flex: 1 } }), actions),
    f.note && h("div", { class: "flag-note", dir: "auto" }, f.note),
    showTurn && h("div", { class: "small" }, h("a", { href: `#/t/${f.turn_id}` }, f.title || f.conversation_id), " · ",
      h("span", { class: "muted", dir: "auto" }, `“${(f.user_text || "").slice(0, 120)}”`), " · ", h("span", { class: "muted" }, `${who(f.user_id)} · ${new Date(f.created_at).toLocaleString()}`)),
    (f.case_id || f.fixed_in) && h("div", { class: "small muted" }, [f.case_id && `case ${f.case_id}`, f.fixed_in && `fixed in ${f.fixed_in}`].filter(Boolean).join(" · ")));
}

async function reviewPage(tab) {
  await loadCatalog();
  const TABS = { queue: ["To fix", "open,in_case"], suggested: ["Suggested", "suggested"], done: ["Done", "fixed,archived,dismissed"] };
  const [all, list] = await Promise.all([get("/api/flags?status=*"), get(`/api/flags?status=${TABS[tab]?.[1] || TABS.queue[1]}`)]);
  const count = (statuses) => all.filter((f) => statuses.split(",").includes(f.status)).length;
  const reload = () => route(true);
  const groups = {};
  for (const f of list) (groups[f.group] ||= {})[f.category] = [...((groups[f.group] || {})[f.category] || []), f];
  mount(root,
    h("h1", {}, "Review"),
    h("div", { class: "muted small" }, "What went wrong: thumbs down from the chat, your flags, and what the detectors found (confirm or dismiss those). The fixing process works through “To fix”."),
    h("div", { class: "tabs" }, Object.entries(TABS).map(([id, [name, statuses]]) => h("a", { href: `#/review/${id}`, class: id === (TABS[tab] ? tab : "queue") ? "active" : "" }, `${name} (${count(statuses)})`))),
    !list.length ? h("div", { class: "card empty" }, tab === "suggested" ? "No suggestions: the detectors found nothing new." : tab === "done" ? "Nothing done yet." : "Nothing to fix. Flag a turn with ⚑, or confirm a suggestion.")
      : Object.entries(groups).map(([group, cats]) => h("div", { class: "card" }, h("h2", {}, group),
        Object.entries(cats).map(([, flags]) => flags.map((f) => flagItem(f, reload))))));
}

// ------------------------------------------------------------------ routing
// ---------------------------------------------------------------------- email
// The email check (services/mail.py): counts and traits only, never an email's content, except those shared on purpose.
const pct = (v) => (v === null || v === undefined ? "–" : `${Math.round(100 * v)}%`);
const mins = (v) => (v === null || v === undefined ? "–" : v >= 120 ? `${(v / 60).toFixed(1)} h` : `${Math.round(v)} min`);
const KIND_LABEL = { application_received: "Application received", rejection: "Rejection", interview_invite: "Interview invite",
  next_step: "Next step", offer: "Offer", recruiter_message: "Recruiter message", unrelated: "Unrelated", other: "About it, nothing to do" };
const REASON_LABEL = { not_checked: "Not checked yet", filter: "Filter skipped it", model: "AI misread it", found: "Was found",
  wrong_application: "Wrong application", wrong_kind: "Misread", not_news: "Not news", already_done: "Already done" };
const entries = (o) => Object.entries(o || {});
const CHANNEL_LABEL = { email: "📧 Email", call: "📞 Call", in_person: "🤝 In person", linkedin: "💬 LinkedIn", other: "Other" };

async function mailPage() {
  const d = await get(`/api/mail?${qs()}`);
  users = d.users;
  const t = d.totals;
  const tile = (label, value, sub, cls = "") => h("div", { class: `tile ${cls}` }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), sub && h("div", { class: "sub" }, sub));
  const counts = (o, label = (k) => k) => table(["", "Count"], entries(o).map(([k, n]) => [label(k), num(n)]), [1]);
  mount(root,
    h("h1", {}, "Email"),
    h("div", { class: "muted small" }, "The email check: what it read, what it found, how people judged it, and what it missed. Only counts and traits (platform, language): never an email's content, except the ones people shared."),
    filterBar(route),
    !t.checks && !d.misses.length ? h("div", { class: "card empty" }, "No email checks in this period. Someone connects their Gmail on Profile → Email.") : [
      h("div", { class: "tiles" },
        tile("Emails read", num(t.looked), `${num(t.about)} about an application`),
        tile("Updates found", num(t.found), `median ${mins(t.delay_min.p50)} after arrival`),
        tile("Precision", pct(t.precision), `${num(t.applied)} applied · ${num(t.dismissed)} dismissed`, t.precision !== null && t.precision < 0.8 ? "warn" : ""),
        tile("Recall", pct(t.recall), `${num(t.caught)} caught of ${num(t.told_by_email)} news people said came by email`, t.recall !== null && t.recall < 0.8 ? "warn" : ""),
        tile("Misses diagnosed", num(d.misses.length), `${num(t.fixes_working)} fixed by the person (${num(t.fixes)} tried)`),
        tile("Check errors", num(t.errors), `${num(t.checks)} checks`, t.errors ? "bad" : "")),
      h("div", { class: "grid2" },
        h("div", { class: "card" }, h("h2", {}, "Per kind of news"), table(["Kind", "Found", "Applied", "Dismissed", "Precision"],
          d.kinds.map((k) => [KIND_LABEL[k.kind] || k.kind, num(k.found), num(k.applied), num(k.dismissed), pct(k.applied + k.dismissed ? k.applied / (k.applied + k.dismissed) : null)]), [1, 2, 3, 4])),
        h("div", { class: "card" }, h("h2", {}, "Why updates were dismissed"), counts(d.dismiss_reasons, (k) => REASON_LABEL[k] || k))),
      h("div", { class: "grid2" },
        h("div", { class: "card" }, h("h2", {}, "Misses, by cause"), counts(d.miss_reasons, (k) => REASON_LABEL[k] || k),
          h("div", { class: "small muted", style: { marginTop: "8px" } }, "Filter: the email doesn't name the application as Ezri knows it (the person can add a name or domain). AI: it reached the model, which misread it.")),
        h("div", { class: "card" }, h("h2", {}, "How people heard news they entered"), counts(d.channels, (k) => CHANNEL_LABEL[k] || k),
          h("div", { class: "small muted", style: { marginTop: "8px" } }, `By email: ${num(d.by_email.found)} caught, ${num(d.by_email.missed)} missed, ${num(d.by_email.not_connected)} without a connected mailbox.`))),
      h("div", { class: "card" }, h("h2", {}, "Misses"), table(["When", "Person", "Cause", "AI answered", "Platform", "Language", "Newsletter", "Matches now", "Fixes offered"],
        d.misses.map((m) => [m.ts.slice(0, 16).replace("T", " "), who(m.user), REASON_LABEL[m.reason] || m.reason, m.outcome || "–", m.platform, m.language,
          m.newsletter ? "yes" : "no", m.matches_now ? "yes" : "no", (m.fixes || []).join(", ") || "–"]))),
      d.labels.length > 0 && h("div", { class: "card" }, h("h2", {}, "AI corrections (what it said → what it was)"), table(["AI said", "It was", "Platform", "Language", "Count"],
        d.labels.map((l) => [l.predicted, KIND_LABEL[l.correct] || l.correct, l.platform, l.language, num(l.count)]), [4])),
      h("div", { class: "grid2" },
        h("div", { class: "card" }, h("h2", {}, "Per day"), table(["Day", "Checks", "Read", "About an application", "Found", "Errors"],
          d.per_day.slice().reverse().map((x) => [x.date, num(x.checks), num(x.looked), num(x.about), num(x.found), num(x.errors)]), [1, 2, 3, 4, 5])),
        h("div", { class: "card" }, h("h2", {}, "What the AI answered"), counts(d.verdicts, (k) => KIND_LABEL[k] || k),
          h("h2", { style: { marginTop: "12px" } }, "Updates by platform"), counts(d.platforms))),
      d.errors.length > 0 && h("div", { class: "card" }, h("h2", {}, "Last check errors"), table(["When", "Person", "Error"],
        d.errors.map((e) => [e.ts.slice(0, 16).replace("T", " "), who(e.user), e.error]))),
      h("div", { class: "card" }, h("h2", {}, `Emails shared for review (${d.shared.length})`),
        d.shared.length ? d.shared.map((x) => h("details", { class: "shared-mail" },
          h("summary", {}, h("b", { dir: "auto" }, x.subject || "(no subject)"), " · ", h("span", { dir: "auto" }, x.sender), ` · shared by ${who(x.user)} on ${x.ts.slice(0, 10)}`),
          h("div", { class: "small muted" }, `Received ${x.received_at} · ${x.platform} · ${x.language} · Ezri's outcome: ${x.outcome || "not checked"}`),
          x.note && h("p", {}, h("i", { dir: "auto" }, x.note)),
          h("pre", { class: "mail-text", dir: "auto" }, x.text)))
          : h("div", { class: "muted small" }, "None. People can share a missed email from the “Ezri missed this email” sheet.")),
    ]);
}

async function route(refresh = false) {
  clearTimeout(timer);
  hideTip();
  const hash = location.hash.slice(1) || "/";
  const section = hash === "/" ? "dashboard" : hash.startsWith("/review") ? "review" : hash.startsWith("/mail") ? "mail" : "conversations";
  for (const a of document.querySelectorAll("nav a")) a.classList.toggle("active", a.dataset.nav === section);
  refreshReviewCount();
  const y = scrollY;
  try {
    if (hash.startsWith("/t/")) await turnPage(decodeURIComponent(hash.slice(3)));
    else if (hash.startsWith("/c/")) await conversationPage(decodeURIComponent(hash.slice(3)));
    else if (hash.startsWith("/conversations")) await conversationList();
    else if (hash.startsWith("/review")) await reviewPage(hash.split("/")[2] || "queue");
    else if (hash.startsWith("/mail")) await mailPage();
    else await dashboard();
    if (refresh) scrollTo(0, y);
  } catch (e) {
    mount(root, h("div", { class: "card empty" }, e.message));
  }
}
addEventListener("hashchange", () => { selected = null; route(); });
route();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
