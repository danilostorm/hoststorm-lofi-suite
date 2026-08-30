FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    TZ=America/Sao_Paulo

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg procps ca-certificates tzdata curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN set -eux; \
    for attempt in 1 2 3 4 5; do \
      if pip install --retries 10 --timeout 120 -r /app/requirements.txt; then break; fi; \
      if [ "$attempt" = "5" ]; then exit 1; fi; \
      sleep $((attempt * 5)); \
    done; \
    rm -rf /root/.cache/pip

COPY app.py /app/app.py
COPY hoststorm /app/hoststorm
COPY templates /app/templates
COPY static /app/static
COPY VERSION /app/VERSION

RUN python -m compileall -q /app/app.py /app/hoststorm

EXPOSE 3040
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD curl -fsS http://127.0.0.1:3040/healthz || exit 1
CMD ["python", "app.py"]
