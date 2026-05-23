FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure log directory exists
RUN mkdir -p /app/logs

# Persist Pyrogram session outside the image layer
VOLUME ["/app/sessions"]

CMD ["python", "main.py"]