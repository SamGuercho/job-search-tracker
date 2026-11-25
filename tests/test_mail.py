"""Email: Google sign-in state, parsing Gmail messages, the filter (only emails naming an application), the check
loop with Gmail and the model stubbed, and applying each kind of update to the process."""
import base64
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from jobtracker import config, registry
from jobtracker.api import app
from jobtracker.services import companies, llm, mail, today
from tests.test_core import reset  # sets the temp data/output env vars first


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def gmail_message(mid, sender, subject, text="", html="", unsubscribe=False, at=1_790_000_000):
    headers = [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]
    if unsubscribe:
        headers.append({"name": "List-Unsubscribe", "value": "<mailto:u@x.com>"})
    parts = ([{"mimeType": "text/plain", "body": {"data": b64(text)}}] if text else []) + \
        ([{"mimeType": "text/html", "body": {"data": b64(html)}}] if html else [])
    return {"id": mid, "internalDate": str(at * 1000), "snippet": "",
            "payload": {"mimeType": "multipart/alternative", "headers": headers, "parts": parts}}


def email(sender="", subject="", text="", unsubscribe=False):
    return {"id": "m", "sender": sender, "subject": subject, "text": text, "received_at": "2026-11-25T10:00:00",
            "list_unsubscribe": unsubscribe}


class Configured(unittest.TestCase):
    def setUp(self):
        reset()
        self._cfg = config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET
        config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET = "client", "secret"
        mail._tokens.clear()

    def tearDown(self):
        config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET = self._cfg

    def connect(self):
        with mail.store.transaction() as book:
            book.account = mail.MailAccount(email="me@gmail.com", refresh_token="r", checked_until=1000)


class SignInTests(Configured):
    def test_state_is_tied_to_the_person_and_expires(self):
        state = mail.make_state("dana@x.com")
        self.assertTrue(mail.check_state(state, "dana@x.com"))
        self.assertFalse(mail.check_state(state, "friend@x.com"))
        self.assertFalse(mail.check_state(state, "dana@x.com", now=time.time() + 3600))
        self.assertFalse(mail.check_state(state[:-1] + ("1" if state[-1] == "0" else "0"), "dana@x.com"))
        self.assertFalse(mail.check_state("garbage", "dana@x.com"))

    def test_connect_url_asks_read_only_offline_access(self):
        url = mail.connect_url("https://pc.tail.ts.net/")
        self.assertIn("gmail.readonly", url)
        self.assertIn("access_type=offline", url)
        self.assertIn("redirect_uri=https%3A%2F%2Fpc.tail.ts.net%2Fmail%2Fcallback", url)

    def test_finish_saves_the_account_and_starts_a_few_days_back(self):
        state = mail.make_state("")
        with mock.patch.object(mail, "_post_form", return_value={"access_token": "a", "refresh_token": "r",
                                                                  "expires_in": 3600, "scope": mail.SCOPE}), \
                mock.patch.object(mail, "_get", return_value={"emailAddress": "me@gmail.com"}):
            account = mail.finish_connect("code", state, "https://pc.tail.ts.net/")
        self.assertEqual((account.email, account.refresh_token), ("me@gmail.com", "r"))
        self.assertLess(account.checked_until, time.time() - 2 * 86400)
        self.assertTrue(registry.run("mail.status", {})["connected"])
        with self.assertRaises(mail.MailError):
            mail.finish_connect("code", "bad-state", "https://pc.tail.ts.net/")

    def test_not_configured(self):
        config.GOOGLE_CLIENT_ID = ""
        with self.assertRaises(mail.MailError):
            mail.connect_url("https://x/")
        self.assertFalse(registry.run("mail.status", {})["configured"])

    def test_routes(self):
        client = TestClient(app)
        r = client.get("/mail/connect", headers={"host": "pc.tail.ts.net"}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith(mail.AUTH_URL))
        self.assertIn("https%3A%2F%2Fpc.tail.ts.net%2Fmail%2Fcallback", r.headers["location"])  # https on Tailscale
        r = client.get("/mail/callback?error=access_denied", follow_redirects=False)
        self.assertIn("mail_error=", r.headers["location"])

    def test_disconnect_forgets_the_token(self):
        self.connect()
        with mock.patch.object(mail, "_post_form", return_value={}) as revoke:
            registry.run("mail.disconnect", {})
        self.assertEqual(revoke.call_args.args[0], mail.REVOKE_URL)
        self.assertFalse(mail.status()["connected"])


class ParseAndFilterTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.nimbus = companies.create_company(name="Nimbus Inc.", position="Data Scientist",
                                               website="https://www.nimbus.io/careers")
        self.orbit = companies.create_company(name="Orbitex", position="ML Engineer")
        self.apps = companies.list_companies()

    def test_parse_prefers_plain_text_else_html(self):
        e = mail.parse(gmail_message("1", "Jobs <jobs@nimbus.io>", "Your application", text="Hello\n\n\n\nthere"))
        self.assertEqual((e["sender"], e["subject"], e["text"]), ("Jobs <jobs@nimbus.io>", "Your application", "Hello\n\nthere"))
        e = mail.parse(gmail_message("2", "x@y.com", "Hi", html="<p>Thanks for applying</p><script>x()</script>"))
        self.assertEqual(e["text"], "Thanks for applying")

    def test_company_domain_or_name(self):
        ids = lambda e: [c.id for c in mail.candidates(e, self.apps)]
        self.assertEqual(ids(email("Talent <talent@careers.nimbus.io>", "Update")), [self.nimbus.id])  # subdomain
        self.assertEqual(ids(email("no-reply@greenhouse.io", "Your application to Nimbus")), [self.nimbus.id])  # no "Inc."
        self.assertEqual(ids(email("no-reply@ashbyhq.com", "Update", "Thank you for your interest in Orbitex")), [self.orbit.id])
        self.assertEqual(ids(email("friend@gmail.com", "Dinner?", "See you at 8")), [])

    def test_position_only_and_job_alerts(self):
        ids = lambda e: [c.id for c in mail.candidates(e, self.apps)]
        self.assertEqual(ids(email("no-reply@lever.co", "Your ML Engineer application")), [self.orbit.id])
        self.assertEqual(ids(email("alerts@linkedin.com", "10 new ML Engineer jobs", unsubscribe=True)), [])
        # a newsletter that names the company still counts: the model decides
        self.assertEqual(ids(email("news@nimbus.io", "Nimbus newsletter", unsubscribe=True)), [self.nimbus.id])


class CheckAndApplyTests(Configured):
    def setUp(self):
        super().setUp()
        self.c = companies.create_company(name="Nimbus", position="Data Scientist", website="https://nimbus.io")
        self.connect()
        self.messages = {
            "a": gmail_message("a", "jobs@nimbus.io", "Interview", text="Technical interview Tuesday 10:00"),
            "b": gmail_message("b", "friend@gmail.com", "Dinner", text="See you"),
            "c": gmail_message("c", "no-reply@greenhouse.io", "Nimbus: an update", text="We won't move forward"),
        }

    def verdict(self, email_, options):
        if "Interview" in email_["subject"]:
            return mail._Verdict(company_id=options[0].id, kind="interview_invite", summary="Technical interview Tue 10:00",
                                 step_name="Technical interview", date="2026-12-01", time="10:00", duration_min=45,
                                 location="https://zoom.us/j/1")
        return mail._Verdict(company_id=options[0].id, kind="rejection", summary="Not moving forward",
                             step_name="", date="", time="", duration_min=0, location="")

    def run_check(self, ids, verdict=None):
        with mock.patch.object(mail, "list_new", return_value=ids), \
                mock.patch.object(mail, "fetch", side_effect=lambda i: mail.parse(self.messages[i])), \
                mock.patch.object(mail, "classify", side_effect=verdict or self.verdict) as classify, \
                mock.patch.object(mail.push, "notify", return_value=1) as notify:
            return registry.run("mail.check", {}), classify, notify

    def test_only_emails_about_applications_reach_the_model(self):
        r, classify, notify = self.run_check(["a", "b", "c"])
        self.assertEqual((r["looked_at"], r["about_applications"]), (3, 2))
        self.assertEqual(classify.call_count, 2)
        self.assertEqual([f["kind"] for f in r["found"]], ["interview_invite", "rejection"])
        self.assertEqual(notify.call_count, 2)
        self.assertIn("Nimbus: interview invite", notify.call_args_list[0].args[0])
        # already seen: not again, and the check moved forward
        r, classify, _ = self.run_check(["a", "b", "c"])
        self.assertEqual((r["looked_at"], classify.call_count), (0, 0))
        self.assertGreater(mail.store.load().account.checked_until, 1000)
        self.assertEqual(len(today.summary()["mail"]), 2)

    def test_model_unavailable_retries_later(self):
        def down(*_):
            raise llm.LLMUnavailable("no key")
        r, _, _ = self.run_check(["a"], verdict=down)
        self.assertIn("no key", r["error"])
        account = mail.store.load().account
        self.assertEqual(account.checked_until, 1000)
        self.assertNotIn("a", mail.store.load().seen)

    def test_unrelated_and_invalid_answers_are_dropped(self):
        def wrong(email_, options):
            return mail._Verdict(company_id="not-a-candidate", kind="rejection", summary="", step_name="", date="",
                                 time="", duration_min=0, location="")
        with mock.patch.object(mail.llm, "structured", side_effect=lambda *a: wrong(None, None)):
            self.assertEqual(mail.classify(email("a@nimbus.io", "x"), [self.c]).kind, "unrelated")

    def test_apply_invite_plans_the_step_and_rejection_closes(self):
        r, _, _ = self.run_check(["a", "c"])
        invite, rejection = r["found"]
        self.assertIn("Plan “Technical interview” on 2026-12-01 at 10:00", invite["action"])
        done = registry.run("mail.apply", {"update": invite["id"]})
        self.assertIn("“Technical interview” added and planned", done["applied"]["result"])
        steps = {n.name: n for n in companies.get_company(self.c.id).process.steps}
        step = steps["Technical interview"]
        self.assertEqual((step.state, step.date, step.time, step.duration_min, step.location),
                         ("planned", "2026-12-01", "10:00", 45, "https://zoom.us/j/1"))
        self.assertEqual(steps["Response received"].state, "done")
        with self.assertRaises(ValueError):
            registry.run("mail.apply", {"update": invite["id"]})  # once only
        registry.run("mail.apply", {"update": rejection["id"]})
        company = companies.get_company(self.c.id)
        self.assertEqual(company.status, "Rejected")
        self.assertEqual(registry.run("mail.inbox", {})["proposed"], [])

    def test_existing_step_is_reused_and_confirmation_marks_applied(self):
        registry.run("process.add_step", {"company": "Nimbus", "name": "Technical interview"})
        r, _, _ = self.run_check(["a"])
        registry.run("mail.apply", {"update": r["found"][0]["id"]})
        names = [n.name for n in companies.get_company(self.c.id).process.steps]
        self.assertEqual(names.count("Technical interview"), 1)
        received = mail.MailEvent(message_id="z", company_id=self.c.id, company="Nimbus", kind="application_received",
                                  received_at="2026-11-20T09:00:00", summary="Thanks for applying")
        with mail.store.transaction() as book:
            book.events.append(received)
        registry.run("mail.apply", {"update": received.id})
        applied = next(n for n in companies.get_company(self.c.id).process.steps if n.name == "Applied")
        self.assertEqual((applied.state, applied.date), ("done", "2026-11-20"))

    def test_dismiss_changes_nothing(self):
        r, _, _ = self.run_check(["c"])
        registry.run("mail.dismiss", {"update": r["found"][0]["id"]})
        self.assertNotEqual(companies.get_company(self.c.id).status, "Rejected")
        self.assertEqual(registry.run("mail.inbox", {})["recent"][0]["status"], "dismissed")


if __name__ == "__main__":
    unittest.main()
