FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for PyNaCl (libsodium)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsodium-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

RUN useradd --create-home app
USER app

EXPOSE 8005

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005", "--log-level", "info"]
