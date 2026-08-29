FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    mediainfo \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir flask werkzeug psutil numpy

COPY app.py.gz.b85 /tmp/app.py.gz.b85
COPY templates.tar.gz.b85 /tmp/templates.tar.gz.b85

RUN python -c "import base64,gzip,pathlib; p=pathlib.Path('/tmp/app.py.gz.b85'); pathlib.Path('/app/app.py').write_bytes(gzip.decompress(base64.b85decode(p.read_bytes())))"
RUN python -c "import base64,io,tarfile,pathlib; data=base64.b85decode(pathlib.Path('/tmp/templates.tar.gz.b85').read_bytes()); pathlib.Path('/app/templates').mkdir(parents=True,exist_ok=True); tarfile.open(fileobj=io.BytesIO(data),mode='r:gz').extractall('/app/templates')"
RUN python -m py_compile /app/app.py && rm -f /tmp/app.py.gz.b85 /tmp/templates.tar.gz.b85

EXPOSE 3035
CMD ["python", "app.py"]
