"""Desk HTTP tests. No LLM, no Camoufox, no network to job boards."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from pipeline.config import load_config


class DeskAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "applications").mkdir()
        example = Path(__file__).resolve().parents[1] / "config.example.yaml"
        if example.exists():
            (self.root / "config.example.yaml").write_text(example.read_text())
        (self.root / "config.yaml").write_text(
            "user:\n"
            "  full_name: Desk Tester\n"
            "  city: Montreal\n"
            "  country: Canada\n"
            "career:\n"
            "  years_experience: 6\n"
            "  target_markets:\n"
            "    - Canada\n"
            "  target_roles:\n"
            "    - Software Engineer\n"
            "hunt:\n"
            "  max_jobs: 0\n"
            "  reject_skills:\n"
            "    - java\n"
        )
        os.environ["JOB_SEARCH_ROOT"] = str(self.root)
        os.environ["CAMOUFOX_VNC_URL"] = "http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale"
        load_config(force=True)
        from web.app import _run_lock, _runs, app

        with _run_lock:
            _runs.clear()
        self.client = TestClient(app)
        self._runs = _runs
        self._run_lock = _run_lock

    def tearDown(self):
        with self._run_lock:
            self._runs.clear()
        os.environ.pop("JOB_SEARCH_ROOT", None)
        os.environ.pop("CAMOUFOX_VNC_URL", None)
        self.tmp.cleanup()
        load_config(force=True)

    def _package(self, name: str = "Acme-Software-Engineer-2026-08-30") -> Path:
        folder = self.root / "applications" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "job.json").write_text(
            '{"company": "Acme", "role": "Software Engineer", "url": "https://example.com/job"}'
        )
        (folder / "Desk_Tester_CV.pdf").write_text("pdf")
        (folder / "cover_letter.md").write_text("Hello Acme")
        (folder / "Desk_Tester_CV_changes.md").write_text("**Score:** 85\n**Honesty:** 90\n")
        return folder

    def test_index_includes_camoufox_panel_and_stop(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("camoufox-panel", res.text)
        self.assertIn('id="stop"', res.text)
        self.assertIn("Hunt from profile", res.text)
        self.assertIn("board-search", res.text)
        self.assertIn("Job name", res.text)
        self.assertIn("Location", res.text)
        self.assertIn("Mode", res.text)
        self.assertIn("ATS", res.text)
        self.assertIn("Apply", res.text)
        self.assertIn(">Delete<", res.text)
        self.assertIn("th-sort", res.text)
        self.assertNotIn('placeholder="Filter"', res.text)
        self.assertIn("col-role", res.text)
        self.assertIn("apply-helper-wrap", res.text)
        self.assertIn("board-tabs", res.text)
        self.assertIn('id="tab-applied"', res.text)

    def test_static_js_reconnects_and_opens_camoufox(self):
        js = (Path(__file__).resolve().parent / "static" / "app.js").read_text()
        self.assertIn("function showCamoufox", js)
        self.assertIn("function applyCamoufoxStage", js)
        self.assertIn("function workModeLabel", js)
        self.assertIn("/api/runs/active", js)
        self.assertIn("function applyCell", js)
        self.assertIn("/api/apply/launch", js)
        self.assertIn("isAggregatorHost", js)
        self.assertIn("edit-files", js)
        self.assertIn("Finding form", js)
        self.assertIn("Ready to apply", js)
        self.assertIn("board-search", js)
        self.assertIn("/api/packages/", js)
        self.assertIn("/applied", js)
        self.assertIn("heldUntilRefresh", js)
        self.assertIn("applied-stamp", js)
        self.assertIn("displayTab", js)
        self.assertIn("board-tab", js)
        css = (Path(__file__).resolve().parent / "static" / "app.css").read_text()
        self.assertIn("min-height: 16rem", css)
        self.assertIn("table-layout: fixed", css)
        self.assertIn("overflow-wrap: anywhere", css)
        self.assertIn(".edit-files", css)
        self.assertIn("flex-direction: column", css)
        self.assertIn(".applied-stamp", css)
        self.assertIn(".board-tabs", css)
        self.assertIn("data.browser", js)
        self.assertNotIn('showCamoufox(mode !== "stopping")', js)

    def test_me_exposes_profile_hunt_and_vnc_url(self):
        res = self.client.get("/api/me")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["name"], "Desk Tester")
        self.assertEqual(body["city"], "Montreal")
        self.assertIn("Software Engineer", body["hunt"]["roles"])
        self.assertIn(body["hunt"]["max_jobs"], (0, None))
        self.assertIn("java", body["hunt"]["reject_skills"])
        self.assertIn("Canada", body["hunt"]["search_locations"])
        self.assertEqual(body["hunt"]["preferred_city"], "Montreal")
        self.assertGreaterEqual(body["hunt"]["login_wait_seconds"], 120)
        self.assertIn("6080", body["camoufox"]["vnc"])
        self.assertIn("apply-helper", body["apply_helper"]["extension_path"])

    def test_inspect_linkedin_is_blocked_and_uses_pasted_jd(self):
        res = self.client.post(
            "/api/inspect",
            json={
                "url": "https://www.linkedin.com/jobs/view/123",
                "jd": "Python Kafka engineer in Montreal",
            },
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["blocked"])
        self.assertFalse(body["needs_jd"])
        self.assertIn("Python", body["jd"])

    def test_packages_list_detail_file_and_delete(self):
        folder = self._package()
        listed = self.client.get("/api/packages")
        self.assertEqual(listed.status_code, 200)
        ids = [row["id"] for row in listed.json()["packages"]]
        self.assertIn(folder.name, ids)

        detail = self.client.get(f"/api/packages/{folder.name}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["company"], "Acme")
        self.assertEqual(detail.json()["role"], "Software Engineer")

        pdf = self.client.get(f"/api/packages/{folder.name}/file/Desk_Tester_CV.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertIn("pdf", (pdf.headers.get("content-type") or "").lower())

        missing_file = self.client.get(f"/api/packages/{folder.name}/file/nope.txt")
        self.assertEqual(missing_file.status_code, 404)

        deleted = self.client.delete(f"/api/packages/{folder.name}")
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(folder.exists())
        self.assertEqual(self.client.get(f"/api/packages/{folder.name}").status_code, 404)

    def test_mark_package_applied(self):
        folder = self._package()
        listed = self.client.get("/api/packages")
        self.assertFalse(listed.json()["packages"][0]["applied"])
        marked = self.client.post(f"/api/packages/{folder.name}/applied", json={"applied": True})
        self.assertEqual(marked.status_code, 200)
        self.assertTrue(marked.json()["applied"])
        self.assertTrue(marked.json()["applied_at"])
        undone = self.client.post(f"/api/packages/{folder.name}/applied", json={"applied": False})
        self.assertFalse(undone.json()["applied"])

    def test_apply_launch_uses_stored_form_url_and_stores_pending_fill(self):
        folder = self._package()
        (folder / "job.json").write_text(
            '{"company": "Acme", "role": "Software Engineer",'
            ' "url": "https://www.linkedin.com/jobs/view/123",'
            ' "apply_url": "https://boards.greenhouse.io/acme/jobs/1",'
            ' "apply_kind": "ats"}'
        )
        res = self.client.post("/api/apply/launch", json={"package_id": folder.name})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("greenhouse.io", body["apply_url"])
        self.assertTrue(body["fields"]["first_name"])
        self.assertTrue(body["fields"]["email"])
        self.assertTrue(body["never_submit"])
        pending = self.client.get("/api/apply/pending")
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json()["package_id"], folder.name)
        filled = self.client.get(f"/api/packages/{folder.name}/fill")
        self.assertEqual(filled.status_code, 200)
        self.assertIn("email", filled.json()["fields"])
        consumed = self.client.post("/api/apply/consumed")
        self.assertEqual(consumed.status_code, 200)
        empty = self.client.get("/api/apply/pending").json()
        self.assertIsNone(empty.get("payload"))

    def test_apply_launch_unwraps_linkedin_to_company_form(self):
        from unittest.mock import AsyncMock

        from pipeline.apply_url import ApplyTarget

        folder = self._package()
        (folder / "job.json").write_text(
            '{"company": "Acme", "role": "Software Engineer",'
            ' "url": "https://www.linkedin.com/jobs/view/123"}'
        )
        with patch(
            "pipeline.apply_url.resolve_apply_from_web",
            return_value=ApplyTarget("https://www.linkedin.com/jobs/view/123", "aggregator", "web"),
        ), patch(
            "pipeline.browser_hunt.resolve_apply_in_browser",
            new_callable=AsyncMock,
            return_value=ApplyTarget("https://boards.greenhouse.io/acme/jobs/9", "ats", "camoufox"),
        ), patch("web.app._browser_busy", return_value=False):
            res = self.client.post("/api/apply/launch", json={"package_id": folder.name})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("greenhouse.io", body["apply_url"])
        self.assertNotIn("linkedin.com", body["apply_url"])

    def test_apply_launch_does_not_return_linkedin_listing(self):
        from unittest.mock import AsyncMock

        from pipeline.apply_url import ApplyTarget

        folder = self._package()
        (folder / "job.json").write_text(
            '{"company": "Acme", "role": "Software Engineer",'
            ' "url": "https://www.linkedin.com/jobs/view/123"}'
        )
        with patch(
            "pipeline.apply_url.resolve_apply_from_web",
            return_value=ApplyTarget("https://www.linkedin.com/jobs/view/123", "aggregator", "web"),
        ), patch(
            "pipeline.browser_hunt.resolve_apply_in_browser",
            new_callable=AsyncMock,
            return_value=ApplyTarget("", "unknown", ""),
        ), patch("web.app._browser_busy", return_value=False):
            res = self.client.post("/api/apply/launch", json={"package_id": folder.name})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["apply_url"], "")
        self.assertIn("linkedin.com", body["posting_url"])

    def test_rebuild_pdf_unknown_package(self):
        res = self.client.post("/api/packages/missing-package/rebuild-pdf")
        self.assertEqual(res.status_code, 404)

    def test_start_run_requires_urls(self):
        res = self.client.post("/api/runs", json={"urls": "", "jd": "Python"})
        self.assertEqual(res.status_code, 400)

    def test_active_run_empty_then_hunt_stop_and_stale_restart(self):
        empty = self.client.get("/api/runs/active")
        self.assertEqual(empty.status_code, 200)
        self.assertIsNone(empty.json()["id"])

        with patch("web.app.threading.Thread") as thread_cls, patch("web.app.hunt_limit", return_value=0):
            mock_thread = thread_cls.return_value
            mock_thread.ident = 11
            mock_thread.is_alive.return_value = True
            started = self.client.post("/api/hunt", json={})
            self.assertEqual(started.status_code, 200)
            run_id = started.json()["id"]
            self.assertTrue(run_id)

            active = self.client.get("/api/runs/active")
            self.assertEqual(active.json()["id"], run_id)
            self.assertEqual(active.json()["kind"], "hunt")
            self.assertFalse(active.json().get("browser"))

            status = self.client.get(f"/api/runs/{run_id}")
            self.assertEqual(status.json()["status"], "running")

            second = self.client.post("/api/hunt", json={})
            self.assertEqual(second.status_code, 409)

            stopped = self.client.post(f"/api/runs/{run_id}/stop")
            self.assertEqual(stopped.status_code, 200)
            self.assertEqual(stopped.json()["status"], "stopping")

            mock_thread.is_alive.return_value = False
            again = self.client.post("/api/hunt", json={})
            self.assertEqual(again.status_code, 200)
            self.assertNotEqual(again.json()["id"], run_id)

        missing = self.client.get("/api/runs/no-such-run")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(self.client.post("/api/runs/no-such-run/stop").status_code, 404)

    def test_start_run_is_mocked_and_returns_count(self):
        with patch("web.app.threading.Thread") as thread_cls:
            thread_cls.return_value.ident = None
            thread_cls.return_value.is_alive.return_value = False
            res = self.client.post(
                "/api/runs",
                json={
                    "urls": "https://boards.greenhouse.io/acme/jobs/1\nhttps://jobs.lever.co/acme/2",
                    "jd": "",
                },
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["count"], 2)
        self.assertTrue(res.json()["id"])
        thread_cls.return_value.start.assert_called()


if __name__ == "__main__":
    unittest.main()
