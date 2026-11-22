// Profile: who you are for the app. Name and LinkedIn are your master CV's (one place, also on your CV header);
// the first name is how Today greets you. LinkedIn goes into referral messages the assistant drafts.
import { call } from "../api.js";
import { attempt, field, h, mount } from "../ui.js";

export async function renderProfile(root) {
  const p = await call("profile.get");
  const name = h("input", { name: "name", value: p.name, autocomplete: "name", dir: "auto" });
  const first = h("input", { name: "preferred_name", value: p.preferred_name, placeholder: p.first_name || "e.g. Sam", dir: "auto" });
  const linkedin = h("input", { name: "linkedin", value: p.linkedin, type: "url", placeholder: "https://www.linkedin.com/in/your-name" });

  const save = async () => {
    const saved = await attempt(() => call("profile.update", { name: name.value, preferred_name: first.value, linkedin: linkedin.value }), "Profile saved");
    if (saved) renderProfile(root);
  };

  mount(root,
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Profile"),
      h("div", { class: "sub muted" }, "How Ezri greets you, and what it puts in the messages it drafts for you"))),
    h("section", { class: "card stack" },
      h("div", { class: "form-grid" },
        field("Your name", name),
        field("First name for greetings", first),
        field("LinkedIn profile", linkedin, "wide")),
      h("p", { class: "small muted" }, "Your LinkedIn goes into the referral messages the assistant drafts, so people can see who you are. ",
        p.linkedin && h("a", { href: p.linkedin, target: "_blank", rel: "noopener" }, "Open your profile ↗")),
      h("p", { class: "small muted" }, "Your name and LinkedIn are your master CV's: changing them here changes your CV header too."
        + (p.email ? ` Email: ${p.email} (edit it on the Master CV page).` : "")),
      h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "primary", onclick: save }, "Save"))));
}
