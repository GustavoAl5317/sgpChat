#!/usr/bin/env bash
# Migra a instancia da Evolution de WHATSAPP-BAILEYS (WhatsApp Web) para o
# Cloud API OFICIAL da Meta (WHATSAPP-BUSINESS). O fluxo do n8n e o painel NAO
# mudam - a Evolution normaliza o webhook e o envio.
#
# ANTES de rodar, preencha no .env os 3 dados da Meta:
#   META_TOKEN=EAAB...        (token permanente do Usuario do Sistema)
#   META_PHONE_ID=1234567890  (Phone Number ID do numero)
#   META_WABA_ID=1234567890   (WhatsApp Business Account ID)
#
# Uso:  bash migrar-evolution-cloud.sh
set -uo pipefail
cd "$(dirname "$0")"

GRN=$'\e[32m'; YLW=$'\e[33m'; RED=$'\e[31m'; RST=$'\e[0m'
log()  { echo "${GRN}==>${RST} $*"; }
warn() { echo "${YLW}[!]${RST} $*"; }
die()  { echo "${RED}[x]${RST} $*"; exit 1; }

[[ -f .env ]] || die ".env nao encontrado. Rode de dentro de ~/sgpChat/sgpChat"
set -a; source .env; set +a

: "${EVOLUTION_INSTANCE:?falta EVOLUTION_INSTANCE no .env}"
: "${EVOLUTION_API_KEY:?falta EVOLUTION_API_KEY no .env}"
: "${META_TOKEN:?falta META_TOKEN no .env (token permanente da Meta)}"
: "${META_PHONE_ID:?falta META_PHONE_ID no .env (Phone Number ID)}"
: "${META_WABA_ID:?falta META_WABA_ID no .env (WABA ID)}"

NET=$(docker inspect botsgp-evolution \
      --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')

api() {
  local method="$1" path="$2" data="${3:-}"
  local args=(-s --max-time 30 -H "apikey: ${EVOLUTION_API_KEY}" \
              -H "Content-Type: application/json" -X "$method")
  [[ -n "$data" ]] && args+=(-d "$data")
  docker run --rm --network "$NET" curlimages/curl:latest \
    "${args[@]}" "http://botsgp-evolution:8080${path}" 2>/dev/null || true
}

warn "Isso APAGA a instancia '${EVOLUTION_INSTANCE}' (Baileys) e recria no Cloud API."
read -rp "Continuar? [s/N]: " GO
[[ "${GO,,}" == "s" ]] || { echo "cancelado"; exit 0; }

log "Encerrando e apagando a instancia atual..."
api DELETE "/instance/logout/${EVOLUTION_INSTANCE}" >/dev/null; sleep 2
api DELETE "/instance/delete/${EVOLUTION_INSTANCE}" >/dev/null; sleep 3

# Cloud API: token = token da Meta, number = Phone Number ID, businessId = WABA.
# Se a Evolution recusar algum campo, a resposta abaixo diz qual - a gente ajusta.
log "Criando a instancia no Cloud API (WHATSAPP-BUSINESS)..."
RESP=$(api POST "/instance/create" "{
  \"instanceName\": \"${EVOLUTION_INSTANCE}\",
  \"integration\": \"WHATSAPP-BUSINESS\",
  \"token\": \"${META_TOKEN}\",
  \"number\": \"${META_PHONE_ID}\",
  \"businessId\": \"${META_WABA_ID}\"
}")
echo "$RESP" | head -c 600 | sed 's/^/    /'; echo

# Webhook para o n8n (o WEBHOOK_GLOBAL_URL do compose ja cobre, mas reforcamos
# por instancia para garantir os eventos de mensagem).
log "Apontando o webhook da instancia para o n8n..."
api POST "/webhook/set/${EVOLUTION_INSTANCE}" "{
  \"webhook\": { \"enabled\": true,
    \"url\": \"http://n8n:5678/webhook/evolution-inbound\",
    \"events\": [\"MESSAGES_UPSERT\"] } }" | head -c 300 | sed 's/^/    /'; echo

cat <<EOF

${GRN}== Falta configurar o webhook DO LADO DA META ==${RST}
No app da Meta (developers.facebook.com) -> WhatsApp -> Configuracao -> Webhook:

  Callback URL : https://${EVOLUTION_DOMAIN:-<EVOLUTION_DOMAIN>}/webhook/meta
  Verify Token : ${EVOLUTION_API_KEY}
  Assinar o campo (Webhook fields): ${YLW}messages${RST}

(Se a Evolution usar outro caminho de callback nesta versao, a resposta do
 create acima ou os logs mostram o certo - me manda que eu confirmo.)

Teste:
  1) manda "oi" do seu numero pro numero da Meta -> o bot responde o menu.
  2) docker compose logs -f evolution   (pra ver a mensagem chegando)
EOF
