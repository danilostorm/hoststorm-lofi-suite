# HostStorm Multi Live Manager 3.0 Professional

Versão final do control plane profissional de transmissão do HostStorm.

## Incluído na 3.0
- login por sessão, Admin/Operador/Visualização, proteção contra brute force e 2FA TOTP;
- criptografia de stream keys e credenciais com chave persistente independente da senha administrativa;
- API Tokens com escopos e API REST v1;
- perfis de transmissão e suporte automático a libx264/NVENC/QSV/VAAPI;
- telemetria FFmpeg por plataforma, qualidade e alertas;
- recuperação individual de destinos, failover de fonte e mídia de manutenção;
- gravação, marcadores e clipes;
- grade 24/7, rotação anti-repetição, vinhetas e comerciais;
- overlays com texto, relógio, logo, QR Code e informações da programação;
- importação por URL, watch folder e retenção automática;
- PWA instalável, Web Push e NOC Wall;
- multi-servidor com placement por carga/tags, failover e sincronização automática de mídia;
- backups/restore, analytics, diagnóstico, alertas e integrações opcionais YouTube/Twitch;
- updater STABLE/BETA solicitado pelo painel e executado por agente seguro no host;
- CI com compilação, testes, Docker build e smoke test `/healthz`.

## Segurança
`HOSTSTORM_SECRET_KEY` pode ser configurada explicitamente e deve permanecer estável. Sem essa variável, a 3.0 gera uma chave aleatória persistente em `multi-live/data/security.key`. Não apague essa chave após a migração.

## Dados persistentes
A 3.0 preserva `multi-live/data`, `multi-live/media`, `multi-live/logs`, `.env` e os diretórios persistentes do Loop Studio.

## Instalação/upgrade
```bash
cd /mnt/user/appdata/hoststorm-lofi-suite
git pull --ff-only
cat VERSION
bash scripts/update.sh
```

Versão esperada: `3.0.0`.
