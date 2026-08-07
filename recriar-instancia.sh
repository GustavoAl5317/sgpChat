#!/usr/bin/env bash
# Diagnostica e, se preciso, recria a instancia da Evolution do zero.
#
# Use quando o QR e gerado mas o celular diz "codigo invalido". As duas causas
# comuns sao:
#   1) o estado da instancia corrompeu depois de uma queda - resolve recriando.
#   2) a versao do WhatsApp Web que o Baileys anuncia envelheceu - o WhatsApp
#      recusa o pareamento. Nao da para pinar por env var nesta imagem (ver o
#      comentario no docker-compose.yml): a versao vem de uma chamada a
#      https://web.whatsapp.com/sw.js feita pelo proprio container. Por isso este
#      script compara o que a Evolution anuncia com o que o WhatsApp publica
#      agora - se divergirem, ou o container nao alcanca a web.whatsapp.com, ou
#      a imagem esta defasada.
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
# O GET / da Evolution chama o mesmo fetchLatestWaWebVersion({}) que o socket
# usa ao parear - entao o que ele responde aqui e exatamente o que vai ser
# anunciado ao WhatsApp.
ANUNCIADA=$(api GET "/" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get('version',''), d.get('whatsappWebVersion',''))
except Exception:
    print('', '')
")
EVO_VER=$(echo "$ANUNCIADA" | awk '{print $1}')
WA_VER=$(echo "$ANUNCIADA" | awk '{print $2}')
echo "    Evolution API          : ${EVO_VER:-<nao consegui ler>}"
echo "    WhatsApp Web anunciada : ${WA_VER:-<nao consegui ler>}"

# Fonte da verdade: o mesmo sw.js que o Baileys consulta. Buscado de dentro da
# rede do container, para o teste valer para quem realmente faz a chamada.
WA_ATUAL=$(docker run --rm --network "$NET" curlimages/curl:latest \
  -s --max-time 20 https://web.whatsapp.com/sw.js 2>/dev/null \
  | grep -oE 'client_revision[^0-9]{0,4}[0-9]+' | head -1 | grep -oE '[0-9]+$' || true)

if [[ -z "$WA_ATUAL" ]]; then
  warn "O container NAO conseguiu baixar https://web.whatsapp.com/sw.js."
  warn "Sem isso o Baileys cai calado na versao embutida no pacote, que e velha"
  warn "- e e exatamente assim que o QR vira \"codigo invalido\". Libere o egress"
  warn "do container para web.whatsapp.com antes de tentar qualquer outra coisa."
else
  echo "    WhatsApp Web publicada : 2.3000.${WA_ATUAL}"
  if [[ "$WA_VER" == "2.3000.${WA_ATUAL}" ]]; then
    echo "    ${GRN}versao em dia - o pareamento nao esta falhando por versao${RST}"
  else
    warn "DIVERGENTE: a Evolution anuncia '${WA_VER}' e o WhatsApp esta em"
    warn "'2.3000.${WA_ATUAL}'. Como a imagem nao aceita pinar por env var, o"
    warn "caminho e subir a tag da Evolution no docker-compose.yml."
  fi
fi

echo
log "Estado atual da instancia '${EVOLUTION_INSTANCE}'"
api GET "/instance/connectionState/${EVOLUTION_INSTANCE}" | sed 's/^/    /'

if [[ "${1:-}" != "--recriar" ]]; then
  cat <<EOF

Se o QR e recusado pelo celular, tente nesta ordem:

  1) Recriar a instancia (estado corrompido e a causa mais comum):
       bash recriar-instancia.sh --recriar

  2) Se o bloco de versoes acima acusou divergencia ou falha de download, o
     problema e a versao anunciada - resolva por ali (egress ou tag da imagem),
     nao adianta recriar a instancia.

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
