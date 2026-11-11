-- 020 — Rateio em partes diferentes (D-089, etapa 2): a parte de cada conta numa despesa ligada a
-- mais de uma. Sem parte, a conta divide em partes iguais o que sobrar. Idempotente.
ALTER TABLE despesa_vinculos ADD COLUMN IF NOT EXISTS parte_centavos bigint
    CHECK (parte_centavos IS NULL OR parte_centavos >= 0);
ALTER TABLE despesa_vinculos ADD COLUMN IF NOT EXISTS parte_percentual numeric(6,2)
    CHECK (parte_percentual IS NULL OR (parte_percentual > 0 AND parte_percentual <= 100));
