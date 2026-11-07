-- 013 — Sinal de vida (revisão de lançamento): o bot grava a hora a cada volta do laço, e a web
-- responde em /saude/bot se ele está vivo, para o aviso externo (UptimeRobot). Idempotente.
CREATE TABLE IF NOT EXISTS sinais_vida (
    servico   text PRIMARY KEY,
    visto_em  timestamptz NOT NULL DEFAULT now()
);
