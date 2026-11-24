"""OpenAI -> Claude fallback (quota, billing, access), with fake clients (no network)."""
import json
import os
import unittest
from types import SimpleNamespace as NS

import anthropic
import httpx2
import openai
from pydantic import BaseModel

from jobtracker import registry
from jobtracker.services import claude, companies, llm
from tests.test_assistant import FakeOpenAI, call
from tests.test_core import reset  # sets the temp data/output env vars first


def openai_error(cls, status, code=None):
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://api.openai.com/v1/responses"))
    return cls(f"Error code: {status}", response=response, body={"code": code, "message": "nope"})


QUOTA = openai_error(openai.RateLimitError, 429, "insufficient_quota")


class RaisingOpenAI(FakeOpenAI):
    """Like FakeOpenAI, but a scripted Exception is raised instead of returned."""
    def create(self, **kwargs):
        if FakeOpenAI.script and isinstance(FakeOpenAI.script[0], Exception):
            FakeOpenAI.sent.append(kwargs)
            raise FakeOpenAI.script.pop(0)
        return super().create(**kwargs)


def text(t):
    return NS(type="text", text=t)


def tool_use(name, args, id="tu1"):
    return NS(type="tool_use", name=name, input=args, id=id)


class FakeClaude:
    """Replaces claude.chat: returns scripted (content, stop_reason) and records the requests."""
    script, sent = [], []

    @staticmethod
    def chat(system, tools, messages, sink=None):
        FakeClaude.sent.append({"system": system, "tools": tools, "messages": json.loads(json.dumps(messages, default=vars))})
        content, stop = FakeClaude.script.pop(0)
        for i, block in enumerate(content):  # what the real stream tells the sink
            if sink and block.type == "text":
                sink.delta("text", i, block.text)
                sink.block_done("text", i, block.text)
            elif sink and block.type == "tool_use":
                sink.tool_call(block.id, block.name, json.dumps(block.input))
        return NS(content=content, stop_reason=stop)


class ChatFallbackTests(unittest.TestCase):
    def setUp(self):
        reset()
        os.environ["OPENAI_API_KEY"], os.environ["ANTHROPIC_API_KEY"] = "test", "test"
        self._openai, openai.OpenAI = openai.OpenAI, RaisingOpenAI
        self._chat, claude.chat = claude.chat, FakeClaude.chat
        FakeOpenAI.sent, FakeClaude.sent = [], []

    def tearDown(self):
        openai.OpenAI, claude.chat = self._openai, self._chat
        os.environ["OPENAI_API_KEY"] = os.environ["ANTHROPIC_API_KEY"] = ""

    def test_quota_error_switches_the_chat_to_claude_and_says_so(self):
        FakeOpenAI.script = [QUOTA]
        FakeClaude.script = [([tool_use("company-create", {"name": "Nimbus"})], "tool_use"),
                             ([text("Added Nimbus.")], "end_turn")]
        chat = registry.run("chat.send", {"message": "Add Nimbus"})

        self.assertEqual(chat["provider"], "claude")
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "notice", "tool", "assistant"])
        self.assertIn("quota", chat["messages"][1]["text"])
        self.assertIn(claude.model(), chat["messages"][1]["text"])
        self.assertIsNotNone(companies.find_by_name("Nimbus"))
        first = FakeClaude.sent[0]
        self.assertEqual(first["messages"], [{"role": "user", "content": "Add Nimbus"}])  # notice not sent
        self.assertIn("company-create", {t["name"] for t in first["tools"]})
        self.assertIn("input_schema", first["tools"][0])
        self.assertEqual(FakeClaude.sent[1]["messages"][-1]["content"][0]["type"], "tool_result")

        # The next message goes straight to Claude, with the history rebuilt from the app's records
        FakeClaude.script = [([text("Sure.")], "end_turn")]
        chat = registry.run("chat.send", {"message": "Thanks", "chat_id": chat["id"]})
        self.assertEqual(len(FakeOpenAI.sent), 1)
        history = FakeClaude.sent[-1]["messages"]
        self.assertEqual([m["role"] for m in history], ["user", "assistant", "user"])
        self.assertIn("[tool log, already run] company.create", history[0]["content"])
        self.assertTrue(history[-1]["content"].endswith("Thanks"))

    def test_failure_mid_turn_keeps_the_tool_that_already_ran(self):
        FakeOpenAI.script = [([call("company-create", {"name": "Nimbus"})], ""), QUOTA]
        FakeClaude.script = [([text("Nimbus is added.")], "end_turn")]
        chat = registry.run("chat.send", {"message": "Add Nimbus"})
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "tool", "notice", "assistant"])
        last = FakeClaude.sent[0]["messages"][-1]
        self.assertEqual(last["role"], "user")
        self.assertIn("company.create", last["content"])

    def test_approval_note_reaches_claude(self):
        target = companies.create_company(name="Nimbus")
        FakeOpenAI.script = [QUOTA]
        FakeClaude.script = [([tool_use("company-delete", {"id": target.id})], "tool_use"),
                             ([text("Please approve.")], "end_turn")]
        chat = registry.run("chat.send", {"message": "Delete Nimbus"})
        event = next(m["tool"] for m in chat["messages"] if m["role"] == "tool")
        FakeClaude.script = [([text("Deleted.")], "end_turn")]
        registry.run("chat.resolve", {"chat_id": chat["id"], "event_id": event["id"], "approve": True})
        self.assertIn("APPROVED company.delete", FakeClaude.sent[-1]["messages"][-1]["content"])

    def test_other_openai_errors_do_not_switch(self):
        FakeOpenAI.script = [openai_error(openai.BadRequestError, 400)]
        chat = registry.run("chat.send", {"message": "Hi"})
        self.assertEqual(chat["provider"], "openai")
        self.assertTrue(chat["messages"][-1]["text"].startswith("⚠ OpenAI error"))
        self.assertEqual(FakeClaude.sent, [])

    def test_no_anthropic_key_means_no_fallback(self):
        os.environ["ANTHROPIC_API_KEY"] = ""
        FakeOpenAI.script = [QUOTA]
        chat = registry.run("chat.send", {"message": "Hi"})
        self.assertEqual(chat["provider"], "openai")
        self.assertIn("OpenAI error", chat["messages"][-1]["text"])

    def test_claude_only_without_openai_key(self):
        os.environ["OPENAI_API_KEY"] = ""
        FakeClaude.script = [([text("Hello.")], "end_turn")]
        chat = registry.run("chat.send", {"message": "Hi"})
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "assistant"])  # no notice: nothing failed
        self.assertEqual(FakeOpenAI.sent, [])


class Out(BaseModel):
    answer: str


class StructuredFallbackTests(unittest.TestCase):
    def setUp(self):
        os.environ["OPENAI_API_KEY"], os.environ["ANTHROPIC_API_KEY"] = "test", "test"
        self._client, self._structured = llm.client, claude.structured
        llm.reset_notices()

    def tearDown(self):
        llm.client, claude.structured = self._client, self._structured
        os.environ["OPENAI_API_KEY"] = os.environ["ANTHROPIC_API_KEY"] = ""

    def failing_openai(self, error):
        def parse(**kwargs):
            raise error
        llm.client = lambda: NS(responses=NS(parse=parse))

    def test_billing_error_uses_claude_and_records_a_notice(self):
        self.failing_openai(openai_error(openai.AuthenticationError, 401))
        claude.structured = lambda i, t, schema: schema(answer="from Claude")
        self.assertEqual(llm.structured("x", "y", Out).answer, "from Claude")
        notice = llm.pop_notice()
        self.assertIn("switched to Claude", notice)
        self.assertIsNone(llm.pop_notice())  # read once

    def test_other_errors_are_reported(self):
        self.failing_openai(openai_error(openai.BadRequestError, 400))
        with self.assertRaises(llm.LLMUnavailable):
            llm.structured("x", "y", Out)
        self.assertIsNone(llm.pop_notice())

    def test_api_returns_the_notice(self):
        from fastapi.testclient import TestClient

        from jobtracker.api import app

        reset()
        self.failing_openai(QUOTA)
        claude.structured = lambda i, t, schema: schema(answer="from Claude")
        name = next(iter(registry.ACTIONS))
        original_run = registry.run
        registry.run = lambda action, payload: llm.structured("x", "y", Out).answer
        try:
            body = TestClient(app).post(f"/api/actions/{name}", json={}).json()
            self.assertEqual(body["result"], "from Claude")
            self.assertIn("switched to Claude", body["notice"])
            registry.run = lambda action, payload: "no AI"
            self.assertNotIn("notice", TestClient(app).post(f"/api/actions/{name}", json={}).json())
        finally:
            registry.run = original_run


def anthropic_sse(model):
    """A streamed Messages API answer: a thinking block, a text block in two deltas, a tool call."""
    events = [
        {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": model,
                                              "content": [], "stop_reason": None, "stop_sequence": None,
                                              "usage": {"input_tokens": 5, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Greeting."}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hi"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": " there"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "tu1", "name": "company-get", "input": {}}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"id": "x"}'}},
        {"type": "content_block_stop", "index": 2},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 9}},
        {"type": "message_stop"},
    ]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


class RequestShapeTests(unittest.TestCase):
    """The real Anthropic SDK builds the requests (fake HTTP transport)."""

    def setUp(self):
        self.requests = []

        def handler(request):
            self.requests.append(request)
            body = json.loads(request.content)
            if body.get("stream"):
                return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                                       content=anthropic_sse(body["model"]).encode())
            reply = '{"answer": "ok"}' if "output_config" in body and "format" in body["output_config"] else "Hi"
            return httpx2.Response(200, json={
                "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                "content": [{"type": "text", "text": reply}], "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1}})

        self._client = claude.client
        claude.client = lambda: anthropic.Anthropic(
            api_key="test", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))

    def tearDown(self):
        claude.client = self._client

    def test_chat_request(self):
        seen = []
        sink = NS(delta=lambda kind, key, t: seen.append(("delta", kind, t)),
                  block_done=lambda kind, key, t: seen.append(("done", kind, t)),
                  tool_call=lambda *a: seen.append(("tool", *a)))
        response = claude.chat("sys", [{"name": "company-get", "description": "d",
                                        "input_schema": {"type": "object", "properties": {}}}],
                               [{"role": "user", "content": "Hi"}], sink)
        self.assertEqual([b.type for b in response.content], ["thinking", "text", "tool_use"])
        self.assertEqual(response.content[1].text, "Hi there")
        self.assertEqual(seen, [("delta", "thinking", "Greeting."), ("done", "thinking", "Greeting."),
                                ("delta", "text", "Hi"), ("delta", "text", " there"), ("done", "text", "Hi there"),
                                ("tool", "tu1", "company-get", '{"id": "x"}')])
        body = json.loads(self.requests[0].content)
        self.assertTrue(body["stream"])
        self.assertEqual(body["thinking"], {"type": "adaptive", "display": "summarized"})
        self.assertEqual(body["model"], claude.model())
        self.assertEqual(body["fallbacks"], "default")
        self.assertEqual(body["output_config"]["effort"], "medium")
        self.assertIn(claude.FALLBACK_BETA, self.requests[0].headers["anthropic-beta"])

    def test_structured_request(self):
        self.assertEqual(claude.structured("sys", "text", Out).answer, "ok")
        body = json.loads(self.requests[0].content)
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")


if __name__ == "__main__":
    unittest.main()
