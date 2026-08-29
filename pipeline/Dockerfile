FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies for building tools + LibreOffice for DOCX→PDF
RUN apt-get update && apt-get install -y \
    curl \
    git \
    libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Antigravity CLI to generate ADC
RUN curl -fsSL https://antigravity.google/cli/install.sh | bash
ENV PATH="/root/.local/bin:${PATH}"

# Keep container alive or run the orchestrator
CMD ["tail", "-f", "/dev/null"]
