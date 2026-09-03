"""Unit tests for the pipeline (no LLM, no browser)."""

from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from pipeline.config import load_config
from pipeline.cv_format import (
    apply_style_tokens,
    reorder_sections,
    style_tokens,
    tailor_layout_instructions,
)
from pipeline.jobs import apply_pasted_job_text, fetch_jd, infer_company_role, listing_has_identity, load_jobs, parse_job_urls, parse_posting_meta, slug
from pipeline.playbook import render_playbook, visa_answers
from pipeline.reports import parse_evaluation
from pipeline.tailor import (
    _retry_section,
    build_critic_prompt,
    build_tailor_prompt,
    escape_html,
    format_phone,
    normalize_parsed,
    parse_tagged_output,
    pdf_scale,
    resume_plain_text,
    strip_skill_prefix,
    strip_title_dates,
)


class SlugTests(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(slug("Hootsuite"), "Hootsuite")
        self.assertEqual(slug("Lead, Data Development"), "Lead-Data-Development")
        self.assertEqual(slug("@@@"), "job")

    def test_infer_from_greenhouse_and_jd_line(self):
        company, role = infer_company_role(
            "https://boards.greenhouse.io/cohere/jobs/123",
            "Forward Deployed Engineer at Cohere (Ottawa)",
        )
        self.assertEqual(company, "Cohere")
        self.assertIn("Forward Deployed", role)

    def test_parse_job_urls_and_linkedin_company_from_html(self):
        from pipeline.browser_hunt import _company_from_host
        from pipeline.jobs import company_role_from_title, is_placeholder_company

        urls = parse_job_urls(
            "https://www.linkedin.com/jobs/view/123\n"
            "https://boards.greenhouse.io/acme/jobs/9\n"
            "not a url\n"
            "www.linkedin.com/jobs/view/456\n"
        )
        self.assertEqual(len(urls), 3)
        self.assertTrue(urls[1].endswith("/jobs/9"))
        html = """
        <title>Software Engineer | Northstar | LinkedIn</title>
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Software Engineer",
         "hiringOrganization":{"name":"Northstar"},
         "description":"<p>Python Kafka</p>"}
        </script>
        """
        meta = parse_posting_meta(html, "https://www.linkedin.com/jobs/view/123")
        self.assertEqual(meta["company"], "Northstar")
        self.assertEqual(meta["role"], "Software Engineer")
        self.assertIn("Python", meta["jd"])
        self.assertEqual(company_role_from_title("Forward Deployed Engineer | Cohere | LinkedIn")[0], "Cohere")
        self.assertTrue(is_placeholder_company("linkedin.com"))
        self.assertTrue(is_placeholder_company("www.linkedin.com"))
        self.assertEqual(_company_from_host("https://www.linkedin.com/jobs/view/123"), "")
        self.assertEqual(_company_from_host("https://boards.greenhouse.io/cohere/jobs/1"), "Cohere")

    def test_pasted_jd_overrides_scraped_text(self):
        listings = apply_pasted_job_text(
            [{"company": "linkedin.com", "role": "Role", "url": "https://www.linkedin.com/jobs/view/1", "jd": "scraped stub"}],
            ["https://www.linkedin.com/jobs/view/1"],
            "Software Engineer at Northstar\n\nBuild Python Kafka services in Montreal.",
        )
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0]["company"], "Northstar")
        self.assertIn("Software Engineer", listings[0]["role"])
        self.assertIn("Python Kafka", listings[0]["jd"])
        self.assertNotIn("scraped stub", listings[0]["jd"])
        self.assertTrue(listing_has_identity(listings[0]))
        empty = apply_pasted_job_text([], ["https://boards.greenhouse.io/acme/jobs/9"], "Staff Platform Engineer at Acme")
        self.assertEqual(empty[0]["company"], "Acme")
        self.assertIn("Staff Platform", empty[0]["role"])


class EvalParseTests(unittest.TestCase):
    def test_parse_evaluation(self):
        md = """
# Evaluation
**Score:** 80/100
**Honesty:** 100/100
**Keyword coverage:** 72/100
**Critique:** Reorder skills.

**Honest gaps:**
- No RAG
"""
        data = parse_evaluation(md)
        self.assertEqual(data["score"], 80)
        self.assertEqual(data["honesty"], 100)
        self.assertEqual(data["keyword_coverage"], 72)
        self.assertIn("Reorder", data["critique"])
        self.assertIn("No RAG", data["gaps"])


class ParseTests(unittest.TestCase):
    def test_parse_and_plain_text(self):
        from pipeline.tailor import job_blocks

        job = job_blocks()[0]
        prefix = job["prefix"]
        raw = f"""
<R_TITLE>match backend</R_TITLE>
<TITLE>Software Engineer - Distributed Systems</TITLE>
<SUMMARY>Built Kafka pipelines.</SUMMARY>
<{prefix}_TITLE>Software Engineer\tSeptember 2023 – Present</{prefix}_TITLE>
<{prefix}_B1>Shipped constraint engines in Python.</{prefix}_B1>
<SKILL_LANG>Programming languages: Python, SQL</SKILL_LANG>
<COVER_LETTER>Dear team</COVER_LETTER>
"""
        parsed = normalize_parsed(parse_tagged_output(raw))
        self.assertEqual(parsed["TITLE"], "Software Engineer - Distributed Systems")
        self.assertEqual(parsed[f"{prefix}_TITLE"], "Software Engineer")
        self.assertEqual(parsed["SKILL_LANG"], "Python, SQL")
        plain = resume_plain_text(parsed)
        self.assertIn("Kafka", plain)
        self.assertIn(job["employer"], plain)

    def test_job_blocks_dynamic_uses_min_max(self):
        from pipeline.config import Config
        from pipeline.tailor import job_blocks

        cfg = Config(
            {
                "cv_format": {"bullets": {"dynamic": True, "min": 3, "max": 8}},
                "experience": {
                    "jobs": [
                        {"prefix": "JOB1", "employer": "Alpha", "default_title": "SWE", "bullets": 7},
                        {"prefix": "JOB2", "employer": "Beta", "default_title": "DE", "bullets": 4},
                        {
                            "prefix": "JOB3",
                            "employer": "Gamma",
                            "default_title": "SWE",
                            "bullets_min": 2,
                            "bullets_max": 5,
                        },
                    ]
                },
            },
            Path("/tmp"),
        )
        jobs = job_blocks(cfg)
        self.assertTrue(jobs[0]["dynamic"])
        self.assertEqual(jobs[0]["bullets_min"], 3)
        self.assertEqual(jobs[0]["bullets_max"], 8)
        self.assertEqual(jobs[0]["bullets"], 8)
        self.assertEqual(jobs[1]["bullets_min"], 3)
        self.assertEqual(jobs[1]["bullets_max"], 8)
        self.assertEqual(jobs[2]["bullets_min"], 2)
        self.assertEqual(jobs[2]["bullets_max"], 5)

    def test_job_blocks_fixed_keeps_configured_count(self):
        from pipeline.config import Config
        from pipeline.tailor import job_blocks

        cfg = Config(
            {
                "cv_format": {"bullets": {"dynamic": False}},
                "experience": {
                    "jobs": [
                        {"prefix": "JOB1", "employer": "Alpha", "default_title": "SWE", "bullets": 7},
                        {"prefix": "JOB2", "employer": "Beta", "default_title": "DE", "bullets": 4},
                    ]
                },
            },
            Path("/tmp"),
        )
        jobs = job_blocks(cfg)
        self.assertFalse(jobs[0]["dynamic"])
        self.assertEqual(jobs[0]["bullets"], 7)
        self.assertEqual(jobs[0]["bullets_min"], 7)
        self.assertEqual(jobs[1]["bullets"], 4)

    def test_ensure_and_strip_bullet_placeholders(self):
        from pipeline.tailor import ensure_bullet_slots, strip_unused_bullet_placeholders

        html = (
            "<ul>\n"
            '        <li><span class="point">{{JOB1_B1}}</span></li>\n'
            '        <li><span class="point">{{JOB1_B2}}</span></li>\n'
            "</ul>"
        )
        padded = ensure_bullet_slots(
            html,
            [{"prefix": "JOB1", "bullets": 4, "bullets_min": 2, "bullets_max": 4}],
        )
        self.assertIn("{{JOB1_B4}}", padded)
        filled = padded.replace("{{JOB1_B1}}", "First").replace("{{JOB1_B2}}", "Second")
        stripped = strip_unused_bullet_placeholders(filled)
        self.assertIn("First", stripped)
        self.assertNotIn("{{JOB1_B3}}", stripped)
        self.assertNotIn("{{JOB1_B4}}", stripped)

    def test_apply_changes_pads_and_strips_unused_bullets(self):
        from pipeline.config import Config
        from pipeline.tailor import apply_changes_to_html

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "resumes").mkdir()
        (root / "resumes" / "template.html").write_text(
            "<html><body>\n"
            "<ul>\n"
            '        <li><span class="point">{{JOB1_B1}}</span></li>\n'
            '        <li><span class="point">{{JOB1_B2}}</span></li>\n'
            '        <li><span class="point">{{JOB1_B3}}</span></li>\n'
            "</ul>\n"
            "</body></html>\n"
        )
        cfg = Config(
            {
                "user": {"full_name": "Test User"},
                "cv_format": {"bullets": {"dynamic": True, "min": 2, "max": 5}},
                "experience": {
                    "jobs": [{"prefix": "JOB1", "employer": "Alpha", "default_title": "SWE"}]
                },
                "pipeline": {"html_template": "resumes/template.html"},
            },
            root,
        )
        parsed = {
            "TITLE": "Software Engineer",
            "SUMMARY": "Built systems.",
            "JOB1_TITLE": "Engineer",
            "JOB1_B1": "First",
            "JOB1_B2": "Second",
            "JOB1_B3": "Third",
            "JOB1_B4": "Fourth",
            "SKILL_LANG": "Python",
            "SKILL_ML": "NLP",
            "SKILL_DATA": "Kafka",
            "SKILL_BACKEND": "FastAPI",
            "SKILL_CLOUD": "AWS",
        }
        out = Path(tmp.name) / "out.html"
        apply_changes_to_html(parsed, out, cfg)
        text = out.read_text()
        self.assertIn("Fourth", text)
        self.assertNotIn("{{JOB1_B5}}", text)
        self.assertNotIn("{{JOB1_B1}}", text)
        tmp.cleanup()

    def test_last_complete_attempt_wins(self):
        raw = "<TITLE>first</TITLE> garbage <R_TITLE>r</R_TITLE><TITLE>second</TITLE>"
        parsed = parse_tagged_output(raw)
        self.assertEqual(parsed["TITLE"], "second")

    def test_escape_html(self):
        self.assertEqual(escape_html("A & B <C>"), "A &amp; B &lt;C&gt;")

    def test_strip_helpers(self):
        self.assertEqual(strip_skill_prefix("ML & AI: MCP, NLP", "ML & AI"), "MCP, NLP")
        self.assertEqual(strip_title_dates("Data Engineer    April 2022"), "Data Engineer")
        self.assertEqual(format_phone("5551234567"), "555-123-4567")
        self.assertEqual(format_phone("15551234567"), "555-123-4567")

    def test_two_page_pdf_is_not_shrunk(self):
        one_and_a_half = 11 * 96 * 1.5
        self.assertEqual(pdf_scale(one_and_a_half, max_pages=2), 1.0)
        self.assertEqual(pdf_scale(one_and_a_half * 2, max_pages=3), 1.0)
        self.assertLess(pdf_scale(one_and_a_half, max_pages=1), 1.0)


class HonestyPromptTests(unittest.TestCase):
    def test_critic_forbids_invention(self):
        prompt = build_critic_prompt("need PostGIS", "python kafka", "python kafka")
        lower = prompt.lower()
        self.assertIn("never tell the writer to invent", lower)
        self.assertNotIn("added/invented", lower)
        self.assertNotIn("invent new", lower)

    def test_retry_forbids_invention(self):
        section = _retry_section("score=40 missing PostGIS")
        self.assertIn("MUST NOT invent", section)
        self.assertNotIn("authorized to completely invent", section.lower())

    def test_tailor_prompt_includes_bank_and_rules(self):
        cfg = load_config(force=True)
        prompt = build_tailor_prompt(cfg, "Cohere", "FDE", "Need Python agents")
        self.assertIn("Experience bank", prompt)
        self.assertIn("MUST NOT invent", prompt)
        self.assertIn("Need Python agents", prompt)
        self.assertIn(f"Target length is {cfg.cv_pages}", prompt)
        self.assertIn("Keep these sections", prompt)

    def test_tailor_prompt_dynamic_bullet_counts(self):
        from pipeline.config import Config

        cfg = Config(
            {
                "user": {"full_name": "Test User"},
                "cv_format": {"pages": 2, "bullets": {"dynamic": True, "min": 3, "max": 8, "max_lines": 2}},
                "experience": {
                    "jobs": [
                        {"prefix": "JOB1", "employer": "Alpha", "default_title": "SWE"},
                        {"prefix": "JOB2", "employer": "Beta", "default_title": "DE"},
                    ]
                },
            },
            Path("/tmp"),
        )
        prompt = build_tailor_prompt(cfg, "Acme", "Engineer", "Need Kafka")
        self.assertIn("Bullet counts are dynamic", prompt)
        self.assertIn("Alpha 3–8", prompt)
        self.assertNotIn("Keep the exact bullet counts", prompt)
        self.assertIn("or empty if unused", prompt)

    def test_tailor_prompt_fixed_bullet_counts(self):
        from pipeline.config import Config

        cfg = Config(
            {
                "user": {"full_name": "Test User"},
                "cv_format": {"pages": 2, "bullets": {"dynamic": False, "max_lines": 2}},
                "experience": {
                    "jobs": [
                        {"prefix": "JOB1", "employer": "Alpha", "default_title": "SWE", "bullets": 7},
                        {"prefix": "JOB2", "employer": "Beta", "default_title": "DE", "bullets": 4},
                    ]
                },
            },
            Path("/tmp"),
        )
        prompt = build_tailor_prompt(cfg, "Acme", "Engineer", "Need Kafka")
        self.assertIn("Keep the exact bullet counts: Alpha 7, Beta 4.", prompt)


class JobsAndPlaybookTests(unittest.TestCase):
    def test_linkedin_is_not_fetched(self):
        self.assertIsNone(fetch_jd("https://www.linkedin.com/jobs/view/123"))

    def test_load_jobs_from_temp_yaml(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "config.yaml").write_text("user:\n  full_name: Test User\n")
        (root / "jobs.yaml").write_text(
            "jobs:\n"
            "  - company: Acme\n"
            "    role: Engineer\n"
            "    jd: Python Kafka\n"
            "  - company: SkipMe\n"
            "    role: Intern\n"
        )
        os.environ["JOB_SEARCH_ROOT"] = str(root)
        try:
            cfg = load_config(force=True)
            jobs = load_jobs(cfg)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["company"], "Acme")
            self.assertEqual(jobs[0]["jd"], "Python Kafka")
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_applied_jobs_move_out_of_jobs_yaml(self):
        from pipeline.jobs import append_job, applied_job_rows, queued_job_rows, set_job_applied, sync_applied_jobs

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "config.yaml").write_text("user:\n  full_name: Test User\n")
        apps = root / "applications" / "Acme-Engineer-2026-09-02"
        apps.mkdir(parents=True)
        (root / "jobs.yaml").write_text(
            "jobs:\n"
            "  - company: Acme\n"
            "    role: Engineer\n"
            "    url: https://example.com/job\n"
            "    jd: Python Kafka\n"
            "  - company: Open\n"
            "    role: Engineer\n"
            "    jd: Still open\n"
        )
        (apps / "job.json").write_text(
            '{"company": "Acme", "role": "Engineer", "url": "https://example.com/job", "applied": true, "applied_at": "2026-09-02"}'
        )
        os.environ["JOB_SEARCH_ROOT"] = str(root)
        try:
            cfg = load_config(force=True)
            sync_applied_jobs(cfg)
            queue = queued_job_rows(cfg)
            applied = applied_job_rows(cfg)
            self.assertEqual([job["company"] for job in queue], ["Open"])
            self.assertEqual(len(applied), 1)
            self.assertEqual(applied[0]["company"], "Acme")
            self.assertTrue(applied[0].get("applied"))
            append_job(cfg, {"company": "Acme", "role": "Engineer", "url": "https://example.com/job", "jd": "Python Kafka"})
            self.assertEqual([job["company"] for job in queued_job_rows(cfg)], ["Open"])
            set_job_applied(cfg, {"company": "Acme", "role": "Engineer", "url": "https://example.com/job"}, applied=False)
            self.assertEqual({job["company"] for job in queued_job_rows(cfg)}, {"Open", "Acme"})
            self.assertEqual(applied_job_rows(cfg), [])
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_cv_pages_comes_from_config(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "config.yaml").write_text("cv_format:\n  pages: 4\n")
        os.environ["JOB_SEARCH_ROOT"] = str(root)
        try:
            cfg = load_config(force=True)
            self.assertEqual(cfg.cv_pages, 4)
            self.assertEqual(cfg.cv_stem, "Candidate_CV")
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_playbook_uses_config_not_literals(self):
        cfg = load_config(force=True)
        visa = visa_answers(cfg)
        self.assertIn(visa["sponsorship_now"], {"Yes", "No"})
        text = render_playbook(
            cfg,
            {"company": "Acme", "role": "Eng", "url": "https://example.com"},
            Path("/tmp/out"),
            Path("/tmp/out/cv.pdf"),
            Path("/tmp/out/cover_letter.md"),
            Path("/tmp/out/why_i_fit.txt"),
        )
        self.assertIn(cfg.full_name, text)
        self.assertIn("click Submit", text)
        self.assertIn(str(cfg.get("user.email")), text)


class CvFormatTests(unittest.TestCase):
    def test_style_tokens_follow_yaml(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "config.yaml").write_text(
            "cv_format:\n"
            "  header_align: left\n"
            "  body_align: left\n"
            "  keep_together: []\n"
            "  hyphenate: false\n"
            "  color:\n"
            "    accent: '#ff0000'\n"
        )
        os.environ["JOB_SEARCH_ROOT"] = str(root)
        try:
            cfg = load_config(force=True)
            tokens = style_tokens(cfg)
            self.assertEqual(tokens["CV_HEADER_ALIGN"], "left")
            self.assertEqual(tokens["CV_CONTACT_JUSTIFY"], "flex-start")
            self.assertEqual(tokens["CV_BODY_ALIGN"], "left")
            self.assertEqual(tokens["CV_KEEP_SKILLS"], "auto")
            self.assertEqual(tokens["CV_KEEP_EDUCATION"], "auto")
            self.assertEqual(tokens["CV_HYPHENS"], "none")
            self.assertEqual(tokens["CV_COLOR_ACCENT"], "#ff0000")
            html = apply_style_tokens("align:{{CV_HEADER_ALIGN}} keep:{{CV_KEEP_SKILLS}}", cfg)
            self.assertEqual(html, "align:left keep:auto")
            self.assertIn("none (sections may split", tailor_layout_instructions(cfg))
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_section_order_from_config(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "config.yaml").write_text(
            "cv_format:\n"
            "  section_order:\n"
            "    - skills\n"
            "    - summary\n"
            "    - experience\n"
        )
        os.environ["JOB_SEARCH_ROOT"] = str(root)
        html = (
            "H"
            "<!--cv-section:summary-->S<!--/cv-section:summary-->"
            "<!--cv-section:experience-->E<!--/cv-section:experience-->"
            "<!--cv-section:skills-->K<!--/cv-section:skills-->"
            "T"
        )
        try:
            cfg = load_config(force=True)
            out = reorder_sections(html, cfg)
            self.assertLess(
                out.find("<!--cv-section:skills-->"),
                out.find("<!--cv-section:summary-->"),
            )
            self.assertLess(
                out.find("<!--cv-section:summary-->"),
                out.find("<!--cv-section:experience-->"),
            )
            self.assertTrue(out.startswith("H"))
            self.assertTrue(out.endswith("T"))
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)


class HuntTests(unittest.TestCase):
    def _cfg(self, root: Path, extra: str = ""):
        (root / "config.yaml").write_text(
            "user:\n"
            "  full_name: Test User\n"
            "  city: Montreal\n"
            "  country: Canada\n"
            "career:\n"
            "  stage: senior\n"
            "  years_experience: 6\n"
            "  target_markets:\n"
            "    - Canada\n"
            "  target_roles:\n"
            "    - Software Engineer\n"
            "    - Forward Deployed Engineer\n"
            "hunt:\n"
            "  max_jobs: 2\n"
            + extra
        )
        os.environ["JOB_SEARCH_ROOT"] = str(root)
        return load_config(force=True)

    def test_score_skips_linkedin_and_interns(self):
        from pipeline.search import score_listing

        tmp = tempfile.TemporaryDirectory()
        try:
            cfg = self._cfg(Path(tmp.name))
            self.assertEqual(
                score_listing(
                    {
                        "role": "Software Engineer",
                        "url": "https://www.linkedin.com/jobs/view/1",
                        "location": "Montreal, Canada",
                    },
                    cfg,
                ),
                0,
            )
            self.assertEqual(
                score_listing(
                    {
                        "role": "Software Engineer Intern",
                        "url": "https://boards.greenhouse.io/acme/jobs/1",
                        "location": "Montreal, Canada",
                    },
                    cfg,
                ),
                0,
            )
            self.assertGreater(
                score_listing(
                    {
                        "role": "Senior Software Engineer",
                        "url": "https://boards.greenhouse.io/acme/jobs/1",
                        "location": "Montreal, Canada",
                    },
                    cfg,
                ),
                0,
            )
            self.assertEqual(
                score_listing(
                    {
                        "role": "Software QA Engineer - Automation",
                        "url": "https://example.com/qa",
                        "location": "Canada",
                    },
                    cfg,
                ),
                0,
            )
            self.assertGreater(
                score_listing(
                    {
                        "role": "Software Engineer",
                        "url": "https://www.linkedin.com/jobs/view/12345678",
                        "location": "Montreal, Canada",
                        "jd": "Python Kafka distributed systems. 5 years of experience.",
                    },
                    cfg,
                ),
                0,
            )
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_score_skips_overlevel_and_stack_from_config(self):
        from pipeline.search import parse_required_years, score_listing

        self.assertEqual(parse_required_years("10-15 years of experience building platforms"), 10)
        self.assertEqual(parse_required_years("6+ years of experience"), 6)
        self.assertIsNone(parse_required_years("supporting 10000 users"))

        tmp = tempfile.TemporaryDirectory()
        extra = (
            "  exclude_levels:\n"
            "    - intern\n"
            "    - principal\n"
            "    - staff\n"
            "  reject_skills:\n"
            "    - java\n"
            "  preferred_skills:\n"
            "    - python\n"
            "  years_buffer: 2\n"
            "  exclude_companies:\n"
            "    - Uber\n"
            "    - Jeppesen ForeFlight\n"
        )
        try:
            cfg = self._cfg(Path(tmp.name), extra)
            self.assertEqual(
                score_listing(
                    {
                        "role": "Principal Software Engineer",
                        "url": "https://example.com/p",
                        "location": "Montreal, Canada",
                    },
                    cfg,
                ),
                0,
            )
            self.assertEqual(
                score_listing(
                    {
                        "role": "Staff Backend Software Engineer",
                        "url": "https://example.com/s",
                        "location": "Canada",
                    },
                    cfg,
                ),
                0,
            )
            self.assertEqual(
                score_listing(
                    {
                        "role": "Java Software Engineer",
                        "url": "https://example.com/j",
                        "location": "Canada",
                    },
                    cfg,
                ),
                0,
            )
            self.assertEqual(
                score_listing(
                    {
                        "role": "Software Engineer",
                        "url": "https://example.com/y",
                        "location": "Montreal, Canada",
                        "jd": "10-15 years of experience. Python Kafka.",
                    },
                    cfg,
                ),
                0,
            )
            self.assertEqual(
                score_listing(
                    {
                        "role": "Software Engineer",
                        "url": "https://example.com/jv",
                        "location": "Canada",
                        "jd": "5+ years of Java required. Spring Boot.",
                    },
                    cfg,
                ),
                0,
            )
            self.assertGreater(
                score_listing(
                    {
                        "role": "Backend Platform Engineer",
                        "url": "https://example.com/be",
                        "location": "Montreal, Canada",
                        "jd": "5+ years of experience with Python, Kafka, and AWS.",
                    },
                    cfg,
                ),
                0,
            )
            self.assertGreater(
                score_listing(
                    {
                        "role": "Member of Technical Staff",
                        "url": "https://example.com/mts",
                        "location": "Canada",
                        "jd": "Python microservices and distributed systems.",
                    },
                    cfg,
                ),
                0,
            )
            self.assertEqual(
                score_listing(
                    {
                        "role": "Technical Recruiter",
                        "url": "https://example.com/rec",
                        "location": "Canada",
                        "jd": "Hire Python engineers for our Kafka platform.",
                    },
                    cfg,
                ),
                0,
            )
            self.assertEqual(
                score_listing(
                    {
                        "company": "Uber Technologies",
                        "role": "Senior Software Engineer",
                        "url": "https://www.uber.com/careers/list/1",
                        "location": "Montreal, Canada",
                        "jd": "5+ years of experience with Python and Kafka.",
                    },
                    cfg,
                ),
                0,
            )
            self.assertEqual(
                score_listing(
                    {
                        "company": "ForeFlight",
                        "role": "Software Engineer",
                        "url": "https://boards.greenhouse.io/foreflight/jobs/1",
                        "location": "Canada",
                        "jd": "Python Kafka",
                        "saved": True,
                    },
                    cfg,
                ),
                0,
            )
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_hunt_queries_prefer_stack_over_exact_title(self):
        from pipeline.search import hunt_queries

        tmp = tempfile.TemporaryDirectory()
        try:
            cfg = self._cfg(
                Path(tmp.name),
                "  preferred_skills:\n    - python\n    - kafka\n",
            )
            self.assertEqual(hunt_queries(cfg)[0].lower(), "python")
            self.assertIn("software engineer", " ".join(hunt_queries(cfg)).lower())
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_camoufox_link_harvest_and_url_templates(self):
        from pipeline.browser_hunt import canonicalize_job_url, collect_job_links, fill_search_url

        html = """
        <a href="/jobs/view/4423802476">Role</a>
        <a href="https://ca.indeed.com/viewjob?jk=abc123&utm=x">Indeed</a>
        <a href="https://boards.greenhouse.io/acme/jobs/9">GH</a>
        <a href="/about">skip</a>
        """
        links = collect_job_links(html, "https://www.linkedin.com/jobs/search")
        self.assertIn("https://www.linkedin.com/jobs/view/4423802476", links)
        self.assertTrue(any("jk=abc123" in item for item in links))
        self.assertTrue(any("greenhouse.io" in item for item in links))
        google_html = """
        <a href="/url?q=https://boards.greenhouse.io/northstar/jobs/99&amp;sa=U">GH via Google</a>
        <a href="/url?q=https://company.icims.com/jobs/12345/job&amp;sa=U">iCIMS</a>
        <a href="/url?q=https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/Montreal/SE_R1&amp;sa=U">Workday</a>
        <a href="/url?q=https://www.google.com/search&amp;sa=U">skip google</a>
        """
        google_links = collect_job_links(google_html, "https://www.google.com/search")
        self.assertIn("https://boards.greenhouse.io/northstar/jobs/99", google_links)
        self.assertTrue(any("icims.com" in item for item in google_links))
        self.assertTrue(any("myworkdayjobs.com" in item for item in google_links))
        from pipeline.browser_hunt import build_google_dork, unwrap_result_url

        self.assertEqual(
            unwrap_result_url("https://www.google.com/url?q=https://jobs.lever.co/acme/abc&sa=U"),
            "https://jobs.lever.co/acme/abc",
        )
        self.assertIn("site:boards.greenhouse.io", build_google_dork("Software Engineer", "site:boards.greenhouse.io", "Montreal, Canada"))
        self.assertIn('"Software Engineer"', build_google_dork("Software Engineer", "greenhouse", "Canada"))
        self.assertEqual(
            canonicalize_job_url("https://www.linkedin.com/jobs/view/4423802476/?trk=flagship"),
            "https://www.linkedin.com/jobs/view/4423802476",
        )
        from pipeline.jobs import is_directory_or_salary_listing, is_job_posting_url
        from pipeline.search import score_listing

        salary = "https://ca.indeed.com/career/senior-software-engineer/salaries/Montr%C3%A9al--QC"
        self.assertFalse(is_job_posting_url(salary))
        self.assertFalse(is_job_posting_url("https://ca.indeed.com/jobs?q=Software+Engineer&l=Montreal"))
        self.assertTrue(is_job_posting_url("https://ca.indeed.com/viewjob?jk=abc123"))
        self.assertTrue(is_job_posting_url("https://www.linkedin.com/jobs/view/4456591134"))
        self.assertFalse(is_job_posting_url("https://www.linkedin.com/jobs/search/?keywords=Software+Engineer"))
        self.assertTrue(is_job_posting_url("https://boards.greenhouse.io/acme/jobs/9"))
        mixed = """
        <a href="https://ca.indeed.com/viewjob?jk=abc123">job</a>
        <a href="https://ca.indeed.com/career/senior-software-engineer/salaries/Montréal--QC">salary</a>
        """
        indeed_links = collect_job_links(mixed, "https://ca.indeed.com/jobs?q=Software+Engineer")
        self.assertTrue(any("jk=abc123" in item for item in indeed_links))
        self.assertFalse(any("/career/" in item or "/salaries" in item for item in indeed_links))
        tmp = tempfile.TemporaryDirectory()
        try:
            cfg = self._cfg(Path(tmp.name))
            self.assertEqual(
                score_listing(
                    {
                        "company": "Unknown",
                        "role": "Senior software engineer salary in Montréal, QC",
                        "url": salary,
                        "location": "Montreal, Canada",
                        "jd": "Average base salary $126,706",
                    },
                    cfg,
                ),
                0,
            )
            self.assertTrue(
                is_directory_or_salary_listing(
                    {"role": "Senior software engineer salary in Montréal, QC", "url": salary}
                )
            )
            url = fill_search_url(
                "https://www.linkedin.com/jobs/search/?keywords={query}&location={location}",
                cfg,
                "Software Engineer",
            )
            self.assertIn("Software+Engineer", url)
            self.assertIn("Canada", url)
            self.assertNotIn("Montreal", url)
            from pipeline.search import hunt_location, hunt_locations, listing_in_scope

            self.assertEqual(hunt_location(cfg), "Canada")
            self.assertIn("United States", hunt_locations(cfg))
            self.assertTrue(
                listing_in_scope(
                    {"location": "Toronto, ON, Canada", "role": "Software Engineer", "jd": ""},
                    cfg,
                )
            )
            self.assertFalse(
                listing_in_scope(
                    {
                        "location": "San Francisco, CA",
                        "role": "Software Engineer",
                        "jd": "Must be located in the United States.",
                    },
                    cfg,
                )
            )
            self.assertTrue(
                listing_in_scope(
                    {
                        "location": "New York, NY",
                        "role": "Software Engineer",
                        "jd": "Remote in the US or Canada. Python.",
                    },
                    cfg,
                )
            )
            nyc = score_listing(
                {
                    "role": "Software Engineer",
                    "url": "https://boards.greenhouse.io/acme/jobs/9",
                    "location": "New York, NY",
                    "jd": "Python Kafka. Must be based in the United States.",
                },
                cfg,
            )
            toronto = score_listing(
                {
                    "role": "Software Engineer",
                    "url": "https://boards.greenhouse.io/acme/jobs/8",
                    "location": "Toronto, ON, Canada",
                    "jd": "Python Kafka distributed systems.",
                },
                cfg,
            )
            montreal = score_listing(
                {
                    "role": "Software Engineer",
                    "url": "https://boards.greenhouse.io/acme/jobs/7",
                    "location": "Montreal, QC, Canada",
                    "jd": "Python Kafka distributed systems.",
                },
                cfg,
            )
            self.assertEqual(nyc, 0)
            self.assertGreater(toronto, 0)
            self.assertGreater(montreal, toronto)
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_linkedin_credentials_from_config(self):
        from pipeline.browser_hunt import linkedin_credentials

        tmp = tempfile.TemporaryDirectory()
        extra = (
            "  browser:\n"
            "    logins:\n"
            "      linkedin:\n"
            "        email: hunter@example.com\n"
            "        password: secret-pass\n"
        )
        try:
            cfg = self._cfg(Path(tmp.name), extra)
            email, password = linkedin_credentials(cfg)
            self.assertEqual(email, "hunter@example.com")
            self.assertEqual(password, "secret-pass")
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_camoufox_launch_uses_compose_display_in_docker(self):
        from pipeline.browser_hunt import _camoufox_launch

        tmp = tempfile.TemporaryDirectory()
        try:
            cfg = self._cfg(Path(tmp.name))
            os.environ["IN_DOCKER"] = "1"
            os.environ["DISPLAY"] = ":99"
            launch = _camoufox_launch(cfg)
            self.assertFalse(launch["headless"])
            self.assertEqual(launch["env"]["MOZ_DISABLE_CONTENT_SANDBOX"], "1")
            self.assertEqual(launch["env"]["DISPLAY"], ":99")
            os.environ.pop("IN_DOCKER", None)
            launch_host = _camoufox_launch(cfg)
            self.assertFalse(launch_host["headless"])
        finally:
            os.environ.pop("IN_DOCKER", None)
            os.environ.pop("DISPLAY", None)
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_wait_for_manual_auth_returns_when_login_clears(self):
        import asyncio
        from unittest.mock import MagicMock, patch

        from pipeline.browser_hunt import _wait_for_manual_auth

        page = MagicMock()
        checks = {"n": 0}

        async def needs_login(_page):
            checks["n"] += 1
            return checks["n"] < 2

        async def run():
            from pipeline import browser_hunt as bh

            token = bh._should_stop.set(lambda: False)
            try:
                with patch("pipeline.browser_hunt._needs_login", needs_login):
                    return await _wait_for_manual_auth(page, 8, "Sign in — use the Camoufox panel")
            finally:
                bh._should_stop.reset(token)

        self.assertTrue(asyncio.run(run()))
        self.assertGreaterEqual(checks["n"], 2)

    def test_wait_for_manual_auth_stops_without_waiting_out_the_timer(self):
        import time
        import asyncio
        from unittest.mock import patch

        from pipeline.browser_hunt import _wait_for_manual_auth

        async def needs_login(_page):
            return True

        async def run():
            from pipeline import browser_hunt as bh

            token = bh._should_stop.set(lambda: True)
            try:
                started = time.monotonic()
                with patch("pipeline.browser_hunt._needs_login", needs_login):
                    ok = await _wait_for_manual_auth(object(), 60, "Sign in — use the Camoufox panel")
                return ok, time.monotonic() - started
            finally:
                bh._should_stop.reset(token)

        ok, elapsed = asyncio.run(run())
        self.assertFalse(ok)
        self.assertLess(elapsed, 1.5)

    def test_needs_login_detects_checkpoint_and_authwall(self):
        import asyncio
        from unittest.mock import MagicMock, AsyncMock
        from pipeline.browser_hunt import _needs_login

        async def check(url, html=""):
            page = MagicMock()
            page.url = url
            page.content = AsyncMock(return_value=html)
            return await _needs_login(page)

        self.assertTrue(asyncio.run(check("https://www.linkedin.com/checkpoint/challenge")))
        self.assertTrue(asyncio.run(check("https://www.linkedin.com/login")))
        self.assertFalse(asyncio.run(check("https://www.linkedin.com/jobs/view/123", "<p>Python Kafka</p>")))

    def test_search_jobs_keeps_every_match(self):
        from pipeline.search import html_to_text, search_jobs

        self.assertIn("Python", html_to_text("<p>Need <b>Python</b></p>"))
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        apps = root / "applications"
        apps.mkdir()
        (apps / "OldCo-Software-Engineer-2026-01-01").mkdir()
        (
            apps / "OldCo-Software-Engineer-2026-01-01" / "job.json"
        ).write_text('{"company": "OldCo", "role": "Software Engineer", "url": "https://example.com/old"}')
        try:
            cfg = self._cfg(root)

            def fake_fetch(url: str):
                if "themuse" in url:
                    return {
                        "results": [
                            {
                                "name": "Software Engineer",
                                "company": {"name": "Acme"},
                                "refs": {"landing_page": "https://boards.greenhouse.io/acme/jobs/1"},
                                "locations": [{"name": "Montreal, Canada"}],
                                "contents": "<p>Python Kafka</p>",
                            },
                            {
                                "name": "Software Engineer",
                                "company": {"name": "OldCo"},
                                "refs": {"landing_page": "https://example.com/old"},
                                "locations": [{"name": "Canada"}],
                                "contents": "<p>Python</p>",
                            },
                            {
                                "name": "Forward Deployed Engineer",
                                "company": {"name": "Northstar"},
                                "refs": {"landing_page": "https://boards.greenhouse.io/northstar/jobs/2"},
                                "locations": [{"name": "Toronto, Canada"}],
                                "contents": "<p>Agents in production</p>",
                            },
                            {
                                "name": "Software Engineer",
                                "company": {"name": "Beta"},
                                "refs": {"landing_page": "https://boards.greenhouse.io/beta/jobs/3"},
                                "locations": [{"name": "Canada"}],
                                "contents": "<p>Python Kafka AWS</p>",
                            },
                            {
                                "name": "Barista",
                                "company": {"name": "Cafe"},
                                "refs": {"landing_page": "https://example.com/coffee"},
                                "locations": [{"name": "Montreal, Canada"}],
                                "contents": "<p>Coffee</p>",
                            },
                        ]
                    }
                if "remotive" in url:
                    return {"jobs": []}
                if "greenhouse" in url:
                    return {"jobs": []}
                return None

            chosen = search_jobs(cfg, fetcher=fake_fetch, jd_fetcher=lambda url: None)
            companies = [item["company"] for item in chosen]
            self.assertNotIn("OldCo", companies)
            self.assertNotIn("Cafe", companies)
            self.assertEqual(len(chosen), 2)
            self.assertTrue(all(item["jd"] for item in chosen))
            self.assertTrue(all(item["url"] for item in chosen))
            (root / "config.yaml").write_text(
                (root / "config.yaml").read_text().replace("max_jobs: 2", "max_jobs: 0")
            )
            cfg = load_config(force=True)
            chosen = search_jobs(cfg, fetcher=fake_fetch, jd_fetcher=lambda url: None)
            companies = [item["company"] for item in chosen]
            self.assertEqual(set(companies), {"Acme", "Northstar", "Beta"})
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_saved_jobs_bypass_fit_gates(self):
        from pipeline.browser_hunt import collect_job_links, saved_job_urls
        from pipeline.mcp_hunt import listings_from_mcp_payload
        from pipeline.search import rank_and_select, score_listing

        tmp = tempfile.TemporaryDirectory()
        extra = (
            "  exclude_levels:\n"
            "    - principal\n"
            "  reject_skills:\n"
            "    - java\n"
            "  saved_jobs:\n"
            "    max: 2\n"
        )
        try:
            cfg = self._cfg(Path(tmp.name), extra)
            saved = {
                "company": "SavedCo",
                "role": "Principal Python Engineer",
                "url": "https://example.com/saved",
                "location": "Montreal, Canada",
                "jd": "10-15 years of Python required.",
                "saved": True,
            }
            search = {
                "company": "FitCo",
                "role": "Senior Software Engineer",
                "url": "https://example.com/fit",
                "location": "Montreal, Canada",
                "jd": "5+ years of experience with Python and Kafka.",
            }
            self.assertEqual(score_listing(saved, cfg), 50)
            self.assertEqual(score_listing({**saved, "saved": False}, cfg), 0)
            self.assertEqual(
                score_listing(
                    {
                        "company": "CppCo",
                        "role": "C++ Software Engineer",
                        "url": "https://example.com/cpp",
                        "location": "Montreal, Canada",
                        "jd": "5+ years of C++ required. STL and templates.",
                        "saved": True,
                    },
                    cfg,
                ),
                0,
            )
            chosen = rank_and_select(cfg, [search, saved])
            self.assertEqual(chosen[0]["company"], "SavedCo")
            self.assertTrue(chosen[0]["saved"])
            self.assertIn("FitCo", [item["company"] for item in chosen])
            html = '<div data-jk="abc999"></div><a href="/jobs/view/4423802476">x</a>'
            links = collect_job_links(html, "https://ca.indeed.com/saved")
            self.assertTrue(any("jk=abc999" in item for item in links))
            self.assertTrue(any("4423802476" in item for item in links))
            urls = saved_job_urls(cfg)
            self.assertTrue(any("linkedin.com/my-items/saved-jobs" in item for item in urls))
            self.assertTrue(any("ca.indeed.com" in item for item in urls))
            parsed = listings_from_mcp_payload(
                {
                    "jobs": [
                        {
                            "title": "Software Engineer",
                            "company": "McpCo",
                            "url": "https://ca.indeed.com/viewjob?jk=1",
                            "location": "Montreal, QC",
                            "description": "Python Kafka",
                        }
                    ]
                }
            )
            self.assertEqual(parsed[0]["company"], "McpCo")
            self.assertIn("Python", parsed[0]["jd"])
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_saved_jobs_pagination_collects_links_across_pages(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from pipeline.browser_hunt import listing_next_is_enabled, _collect_paginated_job_links

        linkedin = """
        <button aria-current="true">1</button>
        <button aria-label="Page 2">2</button>
        <button aria-label="Next">Next</button>
        <a href="https://www.linkedin.com/jobs/view/111111111">job</a>
        """
        self.assertTrue(listing_next_is_enabled(linkedin))
        self.assertFalse(listing_next_is_enabled('<button aria-label="Next" disabled>Next</button>'))
        self.assertFalse(listing_next_is_enabled("<button>Easy Apply</button>"))

        pages = [
            (
                '<a href="https://www.linkedin.com/jobs/view/111111111">a</a>'
                '<button aria-label="Next">Next</button>'
            ),
            (
                '<a href="https://www.linkedin.com/jobs/view/222222222">b</a>'
                '<button aria-label="Next" disabled>Next</button>'
            ),
        ]
        state = {"i": 0}
        page = MagicMock()
        page.url = "https://www.linkedin.com/my-items/saved-jobs/"

        async def content():
            return pages[state["i"]]

        async def evaluate(script):
            text = str(script)
            if "scrollBy" in text or "scrollTo" in text:
                return None
            if state["i"] < 1:
                state["i"] = 1
                return "next"
            return ""

        page.content = content
        page.evaluate = evaluate
        page.goto = AsyncMock()

        async def run():
            from pipeline import browser_hunt as bh

            token = bh._should_stop.set(lambda: False)
            try:
                with patch("pipeline.browser_hunt._open_listings_url", AsyncMock(return_value=True)), patch(
                    "pipeline.browser_hunt._pause_ms", AsyncMock(return_value=False)
                ), patch("pipeline.browser_hunt._click_first", AsyncMock(return_value=False)):
                    return await _collect_paginated_job_links(
                        page,
                        MagicMock(),
                        {"id": "saved jobs", "link_contains": ["/jobs/view/"]},
                        "https://www.linkedin.com/my-items/saved-jobs/",
                        100,
                        0,
                        10,
                        max_pages=5,
                    )
            finally:
                bh._should_stop.reset(token)

        links = asyncio.run(run())
        self.assertEqual(len(links), 2)
        self.assertTrue(any("111111111" in item for item in links))
        self.assertTrue(any("222222222" in item for item in links))

    def test_package_summary_includes_job_link(self):
        from pipeline.reports import package_summary

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        folder = root / "applications" / "Acme-Software-Engineer-2026-08-29"
        folder.mkdir(parents=True)
        (folder / "job.json").write_text(
            '{"company": "Acme", "role": "Software Engineer", "url": "https://example.com/job", "location": "Montreal, QC, Canada", "work_mode": "hybrid"}'
        )
        (folder / "Test_CV.pdf").write_text("pdf")
        try:
            cfg = self._cfg(root)
            summary = package_summary(cfg, folder)
            self.assertEqual(summary["company"], "Acme")
            self.assertEqual(summary["role"], "Software Engineer")
            self.assertEqual(summary["url"], "https://example.com/job")
            self.assertEqual(summary.get("apply_url"), "")
            self.assertEqual(summary["location"], "Montreal")
            self.assertEqual(summary["work_mode"], "hybrid")
            self.assertTrue(summary["has_pdf"])
            self.assertTrue(summary["pdf_path"])
            self.assertFalse(summary["applied"])
            (folder / "job.json").write_text(
                '{"company": "Acme", "role": "Software Engineer", "url": "https://example.com/job", "applied": true, "applied_at": "2026-09-02"}'
            )
            applied = package_summary(cfg, folder)
            self.assertTrue(applied["applied"])
            self.assertEqual(applied["applied_at"], "2026-09-02")
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_list_packages_does_not_rebuild_exports(self):
        from unittest.mock import patch
        from pipeline.reports import list_packages

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        folder = root / "applications" / "Acme-Software-Engineer-2026-08-29"
        folder.mkdir(parents=True)
        (folder / "job.json").write_text('{"company": "Acme", "role": "Engineer"}')
        (folder / "Acme_CV.html").write_text("<html></html>")
        try:
            cfg = self._cfg(root)
            with patch("pipeline.cv_export.html_to_pages") as pages, patch("pipeline.cv_export.html_to_docx") as docx:
                listed = list_packages(cfg)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["company"], "Acme")
            pages.assert_not_called()
            docx.assert_not_called()
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_editable_exports_and_package_delete(self):
        from pipeline.cv_export import html_to_docx, html_to_pages
        from pipeline.reports import delete_package_dir, package_summary

        html = """
        <html><body>
          <header class="header">
            <div class="header-name">Test User</div>
            <div class="header-title">Software Engineer</div>
            <div class="header-contact"><span>Montreal</span></div>
          </header>
          <div class="summary">Python Kafka.</div>
          <div class="section-heading">Work Experience</div>
          <div class="job-block">
            <div class="job-header">
              <span class="company-name">Acme</span>
              <span class="job-place">Montreal</span>
            </div>
            <ul><li><span class="point">Shipped a pipeline.</span></li></ul>
          </div>
        </body></html>
        """
        tmp = tempfile.TemporaryDirectory()
        try:
            root = Path(tmp.name)
            folder = root / "applications" / "Acme-Software-Engineer-2026-08-30"
            folder.mkdir(parents=True)
            html_path = folder / "Test_User_CV.html"
            html_path.write_text(html)
            (folder / "job.json").write_text(
                '{"company": "Acme", "role": "Software Engineer", "url": "https://example.com/job"}'
            )
            docx_path = folder / "Test_User_CV.docx"
            pages_path = folder / "Test_User_CV.pages"
            os.environ["JOB_SEARCH_SKIP_PAGES_APP"] = "1"
            html_to_docx(html_path, docx_path)
            with self.assertRaises(RuntimeError):
                html_to_pages(html_path, pages_path, docx_path=docx_path)
            self.assertTrue(docx_path.is_file())
            self.assertGreater(docx_path.stat().st_size, 1000)
            self.assertFalse(pages_path.exists())
            fake = folder / "fake.pages"
            with zipfile.ZipFile(fake, "w") as zf:
                zf.writestr("index.html", "<html></html>")
            from pipeline.cv_export import is_fake_pages, is_native_pages
            self.assertTrue(is_fake_pages(fake))
            self.assertFalse(is_native_pages(fake))
            native = folder / "native.pages"
            with zipfile.ZipFile(native, "w") as zf:
                zf.writestr("Index/Document.iwa", b"iwa")
            self.assertTrue(is_native_pages(native))
            cfg = self._cfg(root)
            summary = package_summary(cfg, folder)
            self.assertEqual(summary["docx_name"], "Test_User_CV.docx")
            self.assertEqual(summary["html_name"], "Test_User_CV.html")
            self.assertIsNone(summary["pages_name"])
            self.assertTrue(delete_package_dir(cfg, folder.name))
            self.assertFalse(folder.exists())
            self.assertFalse(delete_package_dir(cfg, "../etc"))
            self.assertFalse(delete_package_dir(cfg, "_tracker"))
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            os.environ.pop("JOB_SEARCH_SKIP_PAGES_APP", None)
            tmp.cleanup()
            load_config(force=True)

    def test_rebuild_pdf_from_html(self):
        import asyncio
        from unittest.mock import patch

        from pipeline.tailor import rebuild_package_pdf

        tmp = tempfile.TemporaryDirectory()
        try:
            root = Path(tmp.name)
            folder = root / "applications" / "Acme-Software-Engineer-2026-08-30"
            folder.mkdir(parents=True)
            (folder / "Test_User_CV.html").write_text("<html><body>Hi</body></html>")
            os.environ["JOB_SEARCH_ROOT"] = str(root)
            os.environ["JOB_SEARCH_SKIP_PAGES_APP"] = "1"

            async def fake_pdf(html, pdf, max_pages=None):
                Path(pdf).write_bytes(b"%PDF-fake")

            with patch("pipeline.tailor.html_to_pdf", fake_pdf), patch(
                "pipeline.cv_export.write_editable_exports", return_value={}
            ):
                out = asyncio.run(rebuild_package_pdf(folder))
            self.assertEqual(out.name, "Test_User_CV.pdf")
            self.assertTrue(out.is_file())
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            os.environ.pop("JOB_SEARCH_SKIP_PAGES_APP", None)
            tmp.cleanup()
            load_config(force=True)

    def test_pages_helper_http_error_includes_body(self):
        import io
        import urllib.error
        from unittest.mock import patch

        from pipeline import cv_export

        tmp = tempfile.TemporaryDirectory()
        try:
            docx = Path(tmp.name) / "cv.docx"
            pages = Path(tmp.name) / "cv.pages"
            docx.write_bytes(b"PK")
            err = urllib.error.HTTPError(
                "http://host.docker.internal:8765/pages",
                400,
                "Bad Request",
                hdrs=None,
                fp=io.BytesIO(
                    b'{"ok": false, "error": "Pages got an error: Application isn\\u2019t running. (-600)"}'
                ),
            )
            with patch.object(cv_export, "_pages_app_enabled", return_value=False), patch.object(
                cv_export, "_in_docker", return_value=True
            ), patch.object(cv_export, "_helper_available", return_value=True), patch(
                "pipeline.cv_export.urllib.request.urlopen", side_effect=err
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    cv_export.docx_to_pages(docx, pages)
            msg = str(ctx.exception)
            self.assertIn("HTTP 400", msg)
            self.assertIn("-600", msg)
            self.assertNotIn("unreachable", msg)
        finally:
            tmp.cleanup()

    def test_pages_not_running_detects_apple_event_error(self):
        from pipeline.cv_export import _pages_retryable

        self.assertTrue(
            _pages_retryable("execution error: Pages got an error: Application isn’t running. (-600)")
        )
        self.assertTrue(_pages_retryable("execution error: Pages got an error: Connection is invalid. (-2700)"))
        self.assertFalse(_pages_retryable("execution error: Pages got an error: Access not allowed"))

    def test_llm_nvidia_falls_back_to_agy(self):
        from unittest.mock import patch

        from pipeline.llm import complete_prompt, nvidia_model_chain, primary_provider, worker_count

        tmp = tempfile.TemporaryDirectory()
        try:
            root = Path(tmp.name)
            (root / "config.yaml").write_text(
                "pipeline:\n"
                "  provider: nvidia\n"
                "  model: nvidia/nemotron-3-ultra-550b-a55b\n"
                "  fallback_provider: agy\n"
                "  workers: 5\n"
                "  nvidia:\n"
                "    fallback_model: openai/gpt-oss-120b\n"
            )
            os.environ["JOB_SEARCH_ROOT"] = str(root)
            cfg = load_config(force=True)
            self.assertEqual(primary_provider(cfg), "nvidia")
            self.assertEqual(worker_count(cfg), 5)
            self.assertEqual(
                nvidia_model_chain(cfg),
                ["nvidia/nemotron-3-ultra-550b-a55b", "openai/gpt-oss-120b"],
            )

            def boom(*_a, **_k):
                raise RuntimeError("nvidia down")

            with patch("pipeline.llm._call_nvidia", boom), patch(
                "pipeline.llm.call_agy", lambda prompt, effort="high": "from-agy"
            ):
                self.assertEqual(complete_prompt("hi"), "from-agy")
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_llm_nemotron_falls_back_to_gpt_oss_before_agy(self):
        from unittest.mock import patch

        from pipeline.llm import complete_prompt

        tmp = tempfile.TemporaryDirectory()
        try:
            root = Path(tmp.name)
            (root / "config.yaml").write_text(
                "pipeline:\n"
                "  provider: nvidia\n"
                "  model: nvidia/nemotron-3-ultra-550b-a55b\n"
                "  fallback_provider: agy\n"
                "  nvidia:\n"
                "    fallback_model: openai/gpt-oss-120b\n"
            )
            os.environ["JOB_SEARCH_ROOT"] = str(root)
            load_config(force=True)
            models: list[str] = []

            def nvidia(_prompt, _cfg, *, timeout, effort, model=None):
                models.append(model)
                if model and "gpt-oss" in model:
                    return "from-gpt-oss"
                raise RuntimeError("nemotron down")

            def agy_should_not_run(*_a, **_k):
                raise AssertionError("agy should not run when gpt-oss succeeds")

            with patch("pipeline.llm._call_nvidia", nvidia), patch(
                "pipeline.llm.call_agy", agy_should_not_run
            ):
                self.assertEqual(complete_prompt("hi"), "from-gpt-oss")
            self.assertEqual(
                models,
                ["nvidia/nemotron-3-ultra-550b-a55b", "openai/gpt-oss-120b"],
            )
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_nvidia_gpt_oss_uses_non_stream_content(self):
        from pipeline.llm import _nvidia_message_text

        class Message:
            content = "tagged resume"
            reasoning_content = "internal chain of thought"

        class Choice:
            message = Message()

        class Completion:
            choices = [Choice()]

        self.assertEqual(_nvidia_message_text(Completion()), "tagged resume")

    def test_skips_already_processed_by_url_not_title(self):
        from pipeline.search import find_existing_package, rank_and_select, unique_application_dir

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        folder = root / "applications" / "Acme-Software-Engineer-2026-08-29"
        folder.mkdir(parents=True)
        (folder / "job.json").write_text(
            '{"company": "Acme", "role": "Software Engineer", "url": "https://www.linkedin.com/jobs/view/111?trk=x"}'
        )
        try:
            cfg = self._cfg(root)
            same_title = find_existing_package(
                cfg, {"company": "Acme", "role": "Software Engineer", "url": "https://linkedin.com/jobs/view/222"}
            )
            same_url = find_existing_package(
                cfg, {"company": "Other", "role": "Other Role", "url": "https://linkedin.com/jobs/view/111"}
            )
            miss = find_existing_package(
                cfg, {"company": "Beta", "role": "Engineer", "url": "https://example.com/new"}
            )
            self.assertIsNone(same_title)
            self.assertEqual(same_url.name, folder.name)
            self.assertIsNone(miss)

            first = {
                "company": "GitLab",
                "role": "Senior Backend Engineer",
                "url": "https://boards.greenhouse.io/gitlab/jobs/100",
                "location": "Montreal, Canada",
                "jd": "5+ years of Python and Kafka.",
            }
            second = {
                "company": "GitLab",
                "role": "Senior Backend Engineer",
                "url": "https://boards.greenhouse.io/gitlab/jobs/200",
                "location": "Toronto, Canada",
                "jd": "5+ years of Python and Kafka.",
            }
            dup = dict(first)
            chosen = rank_and_select(cfg, [first, second, dup])
            self.assertEqual(len(chosen), 2)
            self.assertEqual({item["url"] for item in chosen}, {first["url"], second["url"]})

            taken = unique_application_dir(cfg, "Acme", "Software Engineer", "2026-08-29", "https://linkedin.com/jobs/view/222")
            self.assertEqual(taken.name, "Acme-Software-Engineer-2026-08-29-222")
            reuse = unique_application_dir(cfg, "Acme", "Software Engineer", "2026-08-29", "https://linkedin.com/jobs/view/111")
            self.assertEqual(reuse.name, folder.name)
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_job_progress_emits_found_then_queue_then_working(self):
        from pipeline.hunt import JobProgress, progress_snapshot

        events = []
        board = JobProgress(events.append)
        first = {"company": "Acme", "role": "Software Engineer", "url": "https://example.com/1"}
        extra = {"company": "Noise", "role": "Intern", "url": "https://example.com/2"}
        board.found(first)
        board.found(extra)
        board.queue([first])
        board.working(first)
        working = next(item for item in events if item["type"] == "processing")
        self.assertEqual(working["detail"], "Writing CV")
        self.assertEqual(working["status"], "working")
        board.working(first, "Scoring ATS (1/3)")
        scoring = [item for item in events if item["type"] == "processing"][-1]
        self.assertEqual(scoring["detail"], "Scoring ATS (1/3)")
        self.assertEqual(scoring["line"], "")
        board.stage("Searching LinkedIn")
        self.assertEqual(events[-1]["type"], "hunt_stage")
        self.assertEqual(events[-1]["line"], "Searching LinkedIn")
        self.assertFalse(events[-1].get("browser"))
        board.stage("Sign in or extra verification — use the Camoufox panel")
        self.assertTrue(events[-1].get("browser"))
        board.stage("Opening job boards")
        self.assertFalse(events[-1].get("browser"))
        board.stage("Signed in")
        self.assertFalse(events[-1].get("browser"))
        board.ready(first, "acme-software-engineer-2026-08-30")
        types = [item["type"] for item in events]
        self.assertEqual(types[0], "found")
        self.assertIn("queue", types)
        self.assertLess(types.index("found"), types.index("queue"))
        self.assertLess(types.index("queue"), types.index("processing"))
        self.assertLess(types.index("processing"), types.index("package"))
        queue = next(item for item in events if item["type"] == "queue")
        self.assertEqual(len(queue["jobs"]), 1)
        self.assertEqual(queue["jobs"][0]["status"], "queued")
        snap = [item for item in events if item["type"] == "progress"][-1]
        self.assertEqual(snap["found"], 1)
        self.assertEqual(snap["ready"], 1)
        self.assertEqual(snap["waiting"], 0)
        self.assertEqual(snap["processed"], 1)
        self.assertIn("1 processed", snap["line"])

        empty = progress_snapshot([])
        self.assertEqual(empty["found"], 0)
        self.assertIn("0 processed", empty["line"])
        self.assertNotIn("waiting", empty["line"])
        mid = progress_snapshot(
            [{"company": "Acme", "role": "SE", "url": "", "status": "working", "detail": "Writing CV (1/3)"}]
        )
        self.assertIn("Writing CV (1/3)", mid["line"])
        self.assertNotIn("working", mid["line"])

    def test_stage_needs_browser_only_for_user_action(self):
        from pipeline.hunt import stage_needs_browser

        self.assertTrue(
            stage_needs_browser("Sign in or extra verification — use the Camoufox panel")
        )
        self.assertTrue(stage_needs_browser("Complete extra verification — use the Camoufox panel"))
        self.assertFalse(stage_needs_browser("Opening job boards"))
        self.assertFalse(stage_needs_browser("Searching LinkedIn"))
        self.assertFalse(stage_needs_browser("Signed in"))
        self.assertFalse(stage_needs_browser("Sign-in wait ended"))

    def test_stack_gate_and_location_display(self):
        from pipeline.jobs import display_location, infer_work_mode
        from pipeline.search import score_listing
        from pipeline.stack_match import apply_stack_gate, stack_decision

        tmp = tempfile.TemporaryDirectory()
        extra = (
            "  preferred_skills:\n"
            "    - python\n"
            "    - kafka\n"
            "  reject_skills:\n"
            "    - java\n"
        )
        try:
            cfg = self._cfg(Path(tmp.name), extra)
            python_job = {
                "company": "Acme",
                "role": "Software Engineer",
                "url": "https://example.com/py",
                "location": "Montreal, QC, Canada",
                "jd": "5+ years of Python and Kafka. Distributed systems.",
            }
            cpp_job = {
                "company": "ChipCo",
                "role": "C++ Software Engineer",
                "url": "https://example.com/cpp",
                "location": "Toronto, ON (Hybrid)",
                "jd": "5+ years of C++ required. STL, templates, low-level performance.",
            }
            ts_job = {
                "company": "WebCo",
                "role": "Software Engineer",
                "url": "https://example.com/ts",
                "location": "Remote, Canada",
                "jd": "TypeScript and Node.js required. APIs and distributed services.",
            }
            mixed = {
                "company": "MixCo",
                "role": "Software Engineer",
                "url": "https://example.com/mix",
                "location": "Canada",
                "jd": "Systems programming in C++. Python is a plus for tooling.",
            }
            self.assertGreater(score_listing(python_job, cfg), 0)
            self.assertEqual(stack_decision(python_job, cfg), "keep")
            self.assertEqual(score_listing(cpp_job, cfg), 0)
            self.assertEqual(stack_decision(cpp_job, cfg), "drop")
            self.assertEqual(stack_decision(ts_job, cfg), "keep")
            self.assertEqual(stack_decision(mixed, cfg), "doubt")
            self.assertTrue(apply_stack_gate(python_job, cfg, ask_llm=lambda *_: False))
            self.assertFalse(apply_stack_gate(cpp_job, cfg, ask_llm=lambda *_: True))
            self.assertTrue(apply_stack_gate(mixed, cfg, ask_llm=lambda *_: True))
            self.assertFalse(apply_stack_gate(mixed, cfg, ask_llm=lambda *_: False))
            self.assertEqual(infer_work_mode("Toronto, ON (Hybrid)", ""), "hybrid")
            self.assertEqual(infer_work_mode("Remote, Canada", ""), "remote")
            self.assertEqual(infer_work_mode("Montreal, QC", "On-site in downtown Montreal"), "onsite")
            self.assertEqual(display_location("Montreal, QC, Canada", "onsite"), "Montreal")
            self.assertEqual(display_location("Toronto / Montreal", "hybrid"), "Toronto, Montreal")
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_hunt_and_tailor_emits_queue_before_processing(self):
        import asyncio
        from unittest.mock import patch

        from pipeline.hunt import hunt_and_tailor

        listing = {
            "company": "Acme",
            "role": "Software Engineer",
            "url": "https://example.com/acme",
            "jd": "Python Kafka",
            "location": "Montreal",
        }

        async def fake_search(cfg, limit=None, on_listing=None, on_stage=None, should_stop=None):
            if on_listing:
                on_listing(listing)
            return [listing]

        class FakeDir:
            name = "acme-software-engineer-2026-08-30"

        async def fake_process(job, fill_form=False, on_progress=None):
            if on_progress:
                on_progress("Scoring ATS (1/3)")
            return FakeDir()

        events = []
        tmp = tempfile.TemporaryDirectory()
        try:
            cfg = self._cfg(Path(tmp.name))

            async def run():
                with patch("pipeline.hunt.search_jobs_async", fake_search), patch(
                    "pipeline.run_pipeline.process_job", fake_process
                ), patch("pipeline.search.find_existing_package", return_value=None), patch(
                    "pipeline.hunt.append_job"
                ):
                    return await hunt_and_tailor(cfg, on_event=events.append)

            results = asyncio.run(run())
            self.assertEqual(results[0]["package_id"], FakeDir.name)
            types = [item["type"] for item in events if item.get("type") in {"found", "queued", "queue", "processing", "package"}]
            compact = []
            for kind in types:
                if kind == "processing" and compact and compact[-1] == "processing":
                    continue
                compact.append(kind)
            self.assertIn("processing", compact)
            self.assertIn("package", compact)
            self.assertLess(compact.index("queued") if "queued" in compact else compact.index("found"), compact.index("processing"))
            self.assertLess(compact.index("processing"), compact.index("package"))
            details = [item.get("detail") for item in events if item.get("type") == "processing"]
            self.assertIn("Writing CV", details)
            self.assertIn("Scoring ATS (1/3)", details)
            self.assertEqual(results[0]["company"], "Acme")
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_hunt_and_tailor_processes_while_search_continues(self):
        import asyncio
        from unittest.mock import patch

        from pipeline.hunt import hunt_and_tailor

        first = {
            "company": "Acme",
            "role": "Software Engineer",
            "url": "https://example.com/acme",
            "jd": "Python Kafka",
            "location": "Montreal",
        }
        second = {
            "company": "Beta",
            "role": "Software Engineer",
            "url": "https://example.com/beta",
            "jd": "Python Kafka",
            "location": "Montreal",
        }
        search_done = {"v": False}

        class FakeDir:
            def __init__(self, name):
                self.name = name

        events = []
        tmp = tempfile.TemporaryDirectory()
        try:
            cfg = self._cfg(Path(tmp.name))

            async def run():
                started = asyncio.Event()

                async def fake_search(cfg, limit=None, on_listing=None, on_stage=None, should_stop=None):
                    if on_listing:
                        on_listing(first)
                    await asyncio.wait_for(started.wait(), timeout=2)
                    self.assertFalse(search_done["v"])
                    if on_listing:
                        on_listing(second)
                    search_done["v"] = True
                    return [first, second]

                async def fake_process(job, fill_form=False, on_progress=None):
                    started.set()
                    await asyncio.sleep(0)
                    return FakeDir(f"{job['company'].lower()}-software-engineer-2026-08-30")

                with patch("pipeline.hunt.search_jobs_async", fake_search), patch(
                    "pipeline.run_pipeline.process_job", fake_process
                ), patch("pipeline.search.find_existing_package", return_value=None), patch(
                    "pipeline.hunt.append_job"
                ), patch("pipeline.llm.worker_count", return_value=2):
                    return await hunt_and_tailor(cfg, on_event=events.append)

            results = asyncio.run(run())
            companies = {item["company"] for item in results}
            self.assertEqual(companies, {"Acme", "Beta"})
            self.assertTrue(search_done["v"])
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_hunt_and_tailor_stop_skips_remaining_jobs(self):
        import asyncio
        from unittest.mock import patch

        from pipeline.hunt import hunt_and_tailor

        first = {
            "company": "Acme",
            "role": "Software Engineer",
            "url": "https://example.com/acme",
            "jd": "Python Kafka",
            "location": "Montreal",
        }
        second = {
            "company": "Beta",
            "role": "Software Engineer",
            "url": "https://example.com/beta",
            "jd": "Python Kafka",
            "location": "Montreal",
        }
        stop = {"v": False}
        processed: list[str] = []

        class FakeDir:
            name = "acme-software-engineer-2026-08-30"

        events = []
        tmp = tempfile.TemporaryDirectory()
        try:
            cfg = self._cfg(Path(tmp.name))

            async def run():
                started = asyncio.Event()

                async def fake_search(cfg, limit=None, on_listing=None, on_stage=None, should_stop=None):
                    if on_listing:
                        on_listing(first)
                    await asyncio.wait_for(started.wait(), timeout=2)
                    stop["v"] = True
                    if on_listing:
                        on_listing(second)
                    return [first, second]

                async def fake_process(job, fill_form=False, on_progress=None):
                    processed.append(job["company"])
                    started.set()
                    await asyncio.sleep(0.01)
                    return FakeDir()

                with patch("pipeline.hunt.search_jobs_async", fake_search), patch(
                    "pipeline.run_pipeline.process_job", fake_process
                ), patch("pipeline.search.find_existing_package", return_value=None), patch(
                    "pipeline.hunt.append_job"
                ), patch("pipeline.llm.worker_count", return_value=1):
                    return await hunt_and_tailor(
                        cfg,
                        on_event=events.append,
                        should_stop=lambda: stop["v"],
                    )

            asyncio.run(run())
            self.assertEqual(processed, ["Acme"])
            self.assertTrue(any(item.get("status") == "stopped" for item in events))
            self.assertFalse(any(item.get("company") == "Beta" and item.get("type") == "processing" for item in events))
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)


class StopAndHuntLockTests(unittest.TestCase):
    def setUp(self):
        from web import app as desk

        self.desk = desk
        with desk._run_lock:
            desk._runs.clear()
        self._hold = None

    def tearDown(self):
        if self._hold is not None:
            self._hold.set()
        with self.desk._run_lock:
            self.desk._runs.clear()

    def _put_hunt(self, status, *, alive=True, stop=False):
        import queue
        import threading

        sink = queue.Queue()
        run = self.desk._new_run("hunt1", sink, kind="hunt")
        run["status"] = status
        if stop:
            run["stop"].set()
        if alive:
            hold = threading.Event()
            thread = threading.Thread(target=hold.wait, daemon=True)
            thread.start()
            run["thread"] = thread
            self._hold = hold
        else:
            thread = threading.Thread(target=lambda: None)
            thread.start()
            thread.join()
            run["thread"] = thread
        with self.desk._run_lock:
            self.desk._runs["hunt1"] = run
        return run

    def test_pause_ms_returns_immediately_when_stopped(self):
        import time
        import asyncio
        from pipeline import browser_hunt as bh

        async def run():
            token = bh._should_stop.set(lambda: True)
            try:
                started = time.monotonic()
                stopped = await bh._pause_ms(30_000)
                elapsed = time.monotonic() - started
                return stopped, elapsed
            finally:
                bh._should_stop.reset(token)

        stopped, elapsed = asyncio.run(run())
        self.assertTrue(stopped)
        self.assertLess(elapsed, 1.5)

    def test_linkedin_login_does_not_swallow_cancel(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from pipeline.browser_hunt import _linkedin_login
        from pipeline.config import Config

        page = MagicMock()
        page.url = "https://www.linkedin.com/jobs/"
        page.goto = AsyncMock(side_effect=asyncio.CancelledError())
        cfg = Config(
            {"hunt": {"browser": {"logins": {"linkedin": {"email": "a@b.c", "password": "x"}}}}},
            Path("."),
        )

        async def run():
            await _linkedin_login(page, cfg, 400)

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(run())

    def test_dead_stopping_hunt_is_not_busy(self):
        self._put_hunt("stopping", alive=False, stop=True)
        self.assertIsNone(self.desk._active_hunt())

    def test_live_hunt_blocks_a_second_start(self):
        from fastapi import HTTPException
        from web.app import HuntRequest

        self._put_hunt("running", alive=True)
        with self.assertRaises(HTTPException) as ctx:
            self.desk.start_hunt(HuntRequest())
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("already running", ctx.exception.detail)

    def test_stale_stopping_hunt_allows_a_new_start(self):
        from unittest.mock import patch
        from web.app import HuntRequest

        self._put_hunt("stopping", alive=False, stop=True)
        with patch("web.app.threading.Thread") as thread_cls, patch("web.app.load_config"), patch(
            "web.app.hunt_limit", return_value=0
        ):
            thread_cls.return_value.ident = None
            thread_cls.return_value.is_alive.return_value = False
            result = self.desk.start_hunt(HuntRequest())
        self.assertTrue(result["id"])
        self.assertNotEqual(result["id"], "hunt1")

    def test_stop_sets_stopping_and_active_run_reconnects(self):
        run = self._put_hunt("running", alive=True)
        payload = self.desk.stop_run("hunt1")
        self.assertEqual(payload["status"], "stopping")
        self.assertTrue(run["stop"].is_set())
        active = self.desk.active_run()
        self.assertEqual(active["id"], "hunt1")
        self.assertEqual(active["status"], "stopping")

    def test_stop_unknown_run_is_404(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            self.desk.stop_run("missing")
        self.assertEqual(ctx.exception.status_code, 404)


class ApplyUrlTests(unittest.TestCase):
    def test_greenhouse_posting_is_already_the_form(self):
        from pipeline.apply_url import canonicalize_form_url, form_url_for_posting

        url = "https://boards.greenhouse.io/acme/jobs/123"
        target = form_url_for_posting(url)
        self.assertEqual(target.apply_kind, "ats")
        self.assertIn("greenhouse.io", target.apply_url)
        lever = canonicalize_form_url("https://jobs.lever.co/acme/abc-uuid")
        self.assertTrue(lever.endswith("/apply"))

    def test_linkedin_html_preserves_company_form_url(self):
        from pipeline.apply_url import extract_apply_from_html

        html = """
        <html><body>
          <script>{"companyApplyUrl":"https://jobs.ashbyhq.com/intact/job-uuid","easyApply":false}</script>
          <a href="https://jobs.ashbyhq.com/intact/job-uuid">Apply</a>
        </body></html>
        """
        target = extract_apply_from_html(html, "https://www.linkedin.com/jobs/view/4450738702")
        self.assertEqual(target.apply_kind, "ats")
        self.assertIn("ashbyhq.com", target.apply_url)

    def test_voyager_offsite_json_and_html_comments(self):
        from pipeline.apply_url import extract_apply_from_html

        html = """
        <html><body>
          <code id="bpr-guid-1"><!--{"com.linkedin.voyager.dash.jobs.OffsiteApply":{"companyApplyUrl":"https:\\/\\/jobs.lever.co\\/acme\\/uuid"}}--></code>
          <icon data-svg-class-name="apply-button__offsite-apply-icon-svg"></icon>
        </body></html>
        """
        target = extract_apply_from_html(html, "https://www.linkedin.com/jobs/view/4450738702")
        self.assertEqual(target.apply_kind, "ats")
        self.assertIn("lever.co", target.apply_url)

    def test_offsite_icon_is_not_treated_as_easy_apply(self):
        from pipeline.apply_url import extract_apply_from_html

        html = """
        <button>Apply</button>
        <icon data-svg-class-name="apply-button__offsite-apply-icon-svg"></icon>
        """
        target = extract_apply_from_html(html, "https://www.linkedin.com/jobs/view/4394315229")
        self.assertEqual(target.apply_kind, "aggregator")
        self.assertIn("linkedin.com", target.apply_url)

    def test_easy_apply_is_not_overwritten_by_decorate(self):
        from pipeline.jobs import decorate_listing

        listing = decorate_listing(
            {
                "company": "Acme",
                "role": "Engineer",
                "url": "https://www.linkedin.com/jobs/view/4450738702",
                "apply_url": "https://www.linkedin.com/jobs/view/4450738702",
                "apply_kind": "easy_apply",
                "location": "Montreal",
            }
        )
        self.assertEqual(listing["apply_kind"], "easy_apply")
        self.assertIn("linkedin.com", listing["apply_url"])
        from pipeline.apply_url import extract_apply_from_html

        html = """
        <button class="jobs-apply-button" aria-label="Easy Apply">Easy Apply</button>
        <script>{"easyApply": true}</script>
        """
        posting = "https://www.linkedin.com/jobs/view/4450738702"
        target = extract_apply_from_html(html, posting)
        self.assertEqual(target.apply_kind, "easy_apply")
        self.assertEqual(target.apply_url, posting)

    def test_fill_payload_and_playbook_include_form_url(self):
        from pipeline.fill import package_fill_payload
        from pipeline.playbook import render_playbook

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "applications").mkdir()
        (root / "config.yaml").write_text(
            "user:\n  full_name: Desk Tester\n  preferred_name: Desk\n  email: a@b.c\n  phone: 555\n"
            "  city: Montreal\n  country: Canada\n  linkedin: https://linkedin.com/in/x\n"
            "visa:\n  status: permanent-resident\n  description: PR, no sponsorship\n"
        )
        os.environ["JOB_SEARCH_ROOT"] = str(root)
        try:
            cfg = load_config(force=True)
            folder = root / "applications" / "Acme-Engineer-2026-09-02"
            folder.mkdir()
            (folder / "job.json").write_text(
                '{"company":"Acme","role":"Engineer","url":"https://www.linkedin.com/jobs/view/1",'
                '"apply_url":"https://boards.greenhouse.io/acme/jobs/9","apply_kind":"ats"}'
            )
            (folder / "Desk_Tester_CV.pdf").write_text("pdf")
            (folder / "cover_letter.md").write_text("Hello")
            (folder / "why_i_fit.txt").write_text("Python")
            payload = package_fill_payload(cfg, package_id=folder.name, public_base="http://127.0.0.1:8000")
            self.assertEqual(payload["apply_kind"], "ats")
            self.assertIn("greenhouse.io", payload["apply_url"])
            self.assertEqual(payload["fields"]["email"], "a@b.c")
            self.assertTrue(payload["never_submit"])
            self.assertIn("Desk_Tester_CV.pdf", payload["files"]["resume"]["url"])
            book = render_playbook(
                cfg,
                {
                    "company": "Acme",
                    "role": "Engineer",
                    "url": "https://linkedin.com/jobs/view/1",
                    "apply_url": "https://boards.greenhouse.io/acme/jobs/9",
                },
                folder,
                folder / "Desk_Tester_CV.pdf",
                folder / "cover_letter.md",
                folder / "why_i_fit.txt",
            )
            self.assertIn("Form URL:", book)
            self.assertIn("greenhouse.io", book)
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)


class ConfigMergeTests(unittest.TestCase):
    def test_overlay_replaces_lists_and_keeps_nested_defaults(self):
        from pipeline.config import _deep_merge

        merged = _deep_merge(
            {
                "pipeline": {
                    "max_attempts": 3,
                    "nvidia": {"fallback_model": "openai/gpt-oss-120b", "rpm": 40},
                },
                "hunt": {
                    "exclude_levels": ["intern", "staff"],
                    "preferred_skills": ["python"],
                    "browser": {"login_wait_seconds": 300, "headless": False},
                },
            },
            {
                "pipeline": {"max_attempts": 2, "nvidia": {"rpm": 20}},
                "hunt": {
                    "preferred_skills": ["python", "kafka"],
                    "browser": {"queries": ["python"]},
                },
            },
        )
        self.assertEqual(merged["pipeline"]["max_attempts"], 2)
        self.assertEqual(merged["pipeline"]["nvidia"]["fallback_model"], "openai/gpt-oss-120b")
        self.assertEqual(merged["pipeline"]["nvidia"]["rpm"], 20)
        self.assertEqual(merged["hunt"]["exclude_levels"], ["intern", "staff"])
        self.assertEqual(merged["hunt"]["preferred_skills"], ["python", "kafka"])
        self.assertEqual(merged["hunt"]["browser"]["login_wait_seconds"], 300)
        self.assertEqual(merged["hunt"]["browser"]["queries"], ["python"])

    def test_example_config_is_valid_yaml(self):
        import yaml

        path = Path(__file__).resolve().parents[1] / "config.example.yaml"
        data = yaml.safe_load(path.read_text())
        self.assertIsInstance(data, dict)
        self.assertIsInstance(data.get("hunt"), dict)
        self.assertIsInstance(data["hunt"].get("ats_boards"), list)

    def test_invalid_yaml_raises_value_error(self):
        from pipeline.config import _read_yaml

        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "broken.yaml"
        path.write_text("hunt:\n  ats_boards: []\n    - https://example.com\n")
        with self.assertRaises(ValueError) as ctx:
            _read_yaml(path)
        self.assertIn("Invalid YAML", str(ctx.exception))
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
