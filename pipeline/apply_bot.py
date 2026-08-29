"""Optional Greenhouse/Lever form fill. Never clicks Submit."""

from __future__ import annotations

import logging
from pathlib import Path

from pipeline.config import load_config

log = logging.getLogger(__name__)


async def apply_to_job(url: str, cv_path: str, cover_letter_path: str) -> None:
    """Fill known ATS fields and screenshot. User still clicks Submit."""
    if not url:
        log.warning("No URL provided — skip form fill.")
        return

    cfg = load_config()
    first = cfg.preferred_name
    last = cfg.last_name
    full = cfg.full_name
    email = cfg.get("user.email") or ""
    phone = str(cfg.get("user.phone") or "")

    from playwright.async_api import async_playwright

    log.info("Form fill (dry-run) for %s", url)
    screenshot_path = Path(cv_path).with_name(Path(cv_path).stem + "_form_preview.png")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
            if "greenhouse.io" in url:
                log.info("Detected Greenhouse form.")
                await page.fill("input[name='job_application[first_name]']", first)
                await page.fill("input[name='job_application[last_name]']", last)
                await page.fill("input[name='job_application[email]']", email)
                await page.fill("input[name='job_application[phone]']", phone)
                if Path(cv_path).exists():
                    await page.set_input_files("input[name='job_application[resume]']", cv_path)
                if Path(cover_letter_path).exists():
                    await page.set_input_files(
                        "input[name='job_application[cover_letter]']", cover_letter_path
                    )
            elif "lever.co" in url:
                log.info("Detected Lever form.")
                apply_link = page.locator("a.template-btn-submit")
                if await apply_link.count() and await apply_link.first.is_visible():
                    await apply_link.first.click()
                await page.fill("input[name='name']", full)
                await page.fill("input[name='email']", email)
                await page.fill("input[name='phone']", phone)
                if Path(cv_path).exists():
                    await page.set_input_files("input[name='resume']", cv_path)
            else:
                log.warning(
                    "Unknown ATS at %s. Use playbook.md and fill the form yourself.",
                    url,
                )

            await page.screenshot(path=str(screenshot_path), full_page=True)
            log.info("Screenshot saved to %s", screenshot_path)
            log.info("Dry-run: not clicking Submit.")
        except Exception as exc:
            log.error("Form fill failed: %s", exc)
        finally:
            await browser.close()
