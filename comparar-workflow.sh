#!/usr/bin/env bash
# Compara o workflow que esta RODANDO no n8n com o que o repositorio gera.
#
# Editar um node pela interface do n8n funciona e e tentador - so que o
# build-workflow.py sobrescreve o workflow inteiro no proximo import. Sem
# comparar antes, uma melhoria feita na interface some sem aviso e ninguem
# descobre ate um cliente reclamar.
#
# Rode SEMPRE antes de configurar-n8n.sh.
#
# Uso:  bash comparar-workflow.sh
set -uo pipefail
cd "$(dirname "$0")"

GRN=$'\e[32m'; YLW=$'\e[33m'; RED=$'\e[31m'; RST=$'\e[0m'

[[ -f .env ]] || { echo "[x] .env nao encontrado. Rode de dentro de ~/sgpChat/sgpChat"; exit 1; }
set -a; source .env; set +a

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

echo "==> Lendo o workflow que esta no n8n..."
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c \
  "SELECT nodes::text FROM workflow_entity ORDER BY \"updatedAt\" DESC LIMIT 1" > "$TMP/prod.json" 2>/dev/null

if [[ ! -s "$TMP/prod.json" ]]; then
  echo "${RED}[x]${RST} Nao consegui ler o workflow do banco."
  echo "    Confira se o n8n ja importou algum workflow."
  exit 1
fi

python3 - "$TMP/prod.json" "n8n/workflow-wifi-selfservice.json" <<'PY'
import json, sys, difflib, io

prod = json.load(io.open(sys.argv[1], encoding='utf-8'))
repo = json.load(io.open(sys.argv[2], encoding='utf-8'))['nodes']

def codigo(nodes):
    """Só os nodes de codigo importam aqui: e neles que mora a conversa."""
    d = {}
    for n in nodes:
        js = (n.get('parameters') or {}).get('jsCode')
        if js:
            d[n['name']] = js
        q = (n.get('parameters') or {}).get('query')
        if q:
            d[n['name'] + ' (SQL)'] = q
    return d

a, b = codigo(prod), codigo(repo)
difs = 0

for nome in sorted(set(a) | set(b)):
    if nome not in a:
        print('\n  [novo no repo]      ' + nome); difs += 1; continue
    if nome not in b:
        print('\n  [so em producao]    ' + nome); difs += 1; continue
    if a[nome] == b[nome]:
        continue
    difs += 1
    print('\n' + '=' * 62)
    print('DIFERENTE: ' + nome)
    print('=' * 62)
    for l in difflib.unified_diff(a[nome].splitlines(), b[nome].splitlines(),
                                  'producao', 'repositorio', lineterm='', n=1):
        print('  ' + l)

if difs == 0:
    print('\n  Nenhuma diferenca. Pode importar com seguranca.')
else:
    print('\n' + '-' * 62)
    print('  %d node(s) diferentes.' % difs)
    print('  Linhas com "-" existem SO em producao: se forem melhorias, leve')
    print('  para o build-workflow.py ANTES de rodar configurar-n8n.sh, senao')
    print('  o import apaga.')
    sys.exit(2)
PY
