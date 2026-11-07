-- 016 — Busca por significado: um vetor por registro (float32 normalizado, 768 dimensões).
-- Derivado do registro: some junto com ele. Idempotente.
CREATE TABLE IF NOT EXISTS registro_vetores (
    registro_id  uuid PRIMARY KEY REFERENCES registros(id) ON DELETE CASCADE,
    usuario_id   uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    modelo       text NOT NULL,
    vetor        bytea NOT NULL,
    criado_em    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS registro_vetores_usuario ON registro_vetores (usuario_id, modelo);
