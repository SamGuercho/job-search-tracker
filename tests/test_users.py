"""Several users behind Tailscale: identity from the Tailscale-User-Login header, one data folder each."""
import hashlib
import unittest

from fastapi.testclient import TestClient

from jobtracker import config, registry, users
from jobtracker.api import app
from jobtracker.services import web
from tests.test_core import reset  # sets the temp data/output env vars first

OWNER, FRIEND, OTHER_FRIEND = "owner@example.com", "friend@example.com", "other@example.com"


def snapshot(*folders):
    """Content hash of every file in these folders, except the friends' folders inside them."""
    out = {}
    for folder in folders:
        for path in sorted(folder.rglob("*")):
            if path.is_file() and not path.is_relative_to(config.USERS_DIR):
                out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class MultiUserTests(unittest.TestCase):
    def setUp(self):
        reset()
        config.OWNER, config.FRIENDS = OWNER, {FRIEND, OTHER_FRIEND}
        self.client = TestClient(app)

    def tearDown(self):
        config.OWNER, config.FRIENDS, config.PASSWORD = "", set(), ""

    def act(self, login, action, payload=None):
        headers = {"Tailscale-User-Login": login} if login else {}
        return self.client.post(f"/api/actions/{action}", json=payload or {}, headers=headers)

    def names(self, login):
        return {c["name"] for c in self.act(login, "company.list").json()["result"]}

    def test_each_user_sees_only_their_own_applications(self):
        self.act(OWNER, "company.create", {"name": "OwnerCo", "position": "PM"})
        self.act(FRIEND, "company.create", {"name": "FriendCo", "position": "Dev"})
        self.assertIn("OwnerCo", self.names(OWNER))
        self.assertNotIn("FriendCo", self.names(OWNER))
        self.assertIn("FriendCo", self.names(FRIEND))
        self.assertNotIn("OwnerCo", self.names(FRIEND))
        self.assertNotIn("FriendCo", self.names(OTHER_FRIEND))

    def test_cli_and_mcp_act_as_the_owner(self):
        self.act(OWNER, "company.create", {"name": "OwnerCo", "position": "PM"})
        self.assertIn("OwnerCo", {c["name"] for c in registry.run("company.list", {})})  # no HTTP, no header
        self.assertEqual(users.current(), "")                                       # nothing leaks out of a request

    def test_friend_data_lives_in_their_own_folders(self):
        self.act(FRIEND, "company.create", {"name": "FriendCo", "position": "Dev"})
        self.assertTrue((config.USERS_DIR / FRIEND / "data" / "companies.json").exists())

    def test_friend_writes_never_touch_owner_files(self):
        self.act(OWNER, "company.create", {"name": "OwnerCo", "position": "PM"})
        self.act(OWNER, "cv.generate", {"keywords": ["Python"], "company_name": "OwnerCo", "export_pdf": False})
        before = snapshot(config.DATA_DIR, config.OUTPUT_DIR)
        friend = self.act(FRIEND, "company.create", {"name": "OwnerCo", "position": "Dev"}).json()["result"]
        self.act(FRIEND, "cv.profile.update", {"fields": {"name": "Friend Name"}})
        self.act(FRIEND, "cv.generate", {"keywords": ["SQL"], "company_id": friend["id"], "export_pdf": False})
        self.act(FRIEND, "company.delete", {"id": friend["id"]})
        self.assertEqual(snapshot(config.DATA_DIR, config.OUTPUT_DIR), before)
        self.assertNotEqual(self.act(OWNER, "cv.master.get").json()["result"]["profile"]["name"], "Friend Name")

    def test_files_are_served_from_the_users_own_folder_only(self):
        r = self.act(OWNER, "cv.generate", {"keywords": ["Python"], "company_name": "Secret", "export_pdf": False})
        docx = r.json()["result"]["docx"]
        owner_get = self.client.get(f"/files/{docx}", headers={"Tailscale-User-Login": OWNER})
        self.assertEqual(owner_get.status_code, 200)
        self.assertEqual(self.client.get(f"/files/{docx}", headers={"Tailscale-User-Login": FRIEND}).status_code, 404)
        escape = "/files/..%2F..%2F..%2Fdata%2Fcompanies.json"
        self.assertEqual(self.client.get(escape, headers={"Tailscale-User-Login": FRIEND}).status_code, 404)

    def test_strangers_on_the_tailnet_are_refused(self):
        self.assertEqual(self.act("stranger@example.com", "company.list").status_code, 403)
        page = self.client.get("/", headers={"Tailscale-User-Login": "stranger@example.com"})
        self.assertEqual(page.status_code, 403)
        self.assertIn("not invited", page.text)
        self.assertIn("stranger@example.com", page.text)  # tells the owner which login to add
        health = self.client.get("/health", headers={"Tailscale-User-Login": "stranger@example.com"})
        self.assertEqual(health.status_code, 200)

    def test_tailscale_users_need_no_password(self):
        config.PASSWORD = "secret"
        self.assertEqual(self.act(FRIEND, "company.list").status_code, 200)

    def test_the_password_never_makes_someone_the_owner(self):
        config.PASSWORD = "secret"
        self.client.post("/login", data={"password": "secret"})  # a friend who knows the password
        self.assertTrue(self.client.cookies)
        self.assertEqual(self.act(None, "company.list").status_code, 403)
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 403)
        self.assertEqual(self.client.get("/files/x.pdf").status_code, 403)

    def test_header_is_ignored_when_multi_user_is_off(self):
        config.OWNER, config.PASSWORD = "", "secret"
        self.assertEqual(self.act(FRIEND, "company.list").status_code, 401)

    def test_logins_are_case_insensitive(self):
        self.act(FRIEND, "company.create", {"name": "FriendCo", "position": "Dev"})
        self.assertIn("FriendCo", self.names(FRIEND.upper()))

    def test_friend_cannot_import_files_outside_their_folder(self):
        r = self.act(FRIEND, "cv.import.parse", {"file_path": str(config.DATA_DIR / "master_cv.json")})
        self.assertEqual(r.status_code, 400)

    def test_app_info_says_who_is_signed_in(self):
        self.assertEqual(self.act(FRIEND, "app.info").json()["result"]["user"], FRIEND)
        self.assertEqual(self.act(OWNER, "app.info").json()["result"]["user"], OWNER)


class FetchTests(unittest.TestCase):
    def test_links_to_this_machine_or_private_network_are_refused(self):
        for url in ("http://127.0.0.1:8000/files/x.pdf", "http://localhost:8000/api/actions",
                    "http://192.168.1.10/", "http://100.80.53.29/", "http://[::1]/", "file:///etc/passwd"):
            with self.assertRaises(ValueError, msg=url):
                registry.run("job.fetch_url", {"url": url})

    def test_public_addresses_pass(self):
        web.check_public("http://93.184.215.14/")  # a public IP: no DNS needed


if __name__ == "__main__":
    unittest.main()
