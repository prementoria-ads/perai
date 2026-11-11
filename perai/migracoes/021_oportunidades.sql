-- 021 — Oportunidades por conta (D-090, CRM de contas etapa 3). Cada oportunidade aponta para a
-- mensagem que a criou (D-040); cada mudança de etapa, valor ou previsão fica no histórico.
-- Faturas, recebimentos e comissão ficam fora (decisão à parte). Idempotente.
CREATE TABLE IF NOT EXISTS oportunidades (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id      uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    conta_id        uuid REFERENCES contas_relacionamento(id) ON DELETE SET NULL,
    mensagem_id     uuid NOT NULL REFERENCES mensagens(id) ON DELETE CASCADE,
    titulo          text NOT NULL,
    etapa           text NOT NULL DEFAULT 'prospeccao'
                    CHECK (etapa IN ('prospeccao', 'proposta', 'negociacao', 'ganha', 'perdida')),
    valor_centavos  bigint CHECK (valor_centavos IS NULL OR valor_centavos >= 0),
    previsao        date,
    criado_em       timestamptz NOT NULL DEFAULT now(),
    atualizado_em   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS oportunidades_usuario ON oportunidades (usuario_id, atualizado_em DESC);
CREATE TABLE IF NOT EXISTS oportunidade_historico (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id       uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    oportunidade_id  uuid NOT NULL REFERENCES oportunidades(id) ON DELETE CASCADE,
    mensagem_id      uuid REFERENCES mensagens(id) ON DELETE SET NULL,
    campo            text NOT NULL,
    anterior         text,
    novo             text,
    criado_em        timestamptz NOT NULL DEFAULT now()
);
