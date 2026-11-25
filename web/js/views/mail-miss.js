// "Ezri missed an email": the user said news came by email and Ezri proposed nothing. They point at the email among
// a few likely ones; the server (reading their own mailbox) says why it was missed and offers the fix. Only the
// reason and the email's traits reach the server owner's monitor, unless the user shares the email on purpose.
import { call } from "../api.js";
import { attempt, formatDate, h, modal, mount, toast } from "../ui.js";

export function missedEmail(company) {
  const body = h("div", { class: "stack-tight miss-sheet" });
  const close = modal("Ezri missed this email", body);
  const when = (iso) => `${formatDate(iso.slice(0, 10))} ${iso.slice(11, 16)}`;

  const intro = () => mount(body,
    h("p", {}, `Ezri found nothing in your email about ${company.name}. Show it which email it was, and it will tell you why and how to catch the next ones.`),
    h("p", { class: "small muted" }, "Ezri reads your recent emails' senders and subjects on the server to list the likely ones. Nobody else sees them."),
    h("div", { class: "sheet-actions" }, h("button", { onclick: close }, "Not now"), h("button", { class: "primary", onclick: search }, "Find it")));

  const search = async () => {
    mount(body, h("p", { class: "muted" }, "Looking at your last 10 days of email…"));
    const r = await attempt(() => call("mail.find_missed", { company: company.id }));
    if (!r) return close();
    mount(body,
      h("p", {}, r.emails.length ? "Which one was it?" : `Nothing in your last ${r.searched} emails looks like it's about ${company.name}.`),
      r.emails.map((e) => h("button", { class: "miss-pick", onclick: () => diagnose(e) },
        h("b", { dir: "auto" }, e.subject || "(no subject)"),
        h("span", { class: "small muted", dir: "auto" }, `${e.sender} · ${when(e.received_at)}`),
        e.snippet && h("span", { class: "small muted", dir: "auto" }, e.snippet))),
      h("div", { class: "sheet-actions" }, h("button", { onclick: close }, r.emails.length ? "None of these" : "Close")));
  };

  const diagnose = async (e) => {
    mount(body, h("p", { class: "muted" }, "Checking why…"));
    const d = await attempt(() => call("mail.diagnose", { company: company.id, message: e.id }));
    if (!d) return close();
    const fixes = d.fixes.map((f) => {
      const input = f.type === "name" && h("input", { value: f.value, placeholder: `e.g. ${company.name} Ltd, or its name in Hebrew` });
      const apply = async (ev) => {
        const value = input ? input.value.trim() : f.value;
        if (!value) return;
        ev.target.disabled = true;
        const r = await attempt(() => call("mail.fix_filter", { company: company.id, message: e.id, ...(f.type === "name" ? { name: value } : { domain: value }) }));
        if (!r) { ev.target.disabled = false; return; }
        toast(r.works ? "Fixed: emails like this one will be caught" : "Added, but this email still doesn't match: try another name");
        if (r.works) close(); else ev.target.disabled = false;
      };
      return h("div", { class: "miss-fix" }, h("div", { class: "small" }, f.label), h("div", { class: "row" }, input, h("button", { class: "small primary", onclick: apply }, f.type === "name" ? "Add name" : `Add ${f.value}`)));
    });
    const kind = h("select", {}, h("option", { value: "" }, "What was it?"), Object.entries(d.kinds).map(([k, label]) => h("option", { value: k }, label)));
    const correct = d.reason === "model" && h("div", { class: "miss-fix" },
      h("div", { class: "small" }, "Tell Ezri what it was (only the kind is recorded, not the email):"),
      h("div", { class: "row" }, kind, h("button", { class: "small primary", onclick: async () => {
        if (!kind.value) return;
        if (await attempt(() => call("mail.correct", { company: company.id, message: e.id, kind: kind.value }), "Thanks: recorded")) close();
      } }, "Record")));
    const consent = h("input", { type: "checkbox" });
    const note = h("input", { placeholder: "Optional note: what went wrong?" });
    const share = h("details", { class: "small" }, h("summary", {}, "Share this email to help fix Ezri"),
      h("p", { class: "muted" }, "Its sender, subject and text go to the monitor of this server's owner, who can then fix the cause. Only this one email, only now."),
      note, h("label", { class: "row" }, consent, " I agree to share this email"),
      h("button", { class: "small", onclick: async () => {
        if (!consent.checked) return toast("Tick the box to share it", true);
        if (await attempt(() => call("mail.share", { message: e.id, note: note.value }), "Shared, thank you")) close();
      } }, "Share it"));
    mount(body,
      h("div", { class: "small muted", dir: "auto" }, `${d.email.subject} · ${d.email.sender}`),
      h("p", {}, h("b", {}, d.explanation)),
      d.reason === "found" && h("p", { class: "small" }, "It should be on Today under “From your email”."),
      fixes, correct,
      d.reason !== "found" && share,
      h("div", { class: "sheet-actions" }, h("button", { onclick: close }, "Done")));
  };

  intro();
}
