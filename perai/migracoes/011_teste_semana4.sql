-- 011 — Teste das 30 perguntas da semana 4 (D-057): respostas de teste ficam separadas das do dia a
-- dia (não entram no /metricas) e agrupadas por rodada ("lote"). Idempotente.
ALTER TYPE tipo_resposta ADD VALUE IF NOT EXISTS 'teste';
ALTER TABLE respostas ADD COLUMN IF NOT EXISTS lote text;
CREATE INDEX IF NOT EXISTS respostas_lote ON respostas (usuario_id, lote) WHERE lote IS NOT NULL;
