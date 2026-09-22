#!/usr/bin/env bash
# Cria uma NOVA instancia da Evolution no Cloud API (WHATSAPP-BUSINESS) para um
# numero, SEM mexer nas instancias existentes. Use para separar em 2 numeros:
# um segundo numero (ex.: agente 9325) na MESMA WABA.
#
# Pre-requisitos:
#  - o numero ja registrado na WABA (Meta) e com Phone Number ID em maos;
#  - .env com META_TOKEN, META_WABA_ID e EVOLUTION_API_KEY.
#
# Uso:  bash criar-instancia-cloud.sh <nome-da-instancia> <PHONE_NUMBER_ID>
#   ex: bash criar-instancia-cloud.sh agente 111122223333
set -uo pipefail
cd "$(dirname "$0")"

GRN=$'\e[32m'; YLW=$'\e[33m'; RED=$'\e[31m'; RST=$'\e[0m'
die() { echo "${RED}[x]${RST} $*"; exit 1; }

INST="${1:-}"; PHONE_ID="${2:-}"
[[ -n "$INST" && -n "$PHONE_ID" ]] || die "uso: bash criar-instancia-cloud.sh <nome> <PHONE_NUMBER_ID>"

[[ -f .env ]] || die ".env nao encontrado. Rode de dentro de ~/sgpChat/sgpChat"
set -a; source .env; set +a
: "${EVOLUTION_API_KEY:?falta EVOLUTION_API_KEY no .env}"
: "${META_TOKEN:?falta META_TOKEN no .env}"
: "${META_WABA_ID:?falta META_WABA_ID no .env}"

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

echo "${GRN}==>${RST} Criando a instancia '${INST}' no Cloud API (numero ${PHONE_ID})..."
RESP=$(api POST "/instance/create" "{
  \"instanceName\": \"${INST}\",
  \"integration\": \"WHATSAPP-BUSINESS\",
  \"token\": \"${META_TOKEN}\",
  \"number\": \"${PHONE_ID}\",
  \"businessId\": \"${META_WABA_ID}\"
}")
echo "$RESP" | head -c 600 | sed 's/^/    /'; echo

echo "${GRN}==>${RST} Apontando o webhook da instancia para o n8n..."
api POST "/webhook/set/${INST}" "{
  \"webhook\": { \"enabled\": true,
    \"url\": \"http://n8n:5678/webhook/evolution-inbound\",
    \"events\": [\"MESSAGES_UPSERT\"] } }" | head -c 300 | sed 's/^/    /'; echo

cat <<EOF

${GRN}== Instancia '${INST}' criada ==${RST}
O webhook DA META ja esta configurado no app (callback /webhook/meta, verify
token "evolution") e vale para TODOS os numeros da WABA - nao precisa refazer.
Confirme que a WABA esta assinada no app (subscribed_apps) e que o campo
'messages' esta assinado.

Teste: mande "oi" para o numero ${PHONE_ID} -> o bot responde o menu de entrada.
O bot responde na instancia de onde a mensagem veio (numero certo).
EOF
