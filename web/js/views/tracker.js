// Follow-up: applications as a compact grouped list (default) or a full table (desktop); searchable, filterable.
import { call } from "../api.js";
import { attempt, chips, companyLogo, daysBadge, field, h, mapsUrl, modal, mount, readForm, statusPill, toast } from "../ui.js";

const state = { query: "", status: "", workModel: "", hideClosed: false, sort: "days", dir: -1 };

// Each column: how to render it, how to sort it, and whether it shows by default.
const COLUMNS = [
  { key: "name", label: "Company", fixed: true, render: (c) => [h("div", { class: "title row", style: "gap:8px" }, companyLogo(c, 24), c.name),
    c.website && h("a", { href: c.website, target: "_blank", class: "small", onclick: stop }, shortUrl(c.website))] },
  { key: "position", label: "Position", render: (c) => c.position },
  { key: "status", label: "Status", sortValue: (c) => c.status, render: (c) => statusPill(c.status, c.stage) },
  { key: "days", label: "Since update", sortValue: (c) => c.days_since_update ?? -1, render: (c) => daysBadge(c.days_since_update, c.last_update_by) },
  { key: "location", label: "Location", sortValue: (c) => c.location.toLowerCase(), render: (c) => [
    c.location || c.address ? h("a", { href: mapsUrl(c), target: "_blank", onclick: stop, title: "Open in Google Maps" }, "📍 ", c.location || c.address) : h("span", { class: "muted" }, "—"),
    c.work_model && h("div", {}, h("span", { class: `pill wm-${c.work_model.toLowerCase()}` }, c.work_model))] },
  { key: "salary", label: "Salary", sortValue: (c) => c.salary_expectation, render: (c) => [
    c.salary_expectation || h("span", { class: "muted" }, "—"),
    c.salary_posted && h("div", { class: "small muted", title: "Range posted by the company" }, `posted: ${c.salary_posted}`)] },
  { key: "source", label: "Source", sortValue: (c) => `${c.source_type} ${c.source_detail}`, render: (c) => [
    c.source_type || h("span", { class: "muted" }, "—"), c.source_detail && h("div", { class: "small muted" }, c.source_detail)] },
  { key: "keywords", label: "Keywords", sortable: false, render: (c) => keywordChips(c.keywords) },
  { key: "qualities", label: "Main qualities", sortable: false, render: (c) => [chips(c.qualities.slice(0, 4)),
    c.qualities.length > 4 && h("span", { class: "small muted" }, `+${c.qualities.length - 4}`)] },
  { key: "contact", label: "Contact", sortValue: (c) => c.contact.name, render: (c) => [c.contact.name, c.contact.role && h("div", { class: "small muted" }, c.contact.role)] },
  { key: "referee", label: "Referee", render: (c) => c.referee || h("span", { class: "muted" }, "—") },
  { key: "seniority", label: "Seniority", render: (c) => c.seniority },
];

function stop(e) { e.stopPropagation(); }

// Column choice is a per-browser convenience; the page works without storage.
const HIDDEN_KEY = "followup.hiddenColumns";
const loadHidden = () => { try { return new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) || "[]")); } catch { return new Set(); } };
const saveHidden = (set) => { try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...set])); } catch { /* ignore */ } };

let keywordChips = () => null; // set in renderTracker (needs the search box)

// ---------------------------------------------------------------- view options
// List (default): one compact row per application, grouped. Table: every column, for desktop.
const GROUPS = {
  status: { label: "Status", key: (c) => c.status },
  stage: { label: "Stage", key: (c) => c.stage || c.status },
  position: { label: "Position", key: (c) => c.position || "—" },
  source: { label: "Source", key: (c) => c.source_detail || c.source_type || "—" },
  work_model: { label: "Work model", key: (c) => c.work_model || "—" },
  location: { label: "Location", key: (c) => c.location || "—" },
  none: { label: "No grouping", key: () => "" },
};
const SORTS = {
  attention: "What needs attention",
  updated: "Last update (oldest first)",
  name: "Company A–Z",
  added: "Recently added",
};
const VIEW_KEY = "followup.view";
const loadView = () => { try { return JSON.parse(localStorage.getItem(VIEW_KEY) || "{}"); } catch { return {}; } };
const saveView = () => { try { localStorage.setItem(VIEW_KEY, JSON.stringify({ group: state.group, listSort: state.listSort, mode: state.mode, collapsed: [...state.collapsed] })); } catch { /* ignore */ } };
Object.assign(state, { group: "status", listSort: "attention", mode: "list", collapsed: new Set() }, (() => {
  const v = loadView();  // only the known keys: a stale or edited value can't break the page
  return {
    ...(GROUPS[v.group] ? { group: v.group } : {}), ...(SORTS[v.listSort] ? { listSort: v.listSort } : {}),
    ...(["list", "table"].includes(v.mode) ? { mode: v.mode } : {}), collapsed: new Set(Array.isArray(v.collapsed) ? v.collapsed : []),
  };
})());

const needsFollowUp = (c) => ["Applied", "In process"].includes(c.status) && (c.days_since_update ?? 0) >= 14;

// The next scheduled step with a date (today or later): what the row shows first.
function nextInterview(c) {
  const today = new Date().toISOString().slice(0, 10);
  return (c.process?.nodes || [])
    .filter((n) => n.state === "planned" && n.date && n.date.slice(0, 10) >= today && n.kind !== "closed")
    .sort((a, b) => `${a.date}${a.time || ""}`.localeCompare(`${b.date}${b.time || ""}`))[0] || null;
}

function whenLabel(n) {
  const d = new Date(`${n.date.slice(0, 10)}T00:00:00`);
  const days = Math.round((d - new Date(new Date().toDateString())) / 86400000);
  const day = days === 0 ? "Today" : days === 1 ? "Tomorrow" : d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  return `${day}${n.time ? ` ${n.time}` : ""}`;
}

const STATUS_ORDER = ["Offer", "In process", "Applied", "Not applied", "Rejected"];

export async function renderTracker(root) {
  const [companies, info] = await Promise.all([call("company.list"), call("app.info")]);
  const hidden = loadHidden();
  const tbody = h("tbody");
  const head = h("thead");
  const results = h("div");
  const search = h("input", { type: "search", placeholder: "Search company, position, keyword, source…", value: state.query,
    oninput: (e) => { state.query = e.target.value; draw(); } });

  keywordChips = (keywords) => keywords.length
    ? [h("div", { class: "chips" }, keywords.slice(0, 5).map((k) => h("button", {
      class: "chip kw", title: `Show applications with “${k}”`,
      onclick: (e) => { e.stopPropagation(); search.value = state.query = k; draw(); },
    }, k))), keywords.length > 5 && h("span", { class: "small muted" }, `+${keywords.length - 5}`)]
    : h("span", { class: "muted" }, "—");

  const searchable = (c) => [c.name, c.position, c.location, c.address, c.work_model, c.contact.name, c.referee, c.stage,
    c.source_type, c.source_detail, c.salary_expectation, c.salary_posted, ...c.keywords, ...c.qualities].join(" ").toLowerCase();

  const filtered = () => {
    const q = state.query.toLowerCase().trim();
    return companies
      .filter((c) => !state.status || c.status === state.status)
      .filter((c) => !state.workModel || c.work_model === state.workModel)
      .filter((c) => !state.hideClosed || c.status !== "Rejected")
      .filter((c) => !q || searchable(c).includes(q));
  };

  const followedUp = async (e, c) => {
    e.stopPropagation();
    const updated = await attempt(() => call("company.log_update", { id: c.id, by: "me" }), `Follow-up logged for ${c.name}`);
    if (updated) { Object.assign(c, updated); draw(); }
  };

  // ---------------------------------------------------------------- list view
  const rank = (c) => {  // attention: interviews soon, then follow-ups due, then the rest by time without news
    const next = nextInterview(c);
    if (next) return `0${next.date}${next.time || ""}`;
    if (needsFollowUp(c)) return `1${String(9999 - (c.days_since_update ?? 0)).padStart(4, "0")}`;
    if (c.status === "Rejected") return "9";
    return `2${String(9999 - (c.days_since_update ?? 0)).padStart(4, "0")}`;
  };
  const sorters = {
    attention: (a, b) => rank(a).localeCompare(rank(b)),
    updated: (a, b) => (b.days_since_update ?? -1) - (a.days_since_update ?? -1),
    name: (a, b) => a.name.localeCompare(b.name),
    added: (a, b) => (b.created_at || "").localeCompare(a.created_at || ""),
  };

  const row = (c) => {
    const next = nextInterview(c);
    const due = needsFollowUp(c);
    return h("a", { class: `app-row ${c.status === "Rejected" ? "closed" : ""}`, href: `#/company/${c.id}` },
      companyLogo(c, 36),
      h("div", { class: "app-main" },
        h("div", { class: "app-name", dir: "auto" }, c.name),
        h("div", { class: "app-sub small muted", dir: "auto" }, c.position || "Position not set"),
        next && h("div", { class: "app-next small" }, `📅 ${whenLabel(next)} · ${next.name}`)),
      h("div", { class: "app-side" },
        h("span", { class: `pill ${c.status.toLowerCase().replace(/[^a-z]+/g, "-")}`, title: c.stage }, state.group === "status" ? (c.stage || c.status) : c.status),
        h("span", { class: `app-days ${due ? "due" : ""}`, title: `Last news ${c.days_since_update ?? "?"} days ago, from ${c.last_update_by === "me" ? "you" : "them"}` },
          c.days_since_update === null || c.days_since_update === undefined ? "" : `${c.days_since_update}d`),
        due && h("button", { class: "small app-followed", title: "I followed up today (resets the counter)", onclick: (e) => followedUp(e, c) }, "✓ Followed up")));
  };

  const groupsOf = (rows) => {
    const g = GROUPS[state.group] || GROUPS.status;
    const map = new Map();
    for (const c of rows) {
      const k = g.key(c);
      map.set(k, [...(map.get(k) || []), c]);
    }
    const keys = [...map.keys()].sort((a, b) => state.group === "status"
      ? STATUS_ORDER.indexOf(a) - STATUS_ORDER.indexOf(b)
      : a === "—" ? 1 : b === "—" ? -1 : a.localeCompare(b));
    return keys.map((k) => [k, map.get(k).sort(sorters[state.listSort] || sorters.attention)]);
  };

  const drawList = (rows) => {
    if (!rows.length) return mount(results, emptyState());
    mount(results, groupsOf(rows).map(([name, items]) => {
      if (!name) return h("div", { class: "card app-list" }, items.map(row));
      const id = `${state.group}:${name}`;
      const open = !state.collapsed.has(id);
      return h("section", { class: "app-group" },
        h("button", {
          class: "group-head", "aria-expanded": String(open),
          onclick: () => { open ? state.collapsed.add(id) : state.collapsed.delete(id); saveView(); draw(); },
        }, h("span", { class: "chev" }, open ? "▾" : "▸"), h("b", { dir: "auto" }, name), h("span", { class: "count" }, items.length),
          items.some(needsFollowUp) && h("span", { class: "small due-note" }, `${items.filter(needsFollowUp).length} to follow up`)),
        open && h("div", { class: "card app-list" }, items.map(row)));
    }));
  };

  // --------------------------------------------------------------- table view
  const drawTable = (rows) => {
    const cols = COLUMNS.filter((c) => c.fixed || !hidden.has(c.key));
    const col = COLUMNS.find((c) => c.key === state.sort) || COLUMNS[0];
    const value = col.sortValue ?? ((c) => (c[col.key] ?? "").toString().toLowerCase());
    rows = [...rows].sort((a, b) => (value(a) > value(b) ? 1 : value(a) < value(b) ? -1 : 0) * state.dir);
    mount(head, h("tr", {}, cols.map((c) => h("th", {
      onclick: c.sortable === false ? undefined : () => {
        state.dir = state.sort === c.key ? -state.dir : 1;
        state.sort = c.key;
        draw();
      },
    }, c.label, state.sort === c.key ? (state.dir > 0 ? " ▲" : " ▼") : "")), h("th", {}, "")));
    mount(tbody, rows.length ? rows.map((c) => h("tr", { onclick: () => (location.hash = `#/company/${c.id}`) },
      cols.map((col) => h("td", { "data-label": col.label }, col.render(c))),
      h("td", {}, h("button", { class: "icon", title: "I followed up today (resets the counter)", onclick: (e) => followedUp(e, c) }, "✓ Followed up"))))
      : h("tr", {}, h("td", { colspan: cols.length + 1, class: "empty" }, emptyState())));
    mount(results, h("div", { class: "card table-wrap", style: "padding:0" }, h("table", {}, head, tbody)));
  };

  const emptyState = () => (companies.length ? "No application matches these filters." : "No application yet. Add the first one!");

  // ------------------------------------------------------------------ toolbar
  const activeFilters = () => [state.status, state.workModel, state.hideClosed].filter(Boolean).length;
  const filtersBtn = h("button", { onclick: () => openFilters() });
  const openFilters = () => {
    const status = h("select", {}, h("option", { value: "" }, "All statuses"), info.statuses.map((s) => h("option", { value: s, selected: s === state.status }, s)));
    const work = h("select", {}, h("option", { value: "" }, "Any work model"), info.work_models.map((w) => h("option", { value: w, selected: w === state.workModel }, w)));
    const sort = h("select", {}, Object.entries(SORTS).map(([k, label]) => h("option", { value: k, selected: k === state.listSort }, label)));
    const closed = h("input", { type: "checkbox", checked: state.hideClosed });
    const apply = () => {
      Object.assign(state, { status: status.value, workModel: work.value, listSort: sort.value, hideClosed: closed.checked });
      saveView();
      close();
      drawStats();
      draw();
    };
    const close = modal("Filter and sort", h("div", { class: "form-grid" },
      field("Status", status), field("Work model", work), field("Sort", sort),
      h("label", { class: "check wide" }, closed, "Hide closed applications"),
      h("div", { class: "sheet-actions wide" },
        h("button", { onclick: () => { status.value = work.value = ""; closed.checked = false; sort.value = "attention"; } }, "Reset"),
        h("button", { class: "primary", onclick: apply }, "Show"))));
  };
  const groupSelect = h("select", {
    title: "Group by", onchange: (e) => { state.group = e.target.value; saveView(); draw(); },
  }, Object.entries(GROUPS).map(([k, g]) => h("option", { value: k, selected: k === state.group }, k === "none" ? g.label : `By ${g.label.toLowerCase()}`)));
  const modeSwitch = h("div", { class: "seg-switch desktop-only", role: "group", "aria-label": "View" },
    [["list", "List"], ["table", "Table"]].map(([m, label]) => h("button", {
      class: state.mode === m ? "on" : "", onclick: () => { state.mode = m; saveView(); draw(); },
    }, label)));
  const columnsMenu = h("details", { class: "columns-menu" },
    h("summary", { class: "btn" }, "Columns"),
    h("div", { class: "menu" }, COLUMNS.filter((c) => !c.fixed).map((c) => h("label", { class: "check" },
      h("input", {
        type: "checkbox", checked: !hidden.has(c.key),
        onchange: (e) => { e.target.checked ? hidden.delete(c.key) : hidden.add(c.key); saveHidden(hidden); draw(); },
      }), c.label))));
  const toolbar = h("div", { class: "toolbar followup-toolbar" });

  const draw = () => {
    const table = state.mode === "table" && matchMedia("(min-width: 761px)").matches;
    const n = activeFilters();
    filtersBtn.textContent = n ? `Filters · ${n}` : "Filters";
    filtersBtn.classList.toggle("on", Boolean(n));
    [...modeSwitch.children].forEach((b, i) => b.classList.toggle("on", (i === 1) === table));
    mount(toolbar, search, !table && groupSelect, filtersBtn, h("span", { class: "spacer" }), modeSwitch, table && columnsMenu);
    const rows = filtered();
    table ? drawTable(rows) : drawList(rows);
  };

  const counts = info.statuses.map((s) => [s, companies.filter((c) => c.status === s).length]).filter(([, n]) => n);
  const stale = companies.filter(needsFollowUp).length;

  // Status counters double as quick filters (tap again to show all)
  const stats = h("div", { class: "stats" });
  const drawStats = () => mount(stats,
    counts.map(([s, n]) => h("button", {
      class: `stat ${state.status === s ? "on" : ""}`, title: `Show only ${s}`,
      onclick: () => { state.status = state.status === s ? "" : s; drawStats(); draw(); },
    }, h("b", {}, n), s)),
    stale ? h("span", { class: "stat due" }, h("b", {}, stale), "to follow up (14d+)") : null);

  mount(root,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Follow-up"), h("div", { class: "sub muted" }, `${companies.length} applications tracked`)),
      stats,
      h("button", { class: "primary", onclick: () => openCreate(info) }, "+ Add company")),
    chatBar(),
    toolbar,
    results,
  );
  drawStats();
  draw();
}

// Chat bar: a question here opens the assistant with it.
function chatBar() {
  const input = h("input", { placeholder: "Ask the assistant: “Add this job: <link>”, “Who needs a follow-up?”, “Make my CV for Nimbus”…" });
  const go = (e) => {
    e.preventDefault();
    if (input.value.trim()) location.hash = `#/chat?q=${encodeURIComponent(input.value.trim())}`;
  };
  return h("form", { class: "chat-bar", onsubmit: go }, h("span", { "aria-hidden": "true" }, "✦"), input, h("button", { class: "primary", type: "submit" }, "Ask"));
}

function shortUrl(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}

export function openCreate(info) {
  const select = (name, options, placeholder) => h("select", { name }, h("option", { value: "" }, placeholder), options.map((o) => h("option", { value: o }, o)));
  const form = h("form", { class: "form-grid" },
    field("Company *", h("input", { name: "name", required: true, autofocus: true })),
    field("Position", h("input", { name: "position" })),
    field("Seniority", h("input", { name: "seniority", placeholder: "Junior, Senior, Lead…" })),
    field("Location", h("input", { name: "location" })),
    field("Work model", select("work_model", info.work_models, "Unknown")),
    field("Job posting link", h("input", { name: "job_url", type: "url", placeholder: "https://" })),
    field("Source", select("source_type", info.source_types, "Unknown")),
    field("Source detail", h("input", { name: "source_detail", placeholder: "LinkedIn, Dana from TalentCo…" })),
    field("My salary expectation", h("input", { name: "salary_expectation", placeholder: "e.g. 38k ILS/month" })),
    field("Salary posted", h("input", { name: "salary_posted", placeholder: "Range in the ad, if any" })),
    field("Website", h("input", { name: "website", type: "url", placeholder: "https://" })),
    field("Contact name", h("input", { name: "contact.name" })),
    field("Contact role", h("input", { name: "contact.role" })),
    field("Contact email", h("input", { name: "contact.email", type: "email" })),
    field("Referee", h("input", { name: "referee" })),
    field("Main qualities required (comma separated)", h("input", { name: "qualities", "data-list": true }), "wide"),
    field("Job description", h("textarea", { name: "job_description", rows: 5, placeholder: "Paste the job ad: it is saved in the company folder and its keywords are extracted for your CV" }), "wide"),
    h("div", { class: "row wide" }, h("span", { class: "spacer" }), h("button", { class: "primary", type: "submit" }, "Add company")),
  );
  const close = modal("New application", form);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = readForm(form);
    // Quick match (no AI) so adding a company stays instant; the CV builder can run the full match.
    if (data.job_description) data.keywords = await call("cv.keywords.match_job", { text: data.job_description, use_ai: false }).then((r) => r.keywords).catch(() => []);
    const created = await attempt(() => call("company.create", data));
    if (created) {
      close();
      toast(`${created.name} added`);
      location.hash = `#/company/${created.id}`;
    }
  });
}
