#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes do gerador de migracao de perfil.

    python3 test-migrar-perfil.py

O que este arquivo protege e simples de dizer: uma ONU migrada pela metade tira
o assinante do ar. Entao o que se testa aqui e principalmente o que o gerador
NAO deve fazer - nao esquecer o service-port, nao inventar linha, e nao emitir
bloco nenhum quando nao entendeu a configuracao.
"""
import io
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("mig", os.path.join(AQUI, "migrar-perfil.py"))
mig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mig)

falhas = []
total = [0]


def check(condicao, descricao):
    total[0] += 1
    if not condicao:
        falhas.append(descricao)
        print("  FALHOU: " + descricao)


# A configuracao abaixo e a da ONU do Ygson, como ela estava na OLT em
# 04/09/2026, mais vizinhas construidas para exercitar os casos de recusa.
CONFIG = """!
interface gpon_olt-1/2/2
 onu 1 type F670L sn ZTEGDA11A47B
 onu 2 type F670L sn HWTC485A7AAD
 onu 3 type hg8145v5 sn HWTC11112222
 onu 4 type RCNET-HGU sn ZTEGAAAA0001
 onu 5 type F670L sn ZTEGBBBB0002
 onu 6 type F670L sn ZTEGCCCC0003
!
interface gpon_onu-1/2/2:1
 real-speed gpon
 tcont 1 profile SMARTOLT-1G-UP
 gemport 1 tcont 1
 gemport 2 tcont 1
!
pon-onu-mng gpon_onu-1/2/2:1
 mgmt-ip 10.200.0.50 255.255.252.0 vlan 600 priority 0 route 0.0.0.0 0.0.0.0 10.200.0.1 host 1
 service vlan200 gemport 1 vlan 200
 service mgmt gemport 2 vlan 600
 veip 1
 tr069-mgmt 1 state unlock acs 179.197.226.140:7547 validate basic username acs password acs tag pri 0 vlan 600
!
interface vport-1/2/2.1:1
 service-port 1 user-vlan 200 vlan 28
 qos traffic-policy SMARTOLT-1G-DOWN direction egress
!
interface vport-1/2/2.1:2
 service-port 2 user-vlan 600 vlan 600
!
interface gpon_onu-1/2/2:2
 tcont 1 profile SMARTOLT-1G-UP
 gemport 1 tcont 1
!
pon-onu-mng gpon_onu-1/2/2:2
 service vlan200 gemport 1 vlan 200
 veip 1
!
interface vport-1/2/2.2:1
 service-port 1 user-vlan 200 vlan 28
 qos traffic-policy SMARTOLT-1G-DOWN direction egress
!
interface gpon_onu-1/2/2:3
 tcont 1 profile SMARTOLT-1G-UP
 gemport 1 tcont 1
!
pon-onu-mng gpon_onu-1/2/2:3
 service vlan200 gemport 1 vlan 200
 veip 1
!
interface vport-1/2/2.3:1
 service-port 1 user-vlan 200 vlan 28
!
interface gpon_onu-1/2/2:5
 tcont 1 profile SMARTOLT-1G-UP
 gemport 1 tcont 1
!
pon-onu-mng gpon_onu-1/2/2:5
 service vlan200 gemport 1 vlan 200
 veip 1
!
interface gpon_onu-1/2/2:6
 tcont 1 profile SMARTOLT-1G-UP
 gemport 1 tcont 1
 multicast-vlan 700
!
interface vport-1/2/2.6:1
 service-port 1 user-vlan 200 vlan 28
!
interface gpon_olt-1/1/1
 onu 4 type F670L sn ZTEGD420E6A8
 onu 19 type F670L sn HWTC611D44AC
!
interface gpon_onu-1/1/1:19
 real-speed gpon
 tcont 1 profile SMARTOLT-1G-UP
 gemport 1 tcont 1
!
pon-onu-mng gpon_onu-1/1/1:19
 service 1 gemport 1 vlan 200
 vlan port veip_1 mode tag vlan 200
!
interface vport-1/1/1.19:1
 service-port 1 user-vlan 200 vlan 11
!
interface gpon_onu-1/1/1:4
 tcont 1 profile SMARTOLT-1G-UP
 gemport 1 tcont 1
!
pon-onu-mng gpon_onu-1/1/1:4
 service vlan200 gemport 1 vlan 200
 veip 1
!
interface vport-1/1/1.4:1
 service-port 1 user-vlan 200 vlan 11
 qos traffic-policy SMARTOLT-1G-DOWN direction egress
!
"""


# ------------------------------------------------------------------ coleta
print("=== Coleta ===")

onus = mig.coletar(CONFIG)
check(len(onus) == 8, "achou as oito ONUs (achou %d)" % len(onus))

ygson = onus[("1", "2", "2", "1")]
check(ygson.tipo == "F670L", "leu o perfil da ONU")
check(ygson.sn == "ZTEGDA11A47B", "leu o numero de serie")
check(ygson.endereco == "gpon_onu-1/2/2:1", "monta o endereco com o chassi")
check(ygson.olt == "gpon_olt-1/2/2", "monta o endereco da porta da OLT")
check(ygson.vport("2") == "vport-1/2/2.1:2", "monta o endereco do vport")
check(len(ygson.vports) == 2, "achou os dois service-ports")
check(len(ygson.linhas_mng) == 5, "achou as cinco linhas de pon-onu-mng")


# ---------------------------------------------------------------- conferencia
print("=== O que pode e o que nao pode migrar ===")

check(mig.conferir(onus[("1", "2", "2", "1")]), "ONU completa pode migrar")

sem_qos = onus[("1", "2", "2", "3")]
check(mig.conferir(sem_qos), "ONU sem politica de QoS ainda pode migrar")

sem_vport = onus[("1", "2", "2", "5")]
check(not mig.conferir(sem_vport), "ONU sem service-port e recusada")
check(any("service-port" in p for p in sem_vport.problemas),
      "e o motivo diz que e o service-port")

desconhecida = onus[("1", "2", "2", "6")]
check(not mig.conferir(desconhecida), "linha desconhecida faz recusar")
check(any("multicast-vlan" in p for p in desconhecida.problemas),
      "e o motivo mostra qual linha")


# ---------------------------------------------------------------------- bloco
print("=== O bloco gerado ===")

texto = "\n".join(mig.bloco(onus[("1", "2", "2", "1")]))

check("no onu 1" in texto, "apaga a ONU")
check("onu 1 type RCNET-HGU sn ZTEGDA11A47B" in texto,
      "recria no perfil novo com o mesmo serial")
check("tcont 1 profile SMARTOLT-1G-UP" in texto, "recoloca o tcont")
gemports = [l for l in texto.splitlines() if l.strip().startswith("gemport ")]
check(len(gemports) == 2, "recoloca os dois gemports (achou %d)" % len(gemports))
check("service vlan200 gemport 1 vlan 200" in texto, "recoloca o servico de internet")
check("veip 1" in texto, "recoloca o veip")
check("mgmt-ip 10.200.0.50" in texto, "recoloca o IP de gerencia")
check("tr069-mgmt 1 state unlock" in texto, "recoloca o TR-069")

# O que derrubou dois clientes em campo. Se algum dia isto falhar, e porque
# alguem mexeu no gerador sem entender o que ele existe para evitar.
check("service-port 1 user-vlan 200 vlan 28" in texto,
      "RECOLOCA O SERVICE-PORT - e o que faltou e tirou clientes do ar")
check("service-port 2 user-vlan 600 vlan 600" in texto,
      "recoloca tambem o segundo service-port")
check("qos traffic-policy SMARTOLT-1G-DOWN direction egress" in texto,
      "recoloca a politica de QoS")
check("interface vport-1/2/2.1:1" in texto and "interface vport-1/2/2.1:2" in texto,
      "entra nos dois vports pelo endereco certo")

check("real-speed" not in texto, "nao redigita 'real-speed', que e estado")
check(texto.rstrip().endswith("!") or "show service-port interface" in texto,
      "termina com as conferencias")
check("show gpon onu detail-info gpon_onu-1/2/2:1" in texto,
      "manda conferir o estado da ONU")

# A ordem importa: apagar, recriar, e so entao devolver o que estava.
i_no = texto.index("no onu 1")
i_tipo = texto.index("onu 1 type RCNET-HGU")
i_sp = texto.index("service-port 1")
check(i_no < i_tipo < i_sp, "a ordem e apagar, recriar, restaurar")


print("=== VLAN de uplink ===")

check(mig.vlan_uplink("1", "1") == 11, "1/1/1 -> VLAN 11")
check(mig.vlan_uplink("1", "2") == 12, "1/1/2 -> VLAN 12")
check(mig.vlan_uplink("2", "2") == 28, "1/2/2 -> VLAN 28")
check("deveria ser 28" in texto, "o bloco mostra a VLAN esperada da PON")

texto_466 = "\n".join(mig.bloco(onus[("1", "1", "1", "4")]))
check("service-port 1 user-vlan 200 vlan 11" in texto_466,
      "usa a VLAN que estava na configuracao, nao a de outra PON")
check("deveria ser 11" in texto_466, "e a esperada bate com a formula")


print("=== ONU em bridge, Huawei com perfil de ZTE ===")

bridge = onus[("1", "1", "1", "19")]
check(mig.conferir(bridge), "ONU em bridge pode migrar")
texto_br = "\n".join(mig.bloco(bridge))
check("onu 19 type RCNET-HW sn HWTC611D44AC" in texto_br,
      "equipamento Huawei vai para o RCNET-HW mesmo com perfil F670L")
check("vlan port veip_1 mode tag vlan 200" in texto_br,
      "recoloca a marcacao de VLAN do veip, que so existe nas de bridge")
check("service 1 gemport 1 vlan 200" in texto_br, "recoloca o servico")
check("service-port 1 user-vlan 200 vlan 11" in texto_br,
      "recoloca o service-port com a VLAN da PON 1/1/1")
check("qos traffic-policy" not in texto_br,
      "nao inventa politica de QoS onde nao havia")


# O 'show running-config' completo desta OLT separa secoes com '$', nao com '!',
# e algumas ONUs tem 'name'/'description' que a OLT grava na autenticacao. Antes
# do conserto, isso fazia o gerador pular TODAS as ONUs (246 de 246 em campo).
CONFIG_DOLAR = """interface gpon_olt-1/1/1
 onu 5 type F670L sn ZTEGD425C2F9
$
interface gpon_onu-1/1/1:5
 real-speed gpon
 name joao033
 description zone_Zone_authd_20250922
 tcont 1 profile SMARTOLT-1G-UP
 gemport 1 tcont 1
$
pon-onu-mng gpon_onu-1/1/1:5
 service 1 gemport 1 vlan 200
 vlan port veip_1 mode tag vlan 200
$
interface vport-1/1/1.5:1
 service-port 1 user-vlan 11 vlan 11
 qos traffic-policy SMARTOLT-1G-DOWN direction egress
$
"""

print("=== Formato real: separador $ e name/description ===")
od = mig.coletar(CONFIG_DOLAR)
check(len(od) == 1, "acha a ONU mesmo com separador $ (achou %d)" % len(od))
odu = od[("1", "1", "1", "5")]
check(odu.sn == "ZTEGD425C2F9", "le o serial na secao delimitada por $")
check(mig.conferir(odu), "ONU com name/description NAO e pulada")
td = "\n".join(mig.bloco(odu))
check("onu 5 type RCNET-HGU sn ZTEGD425C2F9" in td, "migra a ONU do formato real")
check("service-port 1 user-vlan 11 vlan 11" in td, "recoloca o service-port")
check("name joao033" not in td, "NAO redigita o name (a OLT repoe na auth)")
check("description zone_" not in td, "NAO redigita a description")
check("$" not in td, "o separador $ nao vaza para os comandos")


print("=== Destino por fabricante ===")

huawei = "\n".join(mig.bloco(onus[("1", "2", "2", "3")]))
check("onu 3 type RCNET-HW" in huawei, "perfil Huawei vai para o RCNET-HW")

ja_migrada = onus[("1", "2", "2", "4")]
check(ja_migrada.tipo in mig.DESTINO.values(),
      "ONU ja no perfil novo e reconhecida como pronta")


print("")
if falhas:
    print("%d de %d falharam" % (len(falhas), total[0]))
    sys.exit(1)
print("%d testes, todos passaram" % total[0])
