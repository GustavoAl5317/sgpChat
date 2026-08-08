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
#   bash testar-wifi.sh 999 888 777           sonda varios contratos
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
  - TODOS com "Gerenciador de CPE"  -> o ACS nao esta configurado na base.
    O modulo Wi-Fi nao funciona para ninguem. Ou a RCNet configura o
    Gerenciador de CPE no SGP, ou a opcao 1 sai do menu ate la.
  - ALGUNS passam                   -> e por contrato: so os equipamentos
    provisionados no ACS aceitam. O bot ja manda esses casos para atendente.
  - Todos com outro erro            -> me manda a saida, e outra coisa.

Pergunta a fazer para a RCNet, que economiza um dia de tentativa e erro:
  "O Gerenciador de CPE esta configurado no SGP? Para quais contratos?"
EOF
