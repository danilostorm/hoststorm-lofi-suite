FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    mediainfo \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir flask werkzeug psutil numpy
COPY app.py /app/app.py
COPY templates /app/templates
EXPOSE 3035
CMD ["python", "app.py"]
