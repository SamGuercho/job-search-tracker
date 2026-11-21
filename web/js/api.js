import { h, toast } from "./ui.js";

// Single entry point to the backend: every call is a named action.
//
// Offline reading: the answers of read-only actions are kept on this device (Cache API). When the server
// can't be reached (no network, or the home server is off), those calls answer from the saved copy and a
// banner says the page is offline and read-only; changes are refused until the server is back.
const READ_ONLY = new Set([
  "app.info", "today.get", "company.list", "company.get", "prep.get", "practice.profile", "practice.history",
  "practice.capabilities", "recording.list", "recording.transcript", "cv.master.get", "chat.list", "chat.get",
  "calendar.upcoming", "analytics.summary", "usage.report", "usage.averages", "process.get",
]);
const DATA_CACHE = "jobtracker-data-v1";
const SERVER_DOWN = new Set([502, 503, 504]);  // what Tailscale answers when the home server's app is off
const cacheKey = (action, payload) => `/offline-data/${action}?p=${encodeURIComponent(JSON.stringify(payload))}`;

async function save(action, payload, result) {
  if (!READ_ONLY.has(action) || !("caches" in window)) return;
  try {
    const cache = await caches.open(DATA_CACHE);
    await cache.put(cacheKey(action, payload), new Response(JSON.stringify({ result, at: new Date().toISOString() }),
      { headers: { "Content-Type": "application/json" } }));
  } catch { /* storage full or unavailable: the page still works online */ }
}

async function fromDevice(action, payload) {
  if (!READ_ONLY.has(action)) throw new Error("You're offline: changes need the server. Try again when it's back.");
  const hit = "caches" in window ? await (await caches.open(DATA_CACHE)).match(cacheKey(action, payload)).catch(() => null) : null;
  if (!hit) throw new Error("You're offline, and this page isn't saved on this device yet.");
  const { result, at } = await hit.json();
  showOffline(at);
  return result;
}

export async function call(action, payload = {}) {
  let res;
  try {
    res = await fetch(`/api/actions/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    return fromDevice(action, payload);  // no network
  }
  if (SERVER_DOWN.has(res.status) && READ_ONLY.has(action)) return fromDevice(action, payload);
  if (res.status === 401) { location.href = "/login"; throw new Error("Login required"); }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (SERVER_DOWN.has(res.status)) throw new Error("The server is not reachable: try again in a moment.");
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d) => `${(d.loc || []).join(".")}: ${d.msg}`).join("; ")
      : body.detail;
    throw new Error(detail || `${action} failed (${res.status})`);
  }
  hideOffline();
  save(action, payload, body.result);
  if (body.notice) toast(body.notice);  // e.g. OpenAI quota reached, switched to Claude
  return body.result;
}

// ------------------------------------------------------------------ offline banner
let banner = null;
function showOffline(at) {
  const when = new Date(at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  if (!banner) {
    banner = h("div", { class: "offline-banner", role: "status" });
    (document.querySelector(".topbar") || document.body.firstChild).after(banner);  // right under the top bar
  }
  banner.replaceChildren(h("span", {}, `Offline · read only · saved ${when}`),
    h("button", { class: "small", onclick: () => window.dispatchEvent(new HashChangeEvent("hashchange")) }, "Retry"));
  document.body.classList.add("is-offline");
}
function hideOffline() {
  if (!banner) return;
  banner.remove();
  banner = null;
  document.body.classList.remove("is-offline");
}

// ------------------------------------------------------- save everything for offline
// In the background, at most every 15 minutes: the lists, then each application (prep, recordings, practice),
// the CV and the latest chats, exactly as the pages ask for them (same payloads = same saved entries).
const SYNC_KEY = "offline.lastSync";
export async function saveForOffline({ force = false } = {}) {
  let last = 0;
  try { last = Number(localStorage.getItem(SYNC_KEY) || 0); } catch { /* ignore */ }
  if (!force && Date.now() - last < 15 * 60 * 1000) return null;
  const quiet = (action, payload = {}) => call(action, payload).catch(() => null);
  const [companies, chats] = await Promise.all([quiet("company.list"), quiet("chat.list"),
    quiet("app.info"), quiet("today.get"), quiet("practice.profile"), quiet("practice.history"), quiet("cv.master.get"),
    quiet("usage.report", { days: 30 }), quiet("usage.averages"), quiet("analytics.summary")]);
  if (!companies) return null;  // offline: keep the previous copy
  for (const c of companies) {
    await Promise.all([quiet("company.get", { id: c.id }), quiet("prep.get", { company: c.id }),
      quiet("practice.history", { company: c.id }), quiet("recording.list", { company: c.id })]);
  }
  for (const c of (chats || []).slice(0, 10)) await quiet("chat.get", { id: c.id });
  try { localStorage.setItem(SYNC_KEY, String(Date.now())); } catch { /* ignore */ }
  return { applications: companies.length, at: new Date() };
}

export function lastOfflineSave() {
  try { const t = Number(localStorage.getItem(SYNC_KEY) || 0); return t ? new Date(t) : null; } catch { return null; }
}

export const fileUrl = (path) => `/files/${path.split("/").map(encodeURIComponent).join("/")}`;

// Multipart upload to a non-action endpoint (files can't travel in JSON actions).
export async function upload(url, file) {
  const form = new FormData();
  form.append("file", file);
  let res;
  try { res = await fetch(url, { method: "POST", body: form }); } catch { throw new Error("You're offline: uploading needs the server."); }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Upload failed (${res.status})`);
  return body.result;
}
