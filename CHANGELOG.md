# 3.1.0

HostStorm Multi Live Manager 3.1 — fontes remotas e acabamento da Biblioteca.

## Novo
- agendamentos podem usar vídeos da Biblioteca ou uma URL http/https, incluindo YouTube e sites suportados pelo yt-dlp;
- análise da URL antes de salvar, com título, duração, origem e preview quando disponível;
- preview do YouTube usa embed com domínio `youtube-nocookie.com`; o embed é somente visual — a transmissão continua servidor-side por yt-dlp + FFmpeg;
- duração remota detectada pelo yt-dlp/ffprobe alimenta o mesmo encerramento automático das mídias locais, inclusive a regra de parar alguns segundos antes do fim;
- quando uma fonte remota não informa duração, a agenda exige um limite máximo em minutos para evitar live sem encerramento previsto;
- yt-dlp atualizado para 2026.8.19 com dependências padrão/EJS e Node.js 22 no container para compatibilidade moderna com YouTube;
- Diagnóstico passa a mostrar o estado do yt-dlp e do runtime JavaScript;
- importação da Biblioteca por URL usa yt-dlp em segundo plano, mescla vídeo+áudio em MP4 e permite extração de áudio em MP3;
- Biblioteca redesenhada com upload compacto, status do yt-dlp, busca instantânea, filtros por mídia e cards com preview/metadados consistentes;
- scrollbars personalizadas na interface, sidebar, playlist e calendário;
- proteção contra URLs locais/privadas por padrão para reduzir risco de SSRF; ambientes que realmente precisem disso podem usar `HOSTSTORM_ALLOW_PRIVATE_SOURCE_URLS=1`.

## Compatibilidade
Agendamentos antigos continuam como fonte `library`. A migração adiciona os campos de fonte remota ao SQLite automaticamente sem recriar o banco.

# 3.0.0

HostStorm Multi Live Manager 3.0 Professional.

## Principais mudanças
- autenticação por sessão, papéis de usuário e 2FA;
- proteção e criptografia persistente de credenciais;
- perfis de transmissão, suporte a GPU e telemetria FFmpeg;
- failover de fonte, recuperação por plataforma, gravações e clipes;
- grade 24/7, rotação anti-repetição, vinhetas, comerciais e overlays;
- biblioteca avançada, importação por URL e watch folder;
- PWA, Web Push, NOC Wall, analytics, diagnóstico e alertas;
- operação multi-servidor com balanceamento, failover e sincronização de mídia;
- backups/restore e atualização STABLE/BETA por agente seguro no host;
- CI com testes, validação dos scripts, Docker build e smoke test de health.

## Compatibilidade
A migração da versão 2 continua preservada. Os diretórios persistentes `data/`, `media/`, `logs/` e o `.env` permanecem fora do Git.
