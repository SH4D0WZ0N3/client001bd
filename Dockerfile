FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim AS production

RUN addgroup --system botgroup && adduser --system --ingroup botgroup botuser

WORKDIR /app

COPY --from=builder /install /usr/local

# Install fonts for Pillow watermarking
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=botuser:botgroup . .

RUN mkdir -p /app/sessions /app/logs /app/data \
    && chown -R botuser:botgroup /app/sessions /app/logs /app/data

USER botuser

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV SESSION_DIR=/app/sessions

CMD ["python", "main.py"]