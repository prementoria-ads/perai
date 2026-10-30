"""Bot do Telegram do Peraí (etapa 3 do MVP).

Configuração por variáveis de ambiente (veja .env.example):
    TELEGRAM_BOT_TOKEN          token do @BotFather
    PERAI_TELEGRAM_PERMITIDOS   ids de chat autorizados, separados por vírgula (D-009)
    DATABASE_URL                postgresql://perai:senha@banco:5432/perai
    PERAI_MODELO                modelo de linguagem (provisório até a D-038)
    PERAI_TRANSCRITOR, PERAI_LEITOR_IMAGEM, PERAI_ARQUIVOS

Rodar: python -m perai.bot
"""

from __future__ import annotations

import logging
import re
import os
import sys
import time
import uuid
from pathlib import Path

from perai import migracoes
from perai.agenda import agenda_do_ambiente
from perai.armazenamento import RepositorioMemoria, RepositorioPostgres
from perai.config import TRANSCRITORES, modelo_por_nome
from perai.leitura import EXTENSOES_AUDIO, EXTENSOES_IMAGEM
from perai.llm import Cliente
from perai.servico import Entrada, Perai
from perai.telegram import ErroTelegram, MensagemTelegram, Telegram

log = logging.getLogger("perai.bot")

BOAS_VINDAS = (
    "👋 Oi, {nome}! Eu sou o Peraí, sua memória de bolso.\n\n"
    "🗣️ Me conte, encaminhe ou mande áudio do que você quer lembrar depois: combinados, "
    "conversas, ideias, prints.\n\n"
    "🔎 Quando precisar, é só perguntar: “o que ficou combinado sobre a viagem?”\n"
    "⏰ Lembrete eu só crio quando você pedir: “me lembra amanhã às 9h de…”\n"
    "✏️ Errei? É só corrigir: “não foi sexta, foi sábado”.\n"
    "🧾 Gastos: mande o valor, a foto do cupom, o PDF ou o XML da nota.\n"
    "🧳 Relatório de viagem: /viagem 27/10 29/10\n"
    "📅 Agenda Google: /agenda\n"
    "💻 Pelo navegador (consultar, corrigir, exportar): /web\n"
    "💼 Outra agenda (ex.: a do trabalho): /agenda_ocupado, ou mande o .ics exportado\n\n"
    "🔒 O que você me conta fica na sua memória: outras pessoas que usam o Peraí não veem nada seu.\n"
    "🧪 Ainda estou em teste e posso errar."
)
NAO_AUTORIZADO = (
    "👋 Oi! Eu sou o Peraí, um assistente de memória em teste fechado.\n"
    "🔑 Para liberar seu acesso, mande este código para quem te convidou: {codigo}"
)


PARTE_LONGA = 3500  # o Telegram corta textos em 4096 caracteres; perto disso, é parte de um texto maior


def _parte_longa(m: MensagemTelegram) -> bool:
    return m.tipo == "texto" and len(m.texto or "") >= PARTE_LONGA


def juntar_partes(lote: list[MensagemTelegram]) -> list[MensagemTelegram]:
    """Junta de volta as partes de um texto longo que o Telegram dividiu (mesmo chat, em sequência)."""
    saida: list[MensagemTelegram] = []
    continua = False  # a última parte acrescentada era longa: a próxima do mesmo chat é continuação
    for m in lote:
        anterior = saida[-1] if saida else None
        if continua and anterior and m.tipo == "texto" and anterior.chat_id == m.chat_id:
            saida[-1] = MensagemTelegram(m.update_id, m.chat_id, m.nome, "texto",
                                         (anterior.texto or "") + "\n" + (m.texto or ""))
        else:
            saida.append(m)
        continua = _parte_longa(m)
    return saida


def agenda_fuso():
    from zoneinfo import ZoneInfo
    return ZoneInfo("America/Sao_Paulo")


def tipo_do_documento(nome: str) -> str | None:
    sufixo = Path(nome).suffix.lower()
    if sufixo in (".zip", ".txt"):
        return "conversa_exportada"  # .zip com mídia ou .txt "sem mídia" do WhatsApp
    if sufixo in EXTENSOES_IMAGEM:
        return "print"
    if sufixo in EXTENSOES_AUDIO:
        return "audio"
    if sufixo in (".pdf", ".xml"):
        return "documento"  # nota fiscal (DANFE ou XML da NF-e), recibo, boleto
    if sufixo == ".ics":
        return "documento"  # agenda exportada (D-064)
    if sufixo in (".docx", ".xlsx", ".pptx", ".md", ".markdown"):
        return "documento"  # Word, Excel, PowerPoint: texto lido pelo código
    return None


class Bot:
    def __init__(self, telegram: Telegram, perai: Perai, permitidos: set[int], pasta_arquivos: Path):
        self.telegram = telegram
        self.perai = perai
        self.permitidos = permitidos
        self.pasta = pasta_arquivos

    def tratar(self, m: MensagemTelegram) -> str | None:
        if m.tipo == "ignorar":
            return None
        if m.chat_id not in self.permitidos:
            log.warning("chat não autorizado: %s", m.chat_id)
            # Mostra à própria pessoa o id do chat dela, para o administrador liberar.
            return NAO_AUTORIZADO.format(codigo=m.chat_id)
        usuario = self.perai.repo.usuario_por_telegram(m.chat_id)
        if usuario is None:
            usuario = self.perai.repo.criar_usuario(m.nome or "Usuário", m.chat_id)
        if m.tipo == "botao":
            acao, _, alvo = (m.texto or "").partition(":")
            acoes = {"desfazer": self.perai.desfazer_evento, "feito": self.perai.concluir_pendencia,
                     "adiar": self.perai.adiar_pendencia, "vd": self.perai.vincular_despesa_evento,
                     "va": self.perai.acompanhar_viagem, "av": self.perai.avaliar,
                     "dc": self.perai.desfazer_cancelamento, "di": self.perai.escolher_item}
            if acao in acoes and alvo:
                texto = acoes[acao](usuario, alvo)
                if m.botao_id:
                    self.telegram.responder_botao(m.botao_id, texto)
                if getattr(texto, "trocar", False):
                    # Marcar e desmarcar itens: só atualiza os botões da mesma mensagem.
                    if m.mensagem_id and hasattr(self.telegram, "trocar_botoes"):
                        self.telegram.trocar_botoes(m.chat_id, m.mensagem_id, texto.botoes)
                    return None
                # Na escolha de compromissos dá para tocar em vários: os botões só saem no "Pronto".
                if m.mensagem_id and not (acao == "vd" and not alvo.endswith(":0")):
                    self.telegram.tirar_botoes(m.chat_id, m.mensagem_id)
                return texto
            return None
        if m.tipo == "comando":
            comando = (m.texto or "").split()[0].split("@")[0].lower()
            if comando in ("/start", "/ajuda", "/help"):
                nome = (self.perai.repo.nome_usuario(usuario) or m.nome or "").split()
                return BOAS_VINDAS.format(nome=nome[0] if nome else "tudo bem")
            if comando == "/metricas":
                datas = re.findall(r"(\d{1,2})/(\d{1,2})", m.texto or "")
                from datetime import date as _d, timedelta as _td
                from perai.servico import _agora
                hoje = _agora().date()
                try:
                    inicio_teste = os.environ.get("PERAI_TESTE_INICIO", "").strip()  # D-057
                    desde = (_d(hoje.year, int(datas[0][1]), int(datas[0][0])) if datas
                             else _d.fromisoformat(inicio_teste) if inicio_teste else hoje - _td(days=27))
                except ValueError:
                    return "Não entendi a data. Use, por exemplo: /metricas 29/10"
                return self.perai.relatorio_metricas(usuario, desde)
            if comando == "/viagem":
                return self._viagem(usuario, m.texto)
            if comando == "/agenda":
                return self._agenda(usuario)
            if comando == "/web":
                return self._web(usuario)
            if comando == "/agenda_ocupado":
                return self._agenda_ocupado(usuario, (m.texto or "").split()[1:])
            if comando == "/desconectar_agenda":
                agenda = self.perai.agenda
                if agenda and agenda.disponivel and agenda.desconectar(usuario):
                    return "🔌 Agenda desconectada. Não leio nem crio mais eventos. Para voltar: /agenda"
                return "Sua agenda não estava conectada."
            return None

        tipo, arquivo = m.tipo, None
        if m.tipo == "documento":
            tipo = tipo_do_documento(m.nome_arquivo or "")
            if tipo is None:
                return ("Ainda não sei ler esse tipo de arquivo. Mande texto, áudio, foto, print, PDF, "
                        "XML de nota fiscal, Word, Excel, PowerPoint, .txt, .md ou conversa exportada.")
        if m.file_id:
            # Arquivo guardado por usuário, com nome único (a mensagem original é imutável).
            sufixo = Path(m.nome_arquivo or "").suffix or ".bin"
            destino = self.pasta / usuario / f"{uuid.uuid4().hex}{sufixo}"
            arquivo = self.telegram.baixar(m.file_id, destino)
        return self.perai.receber(usuario, Entrada("telegram", tipo, m.texto, arquivo))

    def _viagem(self, usuario: str, texto: str) -> str:
        """/viagem 27/10 29/10 (ou /viagem sozinho = últimos 7 dias)."""
        from datetime import date, timedelta
        from perai.servico import _agora
        hoje = _agora().date()
        datas = re.findall(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", texto or "")
        try:
            lidas = [date(int(a) + (2000 if a and len(a) == 2 else 0) if a else hoje.year, int(m), int(d))
                     for d, m, a in datas]
        except ValueError:
            return "Não entendi as datas. Use, por exemplo: /viagem 27/10 29/10"
        if not lidas:
            return self.perai.relatorio_atual(usuario)
        else:
            inicio, fim = lidas[0], (lidas[1] if len(lidas) > 1 else lidas[0])
        if fim < inicio:
            inicio, fim = fim, inicio
        return self.perai.relatorio_viagem(usuario, inicio, fim)

    def _agenda_ocupado(self, usuario: str, args: list[str]) -> str:
        """Outra agenda só como livre/ocupado (D-064): /agenda_ocupado e-mail [rótulo] | remover e-mail."""
        from datetime import datetime, timedelta
        from perai.google import ErroGoogle
        from perai.ics import rotulo
        agenda = self.perai.agenda
        conta = agenda.conta(usuario) if agenda and agenda.disponivel else None
        if not conta:
            return "📅 Primeiro conecte a sua agenda Google com /agenda."
        repo = self.perai.repo
        if not args:
            atuais = repo.agendas_ocupado(usuario)
            lista = "\n".join(f"• 💼 {r} ({c})" for c, r in atuais) or "• nenhuma ainda"
            return ("📅 Agendas que eu vejo só como livre/ocupado:\n" + lista + "\n\n"
                    "Para incluir: /agenda_ocupado nelson.takemura@l8group.net\n"
                    "Para tirar: /agenda_ocupado remover nelson.takemura@l8group.net")
        if args[0].lower() == "remover" and len(args) > 1:
            repo.definir_agenda_ocupado(usuario, args[1].lower(), None)
            return f"🗑️ Não vou mais olhar a agenda {args[1]}."
        cal = args[0].strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cal):
            return "Mande o e-mail da agenda, por exemplo: /agenda_ocupado nelson.takemura@l8group.net"
        nome = " ".join(args[1:])[:20] or rotulo(cal)
        agora = datetime.now(agenda_fuso())
        try:
            blocos = agenda.google.livre_ocupado(conta[1], [cal], agora, agora + timedelta(days=7))
        except ErroGoogle as erro:
            if "403" in str(erro):
                return ("🔑 Falta uma permissão nova (ver livre/ocupado). Mande /desconectar_agenda e depois "
                        "/agenda para conectar de novo; em seguida repita este comando.")
            return "⚠️ O Google não respondeu agora. Tente de novo em instantes."
        if cal not in blocos:
            return (f"🔒 O Google não deixou ver o livre/ocupado de {cal}. Confira se a agenda foi adicionada "
                    "na sua conta pessoal (pelo link de compartilhamento) e se a empresa permite.")
        repo.definir_agenda_ocupado(usuario, cal, nome)
        return (f"💼 Pronto: vou mostrar os horários ocupados da agenda {nome} junto da sua, sem detalhes "
                f"(só “ocupado”). Nos próximos 7 dias: {len(blocos[cal])} blocos.\n"
                "Para ter os títulos também, mande o .ics exportado dessa agenda.")

    def _web(self, usuario: str) -> str:
        """Convite de uso único para ligar a conta Google à web (D-063)."""
        from perai.painel import Painel
        agenda = self.perai.agenda
        if not agenda or not agenda.disponivel:
            return "💻 A entrada pela web ainda não foi ligada neste servidor."
        link = Painel(self.perai.repo, agenda.google, agenda.endereco).convite(usuario)
        return ("💻 Para usar o Peraí pelo navegador, abra o link e entre com a sua conta Google "
                "(vale 15 minutos e só funciona uma vez):\n"
                f"{link}\n\n"
                "Depois disso é só entrar em perai.prementoria.com/web com a mesma conta Google, sem passar "
                "pelo Telegram. Lá dá para buscar, corrigir registros, fechar pendências e exportar tudo.")

    def _agenda(self, usuario: str) -> str:
        agenda = self.perai.agenda
        if not agenda or not agenda.disponivel:
            return "📅 A conexão com o Google Agenda ainda não foi ligada neste servidor."
        email = agenda.email(usuario)
        if email:
            return (f"📅 Sua agenda Google ({email or 'conta conectada'}) está conectada.\n"
                    "Conte compromissos com dia e hora que eu coloco lá, e pergunte “o que tenho amanhã?”.\n"
                    "Para desconectar: /desconectar_agenda")
        return ("📅 Para conectar sua agenda Google, toque no link (vale 15 minutos e só funciona uma vez):\n"
                f"{agenda.link_conexao(usuario)}\n\n"
                "O Google vai avisar que o app não é verificado: é normal neste teste. Toque em "
                "“Avançado” e depois em “Acessar Peraí”.\n"
                "🔒 Eu só crio eventos na sua própria agenda e nunca mando convite para ninguém.")

    def enviar_lembretes(self) -> None:
        for l in self.perai.lembretes_para_enviar():
            if l.telegram_chat_id is None:
                continue
            self.telegram.enviar(l.telegram_chat_id, f"⏰ Lembrete: {l.descricao}",
                                 [("✔️ Feito", f"feito:{l.pendencia_id}"),
                                  ("⏰ Adiar 1h", f"adiar:{l.pendencia_id}")])
            self.perai.lembrete_enviado(l.pendencia_id, l.usuario_id)
        for chat, texto in self.perai.checkins_para_enviar():
            self.telegram.enviar(chat, texto)
        agora = time.monotonic()
        if agora - getattr(self, "_ultima_recorrencia", 0) > 3600:  # uma vez por hora
            self._ultima_recorrencia = agora
            self.perai.avancar_recorrencias()

    def rodar(self) -> None:
        ultimo = None
        log.info("Peraí no ar.")
        while True:
            try:
                lote = self.telegram.receber(ultimo, espera_s=30)
                # Texto longo colado chega picado em partes de até 4096 caracteres: espera o resto.
                while lote and _parte_longa(lote[-1]):
                    mais = self.telegram.receber(lote[-1].update_id, espera_s=3)
                    if not mais:
                        break
                    lote += mais
                for m in juntar_partes(lote):
                    ultimo = m.update_id
                    inicio = time.monotonic()
                    log.info("mensagem %s (%s) recebida", m.update_id, m.tipo)  # sem conteúdo
                    if m.tipo not in ("ignorar", "comando", "botao") and m.chat_id in self.permitidos:
                        self.telegram.digitando(m.chat_id)
                    try:
                        resposta = self.tratar(m)
                    except Exception:
                        log.exception("erro ao tratar mensagem %s", m.update_id)
                        resposta = "Tive um problema com essa mensagem. Tente de novo em instantes."
                    if resposta:
                        self.telegram.enviar(m.chat_id, resposta, getattr(resposta, "botoes", ()))
                    log.info("mensagem %s tratada em %.1fs", m.update_id, time.monotonic() - inicio)
                self.enviar_lembretes()
            except (ErroTelegram, OSError) as erro:
                log.warning("Telegram indisponível: %s", erro)
                time.sleep(5)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    try:
        permitidos = {int(x) for x in os.environ.get("PERAI_TELEGRAM_PERMITIDOS", "").split(",") if x.strip()}
    except ValueError:
        print("PERAI_TELEGRAM_PERMITIDOS deve ter só números separados por vírgula", file=sys.stderr)
        return 1
    if not token or not permitidos:
        print("Defina TELEGRAM_BOT_TOKEN e PERAI_TELEGRAM_PERMITIDOS no .env", file=sys.stderr)
        return 1
    url = os.environ.get("DATABASE_URL")
    repo = RepositorioPostgres(url) if url else RepositorioMemoria()
    if url:
        migracoes.aplicar(repo.conn)
    agenda = agenda_do_ambiente(repo)
    pasta = Path(os.environ.get("PERAI_ARQUIVOS", "arquivos"))
    try:
        modelo = modelo_por_nome(os.environ.get("PERAI_MODELO") or "gemini-flash")
        leitor = modelo_por_nome(os.environ.get("PERAI_LEITOR_IMAGEM") or "gemini-flash")
        reserva = [modelo_por_nome(n.strip())
                   for n in os.environ.get("PERAI_MODELO_RESERVA", "").split(",") if n.strip()]
    except ValueError as erro:
        print(erro, file=sys.stderr)
        return 1
    perai = Perai(
        repo, Cliente(raciocinio=os.environ.get("PERAI_RACIOCINIO", "low").strip() or None,
                      tempo_limite=float(os.environ.get("PERAI_TEMPO_LIMITE", "30"))), modelo,
        TRANSCRITORES[os.environ.get("PERAI_TRANSCRITOR") or "gemini-audio"],
        leitor, pasta / "temporario", reserva=reserva, agenda=agenda,
    )
    log.info("Google Agenda %s", "ligada" if agenda.disponivel else "desligada (faltam variáveis do Google)")
    log.info("modelo %s, reservas %s", modelo.id_api, ", ".join(m.id_api for m in reserva) or "nenhuma")
    Bot(Telegram(token), perai, permitidos, pasta).rodar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
