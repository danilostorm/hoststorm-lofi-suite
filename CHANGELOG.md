# Changelog

## 2.0.0 - 2026-08-30

- Refatoração do Multi Live para código fonte normal e modular.
- Banco SQLite com migração automática do `channels.json`.
- Dashboard 2.0 e navegação por páginas.
- Calendário de agendamentos.
- Agenda avançada: semanal, diária, dias úteis, data única, validade e conflito.
- Playlists agendadas, shuffle e repeat.
- Biblioteca inteligente com ffprobe, preview, SHA-256 e duplicados.
- Pré-teste antes de iniciar uma live.
- Histórico estruturado de transmissões.
- FFmpeg isolado por plataforma com recuperação individual e backoff.
- Telegram, Discord e webhooks.
- SSE/API de status em tempo real.
- Scripts de update/rollback com health check.
- CI com pytest, compileall e Docker build.
