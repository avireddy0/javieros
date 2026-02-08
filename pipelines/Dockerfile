FROM ghcr.io/open-webui/pipelines:main

COPY requirements.txt /app/pipelines/requirements.txt
RUN pip install --no-cache-dir -r /app/pipelines/requirements.txt

COPY tools/ /app/pipelines/tools/
COPY anthropic_pipeline.py /app/pipelines/anthropic_pipeline.py
COPY gemini_pipeline.py /app/pipelines/gemini_pipeline.py
COPY dlp_filter.py /app/pipelines/dlp_filter.py
