-- 027 — Plano de conta (D-103): objetivos do ano, meta, iniciativas e revisão das contas-chave.
-- Vale a soma das linhas em ordem (campo vazio não apaga o anterior); cada linha aponta para a
-- mensagem de origem (D-040). Idempotente.
CREATE TABLE IF NOT EXISTS planos_conta (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id     uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    conta_id       uuid REFERENCES contas_relacionamento(id) ON DELETE CASCADE,
    mensagem_id    uuid REFERENCES mensagens(id) ON DELETE CASCADE,
    ano            integer,
    objetivos      jsonb NOT NULL DEFAULT '[]'::jsonb,
    iniciativas    jsonb NOT NULL DEFAULT '[]'::jsonb,
    meta_centavos  bigint,
    revisao        date,
    chave          boolean,
    criado_em      timestamptz NOT NULL DEFAULT now()
);
