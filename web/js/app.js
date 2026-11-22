// Hash router: #/  ·  #/company/<id>  ·  #/cv[?company=<id>]  ·  #/cv/master  ·  #/today  ·  #/chat[/<id>][?q=…]  ·  #/analytics  ·  #/practice  ·  #/costs
import { renderTracker } from "./views/tracker.js";
import { renderCompany } from "./views/company.js";
import { renderCvBuilder } from "./views/cv.js";
import { renderMasterCv } from "./views/master.js";
import { renderChat } from "./views/chat.js";
import { renderAnalytics } from "./views/analytics.js";
import { renderPractice } from "./views/practice.js";
import { renderCosts } from "./views/costs.js";
import { renderToday } from "./views/today.js";
import { bookmarklet, renderClip } from "./views/clip.js";
import { call, lastOfflineSave, saveForOffline } from "./api.js";
import { attempt, h, modal, mount, toast } from "./ui.js";

const routes = [
  { pattern: /^\/$/, nav: "tracker", view: () => renderTracker },
  { pattern: /^\/today$/, nav: "today", view: () => renderToday },
  { pattern: /^\/clip$/, nav: "tracker", view: () => (root, params) => renderClip(root, params) },
  { pattern: /^\/company\/([\w-]+)$/, nav: "tracker", view: () => renderCompany },
  { pattern: /^\/cv$/, nav: "cv", view: () => renderCvBuilder },
  { pattern: /^\/cv\/master$/, nav: "master", view: () => renderMasterCv },
  { pattern: /^\/chat(?:\/([\w-]+))?$/, nav: "chat", view: () => renderChat },
  { pattern: /^\/analytics$/, nav: "analytics", view: () => renderAnalytics },
  { pattern: /^\/practice$/, nav: "practice", view: () => renderPractice },
  { pattern: /^\/costs$/, nav: "costs", view: () => renderCosts },
];

async function route() {
  const [path, query = ""] = (location.hash.slice(1) || "/").split("?");
  const root = document.getElementById("app");
  const match = routes.map((r) => ({ r, m: path.match(r.pattern) })).find((x) => x.m);
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === match?.r.nav));
  document.querySelectorAll("[data-nav-more]").forEach((b) => b.classList.toggle("active", b.dataset.navMore.split(" ").includes(match?.r.nav)));
  if (!match) return mount(root, h("div", { class: "empty" }, "Page not found. ", h("a", { href: "#/" }, "Back to follow-up")));
  try {
    await match.r.view()(root, ...match.m.slice(1), new URLSearchParams(query));
  } catch (e) {
    mount(root, h("div", { class: "empty" }, `Error: ${e.message}`));
  }
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", route);
route();

// Which version of the app runs (the commit CI built it from): to know where to go back to if needed
const versionLine = () => {
  const line = h("div", { class: "app-version small muted" });
  call("app.info").then(({ version: v }) => {
    if (!v?.commit) return;
    line.textContent = `Version ${v.commit}${v.commit_message ? ` · ${v.commit_message}` : ""}${v.built_at ? ` · ${v.built_at.slice(0, 10)}` : ""}`;
  }).catch(() => {});
  return line;
};

// Phones: "More" in the tab bar lists the pages that have no tab
document.querySelector(".tab-more")?.addEventListener("click", () => {
  const pages = [["#/cv", "CV builder", "A tailored CV for an application"], ["#/cv/master", "Master CV", "Your achievements and their wordings"],
    ["#/analytics", "Analytics", "How your search is going"], ["#/costs", "Costs", "What the AI features cost"]];
  const close = modal("More", h("div", {}, h("div", { class: "more-list" }, pages.map(([href, label, note]) =>
    h("a", { href, onclick: () => close() }, h("b", {}, label), h("span", { class: "small muted" }, note))),
    h("a", { href: `webcal://${location.host}/api/calendar/interviews.ics`, onclick: () => close() },
      h("b", {}, "Subscribe to my interviews"), h("span", { class: "small muted" }, "A calendar of all your scheduled interviews, kept up to date (Apple Calendar, Outlook)"))),
  versionLine()));
});

// Pull down at the top of a page to reload it (installed apps have no reload button). A touch that starts
// inside a scrolled list (e.g. the chat) scrolls that list instead.
const pullHint = h("div", { class: "pull-hint", "aria-hidden": "true" }, "↻");
document.body.append(pullHint);
let pullStart = null;
const scrolledInside = (el) => {
  for (; el && el !== document.body; el = el.parentElement) if (el.scrollTop > 0) return true;
  return false;
};
window.addEventListener("touchstart", (e) => {
  pullStart = window.scrollY <= 0 && !document.querySelector(".modal-back") && !scrolledInside(e.target) ? e.touches[0].clientY : null;
}, { passive: true });
window.addEventListener("touchmove", (e) => {
  if (pullStart === null) return;
  const distance = e.touches[0].clientY - pullStart;
  pullHint.classList.toggle("show", distance > 20);
  pullHint.classList.toggle("ready", distance > 80);
  pullHint.style.setProperty("--pull", `${Math.min(Math.max(distance, 0), 120) / 2}px`);
}, { passive: true });
window.addEventListener("touchend", () => {
  if (pullHint.classList.contains("ready")) route();
  pullHint.classList.remove("show", "ready");
  pullStart = null;
});

// Installable app: register the service worker (needs https, or localhost)
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});


// ----------------------------------------------------------- notifications & calendar
// 🔔 in the top bar: turn notifications on for this device, test them, subscribe to the interviews calendar.
const pushSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent);
const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

function keyBytes(b64) {
  const s = atob((b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

function deviceName() {
  const ua = navigator.userAgent;
  const os = /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iPhone" : /Mac/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : "Device";
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "";
  return `${os}${browser ? ` · ${browser}` : ""}${standalone ? " (app)" : ""}`;
}

async function notificationsPanel() {
  const box = h("div", { class: "stack-tight" });
  const draw = async () => {
    if (!pushSupported) {
      return mount(box, h("p", { class: "small" }, isIOS && !standalone
        ? "On iPhone, notifications work in the installed app: Share → Add to Home Screen, then open Ezri from the Home Screen."
        : "This browser can't receive notifications."));
    }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub && Notification.permission === "granted") {
      return mount(box,
        h("p", { class: "small" }, "✓ On for this device: interview reminders (the day before and 1 hour before), prep and debrief alerts, follow-ups due."),
        h("div", { class: "row" },
          h("button", { onclick: () => attempt(() => call("push.test"), "Test sent") }, "Send a test"),
          h("button", {
            onclick: async () => { await attempt(() => call("push.unsubscribe", { endpoint: sub.endpoint })); await sub.unsubscribe(); draw(); },
          }, "Turn off")));
    }
    mount(box,
      h("p", { class: "small" }, "Interview reminders (the day before and 1 hour before), “prep ready” and “debrief ready”, and follow-ups due."),
      Notification.permission === "denied"
        ? h("p", { class: "small muted" }, "Notifications are blocked for this app: allow them in the browser or phone settings, then come back.")
        : h("button", {
          class: "primary", onclick: async () => {
            if ((await Notification.requestPermission()) !== "granted") return toast("Notifications were not allowed", true);
            const { public_key: key } = await call("push.key");
            const created = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
            await attempt(() => call("push.subscribe", {
              subscription: created.toJSON(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, device: deviceName(),
            }), "Notifications on");
            draw();
          },
        }, "Turn on notifications"));
  };
  await draw();
  return box;
}

document.querySelector(".bell")?.addEventListener("click", async () => {
  const saved = lastOfflineSave();
  const offlineInfo = h("span", { class: "small muted" }, saved
    ? `Your applications, preps, debriefs and practice are saved on this device for reading offline (last saved ${saved.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}).`
    : "Your applications, preps, debriefs and practice will be saved on this device for reading offline.");
  const sendTo = h("div", { class: "stack-tight" },
    h("b", {}, "Add jobs from any page"),
    h("span", { class: "small muted" }, "Some sites (LinkedIn, or pages that block robots) can't be read by Ezri. Send them from your own browser:"),
    isIOS || /Android/.test(navigator.userAgent)
      ? h("span", { class: "small" }, "On your phone: select the job's text → Share → Ezri (Android). On iPhone: copy the text and paste it in the assistant.")
      : [h("span", { class: "small" }, "Drag this button to your bookmarks bar, then click it on any job page:"),
        h("a", { class: "btn bookmarklet", href: bookmarklet(), onclick: (e) => { e.preventDefault(); toast("Drag it to your bookmarks bar"); } }, "➕ Send to Ezri")]);
  modal("Notifications, calendar & offline", h("div", { class: "stack" },
    await notificationsPanel(),
    sendTo,
    h("div", { class: "stack-tight" }, offlineInfo,
      h("button", {
        class: "small", onclick: async (e) => {
          e.target.disabled = true;
          e.target.textContent = "Saving…";
          const r = await saveForOffline({ force: true });
          e.target.textContent = r ? `Saved: ${r.applications} applications` : "Offline: kept the previous copy";
        },
      }, "Save now")),
    h("div", { class: "more-list" },
      h("a", { href: `webcal://${location.host}/api/calendar/interviews.ics` },
        h("b", {}, "Subscribe to my interviews"),
        h("span", { class: "small muted" }, "A calendar of all your scheduled interviews that stays up to date (Apple Calendar, Outlook). "
          + "For Google Calendar, use 📅 on each interview step."))),
    versionLine()));
});

// Follow-ups due as a badge on the app icon (Android, desktop; iPhone with notifications on)
const badge = () => call("app.info").then((i) => {
  if (!navigator.setAppBadge) return;
  i.follow_ups_due ? navigator.setAppBadge(i.follow_ups_due) : navigator.clearAppBadge();
}).catch(() => {});
badge();
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") badge(); });

// Keep a copy of your data on this device for offline reading (at most every 15 minutes, in the background)
setTimeout(() => saveForOffline(), 4000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") saveForOffline(); });
