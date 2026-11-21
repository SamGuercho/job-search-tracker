// Process graph of one application: layered layout, click a node to edit it,
// "+" on a link inserts a step there, and a red Closed rail every step leads to.
import { call } from "../api.js";
import { attempt, confirmSheet, field, formatDate, h, mount, readForm } from "../ui.js";

const W = 136, H = 70, COL = 184, ROW = 96, PAD = 12, RAIL = 26;
const SUGGESTIONS = ["Applied", "Response received", "First HR interview", "Hiring manager interview", "Technical interview",
  "Take-home test", "Case study", "Panel interview", "Final interview", "Reference check", "Offer"];
const STATES = [["todo", "Off"], ["planned", "Planned"], ["done", "Done"]];

// Columns: longest path from a start step. Rows: the main path (from the first step, always following the
// earliest-created next step) stays on row 0; branch steps go below, so a branch never hides a direct link.
function layout(process) {
  const steps = process.nodes.filter((n) => n.kind !== "closed");
  const created = Object.fromEntries(steps.map((n, i) => [n.id, i]));
  const preds = Object.fromEntries(steps.map((n) => [n.id, []]));
  const succs = Object.fromEntries(steps.map((n) => [n.id, []]));
  for (const e of process.edges) { preds[e.target]?.push(e.source); succs[e.source]?.push(e.target); }
  const main = new Set();
  let cur = steps.find((n) => !preds[n.id].length)?.id;
  while (cur && !main.has(cur)) {
    main.add(cur);
    cur = [...succs[cur]].sort((a, b) => created[a] - created[b])[0];
  }
  const col = {};
  const depth = (id, seen = new Set()) => {
    if (col[id] !== undefined) return col[id];
    if (seen.has(id)) return 0;
    seen.add(id);
    return (col[id] = preds[id].length ? 1 + Math.max(...preds[id].map((p) => depth(p, seen))) : 0);
  };
  steps.forEach((n) => depth(n.id));
  const rows = {}, pos = {};   // rows[c] = last used row in column c
  for (const n of steps) {
    const c = col[n.id];
    const r = main.has(n.id) ? 0 : (rows[c] = Math.max(rows[c] ?? 0, 0) + 1);
    if (main.has(n.id)) rows[c] = Math.max(rows[c] ?? 0, 0);
    pos[n.id] = { x: PAD + c * COL, y: PAD + r * ROW };
  }
  const cols = Math.max(0, ...Object.values(col)) + 1;
  const maxRows = Math.max(1, ...Object.values(rows).map((r) => r + 1));
  return { steps, pos, width: PAD * 2 + (cols - 1) * COL + W + 150, railY: PAD + maxRows * ROW + 6 };
}

const remembered = new Map(); // company id -> selected node, kept across page re-renders

export function processCard(company, { onChange, stepTypes = {} }) {
  const p = company.process;
  let selected = remembered.get(company.id) ?? null;   // node id
  let adding = null;     // {mode: "end"|"after"|"before"|"between"|"branch", ref, ref2}
  const card = h("div", { class: "card" });

  const run = async (action, payload, msg) => {
    const r = await attempt(() => call(action, { company: company.id, ...payload }), msg);
    if (r) { adding = null; await onChange(); }
    return r;
  };

  function draw() {
    remembered.set(company.id, selected);
    const { steps, pos, width, railY } = layout(p);
    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("width", width);
    svg.setAttribute("height", railY + RAIL);
    svg.classList.add("pg-svg");
    const path = (d, cls) => {
      const el = document.createElementNS(svgNS, "path");
      el.setAttribute("d", d);
      el.setAttribute("class", cls);
      svg.append(el);
    };
    const plus = [];
    for (const e of p.edges) {
      const a = pos[e.source], b = pos[e.target];
      if (!a || !b) continue;
      const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2, mx = (x1 + x2) / 2;
      const done = p.nodes.find((n) => n.id === e.target)?.state === "done";
      path(`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, `pg-edge ${done ? "done" : ""}`);
      plus.push(h("button", {
        class: "pg-plus", title: "Insert a step here", style: `left:${mx - 11}px;top:${(y1 + y2) / 2 - 11}px`,
        onclick: () => { adding = { mode: "between", ref: e.source, ref2: e.target }; selected = null; draw(); },
      }, "+"));
    }
    // every step leads to Closed: dashed drop lines to the rail
    for (const n of steps) path(`M${pos[n.id].x + W / 2},${pos[n.id].y + H} L${pos[n.id].x + W / 2},${railY}`, "pg-drop");
    path(`M${PAD},${railY} L${width - 150},${railY}`, "pg-rail");

    const closed = p.nodes.find((n) => n.kind === "closed");
    const nodeEl = (n) => h("button", {
      class: `pg-node ${n.state} ${n.kind} ${selected === n.id ? "selected" : ""}`,
      style: `left:${pos[n.id].x}px;top:${pos[n.id].y}px;width:${W}px;height:${H}px`,
      title: n.notes || "", onclick: () => { selected = selected === n.id ? null : n.id; adding = null; draw(); },
    },
      h("span", { class: "pg-dot" }, n.state === "done" ? "✓" : n.state === "planned" ? "◷" : ""),
      h("span", { class: "pg-name" }, n.name, n.kind === "offer" ? " 🎉" : ""),
      h("span", { class: "pg-date" }, n.date ? formatDate(n.date) : n.state === "todo" ? "off" : ""));

    mount(card,
      h("div", { class: "row" },
        h("h2", { style: "margin:0" }, "Process"),
        h("span", { class: `pill ${slug(company.status)}` }, company.status),
        h("span", { class: "small muted" }, company.stage !== company.status ? `· ${company.stage}` : ""),
        h("span", { class: "spacer" }),
        h("button", { class: "small", onclick: () => { adding = { mode: "end" }; selected = null; draw(); } }, "+ Add step")),
      h("div", { class: "small muted", style: "margin:4px 0 6px" }, "Click a step to turn it on/off or edit it · + on a link inserts a step · every step can end in Rejected / Closed"),
      h("div", { class: "pg-wrap" },
        h("div", { class: "pg-canvas", style: `width:${width}px;height:${railY + RAIL}px` },
          svg, plus, steps.map(nodeEl),
          h("button", {
            class: `pg-closed ${closed.state === "done" ? "on" : ""} ${selected === closed.id ? "selected" : ""}`,
            style: `left:${width - 146}px;top:${railY - 18}px`,
            onclick: () => { selected = selected === closed.id ? null : closed.id; adding = null; draw(); },
          }, closed.state === "done" ? `✕ ${closed.name}` : closed.name))),
      adding ? addForm() : selected ? editor(p.nodes.find((n) => n.id === selected)) : null);
  }

  // ------------------------------------------------------------- editors
  function editor(n) {
    if (!n) return null;
    if (n.kind === "closed") return closedEditor(n);
    const form = h("form", { class: "step-editor form-grid" },
      field("Step name", h("input", { name: "name", value: n.name, required: true, list: "pg-names" })),
      field("Date", h("input", { name: "date", type: "date", value: n.date || "" })),
      field("Type (for analytics)", h("select", { name: "category" },
        h("option", { value: "" }, `Automatic (${stepTypes[inferred(n)] || "Other"})`),
        Object.entries(stepTypes).map(([k, label]) => h("option", { value: k, selected: n.category === k || (n.kind === "offer" && k === "offer") }, label)))),
      field("Notes", h("textarea", { name: "notes", rows: 3, placeholder: "Who you met, questions asked, feedback…" }, n.notes), "wide"),
      h("div", { class: "row wide" },
        h("span", { class: "small muted" }, "State:"),
        STATES.map(([s, label]) => h("button", {
          type: "button", class: `seg ${n.state === s ? "on " + s : ""}`,
          onclick: () => run("process.set_state", { step: n.id, state: s, ...(s !== "todo" && !n.date ? { date: today() } : {}) }),
        }, label)),
        h("span", { class: "spacer" }),
        h("button", { type: "submit", class: "primary" }, "Save")),
      h("div", { class: "row wide" },
        h("button", { type: "button", class: "small", onclick: () => { adding = { mode: "before", ref: n.id }; draw(); } }, "+ Step before"),
        h("button", { type: "button", class: "small", onclick: () => { adding = { mode: "after", ref: n.id }; draw(); } }, "+ Step after"),
        h("button", { type: "button", class: "small", onclick: () => { adding = { mode: "branch", ref: n.id }; draw(); } }, "⑂ Branch from here"),
        h("span", { class: "spacer" }),
        h("button", {
          type: "button", class: "small danger", onclick: async () => (await confirmSheet("The steps around it are reconnected.",
            { title: `Delete “${n.name}”?`, ok: "Delete", danger: true }))
            && run("process.delete_step", { step: n.id }, "Step deleted").then(() => (selected = null)),
        }, "Delete step")),
      datalist());
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const d = readForm(form);
      run("process.update_step", { step: n.id, fields: { name: d.name, date: d.date || null, notes: d.notes, category: d.category } }, "Step saved");
    });
    return form;
  }

  function closedEditor(n) {
    const isClosed = n.state === "done";
    const form = h("form", { class: "step-editor form-grid closed-editor" },
      h("div", { class: "wide small" }, isClosed
        ? "This application is closed (status Rejected)."
        : "Turn this on when the job is closed for you: rejection, withdrawal, position filled…"),
      field("Name", h("input", { name: "name", value: n.name })),
      field("Date", h("input", { name: "date", type: "date", value: n.date || "" })),
      field("Reason / notes", h("textarea", { name: "notes", rows: 2 }, n.notes), "wide"),
      h("div", { class: "row wide" },
        isClosed
          ? h("button", { type: "button", onclick: () => run("process.reopen", {}, "Reopened") }, "Reopen application")
          : h("button", { type: "button", class: "danger-solid", onclick: () => run("process.close", { reason: readForm(form).notes }, "Application closed") }, "Close this application"),
        h("span", { class: "spacer" }),
        h("button", { type: "submit" }, "Save")));
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const d = readForm(form);
      run("process.update_step", { step: n.id, fields: { name: d.name, date: d.date || null, notes: d.notes } }, "Saved");
    });
    return form;
  }

  function addForm() {
    const name = (id) => p.nodes.find((n) => n.id === id)?.name;
    const where = {
      end: "at the end", after: `after “${name(adding.ref)}”`, before: `before “${name(adding.ref)}”`,
      between: `between “${name(adding.ref)}” and “${name(adding.ref2)}”`, branch: `as a branch from “${name(adding.ref)}”`,
    }[adding.mode];
    const joinable = p.nodes.filter((n) => n.kind !== "closed" && n.id !== adding.ref);
    const form = h("form", { class: "step-editor form-grid" },
      h("div", { class: "wide" }, h("b", {}, "New step "), where),
      field("Step name", h("input", { name: "name", required: true, autofocus: true, list: "pg-names", placeholder: "Technical interview, Take-home test…" })),
      field("State", h("select", { name: "state" }, STATES.map(([s, l]) => h("option", { value: s }, l)))),
      field("Date", h("input", { name: "date", type: "date" })),
      adding.mode === "branch" && field("Joins back at", h("select", { name: "join" },
        h("option", { value: "" }, "Nowhere (dead end)"), joinable.map((n) => h("option", { value: n.id }, n.name)))),
      h("div", { class: "row wide" }, h("span", { class: "spacer" }),
        h("button", { type: "button", class: "ghost", onclick: () => { adding = null; draw(); } }, "Cancel"),
        h("button", { type: "submit", class: "primary" }, "Add step")),
      datalist());
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const d = readForm(form);
      const base = { name: d.name, state: d.state, ...(d.date ? { date: d.date } : {}) };
      const m = adding.mode;
      if (m === "branch") run("process.add_branch", { ...base, from_step: adding.ref, ...(d.join ? { join: d.join } : {}) }, "Branch added");
      else run("process.add_step", {
        ...base,
        ...(m === "after" || m === "between" ? { after: adding.ref } : {}),
        ...(m === "before" ? { before: adding.ref } : m === "between" ? { before: adding.ref2 } : {}),
      }, "Step added");
    });
    return form;
  }

  const datalist = () => h("datalist", { id: "pg-names" }, SUGGESTIONS.map((s) => h("option", { value: s })));
  draw();
  return card;
}

const today = () => new Date().toISOString().slice(0, 10);

// Mirrors Node.type() on the server, to show what "Automatic" means for this step.
const RULES = [["offer", /offer/i], ["reference", /reference/i], ["applied", /appl|sent|submitted/i],
  ["response", /response|repl|answer|got back|screen/i],
  ["technical", /technical|tech\b|coding|take.?home|home assignment|assignment|case|test|system design|live code/i],
  ["hr", /\bhr\b|recruit|talent|human resources|phone screen/i],
  ["interview", /interview|meet|call|panel|onsite|on-site|final|cto|ceo|manager/i]];
const inferred = (n) => (n.kind === "offer" ? "offer" : RULES.find(([, re]) => re.test(n.name))?.[0] || "other");
export const slug = (s) => s.toLowerCase().replace(/[^a-z]+/g, "-");
