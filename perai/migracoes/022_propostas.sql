-- 022 — Propostas e pricing por oportunidade (D-091, CRM de contas etapa 4). Proposta comercial,
-- proposta técnica e planilha de pricing (custo, impostos, lucro e margem) ficam ligadas à conta e à
-- oportunidade, com o arquivo de origem (D-040) e a conferência feita pelo código. Idempotente.
CREATE TABLE IF NOT EXISTS propostas (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id       uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    mensagem_id      uuid NOT NULL REFERENCES mensagens(id) ON DELETE CASCADE,
    conta_id         uuid REFERENCES contas_relacionamento(id) ON DELETE SET NULL,
    oportunidade_id  uuid REFERENCES oportunidades(id) ON DELETE SET NULL,
    tipo             text NOT NULL CHECK (tipo IN ('comercial', 'tecnica', 'pricing')),
    numero           text,
    revisao          text,
    data             date,
    validade         date,
    valor_centavos   bigint,
    custo_centavos   bigint,
    impostos_centavos bigint,
    lucro_centavos   bigint,
    margem           numeric(7,2),
    dados            jsonb NOT NULL,
    conferencia      jsonb NOT NULL DEFAULT '[]'::jsonb,
    criado_em        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (usuario_id, mensagem_id)
);
CREATE INDEX IF NOT EXISTS propostas_oportunidade ON propostas (usuario_id, oportunidade_id);
