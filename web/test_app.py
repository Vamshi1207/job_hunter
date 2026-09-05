"""Desk HTTP tests. No LLM, no Camoufox, no network to job boards."""

from __future__ import annotations

import json
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
        self.assertIn("Job desk fill", res.text)
        self.assertIn("Answer this question", res.text)
        self.assertIn("Cmd+Shift+G", res.text)
        self.assertIn(">extension<", res.text)
        self.assertIn("board-tabs", res.text)
        self.assertIn('id="tab-applied"', res.text)
        self.assertIn("delete-dialog", res.text)
        self.assertIn("delete-keep", res.text)
        self.assertIn("resolve-apply", res.text)
        self.assertIn("Find apply links", res.text)

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
        self.assertIn("function askDelete", js)
        self.assertIn("keep=true", js)
        self.assertIn("/api/jobs/remember", js)
        self.assertIn("/api/apply/resolve", js)
        self.assertIn("Find apply links", (Path(__file__).resolve().parent / "static" / "index.html").read_text())
        self.assertIn("heldUntilRefresh", js)
        self.assertIn("applied-stamp", js)
        self.assertIn("applied-timer", js)
        self.assertIn("displayTab", js)
        self.assertIn("board-tab", js)
        css = (Path(__file__).resolve().parent / "static" / "app.css").read_text()
        self.assertIn("min-height: 16rem", css)
        self.assertIn("table-layout: fixed", css)
        self.assertIn("overflow-wrap: anywhere", css)
        self.assertIn(".edit-files", css)
        self.assertIn("flex-direction: column", css)
        self.assertIn(".applied-stamp", css)
        self.assertIn(".applied-timer", css)
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
        self.assertIn("/extension", body["apply_helper"]["extension_path"].replace("\\", "/"))

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

    def test_delete_package_can_keep_or_drop_jobs_yaml_row(self):
        import yaml

        folder = self._package()
        (self.root / "jobs.yaml").write_text(
            "jobs:\n"
            "  - company: Acme\n"
            "    role: Software Engineer\n"
            "    url: https://example.com/job\n"
            "    jd: Python\n"
            "  - company: Other\n"
            "    role: Engineer\n"
            "    jd: Still open\n"
        )
        kept = self.client.delete(f"/api/packages/{folder.name}?keep=true")
        self.assertEqual(kept.status_code, 200)
        self.assertTrue(kept.json()["keep"])
        queue = yaml.safe_load((self.root / "jobs.yaml").read_text())["jobs"]
        self.assertEqual({row["company"] for row in queue}, {"Acme", "Other"})
        self.assertFalse(folder.exists())

        folder = self._package()
        dropped = self.client.delete(f"/api/packages/{folder.name}")
        self.assertEqual(dropped.status_code, 200)
        self.assertFalse(dropped.json()["keep"])
        queue = yaml.safe_load((self.root / "jobs.yaml").read_text())["jobs"]
        self.assertEqual([row["company"] for row in queue], ["Other"])

    def test_mark_package_applied(self):
        folder = self._package()
        (self.root / "jobs.yaml").write_text(
            "jobs:\n"
            "  - company: Acme\n"
            "    role: Software Engineer\n"
            "    url: https://example.com/job\n"
            "    jd: Python\n"
        )
        listed = self.client.get("/api/packages")
        self.assertFalse(listed.json()["packages"][0]["applied"])
        marked = self.client.post(f"/api/packages/{folder.name}/applied", json={"applied": True})
        self.assertEqual(marked.status_code, 200)
        self.assertTrue(marked.json()["applied"])
        self.assertTrue(marked.json()["applied_at"])
        import yaml

        queue = yaml.safe_load((self.root / "jobs.yaml").read_text()) or {}
        applied = yaml.safe_load((self.root / "applied.yaml").read_text()) or {}
        self.assertEqual(queue.get("jobs") or [], [])
        self.assertEqual(applied["jobs"][0]["company"], "Acme")
        undone = self.client.post(f"/api/packages/{folder.name}/applied", json={"applied": False})
        self.assertFalse(undone.json()["applied"])
        queue = yaml.safe_load((self.root / "jobs.yaml").read_text()) or {}
        applied = yaml.safe_load((self.root / "applied.yaml").read_text()) or {}
        self.assertEqual(queue["jobs"][0]["company"], "Acme")
        self.assertEqual(applied.get("jobs") or [], [])

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

    def test_apply_for_page_returns_matching_package_and_cv_url(self):
        folder = self._package()
        (folder / "job.json").write_text(
            '{"company": "Acme", "role": "Software Engineer",'
            ' "url": "https://job-boards.greenhouse.io/acme/jobs/11111",'
            ' "apply_url": "https://job-boards.greenhouse.io/acme/jobs/11111",'
            ' "apply_kind": "ats"}'
        )
        res = self.client.get(
            "/api/apply/for-page",
            params={"url": "https://job-boards.greenhouse.io/acme/jobs/11111?gh_src=x"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["package_id"], folder.name)
        self.assertIn("Desk_Tester_CV.pdf", body["files"]["resume"]["url"])
        miss = self.client.get(
            "/api/apply/for-page",
            params={"url": "https://jobs.ashbyhq.com/nope/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
        )
        self.assertEqual(miss.status_code, 404)

    def test_apply_answer_returns_llm_answers_for_package(self):
        from unittest.mock import patch

        folder = self._package()
        (folder / "job.json").write_text(
            '{"company": "Acme", "role": "Software Engineer",'
            ' "url": "https://job-boards.greenhouse.io/acme/jobs/11111",'
            ' "apply_url": "https://job-boards.greenhouse.io/acme/jobs/11111",'
            ' "apply_kind": "ats"}'
        )
        with patch(
            "pipeline.llm.complete_prompt",
            return_value='[{"key":"q0","value":"About 70 percent coding","skip":false}]',
        ) as mock_llm:
            res = self.client.post(
                "/api/apply/answer",
                json={
                    "url": "https://job-boards.greenhouse.io/acme/jobs/11111",
                    "package_id": folder.name,
                    "questions": [
                        {
                            "key": "q0",
                            "label": "What percentage of time do you generally enjoy spending coding?",
                            "kind": "text",
                        }
                    ],
                },
            )
            self.assertEqual(res.status_code, 200)
            body = res.json()
            self.assertEqual(body["package_id"], folder.name)
            self.assertEqual(body["answers"][0]["value"], "About 70 percent coding")
            self.assertTrue(body["never_submit"])
            self.assertEqual(mock_llm.call_count, 1)

            # Answers cache file was created
            cache_file = folder / "answers_cache.json"
            self.assertTrue(cache_file.exists())

            # Second call (e.g. page refresh) retrieves from cache with 0 additional LLM calls
            res2 = self.client.post(
                "/api/apply/answer",
                json={
                    "url": "https://job-boards.greenhouse.io/acme/jobs/11111",
                    "package_id": folder.name,
                    "questions": [
                        {
                            "key": "q0_again",
                            "label": "What percentage of time do you generally enjoy spending coding? *",
                            "kind": "text",
                        }
                    ],
                },
            )
            self.assertEqual(res2.status_code, 200)
            body2 = res2.json()
            self.assertEqual(body2["answers"][0]["value"], "About 70 percent coding")
            self.assertTrue(body2.get("stats", {}).get("from_cache"))
            self.assertEqual(mock_llm.call_count, 1)

            # Marking the package applied clears the answers cache
            mark_res = self.client.post(f"/api/packages/{folder.name}/applied", json={"applied": True})
            self.assertEqual(mark_res.status_code, 200)
            self.assertFalse(cache_file.exists())

            # Verify questions mentioning 1Password are not skipped by the password filter
            res_1pwd = self.client.post(
                "/api/apply/answer",
                json={
                    "url": "https://job-boards.greenhouse.io/acme/jobs/11111",
                    "package_id": folder.name,
                    "questions": [
                        {
                            "key": "q_1pwd",
                            "label": "Why 1Password? There are a lot of great companies out there. What makes you excited to work at 1Password?",
                            "kind": "text",
                        }
                    ],
                },
            )
            self.assertEqual(res_1pwd.status_code, 200)
            self.assertEqual(res_1pwd.json()["answers"][0]["key"], "q_1pwd")
            self.assertEqual(mock_llm.call_count, 2)

    def test_extension_lives_at_repo_extension_dir(self):
        root = Path(__file__).resolve().parents[1]
        manifest = (root / "extension" / "manifest.json").read_text()
        self.assertIn("Fill this form", manifest)
        self.assertIn("contextMenus", manifest)
        self.assertIn('"all_frames": true', manifest)
        fill_js = (root / "extension" / "fill.js").read_text()
        self.assertIn("jobDeskAnswerSelected", fill_js)
        self.assertIn("queryAllDeep", fill_js)
        self.assertIn("offerResumeFallback", fill_js)
        self.assertNotIn("answerCustom", fill_js)
        self.assertIn("background.js", manifest)
        self.assertTrue((root / "extension" / "fill.js").exists())
        self.assertNotIn("web/apply-helper", (root / "web" / "static" / "app.js").read_text())

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

    def test_apply_launch_opens_linkedin_for_easy_apply(self):
        from unittest.mock import AsyncMock

        from pipeline.apply_url import ApplyTarget

        folder = self._package()
        posting = "https://www.linkedin.com/jobs/view/123"
        (folder / "job.json").write_text(
            json.dumps(
                {
                    "company": "Acme",
                    "role": "Software Engineer",
                    "url": posting,
                }
            )
        )
        with patch(
            "pipeline.apply_url.resolve_apply_from_web",
            return_value=ApplyTarget(posting, "aggregator", "web"),
        ), patch(
            "pipeline.browser_hunt.resolve_apply_in_browser",
            new_callable=AsyncMock,
            return_value=ApplyTarget(posting, "easy_apply", "camoufox"),
        ), patch("web.app._browser_busy", return_value=False):
            res = self.client.post("/api/apply/launch", json={"package_id": folder.name})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["apply_kind"], "easy_apply")
        self.assertIn("linkedin.com", body["apply_url"])
        meta = json.loads((folder / "job.json").read_text())
        self.assertEqual(meta["apply_kind"], "easy_apply")

    def test_apply_resolve_starts_run_for_unresolved_linkedin(self):
        folder = self._package()
        (folder / "job.json").write_text(
            '{"company": "Acme", "role": "Software Engineer",'
            ' "url": "https://www.linkedin.com/jobs/view/123",'
            ' "apply_url": "https://www.linkedin.com/jobs/view/123",'
            ' "apply_kind": "aggregator"}'
        )
        with patch("web.app.threading.Thread") as thread_cls:
            mock_thread = thread_cls.return_value
            mock_thread.ident = 21
            mock_thread.is_alive.return_value = True
            res = self.client.post("/api/apply/resolve")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["id"])
        self.assertEqual(body["count"], 1)

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

    def test_delete_job_records_in_deleted_yaml(self):
        res = self.client.post(
            "/api/jobs/delete",
            json={
                "company": "DiscardCo",
                "role": "Software Engineer",
                "url": "https://www.linkedin.com/jobs/view/9998887776",
            },
        )
        self.assertEqual(res.status_code, 200)
        from pipeline.config import load_config
        from pipeline.jobs import deleted_linkedin_ids

        self.assertIn("9998887776", deleted_linkedin_ids(load_config()))


if __name__ == "__main__":
    unittest.main()
