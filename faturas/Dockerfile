# Disparo proativo de faturas - Node, dependencias minimas (pg + node-cron).
FROM node:20-alpine

WORKDIR /app

# Instala so as dependencias primeiro, para aproveitar cache entre builds.
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund

COPY disparar-faturas.js ./

# Usuario nao-root: o servico so le SGP/Postgres e chama a Evolution.
RUN addgroup -S faturas && adduser -S faturas -G faturas && chown -R faturas:faturas /app
USER faturas

# Por padrao roda como daemon (agenda o disparo no horario de FATURAS_HORA).
# Para um disparo avulso de teste:
#   docker compose run --rm faturas node disparar-faturas.js --agora --dry-run
CMD ["node", "disparar-faturas.js", "--daemon"]
