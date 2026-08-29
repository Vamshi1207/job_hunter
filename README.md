# Job Hunter AI Pipeline

An automated, agentic job application pipeline that dynamically tailors your resume and cover letter for specific job descriptions, scores them against an ATS (Applicant Tracking System) critic agent, and automatically fills out application forms using a stealth browser (Camoufox).

## Features
- **ATS Critic Loop**: Evaluates tailored resumes against a rigid ATS simulator. If the score is below 80/100, the pipeline refines the document iteratively without hallucinating or fabricating experience.
- **Dynamic Tailoring**: Generates perfect, role-specific PDFs and cover letters.
- **Camoufox Integration**: Bypasses Cloudflare and bot-detection to automatically load job applications and fill out forms.
- **Strict Guardrails**: Enforces reality-grounded constraints (via `memory/feedback.md`) so the AI never invents false technologies or job titles.

## Prerequisites
- Docker and Docker Compose
- *Optional:* A Gemini API Key (`GEMINI_API_KEY`). If you have already run `agy auth login` on your host machine, you do not need this key, as your local authentication is securely mounted into the container.

## Setup

1. **Clone the repository:**
   ```bash
   git clone git@github.com:Vamshi1207/job_hunter.git
   cd job_hunter
   ```

2. **Configure your personal details:**
   Copy the example config file and fill in your actual details. This file is gitignored to protect your privacy.
   ```bash
   cp config.example.yaml config.yaml
   ```
   Edit `config.yaml` with your name, email, phone, and LinkedIn.

3. **Provide your Baseline Experience:**
   - Update `memory/project.md` with your core profile and visa status.
   - Update `memory/feedback.md` with any strict guardrails (e.g., "Do not use the word Staff Engineer").
   - Ensure your master resume markdown is located at `resumes/Vamshi Shalapaati Resume.md` and the template DOCX is present.

4. **Add Jobs to Scrape:**
   Currently, you can configure the jobs you want to apply for inside `pipeline/scraper.py` or by passing them to the pipeline.

## Running the Pipeline

1. **Build the Docker container:**
   ```bash
   docker-compose build pipeline
   ```

2. **Run the automation:**
   If you have already authenticated with the `agy` CLI on your Mac, you can simply run:
   ```bash
   docker-compose up
   ```
   *Alternative:* If you prefer to use a headless API key instead of your local session, pass it as an environment variable:
   ```bash
   GEMINI_API_KEY="your_api_key_here" docker-compose up
   ```

## Output
All generated applications are saved in the `applications/` directory. For every job, you will get:
- A perfectly tailored `Vamshi_Shalapaati_CV.pdf`
- A matching `cover_letter.md`
- A `_changes.md` file showing exactly what the AI changed from your baseline CV and the final ATS score.
- A screenshot (`_form_preview.png`) of the application form filled out by the Camoufox bot.
