#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${HOSTSTORM_BASE:-/mnt/user/appdata/hoststorm-lofi-suite}"
cd "$ROOT"

IMAGE="hoststorm-lofi-suite-multi-live"
ROLLBACK_TAG="$IMAGE:rollback"
BACKUP_DIR="$ROOT/multi-live/data/backups"
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/hoststorm-pre-update-$STAMP.db"

# Snapshot consistente do SQLite usando o Python do container em execução.
# Fallback: copia DB + WAL/SHM para não perder transações ainda não checkpointadas.
backup_ok=0
if docker compose ps --status running --services 2>/dev/null | grep -qx 'multi-live'; then
  if docker compose exec -T multi-live python - "$STAMP" <<'PY'
import sqlite3, sys
from pathlib import Path
stamp=sys.argv[1]
src=Path('/app/data/hoststorm.db')
out=Path('/app/data/backups')/f'hoststorm-pre-update-{stamp}.db'
out.parent.mkdir(parents=True,exist_ok=True)
if src.exists():
    a=sqlite3.connect(src,timeout=30)
    b=sqlite3.connect(out)
    try:
        a.backup(b)
    finally:
        b.close(); a.close()
PY
  then
    [ ! -f "$ROOT/multi-live/data/hoststorm.db" ] || [ -s "$BACKUP_FILE" ] && backup_ok=1
  fi
fi

if [ "$backup_ok" != "1" ] && [ -f "$ROOT/multi-live/data/hoststorm.db" ]; then
  cp -a "$ROOT/multi-live/data/hoststorm.db" "$BACKUP_FILE"
  [ -f "$ROOT/multi-live/data/hoststorm.db-wal" ] && cp -a "$ROOT/multi-live/data/hoststorm.db-wal" "$BACKUP_FILE-wal" || true
  [ -f "$ROOT/multi-live/data/hoststorm.db-shm" ] && cp -a "$ROOT/multi-live/data/hoststorm.db-shm" "$BACKUP_FILE-shm" || true
fi

if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker tag "$IMAGE" "$ROLLBACK_TAG"
fi

if [ "${HOSTSTORM_SKIP_GIT_PULL:-0}" != "1" ]; then
  git pull --ff-only
fi

echo "Versão do repositório: $(cat VERSION 2>/dev/null || cat multi-live/VERSION)"
docker compose build multi-live
docker compose up -d --force-recreate multi-live

ok=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:3040/healthz >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 2
done

if [ "$ok" != 1 ]; then
  echo "Health check falhou. Executando rollback da imagem..." >&2
  "$ROOT/scripts/rollback.sh"
  exit 1
fi

docker compose ps multi-live
curl -fsS http://127.0.0.1:3040/healthz
echo

# Mantém o agente de atualização do host disponível para o botão do painel.
AGENT_SCRIPT="$ROOT/scripts/host-update-agent.sh"
PID_FILE="$ROOT/multi-live/data/update-agent.pid"
AGENT_LOG="$ROOT/multi-live/logs/update-agent.log"
if [ -f "$AGENT_SCRIPT" ]; then
  running=0
  if [ -f "$PID_FILE" ]; then
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
      running=1
    fi
  fi
  if [ "$running" != "1" ]; then
    mkdir -p "$(dirname "$AGENT_LOG")"
    nohup bash "$AGENT_SCRIPT" >>"$AGENT_LOG" 2>&1 &
    sleep 1
  fi
fi
