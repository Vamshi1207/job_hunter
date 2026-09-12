from pipeline.browser_hunt import collect_job_links
from pipeline.jobs import is_job_posting_url

with open("tracker_html.html", "r") as f:
    html = f.read()

contains = ["/jobs/view/", "/viewjob", "jk=", "currentjobid="]
links = collect_job_links(html, "https://www.linkedin.com/jobs-tracker/?stage=saved", contains)
print("Collected:", links)
