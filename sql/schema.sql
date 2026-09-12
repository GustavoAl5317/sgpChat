-- Estado da conversa do bot, por telefone.
-- Cada mensagem do WhatsApp dispara uma execucao independente do workflow,
-- entao a etapa atual e os dados ja coletados precisam ficar aqui.
CREATE TABLE IF NOT EXISTS wa_sessions (
    phone       TEXT PRIMARY KEY,           -- numero normalizado (sem @s.whatsapp.net)
    step        TEXT NOT NULL DEFAULT 'menu',
    data        JSONB NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Auditoria: toda operacao que toca dado do cliente fica registrada
-- (alteracao de Wi-Fi, consulta de 2a via, abertura de chamado).
CREATE TABLE IF NOT EXISTS wa_wifi_change_log (
    id           BIGSERIAL PRIMARY KEY,
    phone        TEXT NOT NULL,
    cpf          TEXT NOT NULL,
    contrato_id  TEXT,
    cpe_id       TEXT,
    ssid_novo    TEXT,
    sucesso      BOOLEAN NOT NULL,
    resposta_sgp JSONB,
    tipo         TEXT NOT NULL DEFAULT 'wifi',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Para bases criadas antes dos modulos de financeiro e suporte existirem.
ALTER TABLE wa_wifi_change_log
    ADD COLUMN IF NOT EXISTS tipo TEXT NOT NULL DEFAULT 'wifi';

CREATE INDEX IF NOT EXISTS idx_wa_wifi_change_log_phone ON wa_wifi_change_log (phone);
CREATE INDEX IF NOT EXISTS idx_wa_wifi_change_log_tipo  ON wa_wifi_change_log (tipo, created_at);

-- Sessoes paradas ha mais de 30 min sao lixo: o cliente desistiu no meio.
-- Elas podem conter CPF, entao nao devem ficar guardadas indefinidamente.
-- Agende no cron do servidor:
--   DELETE FROM wa_sessions WHERE updated_at < now() - interval '30 minutes';

-- Texto das conversas, para a aba do painel. Diferente de wa_sessions (que e
-- efemera) e de wa_wifi_change_log (que e a acao): aqui fica o dialogo, e ele
-- PERSISTE - e o historico de atendimento, como um WhatsApp. Uma linha por
-- mensagem, nos dois sentidos ('in' = cliente, 'out' = bot).
-- Senha de Wi-Fi e data de nascimento (2FA) NUNCA entram aqui em claro: o bot
-- mascara essas mensagens antes de gravar.
CREATE TABLE IF NOT EXISTS wa_messages (
  id         BIGSERIAL PRIMARY KEY,
  phone      TEXT NOT NULL,
  direcao    TEXT NOT NULL CHECK (direcao IN ('in','out')),
  texto      TEXT,
  contrato   TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_wa_messages_phone ON wa_messages (phone, created_at);
