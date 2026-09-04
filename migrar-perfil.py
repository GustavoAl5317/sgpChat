#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gera os comandos para migrar ONUs para um perfil que declara as duas bandas.

    ssh admin@olt 'show running-config' > running-config.txt
    python3 migrar-perfil.py running-config.txt > migrar.txt

Nao toca na OLT. Le um arquivo e escreve texto - quem executa e voce, olhando.

POR QUE ISTO EXISTE
-------------------
A OLT so aceita escrever num indice de Wi-Fi que o perfil (onu-type) da ONU
declara. Os perfis antigos declaram so o de 2.4 GHz, entao o bot nao consegue
atender ninguem que esteja neles. Trocar o perfil exige apagar e recriar a ONU.

E ai esta o risco: o 'no onu' apaga junto o service-port, que e o que traduz a
VLAN do assinante para a do uplink. Duas conexoes foram derrubadas em campo
porque a restauracao foi feita de memoria e esqueceu exatamente isso. O sintoma
na ONT e "ISP Timeout", que parece problema de PPPoE e nao e.

Por isso este script nao inventa nada. Ele le a configuracao REAL de cada ONU e
devolve a restauracao completa. Se encontrar uma linha que nao sabe recolocar,
PULA a ONU e diz por que - preferir uma ONU nao migrada a uma ONU migrada pela
metade e a regra da casa.
"""
import argparse
import re
import sys

# Para onde vai cada perfil antigo. Os dois de destino ja existem na OLT e
# declaram wifi_0/1 ate wifi_0/8, alem de ex-omci enable.
DESTINO = {
    "F670L": "RCNET-HGU",
    "hg8145v5": "RCNET-HW",
    "hg8145v5-v2": "RCNET-HW",
}

# Linhas que o script sabe recolocar depois do 'no onu'. Qualquer outra faz a ONU
# ser pulada: recolocar o que nao se entende e como restaurar de memoria.
CONHECIDAS_ONU = ("real-speed", "tcont ", "gemport ")
CONHECIDAS_MNG = ("service ", "veip ", "mgmt-ip ", "tr069-mgmt ", "security-mgmt ")
CONHECIDAS_VPORT = ("service-port ", "qos traffic-policy ")

# 'real-speed' e estado, nao configuracao: a OLT recoloca sozinha e o comando nao
# existe para digitar.
NAO_REDIGITAR = ("real-speed",)

RE_OLT = re.compile(r"^\s*interface\s+gpon_olt-(\d+)/(\d+)/(\d+)\s*$")
RE_ONU_IF = re.compile(r"^\s*interface\s+gpon_onu-(\d+)/(\d+)/(\d+):(\d+)\s*$")
RE_MNG = re.compile(r"^\s*pon-onu-mng\s+gpon_onu-(\d+)/(\d+)/(\d+):(\d+)\s*$")
RE_VPORT = re.compile(r"^\s*interface\s+vport-(\d+)/(\d+)/(\d+)\.(\d+):(\d+)\s*$")
RE_ONU_DECL = re.compile(r"^\s*onu\s+(\d+)\s+type\s+(\S+)\s+sn\s+(\S+)\s*$")

# Uma secao termina quando comeca outra, ou num '!' sozinho.
RE_INICIO = re.compile(r"^\s*(interface\s+\S+|pon-onu-mng\s+\S+)\s*$")


def vlan_uplink(slot, pon):
    """VLAN do uplink desta rede: 10 + (slot-1)*16 + pon.

    Conferida em campo em tres portas (1/1/1 -> 11, 1/1/2 -> 12, 1/2/2 -> 28).
    So e usada quando a propria configuracao da ONU nao disser a VLAN - e ela
    quase sempre diz, entao isto e rede de seguranca, nao fonte de verdade."""
    return 10 + (int(slot) - 1) * 16 + int(pon)


def ler_secoes(texto):
    """Quebra o running-config em (cabecalho, [linhas])."""
    secoes = []
    atual = None
    for linha in texto.splitlines():
        if linha.strip() == "!":
            atual = None
            continue
        if RE_INICIO.match(linha):
            atual = (linha.strip(), [])
            secoes.append(atual)
            continue
        if atual is not None and linha.strip():
            atual[1].append(linha.strip())
    return secoes


class Onu(object):
    def __init__(self, chassi, slot, pon, num):
        self.chassi, self.slot, self.pon, self.num = chassi, slot, pon, num
        self.tipo = None
        self.sn = None
        self.linhas_onu = []
        self.linhas_mng = []
        self.vports = {}          # indice -> [linhas]
        self.problemas = []

    @property
    def endereco(self):
        return "gpon_onu-%s/%s/%s:%s" % (self.chassi, self.slot, self.pon, self.num)

    @property
    def olt(self):
        return "gpon_olt-%s/%s/%s" % (self.chassi, self.slot, self.pon)

    def vport(self, indice):
        return "vport-%s/%s/%s.%s:%s" % (self.chassi, self.slot, self.pon,
                                         self.num, indice)


def coletar(texto):
    """Junta, por ONU, tudo o que a configuracao diz sobre ela."""
    onus = {}

    def pegar(chassi, slot, pon, num):
        chave = (chassi, slot, pon, num)
        if chave not in onus:
            onus[chave] = Onu(chassi, slot, pon, num)
        return onus[chave]

    for cabecalho, linhas in ler_secoes(texto):
        m = RE_OLT.match(cabecalho)
        if m:
            chassi, slot, pon = m.group(1), m.group(2), m.group(3)
            for l in linhas:
                d = RE_ONU_DECL.match(l)
                if d:
                    o = pegar(chassi, slot, pon, d.group(1))
                    o.tipo, o.sn = d.group(2), d.group(3)
            continue

        m = RE_ONU_IF.match(cabecalho)
        if m:
            o = pegar(m.group(1), m.group(2), m.group(3), m.group(4))
            o.linhas_onu = list(linhas)
            continue

        m = RE_MNG.match(cabecalho)
        if m:
            o = pegar(m.group(1), m.group(2), m.group(3), m.group(4))
            o.linhas_mng = list(linhas)
            continue

        m = RE_VPORT.match(cabecalho)
        if m:
            o = pegar(m.group(1), m.group(2), m.group(3), m.group(4))
            o.vports[m.group(5)] = list(linhas)
            continue

    return onus


def conferir(o):
    """Diz por que esta ONU nao pode ser migrada com seguranca."""
    if not o.tipo or not o.sn:
        o.problemas.append("sem declaracao 'onu N type X sn Y'")
    if o.tipo and o.tipo not in DESTINO:
        o.problemas.append("perfil %s nao tem destino definido" % o.tipo)
    if not o.linhas_onu:
        o.problemas.append("sem tcont/gemport - nao sei o que recolocar")
    if not o.vports:
        o.problemas.append("sem service-port - migrar deixaria o cliente sem internet")

    for l in o.linhas_onu:
        if not l.startswith(CONHECIDAS_ONU):
            o.problemas.append("linha que nao sei recolocar: " + l)
    for l in o.linhas_mng:
        if not l.startswith(CONHECIDAS_MNG):
            o.problemas.append("linha que nao sei recolocar: " + l)
    for linhas in o.vports.values():
        for l in linhas:
            if not l.startswith(CONHECIDAS_VPORT):
                o.problemas.append("linha que nao sei recolocar: " + l)
    return not o.problemas


def bloco(o):
    """Os comandos de migracao desta ONU, na ordem em que devem ser colados."""
    fora = []
    add = fora.append
    novo = DESTINO[o.tipo]

    add("! ---- %s  (%s)  %s -> %s" % (o.endereco, o.sn, o.tipo, novo))
    add("configure terminal")
    add("interface %s" % o.olt)
    add(" no onu %s" % o.num)
    add(" onu %s type %s sn %s" % (o.num, novo, o.sn))
    add("exit")

    add("interface %s" % o.endereco)
    for l in o.linhas_onu:
        if not l.startswith(NAO_REDIGITAR):
            add(" " + l)
    add("exit")

    if o.linhas_mng:
        add("pon-onu-mng %s" % o.endereco)
        for l in o.linhas_mng:
            add(" " + l)
        add("exit")

    for indice in sorted(o.vports, key=lambda x: int(x)):
        add("interface %s" % o.vport(indice))
        for l in o.vports[indice]:
            add(" " + l)
        add("exit")

    add("end")
    add("! a VLAN de uplink desta PON deveria ser %d - se o service-port acima"
        % vlan_uplink(o.slot, o.pon))
    add("! disser outra coisa, confira antes de colar")
    add("! conferir antes de seguir para a proxima:")
    add("!   show gpon onu detail-info %s" % o.endereco)
    add("!     -> Config state deve ser 'success' e o Type o novo")
    add("!   show service-port interface %s" % o.endereco)
    add("!     -> todos os Sport em OK / YES")
    add("")
    return fora


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("arquivo", help="saida de 'show running-config'")
    p.add_argument("--pon", help="so esta PON, no formato slot/pon (ex: 1/2)")
    p.add_argument("--tipo", action="append",
                   help="so estes perfis de origem (pode repetir)")
    p.add_argument("--limite", type=int, help="no maximo N ONUs")
    args = p.parse_args()

    try:
        with open(args.arquivo, encoding="utf-8", errors="replace") as f:
            texto = f.read()
    except IOError as e:
        sys.stderr.write("nao consegui ler %s: %s\n" % (args.arquivo, e))
        return 2

    onus = coletar(texto)
    if not onus:
        sys.stderr.write("nenhuma ONU encontrada - o arquivo e mesmo um "
                         "'show running-config' completo?\n")
        return 2

    migrar, pular, prontas = [], [], []
    for chave in sorted(onus, key=lambda k: tuple(int(x) for x in k)):
        o = onus[chave]
        if args.pon and "%s/%s" % (o.slot, o.pon) != args.pon:
            continue
        if args.tipo and o.tipo not in args.tipo:
            continue
        if o.tipo in DESTINO.values():
            prontas.append(o)
            continue
        if conferir(o):
            migrar.append(o)
        else:
            pular.append(o)

    if args.limite:
        migrar = migrar[:args.limite]

    print("! Migracao de perfil de ONU - gerado por migrar-perfil.py")
    print("!")
    print("! %d para migrar | %d ja no perfil novo | %d puladas"
          % (len(migrar), len(prontas), len(pular)))
    print("!")
    print("! CADA ONU CAI POR ALGUNS MINUTOS. Avise antes, ou rode de madrugada.")
    print("! Rode uma PON por vez e confira as duas linhas de 'show' ao fim de")
    print("! cada bloco. Se um Sport nao voltar em OK/YES, PARE: e exatamente o")
    print("! que deixa o assinante com 'ISP Timeout' e sem internet.")
    print("!")

    if pular:
        print("! ---------------- PULADAS, precisam de olho humano ----------------")
        for o in pular:
            print("! %s (%s): %s" % (o.endereco, o.tipo or "sem tipo",
                                     "; ".join(o.problemas)))
        print("!")

    for o in migrar:
        for linha in bloco(o):
            print(linha)

    if migrar:
        print("write")

    sys.stderr.write("%d para migrar, %d ja prontas, %d puladas\n"
                     % (len(migrar), len(prontas), len(pular)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
