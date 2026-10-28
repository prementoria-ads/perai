-- Peraí — esquema do MVP (D-040, D-041/D-045, D-042, D-043, D-044)
-- Fonte: "Projeto Peraí — Esquema de Dados do MVP — v0.1" na Base Oficial (Google Drive).
-- Regra central: nada é afirmado sem fonte. Todo registro aponta para a mensagem
-- original, que nunca é alterada. Toda tabela de conteúdo carrega usuario_id (D-004).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Tipos fechados --------------------------------------------------------------

CREATE TYPE papel_usuario    AS ENUM ('administrador', 'usuario');
CREATE TYPE canal_mensagem   AS ENUM ('telegram', 'web', 'teste');
CREATE TYPE tipo_mensagem    AS ENUM ('texto', 'audio', 'print', 'foto', 'conversa_exportada');
CREATE TYPE tipo_entidade    AS ENUM ('pessoa', 'organizacao', 'projeto', 'evento', 'local', 'assunto');
-- D-045: quatro contextos.
CREATE TYPE contexto         AS ENUM ('familia', 'profissional', 'projetos_proprios', 'igreja');
-- D-044: opinião de terceiros nunca vira fato.
CREATE TYPE natureza_registro AS ENUM ('confirmado', 'intencao', 'hipotese', 'opiniao');
CREATE TYPE situacao_registro AS ENUM ('ativo', 'corrigido', 'excluido');
CREATE TYPE situacao_pendencia AS ENUM ('aberta', 'adiada', 'concluida', 'descartada');
-- Quem faz: eu, ou estou aguardando outra pessoa.
CREATE TYPE responsavel_pendencia AS ENUM ('eu', 'aguardando');
CREATE TYPE tipo_resposta    AS ENUM ('consulta', 'briefing');
CREATE TYPE finalidade_chamada AS ENUM ('transcricao', 'leitura_imagem', 'extracao', 'consulta', 'briefing', 'embedding');

-- 1. usuarios -----------------------------------------------------------------
-- A conta Peraí, independente do Telegram (D-015). O administrador não tem
-- memórias e não lê conteúdo privado (D-012, D-043).
CREATE TABLE usuarios (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nome               text NOT NULL,
    forma_tratamento   text,
    papel              papel_usuario NOT NULL DEFAULT 'usuario',
    fuso_horario       text NOT NULL DEFAULT 'America/Sao_Paulo',
    avisos_inicio      time,          -- horário permitido para avisos proativos
    avisos_fim         time,
    telegram_chat_id   bigint UNIQUE,
    criado_em          timestamptz NOT NULL DEFAULT now()
);

-- 2. emails_conta (D-043) -----------------------------------------------------
CREATE TABLE emails_conta (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id         uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    email              text NOT NULL,
    principal          boolean NOT NULL DEFAULT false,
    recuperacao        boolean NOT NULL DEFAULT false,
    verificado_em      timestamptz,
    criado_em          timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX emails_conta_email_unico ON emails_conta (lower(email));
CREATE UNIQUE INDEX emails_conta_um_principal ON emails_conta (usuario_id) WHERE principal;

-- 3. mensagens: tudo o que chegou, exatamente como chegou. Nunca é editada. ---
CREATE TABLE mensagens (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id         uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    canal              canal_mensagem NOT NULL,
    tipo               tipo_mensagem NOT NULL,
    texto_original     text,          -- o que você escreveu, ou a legenda
    transcricao        text,          -- áudio transcrito ou texto lido do print
    caminho_arquivo    text,          -- áudio, imagem ou .zip original
    participantes      text[],        -- em prints e conversas exportadas
    data_situacao      date,          -- dia a que a mensagem se refere
    recebida_em        timestamptz NOT NULL DEFAULT now(),
    modelo_transcricao text,
    criado_em          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mensagens_usuario_data ON mensagens (usuario_id, recebida_em DESC);

-- Mensagem imutável: só a exclusão definitiva pedida pelo usuário (D-042) remove.
CREATE FUNCTION bloquear_edicao_mensagem() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'mensagens são imutáveis: corrija o registro, não a mensagem';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER mensagens_imutaveis BEFORE UPDATE ON mensagens
    FOR EACH ROW EXECUTE FUNCTION bloquear_edicao_mensagem();

-- 4. entidades: pessoas, organizações, projetos... ----------------------------
CREATE TABLE entidades (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id         uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    tipo               tipo_entidade NOT NULL,
    nome               text NOT NULL,
    apelidos           text[] NOT NULL DEFAULT '{}',
    contexto           contexto,
    notas              text,
    criado_em          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX entidades_usuario_nome ON entidades (usuario_id, lower(nome));

-- 5. vinculos: "Wagner trabalha na Romagnole" ---------------------------------
CREATE TABLE vinculos (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id         uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    origem_id          uuid NOT NULL REFERENCES entidades(id) ON DELETE CASCADE,
    destino_id         uuid NOT NULL REFERENCES entidades(id) ON DELETE CASCADE,
    tipo_vinculo       text NOT NULL,  -- trabalha em, participa de, é parente de...
    mensagem_id        uuid REFERENCES mensagens(id) ON DELETE CASCADE,
    criado_em          timestamptz NOT NULL DEFAULT now()
);

-- 6. registros: cada fato, intenção, hipótese ou opinião extraído -------------
CREATE TABLE registros (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id         uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    mensagem_id        uuid NOT NULL REFERENCES mensagens(id) ON DELETE CASCADE,  -- fonte obrigatória
    tipo               text NOT NULL,  -- acontecimento, ideia, recomendação...
    conteudo           text NOT NULL,
    data_fato          date,
    dito_por_id        uuid REFERENCES entidades(id),  -- NULL = o próprio usuário (D-044)
    natureza           natureza_registro NOT NULL,
    confianca          numeric(3,2) NOT NULL CHECK (confianca BETWEEN 0 AND 1),
    sensivel           boolean NOT NULL DEFAULT false,  -- saúde, finanças
    situacao           situacao_registro NOT NULL DEFAULT 'ativo',
    substituido_por    uuid REFERENCES registros(id),
    embedding          vector(1536),
    criado_em          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX registros_usuario_ativos ON registros (usuario_id) WHERE situacao = 'ativo';
CREATE INDEX registros_embedding ON registros USING hnsw (embedding vector_cosine_ops);

-- 7. registro_entidades -------------------------------------------------------
CREATE TABLE registro_entidades (
    registro_id        uuid NOT NULL REFERENCES registros(id) ON DELETE CASCADE,
    entidade_id        uuid NOT NULL REFERENCES entidades(id) ON DELETE CASCADE,
    usuario_id         uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    papel              text,
    PRIMARY KEY (registro_id, entidade_id)
);

-- 8. pendencias (D-007: lembretes só com aceite) ------------------------------
CREATE TABLE pendencias (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id         uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    registro_id        uuid NOT NULL REFERENCES registros(id) ON DELETE CASCADE,
    descricao          text NOT NULL,
    responsavel        responsavel_pendencia NOT NULL DEFAULT 'eu',
    quem               text,           -- de quem se espera, quando responsavel = 'aguardando'
    prazo              date,
    prazo_como_dito    text,           -- "até sexta", "antes de março"
    situacao           situacao_pendencia NOT NULL DEFAULT 'aberta',
    lembrete_em        timestamptz,
    lembrete_enviado_em timestamptz,
    lembrete_aceito    boolean NOT NULL DEFAULT false,
    criado_em          timestamptz NOT NULL DEFAULT now(),
    CHECK (lembrete_em IS NULL OR lembrete_aceito)
);

-- 9. correcoes: histórico de cada troca, sem apagar o anterior ----------------
CREATE TABLE correcoes (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id         uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    tabela             text NOT NULL,
    linha_id           uuid NOT NULL,
    campo              text,
    valor_anterior     text,           -- vazio quando foi exclusão definitiva
    valor_novo         text,
    mensagem_id        uuid REFERENCES mensagens(id) ON DELETE SET NULL,
    criado_em          timestamptz NOT NULL DEFAULT now()
);

-- 10. respostas: toda consulta e briefing, com os registros citados -----------
CREATE TABLE respostas (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id         uuid NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    tipo               tipo_resposta NOT NULL,
    pergunta           text NOT NULL,
    resposta           text NOT NULL,
    registros_citados  uuid[] NOT NULL DEFAULT '{}',
    sem_informacao     boolean NOT NULL DEFAULT false,
    avaliacao          text,           -- ajudou, neutro, atrapalhou
    criado_em          timestamptz NOT NULL DEFAULT now(),
    -- Resposta sem registro citado só pode dizer que não há informação.
    CHECK (cardinality(registros_citados) > 0 OR sem_informacao)
);

-- 11. chamadas_modelo: custo e tempo de cada chamada às APIs ------------------
CREATE TABLE chamadas_modelo (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id         uuid REFERENCES usuarios(id) ON DELETE SET NULL,
    finalidade         finalidade_chamada NOT NULL,
    fornecedor         text NOT NULL,
    modelo             text NOT NULL,
    tokens_entrada     integer,
    tokens_saida       integer,
    segundos_audio     numeric,
    custo_usd          numeric(10,6),
    latencia_ms        integer,
    erro               text,
    criado_em          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX chamadas_modelo_data ON chamadas_modelo (criado_em DESC);
