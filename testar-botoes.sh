#!/usr/bin/env bash
# Descobre se mensagem interativa (botao/lista) renderiza nesta instancia.
#
# Por que testar em vez de so implementar: a instancia usa WHATSAPP-BAILEYS,
# que e o WhatsApp Web por baixo, e o WhatsApp restringe mensagem interativa
# vinda dai. Renderiza em alguns aparelhos e em outros chega EM BRANCO - o que
# e pior do que o menu de texto, porque o cliente fica sem saber o que fazer e
# sem nada para digitar.
#
# Este script manda os tres formatos para um numero. Olhe no celular qual
# aparece de verdade antes de mudar o fluxo.
#
# Uso:  bash testar-botoes.sh <telefone>       ex: 5592984769449
set -uo pipefail
cd "$(dirname "$0")"

GRN=$'\e[32m'; YLW=$'\e[33m'; RST=$'\e[0m'

[[ -f .env ]] || { echo "[x] .env nao encontrado. Rode de dentro de ~/sgpChat/sgpChat"; exit 1; }
set -a; source .env; set +a

PHONE="${1:-}"
[[ -n "$PHONE" ]] || { echo "[x] uso: bash testar-botoes.sh <telefone>"; exit 1; }

NET=$(docker inspect botsgp-n8n \
      --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')

evo() {
  local rota="$1" corpo="$2"
  docker run --rm --network "$NET" curlimages/curl:latest -s -m 25 \
    -X POST "http://botsgp-evolution:8080/message/${rota}/${EVOLUTION_INSTANCE}" \
    -H "apikey: ${EVOLUTION_API_KEY}" -H 'Content-Type: application/json' \
    -w '\n__HTTP__%{http_code}' -d "$corpo"
}

ESTADO=$(docker run --rm --network "$NET" curlimages/curl:latest -s -m 15 \
         -H "apikey: ${EVOLUTION_API_KEY}" \
         "http://botsgp-evolution:8080/instance/connectionState/${EVOLUTION_INSTANCE}")
echo "$ESTADO" | grep -q '"state":"open"' || {
  echo "[x] instancia nao conectada: $ESTADO"
  echo "    Reconecte com: bash conectar-whatsapp.sh"
  exit 1
}

echo "${GRN}==>${RST} 1. Texto simples (referencia - este SEMPRE funciona)"
evo sendText "{\"number\":\"${PHONE}\",\"text\":\"*Teste 1/3* - texto simples.\nSe so este chegar, o menu tem que continuar sendo numerado.\"}"
echo; echo

echo "${GRN}==>${RST} 2. Botoes (maximo 3 - serve para confirmar/cancelar)"
# O texto numerado vai junto de proposito: se os botoes nao renderizarem, o
# cliente ainda consegue responder digitando.
evo sendButtons "{
  \"number\": \"${PHONE}\",
  \"title\": \"Teste 2/3 - botoes\",
  \"description\": \"Confirma a alteracao?\n\nSe voce NAO esta vendo dois botoes abaixo, responda *1* ou *2*.\",
  \"footer\": \"Atendimento automatico\",
  \"buttons\": [
    {\"type\": \"reply\", \"displayText\": \"Confirmar\", \"id\": \"1\"},
    {\"type\": \"reply\", \"displayText\": \"Cancelar\",  \"id\": \"2\"}
  ]
}"
echo; echo

echo "${GRN}==>${RST} 3. Lista (ate 10 itens - serve para o menu de 5 opcoes)"
evo sendList "{
  \"number\": \"${PHONE}\",
  \"title\": \"Teste 3/3 - lista\",
  \"description\": \"Escolha uma opcao.\n\nSe voce NAO esta vendo um botao para abrir a lista, digite o numero.\",
  \"buttonText\": \"Ver opcoes\",
  \"footerText\": \"Atendimento automatico\",
  \"sections\": [{
    \"title\": \"Autoatendimento\",
    \"rows\": [
      {\"title\": \"Alterar Wi-Fi\",   \"description\": \"Trocar nome e senha da rede\", \"rowId\": \"1\"},
      {\"title\": \"2a via de boleto\", \"description\": \"Ver faturas em aberto\",       \"rowId\": \"2\"},
      {\"title\": \"Abrir chamado\",    \"description\": \"Registrar um problema\",       \"rowId\": \"3\"},
      {\"title\": \"Diagnostico\",      \"description\": \"Checar o sinal da sua conexao\",\"rowId\": \"4\"},
      {\"title\": \"Falar com atendente\", \"description\": \"Atendimento humano\",       \"rowId\": \"5\"}
    ]
  }]
}"
echo

cat <<EOF

${YLW}Agora olhe o celular ${PHONE}${RST} e responda:

  a) Os tres chegaram?
  b) O 2 mostrou dois botoes clicaveis, ou so o texto?
  c) O 3 mostrou um botao "Ver opcoes" que abre a lista, ou so o texto?
  d) Alguma mensagem chegou EM BRANCO?

Se algum HTTP acima nao for 200/201, o formato nao e suportado nesta versao da
Evolution - o corpo da resposta diz qual campo ela esperava.

Depois de clicar num botao/item, confira o que o fluxo recebeu:
  bash ver-erro.sh          (ultimo erro)
  docker compose logs --tail 30 n8n
EOF
