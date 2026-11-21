// Small DOM helpers shared by all views (no framework, no build step).

/** h("div", {class: "x", onclick: fn}, child, "text", [more children]) */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  // Hebrew/Arabic typed or shown: each field and text block picks its own direction from its content
  if ((tag === "textarea" || tag === "input") && !(attrs && "dir" in attrs)) el.dir = "auto";
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = v;
    else if (k === "dir") el.dir = v;
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

/** A dialog (a bottom sheet on phones). Escape or a tap outside closes it. Returns close(). */
export function modal(title, body, { onClose } = {}) {
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    back.remove();
    document.removeEventListener("keydown", onKey);
    onClose?.();
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const back = h("div", { class: "modal-back", onclick: (e) => e.target === back && close() },
    h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title || "Dialog" },
      h("div", { class: "sheet-handle", "aria-hidden": "true" }),
      h("div", { class: "row", style: "margin-bottom:14px" }, h("h2", { style: "margin:0" }, title),
        h("span", { class: "spacer" }), h("button", { class: "ghost icon", onclick: close, "aria-label": "Close" }, "✕")),
      body));
  document.addEventListener("keydown", onKey);
  document.body.append(back);
  return close;
}

/**
 * In-app replacement for confirm(): resolves true / false.
 *   if (!(await confirmSheet("Delete Nimbus?", { ok: "Delete", danger: true }))) return;
 */
export function confirmSheet(message, { title = "Are you sure?", ok = "OK", danger = false } = {}) {
  return sheet(message, { title, ok, danger });
}

/** In-app replacement for prompt(): resolves the trimmed text, or null if cancelled or empty. */
export function promptSheet(message, value = "", { title = "", ok = "Save", placeholder = "", suggestions = [] } = {}) {
  return sheet(message, { title, ok, input: { value, placeholder, suggestions } });
}

function sheet(message, { title, ok, danger = false, input = null }) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => { if (!settled) { settled = true; close(); resolve(value); } };
    const listId = `sheet-list-${Date.now()}`;
    const field = input && h("input", {
      value: input.value ?? "", placeholder: input.placeholder, list: input.suggestions.length ? listId : null, "aria-label": title || message,
    });
    const body = h("form", {
      class: "sheet-body", onsubmit: (e) => { e.preventDefault(); finish(input ? (field.value.trim() || null) : true); },
    },
      message && h("p", { dir: "auto" }, message),
      field,
      input?.suggestions.length > 0 && h("datalist", { id: listId }, input.suggestions.map((s) => h("option", { value: s }))),
      h("div", { class: "sheet-actions" },
        h("button", { type: "button", onclick: () => finish(input ? null : false) }, "Cancel"),
        h("button", { type: "submit", class: danger ? "danger solid" : "primary" }, ok)));
    const close = modal(title, body, { onClose: () => finish(input ? null : false) });
    setTimeout(() => (field ? field.select() : body.querySelector("[type=submit]").focus()), 50);
  });
}

/** Native share sheet (phones, Safari, Edge) when available; otherwise copies the text. */
export async function share({ title, text }) {
  if (navigator.share) {
    try { await navigator.share({ title, text }); return; } catch (e) { if (e.name === "AbortError") return; }
  }
  await navigator.clipboard.writeText(text);
  toast("Copied");
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

// Minimal, safe markdown (text only, no HTML): headings, paragraphs, "- " lists, ``` code blocks, **bold**, `code`, [text](#/route, /files/… or https://url).
export function markdown(text) {
  const inline = (s) => {
    const out = [];
    const re = /\*\*(.+?)\*\*|`(.+?)`|\[([^\]]+)\]\(((?:#\/|\/files\/|https?:\/\/)[^)\s]+)\)/g;
    let last = 0;
    for (const m of s.matchAll(re)) {
      out.push(s.slice(last, m.index));
      if (m[1]) out.push(h("b", {}, m[1]));
      else if (m[2]) out.push(h("code", {}, m[2]));
      else out.push(h("a", { href: m[4], target: m[4].startsWith("#") ? null : "_blank" }, m[3]));  // files open in a new tab
      last = m.index + m[0].length;
    }
    out.push(s.slice(last));
    return out;
  };
  const blocks = [];
  let items = null;
  let code = null;  // lines of a ``` fenced block
  for (const line of text.split("\n")) {
    if (/^\s*```/.test(line)) {
      if (code) { blocks.push(h("pre", {}, h("code", {}, code.join("\n")))); code = null; }
      else { if (items) { blocks.push(h("ul", {}, items)); items = null; } code = []; }
      continue;
    }
    if (code) { code.push(line); continue; }
    const li = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)/);
    if (li) { (items ??= []).push(h("li", { dir: "auto" }, inline(li[1]))); continue; }
    if (items) { blocks.push(h("ul", {}, items)); items = null; }
    const heading = line.match(/^\s*#{1,6}\s+(.*)/);
    if (heading) blocks.push(h("p", { class: "md-h", dir: "auto" }, inline(heading[1])));
    else if (line.trim()) blocks.push(h("p", { dir: "auto" }, inline(line)));
  }
  if (code) blocks.push(h("pre", {}, h("code", {}, code.join("\n"))));
  if (items) blocks.push(h("ul", {}, items));
  return blocks;
}

/** Money in US dollars: small amounts keep 3 decimals ($0.004), unknown is "—". */
export function usd(v) {
  if (v === null || v === undefined) return "—";
  if (v === 0) return "$0";
  return `$${v < 0.1 ? v.toFixed(3) : v.toFixed(2)}`;
}

/** "Add to calendar" sheet for a scheduled step: Google Calendar, or an .ics file (Apple Calendar, Outlook…). */
export async function addToCalendar(companyId, stepId) {
  const { call } = await import("./api.js");
  let e;
  try { e = await call("calendar.event", { company: companyId, step: stepId }); } catch (err) { return toast(err.message, true); }
  const compact = (v) => v.replace(/[-:]/g, "");  // 2026-12-01T14:30 -> 20261201T1430(00)
  const dates = e.all_day ? `${compact(e.start)}/${compact(e.end)}` : `${compact(e.start)}00/${compact(e.end)}00`;
  const details = `${e.description}\n\nApplication, prep and notes: ${location.origin}/#/company/${companyId}`;
  const google = "https://calendar.google.com/calendar/render?action=TEMPLATE"
    + `&text=${encodeURIComponent(e.title)}&dates=${dates}&details=${encodeURIComponent(details)}`
    + `&location=${encodeURIComponent(e.location || "")}`;
  const ics = `/api/calendar/step.ics?company=${encodeURIComponent(companyId)}&step=${encodeURIComponent(stepId)}`;
  const when = e.all_day ? e.start : e.start.replace("T", " at ");
  const close = modal("Add to calendar", h("div", { class: "more-list" },
    h("p", { class: "small muted", dir: "auto" }, `${e.title} · ${when}`, e.location && ` · ${e.location}`),
    h("a", { href: google, target: "_blank", rel: "noopener", onclick: () => close() },
      h("b", {}, "Google Calendar"), h("span", { class: "small muted" }, "Opens the event, ready to save")),
    h("a", { href: ics, onclick: () => setTimeout(close, 300) },
      h("b", {}, "Apple Calendar, Outlook, other"), h("span", { class: "small muted" }, "Downloads the event (.ics) for your calendar app")),
    h("p", { class: "small muted" }, "Reminders: the day before and 1 hour before. Adding it again updates it.")));
}

/** A company's logo, or its initials on a colour of its own (same name, same colour) when there is none.
 *  `c` needs name and logo_file (and logo_checked_at to refresh the browser's copy when it changes). */
export function companyLogo(c, size = 36) {
  const initials = () => {
    const words = (c.name || c.company || "?").replace(/[^\p{L}\p{N} ]/gu, " ").split(/\s+/).filter(Boolean);
    const text = (words.length > 1 ? words[0][0] + words[1][0] : (words[0] || "?").slice(0, 2)).toUpperCase();
    let hash = 0;
    for (const ch of c.name || c.company || "") hash = (hash * 31 + ch.codePointAt(0)) % 360;
    return h("span", { class: "logo-avatar initials", style: `--size:${size}px; --hue:${hash}`, "aria-hidden": "true" }, text);
  };
  if (!c.logo_file) return initials();
  const img = h("img", {
    class: "logo-avatar", src: `/files/${c.logo_file.split("/").map(encodeURIComponent).join("/")}?v=${encodeURIComponent(c.logo_checked_at || "")}`,
    alt: "", width: size, height: size, loading: "lazy", style: `--size:${size}px`,
  });
  img.addEventListener("error", () => img.replaceWith(initials()));  // offline, or the file is gone
  return img;
}
