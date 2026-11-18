"""Application process graph: operations, derived status/stage, tools, migration."""
import unittest

from tests.test_core import reset  # sets the temp data/output env vars first
from jobtracker import registry
from jobtracker.models import Company
from jobtracker.process import DEFAULT_STEPS, GraphError, Node, default_process
from jobtracker.services import companies


def names(p):
    return [n.name for n in p.order()]


class GraphTests(unittest.TestCase):
    def test_default_process_is_off(self):
        p = default_process()
        self.assertEqual(names(p), DEFAULT_STEPS)
        self.assertEqual(p.status(), "Not applied")
        self.assertEqual(p.closed.state, "todo")

    def test_insert_between_after_before_append(self):
        p = default_process()
        p.insert_between(p.node("First HR interview").id, p.node("Reference check").id, Node(name="Technical"))
        p.insert_before("Applied", Node(name="Referral asked"))
        p.insert_after("Reference check", Node(name="Offer", kind="offer"))
        p.append(Node(name="Signed"))
        self.assertEqual(names(p), ["Referral asked", "Applied", "Response received", "First HR interview",
                                    "Technical", "Reference check", "Offer", "Signed"])

    def test_branch_rejoins_and_delete_reconnects(self):
        p = default_process()
        p.add_branch("Response received", Node(name="Take-home"), join="Reference check")
        self.assertIn(p.node("Take-home").id, p.successors(p.node("Response received").id))
        p.remove("First HR interview")
        self.assertIn(p.node("Reference check").id, p.successors(p.node("Response received").id))

    def test_loops_and_closed_rules(self):
        p = default_process()
        with self.assertRaises(GraphError):
            p.add_branch("Reference check", Node(name="Back"), join="Applied")
        with self.assertRaises(GraphError):
            p.remove(p.closed.id)

    def test_status_and_stage(self):
        p = default_process()
        p.node("Applied").state = "done"
        self.assertEqual((p.status(), p.stage()), ("Applied", "Applied"))
        p.node("First HR interview").state = "planned"
        self.assertEqual((p.status(), p.stage()), ("Applied", "First HR interview"))
        p.node("Response received").state = "done"
        self.assertEqual(p.status(), "In process")
        p.closed.state = "done"
        self.assertEqual(p.status(), "Rejected")

    def test_legacy_steps_are_migrated(self):
        old = {"name": "X", "status": "Interviewing", "steps": [
            {"id": "a", "name": "Application sent", "status": "done"},
            {"id": "b", "name": "HR call", "status": "planned", "date": "2026-12-01"}]}
        c = Company.model_validate(old)
        self.assertEqual(names(c.process), ["Application sent", "HR call"])
        self.assertEqual((c.status, c.process.stage()), ("Applied", "HR call"))
        bare = Company.model_validate({"name": "Y", "status": "Applied", "steps": []})
        self.assertEqual(bare.status, "Not applied")  # a status alone is not trusted
        gone = Company.model_validate({"name": "Z", "status": "Withdrawn", "steps": []})
        self.assertEqual(gone.status, "Rejected")


class ToolTests(unittest.TestCase):
    def setUp(self):
        reset()
        companies.create_company(name="Nimbus", position="Senior Applied AI Scientist")
        companies.create_company(name="Globex", position="AI Engineer")

    def test_new_application_is_not_applied(self):
        c = registry.run("company.create", {"name": "Initech", "position": "Data Scientist"})
        self.assertEqual(c["status"], "Not applied")

    def test_find_by_name_or_position(self):
        self.assertEqual(registry.run("application.find", {"query": "nim"})[0]["name"], "Nimbus")
        self.assertEqual(registry.run("application.find", {"query": "AI engineer"})[0]["name"], "Globex")

    def test_tools_accept_names_for_company_and_step(self):
        v = registry.run("process.set_state", {"company": "Nimbus", "step": "applied", "state": "done"})
        self.assertEqual(v["status"], "Applied")
        v = registry.run("process.add_step", {"company": "nimbus", "name": "Technical interview",
                                              "after": "First HR interview", "before": "Reference check",
                                              "state": "planned", "date": "2026-12-01"})
        self.assertEqual(v["stage"], "Technical interview")
        self.assertTrue(any("Technical interview" in line for line in v["outline"]))
        v = registry.run("process.close", {"company": "Nimbus", "reason": "Position filled"})
        self.assertEqual(v["status"], "Rejected")
        self.assertEqual(registry.run("process.reopen", {"company": "Nimbus"})["status"], "Applied")

    def test_ambiguous_company_lists_candidates(self):
        with self.assertRaises(companies.NotFound) as ctx:
            registry.run("process.get", {"company": "AI"})
        self.assertIn("Nimbus", str(ctx.exception))
        self.assertIn("Globex", str(ctx.exception))

    def test_status_cannot_be_set_by_hand(self):
        c = companies.find_by_name("Nimbus")
        with self.assertRaises(Exception):
            registry.run("company.update", {"id": c.id, "fields": {"status": "Offer"}})


if __name__ == "__main__":
    unittest.main()
