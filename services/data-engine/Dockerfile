# Three stages (Part 0.7):
#   base  — system + runtime Python deps, no application code
#   dev   — base + requirements-dev.txt (pytest). Deliberately NO `COPY . .`:
#           the image is inert on its own; docker-compose.yml mounts the
#           source tree over /app for the `data-engine-dev` service.
#   prod  — base + application code. Last stage, so an untargeted build and
#           the `data-engine` service (target: prod) both get this one.
FROM python:3.12-slim AS base

WORKDIR /app

# Install system dependencies for asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


FROM base AS dev

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]


FROM base AS prod

# Copy application code
COPY . .

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
