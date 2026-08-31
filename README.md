# HostStorm Multi Live Manager 3.0 Professional

A versão 3.0 transforma o Multi Live Manager em um control plane profissional de transmissão, mantendo compatibilidade com os dados persistentes da 2.0.

## Operação e transmissão
- Dashboard, Lives, Agendamentos, Biblioteca, Histórico, Logs e Configurações separados.
- Agendas semanais, diárias, segunda a sexta, datas únicas, múltiplos dias, validade e políticas de conflito.
- Plataformas independentes por agenda, playlists, shuffle, repetição e parada antecipada configurável.
- Processos FFmpeg independentes por plataforma, recuperação isolada e backoff automático.
- Perfis de transmissão e detecção automática de CPU/libx264, NVIDIA NVENC, Intel QSV e VAAPI.
- Telemetria em tempo real por plataforma: FPS, bitrate, velocidade, frames perdidos e qualidade.
- Failover de fonte com vídeo reserva e tela de manutenção.
- Gravação local única por sessão, marcadores e geração de clipes.

## Broadcast 24/7
- Grade de programação contínua com rotação anti-repetição.
- Vinhetas, bumpers e intervalos comerciais.
- Preenchimento automático da grade a partir da biblioteca.
- Overlays profissionais com texto, relógio, logo, QR Code, programa atual e próximo programa.
- NOC Wall para monitoramento em tela grande.

## Biblioteca e automação
- Biblioteca com metadados, preview, duração, resolução, codec, FPS, tamanho, SHA-256 e duplicados.
- Importação por URL com yt-dlp.
- Watch folder para ingestão automática.
- Retenção programada de logs, temporários, gravações, clipes e backups.
- Diagnóstico de FFmpeg/FFprobe, SQLite, DNS, permissões, disco e encoder.

## Segurança
- Login por sessão e papéis Admin, Operador e Visualização.
- Proteção contra brute force e 2FA TOTP com QR Code.
- API Tokens com escopos.
- Stream keys, TOTP e credenciais sensíveis criptografados.
- Em instalações novas da 3.0, a chave de criptografia é gerada e persistida em `multi-live/data/security.key`, independente da senha administrativa.
- Se `HOSTSTORM_SECRET_KEY` estiver configurada, ela permanece a chave principal e deve continuar estável.

> Não apague `multi-live/data/security.key` depois que ela for criada. Não altere uma `HOSTSTORM_SECRET_KEY` já utilizada para criptografar dados.

## PWA e notificações
- PWA instalável no celular/desktop.
- Atualização de status por SSE.
- Web Push real: alertas podem chegar mesmo com o painel fechado, após o usuário autorizar o dispositivo.
- Chaves VAPID são geradas e persistidas automaticamente em `multi-live/data/`, ou podem ser fornecidas por variáveis de ambiente.
- Telegram, Discord e webhook genérico continuam disponíveis.

## Multi-servidor
- Nós/agents com seleção Local, Específica ou Automática.
- Placement por prioridade, CPU, RAM, GPU, quantidade de streams e tags.
- Failover para outro nó quando o servidor ativo deixa de responder.
- Sincronização automática das mídias necessárias antes de delegar a live para um nó remoto.
- API REST v1 para status, controle, biblioteca, agenda, agentes, heartbeat e marcadores.

## Backup e atualização
- Backup e restore do SQLite pelo painel.
- Snapshots automáticos e retenção.
- `scripts/update.sh` preserva uma imagem de rollback, faz build, recria o serviço e exige `/healthz` saudável.
- A página **Atualizações** pode solicitar STABLE ou BETA. A execução de Git/Docker ocorre no host por `scripts/host-update-agent.sh`; o container web não recebe acesso ao Docker socket.
- Após uma atualização manual bem-sucedida, `scripts/update.sh` inicia o agente do host automaticamente.

## GPU
O container padrão continua funcionando em CPU. Para Intel/AMD VAAPI existe `docker-compose.vaapi.yml`. NVIDIA requer NVIDIA Container Toolkit no host e exposição da GPU ao container.

## Compatibilidade e dados persistentes
A migração v2/SQLite permanece válida e o Git não substitui os volumes persistentes:
- `multi-live/data/`
- `multi-live/media/`
- `multi-live/logs/`
- `loop-studio/uploads/`
- `loop-studio/outputs/`
- `loop-studio/logos/`
- `.env`

## Atualizar no Unraid
```bash
cd /mnt/user/appdata/hoststorm-lofi-suite
git pull --ff-only
cat VERSION
bash scripts/update.sh
```

O `cat VERSION` deve mostrar `3.0.0`. Ao final, o health check deve retornar `ok: true`.

Rollback manual:
```bash
cd /mnt/user/appdata/hoststorm-lofi-suite
bash scripts/rollback.sh
```
