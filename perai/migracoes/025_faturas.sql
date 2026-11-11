-- 025 — Faturas e recebimentos reais por projeto (D-094). Recebimento nunca passa do saldo da fatura.
-- Idempotente.
CREATE TABLE IF NOT EXISTS faturas (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id       uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    oportunidade_id  uuid REFERENCES oportunidades(id) ON DELETE SET NULL,
    mensagem_id      uuid NOT NULL REFERENCES mensagens(id) ON DELETE CASCADE,
    numero           text,
    data             date,
    vencimento       date,
    valor_centavos   bigint NOT NULL CHECK (valor_centavos > 0),
    criado_em        timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS recebimentos (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id      uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    fatura_id       uuid NOT NULL REFERENCES faturas(id) ON DELETE CASCADE,
    mensagem_id     uuid NOT NULL REFERENCES mensagens(id) ON DELETE CASCADE,
    data            date,
    valor_centavos  bigint NOT NULL CHECK (valor_centavos > 0),
    criado_em       timestamptz NOT NULL DEFAULT now()
);
