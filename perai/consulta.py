"""Consulta e briefing: responder só com o que está registrado, citando a fonte."""

from __future__ import annotations

from dataclasses import dataclass, field

from perai.extracao import Pendencia, Registro, extrair_json

SISTEMA_CONSULTA = """Você é o Peraí, assistente pessoal brasileiro. Responda à pergunta do usuário
usando SOMENTE os itens de memória listados. Devolva só um objeto JSON.

Regras:
1. Toda afirmação precisa vir de um item listado. Cite os ids usados em "registros_citados".
2. Se a resposta não estiver nos itens, diga que não há registro e marque
   "sem_informacao": true. Nunca complete com conhecimento geral ou suposição.
3. Hipótese e intenção aparecem como tal ("você cogitou", "ficou de"), nunca como fato.
   Opinião aparece com o autor ("segundo a Simone").
4. Se houver correção, use a versão corrigida.
5. Se a informação estiver incompleta, diga o que se sabe e o que falta.
6. Pedido de briefing ("me prepara pra..."): curto, cabe numa tela de celular: o que já foi
   tratado, pendências, o que falta saber.
7. Formato da resposta (texto puro do Telegram, sem asteriscos nem markdown):
   - Com dois ou mais itens, use uma linha por item. Nunca junte vários itens num parágrafo.
   - Comece com uma linha curta de título com o dia ou período, por exemplo "📅 Amanhã, quarta 28/10".
   - Separe em dois blocos, como no Google Agenda, omitindo o bloco vazio:
     "🗓️ Compromissos" — eventos da agenda (itens [A1], [A2]...), em ordem de horário:
       "14:00 · Reunião no Technopark" (dia inteiro: "dia todo · Feriado").
     "✅ Tarefas" — pendências e lembretes, primeiro os com hora, depois por prazo, por último
       os sem data: "⏰ 11:00 · Levar o lixo" (com lembrete), "📌 Enviar o relatório · prazo 28/10",
       "⏳ Aguardando Wagner · proposta" (esperando alguém).
   - Fatos, ideias e opiniões só entram se a pergunta pedir, depois dos blocos: "📝" fato,
     "🎯" intenção, "💡" ideia, "💬" opinião (com o autor).
   - Uma resposta de um item só pode ser uma frase.

Formato: {"resposta": str, "registros_citados": [ids], "sem_informacao": bool}"""


@dataclass
class RespostaConsulta:
    pergunta: str
    resposta: str = ""
    registros_citados: list[str] = field(default_factory=list)
    citacoes_invalidas: list[str] = field(default_factory=list)
    sem_informacao: bool = False
    json_valido: bool = True


def formatar_memoria(registros: list[Registro], pendencias: list[Pendencia]) -> str:
    linhas = []
    for r in registros:
        data = f" | data: {r.data_fato}" if r.data_fato else ""
        corrige = f" | corrige: {r.corrige}" if r.corrige else ""
        linhas.append(
            f"[{r.id}] ({r.natureza}; dito por: {r.dito_por}{data}{corrige}) {r.conteudo}"
        )
    for p in pendencias:
        prazo = p.prazo or p.prazo_como_dito or "sem prazo"
        quem = f" ({p.quem})" if p.quem else ""
        lembrete = f"; lembrete: {p.lembrete_em.replace('T', ' ')}" if p.lembrete_em else ""
        if getattr(p, "recorrencia", None):
            from perai.recorrencia import por_extenso
            lembrete += f"; repete: {por_extenso(p.recorrencia)}"
        linhas.append(f"[{p.id}] (pendência; {p.responsavel}{quem}; prazo: {prazo}{lembrete}) {p.descricao}")
    return "\n".join(linhas) if linhas else "(nenhum item de memória)"


def formatar_evento(codigo: str, e) -> str:
    """Evento da agenda (perai.google.EventoAgenda) como item de memória citável."""
    if e.dia_inteiro:
        quando = f"{e.inicio} (dia inteiro)"
    else:
        quando = (e.inicio or "").replace("T", " ") + (f" a {e.fim[11:]}" if e.fim and len(e.fim) > 11 else "")
    local = f" | local: {e.local}" if e.local else ""
    return f"[{codigo}] (evento da agenda; {quando}{local}) {e.titulo}"


def montar_pedido(pergunta: str, memoria: str, hoje: str | None,
                  conversa: list[str] | None = None) -> str:
    contexto = ""
    if conversa:
        # Só para entender a pergunta ("e com o Rafael?"); não é fonte de resposta.
        contexto = ("Conversa recente (só para entender a pergunta; não é fonte):\n"
                    + "\n".join(conversa) + "\n\n")
    return f"Hoje: {hoje or 'não informado'}.\n\nItens de memória:\n{memoria}\n\n{contexto}Pergunta: {pergunta}"


def interpretar(pergunta: str, resposta: str, ids_validos: set[str]) -> RespostaConsulta:
    resultado = RespostaConsulta(pergunta=pergunta)
    try:
        dados = extrair_json(resposta)
        if not isinstance(dados, dict):
            raise ValueError
    except (ValueError, TypeError):
        resultado.json_valido = False
        resultado.resposta = resposta.strip()[:2000]
        return resultado
    resultado.resposta = str(dados.get("resposta") or "").strip()
    citados = [str(c) for c in (dados.get("registros_citados") or [])]
    resultado.registros_citados = [c for c in citados if c in ids_validos]
    resultado.citacoes_invalidas = [c for c in citados if c not in ids_validos]
    resultado.sem_informacao = bool(dados.get("sem_informacao", False))
    return resultado
