#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Testes do intermediario, sem OLT e sem rede.

Rode com: python3 olt-wifi/test-servico.py

A OLT nao e simulavel de verdade, mas as duas coisas que podem estragar a rede
de um assinante sao: montar um comando errado, e escrever numa ONU cujo perfil
nao aceita as duas bandas. As duas dao para testar aqui, e e o que este arquivo
faz.
"""
import os
import sys
import types

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

# O servico importa paramiko no topo. Instalar paramiko so para rodar teste seria
# atrito a toa: o que interessa aqui nao e o SSH, e o que se decide antes dele.
if "paramiko" not in sys.modules:
    try:
        import paramiko  # noqa: F401
    except ImportError:
        falso = types.ModuleType("paramiko")

        class _Politica(object):
            pass

        falso.AutoAddPolicy = _Politica
        falso.SSHClient = object
        sys.modules["paramiko"] = falso

import servico  # noqa: E402

falhas = []
total = [0]


def check(condicao, descricao):
    total[0] += 1
    if not condicao:
        falhas.append(descricao)
        print("  FALHOU: " + descricao)


# ------------------------------------------------------------------ validacao
print("=== Validacao ===")

d, erro = servico.validar({"onu": "gpon_onu-1/2/2:1", "ssid": "Casa-do-Joao"})
check(erro is None and d["ssid"] == "Casa-do-Joao", "pedido simples e aceito")

_, erro = servico.validar({"onu": "1/2/2:1", "ssid": "X"})
check(erro == "onu_invalida", "endereco de ONU fora do formato e recusado")

_, erro = servico.validar({"onu": "gpon_onu-1/2/2:1", "ssid": "Casa do Joao"})
check(erro == "ssid_invalido", "espaco no nome da rede e recusado")

# O CLI da ZTE abre a ajuda contextual no meio da linha quando ve '?', e isso
# derruba a sessao inteira num estado imprevisivel.
_, erro = servico.validar({"onu": "gpon_onu-1/2/2:1", "ssid": "Casa?"})
check(erro == "ssid_invalido", "interrogacao e recusada")

# Quebra de linha encerraria o comando e transformaria o resto em comando novo.
_, erro = servico.validar({"onu": "gpon_onu-1/2/2:1", "senha": "senha\nend\nreboot"})
check(erro == "senha_invalida", "quebra de linha na senha e recusada")

_, erro = servico.validar({"onu": "gpon_onu-1/2/2:1", "senha": "curta"})
check(erro == "senha_invalida", "senha abaixo de 8 caracteres e recusada")

_, erro = servico.validar({"onu": "gpon_onu-1/2/2:1"})
check(erro == "nada_a_alterar", "pedido vazio e recusado")


# ------------------------------------------------------------------- comandos
print("=== Comandos ===")

cmds = servico.montar_comandos({"onu": "gpon_onu-1/2/2:1", "ssid": "Rede",
                                "senha": "SenhaBoa123"})
check(cmds[0] == "configure terminal", "entra em modo de configuracao")
check(cmds[-1] == "end", "sai do modo de configuracao")
check(any("wifi_0/1 name Rede" in c for c in cmds), "renomeia a rede de 2.4 GHz")
check(any("wifi_0/5 name Rede" in c for c in cmds), "renomeia a rede de 5 GHz")
check(any("wifi_0/1 key SenhaBoa123" in c for c in cmds), "troca a senha de 2.4 GHz")
check(any("wifi_0/5 key SenhaBoa123" in c for c in cmds), "troca a senha de 5 GHz")

# Campo omitido nao vira comando: quem pediu so a senha nao pode ter a rede
# renomeada de brinde.
so_senha = servico.montar_comandos({"onu": "gpon_onu-1/2/2:1", "ssid": None,
                                    "senha": "SenhaBoa123"})
check(not any(" name " in c for c in so_senha),
      "pedido so de senha nao renomeia a rede")


# --------------------------------------------------------------------- perfil
print("=== Leitura do perfil ===")

SAIDA_OLT = """
ONU interface:          gpon_onu-1/2/2:1
  Name:
  Type:                 RCNET-HGU
  Admin state:          enable
  Serial number:        ZTEGDA11A47B
"""

check(servico.perfil_de(SAIDA_OLT) == "RCNET-HGU", "le o perfil da saida da OLT")
check(servico.perfil_de(SAIDA_OLT.replace("RCNET-HGU", "F670L")) == "F670L",
      "le tambem o perfil antigo")
check(servico.perfil_de("") is None, "saida vazia nao inventa perfil")
check(servico.perfil_de("Erro qualquer") is None, "saida sem Type nao inventa perfil")

# A linha "Name:" vem antes e esta vazia - nao pode ser confundida com o tipo.
check(servico.perfil_de(SAIDA_OLT) != "", "linha Name vazia nao vira perfil")

antes = servico.PERFIS_OK
servico.PERFIS_OK = ["RCNET-HGU", "RCNET-HW"]
check(servico.perfil_serve("RCNET-HGU"), "perfil migrado serve")
check(servico.perfil_serve("RCNET-HW"), "perfil Huawei migrado serve")
check(not servico.perfil_serve("F670L"), "perfil antigo nao serve")
check(not servico.perfil_serve("hg8145v5"), "perfil Huawei antigo nao serve")
servico.PERFIS_OK = ["*"]
check(servico.perfil_serve("F670L"), "asterisco desliga a conferencia")
servico.PERFIS_OK = antes


# ------------------------------------------------------------------ paginador
print("=== Paginador ===")

check(servico._e_paginador("---- More ----"), "reconhece o paginador")
check(servico._e_paginador("--More--"), "reconhece a outra forma do paginador")
check(not servico._e_paginador("  Type:  RCNET-HGU"), "nao confunde linha normal")

# Depois da tecla, a OLT continua escrevendo na mesma linha do aviso. Descartar a
# linha inteira levaria junto o que interessa - foi o erro que este teste pegou.
colado = "ONU interface: x\n---- More ----  Type:  RCNET-HGU\n"
check(servico.perfil_de(servico.RE_PAGINADOR.sub("\n", colado)) == "RCNET-HGU",
      "conteudo colado no aviso do paginador nao se perde")


class CanalFalso(object):
    """Um canal SSH de mentira: devolve respostas roteirizadas e guarda o que
    foi enviado, para o teste poder afirmar o que NAO foi escrito."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.enviados = []
        self.pendente = ""

    def send(self, texto):
        if texto.strip():
            self.enviados.append(texto.strip())
        self.pendente = self.respostas.pop(0) if self.respostas else ""

    def recv_ready(self):
        return bool(self.pendente)

    def recv(self, _n):
        dado, self.pendente = self.pendente, ""
        return dado.encode("utf-8")


# A saida vem em duas partes com o paginador no meio, como na OLT de verdade: a
# primeira metade ja esta no canal, e a segunda so chega depois da tecla.
canal = CanalFalso(["  Type:  RCNET-HGU\n"])
canal.pendente = "ONU interface: x\n---- More ----"
lido = servico._ler(canal, 0.6)
check("More" not in lido, "o paginador sai do texto lido")
check(servico.perfil_de(lido) == "RCNET-HGU", "le o perfil mesmo paginado")


# -------------------------------------------------------------- a guarda toda
print("=== A guarda, de ponta a ponta ===")


class ClienteFalso(object):
    """Substitui o paramiko.SSHClient. Nunca abre rede."""

    ultimo = None

    def __init__(self):
        self.canal = None
        ClienteFalso.ultimo = self

    def set_missing_host_key_policy(self, _p):
        pass

    def connect(self, *a, **k):
        pass

    def invoke_shell(self, **k):
        self.canal = CanalFalso(list(ClienteFalso.roteiro))
        self.canal.pendente = "banner de login\n"
        return self.canal

    def close(self):
        pass


def rodar_com_perfil(saida_do_show):
    ClienteFalso.roteiro = [saida_do_show] + ["ok\n"] * 12
    original = servico.paramiko.SSHClient
    servico.paramiko.SSHClient = ClienteFalso
    try:
        ok, detalhe = servico.aplicar({"onu": "gpon_onu-1/2/2:1",
                                       "ssid": "Rede", "senha": "SenhaBoa123"})
    finally:
        servico.paramiko.SSHClient = original
    return ok, detalhe, ClienteFalso.ultimo.canal.enviados


servico.PERFIS_OK = ["RCNET-HGU", "RCNET-HW"]

# O caso que motivou tudo isto: perfil antigo, que so tem a banda de 2.4 GHz.
ok, detalhe, enviados = rodar_com_perfil(SAIDA_OLT.replace("RCNET-HGU", "F670L"))
check(ok is False, "perfil sem 5 GHz e recusado")
check(detalhe == "perfil_sem_5g:F670L", "o motivo diz qual perfil barrou")
check(not any("ssid " in c for c in enviados),
      "NENHUM comando de ssid foi enviado - a rede do assinante nao foi tocada")
check(not any(c == "configure terminal" for c in enviados),
      "nem entrou em modo de configuracao")

# Perfil migrado: escreve normalmente.
ok, detalhe, enviados = rodar_com_perfil(SAIDA_OLT)
check(ok is True, "perfil migrado aplica")
check(any("wifi_0/1 name Rede" in c for c in enviados), "escreveu na banda de 2.4")
check(any("wifi_0/5 name Rede" in c for c in enviados), "escreveu na banda de 5 GHz")

# Se nao der para ler o perfil, tambem nao se escreve. Na duvida, nao mexe.
ok, detalhe, enviados = rodar_com_perfil("comando recusado\n")
check(ok is False and detalhe == "perfil_ilegivel", "perfil ilegivel e recusado")
check(not any("ssid " in c for c in enviados),
      "perfil ilegivel tambem nao escreve nada")

servico.PERFIS_OK = antes

# ---------------------------------------------------------------------- fecho
print("")
if falhas:
    print("%d de %d falharam" % (len(falhas), total[0]))
    sys.exit(1)
print("%d testes, todos passaram" % total[0])
