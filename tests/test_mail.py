"""Email: Google sign-in state, parsing Gmail messages, the filter (only emails naming an application), the check
loop with Gmail and the model stubbed, and applying each kind of update to the process."""
import base64
import tempfile
import time
import unittest
from pathlib import Path
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
            return mail._Verdict(company_id=options[0].id, kind="interview_invite", summary="Technical interview Tue 10:00", person="",
                                 step_name="Technical interview", date="2026-12-01", time="10:00", duration_min=45,
                                 location="https://zoom.us/j/1")
        return mail._Verdict(company_id=options[0].id, kind="rejection", summary="Not moving forward", person="",
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
            return mail._Verdict(company_id="not-a-candidate", kind="rejection", summary="", person="", step_name="", date="",
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
        self.assertEqual(applied.state, "done")  # by the invite already (news from them): the confirmation keeps it

    def test_dismiss_changes_nothing(self):
        r, _, _ = self.run_check(["c"])
        registry.run("mail.dismiss", {"update": r["found"][0]["id"]})
        self.assertNotEqual(companies.get_company(self.c.id).status, "Rejected")
        self.assertEqual(registry.run("mail.inbox", {})["recent"][0]["status"], "dismissed")


if __name__ == "__main__":
    unittest.main()


class MonitoringTests(Configured):
    """Aliases, outcomes, "how did you hear?", the diagnosis of a miss and its fixes, and what the monitor gets."""

    def setUp(self):
        super().setUp()
        self.c = companies.create_company(name="Zafran", position="Data Scientist", website="https://zafran.io")
        self.connect()
        self.events = []
        patcher = mock.patch.object(mail, "_telemetry", side_effect=lambda t, **p: self.events.append((t, p)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.messages = {
            "hebrew": gmail_message("hebrew", "צוות גיוס <jobs@comeet.co>", "עדכון", text="תודה על הזמן. מזפרן: לא נמשיך"),
            "other": gmail_message("other", "Talent <talent@zafran-sec.com>", "Update", text="We won't move forward"),
            "news": gmail_message("news", "x@ads.com", "Sale", text="Buy now", unsubscribe=True),
        }

    def fetch(self, i):
        return mail.parse(self.messages[i])

    def run_check(self, ids, kind="rejection"):
        verdict = lambda e, o: mail._Verdict(company_id=o[0].id, kind=kind, summary="x", person="", step_name="", date="",
                                             time="", duration_min=0, location="")
        with mock.patch.object(mail, "list_new", return_value=ids), mock.patch.object(mail, "fetch", side_effect=self.fetch), \
                mock.patch.object(mail, "classify", side_effect=verdict), mock.patch.object(mail.push, "notify", return_value=1):
            return mail.check()

    def test_hebrew_names_with_prefixes_and_extra_domains(self):
        self.assertTrue(mail.mentions("זפרן", "תודה מזפרן"))
        self.assertFalse(mail.mentions("זפרן", "זפרנים"))
        e = mail.parse(self.messages["other"])
        self.assertEqual(mail.candidates(e, [self.c]), [])
        companies.update_company(self.c.id, email_domains=["zafran-sec.com"])
        self.assertEqual(len(mail.candidates(e, [companies.get_company(self.c.id)])), 1)
        companies.update_company(self.c.id, other_names=["זפרן"])
        self.assertEqual(len(mail.candidates(mail.parse(self.messages["hebrew"]), [companies.get_company(self.c.id)])), 1)

    def test_outcomes_and_counts_without_content(self):
        r = self.run_check(["other", "news"])
        self.assertEqual(r["about_applications"], 0)
        self.assertEqual(mail.store.load().outcomes, {"other": "skipped", "news": "skipped"})
        check = next(p for t, p in self.events if t == "mail.check")
        self.assertEqual((check["looked"], check["found"]), (2, 0))
        companies.update_company(self.c.id, email_domains=["zafran-sec.com"])
        self.messages["other2"] = gmail_message("other2", "talent@zafran-sec.com", "Update", text="No")
        self.run_check(["other2"])
        self.assertEqual(mail.store.load().outcomes["other2"], "proposed")
        found = next(p for t, p in self.events if t == "mail.found")
        self.assertEqual((found["kind"], found["platform"], found["language"]), ("rejection", "direct", "en"))
        self.assertNotIn("subject", found)
        dump = repr(self.events)
        self.assertNotIn("move forward", dump)
        self.assertNotIn("zafran-sec.com", dump)

    def test_heard_found_missed_and_logged(self):
        self.assertEqual(registry.run("company.heard", {"company": "Zafran", "channel": "email", "step": "Rejected / Closed"})["mail"], "missed")
        self.run_check(["hebrew"])  # (name not known yet: skipped)
        companies.update_company(self.c.id, email_domains=["zafran-sec.com"])
        self.run_check(["other"])
        r = registry.run("company.heard", {"company": "Zafran", "channel": "email"})
        self.assertEqual(r["mail"], "found")
        registry.run("company.heard", {"company": "Zafran", "channel": "call", "step": "Applied"})
        log = companies.get_company(self.c.id).news_log
        self.assertEqual([(x.channel, x.mail, x.step_type) for x in log],
                         [("email", "missed", "closed"), ("email", "found", ""), ("call", "", "applied")])
        heard = [p for t, p in self.events if t == "mail.heard"]
        self.assertEqual([p["mail"] for p in heard], ["missed", "found", ""])
        with mail.store.transaction() as book:
            book.account = mail.MailAccount()
        self.assertEqual(mail.heard("Zafran", "email")["mail"], "not_connected")
        with self.assertRaises(ValueError):
            mail.heard("Zafran", "pigeon")

    def test_diagnose_filter_miss_and_fix_it(self):
        self.run_check(["other"])
        with mock.patch.object(mail, "fetch", side_effect=self.fetch):
            d = registry.run("mail.diagnose", {"company": "Zafran", "message": "other"})
            self.assertEqual(d["reason"], "filter")
            fixes = {f["type"]: f["value"] for f in d["fixes"]}
            self.assertEqual(fixes["domain"], "zafran-sec.com")
            r = registry.run("mail.fix_filter", {"company": "Zafran", "message": "other", "domain": "zafran-sec.com"})
        self.assertTrue(r["works"])
        self.assertEqual(companies.get_company(self.c.id).email_domains, ["zafran-sec.com"])
        self.assertEqual(mail.store.load().outcomes["other"], "fixed")
        miss = next(p for t, p in self.events if t == "mail.miss")
        self.assertEqual((miss["reason"], miss["platform"]), ("filter", "direct"))
        self.assertEqual(next(p for t, p in self.events if t == "mail.fixed")["works"], True)

    def test_diagnose_platform_email_offers_a_name_and_other_reasons(self):
        with mock.patch.object(mail, "fetch", side_effect=self.fetch):
            d = mail.diagnose("Zafran", "hebrew")
            self.assertEqual(d["reason"], "not_checked")  # never looked at
            self.assertEqual([f["type"] for f in d["fixes"]], ["name"])  # comeet.co is a platform: no domain fix
            self.assertEqual(next(p for t, p in self.events if t == "mail.miss")["language"], "he")
            r = mail.fix_filter("Zafran", "hebrew", name="זפרן")
            self.assertTrue(r["works"])
            with mail.store.transaction() as book:
                book.outcomes["other"] = "unrelated"
            d = mail.diagnose("Zafran", "other")
            self.assertEqual(d["reason"], "model")
            self.assertIn("unrelated", d["explanation"])
            registry.run("mail.correct", {"company": "Zafran", "message": "other", "kind": "rejection"})
        label = next(p for t, p in self.events if t == "mail.label")
        self.assertEqual((label["predicted"], label["correct"]), ("unrelated", "rejection"))

    def test_share_sends_content_only_when_asked(self):
        with mock.patch.object(mail, "fetch", side_effect=self.fetch):
            registry.run("mail.share", {"message": "other", "note": "missed it"})
        shared = next(p for t, p in self.events if t == "mail.shared")
        self.assertEqual((shared["subject"], shared["note"]), ("Update", "missed it"))
        self.assertIn("move forward", shared["text"])
        from jobtracker.services import assistant
        self.assertTrue("mail.share".startswith(assistant.HIDDEN))
        self.assertTrue(registry.ACTIONS["mail.share"].confirm)

    def test_dismiss_reason_is_counted(self):
        companies.update_company(self.c.id, email_domains=["zafran-sec.com"])
        r = self.run_check(["other"])
        registry.run("mail.dismiss", {"update": r["found"][0]["id"], "reason": "wrong_application"})
        decision = next(p for t, p in self.events if t == "mail.decision")
        self.assertEqual((decision["decision"], decision["reason"]), ("dismissed", "wrong_application"))

    def test_find_missed_ranks_likely_emails(self):
        listing = {"messages": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}
        meta = {
            "1": {"from": "Zafran Talent <no-reply@comeet.co>", "subject": "Your application", "snippet": "Hi"},
            "2": {"from": "friend@gmail.com", "subject": "Dinner", "snippet": "8pm"},
            "3": {"from": "deals@shop.com", "subject": "Zafran-style rugs", "snippet": "", "unsub": True},
        }

        def get(path, params=None):
            if path == "/messages":
                return listing
            m = meta[path.rsplit("/", 1)[1]]
            headers = [{"name": "From", "value": m["from"]}, {"name": "Subject", "value": m["subject"]}]
            if m.get("unsub"):
                headers.append({"name": "List-Unsubscribe", "value": "<x>"})
            return {"internalDate": "1790000000000", "snippet": m["snippet"], "payload": {"headers": headers}}
        with mock.patch.object(mail, "_get", side_effect=get):
            r = registry.run("mail.find_missed", {"company": "Zafran"})
        self.assertEqual([e["id"] for e in r["emails"]], ["1"])  # the newsletter and the dinner are left out


class MonitorEmailPageTests(unittest.TestCase):
    def test_counts_precision_recall_and_misses(self):
        from jobtracker.telemetry import store
        from jobtracker.telemetry.emit import emit
        from monitor import queries
        reset()
        db = Path(tempfile.mkdtemp()) / "t.db"
        saved = config.TELEMETRY_DB, config.TELEMETRY
        config.TELEMETRY_DB, config.TELEMETRY = db, True
        try:
            emit("mail.check", status="ok", looked=10, about_applications=3, found=2, verdicts={"rejection": 2, "unrelated": 1})
            emit("mail.check", status="error", looked=0, about_applications=0, found=0, verdicts={}, error="expired")
            emit("mail.found", kind="rejection", delay_s=600, platform="Greenhouse", language="en")
            emit("mail.found", kind="interview_invite", delay_s=1200, platform="direct", language="he")
            emit("mail.decision", decision="applied", kind="rejection", reason="")
            emit("mail.decision", decision="dismissed", kind="interview_invite", reason="wrong_application")
            emit("mail.heard", channel="email", step_type="closed", mail="found")
            emit("mail.heard", channel="email", step_type="hr", mail="missed")
            emit("mail.heard", channel="call", step_type="hr", mail="")
            emit("mail.miss", reason="filter", outcome="skipped", platform="Comeet", language="he", newsletter=False,
                 matches_now=False, fixes=["name"])
            emit("mail.shared", sender="a@b.c", subject="Hi", received_at="2026-11-25T10:00:00", text="Body", note="",
                 outcome="skipped", platform="Comeet", language="he", newsletter=False)
            self.assertTrue(store.flush())
            conn = store.connect(db, readonly=True)
            m = queries.mail(conn, 30, None)
        finally:
            config.TELEMETRY_DB, config.TELEMETRY = saved
        t = m["totals"]
        self.assertEqual((t["checks"], t["errors"], t["looked"], t["about"], t["found"]), (2, 1, 10, 3, 2))
        self.assertEqual((t["precision"], t["recall"]), (0.5, 0.5))
        self.assertEqual(t["delay_min"]["p50"], 15)
        self.assertEqual(m["dismiss_reasons"], {"wrong_application": 1})
        self.assertEqual(m["channels"], {"email": 2, "call": 1})
        self.assertEqual(m["miss_reasons"], {"filter": 1})
        self.assertEqual(m["shared"][0]["text"], "Body")
        self.assertEqual(m["errors"][0]["error"], "expired")


class ReferralTests(Configured):
    def setUp(self):
        super().setUp()
        self.c = companies.create_company(name="Riskified", position="Data Scientist", website="https://riskified.com")
        self.connect()

    def event(self, kind, person=""):
        e = mail.MailEvent(message_id=kind, company_id=self.c.id, company="Riskified", kind=kind, person=person,
                           received_at="2026-11-24T09:00:00", summary="Dana referred you to Riskified")
        with mail.store.transaction() as book:
            book.events.append(e)
        return e

    def applied(self):
        return next(n for n in companies.get_company(self.c.id).process.steps if n.name == "Applied")

    def test_referral_marks_applied_and_notes_the_referee(self):
        e = self.event("referred", "Dana Levi")
        self.assertEqual(mail.view(e)["action"], "Mark Applied done (referred by Dana Levi), note the referral and log the news")
        r = registry.run("mail.apply", {"update": e.id})
        self.assertIn("“Applied” done", r["applied"]["result"])
        c = companies.get_company(self.c.id)
        self.assertEqual((self.applied().state, self.applied().date), ("done", "2026-11-24"))
        self.assertEqual((c.status, c.source_type, c.referee, c.source_detail), ("Applied", "Referral / network", "Dana Levi", "Dana Levi"))

    def test_referral_keeps_what_the_user_set(self):
        companies.update_company(self.c.id, source_type="Job board", referee="Someone")
        registry.run("mail.apply", {"update": self.event("referred", "Dana").id})
        c = companies.get_company(self.c.id)
        self.assertEqual((c.source_type, c.referee), ("Job board", "Someone"))

    def test_news_from_them_marks_applied_when_forgotten(self):
        e = self.event("next_step")
        self.assertTrue(mail.view(e)["action"].endswith("and mark Applied (not marked yet)"))
        registry.run("mail.apply", {"update": e.id})
        self.assertEqual(self.applied().state, "done")
        self.assertFalse(mail.view(self.event("offer"))["action"].endswith("(not marked yet)"))  # now it is
        msg = self.event("recruiter_message")
        companies.update_step(self.c.id, self.applied().id, state="todo")
        registry.run("mail.apply", {"update": msg.id})
        self.assertEqual(self.applied().state, "todo")  # a recruiter may write before any application

    def test_person_is_kept_for_referrals_only(self):
        email = {"id": "x", "sender": "a@riskified.com", "subject": "s", "received_at": "2026-11-24T09:00:00"}
        v = mail._Verdict(company_id=self.c.id, kind="next_step", summary="s", person="Dana", step_name="", date="",
                          time="", duration_min=0, location="")
        self.assertEqual(mail._event(email, v, self.c).person, "")

