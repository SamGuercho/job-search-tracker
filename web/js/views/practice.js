// Practice: the training profile built from graded answers (what to review, scores by topic, history).
import { call } from "../api.js";
import { h, mount } from "../ui.js";

export async function renderPractice(root) {
  const [p, log] = await Promise.all([call("practice.profile"), call("practice.history")]);
  const t = p.totals;
  const chat = (text) => `#/chat?q=${encodeURIComponent(text)}`;
  const score = (s) => h("span", { class: `score ${s >= 7 ? "good" : "low"}` }, `${s}/10`);
  const table = (...rows) => h("div", { class: "table-wrap" }, h("table", { class: "data" }, ...rows));
  const tile = (label, value, note) => h("div", { class: "kpi" },
    h("div", { class: "kpi-label" }, label), h("div", { class: "kpi-value" }, value ?? "—"), note && h("div", { class: "kpi-note" }, note));

  if (!t.attempts) {
    return mount(root,
      h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Practice"))),
      h("div", { class: "card empty" },
        h("h2", {}, "No practice yet"),
        h("p", { class: "muted" }, "Open an application's Interview prep and tap “Practice with the assistant”. Each graded answer is recorded here: your scores by topic, and what to review.")));
  }

  mount(root,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Practice"), h("div", { class: "sub muted" }, "Built from the answers the assistant graded.")),
      h("a", { class: "btn primary", href: chat("Quiz me on my weak spots: pick from my practice profile's to-review list.") }, "Quiz me on my weak spots")),
    h("div", { class: "kpis" },
      tile("Answers graded", t.attempts),
      tile("Exercises practised", t.exercises),
      tile("Average score", t.average !== null ? `${t.average}/10` : "—"),
      tile("To review", p.to_review.length, "last score below 7")),
    h("div", { class: "grid cols-2" },
      h("div", { class: "card" },
        h("h2", {}, "To review"),
        p.to_review.length
          ? p.to_review.map((e) => h("div", { class: "review-row" },
            h("div", { class: "row" }, score(e.last_score), h("b", {}, e.exercise), h("span", { class: "small muted" }, ` · ${e.company} · ${e.topic || e.kind}`)),
            e.missed.length > 0 && h("div", { class: "small muted" }, `Missed: ${e.missed.join("; ")}`),
            h("a", { class: "small", href: chat(`Let's practice "${e.exercise}" again (${e.company} interview prep). Give me the exercise, then grade my answer with the rubric.`) }, "Practice again →")))
          : h("p", { class: "muted" }, "Nothing to review: every exercise's last score is 7 or more.")),
      h("div", { class: "card" },
        h("h2", {}, "By topic"),
        table(
          h("thead", {}, h("tr", {}, h("th", {}, "Topic"), h("th", {}, "Answers"), h("th", {}, "Average"), h("th", {}, "Last"), h("th", {}, "Trend"))),
          h("tbody", {}, p.topics.map((x) => h("tr", {},
            h("td", {}, x.topic), h("td", {}, x.attempts), h("td", {}, score(x.average)), h("td", {}, `${x.last_score}/10`),
            h("td", {}, x.trend > 0 ? `▲ +${x.trend}` : x.trend < 0 ? `▼ ${x.trend}` : "—"))))))),
    h("div", { class: "card" },
      h("h2", {}, "History"),
      table(
        h("thead", {}, h("tr", {}, h("th", {}, "Date"), h("th", {}, "Exercise"), h("th", {}, "Application"), h("th", {}, "Score"), h("th", {}, "Feedback"))),
        h("tbody", {}, log.slice(0, 50).map((a) => h("tr", {},
          h("td", { class: "small" }, a.at.slice(0, 10)), h("td", {}, a.exercise),
          h("td", {}, h("a", { href: `#/company/${a.company_id}` }, a.company)), h("td", {}, score(a.score)),
          h("td", { class: "small muted" }, a.feedback)))))));
}
