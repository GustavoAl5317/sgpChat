#!/usr/bin/env bash
# Chama o cpemanage do SGP direto, sem passar pelo bot, e mostra a resposta crua.
#
# Serve para responder UMA pergunta: "O Servico de internet nao possui
# Gerenciador de CPE configurado" e daquele contrato ou da base inteira? Se for
# da base, o modulo Wi-Fi nao funciona para ninguem e a opcao 1 nao deveria
# estar no menu ate a RCNet configurar o ACS.
#
# ATENCAO: o cpemanage NAO tem chamada de leitura. Com --aplicar, a chamada
# ESCREVE no roteador do contrato informado. Sem --aplicar, manda so o contrato,
# sem valores novos - deve ser inofensivo, mas nao ha garantia documentada.
# Por isso: use contrato de gente da equipe, nunca de cliente.
#
# Uso:
#   bash testar-wifi.sh --cpf 05789755216     descobre os contratos e sonda
#   bash testar-wifi.sh 999 888 777           sonda contratos que voce ja sabe
#   bash testar-wifi.sh --aplicar 999 NomeNovo            troca so o nome
#   bash testar-wifi.sh --aplicar 999 "" SenhaNova123     troca so a senha
set -uo pipefail
cd "$(dirname "$0")"

GRN=$'\e[32m'; YLW=$'\e[33m'; RED=$'\e[31m'; RST=$'\e[0m'

[[ -f .env ]] || { echo "[x] .env nao encontrado. Rode de dentro de ~/sgpChat/sgpChat"; exit 1; }
set -a; source .env; set +a
: "${SGP_API_URL:?falta SGP_API_URL no .env}"

NET=$(docker inspect botsgp-n8n \
      --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')

# Mesmo corpo que o node "SGP - Definir Wifi" monta: campo que nao muda nao vai.
sgp() {
  local corpo="$1"
  docker run --rm --network "$NET" curlimages/curl:latest -s -m 40 \
    -X POST "${SGP_API_URL}/api/ura/cpemanage/" \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    -w '\n__HTTP__%{http_code}' \
    --data-urlencode "token=${SGP_API_TOKEN}" \
    --data-urlencode "app=${SGP_APP_NAME}" \
    --data "$corpo"
}

# Descobre os contratos de um CPF pelo mesmo endpoint que o bot usa, para nao
# ser preciso caçar o numero do contrato na interface do SGP antes de testar.
if [[ "${1:-}" == "--cpf" ]]; then
  CPF="$(printf '%s' "${2:-}" | tr -cd '0-9')"
  [[ -n "$CPF" ]] || { echo "[x] uso: bash testar-wifi.sh --cpf <cpf>"; exit 1; }

  RESP=$(docker run --rm --network "$NET" curlimages/curl:latest -s -m 30 \
    -X POST "${SGP_API_URL}/api/ura/consultacliente/" \
    -H 'Content-Type: application/json' \
    -d "{\"token\":\"${SGP_API_TOKEN}\",\"app\":\"${SGP_APP_NAME}\",\"cpfcnpj\":\"${CPF}\"}")

  set -- $(printf '%s' "$RESP" | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.stderr.write('    (nao consegui ler a resposta do consultacliente)\n'); raise SystemExit
cs = d.get('contratos') or []
if not cs:
    sys.stderr.write('    (nenhum contrato para esse CPF)\n')
for c in cs:
    st = c.get('contratoStatus')
    # O nome atual da rede e o que permite a sondagem REAL: reenviar o mesmo
    # nome passa pela checagem de CPE sem mudar nada para o cliente.
    ssid = c.get('servico_wifi_ssid') or ''
    sys.stderr.write('    contrato %-8s status=%-2s %-12s wifi_ssid=%s\n' % (
        c.get('contratoId'), st, c.get('contratoStatusDisplay') or '',
        ssid or '(o SGP nao devolve esse campo)'))
    if st == 1:
        print(c.get('contratoId'))
")
  echo
  [[ $# -gt 0 ]] || { echo "[x] nenhum contrato ATIVO para sondar."; exit 1; }
  echo "${GRN}Contratos ativos a sondar:${RST} $*"
  echo
fi

if [[ "${1:-}" == "--aplicar" ]]; then
  CONTRATO="${2:-}"; NOVO_SSID="${3:-}"; NOVA_SENHA="${4:-}"
  [[ -n "$CONTRATO" ]] || { echo "[x] informe o contrato"; exit 1; }
  [[ -n "$NOVO_SSID$NOVA_SENHA" ]] || { echo "[x] informe ao menos nome ou senha"; exit 1; }

  echo "${RED}[!]${RST} Isso ESCREVE no roteador do contrato ${CONTRATO}."
  [[ -n "$NOVO_SSID"  ]] && echo "    novo nome : ${NOVO_SSID}"
  [[ -n "$NOVA_SENHA" ]] && echo "    nova senha: ${NOVA_SENHA}"
  echo "    Todos os aparelhos da casa vao cair. Use so equipamento da equipe."
  read -rp "Continuar? [s/N]: " GO
  [[ "${GO,,}" == "s" ]] || { echo "cancelado"; exit 0; }

  CORPO="contrato=${CONTRATO}"
  if [[ -n "$NOVO_SSID" ]]; then
    CORPO+="&novo_ssid=${NOVO_SSID}&novo_ssid_5g=${NOVO_SSID}"
  fi
  if [[ -n "$NOVA_SENHA" ]]; then
    CORPO+="&nova_senha=${NOVA_SENHA}&nova_senha_5g=${NOVA_SENHA}"
  fi
  echo
  echo "${GRN}==>${RST} corpo enviado: ${CORPO}"
  sgp "$CORPO"
  echo
  cat <<EOF

Leitura:
  "success": true   -> o SGP aceitou. Cheque no celular se a rede mudou MESMO -
                       success do SGP nao prova que o ACS chegou no roteador.
  "Gerenciador de CPE" -> o contrato nao tem CPE configurado no SGP.
EOF
  exit 0
fi

[[ $# -gt 0 ]] || { echo "[x] uso: bash testar-wifi.sh <contrato> [contrato...]"; exit 1; }

echo "${YLW}Sondando sem alterar nada.${RST} Manda so o contrato, sem valores novos."
echo
for C in "$@"; do
  echo "${GRN}==> contrato ${C}${RST}"
  sgp "contrato=${C}" | sed 's/^/    /'
  echo
done

cat <<EOF
Como ler:

  "E necessario enviar um ou mais dos seguintes parametros"
      A sondagem vazia NAO responde nada. O SGP valida os parametros ANTES de
      checar o Gerenciador de CPE, entao esta chamada nunca chega la. Era falha
      do teste, nao do contrato.

      Para chegar na checagem de CPE e preciso uma escrita de verdade. A de
      menor risco e reenviar o nome que a rede JA TEM - passa por todas as
      validacoes e deixa o cliente exatamente como estava:

          bash testar-wifi.sh --aplicar <contrato> "<nome-atual-da-rede>"

      O nome atual sai no wifi_ssid listado acima. Se vier vazio, pegue no SGP
      (contrato -> Servico de Internet) antes de tentar.

  "Gerenciador de CPE"
      Esse contrato nao tem CPE configurado no SGP. Repita em contratos de
      outras pessoas: se TODOS derem isso, o ACS nao esta configurado na base e
      o modulo Wi-Fi nao funciona para ninguem - a opcao 1 deveria sair do menu
      ate a RCNet configurar.

  "success": true
      O SGP aceitou e o contrato TEM CPE. Confirme no celular que a rede
      continua com o mesmo nome (era para nada ter mudado).

Observacao: a lista de parametros aceitos inclui wifi_status e wifi_status_5,
que LIGAM E DESLIGAM o radio. O bot nunca envia esses campos - so nome e senha.

Pergunta a fazer para a RCNet, que economiza um dia de tentativa e erro:
  "O Gerenciador de CPE esta configurado no SGP? Para quais contratos?"
EOF
