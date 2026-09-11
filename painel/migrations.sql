-- Tabelas do painel. Rodam sozinhas no arranque do servico (server.js chama
-- este arquivo com CREATE ... IF NOT EXISTS), entao e seguro reexecutar.

-- Contas de quem acessa o painel. A senha nunca e guardada em claro - so o hash
-- bcrypt. 'papel' e 'admin' ou 'user'; user ve tudo, admin tambem gerencia contas.
CREATE TABLE IF NOT EXISTS painel_users (
  id          BIGSERIAL PRIMARY KEY,
  usuario     TEXT NOT NULL UNIQUE,
  senha_hash  TEXT NOT NULL,
  papel       TEXT NOT NULL DEFAULT 'user' CHECK (papel IN ('admin','user')),
  ativo       BOOLEAN NOT NULL DEFAULT TRUE,
  criado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
  ultimo_login TIMESTAMPTZ
);

-- Texto das conversas. O bot passa a inserir aqui a cada mensagem (entrada e
-- saida). Fica separado do wa_wifi_change_log de proposito: aquele e a acao
-- (trocou a senha), este e o dialogo. Um telefone tem muitas linhas aqui.
CREATE TABLE IF NOT EXISTS wa_messages (
  id         BIGSERIAL PRIMARY KEY,
  phone      TEXT NOT NULL,
  direcao    TEXT NOT NULL CHECK (direcao IN ('in','out')),
  texto      TEXT,
  contrato   TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_wa_messages_phone ON wa_messages (phone, created_at);
