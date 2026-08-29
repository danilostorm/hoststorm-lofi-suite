# HostStorm Lo-fi Suite

Suite para criação de loops longos e gerenciamento de múltiplas lives RTMP.

## Serviços

- **Loop Studio** — porta `3035`
- **Multi Live Manager** — porta `3040`

O Multi Live Manager inclui lives 24/7 e **Lives Agendadas** por dia da semana, horário, vídeo da biblioteca e **plataformas específicas para cada agenda**. Exemplo: uma agenda pode transmitir somente na Twitch, enquanto outra usa Kick + YouTube. A seleção da agenda não altera os destinos usados pela live manual.

Para cada agendamento local, o sistema mede a duração do vídeo com `ffprobe` e encerra a transmissão **60 segundos antes do fim**.

## Como o código é versionado

Os arquivos de aplicação e templates são armazenados em bundles Base85 compactados:

- `loop-studio/app.py.gz.b85`
- `loop-studio/templates.tar.gz.b85`
- `multi-live/app.py.gz.b85`
- `multi-live/templates.tar.gz.b85`

Os Dockerfiles reconstruem `app.py` e `templates/` automaticamente durante `docker compose build`. Isso também evita conflito com cópias antigas que já existam no diretório do Unraid.

Para reconstruir os arquivos localmente para inspeção, use:

```bash
python3 scripts/unpack-source.py
```

## Dados persistentes

As pastas abaixo **não são versionadas** e permanecem no servidor durante `git pull`:

- `loop-studio/uploads/`
- `loop-studio/outputs/`
- `loop-studio/logos/`
- `multi-live/media/`
- `multi-live/data/`
- `multi-live/logs/`

O arquivo `.env` também não é versionado.

## Vincular a instalação existente do Unraid ao GitHub

O diretório usado no servidor é:

```bash
cd /mnt/user/appdata/hoststorm-lofi-suite
```

Antes da primeira sincronização, faça pelo menos um backup das configurações/dados importantes. Em seguida:

```bash
cd /mnt/user/appdata/hoststorm-lofi-suite

git init
git branch -M main
git remote remove origin 2>/dev/null || true
git remote add origin https://github.com/danilostorm/hoststorm-lofi-suite.git
git fetch origin
git reset --hard origin/main
```

As pastas de mídia, dados, logs e outputs são ignoradas pelo Git e não são apagadas por essa sincronização.

## Configurar login

Depois da primeira sincronização:

```bash
cd /mnt/user/appdata/hoststorm-lofi-suite
cp -n .env.example .env
nano .env
```

No `.env`, configure:

```env
LV2_ADMIN_USER=admin
LV2_ADMIN_PASSWORD=SUA_SENHA_FORTE
```

Você pode usar a mesma senha que já utilizava no painel ou definir uma nova. O `.env` não deve ser enviado ao GitHub.

## Subir a versão nova

```bash
cd /mnt/user/appdata/hoststorm-lofi-suite
docker compose up -d --build
```

Verifique:

```bash
docker compose ps
docker compose logs --tail=100 multi-live
```

## Atualizações futuras

Depois dessa configuração inicial, a atualização fica simples:

```bash
cd /mnt/user/appdata/hoststorm-lofi-suite
git pull --ff-only
docker compose up -d --build
```

Para acompanhar os logs:

```bash
docker compose logs --tail=100 -f
```

## Copiar outputs do Loop Studio para o Multi Live

```bash
bash scripts/copy-loop-outputs-to-multi.sh
```

O script usa por padrão `/mnt/user/appdata/hoststorm-lofi-suite`. Se necessário, é possível trocar a raiz com a variável `HOSTSTORM_BASE`.

## Importante

Nunca envie `.env`, `multi-live/data/`, mídia, logs ou arquivos contendo chaves RTMP ao repositório.
