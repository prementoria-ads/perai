-- 017 — E-mail do Peraí (D-072): canal "email" e remetentes cadastrados por pessoa. Idempotente.
ALTER TYPE canal_mensagem ADD VALUE IF NOT EXISTS 'email';
CREATE TABLE IF NOT EXISTS remetentes_email (
    email       text PRIMARY KEY,                 -- em minúsculas; um endereço é de uma pessoa só
    usuario_id  uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    criado_em   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS remetentes_email_usuario ON remetentes_email (usuario_id);
