# Painel de atendimentos - Node + Express, dependencias minimas.
FROM node:20-alpine

WORKDIR /app

# Instala so as dependencias primeiro, para aproveitar cache entre builds.
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund

COPY server.js migrations.sql ./
COPY public ./public

# Usuario nao-root: o painel le o banco, nao precisa de privilegio.
RUN addgroup -S painel && adduser -S painel -G painel && chown -R painel:painel /app
USER painel

EXPOSE 8090
CMD ["node", "server.js"]
