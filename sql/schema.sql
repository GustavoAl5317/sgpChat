-- Tabela de sessão de conversa do bot WhatsApp (estado por telefone)
-- Guarda em qual etapa do fluxo o cliente está e os dados coletados até o momento
-- (CPF, contrato, SSID novo, etc.). Dados sensíveis são removidos assim que
-- deixam de ser necessários (ver node "Processar Definir Wifi" no workflow).

CREATE TABLE IF NOT EXISTS wa_sessions (
    phone       TEXT PRIMARY KEY,           -- número normalizado (sem @s.whatsapp.net)
    step        TEXT NOT NULL DEFAULT 'menu',
    data        JSONB NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Log de auditoria: toda tentativa de alteração de Wi-Fi deve ficar registrada,
-- já que isso mexe em equipamento do cliente e envolve dado pessoal (CPF).
CREATE TABLE IF NOT EXISTS wa_wifi_change_log (
    id           BIGSERIAL PRIMARY KEY,
    phone        TEXT NOT NULL,
    cpf          TEXT NOT NULL,
    contrato_id  TEXT,
    cpe_id       TEXT,
    ssid_novo    TEXT,
    sucesso      BOOLEAN NOT NULL,
    resposta_sgp JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wa_wifi_change_log_phone ON wa_wifi_change_log (phone);
