-- 024 — Resultado por projeto e por conta (D-093): despesa ligada a um projeto (oportunidade ganha) e
-- comissões de cada beneficiário no projeto (custo do projeto). Idempotente.
CREATE TABLE IF NOT EXISTS despesa_projeto (
    usuario_id       uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    despesa_id       uuid NOT NULL REFERENCES despesas(id) ON DELETE CASCADE,
    oportunidade_id  uuid NOT NULL REFERENCES oportunidades(id) ON DELETE CASCADE,
    criado_em        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (despesa_id, oportunidade_id)
);
CREATE TABLE IF NOT EXISTS comissoes_projeto (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id       uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    oportunidade_id  uuid NOT NULL REFERENCES oportunidades(id) ON DELETE CASCADE,
    mensagem_id      uuid NOT NULL REFERENCES mensagens(id) ON DELETE CASCADE,
    beneficiario     text NOT NULL,
    eu               boolean NOT NULL DEFAULT false,
    percentual       numeric(6,2) NOT NULL CHECK (percentual > 0 AND percentual <= 100),
    base             text NOT NULL DEFAULT 'liquido' CHECK (base IN ('liquido', 'lucro')),
    criado_em        timestamptz NOT NULL DEFAULT now()
);
