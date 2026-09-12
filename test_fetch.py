import asyncio
from pipeline.config import load_config
from pipeline.browser_hunt import _camoufox_launch
from camoufox.async_api import AsyncCamoufox

async def main():
    cfg = load_config()
    launch = _camoufox_launch(cfg)
    async with AsyncCamoufox(**launch) as browser:
        page = await browser.new_page()
        print("Navigating...")
        await page.goto("https://www.linkedin.com/jobs-tracker/?stage=saved", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        html = await page.content()
        with open("tracker_html.html", "w") as f:
            f.write(html)
        print("Done. Saved tracker_html.html")

if __name__ == "__main__":
    asyncio.run(main())
