from camoufox.async_api import AsyncCamoufox
import time
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

async def apply_to_job(url: str, cv_path: str, cover_letter_path: str):
    """
    Automates the application process using Playwright.
    Pauses before submitting so the user can review.
    """
    logging.info(f"Starting application process for {url}")
    
    async with AsyncCamoufox(headless=True) as browser:
        page = await browser.new_page()
        
        try:
            await page.goto(url)
            
            # Very basic Greenhouse/Lever automation heuristics
            if "greenhouse.io" in url:
                logging.info("Detected Greenhouse form.")
                await page.fill("input[name='job_application[first_name]']", "Vamshi")
                await page.fill("input[name='job_application[last_name]']", "Shalapaati")
                await page.fill("input[name='job_application[email]']", "shalapaativamshi@gmail.com")
                await page.fill("input[name='job_application[phone]']", "8199196294")
                await page.set_input_files("input[name='job_application[resume]']", cv_path)
                await page.set_input_files("input[name='job_application[cover_letter]']", cover_letter_path)
                
            elif "lever.co" in url:
                logging.info("Detected Lever form.")
                # Sometimes Lever forms are on a separate page
                if await page.locator("a.template-btn-submit").is_visible():
                    await page.click("a.template-btn-submit")
                    
                await page.fill("input[name='name']", "Vamshi Shalapaati")
                await page.fill("input[name='email']", "shalapaativamshi@gmail.com")
                await page.fill("input[name='phone']", "8199196294")
                await page.set_input_files("input[name='resume']", cv_path)
                
            else:
                logging.warning("Unknown ATS. Please fill manually.")
            
            # Take a screenshot of the filled form for review
            screenshot_path = cv_path.replace('.pdf', '_form_preview.png').replace('.md', '_form_preview.png')
            await page.screenshot(path=screenshot_path, full_page=True)
            logging.info(f"Form filled. Screenshot saved to {screenshot_path}")
            
            logging.info("Dry-run mode: Skipping final submit click.")
            # In production: await page.click("button[type='submit']")
            
        except Exception as e:
            logging.error(f"Failed to apply: {e}")
        finally:
            await browser.close()

if __name__ == "__main__":
    pass
