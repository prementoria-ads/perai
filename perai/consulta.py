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
        linhas.append(f"[{p.id}] (pendência; {p.responsavel}{quem}; prazo: {prazo}{lembrete}) {p.descricao}")
    return "\n".join(linhas) if linhas else "(nenhum item de memória)"


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
