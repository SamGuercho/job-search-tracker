"""Assistant loop with a scripted fake OpenAI client (no network)."""
import json
import os
import unittest
from types import SimpleNamespace as NS

import openai

from jobtracker import registry
from jobtracker.services import assistant, companies
from tests.test_core import reset  # sets the temp data/output env vars first


def call(name, args, call_id="c1"):
    return NS(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)


class FakeOpenAI:
    """Returns the scripted responses in order and records what it was sent."""
    script, sent = [], []

    def __init__(self, *a, **k):
        self.responses = self

    def create(self, **kwargs):
        FakeOpenAI.sent.append(kwargs)
        output, text = FakeOpenAI.script.pop(0)
        return NS(id=f"resp{len(FakeOpenAI.sent)}", output=output, output_text=text)


class AssistantTests(unittest.TestCase):
    def setUp(self):
        reset()
        os.environ["OPENAI_API_KEY"] = "test"
        self._orig, openai.OpenAI = openai.OpenAI, FakeOpenAI
        FakeOpenAI.sent = []

    def tearDown(self):
        openai.OpenAI = self._orig
        os.environ["OPENAI_API_KEY"] = ""

    def test_every_action_is_a_tool_except_chat(self):
        names = {t["name"] for t in assistant.tools()}
        self.assertIn("workflow-new_application", names)
        self.assertNotIn("chat-send", names)
        self.assertEqual(len(names), len([a for a in registry.ACTIONS if not a.startswith("chat.")]) + len(assistant.CHAT_TOOLS))

    def test_tool_call_runs_action_then_answers(self):
        FakeOpenAI.script = [([call("company-create", {"name": "Nimbus", "position": "AI Engineer"})], ""),
                             ([], "Added [Nimbus](#/company/x).")]
        chat = registry.run("chat.send", {"message": "Add Nimbus"})
        self.assertIsNotNone(companies.find_by_name("Nimbus"))
        roles = [m["role"] for m in chat["messages"]]
        self.assertEqual(roles, ["user", "tool", "assistant"])
        self.assertEqual(FakeOpenAI.sent[1]["previous_response_id"], "resp1")
        self.assertEqual(FakeOpenAI.sent[1]["input"][0]["type"], "function_call_output")

    def test_destructive_action_waits_for_approval(self):
        target = companies.list_companies()[0]
        FakeOpenAI.script = [([call("company-delete", {"id": target.id})], ""),
                             ([], "Please approve the deletion.")]
        chat = registry.run("chat.send", {"message": "Delete it"})
        event = next(m["tool"] for m in chat["messages"] if m["role"] == "tool")
        self.assertEqual(event["status"], "pending")
        self.assertEqual(companies.get_company(target.id).id, target.id)  # still there

        FakeOpenAI.script = [([], "Deleted.")]
        chat = registry.run("chat.resolve", {"chat_id": chat["id"], "event_id": event["id"], "approve": True})
        with self.assertRaises(companies.NotFound):
            companies.get_company(target.id)
        self.assertEqual(chat["messages"][1]["tool"]["status"], "approved")

    def test_rejected_action_does_not_run(self):
        target = companies.list_companies()[0]
        FakeOpenAI.script = [([call("company-delete", {"id": target.id})], ""), ([], "Approve?")]
        chat = registry.run("chat.send", {"message": "Delete it"})
        event = chat["messages"][1]["tool"]
        FakeOpenAI.script = [([], "OK, kept.")]
        registry.run("chat.resolve", {"chat_id": chat["id"], "event_id": event["id"], "approve": False})
        self.assertEqual(companies.get_company(target.id).id, target.id)

    def test_assistant_can_rename_its_chat(self):
        FakeOpenAI.script = [([call("chat-set_title", {"title": "  Nimbus ·  Technical interview "})], ""), ([], "Done.")]
        chat = registry.run("chat.send", {"message": "Nimbus invited me to the technical interview"})
        self.assertEqual(chat["title"], "Nimbus · Technical interview")
        self.assertIn("chat-set_title", {t["name"] for t in FakeOpenAI.sent[0]["tools"]})
        renamed = registry.run("chat.rename", {"id": chat["id"], "title": "Nimbus · Offer"})
        self.assertEqual(renamed["title"], "Nimbus · Offer")

    def test_title_follows_the_company_and_its_stage(self):
        c = companies.create_company(name="Nimbus")
        companies.update_step(c.id, "Applied", state="done")
        FakeOpenAI.script = [([], "You're at Applied.")]
        chat = registry.run("chat.send", {"message": "Where am I with nimbus?"})
        self.assertEqual(chat["title"], "Nimbus · Applied")
        companies.add_step(c.id, "Technical interview", after="Applied", state="planned")
        FakeOpenAI.script = [([], "Good luck.")]
        chat = registry.run("chat.send", {"message": "Any tips?", "chat_id": chat["id"]})
        self.assertEqual(chat["title"], "Nimbus · Technical interview")

    def test_user_title_is_never_replaced(self):
        companies.create_company(name="Nimbus")
        FakeOpenAI.script = [([], "Hi.")]
        chat = registry.run("chat.send", {"message": "Nimbus question"})
        registry.run("chat.rename", {"id": chat["id"], "title": "My Nimbus notes"})
        FakeOpenAI.script = [([], "Ok.")]
        chat = registry.run("chat.send", {"message": "Nimbus again", "chat_id": chat["id"]})
        self.assertEqual(chat["title"], "My Nimbus notes")

    def test_two_companies_keep_the_title(self):
        companies.create_company(name="Nimbus")
        companies.create_company(name="Initech")
        FakeOpenAI.script = [([], "Compared.")]
        chat = registry.run("chat.send", {"message": "Compare Nimbus and Initech"})
        self.assertEqual(chat["title"], "Compare Nimbus and Initech")

    def test_tool_errors_are_reported_not_raised(self):
        FakeOpenAI.script = [([call("company-get", {"id": "nope"})], ""), ([], "Not found.")]
        chat = registry.run("chat.send", {"message": "Open nope"})
        self.assertEqual(chat["messages"][1]["tool"]["status"], "error")
        self.assertIn("error", json.loads(FakeOpenAI.sent[1]["input"][0]["output"]))


if __name__ == "__main__":
    unittest.main()
