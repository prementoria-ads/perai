-- 006 — Cancelar eventos da agenda que o Peraí não criou (D-059): apaga direto, avisa e
-- oferece Desfazer. Cada cancelamento aponta para a mensagem que pediu (D-040). Idempotente.
--   tipo 'apagado'         evento (ou ocorrência) apagado; desfazer = voltar a "confirmed"
--   tipo 'serie_encerrada' série que se repete terminou antes da próxima vez; desfazer =
--                          voltar a regra de repetição guardada em recorrencia_anterior
CREATE TABLE IF NOT EXISTS cancelamentos_agenda (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id            uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    mensagem_id           uuid REFERENCES mensagens(id) ON DELETE SET NULL,
    google_evento_id      text NOT NULL,
    titulo                text NOT NULL,
    inicio                text,
    tipo                  text NOT NULL CHECK (tipo IN ('apagado', 'serie_encerrada')),
    recorrencia_anterior  text[],
    criado_em             timestamptz NOT NULL DEFAULT now(),
    desfeito_em           timestamptz
);
CREATE INDEX IF NOT EXISTS cancelamentos_agenda_mensagem ON cancelamentos_agenda (usuario_id, mensagem_id);
