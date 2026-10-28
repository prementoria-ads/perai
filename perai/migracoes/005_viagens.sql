-- 005 — Viagens planejadas: nome do relatório (Onfly), período, cidades, companhia e
-- acompanhamento diário só se o usuário pedir (D-007). Idempotente.
CREATE TABLE IF NOT EXISTS viagens (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id     uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    mensagem_id    uuid REFERENCES mensagens(id) ON DELETE SET NULL,
    nome           text NOT NULL,
    inicio         date NOT NULL,
    fim            date NOT NULL CHECK (fim >= inicio),
    cidades        text[] NOT NULL DEFAULT '{}',
    acompanhantes  text[] NOT NULL DEFAULT '{}',
    objetivo       text,
    acompanhar     boolean NOT NULL DEFAULT false,
    hora_checkin   time NOT NULL DEFAULT '19:00',
    situacao       text NOT NULL DEFAULT 'ativa' CHECK (situacao IN ('ativa', 'cancelada')),
    criado_em      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS viagens_usuario_periodo ON viagens (usuario_id, inicio, fim) WHERE situacao = 'ativa';

-- Um acompanhamento por viagem por dia, no máximo.
CREATE TABLE IF NOT EXISTS viagem_checkins (
    viagem_id   uuid NOT NULL REFERENCES viagens(id) ON DELETE CASCADE,
    dia         date NOT NULL,
    enviado_em  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (viagem_id, dia)
);
