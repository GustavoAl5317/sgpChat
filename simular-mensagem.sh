#!/usr/bin/env bash
# Injeta uma mensagem direto no webhook do n8n, no mesmo formato que a
# Evolution envia. Serve para separar "o fluxo esta quebrado" de "a Evolution
# nao esta entregando o evento".
#
# Uso:  bash simular-mensagem.sh <telefone> <texto>
# Ex.:  bash simular-mensagem.sh 5511939146680 2
set -uo pipefail
cd "$(dirname "$0")"

[[ -f .env ]] || { echo "[x] .env nao encontrado. Rode de dentro de ~/sgpChat/sgpChat"; exit 1; }
set -a; source .env; set +a

PHONE="${1:-}"; TEXT="${2:-}"
[[ -n "$PHONE" && -n "$TEXT" ]] || { echo "uso: bash simular-mensagem.sh <telefone> <texto>"; exit 1; }

NET=$(docker inspect botsgp-n8n --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')

read -r -d '' BODY <<EOF || true
{"event":"messages.upsert","instance":"${EVOLUTION_INSTANCE}",
 "data":{"key":{"remoteJid":"${PHONE}@s.whatsapp.net","fromMe":false,"id":"SIMULADO$(date +%s)"},
         "pushName":"Teste","message":{"conversation":"${TEXT}"},
         "messageType":"conversation","messageTimestamp":$(date +%s),"source":"ios"},
 "date_time":"$(date -Iseconds)","sender":"${PHONE}@s.whatsapp.net"}
EOF

echo "==> Enviando \"${TEXT}\" como se viesse de ${PHONE}"
ANTES=$(docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
        "SELECT COALESCE(MAX(id),0) FROM execution_entity;" | tr -d '[:space:]')

docker run --rm --network "$NET" curlimages/curl:latest -s -o /dev/null -w "    HTTP %{http_code}\n" \
  -m 30 -X POST "http://botsgp-n8n:5678/webhook/evolution-inbound" \
  -H 'Content-Type: application/json' -d "$BODY"

sleep 4
echo
echo "==> Execucao gerada:"
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT e.id||' | '||e.status||' | nodes: '||COALESCE((regexp_match(d.data,'\{\"Webhook Evolution API\":[^}]*\}'))[1],'?')
   FROM execution_entity e JOIN execution_data d ON d.\"executionId\"=e.id
   WHERE e.id > ${ANTES} ORDER BY e.id DESC;" | sed 's/^/    /'

echo
echo "==> Sessao apos a mensagem:"
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT phone||' | '||step||' | '||data FROM wa_sessions WHERE phone = '${PHONE}';" | sed 's/^/    /'

echo
echo "Se o node 'Evolution - Enviar Resposta' aparecer acima, o fluxo esta ok"
echo "e voce deve ter recebido a mensagem no WhatsApp."
