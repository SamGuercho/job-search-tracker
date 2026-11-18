// Small DOM helpers shared by all views (no framework, no build step).

/** h("div", {class: "x", onclick: fn}, child, "text", [more children]) */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function mount(root, ...children) {
  root.replaceChildren();
  append(root, children);
}

export function toast(message, isError = false) {
  const t = h("div", { class: `toast${isError ? " error" : ""}` }, message);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), isError ? 6000 : 2200);
}

/** Run an async action, showing errors as toasts. Returns undefined on failure. */
export async function attempt(fn, success) {
  try {
    const r = await fn();
    if (success) toast(success);
    return r;
  } catch (e) {
    toast(e.message, true);
  }
}

export function modal(title, body, { onClose } = {}) {
  const close = () => { back.remove(); onClose?.(); };
  const back = h("div", { class: "modal-back", onclick: (e) => e.target === back && close() },
    h("div", { class: "modal" },
      h("div", { class: "row", style: "margin-bottom:14px" }, h("h2", { style: "margin:0" }, title),
        h("span", { class: "spacer" }), h("button", { class: "ghost icon", onclick: close, "aria-label": "Close" }, "✕")),
      body));
  document.body.append(back);
  return close;
}

export const field = (label, input, cls = "") => h("label", { class: `field ${cls}` }, label, input);

/** Read named inputs of a container into an object (supports "a.b" nested names). */
export function readForm(root) {
  const out = {};
  for (const el of root.querySelectorAll("[name]")) {
    let v = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
    if (el.dataset.list !== undefined) v = splitList(v);
    const path = el.name.split(".");
    let o = out;
    path.slice(0, -1).forEach((p) => (o = o[p] ??= {}));
    o[path.at(-1)] = v;
  }
  return out;
}

export const splitList = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
export const joinList = (a) => (a || []).join(", ");

export function chips(items, cls = "") {
  return h("div", { class: "chips" }, (items || []).map((i) => h("span", { class: `chip ${cls}`, title: i }, i)));
}

export function statusPill(status, stage) {
  const pill = h("span", { class: `pill ${status.toLowerCase().replace(/[^a-z]+/g, "-")}` }, status);
  return stage && stage !== status ? h("span", { class: "stack-tight" }, pill, h("div", { class: "small muted" }, stage)) : pill;
}

export function daysBadge(days, by) {
  if (days === null || days === undefined) return h("span", { class: "muted" }, "—");
  const level = days < 7 ? "fresh" : days < 14 ? "warm" : "stale";
  return h("span", { class: `days ${level}`, title: `Last update from ${by === "me" ? "you" : "them"}` },
    h("b", {}, `${days}d`), h("span", { class: "small muted" }, by === "me" ? "by me" : "by them"));
}

export function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function debounce(fn, ms = 300) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/** Same rule as the server (master_cv.keyword_pattern): case-insensitive whole word,
 *  spaces/hyphens interchangeable, optional plural. */
export function keywordPattern(keyword, flags = "i") {
  const k = keyword.trim();
  const parts = k.split(/[\s-]+/).map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const plural = /[A-Za-z0-9]$/.test(k) ? "(?:e?s)?" : "";
  return new RegExp(`(?<![\\w])${parts.join("[\\s\\-]+")}${plural}(?![\\w])`, flags);
}

/** Google Maps search link for an application: exact address if known, else company + location. */
export function mapsUrl(c) {
  const query = c.address || [c.name, c.location].filter(Boolean).join(", ");
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}`;
}
