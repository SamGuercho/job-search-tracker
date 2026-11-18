// Follow-up: one row per application, sortable, searchable (incl. keywords), with selectable columns.
import { call } from "../api.js";
import { attempt, chips, daysBadge, field, h, mapsUrl, modal, mount, readForm, statusPill, toast } from "../ui.js";

const state = { query: "", status: "", workModel: "", hideClosed: false, sort: "days", dir: -1 };

// Each column: how to render it, how to sort it, and whether it shows by default.
const COLUMNS = [
  { key: "name", label: "Company", fixed: true, render: (c) => [h("div", { class: "title" }, c.name),
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

export async function renderTracker(root) {
  const [companies, info] = await Promise.all([call("company.list"), call("app.info")]);
  const hidden = loadHidden();
  const tbody = h("tbody");
  const head = h("thead");
  const search = h("input", { placeholder: "Search company, position, keyword, source…", style: "max-width:320px", value: state.query,
    oninput: (e) => { state.query = e.target.value; draw(); } });

  keywordChips = (keywords) => keywords.length
    ? [h("div", { class: "chips" }, keywords.slice(0, 5).map((k) => h("button", {
      class: "chip kw", title: `Show applications with “${k}”`,
      onclick: (e) => { e.stopPropagation(); search.value = state.query = k; draw(); },
    }, k))), keywords.length > 5 && h("span", { class: "small muted" }, `+${keywords.length - 5}`)]
    : h("span", { class: "muted" }, "—");

  const searchable = (c) => [c.name, c.position, c.location, c.address, c.work_model, c.contact.name, c.referee, c.stage,
    c.source_type, c.source_detail, c.salary_expectation, c.salary_posted, ...c.keywords, ...c.qualities].join(" ").toLowerCase();

  const draw = () => {
    const cols = COLUMNS.filter((c) => c.fixed || !hidden.has(c.key));
    const col = COLUMNS.find((c) => c.key === state.sort) || COLUMNS[0];
    const value = col.sortValue ?? ((c) => (c[col.key] ?? "").toString().toLowerCase());
    const q = state.query.toLowerCase().trim();
    const rows = companies
      .filter((c) => !state.status || c.status === state.status)
      .filter((c) => !state.workModel || c.work_model === state.workModel)
      .filter((c) => !state.hideClosed || c.status !== "Rejected")
      .filter((c) => !q || searchable(c).includes(q))
      .sort((a, b) => (value(a) > value(b) ? 1 : value(a) < value(b) ? -1 : 0) * state.dir);

    mount(head, h("tr", {}, cols.map((c) => h("th", {
      onclick: c.sortable === false ? undefined : () => {
        state.dir = state.sort === c.key ? -state.dir : 1;
        state.sort = c.key;
        draw();
      },
    }, c.label, state.sort === c.key ? (state.dir > 0 ? " ▲" : " ▼") : "")), h("th", {}, "")));

    mount(tbody, rows.length ? rows.map((c) => h("tr", { onclick: () => (location.hash = `#/company/${c.id}`) },
      cols.map((col) => h("td", { "data-label": col.label }, col.render(c))),
      h("td", {}, h("button", {
        class: "icon", title: "I followed up today (resets the counter)",
        onclick: async (e) => {
          e.stopPropagation();
          const updated = await attempt(() => call("company.log_update", { id: c.id, by: "me" }), `Follow-up logged for ${c.name}`);
          if (updated) { Object.assign(c, updated); draw(); }
        },
      }, "✓ Followed up"))))
      : h("tr", {}, h("td", { colspan: cols.length + 1, class: "empty" },
        companies.length ? "No application matches these filters." : "No application yet. Add the first one!")));
  };

  const columnsMenu = h("details", { class: "columns-menu" },
    h("summary", { class: "btn" }, "Columns"),
    h("div", { class: "menu" }, COLUMNS.filter((c) => !c.fixed).map((c) => h("label", { class: "check" },
      h("input", {
        type: "checkbox", checked: !hidden.has(c.key),
        onchange: (e) => { e.target.checked ? hidden.delete(c.key) : hidden.add(c.key); saveHidden(hidden); draw(); },
      }), c.label))));

  const counts = info.statuses.map((s) => [s, companies.filter((c) => c.status === s).length]).filter(([, n]) => n);
  const stale = companies.filter((c) => (c.days_since_update ?? 0) >= 14 && ["Applied", "In process"].includes(c.status)).length;

  mount(root,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Follow-up"), h("div", { class: "sub muted" }, `${companies.length} applications tracked`)),
      h("div", { class: "stats" },
        counts.map(([s, n]) => h("span", { class: "stat" }, h("b", {}, n), s)),
        stale ? h("span", { class: "stat", style: "color:var(--red)" }, h("b", {}, stale), "need a follow-up (14d+)") : null),
      h("button", { class: "primary", onclick: () => openCreate(info) }, "+ Add company")),
    chatBar(),
    h("div", { class: "toolbar" },
      search,
      h("select", { style: "max-width:170px", onchange: (e) => { state.status = e.target.value; draw(); } },
        h("option", { value: "" }, "All statuses"), info.statuses.map((s) => h("option", { value: s, selected: s === state.status }, s))),
      h("select", { style: "max-width:170px", onchange: (e) => { state.workModel = e.target.value; draw(); } },
        h("option", { value: "" }, "Any work model"), info.work_models.map((w) => h("option", { value: w, selected: w === state.workModel }, w))),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: state.hideClosed, onchange: (e) => { state.hideClosed = e.target.checked; draw(); } }), "Hide closed"),
      h("span", { class: "spacer" }),
      columnsMenu),
    h("div", { class: "card table-wrap", style: "padding:0" }, h("table", {}, head, tbody)),
  );
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
