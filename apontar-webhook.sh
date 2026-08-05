#!/usr/bin/env bash
# Aponta o webhook da instancia da Evolution para o workflow do n8n.
#
# A variavel WEBHOOK_GLOBAL_URL do compose so vale para instancias criadas
# depois dela. Se a instancia ja existia, e preciso configurar via API - e
# isso muda de formato entre versoes da Evolution, por isso tentamos os dois.
#
# Uso:  bash apontar-webhook.sh
set -uo pipefail
cd "$(dirname "$0")"

GRN=$'\e[32m'; RED=$'\e[31m'; RST=$'\e[0m'
[[ -f .env ]] || { echo "[x] .env nao encontrado."; exit 1; }
set -a; source .env; set +a

DEST="http://n8n:5678/webhook/evolution-inbound"
NET=$(docker inspect botsgp-evolution --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')
API="http://botsgp-evolution:8080"

post() {
  docker run --rm --network "$NET" curlimages/curl:latest -s -m 20 \
    -H "apikey: ${EVOLUTION_API_KEY}" -H "Content-Type: application/json" \
    -X POST -d "$2" "${API}$1" 2>/dev/null
}

echo "==> Apontando a instancia '${EVOLUTION_INSTANCE}' para ${DEST}"

# Formato v2: objeto aninhado em "webhook"
R=$(post "/webhook/set/${EVOLUTION_INSTANCE}" "{\"webhook\":{\"enabled\":true,\"url\":\"${DEST}\",\"byEvents\":false,\"base64\":false,\"events\":[\"MESSAGES_UPSERT\"]}}")
echo "    v2: ${R:0:200}"

if ! echo "$R" | grep -qi 'evolution-inbound'; then
  # Formato v1: campos na raiz
  R=$(post "/webhook/set/${EVOLUTION_INSTANCE}" "{\"enabled\":true,\"url\":\"${DEST}\",\"webhook_by_events\":false,\"events\":[\"MESSAGES_UPSERT\"]}")
  echo "    v1: ${R:0:200}"
fi

echo
echo "==> Conferindo..."
V=$(docker run --rm --network "$NET" curlimages/curl:latest -s -m 15 \
     -H "apikey: ${EVOLUTION_API_KEY}" "${API}/webhook/find/${EVOLUTION_INSTANCE}" 2>/dev/null)
echo "    ${V:0:300}"

if echo "$V" | grep -q 'evolution-inbound'; then
  echo
  echo "${GRN}Pronto. Mande uma mensagem no WhatsApp para testar.${RST}"
else
  echo
  echo "${RED}Nao confirmou. Veja:  docker compose logs --tail 40 evolution${RST}"
  exit 1
fi
