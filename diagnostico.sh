#!/usr/bin/env bash
# Checa a cadeia inteira: WhatsApp -> Evolution -> webhook do n8n -> Postgres.
# Uso:  bash diagnostico.sh
set -uo pipefail
cd "$(dirname "$0")"

GRN=$'\e[32m'; YLW=$'\e[33m'; RED=$'\e[31m'; RST=$'\e[0m'
ok()   { echo "  ${GRN}OK${RST}    $*"; }
bad()  { echo "  ${RED}FALHA${RST} $*"; }
warn() { echo "  ${YLW}?${RST}     $*"; }
sec()  { echo; echo "=== $* ==="; }

set -a; source .env 2>/dev/null; set +a
NET=$(docker inspect botsgp-n8n --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null | awk '{print $1}')
CURL() { docker run --rm --network "$NET" curlimages/curl:latest "$@" 2>/dev/null; }
PSQL() { docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1" 2>/dev/null | tr -d '\r'; }

sec "1. Containers"
for c in botsgp-postgres botsgp-redis botsgp-n8n botsgp-evolution; do
  S=$(docker inspect "$c" --format '{{.State.Status}}' 2>/dev/null || echo ausente)
  [[ "$S" == "running" ]] && ok "$c" || bad "$c ($S)"
done

sec "2. Workflow no n8n"
ATIVOS=$(PSQL "SELECT id||' | '||name FROM workflow_entity WHERE active = true;")
[[ -n "$ATIVOS" ]] && { ok "workflow(s) ativo(s):"; echo "$ATIVOS" | sed 's/^/          /'; } \
                   || bad "nenhum workflow ativo"

ROTAS=$(PSQL "SELECT method||' '||\"webhookPath\" FROM webhook_entity;")
if echo "$ROTAS" | grep -q '^POST evolution-inbound$'; then
  ok "rota registrada: POST evolution-inbound"
elif [[ -n "$ROTAS" ]]; then
  bad "rota registrada com caminho errado:"; echo "$ROTAS" | sed 's/^/          /'
  echo "          -> falta webhookId no node. Rode: bash configurar-n8n.sh"
else
  bad "nenhuma rota de webhook registrada"
fi

sec "3. Webhook responde?"
CODE=$(CURL -s -o /dev/null -w '%{http_code}' -m 10 -X POST \
  "http://botsgp-n8n:5678/webhook/evolution-inbound" -H 'Content-Type: application/json' -d '{}')
case "$CODE" in
  200) ok "HTTP 200 - webhook no ar" ;;
  404) bad "HTTP 404 - workflow inativo ou rota errada" ;;
  *)   bad "HTTP $CODE" ;;
esac

sec "4. Evolution -> WhatsApp"
ST=$(CURL -s -m 15 -H "apikey: ${EVOLUTION_API_KEY}" \
     "http://botsgp-evolution:8080/instance/connectionState/${EVOLUTION_INSTANCE}")
echo "$ST" | grep -q '"open"' && ok "WhatsApp conectado" || bad "WhatsApp NAO conectado: ${ST:0:160}"

sec "5. Para onde a Evolution manda os eventos"
WH=$(CURL -s -m 15 -H "apikey: ${EVOLUTION_API_KEY}" \
     "http://botsgp-evolution:8080/webhook/find/${EVOLUTION_INSTANCE}")
echo "          ${WH:0:300}"
if echo "$WH" | grep -q 'evolution-inbound'; then
  ok "aponta para o webhook do n8n"
else
  bad "a instancia NAO tem webhook apontando para o n8n"
  echo "          -> corrija com: bash apontar-webhook.sh"
fi

sec "6. Execucoes recentes do n8n"
EXECS=$(PSQL "SELECT COUNT(*) FROM execution_entity;")
echo "          total de execucoes: ${EXECS:-0}"
if [[ "${EXECS:-0}" != "0" ]]; then
  PSQL "SELECT \"startedAt\"||' | '||status FROM execution_entity ORDER BY \"startedAt\" DESC LIMIT 5;" \
    | sed 's/^/          /'
fi

sec "7. Sessoes gravadas"
PSQL "SELECT phone||' | '||step FROM wa_sessions ORDER BY updated_at DESC LIMIT 5;" | sed 's/^/          /'

sec "8. Ultimos erros"
docker compose logs --tail 200 n8n 2>/dev/null | grep -iE 'error|unknown webhook|refused' | tail -5 | sed 's/^/  n8n  /'
docker compose logs --tail 200 evolution 2>/dev/null | grep -iE 'error|refused|webhook' | tail -5 | sed 's/^/  evo  /'
echo
