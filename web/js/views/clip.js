// "Send to Ezri": a job read in your own browser (bookmark on a computer, Share → Ezri on a phone) arrives here,
// in the address's #fragment (never sent to the server). Nothing happens until you confirm.
import { call } from "../api.js";
import { askAssistant, h, mount } from "../ui.js";

export function decodeClip(data) {
  try {
    const json = decodeURIComponent(escape(atob(data.replace(/-/g, "+").replace(/_/g, "/"))));
    const c = JSON.parse(json);
    return { url: String(c.u || ""), title: String(c.t || ""), text: String(c.x || ""), greenhouse: String(c.g || "") };
  } catch {
    return null;
  }
}

export async function renderClip(root, params) {
  const clip = decodeClip(params.get("d") || "");
  let host = "";
  try { host = clip?.url ? new URL(clip.url).hostname.replace(/^www\./, "") : ""; } catch { /* not a URL */ }
  if (!clip || (!clip.text.trim() && !clip.url)) {
    return mount(root, h("div", { class: "card empty" }, h("h2", {}, "Nothing to add"),
      h("p", { class: "muted" }, "This link doesn't contain a job. Use “Send to Ezri” from a job page, or paste the job in the assistant.")));
  }
  const add = async () => {
    if (clip.greenhouse && clip.url) {  // the page told us its job board: next links from this site go through its API
      await call("job.remember_board", { url: clip.url, platform: "greenhouse", account: clip.greenhouse }).catch(() => null);
    }
    const source = clip.url ? ` It comes from ${clip.url}` : "";
    askAssistant(clip.text.trim()
      ? `Add this job and make my CV.${source} (read in my browser):\n\n${clip.title ? `${clip.title}\n\n` : ""}${clip.text.trim()}`
      : `Add this job and make my CV: ${clip.url}`);
  };
  mount(root,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Add this job?"),
      h("div", { class: "sub muted" }, host ? `Sent from ${host}` : "Sent from your browser"))),
    h("div", { class: "card stack-tight clip" },
      clip.title && h("h2", { dir: "auto", style: "margin:0" }, clip.title),
      clip.url && h("a", { class: "small", href: clip.url, target: "_blank", rel: "noopener" }, clip.url.slice(0, 90)),
      clip.text && h("pre", { class: "transcript", dir: "auto" }, clip.text.slice(0, 1500) + (clip.text.length > 1500 ? "…" : "")),
      h("p", { class: "small muted" }, "The assistant will create the application from this text, save the job description and make your tailored CV."),
      h("div", { class: "sheet-actions" },
        h("a", { class: "btn", href: "#/today" }, "Cancel"),
        h("button", { class: "primary", onclick: add }, "Add the job & make my CV"))));
}

/** The "Send to Ezri" bookmarklet for this server: reads the page you are on (your browser, your login) and
 *  opens Ezri with it. The job text, title, link and any Greenhouse account travel in the #fragment. */
export function bookmarklet() {
  const origin = location.origin;
  const code = `(()=>{try{const ld=[...document.querySelectorAll('script[type="application/ld+json"]')].map(s=>{try{return JSON.parse(s.textContent)}catch(e){return null}});`
    + `const all=ld.flatMap(d=>Array.isArray(d)?d:(d&&d['@graph'])||[d]).filter(Boolean);`
    + `const job=all.find(d=>[].concat(d['@type']).includes('JobPosting'));`
    + `const div=document.createElement('div');div.innerHTML=job&&job.description?String(job.description):'';`
    + `const text=(div.innerText||document.body.innerText||'').replace(/\\n{3,}/g,'\\n\\n').slice(0,15000);`
    + `const gh=(document.documentElement.innerHTML.match(/greenhouse\\.io\\/embed\\/job_(?:board(?:\\/js)?|app)\\?for=([A-Za-z0-9_-]+)/)||[])[1]||'';`
    + `const d=btoa(unescape(encodeURIComponent(JSON.stringify({u:location.href,t:(job&&job.title)||document.title,x:text,g:gh})))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');`
    + `window.open('${origin}/#/clip?d='+d,'_blank')}catch(e){alert('Send to Ezri: '+e.message)}})()`;
  return `javascript:${encodeURI(code)}`;
}
