#!/usr/bin/env bash
# Migra uma PON inteira de ONUs ZTE (perfil F670L) para RCNET-HGU e confere
# cada ONU. NAO toca em Huawei (essas trocam pelo ACS, nao dependem do onu-type).
#
#   bash migrar-pon.sh 1/2
#
# Rode da pasta do projeto (onde esta o docker-compose.yml). Precisa do
# running-config.txt ja salvo aqui (veja README/handoff). Faz, por PON:
#   1) gera o bloco de migracao (migrar-perfil.py, so ZTEG, so esta PON)
#   2) aplica na OLT pela sessao SSH do container olt-wifi (que tem a credencial)
#   3) espera o re-registro e imprime PASS/FALHA por ONU
#
# CADA ONU CAI POR ALGUNS MINUTOS. Rode de madrugada. Se der FALHA, PARE e
# confira aquela ONU antes de seguir - e o que deixa o cliente com "ISP Timeout".
#
# Serial duplicado (mesmo SN em duas posicoes) aparece como FALHA na posicao
# fantasma (o GPON so registra um SN por vez) - e esperado; confira qual posicao
# esta online e deixe so ela.
set -euo pipefail

PON="${1:?uso: bash migrar-pon.sh slot/pon   (ex: 1/2)}"
CFG="${CFG:-running-config.txt}"
ESPERA="${ESPERA:-90}"
ARQ="migrar-$(echo "$PON" | tr '/' '-').txt"

[ -f "$CFG" ] || { echo "nao achei $CFG nesta pasta"; exit 1; }

python3 migrar-perfil.py "$CFG" --fabricante ZTEG --pon "$PON" > "$ARQ"
QTD=$(grep -c '^! ----' "$ARQ" || true)
echo "== PON $PON: $QTD ONU(s) para migrar -> $ARQ =="
[ "$QTD" -gt 0 ] || { echo "nada a migrar nesta PON"; exit 0; }

APLICAR='
import os,sys,time,paramiko
cmds=[l.rstrip() for l in sys.stdin if l.strip()]
H=os.environ["OLT_HOST"];U=os.environ["OLT_USER"];PW=os.environ["OLT_PASS"]
c=paramiko.SSHClient();c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(H,port=int(os.environ.get("OLT_PORT","22")),username=U,password=PW,timeout=20,look_for_keys=False,allow_agent=False)
ch=c.invoke_shell(width=256,height=256);time.sleep(2)
def drain(t):
    e=time.time()+t
    while time.time()<e:
        if ch.recv_ready(): ch.recv(65535); e=time.time()+0.4
        else: time.sleep(0.05)
for cmd in cmds:
    sys.stderr.write("> "+cmd+"\n"); ch.send(cmd+"\n"); drain(1.2)
drain(6)
'

CONFERIR='
import os,sys,time,re,paramiko
data=sys.stdin.read()
addrs=re.findall(r"gpon_onu-\d+/\d+/\d+:\d+","\n".join(l for l in data.splitlines() if l.strip().startswith("! ----")))
H=os.environ["OLT_HOST"];U=os.environ["OLT_USER"];PW=os.environ["OLT_PASS"]
more=lambda l:"More" in l and "--" in l;RE=re.compile("-+ *More *-+")
c=paramiko.SSHClient();c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(H,port=int(os.environ.get("OLT_PORT","22")),username=U,password=PW,timeout=20,look_for_keys=False,allow_agent=False)
ch=c.invoke_shell(width=256,height=256)
def rd(t=6):
    o="";e=time.time()+t
    while time.time()<e:
        if ch.recv_ready():
            d=ch.recv(65535).decode("utf-8","replace");o+=d
            if any(more(l) for l in d.splitlines()):ch.send(" ")
            e=time.time()+0.4
        else:time.sleep(0.05)
    return RE.sub("\n",o).replace("\r","").replace("\x08","")
rd(2)
falhas=0
for a in addrs:
    ch.send("show gpon onu detail-info %s\n"%a);d1=rd(6)
    ch.send("show service-port interface %s\n"%a);d2=rd(6)
    st=("success" in d1.lower() and "working" in d1.lower())
    sp=[x for x in d2.splitlines() if re.match(r"\s*\d+\s+\d+",x)]
    ok=bool(sp) and all(("OK" in x and "YES" in x) for x in sp)
    if not (st and ok): falhas+=1
    print(("PASS  " if (st and ok) else "FALHA ")+a+("" if (st and ok) else "  config=%s sport=%s"%(st,ok)))
sys.exit(1 if falhas else 0)
'

echo "== aplicando na OLT =="
grep -vE '^[[:space:]]*(!|$)' "$ARQ" | docker compose exec -T olt-wifi python3 -c "$APLICAR"

echo "== aguardando ${ESPERA}s o re-registro =="
sleep "$ESPERA"

echo "== conferindo =="
docker compose exec -T olt-wifi python3 -c "$CONFERIR" < "$ARQ" \
  && echo "== PON $PON OK ==" \
  || echo "== PON $PON TEM FALHA - PARE e confira acima antes da proxima =="
