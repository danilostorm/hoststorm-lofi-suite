# HostStorm Multi Live Manager 3.0 Professional — RC1

Esta release candidate amplia a base 2.0 sem alterar os volumes persistentes.

## Núcleo Professional incluído
- login por sessão, papéis Admin/Operador/Visualização, 2FA TOTP com QR e proteção contra brute force;
- criptografia das stream keys e credenciais sensíveis no SQLite;
- API tokens com escopos;
- perfis de transmissão e detecção automática de libx264/NVENC/QSV/VAAPI;
- telemetria FFmpeg por plataforma (FPS, bitrate, speed, qualidade) e alertas;
- failover de fonte para vídeo reserva/tela de manutenção;
- gravação única por sessão, marcadores e geração de clipes;
- grade 24/7 com rotação anti-repetição, vinhetas e intervalos comerciais;
- importação por URL, watch folder automática e retenção programada;
- PWA instalável e NOC Wall;
- multi-servidor com agentes, placement por prioridade/carga/tags e failover;
- backups/restore pelo painel e snapshots automáticos;
- analytics, diagnóstico, central de alertas e integrações opcionais YouTube/Twitch;
- API REST v1 para status, controle, biblioteca, agenda, agentes e marcadores.

## GPU
O container padrão continua compatível com CPU. Para Intel/AMD VAAPI, use o override `docker-compose.vaapi.yml` quando `/dev/dri` existir no host. NVIDIA depende do NVIDIA Container Toolkit do host e da exposição da GPU ao container.

## Segurança
Defina `HOSTSTORM_SECRET_KEY` antes da produção. A chave deve permanecer estável: ela é usada para criptografar stream keys, TOTP e credenciais de integrações.

## Estado
RC1 permanece na branch `v3-professional` até CI, Docker smoke test e testes de migração passarem. Não usar em produção antes da promoção para `main`.
