-- 018 — Histórico de preços (D-084): ofertas de panfletos e preços pagos nas notas fiscais.
-- Cada preço aponta para a mensagem de origem (D-040). Idempotente.
CREATE TABLE IF NOT EXISTS precos (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id     uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    mensagem_id    uuid NOT NULL REFERENCES mensagens(id) ON DELETE CASCADE,
    origem         text NOT NULL CHECK (origem IN ('oferta', 'compra')),
    produto        text NOT NULL,
    marca          text,
    quantidade     numeric,
    unidade        text,
    preco_centavos integer NOT NULL CHECK (preco_centavos > 0),
    por            text NOT NULL,
    ref_centavos   integer NOT NULL,
    ref_unidade    text NOT NULL,
    condicao       text,
    loja           text,
    cidade         text,
    data           date,
    criado_em      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS precos_usuario ON precos (usuario_id, data DESC);
