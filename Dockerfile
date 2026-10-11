FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# DejaVu gives the PDF renderer full Unicode coverage (names in any script).
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Non-root user; /data/certificates is the shared volume for generated PDFs.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/certificates /tmp/celerybeat \
    && chown -R appuser:appuser /data /tmp/celerybeat

WORKDIR /app

# Dependencies first so Docker caches this layer until requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY app ./app

USER appuser
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
