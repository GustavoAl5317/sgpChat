#!/usr/bin/env bash
# Cria a instancia na Evolution API e mostra o QR Code para parear o WhatsApp.
# Uso:  bash conectar-whatsapp.sh
set -euo pipefail
cd "$(dirname "$0")"

[[ -f .env ]] || { echo "[x] .env nao encontrado. Rode install.sh antes."; exit 1; }
# shellcheck disable=SC1091
source .env
: "${EVOLUTION_API_KEY:?falta EVOLUTION_API_KEY no .env}"
: "${EVOLUTION_INSTANCE:?falta EVOLUTION_INSTANCE no .env}"

NET="${TRAEFIK_NETWORK:-easypanel}"

# A Evolution nao publica porta. Falamos com ela de dentro da rede do Docker,
# usando um container efemero de curl - assim nao dependemos de ter wget/curl
# instalado dentro da imagem da Evolution, que varia entre versoes.
api() {
  local method="$1" path="$2" data="${3:-}"
  local args=(-s --max-time 30 -H "apikey: ${EVOLUTION_API_KEY}" -H "Content-Type: application/json" -X "$method")
  [[ -n "$data" ]] && args+=(-d "$data")
  docker run --rm --network "$(docker inspect botsgp-evolution \
      --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')" \
    curlimages/curl:latest "${args[@]}" "http://botsgp-evolution:8080${path}" 2>/dev/null || true
}

jqf() { python3 -c "import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(1)
print(d$1 if isinstance(d, dict) else '')" 2>/dev/null || true; }

echo "==> Verificando a instancia '${EVOLUTION_INSTANCE}'..."
STATE=$(api GET "/instance/connectionState/${EVOLUTION_INSTANCE}")

if echo "$STATE" | grep -q '"state"'; then
  if echo "$STATE" | grep -q '"open"'; then
    echo "==> WhatsApp JA esta conectado. Nada a fazer."
    exit 0
  fi
  echo "    Instancia existe, mas desconectada."
else
  echo "==> Criando instancia..."
  api POST "/instance/create" \
    "{\"instanceName\":\"${EVOLUTION_INSTANCE}\",\"integration\":\"WHATSAPP-BAILEYS\",\"qrcode\":true}" >/dev/null
  sleep 4
fi

echo "==> Gerando QR Code..."
RESP=$(api GET "/instance/connect/${EVOLUTION_INSTANCE}")
CODE=$(echo "$RESP" | grep -oP '"code"\s*:\s*"\K[^"]+' | head -1 || true)

if [[ -z "$CODE" ]]; then
  echo "[x] Nao consegui obter o QR Code. Resposta da Evolution:"
  echo "${RESP:0:500}"
  echo
  echo "Diagnostico:  docker compose logs --tail 40 evolution"
  exit 1
fi

if ! command -v qrencode >/dev/null 2>&1; then
  echo "==> Instalando qrencode para desenhar o QR no terminal..."
  apt-get install -y -qq qrencode >/dev/null 2>&1 || true
fi

echo
if command -v qrencode >/dev/null 2>&1; then
  qrencode -t ANSIUTF8 "$CODE"
else
  echo "Sem qrencode. Cole este codigo em um gerador de QR:"
  echo; echo "$CODE"
fi

cat <<EOF

No celular da EMPRESA:
  WhatsApp -> Menu (tres pontos) -> Aparelhos conectados -> Conectar aparelho

O QR expira em ~60s. Se expirar, rode este script de novo.

Depois de escanear, confirme com:
  bash conectar-whatsapp.sh
EOF
