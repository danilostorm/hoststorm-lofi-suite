# 4.0.4

HostStorm Multi Live Manager 4.0.4 — agendamentos concorrentes e recovery correto de fontes AO VIVO.

## Agendamentos / plataformas
- uma plataforma salva sem RTMP/chave válida deixa de cancelar toda a agenda;
- destinos inválidos são ignorados quando existe pelo menos uma plataforma válida para iniciar;
- o auditor registra `schedule_platform_ignored` com os destinos descartados;
- se nenhuma plataforma válida restar, a mensagem passa a indicar claramente quais destinos precisam ser configurados;
- a correção vale para agendas por URL e por Biblioteca, inclusive `Executar agora` e agendas antigas já salvas.

## Recovery de URL ao vivo
- fontes VOD continuam retomando do checkpoint com `-ss`;
- fontes que o yt-dlp identifica como transmissão realmente AO VIVO deixam de receber seek absoluto durante recovery;
- após queda/restart, uma fonte AO VIVO volta ao live edge atual, evitando loops de EOF/recovery causados por seek acumulado;
- detecção usa `is_live`/`live_status` do yt-dlp e é reavaliada a cada novo start/retry.

## Testes
- cobertura para agenda com Kick válido + Custom RTMP vazio, garantindo que Kick inicia e Custom é apenas ignorado;
- cobertura para falha quando todos os destinos estão sem RTMP/chave;
- cobertura para remoção de seek em fonte AO VIVO e manutenção do comportamento VOD.

# 4.0.3

HostStorm Multi Live Manager 4.0.3 — Live Recovery Engine e qualidade máxima de fonte.

## Recovery / continuidade
- lives passam a persistir checkpoints de reprodução no SQLite durante a transmissão;
- queda de internet, encerramento inesperado do FFmpeg ou reinício do container/servidor pode retomar a mídia a partir do último ponto salvo em vez de começar do zero;
- checkpoints guardam canal, sessão, fonte, plataformas, mídia, agendamento, posição, duração, qualidade e contagem de recoveries;
- fontes locais em loop retomam no ponto equivalente dentro do arquivo/playlist, preservando também o tempo total já exibido;
- agendamentos URL e Biblioteca são reconstruídos após restart quando ainda possuem conteúdo restante;
- parada manual/fim normal desabilita a retomada daquele checkpoint para não ressuscitar uma live encerrada intencionalmente;
- checkpoints abandonados expiram por segurança, configurável por `HOSTSTORM_RECOVERY_MAX_AGE_HOURS`;
- intervalo de checkpoint configurável por `HOSTSTORM_RECOVERY_CHECKPOINT_SECONDS` (padrão 5s);
- status runtime passa a expor posição recuperável, qualidade da fonte, número de recoveries e último motivo.

## Rede / FFmpeg
- inputs HTTP recebem opções de reconexão do FFmpeg (`reconnect`, `reconnect_streamed`, `reconnect_delay_max`);
- recovery de plataforma força nova resolução de URLs assinadas do YouTube/DASH antes de reiniciar o encoder;
- seek de retomada é aplicado somente aos inputs da fonte, sem deslocar áudio externo configurado no canal;
- backoff existente do supervisor continua em 5/15/30/60s e agora trabalha em conjunto com o checkpoint persistente.

## Qualidade de fonte
- removido o teto artificial de 1080p da resolução yt-dlp;
- fontes URL passam a priorizar `bv*+ba/b`, pegando o melhor vídeo e o melhor áudio disponíveis antes dos fallbacks compatíveis;
- continua suportado vídeo/áudio separados quando o YouTube não oferece formato progressivo muxado;
- a qualidade da fonte fica registrada como máxima automática; o perfil de saída ainda pode redimensionar/transcodificar para a resolução configurada da plataforma.

## Testes
- testes automatizados cobrem persistência de checkpoint, stop sem auto-resume, cálculo de tempo restante, seek/reconnect HTTP, loop local e seleção yt-dlp sem limite de 1080p.

# 4.0.2

HostStorm Multi Live Manager 4.0.2 — robustez de fontes remotas/YouTube.

## Corrigido
- agendamentos por URL deixam de depender exclusivamente de um formato progressivo já muxado do YouTube;
- resolução com yt-dlp tenta primeiro A/V único até 1080p e faz fallback para vídeo-only + áudio-only quando necessário;
- FFmpeg recebe os dois inputs separados e mapeia corretamente vídeo e áudio;
- áudio externo configurado no canal continua tendo prioridade quando existe;
- URLs assinadas resolvidas ficam em cache curto e são atualizadas durante recovery de lives longas;
- falhas ao preparar a fonte deixam de escapar como HTTP 500 no botão `Executar agora`;
- o scheduler passa a registrar uma falha de resolução como uma tentativa concluída, evitando repetir o mesmo erro a cada ciclo de ~10 segundos;
- testes automatizados cobrem o fallback A/V separado e o mapeamento de áudio.

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
