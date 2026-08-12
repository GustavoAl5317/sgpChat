# O projeto GenieACS nao publica imagem oficial. Em vez de depender de imagem
# de terceiro (que ninguem audita e que some sem aviso), instalamos do npm -
# a mesma fonte que a documentacao oficial manda usar.
FROM node:20-bookworm-slim

# A versao fica presa na major.minor: 1.2 e a linha estavel que o SGP integra.
# Sem trava nenhuma, um "docker compose build" daqui a seis meses traria uma
# major nova e quebraria em producao sem ninguem ter mudado nada.
RUN npm install -g "genieacs@^1.2.0" \
 && mkdir -p /opt/genieacs/ext \
 && chown -R node:node /opt/genieacs

USER node
ENV NODE_OPTIONS=--enable-source-maps

# Os quatro servicos do GenieACS sao processos separados. Num piloto de uma ONU
# nao vale a pena quatro containers - mas se um morrer, o container inteiro cai
# e o restart do compose sobe tudo de novo, em vez de ficar meio funcionando.
COPY --chown=node:node entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/bin/bash", "/entrypoint.sh"]
