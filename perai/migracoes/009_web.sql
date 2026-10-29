-- 009 — Web mínima (etapa 7, D-001, D-015, D-063): entrar com Google, sem depender do Telegram.
-- Idempotente. Tokens (convite e sessão) só como hash; nada de senha no Peraí.

-- A conta Google que entra na web fica em emails_conta (D-043), presa pelo "sub" do Google,
-- que não muda mesmo se o e-mail mudar.
ALTER TABLE emails_conta ADD COLUMN IF NOT EXISTS google_sub text;
CREATE UNIQUE INDEX IF NOT EXISTS emails_conta_google_sub ON emails_conta (google_sub) WHERE google_sub IS NOT NULL;

-- Convite de uso único (/web no Telegram, 15 minutos) para ligar a conta Google à conta do Peraí.
CREATE TABLE IF NOT EXISTS web_convites (
    token_hash  text PRIMARY KEY,
    usuario_id  uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    expira_em   timestamptz NOT NULL,
    usado_em    timestamptz
);

-- Sessões da web: cookie com token aleatório; no banco, só o hash.
CREATE TABLE IF NOT EXISTS web_sessoes (
    token_hash    text PRIMARY KEY,
    usuario_id    uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    csrf          text NOT NULL,
    criada_em     timestamptz NOT NULL DEFAULT now(),
    expira_em     timestamptz NOT NULL,
    encerrada_em  timestamptz
);
CREATE INDEX IF NOT EXISTS web_sessoes_usuario ON web_sessoes (usuario_id);
