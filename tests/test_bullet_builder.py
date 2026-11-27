"""Bullet building: variants built around the job's word, What / How / Result, placing a job's words."""
import json
import os
import unittest

import openai

from jobtracker import registry
from jobtracker.models import MasterCV
from jobtracker.services import assistant, bullet_builder, master_cv, wording
from tests.test_assistant import FakeOpenAI, call
from tests.test_core import reset  # sets the temp data/output env vars first


def draft(text, lead="", edit="swap", swaps=(), evidence="GPT-based"):
    return {"evidence": evidence, "lead": lead, "text": text, "edit": edit, "swaps": list(swaps)}


def all_true(text, schema, false=()):
    """The fact-check's answer: every draft true, except the numbers in `false`."""
    n = text.count("\nDRAFT ") + text.startswith("DRAFT ")
    return schema.model_validate({"verdicts": [{"draft": i, "true": i not in false, "why": "stretch"}
                                               for i in range(1, n + 1)]})


class Scripted:
    """llm.structured stand-in: answers in order, records the prompts. The fact-check passes every draft
    unless `false` lists draft numbers it rejects."""

    def __init__(self, *answers, false=()):
        self.answers, self.prompts, self.false = list(answers), [], false

    def __call__(self, instructions, text, schema):
        if schema.__name__ == "_Verdicts":
            return all_true(text, schema, self.false)
        self.prompts.append(text)
        return schema.model_validate(self.answers.pop(0))


class VariantKeywordTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.orig = wording.llm.structured
        exp = master_cv.get_master().experiences[0]
        self.b = master_cv.add_bullet(exp.id, "Built a GPT-based assistant used by 200 analysts.")

    def tearDown(self):
        wording.llm.structured = self.orig

    def test_drafts_without_the_keyword_are_rejected_and_the_model_retries_once(self):
        fake = wording.llm.structured = Scripted(
            {"drafts": [draft("Developed a GPT-based assistant used by 200 analysts.")]},
            {"drafts": [draft("Built an LLM-based assistant used by 200 analysts.",
                              swaps=[{"phrase": "Built", "options": ["Developed", "Built", "Shipped 3"]},
                                     {"phrase": "not there", "options": ["x"]},
                                     {"phrase": "LLM", "options": ["AI"]}])]})
        r = registry.run("cv.variant.draft", {"bullet_id": self.b.id, "keywords": ["LLM"]})
        self.assertEqual(len(fake.prompts), 2)
        self.assertIn("WERE THROWN AWAY", fake.prompts[1])
        self.assertEqual([d["text"] for d in r["drafts"]], ["Built an LLM-based assistant used by 200 analysts."])
        d = r["drafts"][0]
        self.assertEqual((d["covers"], d["spans"], d["edit"]), (["LLM"], ["LLM"], "swap"))
        # a phrase not in the text, the keyword itself and options with new numbers are never offered
        self.assertEqual(d["swaps"], [{"phrase": "Built", "options": ["Developed"]}])
        self.assertIn("doesn't use 'LLM'", r["rejected"][0]["reason"])

    def test_a_keyword_with_nothing_behind_it_is_rejected(self):
        wording.llm.structured = Scripted({"drafts": [
            draft("Built a GPT-based assistant on Kubernetes used by 200 analysts.", evidence="cloud platform"),
            draft("Built a Kubernetes assistant used by 200 analysts.", evidence="")]})
        r = registry.run("cv.variant.draft", {"bullet_id": self.b.id, "keywords": ["Kubernetes"]})
        self.assertEqual(r["drafts"], [])
        self.assertEqual([x["reason"] for x in r["rejected"]],
                         ["no words of the original say it (cited “cloud platform”)", "no words of the original say it"])

    def test_the_fact_check_drops_a_stretched_keyword(self):
        wording.llm.structured = Scripted({"drafts": [
            draft("Built an LLM-based assistant used by 200 analysts."),
            draft("Built a GPT-based LLM assistant used by 200 analysts.", edit="restructure")]}, false=(2,))
        r = registry.run("cv.variant.draft", {"bullet_id": self.b.id, "keywords": ["LLM"]})
        self.assertEqual([d["text"] for d in r["drafts"]], ["Built an LLM-based assistant used by 200 analysts."])
        self.assertEqual(r["drafts"][0]["evidence"], "GPT-based")
        self.assertEqual(r["rejected"][-1]["reason"], "not true here: stretch")

    def test_no_retry_when_a_draft_uses_the_keyword(self):
        fake = wording.llm.structured = Scripted({"drafts": [
            draft("Built an LLM assistant used by 200 analysts."), draft("Built a chatbot used by 200 analysts.")]})
        r = registry.run("cv.variant.draft", {"bullet_id": self.b.id, "keywords": ["LLM"]})
        self.assertEqual((len(fake.prompts), len(r["drafts"])), (1, 1))

    def test_a_keyword_bolted_on_is_rejected(self):
        exp = master_cv.get_master().experiences[0]
        b = master_cv.add_bullet(exp.id, "Built a forecasting model for store replenishment.")
        wording.llm.structured = Scripted({"drafts": [
            draft("Built a demand forecasting model for store replenishment.", evidence="forecasting model"),
            draft("Built a forecasting model for store replenishment using demand forecasting.", edit="restructure",
                  evidence="forecasting model")]})
        r = registry.run("cv.variant.draft", {"bullet_id": b.id, "keywords": ["demand forecasting"]})
        self.assertEqual([d["text"] for d in r["drafts"]], ["Built a demand forecasting model for store replenishment."])
        self.assertIn("repeats 'forecasting'", r["rejected"][0]["reason"])

    def test_plural_keywords_offer_the_singular(self):
        fake = wording.llm.structured = Scripted({"drafts": []})
        registry.run("cv.variant.draft", {"bullet_id": self.b.id, "keywords": ["vector databases", "NLP"]})
        self.assertIn('TARGET KEYWORDS: vector databases (or "vector database"), NLP', fake.prompts[0])

    def test_a_form_of_the_word_counts(self):
        exp = master_cv.get_master().experiences[0]
        b = master_cv.add_bullet(exp.id, "Studied sales figures weekly.")
        wording.llm.structured = Scripted({"drafts": [draft("Analyzed sales figures weekly.", evidence="Studied sales figures")]},
                                           {"drafts": []})
        r = registry.run("cv.variant.draft", {"bullet_id": b.id, "keywords": ["data analysis"]})
        self.assertEqual(r["drafts"], [])  # "data" isn't there: not a form of the job's word
        wording.llm.structured = Scripted({"drafts": [draft("Analyzed weekly sales data.", evidence="Studied sales figures")]})
        r = registry.run("cv.variant.draft", {"bullet_id": b.id, "keywords": ["data analysis"]})
        self.assertEqual(r["drafts"][0]["covers"], ["data analysis"])
        self.assertEqual(sorted(r["drafts"][0]["spans"]), ["Analyzed", "data"])

    def test_the_bullets_parts_are_true_facts(self):
        registry.run("cv.bullet.update", {"id": self.b.id, "fields": {"parts": {"what": "Built an assistant",
                                                                               "result": "saved 5 hours a week"}}})
        fake = wording.llm.structured = Scripted({"drafts": [
            draft("Built an LLM assistant used by 200 analysts, saving 5 hours a week.", edit="restructure")]})
        r = registry.run("cv.variant.draft", {"bullet_id": self.b.id, "keywords": ["LLM"]})
        self.assertIn("saved 5 hours a week", fake.prompts[0])
        self.assertEqual(len(r["drafts"]), 1)  # the 5 comes from the parts: not invented


class PartsTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_parts_round_trip_and_old_files_load(self):
        exp = master_cv.get_master().experiences[0]
        b = registry.run("cv.bullet.add", {"experience_id": exp.id, "text": "Did it.",
                                           "parts": {"what": "Did it", "how": "carefully", "result": ""}})
        self.assertEqual(b["parts"]["how"], "carefully")
        registry.run("cv.bullet.update", {"id": b["id"], "fields": {"parts": {"what": "Did it again"}}})
        saved = next(x for x in master_cv.get_master().experiences[0].bullets if x.id == b["id"])
        self.assertEqual((saved.parts.what, saved.parts.how), ("Did it again", ""))
        registry.run("cv.bullet.update", {"id": b["id"], "fields": {"parts": {"what": " "}}})
        self.assertIsNone(next(x for x in master_cv.get_master().experiences[0].bullets if x.id == b["id"]).parts)
        old = {"experiences": [{"company": "Acme", "bullets": [{"text": "Old bullet"}]}]}
        self.assertIsNone(MasterCV.model_validate(old).experiences[0].bullets[0].parts)


class ComposeTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.orig = bullet_builder.llm.structured

    def tearDown(self):
        bullet_builder.llm.structured = self.orig

    def test_compose_keeps_the_users_numbers_only(self):
        exp = master_cv.get_master().experiences[0]
        fake = bullet_builder.llm.structured = Scripted({"drafts": [
            {"order": "how-led", "lead": "", "text": "Redesigned checkout by testing 4 layouts, cutting drop-off by 18%.",
             "swaps": [{"phrase": "Redesigned", "options": ["Rebuilt"]}]},
            {"order": "result-first", "lead": "", "text": "Cut drop-off by 25% by redesigning checkout.", "swaps": []},
            {"order": "compact", "lead": "", "text": "Redesigned checkout with A/B testing, cutting drop-off by 18%.",
             "swaps": []}]})
        r = registry.run("cv.bullet.compose", {"what": "Redesigned checkout", "how": "testing 4 layouts",
                                               "result": "cut drop-off by 18%", "keywords": ["A/B testing"],
                                               "owner_id": exp.id})
        self.assertEqual([d["order"] for d in r["drafts"]], ["how-led", "compact"])
        self.assertIn("25", r["rejected"][0]["reason"])
        self.assertEqual(r["drafts"][1]["covers"], ["A/B testing"])
        self.assertEqual(r["drafts"][0]["swaps"], [{"phrase": "Redesigned", "options": ["Rebuilt"]}])
        self.assertIn(exp.company, fake.prompts[0])
        self.assertEqual(master_cv.get_master().experiences[0].bullets, exp.bullets)  # nothing saved

    def test_no_result_first_without_a_number(self):
        bullet_builder.llm.structured = Scripted({"drafts": [
            {"order": "how-led", "lead": "", "text": "Built a help bot so buyers could self-serve.", "swaps": []},
            {"order": "result-first", "lead": "", "text": "Buyers could self-serve by building a help bot.",
             "swaps": []}]})
        r = registry.run("cv.bullet.compose", {"what": "Built a help bot", "result": "so buyers could self-serve"})
        self.assertEqual([d["order"] for d in r["drafts"]], ["how-led"])

    def test_compose_needs_the_what(self):
        with self.assertRaises(ValueError):
            registry.run("cv.bullet.compose", {"what": " ", "how": "x"})


class PlaceTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.orig = bullet_builder.llm.structured, wording.llm.structured
        exp = master_cv.get_master().experiences[0]
        self.a = master_cv.add_bullet(exp.id, "Built GPT-based chat tools for support.")
        self.b = master_cv.add_bullet(exp.id, "Ran weekly sales reviews.")

    def tearDown(self):
        bullet_builder.llm.structured, wording.llm.structured = self.orig

    def test_keywords_are_grouped_by_achievement_and_the_rest_has_no_slot(self):
        slots = {"slots": [
            {"keyword": "LLM", "bullet_id": self.a.id, "why": "GPT is an LLM"},
            {"keyword": "chatbot", "bullet_id": self.a.id, "why": "chat tools"},
            {"keyword": "Kubernetes", "bullet_id": "", "why": "never mentioned"}]}
        seen = []

        def fake(instructions, text, schema):  # one llm module: answer by the schema asked
            if schema.__name__ == "_Slots":
                return schema.model_validate(slots)
            if schema.__name__ == "_Verdicts":
                return all_true(text, schema)
            seen.append(text)
            return schema.model_validate({"drafts": [draft("Built LLM chatbot tools for support.")]})  # cites GPT-based
        wording.llm.structured = fake
        r = registry.run("cv.keywords.place", {"keywords": ["LLM", "chatbot", "Kubernetes"]})
        self.assertEqual(len(seen), 1)
        self.assertIn("TARGET KEYWORDS: LLM, chatbot", seen[0])
        p = r["placements"][0]
        self.assertEqual((p["bullet_id"], p["keywords"]), (self.a.id, ["LLM", "chatbot"]))
        self.assertEqual(r["no_slot"], [{"keyword": "Kubernetes", "why": "never mentioned"}])


class AssistantComposeTests(unittest.TestCase):
    def setUp(self):
        reset()
        os.environ["OPENAI_API_KEY"] = "test"
        self._orig, openai.OpenAI = openai.OpenAI, FakeOpenAI
        self._structured = bullet_builder.llm.structured
        FakeOpenAI.sent = []

    def tearDown(self):
        openai.OpenAI, bullet_builder.llm.structured = self._orig, self._structured
        os.environ["OPENAI_API_KEY"] = ""

    def test_composed_bullet_is_proposed_with_its_parts_and_the_card_shows_the_original(self):
        exp = master_cv.get_master().experiences[0]
        bullet_builder.llm.structured = Scripted({"drafts": [
            {"order": "how-led", "lead": "", "text": "Automated invoicing by scripting the exports.", "swaps": []}]})
        parts = {"what": "Automated invoicing", "how": "scripting the exports", "result": ""}
        FakeOpenAI.script = [
            ([call("cv-bullet-compose", {**parts, "owner_id": exp.id})], ""),
            ([call("cv-bullet-add", {"experience_id": exp.id, "text": "Automated invoicing by scripting the exports.",
                                     "parts": parts}, "c2")], ""),
            ([], "Here is the achievement: approve it to add it.")]
        chat = registry.run("chat.send", {"message": "Add: I automated invoicing by scripting the exports"})
        events = [m["tool"] for m in chat["messages"] if m.get("tool")]
        self.assertEqual([(e["action"], e["status"]) for e in events],
                         [("cv.bullet.compose", "done"), ("cv.bullet.add", "pending")])
        self.assertEqual(events[1]["args"]["parts"]["how"], "scripting the exports")

        b = exp.bullets[0]
        out = assistant._tool_outcome("cv-variant-add", registry.ACTIONS["cv.variant.add"],
                                      {"bullet_id": b.id, "text": "Other words."})
        self.assertEqual(out.message.tool.result, {"original": b.text})  # no lead proposed: texts compared
        self.assertIn("waiting_for_user_approval", json.dumps(out.output))


if __name__ == "__main__":
    unittest.main()
