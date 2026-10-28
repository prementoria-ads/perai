# Peraí

Assistente pessoal com memória: você conta, encaminha ou fala, e o Peraí lembra, prepara e questiona. Quem manda é você. A gente pensa junto. Quem brilha é você.

Este repositório contém o MVP para o usuário zero (D-008). As decisões estão no Registro de Decisões, na Base Oficial do projeto no Google Drive.

## Estado

| Etapa do MVP | Situação |
| --- | --- |
| 1. Prova de conceito técnica | Teste comparativo pronto para rodar (D-038) |
| 2. Esquema de dados | Pronto: `db/schema.sql` (11 tabelas) |
| 3. Captura pelo Telegram | Pronto para instalar: `perai/bot.py` (testado com modelo simulado) |
| 4. Consulta | Primeira versão no bot: resposta só com fonte |
| 5. Briefing | A fazer |
| 6. Lembretes opt-in | Primeira versão no bot: só quando você pede |
| 7. Web mínima (perai.prementoria.com) | A fazer |
| 8. Teste de 4 semanas | A fazer |

## Rodar o banco localmente

```bash
cp .env.example .env   # preencha POSTGRES_PASSWORD
docker compose up -d banco
```

O esquema é aplicado na primeira subida do banco.

## Bot do Telegram (etapa 3)

O que ele faz:

- **Registrar:** texto, áudio, print, foto ou conversa exportada do WhatsApp (.zip). A mensagem
  original é guardada como chegou; o bot responde o que anotou, com natureza (fato, intenção,
  ideia, opinião de alguém) e pendências.
- **Perguntar:** "o que combinei com o Wagner?", "me prepara pra reunião de quinta". A resposta
  só sai com fonte nos registros; sem fonte, a resposta é "não encontrei".
- **Corrigir:** "não foi o Wagner, foi o Marcos". O registro anterior fica marcado como corrigido,
  sem ser apagado, e a troca vai para `correcoes`.
- **Lembrete:** só quando você pede ("me lembra quinta às 9h de..."), nunca por conta própria (D-007).
- **Privacidade:** só os chats listados em `PERAI_TELEGRAM_PERMITIDOS` são atendidos.

No servidor: `perai-instalar` pede o token e sobe `banco` e `bot` com Docker.

## Teste comparativo dos modelos (D-038)

Compara Claude Haiku 5.5, gpt-oss-120b (Groq), Gemini 3.8 Flash e Claude Sonnet 5.5 nas mesmas
mensagens reais. Só usa a biblioteca padrão do Python (3.11 ou mais).

1. Exporte as abas **Mensagens** e **Perguntas** da planilha "Conjunto de Teste do MVP" como CSV
   para `dados/mensagens.csv` e `dados/perguntas.csv`.
2. Copie os arquivos da pasta "Conjunto de Teste — Arquivos" do Drive para `dados/arquivos/`.
3. Preencha as chaves no `.env` (veja `.env.example`).
4. Rode:

```bash
python -m teste_comparativo.rodar --somente 5        # ensaio barato com 5 mensagens
python -m teste_comparativo.rodar                    # teste completo
python -m teste_comparativo.rodar --modelos haiku gemini-flash   # só alguns modelos
python -m teste_comparativo.rodar --simulado         # sem rede e sem custo
```

Como funciona:

- **Preparação:** áudios são transcritos e prints/fotos lidos uma vez só, para todos os modelos
  receberem o mesmo texto. Fica guardado em `dados/preparado/`, e uma nova rodada não paga de novo.
- **Extração:** cada modelo transforma cada mensagem em entidades, registros (com natureza e
  quem disse) e pendências.
- **Consulta:** cada modelo responde às perguntas usando só a memória que ele montou, citando a fonte.
- **Relatório** em `relatorios/<data>/`: `resumo.md` com as medidas automáticas (armadilhas,
  fonte correta, nomes sem fonte, hipóteses, opiniões, custo, tempo) e duas planilhas para revisão
  humana, `revisao_extracoes.csv` e `revisao_respostas.csv`.

Testes do próprio programa: `python -m unittest discover -s tests -t .`

No servidor, o teste roda dentro do contêiner do bot:
`docker compose run --rm -v $PWD/dados:/app/dados -v $PWD/relatorios:/app/relatorios bot python -m teste_comparativo.rodar`

## Cópia de segurança (D-049)

No servidor, uma vez: `bash /home/perai/perai/infra/instalar-backup.sh`. Todo dia às 03:17 (Brasília)
o banco e os arquivos originais são copiados para `/var/backups/perai` (só root, 14 dias); aos
domingos a cópia mais recente é restaurada num banco temporário e conferida. Histórico em
`/var/backups/perai/registro.log`. Comandos: `perai-backup` e `perai-backup --testar`.
Git não é backup de dados.

Cópia fora da VPS: `bash infra/instalar-copia-drive.sh` liga o envio diário ao Google Drive,
criptografado (rclone crypt, acesso `drive.file`, 30 dias). A autorização é feita uma vez no
computador do fundador com `rclone authorize "drive" "eyJzY29wZSI6ImRyaXZlLmZpbGUifQ"`. As senhas do
cofre aparecem uma única vez e devem ir para o gerenciador de senhas. Com o Drive ligado, o teste de
domingo restaura a partir da cópia do Drive.

## Regras

- Segredos só no `.env`, nunca no código ou no Git.
- Dados reais (mensagens, áudios, prints) ficam no Google Drive, fora do repositório.
- Nada é afirmado sem fonte: todo registro aponta para a mensagem original, que nunca é editada.
