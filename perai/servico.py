"""O coração do Peraí, independente do canal (D-002).

Recebe uma mensagem já baixada (texto, áudio, imagem ou conversa exportada), guarda
a original, transforma em texto, decide se é algo para registrar ou uma pergunta, e
devolve a resposta que o canal vai mostrar.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from perai import consulta, extracao, recorrencia
from perai.armazenamento import NovaMensagem, Repositorio
from perai.config import Modelo, Transcritor
from perai.leitura import ler_conversa_exportada, ler_imagem
from perai.llm import Cliente, ErroModelo

log = logging.getLogger("perai.servico")
CONVERSA_FALAS = 8                      # falas guardadas por usuário
CONVERSA_JANELA = timedelta(minutes=30)  # depois disso, assunto novo
FUSO = ZoneInfo("America/Sao_Paulo")  # usuário zero (D-008); por usuário depois

SISTEMA_ROTEADOR = """Classifique a mensagem que o usuário mandou ao Peraí, seu assistente de memória.
"pergunta": ele quer saber algo do que já foi registrado ou pede para preparar uma conversa
("o que combinei com o Wagner?", "me prepara pra reunião de quinta", "me lembra do que
falei com a Claudia").
"registro": ele conta algo, encaminha uma conversa, pede um lembrete para o futuro
("me lembra sexta às 9h de mandar a proposta") ou corrige algo.
Devolva só JSON: {"tipo": "pergunta" | "registro"}"""

INICIOS_PERGUNTA = ("o que ", "quando ", "quem ", "qual ", "quais ", "quanto ", "como foi",
                    "me prepara", "me ajuda a preparar", "me lembra do que", "me lembra o que",
                    "tenho alguma", "tem alguma")

NATUREZA_ROTULO = {"confirmado": "", "intencao": "intenção: ", "hipotese": "ideia: ", "opiniao": "opinião de "}
# Um ícone por natureza, para bater o olho e saber o que é fato, intenção, ideia ou opinião.
NATUREZA_ICONE = {"confirmado": "📝", "intencao": "🎯", "hipotese": "💡", "opiniao": "💬"}
NAO_ENCONTREI = "🔎 Não encontrei isso nos seus registros."


@dataclass
class Entrada:
    canal: str                      # telegram, web
    tipo: str                       # texto, audio, print, foto, conversa_exportada
    texto: str | None = None        # texto ou legenda
    arquivo: Path | None = None     # áudio, imagem ou .zip já salvos no servidor


def _agora() -> datetime:
    return datetime.now(FUSO).replace(tzinfo=None)


def _parece_pergunta(texto: str) -> bool:
    t = " ".join(texto.lower().split())
    return t.endswith("?") or t.startswith(INICIOS_PERGUNTA)


def _palavras(texto: str) -> set[str]:
    return {p for p in re.findall(r"\w+", texto.lower()) if len(p) >= 4}


class Perai:
    def __init__(self, repo: Repositorio, cliente: Cliente, modelo: Modelo,
                 transcritor: Transcritor, leitor_imagem: Modelo, pasta_temporaria: Path,
                 reserva: Modelo | list[Modelo] | None = None, agenda=None):
        self.repo = repo
        self.agenda = agenda  # perai.agenda.Agenda, ou None sem Google (D-051)
        # Últimas falas de cada usuário, para entender "muda para as 11h" ou "e com o Rafael?".
        # Fica só na memória do processo: não é fonte de nada, a fonte é sempre `mensagens`.
        self._conversas: dict[str, deque] = {}
        # Usados em ordem quando o modelo principal falha (sobrecarga, limite, fora do ar).
        self.reservas = reserva if isinstance(reserva, list) else ([reserva] if reserva else [])
        self.cliente = cliente
        self.modelo = modelo
        self.transcritor = transcritor
        self.leitor_imagem = leitor_imagem
        self.pasta_temporaria = pasta_temporaria

    # -- chamadas aos modelos, sempre registradas em chamadas_modelo ------------------

    def _conversar(self, usuario_id, tarefa, sistema, pedido, **kw):
        principal = self.leitor_imagem if tarefa == "leitura_imagem" else self.modelo
        candidatos = [principal] + [m for m in self.reservas if m != principal]
        for i, modelo in enumerate(candidatos):
            try:
                r = self.cliente.conversar(modelo, tarefa, sistema, pedido, **kw)
            except ErroModelo as erro:
                self.repo.registrar_chamada(usuario_id, _finalidade(tarefa), modelo.fornecedor,
                                            modelo.id_api, erro=str(erro)[:500])
                if i + 1 < len(candidatos):
                    log.warning("%s falhou em %s; usando a reserva %s", modelo.apelido, tarefa,
                                candidatos[i + 1].apelido)
                    continue
                raise
            self.repo.registrar_chamada(usuario_id, _finalidade(tarefa), modelo.fornecedor, modelo.id_api,
                                        r.tokens_entrada, r.tokens_saida, 0, r.custo_usd, r.latencia_ms)
            return r
        raise ErroModelo("nenhum modelo disponível")

    # -- entrada ---------------------------------------------------------------------

    def receber(self, usuario_id: str, entrada: Entrada) -> str:
        resposta, texto = self._receber(usuario_id, entrada)
        conversa = self._conversas.setdefault(usuario_id, deque(maxlen=CONVERSA_FALAS))
        agora = _agora()
        if texto and texto.strip():
            conversa.append((agora, "Você", texto.strip()[:600]))
        conversa.append((agora, "Peraí", resposta[:600]))
        return resposta

    def _conversa_recente(self, usuario_id: str) -> list[str]:
        limite = _agora() - CONVERSA_JANELA
        return [f"{quem}: {texto}" for quando, quem, texto in self._conversas.get(usuario_id, ())
                if quando >= limite]

    def _receber(self, usuario_id: str, entrada: Entrada) -> tuple[str, str]:
        try:
            texto, transcricao, modelo_transcricao = self._para_texto(usuario_id, entrada)
        except (ErroModelo, OSError) as erro:
            # Falhou ao ouvir ou ler o arquivo: guarda a mensagem original mesmo assim (D-040).
            log.warning("não consegui converter %s em texto: %s", entrada.tipo, str(erro)[:300])
            self.repo.salvar_mensagem(usuario_id, NovaMensagem(
                canal=entrada.canal, tipo=entrada.tipo, texto_original=entrada.texto,
                caminho_arquivo=str(entrada.arquivo) if entrada.arquivo else None,
                data_situacao=_agora().date().isoformat()))
            o_que = {"audio": "o áudio", "print": "a imagem", "foto": "a foto",
                     "conversa_exportada": "a conversa"}.get(entrada.tipo, "o arquivo")
            return (f"⚠️ Guardei {o_que}, mas não consegui {'ouvir' if entrada.tipo == 'audio' else 'ler'} "
                    "agora. Ele não se perde; tente mandar de novo em instantes."), ""
        mensagem_id = self.repo.salvar_mensagem(usuario_id, NovaMensagem(
            canal=entrada.canal, tipo=entrada.tipo, texto_original=entrada.texto,
            transcricao=transcricao,
            caminho_arquivo=str(entrada.arquivo) if entrada.arquivo else None,
            data_situacao=_agora().date().isoformat(), modelo_transcricao=modelo_transcricao,
        ))
        if not texto.strip():
            return "🤔 Recebi, mas não consegui ler nada aqui. Pode mandar de novo ou escrever?", texto
        try:
            if entrada.tipo in ("texto", "audio") and self._e_pergunta(usuario_id, texto):
                return self._responder(usuario_id, texto), texto
            return self._registrar(usuario_id, mensagem_id, entrada.tipo, texto), texto
        except ErroModelo:
            return ("⚠️ Guardei a sua mensagem original, mas o modelo de IA não respondeu agora. "
                    "Ela continua salva; tente de novo em instantes."), texto

    def _para_texto(self, usuario_id, entrada):
        if entrada.tipo == "audio" and entrada.arquivo:
            t = self.cliente.transcrever(self.transcritor, entrada.arquivo)
            self.repo.registrar_chamada(usuario_id, "transcricao", self.transcritor.fornecedor,
                                        self.transcritor.id_api, segundos_audio=t.segundos_audio,
                                        custo_usd=t.custo_usd, latencia_ms=t.latencia_ms)
            return t.texto, t.texto, self.transcritor.id_api
        if entrada.tipo in ("print", "foto") and entrada.arquivo:
            texto, custo, latencia = ler_imagem(self.cliente, self.leitor_imagem, entrada.arquivo, entrada.texto)
            self.repo.registrar_chamada(usuario_id, "leitura_imagem", self.leitor_imagem.fornecedor,
                                        self.leitor_imagem.id_api, custo_usd=custo, latencia_ms=latencia)
            return texto, texto, self.leitor_imagem.id_api
        if entrada.tipo == "conversa_exportada" and entrada.arquivo:
            destino = self.pasta_temporaria / entrada.arquivo.stem
            texto, custo, latencia = ler_conversa_exportada(self.cliente, self.transcritor,
                                                            entrada.arquivo, destino)
            self.repo.registrar_chamada(usuario_id, "transcricao", self.transcritor.fornecedor,
                                        self.transcritor.id_api, custo_usd=custo, latencia_ms=latencia)
            if entrada.texto:
                texto = f"Legenda do usuário: {entrada.texto}\n{texto}"
            return texto, texto, self.transcritor.id_api
        return entrada.texto or "", None, None

    def _e_pergunta(self, usuario_id, texto: str) -> bool:
        t = " ".join(texto.lower().split())
        # Casos claros, sem gastar uma chamada de modelo (cerca de 4 s a menos):
        if t.startswith(("me lembra do que", "me lembra o que")) or t.endswith("?"):
            return True
        if t.startswith("me lembra") or not _parece_pergunta(texto):
            return False   # pedido de lembrete ou relato: é registro
        # Começa como pergunta mas não termina com "?" ("quando o Marcos ligar, ..."): o modelo decide.
        r = self._conversar(usuario_id, "roteamento", SISTEMA_ROTEADOR, texto)
        try:
            tipo = extracao.extrair_json(r.texto).get("tipo")
        except (ValueError, AttributeError):
            tipo = None
        return tipo == "pergunta" if tipo in ("pergunta", "registro") else True

    # -- registrar -----------------------------------------------------------------

    def _agenda_conectada(self, usuario_id) -> bool:
        return bool(self.agenda and self.agenda.disponivel and self.agenda.conta(usuario_id))

    def _registrar(self, usuario_id, mensagem_id, tipo, texto) -> str:
        anteriores, abertas = self.repo.memoria(usuario_id)
        codigos = {f"P{i}": p for i, p in enumerate(abertas[-30:], start=1)}
        conectada = self._agenda_conectada(usuario_id)
        criados = self.agenda.criados_futuros(usuario_id)[:30] if conectada else []
        codigos_ev = {f"E{i}": e for i, e in enumerate(criados, start=1)}
        pedido = extracao.montar_pedido(
            "1", texto, _hoje_extenso(), tipo, self.repo.entidades_conhecidas(usuario_id),
            conversa=self._conversa_recente(usuario_id),
            pendencias_abertas=[f"[{c}] {_descrever_pendencia(p)}" for c, p in codigos.items()],
            eventos_criados=[f"[{c}] {e['titulo']} | {e['inicio'].replace('T', ' ')}" for c, e in codigos_ev.items()],
        )
        r = self._conversar(usuario_id, "extracao", extracao.SISTEMA_EXTRACAO, pedido)
        ext = extracao.interpretar("1", r.texto)
        if not ext.json_valido:
            return "⚠️ Guardei a mensagem como chegou, mas não consegui organizar agora. Ela não se perde."
        mapa = self.repo.salvar_extracao(usuario_id, mensagem_id, ext)
        corrigidos, descartadas = self._aplicar_correcoes(usuario_id, mensagem_id, ext, mapa, anteriores)
        ajustados = []
        for a in ext.ajustes:
            alvo = codigos.get(a.pendencia)
            if alvo is None:
                continue
            # Só o que muda de verdade: prazo igual ao atual não vira correção nem aviso.
            if a.prazo and a.prazo == alvo.prazo:
                a.prazo = None
            if a.lembrete_em and a.lembrete_em == alvo.lembrete_em:
                a.lembrete_em = None
            if not (a.lembrete_em or a.prazo or a.situacao):
                continue
            self.repo.ajustar_pendencia(usuario_id, alvo.id, mensagem_id, a.lembrete_em, a.prazo, a.situacao)
            if a.situacao == "concluida":
                self._avancar(usuario_id, alvo.id)
            ajustados.append((alvo, a))
        linhas_agenda, botoes = self._aplicar_agenda(usuario_id, mensagem_id, ext, mapa, codigos_ev, conectada)
        texto = _resumo_registro(ext, corrigidos, descartadas, ajustados)
        if linhas_agenda:
            texto = (texto + "\n" if texto != "✅ Anotado." or ext.registros else "") + "\n".join(linhas_agenda)
        return saida(texto, botoes)

    def _aplicar_agenda(self, usuario_id, mensagem_id, ext, mapa, codigos_ev, conectada):
        """Cria os compromissos na agenda e aplica mudanças nos eventos que o Peraí criou."""
        linhas, botoes = [], []
        if ext.compromissos and not conectada:
            if self.agenda and self.agenda.disponivel:
                linhas.append("📅 Isso parece um compromisso. Para eu colocar na sua agenda, use /agenda.")
            return linhas, botoes
        registro_id = next((v for k, v in mapa.items() if k.startswith("R")), None)
        for c in ext.compromissos:
            try:
                e = self.agenda.criar(usuario_id, mensagem_id, registro_id, c.titulo, c.inicio, c.fim, c.local)
            except Exception as erro:  # a anotação já está salva; só a agenda falhou
                log.warning("não consegui criar o evento: %s", str(erro)[:300])
                linhas.append(f"⚠️ Anotei, mas não consegui colocar “{c.titulo}” na agenda agora.")
                continue
            linhas.append(f"📅 Coloquei na sua agenda: {e['titulo']}, {_quando(e['inicio'], e['fim'])}"
                          + (f" · {e['local']}" if e.get("local") else ""))
            botoes.append((f"↩️ Desfazer: {e['titulo'][:30]}", f"desfazer:{e['id']}"))
        for a in ext.ajustes_eventos:
            alvo = codigos_ev.get(a.evento)
            if alvo is None:
                continue
            try:
                e = self.agenda.ajustar(usuario_id, alvo["id"], mensagem_id, a.inicio, a.fim, a.cancelar)
            except Exception as erro:
                log.warning("não consegui alterar o evento: %s", str(erro)[:300])
                linhas.append(f"⚠️ Não consegui mudar “{alvo['titulo']}” na agenda agora.")
                continue
            if e is None:
                continue
            if a.cancelar:
                linhas.append(f"🗑️ Tirei da agenda: {alvo['titulo']}.")
            else:
                linhas.append(f"📅 Mudei na agenda: {alvo['titulo']}, {_quando(e['inicio'], e['fim'])}.")
        return linhas, botoes

    def desfazer_evento(self, usuario_id: str, evento_id: str) -> str:
        if not self.agenda or not _uuid(evento_id):
            return "Não encontrei esse evento."
        try:
            e = self.agenda.desfazer(usuario_id, evento_id)
        except Exception as erro:
            log.warning("não consegui desfazer o evento: %s", str(erro)[:300])
            return "⚠️ Não consegui tirar da agenda agora. Tente de novo em instantes."
        return f"↩️ Tirei da agenda: {e['titulo']}." if e else "Esse evento já tinha saído da agenda."

    def _aplicar_correcoes(self, usuario_id, mensagem_id, ext, mapa, anteriores):
        """Corrige o registro anterior mais parecido com o que o usuário disse que mudou."""
        feitos, descartadas = [], []
        for r in ext.registros:
            if not r.corrige:
                continue
            alvo = _mais_parecido(r.corrige + " " + r.conteudo, anteriores)
            if alvo is None:
                continue
            descartadas += self.repo.corrigir_registro(usuario_id, alvo.id, mapa[r.id], mensagem_id) or []
            feitos.append(alvo.conteudo)
        return feitos, descartadas

    # -- responder -----------------------------------------------------------------

    def _responder(self, usuario_id, pergunta) -> str:
        registros, pendencias = self.repo.memoria(usuario_id)
        tipo = "briefing" if "prepar" in pergunta.lower() else "consulta"
        memoria = consulta.formatar_memoria(registros, pendencias)
        # Eventos da agenda entram como itens [A1], [A2]... e podem ser citados como fonte.
        eventos = self.agenda.eventos(usuario_id) if self._agenda_conectada(usuario_id) else []
        codigos_ag = {f"A{i}": e for i, e in enumerate(eventos, start=1)}
        if codigos_ag:
            linhas = [consulta.formatar_evento(c, e) for c, e in codigos_ag.items()]
            memoria = (memoria if registros or pendencias else "") + "\n" + "\n".join(linhas)
        pedido = consulta.montar_pedido(pergunta, memoria.strip() or "(nenhum item de memória)",
                                        _hoje_extenso(), self._conversa_recente(usuario_id))
        r = self._conversar(usuario_id, tipo, consulta.SISTEMA_CONSULTA, pedido)
        ids = {x.id for x in registros} | {p.id for p in pendencias} | set(codigos_ag)
        resp = consulta.interpretar(pergunta, r.texto, ids)
        citados = [c for c in resp.registros_citados if c not in codigos_ag]
        externos = [f"google:{codigos_ag[c].id}" for c in resp.registros_citados if c in codigos_ag]
        # Nada sem fonte (D-040): sem citação válida, a resposta só pode dizer que não sabe.
        if not (citados or externos) or resp.sem_informacao or not resp.json_valido:
            texto = NAO_ENCONTREI
            self.repo.salvar_resposta(usuario_id, tipo, pergunta, texto, [], True)
            return texto
        self.repo.salvar_resposta(usuario_id, tipo, pergunta, resp.resposta, citados, False, externos)
        return resp.resposta

    # -- lembretes -----------------------------------------------------------------

    def lembretes_para_enviar(self):
        """Lembretes vencidos. Só existem os que o usuário pediu (D-007)."""
        return self.repo.lembretes_vencidos(_agora())

    def lembrete_enviado(self, pendencia_id: str, usuario_id: str | None = None) -> None:
        self.repo.marcar_lembrete_enviado(pendencia_id, _agora())
        if usuario_id:
            self._avancar(usuario_id, pendencia_id)  # repetição: já agenda a próxima vez

    def _avancar(self, usuario_id: str, pendencia_id: str) -> dict | None:
        """Pendência que se repete: cria a próxima vez (uma só, mesmo se chamada de novo)."""
        p = self.repo.pendencia(usuario_id, pendencia_id)
        if not p or not p.get("recorrencia"):
            return None
        prazo, lembrete = recorrencia.proxima_ocorrencia(p["recorrencia"], p["prazo"], p["lembrete_em"],
                                                         _agora().date())
        novo = self.repo.criar_seguinte(usuario_id, pendencia_id, prazo, lembrete)
        return {"id": novo, "prazo": prazo, "lembrete_em": lembrete} if novo else None

    def avancar_recorrencias(self) -> int:
        """Repetições cujo prazo passou sem ninguém marcar: a próxima vez entra mesmo assim."""
        feitas = 0
        for pendencia_id, usuario_id in self.repo.recorrentes_vencidas(_agora().date()):
            if self._avancar(usuario_id, pendencia_id):
                feitas += 1
        return feitas

    def concluir_pendencia(self, usuario_id: str, pendencia_id: str) -> str:
        p = self.repo.pendencia(usuario_id, pendencia_id) if _uuid(pendencia_id) else None
        if not p:
            return "Não encontrei essa tarefa."
        if p["situacao"] in ("concluida", "descartada"):
            return "Essa tarefa já estava fechada."
        self.repo.ajustar_pendencia(usuario_id, pendencia_id, None, situacao="concluida")
        texto = f"✔️ Feito: {p['descricao']}."
        if p.get("recorrencia"):
            self._avancar(usuario_id, pendencia_id)
            prazo, lembrete = recorrencia.proxima_ocorrencia(p["recorrencia"], p["prazo"], p["lembrete_em"],
                                                             _agora().date())
            texto += f"\n🔁 Próxima vez: {_data_br(lembrete or prazo)}."
        return texto

    def adiar_pendencia(self, usuario_id: str, pendencia_id: str, horas: int = 1) -> str:
        p = self.repo.pendencia(usuario_id, pendencia_id) if _uuid(pendencia_id) else None
        if not p or p["situacao"] in ("concluida", "descartada"):
            return "Essa tarefa já estava fechada."
        novo = (_agora() + timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M")
        self.repo.ajustar_pendencia(usuario_id, pendencia_id, None, lembrete_em=novo)
        return f"⏰ Adiado: {p['descricao']}, te lembro às {novo[11:]}."


def _finalidade(tarefa: str) -> str:
    return {"roteamento": "consulta", "extracao": "extracao", "consulta": "consulta",
            "briefing": "briefing", "leitura_imagem": "leitura_imagem"}.get(tarefa, "consulta")


def _mais_parecido(texto: str, registros) -> object | None:
    chave = _palavras(texto)
    melhor, pontos = None, 0
    for r in registros:
        p = len(chave & _palavras(r.conteudo))
        if p > pontos:
            melhor, pontos = r, p
    return melhor if pontos >= 2 else None


def _formatar_prazo(p) -> str:
    if p.prazo:
        a, m, d = p.prazo.split("-")
        return f" até {d}/{m}"
    return f" ({p.prazo_como_dito})" if p.prazo_como_dito else ""


def _uuid(valor: str) -> bool:
    try:
        uuid.UUID(str(valor))
        return True
    except ValueError:
        return False


class Saida(str):
    """Texto de resposta que pode levar botões do Telegram: [(rótulo, dados)]."""
    botoes: tuple = ()


def saida(texto: str, botoes=()) -> Saida:
    s = Saida(texto)
    s.botoes = tuple(botoes)
    return s


DIAS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


def _hoje_extenso() -> str:
    hoje = _agora().date()
    dia = DIAS[hoje.weekday()]
    return f"{hoje.isoformat()} ({dia}-feira)" if hoje.weekday() < 5 else f"{hoje.isoformat()} ({dia})"


def _quando(inicio: str, fim: str | None) -> str:
    d = datetime.fromisoformat(inicio)
    texto = f"{DIAS[d.weekday()][:3]} {d:%d/%m}, {d:%H:%M}"
    if fim:
        f = datetime.fromisoformat(fim)
        texto += f"–{f:%H:%M}" if f.date() == d.date() else f" até {f:%d/%m %H:%M}"
    return texto


def _data_br(data_hora: str) -> str:
    data, _, hora = data_hora.partition("T")
    a, m, d = data.split("-")
    return f"{d}/{m} às {hora}" if hora else f"{d}/{m}"


def _descrever_pendencia(p) -> str:
    partes = [p.descricao]
    if p.prazo:
        partes.append(f"prazo {p.prazo}")
    if p.lembrete_em:
        partes.append(f"lembrete {p.lembrete_em.replace('T', ' ')}")
    return " | ".join(partes)


def _resumo_registro(ext: extracao.Extracao, corrigidos: list[str], descartadas: list[str] | None = None,
                     ajustados: list | None = None) -> str:
    descartadas, ajustados = descartadas or [], ajustados or []
    if not (ext.registros or ext.pendencias or ajustados or corrigidos):
        # Nada foi organizado: só a dúvida (a mensagem original continua guardada).
        if ext.duvidas:
            return "❓ Antes de mexer em algo, uma dúvida: " + ext.duvidas[0]
        return "✅ Anotado."
    linhas = ["✅ Anotado."] if (ext.registros or ext.pendencias) else []
    for r in ext.registros:
        if ajustados and not ext.pendencias and r.natureza == "intencao":
            continue  # o ajuste abaixo já diz o que mudou
        rotulo = NATUREZA_ROTULO.get(r.natureza, "")
        if r.natureza == "opiniao":
            rotulo = f"opinião de {r.dito_por}: " if r.dito_por.lower() != "eu" else "sua opinião: "
        linhas.append(f"{NATUREZA_ICONE.get(r.natureza, '•')} {rotulo}{r.conteudo}")
    for p in ext.pendencias:
        quem = "📌 Você" if p.responsavel == "eu" else f"⏳ Aguardando {p.quem or 'alguém'}"
        linha = f"{quem}: {p.descricao}{_formatar_prazo(p)}"
        if p.recorrencia:
            linha += f" · 🔁 {recorrencia.por_extenso(p.recorrencia)}"
        if p.lembrete_em:
            linha += f"\n   ⏰ lembrete {_data_br(p.lembrete_em)}"
        linhas.append(linha)
    for alvo, a in ajustados:
        if a.situacao == "concluida":
            linhas.append(f"✔️ Marquei como feito: {alvo.descricao}.")
        elif a.situacao == "descartada":
            linhas.append(f"🗑️ Cancelei: {alvo.descricao}.")
        if a.lembrete_em:
            linhas.append(f"⏰ Lembrete de “{alvo.descricao}” mudou para {_data_br(a.lembrete_em)}.")
        if a.prazo:
            linhas.append(f"📅 Prazo de “{alvo.descricao}” mudou para {_data_br(a.prazo)}.")
    for antigo in corrigidos:
        linhas.append(f"✏️ Corrigi o registro anterior: “{antigo}”.")
    for d in descartadas:
        linhas.append(f"🗑️ A pendência “{d}” deixou de valer.")
    if ext.duvidas:
        linhas.append("❓ Fiquei com uma dúvida: " + ext.duvidas[0])
    elif any(p.responsavel == "eu" and not p.lembrete_em for p in ext.pendencias):
        linhas.append("⏰ Se quiser lembrete, é só pedir: “me lembra sexta às 9h de…”.")
    return "\n".join(linhas)
