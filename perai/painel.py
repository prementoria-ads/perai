"""Web mínima do Peraí (etapa 7; D-001, D-015, D-063).

Entrar com Google, sem depender do Telegram; consultar e corrigir registros e pendências;
exportar tudo em JSON. Regras:
- A conta Google é ligada uma vez por um convite de uso único (/web no Telegram, 15 minutos).
  Depois disso, "Entrar com Google" funciona sozinho.
- Do Google o Peraí guarda só o "sub" (identificador da conta) e o e-mail. Nada de senha.
- Sessão por cookie (HttpOnly, Secure, SameSite=Lax) com token aleatório; no banco, só o hash.
- Todo formulário leva o código CSRF da sessão.
- Toda correção feita pela web vira uma mensagem do canal "web", que é a fonte dela (D-040).
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets
import time
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from perai import despesas, extracao, recorrencia
from perai.armazenamento import NovaMensagem
from perai.google import Google, hash_token

FUSO = ZoneInfo("America/Sao_Paulo")
VALIDADE_CONVITE = timedelta(minutes=15)
VALIDADE_SESSAO = timedelta(days=30)
VALIDADE_ESTADO = 600  # segundos: tempo para concluir o login no Google
NATUREZA = {"confirmado": "📝 fato", "intencao": "🎯 intenção", "hipotese": "💡 ideia", "opiniao": "💬 opinião"}


class ErroPainel(Exception):
    """Mensagem para mostrar à pessoa (sem detalhes internos)."""


def _agora() -> datetime:
    return datetime.now(FUSO).replace(tzinfo=None)


def _normal(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "").lower()
    return "".join(ch for ch in t if not unicodedata.combining(ch))


def _e(texto) -> str:
    return html.escape("" if texto is None else str(texto))


class Painel:
    def __init__(self, repo, google: Google | None, endereco: str):
        self.repo = repo
        self.google = google
        self.endereco = endereco.rstrip("/")

    @property
    def disponivel(self) -> bool:
        return self.google is not None

    # -- convite, login e sessão ----------------------------------------------------

    def convite(self, usuario_id: str) -> str:
        """Link de uso único para ligar a conta Google (mandado pelo /web no Telegram)."""
        token = secrets.token_urlsafe(24)
        self.repo.criar_convite_web(usuario_id, hash_token(token), _agora() + VALIDADE_CONVITE)
        return f"{self.endereco}/web/ligar?t={token}"

    def iniciar(self, convite: str | None = None) -> tuple[str, str]:
        """(nonce para o cookie, endereço do Google). Com convite: liga a conta; sem: só entra."""
        if convite is not None and not self.repo.convite_web(hash_token(convite), _agora()):
            raise ErroPainel("Este link já foi usado ou passou de 15 minutos. Mande /web no Telegram "
                             "para receber outro.")
        nonce = secrets.token_urlsafe(16)
        estado = f"web.{nonce}" + (f".{convite}" if convite else "")
        return nonce, self.google.url_login(estado)

    def concluir(self, estado: str, nonce_cookie: str | None, codigo: str) -> tuple[str, str]:
        """Retorno do Google. Devolve (usuario_id, token da sessão)."""
        partes = estado.split(".")
        if len(partes) not in (2, 3) or partes[0] != "web" or not nonce_cookie \
                or not hmac.compare_digest(partes[1], nonce_cookie):
            raise ErroPainel("O login expirou ou veio de outra janela. Tente entrar de novo.")
        sub, email, verificado = self.google.identidade(codigo)
        if not verificado:
            raise ErroPainel("Essa conta Google não tem o e-mail verificado.")
        agora = _agora()
        if len(partes) == 3:
            usuario_id = self.repo.usar_convite_web(hash_token(partes[2]), agora)
            if not usuario_id:
                raise ErroPainel("Este link já foi usado ou passou de 15 minutos. Mande /web no Telegram "
                                 "para receber outro.")
            if not self.repo.ligar_google_web(usuario_id, email, sub, agora):
                raise ErroPainel("Essa conta Google já está ligada a outra pessoa no Peraí.")
        else:
            usuario_id = self.repo.usuario_por_google_sub(sub)
            if not usuario_id:
                raise ErroPainel("Essa conta Google ainda não está ligada ao Peraí. Mande /web no Telegram "
                                 "e abra o link que chegar.")
        token = secrets.token_urlsafe(32)
        self.repo.criar_sessao_web(usuario_id, hash_token(token), secrets.token_urlsafe(16),
                                   agora + VALIDADE_SESSAO)
        return usuario_id, token

    def entrar_telegram(self, init_data: str, token_bot: str) -> tuple[str, str]:
        """Painel aberto dentro do Telegram (Mini App): o Telegram assina quem abriu; conferida a
        assinatura, a pessoa entra sem passar pelo Google. Devolve (usuario_id, token da sessão)."""
        usuario_tg = validar_init_data(init_data, token_bot, time.time())
        usuario_id = self.repo.usuario_por_telegram(int(usuario_tg["id"]))
        if not usuario_id:
            raise ErroPainel("Esta conta do Telegram ainda não usa o Peraí.")
        token = secrets.token_urlsafe(32)
        self.repo.criar_sessao_web(usuario_id, hash_token(token), secrets.token_urlsafe(16),
                                   _agora() + VALIDADE_SESSAO)
        return usuario_id, token

    def sessao(self, token: str | None) -> tuple[str, str] | None:
        return self.repo.sessao_web(hash_token(token), _agora()) if token else None

    def sair(self, token: str | None) -> None:
        if token:
            self.repo.encerrar_sessao_web(hash_token(token), _agora())

    # -- ações -------------------------------------------------------------------------

    def _mensagem_web(self, usuario_id: str, texto: str) -> str:
        return self.repo.salvar_mensagem(usuario_id, NovaMensagem(
            canal="web", tipo="texto", texto_original=texto, data_situacao=_agora().date().isoformat()))

    def corrigir_registro(self, usuario_id: str, registro_id: str, texto: str) -> str:
        texto = " ".join((texto or "").split())[:2000]
        registros, _ = self.repo.memoria(usuario_id)
        antigo = next((r for r in registros if r.id == registro_id), None)
        if antigo is None:
            raise ErroPainel("Esse registro não existe mais (talvez já tenha sido corrigido).")
        if not texto or texto == antigo.conteudo:
            return "Nada mudou."
        mensagem_id = self._mensagem_web(usuario_id, f"Correção pela web: “{antigo.conteudo}” → “{texto}”")
        ext = extracao.Extracao("1", registros=[extracao.Registro(
            "R1-1", "1", texto, antigo.natureza, antigo.dito_por, antigo.data_fato, list(antigo.entidades),
            1.0, antigo.sensivel)])
        mapa = self.repo.salvar_extracao(usuario_id, mensagem_id, ext)
        self.repo.corrigir_registro(usuario_id, antigo.id, mapa["R1-1"], mensagem_id, manter_pendencias=True)
        return "Registro corrigido."

    def mudar_pendencia(self, usuario_id: str, pendencia_id: str, acao: str) -> str:
        situacao = {"feita": "concluida", "descartar": "descartada"}.get(acao)
        _, pendencias = self.repo.memoria(usuario_id)
        p = next((x for x in pendencias if x.id == pendencia_id), None)
        if situacao is None or p is None:
            raise ErroPainel("Essa pendência não está mais aberta.")
        rotulo = "Marquei como feita" if situacao == "concluida" else "Descartei"
        mensagem_id = self._mensagem_web(usuario_id, f"Pela web: {rotulo.lower()} “{p.descricao}”")
        self.repo.ajustar_pendencia(usuario_id, p.id, mensagem_id, situacao=situacao)
        if situacao == "concluida":
            atual = self.repo.pendencia(usuario_id, p.id)
            if atual and atual.get("recorrencia"):
                prazo, lembrete = recorrencia.proxima_ocorrencia(atual["recorrencia"], atual["prazo"],
                                                                 atual["lembrete_em"], _agora().date())
                self.repo.criar_seguinte(usuario_id, p.id, prazo, lembrete)
        return f"{rotulo}: {p.descricao}."

    def exportar(self, usuario_id: str) -> bytes:
        dados = {"exportado_em": _agora().isoformat(timespec="seconds"), "formato": "Peraí 1", **self.repo.exportar(usuario_id)}
        return json.dumps(dados, ensure_ascii=False, indent=1, default=str).encode()

    # -- páginas ---------------------------------------------------------------------

    def pagina_entrar(self, aviso: str | None = None) -> str:
        return _pagina("Peraí — Entrar", f"""
<main class="estreito"><h1>Peraí</h1>
{f'<p class="aviso">{_e(aviso)}</p>' if aviso else ''}
<p>Sua memória, também pelo navegador: consultar, corrigir e exportar o que você já guardou.</p>
<p><a class="botao" href="/web/entrar">Entrar com Google</a></p>
<p class="dica">Primeira vez? Mande <b>/web</b> no Telegram e abra o link que chegar: ele liga a sua conta
Google ao Peraí. Depois disso é só entrar por aqui.</p>
<p class="dica"><a href="/privacidade">Privacidade</a></p></main>""")

    # -- cálculos do Peraí (saúde, resultado, riscos), sem IA ---------------------------------

    @property
    def calc(self):
        """O serviço do Peraí só para os cálculos do código (saúde, projetos, riscos). Usa o modelo
        "simulado": nada aqui chama IA nem sai do servidor."""
        if getattr(self, "_calc", None) is None:
            import tempfile
            from pathlib import Path
            from perai.config import MODELOS, TRANSCRITORES
            from perai.llm import Cliente
            from perai.servico import Perai
            self._calc = Perai(self.repo, Cliente(), MODELOS["simulado"], TRANSCRITORES["simulado"],
                               MODELOS["simulado"], Path(tempfile.mkdtemp(prefix="perai-painel-")))
        return self._calc

    def _lista_compras(self, usuario_id: str):
        from perai.servico import itens_da_lista
        _, pendencias = self.repo.memoria(usuario_id)
        listas = self.calc._listas_de_compras(pendencias)
        return (listas[0] if listas else None), (itens_da_lista(listas[0].descricao)[1] if listas else [])

    def mudar_compras(self, usuario_id: str, dados: dict) -> str:
        """Tira os itens marcados e inclui os digitados, do mesmo jeito que pelo Telegram (D-087)."""
        from perai.precos import itens_unicos
        from perai.servico import mudar_lista
        lista, itens = self._lista_compras(usuario_id)
        tirar = [v for k, v in dados.items() if k.startswith("tirar_") and v in itens]
        incluir = [i.strip()[:80] for i in __import__("re").split(r"[,;\n]+", dados.get("incluir", "")) if i.strip()][:30]
        if not tirar and not incluir:
            return "Nada mudou."
        if lista is None:
            if not incluir:
                return "Nada mudou."
            novos = itens_unicos(incluir)
            texto = "Comprar " + (novos[0] if len(novos) == 1 else ", ".join(novos[:-1]) + " e " + novos[-1])
            mid = self._mensagem_web(usuario_id, f"Pela web: lista de compras — {texto}")
            self.repo.salvar_extracao(usuario_id, mid, extracao.Extracao("1", pendencias=[extracao.Pendencia(
                "P1-1", "1", texto, "eu", None, None, None)]))
            return f"Lista criada com {len(novos)} item(ns)."
        nova = mudar_lista(lista.descricao, incluir, tirar)
        restantes = len(itens_da_lista_seguro(nova))
        partes = ([f"comprado: {', '.join(tirar)}"] if tirar else []) + ([f"incluído: {', '.join(incluir)}"] if incluir else [])
        mid = self._mensagem_web(usuario_id, "Pela web, lista de compras — " + "; ".join(partes))
        if nova == lista.descricao or not restantes or (tirar and set(tirar) >= set(itens) and not incluir):
            # Tudo comprado: a lista fica concluída (o histórico guarda o que havia).
            self.repo.ajustar_pendencia(usuario_id, lista.id, mid, situacao="concluida")
            return "Lista concluída: tudo comprado. 🎉"
        self.repo.ajustar_pendencia(usuario_id, lista.id, mid, descricao=nova)
        return "Lista atualizada: " + "; ".join(partes) + "."

    # -- páginas do painel ---------------------------------------------------------------

    ABAS = [("inicio", "🏠 Início"), ("pendencias", "✅ Pendências"), ("compras", "🛒 Compras"),
            ("contas", "🏢 Contas"), ("negocios", "📈 Negócios"), ("registros", "📝 Registros"),
            ("despesas", "🧾 Despesas"), ("dados", "⬇️ Dados")]

    def pagina_painel(self, usuario_id: str, csrf: str, busca: str = "", aviso: str | None = None,
                      aba: str = "") -> str:
        nome = (self.repo.nome_usuario(usuario_id) or "").split(" ")[0]
        c = _e(csrf)
        termo = _normal(busca.strip())
        aba = aba if aba in dict(self.ABAS) else ("busca" if termo else "inicio")
        if termo:
            aba = "busca"
        self._aba_atual = aba if aba != "busca" else "inicio"
        conteudo = {"inicio": self._aba_inicio, "pendencias": self._aba_pendencias, "compras": self._aba_compras,
                    "contas": self._aba_contas, "negocios": self._aba_negocios, "registros": self._aba_registros,
                    "despesas": self._aba_despesas, "dados": self._aba_dados,
                    "busca": self._aba_busca}[aba](usuario_id, c, termo)
        nav = "".join(f'<a href="/web?aba={k}"{" aria-current=page" if k == aba else ""}>{_e(r)}</a>'
                      for k, r in self.ABAS)
        return _pagina("Peraí", f"""
<header><h1>Peraí</h1><span>{_e(nome)}</span>
<form method="post" action="/web/sair"><input type="hidden" name="csrf" value="{c}"><button class="sec">Sair</button></form></header>
<nav class="abas" aria-label="Seções">{nav}</nav>
<main>
{f'<p class="aviso">{_e(aviso)}</p>' if aviso else ''}
<form class="busca" method="get" action="/web"><input type="search" name="q" value="{_e(busca)}"
placeholder="Buscar em tudo: Júlio, Gazin, proposta…" aria-label="Buscar"><button>Buscar</button></form>
{conteudo}
</main>""")

    # Cada aba devolve o HTML do seu conteúdo.

    def _li_pendencia(self, p, c: str) -> str:
        quando = _data_br(p.prazo) if p.prazo else (p.prazo_como_dito or "sem prazo")
        quem = "você" if p.responsavel == "eu" else f"aguardando {p.quem or 'alguém'}"
        return f"""<li><div><b>{_e(p.descricao)}</b><span class="meta">{_e(quem)} · {_e(quando)}
{f' · ⏰ {_e(_data_br(p.lembrete_em[:10]) + p.lembrete_em[10:].replace("T", " às "))}' if p.lembrete_em else ''}</span></div>
<form method="post" action="/web/pendencia"><input type="hidden" name="csrf" value="{c}">
<input type="hidden" name="id" value="{_e(p.id)}"><input type="hidden" name="aba" value="{_e(getattr(self, '_aba_atual', 'inicio'))}">
<button name="acao" value="feita">✔️ Feita</button><button name="acao" value="descartar" class="sec">Descartar</button>
</form></li>"""

    def _li_registro(self, r, c: str) -> str:
        quem = "você" if r.dito_por == "eu" else r.dito_por
        return f"""<li><details><summary><span class="tag">{_e(NATUREZA.get(r.natureza, r.natureza))}</span>
{_e(r.conteudo)}<span class="meta">{_e(quem)}{f' · {_e(r.data_fato)}' if r.data_fato else ''}
{' · confiança baixa' if r.confianca < 0.6 else ''}</span></summary>
<form method="post" action="/web/registro"><input type="hidden" name="csrf" value="{c}">
<input type="hidden" name="id" value="{_e(r.id)}"><label>Corrigir para
<textarea name="texto" rows="3">{_e(r.conteudo)}</textarea></label>
<button>Salvar correção</button><span class="dica">O original continua guardado no histórico.</span></form>
</details></li>"""

    @staticmethod
    def _li_despesa(d) -> str:
        return f"""<li><div><b>{_e(despesas.brl(d['valor_centavos']))}</b> · {_e(d['descricao'])}
<span class="meta">{_e(d['data'] or 'sem data')} · {_e(despesas.ROTULO_CATEGORIA.get(d['categoria'], d['categoria']))}
{f" · {_e(d['contexto'])}" if d.get('contexto') else ''}</span></div></li>"""

    @staticmethod
    def _li_oportunidade(o, placar=None) -> str:
        from perai import contas as contas_mod
        return f"""<li><div><span><b>{_e(o.get('conta') or 'sem conta')}</b> · {_e(o['titulo'])}</span>
<span class="meta">{_e(contas_mod.ROTULO_ETAPA[o['etapa']])} · {_e(contas_mod._valor(o))}
{f" · previsão {_e(o['previsao'])}" if o.get('previsao') else ''}{f" · MEDDPICC {placar.total}/16" if placar else ''}</span></div></li>"""

    def _aba_inicio(self, usuario_id: str, c: str, termo: str) -> str:
        from perai import crm
        registros, pendencias = self.repo.memoria(usuario_id)
        hoje = _agora().date().isoformat()
        atrasadas = [p for p in pendencias if p.prazo and p.prazo < hoje]
        de_hoje = [p for p in pendencias if p.prazo == hoje]
        _, itens = self._lista_compras(usuario_id)
        try:
            saudes, _ = self.calc._saudes(usuario_id)
        except Exception:
            saudes = []
        cores = {k: sum(1 for x in saudes if x.cor == k) for k in ("vermelho", "amarelo", "verde")}
        fats = self.repo.faturas(usuario_id)
        vencidas = [f for f in fats if f.get("vencimento") and f["vencimento"] < hoje and
                    f["valor_centavos"] - sum(r["valor_centavos"] for r in f.get("recebimentos") or []) > 0]
        abertas = [o for o in self.repo.oportunidades(usuario_id) if o["etapa"] not in ("ganha", "perdida")]
        soma = sum(o.get("valor_centavos") or 0 for o in abertas)
        cartoes = [
            ("pendencias", "Pendências", f"{len(pendencias)}", f"{len(atrasadas)} atrasada(s) · {len(de_hoje)} para hoje"),
            ("compras", "Lista de compras", f"{len(itens)}", "itens para comprar" if itens else "sem lista aberta"),
            ("contas", "Saúde das contas", f"🔴{cores['vermelho']} 🟡{cores['amarelo']} 🟢{cores['verde']}",
             f"{len(saudes)} conta(s) acompanhadas"),
            ("negocios", "Em negociação", despesas.brl(soma) if soma else f"{len(abertas)}",
             f"{len(abertas)} oportunidade(s) aberta(s)" + (f" · {len(vencidas)} nota(s) vencida(s)" if vencidas else "")),
        ]
        grade = "".join(f'<a class="cartao" href="/web?aba={k}"><span class="meta">{_e(t)}</span>'
                        f'<b{" class=cores" if k == "contas" else ""}>{_e(v)}</b><span class="meta">{_e(d)}</span></a>'
                        for k, t, v, d in cartoes)
        alertas = crm.alertas(saudes, 5)
        ativs = sorted((a for a in self.repo.crm(usuario_id, "atividades")
                        if a["situacao"] == "agendada" and (a.get("data") or "") >= hoje), key=lambda a: a["data"])[:5]
        nomes = {x["id"]: x["nome"] for x in self.repo.contas_relacionamento(usuario_id)}
        urgentes = (atrasadas + de_hoje)[:6]
        recentes = list(reversed(registros))[:8]
        vazio = '<p class="dica">Nada por aqui.</p>'
        return f"""<div class="cartoes">{grade}</div>
<section><h2>⚠️ Para hoje e atrasadas <span class="conta">{len(atrasadas) + len(de_hoje)}</span></h2>
<ul>{''.join(self._li_pendencia(p, c) for p in urgentes) or vazio}</ul>
{'<p><a href="/web?aba=pendencias">Ver todas as pendências</a></p>' if pendencias else ''}</section>
<section><h2>🩺 Contas que pedem atenção</h2>
<ul>{''.join(f'<li><div>{_e(a)}</div></li>' for a in alertas) or '<li><div class="dica">Nenhum alerta. Conte as calls e visitas para o Peraí acompanhar as contas.</div></li>'}</ul></section>
<section><h2>🗓️ Próximas atividades com clientes</h2>
<ul>{''.join(f'<li><div><b>{_e(crm._dia(a["data"]))} · {_e(crm.TIPOS.get(a["tipo"], a["tipo"]))} · {_e(nomes.get(a["conta_id"], "?"))}</b><span class="meta">{_e(a.get("resumo") or "")}</span></div></li>' for a in ativs) or vazio}</ul></section>
<section><h2>📝 Últimos registros</h2>
<ul>{''.join(self._li_registro(r, c) for r in recentes) or vazio}</ul>
{'<p><a href="/web?aba=registros">Ver todos os registros</a></p>' if registros else ''}</section>"""

    def _aba_pendencias(self, usuario_id: str, c: str, termo: str) -> str:
        _, pendencias = self.repo.memoria(usuario_id)
        grupos = {"Atrasadas": [], "Próximos 7 dias": [], "Mais adiante": [], "Sem prazo": [], "Aguardando outras pessoas": []}
        hoje = _agora().date()
        semana = (hoje + timedelta(days=7)).isoformat()
        for p in pendencias:
            if p.responsavel == "aguardando":
                grupos["Aguardando outras pessoas"].append(p)
            elif not p.prazo:
                grupos["Sem prazo"].append(p)
            elif p.prazo < hoje.isoformat():
                grupos["Atrasadas"].append(p)
            elif p.prazo <= semana:
                grupos["Próximos 7 dias"].append(p)
            else:
                grupos["Mais adiante"].append(p)
        partes = [f'<h2>✅ Pendências abertas <span class="conta">{len(pendencias)}</span></h2>']
        for g, ps in grupos.items():
            if ps:
                ps.sort(key=lambda p: p.prazo or "9999")
                partes.append(f'<h3>{_e(g)} <span class="conta">{len(ps)}</span></h3><ul>'
                              + "".join(self._li_pendencia(p, c) for p in ps) + "</ul>")
        return "<section>" + "".join(partes) + ("" if pendencias else '<p class="dica">Nada por aqui.</p>') + "</section>"

    def _aba_compras(self, usuario_id: str, c: str, termo: str) -> str:
        from perai import precos
        lista, itens = self._lista_compras(usuario_id)
        itens = precos.itens_unicos(itens)
        grupos: dict[str, list[str]] = {}
        for i in itens:
            grupos.setdefault(precos.departamento(i), []).append(i)
        ordem = [n for n, _ in precos.DEPARTAMENTOS] + ["📦 Outros"]
        caixas, n = [], 0
        for d in ordem:
            if d not in grupos:
                continue
            caixas.append(f"<fieldset><legend>{_e(d)}</legend>")
            for i in grupos[d]:
                n += 1
                caixas.append(f'<label class="caixa"><input type="checkbox" name="tirar_{n}" value="{_e(i)}">'
                              f' {_e(i[:1].upper() + i[1:])}</label>')
            caixas.append("</fieldset>")
        if itens:
            corpo = f"""<form method="post" action="/web/compras" class="compras"><input type="hidden" name="csrf" value="{c}">
{''.join(caixas)}
<button>✔️ Tirar os marcados (comprados)</button></form>"""
        else:
            corpo = '<p class="dica">Nenhuma lista aberta. Inclua itens abaixo ou mande a lista no Telegram.</p>'
        incluir = f"""<form method="post" action="/web/compras" class="incluir"><input type="hidden" name="csrf" value="{c}">
<label>Incluir na lista<input type="text" name="incluir" placeholder="pão, frutas, detergente" maxlength="600"></label>
<button>Incluir</button></form>"""
        # Ofertas vigentes registradas, por loja: só os números guardados, sem IA.
        hoje = _agora().date().isoformat()
        ofertas = [p for p in self.repo.precos(usuario_id, hoje) if p["origem"] == "oferta" and (p.get("data") or "") >= hoje]
        ofertas = precos.sem_repetidos(ofertas)
        lojas: dict[str, list[dict]] = {}
        for p in ofertas:
            lojas.setdefault(p.get("loja") or "loja não informada", []).append(p)
        blocos = []
        for loja, ps in sorted(lojas.items(), key=lambda x: -len(x[1])):
            linhas = "".join(_li_oferta(p) for p in sorted(ps, key=lambda p: p["produto"].lower()))
            blocos.append(f'<details><summary>{_e(loja)} <span class="conta">{len(ps)} oferta(s)</span></summary><ul>{linhas}</ul></details>')
        ofertas_html = ("".join(blocos) if blocos else '<p class="dica">Nenhuma oferta vigente. Mande o panfleto do mercado no Telegram.</p>')
        return f"""<section><h2>🛒 Lista de compras <span class="conta">{len(itens)}</span></h2>
<p class="dica">Marque o que já comprou e toque em “Tirar os marcados”. Fica registrado como mensagem sua, igual ao Telegram.</p>
{corpo}{incluir}</section>
<section><h2>🏷️ Ofertas vigentes registradas <span class="conta">{len(ofertas)}</span></h2>
<p class="dica">Para saber onde cada item da lista sai mais barato, pergunte no Telegram: “em qual mercado é melhor eu ir?”.</p>
{ofertas_html}</section>"""

    def _aba_contas(self, usuario_id: str, c: str, termo: str) -> str:
        from perai import contas as contas_mod, crm
        try:
            saudes, fora = self.calc._saudes(usuario_id)
        except Exception:
            saudes, fora = [], 0
        ativs = self.repo.crm(usuario_id, "atividades")
        mat = self.repo.crm(usuario_id, "maturidade_cliente")
        segs = self.calc._segmentos(usuario_id)
        custos = {k.conta_id: k for k in contas_mod.custos_do_usuario(self.repo, usuario_id)}
        planos = self.calc._planos(usuario_id)
        itens = []
        for s in saudes:
            a_c = sorted((a for a in ativs if a["conta_id"] == s.conta_id and a["situacao"] == "feita"),
                         key=lambda a: a.get("data") or "", reverse=True)[:3]
            m_c = crm.maturidade_conta([m for m in mat if m["conta_id"] == s.conta_id])
            k = custos.get(s.conta_id)
            ordenados = sorted(s.sinais, key=lambda x: -crm.ORDEM[x.nivel])
            itens.append(f"""<li><details><summary><b>{crm.COR[s.cor]} {_e(s.nome)}</b>
<span class="meta">{_e(crm.FASES.get(s.fase, s.fase or ''))}{f' · último contato há {s.dias_sem_contato} dia(s)' if s.dias_sem_contato is not None else ''}
{f' · {len(s.sinais)} alerta(s)' if s.sinais else ' · em dia'}{f' · {_e(segs[s.conta_id])}' if s.conta_id in segs else ''}
{f' · custo {_e(despesas.brl(k.total_centavos))}' if k and k.total_centavos else ''}</span></summary>
<ul class="dentro">{''.join(f'<li>{crm.COR[x.nivel]} {_e(x.texto)} → <b>{_e(x.acao)}</b></li>' for x in ordenados)}</ul>
{('<h4>Últimas atividades</h4><ul class="dentro">' + ''.join(_li_atividade(a) for a in a_c) + '</ul>') if a_c else ''}
{_plano_html(planos.get(s.conta_id))}
{('<h4>Maturidade de TI</h4><p class="meta">' + ' · '.join(f'{_e(crm.DOMINIOS[d])}: {v["nivel"]}' for d, v in m_c.items()) + '</p>') if m_c else ''}
</details></li>""")
        sem_crm = [k for cid, k in custos.items() if cid not in {s.conta_id for s in saudes}]
        linhas_k = [f"""<li><div><b>{_e(k.nome)}</b> · {_e(despesas.brl(k.total_centavos))}
<span class="meta">{len(k.despesas)} despesa(s){f' · {sum(1 for x in k.despesas if x["rateada_com"])} rateada(s)' if any(x["rateada_com"] for x in k.despesas) else ''}{f' · {k.sem_valor} sem valor' if k.sem_valor else ''}</span></div></li>"""
                    for k in sorted(sem_crm, key=lambda k: -k.total_centavos)]
        try:
            riscos = self.calc.listar_riscos(usuario_id)
        except Exception:
            riscos = ""
        vazio = '<p class="dica">Nenhuma conta com atividades ou negócios ainda.</p>'
        return f"""<section><h2>🩺 Saúde das contas <span class="conta">{len(saudes)}</span></h2>
<p class="dica">Calculada pelo Peraí: dias sem contato pela cadência da fase, decisores, propostas e notas, entregas de parceiros e riscos do segmento. Toque numa conta para ver o que fazer.</p>
<ul>{''.join(itens) or vazio}</ul></section>
{f'<section><h2>💰 {"Custo de outras contas" if saudes else "Custo por conta"} <span class="conta">{len(linhas_k)}</span></h2><ul>{"".join(linhas_k)}</ul></section>' if linhas_k else ''}
<section><h2>🧭 Radar de risco</h2><div class="texto">{_e(riscos)}</div></section>"""

    def _aba_negocios(self, usuario_id: str, c: str, termo: str) -> str:
        from perai import contas as contas_mod, crm
        ops = self.repo.oportunidades(usuario_id)
        ev = self.repo.crm(usuario_id, "meddpicc")
        partes = []
        for etapa in ("prospeccao", "proposta", "negociacao", "ganha", "perdida"):
            os_ = [o for o in ops if o["etapa"] == etapa]
            if not os_:
                continue
            soma = sum(o.get("valor_centavos") or 0 for o in os_)
            partes.append(f'<h3>{_e(contas_mod.ROTULO_ETAPA[etapa])} <span class="conta">{len(os_)}'
                          f'{f" · {despesas.brl(soma)}" if soma else ""}</span></h3><ul>'
                          + "".join(self._li_oportunidade(o, crm.placar(o, [e for e in ev if e["oportunidade_id"] == o["id"]])
                                                          if etapa not in ("ganha", "perdida") else None) for o in os_)
                          + "</ul>")
        blocos = []
        for titulo, f in (("📦 Projetos e resultado", self.calc.listar_projetos), ("🧾 Faturas", self.calc.listar_faturas),
                          ("🤝 Contratos e parcerias", self.calc.listar_contratos), ("💼 Comissões", self.calc.listar_comissoes)):
            try:
                t = f(usuario_id)
            except Exception:
                t = ""
            if t and not t.lstrip("🧾🤝📦💼 ").startswith(("Nenhum", "Nenhuma")):
                blocos.append(f'<section><h2>{titulo}</h2><div class="texto">{_e(t)}</div></section>')
        return (f'<section><h2>📈 Oportunidades <span class="conta">{len(ops)}</span></h2>'
                '<p class="dica">Valores como você disse; o MEDDPICC vai de 0 a 16 e só conta o que foi registrado.</p>'
                + ("".join(partes) or '<p class="dica">Nada por aqui.</p>') + "</section>" + "".join(blocos))

    def _aba_registros(self, usuario_id: str, c: str, termo: str) -> str:
        registros, _ = self.repo.memoria(usuario_id)
        registros = list(reversed(registros))[:200]
        return (f'<section><h2>📝 Registros <span class="conta">{len(registros)}</span></h2>'
                '<p class="dica">Toque num registro para corrigir.</p><ul>'
                + ("".join(self._li_registro(r, c) for r in registros) or '<p class="dica">Nada por aqui.</p>')
                + "</ul></section>")

    def _aba_despesas(self, usuario_id: str, c: str, termo: str) -> str:
        gastos = self.repo.despesas(usuario_id, _agora().date() - timedelta(days=60))
        total = sum(d["valor_centavos"] or 0 for d in gastos)
        return (f'<section><h2>🧾 Despesas dos últimos 60 dias <span class="conta">{len(gastos)} · '
                f'{_e(despesas.brl(total))}</span></h2><ul>'
                + ("".join(self._li_despesa(d) for d in gastos[:120]) or '<p class="dica">Nada por aqui.</p>')
                + "</ul></section>")

    def _aba_dados(self, usuario_id: str, c: str, termo: str) -> str:
        return """<section><h2>⬇️ Seus dados</h2><p>Tudo o que o Peraí guardou de você, num arquivo JSON (formato aberto).</p>
<p><a class="botao" href="/web/exportar">Exportar tudo</a></p>
<p class="dica"><a href="/web/teste">Resultado do teste das perguntas (semana 4)</a> · <a href="/privacidade">Privacidade</a></p></section>"""

    def _aba_busca(self, usuario_id: str, c: str, termo: str) -> str:
        registros, pendencias = self.repo.memoria(usuario_id)
        registros = [r for r in reversed(registros) if termo in _normal(f"{r.conteudo} {r.dito_por} {' '.join(r.entidades)}")][:100]
        pendencias = [p for p in pendencias if termo in _normal(f"{p.descricao} {p.quem or ''}")]
        ops = [o for o in self.repo.oportunidades(usuario_id) if termo in _normal(f"{o['titulo']} {o.get('conta') or ''}")]
        gastos = [d for d in self.repo.despesas(usuario_id, _agora().date() - timedelta(days=365))
                  if termo in _normal(f"{d['descricao']} {d.get('estabelecimento') or ''} {d.get('contexto') or ''}")][:60]
        sec = lambda t, n, corpo: (f'<section><h2>{t} <span class="conta">{n}</span></h2><ul>{corpo}</ul></section>' if n else "")
        achou = sec("✅ Pendências", len(pendencias), "".join(self._li_pendencia(p, c) for p in pendencias)) \
            + sec("📝 Registros", len(registros), "".join(self._li_registro(r, c) for r in registros)) \
            + sec("📈 Oportunidades", len(ops), "".join(self._li_oportunidade(o) for o in ops)) \
            + sec("🧾 Despesas (12 meses)", len(gastos), "".join(self._li_despesa(d) for d in gastos))
        return achou or '<p class="dica">Nada encontrado.</p>'


def _plano_html(pl: dict | None) -> str:
    from perai import crm
    if not crm.tem_plano(pl):
        return ""
    partes = [f"• {_e(x)}" for x in (pl.get("objetivos") or [])]
    meta = f'<p class="meta">Meta {_e(despesas.brl(pl["meta_centavos"]))}</p>' if pl.get("meta_centavos") else ""
    rev = f'<p class="meta">Revisão {_e(_data_br(pl["revisao"]))}</p>' if pl.get("revisao") else ""
    return '<h4>📋 Plano de conta</h4><p>' + "<br>".join(partes) + "</p>" + meta + rev


def _data_br(iso: str | None) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso and len(iso) >= 10 else (iso or "")


def _li_oferta(p: dict) -> str:
    ref = ""
    if p.get("ref_unidade") and p.get("ref_centavos") and (p["ref_unidade"] != p["por"] or p["ref_centavos"] != p["preco_centavos"]):
        ref = f" ({despesas.brl(p['ref_centavos'])}/{p['ref_unidade']})"
    ate = f" · até {p['data'][8:10]}/{p['data'][5:7]}" if p.get("data") else ""
    return (f'<li><div><b>{_e(p["produto"][:1].upper() + p["produto"][1:])}{_e(" " + p["marca"]) if p.get("marca") else ""}</b>'
            f'<span class="meta">{_e(despesas.brl(p["preco_centavos"]))}/{_e(p["por"])}{_e(ref)}{_e(ate)}</span></div></li>')


def _li_atividade(a: dict) -> str:
    from perai import crm
    resumo = f" · {a['resumo']}" if a.get("resumo") else ""
    return f"<li>{_e(crm._dia(a.get('data')))} · {_e(crm.TIPOS.get(a['tipo'], a['tipo']))}{_e(resumo)}</li>"


def itens_da_lista_seguro(descricao: str) -> list[str]:
    from perai.servico import itens_da_lista
    return itens_da_lista(descricao or "")[1]


def _celula(texto: str) -> str:
    """Texto para uma célula da planilha: sem tabulação nem quebra de linha."""
    return " / ".join(p.strip() for p in str(texto or "").replace("\t", " ").splitlines() if p.strip())


def pagina_teste(resultado: list[dict]) -> str:
    """Última rodada do teste da semana 4: conferir e copiar para a aba "Semana 4" (colunas F e G)."""
    if not resultado:
        corpo = ('<p>Nenhuma rodada ainda. No Telegram, mande <b>/teste</b> e, nas linhas de baixo, '
                 'uma pergunta por linha.</p>')
    else:
        tsv = "\n".join(f"{_celula(r['resposta'])}\t{_celula(' | '.join(r['fontes']) or 'sem fonte')}"
                        for r in resultado)
        linhas = "".join(
            f"<li><div><b>{n}. {_e(r['pergunta'])}</b><span>{_e(r['resposta'])}</span>"
            + "".join(f'<span class="meta">📎 {_e(f)}</span>' for f in r["fontes"])
            + ("" if r["fontes"] else '<span class="meta">sem fonte</span>') + "</div></li>"
            for n, r in enumerate(resultado, start=1))
        corpo = (f"<p>{len(resultado)} respostas da última rodada. Para a planilha: copie o bloco abaixo e cole na "
                 f"aba <b>Semana 4</b>, na célula <b>F8</b> (resposta e fontes, uma pergunta por linha).</p>"
                 f'<textarea rows="8" readonly aria-label="Respostas para colar">{_e(tsv)}</textarea>'
                 f"<h2>Conferir</h2><ul>{linhas}</ul>")
    return _pagina("Peraí — Teste da semana 4",
                   f'<header><h1>Teste da semana 4</h1><a href="/web">Voltar</a></header><main>{corpo}</main>')


ESTILO = """:root{--f:#f7f7f3;--p:#fff;--t:#1d2320;--t2:#5b6661;--l:#dfe3df;--a:#0f6b5f;--s:#e3f0ec;--av:#fff4dc;--bt:#fff}
@media (prefers-color-scheme:dark){:root{--f:#131715;--p:#1b211e;--t:#e6ece9;--t2:#9fb0a9;--l:#2d3733;--a:#5cc4b2;--s:#1d3330;--av:#3a2f17;--bt:#0b1f1c}}
*{box-sizing:border-box}body{margin:0;background:var(--f);color:var(--t);font:16px/1.5 system-ui,sans-serif}
header{display:flex;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--l);background:var(--p)}
header h1{font-size:20px;margin:0;flex:1}header form{margin:0}
main{max-width:46rem;margin:0 auto;padding:16px}main.estreito{max-width:30rem;margin-top:12vh}
h2{font-size:18px;margin:28px 0 8px}.conta{color:var(--t2);font-weight:400;font-size:14px}
ul{list-style:none;margin:0;padding:0}li{background:var(--p);border:1px solid var(--l);border-radius:8px;padding:10px 12px;margin:8px 0}
li>div{display:grid;gap:2px}.meta{display:block;color:var(--t2);font-size:14px}.tag{font-size:13px;color:var(--t2);margin-right:6px}
summary{cursor:pointer}details form{display:grid;gap:8px;margin-top:10px}
textarea,input[type=search]{width:100%;font:inherit;padding:8px;border:1px solid var(--l);border-radius:6px;background:var(--f);color:var(--t)}
button,.botao{font:inherit;border:0;border-radius:6px;padding:8px 14px;background:var(--a);color:var(--bt);cursor:pointer;text-decoration:none;display:inline-block}
button.sec{background:var(--s);color:var(--t)}li form{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
.busca{display:flex;gap:8px}.dica{color:var(--t2);font-size:14px}.aviso{background:var(--av);padding:10px 12px;border-radius:8px}
a{color:var(--a)}
.abas{display:flex;gap:6px;overflow-x:auto;padding:8px 16px;background:var(--p);border-bottom:1px solid var(--l);
scrollbar-width:none}.abas a{white-space:nowrap;text-decoration:none;color:var(--t);padding:6px 12px;border-radius:999px;
font-size:15px}.abas a[aria-current=page]{background:var(--s);color:var(--a);font-weight:600}
.cartoes{display:grid;grid-template-columns:repeat(auto-fit,minmax(10rem,1fr));gap:10px;margin:16px 0}
.cartao{display:grid;gap:2px;background:var(--p);border:1px solid var(--l);border-radius:8px;padding:12px;
text-decoration:none;color:var(--t)}.cartao b{font-size:20px}
h3{font-size:16px;margin:18px 0 4px}h4{font-size:14px;margin:12px 0 4px;color:var(--t2)}
ul.dentro{margin:8px 0}ul.dentro li{border:0;background:var(--f);margin:4px 0;padding:6px 10px;font-size:15px}
.texto{white-space:pre-wrap;background:var(--p);border:1px solid var(--l);border-radius:8px;padding:12px;font-size:15px;
overflow-wrap:anywhere}
fieldset{border:1px solid var(--l);border-radius:8px;background:var(--p);margin:10px 0;padding:6px 12px 10px}
legend{font-weight:600;padding:0 4px}.caixa{display:flex;gap:10px;align-items:center;padding:6px 0;font-size:16px;cursor:pointer}
.caixa input{width:22px;height:22px;accent-color:var(--a)}.caixa:has(input:checked){text-decoration:line-through;color:var(--t2)}
.incluir{display:flex;gap:8px;align-items:end;margin-top:12px;flex-wrap:wrap}.incluir label{flex:1;display:grid;gap:4px;min-width:12rem}
input[type=text]{width:100%;font:inherit;padding:8px;border:1px solid var(--l);border-radius:6px;background:var(--f);color:var(--t)}
summary{list-style:none;cursor:pointer}summary::-webkit-details-marker{display:none}
summary::before{content:"▸";color:var(--t2);display:inline-block;width:1.1em;transition:transform .15s}
details[open]>summary::before{transform:rotate(90deg)}
section>details{background:var(--p);border:1px solid var(--l);border-radius:8px;padding:10px 12px;margin:8px 0}
.cartao b.cores{font-size:17px;letter-spacing:.02em}.cartao b{font-size:19px;overflow-wrap:anywhere}"""


def _pagina(titulo: str, corpo: str) -> str:
    return (f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{_e(titulo)}</title>'
            f'<link rel="stylesheet" href="/web/estilo.css"></head><body>{corpo}</body></html>')


VALIDADE_INIT_DATA = 3600   # segundos: a assinatura do Telegram vale 1 hora para entrar


def validar_init_data(init_data: str, token_bot: str, agora_ts: float) -> dict:
    """Confere a assinatura que o Telegram põe no painel aberto pelo bot (Mini App) e devolve o
    usuário do Telegram. Regra do Telegram: chave = HMAC-SHA256("WebAppData", token do bot);
    assinatura = HMAC-SHA256(chave, campos em ordem alfabética, "chave=valor" por linha, sem "hash")."""
    import json as _json
    import urllib.parse as _up
    if not token_bot or not init_data:
        raise ErroPainel("Abra o painel pelo botão do Peraí no Telegram.")
    campos = dict(_up.parse_qsl(init_data, keep_blank_values=True))
    recebido = campos.pop("hash", "")
    texto = "\n".join(f"{k}={v}" for k, v in sorted(campos.items()))
    chave = hmac.new(b"WebAppData", token_bot.encode(), hashlib.sha256).digest()
    esperado = hmac.new(chave, texto.encode(), hashlib.sha256).hexdigest()
    if not recebido or not hmac.compare_digest(esperado, recebido):
        raise ErroPainel("Não consegui confirmar que o painel veio do Telegram. Abra de novo pelo botão.")
    try:
        quando = int(campos.get("auth_date", "0"))
        usuario = _json.loads(campos.get("user", "{}"))
    except ValueError:
        raise ErroPainel("Dados do Telegram inválidos. Abra de novo pelo botão.") from None
    if agora_ts - quando > VALIDADE_INIT_DATA or not usuario.get("id"):
        raise ErroPainel("O botão do painel expirou. Mande /painel de novo.")
    return usuario
