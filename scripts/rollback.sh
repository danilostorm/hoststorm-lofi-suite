#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="${HOSTSTORM_BASE:-/mnt/user/appdata/hoststorm-lofi-suite}"
cd "$ROOT"
IMAGE="hoststorm-lofi-suite-multi-live"
ROLLBACK_TAG="$IMAGE:rollback"
if ! docker image inspect "$ROLLBACK_TAG" >/dev/null 2>&1; then
  echo "Não existe imagem de rollback salva." >&2
  exit 1
fi
docker tag "$ROLLBACK_TAG" "$IMAGE:latest"
docker compose up -d --no-build --force-recreate multi-live
for _ in $(seq 1 20); do
  curl -fsS http://127.0.0.1:3040/healthz >/dev/null 2>&1 && break
  sleep 2
done
docker compose ps multi-live
