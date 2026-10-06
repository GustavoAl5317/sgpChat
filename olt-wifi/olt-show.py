#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnostico SO-LEITURA da OLT.

Roda comandos `show`/`display` na OLT e imprime a saida. Recusa qualquer coisa
que nao comece com show/display - nunca entra em modo de configuracao, nunca
altera nada. Serve para descobrir a sintaxe real (ex.: TR-069) e o estado de uma
ONU antes de provisionar, sem risco.

Uso (de dentro de ~/sgpChat/sgpChat):
  docker compose run --rm olt-wifi python3 olt-show.py "show ..." ["show ..."]
"""
import os
import re
import sys
import time

import paramiko

OLT_HOST = os.environ.get("OLT_HOST", "")
OLT_PORT = int(os.environ.get("OLT_PORT", "22"))
OLT_USER = os.environ.get("OLT_USER", "")
OLT_PASS = os.environ.get("OLT_PASS", "")

RE_PAG = re.compile("-+ *More *-+")


def _e_paginador(linha):
    return "More" in linha and "--" in linha


def ler(canal, ate=4.0):
    saida = ""
    fim = time.time() + ate
    while time.time() < fim:
        if canal.recv_ready():
            pedaco = canal.recv(65535).decode("utf-8", "replace")
            saida += pedaco
            if any(_e_paginador(l) for l in pedaco.splitlines()):
                canal.send(" ")
            fim = time.time() + 0.6
        else:
            time.sleep(0.05)
    return RE_PAG.sub("\n", saida).replace("\r", "").replace("\x08", "")


def main():
    cmds = sys.argv[1:]
    if not cmds:
        print("uso: olt-show.py 'show ...' ['show ...']")
        sys.exit(2)
    for cm in cmds:
        if not cm.strip().lower().startswith(("show", "display")):
            print("[recusado] so leitura (show/display): %r" % cm)
            sys.exit(2)
    for k, v in (("OLT_HOST", OLT_HOST), ("OLT_USER", OLT_USER), ("OLT_PASS", OLT_PASS)):
        if not v:
            print("[x] falta %s no ambiente" % k)
            sys.exit(1)

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        cli.connect(OLT_HOST, port=OLT_PORT, username=OLT_USER, password=OLT_PASS,
                    timeout=20, banner_timeout=20, auth_timeout=20,
                    look_for_keys=False, allow_agent=False)
    except Exception as e:
        print("[x] ssh falhou:", type(e).__name__, e)
        sys.exit(1)
    try:
        canal = cli.invoke_shell(width=200, height=512)
        ler(canal, 3.0)  # banner
        for cm in cmds:
            canal.send(cm + "\n")
            out = ler(canal, 8.0)
            print("===== %s =====" % cm)
            print(out)
            print()
    finally:
        try:
            cli.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
