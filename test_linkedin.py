import asyncio
from camoufox.async_api import AsyncCamoufox
from bs4 import BeautifulSoup
import os

async def main():
    profile = os.path.abspath(".camoufox-profile")
    async with AsyncCamoufox(headless=True, user_data_dir=profile) as browser:
        page = await browser.new_page()
        print("Navigating...")
        await page.goto("https://www.linkedin.com/my-items/saved-jobs/")
        await page.wait_for_timeout(3000)
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        links = soup.find_all("a", href=True)
        print("Got", len(links), "links")
        for tag in links:
            href = tag["href"]
            if "job" in href.lower() or "currentJobId" in href:
                print("HREF:", href)
        
        with open("saved_jobs.html", "w") as f:
            f.write(html)

if __name__ == "__main__":
    asyncio.run(main())
