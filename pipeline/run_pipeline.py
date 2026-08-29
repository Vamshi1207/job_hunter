import asyncio
import logging
from scraper import run_scraper
from tailor import generate_tailored_materials, save_materials, evaluate_ats_score
from apply_bot import apply_to_job

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

async def main():
    logging.info("=== Starting Automated Job Pipeline ===")
    
    # 1. Scrape Jobs
    jobs = run_scraper()
    if not jobs:
        logging.info("No jobs found via scraping. Using a test mock job for validation.")
        jobs = [{
            'url': 'https://boards.greenhouse.io/testcompany/jobs/123456',
            'jd_text': 'Senior Backend Software Engineer. Must have 5+ years of experience with Python, Kafka, and distributed systems. Remote in Canada allowed. Salary: $170k - $200k.'
        }]
        
    logging.info(f"Found {len(jobs)} jobs to process. Processing only the first one for testing.")
    
    for job in jobs[:1]:
        url = job['url']
        jd_text = job['jd_text']
        
        # We will parse company and role naive-ly from URL for now
        parts = [p for p in url.replace("https://", "").replace("http://", "").split("/") if p]
        domain = parts[0].replace('www.', '')
        path_id = parts[-1] if len(parts) > 1 else "job"
        company = f"{domain.split('.')[0]}_{path_id}"
        role = "Senior Software Engineer" # Simplified
        
        logging.info(f"Processing job at {company}...")
        
        # 2. Tailor Materials with ATS Actor-Critic Loop
        max_retries = 3
        threshold = 80
        feedback_history = ""
        best_output = ""
        best_score = 0
        final_critique = ""

        for attempt in range(1, max_retries + 1):
            logging.info(f"ATS Optimization Loop: Attempt {attempt} of {max_retries}")
            llm_output = await generate_tailored_materials(company, role, jd_text, feedback_history)
            
            logging.info(f"Evaluating ATS Match Score for Attempt {attempt}...")
            eval_result = await evaluate_ats_score(jd_text, llm_output)
            score = eval_result.get("score", 0)
            critique = eval_result.get("critique", "No critique provided.")
            
            logging.info(f"ATS Score: {score}/100")
            logging.info(f"Critique: {critique}")
            
            if score > best_score:
                best_score = score
                best_output = llm_output
                final_critique = critique
                
            if score >= threshold:
                logging.info(f"✅ Target ATS score of {threshold} reached!")
                break
            
            logging.warning(f"⚠️ ATS score {score} is below {threshold}. Refining...")
            feedback_history += f"\nAttempt {attempt} Score: {score}/100\nCritique: {critique}\n"

        logging.info(f"Saving Best Attempt (Score: {best_score})...")
        
        # Save DOCX + PDF + cover letter
        output_dir = await save_materials(company, role, best_output)
        
        # Append ATS score to the changes file
        diff_path = f"{output_dir}/Vamshi_Shalapaati_CV_changes.md"
        with open(diff_path, "a") as f:
            f.write(f"\n# ATS Evaluation\n**Final Score:** {best_score}/100\n**Final Critique:** {final_critique}\n")
            if feedback_history:
                f.write(f"\n**Retry History:**\n```\n{feedback_history}\n```\n")

        docx_path = f"{output_dir}/Vamshi_Shalapaati_CV.docx"
        pdf_path  = f"{output_dir}/Vamshi_Shalapaati_CV.pdf"
        cl_path   = f"{output_dir}/cover_letter.md"

        # 3. Dry-run Application
        logging.info(f"[REVIEW] Generated materials for {company}.")
        logging.info(f"  DOCX: {docx_path}")
        logging.info(f"  PDF:  {pdf_path}")
        logging.info(f"  Cover letter: {cl_path}")
        logging.info("Triggering Playwright bot (dry-run mode: will fill form and capture screenshot, but NOT submit).")

        await apply_to_job(url, pdf_path, cl_path)

        # Log to tracker
        with open("/app/applications/_tracker.md", "a") as f:
            f.write(f"| {company} | {role} | {url} | 📤 pipeline processed | {output_dir} | |\n")
            
        logging.info(f"Finished processing {company}. Moving to next...\n")

if __name__ == "__main__":
    asyncio.run(main())
