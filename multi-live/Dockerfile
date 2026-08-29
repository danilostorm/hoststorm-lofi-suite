FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    procps \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN set -eux; \
    for attempt in 1 2 3 4 5; do \
      if pip install --retries 10 --timeout 120 flask werkzeug psutil numpy yt-dlp; then \
        rm -rf /root/.cache/pip; \
        break; \
      fi; \
      if [ "$attempt" = "5" ]; then \
        echo "pip install falhou após 5 tentativas" >&2; \
        exit 1; \
      fi; \
      sleep $((attempt * 5)); \
    done

COPY app.py.gz.b85 /tmp/app.py.gz.b85
COPY templates.tar.gz.b85 /tmp/templates.tar.gz.b85
COPY patch_schedule_v3.py /tmp/patch_schedule_v3.py

RUN python -c "import base64,gzip,pathlib; p=pathlib.Path('/tmp/app.py.gz.b85'); pathlib.Path('/app/app.py').write_bytes(gzip.decompress(base64.b85decode(p.read_bytes())))"
RUN python -c "import base64,io,tarfile,pathlib; data=base64.b85decode(pathlib.Path('/tmp/templates.tar.gz.b85').read_bytes()); pathlib.Path('/app/templates').mkdir(parents=True,exist_ok=True); tarfile.open(fileobj=io.BytesIO(data),mode='r:gz').extractall('/app/templates')"
RUN python /tmp/patch_schedule_v3.py
RUN python -m py_compile /app/app.py && rm -f /tmp/app.py.gz.b85 /tmp/templates.tar.gz.b85 /tmp/patch_schedule_v3.py

CMD ["python", "app.py"]
