"""HTTP layer: health check and the optional password login."""
import unittest

from fastapi.testclient import TestClient

from jobtracker import auth, config
from jobtracker.api import app
from tests.test_core import reset  # sets the temp data/output env vars first


class ApiTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.client = TestClient(app)

    def tearDown(self):
        config.PASSWORD = ""

    def test_health_is_public_and_reveals_no_data(self):
        config.PASSWORD = "secret"
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")
        self.assertTrue(r.json()["login"])

    def test_no_password_means_no_login(self):
        r = self.client.post("/api/actions/company.list", json={})
        self.assertEqual(r.status_code, 200)

    def test_password_protects_api_pages_and_files(self):
        config.PASSWORD = "secret"
        self.assertEqual(self.client.post("/api/actions/company.list", json={}).status_code, 401)
        self.assertEqual(self.client.get("/files/x.pdf").status_code, 401)
        page = self.client.get("/", follow_redirects=False)
        self.assertEqual((page.status_code, page.headers["location"]), (303, "/login"))
        self.assertEqual(self.client.get("/favicon.svg").status_code, 200)  # login page assets stay public

    def test_login_and_logout(self):
        config.PASSWORD = "secret"
        wrong = self.client.post("/login", data={"password": "nope"}, follow_redirects=False)
        self.assertIn("Wrong password", wrong.text)
        ok = self.client.post("/login", data={"password": "secret"}, follow_redirects=False)
        self.assertEqual(ok.status_code, 303)
        self.assertIn("httponly", ok.headers["set-cookie"].lower())
        self.assertEqual(self.client.post("/api/actions/company.list", json={}).status_code, 200)
        self.client.get("/logout")
        self.assertEqual(self.client.post("/api/actions/company.list", json={}).status_code, 401)

    def test_tokens_expire_and_are_tied_to_the_password(self):
        config.PASSWORD = "secret"
        token = auth.make_token(now=1000)
        self.assertTrue(auth.valid(token, now=1001))
        self.assertFalse(auth.valid(token, now=1000 + config.SESSION_DAYS * 86400 + 1))
        config.PASSWORD = "changed"
        self.assertFalse(auth.valid(token, now=1001))
        self.assertFalse(auth.valid("garbage"))


if __name__ == "__main__":
    unittest.main()
