# HostStorm Lo-fi Suite

Suite para criação de loops longos e gerenciamento de múltiplas lives RTMP.

## Serviços

- **Loop Studio** — porta `3035`
- **Multi Live Manager** — porta `3040`

O Multi Live Manager inclui lives 24/7 e **Lives Agendadas** por dia da semana, horário e vídeo da biblioteca. Para cada agendamento local, o sistema mede a duração do vídeo com `ffprobe` e encerra a transmissão 60 segundos antes do fim.

## Dados persistentes

As pastas abaixo **não são versionadas** e permanecem no servidor durante `git pull`:

- `loop-studio/uploads/`
- `loop-studio/outputs/`
- `loop-studio/logos/`
- `multi-live/media/`
- `multi-live/data/`
- `multi-live/logs/`

O arquivo `.env` também não é versionado.

## Primeira configuração no Unraid

Diretório recomendado:

```bash
cd /mnt/user/appdata/hoststorm-lofi-suite
```

Crie o arquivo `.env` antes de subir os containers:

```bash
cp .env.example .env
nano .env
```

Defina uma senha forte em `LV2_ADMIN_PASSWORD`.

Depois:

```bash
docker compose up -d --build
```

## Atualização

Depois que o diretório já estiver ligado a este repositório:

```bash
cd /mnt/user/appdata/hoststorm-lofi-suite
git pull --ff-only
docker compose up -d --build
```

Para acompanhar os logs:

```bash
docker compose logs --tail=100 -f
```

## Importante

Nunca envie `.env`, `multi-live/data/`, mídia, logs ou arquivos contendo chaves RTMP ao repositório.
