FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1
ENV PATH="/root/.local/bin:${PATH}"
ENV JOB_SEARCH_ROOT=/app
ENV IN_DOCKER=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    xvfb \
    libgtk-3-0 \
    libdbus-glib-1-2 \
    libxt6 \
    libx11-xcb1 \
    libasound2 \
    libnss3 \
    libxss1 \
    libxrandr2 \
    libpangocairo-1.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libgbm1 \
    fonts-liberation \
    xauth \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://antigravity.google/cli/install.sh | bash

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install --with-deps chromium \
    && python3 -m camoufox fetch

CMD ["python3", "-m", "uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
