-- 026 — CRM de contas, etapas 8 a 13 (D-095 a D-100): atividades, sinais da conta, parceiros que
-- entregam projetos, MEDDPICC, mapa de oportunidades, maturidade, segmento e fatores de risco.
-- Cada linha aponta para a mensagem de origem (D-040); só o que veio de comando (/segmento) fica sem.
-- Vale sempre a linha mais recente de cada item (histórico preservado). Idempotente.
CREATE TABLE IF NOT EXISTS atividades (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id       uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    conta_id         uuid REFERENCES contas_relacionamento(id) ON DELETE SET NULL,
    oportunidade_id  uuid REFERENCES oportunidades(id) ON DELETE SET NULL,
    mensagem_id      uuid REFERENCES mensagens(id) ON DELETE CASCADE,
    tipo             text NOT NULL,
    fase             text,
    data             date,
    situacao         text NOT NULL DEFAULT 'feita',
    resumo           text,
    proximo_passo    text,
    participantes    jsonb NOT NULL DEFAULT '[]'::jsonb,
    pelo_parceiro    text,
    spin             jsonb,
    criado_em        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS atividades_conta ON atividades (usuario_id, conta_id, data DESC);

CREATE TABLE IF NOT EXISTS sinais_conta (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id    uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    conta_id      uuid REFERENCES contas_relacionamento(id) ON DELETE SET NULL,
    mensagem_id   uuid REFERENCES mensagens(id) ON DELETE CASCADE,
    tipo          text NOT NULL,
    descricao     text NOT NULL,
    dito_por      text,
    situacao      text NOT NULL DEFAULT 'aberto',
    resolvido_em  date,
    criado_em     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS parceiros_projeto (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id         uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    oportunidade_id    uuid REFERENCES oportunidades(id) ON DELETE CASCADE,
    parceiro_conta_id  uuid REFERENCES contas_relacionamento(id) ON DELETE SET NULL,
    mensagem_id        uuid REFERENCES mensagens(id) ON DELETE CASCADE,
    parceiro           text NOT NULL,
    papel              text NOT NULL,
    criado_em          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meddpicc (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id       uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    oportunidade_id  uuid REFERENCES oportunidades(id) ON DELETE CASCADE,
    mensagem_id      uuid REFERENCES mensagens(id) ON DELETE CASCADE,
    elemento         text NOT NULL,
    nota             smallint NOT NULL CHECK (nota BETWEEN 0 AND 2),
    evidencia        text,
    dito_por         text,
    criado_em        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mapa_ofertas (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id   uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    conta_id     uuid REFERENCES contas_relacionamento(id) ON DELETE CASCADE,
    mensagem_id  uuid REFERENCES mensagens(id) ON DELETE CASCADE,
    oferta       text NOT NULL,
    situacao     text NOT NULL,
    concorrente  text,
    dito_por     text,
    criado_em    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS maturidade_cliente (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id   uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    conta_id     uuid REFERENCES contas_relacionamento(id) ON DELETE CASCADE,
    mensagem_id  uuid REFERENCES mensagens(id) ON DELETE CASCADE,
    dominio      text NOT NULL,
    nivel        smallint NOT NULL CHECK (nivel BETWEEN 1 AND 4),
    nota         text,
    dito_por     text,
    criado_em    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS segmentos_conta (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id   uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    conta_id     uuid REFERENCES contas_relacionamento(id) ON DELETE CASCADE,
    mensagem_id  uuid REFERENCES mensagens(id) ON DELETE CASCADE,
    segmento     text NOT NULL,
    criado_em    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fatores_risco (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id   uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    mensagem_id  uuid REFERENCES mensagens(id) ON DELETE CASCADE,
    titulo       text NOT NULL,
    direcao      text NOT NULL,
    segmentos    jsonb NOT NULL DEFAULT '[]'::jsonb,
    situacao     text NOT NULL DEFAULT 'observacao',
    intensidade  text NOT NULL DEFAULT 'media',
    fonte_url    text,
    criado_em    timestamptz NOT NULL DEFAULT now()
);
