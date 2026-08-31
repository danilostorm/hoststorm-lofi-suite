#!/usr/bin/env bash
set -u

ROOT="${HOSTSTORM_BASE:-/mnt/user/appdata/hoststorm-lofi-suite}"
STATE="$ROOT/multi-live/data"
REQ="$STATE/update-request.json"
PROCESSING="$STATE/update-processing.json"
RESULT="$STATE/update-result.json"
PID_FILE="$STATE/update-agent.pid"
INTERVAL="${HOSTSTORM_UPDATE_AGENT_INTERVAL_SECONDS:-5}"

mkdir -p "$STATE" "$ROOT/multi-live/logs"

if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" >/dev/null 2>&1; then
    exit 0
  fi
fi

echo $$ > "$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT INT TERM

timestamp() {
  date '+%Y-%m-%dT%H:%M:%S%z'
}

json_result() {
  ok="$1"
  channel="$2"
  version="$3"
  message="$4"
  cat > "$RESULT.tmp" <<EOF
{
  "ok": $ok,
  "channel": "$channel",
  "version": "$version",
  "message": "$message",
  "finished_at": "$(timestamp)"
}
EOF
  mv -f "$RESULT.tmp" "$RESULT"
}

# Recupera uma solicitação interrompida após reboot/crash.
if [ -f "$PROCESSING" ] && [ ! -f "$REQ" ]; then
  mv -f "$PROCESSING" "$REQ"
fi

while true; do
  if [ -f "$REQ" ] && [ ! -f "$PROCESSING" ]; then
    mv -f "$REQ" "$PROCESSING"
    channel="$(grep -o '"channel"[[:space:]]*:[[:space:]]*"[^"]*"' "$PROCESSING" | head -1 | cut -d'"' -f4)"
    [ -n "$channel" ] || channel="stable"

    case "$channel" in
      stable) branch="main" ;;
      beta) branch="beta" ;;
      *)
        json_result false "$channel" "" "Canal de atualização inválido."
        rm -f "$PROCESSING"
        sleep "$INTERVAL"
        continue
        ;;
    esac

    cd "$ROOT" || {
      json_result false "$channel" "" "Diretório do HostStorm indisponível."
      rm -f "$PROCESSING"
      sleep "$INTERVAL"
      continue
    }

    if ! git diff --quiet || ! git diff --cached --quiet; then
      json_result false "$channel" "" "Existem alterações locais rastreadas; atualização automática cancelada por segurança."
      rm -f "$PROCESSING"
      sleep "$INTERVAL"
      continue
    fi

    echo "[$(timestamp)] Atualização solicitada: $channel -> $branch"

    if ! git fetch origin "$branch"; then
      json_result false "$channel" "" "Falha no git fetch do canal solicitado."
      rm -f "$PROCESSING"
      sleep "$INTERVAL"
      continue
    fi

    if git show-ref --verify --quiet "refs/heads/$branch"; then
      if ! git checkout "$branch"; then
        json_result false "$channel" "" "Falha selecionando branch $branch."
        rm -f "$PROCESSING"
        sleep "$INTERVAL"
        continue
      fi
    else
      if ! git checkout -b "$branch" --track "origin/$branch"; then
        json_result false "$channel" "" "Falha criando branch local $branch."
        rm -f "$PROCESSING"
        sleep "$INTERVAL"
        continue
      fi
    fi

    if ! git merge --ff-only "origin/$branch"; then
      json_result false "$channel" "" "Branch local não permite fast-forward seguro."
      rm -f "$PROCESSING"
      sleep "$INTERVAL"
      continue
    fi

    if HOSTSTORM_SKIP_GIT_PULL=1 bash "$ROOT/scripts/update.sh"; then
      version="$(cat "$ROOT/VERSION" 2>/dev/null || cat "$ROOT/multi-live/VERSION" 2>/dev/null || true)"
      json_result true "$channel" "$version" "Atualização concluída e health check aprovado."
    else
      version="$(cat "$ROOT/VERSION" 2>/dev/null || cat "$ROOT/multi-live/VERSION" 2>/dev/null || true)"
      json_result false "$channel" "$version" "Atualização falhou; consulte update-agent.log. O update.sh tentou rollback automático."
    fi
    rm -f "$PROCESSING"
  fi
  sleep "$INTERVAL"
done
