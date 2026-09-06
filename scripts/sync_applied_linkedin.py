#!/usr/bin/env python3
"""One-time script to sync all applied jobs that match LinkedIn saved jobs to LinkedIn.

For each matching job, it navigates to the LinkedIn posting, clicks 'Apply',
and confirms 'Yes' to 'Did you finish applying?'.
"""
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from pipeline.config import load_config
from pipeline.browser_hunt import sync_all_applied_saved_jobs_on_linkedin


def main():
    cfg = load_config(force=True)
    res = asyncio.run(sync_all_applied_saved_jobs_on_linkedin(cfg, on_progress=lambda m: print(f"-> {m}", flush=True)))
    print("\n===============================")
    print(f"Total: {res.get('total')}, Success: {res.get('success')}, Failed: {res.get('failed')}")
    for item in res.get("items", []):
        status = "OK" if item.get("ok") else f"FAILED ({item.get('error', 'not applied')})"
        print(f"- {item.get('company')} | {item.get('role')}: {status}")
    print("===============================\n")


if __name__ == "__main__":
    main()
