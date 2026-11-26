"""Projects (work outside a company) and the order of the CV's sections."""
import json
import unittest

from docx import Document

from jobtracker import registry
from jobtracker.models import DEFAULT_SECTIONS, MasterCV
from jobtracker.services import cv_builder, jd_match, master_cv
from tests.test_core import TMP, reset  # sets the temp data/output env vars first


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


class ProjectTests(unittest.TestCase):
    def setUp(self):
        reset()
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
        self.assertIn("Open Recipes, Open-source maintainer • github.com/ada/recipes", text)
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

    def test_empty_projects_section_is_not_printed(self):
        registry.run("cv.project.delete", {"id": self.project["id"]})
        r = cv_builder.generate_cv(["Kubernetes"], company_name="NoProjects", export_pdf=False)
        self.assertNotIn("PROJECTS", headings(TMP / "out" / r["docx"]))


if __name__ == "__main__":
    unittest.main()
