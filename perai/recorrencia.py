"""Repetição de pendências: "todo dia 1", "toda segunda", "todo dia", "todo ano em 15/03".

Formato guardado no banco (texto curto, fácil de ler e de validar):
  diaria          todo dia
  semanal:N       toda semana, N = 0 (segunda) ... 6 (domingo)
  mensal:D        todo mês no dia D (1 a 31; em mês mais curto, cai no último dia)
  anual:MM-DD     todo ano nessa data
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta

DIAS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
_FORMATO = re.compile(r"^(diaria|semanal:[0-6]|mensal:([1-9]|[12]\d|3[01])|anual:(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01]))$")


def valida(valor) -> str | None:
    if not isinstance(valor, str):
        return None
    valor = valor.strip().lower()
    return valor if _FORMATO.match(valor) else None


def _dia_no_mes(ano: int, mes: int, dia: int) -> date:
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


def proxima(regra: str, depois_de: date) -> date:
    """Primeira data da regra estritamente depois de `depois_de`."""
    tipo, _, arg = regra.partition(":")
    if tipo == "diaria":
        return depois_de + timedelta(days=1)
    if tipo == "semanal":
        alvo = int(arg)
        dias = (alvo - depois_de.weekday()) % 7 or 7
        return depois_de + timedelta(days=dias)
    if tipo == "mensal":
        dia = int(arg)
        candidato = _dia_no_mes(depois_de.year, depois_de.month, dia)
        if candidato > depois_de:
            return candidato
        ano, mes = (depois_de.year + 1, 1) if depois_de.month == 12 else (depois_de.year, depois_de.month + 1)
        return _dia_no_mes(ano, mes, dia)
    if tipo == "anual":
        mes, dia = (int(x) for x in arg.split("-"))
        candidato = _dia_no_mes(depois_de.year, mes, dia)
        return candidato if candidato > depois_de else _dia_no_mes(depois_de.year + 1, mes, dia)
    raise ValueError(f"regra desconhecida: {regra}")


def proxima_ocorrencia(regra: str, prazo: str | None, lembrete_em: str | None,
                       hoje: date) -> tuple[str, str | None]:
    """(novo prazo, novo lembrete) da próxima vez, mantendo a hora do lembrete."""
    base = date.fromisoformat(prazo) if prazo else hoje
    nova = proxima(regra, max(base, hoje - timedelta(days=1)))
    lembrete = None
    if lembrete_em:
        hora = datetime.fromisoformat(lembrete_em).strftime("%H:%M")
        lembrete = f"{nova.isoformat()}T{hora}"
    return nova.isoformat(), lembrete


def por_extenso(regra: str | None) -> str:
    if not regra:
        return ""
    tipo, _, arg = regra.partition(":")
    if tipo == "diaria":
        return "todo dia"
    if tipo == "semanal":
        dia = DIAS[int(arg)]
        return f"todo {dia}" if dia in ("sábado", "domingo") else f"toda {dia}"
    if tipo == "mensal":
        return f"todo dia {int(arg)}"
    if tipo == "anual":
        mes, dia = arg.split("-")
        return f"todo ano em {dia}/{mes}"
    return regra
