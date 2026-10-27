-- Checagens das regras do esquema. Rodar num banco recém-criado:
-- psql -d perai -v ON_ERROR_STOP=1 -f db/checagens.sql
-- Cada regra imprime "OK ..."; qualquer falha interrompe com erro.
INSERT INTO usuarios (nome) VALUES ('Nelson') RETURNING id \gset u_
INSERT INTO mensagens (usuario_id, canal, tipo, texto_original) VALUES (:'u_id','teste','texto','Hoje conversei com o Wagner') RETURNING id \gset m_
-- 1) mensagem imutável
DO $$ BEGIN UPDATE mensagens SET texto_original='x'; RAISE EXCEPTION 'FALHOU: editou mensagem'; EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE 'FALHOU%' THEN RAISE; END IF; RAISE NOTICE 'OK imutável'; END $$;
-- 2) resposta sem fonte é recusada
DO $$ BEGIN INSERT INTO respostas (usuario_id,tipo,pergunta,resposta) SELECT id,'consulta','p','r' FROM usuarios LIMIT 1; RAISE EXCEPTION 'FALHOU'; EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK resposta exige fonte'; END $$;
INSERT INTO respostas (usuario_id,tipo,pergunta,resposta,sem_informacao) VALUES (:'u_id','consulta','p','Não há registro',true);
-- 3) lembrete sem aceite é recusado
INSERT INTO registros (usuario_id,mensagem_id,tipo,conteudo,natureza,confianca) VALUES (:'u_id',:'m_id','acontecimento','Conversa com Wagner','confirmado',0.9) RETURNING id \gset r_
DO $$ BEGIN INSERT INTO pendencias (usuario_id,registro_id,descricao,lembrete_em) SELECT usuario_id,id,'x',now() FROM registros LIMIT 1; RAISE EXCEPTION 'FALHOU'; EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK lembrete exige aceite'; END $$;
-- 4) dois e-mails principais recusados
INSERT INTO emails_conta (usuario_id,email,principal) VALUES (:'u_id','a@x.com',true);
DO $$ BEGIN INSERT INTO emails_conta (usuario_id,email,principal) SELECT id,'b@x.com',true FROM usuarios LIMIT 1; RAISE EXCEPTION 'FALHOU'; EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'OK um principal'; END $$;
-- 5) exclusão definitiva apaga em cascata
DELETE FROM mensagens WHERE id=:'m_id';
SELECT count(*) AS registros_restantes FROM registros;
SELECT count(*) AS tabelas FROM information_schema.tables WHERE table_schema='public';
