#!/usr/bin/env bash
# Publica o n8n no Traefik do EasyPanel via file provider.
#
# Por que assim e nao por labels do Docker: o Traefik do EasyPanel roda em
# Swarm e le a configuracao dinamica de /etc/easypanel/traefik/config/*.yaml.
# Um arquivo de rota ali funciona sempre; labels em container de compose so
# funcionam se o provider Docker estiver ligado - e nao esta.
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

cat > "$DEST" <<EOF
# Gerado por publicar-no-traefik.sh - nao editar a mao.
# Publica o container botsgp-n8n em https://${DOMAIN}
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
  services:
    botsgp-n8n:
      loadBalancer:
        passHostHeader: true
        servers:
          - url: "http://botsgp-n8n:5678"
EOF

echo "==> Rota escrita em $DEST"

# O Traefik precisa alcancar o container: os dois tem que estar na mesma rede.
NET=${TRAEFIK_NETWORK:-easypanel}
if ! docker inspect botsgp-n8n --format '{{json .NetworkSettings.Networks}}' 2>/dev/null | grep -q "\"$NET\""; then
  echo "[!] botsgp-n8n nao esta na rede '$NET'. Conectando..."
  docker network connect "$NET" botsgp-n8n || true
fi

echo "==> O Traefik recarrega sozinho (file provider com watch). Testando..."
sleep 5
for i in $(seq 1 12); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "https://${DOMAIN}/" || echo 000)
  case "$CODE" in
    200|401|302) echo "==> OK! n8n respondendo em https://${DOMAIN} (HTTP $CODE)"; exit 0 ;;
  esac
  echo "    tentativa $i: HTTP $CODE (certificado pode estar sendo emitido)"
  sleep 10
done

echo "[!] Ainda nao respondeu. Verifique:"
echo "    docker logs \$(docker ps -qf name=easypanel-traefik) --tail 40"
