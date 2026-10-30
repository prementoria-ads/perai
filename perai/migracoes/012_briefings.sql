-- 012 — Briefing automático (D-068): horários escolhidos por pessoa (começa desligado) e
-- trava de um envio por horário e por dia. Idempotente.
CREATE TABLE IF NOT EXISTS briefings_config (
    usuario_id  uuid PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
    horas       int[] NOT NULL DEFAULT '{}',
    mudado_em   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS briefings_enviados (
    usuario_id  uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    dia         date NOT NULL,
    hora        int NOT NULL,
    enviado_em  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (usuario_id, dia, hora)
);
