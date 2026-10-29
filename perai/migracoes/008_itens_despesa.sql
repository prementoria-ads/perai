-- 008 — Itens da nota de uma despesa (D-061): o usuário confirma quais entram no lançamento
-- ("lancei só o combustível"). O valor da despesa passa a ser a soma dos itens incluídos;
-- cada mudança de valor fica na trilha de correções. Idempotente.
CREATE TABLE IF NOT EXISTS despesa_itens (
    despesa_id      uuid NOT NULL REFERENCES despesas(id) ON DELETE CASCADE,
    usuario_id      uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    n               integer NOT NULL CHECK (n > 0),
    descricao       text NOT NULL,
    valor_centavos  integer CHECK (valor_centavos >= 0),
    incluido        boolean NOT NULL DEFAULT true,
    total_nota      integer,          -- total da nota (com descontos): vale quando todos entram
    PRIMARY KEY (despesa_id, n)
);
