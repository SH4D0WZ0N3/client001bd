# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – dependency builder
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools needed for some C extensions (e.g. tgcrypto)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – production image
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS production

# Create a non-root user for security
RUN addgroup --system botgroup && adduser --system --ingroup botgroup botuser

WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=botuser:botgroup . .

# ── Persistent directory layout ───────────────────────────────────────────────
# /app/sessions  – Pyrogram .session file   → mount Railway Volume here
# /app/logs      – rotating log files       → stdout is primary on Railway
# /app/data      – any local scratch space
# ─────────────────────────────────────────────────────────────────────────────
RUN mkdir -p /app/sessions /app/logs /app/data \
    && chown -R botuser:botgroup /app/sessions /app/logs /app/data

USER botuser

# Railway injects PORT but this bot has no HTTP server.
# We declare it so Railway's build system is satisfied; the app never binds it.
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Tell Python where our sessions live (used in app/bot/bot.py workdir param)
ENV SESSION_DIR=/app/sessions

CMD ["python", "main.py"]