FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY e2epool/__init__.py e2epool/__init__.py
RUN pip install --no-cache-dir .

COPY . .
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8080

CMD ["uvicorn", "e2epool.main:app", "--host", "0.0.0.0", "--port", "8080"]
