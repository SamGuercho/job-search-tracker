// Hash router: #/  ·  #/company/<id>  ·  #/cv[?company=<id>]  ·  #/cv/master  ·  #/chat[/<id>][?q=…]  ·  #/analytics  ·  #/practice  ·  #/costs
import { renderTracker } from "./views/tracker.js";
import { renderCompany } from "./views/company.js";
import { renderCvBuilder } from "./views/cv.js";
import { renderMasterCv } from "./views/master.js";
import { renderChat } from "./views/chat.js";
import { renderAnalytics } from "./views/analytics.js";
import { renderPractice } from "./views/practice.js";
import { renderCosts } from "./views/costs.js";
import { h, modal, mount } from "./ui.js";

const routes = [
  { pattern: /^\/$/, nav: "tracker", view: () => renderTracker },
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

// Phones: "More" in the tab bar lists the pages that have no tab
document.querySelector(".tab-more")?.addEventListener("click", () => {
  const pages = [["#/cv/master", "Master CV", "Your achievements and their wordings"],
    ["#/analytics", "Analytics", "How your search is going"], ["#/costs", "Costs", "What the AI features cost"]];
  const close = modal("More", h("div", { class: "more-list" }, pages.map(([href, label, note]) =>
    h("a", { href, onclick: () => close() }, h("b", {}, label), h("span", { class: "small muted" }, note)))));
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
