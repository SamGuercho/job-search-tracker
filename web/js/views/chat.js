// Assistant: conversations with the LLM agent that drives the app's actions.
import { call, fileUrl } from "../api.js";
import { attempt, h, markdown, mount, promptSheet, usd } from "../ui.js";

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
        const title = await promptSheet("", chat.title, { title: "Rename this conversation", ok: "Rename" });
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

  // Phones: the list replaces the conversation (no room for both); a "Chats" button switches between them.
  const layout = h("div", { class: "chat-layout" });
  const toggleList = () => layout.classList.toggle("show-list");
  const mobileBar = h("div", { class: "chat-mobile-bar" },
    h("button", { class: "ghost", onclick: toggleList }, `☰ Chats${chats.length ? ` (${chats.length})` : ""}`),
    h("a", { class: "btn ghost", href: "#/chat" }, "+ New"));

  const drawList = () => mount(list,
    h("button", { class: "ghost chat-list-close", onclick: toggleList }, "← Back to the conversation"),
    h("a", { class: "btn", href: "#/chat", style: "justify-content:center" }, "+ New chat"),
    chats.map((c) => h("a", {
      class: `chat-item ${chat?.id === c.id ? "active" : ""}`, href: `#/chat/${c.id}`,
      onclick: () => layout.classList.remove("show-list"),  // same chat: no navigation, just close the list
    }, c.title)));

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

  const bubble = (role, text) => h("div", { class: `msg ${role}`, dir: "auto" }, role === "assistant" ? markdown(text) : text);

  const message = (m) => (m.role === "tool" ? toolEvent(m.tool)
    : [bubble(m.role, m.text), m.cost_usd !== null && m.cost_usd !== undefined
      && h("div", { class: "msg-cost", title: "What the AI calls behind this answer cost (estimate)" }, usd(m.cost_usd))]);

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

  mount(layout,
    h("aside", {}, list),
    h("section", { class: "card chat-main" },
      mobileBar,
      !info.llm_available && h("div", { class: "result warn card" }, "Add OPENAI_API_KEY (or ANTHROPIC_API_KEY) to .env and restart the server to use the assistant."),
      head,
      thread,
      h("div", { class: "chat-input" }, input, sendBtn)));
  mount(root, layout);
  drawList();
  drawHead();
  drawThread();

  const q = params.get("q");
  if (q && !chat) send(q);
  else input.focus();
}
