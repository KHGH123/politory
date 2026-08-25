FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Build from the repository root:
# docker build -f incremental_pipeline/Dockerfile .
COPY incremental_pipeline/requirements.txt /app/incremental_pipeline/requirements.txt
RUN pip install --no-cache-dir -r /app/incremental_pipeline/requirements.txt

COPY . /app

ENTRYPOINT ["python", "-m", "incremental_pipeline.main"]

