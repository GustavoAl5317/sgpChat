#!/usr/bin/env bash
# Testa o envio pela Evolution isoladamente, fora do n8n.
#
# O n8n so guarda "Bad Request" quando a Evolution devolve 400 - a explicacao
# fica no corpo da resposta, que se perde. Aqui a gente ve o corpo inteiro.
#
# Uso:  bash testar-envio.sh [telefone]
set -uo pipefail
cd "$(dirname "$0")"

GRN=$'\e[32m'; YLW=$'\e[33m'; RED=$'\e[31m'; RST=$'\e[0m'
log()  { echo "${GRN}==>${RST} $*"; }
warn() { echo "${YLW}[!]${RST} $*"; }

[[ -f .env ]] || { echo "[x] .env nao encontrado. Rode de dentro de ~/sgpChat/sgpChat"; exit 1; }
set -a; source .env; set +a

PHONE="${1:-}"
NET=$(docker inspect botsgp-n8n --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')

# Roda o curl de dentro da rede do Docker, igual o n8n faz - assim testamos o
# mesmo caminho de rede, nao o acesso externo.
evo() {
  docker run --rm --network "$NET" curlimages/curl:latest -s -m 25 "$@"
}

log "1. Estado da instancia '${EVOLUTION_INSTANCE}'"
ESTADO=$(evo -H "apikey: ${EVOLUTION_API_KEY}" \
         "http://botsgp-evolution:8080/instance/connectionState/${EVOLUTION_INSTANCE}")
echo "    ${ESTADO}"

if echo "$ESTADO" | grep -q '"state":"open"'; then
  log "   Conectada."
else
  warn "   NAO esta conectada. Enquanto estiver assim, todo envio devolve 400."
  warn "   Reconecte com: bash conectar-whatsapp.sh"
fi

echo
log "2. Instancias existentes (nome e estado)"
evo -H "apikey: ${EVOLUTION_API_KEY}" "http://botsgp-evolution:8080/instance/fetchInstances" \
  | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    d=d if isinstance(d,list) else d.get('instances',[d])
    for i in d:
        i=i.get('instance',i)
        print('      %-20s %s' % (i.get('instanceName') or i.get('name'),
                                  i.get('connectionStatus') or i.get('status')))
except Exception as e: print('      (nao consegui ler:', e, ')')
" 2>/dev/null || true

if [[ -z "$PHONE" ]]; then
  echo
  echo "Para testar um envio de verdade:  bash testar-envio.sh <telefone>"
  exit 0
fi

echo
log "3. Enviando texto simples para ${PHONE}"
RESP=$(evo -X POST "http://botsgp-evolution:8080/message/sendText/${EVOLUTION_INSTANCE}" \
       -H "apikey: ${EVOLUTION_API_KEY}" -H 'Content-Type: application/json' \
       -w '\n__HTTP__%{http_code}' \
       -d "{\"number\":\"${PHONE}\",\"text\":\"teste simples\"}")
echo "    ${RESP}"

echo
log "4. Enviando texto com formatacao (igual as respostas do bot)"
RESP2=$(evo -X POST "http://botsgp-evolution:8080/message/sendText/${EVOLUTION_INSTANCE}" \
        -H "apikey: ${EVOLUTION_API_KEY}" -H 'Content-Type: application/json' \
        -w '\n__HTTP__%{http_code}' \
        -d "{\"number\":\"${PHONE}\",\"text\":\"*Negrito* e quebra:\\nlinha 2\\n\\nDigite *menu*.\"}")
echo "    ${RESP2}"

echo
log "5. Texto vazio (o bot manda isso se reply_text vier nulo)"
RESP3=$(evo -X POST "http://botsgp-evolution:8080/message/sendText/${EVOLUTION_INSTANCE}" \
        -H "apikey: ${EVOLUTION_API_KEY}" -H 'Content-Type: application/json' \
        -w '\n__HTTP__%{http_code}' \
        -d "{\"number\":\"${PHONE}\",\"text\":null}")
echo "    ${RESP3}"

cat <<EOF

Leitura do resultado:
  - 3 e 4 com HTTP 200/201  -> a Evolution envia bem; o 400 vem do CONTEUDO
                               que o fluxo montou (provavelmente texto nulo).
  - 3 falhando tambem       -> problema de instancia/apikey, nao do fluxo.
  - 5 devolvendo 400        -> confirma que texto nulo e a causa.
EOF
