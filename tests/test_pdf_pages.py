"""A PDF's pages as images, for the iPhone file viewer: page count, one PNG per page, and only the user's files."""
import unittest

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from jobtracker import config
from jobtracker.api import app
from tests.test_core import reset  # sets the temp data/output env vars first


class PdfPagesTests(unittest.TestCase):
    def setUp(self):
        reset()
        folder = config.OUTPUT_DIR / "Nimbus"
        folder.mkdir(parents=True, exist_ok=True)
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.add_blank_page(width=595, height=842)
        with open(folder / "Dana CV.pdf", "wb") as f:
            writer.write(f)
        (folder / "notes.txt").write_text("hello", encoding="utf-8")
        self.client = TestClient(app)

    def test_page_count_and_each_page_as_png(self):
        r = self.client.get("/api/file-pages/Nimbus/Dana%20CV.pdf")
        self.assertEqual(r.json(), {"pages": 2})
        r = self.client.get("/files/Nimbus/Dana%20CV.pdf?page=2&width=600")
        self.assertEqual(r.headers["content-type"], "image/png")
        self.assertTrue(r.content.startswith(b"\x89PNG"))
        self.assertEqual(self.client.get("/files/Nimbus/Dana%20CV.pdf?page=3").status_code, 404)

    def test_without_page_the_file_itself(self):
        r = self.client.get("/files/Nimbus/Dana%20CV.pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_only_pdfs_and_only_the_users_folder(self):
        self.assertEqual(self.client.get("/api/file-pages/Nimbus/notes.txt").status_code, 400)
        self.assertEqual(self.client.get("/api/file-pages/../secret.pdf").status_code, 404)
        self.assertEqual(self.client.get("/api/file-pages/Nimbus/missing.pdf").status_code, 404)


if __name__ == "__main__":
    unittest.main()
