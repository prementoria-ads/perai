-- 002 — Pendências que se repetem ("todo dia 1") e ligação entre uma vez e a seguinte.
-- Idempotente. Formato de recorrencia em perai/recorrencia.py.
ALTER TABLE pendencias ADD COLUMN IF NOT EXISTS recorrencia text;
ALTER TABLE pendencias ADD COLUMN IF NOT EXISTS anterior_id uuid REFERENCES pendencias(id) ON DELETE SET NULL;
-- Cada vez tem no máximo uma seguinte: evita criar a próxima duas vezes.
CREATE UNIQUE INDEX IF NOT EXISTS pendencias_anterior_unica ON pendencias (anterior_id) WHERE anterior_id IS NOT NULL;
