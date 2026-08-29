#!/usr/bin/env python3
"""Reconstrói app.py e templates locais a partir dos bundles versionados."""

import base64
import gzip
import io
import shutil
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

for service in ('loop-studio', 'multi-live'):
    service_dir = ROOT / service
    app_bundle = service_dir / 'app.py.gz.b85'
    templates_bundle = service_dir / 'templates.tar.gz.b85'

    app_data = gzip.decompress(base64.b85decode(app_bundle.read_bytes()))
    (service_dir / 'app.py').write_bytes(app_data)

    templates_dir = service_dir / 'templates'
    if templates_dir.exists():
        shutil.rmtree(templates_dir)
    templates_dir.mkdir(parents=True, exist_ok=True)

    archive = base64.b85decode(templates_bundle.read_bytes())
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        tar.extractall(templates_dir)

    print(f'OK: {service}/app.py e {service}/templates reconstruídos')
