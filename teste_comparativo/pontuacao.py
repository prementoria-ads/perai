"""Medidas automáticas. Só o que dá para medir sem julgamento; o resto vai para a
planilha de revisão, conferida pelo fundador item a item."""

from __future__ import annotations

from dataclasses import dataclass

from teste_comparativo.dados import MensagemTeste, PerguntaTeste, normalizar
from teste_comparativo.executar import ResultadoModelo
from teste_comparativo.preparar import Entrada

FRASES_SEM_INFORMACAO = ("nao ha registro", "nao tenho registro", "nao encontrei", "sem registro",
                         "nao ha informacao", "nao tenho informacao")


def e_armadilha(p: PerguntaTeste) -> bool:
    """Pergunta cujo gabarito é 'não há registro'."""
    return "nao ha registro" in normalizar(p.resposta_correta)


def disse_que_nao_sabe(texto: str, sem_informacao: bool) -> bool:
    return sem_informacao or any(f in normalizar(texto) for f in FRASES_SEM_INFORMACAO)


def nome_tem_fonte(nome: str, texto_fonte: str) -> bool:
    """Algum pedaço significativo do nome aparece no texto da mensagem?"""
    fonte = f" {normalizar(texto_fonte)} "
    pedacos = [p for p in normalizar(nome).split() if len(p) >= 3]
    if not pedacos:
        return True
    return any(f" {p}" in fonte for p in pedacos)


def _esperado(valor: str) -> bool:
    n = normalizar(valor)
    return bool(n) and not n.startswith("nenhum") and not n.startswith("preencher")


@dataclass
class Placar:
    modelo: str
    mensagens: int
    json_extracao_ok: int
    registros: int
    pendencias: int
    nomes_sem_fonte: list[str]
    hipoteses_esperadas: int
    hipoteses_ok: int
    opinioes_esperadas: int
    opinioes_ok: int
    pendencias_concordantes: int
    pendencias_avaliadas: int
    perguntas: int
    armadilhas: int
    armadilhas_ok: int
    com_fonte_esperada: int
    respondeu_com_fonte: int
    fonte_correta: int
    citacoes_invalidas: int
    custo_usd: float
    latencia_media_ms: int
    latencia_max_ms: int
    erros: int


def pontuar(resultado: ResultadoModelo, mensagens: list[MensagemTeste],
            perguntas: list[PerguntaTeste], entradas: list[Entrada]) -> Placar:
    textos = {e.numero: e.texto for e in entradas}
    gabarito = {m.numero: m for m in mensagens}

    nomes_sem_fonte: list[str] = []
    hip_esp = hip_ok = op_esp = op_ok = pend_ok = pend_aval = 0
    for numero, ext in resultado.extracoes.items():
        for ent in ext.entidades:
            if not nome_tem_fonte(ent.nome, textos.get(numero, "")):
                nomes_sem_fonte.append(f"{numero}: {ent.nome}")
        g = gabarito.get(numero)
        if g is None:
            continue
        naturezas = {r.natureza for r in ext.registros}
        natureza_g = normalizar(g.natureza_esperada)
        if "hipotese" in natureza_g:
            hip_esp += 1
            hip_ok += "hipotese" in naturezas
        if "opiniao" in natureza_g:
            op_esp += 1
            op_ok += "opiniao" in naturezas
        if normalizar(g.pendencia_esperada) and not normalizar(g.pendencia_esperada).startswith("preencher"):
            pend_aval += 1
            pend_ok += _esperado(g.pendencia_esperada) == bool(ext.pendencias)

    origem = {r.id: r.mensagem for r in resultado.registros}
    origem.update({p.id: p.mensagem for p in resultado.pendencias})
    armadilhas = armadilhas_ok = com_fonte_esp = com_fonte = fonte_ok = invalidas = 0
    for p in perguntas:
        resp = resultado.respostas.get(p.numero)
        if resp is None:
            continue
        invalidas += len(resp.citacoes_invalidas)
        if e_armadilha(p):
            armadilhas += 1
            armadilhas_ok += disse_que_nao_sabe(resp.resposta, resp.sem_informacao)
            continue
        com_fonte_esp += 1
        if resp.registros_citados and not resp.sem_informacao:
            com_fonte += 1
            citadas = {origem[c] for c in resp.registros_citados if c in origem}
            fonte_ok += bool(citadas & set(p.mensagens_fonte)) if p.mensagens_fonte else 0

    latencias = [c.latencia_ms for c in resultado.chamadas if not c.erro]
    return Placar(
        modelo=resultado.modelo,
        mensagens=len(resultado.extracoes),
        json_extracao_ok=sum(e.json_valido for e in resultado.extracoes.values()),
        registros=len(resultado.registros),
        pendencias=len(resultado.pendencias),
        nomes_sem_fonte=nomes_sem_fonte,
        hipoteses_esperadas=hip_esp,
        hipoteses_ok=hip_ok,
        opinioes_esperadas=op_esp,
        opinioes_ok=op_ok,
        pendencias_concordantes=pend_ok,
        pendencias_avaliadas=pend_aval,
        perguntas=len(resultado.respostas),
        armadilhas=armadilhas,
        armadilhas_ok=armadilhas_ok,
        com_fonte_esperada=com_fonte_esp,
        respondeu_com_fonte=com_fonte,
        fonte_correta=fonte_ok,
        citacoes_invalidas=invalidas,
        custo_usd=sum(c.custo_usd for c in resultado.chamadas),
        latencia_media_ms=int(sum(latencias) / len(latencias)) if latencias else 0,
        latencia_max_ms=max(latencias, default=0),
        erros=sum(1 for c in resultado.chamadas if c.erro),
    )
