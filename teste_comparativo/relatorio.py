"""Etapa 4: resumo para decidir a D-038 e planilhas para revisão humana."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from teste_comparativo.dados import MensagemTeste, PerguntaTeste
from teste_comparativo.executar import ResultadoModelo
from teste_comparativo.pontuacao import Placar, e_armadilha
from teste_comparativo.preparar import Entrada

CAMBIO_PADRAO = 5.29  # R$ por US$, em 26/10/2026


def _pct(parte: int, todo: int) -> str:
    return f"{parte}/{todo} ({parte / todo:.0%})" if todo else "—"


def escrever(
    pasta: Path,
    placares: list[Placar],
    resultados: list[ResultadoModelo],
    mensagens: list[MensagemTeste],
    perguntas: list[PerguntaTeste],
    entradas: list[Entrada],
    avisos: list[str],
    cambio: float = CAMBIO_PADRAO,
) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    custo_preparo = sum(e.custo_usd for e in entradas)

    linhas = [
        "# Teste comparativo dos modelos (D-038)",
        "",
        f"{len(entradas)} mensagens e {len(perguntas)} perguntas. "
        f"Preparação (transcrição e leitura de imagens): US$ {custo_preparo:.4f}, comum a todos.",
        "",
        "As medidas abaixo são automáticas. A decisão final depende da revisão em "
        "`revisao_respostas.csv` e `revisao_extracoes.csv`.",
        "",
        "| Medida | " + " | ".join(p.modelo for p in placares) + " |",
        "| --- | " + " | ".join("---" for _ in placares) + " |",
    ]

    def linha(nome: str, valores: list[str]) -> None:
        linhas.append(f"| {nome} | " + " | ".join(valores) + " |")

    linha("Armadilhas: disse que não sabe", [_pct(p.armadilhas_ok, p.armadilhas) for p in placares])
    linha("Nomes sem fonte na mensagem (revisar)", [str(len(p.nomes_sem_fonte)) for p in placares])
    linha("Respondeu citando fonte", [_pct(p.respondeu_com_fonte, p.com_fonte_esperada) for p in placares])
    linha("Fonte citada é a do gabarito", [_pct(p.fonte_correta, p.com_fonte_esperada) for p in placares])
    linha("Citações inexistentes", [str(p.citacoes_invalidas) for p in placares])
    linha("Hipótese marcada como hipótese", [_pct(p.hipoteses_ok, p.hipoteses_esperadas) for p in placares])
    linha("Opinião marcada como opinião", [_pct(p.opinioes_ok, p.opinioes_esperadas) for p in placares])
    linha("Pendência existe quando devia", [_pct(p.pendencias_concordantes, p.pendencias_avaliadas) for p in placares])
    linha("JSON válido na extração", [_pct(p.json_extracao_ok, p.mensagens) for p in placares])
    linha("Registros / pendências extraídos", [f"{p.registros} / {p.pendencias}" for p in placares])
    linha("Custo do teste (US$)", [f"{p.custo_usd:.4f}" for p in placares])
    linha("Custo do teste (R$)", [f"{p.custo_usd * cambio:.2f}" for p in placares])
    linha("Tempo médio por chamada (s)", [f"{p.latencia_media_ms / 1000:.1f}" for p in placares])
    linha("Tempo máximo (s)", [f"{p.latencia_max_ms / 1000:.1f}" for p in placares])
    linha("Erros de chamada", [str(p.erros) for p in placares])

    linhas += [
        "",
        "Critério da D-038: o vencedor é o mais barato entre os que passam nos critérios do "
        "documento MVP e Critérios de Aceite, com zero memórias inventadas.",
    ]
    if any(p.nomes_sem_fonte for p in placares):
        linhas += ["", "## Nomes sem fonte na mensagem", "",
                   "Podem ser invenção ou só grafia diferente (Tecnoparque x Technopark): confira.", ""]
        for p in placares:
            if p.nomes_sem_fonte:
                linhas.append(f"- **{p.modelo}:** " + "; ".join(p.nomes_sem_fonte))
    if avisos:
        linhas += ["", "## Avisos", ""] + [f"- {a}" for a in avisos]
    (pasta / "resumo.md").write_text("\n".join(linhas) + "\n", encoding="utf-8")

    gabarito = {m.numero: m for m in mensagens}
    with (pasta / "revisao_extracoes.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Nº", "Modelo", "Pessoas esperadas", "Entidades extraídas", "Fatos esperados",
                     "Registros extraídos", "Pendência esperada", "Pendências extraídas",
                     "Dúvidas do modelo", "Correto? (sim/não/parcial)", "Comentário"])
        for numero in sorted(gabarito, key=int):
            g = gabarito[numero]
            for r in resultados:
                ext = r.extracoes.get(numero)
                if ext is None:
                    continue
                w.writerow([
                    numero, r.modelo, g.pessoas_esperadas,
                    "; ".join(f"{e.nome} ({e.tipo}, {e.contexto or '?'})" for e in ext.entidades),
                    g.fatos_esperados,
                    "\n".join(f"[{x.natureza}; {x.dito_por}] {x.conteudo}" for x in ext.registros),
                    g.pendencia_esperada,
                    "\n".join(f"[{p.responsavel}{'; ' + p.quem if p.quem else ''}; "
                              f"{p.prazo or p.prazo_como_dito or 'sem prazo'}] {p.descricao}"
                              for p in ext.pendencias),
                    "; ".join(ext.duvidas + ext.problemas), "", "",
                ])

    with (pasta / "revisao_respostas.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Nº", "Pergunta", "Resposta correta", "Armadilha", "Modelo", "Resposta do modelo",
                     "Fontes citadas", "Correto? (sim/não/parcial)", "Comentário"])
        for p in perguntas:
            for r in resultados:
                resp = r.respostas.get(p.numero)
                if resp is None:
                    continue
                w.writerow([p.numero, p.pergunta, p.resposta_correta, "sim" if e_armadilha(p) else "",
                            r.modelo, resp.resposta, ", ".join(resp.registros_citados), "", ""])

    bruto = {
        "placares": [asdict(p) for p in placares],
        "resultados": [
            {
                "modelo": r.modelo,
                "extracoes": {k: asdict(v) for k, v in r.extracoes.items()},
                "respostas": {k: asdict(v) for k, v in r.respostas.items()},
                "chamadas": [asdict(c) for c in r.chamadas],
            }
            for r in resultados
        ],
    }
    (pasta / "resultado.json").write_text(json.dumps(bruto, ensure_ascii=False, indent=2), encoding="utf-8")
    return pasta / "resumo.md"
