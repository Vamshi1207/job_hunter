import yaml
from copy import deepcopy

def clean_yaml(filename, job_ids):
    with open(filename, 'r') as f:
        data = yaml.safe_load(f)
    
    if not data or 'jobs' not in data:
        return
    
    new_jobs = []
    for job in data['jobs']:
        url = str(job.get('url', ''))
        apply_url = str(job.get('apply_url', ''))
        should_keep = True
        for jid in job_ids:
            if jid in url or jid in apply_url:
                should_keep = False
                print(f"Removing {jid} from {filename}: {job.get('company', '')} - {job.get('role', '')}")
                break
        if should_keep:
            new_jobs.append(job)
            
    data['jobs'] = new_jobs
    with open(filename, 'w') as f:
        yaml.dump(data, f, sort_keys=False, allow_unicode=True)

job_ids = [
    "4462964697",
    "4454537422",
    "4453741370",
    "4450359079",
    "4463625729",
    "4452739658",
    "4453331933",
    "4441534579"
]

clean_yaml("applied.yaml", job_ids)
clean_yaml("deleted.yaml", job_ids)
