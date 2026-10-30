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
       "14:00 · Reunião no Technopark" (dia inteiro: "dia todo · Feriado"). Item de outra agenda
       ("agenda L8, só leitura") leva o rótulo: "14:00 · 💼 L8 · Call Gazin"; bloco só de livre/ocupado:
       "14:00–15:00 · 💼 L8 · ocupado". Nunca invente o assunto de um bloco ocupado.
     "✅ Tarefas" — pendências e lembretes, primeiro os com hora, depois por prazo, por último
       os sem data: "⏰ 11:00 · Levar o lixo" (com lembrete), "📌 Enviar o relatório · prazo 28/10",
       "⏳ Aguardando Wagner · proposta" (esperando alguém).
   - Fatos, ideias e opiniões só entram se a pergunta pedir, depois dos blocos: "📝" fato,
     "🎯" intenção, "💡" ideia, "💬" opinião (com o autor).
   - Uma resposta de um item só pode ser uma frase.
8. Gastos: itens [D1], [D2]... são despesas; itens [T1], [T2]... são totais por mês e categoria
   já calculados pelo Peraí. Para qualquer soma ou total use SÓ os valores dos itens [T...] e
   cite-os; nunca some, multiplique ou estime valores você mesmo. Se o total pedido não existir
   pronto (outro período ou combinação), liste as despesas [D...] e diga que o total exato não
   foi calculado. "Valor não informado" nunca conta como zero.

9. "Próximos" eventos ou compromissos: só os que ainda não passaram (depois de "agora"), em ordem,
   começando hoje e seguindo pelos próximos dias. Com mais de um dia, um título curto por dia
   ("📅 Quinta 29/10") e os itens embaixo. Até uns 15 itens; se houver mais, diga que há mais.
10. Pendência de marcar ou agendar algo ("agendar conversa com o Akira") quando já existe evento
   da agenda com a mesma pessoa ou assunto: não diga que falta agendar; mostre o evento e, na
   linha da pendência, "(já está na agenda? diga “já agendei” que eu dou baixa)".
11. Resumo de um dia ou período ("resuma o dia de hoje", "o que rolou essa semana"): use os
   registros "anotado em" ou com data do fato no período e os eventos da agenda do período. Blocos:
   "🗓️ Compromissos", "📝 O que aconteceu" (fatos e conversas, com quem disse), "✅ Ficou para
   fazer" (pendências que surgiram). Curto, uma linha por item; sem nada no período, diga isso.
12. Briefing sem registros sobre a pessoa ou a pauta: diga numa linha o que se sabe (ex.: o
   horário) e termine com "Para eu te preparar, me conta:" e até 3 perguntas curtas numeradas
   sobre o que falta (quem é e de onde, objetivo da conversa, o que ficou pendente da última
   vez), e marque "pedir_informacoes": true. Nunca invente pauta.

Formato: {"resposta": str, "registros_citados": [ids], "sem_informacao": bool,
          "pedir_informacoes": bool}"""


@dataclass
class RespostaConsulta:
    pergunta: str
    resposta: str = ""
    registros_citados: list[str] = field(default_factory=list)
    citacoes_invalidas: list[str] = field(default_factory=list)
    sem_informacao: bool = False
    json_valido: bool = True
    pedir_informacoes: bool = False


LIMITE_REGISTROS = 150   # registros mandados ao modelo por pergunta (tempo e custo; meta < 10 s)
_PALAVRAS_VAZIAS = {"para", "pra", "com", "sobre", "que", "qual", "quais", "quando", "quem", "como", "onde",
                    "tenho", "tem", "foi", "era", "isso", "essa", "esse", "minha", "meu", "uma", "dos", "das",
                    "nos", "nas", "pelo", "pela", "mais", "ainda", "ficou", "combinado", "combinei", "falei",
                    "prepara", "prepare", "reuniao", "conversa", "hoje", "amanha", "ontem"}


def _normal(texto: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", texto or "").lower()
    return "".join(ch for ch in t if not unicodedata.combining(ch))


def _raizes(texto: str) -> set[str]:
    """Palavras com 3+ letras, sem acento, cortadas em 5 letras (reunião/reuniões, Gazin/Gazins)."""
    import re
    return {p[:5] for p in re.findall(r"[a-z0-9]{3,}", _normal(texto)) if p not in _PALAVRAS_VAZIAS}


def selecionar(registros: list[Registro], pergunta: str, contexto: str = "", hoje: str | None = None,
               limite: int = LIMITE_REGISTROS) -> list[Registro]:
    """Com memória grande, manda ao modelo só o que tem a ver com a pergunta: registros com palavras
    em comum (pergunta e conversa recente), os anotados nos últimos 3 dias e, se a pergunta fala de um
    período ("hoje", "semana"), os do período. Até `limite`, na ordem original."""
    if len(registros) <= limite:
        return registros
    alvo = _raizes(pergunta) | _raizes(contexto)
    pontos = {}
    for i, r in enumerate(registros):
        comum = len(alvo & _raizes(f"{r.conteudo} {r.dito_por} {' '.join(r.entidades)}"))
        pontos[i] = comum * 10
    if hoje:
        from datetime import date, timedelta
        dia = date.fromisoformat(hoje[:10])
        t = _normal(pergunta)
        janela = 7 if "semana" in t else 1 if ("ontem" in t or "hoje" in t or "dia" in t) else 3
        desde = (dia - timedelta(days=janela)).isoformat()
        for i, r in enumerate(registros):
            if (r.anotado_em or "") >= desde or (r.data_fato or "") >= desde:
                pontos[i] = pontos.get(i, 0) + (8 if janela <= 1 or "semana" in t else 3)
    escolhidos = sorted((i for i in pontos if pontos[i] > 0), key=lambda i: (-pontos[i], -i))[:limite]
    return [registros[i] for i in sorted(escolhidos)]


def formatar_memoria(registros: list[Registro], pendencias: list[Pendencia]) -> str:
    linhas = []
    for r in registros:
        data = f" | data: {r.data_fato}" if r.data_fato else ""
        corrige = f" | corrige: {r.corrige}" if r.corrige else ""
        anotado = f" | anotado em: {r.anotado_em}" if getattr(r, "anotado_em", None) else ""
        linhas.append(
            f"[{r.id}] ({r.natureza}; dito por: {r.dito_por}{data}{anotado}{corrige}) {r.conteudo}"
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
    agenda = getattr(e, "agenda", None)
    if getattr(e, "so_ocupado", False):
        return f"[{codigo}] (agenda {agenda}, só livre/ocupado; {quando}) ocupado, sem detalhes"
    origem = f"agenda {agenda}, só leitura" if agenda else "evento da agenda"
    return f"[{codigo}] ({origem}; {quando}{local}) {e.titulo}"


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
    resultado.pedir_informacoes = dados.get("pedir_informacoes") is True
    return resultado
