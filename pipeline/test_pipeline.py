"""Unit tests for the pipeline (no LLM, no browser)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pipeline.config import load_config
from pipeline.jobs import fetch_jd, load_jobs, slug
from pipeline.playbook import render_playbook, visa_answers
from pipeline.tailor import (
    _retry_section,
    build_critic_prompt,
    build_tailor_prompt,
    escape_html,
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


class ParseTests(unittest.TestCase):
    def test_parse_and_plain_text(self):
        raw = """
<R_TITLE>match backend</R_TITLE>
<TITLE>Software Engineer - Distributed Systems</TITLE>
<SUMMARY>Built Kafka pipelines.</SUMMARY>
<JEPPESEN_TITLE>Software Engineer\tSeptember 2023 – Present</JEPPESEN_TITLE>
<JEPPESEN_B1>Shipped constraint engines in Python.</JEPPESEN_B1>
<SKILL_LANG>Programming languages: Python, SQL</SKILL_LANG>
<COVER_LETTER>Dear team</COVER_LETTER>
"""
        parsed = normalize_parsed(parse_tagged_output(raw))
        self.assertEqual(parsed["TITLE"], "Software Engineer - Distributed Systems")
        self.assertEqual(parsed["JEPPESEN_TITLE"], "Software Engineer")
        self.assertEqual(parsed["SKILL_LANG"], "Python, SQL")
        plain = resume_plain_text(parsed)
        self.assertIn("Kafka", plain)
        self.assertIn("Jeppesen", plain)

    def test_last_complete_attempt_wins(self):
        raw = "<TITLE>first</TITLE> garbage <R_TITLE>r</R_TITLE><TITLE>second</TITLE>"
        parsed = parse_tagged_output(raw)
        self.assertEqual(parsed["TITLE"], "second")

    def test_escape_html(self):
        self.assertEqual(escape_html("A & B <C>"), "A &amp; B &lt;C&gt;")

    def test_strip_helpers(self):
        self.assertEqual(strip_skill_prefix("ML & AI: MCP, NLP", "ML & AI"), "MCP, NLP")
        self.assertEqual(strip_title_dates("Data Engineer    April 2022"), "Data Engineer")

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


if __name__ == "__main__":
    unittest.main()
