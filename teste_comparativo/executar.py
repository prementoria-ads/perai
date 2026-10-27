"""Etapas 2 e 3: cada modelo extrai as mensagens e depois responde às perguntas
usando só a memória que ele mesmo montou."""

from __future__ import annotations

from dataclasses import dataclass, field

from perai import consulta, extracao
from perai.config import Modelo
from perai.llm import Cliente, ErroModelo
from teste_comparativo.dados import PerguntaTeste
from teste_comparativo.preparar import Entrada


@dataclass
class Chamada:
    etapa: str
    item: str
    custo_usd: float
    latencia_ms: int
    erro: str | None = None


@dataclass
class ResultadoModelo:
    modelo: str
    extracoes: dict[str, extracao.Extracao] = field(default_factory=dict)
    respostas: dict[str, consulta.RespostaConsulta] = field(default_factory=dict)
    chamadas: list[Chamada] = field(default_factory=list)

    @property
    def registros(self) -> list[extracao.Registro]:
        return [r for e in self.extracoes.values() for r in e.registros]

    @property
    def pendencias(self) -> list[extracao.Pendencia]:
        return [p for e in self.extracoes.values() for p in e.pendencias]


def executar_modelo(
    cliente: Cliente,
    modelo: Modelo,
    entradas: list[Entrada],
    perguntas: list[PerguntaTeste],
    hoje: str | None,
    progresso=print,
) -> ResultadoModelo:
    resultado = ResultadoModelo(modelo=modelo.apelido)
    conhecidas: list[str] = []

    # Mensagens em ordem: cada uma já conhece as entidades das anteriores.
    for entrada in sorted(entradas, key=lambda e: int(e.numero)):
        if not entrada.texto.strip():
            continue
        pedido = extracao.montar_pedido(
            entrada.numero, entrada.texto, entrada.data_situacao, entrada.tipo, conhecidas
        )
        try:
            r = cliente.conversar(modelo, "extracao", extracao.SISTEMA_EXTRACAO, pedido)
            ext = extracao.interpretar(entrada.numero, r.texto)
            resultado.chamadas.append(Chamada("extracao", entrada.numero, r.custo_usd, r.latencia_ms))
        except ErroModelo as erro:
            ext = extracao.Extracao(mensagem=entrada.numero, json_valido=False, problemas=[str(erro)])
            resultado.chamadas.append(Chamada("extracao", entrada.numero, 0.0, 0, str(erro)))
        resultado.extracoes[entrada.numero] = ext
        conhecidas.extend(e.nome for e in ext.entidades)
        progresso(f"  {modelo.apelido}: mensagem {entrada.numero} extraída")

    memoria = consulta.formatar_memoria(resultado.registros, resultado.pendencias)
    ids = {r.id for r in resultado.registros} | {p.id for p in resultado.pendencias}
    for pergunta in perguntas:
        pedido = consulta.montar_pedido(pergunta.pergunta, memoria, hoje)
        try:
            r = cliente.conversar(modelo, "consulta", consulta.SISTEMA_CONSULTA, pedido)
            resposta = consulta.interpretar(pergunta.pergunta, r.texto, ids)
            resultado.chamadas.append(Chamada("consulta", pergunta.numero, r.custo_usd, r.latencia_ms))
        except ErroModelo as erro:
            resposta = consulta.RespostaConsulta(pergunta=pergunta.pergunta, json_valido=False,
                                                 resposta=f"[erro] {erro}")
            resultado.chamadas.append(Chamada("consulta", pergunta.numero, 0.0, 0, str(erro)))
        resultado.respostas[pergunta.numero] = resposta
        progresso(f"  {modelo.apelido}: pergunta {pergunta.numero} respondida")
    return resultado
