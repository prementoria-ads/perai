-- 004 — Despesa ligada a compromissos da agenda e a clientes, para montar o relatório de
-- viagem (trip report) que o usuário lança no Onfly. Idempotente.
CREATE TABLE IF NOT EXISTS despesa_vinculos (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id        uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    despesa_id        uuid NOT NULL REFERENCES despesas(id) ON DELETE CASCADE,
    tipo              text NOT NULL CHECK (tipo IN ('evento', 'cliente')),
    google_evento_id  text,
    titulo            text,
    inicio            timestamptz,
    cliente           text,
    criado_em         timestamptz NOT NULL DEFAULT now(),
    CHECK ((tipo = 'evento' AND google_evento_id IS NOT NULL) OR (tipo = 'cliente' AND cliente IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS despesa_vinculo_evento ON despesa_vinculos (despesa_id, google_evento_id)
    WHERE tipo = 'evento';
CREATE UNIQUE INDEX IF NOT EXISTS despesa_vinculo_cliente ON despesa_vinculos (despesa_id, lower(cliente))
    WHERE tipo = 'cliente';
