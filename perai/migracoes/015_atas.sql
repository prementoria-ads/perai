-- 015 — Atas de conversas com clientes e parceiros (D-078). Cada ata aponta para a mensagem de
-- origem (D-040); a conta guarda onde ficam a pasta e o documento mestre no Google Drive.
-- Idempotente.
CREATE TABLE IF NOT EXISTS contas_relacionamento (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id       uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    nome             text NOT NULL,
    tipo             text NOT NULL DEFAULT 'cliente',
    pasta_drive_id   text,
    mestre_drive_id  text,
    mestre_link      text,
    criado_em        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (usuario_id, nome)
);
CREATE TABLE IF NOT EXISTS atas (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id   uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    mensagem_id  uuid NOT NULL REFERENCES mensagens(id) ON DELETE CASCADE,
    conta_id     uuid REFERENCES contas_relacionamento(id) ON DELETE SET NULL,
    conteudo     jsonb NOT NULL,
    drive_id     text,
    drive_link   text,
    criado_em    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (usuario_id, mensagem_id)
);
CREATE INDEX IF NOT EXISTS atas_conta ON atas (usuario_id, conta_id);
