FROM python:3.12-slim

# paramiko e a unica dependencia. Sem framework web: a superficie deste servico
# e um endpoint, e biblioteca a mais aqui e superficie a mais num processo que
# guarda a senha da OLT.
RUN pip install --no-cache-dir paramiko==3.4.1

# Nao roda como root: se alguem escapar do processo, cai num usuario sem nada.
RUN useradd --create-home --shell /usr/sbin/nologin olt
USER olt
WORKDIR /app

COPY --chown=olt:olt servico.py /app/servico.py
# Diagnostico so-leitura (show/display). Nao entra no CMD; roda sob demanda via
# `docker compose run --rm olt-wifi python3 olt-show.py "show ..."`.
COPY --chown=olt:olt olt-show.py /app/olt-show.py

EXPOSE 8080
CMD ["python", "-u", "/app/servico.py"]
