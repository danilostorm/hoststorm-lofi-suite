FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    procps \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir flask werkzeug psutil numpy yt-dlp

COPY app.py /app/app.py
COPY templates /app/templates

CMD ["python", "app.py"]
