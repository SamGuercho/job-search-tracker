"""Achievements with several wordings + coverage-based selection."""
import unittest

from jobtracker import registry
from jobtracker.services import cv_builder, master_cv
from tests.test_core import reset  # sets the temp data/output env vars first


def chosen(cv):
    return [b for e in cv.experiences for b in e.bullets]


class VariantSelectionTests(unittest.TestCase):
    def setUp(self):
        reset()
        exp = master_cv.get_master().experiences[0]
        # a clean experience with controlled achievements
        for b in list(exp.bullets):
            master_cv.delete_bullet(b.id)
        self.exp = exp
        self.a = master_cv.add_bullet(exp.id, "Built an evaluation pipeline with guardrails.", priority=1)
        self.v = master_cv.add_variant(self.a.id, "Built an evaluation pipeline in Python monitored with OpenTelemetry for observability.")
        self.b = master_cv.add_bullet(exp.id, "Shipped guardrails for compliance.")

    def test_one_wording_per_achievement(self):
        cv = cv_builder.select_bullets(["guardrails", "OpenTelemetry", "observability"])
        ids = [b.id for b in chosen(cv)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_best_wording_is_chosen_for_new_coverage(self):
        cv = cv_builder.select_bullets(["OpenTelemetry", "observability", "guardrails"])
        a = next(b for b in chosen(cv) if b.id == self.a.id)
        self.assertEqual(a.variant_id, self.v.id)       # covers 2 keywords vs 1 for the main wording
        b = next(b for b in chosen(cv) if b.id == self.b.id)
        self.assertFalse(b.redundant)                   # it's the one that brings "guardrails"

    def test_redundant_bullets_are_marked_and_trimmed_first(self):
        cv = cv_builder.select_bullets(["guardrails"])
        flags = {b.id: b.redundant for b in chosen(cv) if b.experience_id == self.exp.id}
        self.assertEqual(sorted(flags.values()), [False, True])
        while (removed := cv_builder._remove_weakest(cv)).fallback:  # bullets matching nothing go first
            pass
        self.assertTrue(removed.redundant)                             # then those repeating covered keywords

    def test_required_keywords_weigh_more(self):
        master_cv.add_bullet(self.exp.id, "Led a SQL migration.", priority=5)
        cv = cv_builder.select_bullets(["SQL", "OpenTelemetry"], required=["OpenTelemetry"])
        first = max(chosen(cv), key=lambda b: b.gain)
        self.assertIn("OpenTelemetry", first.matched)

    def test_variant_crud_and_keyword_detection(self):
        self.assertIn("Python", self.v.keywords)  # known keyword auto-detected from the text
        registry.run("cv.variant.update", {"id": self.v.id, "fields": {"text": "Monitored it with OpenTelemetry."}})
        b = master_cv.get_master().experiences[0].bullets[0]
        self.assertEqual(b.variants[0].text, "Monitored it with OpenTelemetry.")
        registry.run("cv.variant.delete", {"id": self.v.id})
        self.assertEqual(master_cv.get_master().experiences[0].bullets[0].variants, [])


if __name__ == "__main__":
    unittest.main()


class DraftTests(unittest.TestCase):
    def setUp(self):
        reset()
        from jobtracker.services import wording
        self.wording = wording
        self.orig = wording.llm.structured
        exp = master_cv.get_master().experiences[0]
        self.b = master_cv.add_bullet(exp.id, "Built a GPT-based assistant used by 200 analysts.", lead="Copilot:")

    def tearDown(self):
        self.wording.llm.structured = self.orig

    def fake(self, drafts):
        def answer(instructions, text, schema):
            if schema.__name__ == "_Verdicts":  # the fact-check: every draft is true
                return schema.model_validate({"verdicts": [{"draft": i, "true": True, "why": ""}
                                                           for i in range(1, text.count("DRAFT ") + 1)]})
            return schema.model_validate(
                {"drafts": [{"edit": "swap", "swaps": [], "evidence": "GPT-based", **d} for d in drafts]})
        self.wording.llm.structured = answer

    def test_drafts_with_invented_numbers_are_rejected(self):
        self.fake([{"lead": "Copilot:", "text": "Built an LLM-based assistant used by 200 analysts."},
                   {"lead": "Copilot:", "text": "Built an LLM assistant that cut work by 40% for 200 analysts."},
                   {"lead": "Copilot:", "text": "Built a GPT-based assistant used by 200 analysts."}])
        r = registry.run("cv.variant.draft", {"bullet_id": self.b.id, "keywords": ["LLM"]})
        self.assertEqual([d["text"] for d in r["drafts"]], ["Built an LLM-based assistant used by 200 analysts."])
        self.assertEqual(r["drafts"][0]["covers"], ["LLM"])
        reasons = " ".join(x["reason"] for x in r["rejected"])
        self.assertIn("40", reasons)
        self.assertIn("existing", reasons)
        self.assertEqual(master_cv.get_master().experiences[0].bullets[-1].variants, [])  # nothing saved

    def test_user_facts_allow_new_numbers(self):
        self.fake([{"lead": "Copilot:", "text": "Built a GPT assistant for 200 analysts in 3 months."}])
        r = registry.run("cv.variant.draft", {"bullet_id": self.b.id, "facts": "It took 3 months."})
        self.assertEqual(len(r["drafts"]), 1)


class GeneralCvTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_general_cv_has_every_achievement_in_main_wording(self):
        exp = master_cv.get_master().experiences[0]
        b = master_cv.add_bullet(exp.id, "Main wording.", priority=1)
        master_cv.add_variant(b.id, "Other wording with Python.")
        cv = cv_builder.select_bullets([], everything=True)
        mine = next(x for x in chosen(cv) if x.id == b.id)
        self.assertEqual((mine.text, mine.variant_id), ("Main wording.", ""))
        total = sum(len(e.bullets) for e in master_cv.get_master().experiences)
        self.assertEqual(len(chosen(cv)), total)

    def test_general_cv_files_go_to_master_cv_folder(self):
        r = registry.run("cv.generate_general")
        self.assertTrue(r["docx"].startswith("Master CV/"))
