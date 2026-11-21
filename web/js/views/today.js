// Today: the page to open each morning. Next interviews (with prep status), what's waiting for you,
// follow-ups due, what to review, what came back. Everything links to where you act on it.
import { call } from "../api.js";
import { addToCalendar, attempt, chatHref, companyLogo, h, mount } from "../ui.js";

const chat = (q) => chatHref(q);

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

export async function renderToday(root) {
  const [t, info] = await Promise.all([call("today.get"), call("app.info")]);
  const isLink = (s) => /^https?:\/\//.test(s || "");

  const prepPill = (i) => {
    if (i.prep.status === "running") return h("span", { class: "pill warn-pill" }, "Prep being written…");
    if (i.prep.ready) {
      return h("span", { class: "pill good-pill" }, `Prep ready · ${i.prep.exercises} exercises`
        + (i.practice_average !== null ? ` · ${i.practice_average}/10` : ""));
    }
    return h("span", { class: "pill" }, info.premium ? "No prep yet" : "No prep");
  };

  const interview = (i) => h("div", { class: "today-item" },
    dayChip(i),
    h("div", { class: "today-main" },
      h("a", { href: `#/company/${i.company_id}`, class: "today-title row", style: "gap:8px", dir: "auto" },
        companyLogo({ name: i.company, logo_file: i.logo_file, logo_checked_at: i.logo_checked_at }, 22), `${i.step} · ${i.company}`),
      h("div", { class: "small muted" }, [i.time && `${i.time}${i.duration_min ? ` · ${i.duration_min} min` : ""}`, i.position].filter(Boolean).join(" · ")),
      i.location && (isLink(i.location)
        ? h("a", { class: "small", href: i.location, target: "_blank", rel: "noopener" }, "🎥 Join the call")
        : h("div", { class: "small", dir: "auto" }, `📍 ${i.location}`)),
      h("div", { class: "row today-actions" }, prepPill(i),
        h("a", { class: "btn small", href: `#/company/${i.company_id}` }, i.prep.ready ? "Review prep" : info.premium ? "Prepare" : "Open"),
        i.prep.ready && h("a", { class: "btn small", href: chat(`Quiz me for my ${i.step} at ${i.company}: one exercise at a time from my prep, graded with the rubric.`) }, "Practice"),
        h("button", { class: "small", onclick: () => addToCalendar(i.company_id, i.step_id) }, "📅"))));

  const followUp = (f) => h("div", { class: "today-row" },
    companyLogo({ name: f.company, logo_file: f.logo_file, logo_checked_at: f.logo_checked_at }, 32),
    h("a", { href: `#/company/${f.company_id}`, class: "today-main" },
      h("b", { dir: "auto" }, f.company), h("div", { class: "small muted", dir: "auto" }, `${f.position || ""}${f.stage ? ` · ${f.stage}` : ""}`)),
    h("span", { class: "app-days due" }, `${f.days}d`),
    h("button", {
      class: "small", title: "I followed up today (resets the counter)", onclick: async (e) => {
        if (await attempt(() => call("company.log_update", { id: f.company_id, by: "me" }), `Follow-up logged for ${f.company}`)) {
          e.target.closest(".today-row").remove();
        }
      },
    }, "✓ Followed up"));

  const item = (r) => h("a", { class: "today-row", href: `#/company/${r.company_id}` },
    h("div", { class: "today-main" }, h("b", {}, r.what), h("div", { class: "small muted", dir: "auto" },
      [r.company, r.step, r.detail].filter(Boolean).join(" · "))), h("span", { class: "chev" }, "›"));

  const review = (e) => h("a", { class: "today-row", href: chat(`Let's practice "${e.exercise}" again (${e.company} interview prep). Give me the exercise, then grade my answer with the rubric.`) },
    h("span", { class: `score ${e.last_score >= 7 ? "good" : "low"}` }, `${e.last_score}/10`),
    h("div", { class: "today-main" }, h("b", { dir: "auto" }, e.exercise), h("div", { class: "small muted" }, `${e.company} · ${e.capability || e.topic || e.kind}`)),
    h("span", { class: "chev" }, "›"));

  const card = (title, items, extra) => items.length > 0 && h("section", { class: "card today-card" }, h("h2", {}, title), items, extra);
  const nothing = !t.interviews.length && !t.follow_ups.length && !t.waiting.length && !t.recent.length && !t.practice.to_review.length;
  const date = new Date(`${t.date}T00:00:00`).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });

  mount(root,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, greeting()), h("div", { class: "sub muted" }, date))),
    h("div", { class: "today-stats" },
      h("a", { href: "#/" }, h("b", {}, t.week.interviews_next_7_days), "interviews this week"),
      h("a", { href: "#/" }, h("b", { class: t.follow_ups.length ? "due" : "" }, t.follow_ups.length), "to follow up"),
      h("a", { href: "#/practice" }, h("b", {}, t.practice.to_review.length), "to review"),
      h("a", { href: "#/" }, h("b", {}, t.week.active), "active applications")),
    nothing && h("div", { class: "card empty" }, h("h2", {}, "Nothing urgent today"),
      h("p", { class: "muted" }, "No interview coming, no follow-up due. A good moment to add a job or practice."),
      h("div", { class: "row", style: "justify-content:center" },
        h("a", { class: "btn primary", href: chat("Add this job and make my CV: ") }, "+ Add a job"),
        h("a", { class: "btn", href: "#/practice" }, "Practice"))),
    h("div", { class: "today-grid" },
      h("div", { class: "stack" },
        card("Next interviews", t.interviews.map(interview)),
        card("Waiting for you", t.waiting.map(item)),
        card("Follow-ups due", t.follow_ups.map(followUp))),
      h("div", { class: "stack" },
        card("Just in", t.recent.map(item)),
        card("To review", t.practice.to_review.map(review),
          h("a", { class: "btn small", href: chat("Quiz me on my weak spots: pick from my practice profile's to-review list.") }, "Quiz me on my weak spots")),
        t.practice.needs_work.length > 0 && h("section", { class: "card today-card" }, h("h2", {}, "Needs work"),
          h("p", { class: "small" }, t.practice.needs_work.join(" · ")), h("a", { class: "small", href: "#/practice" }, "See your progress ›")))));
}
