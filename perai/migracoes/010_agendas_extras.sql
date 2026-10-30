-- 010 — Outras agendas, só leitura (D-064): a agenda da L8 (ou outra) entra de dois jeitos.
-- Idempotente.

-- 1) Livre/ocupado: agendas que o Google deixa ver só como "ocupado" (sem título nem convidados).
CREATE TABLE IF NOT EXISTS agendas_ocupado (
    usuario_id   uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    calendario   text NOT NULL,          -- id da agenda no Google (normalmente o e-mail)
    rotulo       text NOT NULL,          -- nome curto mostrado ("L8")
    criado_em    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (usuario_id, calendario)
);

-- 2) Agenda exportada (.ics): cada importação guarda os compromissos de uma janela de datas,
--    ligados à mensagem que trouxe o arquivo (D-040). Vale a importação mais recente de cada agenda.
CREATE TABLE IF NOT EXISTS agenda_importada (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id   uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    mensagem_id  uuid NOT NULL REFERENCES mensagens(id) ON DELETE CASCADE,
    origem       text NOT NULL,          -- nome da agenda no arquivo
    rotulo       text NOT NULL,
    uid          text NOT NULL,
    titulo       text NOT NULL,
    inicio       text NOT NULL,          -- AAAA-MM-DDTHH:MM (Brasília) ou AAAA-MM-DD
    fim          text,
    local        text,
    dia_inteiro  boolean NOT NULL DEFAULT false,
    criado_em    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agenda_importada_usuario ON agenda_importada (usuario_id, origem, criado_em DESC);
