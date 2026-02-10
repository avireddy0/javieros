FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV PORT=8080
EXPOSE 8080

RUN useradd --create-home app && chown -R app:app /app
USER app

CMD ["python", "main.py"]
