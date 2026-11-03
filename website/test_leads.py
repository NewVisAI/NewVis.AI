"""End-to-end checks that a "Request a callback" submission really lands in the database.

Run from the repo root:  python -m unittest website.test_leads
Each test gets a fresh SQLite file, so nothing touches real leads.
"""
import importlib
import os
import sqlite3
import tempfile
import threading
import unittest

from fastapi.testclient import TestClient

VALID = {
    "name": "Asha Rao", "email": "asha@school.in", "phone": "+91 98765 43210",
    "org": "Green Valley School", "use_case": "School / campus",
    "message": "About 200 cameras across two blocks.", "website": "",
}


class LeadsDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # A nested path that does not exist yet: the server must create it.
        self.db = os.path.join(self.tmp.name, "nested", "leads.db")
        os.environ.update({"SITE_DB_PATH": self.db, "LEADS_ADMIN_TOKEN": "t0ken", "LEADS_PER_HOUR": "5"})
        for k in ("SMTP_HOST", "LEADS_NOTIFY_TO", "TRUST_PROXY"):
            os.environ.pop(k, None)
        import website.server as server
        self.server = importlib.reload(server)  # re-read env-derived settings
        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM leads ORDER BY id")]
        finally:
            conn.close()

    def post(self, data):
        return self.client.post("/api/leads", json=data, headers={"user-agent": "test-browser"})

    # --- the happy path ---------------------------------------------------
    def test_valid_submission_is_stored_with_every_field(self):
        r = self.post(VALID)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"ok": True})
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        for k in ("name", "email", "phone", "org", "use_case", "message"):
            self.assertEqual(row[k], VALID[k], k)
        self.assertEqual(row["user_agent"], "test-browser")
        self.assertTrue(row["created_at"].startswith("20"))
        self.assertTrue(row["source_ip"])

    def test_whitespace_trimmed_and_long_fields_capped(self):
        data = dict(VALID, name="  Asha  ", message="x" * 1500)
        self.assertEqual(self.post(data).status_code, 200)
        row = self.rows()[0]
        self.assertEqual(row["name"], "Asha")
        self.assertEqual(len(row["message"]), 1000)

    def test_lead_survives_a_server_restart(self):
        self.post(VALID)
        importlib.reload(self.server)  # simulates a redeploy against the same file
        self.assertEqual(len(self.rows()), 1)

    def test_concurrent_submissions_are_all_stored(self):
        os.environ["LEADS_PER_HOUR"] = "1000"
        server = importlib.reload(self.server)
        client = TestClient(server.app)
        errors = []

        def submit(i):
            r = client.post("/api/leads", json=dict(VALID, name=f"Person {i}"))
            if r.status_code != 200:
                errors.append(r.status_code)

        threads = [threading.Thread(target=submit, args=(i,)) for i in range(25)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(sorted(r["name"] for r in self.rows()), sorted(f"Person {i}" for i in range(25)))

    # --- things that must NOT be stored -------------------------------------
    def test_honeypot_submissions_are_accepted_but_not_stored(self):
        r = self.post(dict(VALID, website="http://spam.example"))
        self.assertEqual(r.json(), {"ok": True})
        self.assertFalse(os.path.exists(self.db) and self.rows())

    def test_invalid_input_is_rejected_and_not_stored(self):
        for bad in (dict(VALID, email="not-an-email"), dict(VALID, phone="abc"),
                    dict(VALID, name=""), dict(VALID, use_case="")):
            self.assertEqual(self.post(bad).status_code, 422, bad)
        self.assertFalse(os.path.exists(self.db) and self.rows())

    def test_rate_limit_stops_flooding(self):
        codes = [self.post(dict(VALID, name=f"P{i}")).status_code for i in range(7)]
        self.assertEqual(codes[:5], [200] * 5)
        self.assertEqual(codes[5:], [429, 429])
        self.assertEqual(len(self.rows()), 5)

    # --- reading the call list -------------------------------------------
    def test_call_list_requires_the_admin_token(self):
        self.post(VALID)
        self.assertEqual(self.client.get("/api/leads").status_code, 401)
        self.assertEqual(self.client.get("/api/leads", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        r = self.client.get("/api/leads", headers={"Authorization": "Bearer t0ken"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 1)
        self.assertEqual(r.json()["leads"][0]["email"], VALID["email"])

    def test_csv_export_neutralises_spreadsheet_formulas(self):
        self.post(dict(VALID, org="=HYPERLINK(\"http://evil\",\"x\")"))
        r = self.client.get("/api/leads?format=csv", headers={"Authorization": "Bearer t0ken"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("'=HYPERLINK", r.text)
        self.assertIn("+91 98765 43210", r.text)  # phone numbers are left alone

    def test_call_list_disabled_without_a_token_configured(self):
        os.environ["LEADS_ADMIN_TOKEN"] = ""
        server = importlib.reload(self.server)
        self.assertEqual(TestClient(server.app).get("/api/leads").status_code, 404)


if __name__ == "__main__":
    unittest.main()
