-- 028 — Hora da despesa (D-112): a hora da nota (emissão) ou a dita, para o relatório saber se
-- foi café da manhã, almoço, lanche ou jantar. NULL = não informada. Idempotente.
ALTER TABLE despesas ADD COLUMN IF NOT EXISTS hora time;
