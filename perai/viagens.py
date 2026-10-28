"""Viagens (D-056): nome do relatório, período e textos do acompanhamento diário."""

from __future__ import annotations

from datetime import date

MESES_CURTOS = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def _lista(nomes: list[str]) -> str:
    nomes = [n for n in nomes if n]
    if len(nomes) <= 1:
        return "".join(nomes)
    return ", ".join(nomes[:-1]) + " e " + nomes[-1]


def periodo(inicio: date, fim: date) -> str:
    """'5 a 7 out26', '30 set a 2 out26', '28 dez25 a 3 jan26', '5 out26'."""
    a = f"{fim.year % 100:02d}"
    if inicio == fim:
        return f"{inicio.day} {MESES_CURTOS[inicio.month - 1]}{a}"
    if (inicio.year, inicio.month) == (fim.year, fim.month):
        return f"{inicio.day} a {fim.day} {MESES_CURTOS[fim.month - 1]}{a}"
    if inicio.year == fim.year:
        return f"{inicio.day} {MESES_CURTOS[inicio.month - 1]} a {fim.day} {MESES_CURTOS[fim.month - 1]}{a}"
    return (f"{inicio.day} {MESES_CURTOS[inicio.month - 1]}{inicio.year % 100:02d} a "
            f"{fim.day} {MESES_CURTOS[fim.month - 1]}{a}")


def nome_padrao(inicio: date, fim: date, objetivo: str | None, cidades: list[str],
                acompanhantes: list[str]) -> str:
    """'Prospecção comercial com Júlio em Foz, Maringá e Londrina · 5 a 7 out26'."""
    base = (objetivo or "Viagem").strip()
    base = base[0].upper() + base[1:]
    if acompanhantes:
        base += f" com {_lista(acompanhantes)}"
    if cidades:
        base += f" em {_lista(cidades)}"
    return f"{base} · {periodo(inicio, fim)}"


def dia_da_viagem(inicio: date, fim: date, hoje: date) -> tuple[int, int]:
    return (hoje - inicio).days + 1, (fim - inicio).days + 1


def rotulo_dia(d: date) -> str:
    return f"{DIAS[d.weekday()]} {d:%d/%m}"
