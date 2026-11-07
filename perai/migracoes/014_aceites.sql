-- 014 — Aceite da política de privacidade e dos termos de uso (revisão de lançamento): versão aceita,
-- quando, e o consentimento específico para dados sensíveis (religião e saúde; NULL = ainda não
-- respondeu). Idempotente.
CREATE TABLE IF NOT EXISTS aceites (
    usuario_id  uuid PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
    versao      text NOT NULL,
    aceito_em   timestamptz NOT NULL DEFAULT now(),
    sensiveis   boolean
);
