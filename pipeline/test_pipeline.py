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
                        "role": "Senior Software Engineer",
                        "url": "https://example.com/ok",
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
        <a href="/url?q=https://www.google.com/search&amp;sa=U">skip google</a>
        """
        google_links = collect_job_links(google_html, "https://www.google.com/search")
        self.assertEqual(google_links, ["https://boards.greenhouse.io/northstar/jobs/99"])
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
            self.assertIn("Montreal", url)
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

    def test_camoufox_launch_uses_virtual_display_in_docker(self):
        from pipeline.browser_hunt import _camoufox_launch

        tmp = tempfile.TemporaryDirectory()
        try:
            cfg = self._cfg(Path(tmp.name))
            os.environ["IN_DOCKER"] = "1"
            os.environ["DISPLAY"] = ":99"
            launch = _camoufox_launch(cfg)
            self.assertEqual(launch["headless"], "virtual")
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

    def test_search_jobs_ranks_and_caps(self):
        from pipeline.search import html_to_text, search_jobs

        self.assertIn("Python", html_to_text("<p>Need <b>Python</b></p>"))
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        apps = root / "applications"
        apps.mkdir()
        (apps / "OldCo-Software-Engineer-2026-01-01").mkdir()
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
            self.assertIn("Acme", companies)
            self.assertIn("Northstar", companies)
            self.assertNotIn("OldCo", companies)
            self.assertNotIn("Cafe", companies)
            self.assertLessEqual(len(chosen), 2)
            self.assertTrue(all(item["jd"] for item in chosen))
            self.assertTrue(all(item["url"] for item in chosen))
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
                "role": "Principal Java Engineer",
                "url": "https://example.com/saved",
                "location": "Montreal, Canada",
                "jd": "10-15 years of Java required.",
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

    def test_package_summary_includes_job_link(self):
        from pipeline.reports import package_summary

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        folder = root / "applications" / "Acme-Software-Engineer-2026-08-29"
        folder.mkdir(parents=True)
        (folder / "job.json").write_text(
            '{"company": "Acme", "role": "Software Engineer", "url": "https://example.com/job"}'
        )
        (folder / "Test_CV.pdf").write_text("pdf")
        try:
            cfg = self._cfg(root)
            summary = package_summary(cfg, folder)
            self.assertEqual(summary["company"], "Acme")
            self.assertEqual(summary["role"], "Software Engineer")
            self.assertEqual(summary["url"], "https://example.com/job")
            self.assertTrue(summary["has_pdf"])
            self.assertTrue(summary["pdf_path"])
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
            html_to_docx(html_path, docx_path)
            html_to_pages(html_path, pages_path)
            self.assertTrue(docx_path.is_file())
            self.assertGreater(docx_path.stat().st_size, 1000)
            self.assertTrue(zipfile.ZipFile(pages_path).namelist())
            cfg = self._cfg(root)
            summary = package_summary(cfg, folder)
            self.assertEqual(summary["docx_name"], "Test_User_CV.docx")
            self.assertEqual(summary["html_name"], "Test_User_CV.html")
            self.assertEqual(summary["pages_name"], "Test_User_CV.pages")
            self.assertTrue(delete_package_dir(cfg, folder.name))
            self.assertFalse(folder.exists())
            self.assertFalse(delete_package_dir(cfg, "../etc"))
            self.assertFalse(delete_package_dir(cfg, "_tracker"))
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)

    def test_skips_already_processed_role_and_url(self):
        from pipeline.search import find_existing_package

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        folder = root / "applications" / "Acme-Software-Engineer-2026-08-29"
        folder.mkdir(parents=True)
        (folder / "job.json").write_text(
            '{"company": "Acme", "role": "Software Engineer", "url": "https://example.com/job"}'
        )
        try:
            cfg = self._cfg(root)
            by_role = find_existing_package(
                cfg, {"company": "Acme", "role": "Software Engineer", "url": "https://other.example/new"}
            )
            by_url = find_existing_package(
                cfg, {"company": "Other", "role": "Other Role", "url": "https://example.com/job"}
            )
            miss = find_existing_package(
                cfg, {"company": "Beta", "role": "Engineer", "url": "https://example.com/new"}
            )
            self.assertEqual(by_role.name, folder.name)
            self.assertEqual(by_url.name, folder.name)
            self.assertIsNone(miss)
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
        self.assertIn("0 waiting", empty["line"])

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

        async def fake_search(cfg, limit=None, on_listing=None):
            if on_listing:
                on_listing(listing)
            return [listing]

        class FakeDir:
            name = "acme-software-engineer-2026-08-30"

        async def fake_process(job, fill_form=False):
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
            types = [item["type"] for item in events if item.get("type") in {"found", "queue", "processing", "package"}]
            self.assertEqual(types, ["found", "queue", "processing", "package"])
            self.assertEqual(results[0]["company"], "Acme")
        finally:
            os.environ.pop("JOB_SEARCH_ROOT", None)
            tmp.cleanup()
            load_config(force=True)


if __name__ == "__main__":
    unittest.main()
