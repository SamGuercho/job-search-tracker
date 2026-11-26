"""Projects (work outside a company) and the order of the CV's sections."""
import json
import unittest

from docx import Document

from jobtracker import registry
from jobtracker.cv_templates import elegant, fonts
from jobtracker.cv_templates.elegant import entry_fits
from jobtracker.models import DEFAULT_SECTIONS, JobMatch, MasterCV, Requirement
from jobtracker.services import companies, cv_builder, jd_match, keyword_stats, master_cv
from tests.test_core import TMP, reset  # sets the temp data/output env vars first


def no_applications():
    """Keyword history starts empty (the seed has example applications)."""
    for c in companies.list_companies():
        companies.delete_company(c.id)


def headings(docx_path) -> list[str]:
    """The section titles of a generated CV, top to bottom."""
    titles = {"SUMMARY", "SKILLS", "PROFESSIONAL EXPERIENCE", "PROJECTS", "EDUCATION", "LANGUAGES"}
    return [p.text for p in Document(str(docx_path)).paragraphs if p.text in titles]


class SectionOrderModelTests(unittest.TestCase):
    def test_default_puts_projects_under_experience(self):
        self.assertEqual(MasterCV().sections,
                         ["summary", "skills", "experience", "projects", "education", "languages"])

    def test_saved_cv_without_the_field_gets_the_default(self):
        old = {"profile": {"name": "Ada"}, "experiences": [{"company": "Acme", "bullets": []}]}
        cv = MasterCV.model_validate(json.loads(json.dumps(old)))
        self.assertEqual(cv.sections, DEFAULT_SECTIONS)
        self.assertEqual(cv.projects, [])

    def test_unknown_dropped_missing_put_back_in_place(self):
        cv = MasterCV(sections=["projects", "experience", "bogus", "projects", "education"])
        self.assertEqual(cv.sections, ["summary", "skills", "projects", "experience", "education", "languages"])
        # an order saved before a section existed: the new one lands where the default puts it
        cv = MasterCV(sections=["summary", "experience", "skills", "education", "languages"])
        self.assertEqual(cv.sections, ["summary", "experience", "projects", "skills", "education", "languages"])


class KeywordImportanceTests(unittest.TestCase):
    def setUp(self):
        reset()
        no_applications()

    def test_counts_once_per_application_and_finds_strong_and_missing(self):
        companies.create_company(name="A", keywords=["Python", "SQL"])
        b = companies.create_company(name="B", keywords=["python"])
        with companies.store.transaction() as book:
            c = next(x for x in book.companies if x.id == b.id)
            c.job_match = JobMatch(text_hash="x", requirements=[
                Requirement(term="Python", keywords=["Python"], required=True),
                Requirement(term="Rust", keywords=[], required=True)])
        companies.create_company(name="C", keywords=["SQL", "Tableau"])
        rows = {r["keyword"].lower(): r for r in keyword_stats.stats()}
        self.assertEqual((rows["python"]["applications"], rows["python"]["required"]), (2, 1))
        self.assertEqual(rows["sql"]["applications"], 2)
        self.assertEqual(keyword_stats.strong_keywords(), {"python", "sql"})
        report = registry.run("cv.keywords.importance", {})
        self.assertEqual(report["applications"], 3)
        self.assertEqual([r["keyword"] for r in report["missing"]], ["Rust"])  # asked, nowhere in the CV


class EntryLayoutTests(unittest.TestCase):
    def test_long_header_puts_the_organisation_on_its_own_line(self):
        self.assertTrue(entry_fits("MSc Data Science", "Université Paris-Saclay (Paris)", "2016 - 2018"))
        long_degree = "Master of Science in Industrial Engineering and Management, specialisation Operations Research"
        self.assertFalse(entry_fits(long_degree, "Technion - Israel Institute of Technology (Haifa)", "2016 - 2018"))

    @unittest.skipUnless(fonts.installed("STIX Two Text"), "needs the STIX Two Text font files")
    def test_text_runs_almost_up_to_the_dates(self):
        when = "2016 - 2018"
        room = elegant.USABLE_W_PT - fonts.width_pt(when, "STIX Two Text", 10) - fonts.width_pt(", ", "STIX Two Text", 10)
        title, org = "Master of Science in Industrial Engineering", "Technion - Israel Institute of Technology"
        while (fonts.width_pt(title, "STIX Two Text", 10, bold=True)
               + fonts.width_pt(org + " (Haifa)", "STIX Two Text", 10, italic=True)) < room - 10:
            org += " x"
        self.assertTrue(entry_fits(title, org + " (Haifa)", when))           # ~10pt of air left before the dates: one line
        self.assertFalse(entry_fits(title, org + " xxxx (Haifa)", when))  # a few more letters: two lines

    def test_title_too_long_for_the_dates_puts_them_with_the_organisation(self):
        title = "Master of Science in Industrial Engineering and Management, specialisation Operations Research and Logistics"
        self.assertEqual(elegant.entry_layout(title, "Technion (Haifa)", "2014 - 2016"), "org_dates")
        self.assertEqual(elegant.entry_layout("MSc", "Technion (Haifa)", "2014 - 2016"), "one")
        reset()
        master_cv.set_education([{"school": "Technion", "degree": title, "location": "Haifa", "start": "2014",
                                  "end": "2016"}])
        r = cv_builder.generate_cv(["SQL"], company_name="Edu", export_pdf=False)
        paras = [p.text.replace("\u00a0", " ") for p in Document(str(TMP / "out" / r["docx"])).paragraphs]
        i = paras.index(title)
        self.assertEqual(paras[i + 1], "Technion (Haifa)\t2014 - 2016")

    def test_without_font_files_counts_characters(self):
        self.assertTrue(entry_fits("MSc Data Science", "Université Paris-Saclay (Paris)", "2016 - 2018", "No Such Font"))
        self.assertFalse(entry_fits("x" * 80, "y" * 60, "2016 - 2018", "No Such Font"))

    def test_rendered_two_lines_when_too_long(self):
        reset()
        long_degree = "Master of Science in Industrial Engineering and Management, specialisation Operations Research"
        master_cv.set_education([{"school": "Technion - Israel Institute of Technology", "degree": long_degree,
                                  "location": "Haifa", "start": "2016", "end": "2018"}])
        r = cv_builder.generate_cv(["SQL"], company_name="Edu", export_pdf=False)
        paras = [p.text.replace("\u00a0", " ") for p in Document(str(TMP / "out" / r["docx"])).paragraphs]
        i = paras.index(f"{long_degree}\t2016 - 2018")
        self.assertEqual(paras[i + 1], "Technion - Israel Institute of Technology (Haifa)")


class FontTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_default_and_choice_are_used_by_generated_cvs(self):
        self.assertEqual(MasterCV().font, "STIX Two Text")
        listed = registry.run("cv.font.list", {})
        self.assertEqual((listed["current"], listed["fonts"][0]["name"]), ("STIX Two Text", "STIX Two Text"))
        with self.assertRaises(ValueError):
            registry.run("cv.font.set", {"font": "Comic Sans of Nowhere"})
        other = next((f["name"] for f in listed["fonts"] if f["name"] != "STIX Two Text"), None)
        if other is None:
            self.skipTest("only the default font is installed here")
        self.assertEqual(registry.run("cv.font.set", {"font": other.lower()}), other)  # any case, saved as listed
        r = cv_builder.generate_cv(["SQL"], company_name="Font", export_pdf=False)
        self.assertEqual(Document(str(TMP / "out" / r["docx"])).styles["Normal"].font.name, other)


class ProjectTests(unittest.TestCase):
    def setUp(self):
        reset()
        no_applications()
        self.project = registry.run("cv.project.add", {"name": "Open Recipes", "role": "Open-source maintainer",
                                                       "link": " https://github.com/ada/recipes/ ", "start": "2023"})
        self.bullet = registry.run("cv.bullet.add", {
            "experience_id": self.project["id"], "text": "Built a Kubernetes operator used by 40 teams.",
            "keywords": ["Kubernetes"]})

    def test_crud(self):
        pid = self.project["id"]
        self.assertEqual(self.project["link"], "https://github.com/ada/recipes/")
        registry.run("cv.project.update", {"id": pid, "fields": {"role": "Creator", "end": "2024"}})
        second = registry.run("cv.project.add", {"name": "Thesis", "role": "Master's thesis"})
        registry.run("cv.project.move", {"id": second["id"], "offset": -1})
        cv = master_cv.get_master()
        self.assertEqual([p.name for p in cv.projects], ["Thesis", "Open Recipes"])
        self.assertEqual((cv.projects[1].role, cv.projects[1].end), ("Creator", "2024"))
        self.assertEqual(len(cv.projects[1].bullets), 1)  # update leaves the bullets alone
        registry.run("cv.project.delete", {"id": pid})
        self.assertEqual([p.name for p in master_cv.get_master().projects], ["Thesis"])

    def test_bullets_and_wordings_work_like_experience_ones(self):
        registry.run("cv.bullet.update", {"id": self.bullet["id"], "fields": {"priority": 3}})
        v = registry.run("cv.variant.add", {"bullet_id": self.bullet["id"], "text": "Built a K8s operator for 40 teams."})
        registry.run("cv.variant.update", {"id": v["id"], "fields": {"keywords": ["K8s"]}})
        b = master_cv.get_master().projects[0].bullets[0]
        self.assertEqual((b.priority, b.variants[0].keywords), (3, ["K8s"]))
        self.assertIn("K8s", master_cv.keyword_vocabulary())        # project keywords are vocabulary
        self.assertIn("Kubernetes", master_cv.keyword_vocabulary())
        registry.run("cv.variant.delete", {"id": v["id"]})
        registry.run("cv.bullet.delete", {"id": self.bullet["id"]})
        self.assertEqual(master_cv.get_master().projects[0].bullets, [])

    def test_matching_counts_project_bullets(self):
        r = jd_match.match_job_description("We run everything on Kubernetes.", use_ai=False)
        k8s = next(m for m in r["matches"] if m.jd_term == "Kubernetes")
        self.assertTrue(k8s.literal)
        self.assertEqual(k8s.bullets, 1)

    def test_synonym_only_in_a_project_is_a_reword_gap(self):
        registry.run("cv.bullet.update", {"id": self.bullet["id"], "fields": {
            "text": "Built a K8s operator used by 40 teams.", "keywords": ["K8s"]}})
        master_cv.set_skills([])
        r = jd_match.match_job_description("Kubernetes is a must.", use_ai=False)
        k8s = next(m for m in r["reword"] if m.jd_term == "Kubernetes")
        self.assertEqual(k8s.reword_bullets[0]["id"], self.bullet["id"])

    def test_selection_picks_project_bullets(self):
        cv = cv_builder.select_bullets(["Kubernetes"])
        self.assertEqual([p.name for p in cv.projects], ["Open Recipes"])
        chosen = cv.projects[0].bullets[0]
        self.assertEqual((chosen.experience_id, chosen.matched), (self.project["id"], ["Kubernetes"]))
        self.assertFalse(chosen.fallback)

    def test_trimming_cuts_unmatched_project_bullets_first(self):
        for i in range(60):
            master_cv.add_bullet(self.project["id"], f"Side detail number {i} that matches nothing in the job at all.")
        cv = cv_builder.fit_estimate(cv_builder.select_bullets(["Kubernetes"]))
        kept = [b.id for b in cv.projects[0].bullets]
        self.assertIn(self.bullet["id"], kept)
        self.assertTrue(any(b.experience_id == self.project["id"] for b in cv.removed))

    def test_generated_cv_has_projects_in_the_chosen_order(self):
        r = cv_builder.generate_cv(["Kubernetes"], company_name="Order", export_pdf=False)
        self.assertIn(self.bullet["id"], [b["id"] for b in r["kept"]])
        doc = TMP / "out" / r["docx"]
        order = headings(doc)
        self.assertLess(order.index("PROFESSIONAL EXPERIENCE"), order.index("PROJECTS"))
        text = "\n".join(p.text for p in Document(str(doc)).paragraphs)
        self.assertIn("Open Recipes, Open-source maintainer", text)   # the name is the link: no extra link text
        self.assertNotIn("github.com/ada/recipes", text)
        links = [r.target_ref for r in Document(str(doc)).part.rels.values() if "hyperlink" in r.reltype]
        self.assertIn("https://github.com/ada/recipes/", links)
        self.assertIn("Built a Kubernetes operator used by 40 teams.", text)

        chosen = ["summary", "projects", "experience", "skills", "education", "languages"]
        self.assertEqual(registry.run("cv.sections.set", {"sections": chosen}), chosen)
        self.assertEqual(master_cv.get_master().sections, chosen)
        r = cv_builder.generate_cv(["Kubernetes"], company_name="Order", export_pdf=False)
        order = headings(TMP / "out" / r["docx"])
        expected = [t for t in ("SUMMARY", "PROJECTS", "PROFESSIONAL EXPERIENCE", "SKILLS", "EDUCATION", "LANGUAGES")
                    if t in order]
        self.assertEqual(order, expected)
        self.assertLess(order.index("PROJECTS"), order.index("PROFESSIONAL EXPERIENCE"))

    def test_project_matching_nothing_is_left_out_of_a_job_cv(self):
        side = master_cv.add_project("Garden robot", role="Personal")
        robot = master_cv.add_bullet(side.id, "Built a watering robot for my balcony.")
        cv = cv_builder.select_bullets(["Kubernetes"])
        self.assertEqual([p.name for p in cv.projects], ["Open Recipes"])
        self.assertEqual([p.name for p in cv.left_out], ["Garden robot"])
        self.assertIn("Built a watering robot for my balcony.", [b.text for b in cv.removed])
        self.assertEqual(len(cv.experiences), 3)  # experiences keep their unmatched roles
        forced = cv_builder.select_bullets(["Kubernetes"], include=[robot.id])
        self.assertIn("Garden robot", [p.name for p in forced.projects])

    def test_project_repeating_experience_is_cut_before_any_experience_bullet(self):
        dup = master_cv.add_bullet(self.project["id"], "Wrote Python scripts for the recipe importer.", ["Python"])
        cv = cv_builder.select_bullets(["Python", "Kubernetes"])
        first = cv_builder._remove_weakest(cv)
        self.assertEqual(first.id, dup.id)            # Python is proven by experience: the project goes first
        self.assertTrue(first.project and not first.unique)
        k8s = next(b for b in cv.projects[0].bullets if b.id == self.bullet["id"])
        self.assertEqual(k8s.unique, ["Kubernetes"])  # only the project proves it
        while (b := cv_builder._remove_weakest(cv)) and b.id != self.bullet["id"]:
            self.assertFalse(b.project)               # every experience bullet goes before it
        self.assertEqual(cv.left_out[-1].name, "Open Recipes")  # emptied: the project leaves the CV

    def test_low_importance_unique_keyword_does_not_protect(self):
        for name, kws in (("A", ["Python", "SQL"]), ("B", ["Python", "SQL"]), ("C", ["Python"])):
            companies.create_company(name=name, keywords=kws)
        for k in ("Python", "SQL", "Kubernetes"):
            self.assertIn(k.lower(), keyword_stats.importance() | {"kubernetes": 0})
        cv = cv_builder.select_bullets(["Python", "SQL", "Kubernetes"])
        b = cv.projects[0].bullets[0]
        self.assertEqual(b.unique, [])  # never asked before, below the job's median: experience first
        cv = cv_builder.select_bullets(["Python", "SQL", "Kubernetes"], required=["Kubernetes"])
        self.assertEqual(cv.projects[0].bullets[0].unique, ["Kubernetes"])  # required by this job: kept

    def test_general_cv_keeps_projects_proving_a_strong_keyword(self):
        cv = cv_builder.select_bullets([], everything=True)
        self.assertEqual(cv.projects[0].bullets[0].unique, [])  # no history: nothing is strong yet
        for name in ("A", "B"):
            companies.create_company(name=name, keywords=["Kubernetes"])
        cv = cv_builder.select_bullets([], everything=True)
        self.assertEqual(cv.projects[0].bullets[0].unique, ["Kubernetes"])

    def test_full_master_cv_cuts_nothing(self):
        for i in range(40):
            master_cv.add_bullet(self.project["id"], f"Another detail number {i} written to fill a long page of text.")
        r = cv_builder.generate_full(export_pdf=False)
        self.assertEqual(r["removed"], [])
        self.assertTrue(r["docx"].endswith("-Full.docx"))
        self.assertGreater(r["estimated_pages"], 1)
        general = cv_builder.generate_general(export_pdf=False)
        self.assertTrue(general["removed"])         # the general CV still fits one page
        self.assertNotEqual(general["docx"], r["docx"])

    def test_empty_projects_section_is_not_printed(self):
        registry.run("cv.project.delete", {"id": self.project["id"]})
        r = cv_builder.generate_cv(["Kubernetes"], company_name="NoProjects", export_pdf=False)
        self.assertNotIn("PROJECTS", headings(TMP / "out" / r["docx"]))


if __name__ == "__main__":
    unittest.main()
