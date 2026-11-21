// Assistant: conversations with the LLM agent that drives the app's actions.
import { call, fileUrl } from "../api.js";
import { attempt, h, mount } from "../ui.js";

const LABELS = {
  "company.list": "Looked at your companies", "company.get": "Opened company", "company.create": "Added company",
  "company.update": "Updated company", "company.delete": "Delete company", "company.log_update": "Logged a follow-up",
  "application.find": "Looked up the application", "process.get": "Read the process",
  "process.add_step": "Added a process step", "process.add_branch": "Added a branch", "process.update_step": "Updated a step",
  "process.set_state": "Updated a step", "process.close": "Closed the application", "process.reopen": "Reopened the application",
  "process.delete_step": "Delete a process step", "process.reset": "Reset the process",
  "cv.master.get": "Read your master CV", "cv.keywords.match_job": "Matched the job description with your CV",
  "cv.generate": "Generated a CV", "cv.preview": "Previewed CV bullets", "job.fetch_url": "Read the job posting",
  "workflow.new_application": "Created the application", "cv.bullet.add": "Add an achievement", "cv.bullet.update": "Update an achievement",
  "cv.variant.add": "Add a wording", "cv.variant.update": "Update a wording", "cv.variant.delete": "Delete a wording",
  "cv.variant.draft": "Drafted wordings", "cv.keywords.missing": "Checked keywords", "chat.set_title": "Renamed the conversation",
};
const label = (a) => LABELS[a] || a;

export async function renderChat(root, chatId, params) {
  const info = await call("app.info");
  let chats = await call("chat.list");
  let chat = chatId ? await call("chat.get", { id: chatId }) : null;
  let busy = false;

  const list = h("div", { class: "chat-list" });
  const head = h("div", { class: "chat-head" });
  const drawHead = () => mount(head, chat && [
    h("h2", { style: "margin:0" }, chat.title),
    h("button", {
      class: "ghost icon", title: "Rename this conversation", onclick: async () => {
        const title = prompt("Conversation title", chat.title);
        if (!title?.trim()) return;
        const renamed = await attempt(() => call("chat.rename", { id: chat.id, title }), "Renamed");
        if (renamed) { chat = renamed; chats = await call("chat.list"); drawList(); drawHead(); }
      },
    }, "✎")]);
  const thread = h("div", { class: "chat-thread" });
  const input = h("textarea", {
    rows: 2, placeholder: "Ask anything: “Add this job: <link>”, “Who needs a follow-up?”, “Make a CV for Nimbus”…",
    onkeydown: (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } },
  });
  const sendBtn = h("button", { class: "primary", onclick: () => send() }, "Send");

  const drawList = () => mount(list,
    h("a", { class: "btn", href: "#/chat", style: "justify-content:center" }, "+ New chat"),
    chats.map((c) => h("a", { class: `chat-item ${chat?.id === c.id ? "active" : ""}`, href: `#/chat/${c.id}` }, c.title)));

  const drawThread = (pendingText) => {
    const msgs = chat?.messages || [];
    mount(thread,
      !msgs.length && !pendingText && h("div", { class: "empty" },
        h("h2", {}, "What can I do for you?"),
        h("div", { class: "chips", style: "justify-content:center" },
          ["Which companies need a follow-up?", "Add this job and make my CV: <paste the job description>", "What are my gaps for the last job I added?"]
            .map((q) => h("button", { class: "chip suggest", onclick: () => { input.value = q; input.focus(); } }, q)))),
      msgs.map(message),
      pendingText && [bubble("user", pendingText), h("div", { class: "msg assistant thinking" }, "Working on it…")]);
    thread.scrollTop = thread.scrollHeight;
  };

  const bubble = (role, text) => h("div", { class: `msg ${role}` }, role === "assistant" ? markdown(text) : text);

  const message = (m) => (m.role === "tool" ? toolEvent(m.tool) : bubble(m.role, m.text));

  const toolEvent = (t) => {
    const icon = { done: "✓", approved: "✓", error: "⚠", rejected: "✕", pending: "?" }[t.status];
    if (t.status === "pending") {
      return h("div", { class: "tool pending" },
        h("div", {}, h("b", {}, "Approval needed: "), label(t.action)),
        // Text going into the CV is shown as text so it can be read before approving
        (t.args.text || t.args.fields?.text) && h("blockquote", {},
          (t.args.lead || t.args.fields?.lead) && h("b", {}, `${t.args.lead || t.args.fields.lead} `), t.args.text || t.args.fields.text),
        h("details", {}, h("summary", { class: "small muted" }, "Details"), h("pre", {}, JSON.stringify(t.args, null, 2))),
        h("div", { class: "row" },
          h("button", { class: "primary", onclick: () => resolve(t.id, true) }, "Approve"),
          h("button", { onclick: () => resolve(t.id, false) }, "Reject")));
    }
    const detail = t.action === "chat.set_title" ? ` to “${t.args.title}”` : "";
    return h("div", { class: `tool ${t.status}`, title: t.error || JSON.stringify(t.args) },
      `${icon} ${t.status === "approved" ? "Approved: " : ""}${label(t.action)}${detail}${t.status === "rejected" ? " (rejected)" : ""}`, links(t.result), t.error && h("span", { class: "small" }, ` · ${t.error}`));
  };

  // Shortcuts from tool results: open the company, download the CV.
  const links = (r) => {
    if (!r || typeof r !== "object") return null;
    const company = r.company && typeof r.company === "object" ? r.company : r.name && r.id && r.position !== undefined ? r : null;
    const cv = r.cv || (r.docx ? r : null);
    return [
      company && h("a", { href: `#/company/${company.id}` }, ` · ${company.name}`),
      cv?.docx && h("a", { href: fileUrl(cv.docx) }, " · Word"),
      cv?.pdf && h("a", { href: fileUrl(cv.pdf), target: "_blank" }, " · PDF"),
    ];
  };

  async function run(fn, pendingText) {
    if (busy) return;
    busy = true;
    sendBtn.disabled = true;
    drawThread(pendingText);
    const result = await attempt(fn);
    busy = false;
    sendBtn.disabled = false;
    if (result) {
      const isNew = !chat;
      chat = result;
      chats = await call("chat.list");
      if (isNew) history.replaceState(null, "", `#/chat/${chat.id}`);
      drawList();
      drawHead();
    }
    drawThread();
    input.focus();
  }

  const send = (text = input.value.trim()) => {
    if (!text) return;
    input.value = "";
    run(() => call("chat.send", { message: text, ...(chat ? { chat_id: chat.id } : {}) }), text);
  };
  const resolve = (eventId, approve) => run(() => call("chat.resolve", { chat_id: chat.id, event_id: eventId, approve }));

  mount(root,
    h("div", { class: "chat-layout" },
      h("aside", {}, list),
      h("section", { class: "card chat-main" },
        !info.llm_available && h("div", { class: "result warn card" }, "Add OPENAI_API_KEY (or ANTHROPIC_API_KEY) to .env and restart the server to use the assistant."),
        head,
        thread,
        h("div", { class: "chat-input" }, input, sendBtn))));
  drawList();
  drawHead();
  drawThread();

  const q = params.get("q");
  if (q && !chat) send(q);
  else input.focus();
}

// Minimal, safe markdown: headings, paragraphs, "- " lists, **bold**, `code`, [text](#/route, /files/… or https://url).
function markdown(text) {
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
  for (const line of text.split("\n")) {
    const li = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)/);
    if (li) { (items ??= []).push(h("li", {}, inline(li[1]))); continue; }
    if (items) { blocks.push(h("ul", {}, items)); items = null; }
    const heading = line.match(/^\s*#{1,6}\s+(.*)/);
    if (heading) blocks.push(h("p", { class: "md-h" }, inline(heading[1])));
    else if (line.trim()) blocks.push(h("p", {}, inline(line)));
  }
  if (items) blocks.push(h("ul", {}, items));
  return blocks;
}
