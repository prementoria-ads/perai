"""Agenda do usuário (D-051): junta o repositório, o cliente do Google e a cifra.

O resto do Peraí só conhece esta classe. Sem Google configurado, `disponivel` é falso
e nada muda no comportamento do bot.
"""

from __future__ import annotations

import logging
import unicodedata
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from perai.google import (VALIDADE_LINK, Cifra, ErroGoogle, EventoAgenda, Google, _local, fim_padrao,
                          hash_token, novo_token_link)

log = logging.getLogger("perai.agenda")
FUSO = ZoneInfo("America/Sao_Paulo")


def _agora() -> datetime:
    return datetime.now(FUSO).replace(tzinfo=None)


MAXIMO_APAGAR = 60   # ocorrências apagadas uma a uma num pedido só
_PEQUENAS = {"de", "do", "da", "dos", "das", "com", "o", "a", "os", "as", "e", "no", "na", "em"}


def _normal(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "").lower()
    return "".join(ch for ch in t if not unicodedata.combining(ch))


def combina(titulo: str, busca: str) -> bool:
    """Todas as palavras ditas ("rotary umuarama") estão no título do evento."""
    palavras = [p for p in _normal(busca).split() if p not in _PEQUENAS]
    alvo = _normal(titulo)
    return bool(palavras) and all(p in alvo for p in palavras)


def regra_ate(regra: str, primeira: str, dia_inteiro: bool) -> str:
    """RRULE que termina antes de `primeira` (AAAA-MM-DDTHH:MM em Brasília, ou AAAA-MM-DD)."""
    partes = [p for p in regra.split(":", 1)[1].split(";") if p and not p.upper().startswith(("UNTIL=", "COUNT="))]
    if dia_inteiro:
        ate = (date.fromisoformat(primeira[:10]) - timedelta(days=1)).strftime("%Y%m%d")
    else:
        limite = datetime.fromisoformat(primeira).replace(tzinfo=FUSO) - timedelta(minutes=1)
        ate = limite.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "RRULE:" + ";".join(partes + [f"UNTIL={ate}"])


class Agenda:
    def __init__(self, repo, google: Google | None, cifra: Cifra | None, endereco_publico: str):
        self.repo = repo
        self.google = google
        self.cifra = cifra
        self.endereco = endereco_publico.rstrip("/")

    @property
    def disponivel(self) -> bool:
        return self.google is not None and self.cifra is not None

    # -- conexão -------------------------------------------------------------------------

    def link_conexao(self, usuario_id: str) -> str:
        token = novo_token_link()
        self.repo.criar_conexao_google(usuario_id, hash_token(token), _agora() + VALIDADE_LINK)
        return f"{self.endereco}/google/conectar?t={token}"

    def url_google(self, token: str) -> str | None:
        """Passo 2 do link: confere o token e devolve o endereço de autorização do Google."""
        if not self.repo.conexao_google(hash_token(token), _agora()):
            return None
        return self.google.url_autorizacao(token)

    def concluir(self, estado: str, codigo: str) -> tuple[str, str]:
        """Retorno do Google: troca o código, guarda o refresh token cifrado. (usuario_id, email)"""
        usuario_id = self.repo.conexao_google(hash_token(estado), _agora())
        if not usuario_id:
            raise PermissionError("link vencido ou já usado")
        refresh, escopos, email = self.google.trocar_codigo(codigo)
        self.repo.salvar_conta_google(usuario_id, email, self.cifra.cifrar(refresh), escopos)
        self.repo.usar_conexao_google(hash_token(estado), _agora())
        return usuario_id, email

    def conta(self, usuario_id: str) -> tuple[str, str, str] | None:
        """(email, refresh_token, agenda_id) ou None."""
        if not self.disponivel:
            return None
        c = self.repo.conta_google(usuario_id)
        if not c:
            return None
        email, cifrado, agenda = c
        return email, self.cifra.decifrar(cifrado), agenda

    def email(self, usuario_id: str) -> str | None:
        c = self.conta(usuario_id)
        return c[0] if c else None

    def desconectar(self, usuario_id: str) -> bool:
        c = self.conta(usuario_id)
        if not c:
            return False
        self.google.revogar(c[1])
        self.repo.desconectar_google(usuario_id, _agora())
        return True

    # -- leitura -------------------------------------------------------------------------

    def eventos(self, usuario_id: str, dias_antes: int = 1, dias_depois: int = 14) -> list[EventoAgenda]:
        """Eventos da agenda na janela; em caso de falha, lista vazia (o bot segue sem agenda)."""
        c = self.conta(usuario_id)
        if not c:
            return []
        hoje = datetime.now(FUSO).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            return self.google.listar(c[1], c[2], hoje - timedelta(days=dias_antes),
                                      hoje + timedelta(days=dias_depois + 1), maximo=120)
        except (ErroGoogle, OSError, ValueError) as erro:
            log.warning("não consegui ler a agenda: %s", erro)
            return []

    def eventos_em(self, usuario_id: str, dia) -> list[EventoAgenda]:
        """Compromissos com hora num dia (para ligar uma despesa a eles), com os da agenda importada."""
        c = self.conta(usuario_id)
        eventos = []
        if c:
            inicio = datetime(dia.year, dia.month, dia.day, tzinfo=FUSO)
            try:
                eventos = self.google.listar(c[1], c[2], inicio, inicio + timedelta(days=1))
            except (ErroGoogle, OSError, ValueError) as erro:
                log.warning("não consegui ler a agenda: %s", erro)
        eventos += self.importados(usuario_id, dia.isoformat(), dia.isoformat() + "T23:59")
        eventos.sort(key=lambda e: e.inicio or "")
        return [e for e in eventos if not e.dia_inteiro and (e.inicio or "").startswith(dia.isoformat())]

    # -- outras agendas, só leitura (D-064) -------------------------------------------------

    def importados(self, usuario_id: str, desde: str, ate: str) -> list[EventoAgenda]:
        """Compromissos da última importação (.ics) de cada agenda, na janela de datas."""
        return [EventoAgenda(id=f"ics:{x['mensagem_id']}:{x['uid']}:{x['inicio']}", titulo=x["titulo"],
                             inicio=x["inicio"], fim=x["fim"], local=x["local"], dia_inteiro=x["dia_inteiro"],
                             agenda=x["rotulo"])
                for x in self.repo.agenda_importada(usuario_id, desde, ate)]

    def ocupados(self, usuario_id: str, desde: datetime, ate: datetime) -> list[EventoAgenda]:
        """Blocos "ocupado" das agendas de outra conta (ex.: L8), sem título."""
        c = self.conta(usuario_id)
        agendas = dict(self.repo.agendas_ocupado(usuario_id))
        if not c or not agendas:
            return []
        try:
            blocos = self.google.livre_ocupado(c[1], list(agendas), desde, ate)
        except (ErroGoogle, OSError, ValueError) as erro:
            log.warning("não consegui ler livre/ocupado: %s", erro)
            return []
        return [EventoAgenda(id=f"ocupado:{cal}:{ini}", titulo="ocupado", inicio=ini, fim=fim,
                             agenda=agendas[cal], so_ocupado=True)
                for cal, lista in blocos.items() for ini, fim in lista if ini]

    def eventos_completos(self, usuario_id: str, dias_antes: int = 1, dias_depois: int = 14) -> list[EventoAgenda]:
        """Agenda principal + agenda importada + livre/ocupado, em ordem. Bloco ocupado que coincide
        com um compromisso importado da mesma agenda sai (o importado tem mais detalhes)."""
        hoje = datetime.now(FUSO).replace(hour=0, minute=0, second=0, microsecond=0)
        desde, ate = hoje - timedelta(days=dias_antes), hoje + timedelta(days=dias_depois + 1)
        principal = self.eventos(usuario_id, dias_antes, dias_depois)
        importados = self.importados(usuario_id, desde.strftime("%Y-%m-%d"), ate.strftime("%Y-%m-%dT%H:%M"))
        ocupados = [o for o in self.ocupados(usuario_id, desde, ate)
                    if not any(i.agenda == o.agenda and not i.dia_inteiro and i.inicio < (o.fim or o.inicio)
                               and (i.fim or i.inicio) > o.inicio for i in importados)]
        return sorted(principal + importados + ocupados, key=lambda e: e.inicio or "")

    # -- escrita em eventos criados pelo Peraí -------------------------------------------

    def criar(self, usuario_id: str, mensagem_id: str | None, registro_id: str | None, titulo: str,
              inicio: str, fim: str | None, local: str | None) -> dict | None:
        c = self.conta(usuario_id)
        if not c:
            return None
        fim = fim if fim and fim > inicio else fim_padrao(inicio)
        descricao = "Criado pelo Peraí a partir da sua mensagem no Telegram."
        google_id = self.google.criar(c[1], c[2], titulo, inicio, fim, local, descricao)
        local_id = self.repo.salvar_evento(usuario_id, mensagem_id, registro_id, google_id, titulo,
                                           inicio, fim, local)
        return {"id": local_id, "titulo": titulo, "inicio": inicio, "fim": fim, "local": local}

    def criados_futuros(self, usuario_id: str) -> list[dict]:
        if not self.conta(usuario_id):
            return []
        return self.repo.eventos_criados(usuario_id, _agora() - timedelta(hours=12))

    def desfazer(self, usuario_id: str, evento_id: str) -> dict | None:
        e = self.repo.evento(usuario_id, evento_id)
        c = self.conta(usuario_id)
        if not e or not c or e["situacao"] in ("desfeito", "cancelado"):
            return None
        self.google.apagar(c[1], c[2], e["google_evento_id"])
        self.repo.mudar_evento(usuario_id, evento_id, None, situacao="desfeito")
        return e

    def ajustar(self, usuario_id: str, evento_id: str, mensagem_id: str | None,
                inicio: str | None = None, fim: str | None = None, cancelar: bool = False,
                titulo: str | None = None) -> dict | None:
        e = self.repo.evento(usuario_id, evento_id)
        c = self.conta(usuario_id)
        if not e or not c:
            return None
        if cancelar:
            self.google.apagar(c[1], c[2], e["google_evento_id"])
            self.repo.mudar_evento(usuario_id, evento_id, mensagem_id, situacao="cancelado")
            return {**e, "situacao": "cancelado"}
        if inicio and not fim:
            # Mantém a duração original.
            duracao = datetime.fromisoformat(e["fim"]) - datetime.fromisoformat(e["inicio"])
            fim = (datetime.fromisoformat(inicio) + duracao).strftime("%Y-%m-%dT%H:%M")
        self.google.alterar(c[1], c[2], e["google_evento_id"], inicio=inicio, fim=fim, titulo=titulo)
        campos = {k: v for k, v in (("inicio", inicio), ("fim", fim), ("titulo", titulo)) if v}
        campos["situacao"] = "alterado"
        self.repo.mudar_evento(usuario_id, evento_id, mensagem_id, **campos)
        return {**e, **campos}


    # -- cancelar eventos que o Peraí não criou (D-059): apaga direto e oferece Desfazer ----

    def cancelar_eventos(self, usuario_id: str, mensagem_id: str | None, busca: str,
                         desde: str | None = None, ate: str | None = None) -> list[dict] | None:
        """Apaga os eventos cujo título tem as palavras de `busca`. Sem período, "todos os próximos":
        série que se repete termina antes da próxima vez (o passado fica). None = agenda desligada."""
        c = self.conta(usuario_id)
        if not c:
            return None
        refresh, agenda = c[1], c[2]
        agora = datetime.now(FUSO)
        inicio = datetime.fromisoformat(desde).replace(tzinfo=FUSO) if desde else agora
        fim = (datetime.fromisoformat(ate).replace(tzinfo=FUSO) + timedelta(days=1)) if ate \
            else inicio + timedelta(days=366)
        eventos = [e for e in self.google.listar(refresh, agenda, inicio, fim, maximo=250, busca=busca)
                   if combina(e.titulo, busca)]
        feitos, avulsos, series = [], [], {}
        for e in eventos:
            if e.recorrente_de and e.organizador and not ate:
                series.setdefault(e.recorrente_de, []).append(e)
            else:
                avulsos.append(e)
        for serie_id, ocorrencias in series.items():
            primeira = ocorrencias[0]
            try:
                mestre = self.google.obter(refresh, agenda, serie_id)
                regras = list(mestre.get("recurrence") or [])
                comeco = mestre.get("start", {})
                comeco = comeco.get("date") or _local(comeco.get("dateTime")) or ""
                if not regras or comeco >= primeira.inicio:
                    # A série ainda não começou: sai inteira.
                    self.google.apagar(refresh, agenda, serie_id)
                    tipo, anterior = "apagado", None
                else:
                    novas = [regra_ate(r, primeira.inicio, primeira.dia_inteiro)
                             if r.upper().startswith("RRULE:") else r for r in regras]
                    self.google.mudar_recorrencia(refresh, agenda, serie_id, novas)
                    tipo, anterior = "serie_encerrada", regras
            except (ErroGoogle, OSError, ValueError) as erro:
                log.warning("não consegui encerrar a série; apago uma a uma: %s", erro)
                avulsos.extend(ocorrencias)
                continue
            self.repo.salvar_cancelamento(usuario_id, mensagem_id, serie_id, primeira.titulo,
                                          primeira.inicio, tipo, anterior)
            feitos.append({"titulo": primeira.titulo, "inicio": primeira.inicio, "serie": True})
        avulsos.sort(key=lambda e: e.inicio or "")
        for e in avulsos[:MAXIMO_APAGAR]:
            self.google.apagar(refresh, agenda, e.id)
            self.repo.salvar_cancelamento(usuario_id, mensagem_id, e.id, e.titulo, e.inicio, "apagado", None)
            feitos.append({"titulo": e.titulo, "inicio": e.inicio, "serie": False})
        if len(avulsos) > MAXIMO_APAGAR:
            feitos.append({"titulo": None, "restantes": len(avulsos) - MAXIMO_APAGAR})
        return feitos

    def mudar_evento(self, usuario_id: str, mensagem_id: str | None, busca: str, dia: str | None = None,
                     novo_dia: str | None = None, nova_hora: str | None = None,
                     novo_titulo: str | None = None) -> dict | None:
        """Muda horário e/ou nome da próxima vez de um evento (D-060). Evento que se repete: só
        aquela vez. Devolve {"achou": False} se não encontrar; None se a agenda está desligada."""
        c = self.conta(usuario_id)
        if not c:
            return None
        refresh, agenda = c[1], c[2]
        agora = datetime.now(FUSO)
        inicio = datetime.fromisoformat(dia).replace(tzinfo=FUSO) if dia else agora
        fim = inicio + timedelta(days=1 if dia else 15)
        candidatos = [e for e in self.google.listar(refresh, agenda, inicio, fim, maximo=100, busca=busca)
                      if combina(e.titulo, busca) and (dia or (e.inicio or "") >= agora.strftime("%Y-%m-%dT%H:%M"))]
        if not candidatos:
            return {"achou": False}
        e = candidatos[0]
        novo_inicio = novo_fim = None
        if novo_dia or nova_hora:
            if e.dia_inteiro:
                return {"achou": True, "dia_inteiro": True, "titulo": e.titulo}
            novo_inicio = f"{novo_dia or e.inicio[:10]}T{nova_hora or e.inicio[11:16]}"
            duracao = (datetime.fromisoformat(e.fim) - datetime.fromisoformat(e.inicio)) if e.fim \
                else timedelta(hours=1)
            novo_fim = (datetime.fromisoformat(novo_inicio) + duracao).strftime("%Y-%m-%dT%H:%M")
        titulo = novo_titulo if novo_titulo and novo_titulo != e.titulo else None
        if not (novo_inicio or titulo):
            return {"achou": True, "igual": True, "titulo": e.titulo}
        self.google.alterar(refresh, agenda, e.id, inicio=novo_inicio, fim=novo_fim, titulo=titulo)
        anterior = {"titulo": e.titulo, "inicio": None if e.dia_inteiro else e.inicio,
                    "fim": None if e.dia_inteiro else e.fim}
        self.repo.salvar_cancelamento(usuario_id, mensagem_id, e.id, e.titulo, e.inicio, "alterado", None,
                                      anterior)
        return {"achou": True, "titulo_antigo": e.titulo, "titulo": titulo or e.titulo,
                "inicio": novo_inicio or e.inicio, "fim": novo_fim or e.fim, "mudou_hora": bool(novo_inicio),
                "recorrente": bool(e.recorrente_de), "dia_inteiro": e.dia_inteiro}

    def desfazer_cancelamentos(self, usuario_id: str, mensagem_id: str) -> list[dict]:
        """Botão Desfazer: devolve à agenda o que aquele pedido cancelou."""
        c = self.conta(usuario_id)
        if not c:
            return []
        voltaram = []
        for x in self.repo.cancelamentos(usuario_id, mensagem_id):
            if x["tipo"] == "serie_encerrada":
                self.google.mudar_recorrencia(c[1], c[2], x["google_evento_id"], x["recorrencia_anterior"])
            elif x["tipo"] == "alterado":
                a = x.get("anterior") or {}
                self.google.alterar(c[1], c[2], x["google_evento_id"], inicio=a.get("inicio"), fim=a.get("fim"),
                                    titulo=a.get("titulo"))
            else:
                self.google.restaurar(c[1], c[2], x["google_evento_id"])
            self.repo.marcar_cancelamento_desfeito(usuario_id, x["id"], _agora())
            voltaram.append(x)
        return voltaram


def agenda_do_ambiente(repo) -> Agenda:
    """Monta a Agenda a partir do .env. Sem as variáveis do Google, fica indisponível."""
    import os
    endereco = os.environ.get("PERAI_ENDERECO", "https://perai.prementoria.com")
    cid, segredo = os.environ.get("GOOGLE_CLIENT_ID", "").strip(), os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    chave = os.environ.get("PERAI_CHAVE_CIFRA", "").strip()
    if not (cid and segredo and chave):
        return Agenda(repo, None, None, endereco)
    return Agenda(repo, Google(cid, segredo, f"{endereco.rstrip('/')}/google/retorno"), Cifra(chave), endereco)
