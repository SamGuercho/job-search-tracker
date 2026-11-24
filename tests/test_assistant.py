"""Assistant loop with a scripted fake OpenAI client (no network)."""
import json
import os
import unittest
from types import SimpleNamespace as NS

import openai

from jobtracker import registry
from jobtracker.services import assistant, companies, llm
from tests.test_core import reset  # sets the temp data/output env vars first


def call(name, args, call_id="c1"):
    return NS(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)


class FakeOpenAI:
    """Returns the scripted responses in order (streamed when asked) and records what it was sent.
    `extra`: more attributes for every response (usage, model…)."""
    script, sent, extra = [], [], {}

    def __init__(self, *a, **k):
        self.responses = self

    def create(self, **kwargs):
        FakeOpenAI.sent.append(kwargs)
        output, text = FakeOpenAI.script.pop(0)
        response = NS(id=f"resp{len(FakeOpenAI.sent)}", output=output, output_text=text, **FakeOpenAI.extra)
        return openai_stream(response) if kwargs.get("stream") else response


def openai_stream(response):
    """The Responses API stream events for a scripted response: text in small deltas, one event per tool call."""
    text = response.output_text
    for i in range(0, len(text), 4):
        yield NS(type="response.output_text.delta", output_index=0, content_index=0, delta=text[i:i + 4])
    if text:
        yield NS(type="response.output_text.done", output_index=0, content_index=0, text=text)
    for item in response.output:
        yield NS(type="response.output_item.done", item=item)
    yield NS(type="response.completed", response=response)


class CompactTests(unittest.TestCase):
    def setUp(self):
        reset()
        os.environ["OPENAI_API_KEY"] = "test"
        self._orig, openai.OpenAI = openai.OpenAI, FakeOpenAI
        self._structured = llm.structured
        FakeOpenAI.sent = []

    def tearDown(self):
        openai.OpenAI, llm.structured = self._orig, self._structured
        os.environ["OPENAI_API_KEY"] = ""

    def test_compacting_continues_from_a_summary_and_keeps_the_messages(self):
        FakeOpenAI.script = [([], "Nimbus is at the technical interview.")]
        chat = registry.run("chat.send", {"message": "Where am I with {Nimbus}?"})
        self.assertFalse(chat["compact_suggested"])
        seen = {}

        def fake(instructions, text, schema):
            seen["text"] = text
            return schema(summary="- Nimbus: technical interview next {braces ok}")
        llm.structured = fake
        chat = registry.run("chat.compact", {"id": chat["id"]})
        self.assertIn("USER: Where am I with {Nimbus}?", seen["text"])
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "assistant", "notice"])  # all still visible
        self.assertEqual((chat["history_from"], chat["previous_response_id"]), (3, None))

        FakeOpenAI.script = [([], "Good luck!")]
        chat = registry.run("chat.send", {"message": "Thanks", "chat_id": chat["id"]})
        sent = FakeOpenAI.sent[-1]
        self.assertIsNone(sent["previous_response_id"])  # OpenAI's old history is not reused
        self.assertIn("technical interview next {braces ok}", sent["instructions"])

        # a second compaction merges the earlier summary and only sends what came after it
        llm.structured = fake
        registry.run("chat.compact", {"id": chat["id"]})
        self.assertIn("Earlier summary:", seen["text"])
        self.assertNotIn("Where am I", seen["text"].split("Conversation:")[1])
        with self.assertRaises(ValueError):  # nothing new since
            registry.run("chat.compact", {"id": chat["id"]})

    def test_claude_rebuilds_only_the_messages_after_the_summary(self):
        chat = assistant.Chat(messages=[assistant.ChatMessage(role="user", text="old"),
                                        assistant.ChatMessage(role="assistant", text="old answer"),
                                        assistant.ChatMessage(role="notice", text="compacted"),
                                        assistant.ChatMessage(role="user", text="new")], history_from=3, summary="s")
        self.assertEqual(assistant._claude_history(chat, None), [{"role": "user", "content": "new"}])

    def test_long_conversations_suggest_compacting(self):
        chat = assistant.Chat(context_tokens=70_000)
        self.assertTrue(chat.compact_suggested)
        self.assertTrue(assistant.Chat(messages=[assistant.ChatMessage(role="user", text="x")] * 130).compact_suggested)
        self.assertFalse(assistant.Chat(messages=[assistant.ChatMessage(role="user", text="x")] * 130,
                                        history_from=100).compact_suggested)


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


    def test_archive_and_delete_conversations(self):
        FakeOpenAI.script = [([], "Hi."), ([], "Hello again.")]
        first = registry.run("chat.send", {"message": "First"})
        second = registry.run("chat.send", {"message": "Second"})
        archived = registry.run("chat.archive", {"id": first["id"]})
        self.assertTrue(archived["archived"])
        listed = {c["id"]: c["archived"] for c in registry.run("chat.list", {})}
        self.assertEqual(listed, {first["id"]: True, second["id"]: False})
        FakeOpenAI.script = [([], "Back.")]
        again = registry.run("chat.send", {"message": "Still there?", "chat_id": first["id"]})
        self.assertFalse(again["archived"])  # writing in it brings it back
        registry.run("chat.delete", {"id": second["id"]})
        self.assertEqual([c["id"] for c in registry.run("chat.list", {})], [first["id"]])

    def test_conversations_are_separate(self):
        FakeOpenAI.script = [([], "A."), ([], "B."), ([], "A again.")]
        a = registry.run("chat.send", {"message": "In A"})
        b = registry.run("chat.send", {"message": "In B"})
        registry.run("chat.send", {"message": "A, second message", "chat_id": a["id"]})
        self.assertIsNone(FakeOpenAI.sent[1]["previous_response_id"])          # B starts with no history
        self.assertEqual(FakeOpenAI.sent[2]["previous_response_id"], "resp1")  # A continues A only
        self.assertNotIn("In B", [m["text"] for m in registry.run("chat.get", {"id": a["id"]})["messages"]])
        self.assertNotEqual(a["id"], b["id"])


if __name__ == "__main__":
    unittest.main()
