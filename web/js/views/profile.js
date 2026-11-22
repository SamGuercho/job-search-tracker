// Profile: who you are for the app and what you're looking for. Name and LinkedIn are your master CV's (one place,
// also on your CV header). The rest is used: greetings, referral messages, warnings on jobs that don't fit,
// the default salary of new applications, the default language of preps, and weekly goals on Today.
import { call } from "../api.js";
import { attempt, field, h, joinList, mount, splitList } from "../ui.js";

const WORK_MODELS = ["On-site", "Hybrid", "Remote"];
const LANGUAGES = [["English", "English"], ["Hebrew", "Hebrew · עברית"], ["French", "French"]];

export async function renderProfile(root) {
  const p = await call("profile.get");
  const input = (value, attrs = {}) => h("input", { value: value ?? "", dir: "auto", ...attrs });

  const name = input(p.name, { autocomplete: "name" });
  const first = input(p.preferred_name, { placeholder: p.first_name || "e.g. Sam" });
  const linkedin = input(p.linkedin, { type: "url", placeholder: "https://www.linkedin.com/in/your-name", dir: "ltr" });
  const roles = input(joinList(p.target_roles), { placeholder: "e.g. Data Scientist, AI Engineer" });
  const seniority = input(p.seniority, { placeholder: "e.g. Senior" });
  const areas = input(joinList(p.locations), { placeholder: "e.g. Tel Aviv, Herzliya, Ramat Gan" });
  const models = WORK_MODELS.map((m) => h("label", { class: "check" },
    h("input", { type: "checkbox", value: m, checked: p.work_models.includes(m) }), ` ${m}`));
  const commute = input(p.max_commute_min, { type: "number", min: 0, step: 5, placeholder: "minutes" });
  const salary = input(p.salary_expectation, { placeholder: "e.g. 38k ILS/month" });
  const notice = input(p.notice_period, { placeholder: "e.g. 1 month, immediately" });
  const from = input(p.available_from, { type: "date" });
  const language = h("select", {}, LANGUAGES.map(([v, label]) => h("option", { value: v, selected: v === p.interview_language }, label)));
  const goalApps = input(p.goals.applications || "", { type: "number", min: 0, max: 100, placeholder: "0 = no goal" });
  const goalPractice = input(p.goals.practice_answers || "", { type: "number", min: 0, max: 100, placeholder: "0 = no goal" });

  const save = async () => {
    const changes = {
      name: name.value, preferred_name: first.value, linkedin: linkedin.value,
      target_roles: splitList(roles.value), seniority: seniority.value, locations: splitList(areas.value),
      work_models: models.map((l) => l.querySelector("input")).filter((c) => c.checked).map((c) => c.value),
      max_commute_min: Number(commute.value) || 0, salary_expectation: salary.value, notice_period: notice.value,
      available_from: from.value, interview_language: language.value,
      goals: { applications: Number(goalApps.value) || 0, practice_answers: Number(goalPractice.value) || 0 },
    };
    if (await attempt(() => call("profile.update", changes), "Profile saved")) renderProfile(root);
  };

  const section = (title, note, ...body) => h("section", { class: "card stack" },
    h("div", {}, h("h2", { style: "margin:0" }, title), note && h("p", { class: "small muted", style: "margin:4px 0 0" }, note)), ...body);

  mount(root,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Profile"),
      h("div", { class: "sub muted" }, "Who you are and what you're looking for: Ezri uses it to greet you, draft your messages and warn you about jobs that don't fit"))),
    h("div", { class: "stack" },
      section("You", "Your name and LinkedIn are your master CV's: changing them here changes your CV header too."
        + (p.email ? ` Email: ${p.email} (on the Master CV page).` : ""),
      h("div", { class: "form-grid" }, field("Your name", name), field("First name for greetings", first), field("LinkedIn profile", linkedin, "wide")),
      h("p", { class: "small muted" }, "Your LinkedIn goes into the referral messages the assistant drafts. ",
        p.linkedin && h("a", { href: p.linkedin, target: "_blank", rel: "noopener" }, "Open your profile ↗"))),
      section("What you're looking for", "Jobs outside it get a warning on Today, and the assistant points it out. Separate several with commas.",
        h("div", { class: "form-grid" },
          field("Target roles", roles, "wide"), field("Seniority", seniority), field("Max commute (min)", commute),
          field("Areas", areas, "wide"),
          h("div", { class: "field wide" }, "Work models you accept", h("div", { class: "row" }, models)))),
      section("Practical", "Your salary expectation fills new applications; the language is the default for interview preps.",
        h("div", { class: "form-grid" },
          field("Salary expectation", salary), field("Notice period", notice), field("Available from", from),
          field("Interview prep language", language))),
      section("Weekly goals", "Shown on Today as this week's progress (weeks start on Monday).",
        h("div", { class: "form-grid" }, field("Applications per week", goalApps), field("Practice answers per week", goalPractice))),
      h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "primary", onclick: save }, "Save"))));
}
