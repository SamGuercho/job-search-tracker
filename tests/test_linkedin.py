"""LinkedIn data export: import, contacts per application, profile vs master CV."""
import io
import unittest
import zipfile

from jobtracker import registry
from jobtracker.services import companies, linkedin, master_cv, rules
from tests.test_core import reset  # sets the temp data/output env vars first

CONNECTIONS = """Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Dana,Levi,https://www.linkedin.com/in/dana-levi,,Northwind Labs Ltd.,Data Engineer,15 Mar 2024
Omer,Katz,https://www.linkedin.com/in/omer-katz,,Northwind Labs,Talent Acquisition Partner,02 Jan 2021
Noa,Bar,https://www.linkedin.com/in/noa-bar,,Metal Works,Analyst,10 Feb 2023
"""
PROFILE = """First Name,Last Name,Maiden Name,Address,Birth Date,Headline,Summary,Industry
Alex,Morgan,,,,Senior Data Analyst | Product analytics,I turn data into decisions.,Software
"""
POSITIONS = """Company Name,Title,Description,Location,Started On,Finished On
Acme Corp,Senior Data Analyst,Analytics stack,Paris,Mar 2022,
Globex,Data Scientist,,Paris,Jan 2018,Dec 2021
Hooli,Barista,,Paris,2015,2016
"""
EDUCATION = """School Name,Start Date,End Date,Notes,Degree Name,Activities
Université Paris-Saclay,2016,2018,,MSc Data Science,
"""
SKILLS = "Name\nSQL\nPython\nUnderwater Basket Weaving\n"


def export(**files) -> io.BytesIO:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        for name, text in files.items():
            z.writestr(f"Basic_LinkedInDataExport/{name}.csv", text)
        z.writestr("messages.csv", "never read")
    data.seek(0)
    return data


class ImportTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_zip_keeps_contacts_and_profile(self):
        r = linkedin.import_export("export.zip", export(Connections=CONNECTIONS, Profile=PROFILE, Positions=POSITIONS))
        self.assertEqual(r["contacts"], 3)
        self.assertTrue(r["profile"])
        self.assertNotIn("messages.csv", r["imported"])
        dana = linkedin.store.load().contacts[0]
        self.assertEqual((dana.name, dana.connected_on), ("Dana Levi", "2024-03-15"))
        self.assertEqual(linkedin.get_profile().positions[0].start, "2022-03")

    def test_reimport_replaces_only_what_it_contains(self):
        linkedin.import_export("export.zip", export(Connections=CONNECTIONS, Profile=PROFILE))
        linkedin.import_export("Connections.csv", io.BytesIO(CONNECTIONS.splitlines(True)[0].encode()
                                                              + b"\nFirst Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
                                                              + b"Yael,Ron,,,Initech,PM,01 May 2025\n"))
        self.assertEqual([c.name for c in linkedin.store.load().contacts], ["Yael Ron"])
        self.assertIsNotNone(linkedin.get_profile())  # kept

    def test_not_an_export(self):
        with self.assertRaises(ValueError):
            linkedin.import_export("x.zip", io.BytesIO(b"not a zip"))
        with self.assertRaises(ValueError):
            linkedin.import_export("x.zip", export(Other="a,b\n1,2"))


class ContactsTests(unittest.TestCase):
    def setUp(self):
        reset()
        linkedin.import_export("export.zip", export(Connections=CONNECTIONS))
        self.c = companies.create_company(name="Northwind", position="Senior Data Engineer")

    def test_company_names_match_loosely_but_not_prefixes(self):
        self.assertTrue(linkedin.same_company("Northwind", "Northwind Labs Ltd."))
        self.assertFalse(linkedin.same_company("Meta", "Metal Works"))

    def test_recruiters_first_then_close_role(self):
        r = registry.run("linkedin.contacts", {"company": "Northwind"})
        self.assertEqual([p["name"] for p in r["contacts"]], ["Omer Katz", "Dana Levi"])
        self.assertTrue(r["contacts"][1]["close_role"])
        self.assertIn("network=%5B%22S%22%5D", r["search"]["second_degree"])

    def test_today_shows_contacts_until_a_referee_is_set(self):
        item = rules.to_apply([companies.get_company(self.c.id)])[0]
        self.assertEqual(item["contacts_count"], 2)
        registry.run("company.referral", {"company": self.c.id, "referee": "Dana Levi", "asked": False})
        self.assertEqual(rules.to_apply([companies.get_company(self.c.id)])[0]["contacts_count"], 0)

    def test_find(self):
        self.assertEqual([p["name"] for p in registry.run("linkedin.find", {"query": "talent northwind"})], ["Omer Katz"])


class CheckTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_differences_with_master_cv(self):
        linkedin.import_export("export.zip", export(Profile=PROFILE, Positions=POSITIONS, Education=EDUCATION, Skills=SKILLS))
        issues = registry.run("linkedin.check")["issues"]
        text = lambda kind: [i for i in issues if i["kind"] == kind]
        cv = master_cv.get_master()
        globex = next(e for e in cv.experiences if e.company == "Globex")
        self.assertTrue(any("Globex" in i["message"] for i in text("title")))      # Data Analyst vs Data Scientist
        self.assertTrue(any("Globex" in i["message"] for i in text("dates")))      # 2019 vs 2018
        self.assertFalse(any("Acme" in i["message"] for i in text("dates")))       # 2022 == Mar 2022
        self.assertTrue(any("Initech" in i["message"] and i["severity"] == "fix" for i in text("experience")))
        self.assertTrue(any("Hooli" in i["message"] and i["severity"] == "info" for i in text("experience")))
        self.assertTrue(any("Lyon" in i["message"] for i in text("education")))
        missing = next(i for i in text("skills") if i["severity"] == "fix")["cv"]
        self.assertIn("dbt", missing)
        self.assertNotIn("Python", missing)
        self.assertEqual(globex.role, "Data Analyst")

    def test_needs_a_profile(self):
        with self.assertRaises(ValueError):
            registry.run("linkedin.check")


if __name__ == "__main__":
    unittest.main()
