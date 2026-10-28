-- 003 — Despesas (D-052). Idempotente.
-- Valor em centavos; NULL = "não informado" (nunca zero). Sempre ligada à mensagem de origem.
DO $$ BEGIN
    CREATE TYPE categoria_despesa AS ENUM ('alimentacao', 'combustivel', 'transporte', 'hospedagem', 'saude',
        'mercado', 'casa', 'educacao', 'lazer', 'servicos', 'impostos', 'outros');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS despesas (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id       uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    mensagem_id      uuid NOT NULL REFERENCES mensagens(id) ON DELETE CASCADE,
    registro_id      uuid REFERENCES registros(id) ON DELETE SET NULL,
    descricao        text NOT NULL,
    valor_centavos   bigint CHECK (valor_centavos IS NULL OR valor_centavos >= 0),
    moeda            char(3) NOT NULL DEFAULT 'BRL',
    data             date,
    categoria        categoria_despesa NOT NULL DEFAULT 'outros',
    estabelecimento  text,
    cnpj             text,
    forma_pagamento  text,
    contexto         contexto,
    local            text,
    origem           text NOT NULL DEFAULT 'texto',   -- texto, audio, foto, print, pdf, xml
    chave_nfe        text,
    situacao         text NOT NULL DEFAULT 'ativa' CHECK (situacao IN ('ativa', 'cancelada')),
    criado_em        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS despesas_usuario_data ON despesas (usuario_id, data) WHERE situacao = 'ativa';
-- A mesma NF-e mandada duas vezes não vira duas despesas.
CREATE UNIQUE INDEX IF NOT EXISTS despesas_nfe_unica ON despesas (usuario_id, chave_nfe)
    WHERE chave_nfe IS NOT NULL AND situacao = 'ativa';

ALTER TYPE tipo_mensagem ADD VALUE IF NOT EXISTS 'documento';
