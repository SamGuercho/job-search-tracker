"""Recordings (upload, estimate, transcription with fallback, speaker choice, debrief) and usage/costs."""
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

import httpx2
import openai
from fastapi.testclient import TestClient

from jobtracker import config, prices, registry, users
from jobtracker.api import app
from jobtracker.models import Debrief, DebriefQuestion, TranscriptSegment
from jobtracker.services import companies, llm, practice, prep, recordings, usage
from tests.test_assistant import FakeOpenAI
from tests.test_core import reset  # sets the temp data/output env vars first
from tests.test_users import FRIEND, OWNER, set_cv_email

FAKE_TOOL = """#!{python}
import sys, pathlib
args = sys.argv[1:]
if "ffprobe" in sys.argv[0]:
    print("{duration}")
else:
    pathlib.Path(args[-1]).write_bytes(b"mp3")
"""

DEBRIEF = Debrief(
    summary="Went well overall.", about_the_role="Team of 6, Snowflake.", next_steps="Onsite next week.",
    questions=[DebriefQuestion(question="How would you rank customers by revenue per month?", answer_summary="Used RANK",
                               kind="technical", capability="sql", topic="window functions", score=6,
                               missed=["Ties"], feedback="Mention DENSE_RANK."),
               DebriefQuestion(question="Tell me about a conflict", answer_summary="Story about a deadline",
                               kind="behavioral", capability="Behavioral", topic="conflict", score=8, missed=[],
                               feedback="Good STAR structure.")],
    strengths=["Clear SQL"], to_improve=["Ties in rankings"], thank_you_email="Thank you for today!")


def tools(tmp: Path, duration=2700.0):
    for name in ("ffprobe", "ffmpeg"):
        path = tmp / name
        path.write_text(FAKE_TOOL.format(python=sys.executable, duration=duration))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    config.FFPROBE_BIN, config.FFMPEG_BIN = str(tmp / "ffprobe"), str(tmp / "ffmpeg")


class FakeAudio:
    """llm.client() replacement: audio.transcriptions.create, diarized or not."""
    calls = []

    def __init__(self, diarize_fails=False):
        self.diarize_fails = diarize_fails
        self.audio = NS(transcriptions=self)

    def create(self, model, file, **kw):
        FakeAudio.calls.append((model, kw))
        if kw.get("response_format") == "diarized_json":
            if self.diarize_fails:
                raise openai.NotFoundError("model gone", response=httpx2.Response(
                    404, request=httpx2.Request("POST", "https://api.openai.com/v1/audio/transcriptions")), body=None)
            return NS(segments=[NS(speaker="A", start=0, end=3, text="How would you rank customers by revenue per month?"),
                                NS(speaker="B", start=3, end=20, text="I would use a window function with RANK over months."),
                                NS(speaker="A", start=20, end=22, text="Tell me about a conflict?"),
                                NS(speaker="B", start=22, end=60, text="Once we had a tight deadline and two teams disagreed.")])
        return NS(text="Interviewer asks about SQL. Candidate answers with a window function.")


def wait(recording_id, statuses=("transcribing", "analyzing"), timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        r = recordings.get(recording_id)
        if r.status not in statuses:
            return r
        time.sleep(0.05)
    raise AssertionError("still running")


class RecordingTests(unittest.TestCase):
    def setUp(self):
        reset()
        os.environ["OPENAI_API_KEY"] = "test"
        self.tmp = Path(tempfile.mkdtemp())
        tools(self.tmp)
        self._client, self._structured = llm.client, llm.structured
        llm.structured = lambda instructions, text, schema: DEBRIEF
        FakeAudio.calls = []
        self.company = companies.create_company(name="Nimbus", position="Data Analyst")
        self.client = TestClient(app)

    def tearDown(self):
        llm.client, llm.structured = self._client, self._structured
        config.FFPROBE_BIN, config.FFMPEG_BIN = "ffprobe", "ffmpeg"
        os.environ["OPENAI_API_KEY"] = ""

    def upload(self, name="interview.m4a"):
        response = self.client.post("/api/recordings/upload", params={"company": "Nimbus", "step": "Applied"},
                                    files={"file": (name, b"audio-bytes", "audio/mp4")})
        return response

    def test_upload_measures_and_estimates_before_spending(self):
        r = self.upload().json()["result"]
        self.assertEqual((r["status"], r["duration_s"], r["step_name"]), ("uploaded", 2700.0, "Applied"))
        self.assertAlmostEqual(r["estimate_usd"]["transcription"], 45 * 0.006)
        self.assertGreater(r["estimate_usd"]["analysis"], 0)
        self.assertTrue((users.output_dir() / r["file"]).exists())
        self.assertEqual(self.upload("notes.txt").status_code, 400)
        self.assertEqual(FakeAudio.calls, [])  # nothing spent yet

    def test_diarized_flow_with_speaker_choice_and_debrief(self):
        llm.client = lambda: FakeAudio()
        rec_id = self.upload().json()["result"]["id"]
        registry.run("recording.transcribe", {"id": rec_id})
        r = wait(rec_id)
        self.assertEqual((r.status, r.diarized, r.me), ("choose_speaker", True, "B"))  # B answers, A asks
        self.assertAlmostEqual(r.cost_usd["transcription"], 0.27)
        view = registry.run("recording.get", {"id": rec_id})
        self.assertEqual([s["speaker"] for s in view["speakers"]], ["A", "B"])
        registry.run("recording.choose_speaker", {"id": rec_id, "me": "B"})
        r = wait(rec_id)
        self.assertEqual(r.status, "done", r.error)
        self.assertIn("[CANDIDATE] I would use a window function", recordings.transcript_text(r))
        self.assertTrue((users.output_dir() / r.debrief_file).read_text(encoding="utf-8").startswith("# Debrief – Nimbus"))
        interview = [a for a in practice.store.load().attempts if a.source == "interview"]
        self.assertEqual([(a.capability, a.score) for a in interview], [("SQL", 6), ("Behavioral", 8)])
        self.assertIn("Onsite next week", prep.brief("Nimbus")["context"])  # the next prep builds on it
        registry.run("recording.delete", {"id": rec_id})
        self.assertEqual([a for a in practice.store.load().attempts if a.source == "interview"], [])

    def test_text_only_fallback_when_the_diarize_model_is_gone(self):
        llm.client = lambda: FakeAudio(diarize_fails=True)
        rec_id = self.upload().json()["result"]["id"]
        registry.run("recording.transcribe", {"id": rec_id})
        r = wait(rec_id)
        self.assertEqual((r.status, r.diarized, r.model), ("done", False, config.TRANSCRIBE_FALLBACK_MODEL), r.error)
        self.assertAlmostEqual(r.cost_usd["transcription"], 45 * 0.0045)

    def test_guess_me(self):
        segs = [TranscriptSegment(speaker="X", text="Why? And how?"), TranscriptSegment(speaker="Y", text="Because " * 30)]
        self.assertEqual(recordings.guess_me(segs), "Y")


class UsageTests(unittest.TestCase):
    def setUp(self):
        reset()

    def openai_response(self, input_tokens=1000, cached=200, output=500, model="gpt-5.4-mini-2026-03-17"):
        return NS(model=model, usage=NS(input_tokens=input_tokens, output_tokens=output,
                                        input_tokens_details=NS(cached_tokens=cached)))

    def test_costs_from_reported_usage(self):
        with usage.feature("cv.import.parse"):
            usage.record_openai(self.openai_response(), "gpt-5.4-mini")
            usage.record_anthropic(NS(model="claude-sonnet-5-5", usage=NS(
                input_tokens=1000, output_tokens=100, cache_read_input_tokens=500, cache_creation_input_tokens=0)))
            usage.record_audio("gpt-4o-transcribe-diarize", 600)
            usage.record_plan("gpt-6.2-astra", 300)
        e = usage.store.load().entries
        self.assertAlmostEqual(e[0].cost_usd, (800 * 0.75 + 200 * 0.075 + 500 * 4.5) / 1e6)  # dated model -> base price
        self.assertAlmostEqual(e[1].cost_usd, (1000 * 2 + 500 * 0.1 + 100 * 10) / 1e6)
        self.assertAlmostEqual(e[2].cost_usd, 0.06)
        self.assertTrue(e[3].covered_by_plan and e[3].cost_usd == 0)
        report = registry.run("usage.report", {})
        self.assertAlmostEqual(report["this_month_usd"], round(sum(x.cost_usd for x in e), 4))
        self.assertEqual(report["by_feature"][0]["name"], "cv.import.parse")
        self.assertEqual(report["plan_runs_this_month"], 1)
        self.assertIsNone(prices.token_cost("openai", "unknown-model", 10, 10))

    def test_actions_are_billed_to_the_outermost_feature(self):
        with usage.feature("chat.send"), usage.feature("cv.summary.review"):
            usage.record_openai(self.openai_response(), "gpt-5.4-mini")
        self.assertEqual(usage.store.load().entries[0].feature, "chat.send")

    def test_assistant_reply_shows_its_cost(self):
        os.environ["OPENAI_API_KEY"] = "test"
        orig, openai.OpenAI = openai.OpenAI, FakeOpenAI
        FakeOpenAI.sent = []
        original_create = FakeOpenAI.create
        FakeOpenAI.create = lambda self, **kw: NS(**{**vars(original_create(self, **kw)), "usage": self_usage, "model": "gpt-5.4-mini"})
        self_usage = NS(input_tokens=2000, output_tokens=100, input_tokens_details=NS(cached_tokens=0))
        try:
            FakeOpenAI.script = [([], "Hello.")]
            chat = registry.run("chat.send", {"message": "Hi"})
        finally:
            openai.OpenAI, FakeOpenAI.create = orig, original_create
            os.environ["OPENAI_API_KEY"] = ""
        self.assertAlmostEqual(chat["messages"][-1]["cost_usd"], (2000 * 0.75 + 100 * 4.5) / 1e6)
        self.assertEqual(usage.average_cost("chat.send")["uses"], 1)

    def test_owner_sees_everyone(self):
        set_cv_email(OWNER)
        config.TAILSCALE_USERS = True
        try:
            client = TestClient(app)
            with users.acting_as(FRIEND):
                usage.record_audio("gpt-transcribe", 60)
            owner = client.post("/api/actions/usage.report", json={}, headers={"Tailscale-User-Login": OWNER}).json()["result"]
            friend = client.post("/api/actions/usage.report", json={}, headers={"Tailscale-User-Login": FRIEND}).json()["result"]
        finally:
            config.TAILSCALE_USERS, config.OWNER = False, ""
        self.assertIn(0.0045, [p["this_month_usd"] for p in owner["people"]])
        self.assertIsNone(friend["people"])
        self.assertEqual(friend["this_month_usd"], 0.0045)


if __name__ == "__main__":
    unittest.main()
