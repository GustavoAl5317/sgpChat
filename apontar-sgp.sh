#!/usr/bin/env bash
# Aponta o bot para outro SGP (ex: da demo para o do provedor).
# Valida as credenciais ANTES de trocar - nao adianta reiniciar o stack para
# so depois descobrir que o token esta errado.
#
# Uso:  bash apontar-sgp.sh
set -uo pipefail
cd "$(dirname "$0")"

GRN=$'\e[32m'; YLW=$'\e[33m'; RED=$'\e[31m'; RST=$'\e[0m'
log()  { echo "${GRN}==>${RST} $*"; }
warn() { echo "${YLW}[!]${RST} $*"; }
die()  { echo "${RED}[x]${RST} $*" >&2; exit 1; }

[[ -f .env ]] || die ".env nao encontrado. Rode de dentro de ~/sgpChat/sgpChat"
set -a; source .env; set +a

echo "Configuracao atual:"
echo "    SGP_API_URL   = ${SGP_API_URL:-}"
echo "    SGP_APP_NAME  = ${SGP_APP_NAME:-}"
echo

read -rp "URL do SGP (ex: https://provedor.sgp.net.br): " NOVA_URL
NOVA_URL="${NOVA_URL%/}"
[[ "$NOVA_URL" == https://* ]] || die "a URL precisa comecar com https://"
read -rp "Token da API: " NOVO_TOKEN
read -rp "Appname associado ao token: " NOVO_APP
[[ -n "$NOVO_TOKEN" && -n "$NOVO_APP" ]] || die "token e appname sao obrigatorios"

# ---- valida antes de mexer no .env -------------------------------------
log "Testando as credenciais..."
RESP=$(curl -s -m 25 -X POST "${NOVA_URL}/api/ura/consultacliente/" \
        -H "Content-Type: application/json" \
        -d "{\"token\":\"${NOVO_TOKEN}\",\"app\":\"${NOVO_APP}\",\"cpfcnpj\":\"00000000000\"}" || true)

if echo "$RESP" | grep -qi 'Credenciais de autentica'; then
  die "token ou appname incorretos - o SGP recusou a autenticacao"
elif echo "$RESP" | grep -q '"contratos"'; then
  log "Autenticacao OK (a API respondeu com a estrutura esperada)"
elif [[ -z "$RESP" ]]; then
  die "sem resposta de ${NOVA_URL} - confira a URL"
else
  warn "resposta inesperada: ${RESP:0:200}"
  read -rp "Continuar mesmo assim? [s/N]: " GO
  [[ "${GO,,}" == "s" ]] || exit 1
fi

# ---- tipos de ocorrencia deste SGP -------------------------------------
log "Tipos de ocorrencia disponiveis (para o modulo de suporte):"
curl -s -m 20 -G "${NOVA_URL}/api/os/ocorrencia/tipo/list/" \
     --data-urlencode "token=${NOVO_TOKEN}" --data-urlencode "app=${NOVO_APP}" \
  | python3 -c "import json,sys
try:
    for o in json.load(sys.stdin):
        if o.get('ativo'): print('      %-5s %s' % (o['id'], o['descricao']))
except Exception: print('      (nao consegui listar)')" 2>/dev/null || true

echo
echo "Escolha um tipo generico, de preferencia criado para o bot"
echo "(ex: 'Autoatendimento WhatsApp'). Assim a equipe distingue na fila."
read -rp "ID do tipo de ocorrencia [${SGP_OCORRENCIA_TIPO:-1}]: " NOVO_TIPO
NOVO_TIPO=${NOVO_TIPO:-${SGP_OCORRENCIA_TIPO:-1}}

# ---- aplicar -----------------------------------------------------------
cp .env ".env.bak.$(date +%Y%m%d%H%M%S)"
log "Backup do .env criado."

python3 - "$NOVA_URL" "$NOVO_TOKEN" "$NOVO_APP" "$NOVO_TIPO" <<'PY'
import sys, re, pathlib
url, tok, app, tipo = sys.argv[1:5]
p = pathlib.Path('.env'); t = p.read_text(encoding='utf-8')
for chave, valor in (('SGP_API_URL', url), ('SGP_API_TOKEN', tok),
                     ('SGP_APP_NAME', app), ('SGP_OCORRENCIA_TIPO', tipo)):
    linha = f'{chave}={valor}'
    if re.search(rf'(?m)^{chave}=', t):
        t = re.sub(rf'(?m)^{chave}=.*$', linha, t)
    else:
        t = t.rstrip('\n') + '\n' + linha + '\n'
p.write_text(t, encoding='utf-8')
print('    .env atualizado')
PY

log "Recriando o n8n para carregar as novas variaveis..."
docker compose up -d --force-recreate n8n >/dev/null

cat <<EOF

${GRN}================== APONTADO PARA O SGP REAL ==================${RST}

  URL     : ${NOVA_URL}
  Appname : ${NOVO_APP}
  Chamados: tipo de ocorrencia ${NOVO_TIPO}

${YLW}ANTES DE LIBERAR PARA CLIENTES:${RST}

  A opcao 1 (Wi-Fi) altera o roteador de verdade agora. Teste primeiro
  com o contrato de alguem da equipe, nunca com o de cliente - se algum
  campo estiver mapeado errado, quem perde a rede e a pessoa do outro lado.

  Teste tambem a opcao 4 (diagnostico): so com a base real da para saber
  se o vinculo contrato -> ONU funciona e como a OLT de voces formata o
  sinal optico. Se o sinal nao aparecer, me mande a saida de:

    source .env && curl -s -G "\$SGP_API_URL/api/fttx/onu/list/" \\
      --data-urlencode "token=\$SGP_API_TOKEN" --data-urlencode "app=\$SGP_APP_NAME" \\
      --data-urlencode "contrato=<ID_DE_UM_CONTRATO>"

EOF
