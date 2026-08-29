#!/bin/bash
# Tailor application materials for jobs listed in jobs.yaml.
# HTML → PDF is handled by Playwright Chromium (see pipeline/tailor.py).

set -euo pipefail
cd "$(dirname "$0")"

exec python3 -m pipeline.run_pipeline "$@"
