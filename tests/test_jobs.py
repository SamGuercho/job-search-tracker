"""Reading job links: job board APIs, remembered accounts, embedded job data, polite retry, clear failures,
and the share target (text -> confirmation page, link -> pre-filled message)."""
import base64
import json
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from jobtracker import registry
from jobtracker.api import app
from jobtracker.services import jobs, web
from tests.test_core import reset  # sets the temp data/output env vars first

GH_JOB = {"title": "Staff Data Scientist", "company_name": "Orbitex", "location": {"name": "Israel"},
          "absolute_url": "https://www.orbitex.com/jobs/?gh_jid=4815162003",
          "content": "&lt;h3&gt;Our Purpose&lt;/h3&gt;&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;/ul&gt;"}
S1 = "https://www.orbitex.com/jobs/?search=data&gh_jid=4815162003&utm_source=telegram"


class FakeWeb:
    """web.fetch_bytes replacement: url -> (status or body, content type). A list gives successive answers."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, max_bytes=0, accept="*/*", truncate=False):
        self.calls.append(url)
        answer = self.routes.get(url)
        if isinstance(answer, list):
            answer = answer.pop(0)
        if answer is None:
            raise web.FetchError(f"Could not open {url}: HTTP Error 404", 404)
        if isinstance(answer, web.FetchError):
            raise answer
        body, ctype = answer
        return (json.dumps(body).encode() if isinstance(body, (dict, list)) else body), ctype, url


class JobLinkTests(unittest.TestCase):
    def setUp(self):
        reset()
        self._fetch = web.fetch_bytes

    def tearDown(self):
        web.fetch_bytes = self._fetch

    def use(self, routes):
        web.fetch_bytes = FakeWeb(routes)
        return web.fetch_bytes

    def test_links_that_name_their_board(self):
        self.assertEqual(jobs.board_link("https://job-boards.greenhouse.io/orbitexlabs/jobs/4815162003"), ("greenhouse", "orbitexlabs", "4815162003"))
        self.assertEqual(jobs.board_link("https://boards.greenhouse.io/embed/job_app?for=acme&token=42"), ("greenhouse", "acme", "42"))
        lever = "https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd"
        self.assertEqual(jobs.board_link(lever)[0:2], ("lever", "palantir"))
        self.assertEqual(jobs.board_link("https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245")[0], "ashby")
        self.assertEqual(jobs.board_link("https://jobs.smartrecruiters.com/BoschGroup/744000162835320-quality-manager"),
                         ("smartrecruiters", "BoschGroup", "744000162835320"))
        self.assertIsNone(jobs.board_link(S1))  # gh_jid on the company's site: account not known yet

    def test_greenhouse_api_text(self):
        self.use({"https://boards-api.greenhouse.io/v1/boards/orbitexlabs/jobs/4815162003": (GH_JOB, "application/json")})
        r = jobs.read("https://job-boards.greenhouse.io/orbitexlabs/jobs/4815162003")
        self.assertEqual((r["via"], r["title"], r["location"]), ("Greenhouse API", "Staff Data Scientist", "Israel"))
        self.assertIn("Build & ship", r["text"])
        self.assertIn("• Python", r["text"])
        self.assertTrue(r["text"].startswith("Staff Data Scientist\nCompany: Orbitex"))

    def test_blocked_site_then_remembered_account(self):
        fake = self.use({S1: web.FetchError("HTTP Error 429", 429, retry_after=None),
                         "https://boards-api.greenhouse.io/v1/boards/orbitexlabs/jobs/4815162003": (GH_JOB, "application/json")})
        with self.assertRaises(ValueError) as e:
            registry.run("job.fetch_url", {"url": S1})
        self.assertIn("refuses automated reading (HTTP 429)", str(e.exception))
        self.assertIn("Share → Ezri", str(e.exception))
        registry.run("job.remember_board", {"url": "https://www.orbitex.com/jobs/", "platform": "greenhouse", "account": "orbitexlabs"})
        r = registry.run("job.fetch_url", {"url": S1})
        self.assertEqual(r["via"], "Greenhouse API")
        self.assertEqual(fake.calls[-1], "https://boards-api.greenhouse.io/v1/boards/orbitexlabs/jobs/4815162003")

    def test_greenhouse_account_found_without_the_company_site(self):
        embed = "https://boards.greenhouse.io/embed/job_app?token=4815162003"
        api = "https://boards-api.greenhouse.io/v1/boards/orbitexlabs/jobs/4815162003"
        fake = self.use({S1: web.FetchError("HTTP Error 429", 429), embed: (b"<html>Staff Data Scientist</html>", "text/html"), api: (GH_JOB, "application/json")})
        fake_final = fake.__call__

        def with_redirect(url, *a, **k):  # Greenhouse redirects to ...?for=<account>&token=<id>
            body, ctype, final = fake_final(url, *a, **k)
            return body, ctype, ("https://job-boards.greenhouse.io/embed/job_app?for=orbitexlabs&token=4815162003" if url == embed else final)
        web.fetch_bytes = with_redirect
        r = jobs.read(S1)
        self.assertEqual((r["via"], r["title"]), ("Greenhouse API", "Staff Data Scientist"))
        self.assertNotIn(S1, fake.calls)  # the company's protected site was never requested
        self.assertEqual(jobs.known_board("https://www.orbitex.com/").account, "orbitexlabs")

    def test_page_teaches_its_board_and_polite_retry(self):
        page = b'<html><script src="https://boards.greenhouse.io/embed/job_board/js?for=acmeco"></script></html>'
        url = "https://acme.example/careers?gh_jid=42"
        self.use({url: [web.FetchError("HTTP Error 429", 429, retry_after=0), (page, "text/html")],
                  "https://boards-api.greenhouse.io/v1/boards/acmeco/jobs/42": ({**GH_JOB, "title": "Analyst"}, "application/json")})
        with mock.patch("time.sleep") as slept:
            r = jobs.read(url)
        slept.assert_called_once()
        self.assertEqual((r["via"], r["title"]), ("Greenhouse API", "Analyst"))
        self.assertEqual(jobs.known_board("https://acme.example/other").account, "acmeco")

    def test_embedded_job_data_and_javascript_pages(self):
        ld = {"@context": "https://schema.org", "@graph": [{"@type": "Organization", "name": "Acme"},
              {"@type": "JobPosting", "title": "Data Engineer", "hiringOrganization": {"name": "Acme"},
               "jobLocation": {"address": {"addressLocality": "Tel Aviv", "addressCountry": "IL"}},
               "description": "<p>Build pipelines with <b>dbt</b></p>"}]}
        page = f'<html><script type="application/ld+json">{json.dumps(ld)}</script><div id=app></div></html>'.encode()
        self.use({"https://acme.example/job/1": (page, "text/html"), "https://empty.example/job": (b"<html><div id=root></div></html>", "text/html")})
        r = jobs.read("https://acme.example/job/1")
        self.assertEqual((r["via"], r["title"], r["company"], r["location"]), ("job data embedded in the page", "Data Engineer", "Acme", "Tel Aviv, IL"))
        self.assertIn("Build pipelines with dbt", r["text"])
        with self.assertRaises(ValueError) as e:
            jobs.read("https://empty.example/job")
        self.assertIn("JavaScript", str(e.exception))

    def test_closed_job(self):
        self.use({})
        with self.assertRaises(ValueError) as e:
            jobs.read("https://job-boards.greenhouse.io/orbitexlabs/jobs/4815169003")
        self.assertIn("no longer published", str(e.exception))

    def test_lever_ashby_smartrecruiters(self):
        lever_id = "6ed76ce8-4156-4b60-b120-403538bd66cd"
        ashby_id = "34413f8d-26bf-4bbc-8ade-eb309a0e2245"
        self.use({f"https://api.lever.co/v0/postings/palantir/{lever_id}": ({"text": "Partner", "categories": {"location": "Singapore"},
                  "descriptionPlain": "Intro", "lists": [{"text": "What we value", "content": "<li>Grit</li>"}], "additionalPlain": "Apply"}, "application/json"),
                  "https://api.ashbyhq.com/posting-api/job-board/ramp": ({"jobs": [{"id": ashby_id, "title": " Engineer ", "location": "NY", "descriptionPlain": "About Ramp"}]}, "application/json"),
                  "https://api.smartrecruiters.com/v1/companies/BoschGroup/postings/744000162835320": ({"name": "Manager", "company": {"name": "Bosch"},
                   "location": {"fullLocation": "Queretaro"}, "jobAd": {"sections": {"jobDescription": {"title": "Job", "text": "<p>Lead</p>"}}}}, "application/json")})
        self.assertIn("• Grit", jobs.read(f"https://jobs.lever.co/palantir/{lever_id}")["text"])
        self.assertEqual(jobs.read(f"https://jobs.ashbyhq.com/ramp/{ashby_id}")["title"], "Engineer")
        sr = jobs.read("https://jobs.smartrecruiters.com/BoschGroup/744000162835320-manager")
        self.assertEqual((sr["company"], sr["location"]), ("Bosch", "Queretaro"))
        self.assertIn("Lead", sr["text"])


class ShareTargetTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.client = TestClient(app)

    def test_shared_text_goes_to_the_confirmation_page(self):
        text = "Staff Data Scientist at Orbitex. " * 20 + "https://www.orbitex.com/jobs/?gh_jid=1"
        r = self.client.get("/share", params={"title": "Job", "text": text}, follow_redirects=False)
        location = r.headers["location"]
        self.assertTrue(location.startswith("/#/clip?d="))
        data = json.loads(base64.urlsafe_b64decode(location.split("d=")[1] + "==").decode())
        self.assertEqual(data["u"], "https://www.orbitex.com/jobs/?gh_jid=1")
        self.assertIn("Staff Data Scientist", data["x"])
        self.assertNotIn("https://", data["x"])

    def test_a_link_only_prefills_the_assistant(self):
        r = self.client.get("/share", params={"text": "https://jobs.lever.co/x/1"}, follow_redirects=False)
        self.assertTrue(r.headers["location"].startswith("/#/chat?q="))
        self.assertNotIn("go=", r.headers["location"])  # never sent automatically from outside the app


LINKEDIN_PAGE = """Skip to main content
Home
My Network
Jobs
3 notifications
Try Premium for $0
Data Analyst
Acme · Tel Aviv · 2 days ago
Edikted
Share
Show more options
Retail Data Analyst
Los Angeles, CA · 1 week ago · Over 200 people clicked apply
Promoted by hirer · Responses managed off LinkedIn
On-site
Full-time
Apply
Save
Save Retail Data Analyst at Edikted
Tailor my resume
About the job
Edikted is a Gen Z fashion brand.
Responsibilities:
Analyze sales data to optimize stock replenishment.
Premium service mindset matters here.
… more
Requirements added by the job poster
* Bachelor's Degree
* 1+ years of work experience with Microsoft Excel
Set alert for similar jobs
About the company
Edikted
12,345 followers
Follow"""


class LinkedInTextTests(unittest.TestCase):
    """A LinkedIn job page sent from the browser (or pasted whole) keeps the job only."""

    def setUp(self):
        reset()

    def test_keeps_the_top_card_the_job_and_the_posters_requirements(self):
        text = jobs.clean_posting(LINKEDIN_PAGE, "https://www.linkedin.com/jobs/view/1",
                                  "(3) Retail Data Analyst | Edikted | LinkedIn")
        self.assertTrue(text.startswith("Retail Data Analyst\nEdikted · Los Angeles, CA · On-site · Full-time"))
        self.assertIn("Analyze sales data to optimize stock replenishment.", text)
        self.assertIn("Premium service mindset matters here.", text)  # a job line starting like a LinkedIn section
        self.assertIn("Requirements added by the job poster\n* Bachelor's Degree", text)
        for noise in ("Save", "Tailor my resume", "clicked apply", "… more", "Set alert", "followers", "My Network",
                      "Acme", "Promoted"):
            self.assertNotIn(noise, text)

    def test_a_paste_without_link_or_title_is_recognised(self):
        text = jobs.clean_posting(LINKEDIN_PAGE)
        self.assertTrue(text.startswith("Retail Data Analyst\nEdikted · Los Angeles, CA"), text[:80])
        self.assertNotIn("Tailor my resume", text)

    def test_other_text_is_left_alone(self):
        plain = "About the job\nWe need SQL.\nSave money on cloud costs."
        self.assertEqual(jobs.clean_posting(plain), plain)
        self.assertEqual(registry.run("job.clean_text", {"text": plain})["text"], plain)

    def test_a_new_application_stores_the_job_only(self):
        r = registry.run("workflow.new_application", {"name": "Edikted", "job_description": LINKEDIN_PAGE,
                                                       "job_url": "https://www.linkedin.com/jobs/view/1",
                                                       "generate_cv": False})
        stored = registry.run("company.get", {"id": r["company"]["id"]})["job_description"]
        self.assertNotIn("Tailor my resume", stored)
        self.assertIn("About the job", stored)


if __name__ == "__main__":
    unittest.main()
