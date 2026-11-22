"""Server robustness: data check at startup, backup status, usage archive, streamed recordings."""
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from jobtracker import config, maintenance, users
from jobtracker.api import app
from jobtracker.services import companies, recordings, usage
from tests.test_core import reset  # sets the temp data/output env vars first


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        reset()
        companies.list_companies()  # creates companies.json from the seed
        maintenance.check_data()     # clean state for each test

    def tearDown(self):
        config.TAILSCALE_USERS = False

    def write_backup(self, **fields):
        (config.DATA_DIR / maintenance.BACKUP_FILE).write_text(json.dumps(fields), encoding="utf-8-sig")

    def test_readable_data_is_healthy(self):
        self.assertEqual(maintenance.check_data(), [])
        with TestClient(app) as client:  # startup runs the check
            health = client.get("/health").json()
        self.assertEqual((health["status"], health["data_problems"], health["backup"]), ("ok", 0, None))

    def test_unreadable_data_makes_health_report_an_error(self):
        (config.DATA_DIR / "companies.json").write_text('{"companies": [{"no_name": 1}]}', encoding="utf-8")
        friend = config.USERS_DIR / "friend@x.com" / "data"
        friend.mkdir(parents=True)
        (friend / "chats.json").write_text("{not json", encoding="utf-8")
        problems = maintenance.check_data()
        self.assertEqual(len(problems), 2)
        self.assertTrue(problems[0].startswith("companies.json: ValidationError"))
        self.assertTrue(problems[1].startswith("users/friend@x.com/data/chats.json"))
        with TestClient(app) as client:
            health = client.get("/health").json()
        self.assertEqual((health["status"], health["data_problems"]), ("error", 2))
        self.assertNotIn("friend", json.dumps(health))  # public: reveals no data

    def test_version_comes_from_the_image_or_git(self):
        import os
        maintenance.version.cache_clear()
        os.environ.update(JOBTRACKER_COMMIT="88a95d4", JOBTRACKER_COMMIT_MESSAGE="Compact chats", JOBTRACKER_BUILT_AT="2026-11-22T08:00:00Z")
        try:
            with TestClient(app) as client:
                health = client.get("/health").json()
                info = client.post("/api/actions/app.info", json={}).json()["result"]
            self.assertEqual((health["commit"], health["commit_message"]), ("88a95d4", "Compact chats"))
            self.assertEqual(info["version"]["commit"], "88a95d4")
        finally:
            for k in ("JOBTRACKER_COMMIT", "JOBTRACKER_COMMIT_MESSAGE", "JOBTRACKER_BUILT_AT"):
                os.environ.pop(k, None)
            maintenance.version.cache_clear()
        self.assertRegex(maintenance.version()["commit"], r"^[0-9a-f]{7,}$|^$")  # from git in a checkout

    def test_backup_status(self):
        self.assertIsNone(maintenance.backup_status())
        now = datetime(2026, 11, 22, 9, 0)
        self.write_backup(at="2026-11-22T03:00:05", ok=True, destination="C:/secret")
        b = maintenance.backup_status(now)
        self.assertEqual((b["ok"], b["age_hours"], b["stale"]), (True, 6.0, False))
        self.assertNotIn("destination", b)
        self.assertTrue(maintenance.backup_status(now + timedelta(days=4))["stale"])
        self.write_backup(at="2026-11-22T03:00:05", ok=False, error="Disk full")
        self.assertEqual(maintenance.backup_status(now)["error"], "Disk full")

    def test_warnings_only_for_the_owner_on_the_server(self):
        self.assertEqual(maintenance.warnings(), [])  # not on a server
        config.TAILSCALE_USERS = True
        self.assertIn("No backup yet", maintenance.warnings()[0])
        self.write_backup(at=datetime.now().isoformat(timespec="seconds"), ok=True)
        self.assertEqual(maintenance.warnings(), [])
        self.write_backup(at=(datetime.now() - timedelta(days=5)).isoformat(timespec="seconds"), ok=True)
        self.assertIn("5 days ago", maintenance.warnings()[0])
        with users.acting_as("friend@x.com"):
            self.assertEqual(maintenance.warnings(), [])

    def test_old_usage_entries_are_archived_not_lost(self):
        old = usage.MAX_ENTRIES
        usage.MAX_ENTRIES = 3
        try:
            for i in range(5):
                usage._add(usage.UsageEntry(provider="openai", model="m", feature=f"f{i}"))
        finally:
            usage.MAX_ENTRIES = old
        self.assertEqual([e.feature for e in usage.store.load().entries], ["f2", "f3", "f4"])
        lines = (config.DATA_DIR / usage.ARCHIVE).read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(x)["feature"] for x in lines], ["f0", "f1"])

    def test_recordings_are_copied_in_chunks_with_a_size_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.m4a"
            recordings._write(target, io.BytesIO(b"x" * 3_000_000))
            self.assertEqual(target.stat().st_size, 3_000_000)
            old = config.MAX_RECORDING_MB
            config.MAX_RECORDING_MB = 2
            try:
                with self.assertRaises(ValueError):
                    recordings._write(target, io.BytesIO(b"x" * 3_000_000))
            finally:
                config.MAX_RECORDING_MB = old
            self.assertFalse(target.exists())  # no half-written file left


if __name__ == "__main__":
    unittest.main()
