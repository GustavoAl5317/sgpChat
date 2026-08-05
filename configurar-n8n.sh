#!/usr/bin/env bash
# Importa o workflow, cria a credencial do Postgres e ativa - tudo pelo CLI do
# n8n, sem precisar abrir a interface web.
#
# Uso:  bash configurar-n8n.sh
set -euo pipefail
cd "$(dirname "$0")"

GRN=$'\e[32m'; YLW=$'\e[33m'; RED=$'\e[31m'; RST=$'\e[0m'
log()  { echo "${GRN}==>${RST} $*"; }
warn() { echo "${YLW}[!]${RST} $*"; }
die()  { echo "${RED}[x]${RST} $*" >&2; exit 1; }

[[ -f .env ]] || die ".env nao encontrado. Rode install.sh antes."
# set -a exporta tudo que vier do .env: sem isso o Python chamado abaixo
# nao enxerga as variaveis (source define so no shell atual).
set -a
# shellcheck disable=SC1091
source .env
set +a
: "${POSTGRES_DB:?falta POSTGRES_DB no .env}"
: "${POSTGRES_USER:?falta POSTGRES_USER no .env}"
: "${POSTGRES_PASSWORD:?falta POSTGRES_PASSWORD no .env}"

WF_SRC="n8n/workflow-wifi-selfservice.json"
[[ -f "$WF_SRC" ]] || die "$WF_SRC nao encontrado."

CRED_ID="botsgpPostgres"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

# ---- credencial do Postgres --------------------------------------------
# O n8n cifra o conteudo na importacao usando N8N_ENCRYPTION_KEY.
log "Montando a credencial do Postgres..."
python3 - "$TMP/cred.json" <<PY
import json, os, sys
json.dump([{
    "id": "${CRED_ID}",
    "name": "Postgres - botSgp",
    "type": "postgres",
    "data": {
        "host": "postgres",
        "port": 5432,
        "database": os.environ["POSTGRES_DB"],
        "user": os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
        "allowUnauthorizedCerts": False,
        "ssl": "disable",
    },
}], open(sys.argv[1], "w"))
PY

# ---- workflow, ja apontando para a credencial --------------------------
log "Preparando o workflow..."
python3 - "$WF_SRC" "$TMP/wf.json" "$CRED_ID" <<'PY'
import json, sys
src, dst, cred = sys.argv[1], sys.argv[2], sys.argv[3]
wf = json.load(open(src, encoding="utf-8"))
n = 0
for node in wf["nodes"]:
    c = node.get("credentials", {}).get("postgres")
    if c:
        c["id"] = cred          # troca o placeholder REPLACE_ME
        n += 1
json.dump(wf, open(dst, "w", encoding="utf-8"), ensure_ascii=False)
print("    nodes de Postgres apontados para a credencial:", n)
PY

# ---- importar ----------------------------------------------------------
docker cp "$TMP/cred.json" botsgp-n8n:/tmp/cred.json
docker cp "$TMP/wf.json"   botsgp-n8n:/tmp/wf.json

log "Importando credencial..."
docker compose exec -T -u node n8n n8n import:credentials --input=/tmp/cred.json \
  || die "falha ao importar a credencial"

log "Importando workflow..."
docker compose exec -T -u node n8n n8n import:workflow --input=/tmp/wf.json \
  || die "falha ao importar o workflow"

docker compose exec -T n8n rm -f /tmp/cred.json /tmp/wf.json 2>/dev/null || true

# ---- ativar ------------------------------------------------------------
WF_NAME=$(python3 -c "import json;print(json.load(open('$WF_SRC',encoding='utf-8'))['name'])")
WF_ID=$(docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT id FROM workflow_entity WHERE name = \$\$${WF_NAME}\$\$ ORDER BY \"updatedAt\" DESC LIMIT 1;" \
  2>/dev/null | tr -d '[:space:]')

[[ -n "$WF_ID" ]] || die "nao encontrei o workflow no banco apos importar"
log "Workflow id: $WF_ID"

log "Ativando..."
docker compose exec -T -u node n8n n8n update:workflow --id="$WF_ID" --active=true \
  || warn "o comando de ativacao falhou - ative pelo toggle na interface"

# O webhook so passa a responder depois que o n8n recarrega os workflows ativos.
log "Reiniciando o n8n para registrar o webhook..."
docker compose restart n8n >/dev/null

log "Aguardando o webhook responder..."
NET=$(docker inspect botsgp-n8n --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')
for i in $(seq 1 40); do
  CODE=$(docker run --rm --network "$NET" curlimages/curl:latest -s -o /dev/null -w '%{http_code}' \
         -m 10 -X POST "http://botsgp-n8n:5678/webhook/evolution-inbound" \
         -H 'Content-Type: application/json' -d '{}' 2>/dev/null || echo 000)
  if [[ "$CODE" == "200" ]]; then
    echo
    echo "${GRN}================================================${RST}"
    echo " Webhook ativo. Mande uma mensagem para o numero"
    echo " pareado e o bot deve responder com o menu."
    echo "${GRN}================================================${RST}"
    exit 0
  fi
  sleep 3
done

warn "O webhook respondeu HTTP $CODE (esperado 200)."
echo "    Veja o que aconteceu:  docker compose logs --tail 40 n8n"
