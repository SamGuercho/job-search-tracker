// Hash router: #/  ·  #/company/<id>  ·  #/cv[?company=<id>]  ·  #/cv/master  ·  #/chat[/<id>][?q=…]  ·  #/analytics
import { renderTracker } from "./views/tracker.js";
import { renderCompany } from "./views/company.js";
import { renderCvBuilder } from "./views/cv.js";
import { renderMasterCv } from "./views/master.js";
import { renderChat } from "./views/chat.js";
import { renderAnalytics } from "./views/analytics.js";
import { h, mount } from "./ui.js";

const routes = [
  { pattern: /^\/$/, nav: "tracker", view: () => renderTracker },
  { pattern: /^\/company\/([\w-]+)$/, nav: "tracker", view: () => renderCompany },
  { pattern: /^\/cv$/, nav: "cv", view: () => renderCvBuilder },
  { pattern: /^\/cv\/master$/, nav: "master", view: () => renderMasterCv },
  { pattern: /^\/chat(?:\/([\w-]+))?$/, nav: "chat", view: () => renderChat },
  { pattern: /^\/analytics$/, nav: "analytics", view: () => renderAnalytics },
];

async function route() {
  const [path, query = ""] = (location.hash.slice(1) || "/").split("?");
  const root = document.getElementById("app");
  const match = routes.map((r) => ({ r, m: path.match(r.pattern) })).find((x) => x.m);
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === match?.r.nav));
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

// Installable app: register the service worker (needs https, or localhost)
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
