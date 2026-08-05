#!/usr/bin/env bash
# Provisiona o stack do bot num Ubuntu 24.04 limpo.
# Uso:  sudo bash install.sh
set -euo pipefail

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; RST=$'\e[0m'
log()  { echo "${GRN}==>${RST} $*"; }
warn() { echo "${YLW}[!]${RST} $*"; }
die()  { echo "${RED}[x]${RST} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Rode como root:  sudo bash install.sh"
cd "$(dirname "$0")"

# ---------------------------------------------------------------- Docker
if ! command -v docker >/dev/null 2>&1; then
  log "Instalando Docker..."
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
                          docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
else
  log "Docker ja instalado: $(docker --version)"
fi

# ---------------------------------------------------------------- .env
if [[ -f .env ]]; then
  warn ".env ja existe - mantendo o arquivo atual (nao vou sobrescrever seus segredos)."
else
  log "Gerando .env com senhas aleatorias..."
  gen() { openssl rand -hex 24; }

  read -rp "Dominio apontando para este servidor (ex: bot.seuprovedor.com.br): " DOMAIN
  [[ -n "$DOMAIN" ]] || die "Dominio e obrigatorio para o HTTPS funcionar."

  # Este servidor ja tem um proxy (Traefik/EasyPanel) nas portas 80/443.
  # Em vez de disputar as portas, o n8n entra na rede dele e e publicado por ele.
  TRAEFIK_CID=$(docker ps -qf name=traefik | head -1)
  if [[ -n "$TRAEFIK_CID" ]]; then
    DETECTED_NET=$(docker inspect "$TRAEFIK_CID" \
      --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' \
      | grep -v '^$' | grep -v '^bridge$' | head -1)
    log "Traefik detectado na rede: ${DETECTED_NET:-<nenhuma>}"
  fi
  read -rp "Rede do Traefik [${DETECTED_NET:-}]: " TRAEFIK_NETWORK
  TRAEFIK_NETWORK=${TRAEFIK_NETWORK:-$DETECTED_NET}
  [[ -n "$TRAEFIK_NETWORK" ]] || die "Sem rede do Traefik nao da para publicar o n8n."
  read -rp "Entrypoint HTTPS do Traefik [websecure]: " TRAEFIK_ENTRYPOINT
  TRAEFIK_ENTRYPOINT=${TRAEFIK_ENTRYPOINT:-websecure}
  read -rp "Certresolver do Traefik [letsencrypt]: " TRAEFIK_CERTRESOLVER
  TRAEFIK_CERTRESOLVER=${TRAEFIK_CERTRESOLVER:-letsencrypt}

  read -rp "URL do SGP (ex: https://seuprovedor.sgp.net.br): " SGP_API_URL
  read -rp "Token da API do SGP: " SGP_API_TOKEN
  read -rp "Appname cadastrado junto ao token no SGP: " SGP_APP_NAME

  cat > .env <<EOF
# ---- gerado por install.sh em $(date -Is) ----
DOMAIN=${DOMAIN}

# Proxy que ja existe no servidor e publica o n8n (nao criado por este compose)
TRAEFIK_NETWORK=${TRAEFIK_NETWORK}
TRAEFIK_ENTRYPOINT=${TRAEFIK_ENTRYPOINT}
TRAEFIK_CERTRESOLVER=${TRAEFIK_CERTRESOLVER}

POSTGRES_DB=botsgp
POSTGRES_USER=botsgp
POSTGRES_PASSWORD=$(gen)

N8N_ENCRYPTION_KEY=$(gen)
N8N_USER=admin
N8N_PASSWORD=$(gen)

EVOLUTION_INSTANCE=principal
EVOLUTION_API_KEY=$(gen)

SGP_API_URL=${SGP_API_URL}
SGP_API_TOKEN=${SGP_API_TOKEN}
SGP_APP_NAME=${SGP_APP_NAME}
EOF
  chmod 600 .env
  log ".env criado (permissao 600)."
fi

# ---------------------------------------------------------------- Firewall
log "Configurando firewall (libera apenas SSH, HTTP e HTTPS)..."
apt-get install -y -qq ufw >/dev/null
ufw --force reset >/dev/null
ufw default deny incoming  >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp  >/dev/null
ufw allow 80/tcp  >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
log "Firewall ativo. Postgres e Evolution NAO estao expostos na internet."

# ---------------------------------------------------------------- Subir
log "Subindo os containers..."
docker compose pull -q
docker compose up -d

log "Aguardando o n8n responder..."
for i in $(seq 1 60); do
  if docker compose exec -T n8n wget -qO- http://localhost:5678/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 3
  [[ $i -eq 60 ]] && warn "n8n demorou para subir - veja: docker compose logs n8n"
done

# shellcheck disable=SC1091
source .env

cat <<EOF

${GRN}================== PRONTO ==================${RST}

  n8n:     https://${DOMAIN}
  usuario: ${N8N_USER}
  senha:   ${N8N_PASSWORD}

Proximos passos:

  1. Acesse o n8n e importe  n8n/workflow-wifi-selfservice.json
  2. Crie a credencial Postgres no n8n:
       host=postgres  porta=5432  database=${POSTGRES_DB}
       usuario=${POSTGRES_USER}  senha=(veja POSTGRES_PASSWORD no .env)
     e associe aos nodes "Get Session", "Upsert Session" e "Gravar Auditoria".
  3. Conecte o WhatsApp (gera o QR Code):
       bash conectar-whatsapp.sh
  4. Ative o workflow e mande "1" para o numero conectado.

Segredos ficam em .env (chmod 600). ${YLW}Faca backup dele.${RST}
EOF
