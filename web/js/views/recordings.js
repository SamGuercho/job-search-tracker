// Interview recordings card (application page): upload → estimate → transcribe → pick your voice → debrief.
// Nothing is spent before "Transcribe"; each step shows its estimated cost, then what it actually cost.
import { call, fileUrl, upload } from "../api.js";
import { attempt, confirmSheet, h, markdown, mount, share, toast, usd } from "../ui.js";

const LANGUAGES = [["", "Same as the interview"], ["English", "English"], ["Hebrew", "Hebrew · עברית"], ["French", "French"]];
const STATUS = {
  uploaded: "Ready to transcribe", transcribing: "Transcribing…", choose_speaker: "Which voice is yours?",
  analyzing: "Writing the debrief…", done: "Debrief ready", error: "Failed",
};

export function recordingsCard(company, info) {
  const root = h("div", { class: "card recordings" });
  let list = [];
  let busy = false;
  const here = location.hash;

  const load = async () => {
    list = await call("recording.list", { company: company.id }).catch(() => list);
    draw();
    if (list.some((r) => r.status === "transcribing" || r.status === "analyzing")) {
      setTimeout(() => { if (location.hash === here) load(); }, 8000);  // stops when you leave the page
    }
  };

  const minutes = (s) => (s >= 60 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`);
  const total = (o) => Object.values(o || {}).reduce((a, b) => a + (b || 0), 0);

  // ---------------------------------------------------------------- upload
  const steps = company.process.nodes.filter((n) => !/^rejected|closed/i.test(n.name));
  const stepSelect = h("select", {}, h("option", { value: "" }, "Interview stage…"),
    steps.map((n) => h("option", { value: n.id, selected: n.state === "planned" }, n.name)));
  const fileInput = h("input", { type: "file", accept: "audio/*,video/*,.m4a,.mp3,.wav,.ogg,.opus,.webm,.mp4,.aac,.amr,.3gp" });
  const uploadBtn = h("button", {
    onclick: async () => {
      const file = fileInput.files[0];
      if (!file) return toast("Choose the recording first", true);
      uploadBtn.disabled = true;
      uploadBtn.textContent = "Uploading…";
      try {
        await upload(`/api/recordings/upload?company=${encodeURIComponent(company.id)}&step=${encodeURIComponent(stepSelect.value)}`, file);
        fileInput.value = "";
        toast("Uploaded: check the estimate, then transcribe");
      } catch (e) { toast(e.message, true); }
      uploadBtn.disabled = false;
      uploadBtn.textContent = "Upload";
      load();
    },
  }, "Upload");

  // ------------------------------------------------------------- one recording
  const act = async (fn, msg) => {
    if (busy) return;
    busy = true;
    await attempt(fn, msg);
    busy = false;
    load();
  };

  const header = (r) => h("div", { class: "row" },
    h("b", {}, r.step_name || "Interview"), h("span", { class: "small muted" }, ` · ${r.uploaded_at.slice(0, 10)} · ${minutes(r.duration_s)}`),
    h("span", { class: "spacer" }), h("span", { class: `pill rec-${r.status}` }, STATUS[r.status] || r.status),
    h("button", {
      class: "ghost icon", title: "Delete this recording (audio, debrief, graded answers)",
      onclick: async () => (await confirmSheet("The audio, its debrief and the answers graded from it are deleted.",
        { title: "Delete this recording?", ok: "Delete", danger: true })) && act(() => call("recording.delete", { id: r.id }), "Deleted"),
    }, "🗑"));

  const languageSelect = (value = "") => h("select", { title: "Language of the debrief" },
    LANGUAGES.map(([v, label]) => h("option", { value: v, selected: v === value }, label)));

  const costLine = (r) => {
    const est = r.estimate_usd || {};
    const spent = r.cost_usd || {};
    const parts = [`Estimate: transcription ${usd(est.transcription)} + debrief ${usd(est.analysis)}`];
    if (Object.keys(spent).length) parts.push(`spent so far: ${usd(total(spent))}`);
    return h("div", { class: "small muted" }, parts.join(" · "));
  };

  const uploaded = (r) => {
    const lang = languageSelect(r.language);
    return [costLine(r), h("div", { class: "row" }, lang,
      h("button", { class: "primary", onclick: () => act(() => call("recording.transcribe", { id: r.id, language: lang.value }), "Transcription started") },
        `Transcribe · ≈ ${usd(total(r.estimate_usd))}`))];
  };

  const chooseSpeaker = (r) => [
    h("p", { class: "small" }, "The transcription found these voices. Which one is you? (Our guess is highlighted.)"),
    r.speakers.map((s) => h("div", { class: `speaker ${s.speaker === r.me ? "guess" : ""}` },
      h("div", { class: "row" }, h("b", {}, `Voice ${s.speaker}`), h("span", { class: "small muted" }, ` · ${s.words} words`), h("span", { class: "spacer" }),
        h("button", { class: s.speaker === r.me ? "primary" : "", onclick: () => act(() => call("recording.choose_speaker", { id: r.id, me: s.speaker }), "Writing the debrief") },
          `This is me · ≈ ${usd(r.estimate_usd?.analysis)}`)),
      s.samples.map((t) => h("div", { class: "small muted sample", dir: "auto" }, `“${t}”`)))),
    costLine(r)];

  const debrief = (r) => {
    const d = r.debrief;
    const avg = d.questions.length ? (d.questions.reduce((a, q) => a + q.score, 0) / d.questions.length).toFixed(1) : null;
    const lang = languageSelect(r.language);
    const transcript = h("details", {
      ontoggle: async (e) => {
        if (!e.target.open || e.target.dataset.loaded) return;
        e.target.dataset.loaded = "1";
        const t = await call("recording.transcript", { id: r.id });
        e.target.append(h("pre", { class: "transcript", dir: "auto" }, t.text));
      },
    }, h("summary", {}, "Full transcript"));
    return h("div", { class: "stack-tight" },
      h("div", { class: "small muted" }, `Cost: ${usd(total(r.cost_usd))} (transcription ${usd(r.cost_usd?.transcription)}, debrief ${usd(r.cost_usd?.analysis)}) · estimate was ${usd(total(r.estimate_usd))}`,
        r.debrief_file && [" · ", h("a", { href: fileUrl(r.debrief_file), target: "_blank" }, "📄 Debrief")], " · ",
        h("a", { href: "#", onclick: (e) => { e.preventDefault(); share({ title: `Debrief – ${company.name}`, text: debriefText(r) }); } }, "Share")),
      h("div", { class: "md" }, markdown(d.summary)),
      d.next_steps && h("p", { dir: "auto" }, h("b", {}, "Next steps: "), d.next_steps),
      h("details", { open: true }, h("summary", {}, `Questions (${d.questions.length})${avg ? ` · average ${avg}/10` : ""}`),
        d.questions.map((q) => h("div", { class: "debrief-q" },
          h("div", { class: "row" }, h("span", { class: `score ${q.score >= 7 ? "good" : "low"}` }, `${q.score}/10`), h("b", { dir: "auto" }, q.question)),
          h("div", { class: "small muted" }, `${q.kind} · ${q.capability} · ${q.topic}`),
          h("p", { class: "small", dir: "auto" }, h("i", {}, "You: "), q.answer_summary),
          h("p", { class: "small", dir: "auto" }, q.feedback),
          q.missed.length > 0 && h("ul", { class: "small" }, q.missed.map((m) => h("li", { dir: "auto" }, m)))))),
      h("details", {}, h("summary", {}, "Strengths and what to improve"),
        h("p", { class: "md-h" }, "Strengths"), h("ul", {}, d.strengths.map((s) => h("li", { dir: "auto" }, s))),
        h("p", { class: "md-h" }, "To improve"), h("ul", {}, d.to_improve.map((s) => h("li", { dir: "auto" }, s)))),
      h("details", {}, h("summary", {}, "About the role (what they said)"), h("div", { class: "md" }, markdown(d.about_the_role))),
      h("details", {}, h("summary", {}, "Thank-you email"),
        h("pre", { class: "transcript", dir: "auto" }, d.thank_you_email),
        h("button", { onclick: () => share({ title: `Thank you – ${company.name}`, text: d.thank_you_email }) }, "Share or copy")),
      transcript,
      h("div", { class: "row small" }, "Debrief again in ", lang,
        h("button", { onclick: () => act(() => call("recording.reanalyze", { id: r.id, language: lang.value }), "Writing the debrief") }, `Redo · ≈ ${usd(r.estimate_usd?.analysis)}`)));
  };

  const recording = (r) => h("div", { class: "recording" }, header(r),
    r.status === "uploaded" && uploaded(r),
    (r.status === "transcribing" || r.status === "analyzing") && [h("p", { class: "small" }, "⏳ This takes a minute or two for a 45-minute interview. You can leave this page."), costLine(r)],
    r.status === "choose_speaker" && chooseSpeaker(r),
    r.status === "error" && [h("div", { class: "result warn card" }, r.error),
      h("button", { onclick: () => act(() => call(r.model ? "recording.reanalyze" : "recording.transcribe", { id: r.id }), "Retrying") }, "Retry")],
    r.status === "done" && r.debrief && debrief(r));

  function draw() {
    mount(root,
      h("div", { class: "row" }, h("h2", { style: "margin:0" }, "Interview recordings")),
      h("p", { class: "small muted" }, "Upload the audio of an interview: it is transcribed (≈ $0.006 per minute), you pick your voice, and you get a debrief: the questions asked, your answers graded, what to improve, and a thank-you email. The graded answers go to your Practice page, and the next prep builds on them."),
      !info.recordings && h("div", { class: "result warn card" }, "Recordings need OPENAI_API_KEY and ffmpeg on the server."),
      info.recordings && h("div", { class: "row" }, stepSelect, fileInput, uploadBtn),
      list.length ? [...list].reverse().map(recording) : h("p", { class: "muted small" }, "No recording yet."));
  }

  draw();
  load();
  return root;
}

// Plain-text debrief for the share sheet (WhatsApp, mail, notes…)
function debriefText(r) {
  const d = r.debrief;
  return [`${r.step_name || "Interview"} · ${r.uploaded_at.slice(0, 10)}`, "", d.summary, "",
    d.next_steps && `Next steps: ${d.next_steps}`, "", "Questions:",
    ...d.questions.map((q) => `- ${q.question} (${q.score}/10): ${q.feedback}`), "",
    "To improve:", ...d.to_improve.map((t) => `- ${t}`)].filter((x) => x !== false && x !== undefined).join("\n");
}
