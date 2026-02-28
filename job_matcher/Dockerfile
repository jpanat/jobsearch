FROM python:3.11-slim

WORKDIR /app

# Install system deps for PyMuPDF + curl (healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

COPY job_matcher/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy full project so imports resolve correctly
COPY . /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
