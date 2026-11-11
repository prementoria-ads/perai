-- 023 — Parcerias, contratos, parcelas, indicações e a comissão do fundador (D-092). Cada linha aponta
-- para a mensagem de origem (D-040). Idempotente.
CREATE TABLE IF NOT EXISTS configuracoes (
    usuario_id  uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    chave       text NOT NULL,
    valor       jsonb NOT NULL,
    mudado_em   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (usuario_id, chave)
);
CREATE TABLE IF NOT EXISTS contratos (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id            uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    mensagem_id           uuid NOT NULL REFERENCES mensagens(id) ON DELETE CASCADE,
    conta_id              uuid REFERENCES contas_relacionamento(id) ON DELETE SET NULL,
    empresa_l8            text,
    titulo                text NOT NULL,
    numero                text,
    data                  date,
    vigencia_ate          date,
    assinatura            date,
    valor_centavos        bigint,
    percentual_indicacao  numeric(6,2),
    dados                 jsonb NOT NULL,
    conferencia           jsonb NOT NULL DEFAULT '[]'::jsonb,
    criado_em             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (usuario_id, mensagem_id)
);
CREATE TABLE IF NOT EXISTS contrato_parcelas (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id      uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    contrato_id     uuid NOT NULL REFERENCES contratos(id) ON DELETE CASCADE,
    n               int NOT NULL,
    descricao       text NOT NULL,
    tipo            text NOT NULL CHECK (tipo IN ('pagar', 'compensar', 'receber')),
    valor_centavos  bigint,
    regra           text,
    vencimento      date,
    situacao        text NOT NULL DEFAULT 'aberta' CHECK (situacao IN ('aberta', 'paga')),
    pago_em         date,
    UNIQUE (contrato_id, n)
);
CREATE TABLE IF NOT EXISTS indicacoes (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id   uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    contrato_id  uuid REFERENCES contratos(id) ON DELETE SET NULL,
    mensagem_id  uuid NOT NULL REFERENCES mensagens(id) ON DELETE CASCADE,
    parceiro     text NOT NULL,
    cliente      text NOT NULL,
    sentido      text NOT NULL CHECK (sentido IN ('eu_indiquei', 'parceiro_indicou')),
    criado_em    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS recebimentos_indicacao (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id      uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    indicacao_id    uuid NOT NULL REFERENCES indicacoes(id) ON DELETE CASCADE,
    mensagem_id     uuid NOT NULL REFERENCES mensagens(id) ON DELETE CASCADE,
    data            date,
    base_centavos   bigint NOT NULL CHECK (base_centavos >= 0),
    situacao        text NOT NULL DEFAULT 'previsto' CHECK (situacao IN ('previsto', 'recebido')),
    criado_em       timestamptz NOT NULL DEFAULT now()
);
