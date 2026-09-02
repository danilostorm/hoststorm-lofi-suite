# 4.0.1

HostStorm Multi Live Manager 4.0.1 — compatibilidade de providers AI.

## Corrigido
- respostas de gateways OpenAI-compatible agora são normalizadas para o contrato interno do AI Live Host mesmo quando o modelo roteado usa aliases como `mensagem`, `message`, `text`, `response`, `answer`, `content`, `output`, `result` ou `resposta` em vez de `reply`;
- `voice`, `memory_facts` e `reason` recebem defaults seguros quando o provider retorna apenas texto/resposta;
- driver Chat Completions reforça o contrato JSON solicitado ao modelo;
- teste do provider mostra também o modelo efetivamente roteado quando o gateway informa `_routed_via`/`model`;
- testes automatizados cobrem aliases e resultados JSON aninhados, incluindo o comportamento observado com FreeLLMAPI em `model:auto`.

# 4.0.0

HostStorm Multi Live Manager 4.0 — AI Live Host.

## AI Live Host
- chat unificado para Kick, Twitch e YouTube, com bindings por conta/canal;
- ingestão de chat por webhook/polling conforme a plataforma e normalização em um formato único;
- buffer variável de 15–30 segundos, ranking de mensagens, probabilidades e limites por hora para evitar comportamento robótico;
- Copilot e Autopilot, com fila de sugestões, aprovação manual, edição, envio e histórico;
- persona configurável, humanizer, respostas de tamanhos variados, emojis e perguntas de volta;
- memória de live, memória curta por viewer e contexto opcional compartilhado entre plataformas;
- cooldown global e por viewer, anti-loop, deduplicação e proteção contra prompt injection/spam/links;
- eventos de follow/sub/gifts/KICKs quando disponíveis pelas integrações oficiais;
- contexto automático da transmissão atual para o LLM;
- providers LLM configuráveis: OpenAI Responses, endpoints OpenAI-compatible, Ollama, webhook e fallback local de teste;
- AI Vision opcional por snapshots periódicos da transmissão, sem análise contínua de vídeo;
- TTS configurável com providers cloud/local/webhook;
- barramento de voz por transmissão com FFmpeg, mixagem e ducking automático do áudio principal;
- métricas, histórico, viewers/memória, regras, personas e providers em uma nova área AI HOST do painel.

## Kick / permissões
- OAuth Kick passa a solicitar também `chat:write`, `events:subscribe` e `kicks:read` além dos scopes anteriores;
- tokens continuam criptografados e o refresh automático permanece compatível;
- webhook existente pode continuar sendo utilizado para os eventos do AI Host.

## Segurança / operação
- conteúdo recebido do chat é tratado como dado não confiável e nunca como instrução privilegiada;
- segredos, stream keys, tokens OAuth e `security.key` não entram no contexto enviado ao modelo;
- assinatura visual configurável (`🤖` por padrão) identifica mensagens automatizadas;
- recursos AI ficam desacoplados do pipeline de transmissão e podem ser desligados sem afetar lives normais.

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
