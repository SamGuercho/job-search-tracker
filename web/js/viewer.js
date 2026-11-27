import { h, sendPdfButton, toast } from "./ui.js";

// In-app file viewer, for the app opened from the iPhone home screen. There, a link to a file (a CV's PDF, a
// debrief, a saved job description) would replace the app's window with no browser bar and no way back. The
// viewer keeps the app around it: a ‹ Back bar, a Send button (share sheet: Files, Books, Mail…), and the file.
// A PDF shows as one image per page, rendered by the server: an iPhone shows only the first page of a PDF
// placed inside a page. Back, the Escape key and the back-swipe history step all close it.

let current = null;  // the open viewer's close function

export function openFile(href) {
  if (current) current({ fromHistory: false });
  const url = new URL(href, location.origin);
  const path = url.pathname;                       // /files/Nimbus/Dana_CV.pdf
  const name = decodeURIComponent(path.split("/").pop());
  const ext = name.includes(".") ? name.split(".").pop().toLowerCase() : "";
  const body = h("div", { class: "viewer-body" });
  const send = ext === "pdf" ? sendPdfButton(path, "btn small") : sendFileButton(path, name);
  const back = h("button", { class: "ghost viewer-back", "aria-label": "Back" }, "‹ Back");
  const box = h("div", { class: "viewer", role: "dialog", "aria-modal": "true", "aria-label": name },
    h("div", { class: "viewer-bar" }, back, h("span", { class: "viewer-title", dir: "auto" }, name), send),
    body);

  const onKey = (e) => { if (e.key === "Escape") history.back(); };
  const onPop = () => close({ fromHistory: true });
  function close({ fromHistory }) {
    if (current !== close) return;
    current = null;
    box.remove();
    document.body.classList.remove("viewer-open");
    document.removeEventListener("keydown", onKey);
    window.removeEventListener("popstate", onPop);
    if (!fromHistory) history.back();  // drop the step added on opening
  }
  back.addEventListener("click", () => history.back());
  history.pushState({ viewer: true }, "");  // so the back gesture closes the viewer instead of leaving the page
  window.addEventListener("popstate", onPop);
  document.addEventListener("keydown", onKey);
  current = close;
  document.body.classList.add("viewer-open");
  document.body.append(box);
  fill(body, path, ext);
}

async function fill(body, path, ext) {
  if (ext === "pdf") {
    body.append(h("p", { class: "muted small viewer-note" }, "Loading…"));
    let pages = 0;
    try {
      const res = await fetch(`/api/file-pages${path.slice("/files".length)}`);
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Error ${res.status}`);
      pages = (await res.json()).pages;
    } catch (e) {
      body.replaceChildren(h("p", { class: "small viewer-note" }, `This PDF can't be shown: ${e.message}. Send opens it in another app.`));
      return;
    }
    const width = Math.min(1600, Math.round(Math.min(window.innerWidth, 900) * (window.devicePixelRatio || 2)));
    body.replaceChildren(...Array.from({ length: pages }, (_, i) =>
      h("img", { class: "viewer-page", src: `${path}?page=${i + 1}&width=${width}`, alt: `Page ${i + 1} of ${pages}`,
        loading: i ? "lazy" : "eager" })));
  } else if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) {
    body.append(h("img", { class: "viewer-page", src: path, alt: "" }));
  } else if (["md", "txt", "html", "htm"].includes(ext)) {
    // sandboxed: a saved web page shows, but nothing in it runs
    body.append(h("iframe", { class: "viewer-frame", src: path, sandbox: "", title: "File" }));
  } else {
    body.append(h("p", { class: "small viewer-note" }, "This file can't be shown here: Send opens it in another app (Files, Word…)."));
  }
}

// Send for any file (sendPdfButton is for PDFs): the share sheet with the file itself
function sendFileButton(path, name) {
  const btn = h("button", { class: "btn small" }, "Send");
  btn.addEventListener("click", async () => {
    try {
      const res = await fetch(path);
      if (!res.ok) throw new Error("File not found");
      const blob = await res.blob();
      const file = new File([blob], name, { type: blob.type || "application/octet-stream" });
      if (!navigator.canShare?.({ files: [file] })) return toast("This browser can't send files", true);
      await navigator.share({ files: [file], title: name });
    } catch (e) {
      if (e.name !== "AbortError") toast(e.message, true);
    }
  });
  return btn;
}

/** On the iPhone home-screen app, links to files open in the viewer instead of replacing the app's window. */
export function useViewerForFiles() {
  document.addEventListener("click", (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey) return;
    const a = e.target.closest?.("a[href]");
    if (!a) return;
    const url = new URL(a.getAttribute("href"), location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith("/files/") || url.searchParams.has("page")) return;
    e.preventDefault();
    openFile(url.pathname);
  });
}
