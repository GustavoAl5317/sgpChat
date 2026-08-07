#!/usr/bin/env bash
# Mostra o erro real de uma execucao do n8n.
#
# Por que existe: o n8n grava execution_data em formato "flatted" - um array
# onde os objetos guardam INDICES em vez de valores, para nao repetir string.
# Por isso um grep ingenuo devolve '"message":"23"': 23 e a posicao do texto
# dentro do array, nao o texto. Este script resolve os ponteiros.
#
# Uso:  bash ver-erro.sh <id-da-execucao>
# Ex.:  bash ver-erro.sh 26
set -uo pipefail
cd "$(dirname "$0")"

[[ -f .env ]] || { echo "[x] .env nao encontrado. Rode de dentro de ~/sgpChat/sgpChat"; exit 1; }
set -a; source .env; set +a

ID="${1:-}"
if [[ -z "$ID" ]]; then
  ID=$(docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
       "SELECT id FROM execution_entity WHERE status='error' ORDER BY id DESC LIMIT 1;" | tr -d '[:space:]')
  [[ -n "$ID" ]] || { echo "nenhuma execucao com erro encontrada"; exit 0; }
  echo "==> Sem id informado; usando a ultima execucao com erro: ${ID}"
fi

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT data FROM execution_data WHERE \"executionId\"=${ID};" > "$TMP"

[[ -s "$TMP" ]] || { echo "[x] execucao ${ID} nao encontrada"; exit 1; }

python3 - "$TMP" <<'PY'
import json, sys

raw = open(sys.argv[1], encoding='utf-8', errors='replace').read().strip()
try:
    arr = json.loads(raw)
except Exception as e:
    print('nao consegui ler o JSON:', e); raise SystemExit(1)

def deref(v, profundidade=0):
    """No formato flatted, uma string de digitos e um indice para o array."""
    if profundidade > 6:
        return v
    if isinstance(v, str) and v.isdigit():
        i = int(v)
        if 0 <= i < len(arr):
            return deref(arr[i], profundidade + 1)
    return v

CAMPOS = ('name', 'message', 'description', 'httpCode', 'reason',
          'code', 'status', 'statusText', 'node', 'stack')

vistos = set()
achou = False
for el in arr:
    if not isinstance(el, dict):
        continue
    if not any(k in el for k in ('message', 'httpCode', 'stack')):
        continue
    saida = {}
    for k in CAMPOS:
        if k not in el:
            continue
        val = deref(el[k])
        if isinstance(val, dict):                 # ex.: node -> {name: ...}
            val = deref(val.get('name', val.get('message', '')))
        if isinstance(val, (list, dict)):
            continue
        val = str(val).strip()
        if val and val != 'None':
            saida[k] = val[:600]
    if not saida:
        continue
    chave = json.dumps(saida, sort_keys=True)
    if chave in vistos:
        continue
    vistos.add(chave)
    achou = True
    print('-' * 60)
    for k in CAMPOS:
        if k in saida:
            print('%-12s %s' % (k + ':', saida[k]))

if not achou:
    print('Nenhum bloco de erro encontrado nessa execucao.')
PY
