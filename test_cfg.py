from pipeline.config import load_config
from pipeline.browser_hunt import saved_jobs_enabled, saved_job_urls
cfg = load_config()
print("enabled:", saved_jobs_enabled(cfg))
print("urls:", saved_job_urls(cfg))
