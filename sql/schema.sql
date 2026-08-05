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
