# ── Build stage: install Python deps ──────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

# iputils-ping provides the `ping` binary needed for ICMP probing
RUN apt-get update \
 && apt-get install -y --no-install-recommends iputils-ping \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy pre-installed packages
COPY --from=builder /install /usr/local

# Application source
COPY backend/app ./app
COPY frontend    ./frontend

# Create non-root user and give it ownership of the data directory
RUN useradd -m appuser \
 && mkdir -p /data \
 && chown appuser:appuser /data

# Persistent data lives here (mount as a volume)
VOLUME ["/data"]

EXPOSE 3000

USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000", \
     "--workers", "1", "--loop", "uvloop", "--http", "httptools"]
