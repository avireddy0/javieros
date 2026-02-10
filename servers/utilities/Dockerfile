# Pin to python:3.11-slim as of 2026-02-10
FROM python@sha256:0b23cfb7425d065008b778022a17b1551c82f8b4866ee5a7a200084b7e2eafbf

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV PORT=8080
EXPOSE 8080

RUN useradd --create-home app && chown -R app:app /app
USER app

CMD ["python", "main.py"]
