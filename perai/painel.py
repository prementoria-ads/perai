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

import hmac
import html
import json
import secrets
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

    def pagina_painel(self, usuario_id: str, csrf: str, busca: str = "", aviso: str | None = None) -> str:
        registros, pendencias = self.repo.memoria(usuario_id)
        nome = (self.repo.nome_usuario(usuario_id) or "").split(" ")[0]
        termo = _normal(busca.strip())
        if termo:
            registros = [r for r in registros if termo in _normal(f"{r.conteudo} {r.dito_por} {' '.join(r.entidades)}")]
            pendencias = [p for p in pendencias if termo in _normal(f"{p.descricao} {p.quem or ''}")]
        registros = list(reversed(registros))[:200]
        gastos = self.repo.despesas(usuario_id, _agora().date() - timedelta(days=60))
        if termo:
            gastos = [d for d in gastos if termo in _normal(f"{d['descricao']} {d.get('estabelecimento') or ''}")]
        c = _e(csrf)

        linhas_p = []
        for p in pendencias:
            quando = p.prazo or p.prazo_como_dito or "sem prazo"
            quem = "você" if p.responsavel == "eu" else f"aguardando {p.quem or 'alguém'}"
            linhas_p.append(f"""<li><div><b>{_e(p.descricao)}</b><span class="meta">{_e(quem)} · {_e(quando)}
{f' · ⏰ {_e(p.lembrete_em.replace("T", " "))}' if p.lembrete_em else ''}</span></div>
<form method="post" action="/web/pendencia"><input type="hidden" name="csrf" value="{c}">
<input type="hidden" name="id" value="{_e(p.id)}">
<button name="acao" value="feita">✔️ Feita</button><button name="acao" value="descartar" class="sec">Descartar</button>
</form></li>""")
        linhas_r = []
        for r in registros:
            quem = "você" if r.dito_por == "eu" else r.dito_por
            linhas_r.append(f"""<li><details><summary><span class="tag">{_e(NATUREZA.get(r.natureza, r.natureza))}</span>
{_e(r.conteudo)}<span class="meta">{_e(quem)}{f' · {_e(r.data_fato)}' if r.data_fato else ''}
{' · confiança baixa' if r.confianca < 0.6 else ''}</span></summary>
<form method="post" action="/web/registro"><input type="hidden" name="csrf" value="{c}">
<input type="hidden" name="id" value="{_e(r.id)}"><label>Corrigir para
<textarea name="texto" rows="3">{_e(r.conteudo)}</textarea></label>
<button>Salvar correção</button><span class="dica">O original continua guardado no histórico.</span></form>
</details></li>""")
        linhas_d = [f"""<li><div><b>{_e(despesas.brl(d['valor_centavos']))}</b> · {_e(d['descricao'])}
<span class="meta">{_e(d['data'] or 'sem data')} · {_e(despesas.ROTULO_CATEGORIA.get(d['categoria'], d['categoria']))}
{f" · {_e(d['contexto'])}" if d.get('contexto') else ''}</span></div></li>""" for d in gastos[:60]]

        vazio = '<p class="dica">Nada por aqui.</p>'
        return _pagina("Peraí", f"""
<header><h1>Peraí</h1><span>{_e(nome)}</span>
<form method="post" action="/web/sair"><input type="hidden" name="csrf" value="{c}"><button class="sec">Sair</button></form></header>
<main>
{f'<p class="aviso">{_e(aviso)}</p>' if aviso else ''}
<form class="busca" method="get" action="/web"><input type="search" name="q" value="{_e(busca)}"
placeholder="Buscar: Júlio, Gazin, proposta…" aria-label="Buscar"><button>Buscar</button></form>
<section><h2>✅ Pendências abertas <span class="conta">{len(pendencias)}</span></h2>
<ul>{''.join(linhas_p) or vazio}</ul></section>
<section><h2>📝 Registros <span class="conta">{len(registros)}</span></h2>
<p class="dica">Toque num registro para corrigir.</p><ul>{''.join(linhas_r) or vazio}</ul></section>
<section><h2>🧾 Despesas dos últimos 60 dias <span class="conta">{len(gastos)}</span></h2>
<ul>{''.join(linhas_d) or vazio}</ul></section>
<section><h2>⬇️ Seus dados</h2><p>Tudo o que o Peraí guardou de você, num arquivo JSON (formato aberto).</p>
<p><a class="botao" href="/web/exportar">Exportar tudo</a></p></section>
</main>""")


ESTILO = """:root{--f:#f7f7f3;--p:#fff;--t:#1d2320;--t2:#5b6661;--l:#dfe3df;--a:#0f6b5f;--s:#e3f0ec;--av:#fff4dc}
@media (prefers-color-scheme:dark){:root{--f:#131715;--p:#1b211e;--t:#e6ece9;--t2:#9fb0a9;--l:#2d3733;--a:#5cc4b2;--s:#1d3330;--av:#3a2f17}}
*{box-sizing:border-box}body{margin:0;background:var(--f);color:var(--t);font:16px/1.5 system-ui,sans-serif}
header{display:flex;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--l);background:var(--p)}
header h1{font-size:20px;margin:0;flex:1}header form{margin:0}
main{max-width:46rem;margin:0 auto;padding:16px}main.estreito{max-width:30rem;margin-top:12vh}
h2{font-size:18px;margin:28px 0 8px}.conta{color:var(--t2);font-weight:400;font-size:14px}
ul{list-style:none;margin:0;padding:0}li{background:var(--p);border:1px solid var(--l);border-radius:8px;padding:10px 12px;margin:8px 0}
li>div{display:grid;gap:2px}.meta{display:block;color:var(--t2);font-size:14px}.tag{font-size:13px;color:var(--t2);margin-right:6px}
summary{cursor:pointer}details form{display:grid;gap:8px;margin-top:10px}
textarea,input[type=search]{width:100%;font:inherit;padding:8px;border:1px solid var(--l);border-radius:6px;background:var(--f);color:var(--t)}
button,.botao{font:inherit;border:0;border-radius:6px;padding:8px 14px;background:var(--a);color:#fff;cursor:pointer;text-decoration:none;display:inline-block}
button.sec{background:var(--s);color:var(--t)}li form{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
.busca{display:flex;gap:8px}.dica{color:var(--t2);font-size:14px}.aviso{background:var(--av);padding:10px 12px;border-radius:8px}
a{color:var(--a)}"""


def _pagina(titulo: str, corpo: str) -> str:
    return (f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{_e(titulo)}</title>'
            f'<link rel="stylesheet" href="/web/estilo.css"></head><body>{corpo}</body></html>')
