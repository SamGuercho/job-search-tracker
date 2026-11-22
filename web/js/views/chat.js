// Assistant: conversations with the LLM agent that drives the app's actions.
import { call, fileUrl } from "../api.js";
import { attempt, chatRequest, confirmSheet, h, markdown, mount, promptSheet, toast, usd } from "../ui.js";

const LABELS = {
  "company.list": "Looked at your companies", "company.get": "Opened company", "company.create": "Added company",
  "company.update": "Updated company", "company.delete": "Delete company", "company.log_update": "Logged a follow-up",
  "application.find": "Looked up the application", "process.get": "Read the process",
  "process.add_step": "Added a process step", "process.add_branch": "Added a branch", "process.update_step": "Updated a step",
  "process.set_state": "Updated a step", "process.close": "Closed the application", "process.reopen": "Reopened the application",
  "process.delete_step": "Delete a process step", "process.reset": "Reset the process",
  "cv.master.get": "Read your master CV", "cv.keywords.match_job": "Matched the job description with your CV", "cv.match_score": "Scored the CV against the job",
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
  const refreshList = async () => { chats = await call("chat.list"); drawList(); drawMobileBar(); };
  const drawHead = () => mount(head, chat && [
    h("h2", { style: "margin:0", dir: "auto" }, chat.title),
    chat.archived && h("span", { class: "pill" }, "Archived"),
    h("span", { class: "spacer" }),
    canCompact() && h("button", {
      class: "ghost small", title: "The assistant continues from a short summary: cheaper, faster replies. Messages stay here. (or type /compact)",
      onclick: () => compact(),
    }, "Compact"),
    h("button", {
      class: "ghost icon", title: "Rename this conversation", "aria-label": "Rename", onclick: async () => {
        const title = await promptSheet("", chat.title, { title: "Rename this conversation", ok: "Rename" });
        if (!title?.trim()) return;
        const renamed = await attempt(() => call("chat.rename", { id: chat.id, title }), "Renamed");
        if (renamed) { chat = renamed; await refreshList(); drawHead(); }
      },
    }, "✎"),
    h("button", {
      class: "ghost icon", title: chat.archived ? "Bring back to the list" : "Archive (keep it, out of the list)",
      "aria-label": chat.archived ? "Unarchive" : "Archive", onclick: async () => {
        const updated = await attempt(() => call("chat.archive", { id: chat.id, archived: !chat.archived }),
          chat.archived ? "Back in your conversations" : "Archived");
        if (updated) { chat = updated; await refreshList(); drawHead(); }
      },
    }, chat.archived ? "↩" : "🗄"),
    h("button", {
      class: "ghost icon", title: "Delete this conversation", "aria-label": "Delete", onclick: async () => {
        if (!(await confirmSheet("Its messages are deleted for good. What the assistant did (applications, CVs…) stays.",
          { title: "Delete this conversation?", ok: "Delete", danger: true }))) return;
        if (await attempt(() => call("chat.delete", { id: chat.id }), "Conversation deleted")) location.hash = "#/chat";
      },
    }, "🗑")]);
  const thread = h("div", { class: "chat-thread" });
  const input = h("textarea", {
    rows: 2, placeholder: "Ask anything: “Add this job: <link>”, “Who needs a follow-up?”, “Make a CV for Nimbus”…",
    onkeydown: (e) => {
      if (e.key !== "Enter" || e.isComposing) return;          // typing with an input method (e.g. Hebrew, emoji)
      if (e.shiftKey || touchKeyboard) return;                 // new line (phones: tap Send to send)
      e.preventDefault();
      if (e.altKey) {                                          // Alt/Option + Enter: new line too
        input.setRangeText("\n", input.selectionStart, input.selectionEnd, "end");
        return;
      }
      send();
    },
  });
  const touchKeyboard = matchMedia("(pointer: coarse)").matches && !matchMedia("(pointer: fine)").matches;
  const keysHint = h("div", { class: "keys-hint small muted" }, touchKeyboard
    ? "Return adds a new line · tap Send to send"
    : "Enter to send · Shift+Enter for a new line");
  const sendBtn = h("button", { class: "primary", onclick: () => send() }, "Send");

  // Phones: the list replaces the conversation (no room for both); a "Chats" button switches between them.
  const layout = h("div", { class: "chat-layout" });
  const toggleList = () => layout.classList.toggle("show-list");
  const active = () => chats.filter((c) => !c.archived);
  const mobileBar = h("div", { class: "chat-mobile-bar" });
  const drawMobileBar = () => mount(mobileBar,
    h("button", { class: "ghost", onclick: toggleList }, `☰ Chats${active().length ? ` (${active().length})` : ""}`),
    h("a", { class: "btn ghost", href: "#/chat" }, "+ New"));
  drawMobileBar();

  const drawList = () => mount(list,
    h("button", { class: "ghost chat-list-close", onclick: toggleList }, "← Back to the conversation"),
    h("a", { class: "btn", href: "#/chat", style: "justify-content:center" }, "+ New chat"),
    active().map(item),
    chats.some((c) => c.archived) && h("details", { class: "chat-archived", open: Boolean(chat?.archived) },
      h("summary", { class: "small muted" }, `Archived (${chats.filter((c) => c.archived).length})`),
      chats.filter((c) => c.archived).map(item)));
  const item = (c) => h("a", {
    class: `chat-item ${chat?.id === c.id ? "active" : ""}`, href: `#/chat/${c.id}`, dir: "auto",
    onclick: () => layout.classList.remove("show-list"),  // same chat: no navigation, just close the list
  }, c.title);

  const canCompact = () => Boolean(chat) && chat.messages.slice(chat.history_from || 0).some((m) => (m.role === "user" || m.role === "assistant") && m.text);
  const compactTip = h("div", { class: "compact-tip small" });
  const drawTip = () => mount(compactTip, chat?.compact_suggested && canCompact() && [
    h("span", {}, "This conversation is long: every message re-sends all of it. Compact it for cheaper, faster answers (the messages stay here)."),
    h("button", { class: "small", onclick: () => compact() }, "Compact")]);

  const drawThread = (pendingText, working = "Working on it…") => {
    const msgs = chat?.messages || [];
    const lastCompaction = chat?.summary ? (chat.history_from || 0) - 1 : -1;  // the notice that ends the compacted part
    mount(thread,
      !msgs.length && !pendingText && h("div", { class: "empty" },
        h("h2", {}, "What can I do for you?"),
        h("div", { class: "chips", style: "justify-content:center" },
          ["Which companies need a follow-up?", "Add this job and make my CV: <paste the job description>", "What are my gaps for the last job I added?"]
            .map((q) => h("button", { class: "chip suggest", onclick: () => { input.value = q; input.focus(); } }, q)))),
      msgs.map((m, i) => [message(m), i === lastCompaction && h("details", { class: "compact-summary" },
        h("summary", { class: "small muted" }, "What the assistant remembers"), h("div", { dir: "auto" }, markdown(chat.summary)))]),
      pendingText && bubble("user", pendingText),
      (pendingText || busy) && h("div", { class: "msg assistant thinking" }, working));
    drawTip();  // before scrolling: the tip changes the thread's height
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

  async function run(fn, pendingText, working) {
    if (busy) return;
    busy = true;
    sendBtn.disabled = true;
    drawThread(pendingText, working);
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
    } else if (pendingText && !input.value.trim()) {
      input.value = pendingText;  // not sent (offline, no AI key…): give the message back instead of losing it
    }
    drawThread();
    input.focus();
  }

  const compact = () => chat && run(() => call("chat.compact", { id: chat.id }), null, "Summarising the conversation…");

  const send = (text = input.value.trim()) => {
    if (!text) return;
    if (text.toLowerCase() === "/compact") {  // chat command
      if (!canCompact()) { toast("Nothing to compact yet"); return; }
      input.value = "";
      compact();
      return;
    }
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
      compactTip,
      h("div", { class: "chat-input" }, input, sendBtn),
      keysHint));
  mount(root, layout);
  drawList();
  drawHead();
  drawThread();

  const request = chatRequest(params);
  if (request.text && !chat && request.send) send(request.text);
  else {
    if (request.text && !chat) input.value = request.text;  // from outside the app: you review it and press Send
    input.focus();
  }
}
