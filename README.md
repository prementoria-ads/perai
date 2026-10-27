# Peraí

Assistente pessoal com memória: você conta, encaminha ou fala, e o Peraí lembra, prepara e questiona. Quem manda é você. A gente pensa junto. Quem brilha é você.

Este repositório contém o MVP para o usuário zero (D-008). As decisões estão no Registro de Decisões, na Base Oficial do projeto no Google Drive.

## Estado

| Etapa do MVP | Situação |
| --- | --- |
| 1. Prova de conceito técnica | Em andamento: teste comparativo dos modelos (D-038) |
| 2. Esquema de dados | Pronto: `db/schema.sql` (11 tabelas) |
| 3. Captura pelo Telegram | A fazer |
| 4. Consulta | A fazer |
| 5. Briefing | A fazer |
| 6. Lembretes opt-in | A fazer |
| 7. Web mínima (perai.prementoria.com) | A fazer |
| 8. Teste de 4 semanas | A fazer |

## Rodar o banco localmente

```bash
cp .env.example .env   # preencha POSTGRES_PASSWORD
docker compose up -d banco
```

O esquema é aplicado na primeira subida do banco.

## Regras

- Segredos só no `.env`, nunca no código ou no Git.
- Dados reais (mensagens, áudios, prints) ficam no Google Drive, fora do repositório.
- Nada é afirmado sem fonte: todo registro aponta para a mensagem original, que nunca é editada.
