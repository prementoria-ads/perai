-- 001 — Google Agenda (D-051). Idempotente: pode rodar de novo sem efeito.
-- Fonte das decisões: Registro de Decisões (D-040 nada sem fonte, D-051 agenda).

-- Contas Google conectadas: uma por usuário. O refresh token fica cifrado com
-- PERAI_CHAVE_CIFRA (só no .env do servidor).
CREATE TABLE IF NOT EXISTS google_contas (
    usuario_id            uuid PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
    email                 text,
    refresh_token_cifrado text NOT NULL,
    escopos               text NOT NULL,
    agenda_id             text NOT NULL DEFAULT 'primary',
    conectada_em          timestamptz NOT NULL DEFAULT now(),
    desconectada_em       timestamptz
);

-- Links de conexão de uso único (o /agenda gera; vale 15 minutos). Só o hash fica no banco.
CREATE TABLE IF NOT EXISTS google_conexoes (
    token_hash  text PRIMARY KEY,
    usuario_id  uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    expira_em   timestamptz NOT NULL,
    usado_em    timestamptz
);

DO $$ BEGIN
    CREATE TYPE situacao_evento AS ENUM ('criado', 'desfeito', 'alterado', 'cancelado');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Eventos que o Peraí criou na agenda, sempre ligados à mensagem de origem (D-040).
-- Só estes o Peraí altera ou cancela; eventos criados por outras pessoas ele só lê.
CREATE TABLE IF NOT EXISTS eventos_agenda (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id        uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    mensagem_id       uuid REFERENCES mensagens(id) ON DELETE SET NULL,
    registro_id       uuid REFERENCES registros(id) ON DELETE SET NULL,
    google_evento_id  text NOT NULL,
    titulo            text NOT NULL,
    inicio            timestamptz NOT NULL,
    fim               timestamptz NOT NULL,
    local             text,
    situacao          situacao_evento NOT NULL DEFAULT 'criado',
    criado_em         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (usuario_id, google_evento_id)
);
CREATE INDEX IF NOT EXISTS eventos_agenda_usuario_inicio ON eventos_agenda (usuario_id, inicio);

-- Respostas podem citar eventos da agenda (fontes externas), além de registros.
ALTER TABLE respostas ADD COLUMN IF NOT EXISTS fontes_externas text[] NOT NULL DEFAULT '{}';
ALTER TABLE respostas DROP CONSTRAINT IF EXISTS respostas_check;
ALTER TABLE respostas DROP CONSTRAINT IF EXISTS respostas_fonte;
ALTER TABLE respostas ADD CONSTRAINT respostas_fonte
    CHECK (cardinality(registros_citados) > 0 OR cardinality(fontes_externas) > 0 OR sem_informacao);

ALTER TYPE finalidade_chamada ADD VALUE IF NOT EXISTS 'agenda';
