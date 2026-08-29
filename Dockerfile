FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1
ENV PATH="/root/.local/bin:${PATH}"

RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://antigravity.google/cli/install.sh | bash

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright OS dependencies (covers both Chromium and Firefox/Camoufox)
RUN playwright install --with-deps chromium firefox
RUN python3 -m camoufox fetch

CMD ["python3", "pipeline/run_pipeline.py"]
