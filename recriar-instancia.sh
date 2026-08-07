#!/usr/bin/env bash
# Diagnostica e, se preciso, recria a instancia da Evolution do zero.
#
# Use quando o QR e gerado mas o celular diz "codigo invalido". As duas causas
# comuns sao:
#   1) a versao do WhatsApp Web embutida no Baileys envelheceu - o WhatsApp
#      recusa o pareamento. Resolve pinando WA_WEB_VERSION no .env.
#   2) o estado da instancia corrompeu depois de uma queda - resolve recriando.
#
# Uso:  bash recriar-instancia.sh            (so diagnostica)
#       bash recriar-instancia.sh --recriar  (apaga e recria a instancia)
set -uo pipefail
cd "$(dirname "$0")"

GRN=$'\e[32m'; YLW=$'\e[33m'; RED=$'\e[31m'; RST=$'\e[0m'
log()  { echo "${GRN}==>${RST} $*"; }
warn() { echo "${YLW}[!]${RST} $*"; }

[[ -f .env ]] || { echo "[x] .env nao encontrado. Rode de dentro de ~/sgpChat/sgpChat"; exit 1; }
set -a; source .env; set +a

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

log "Versoes em uso"
api GET "/" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    print('    Evolution API      :', d.get('version'))
    print('    WhatsApp Web (Baileys):', d.get('whatsappWebVersion'))
except Exception:
    print('    (nao consegui ler a resposta)')
"
echo "    CONFIG_SESSION_PHONE_VERSION no .env: ${WA_WEB_VERSION:-<vazio, usando o default da imagem>}"

echo
log "Estado atual da instancia '${EVOLUTION_INSTANCE}'"
api GET "/instance/connectionState/${EVOLUTION_INSTANCE}" | sed 's/^/    /'

if [[ "${1:-}" != "--recriar" ]]; then
  cat <<EOF

Se o QR e recusado pelo celular, tente nesta ordem:

  1) Recriar a instancia (estado corrompido e a causa mais comum):
       bash recriar-instancia.sh --recriar

  2) Se continuar recusando, a versao do WhatsApp Web esta velha. Pegue a
     versao atual em https://web.whatsapp.com (F12 -> Console ->
     window.Debug.VERSION) e fixe:
       echo 'WA_WEB_VERSION=2.3000.XXXXXXXXX' >> .env
       docker compose up -d --force-recreate evolution
       bash conectar-whatsapp.sh --forcar-qr

  3) Para ver o motivo real da recusa, suba o log da Evolution:
       echo 'LOG_LEVEL_EVOLUTION=DEBUG' >> .env
       docker compose up -d --force-recreate evolution
       docker compose logs -f evolution
EOF
  exit 0
fi

echo
warn "Isso APAGA a instancia '${EVOLUTION_INSTANCE}' e o pareamento atual."
warn "Como ela ja esta desconectada, nao ha conversa em andamento a perder -"
warn "mas sera preciso escanear o QR de novo no celular da empresa."
read -rp "Continuar? [s/N]: " GO
[[ "${GO,,}" == "s" ]] || { echo "cancelado"; exit 0; }

log "Encerrando sessao..."
api DELETE "/instance/logout/${EVOLUTION_INSTANCE}" >/dev/null
sleep 2
log "Apagando instancia..."
api DELETE "/instance/delete/${EVOLUTION_INSTANCE}" >/dev/null
sleep 3

log "Criando de novo..."
api POST "/instance/create" \
  "{\"instanceName\":\"${EVOLUTION_INSTANCE}\",\"integration\":\"WHATSAPP-BAILEYS\",\"qrcode\":true}" \
  | head -c 300 | sed 's/^/    /'
echo
sleep 4

log "Instancia recriada. Gere o QR com:"
echo "    bash conectar-whatsapp.sh --forcar-qr"
