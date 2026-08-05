#!/usr/bin/env bash
# Publica o n8n e o Manager da Evolution no Traefik do EasyPanel.
#
# Por que via arquivo e nao por labels do Docker: o Traefik do EasyPanel roda
# em Swarm e le a configuracao dinamica de /etc/easypanel/traefik/config/*.yaml.
# Um arquivo de rota ali funciona sempre; labels em container de compose so
# funcionariam com o provider Docker ligado - e nao esta.
#
# Uso:  bash publicar-no-traefik.sh
set -euo pipefail
cd "$(dirname "$0")"

CONFIG_DIR=/etc/easypanel/traefik/config
DEST="$CONFIG_DIR/botsgp.yaml"

[[ -f .env ]] || { echo "[x] .env nao encontrado. Rode install.sh antes."; exit 1; }
[[ -d "$CONFIG_DIR" ]] || { echo "[x] $CONFIG_DIR nao existe - este servidor usa EasyPanel?"; exit 1; }
# shellcheck disable=SC1091
source .env

: "${DOMAIN:?defina DOMAIN no .env}"
CERTRESOLVER=${TRAEFIK_CERTRESOLVER:-letsencrypt}
# Dominio do Manager da Evolution: onde a EMPRESA acessa para parear o WhatsApp
# pelo navegador, sem precisar de acesso ao servidor.
EVOLUTION_DOMAIN=${EVOLUTION_DOMAIN:-evolution.${DOMAIN}}

cat > "$DEST" <<EOF
# Gerado por publicar-no-traefik.sh - nao editar a mao.
http:
  routers:
    botsgp-n8n-http:
      rule: "Host(\`${DOMAIN}\`)"
      entryPoints: [http]
      middlewares: [redirect-to-https]
      service: botsgp-n8n
    botsgp-n8n:
      rule: "Host(\`${DOMAIN}\`)"
      entryPoints: [https]
      service: botsgp-n8n
      tls:
        certResolver: ${CERTRESOLVER}

    botsgp-evolution-http:
      rule: "Host(\`${EVOLUTION_DOMAIN}\`)"
      entryPoints: [http]
      middlewares: [redirect-to-https]
      service: botsgp-evolution
    botsgp-evolution:
      rule: "Host(\`${EVOLUTION_DOMAIN}\`)"
      entryPoints: [https]
      service: botsgp-evolution
      tls:
        certResolver: ${CERTRESOLVER}

  services:
    botsgp-n8n:
      loadBalancer:
        passHostHeader: true
        servers:
          - url: "http://botsgp-n8n:5678"
    botsgp-evolution:
      loadBalancer:
        passHostHeader: true
        servers:
          - url: "http://botsgp-evolution:8080"
EOF

echo "==> Rotas escritas em $DEST"

# O Traefik so alcanca os containers se estiverem na mesma rede que ele.
NET=${TRAEFIK_NETWORK:-easypanel}
for C in botsgp-n8n botsgp-evolution; do
  if ! docker inspect "$C" --format '{{json .NetworkSettings.Networks}}' 2>/dev/null | grep -q "\"$NET\""; then
    echo "[!] $C fora da rede '$NET'. Conectando..."
    docker network connect "$NET" "$C" || true
  fi
done

echo "==> Aguardando o Traefik recarregar e emitir certificados..."
sleep 5
check() {
  for i in $(seq 1 12); do
    CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "https://$1/" || echo 000)
    case "$CODE" in
      200|401|302|404) echo "    OK  https://$1  (HTTP $CODE)"; return 0 ;;
    esac
    sleep 10
  done
  echo "    [!] https://$1 nao respondeu (ultimo HTTP $CODE)"
  return 1
}
check "$DOMAIN"           || true
check "$EVOLUTION_DOMAIN" || true

cat <<EOF

================================================================
 Para VOCE (dev):
   n8n .................. https://${DOMAIN}

 Para a EMPRESA parear o WhatsApp (nao precisa de acesso ao servidor):
   Evolution Manager .... https://${EVOLUTION_DOMAIN}/manager
   API Key .............. ${EVOLUTION_API_KEY}

   A pessoa abre o link, cola a API Key, clica na instancia
   "${EVOLUTION_INSTANCE}" e escaneia o QR Code pelo celular da empresa.
================================================================
EOF
