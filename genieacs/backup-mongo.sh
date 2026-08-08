#!/usr/bin/env bash
# Backup do Mongo do GenieACS. Perder esse banco nao desconfigura o roteador
# de ninguem, mas o GenieACS "esquece" quem gerencia - toda ONU precisaria
# reconectar do zero para reaparecer, e ate la a troca de Wi-Fi para de
# funcionar para a base inteira. Nao e opcional em producao.
#
# Uso:  bash backup-mongo.sh
# Agende no cron do servidor, por exemplo diario as 4h:
#   0 4 * * * cd /caminho/para/genieacs && bash backup-mongo.sh >> backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"

DIR="backups"
mkdir -p "$DIR"
ARQ="$DIR/genieacs-$(date +%Y%m%d-%H%M%S).archive.gz"

docker compose exec -T mongo mongodump --archive --gzip --db genieacs > "$ARQ"
echo "backup salvo em $ARQ ($(du -h "$ARQ" | cut -f1))"

# Mantem so os ultimos 14 - backup que ninguem limpa vira disco cheio, que
# derruba o Mongo, que e o problema que este script existe para evitar.
find "$DIR" -name 'genieacs-*.archive.gz' -mtime +14 -delete

cat <<EOF

Para restaurar (se precisar):
  gunzip -c $ARQ | docker compose exec -T mongo mongorestore --archive --gzip --drop
EOF
