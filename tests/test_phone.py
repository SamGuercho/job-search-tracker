"""Phone features: calendar events and feed, share target, recordings without a length header, notifications
and reminders."""
import stat
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from jobtracker import config, registry
from jobtracker.api import app
from jobtracker.services import calendar, companies, push, recordings, reminders
from tests.test_core import reset  # sets the temp data/output env vars first

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


def schedule(company="Nimbus", name="Technical interview", time="10:00", **kw):
    registry.run("process.add_step", {"company": company, "name": name, "state": "planned", "date": TOMORROW,
                                      **({"time": time} if time else {}), **kw})
    c = companies.find_by_name(company)
    return c, next(n for n in c.process.nodes if n.name == name)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        reset()
        companies.create_company(name="Nimbus", position="Data Analyst", address="1 Rothschild Blvd, Tel Aviv",
                                 job_url="https://nimbus.example/jobs/1")
        self.client = TestClient(app)

    def test_timed_event_with_reminders(self):
        c, n = schedule(location="https://meet.google.com/abc-defg-hij", duration_min=45, notes="With Dana, CTO; bring a laptop")
        e = registry.run("calendar.event", {"company": "Nimbus", "step": n.id})
        self.assertEqual((e["start"], e["end"], e["all_day"]), (f"{TOMORROW}T10:00", f"{TOMORROW}T10:45", False))
        self.assertEqual(e["location"], "https://meet.google.com/abc-defg-hij")
        text = calendar.ics([e])
        self.assertIn(f"DTSTART:{TOMORROW.replace('-', '')}T100000\r\n", text)  # floating local time
        self.assertIn("TRIGGER:-PT1H", text)
        self.assertIn("TRIGGER:-P1D", text)
        self.assertIn("With Dana\\, CTO\\; bring a laptop", text.replace("\r\n ", ""))
        self.assertTrue(all(len(line.encode()) <= 75 for line in text.split("\r\n")))
        self.assertIn(f"UID:{n.id}@job-search-tracker", text)

    def test_all_day_event_and_errors(self):
        c, n = schedule(time=None)
        e = calendar.event(c, n)
        self.assertTrue(e["all_day"])
        self.assertIn(f"DTSTART;VALUE=DATE:{TOMORROW.replace('-', '')}", calendar.ics([e]))
        self.assertEqual(e["location"], "1 Rothschild Blvd, Tel Aviv")  # falls back to the company address
        registry.run("process.add_step", {"company": "Nimbus", "name": "Onsite", "state": "planned"})
        r = self.client.get("/api/calendar/step.ics", params={"company": "Nimbus", "step": "Onsite"})
        self.assertEqual(r.status_code, 400)  # no date yet

    def test_ics_download_and_feed(self):
        c, n = schedule()
        r = self.client.get("/api/calendar/step.ics", params={"company": "Nimbus", "step": n.id})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/calendar"))
        self.assertIn(f"/#/company/{c.id}", r.text.replace("\r\n ", ""))  # link back to the application
        companies.create_company(name="Closed Co")
        schedule("Closed Co", "HR call")
        registry.run("process.close", {"company": "Closed Co", "reason": "Rejected"})
        feed = self.client.get("/api/calendar/interviews.ics").text
        self.assertIn("Technical interview – Nimbus", feed)
        self.assertNotIn("Closed Co", feed)
        self.assertIn("X-WR-CALNAME:Job interviews", feed)

    def test_share_target_sends_the_link_to_the_assistant(self):
        r = self.client.get("/share", params={"title": "Senior Analyst", "text": "Check this https://linkedin.com/jobs/view/42"},
                            follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("/#/chat?q=Add%20this%20job", r.headers["location"])
        self.assertIn("linkedin.com/jobs/view/42", r.headers["location"])
        self.assertEqual(self.client.get("/share", follow_redirects=False).headers["location"], "/#/")


class BrowserRecordingTests(unittest.TestCase):
    """WebM from MediaRecorder has no duration in its header: it is measured by decoding."""

    def test_duration_from_decoding(self):
        tmp = Path(tempfile.mkdtemp())
        for name, script in (("ffprobe", 'print("N/A")'),
                             ("ffmpeg", 'print("out_time_us=1500000\\nprogress=continue\\nout_time_us=62500000\\nprogress=end")')):
            path = tmp / name
            path.write_text(f"#!{sys.executable}\n{script}\n")
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        old = config.FFPROBE_BIN, config.FFMPEG_BIN
        config.FFPROBE_BIN, config.FFMPEG_BIN = str(tmp / "ffprobe"), str(tmp / "ffmpeg")
        try:
            self.assertAlmostEqual(recordings.duration(tmp / "x.webm"), 62.5)
        finally:
            config.FFPROBE_BIN, config.FFMPEG_BIN = old


class PushTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.sent = []
        import pywebpush
        self._webpush = pywebpush.webpush

        def fake(subscription_info, data, **kw):
            if subscription_info["endpoint"].endswith("gone"):
                raise pywebpush.WebPushException("gone", response=NS(status_code=410))
            self.sent.append((subscription_info["endpoint"], data))
        pywebpush.webpush = fake

    def tearDown(self):
        import pywebpush
        pywebpush.webpush = self._webpush

    def subscribe(self, endpoint="https://push.example/1", tz="Asia/Jerusalem"):
        return registry.run("push.subscribe", {"subscription": {"endpoint": endpoint, "keys": {"p256dh": "x", "auth": "y"}},
                                               "timezone": tz, "device": "Android · Chrome (app)"})

    def test_key_subscribe_notify_and_cleanup(self):
        key = registry.run("push.key", {})["public_key"]
        self.assertEqual(len(key), 87)  # 65-byte P-256 point, base64url
        self.assertEqual(registry.run("push.key", {})["public_key"], key)  # created once
        self.subscribe()
        self.subscribe("https://push.example/gone")
        self.assertEqual(self.subscribe(tz="Not/AZone")["devices"], 2)  # same endpoint replaced; bad tz -> UTC
        self.assertEqual(registry.run("push.test", {})["sent"], 1)
        self.assertEqual([d["endpoint"] for d in registry.run("push.devices", {})], ["https://push.example/1"])  # 410 dropped
        self.assertEqual(push.store.load().subscriptions[0].timezone, "UTC")

    def test_reminders_at_local_times_and_only_once(self):
        companies.create_company(name="Nimbus")
        c, n = schedule(time="10:00", location="Floor 3")
        self.subscribe(tz="Asia/Jerusalem")
        tz = ZoneInfo("Asia/Jerusalem")
        local = lambda d, h, m=0: datetime.combine(date.fromisoformat(d), datetime.min.time()).replace(  # noqa: E731
            hour=h, minute=m, tzinfo=tz).astimezone(ZoneInfo("UTC"))
        today = date.today().isoformat()
        self.assertEqual([r["title"] for r in reminders.due(local(today, 8))], [])  # 26 h before: nothing yet
        interviews = lambda rs: [r["title"] for r in rs if "follow-up" not in r["title"]]  # noqa: E731 (example data has one)
        self.assertEqual(interviews(reminders.due(local(today, 12))), ["Tomorrow 10:00: Technical interview"])
        self.assertGreaterEqual(reminders.run_once(local(today, 12)), 1)
        self.assertEqual(reminders.run_once(local(today, 12)), 0)  # each reminder is sent once
        hour = [r for r in reminders.due(local(TOMORROW, 9, 15)) if "follow-up" not in r["title"]]
        self.assertEqual([r["title"] for r in hour], ["In 1 hour: Technical interview"])
        self.assertIn("Floor 3", hour[0]["body"])

    def test_follow_up_reminder_once_a_day(self):
        before = registry.run("app.info", {})["follow_ups_due"]  # the example data has stale applications
        c = companies.create_company(name="Quiet Co")
        registry.run("process.set_state", {"company": "Quiet Co", "step": "Applied", "state": "done"})
        companies.update_company(c.id, last_update=(date.today() - timedelta(days=20)).isoformat())
        self.subscribe(tz="UTC")
        morning = datetime.combine(date.today(), datetime.min.time()).replace(hour=10, tzinfo=ZoneInfo("UTC"))
        titles = [r["title"] for r in reminders.due(morning)]
        self.assertTrue(any("need a follow-up" in t for t in titles), titles)
        self.assertEqual(registry.run("app.info", {})["follow_ups_due"], before + 1)
        early = morning.replace(hour=7)
        self.assertFalse(any("follow-up" in r["title"] for r in reminders.due(early)))


if __name__ == "__main__":
    unittest.main()
