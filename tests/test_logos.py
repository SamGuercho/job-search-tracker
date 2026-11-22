"""Company logos: candidates from a page, choosing the best image, upload/remove, safe SVG serving."""
import io
import os
import time
import unittest

from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from jobtracker import registry, users
from jobtracker.api import app
from jobtracker.services import companies, logos, web
from tests.test_core import reset  # sets the temp data/output env vars first


def png(w, h=None, color=(73, 52, 201, 255)):
    out = io.BytesIO()
    Image.new("RGBA", (w, h or w), color).save(out, format="PNG")
    return out.getvalue()


def ico(sizes):
    out = io.BytesIO()
    Image.new("RGBA", (max(sizes), max(sizes)), (200, 30, 30, 255)).save(out, format="ICO", sizes=[(s, s) for s in sizes])
    return out.getvalue()


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><script>alert(1)</script><rect width="10" height="10"/></svg>'
PAGE = b"""<html><head>
<link rel="icon" href="/favicon-16.png" sizes="16x16">
<link rel="apple-touch-icon" href="https://cdn.nimbus.example/touch.png">
<link rel="manifest" href="/site.webmanifest">
</head><body><img class="site-logo" src="/img/wordmark.png"></body></html>"""


class FakeWeb:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, max_bytes=0, accept="*/*", truncate=False):
        self.calls.append(url)
        if url not in self.routes:
            raise ValueError(f"Could not open {url}: 404")
        body, ctype = self.routes[url]
        return body, ctype, url


class LogoTests(unittest.TestCase):
    def setUp(self):
        reset()
        self._fetch = web.fetch_bytes

    def tearDown(self):
        web.fetch_bytes = self._fetch
        os.environ["JOBTRACKER_LOGOS"] = "0"

    def use(self, routes):
        web.fetch_bytes = FakeWeb(routes)
        return web.fetch_bytes

    def test_candidates_best_first(self):
        fake = self.use({"https://nimbus.example/site.webmanifest": (b'{"icons":[{"src":"/android-512.png","sizes":"512x512"}]}', "application/json")})
        urls = [u for _, u in logos.candidates("https://nimbus.example/", PAGE.decode())]
        self.assertEqual(urls[0], "https://cdn.nimbus.example/touch.png")          # phone icon first
        self.assertEqual(urls[1], "https://nimbus.example/android-512.png")        # then the manifest's
        self.assertLess(urls.index("https://nimbus.example/img/wordmark.png"), urls.index("https://nimbus.example/favicon-16.png"))
        self.assertEqual(urls[-1], "https://nimbus.example/favicon.ico")
        self.assertIn("https://nimbus.example/site.webmanifest", fake.calls)

    def test_finds_the_phone_icon_and_stops(self):
        fake = self.use({"https://nimbus.example": (PAGE, "text/html"),
                         "https://cdn.nimbus.example/touch.png": (png(180), "image/png")})
        found = logos.find("nimbus.example")  # scheme added
        self.assertEqual((found["ext"], found["size"], found["source"]), (".png", 180, "https://cdn.nimbus.example/touch.png"))
        self.assertNotIn("https://nimbus.example/favicon.ico", fake.calls)  # good enough: no more downloads

    def test_square_beats_a_wide_wordmark_and_ico_is_converted(self):
        self.use({"https://acme.example": (b'<img id="logo" src="/wide.png"><link rel="icon" href="/favicon.ico">', "text/html"),
                  "https://acme.example/wide.png": (png(400, 80), "image/png"),
                  "https://acme.example/favicon.ico": (ico([16, 32, 48]), "image/x-icon")})
        found = logos.find("https://acme.example")
        self.assertEqual((found["source"], found["size"]), ("https://acme.example/favicon.ico", 48))  # largest size in the .ico
        self.assertTrue(found["data"].startswith(b"\x89PNG"))

    def test_svg_kept_and_errors(self):
        self.use({"https://vec.example": (b'<link rel="icon" type="image/svg+xml" href="/logo.svg">', "text/html"),
                  "https://vec.example/logo.svg": (SVG, "image/svg+xml")})
        self.assertEqual(logos.find("https://vec.example")["ext"], ".svg")
        self.use({"https://none.example": (b"<html></html>", "text/html"),
                  "https://none.example/favicon.ico": (b"<html>not found</html>", "text/html")})
        with self.assertRaises(ValueError):
            logos.find("https://none.example")

    def test_refresh_upload_remove_and_safe_serving(self):
        c = companies.create_company(name="Nimbus", website="https://nimbus.example")
        self.use({"https://nimbus.example": (PAGE, "text/html"), "https://cdn.nimbus.example/touch.png": (png(180), "image/png")})
        r = registry.run("company.find_logo", {"company": "Nimbus"})
        self.assertEqual(r["logo_file"], "Nimbus/logo.png")
        self.assertTrue((users.output_dir() / "Nimbus/logo.png").exists())
        client = TestClient(app)
        up = client.post("/api/companies/logo", params={"company": c.id}, files={"file": ("mine.svg", SVG, "image/svg+xml")})
        self.assertEqual(up.json()["result"], {"logo_file": "Nimbus/logo.svg", "source": "uploaded"})
        self.assertFalse((users.output_dir() / "Nimbus/logo.png").exists())  # replaced
        served = client.get("/files/Nimbus/logo.svg")
        self.assertIn("sandbox", served.headers["content-security-policy"])  # its <script> can never run
        self.assertEqual(client.post("/api/companies/logo", params={"company": c.id},
                                     files={"file": ("x.png", b"not an image", "image/png")}).status_code, 400)
        registry.run("company.remove_logo", {"company": "Nimbus"})
        c = companies.get_company(c.id)
        self.assertEqual((c.logo_file, c.logo_source), (None, "removed"))
        with self.assertRaises(ValidationError):  # logo fields are not editable through company.update
            registry.run("company.update", {"id": c.id, "fields": {"logo_file": "../../etc/passwd"}})

    def test_searched_automatically_when_a_website_is_set(self):
        os.environ["JOBTRACKER_LOGOS"] = "1"
        self.use({"https://auto.example": (PAGE.replace(b"https://cdn.nimbus.example/touch.png", b"/touch.png"), "text/html"),
                  "https://auto.example/touch.png": (png(192), "image/png")})
        c = companies.create_company(name="Auto Co")
        companies.update_company(c.id, website="https://auto.example")
        for _ in range(100):
            if companies.get_company(c.id).logo_file:
                break
            time.sleep(0.05)
        self.assertEqual(companies.get_company(c.id).logo_file, "Auto Co/logo.png")
        self.assertEqual(companies.get_company(c.id).logo_source, "https://auto.example/touch.png")


    def test_missing_website_is_found_verified_and_saved(self):
        real = b"<html><head><title>Brightwall Software | Cyber Security</title></head><body>" + b"x" * 3000 + b"</body></html>"
        parked = b"<html><head><title>brightwall.io</title></head><body>" + b"x" * 3000 + b"</body></html>"
        self.use({"https://www.brightwall.io": (parked, "text/html"),              # titled with its own domain: parked
                  "https://www.brightwall.com": (real, "text/html"),
                  "https://www.brightwall.com/favicon.ico": (ico([32, 64]), "image/x-icon")})
        companies.create_company(name="Brightwall", job_url="https://www.linkedin.com/jobs/view/42")  # a job board: ignored
        r = registry.run("company.find_logo", {"company": "Brightwall"})
        self.assertEqual(r["website_found"], "https://www.brightwall.com")
        self.assertEqual(companies.find_by_name("Brightwall").website, "https://www.brightwall.com")
        self.assertEqual(r["logo_file"], "Brightwall/logo.png")

    def test_guess_uses_the_posting_or_email_site_and_refuses_parked_pages(self):
        from types import SimpleNamespace as NS
        page = b"<html><title>Quorra - Exposure management</title>" + b"y" * 3000 + b"</html>"
        fake = self.use({"https://careers.quorra.io": (page, "text/html")})
        c = NS(name="Quorra", job_url="https://careers.quorra.io/jobs/123", contact=NS(email=""))
        self.assertEqual(logos.guess_website(c), "https://careers.quorra.io")
        self.assertEqual(fake.calls[0], "https://careers.quorra.io")  # the posting's own site is tried first
        self.use({"https://www.acme.com": (b"<title>Acme</title><p>This domain is for sale</p>" + b"z" * 3000, "text/html")})
        self.assertIsNone(logos.guess_website(NS(name="Acme", job_url="", contact=NS(email="me@gmail.com"))))
        with self.assertRaises(ValueError):  # nothing found: a clear message
            companies.create_company(name="Acme")
            registry.run("company.find_logo", {"company": "Acme"})

    def test_links_with_spaces_still_work(self):
        from jobtracker.services import assistant
        c = companies.create_company(name="Quorra")
        (users.output_dir() / "Quorra").mkdir(parents=True, exist_ok=True)
        (users.output_dir() / "Quorra/logo.png").write_bytes(png(32))
        fixed = assistant.fix_links(f"Logo: [logo]( /files/Quorra/logo.png ) · [Quorra]( #/company/{c.id} )")
        self.assertEqual(fixed, f"Logo: [logo](/files/Quorra/logo.png) · [Quorra](#/company/{c.id})")


if __name__ == "__main__":
    unittest.main()
