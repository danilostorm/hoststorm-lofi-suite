#!/usr/bin/env bash
set -euo pipefail

BASE="${HOSTSTORM_BASE:-/mnt/user/appdata/hoststorm-lofi-suite}"

mkdir -p "$BASE/multi-live/media/videos" "$BASE/multi-live/media/audios"

find "$BASE/loop-studio/outputs" -maxdepth 1 -type f \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.mkv' -o -iname '*.webm' \) -exec cp -v {} "$BASE/multi-live/media/videos/" \;
find "$BASE/loop-studio/outputs" -maxdepth 1 -type f \( -iname '*.mp3' -o -iname '*.wav' -o -iname '*.m4a' -o -iname '*.aac' -o -iname '*.flac' -o -iname '*.ogg' \) -exec cp -v {} "$BASE/multi-live/media/audios/" \;

echo "OK: arquivos copiados do Loop Studio para o Multi Live."
