from __future__ import annotations

import json
import re
import secrets
import shlex
import socket
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

import paramiko

REPO_URL = 'https://github.com/danilostorm/hoststorm-lofi-suite.git'
INSTALL_ROOT = '/opt/hoststorm-agent'


@dataclass
class BootstrapResult:
    name: str
    host: str
    port: int
    base_url: str
    agent_token: str
    tags: list[str]
    capabilities: dict


class BootstrapError(RuntimeError):
    pass


def _emit(progress: Callable[[str], None] | None, message: str):
    if progress:
        progress(str(message))


def _safe_host(value: str) -> str:
    value = str(value or '').strip()
    if not value or len(value) > 255 or re.search(r'[\s/\\;`$]', value):
        raise BootstrapError('IP/hostname SSH inválido.')
    return value


def _safe_user(value: str) -> str:
    value = str(value or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value):
        raise BootstrapError('Usuário SSH inválido.')
    return value


def _run(client: paramiko.SSHClient, command: str, *, username: str, password: str, timeout=1800) -> str:
    shell = f"sh -lc {shlex.quote(command)}"
    if username != 'root':
        shell = f"sudo -S -p '' {shell}"
    stdin, stdout, stderr = client.exec_command(shell, timeout=timeout, get_pty=True)
    if username != 'root':
        stdin.write((password or '') + '\n')
        stdin.flush()
    out = stdout.read().decode('utf-8', 'replace')
    err = stderr.read().decode('utf-8', 'replace')
    code = stdout.channel.recv_exit_status()
    if code != 0:
        tail = (err or out)[-1800:]
        raise BootstrapError(f'Comando remoto falhou ({code}): {tail}')
    return out.strip()


def _validate_base_url(value: str, host: str, agent_port: int) -> str:
    raw = str(value or '').strip() or f'http://{host}:{agent_port}'
    parsed = urlparse(raw)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise BootstrapError('URL base do Agent precisa ser http:// ou https://.')
    return raw.rstrip('/')


def _wait_agent(base_url: str, token: str, timeout_seconds=240) -> dict:
    deadline = time.time() + timeout_seconds
    last_error = ''
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                base_url + '/api/v1/status',
                headers={'Authorization': 'Bearer ' + token},
            )
            with urllib.request.urlopen(req, timeout=6) as response:
                payload = json.loads(response.read().decode('utf-8'))
                if payload.get('ok'):
                    return payload
        except Exception as exc:
            last_error = str(exc)
        time.sleep(3)
    raise BootstrapError('Agent iniciou no SSH, mas o controlador não conseguiu acessar a API. ' + last_error)


def install_agent(config: dict, progress: Callable[[str], None] | None = None) -> BootstrapResult:
    host = _safe_host(config.get('host'))
    username = _safe_user(config.get('username') or 'root')
    password = str(config.get('password') or '')
    if not password:
        raise BootstrapError('Senha SSH obrigatória para o provisionamento automático.')
    try:
        ssh_port = int(config.get('ssh_port') or 22)
        agent_port = int(config.get('agent_port') or 3040)
    except Exception:
        raise BootstrapError('Porta SSH/Agent inválida.')
    if not 1 <= ssh_port <= 65535 or not 1 <= agent_port <= 65535:
        raise BootstrapError('Porta SSH/Agent fora do intervalo permitido.')

    name = str(config.get('name') or host).strip()[:80]
    base_url = _validate_base_url(config.get('base_url'), host, agent_port)
    node_id = str(config.get('node_id') or secrets.token_hex(6))[:32]
    agent_token = 'hst_agent_' + secrets.token_urlsafe(36)
    admin_password = secrets.token_urlsafe(28)
    secret_key = secrets.token_urlsafe(48)
    controller_url = str(config.get('controller_url') or '').strip().rstrip('/')

    _emit(progress, f'Conectando por SSH em {host}:{ssh_port}…')
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=ssh_port,
            username=username,
            password=password,
            timeout=15,
            banner_timeout=20,
            auth_timeout=20,
            look_for_keys=False,
            allow_agent=False,
        )
        _emit(progress, 'SSH autenticado. Detectando sistema…')
        system_info = _run(
            client,
            "printf 'ARCH='; uname -m; printf 'OS='; (. /etc/os-release 2>/dev/null; echo ${ID:-linux}); printf 'KERNEL='; uname -r",
            username=username,
            password=password,
            timeout=30,
        )
        _emit(progress, system_info.replace('\n', ' · '))

        _emit(progress, 'Instalando Docker, Compose, Git e dependências…')
        bootstrap = r'''
set -eu
export DEBIAN_FRONTEND=noninteractive
if ! command -v curl >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y curl ca-certificates;
  elif command -v dnf >/dev/null 2>&1; then dnf install -y curl ca-certificates;
  elif command -v yum >/dev/null 2>&1; then yum install -y curl ca-certificates;
  else echo 'Gerenciador de pacotes não suportado'; exit 20; fi
fi
if ! command -v git >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y git;
  elif command -v dnf >/dev/null 2>&1; then dnf install -y git;
  elif command -v yum >/dev/null 2>&1; then yum install -y git; fi
fi
if ! command -v docker >/dev/null 2>&1; then curl -fsSL https://get.docker.com | sh; fi
docker version >/dev/null
docker compose version >/dev/null
mkdir -p /opt/hoststorm-agent/{media,data,logs}
'''
        _run(client, bootstrap, username=username, password=password)

        _emit(progress, 'Baixando HostStorm Agent e fixando a versão main…')
        repo_cmd = f'''
set -eu
mkdir -p {INSTALL_ROOT}
if [ -d {INSTALL_ROOT}/repo/.git ]; then
  cd {INSTALL_ROOT}/repo
  git fetch --prune origin
  git checkout main
  git reset --hard origin/main
else
  rm -rf {INSTALL_ROOT}/repo
  git clone --depth 1 --branch main {shlex.quote(REPO_URL)} {INSTALL_ROOT}/repo
fi
'''
        _run(client, repo_cmd, username=username, password=password)

        env_text = '\n'.join([
            'TZ=America/Sao_Paulo',
            'LV2_ADMIN_USER=agent-admin',
            f'LV2_ADMIN_PASSWORD={admin_password}',
            f'HOSTSTORM_SECRET_KEY={secret_key}',
            f'HOSTSTORM_AGENT_TOKEN={agent_token}',
            f'HOSTSTORM_NODE_ID={node_id}',
            f'HOSTSTORM_CONTROLLER_URL={controller_url}',
            f'HOSTSTORM_AGENT_PORT={agent_port}',
            '',
        ])
        _emit(progress, 'Gerando credenciais exclusivas do Agent…')
        write_env = f"umask 077; cat > {INSTALL_ROOT}/.env <<'HOSTSTORM_ENV'\n{env_text}HOSTSTORM_ENV\n"
        _run(client, write_env, username=username, password=password, timeout=30)

        _emit(progress, 'Construindo e iniciando o container HostStorm Agent…')
        start_cmd = f'''
set -eu
cd {INSTALL_ROOT}/repo
docker compose --env-file {INSTALL_ROOT}/.env -f docker-compose.agent.yml up -d --build
docker compose --env-file {INSTALL_ROOT}/.env -f docker-compose.agent.yml ps
'''
        _run(client, start_cmd, username=username, password=password, timeout=1800)

        _emit(progress, 'Detectando recursos de hardware…')
        capability_text = _run(
            client,
            "printf 'NVIDIA='; (nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true); "
            "printf '\nDRI='; ([ -d /dev/dri ] && echo yes || echo no); "
            "printf '\nCPU='; nproc; printf '\nMEM_KB='; awk '/MemTotal/{print $2}' /proc/meminfo",
            username=username,
            password=password,
            timeout=30,
        )
        caps = {'raw': capability_text}
        tags = ['agent', 'linux']
        if 'NVIDIA=' in capability_text and capability_text.split('NVIDIA=', 1)[1].split('\n', 1)[0].strip():
            tags += ['gpu', 'nvidia', 'nvenc']
        if 'DRI=yes' in capability_text:
            tags += ['dri', 'vaapi']
        arch = _run(client, 'uname -m', username=username, password=password, timeout=10).strip()
        if arch:
            tags.append(arch)

    except (paramiko.SSHException, socket.error, TimeoutError) as exc:
        raise BootstrapError('Falha na conexão SSH: ' + str(exc)) from exc
    finally:
        client.close()

    _emit(progress, f'Validando API do Agent em {base_url}…')
    status = _wait_agent(base_url, agent_token)
    caps['agent_version'] = status.get('version', '')
    _emit(progress, 'Agent online e autenticado. Provisionamento concluído.')
    return BootstrapResult(
        name=name,
        host=host,
        port=agent_port,
        base_url=base_url,
        agent_token=agent_token,
        tags=list(dict.fromkeys(tags)),
        capabilities=caps,
    )
