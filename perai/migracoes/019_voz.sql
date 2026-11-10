-- 019 — Resposta falada: quem liga recebe resposta em áudio quando manda áudio. Idempotente.
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS responder_com_voz boolean NOT NULL DEFAULT false;
