# HostStorm Lo-fi Suite

Suite para criação de loops e gerenciamento de múltiplas transmissões RTMP no Unraid.

## HostStorm Multi Live Manager 2.0

A versão 2.0 reorganiza o Multi Live como uma aplicação normal e modular. O código do Multi Live agora fica diretamente no Git (`app.py`, pacote `hoststorm/`, `templates/` e `static/`), sem Base85 e sem patches aplicados durante o build.

### Principais recursos

- Dashboard com lives ativas, próxima agenda, agenda do dia, CPU, RAM, disco e histórico.
- Página separada para Lives, Agendamentos, Biblioteca, Histórico, Logs e Configurações.
- SQLite em `/app/data/hoststorm.db` com WAL, histórico e auditoria.
- Migração automática do antigo `/app/data/channels.json`, preservando um backup `channels.json.pre-v2-backup`.
- Agendamento semanal, diário, segunda a sexta e data única.
- Vários dias por agenda, intervalo de validade e políticas de conflito.
- Plataformas independentes por agendamento.
- Playlist com vários vídeos, embaralhamento e repetição.
- Encerramento configurável antes do fim da mídia; padrão continua em 60 segundos.
- Pré-teste de mídia, FFmpeg/FFprobe e destinos RTMP.
- Biblioteca com preview, duração, resolução, codec, FPS, tamanho, SHA-256 e detecção de duplicados.
- Histórico de execuções e histórico individual por plataforma.
- Cada plataforma usa seu próprio processo FFmpeg: se Twitch cair, Twitch é recuperada sem derrubar Kick/YouTube.
- Backoff de recuperação: 5s, 15s, 30s e 60s.
- Notificações opcionais via Telegram, Discord e webhook genérico.
- Atualização de status em tempo real via SSE + API de status.
- Scripts de atualização e rollback com health check.
- GitHub Actions com testes, compilação e Docker build.

## Serviços

- Loop Studio: `3035`
- Multi Live Manager: `3040`

## Atualizar no Unraid

```bash
cd /mnt/user/appdata/hoststorm-lofi-suite
bash scripts/update.sh
```

O script:

1. salva backup do SQLite existente;
2. marca a imagem Docker atual como rollback;
3. executa `git pull --ff-only`;
4. constrói o Multi Live;
5. recria o container;
6. verifica `/healthz`;
7. executa rollback automaticamente se o serviço novo não ficar saudável.

Rollback manual:

```bash
cd /mnt/user/appdata/hoststorm-lofi-suite
bash scripts/rollback.sh
```

## Atualização manual

```bash
cd /mnt/user/appdata/hoststorm-lofi-suite
git pull --ff-only
docker compose build multi-live
docker compose up -d --force-recreate multi-live
curl -fsS http://127.0.0.1:3040/healthz
```

## Dados persistentes

O Git não altera:

- `multi-live/data/`
- `multi-live/media/`
- `multi-live/logs/`
- `loop-studio/uploads/`
- `loop-studio/outputs/`
- `loop-studio/logos/`
- `.env`

Nunca envie stream keys ou `.env` ao repositório.
