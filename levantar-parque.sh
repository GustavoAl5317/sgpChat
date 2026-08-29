#!/usr/bin/env bash
# Levanta o parque de ONUs perguntando ao SGP, SEM tocar na OLT e SEM escrever
# nada em lugar nenhum. So faz um GET.
#
# Responde as perguntas que travam o provisionamento do TR-069:
#
#   1. Qual OLT (ou quais) atende a base, e de que fabricante.
#   2. Quais modelos de ONU existem - e o modelo e o que decide se ha cliente
#      TR-069 no firmware. Nao ha configuracao que resolva a falta dele.
#   3. Quantas ONUs estao em BRIDGE. Nessas, o Wi-Fi que o assinante usa e o do
#      roteador dele, nao o da ONU: o ACS alcanca a ONU e muda uma rede que
#      ninguem enxerga. Sao os contratos que precisam ficar em WIFI_MODO
#      chamado para sempre, e saber quantos sao ANTES evita descobrir pelo
#      cliente reclamando que "mudou e nao mudou".
#
# Uso (no servidor, de dentro de ~/sgpChat/sgpChat):
#   bash levantar-parque.sh
#   bash levantar-parque.sh --csv > parque.csv    # uma linha por ONU
set -uo pipefail
cd "$(dirname "$0")"

[[ -f .env ]] || { echo "[x] .env nao encontrado. Rode de dentro de ~/sgpChat/sgpChat"; exit 1; }
set -a; source .env; set +a
: "${SGP_API_URL:?falta SGP_API_URL no .env}"

CSV=0
[[ "${1:-}" == "--csv" ]] && CSV=1

NET=$(docker inspect botsgp-n8n \
      --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')

[[ $CSV -eq 1 ]] || echo "Consultando $SGP_API_URL ... (a base inteira; pode demorar)"

# Sem filtro: queremos o parque todo, nao um contrato. O endpoint aceita e
# devolve o array completo.
RESP=$(docker run --rm --network "$NET" curlimages/curl:latest -s -m 180 \
  -G "${SGP_API_URL}/api/fttx/onu/list/" \
  --data-urlencode "token=${SGP_API_TOKEN}" \
  --data-urlencode "app=${SGP_APP_NAME}")

printf '%s' "$RESP" | CSV=$CSV python3 -c '
import json, os, sys, collections

csv = os.environ.get("CSV") == "1"
try:
    onus = json.load(sys.stdin)
except Exception:
    sys.stderr.write("[x] resposta do SGP nao e JSON. Confira token/app:\n")
    sys.stderr.write(sys.stdin.read()[:400] + "\n")
    raise SystemExit(1)

if isinstance(onus, dict):
    # Algumas versoes envelopam a lista; aceita os dois formatos.
    onus = onus.get("results") or onus.get("onus") or []
if not onus:
    print("[!] nenhuma ONU retornada. Ou a base nao tem FTTx cadastrado,")
    print("    ou o token nao tem permissao no modulo.")
    raise SystemExit(0)

if csv:
    campos = ("olt_name", "type", "mode", "phy_addr", "service_contrato")
    print("olt,modelo,modo,serial,contrato")
    for o in onus:
        linha = []
        for k in campos:
            v = str(o.get(k) if o.get(k) is not None else "")
            # Nome de OLT com virgula quebraria a coluna seguinte.
            linha.append('"' + v.replace('"', '""') + '"' if "," in v or '"' in v else v)
        print(",".join(linha))
    raise SystemExit(0)

# O prefixo do phy_addr e o vendor ID do GPON: ZTEG=ZTE, HWTC=Huawei,
# FHTT=Fiberhome, CMSZ/GPON=varios. E mais confiavel que o nome que o
# provedor deu a OLT, que costuma ser apelido interno.
VENDOR = {"ZTEG": "ZTE", "HWTC": "Huawei", "FHTT": "Fiberhome", "CXNK": "Nokia",
          "DTMK": "Datacom", "PARK": "Parks", "ALCL": "Nokia/ALU",
          "TPLG": "TP-Link", "INTB": "Intelbras", "RTKG": "Realtek"}

def conta(campo, transform=None):
    c = collections.Counter()
    for o in onus:
        v = o.get(campo) or "(vazio)"
        c[transform(v) if transform else v] += 1
    return c

def bloco(titulo, contador, nota=None):
    print("\n" + titulo)
    print("-" * len(titulo))
    total = sum(contador.values())
    for k, n in contador.most_common(20):
        print("  %-28s %6d  (%4.1f%%)" % (str(k)[:28], n, 100.0 * n / total))
    if len(contador) > 20:
        print("  ... e mais %d" % (len(contador) - 20))
    if nota:
        print("  " + nota)

print("\n%d ONUs na base." % len(onus))

bloco("OLTs", conta("olt_name"))
bloco("Fabricante (pelo prefixo do serial GPON)",
      conta("phy_addr", lambda v: "(sem serial)" if v == "(vazio)"
            else VENDOR.get(str(v)[:4].upper(), "desconhecido: " + str(v)[:4].upper())))
bloco("Modelos de ONU", conta("type"),
      "^ o modelo decide se ha TR-069 no firmware. Confira os 3-4 primeiros.")

modos = conta("mode")
bloco("Modo de conexao", modos)

bridge = sum(n for m, n in modos.items() if "bridge" in str(m).lower())
if bridge:
    print("\n  >> %d ONUs em bridge (%.1f%% da base)." % (bridge, 100.0 * bridge / len(onus)))
    print("     Nesses contratos o Wi-Fi e do roteador do cliente, fora do")
    print("     alcance do ACS. Mantenha-os no atendimento por chamado.")
else:
    print("\n  >> Nenhuma ONU marcada como bridge neste campo. Bom sinal, mas")
    print("     confirme numa ONU real: nem todo SGP preenche esse campo.")

print("\nProximo passo: escolha UMA ONU de alguem da equipe, num modelo dos mais")
print("comuns acima, e provisione so ela por serial - nunca por perfil.")
'
