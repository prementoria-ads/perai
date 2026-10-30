"""Agenda exportada em .ics (D-064): lida pelo código, sem IA.

O Google Agenda exporta um .zip com um .ics por agenda (Configurações → Importar e exportar).
Aqui o arquivo vira uma lista de compromissos numa janela de datas, com as repetições
(RRULE) abertas, exceções (EXDATE) e mudanças de uma vez só (RECURRENCE-ID) aplicadas.
Só leitura: nada volta para a agenda de origem.
"""

from __future__ import annotations

import calendar
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FUSO = ZoneInfo("America/Sao_Paulo")
DIAS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
LIMITE_ARQUIVO = 50_000_000      # bytes de um .ics (descompactado)
LIMITE_OCORRENCIAS = 3000        # por série, para não girar à toa


@dataclass
class EventoIcs:
    uid: str
    titulo: str
    inicio: str                  # AAAA-MM-DDTHH:MM (Brasília) ou AAAA-MM-DD (dia inteiro)
    fim: str | None
    local: str | None
    dia_inteiro: bool


def rotulo(nome: str | None) -> str:
    """Nome curto da agenda: "nelson.takemura@l8group.net" -> "L8"."""
    nome = (nome or "").strip()
    if "@" in nome:
        base = nome.split("@", 1)[1].split(".")[0]
        base = re.sub(r"group$", "", base, flags=re.I) or base
        return base.upper() if len(base) <= 3 else base.capitalize()
    return nome[:30] or "agenda importada"


def _desdobrar(texto: str) -> list[str]:
    linhas: list[str] = []
    for linha in texto.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if linha[:1] in (" ", "\t") and linhas:
            linhas[-1] += linha[1:]
        elif linha:
            linhas.append(linha)
    return linhas


def _propriedade(linha: str) -> tuple[str, dict, str]:
    cabeca, _, valor = linha.partition(":")
    partes = cabeca.split(";")
    params = {}
    for p in partes[1:]:
        k, _, v = p.partition("=")
        params[k.upper()] = v.strip('"')
    return partes[0].upper(), params, valor


def _texto(valor: str) -> str:
    return (valor.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";")
            .replace("\\\\", "\\").strip())


def _fuso(params: dict) -> ZoneInfo:
    try:
        return ZoneInfo(params.get("TZID", "America/Sao_Paulo"))
    except (ZoneInfoNotFoundError, ValueError):
        return FUSO


def _momento(valor: str, params: dict) -> tuple[datetime, bool, ZoneInfo]:
    """(início como datetime na zona do evento, dia inteiro?, zona)."""
    valor = valor.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", valor):
        return datetime.strptime(valor[:8], "%Y%m%d"), True, FUSO
    if valor.endswith("Z"):
        utc = datetime.strptime(valor[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return utc.astimezone(FUSO).replace(tzinfo=None), False, FUSO
    zona = _fuso(params)
    return datetime.strptime(valor[:15], "%Y%m%dT%H%M%S"), False, zona


def _local_brasilia(momento: datetime, zona: ZoneInfo) -> datetime:
    return momento.replace(tzinfo=zona).astimezone(FUSO).replace(tzinfo=None)


def _regra(texto: str) -> dict:
    return {k.upper(): v for k, _, v in (p.partition("=") for p in texto.split(";") if p)}


def _nesimo(ano: int, mes: int, dia_semana: int, n: int) -> date | None:
    dias = [d for d in range(1, calendar.monthrange(ano, mes)[1] + 1) if date(ano, mes, d).weekday() == dia_semana]
    try:
        return date(ano, mes, dias[n - 1 if n > 0 else n])
    except IndexError:
        return None


def _ocorrencias(inicio: datetime, regra: dict, ate: datetime):
    """Inícios da série (na zona do evento), em ordem, até `ate` ou o fim da regra."""
    freq = regra.get("FREQ", "")
    passo = max(1, int(regra.get("INTERVAL", "1") or 1))
    total = int(regra["COUNT"]) if regra.get("COUNT", "").isdigit() else None
    limite = ate
    if regra.get("UNTIL"):
        u = regra["UNTIL"]
        try:
            fim, _, _ = _momento(u, {})
            limite = min(limite, fim if len(u) > 8 else fim + timedelta(days=1) - timedelta(seconds=1))
        except ValueError:
            pass
    byday = [d for d in regra.get("BYDAY", "").split(",") if d]
    bymonthday = [int(d) for d in regra.get("BYMONTHDAY", "").split(",") if d.lstrip("-").isdigit()]
    hora = inicio.time()
    contados = 0

    def emitir(momento):
        nonlocal contados
        if momento < inicio:
            return None
        contados += 1
        return momento

    if freq == "DAILY":
        m = inicio
        while m <= limite and contados < LIMITE_OCORRENCIAS and (total is None or contados < total):
            if emitir(m):
                yield m
            m += timedelta(days=passo)
    elif freq == "WEEKLY":
        dias = sorted(DIAS[d[-2:]] for d in byday if d[-2:] in DIAS) or [inicio.weekday()]
        semana = inicio.date() - timedelta(days=inicio.weekday())
        while contados < LIMITE_OCORRENCIAS:
            for d in dias:
                m = datetime.combine(semana + timedelta(days=d), hora)
                if m > limite or (total is not None and contados >= total):
                    return
                if emitir(m):
                    yield m
            semana += timedelta(weeks=passo)
    elif freq in ("MONTHLY", "YEARLY"):
        ano, mes = inicio.year, inicio.month
        while contados < LIMITE_OCORRENCIAS:
            candidatos = []
            if freq == "YEARLY" and not byday and not bymonthday:
                ultimo = calendar.monthrange(ano, inicio.month)[1]
                if inicio.day <= ultimo:
                    candidatos = [date(ano, inicio.month, inicio.day)]
            elif byday:
                for d in byday:
                    achado = re.fullmatch(r"([+-]?\d+)?([A-Z]{2})", d)
                    if achado and achado.group(2) in DIAS:
                        n = int(achado.group(1) or 1)
                        dia = _nesimo(ano, mes, DIAS[achado.group(2)], n)
                        if dia:
                            candidatos.append(dia)
            else:
                ultimo = calendar.monthrange(ano, mes)[1]
                for d in bymonthday or [inicio.day]:
                    d = d if d > 0 else ultimo + 1 + d
                    if 1 <= d <= ultimo:
                        candidatos.append(date(ano, mes, d))
            for dia in sorted(candidatos):
                m = datetime.combine(dia, hora)
                if m > limite or (total is not None and contados >= total):
                    return
                if emitir(m):
                    yield m
            if freq == "YEARLY":
                ano += passo
            else:
                mes += passo
                ano, mes = ano + (mes - 1) // 12, (mes - 1) % 12 + 1
            if datetime(ano, mes if freq == "MONTHLY" else inicio.month, 1) > limite:
                return
    else:
        yield inicio


def ler_ics(texto: str, desde: datetime, ate: datetime) -> tuple[str | None, list[EventoIcs]]:
    """(nome da agenda, compromissos que tocam a janela [desde, ate]) em horário de Brasília."""
    nome = None
    blocos: list[dict] = []
    atual: dict | None = None
    for linha in _desdobrar(texto):
        prop, params, valor = _propriedade(linha)
        if prop == "X-WR-CALNAME" and nome is None:
            nome = _texto(valor)
        elif prop == "BEGIN" and valor.upper() == "VEVENT":
            atual = {"exdate": []}
        elif prop == "END" and valor.upper() == "VEVENT" and atual is not None:
            blocos.append(atual)
            atual = None
        elif atual is not None:
            if prop in ("DTSTART", "DTEND", "RECURRENCE-ID"):
                atual[prop] = (valor, params)
            elif prop == "EXDATE":
                atual["exdate"] += [(v, params) for v in valor.split(",") if v]
            elif prop in ("UID", "SUMMARY", "LOCATION", "RRULE", "STATUS", "DURATION"):
                atual[prop] = valor

    # Mudanças de uma vez só: (uid, início original em Brasília) -> bloco que substitui.
    trocas: dict[tuple[str, datetime], dict] = {}
    for b in blocos:
        if "RECURRENCE-ID" in b and b.get("UID"):
            m, _, zona = _momento(*b["RECURRENCE-ID"])
            trocas[(b["UID"], _local_brasilia(m, zona))] = b

    eventos: list[EventoIcs] = []

    def montar(b, inicio_bras: datetime, duracao: timedelta, dia_inteiro: bool):
        if b.get("STATUS", "").upper() == "CANCELLED":
            return
        fim_bras = inicio_bras + duracao
        if fim_bras < desde or inicio_bras > ate:
            return
        fmt = "%Y-%m-%d" if dia_inteiro else "%Y-%m-%dT%H:%M"
        eventos.append(EventoIcs(b.get("UID", ""), _texto(b.get("SUMMARY", "")) or "(sem título)",
                                 inicio_bras.strftime(fmt),
                                 (fim_bras - timedelta(days=1) if dia_inteiro else fim_bras).strftime(fmt)
                                 if duracao else None,
                                 _texto(b["LOCATION"]) if b.get("LOCATION") else None, dia_inteiro))

    for b in blocos:
        if "DTSTART" not in b:
            continue
        try:
            inicio, dia_inteiro, zona = _momento(*b["DTSTART"])
            if "DTEND" in b:
                fim, _, zona_fim = _momento(*b["DTEND"])
                duracao = (_local_brasilia(fim, zona_fim) - _local_brasilia(inicio, zona)) if not dia_inteiro \
                    else fim - inicio
            else:
                duracao = timedelta(days=1) if dia_inteiro else timedelta(0)
        except ValueError:
            continue
        if "RECURRENCE-ID" in b:
            montar(b, inicio if dia_inteiro else _local_brasilia(inicio, zona), duracao, dia_inteiro)
            continue
        if "RRULE" not in b:
            montar(b, inicio if dia_inteiro else _local_brasilia(inicio, zona), duracao, dia_inteiro)
            continue
        excecoes = set()
        for v, p in b["exdate"]:
            try:
                m, _, z = _momento(v, p)
                excecoes.add(m if len(v.strip()) == 8 else _local_brasilia(m, z))
            except ValueError:
                pass
        # A regra é aberta no horário do próprio evento (respeita horário de verão de outros fusos).
        ate_zona = ate.replace(tzinfo=FUSO).astimezone(zona).replace(tzinfo=None) + timedelta(days=1)
        for m in _ocorrencias(inicio, _regra(b["RRULE"]), ate_zona):
            bras = m if dia_inteiro else _local_brasilia(m, zona)
            if bras in excecoes or (b.get("UID"), bras) in trocas:
                continue
            if bras + duracao >= desde:
                montar(b, bras, duracao, dia_inteiro)
    eventos.sort(key=lambda e: e.inicio)
    return nome, eventos


def agendas_do_arquivo(arquivo: Path) -> list[str]:
    """Textos .ics de um .ics solto ou de dentro do .zip exportado pelo Google."""
    if arquivo.suffix.lower() == ".ics":
        return [arquivo.read_text(encoding="utf-8", errors="replace")]
    textos = []
    with zipfile.ZipFile(arquivo) as z:
        for info in z.infolist():
            if info.filename.lower().endswith(".ics") and info.file_size <= LIMITE_ARQUIVO:
                textos.append(z.read(info).decode("utf-8", errors="replace"))
    return textos


def zip_tem_ics(arquivo: Path) -> bool:
    try:
        with zipfile.ZipFile(arquivo) as z:
            return any(n.lower().endswith(".ics") for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
