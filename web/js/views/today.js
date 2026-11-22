// Today: the page to open each morning, in the order things matter: the interview to train for first and the next
// interviews (with readiness), interviews to debrief, applications to send (positions fill fast), follow-ups and
// "probably no answer", then what's waiting, what came back and what to review. The rules are in services/rules.py.
import { call } from "../api.js";
import { addToCalendar, attempt, chatHref, companyLogo, h, mount, promptSheet } from "../ui.js";

const chat = (q) => chatHref(q);
const DISMISSED_KEY = "today-dismissed";  // debrief prompts closed on this device (they expire after 3 days anyway)

function dayChip(i) {
  const d = new Date(`${i.date}T00:00:00`);
  const top = i.days === 0 ? "Today" : i.days === 1 ? "Tomorrow" : d.toLocaleDateString(undefined, { weekday: "short" });
  return h("div", { class: `day-chip ${i.days <= 1 ? "soon" : ""}` },
    h("span", { class: "small" }, top), h("b", {}, String(d.getDate())),
    h("span", { class: "small" }, d.toLocaleDateString(undefined, { month: "short" })));
}

function greeting() {
  const hour = new Date().getHours();
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

const inDays = (d) => (d === 0 ? "today" : d === 1 ? "tomorrow" : `in ${d} days`);

function dismissed() {
  try { return JSON.parse(localStorage.getItem(DISMISSED_KEY) || "[]"); } catch { return []; }
}
function dismiss(key) {
  try { localStorage.setItem(DISMISSED_KEY, JSON.stringify([...dismissed().slice(-50), key])); } catch { /* private mode */ }
}

export async function renderToday(root) {
  const [raw, info] = await Promise.all([call("today.get"), call("app.info")]);
  // defaults: an offline copy saved by an older version may lack the newer lists
  const t = { to_apply: [], no_answer: [], debriefs: [], train_first: null, ...raw };
  const isLink = (s) => /^https?:\/\//.test(s || "");
  const reload = () => renderToday(root);
  const logo = (x, size = 32) => companyLogo({ name: x.company, logo_file: x.logo_file, logo_checked_at: x.logo_checked_at }, size);

  // --------------------------------------------------------------- interviews
  const readiness = (i) => {
    const r = i.readiness || { score: 0, practised: 0, total: null, average: null, weak: [], level: "low" };
    if (i.prep.status === "running") return h("span", { class: "pill warn-pill" }, "Prep being written…");
    const detail = r.total ? `${r.practised}/${r.total} exercises` : r.practised ? `${r.practised} answers` : (i.prep.ready ? "not practised yet" : info.premium ? "no prep yet" : "no practice yet");
    return h("div", { class: "readiness", title: "Exercises practised × your average score on them" },
      h("div", { class: `readiness-bar ${r.level}` }, h("span", { style: `width:${Math.max(r.score, 3)}%` })),
      h("span", { class: "small" }, h("b", {}, `Ready ${r.score}%`), ` · ${detail}`
        + (r.average !== null ? ` · ${r.average}/10` : "") + (r.weak.length ? ` · work on ${r.weak.join(", ")}` : "")));
  };
  const practiseHref = (i) => chat(`Quiz me for my ${i.step} at ${i.company}: one exercise at a time from my prep, graded with the rubric. Start with the ones I haven't practised.`);

  const interview = (i) => h("div", { class: "today-item" },
    dayChip(i),
    h("div", { class: "today-main" },
      h("a", { href: `#/company/${i.company_id}`, class: "today-title row", style: "gap:8px", dir: "auto" }, logo(i, 22), `${i.step} · ${i.company}`),
      h("div", { class: "small muted" }, [i.time && `${i.time}${i.duration_min ? ` · ${i.duration_min} min` : ""}`, i.position].filter(Boolean).join(" · ")),
      i.location && (isLink(i.location)
        ? h("a", { class: "small", href: i.location, target: "_blank", rel: "noopener" }, "🎥 Join the call")
        : h("div", { class: "small", dir: "auto" }, `📍 ${i.location}`)),
      readiness(i),
      h("div", { class: "row today-actions" },
        i.prep.ready
          ? h("a", { class: "btn small primary", href: practiseHref(i) }, "Practise")
          : h("a", { class: "btn small", href: `#/company/${i.company_id}` }, info.premium && i.prep.status !== "running" ? "Prepare" : "Open"),
        i.prep.ready && h("a", { class: "btn small", href: `#/company/${i.company_id}` }, "Review prep"),
        h("button", { class: "small", onclick: () => addToCalendar(i.company_id, i.step_id) }, "📅"))));

  const trainFirst = (f) => f && h("a", { class: `card train-first ${f.readiness.level}`, href: f.prep.ready ? practiseHref(f) : `#/company/${f.company_id}` },
    h("div", { class: "today-main" },
      h("div", { class: "small muted" }, "Train first"),
      h("b", { dir: "auto" }, `${f.company}: ${f.step} ${inDays(f.days)}`),
      h("div", { class: "small" }, f.readiness.total
        ? `${f.readiness.practised} of ${f.readiness.total} exercises done · ready ${f.readiness.score}%`
        : f.prep.ready ? `Ready ${f.readiness.score}%` : "No prep yet: prepare, then practise")),
    h("span", { class: "btn small primary" }, f.prep.ready ? "Practise" : "Prepare"));

  // ------------------------------------------------------------ debriefs
  const debrief = (d) => {
    const key = `debrief:${d.step_id}:${d.date}`;
    if (dismissed().includes(key)) return null;
    const when = d.days_ago === 1 ? "yesterday" : `${d.days_ago} days ago`;
    return h("div", { class: "today-row debrief" },
      logo(d),
      h("div", { class: "today-main" },
        h("b", { dir: "auto" }, `${d.step} · ${d.company}`),
        h("div", { class: "small muted" }, `${when}. Debrief it while it's fresh, and send a thank-you note within 24 hours.`),
        h("div", { class: "row today-actions" },
          h("a", { class: "btn small primary", href: chat(`My ${d.step} at ${d.company} was ${when}. Debrief me: ask me how it went (questions, what went well, what didn't), save notes on that step, mark it done, and tell me what to work on next.`) }, "Debrief"),
          h("a", { class: "btn small", href: `#/company/${d.company_id}` }, "Add the recording"),
          h("a", { class: "btn small", href: chat(`Draft a short thank-you note for my ${d.step} at ${d.company} (${when}). Ask me who I met and one thing we discussed if you don't know.`) }, "Thank-you note"))),
      h("button", { class: "ghost icon", title: "Hide", "aria-label": "Hide", onclick: (e) => { dismiss(key); e.target.closest(".today-row").remove(); } }, "✕"));
  };

  // ------------------------------------------------------------ to apply
  const toApply = (a) => h("div", { class: "today-row" },
    logo(a),
    h("div", { class: "today-main" },
      h("a", { href: `#/company/${a.company_id}`, class: "today-title", dir: "auto" }, a.company),
      h("div", { class: "small muted", dir: "auto" }, [a.position, a.cv_ready ? "CV ready" : "no CV yet"].filter(Boolean).join(" · "),
        a.job_url && h("a", { href: a.job_url, target: "_blank", rel: "noopener", title: "The job posting" }, " ↗")),
      a.stale
        ? h("div", { class: "row today-actions" }, h("span", { class: "small" }, `Added ${a.age} days ago. Still interested?`),
          h("button", { class: "small", onclick: () => drop(a) }, "Drop it"),
          h("button", { class: "small", onclick: () => snooze(a, "Kept: asked again in 2 weeks") }, "Keep"))
        : [referral(a), h("div", { class: "row today-actions" },
          !a.cv_ready && h("a", { class: "btn small", href: chat(`Make my CV for ${a.company}${a.position ? ` (${a.position})` : ""}.`) }, "Make CV"),
          a.applied_step_id && h("button", { class: `small ${a.cv_ready ? "primary" : ""}`, onclick: () => markApplied(a) }, "✓ Applied"))]),
    h("span", { class: `age ${a.level}`, title: "Days since you added it: positions fill fast" }, `${a.age}d`));

  // A referral multiplies the chances: ask the referee (then remind them), or find one on LinkedIn first
  const myLinkedin = t.me?.linkedin ? ` My LinkedIn: ${t.me.linkedin}.` : "";
  const role = (a) => `the ${a.position || "open"} role at ${a.company}${a.job_url ? ` (${a.job_url})` : ""}`;
  const peopleSearch = (a) => `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(a.company)}&network=%5B%22F%22%2C%22S%22%5D`;
  const referral = (a) => {
    if (a.referral === "none") {
      return h("div", { class: "row today-actions referral none" },
        h("span", { class: "small muted" }, "No referee yet"),
        h("a", { class: "btn small", href: peopleSearch(a), target: "_blank", rel: "noopener", title: "Your 1st and 2nd-degree connections there" }, "Find one on LinkedIn ↗"),
        h("button", { class: "small", onclick: () => setReferee(a) }, "+ Referee"));
    }
    const ask = chat(`Draft a short, warm LinkedIn message to ${a.referee} asking if they'd refer me for ${role(a)}. Say in one line why I fit, from my CV.${myLinkedin}`);
    const remind = chat(`Draft a short, friendly reminder to ${a.referee} about the referral I asked ${a.asked_days} days ago for ${role(a)}.${myLinkedin}`);
    return h("div", { class: `row today-actions referral ${a.referral}` },
      h("span", { class: "small" }, "🤝 ", h("b", { dir: "auto" }, a.referee),
        a.referral === "to_ask" ? " · not asked yet" : ` · asked ${a.asked_days === 0 ? "today" : `${a.asked_days}d ago`}${a.referral === "remind" ? ": remind them" : ""}`),
      a.referral === "to_ask" && h("a", { class: "btn small primary", href: ask }, `Message ${a.referee.split(" ")[0]}`),
      a.referral === "to_ask" && h("button", { class: "small", onclick: () => asked(a, `Asked ${a.referee}: reminder in 3 days`) }, "✓ Asked"),
      a.referral === "remind" && h("a", { class: "btn small primary", href: remind }, "Remind"),
      a.referral === "remind" && h("button", { class: "small", onclick: () => asked(a, `Reminded ${a.referee}`) }, "✓ Reminded"));
  };
  const setReferee = async (a) => {
    const name = await promptSheet("Who can refer you there? Today will remind you to ask them.", "", { title: `Referee at ${a.company}`, ok: "Save", placeholder: "e.g. Dana Levi" });
    if (name?.trim() && await attempt(() => call("company.referral", { company: a.company_id, referee: name, asked: false }), `${name.trim()} added`)) reload();
  };
  const asked = async (a, msg) => {
    if (await attempt(() => call("company.referral", { company: a.company_id, asked: true }), msg)) reload();
  };

  const markApplied = async (a) => {
    if (await attempt(() => call("process.set_state", { company: a.company_id, step: a.applied_step_id, state: "done" }), `${a.company}: applied`)) reload();
  };
  const drop = async (a) => {
    if (await attempt(() => call("process.close", { company: a.company_id, reason: "Not interested anymore" }), `${a.company} dropped (reopen it from its page)`)) reload();
  };
  const snooze = async (a, msg) => {
    if (await attempt(() => call("today.snooze", { company: a.company_id, days: 14 }), msg)) reload();
  };

  // ----------------------------------------------------- follow-ups, no answer
  const followUp = (f) => h("div", { class: "today-row" },
    logo(f),
    h("a", { href: `#/company/${f.company_id}`, class: "today-main" },
      h("b", { dir: "auto" }, f.company), h("div", { class: "small muted", dir: "auto" }, f.why)),
    h("span", { class: "app-days due" }, `${f.days}d`),
    h("button", {
      class: "small", title: "I followed up today (resets the counter)", onclick: async (e) => {
        if (await attempt(() => call("company.log_update", { id: f.company_id, by: "me" }), `Follow-up logged for ${f.company}`)) {
          e.target.closest(".today-row").remove();
        }
      },
    }, "✓ Followed up"));

  const noAnswer = (n) => h("div", { class: "today-row" },
    logo(n),
    h("div", { class: "today-main" },
      h("a", { href: `#/company/${n.company_id}`, class: "today-title", dir: "auto" }, n.company),
      h("div", { class: "small muted", dir: "auto" }, `${n.why}${n.position ? ` · ${n.position}` : ""}`),
      h("div", { class: "row today-actions" },
        h("button", {
          class: "small", onclick: async () => {
            if (await attempt(() => call("process.close", { company: n.company_id, reason: "No answer" }), `${n.company} closed: no answer (reopen it from its page if they reply)`)) reload();
          },
        }, "Close: no answer"),
        h("button", { class: "small", onclick: () => snooze(n, "Asked again in 2 weeks") }, "Keep waiting"))));

  // ------------------------------------------------------------------ others
  const item = (r) => h("a", { class: "today-row", href: `#/company/${r.company_id}` },
    h("div", { class: "today-main" }, h("b", {}, r.what), h("div", { class: "small muted", dir: "auto" },
      [r.company, r.step, r.detail].filter(Boolean).join(" · "))), h("span", { class: "chev" }, "›"));

  const review = (e) => h("a", { class: "today-row", href: chat(`Let's practice "${e.exercise}" again (${e.company} interview prep). Give me the exercise, then grade my answer with the rubric.`) },
    h("span", { class: `score ${e.last_score >= 7 ? "good" : "low"}` }, `${e.last_score}/10`),
    h("div", { class: "today-main" }, h("b", { dir: "auto" }, e.exercise), h("div", { class: "small muted" }, `${e.company} · ${e.capability || e.topic || e.kind}`)),
    h("span", { class: "chev" }, "›"));

  const card = (title, items, extra, note) => {
    const shown = items.filter(Boolean);
    return shown.length > 0 && h("section", { class: "card today-card" }, h("h2", {}, title, note && h("span", { class: "small muted" }, ` ${note}`)), shown, extra);
  };
  const debriefs = t.debriefs.map(debrief).filter(Boolean);
  const nothing = !t.interviews.length && !t.follow_ups.length && !t.no_answer.length && !t.to_apply.length && !debriefs.length
    && !t.waiting.length && !t.recent.length && !t.practice.to_review.length;
  const date = new Date(`${t.date}T00:00:00`).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  const urgentApply = t.to_apply.filter((a) => a.level !== "new").length;
  const withReferee = t.to_apply.filter((a) => a.referee);
  const noReferee = t.to_apply.filter((a) => !a.referee);

  mount(root,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", { dir: "auto" }, greeting() + (t.me?.first_name ? `, ${t.me.first_name}` : "")), h("div", { class: "sub muted" }, date))),
    (t.server || []).length > 0 && h("div", { class: "card server-warning", role: "status" },
      h("b", {}, "Server"), (t.server || []).map((w) => h("p", { class: "small" }, w))),
    h("div", { class: "today-stats" },
      h("a", { href: "#/today" }, h("b", {}, t.week.interviews_next_7_days), "interviews this week"),
      h("a", { href: "#/today" }, h("b", { class: urgentApply ? "due" : "" }, t.to_apply.length), "to apply"),
      h("a", { href: "#/today" }, h("b", { class: t.follow_ups.length + t.no_answer.length ? "due" : "" }, t.follow_ups.length + t.no_answer.length), "to follow up"),
      h("a", { href: "#/practice" }, h("b", {}, t.practice.to_review.length), "to review")),
    trainFirst(t.train_first),
    nothing && h("div", { class: "card empty" }, h("h2", {}, "Nothing urgent today"),
      h("p", { class: "muted" }, "No interview coming, nothing to apply to or follow up. A good moment to add a job or practise."),
      h("div", { class: "row", style: "justify-content:center" },
        h("a", { class: "btn primary", href: chat("Add this job and make my CV: ") }, "+ Add a job"),
        h("a", { class: "btn", href: "#/practice" }, "Practise"))),
    h("div", { class: "today-grid" },
      h("div", { class: "stack" },
        card("Next interviews", t.interviews.map(interview)),
        card("How did it go?", debriefs),
        card("To apply", [
          withReferee.length > 0 && noReferee.length > 0 && h("div", { class: "group-label small muted" }, "With a referee"),
          withReferee.map(toApply),
          withReferee.length > 0 && noReferee.length > 0 && h("div", { class: "group-label small muted" }, "No referee yet: find one before applying"),
          noReferee.map(toApply)].flat(), null, "oldest first: positions fill fast")),
      h("div", { class: "stack" },
        card("Follow-ups due", t.follow_ups.map(followUp)),
        card("Probably no answer", t.no_answer.map(noAnswer), null, "nothing closes by itself"),
        card("Waiting for you", t.waiting.map(item)),
        card("Just in", t.recent.map(item)),
        card("To review", t.practice.to_review.map(review),
          h("a", { class: "btn small", href: chat("Quiz me on my weak spots: pick from my practice profile's to-review list.") }, "Quiz me on my weak spots")),
        t.practice.needs_work.length > 0 && h("section", { class: "card today-card" }, h("h2", {}, "Needs work"),
          h("p", { class: "small" }, t.practice.needs_work.join(" · ")), h("a", { class: "small", href: "#/practice" }, "See your progress ›")))));
}
