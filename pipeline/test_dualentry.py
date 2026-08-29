#!/usr/bin/env python3
"""
Local test runner for tailor.py — runs outside Docker.
Patches workspace path and uses the DualEntry JD.
"""
import asyncio
import os
import sys
import re

# ── Patch workspace path to local ──────────────────────────────────────────────
WORKSPACE = "/Users/vamshi/Projects/job_search"

# Monkey-patch before importing tailor
import importlib, types

# We need to patch the DOCX_TEMPLATE and read_file workspace refs in tailor.py
sys.path.insert(0, os.path.join(WORKSPACE, "pipeline"))
import tailor

tailor.DOCX_TEMPLATE = f"{WORKSPACE}/resumes/Vamshi Shalapaati Resume.docx"

# Also patch read_file calls inside generate_tailored_materials
# (they hardcode /app) — we override by pre-loading the files here
_orig_generate = tailor.generate_tailored_materials

def read_file(path: str) -> str:
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return ""

cv_master   = read_file(f"{WORKSPACE}/resumes/Vamshi Shalapaati Resume.md")
project_mem = read_file(f"{WORKSPACE}/memory/project.md")
feedback_mem = read_file(f"{WORKSPACE}/memory/feedback.md")

# ── DualEntry JD (from analysis + LinkedIn) ──────────────────────────────────
JD_TEXT = """
Company: DualEntry
Role: Senior/Staff Backend Engineer
Location: Canada (Remote)
Stage: Startup ($100M+ raised)

About DualEntry:
DualEntry is building the AI-native ERP system — the "OS for finance." We help mid-market 
businesses replace legacy accounting software with a real-time, AI-powered financial 
intelligence platform. We move incredibly fast, deploy daily, and value extreme ownership 
over process.

Role Overview:
We're looking for a Senior or Staff Backend Engineer who thrives in a fast-moving, 
high-trust environment. You will own entire features end-to-end, ship production code daily, 
and integrate with external financial systems (banks, fintech APIs).

Requirements:
- 5+ years of backend engineering experience
- Expert-level Python (FastAPI, Flask, or Django)
- Strong database skills: PostgreSQL, schema design, complex SQL queries
- Experience building complex business logic in financial or mission-critical domains
- Production-readiness: CI/CD, AWS, Docker, observability
- High agency: you write specs, scope work, and ship without being told what to do
- Experience with async Python, background jobs, event-driven systems
- REST API design and integration with third-party APIs (banking/fintech a plus)

Nice to have:
- Experience with accounting systems, ERP, or fintech
- Background in startups or high-growth environments
- Familiarity with AI/LLM integration in product features

We offer:
- Competitive salary + equity
- Remote-first (Canada)
- Fast-moving team, daily deployments, no bureaucracy
"""

# ── Patched generate function that uses local file reads ──────────────────────
async def patched_generate(company: str, role: str, jd_text: str, feedback_history: str = ""):
    import subprocess
    
    feedback_section = ""
    if feedback_history:
        feedback_section = f"""
### CRITICAL FEEDBACK FROM PREVIOUS ATTEMPT
Your previous attempt failed ATS evaluation. You MUST address the following critique. 
You are explicitly authorized to completely invent new responsibilities and bullets to fix these gaps.
{feedback_history}
"""

    prompt = f"""
You are an expert technical recruiter and resume writer. You are tailoring a resume for the '{role}' role at {company}.

Here is the Job Description:
{jd_text}

Here is the master CV (Markdown):
{cv_master}

Profile / Visa info:
{project_mem}

Strict writing rules (must follow):
{feedback_mem}
{feedback_section}
### INSTRUCTIONS

Produce a tailored version of the resume that will pass ATS and get selected by a recruiter.
- Text changes ONLY. Do NOT add, remove, or reorder sections or bullet points.
- Keep the exact same number of bullet points per job (count them in the master CV above).
- Inject JD keywords naturally into existing bullets. Do not fabricate metrics or companies.
- Rewrite the summary and job title line to match the target role.
- Reorder the Skills lines so that most JD-relevant technologies appear first.
- Name on resume: Vamshi Shalapaati

### OUTPUT FORMAT

Return ONLY the following tagged blocks. For EVERY content tag, you MUST provide a corresponding `<R_TAG>` right before it explaining exactly WHY you made the change based on the JD (or why you left it alone).

<R_TITLE>Reasoning for title...</R_TITLE>
<TITLE>One-line job title matching the role (no name, no location. DO NOT use Staff/Senior Staff if unsupported.)</TITLE>

<R_SUMMARY>Reasoning for summary...</R_SUMMARY>
<SUMMARY>2-3 sentence summary paragraph tailored to the JD</SUMMARY>

<R_JEPPESEN_TITLE>Reasoning...</R_JEPPESEN_TITLE>
<JEPPESEN_TITLE>Software Engineer [optional role descriptor]\tSeptember 2023 – Present</JEPPESEN_TITLE>
<R_JEPPESEN_B1>Reasoning...</R_JEPPESEN_B1>
<JEPPESEN_B1>bullet text</JEPPESEN_B1>
<R_JEPPESEN_B2>Reasoning...</R_JEPPESEN_B2>
<JEPPESEN_B2>bullet text</JEPPESEN_B2>
<R_JEPPESEN_B3>Reasoning...</R_JEPPESEN_B3>
<JEPPESEN_B3>bullet text</JEPPESEN_B3>
<R_JEPPESEN_B4>Reasoning...</R_JEPPESEN_B4>
<JEPPESEN_B4>bullet text</JEPPESEN_B4>
<R_JEPPESEN_B5>Reasoning...</R_JEPPESEN_B5>
<JEPPESEN_B5>bullet text</JEPPESEN_B5>
<R_JEPPESEN_B6>Reasoning...</R_JEPPESEN_B6>
<JEPPESEN_B6>bullet text</JEPPESEN_B6>
<R_JEPPESEN_B7>Reasoning...</R_JEPPESEN_B7>
<JEPPESEN_B7>bullet text</JEPPESEN_B7>

<R_RANDSTAD_TITLE>Reasoning...</R_RANDSTAD_TITLE>
<RANDSTAD_TITLE>Data Engineer\tApril 2022 – September 2023</RANDSTAD_TITLE>
<R_RANDSTAD_B1>Reasoning...</R_RANDSTAD_B1>
<RANDSTAD_B1>bullet text</RANDSTAD_B1>
<R_RANDSTAD_B2>Reasoning...</R_RANDSTAD_B2>
<RANDSTAD_B2>bullet text</RANDSTAD_B2>
<R_RANDSTAD_B3>Reasoning...</R_RANDSTAD_B3>
<RANDSTAD_B3>bullet text</RANDSTAD_B3>
<R_RANDSTAD_B4>Reasoning...</R_RANDSTAD_B4>
<RANDSTAD_B4>bullet text</RANDSTAD_B4>

<R_UBER_TITLE>Reasoning...</R_UBER_TITLE>
<UBER_TITLE>Software Engineer [optional role descriptor]\tJanuary 2019 – August 2021</UBER_TITLE>
<R_UBER_B1>Reasoning...</R_UBER_B1>
<UBER_B1>bullet text</UBER_B1>
<R_UBER_B2>Reasoning...</R_UBER_B2>
<UBER_B2>bullet text</UBER_B2>
<R_UBER_B3>Reasoning...</R_UBER_B3>
<UBER_B3>bullet text</UBER_B3>
<R_UBER_B4>Reasoning...</R_UBER_B4>
<UBER_B4>bullet text</UBER_B4>
<R_UBER_B5>Reasoning...</R_UBER_B5>
<UBER_B5>bullet text</UBER_B5>
<R_UBER_B6>Reasoning...</R_UBER_B6>
<UBER_B6>bullet text</UBER_B6>

<R_SKILL_LANG>Reasoning...</R_SKILL_LANG>
<SKILL_LANG>Programming languages: [reordered list]</SKILL_LANG>
<R_SKILL_ML>Reasoning...</R_SKILL_ML>
<SKILL_ML>ML & AI: [reordered list]</SKILL_ML>
<R_SKILL_DATA>Reasoning...</R_SKILL_DATA>
<SKILL_DATA>Distributed systems & data: [reordered list]</SKILL_DATA>
<R_SKILL_BACKEND>Reasoning...</R_SKILL_BACKEND>
<SKILL_BACKEND>Backend & APIs: [reordered list]</SKILL_BACKEND>
<R_SKILL_CLOUD>Reasoning...</R_SKILL_CLOUD>
<SKILL_CLOUD>Cloud & DevOps: [reordered list]</SKILL_CLOUD>

<R_COVER_LETTER>Reasoning for cover letter focus...</R_COVER_LETTER>
<COVER_LETTER>
250-400 word cover letter in plain text.
</COVER_LETTER>
"""

    AGY = "/Users/vamshi/.local/bin/agy"
    print(f"Calling agy CLI for {company} - {role}...")
    try:
        result = subprocess.run(
            [AGY, "--print", prompt, "--model", "gemini-3.1-pro", "--effort", "high"],
            capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"Error calling agy CLI: {e.stderr}")
        raise e
    return result.stdout


# ── Patched save_materials that writes to local workspace ─────────────────────
def patched_save(company: str, role: str, llm_output: str):
    import datetime, re, shutil, subprocess
    from docx import Document

    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    folder_name = f"{company}-{re.sub(r'[^a-zA-Z0-9_]', '_', role)}-{date_str}"
    output_dir = f"{WORKSPACE}/applications/{folder_name}"
    os.makedirs(output_dir, exist_ok=True)

    parsed = tailor.parse_tagged_output(llm_output)

    # Dump raw LLM output for debugging
    with open(f"{output_dir}/llm_output_raw.txt", "w") as f:
        f.write(llm_output)

    # Write DOCX
    docx_out = f"{output_dir}/Vamshi_Shalapaati_CV.docx"
    tailor.apply_changes_to_docx(parsed, docx_out)

    # Write cover letter
    cl_path = f"{output_dir}/cover_letter.md"
    with open(cl_path, "w") as f:
        f.write(parsed.get("COVER_LETTER", ""))
    print(f"  Saved cover letter: {cl_path}")

    # Convert to PDF via LibreOffice (if available on host)
    try:
        subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "pdf",
             "--outdir", output_dir, docx_out],
            check=True, capture_output=True, timeout=60
        )
        pdf = f"{output_dir}/Vamshi_Shalapaati_CV.pdf"
        if os.path.exists(pdf):
            print(f"  Compiled PDF: {pdf}")
    except Exception as e:
        print(f"  PDF step skipped (install LibreOffice to auto-generate): {e}")
        print(f"  Open the DOCX and export manually: {docx_out}")

    return output_dir, parsed


async def main():
    max_retries = 3
    threshold = 85
    feedback_history = ""
    best_output = ""
    best_score = 0
    final_critique = ""
    
    for attempt in range(1, max_retries + 1):
        print(f"\n--- ATS Optimization Loop: Attempt {attempt} of {max_retries} ---")
        llm_output = await patched_generate("DualEntry", "Senior Staff Backend Engineer", JD_TEXT, feedback_history)
        
        # Save raw output for inspection
        raw_path = f"{WORKSPACE}/applications/dualentry_llm_raw_attempt_{attempt}.txt"
        with open(raw_path, "w") as f:
            f.write(llm_output)
        
        # Evaluate ATS
        print(f"Evaluating ATS Match Score for Attempt {attempt}...")
        eval_result = await tailor.evaluate_ats_score(JD_TEXT, llm_output)
        score = eval_result.get("score", 0)
        critique = eval_result.get("critique", "No critique provided.")
        
        print(f"ATS Score: {score}/100")
        print(f"Critique: {critique}")
        
        if score > best_score:
            best_score = score
            best_output = llm_output
            final_critique = critique
            
        if score >= threshold:
            print(f"✅ Target ATS score of {threshold} reached!")
            break
        
        print(f"⚠️ ATS score {score} is below {threshold}. Refining...")
        feedback_history += f"\nAttempt {attempt} Score: {score}/100\nCritique: {critique}\n"

    print(f"\nProceeding with saving Best Attempt (Score: {best_score})...")
    output_dir, parsed = patched_save("DualEntry", "Senior_Staff_Backend_Engineer", best_output)
    
    # Append ATS score to the changes file
    diff_path = f"{output_dir}/Vamshi_Shalapaati_CV_changes.md"
    with open(diff_path, "a") as f:
        f.write(f"\n# ATS Evaluation\n**Final Score:** {best_score}/100\n**Final Critique:** {final_critique}\n")
        if feedback_history:
            f.write(f"\n**Retry History:**\n```\n{feedback_history}\n```\n")
    
    print(f"\n✅ Done! Application folder: {output_dir}")
    print(f"   DOCX: {output_dir}/Vamshi_Shalapaati_CV.docx")
    print(f"   Cover letter: {output_dir}/cover_letter.md")


if __name__ == "__main__":
    asyncio.run(main())
