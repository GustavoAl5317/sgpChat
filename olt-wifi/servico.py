#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Intermediario entre o bot e a OLT.

O bot NAO fala com a OLT. Ele pede a este servico "troque o Wi-Fi da ONU X para
o nome Y e a senha Z", e e aqui que a credencial da OLT vive.

O motivo e o tamanho do estrago: a OLT controla a rede inteira do provedor. Se o
n8n tivesse a senha dela, um comprometimento do bot - que conversa com o publico
pelo WhatsApp - viraria controle total da rede. Com este servico no meio, o bot
so consegue pedir esta operacao, com estes parametros, nesta ONU. Nao ha caminho
para executar outra coisa.

Por isso os comandos sao montados AQUI, a partir de campos validados, e nunca
recebidos prontos.
"""
import json
import os
import re
import sys
import time
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paramiko

log = logging.getLogger("olt-wifi")

OLT_HOST = os.environ.get("OLT_HOST", "")
OLT_PORT = int(os.environ.get("OLT_PORT", "22"))
OLT_USER = os.environ.get("OLT_USER", "")
OLT_PASS = os.environ.get("OLT_PASS", "")
TOKEN = os.environ.get("OLT_WIFI_TOKEN", "")
PORTA = int(os.environ.get("OLT_WIFI_PORT", "8080"))

# Indices de SSID por banda. Nas ZTE, 1-4 sao da radio de 2.4 GHz e 5-8 da de
# 5 GHz - confirmado em campo em 31/08/2026 (wifi_0/1 mudou a rede de 2.4 e
# wifi_0/5 mudou a de 5 GHz do mesmo aparelho).
#
# So a PRIMEIRA de cada banda entra. As outras sao redes de visitantes, e
# renomear a rede de visitantes de alguem que nao pediu isso e mexer no que nao
# foi autorizado.
WIFI_24 = os.environ.get("OLT_WIFI_IF_24", "wifi_0/1")
WIFI_5G = os.environ.get("OLT_WIFI_IF_5G", "wifi_0/5")

# ---------------------------------------------------------------- validacao
#
# Estes campos vem de uma pessoa digitando no WhatsApp e terminam numa linha de
# comando de switch. Whitelist, nunca blacklist.
#
# Dois caracteres merecem atencao especial no CLI da ZTE:
#   ?  abre a ajuda contextual no meio da linha e quebra a sessao inteira
#   \n encerra a linha e o resto vira um comando novo
# Nenhum dos dois pode chegar perto do SSH.
RE_ONU = re.compile(r"^gpon_onu-\d{1,2}/\d{1,2}/\d{1,2}:\d{1,3}$")
RE_SSID = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
RE_SENHA = re.compile(r"^[A-Za-z0-9!@#$%^&*()_+=,.:;<>\[\]{}|~-]{8,63}$")


def validar(corpo):
    """Devolve (dados, erro). Erro e uma string para o log - nunca vai ao cliente."""
    onu = str(corpo.get("onu") or "").strip()
    if not RE_ONU.match(onu):
        return None, "onu_invalida"

    ssid = corpo.get("ssid")
    senha = corpo.get("senha")
    ssid = str(ssid).strip() if ssid else None
    senha = str(senha) if senha else None

    if ssid is not None and not RE_SSID.match(ssid):
        return None, "ssid_invalido"
    if senha is not None and not RE_SENHA.match(senha):
        return None, "senha_invalida"
    if ssid is None and senha is None:
        return None, "nada_a_alterar"

    return {"onu": onu, "ssid": ssid, "senha": senha}, None


def montar_comandos(d):
    """Monta a sequencia exata. Nada aqui vem pronto do bot."""
    cmds = ["configure terminal", "pon-onu-mng " + d["onu"]]
    for iface in (WIFI_24, WIFI_5G):
        if d["ssid"]:
            cmds.append("ssid ctrl %s name %s" % (iface, d["ssid"]))
        if d["senha"]:
            cmds.append("ssid auth wpa %s key %s" % (iface, d["senha"]))
    cmds.append("end")
    return cmds


# -------------------------------------------------------------------- SSH
def _ler(canal, ate=2.0):
    """Le o que a OLT devolveu. O CLI e interativo e nao tem marcador de fim
    confiavel, entao a leitura e por silencio: para quando nao chega mais nada."""
    saida = ""
    fim = time.time() + ate
    while time.time() < fim:
        if canal.recv_ready():
            saida += canal.recv(65535).decode("utf-8", "replace")
            fim = time.time() + 0.4
        else:
            time.sleep(0.05)
    return saida


def aplicar(d):
    """Executa na OLT. Devolve (ok, detalhe)."""
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        cli.connect(OLT_HOST, port=OLT_PORT, username=OLT_USER, password=OLT_PASS,
                    timeout=20, banner_timeout=20, auth_timeout=20,
                    look_for_keys=False, allow_agent=False)
    except Exception as e:
        log.error("ssh falhou: %s", type(e).__name__)
        return False, "olt_inacessivel"

    try:
        canal = cli.invoke_shell(width=200, height=200)
        _ler(canal, 3.0)  # banner de login

        for cmd in montar_comandos(d):
            canal.send(cmd + "\n")
            resp = _ler(canal)

            # A OLT nao usa codigo de saida: quem diz que deu errado e o texto.
            if "%Error" in resp or "Invalid input" in resp:
                # A resposta pode ecoar o comando - e o comando carrega a senha.
                linha = [l for l in resp.splitlines() if "%Error" in l or "Invalid input" in l]
                erro = (linha[0].strip() if linha else "erro")[:120]
                log.error("comando recusado pela OLT: %s", erro)
                # Sai do modo de configuracao antes de largar a sessao, para nao
                # deixar a proxima conexao caindo num contexto estranho.
                canal.send("end\n")
                _ler(canal, 1.0)
                return False, erro

        return True, "ok"
    except Exception as e:
        log.error("falha na sessao: %s", type(e).__name__)
        return False, "sessao_falhou"
    finally:
        try:
            cli.close()
        except Exception:
            pass


# -------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def _responder(self, codigo, corpo):
        dados = json.dumps(corpo).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        if self.path == "/saude":
            return self._responder(200, {"ok": True})
        self._responder(404, {"ok": False})

    def do_POST(self):
        if self.path != "/trocar-wifi":
            return self._responder(404, {"ok": False, "erro": "rota_desconhecida"})

        # Token compartilhado com o n8n. A rede interna do Docker ja limita quem
        # alcanca esta porta; o token existe para o caso de ela deixar de limitar.
        if not TOKEN or self.headers.get("X-Token") != TOKEN:
            log.warning("requisicao sem token valido de %s", self.client_address[0])
            return self._responder(401, {"ok": False, "erro": "nao_autorizado"})

        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 8192:
                raise ValueError("tamanho")
            corpo = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return self._responder(400, {"ok": False, "erro": "corpo_invalido"})

        dados, erro = validar(corpo)
        if erro:
            log.warning("recusado na validacao: %s", erro)
            return self._responder(400, {"ok": False, "erro": erro})

        # O log registra o que mudou, nunca a senha: ele e lido pela equipe toda.
        log.info("aplicando em %s | ssid=%s | senha=%s", dados["onu"],
                 dados["ssid"] or "(mantem)", "(nova)" if dados["senha"] else "(mantem)")

        ok, detalhe = aplicar(dados)
        log.info("resultado %s | %s", dados["onu"], "ok" if ok else detalhe)
        self._responder(200 if ok else 502, {"ok": ok, "detalhe": detalhe})

    def log_message(self, fmt, *args):
        pass  # o log util e o nosso; o do http repetiria sem acrescentar


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    faltando = [k for k, v in (("OLT_HOST", OLT_HOST), ("OLT_USER", OLT_USER),
                               ("OLT_PASS", OLT_PASS), ("OLT_WIFI_TOKEN", TOKEN)) if not v]
    if faltando:
        log.error("faltam variaveis de ambiente: %s", ", ".join(faltando))
        raise SystemExit(1)

    log.info("ouvindo na porta %d | olt=%s | 2.4=%s | 5g=%s",
             PORTA, OLT_HOST, WIFI_24, WIFI_5G)
    ThreadingHTTPServer(("0.0.0.0", PORTA), Handler).serve_forever()


if __name__ == "__main__":
    main()
