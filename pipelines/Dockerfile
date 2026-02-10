# Pin to pipelines:main as of 2026-02-09
FROM ghcr.io/open-webui/pipelines@sha256:b48e9bc338ce2be0acfbeff01810db72408a12f07739f9e3879c1f2b00952d6e

COPY requirements.txt /app/pipelines/requirements.txt
RUN pip install --no-cache-dir -r /app/pipelines/requirements.txt

COPY tools/ /app/pipelines/tools/
COPY anthropic_pipeline.py /app/pipelines/anthropic_pipeline.py
COPY gemini_pipeline.py /app/pipelines/gemini_pipeline.py
COPY dlp_filter.py /app/pipelines/dlp_filter.py

RUN useradd --create-home app && chown -R app:app /app
USER app
