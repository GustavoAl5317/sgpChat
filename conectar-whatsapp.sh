#!/usr/bin/env bash
# Cria a instancia na Evolution API e mostra o QR Code para parear o WhatsApp.
# Uso:  bash conectar-whatsapp.sh
set -euo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo "[x] .env nao encontrado. Rode install.sh antes."; exit 1; }
# shellcheck disable=SC1091
source .env

API="http://localhost:8080"
# A Evolution nao expoe porta publica; falamos com ela de dentro da rede Docker.
call() {
  docker compose exec -T evolution \
    wget -qO- --header="apikey: ${EVOLUTION_API_KEY}" \
              --header="Content-Type: application/json" "$@" 2>/dev/null || true
}

echo "==> Verificando instancia '${EVOLUTION_INSTANCE}'..."
STATE=$(call "${API}/instance/connectionState/${EVOLUTION_INSTANCE}")

if echo "$STATE" | grep -q '"state"'; then
  echo "    Instancia existe: $STATE"
  if echo "$STATE" | grep -q '"open"'; then
    echo "==> WhatsApp JA esta conectado. Nada a fazer."
    exit 0
  fi
else
  echo "==> Criando instancia..."
  call --post-data="{\"instanceName\":\"${EVOLUTION_INSTANCE}\",\"integration\":\"WHATSAPP-BAILEYS\",\"qrcode\":true}" \
       "${API}/instance/create" >/dev/null
  sleep 3
fi

echo "==> Gerando QR Code..."
RESP=$(call "${API}/instance/connect/${EVOLUTION_INSTANCE}")

CODE=$(echo "$RESP" | grep -oP '"code"\s*:\s*"\K[^"]+' | head -1 || true)
if [[ -z "$CODE" ]]; then
  echo "[x] Nao consegui obter o QR Code. Resposta:"
  echo "$RESP"
  echo "Veja os logs:  docker compose logs -f evolution"
  exit 1
fi

echo
if command -v qrencode >/dev/null 2>&1; then
  qrencode -t ANSIUTF8 "$CODE"
else
  echo "Instale o qrencode para ver o QR no terminal:  apt-get install -y qrencode"
  echo "Ou cole este codigo em um gerador de QR:"
  echo
  echo "$CODE"
fi

echo
echo "Escaneie no WhatsApp:  Menu -> Aparelhos conectados -> Conectar aparelho"
echo "Depois confirme com:   bash conectar-whatsapp.sh"
