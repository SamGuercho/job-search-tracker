// Profile: who you are for the app and what you're looking for. Name and LinkedIn are your master CV's (one place,
// also on your CV header). The rest is used: greetings, referral messages, warnings on jobs that don't fit,
// the default salary of new applications, the default language of preps, weekly goals on Today, and the working and
// study hours study sessions are planned in.
import { call, upload } from "../api.js";
import { attempt, chatHref, field, formatDate, h, joinList, mount, splitList, toast } from "../ui.js";

const WORK_MODELS = ["On-site", "Hybrid", "Remote"];
const DAYS = [[6, "Sun"], [0, "Mon"], [1, "Tue"], [2, "Wed"], [3, "Thu"], [4, "Fri"], [5, "Sat"]];  // Python weekdays
const LANGUAGES = [["English", "English"], ["Hebrew", "Hebrew · עברית"], ["French", "French"]];

export async function renderProfile(root) {
  const [p, li] = await Promise.all([call("profile.get"), call("linkedin.status")]);
  const input = (value, attrs = {}) => h("input", { value: value ?? "", dir: "auto", ...attrs });

  const firstName = input(p.first_name, { autocomplete: "given-name" });
  const lastName = input(p.last_name, { autocomplete: "family-name" });
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

  const st = p.study;
  const time = (value) => input(value, { type: "time", step: 900 });
  const workStart = time(st.work_start), workEnd = time(st.work_end);
  const studyStart = time(st.study_start), studyEnd = time(st.study_end);
  const weekendStart = time(st.weekend_start), weekendEnd = time(st.weekend_end);
  const weekendFree = h("input", { type: "checkbox", checked: st.weekend_free });
  const weekendDays = DAYS.map(([n, label]) => h("label", { class: "check" },
    h("input", { type: "checkbox", value: n, checked: st.weekend_days.includes(n) }), ` ${label}`));
  const maxPerDay = input(st.max_per_day_min, { type: "number", min: 15, max: 600, step: 15 });

  const save = async () => {
    const changes = {
      first_name: firstName.value, last_name: lastName.value, preferred_name: first.value, linkedin: linkedin.value,
      target_roles: splitList(roles.value), seniority: seniority.value, locations: splitList(areas.value),
      work_models: models.map((l) => l.querySelector("input")).filter((c) => c.checked).map((c) => c.value),
      max_commute_min: Number(commute.value) || 0, salary_expectation: salary.value, notice_period: notice.value,
      available_from: from.value, interview_language: language.value,
      goals: { applications: Number(goalApps.value) || 0, practice_answers: Number(goalPractice.value) || 0 },
      study: {
        work_start: workStart.value, work_end: workEnd.value, study_start: studyStart.value, study_end: studyEnd.value,
        weekend_days: weekendDays.map((l) => l.querySelector("input")).filter((c) => c.checked).map((c) => Number(c.value)),
        weekend_free: weekendFree.checked, weekend_start: weekendStart.value, weekend_end: weekendEnd.value,
        max_per_day_min: Number(maxPerDay.value) || 90,
      },
    };
    if (await attempt(() => call("profile.update", changes), "Profile saved")) renderProfile(root);
  };

  const section = (title, note, ...body) => h("section", { class: "card stack" },
    h("div", {}, h("h2", { style: "margin:0" }, title), note && h("p", { class: "small muted", style: "margin:4px 0 0" }, note)), ...body);

  mount(root,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Profile"),
      h("div", { class: "sub muted" }, "Who you are and what you're looking for: Ezri uses it to greet you, draft your messages and warn you about jobs that don't fit"))),
    h("div", { class: "stack" },
      section("You", "Your name and LinkedIn are your master CV's: changing them here changes your CV header too. "
        + "Your CV files are named first-last-position-company (e.g. Dana-Levi-Data-Scientist-Wix.pdf)."
        + (p.email ? ` Email: ${p.email} (on the Master CV page).` : ""),
      h("div", { class: "form-grid" }, field("First name", firstName), field("Last name", lastName), field("Greet me as", first), field("LinkedIn profile", linkedin, "wide")),
      h("p", { class: "small muted" }, "Your LinkedIn goes into the referral messages the assistant drafts. ",
        p.linkedin && h("a", { href: p.linkedin, target: "_blank", rel: "noopener" }, "Open your profile ↗"))),
      linkedinSection(li, section, () => renderProfile(root)),
      section("What you're looking for", "Jobs outside it get a warning on Today, and the assistant points it out. Separate several with commas.",
        h("div", { class: "form-grid" },
          field("Target roles", roles, "wide"), field("Seniority", seniority), field("Max commute (min)", commute),
          field("Areas", areas, "wide"),
          h("div", { class: "field wide" }, "Work models you accept", h("div", { class: "row" }, models)))),
      section("Practical", "Your salary expectation fills new applications; the language is the default for interview preps.",
        h("div", { class: "form-grid" },
          field("Salary expectation", salary), field("Notice period", notice), field("Available from", from),
          field("Interview prep language", language))),
      section("Working and study hours", "Study sessions are planned only in your study time, never in working hours or around interviews. Leave working hours empty if you're not working.",
        h("div", { class: "form-grid" },
          field("Work starts", workStart), field("Work ends", workEnd),
          h("div", { class: "field wide" }, "Weekend days", h("div", { class: "day-picks" }, weekendDays)),
          field("On work days, I can study from", studyStart), field("until", studyEnd),
          h("div", { class: "field wide" }, h("label", { class: "check" }, weekendFree, " I can also study on weekends")),
          field("On weekends, from", weekendStart), field("until", weekendEnd),
          field("At most per day (minutes)", maxPerDay)),
        !(st.study_start && st.study_end) && !st.weekend_free && h("p", { class: "small", style: "color:var(--amber)" }, "Set your study hours to schedule interview preps.")),
      section("Weekly goals", "Shown on Today as this week's progress (weeks start on Monday).",
        h("div", { class: "form-grid" }, field("Applications per week", goalApps), field("Practice answers per week", goalPractice))),
      h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "primary", onclick: save }, "Save"))));
}

// LinkedIn: no API gives your network, so the contacts come from LinkedIn's own data export, uploaded here.
function linkedinSection(li, section, refresh) {
  const when = (iso) => (iso ? formatDate(iso.slice(0, 10)) : "");
  const fileInput = h("input", { type: "file", accept: ".zip,.csv", style: "display:none" });
  const button = h("button", { class: "primary", onclick: () => fileInput.click() }, li.contacts ? "Update my LinkedIn contacts" : "Import my LinkedIn contacts");
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    if (!file) return;
    button.disabled = true;
    button.textContent = "Importing…";
    const r = await attempt(() => upload("/api/linkedin/import", file));
    if (r) { toast(`${r.contacts} contacts${r.profile ? " and your profile" : ""} imported`); refresh(); }
    else { button.disabled = false; button.textContent = "Update my LinkedIn contacts"; }
  });
  const result = h("div");
  const check = async (e) => {
    e.target.disabled = true;
    const r = await attempt(() => call("linkedin.check"));
    e.target.disabled = false;
    if (!r) return;
    const row = (i) => h("div", { class: "issue" },
      h("div", { class: "small" }, h("b", { style: `color:var(${i.severity === "fix" ? "--amber" : "--muted"})` }, i.severity === "fix" ? "To fix · " : "Info · "), i.message),
      (i.cv || i.linkedin) && h("div", { class: "vs small" },
        i.cv && [h("span", { class: "muted" }, "CV"), h("span", { dir: "auto" }, i.cv)],
        i.linkedin && [h("span", { class: "muted" }, "LinkedIn"), h("span", { dir: "auto" }, i.linkedin)]));
    mount(result, h("div", { class: "stack-tight" },
      h("div", { class: "row" },
        h("b", {}, r.issues.length ? `${r.fix} to fix, ${r.issues.length - r.fix} to look at` : "Your LinkedIn and your CV agree ✓"),
        h("span", { class: "spacer" }),
        r.issues.length > 0 && h("a", { class: "btn small", href: chatHref("Run linkedin.check and help me fix the differences between my LinkedIn and my master CV: for each one, tell me which side looks right and give me the exact text to paste on LinkedIn.") }, "Fix with the assistant")),
      r.issues.map(row)));
  };
  return section("LinkedIn",
    "LinkedIn gives no app access to your network, so Ezri uses your own LinkedIn data export. Only names, companies, positions, links and your profile are kept; the zip itself isn't stored.",
    h("div", { class: "small" }, li.contacts
      ? `${li.contacts} contacts, imported ${when(li.contacts_imported_at)}${li.profile ? ` · profile imported ${when(li.profile_imported_at)}` : " · no profile yet"}.`
      : "Not imported yet: applications will then show who you know there, to ask for a referral."),
    h("ol", { class: "small muted", style: "margin:0; padding-left:18px" },
      h("li", {}, h("a", { href: li.export_url, target: "_blank", rel: "noopener" }, "Request your data on LinkedIn ↗"),
        ": pick ", h("b", {}, "Connections"), ", ", h("b", {}, "Profile"), ", ", h("b", {}, "Positions"), ", ", h("b", {}, "Education"), " and ", h("b", {}, "Skills"), "."),
      h("li", {}, "LinkedIn emails you a link within about 10 minutes: download the zip."),
      h("li", {}, "Upload it here. Do it again every few weeks to keep your contacts current.")),
    h("div", { class: "row" }, button, fileInput, h("span", { class: "spacer" }),
      h("button", { disabled: !li.profile, title: li.profile ? "" : "Import an export that includes your profile first", onclick: check }, "Check LinkedIn vs my CV")),
    result);
}
