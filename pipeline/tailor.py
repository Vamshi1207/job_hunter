import asyncio
import os
import re
import subprocess
import shutil
import datetime
import json
from playwright.async_api import async_playwright
HTML_TEMPLATE = "/app/resumes/template.html"

def read_file(path: str) -> str:
    if os.path.exists(path):
        with open(path, "r") as f:
            return f.read()
    return ""


async def generate_tailored_materials(company: str, role: str, jd_text: str, feedback_history: str = ""):
    """Call the agy CLI to generate tailored text content from the JD."""

    workspace = "/app"
    cv_master = read_file(f"{workspace}/resumes/Vamshi Shalapaati Resume.md")
    project_mem = read_file(f"{workspace}/memory/project.md")
    feedback_mem = read_file(f"{workspace}/memory/feedback.md")

    feedback_section = ""
    if feedback_history:
        feedback_section = f"""
### CRITICAL FEEDBACK FROM PREVIOUS ATTEMPT
Your previous attempt failed ATS evaluation. You MUST address the following critique. 
CRITICAL RULE: You are NOT authorized to invent new core technologies, frameworks, or domain experience. You must address the critique ONLY by reframing and highlighting the candidate's existing experience to match the ATS requirements as closely as possible without lying.
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

    print(f"Tailoring materials for {company} - {role} using agy CLI...")
    import shutil
    import subprocess
    AGY = shutil.which("agy") or "/root/.local/bin/agy"
    try:
        result = subprocess.run(
            [AGY, "--print", prompt, "--model", "gemini-3.1-pro", "--effort", "high"],
            capture_output=True,
            text=True,
            check=True,
        )
        output = result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error calling agy CLI: {e.stderr}")
        output = ""

    return output


def parse_tagged_output(output: str) -> dict:
    """Extract all tagged sections from the LLM output."""
    # If the LLM restarted its output (due to a glitch), discard the aborted first attempt.
    if "<TITLE>" in output:
        output = "<TITLE>" + output.split("<TITLE>")[-1]
        
    tags = [
        "TITLE", "SUMMARY",
        "JEPPESEN_TITLE", "JEPPESEN_B1", "JEPPESEN_B2", "JEPPESEN_B3",
        "JEPPESEN_B4", "JEPPESEN_B5", "JEPPESEN_B6", "JEPPESEN_B7",
        "RANDSTAD_TITLE", "RANDSTAD_B1", "RANDSTAD_B2", "RANDSTAD_B3", "RANDSTAD_B4",
        "UBER_TITLE", "UBER_B1", "UBER_B2", "UBER_B3",
        "UBER_B4", "UBER_B5", "UBER_B6",
        "SKILL_LANG", "SKILL_ML", "SKILL_DATA", "SKILL_BACKEND", "SKILL_CLOUD",
        "COVER_LETTER",
    ]
    result = {}
    for tag in tags:
        # Extract content
        matches = re.findall(rf"<{tag}>(.*?)</{tag}>", output, re.DOTALL)
        if matches:
            result[tag] = matches[-1].strip()
        else:
            result[tag] = ""
            
        # Extract reasoning
        r_matches = re.findall(rf"<R_{tag}>(.*?)</R_{tag}>", output, re.DOTALL)
        if r_matches:
            result[f"R_{tag}"] = r_matches[-1].strip()
        else:
            result[f"R_{tag}"] = ""
            
    return result


async def evaluate_ats_score(jd_text: str, tailored_text: str) -> dict:
    """
    Evaluates the tailored resume against the JD and returns an ATS score.
    Returns dict with keys: 'score' (int), 'critique' (str)
    """
    import subprocess
    import shutil
    import re
    import json
    
    prompt = f"""
You are an expert ATS (Applicant Tracking System) evaluating a candidate's resume against a Job Description.

Job Description:
{jd_text}

Tailored Resume:
{tailored_text}

Evaluate the resume and provide a probability score (0-100) that this resume will pass the ATS and be selected for an interview. 
Be extremely critical. If key requirements, technologies, or domain experience (e.g., fintech, scaling) are missing, deduct heavily.

Return exactly this JSON format:
{{
  "score": <integer from 0 to 100>,
  "critique": "<string detailing exactly what is missing and what must be added/invented to hit 100%>"
}}
"""
    AGY = shutil.which("agy") or "/root/.local/bin/agy"
    print("Evaluating ATS Score using agy CLI...")
    try:
        result = subprocess.run(
            [AGY, "--print", prompt, "--model", "gemini-3.1-pro", "--effort", "low"],
            capture_output=True,
            text=True,
            check=True
        )
        out = result.stdout
                
        # Extract JSON
        match = re.search(r'\{.*\}', out, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        else:
            return {"score": 0, "critique": "Failed to parse JSON evaluation."}
    except Exception as e:
        print(f"Error evaluating ATS: {e}")
        return {"score": 0, "critique": f"Error: {str(e)}"}


def escape_html(text: str) -> str:
    """Escape special HTML characters in the LLM text output."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text

def apply_changes_to_html(parsed: dict, output_path: str):
    """
    Copy the master HTML template, replace the {{PLACEHOLDERS}}, and save.
    """
    with open(HTML_TEMPLATE, "r") as f:
        html_content = f.read()

    tags = [
        "TITLE", "SUMMARY",
        "JEPPESEN_TITLE", "JEPPESEN_B1", "JEPPESEN_B2", "JEPPESEN_B3",
        "JEPPESEN_B4", "JEPPESEN_B5", "JEPPESEN_B6", "JEPPESEN_B7",
        "RANDSTAD_TITLE", "RANDSTAD_B1", "RANDSTAD_B2", "RANDSTAD_B3", "RANDSTAD_B4",
        "UBER_TITLE", "UBER_B1", "UBER_B2", "UBER_B3",
        "UBER_B4", "UBER_B5", "UBER_B6",
        "SKILL_LANG", "SKILL_ML", "SKILL_DATA", "SKILL_BACKEND", "SKILL_CLOUD",
    ]

    changes = []
    for tag in tags:
        new_text = parsed.get(tag, "").strip()
        if not new_text:
            print(f"  Warning: no content for tag <{tag}> — skipping")
            continue
            
        reasoning = parsed.get(f"R_{tag}", "No specific reasoning provided.")
        changes.append({"tag": tag, "new": new_text, "reasoning": reasoning})
        
        # Escape for HTML
        new_text_escaped = escape_html(new_text)
        html_content = html_content.replace(f"{{{{{tag}}}}}", new_text_escaped)

    with open(output_path, "w") as f:
        f.write(html_content)
    print(f"  Saved tailored HTML: {output_path}")
    
    # Write a changes diff for review
    diff_path = output_path.replace(".html", "_changes.md")
    with open(diff_path, "w") as f:
        f.write("# Resume Tailoring Changes\n\n")
        if not changes:
            f.write("No text changes were made.\n")
        for c in changes:
            f.write(f"### {c['tag']}\n")
            f.write(f"**Reasoning:** _{c['reasoning']}_\n\n")
            f.write(f"**Tailored:** {c['new']}\n\n")
            f.write("---\n\n")
    print(f"  Saved changes diff: {diff_path}")


async def save_materials(company: str, role: str, llm_output: str):
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    folder_name = f"{company}-{re.sub(r'[^a-zA-Z0-9_]', '_', role)}-{date_str}"
    output_dir = f"/app/applications/{folder_name}"
    os.makedirs(output_dir, exist_ok=True)

    parsed = parse_tagged_output(llm_output)

    # 1. Write tailored HTML
    html_out = f"{output_dir}/Vamshi_Shalapaati_CV.html"
    apply_changes_to_html(parsed, html_out)

    # 2. Save cover letter
    cl_path = f"{output_dir}/cover_letter.md"
    with open(cl_path, "w") as f:
        f.write(parsed.get("COVER_LETTER", ""))
    print(f"  Saved cover letter: {cl_path}")

    # 3. Convert HTML → PDF using Playwright
    pdf_out = f"{output_dir}/Vamshi_Shalapaati_CV.pdf"
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            # Convert local path to file URL
            file_url = "file://" + os.path.abspath(html_out)
            await page.goto(file_url, wait_until="networkidle")
            await page.pdf(
                path=pdf_out,
                format="Letter",
                print_background=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"} 
                # Note: Margins are 0 here because they are defined in the HTML @page CSS
            )
            await browser.close()
        print(f"  Compiled PDF: {pdf_out}")
    except Exception as e:
        print(f"  PDF conversion failed: {e}")

    return output_dir
