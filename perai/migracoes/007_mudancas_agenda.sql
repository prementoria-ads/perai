-- 007 — Mudar horário ou nome de evento que o Peraí não criou (D-060), com Desfazer.
-- Reaproveita cancelamentos_agenda: tipo 'alterado' guarda como o evento estava em "anterior".
ALTER TABLE cancelamentos_agenda ADD COLUMN IF NOT EXISTS anterior jsonb;
ALTER TABLE cancelamentos_agenda DROP CONSTRAINT IF EXISTS cancelamentos_agenda_tipo_check;
ALTER TABLE cancelamentos_agenda ADD CONSTRAINT cancelamentos_agenda_tipo_check
    CHECK (tipo IN ('apagado', 'serie_encerrada', 'alterado'));
