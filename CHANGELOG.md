# 3.3.0

HostStorm Multi Live Manager 3.3 — Kick OAuth & Schedule Timing.

## Novo / corrigido
- integração Kick passa a usar o fluxo oficial OAuth 2.1 Authorization Code + PKCE como método recomendado;
- uma única aplicação Kick pode autorizar contas no HostStorm; não é necessário criar uma aplicação nova por canal;
- access token e refresh token da Kick são armazenados criptografados e renovados automaticamente antes de expirar;
- escopos solicitados pela automação Kick: `user:read channel:read channel:write`;
- callback padrão do HostStorm: `/api/kick/oauth/callback`, preferencialmente fixado por `HOSTSTORM_PUBLIC_URL`;
- integração Kick pode ser editada e reautorizada sem remover/recriar o cadastro;
- botão `Testar API` agora usa o verificador atual também para Kick;
- modo de token manual permanece disponível apenas como compatibilidade;
- análise de URL/YouTube passa a preencher automaticamente o limite de segurança em minutos a partir da duração detectada;
- a duração exata em segundos continua sendo a fonte principal do encerramento: o limite em minutos funciona apenas como teto de segurança.

# 3.2.0

HostStorm Multi Live Manager 3.2 — Account & Broadcast Automation.

## Novo
- Passkeys/WebAuthn como login sem senha, compatível com Windows Hello, Android, iPhone/Face ID/Touch ID e chaves FIDO2;
- TOTP continua disponível e pode coexistir com Passkeys;
- Integrações expandidas para Twitch, YouTube, Kick e Webhook/API externa;
- painel de integrações mostra capacidades por conta e mantém tokens criptografados no SQLite;
- agendamentos podem selecionar uma ou mais contas conectadas para automação de metadados;
- título, categoria/jogo e descrição são opcionais: campos vazios não alteram o valor da plataforma;
- variáveis de título/descrição `{source}`, `{channel}`, `{date}` e `{time}`;
- busca de categoria na API da primeira conta selecionada;
- atualização de metadados acontece antes do início do FFmpeg, inclusive quando a live será delegada a outro nó;
- Twitch: título e categoria/jogo via Helix `Modify Channel Information`;
- YouTube: título, descrição e categoria do broadcast ativo/próximo;
- Kick: título e categoria pelo Public API, usando token OAuth com `channel:write`;
- política por agenda para continuar a live caso a API falhe ou bloquear o início até corrigir a integração;
- relatório da última automação fica visível dentro do agendamento.

## Segurança / compatibilidade
- Passkeys exigem contexto seguro HTTPS; `HOSTSTORM_PUBLIC_URL` pode fixar a URL pública atrás do proxy reverso;
- credenciais WebAuthn são armazenadas por usuário no SQLite; a chave privada do autenticador nunca é enviada ao HostStorm;
- migração adiciona os campos da automação aos agendamentos existentes sem recriar o banco;
- agendas antigas continuam funcionando sem automação até que ela seja explicitamente ativada.

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
