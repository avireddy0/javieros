FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

RUN useradd --create-home app && chown -R app:app /app
USER app

ENV PORT=8001
EXPOSE 8001

CMD ["python", "main.py"]
