"""O coração do Peraí, independente do canal (D-002).

Recebe uma mensagem já baixada (texto, áudio, imagem ou conversa exportada), guarda
a original, transforma em texto, decide se é algo para registrar ou uma pergunta, e
devolve a resposta que o canal vai mostrar.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal
import logging
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET
import urllib.parse
import unicodedata
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from perai import nfce
from perai import comissao, contas as contas_mod, resultado as resultado_mod, parcerias, propostas as propostas_mod, atas, consulta, despesas, extracao, ics, precos, recorrencia, sites, viagens, voz
from perai.armazenamento import NovaMensagem, Repositorio
from perai import cambio, crm as crm_mod
from perai.servico_crm import CRMMixin
from perai.config import Modelo, Transcritor, fator_custo
from perai.leitura import (EXTENSOES_OFFICE, EXTENSOES_TEXTO, ler_conversa_exportada, ler_imagem, ler_office,
                           ler_pdf, ler_texto, EXTENSOES_IMAGEM)
from perai.google import ErroGoogle
from perai.llm import Cliente, ErroModelo

log = logging.getLogger("perai.servico")
CONVERSA_FALAS = 8                      # falas guardadas por usuário
CONVERSA_JANELA = timedelta(minutes=30)  # depois disso, assunto novo
FUSO = ZoneInfo("America/Sao_Paulo")  # usuário zero (D-008); por usuário depois
ETAPAS = {"prospeccao": "prospecção", "proposta": "proposta", "negociacao": "negociação", "ganha": "ganha",
          "perdida": "perdida"}
FALA_DE_OPORTUNIDADE = re.compile(r"oportunidad|propost|lucro|margem|pricing|negocia|funil|pipeline|projet|vend[ae]|fech|cota[cç]|or[cç]ament",
                                  re.I)
FALA_DE_RESULTADO = re.compile(r"resultado|saldo|fatur|receb|pag[ao]u|nota|nf\b|vale|investi|compensa|lucro|faturamento|projeto|evento|retorno|"
                               r"rentab|margem", re.I)
FALA_DE_PARCERIA = re.compile(r"comiss|indica|parceri|contrato|hub|parcela|imposto|receber", re.I)
FALA_DE_CUSTO = re.compile(r"gast|cust|despes|invest|rateio|rate[ai]", re.I)
EXTENSOES_VISUAIS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}   # panfleto lido direto do arquivo (D-084)

SISTEMA_ROTEADOR = """Classifique a mensagem que o usuário mandou ao Peraí, seu assistente de memória.
"pergunta": ele quer saber algo do que já foi registrado ou pede para preparar uma conversa
("o que combinei com o Wagner?", "me prepara pra reunião de quinta", "me lembra do que
falei com a Claudia").
"registro": ele conta algo, encaminha uma conversa, pede um lembrete para o futuro
("me lembra sexta às 9h de mandar a proposta") ou corrige algo.
Devolva só JSON: {"tipo": "pergunta" | "registro"}"""

INICIOS_PERGUNTA = ("o que ", "quando ", "quem ", "qual ", "quais ", "quanto ", "como foi",
                    "me prepara", "me prepare", "me ajuda a preparar", "me lembra do que",
                    "me lembra o que",
                    "tenho alguma", "tem alguma", "resum", "faça um resumo", "faz um resumo", "me dá um resumo",
                    "me de um resumo", "me dê um resumo") + (
    # Pedidos de consulta no imperativo ("liste os próximos eventos"), sem "?".
    "liste ", "listar ", "lista os ", "lista as ", "lista meus ", "lista minhas ", "mostre ", "mostra ",
    "me mostra", "me mostre", "me diga ", "me fala ", "minha agenda", "minhas tarefas",
    "próximos eventos", "proximos eventos", "próximos compromissos", "proximos compromissos")

NATUREZA_ROTULO = {"confirmado": "", "intencao": "intenção: ", "hipotese": "ideia: ", "opiniao": "opinião de "}
# Um ícone por natureza, para bater o olho e saber o que é fato, intenção, ideia ou opinião.
NATUREZA_ICONE = {"confirmado": "📝", "intencao": "🎯", "hipotese": "💡", "opiniao": "💬"}
NAO_ENCONTREI = "🔎 Não encontrei isso nos seus registros."
# Briefing sem nada registrado: em vez de "não encontrei", pede o que falta (sem afirmar nada).
PEDIR_BRIEFING = ("📋 Ainda não tenho nada anotado sobre isso. Para eu te preparar, me conta:\n"
                  "1. Quem é (empresa, cargo) e de onde vocês se conhecem?\n"
                  "2. Qual é o objetivo da conversa?\n"
                  "3. Ficou algo pendente da última vez? Se tiver print ou áudio, pode mandar.\n"
                  "Pode responder num áudio só; em seguida eu monto o briefing.")
BRIEFING_JANELA = timedelta(minutes=30)
JANELA_ICS_DIAS = 90
# Meta de custo (R$ 200/mês) em dólares: câmbio em PERAI_DOLAR (padrão R$ 5,20, perto do Focus de out/2026).
CUSTO_META_USD = 200 / float(os.environ.get("PERAI_DOLAR", "5.20") or 5.20)   # agenda importada: de ontem até 90 dias à frente


@dataclass
class Entrada:
    canal: str                      # telegram, web
    tipo: str                       # texto, audio, print, foto, conversa_exportada
    texto: str | None = None        # texto ou legenda
    arquivo: Path | None = None     # áudio, imagem ou .zip já salvos no servidor
    nome_original: str | None = None  # nome do arquivo como o usuário mandou ("facco.md")
    encaminhada: str | None = None    # encaminhada no Telegram: de quem veio
    resposta_a: str | None = None     # mensagem a que o usuário respondeu no Telegram


def _agora() -> datetime:
    return datetime.now(FUSO).replace(tzinfo=None)


def _parece_pergunta(texto: str) -> bool:
    t = " ".join(texto.lower().split())
    return t.endswith("?") or t.startswith(INICIOS_PERGUNTA)


def _palavras(texto: str) -> set[str]:
    return {p for p in re.findall(r"\w+", texto.lower()) if len(p) >= 4}


# Importar a memória de outra IA (D-071): o pedido que o usuário cola no ChatGPT, Claude ou Gemini.
PEDIDO_IMPORTACAO = (
    "Quero levar para o meu assistente Peraí o que você sabe sobre mim. Comece a resposta com esta linha, "
    "exatamente assim: MEMÓRIA IMPORTADA — seu nome (ChatGPT, Claude ou Gemini).\n"
    "Depois liste, em tópicos curtos, só o que eu mesmo contei a você nas nossas conversas ou na sua "
    "memória: quem eu sou; trabalho e empresas; pessoas próximas e o papel de cada uma; clientes e "
    "parceiros; projetos; rotina e preferências; metas; datas importantes; compromissos combinados.\n"
    "Em cada item, diga quando ficou sabendo (mês e ano), se lembrar. Não invente nem complete: se não "
    "tiver certeza, escreva (incerto). Não inclua senhas, números de documentos, dados de cartão, nem "
    "saúde ou religião de outras pessoas.")
MARCA_IMPORTACAO = re.compile(r"^\s*\**\s*MEM[ÓO]RIA\s+IMPORTADA\s*\**\s*[—–:-]+\s*([^\n]*)\n?", re.I | re.M)


class Perai(CRMMixin):
    def __init__(self, repo: Repositorio, cliente: Cliente, modelo: Modelo,
                 transcritor: Transcritor, leitor_imagem: Modelo, pasta_temporaria: Path,
                 reserva: Modelo | list[Modelo] | None = None, agenda=None):
        self.repo = repo
        self.agenda = agenda  # perai.agenda.Agenda, ou None sem Google (D-051)
        # Últimas falas de cada usuário, para entender "muda para as 11h" ou "e com o Rafael?".
        # Fica só na memória do processo: não é fonte de nada, a fonte é sempre `mensagens`.
        self._conversas: dict[str, deque] = {}
        # Briefing que pediu informações: a próxima mensagem do usuário (até 30 min) refaz o briefing.
        self._briefing_pendente: dict[str, tuple[str, datetime]] = {}
        # "Outra conta" numa ata (D-078): a próxima mensagem curta do usuário (até 10 min) é o nome.
        self._conta_pendente: dict[str, tuple[str, datetime]] = {}
        # Preços citados por resposta, para o botão "Compartilhar preços" (D-086); só na memória do processo.
        self._precos_resposta: dict[str, dict[str, list[dict]]] = {}
        self._avisos_qr: dict[str, tuple[bool, str]] = {}   # D-111: por que o QR code não serviu
        # Importação de memória de outra IA (D-071): o Telegram corta textos longos em várias mensagens;
        # as que chegam até 3 minutos depois da primeira continuam a mesma importação.
        self._importacao: dict[str, tuple[str, datetime]] = {}
        # Último briefing automático enviado (dia, o que ele mostrava): sem novidade, silêncio (D-074).
        self._ultimo_briefing: dict[str, tuple[date, frozenset]] = {}
        # Usados em ordem quando o modelo principal falha (sobrecarga, limite, fora do ar).
        self.reservas = reserva if isinstance(reserva, list) else ([reserva] if reserva else [])
        self.cliente = cliente
        self.modelo = modelo
        self.transcritor = transcritor
        self.leitor_imagem = leitor_imagem
        self.pasta_temporaria = pasta_temporaria

    # -- chamadas aos modelos, sempre registradas em chamadas_modelo ------------------

    def _conversar(self, usuario_id, tarefa, sistema, pedido, **kw):
        principal = self.leitor_imagem if tarefa in ("leitura_imagem", "precos_imagem", "proposta_imagem",
                                                     "parceria_imagem") \
            else self.modelo
        candidatos = [principal] + [m for m in self.reservas if m != principal]
        if kw.get("imagem") is not None:
            candidatos = [m for m in candidatos if m.le_imagem]
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
        aviso = self._avisos_qr.pop(usuario_id, None)
        if aviso and (aviso[0] or "🧾 Despesa" in str(resposta)):
            # D-111: a nota foi lida pela IA; diz por que o QR code não serviu.
            resposta = _anexar(resposta, "\n" + aviso[1])
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
        if entrada.tipo == "documento" and entrada.arquivo and entrada.arquivo.suffix.lower() == ".xml":
            return self._nota_xml(usuario_id, entrada)
        if entrada.tipo in ("documento", "foto", "print") and entrada.arquivo:
            lida = self._nfce_sem_ia(usuario_id, entrada)   # D-111: consulta da Sefaz ou QR code, sem IA
            if lida:
                return lida
        if entrada.arquivo and (entrada.arquivo.suffix.lower() == ".ics" or (
                entrada.tipo == "conversa_exportada" and ics.zip_tem_ics(entrada.arquivo))):
            return self._agenda_ics(usuario_id, entrada)
        if entrada.tipo == "texto" and not entrada.encaminhada:
            fonte = self._fonte_importacao(usuario_id, entrada.texto or "")
            if fonte:
                return self._importar(usuario_id, entrada, fonte)
        if (entrada.tipo == "texto" and not entrada.encaminhada and not getattr(entrada, "sem_site", False)
                and sites.links(entrada.texto) and len(sites.PADRAO_LINK.sub("", entrada.texto).strip()) <= 60):
            # Mensagem que é basicamente um link (D-062). Link no meio de um relato ("reunião às 10h,
            # link do Meet ...") segue o caminho normal.
            return self._ler_site(usuario_id, entrada)
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
        fonte = (self._fonte_importacao(usuario_id, transcricao, arquivo=True)
                 if entrada.tipo in ("documento", "conversa_exportada") and transcricao else None)
        if fonte:
            return self._importar(usuario_id, entrada, fonte, transcricao, modelo_transcricao)
        mensagem_id = self.repo.salvar_mensagem(usuario_id, NovaMensagem(
            canal=entrada.canal, tipo=entrada.tipo, texto_original=entrada.texto,
            transcricao=transcricao,
            caminho_arquivo=str(entrada.arquivo) if entrada.arquivo else None,
            data_situacao=_agora().date().isoformat(), modelo_transcricao=modelo_transcricao,
        ))
        if not texto.strip():
            return "🤔 Recebi, mas não consegui ler nada aqui. Pode mandar de novo ou escrever?", texto
        pendente_conta = self._conta_pendente.pop(usuario_id, None)
        if (pendente_conta and entrada.tipo == "texto" and _agora() - pendente_conta[1] <= timedelta(minutes=10)
                and len(texto.strip()) <= 80):
            conta_id = self._conta(usuario_id, texto.strip(), "cliente")
            return self._aplicar_conta(usuario_id, pendente_conta[0], conta_id), texto
        try:
            if entrada.resposta_a:
                texto = f"{texto}\n(Respondendo à mensagem: “{entrada.resposta_a}”)"
            if entrada.encaminhada:
                quem = "alguém" if entrada.encaminhada == "alguém" else entrada.encaminhada
                texto = f"Mensagem encaminhada (de {quem}):\n{texto}"
            # Pergunta é só o que o próprio usuário escreve ou fala, curto e sem legenda. Áudio com legenda,
            # áudio longo ou mensagem encaminhada é registro, mesmo que termine com "?".
            pergunta_possivel = (not entrada.encaminhada and (entrada.tipo == "texto" or (
                entrada.tipo == "audio" and not entrada.texto and len(texto) < 400)))
            if pergunta_possivel and self._e_pergunta(usuario_id, texto):
                self._briefing_pendente.pop(usuario_id, None)
                return self._responder(usuario_id, texto), texto
            registro = self._registrar(usuario_id, mensagem_id, entrada.tipo, texto, entrada.arquivo)
            pendente = self._briefing_pendente.pop(usuario_id, None)
            if pendente and _agora() - pendente[1] <= BRIEFING_JANELA:
                # O usuário respondeu ao que o briefing pediu: monta o briefing de novo, uma vez.
                briefing = self._responder(usuario_id, pendente[0], pode_pedir=False)
                return saida(f"{registro}\n\n{briefing}",
                             tuple(getattr(registro, "botoes", ())) + tuple(getattr(briefing, "botoes", ()))), texto
            return registro, texto
        except ErroModelo:
            return ("⚠️ Guardei a sua mensagem original, mas o modelo de IA não respondeu agora. "
                    "Ela continua salva; tente de novo em instantes."), texto

    # -- importar a memória de outra IA (D-071) ----------------------------------------

    def _fonte_importacao(self, usuario_id: str, texto: str, arquivo: bool = False) -> str | None:
        """"ChatGPT", "Claude", "Gemini"... se o texto é (a continuação de) uma memória importada.
        Em arquivo, a marca pode vir depois da legenda e do nome do arquivo."""
        m = MARCA_IMPORTACAO.search(texto[:2000]) if arquivo else MARCA_IMPORTACAO.match(texto)
        if m:
            fonte = m.group(1).strip(" .:*<>")[:40] or "outra IA"
            self._importacao[usuario_id] = (fonte, _agora())
            return fonte
        anterior = self._importacao.get(usuario_id)
        if anterior and _agora() - anterior[1] <= timedelta(minutes=3) and len(texto) >= 1000:
            self._importacao[usuario_id] = (anterior[0], _agora())
            return anterior[0]
        return None

    def pedido_importacao(self) -> str:
        return ("📥 Para eu começar já sabendo de você, traga o que outra IA (ChatGPT, Claude ou Gemini) "
                "aprendeu nas suas conversas:\n\n1. Copie o pedido abaixo (toque no bloco) e cole lá.\n"
                "2. Copie a resposta inteira e cole aqui. Se for muito longa, mande como arquivo .txt.\n\n"
                "```\n" + PEDIDO_IMPORTACAO + "\n```\n\n"
                "Tudo entra como “segundo o ChatGPT” (ou a IA que você usar), nunca como fato, e você pode "
                "corrigir ou apagar o que estiver errado.")

    def _importar(self, usuario_id: str, entrada: Entrada, fonte: str, lido: str | None = None,
                  modelo_transcricao: str | None = None) -> tuple[str, str]:
        """`lido`: texto tirado de um arquivo .txt (a mensagem guarda o arquivo e o texto lido)."""
        corpo = MARCA_IMPORTACAO.sub("", lido if lido is not None else entrada.texto, count=1).strip()
        mensagem_id = self.repo.salvar_mensagem(usuario_id, NovaMensagem(
            canal=entrada.canal, tipo=entrada.tipo, texto_original=entrada.texto, transcricao=lido,
            caminho_arquivo=str(entrada.arquivo) if entrada.arquivo else None,
            data_situacao=_agora().date().isoformat(), modelo_transcricao=modelo_transcricao))
        texto = (f"Legenda do usuário: memória importada do {fonte}.\nConteúdo escrito pelo {fonte} sobre o "
                 f"usuário (segundo o {fonte}; não confirmado pelo usuário). Cada registro diz que veio do "
                 f"{fonte}; o que estiver marcado como incerto fica como opinião:\n{corpo}")
        try:
            registro = self._registrar(usuario_id, mensagem_id, "documento", texto)
        except ErroModelo:
            return "⚠️ Guardei o texto, mas o modelo de IA não respondeu agora para organizar.", texto
        return saida(f"📥 Importei o que o {fonte} lembra de você. Tudo ficou como “segundo o {fonte}”: confira "
                     "e corrija o que estiver errado (“isso está errado: …”) ou pelo /web.\n" + str(registro),
                     getattr(registro, "botoes", ())), texto

    def _ler_site(self, usuario_id: str, entrada: Entrada) -> tuple[str, str]:
        """Link na mensagem (D-062): lê a página e anota o conteúdo como "segundo o site X" (D-044).
        A página lida fica guardada na própria mensagem (transcrição), que é a fonte (D-040)."""
        url = sites.links(entrada.texto)[0]
        try:
            pagina = self.ler_pagina(url)
        except sites.ErroSite as erro:
            # Sem a página, a mensagem segue o caminho normal, com o aviso na frente.
            entrada.sem_site = True
            resposta, texto = self._receber(usuario_id, entrada)
            return saida(f"🌐 Não consegui ler a página: {erro}.\n{resposta}",
                         getattr(resposta, "botoes", ())), texto
        site = urllib.parse.urlsplit(pagina.url).hostname or url
        lida = (f"Página {pagina.url} ({pagina.titulo or site}), lida em {_agora():%d/%m/%Y}. Conteúdo do site "
                f"{site}, não do usuário:\n{pagina.texto}")
        mensagem_id = self.repo.salvar_mensagem(usuario_id, NovaMensagem(
            canal=entrada.canal, tipo="texto", texto_original=entrada.texto, transcricao=lida,
            data_situacao=_agora().date().isoformat(), modelo_transcricao="leitor-site"))
        texto = f"Legenda do usuário: {entrada.texto}\n{lida}"
        try:
            registro = self._registrar(usuario_id, mensagem_id, "documento", texto)
        except ErroModelo:
            return "⚠️ Li a página e guardei, mas o modelo de IA não respondeu agora para organizar.", texto
        return saida(f"🌐 Li {pagina.titulo or site}.\n{registro}", getattr(registro, "botoes", ())), texto

    def ler_pagina(self, url: str):
        return sites.ler(url)

    def _agenda_ics(self, usuario_id: str, entrada: Entrada) -> tuple[str, str]:
        """Agenda exportada (.ics ou o .zip do Google): lida pelo código, só leitura (D-064)."""
        hoje = datetime.combine(_agora().date(), datetime.min.time())
        desde, ate = hoje - timedelta(days=1), hoje + timedelta(days=JANELA_ICS_DIAS)
        try:
            agendas = [ics.ler_ics(t, desde, ate) for t in ics.agendas_do_arquivo(entrada.arquivo)]
        except (OSError, ValueError, zipfile.BadZipFile) as erro:
            log.warning("agenda .ics ilegível: %s", str(erro)[:300])
            agendas = []
        resumo = "; ".join(f"{nome or 'agenda'}: {len(evs)} compromissos" for nome, evs in agendas)
        mensagem_id = self.repo.salvar_mensagem(usuario_id, NovaMensagem(
            canal=entrada.canal, tipo="documento", texto_original=entrada.texto,
            transcricao=f"Agenda importada ({desde:%d/%m} a {ate:%d/%m}): {resumo}" if agendas else None,
            caminho_arquivo=str(entrada.arquivo), data_situacao=_agora().date().isoformat(),
            modelo_transcricao="leitor-ics" if agendas else None))
        if not agendas:
            return "🤔 Guardei o arquivo, mas não encontrei uma agenda (.ics) que eu consiga ler.", ""
        linhas = []
        for nome, evs in agendas:
            rot = ics.rotulo(nome or (Path(entrada.nome_original or entrada.arquivo.name).stem if entrada.arquivo.suffix.lower() == ".ics" else None))
            self.repo.salvar_agenda_importada(usuario_id, mensagem_id, nome or rot, rot, evs)
            linhas.append(f"📅 Agenda {rot} importada (só leitura): {len(evs)} compromissos de "
                          f"{desde:%d/%m} a {ate:%d/%m}.")
            proximos = [e for e in evs if e.inicio >= _agora().strftime("%Y-%m-%dT%H:%M")][:3]
            linhas += [f"  💼 {_dia_hora(e.inicio)} · {e.titulo}" for e in proximos]
        linhas.append("Ela entra nas respostas (“o que tenho amanhã?”) e nos relatórios de viagem. Mande o "
                      "arquivo de novo toda semana para atualizar: vale sempre o mais recente.")
        return "\n".join(linhas), resumo

    def _nota_xml(self, usuario_id: str, entrada: Entrada) -> tuple[str, str]:
        """XML da NF-e: lido pelo código, sem IA, com valor exato (D-052)."""
        nota = despesas.ler_nfe_xml(entrada.arquivo)
        return self._nota_sem_ia(usuario_id, entrada, nota, "xml")

    def _nfce_sem_ia(self, usuario_id: str, entrada: Entrada):
        """D-111: PDF que é a consulta da Sefaz (texto) ou foto/PDF com o QR code da NFC-e. None = não é
        nota que dê para ler assim; segue para a leitura pela IA."""
        arquivo = entrada.arquivo
        if not arquivo or not arquivo.exists():
            return None
        pdf = arquivo.suffix.lower() == ".pdf"
        if pdf:
            texto = nfce.texto_do_pdf(arquivo)
            if nfce.e_consulta(texto):
                nota = nfce.ler_texto(texto)
                if nota:
                    return self._nota_sem_ia(usuario_id, entrada, nota, "consulta")
            # PDF longo e com texto que não fala de nota (proposta, contrato): nem procura QR.
            if len(texto) > 400 and not re.search(r"NFC-?e|nota fiscal|DANFE|chave de acesso", texto, re.I):
                return None
        elif arquivo.suffix.lower() not in EXTENSOES_IMAGEM:
            return None
        url = nfce.ler_qr(arquivo)
        if not url:
            # Só aparece se a IA achar uma despesa na foto (foto qualquer não ganha aviso).
            self._avisos_qr[usuario_id] = (False, "📷 Não consegui ler o QR code da nota nesta foto, então li a imagem "
                                                  "com IA. Para valores exatos, mande o QR bem nítido e reto.")
            return None
        try:
            texto = self._consulta_sefaz(url)
        except Exception as erro:   # Sefaz fora do ar, captcha, rede: a IA lê, como antes
            log.warning("consulta da NFC-e pelo QR falhou: %s", str(erro)[:200])
            self._avisos_qr[usuario_id] = (True, f"⚠️ Li o QR code, mas a consulta da Sefaz falhou ({str(erro)[:80]}); "
                                                 "li a imagem com IA.")
            return None
        nota = nfce.ler_texto(texto)
        if not nota:
            log.warning("consulta da NFC-e pelo QR: página sem itens que eu saiba ler (%s caracteres): %s",
                        len(texto), " ".join(texto.split())[:300])
            self._avisos_qr[usuario_id] = (True, "⚠️ Li o QR code e abri a consulta da Sefaz, mas a página veio num "
                                                 "formato que ainda não sei ler; li a imagem com IA.")
        if nota and not nota.chave:
            nota.chave = nfce.chave_do_qr(url)
        return self._nota_sem_ia(usuario_id, entrada, nota, "qrcode") if nota else None

    def _consulta_sefaz(self, url: str) -> str:
        """Página pública da consulta (só internet pública, limite de tamanho), seguindo até 3 redirecionamentos."""
        from perai import sites
        for _ in range(4):
            status, cabecalhos, corpo = sites._pedir(url, cabecalhos=NAVEGADOR)
            if status in (301, 302, 303, 307, 308) and cabecalhos.get("location"):
                url = urllib.parse.urljoin(url, cabecalhos["location"])
                continue
            if status != 200:
                raise OSError(f"Sefaz respondeu {status}")
            charset = re.search(r"charset=([\w-]+)", cabecalhos.get("content-type", ""))
            return nfce.html_para_texto(corpo, charset.group(1) if charset else None)
        raise OSError("redirecionamentos demais")

    def _nota_sem_ia(self, usuario_id: str, entrada: Entrada, nota, fonte: str) -> tuple[str, str]:
        """Nota lida pelo código (XML, consulta da Sefaz ou QR code): valores exatos, sem IA."""
        rotulo = {"xml": "XML", "consulta": "consulta da Sefaz", "qrcode": "QR code e consulta da Sefaz"}[fonte]
        resumo = despesas.resumo_nota(nota, rotulo) if nota else ""
        mensagem_id = self.repo.salvar_mensagem(usuario_id, NovaMensagem(
            canal=entrada.canal, tipo=entrada.tipo if entrada.tipo in ("foto", "print") else "documento",
            texto_original=entrada.texto, transcricao=resumo or None,
            caminho_arquivo=str(entrada.arquivo), data_situacao=_agora().date().isoformat(),
            modelo_transcricao={"xml": "leitor-nfe", "consulta": "leitor-nfce", "qrcode": "leitor-qrcode"}[fonte]
            if nota else None))
        if not nota:
            return "🤔 Guardei o arquivo, mas esse XML não parece uma nota fiscal eletrônica.", ""
        conteudo = f"Compra de {despesas.brl(nota.total_centavos)} em {nota.emitente}"
        ext = extracao.Extracao("1", registros=[extracao.Registro(
            "R1-1", "1", conteudo, "confirmado", "eu", nota.data, [nota.emitente], 1.0, False)])
        mapa = self.repo.salvar_extracao(usuario_id, mensagem_id, ext)
        d = extracao.Despesa(descricao=f"Compra em {nota.emitente}", valor_centavos=nota.total_centavos,
                             data=nota.data, categoria=despesas.categoria_por_itens(nota),
                             estabelecimento=nota.emitente, cnpj=nota.cnpj, forma_pagamento=nota.pagamento,
                             contexto=None, local=nota.cidade, hora=nota.hora)
        origem = {"xml": "xml", "consulta": "pdf", "qrcode": "pdf" if entrada.tipo == "documento" else entrada.tipo}[fonte]
        despesa_id = self.repo.salvar_despesa(usuario_id, mensagem_id, mapa.get("R1-1"), d, origem, nota.chave)
        if despesa_id is None:
            return self._nota_repetida(usuario_id, mensagem_id, entrada, nota, fonte), resumo
        como = {"xml": "Lida direto do XML da nota", "consulta": "Lida do texto da consulta da Sefaz",
                "qrcode": "Lida pelo QR code, na consulta da Sefaz"}[fonte]
        linhas = [_linha_despesa(d), f"📄 {como}, sem IA: valores exatos.",
                  "Se for de trabalho, diga “essa é profissional”."]
        pagos = precos.de_nota(nota)
        if pagos:   # D-084: o que você pagou entra no histórico de preços
            self.repo.salvar_precos(usuario_id, mensagem_id, "compra", pagos)
            linhas.append(f"🏷️ Guardei o preço de {len(pagos)} item(ns) para comparar com as próximas ofertas.")
        itens_linhas, botoes = self._oferecer_itens(usuario_id, despesa_id, nota.itens, nota.total_centavos,
                                                     mensagem_id)
        if not botoes:
            itens_linhas, botoes = self._pergunta_vinculo(usuario_id, despesa_id, d)
        return saida("\n".join(linhas + itens_linhas), botoes), resumo

    def falar(self, usuario_id: str, texto: str) -> bytes | None:
        """Resposta falada (/voz): OGG/Opus pronto para o Telegram, ou None (nada a falar, limite do
        dia ou falha; o texto já foi enviado de qualquer jeito)."""
        falado = voz.falavel(texto)
        if not falado or self.passou_do_teto(usuario_id) or self.modelo.fornecedor == "simulado":
            return None
        inicio = time.monotonic()
        try:
            pcm, entrada, saida_ = self.cliente.falar(voz.pedido_fala(falado), voz.modelo_voz(), voz.nome_voz())
            ogg = voz.pcm_para_ogg(pcm)
        except (ErroModelo, OSError, ValueError, KeyError) as erro:
            log.warning("não consegui gerar a voz: %s", str(erro)[:200])
            return None
        # gemini-2.5-flash-preview-tts: US$ 0,50/milhão de tokens de texto e US$ 10/milhão de tokens de áudio.
        self.repo.registrar_chamada(usuario_id, _finalidade("voz"), "google", voz.modelo_voz(), entrada, saida_, 0,
                                    entrada * 0.5 / 1_000_000 + saida_ * 10 / 1_000_000,
                                    int((time.monotonic() - inicio) * 1000))
        return ogg

    def _guardar_ofertas(self, usuario_id: str, mensagem_id: str, texto: str, arquivo=None) -> str:
        """Panfleto de ofertas (D-084): cada preço vira um item do histórico, para comparar depois.
        Foto, print ou PDF: os preços saem do próprio arquivo (o texto lido é só um resumo)."""
        itens = []
        try:
            if arquivo is not None:
                r = self._conversar(usuario_id, "precos_imagem", precos.SISTEMA_PRECOS,
                                    f"{precos.PEDIDO_ARQUIVO}\n\n{texto[:2_000]}", imagem=Path(arquivo))
                itens = precos.interpretar(r.texto)
            if not itens:
                r = self._conversar(usuario_id, "precos", precos.SISTEMA_PRECOS, texto[:40_000])
                itens = precos.interpretar(r.texto)
        except ErroModelo:
            return "🏷️ Não consegui separar os preços agora; o panfleto ficou guardado."
        hoje = _agora().date().isoformat()
        for i in itens:
            i["data"] = i["data"] or hoje
        if not itens:
            return "🏷️ Não achei preços para guardar nesse texto."
        # O mesmo encarte mandado de novo não duplica os preços.
        ja = {precos.identidade(p) for p in self.repo.precos(usuario_id, (_agora().date() - timedelta(days=60)).isoformat())}
        novos = [i for i in precos.sem_repetidos([{**i, "origem": "oferta"} for i in itens])
                 if precos.identidade(i) not in ja]
        if not novos:
            return f"🏷️ Esses {len(itens)} preço(s) já estavam guardados; não repeti."
        itens = [{k: v for k, v in i.items() if k != "origem"} for i in novos]
        self.repo.salvar_precos(usuario_id, mensagem_id, "oferta", itens)
        lojas = sorted({i["loja"] for i in itens if i.get("loja")})
        return (f"🏷️ Guardei {len(itens)} preço(s) para comparar" + (f" ({', '.join(lojas[:3])})" if lojas else "")
                + ". Pergunte, por exemplo: “o frango está barato?”")

    def _ler_arquivo(self, usuario_id, leitor_fn, entrada):
        """Foto, print ou PDF: tenta o leitor de imagem e, se falhar, as reservas que leem imagem."""
        candidatos = []
        for m in [self.leitor_imagem, self.modelo] + self.reservas:
            if m.le_imagem and m not in candidatos:
                candidatos.append(m)
        ultimo = None
        for leitor in candidatos:
            try:
                texto, custo, latencia = leitor_fn(self.cliente, leitor, entrada.arquivo, entrada.texto)
            except ErroModelo as erro:
                ultimo = erro
                log.warning("leitor %s falhou: %s", leitor.apelido, str(erro)[:300])
                self.repo.registrar_chamada(usuario_id, "leitura_imagem", leitor.fornecedor, leitor.id_api,
                                            erro=str(erro)[:500])
                continue
            self.repo.registrar_chamada(usuario_id, "leitura_imagem", leitor.fornecedor, leitor.id_api,
                                        custo_usd=custo, latencia_ms=latencia)
            return texto, texto, leitor.id_api
        raise ultimo or ErroModelo("nenhum leitor de imagem disponível")

    def _para_texto(self, usuario_id, entrada):
        if entrada.tipo == "documento" and entrada.arquivo and entrada.arquivo.suffix.lower() == ".pdf":
            return self._ler_arquivo(usuario_id, ler_pdf, entrada)
        if entrada.tipo == "documento" and entrada.arquivo and entrada.arquivo.suffix.lower() == ".csv":
            texto = (f"Legenda do usuário: {entrada.texto or ''}\nArquivo: {entrada.nome_original or entrada.arquivo.name}"
                     f"\n{propostas_mod.texto_do_arquivo(entrada.arquivo)}")
            return texto, texto, "leitor-csv"
        if entrada.tipo == "documento" and entrada.arquivo and entrada.arquivo.suffix.lower() in EXTENSOES_TEXTO:
            texto = f"Legenda do usuário: {entrada.texto or ''}\nArquivo: {entrada.nome_original or entrada.arquivo.name}\n{ler_texto(entrada.arquivo)}"
            return texto, texto, "leitor-texto"
        if entrada.tipo == "documento" and entrada.arquivo and entrada.arquivo.suffix.lower() in EXTENSOES_OFFICE:
            # Word, Excel, PowerPoint: o código tira o texto, sem IA; a extração vem depois, como sempre.
            try:
                texto = ler_office(entrada.arquivo)
            except (ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as erro:
                raise OSError(f"arquivo do Office ilegível: {erro}") from None
            texto = (f"Legenda do usuário: {entrada.texto or ''}\nArquivo: {entrada.nome_original or entrada.arquivo.name}\n{texto}")
            return texto, texto, "leitor-office"
        if entrada.tipo == "audio" and entrada.arquivo:
            dono = self.repo.nome_usuario(usuario_id) or "o usuário"
            if entrada.encaminhada:
                quem = f"Áudio encaminhado (de {entrada.encaminhada}); quem encaminhou é {dono}."
            else:
                quem = (f"Áudio gravado pelo próprio {dono}, dono desta memória: se só uma pessoa fala, é ele; "
                        "não ponha \"Pessoa 1:\" nesse caso.")
            nomes = ", ".join(self.repo.entidades_conhecidas(usuario_id)[:200])
            contexto = quem + (f"\nNomes que podem aparecer (se ouvir algo parecido, use esta grafia): {nomes}."
                               if nomes else "")
            t = self.cliente.transcrever(self.transcritor, entrada.arquivo, contexto)
            if not entrada.encaminhada and "Pessoa 2:" not in t.texto:
                # Rede de proteção: áudio do próprio dono com uma só voz não vira "Pessoa 1" (09/11).
                t.texto = re.sub(r"^\s*Pessoa 1:\s*", "", t.texto, flags=re.M)
            self.repo.registrar_chamada(usuario_id, "transcricao", self.transcritor.fornecedor,
                                        self.transcritor.id_api, segundos_audio=t.segundos_audio,
                                        custo_usd=t.custo_usd, latencia_ms=t.latencia_ms)
            # A legenda diz de quem é o áudio e do que trata ("Irajá sobre a reunião de hoje").
            texto = f"Legenda do usuário: {entrada.texto}\nÁudio: {t.texto}" if entrada.texto else t.texto
            return texto, t.texto, self.transcritor.id_api
        if entrada.tipo in ("print", "foto") and entrada.arquivo:
            return self._ler_arquivo(usuario_id, ler_imagem, entrada)
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
        # "me prepara pra reunião..." é briefing; "Preparar uma página com..." (num texto colado) não é.
        if precos.pede_ver_lista(texto):
            return True    # "Lista completa", "organiza a lista por departamento": mostrar a lista
        if (len(t) < 300 and re.match(r"(me )?prepar[ae](-me)? (para|pra|pro|a|o) ", t)) or t.startswith(("me lembra do que", "me lembra o que", "me ajuda a preparar",
                         "quanto gastei", "o que tenho", "liste ", "listar ", "lista os ", "lista as ",
                         "me mostra", "me mostre", "minha agenda", "próximos eventos", "proximos eventos",
                         "resum", "faça um resumo", "faz um resumo", "me dá um resumo", "me de um resumo")) \
                or t.endswith("?"):
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

    def _registrar(self, usuario_id, mensagem_id, tipo, texto, arquivo=None) -> str:
        ultima = next((f for f in reversed(self._conversa_recente(usuario_id)) if f.startswith("Peraí:")), "")
        if (tipo == "texto" and re.fullmatch(r"\W*(sim|n[aã]o|s|n)\W*", texto.strip().lower())
                and "?" not in ultima):
            # "sim" solto não tem o que anotar: provavelmente respondia a uma pergunta com botões.
            return ("👍 Se foi resposta a uma pergunta minha, toque no botão dela ou me diga o que você quer "
                    "que eu faça (ex.: “sim, me lembra às 19h”).")
        if tipo == "texto":
            achadas = self._cancelar_pelo_codigo(usuario_id, mensagem_id, texto)
            if achadas:
                return achadas
        anteriores, abertas = self.repo.memoria(usuario_id)
        codigos = {f"P{i}": p for i, p in enumerate(abertas[-30:], start=1)}
        conectada = self._agenda_conectada(usuario_id)
        criados = self.agenda.criados_futuros(usuario_id)[:30] if conectada else []
        codigos_ev = {f"E{i}": e for i, e in enumerate(criados, start=1)}
        recentes = _despesas_para_ajuste(self.repo.despesas(usuario_id, _agora().date() - timedelta(days=60)), texto)
        codigos_g = {f"G{i}": d for i, d in enumerate(recentes, start=1)}
        # D-114: "desfaz", "volta a do posto": as canceladas nas últimas 48 h entram como [X1], [X2]...
        if re.search(r"desfa[zç]|\bvolt|devolv|restaur|n[ãa]o era (para|pra) tirar|tirou errado|errou", texto or "", re.I):
            codigos_g.update({f"X{i}": d for i, d in enumerate(self.repo.despesas_canceladas(usuario_id), start=1)})
        ligadas = self.repo.vinculos_despesas(usuario_id, [d["id"] for d in codigos_g.values()]) if codigos_g else {}
        abertas_o = [o for o in self.repo.oportunidades(usuario_id) if o["etapa"] not in ("ganha", "perdida")][:20]
        codigos_o = {f"O{i}": o for i, o in enumerate(abertas_o, start=1)}
        codigos_c = {f"C{i}": k for i, k in enumerate(self.repo.contratos(usuario_id)[:15], start=1)}
        pedido = extracao.montar_pedido(
            "1", texto, _hoje_extenso(), tipo, self.repo.entidades_conhecidas(usuario_id),
            conversa=self._conversa_recente(usuario_id),
            pendencias_abertas=[f"[{c}] {_descrever_pendencia(p)}" for c, p in codigos.items()],
            eventos_criados=[f"[{c}] {e['titulo']} | {e['inicio'].replace('T', ' ')}" for c, e in codigos_ev.items()],
            dono=self.repo.nome_usuario(usuario_id),
            despesas_recentes=[_linha_despesa_recente(c, d, ligadas.get(d["id"], [])) for c, d in codigos_g.items()
                               if c.startswith("G")]
            + (["Despesas canceladas nas últimas 48 horas (só para desfazer):"]
               + [_linha_despesa_recente(c, d, ligadas.get(d["id"], [])) for c, d in codigos_g.items()
                  if c.startswith("X")] if any(c.startswith("X") for c in codigos_g) else []),
            oportunidades_abertas=[f"[{c}] {o['conta'] or 'sem conta'} | {o['titulo']} | {ETAPAS[o['etapa']]} | "
                                   f"{despesas.brl(o['valor_centavos'])}" for c, o in codigos_o.items()],
            contratos=[f"[{c}] {k['titulo']} | {k.get('conta') or 'sem conta'} | "
                       f"{'assinado em ' + k['assinatura'] if k.get('assinatura') else 'não assinado'} | "
                       f"{len(k.get('parcelas') or [])} pagamentos" for c, k in codigos_c.items()],
        )
        sistema = extracao.SISTEMA_EXTRACAO
        aceite = self.repo.aceite(usuario_id)
        if aceite and aceite.get("sensiveis") is not True:
            # Sem o sim para dados sensíveis (LGPD): religião e saúde não viram registro.
            sistema += SEM_SENSIVEIS
        r = self._conversar(usuario_id, "extracao", sistema, pedido)
        ext = extracao.interpretar("1", r.texto)
        if not ext.json_valido:
            return "⚠️ Guardei a mensagem como chegou, mas não consegui organizar agora. Ela não se perde."
        # Compromisso só com o horário dito na própria mensagem (o modelo às vezes tira da conversa recente).
        sem_hora = [c for c in ext.compromissos if not _hora_dita(texto, c.inicio)]
        ext.compromissos = [c for c in ext.compromissos if c not in sem_hora]
        for c in sem_hora:
            ext.duvidas.append(f"Não pus “{c.titulo}” na agenda: não vi o horário nesta mensagem. "
                               "Se for compromisso, me diga o dia e a hora.")
        proprio = not texto.startswith(("Legenda do usuário", "Mensagem encaminhada"))
        if tipo in ("texto", "audio") and proprio and len(texto) < 300 and _extracao_vazia(ext):
            if re.fullmatch(r"\W*(ok+|okay|obrigad[oa]|valeu|vlw|blz|beleza|show|top|perfeito|certo|"
                            r"boa( noite| tarde| dia)?|bom dia|tchau|👍|🙏)\W*", texto.strip().lower()):
                return "👍"
            # Nada para anotar num texto curto: provavelmente é um pedido ("resuma o dia"), não um relato.
            return self._responder(usuario_id, texto)
        if _extracao_vazia(ext):
            # Texto longo ou arquivo sem nada do usuário (ex.: instruções genéricas): não fingir que anotou.
            return ("📭 Li tudo, mas não encontrei nada seu para guardar: fatos, combinados, tarefas, "
                    "compromissos ou gastos. O original ficou salvo; se havia algo importante, me diga o quê.")
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
            nova = mudar_lista(alvo.descricao, a.incluir, a.retirar) if (a.incluir or a.retirar) else None
            a.descricao = nova if nova and nova != alvo.descricao else None
            if not (a.lembrete_em or a.prazo or a.situacao or a.descricao):
                continue
            self.repo.ajustar_pendencia(usuario_id, alvo.id, mensagem_id, a.lembrete_em, a.prazo, a.situacao,
                                        descricao=a.descricao)
            if a.situacao == "concluida":
                self._avancar(usuario_id, alvo.id)
            ajustados.append((alvo, a))
        if (ext.cancelar_eventos or ext.mudar_eventos) and not (proprio and tipo in ("texto", "audio")):
            # Texto de outra pessoa (encaminhado, arquivo, conversa exportada) nunca mexe na agenda:
            # só o próprio usuário pede para cancelar ou mudar eventos.
            ext.cancelar_eventos, ext.mudar_eventos = [], []
            bloqueado = True
        else:
            bloqueado = False
        linhas_agenda, botoes = self._aplicar_agenda(usuario_id, mensagem_id, ext, mapa, codigos_ev, conectada)
        if bloqueado:
            linhas_agenda.append("🔒 Esse conteúdo pede para cancelar ou mudar eventos, mas não veio de você. "
                                 "Não mexi na agenda; se quiser, me peça com suas palavras.")
        linhas_desp, botoes_desp = self._aplicar_despesas(usuario_id, mensagem_id, tipo, ext, mapa, codigos_g, texto)
        linhas_viag, botoes_viag = self._aplicar_viagens(usuario_id, mensagem_id, ext, texto)
        linhas_viag += self._aplicar_oportunidades(usuario_id, mensagem_id, ext, codigos_o,
                                                   proprio and tipo in ("texto", "audio"))
        linhas_viag += self._aplicar_parcerias(usuario_id, mensagem_id, ext, codigos_c,
                                               proprio and tipo in ("texto", "audio"))
        linhas_viag += self._aplicar_crm(usuario_id, mensagem_id, ext, codigos_o)   # D-095 a D-100
        linhas_extra = linhas_agenda + linhas_desp + linhas_viag
        botoes = list(botoes) + botoes_desp + botoes_viag
        if atas.parece_reuniao(tipo, texto, ext.entidades):
            botoes.append(("📄 Ata da reunião", f"at:{mensagem_id}"))   # D-078
        if tipo == "documento" and parcerias.PARECE_PARCERIA.search(texto):
            botoes.append(("🤝 Registrar parceria", f"pc:{mensagem_id}"))   # D-092
        elif tipo == "documento" and propostas_mod.PARECE_PROPOSTA.search(texto):
            botoes.append(("📑 Registrar proposta", f"pp:{mensagem_id}"))   # D-091
        visual = arquivo if arquivo is not None and Path(arquivo).suffix.lower() in EXTENSOES_VISUAIS else None
        if precos.parece_oferta(texto) or (visual is not None and precos.parece_panfleto(texto)):
            linhas_extra.append(self._guardar_ofertas(usuario_id, mensagem_id, texto, visual))   # D-084
        texto = _resumo_registro(ext, corrigidos, descartadas, ajustados)
        if linhas_extra:
            sozinho = texto == "✅ Anotado." and not ext.registros and not ext.pendencias
            texto = ("" if sozinho else texto + "\n") + "\n".join(linhas_extra)
        return saida(texto, botoes)

    # -- atas de clientes e parceiros (D-078) ------------------------------------------

    def _conta(self, usuario_id, nome: str, tipo: str) -> str:
        """Conta já conhecida com o mesmo nome (sem acento e caixa), ou uma nova."""
        alvo = atas.chave(nome)
        for c in self.repo.contas_relacionamento(usuario_id):
            if atas.chave(c["nome"]) == alvo:
                return c["id"]
        return self.repo.criar_conta_relacionamento(usuario_id, nome.strip()[:120], tipo)

    def _nome_conta(self, usuario_id, conta_id) -> dict | None:
        return next((c for c in self.repo.contas_relacionamento(usuario_id) if c["id"] == conta_id), None)

    @staticmethod
    def _botoes_ata(ata_id: str, drive: bool = True, crm: bool = True) -> list:
        return ([("💾 Salvar no Drive", f"ad:{ata_id}")] if drive else []) + \
               ([("📋 Resumo para o CRM", f"ar:{ata_id}")] if crm else []) + \
               [("✉️ Preparar retorno", f"af:{ata_id}"), ("🏷️ Trocar conta", f"ak:{ata_id}")]

    def gerar_ata(self, usuario_id: str, mensagem_id: str) -> str:
        """Monta a ata padrão da reunião contada nesta mensagem (só com o que está nela)."""
        if not _uuid(mensagem_id):
            return "Não achei essa mensagem."
        if self.passou_do_teto(usuario_id):
            return "⏸️ Cheguei ao limite de uso de hoje. A ata fica para depois da meia-noite: toque de novo amanhã."
        texto = self.repo.texto_mensagem(usuario_id, mensagem_id)
        if not texto or not texto.strip():
            return "Não achei o texto dessa mensagem."
        pedido = (f"Dono desta memória (o usuário): {self.repo.nome_usuario(usuario_id) or 'não informado'}.\n"
                  f"Hoje: {_hoje_extenso()}.\n\nTranscrição ou relato:\n{texto[:atas.MAXIMO_TEXTO]}")
        try:
            r = self._conversar(usuario_id, "ata", atas.SISTEMA_ATA, pedido)
        except ErroModelo:
            return "⚠️ O modelo de IA não respondeu agora. Toque de novo em instantes."
        ata = atas.interpretar(r.texto)
        if ata is None:
            return "⚠️ Não consegui montar a ata agora. Toque de novo em instantes."
        if not ata["data"]:
            ata["data"], ata["data_do_envio"] = _agora().date().isoformat(), True
        conta_id = self._conta(usuario_id, ata["conta"], ata["tipo"]) if ata["conta"] else None
        ata_id = self.repo.salvar_ata(usuario_id, mensagem_id, conta_id, ata)
        conta = self._nome_conta(usuario_id, conta_id) if conta_id else None
        return saida(atas.texto_telegram(ata, conta["nome"] if conta else None), self._botoes_ata(ata_id))

    def resumo_crm(self, usuario_id: str, ata_id: str) -> str:
        a = self.repo.ata(usuario_id, ata_id) if _uuid(ata_id) else None
        if not a:
            return "Não achei essa ata."
        conta = self._nome_conta(usuario_id, a["conta_id"]) if a["conta_id"] else None
        return saida(atas.texto_crm(a["conteudo"], conta["nome"] if conta else None,
                                    self.repo.nome_usuario(usuario_id), _agora().date()),
                     [] if a["drive_id"] else [("💾 Salvar no Drive", f"ad:{ata_id}")])

    def preparar_retorno(self, usuario_id: str, ata_id: str) -> str:
        """Rascunho do e-mail de retorno da reunião (D-073), só com o que está na ata. O Peraí não envia:
        o usuário copia, ajusta e manda."""
        a = self.repo.ata(usuario_id, ata_id) if _uuid(ata_id) else None
        if not a:
            return "Não achei essa ata."
        ata = a["conteudo"]
        if not (ata["combinados"] or ata["proximos_passos"] or ata["decisoes"]):
            return "✉️ A ata não tem combinados, decisões nem próximos passos para um retorno."
        if self.passou_do_teto(usuario_id):
            return "⏸️ Cheguei ao limite de uso de hoje. O retorno fica para depois da meia-noite."
        conta = self._nome_conta(usuario_id, a["conta_id"]) if a["conta_id"] else None
        pedido = (f"Usuário (quem escreve): {self.repo.nome_usuario(usuario_id) or 'não informado'}.\n"
                  f"Ata:\n{json.dumps(atas.ata_para_retorno(ata, conta['nome'] if conta else None), ensure_ascii=False)}")
        try:
            r = self._conversar(usuario_id, "retorno", atas.SISTEMA_RETORNO, pedido)
        except ErroModelo:
            return "⚠️ O modelo de IA não respondeu agora. Toque de novo em instantes."
        retorno = atas.interpretar_retorno(r.texto)
        if not retorno:
            return "⚠️ Não consegui preparar o retorno agora. Toque de novo em instantes."
        whatsapp, assunto, corpo = retorno
        partes = ["✉️ Retorno da reunião", "Confira e ajuste antes de mandar: o Peraí não envia nada. "
                  "Toque num bloco para copiar.", ""]
        if whatsapp:
            partes += ["💬 WhatsApp, para mandar agora:", "```", whatsapp, "```", ""]
        partes += ["📧 E-mail de acompanhamento:", f"Assunto: {assunto}", "```", corpo, "```"]
        return "\n".join(partes)

    def salvar_ata_drive(self, usuario_id: str, ata_id: str) -> str:
        """Ata como Google Doc na pasta da conta e documento mestre refeito (D-078)."""
        a = self.repo.ata(usuario_id, ata_id) if _uuid(ata_id) else None
        if not a:
            return "Não achei essa ata."
        if not (self.agenda and self.agenda.disponivel):
            return "O Google ainda não está configurado neste Peraí."
        if not self.agenda.conta(usuario_id):
            return "📁 Para salvar no Drive, conecte primeiro a sua conta Google com /agenda."
        if not self.agenda.tem_drive(usuario_id):
            link = self.agenda.link_conexao(usuario_id)
            return saida("📁 Falta autorizar o Peraí a criar arquivos no seu Drive. Ele só vê e muda o que ele "
                         f"mesmo criar, nada mais do seu Drive.\nAbra este link (vale 15 minutos e um uso):\n{link}"
                         "\n\nDepois, toque de novo em Salvar no Drive.", [("💾 Salvar no Drive", f"ad:{ata_id}")])
        conta = self._nome_conta(usuario_id, a["conta_id"]) if a["conta_id"] else None
        if not conta:
            return saida("🏷️ Antes de salvar, me diga de qual cliente ou parceiro é esta ata.",
                         [("🏷️ Escolher conta", f"ak:{ata_id}")])
        ata = a["conteudo"]
        pastas = ["Clientes e parceiros", conta["nome"]]
        fonte = f"mensagem de {a['criado_em']:%d/%m/%Y} no Peraí" if a.get("criado_em") else "mensagem no Peraí"
        try:
            doc_id, link, pasta = self.agenda.salvar_doc(
                usuario_id, pastas, f"{ata['data']} — {ata['assunto']}"[:120],
                atas.html_ata(ata, conta["nome"], fonte), conta["pasta_drive_id"], a["drive_id"])
            self.repo.ata_no_drive(usuario_id, ata_id, doc_id, link)
            pasta = pasta or conta["pasta_drive_id"]
            _, abertas = self.repo.memoria(usuario_id)
            alvo = atas.chave(conta["nome"])
            pendencias = [_descrever_pendencia(p) for p in abertas
                          if alvo and alvo in atas.chave(f"{p.descricao} {p.quem or ''}")]
            mestre = atas.html_mestre(conta, self.repo.atas_da_conta(usuario_id, conta["id"]), pendencias,
                                      _agora().strftime("%d/%m/%Y %H:%M"))
            mestre_id, mestre_link, pasta = self.agenda.salvar_doc(
                usuario_id, pastas, f"{conta['nome']} — Documento mestre", mestre, pasta, conta["mestre_drive_id"])
            self.repo.conta_no_drive(usuario_id, conta["id"], pasta or conta["pasta_drive_id"], mestre_id, mestre_link)
        except (ErroGoogle, OSError, KeyError, ValueError) as erro:
            log.warning("não consegui salvar a ata no Drive: %s", str(erro)[:300])
            return saida("⚠️ Não consegui salvar no Drive agora. A ata continua guardada; toque de novo em instantes.",
                         [("💾 Salvar no Drive", f"ad:{ata_id}")])
        return saida(f"💾 Salvo no Drive, em Peraí › Clientes e parceiros › {conta['nome']}:\n"
                     f"• Ata: {link}\n• Documento mestre: {mestre_link}", [("📋 Resumo para o CRM", f"ar:{ata_id}")])

    def trocar_conta_ata(self, usuario_id: str, ata_id: str) -> str:
        if not (_uuid(ata_id) and self.repo.ata(usuario_id, ata_id)):
            return "Não achei essa ata."
        contas = self.repo.contas_relacionamento(usuario_id)[:12]
        botoes = [(_curto(c["nome"], 30), f"aj:{ata_id}:{n}") for n, c in enumerate(contas)]
        return saida("🏷️ De qual cliente ou parceiro é esta ata?", botoes + [("✏️ Outra", f"aj:{ata_id}:o")])

    def escolher_conta_ata(self, usuario_id: str, alvo: str) -> str:
        ata_id, _, n = alvo.rpartition(":")
        if not (_uuid(ata_id) and self.repo.ata(usuario_id, ata_id)):
            return "Não achei essa ata."
        if n == "o":
            self._conta_pendente[usuario_id] = (ata_id, _agora())
            return "✏️ Me diga, na próxima mensagem, o nome do cliente ou parceiro."
        contas = self.repo.contas_relacionamento(usuario_id)[:12]
        if not n.isdigit() or int(n) >= len(contas):
            return "Essa opção mudou. Toque de novo em Trocar conta."
        return self._aplicar_conta(usuario_id, ata_id, contas[int(n)]["id"])

    def _aplicar_conta(self, usuario_id: str, ata_id: str, conta_id: str) -> str:
        antes = self.repo.ata(usuario_id, ata_id)
        if not antes:
            return "Não achei essa ata."
        self.repo.trocar_conta_ata(usuario_id, ata_id, conta_id)
        conta = self._nome_conta(usuario_id, conta_id)
        aviso = ("\nO documento que já estava no Drive ficou na pasta da conta anterior; salve de novo para "
                 "criar na pasta certa." if antes["drive_id"] else "")
        return saida(f"🏷️ Pronto: a ata agora é de {conta['nome'] if conta else 'outra conta'}.{aviso}",
                     self._botoes_ata(ata_id)[:2])

    def _aplicar_despesas(self, usuario_id, mensagem_id, tipo, ext, mapa, codigos_g, texto=None):
        linhas, botoes = [], []
        registro_id = next((v for k, v in mapa.items() if k.startswith("R")), None)
        origem = {"documento": "pdf"}.get(tipo, tipo)
        salvas = []
        for d in ext.despesas:
            if d.data is None:
                d.data = _agora().date().isoformat()
            despesa_id = self.repo.salvar_despesa(usuario_id, mensagem_id, registro_id, d, origem, d.chave_nfe)
            if despesa_id is None and d.chave_nfe:   # a mesma NFC-e mandada de novo (D-109)
                linhas.append(f"🧾 Essa nota ({d.estabelecimento or d.descricao}, {despesas.brl(d.valor_centavos)}) "
                              "já estava registrada.")
                continue
            linhas.append(_linha_despesa(d))
            # Nota de farmácia entra também: o fundador decidiu registrar dados de saúde (D-110).
            pagos = precos.de_detalhes(d.detalhes, d.estabelecimento, d.data) if despesa_id else []
            if pagos:   # D-109: o preço pago por item entra no histórico, como no XML
                self.repo.salvar_precos(usuario_id, mensagem_id, "compra", pagos)
                linhas.append(f"🏷️ Guardei o preço de {len(pagos)} item(ns) para comparar com as ofertas.")
            if d.valor_centavos is None:
                linhas.append("❓ Não consegui ver o valor. Quanto foi?")
            if despesa_id:
                for cliente in d.clientes:
                    self.repo.vincular_despesa(usuario_id, despesa_id, "cliente", cliente=cliente)
                if d.clientes:
                    linhas.append("🤝 Cliente: " + ", ".join(d.clientes))
                linhas += self._aplicar_partes(usuario_id, despesa_id, d.partes)
                linhas += self._ligar_projeto(usuario_id, despesa_id, d.projeto)
                salvas.append((despesa_id, d))
        if any(d.contexto == "profissional" for d in ext.despesas):   # reembolso da L8 continua no Onfly
            linhas.append("💼 Se for reembolso da L8, lance também no Onfly (/viagem monta o relatório).")
        # Pergunta a qual compromisso a despesa se liga (para o relatório de viagem no Onfly).
        if len(salvas) == 1:
            despesa_id, d = salvas[0]
            pergunta, opcoes = self._oferecer_itens(usuario_id, despesa_id, d.itens, d.valor_centavos,
                                                     mensagem_id)
            if not opcoes:
                pergunta, opcoes = self._pergunta_vinculo(usuario_id, despesa_id, d)
            linhas += pergunta
            botoes += opcoes
        recusadas = 0
        for a in ext.ajustes_despesas:
            alvo = codigos_g.get(a.despesa)
            if alvo is None:
                continue
            if a.reativar:
                if self._confere_alvo(usuario_id, texto, alvo):
                    self.repo.mudar_despesa(usuario_id, alvo["id"], mensagem_id, situacao="ativa")
                    linhas.append(f"↩️ Voltou a despesa: {alvo.get('estabelecimento') or alvo['descricao']} "
                                  f"({despesas.brl(alvo['valor_centavos'])}).")
                continue
            if a.despesa.startswith("X"):
                continue   # cancelada só serve para voltar
            if a.cancelar:
                # D-114: o código confere a escolha da IA com o que o usuário disse (nome e valor).
                if not self._confere_alvo(usuario_id, texto, alvo):
                    recusadas += 1
                    continue
                self.repo.mudar_despesa(usuario_id, alvo["id"], mensagem_id, situacao="cancelada")
                linhas.append(f"🗑️ Tirei a despesa: {alvo.get('estabelecimento') or alvo['descricao']} "
                              f"({despesas.brl(alvo['valor_centavos'])}).")
                if len([b for b in botoes if b[1].startswith("dd:")]) < 3:
                    botoes.append((f"↩️ Desfazer: {(alvo.get('estabelecimento') or alvo['descricao'])[:24]}",
                                   f"dd:{alvo['id']}"))
                continue
            for cliente in a.clientes:
                self.repo.vincular_despesa(usuario_id, alvo["id"], "cliente", cliente=cliente)
            if a.clientes:
                linhas.append(f"🤝 Liguei “{alvo['descricao']}” a: " + ", ".join(a.clientes) + ".")
            linhas += self._aplicar_partes(usuario_id, alvo["id"], a.partes)
            linhas += self._ligar_projeto(usuario_id, alvo["id"], a.projeto)
            campos = {k: v for k, v in (("valor_centavos", a.valor_centavos), ("categoria", a.categoria),
                                         ("data", a.data), ("contexto", a.contexto))
                      if v is not None and v != alvo.get(k)}
            if not campos:
                continue
            self.repo.mudar_despesa(usuario_id, alvo["id"], mensagem_id, **campos)
            novo = {**alvo, **campos}
            linhas.append("✏️ Corrigi a despesa: " + _linha_despesa(extracao.Despesa(
                novo["descricao"], novo["valor_centavos"], novo["data"], novo["categoria"],
                novo.get("estabelecimento"), None, None, novo.get("contexto"))).removeprefix("🧾 Despesa: "))
        if recusadas:
            linhas.append("🔎 Não achei despesa que bata com o que você disse (lugar ou valor), então não tirei "
                          "nenhuma. Diga o lugar e o valor como estão na nota.")
        return linhas, botoes

    def _nota_repetida(self, usuario_id, mensagem_id, entrada, nota, fonte):
        """D-116: a chave já está numa despesa ativa — mostra qual é e oferece trocar pela leitura exata."""
        velha = self.repo.despesa_por_chave(usuario_id, nota.chave) if nota.chave else None
        if not velha:
            return f"🧾 Essa nota ({nota.emitente}, {despesas.brl(nota.total_centavos)}) já estava registrada."
        como = {"xml": "pelo XML", "pdf": "pelo PDF", "foto": "pela foto", "print": "pela imagem",
                "texto": "pelo texto"}.get(velha.get("origem"), "antes")
        linhas = [f"🧾 Essa nota já estava registrada (lançada {como}): "
                  f"{velha.get('estabelecimento') or velha['descricao']} · {despesas.data_br(velha['data'])}"
                  f"{' ' + velha['hora'] if velha.get('hora') else ''} · {despesas.brl(velha['valor_centavos'])}."]
        if velha["valor_centavos"] != nota.total_centavos:
            linhas.append(f"A leitura exata da nota dá {despesas.brl(nota.total_centavos)}.")
        igual = (velha["valor_centavos"] == nota.total_centavos and velha.get("hora") == nota.hora
                 and _sem_acento(velha.get("estabelecimento") or "").lower() == _sem_acento(nota.emitente).lower())
        if igual:   # a mesma leitura (ou a IA acertou tudo): não há o que trocar
            return linhas[0]
        self.repo.definir_config(usuario_id, "trocar_nota", {"despesa_id": velha["id"], "mensagem_id": mensagem_id,
                                                             "fonte": fonte, "tipo": entrada.tipo})
        linhas.append("Quer trocar a que está pela leitura exata (itens, hora e preços da própria nota)?")
        return saida("\n".join(linhas), [("🔄 Trocar pela leitura exata", f"tq:{velha['id']}"),
                                          ("Manter a que está", "tq:0")])

    def trocar_nota(self, usuario_id: str, alvo: str):
        """Botão "tq:<despesa>": cancela a despesa antiga e grava a nota lida de novo pelo código (D-116)."""
        if alvo == "0":
            return "👍 Mantive a que estava."
        prop = self.repo.config(usuario_id, "trocar_nota") or {}
        velha = self.repo.despesa(usuario_id, alvo) if prop.get("despesa_id") == alvo else None
        arquivo = self.repo.arquivo_mensagem(usuario_id, prop.get("mensagem_id")) if velha else None
        if not velha or velha["situacao"] != "ativa" or not arquivo or not Path(arquivo).exists():
            return "Essa troca não está mais disponível. Mande a nota de novo."
        caminho, fonte = Path(arquivo), prop.get("fonte")
        try:
            if fonte == "xml":
                nota = despesas.ler_nfe_xml(caminho)
            elif fonte == "consulta":
                nota = nfce.ler_texto(nfce.texto_do_pdf(caminho))
            else:
                url = nfce.ler_qr(caminho)
                nota = nfce.ler_texto(self._consulta_sefaz(url)) if url else None
                if nota and not nota.chave:
                    nota.chave = nfce.chave_do_qr(url)
        except Exception as erro:
            log.warning("troca de nota: não consegui ler de novo: %s", str(erro)[:200])
            nota = None
        if not nota:
            return "⚠️ Não consegui ler a nota de novo agora (a Sefaz pode estar fora). Nada mudou; tente mais tarde."
        self.repo.mudar_despesa(usuario_id, velha["id"], prop.get("mensagem_id"), situacao="cancelada")
        self.repo.definir_config(usuario_id, "trocar_nota", {})
        nova = self._nota_sem_ia(usuario_id, Entrada("telegram", prop.get("tipo") or "documento", None, caminho),
                                 nota, fonte)[0]
        aviso = (f"🔄 Troquei: a despesa antiga ({despesas.brl(velha['valor_centavos'])}) saiu e entrou a leitura exata. "
                 "Se quiser a antiga de volta, diga “desfaz”.\n")
        return _anexar(saida(aviso), str(nova)) if not getattr(nova, "botoes", None) else \
            saida(aviso + str(nova), nova.botoes)

    def _confere_alvo(self, usuario_id, texto: str | None, alvo: dict) -> bool:
        """D-114: a despesa escolhida pela IA tem de bater com o que o usuário disse. Valor citado → o mesmo
        valor; nome próprio citado (palavra com maiúscula fora do início) → o nome na despesa, na cidade
        ou no cliente ligado. Sem valor nem nome citados ("desfaz", "tira a última"), vale a escolha."""
        texto = texto or ""
        valores = {int(re.sub(r"\D", "", v)) for v in re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", texto)}
        if valores and alvo.get("valor_centavos") not in valores:
            return False
        palavras = re.findall(r"[A-Za-zÀ-ÿ]+", texto)
        nomes = {_sem_acento(w).lower() for i, w in enumerate(palavras)
                 if i > 0 and w[:1].isupper() and len(w) >= 3 and w.lower() not in ("r", "rs")}
        if not nomes:
            return True
        ligadas = self.repo.vinculos_despesas(usuario_id, [alvo["id"]]).get(alvo["id"], [])
        onde = _sem_acento(" ".join([alvo.get("estabelecimento") or "", alvo.get("descricao") or "",
                                     alvo.get("local") or ""] + [v.get("cliente") or v.get("titulo") or ""
                                                                  for v in ligadas])).lower()
        return any(n[:5] in onde for n in nomes)

    def _cancelar_pelo_codigo(self, usuario_id, mensagem_id, texto: str):
        """D-115: "lancei errado as notas do Gnann de R$ 173,80" — o código acha as despesas que batem em
        tudo o que foi citado (valor e nome) e oferece botões; nada sai sem o toque. None = segue para a IA."""
        t = texto or ""
        if not re.search(r"lan[cç](ei|ou|amento) errad|\bcancel|\bapag|\bexclu|\bremov|\btir[ae]r?\b", t, re.I):
            return None
        if re.search(r"desfa[zç]|\bvolt|devolv|restaur|n[ãa]o era (para|pra) tirar", t, re.I):
            return None   # é para devolver, não para tirar (D-114)
        tem_valor = bool(re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}", t))
        palavras = re.findall(r"[A-Za-zÀ-ÿ]+", t)
        tem_nome = any(i > 0 and w[:1].isupper() and len(w) >= 3 for i, w in enumerate(palavras))
        if not (tem_valor or tem_nome):
            return None
        if not re.search(r"lan[cç](ei|ou|amento) errad|nota|despesa|gasto|compra|cupom|R\$", t, re.I):
            return None   # "tira o detergente" é da lista de compras, não despesa
        candidatas = [d for d in self.repo.despesas(usuario_id, _agora().date() - timedelta(days=60))
                      if self._confere_alvo(usuario_id, t, d)]
        if not candidatas:
            return None
        candidatas = candidatas[:5]
        self.repo.definir_config(usuario_id, "cancelar_despesas",
                                 {"ids": [d["id"] for d in candidatas], "mensagem_id": mensagem_id})
        nome = lambda d: (d.get("estabelecimento") or d["descricao"])
        linhas = ["🔎 Achei " + ("esta despesa:" if len(candidatas) == 1 else f"{len(candidatas)} despesas que batem:")]
        linhas += [f"  • {nome(d)} · {despesas.data_br(d['data'])}{' ' + d['hora'] if d.get('hora') else ''} · "
                   f"{despesas.brl(d['valor_centavos'])}" for d in candidatas]
        linhas.append("Toque para tirar. Nada sai sem o seu toque.")
        botoes = [(f"🗑️ Tirar: {nome(d)[:20]} {despesas.data_br(d['data'])} {despesas.brl(d['valor_centavos'])}",
                   f"dx:{d['id']}") for d in candidatas]
        if len(candidatas) > 1:
            botoes.append((f"🗑️ Tirar todas ({len(candidatas)})", "dx:todas"))
        return saida("\n".join(linhas), botoes)

    def cancelar_despesa_botao(self, usuario_id: str, alvo: str):
        """Botão "dx:<despesa>" ou "dx:todas" da lista do D-115."""
        prop = self.repo.config(usuario_id, "cancelar_despesas") or {}
        ids = prop.get("ids") or []
        escolhidas = ids if alvo == "todas" else [alvo] if alvo in ids else []
        linhas, botoes = [], []
        for i in escolhidas:
            d = self.repo.despesa(usuario_id, i)
            if not d or d["situacao"] != "ativa":
                continue
            self.repo.mudar_despesa(usuario_id, i, prop.get("mensagem_id"), situacao="cancelada")
            linhas.append(f"🗑️ Tirei a despesa: {d.get('estabelecimento') or d['descricao']} "
                          f"({despesas.brl(d['valor_centavos'])}).")
            botoes.append((f"↩️ Desfazer: {(d.get('estabelecimento') or d['descricao'])[:24]}", f"dd:{i}"))
        if not linhas:
            return "Essa despesa já tinha saído (ou a lista é antiga). Peça de novo, se precisar."
        return saida("\n".join(linhas), botoes[:5])

    def desfazer_despesa(self, usuario_id: str, despesa_id: str) -> str:
        """Botão "dd:<despesa>": devolve a despesa cancelada (D-114)."""
        d = self.repo.despesa(usuario_id, despesa_id) if _uuid(despesa_id) else None
        if not d:
            return "Não encontrei essa despesa."
        if d["situacao"] != "cancelada":
            return "Essa despesa já está de volta."
        self.repo.mudar_despesa(usuario_id, despesa_id, None, situacao="ativa")
        return f"↩️ Voltou a despesa: {d.get('estabelecimento') or d['descricao']} ({despesas.brl(d['valor_centavos'])})."

    def _aplicar_agenda(self, usuario_id, mensagem_id, ext, mapa, codigos_ev, conectada):
        """Cria os compromissos na agenda e aplica mudanças nos eventos que o Peraí criou."""
        linhas, botoes = [], []
        if ext.cancelar_eventos or ext.mudar_eventos:
            if conectada:
                linhas_c, cancelou = self._cancelar_eventos(usuario_id, mensagem_id, ext.cancelar_eventos)
                linhas_m, mudou = self._mudar_eventos(usuario_id, mensagem_id, ext.mudar_eventos)
                linhas += linhas_c + linhas_m
                if (cancelou or mudou) and mensagem_id:
                    rotulo = ("↩️ Desfazer" if cancelou and mudou else
                              "↩️ Desfazer cancelamento" if cancelou else "↩️ Desfazer mudança")
                    botoes.append((rotulo, f"dc:{mensagem_id}"))
                if getattr(self, "_serie_pendente", False) and mensagem_id:
                    botoes.append(("🔁 Mudar a série toda", f"ms:{mensagem_id}"))
            elif self.agenda and self.agenda.disponivel:
                linhas.append("📅 Para eu mexer na sua agenda, conecte com /agenda.")
            else:
                linhas.append("📅 Ainda não estou ligado à sua agenda, então não consigo cancelar eventos.")
        if ext.compromissos and not conectada:
            if self.agenda and self.agenda.disponivel:
                linhas.append("📅 Isso parece um compromisso. Para eu colocar na sua agenda, use /agenda.")
            return linhas, botoes
        registro_id = next((v for k, v in mapa.items() if k.startswith("R")), None)
        for c in ext.compromissos:
            regra = None
            if c.repeticao:   # D-058: evento que se repete
                inicio = recorrencia.primeira_vez(c.repeticao, c.inicio)
                if c.fim:
                    c.fim = (datetime.fromisoformat(c.fim) + (datetime.fromisoformat(inicio)
                             - datetime.fromisoformat(c.inicio))).strftime("%Y-%m-%dT%H:%M")
                c.inicio = inicio
                regra = [recorrencia.rrule(c.repeticao, c.repeticao_ate)]
            try:
                e = self.agenda.criar(usuario_id, mensagem_id, registro_id, c.titulo, c.inicio, c.fim, c.local,
                                      regra) if regra else \
                    self.agenda.criar(usuario_id, mensagem_id, registro_id, c.titulo, c.inicio, c.fim, c.local)
            except Exception as erro:  # a anotação já está salva; só a agenda falhou
                log.warning("não consegui criar o evento: %s", str(erro)[:300])
                linhas.append(f"⚠️ Anotei, mas não consegui colocar “{c.titulo}” na agenda agora.")
                continue
            if regra:
                vezes = ", ".join(d.strftime("%d/%m") for d in recorrencia.proximas(c.repeticao, c.inicio, 3,
                                                                                    c.repeticao_ate))
                linhas.append(f"🔁 Coloquei na sua agenda: {e['titulo']}, {recorrencia.por_extenso(c.repeticao)} "
                              f"às {c.inicio[11:16]}" + (f" até {_dia_mes(c.repeticao_ate)}" if c.repeticao_ate else "")
                              + f" (próximas: {vezes})" + (f" · {e['local']}" if e.get("local") else ""))
            else:
                linhas.append(f"📅 Coloquei na sua agenda: {e['titulo']}, {_quando(e['inicio'], e['fim'])}"
                              + (f" · {e['local']}" if e.get("local") else ""))
            botoes.append((f"↩️ Desfazer: {e['titulo'][:30]}", f"desfazer:{e['id']}"))
        for a in ext.ajustes_eventos:
            alvo = codigos_ev.get(a.evento)
            if alvo is None:
                continue
            try:
                e = self.agenda.ajustar(usuario_id, alvo["id"], mensagem_id, a.inicio, a.fim, a.cancelar,
                                        a.titulo)
            except Exception as erro:
                log.warning("não consegui alterar o evento: %s", str(erro)[:300])
                linhas.append(f"⚠️ Não consegui mudar “{alvo['titulo']}” na agenda agora.")
                continue
            if e is None:
                continue
            if a.cancelar:
                linhas.append(f"🗑️ Tirei da agenda: {alvo['titulo']}.")
            else:
                if a.titulo and a.titulo != alvo["titulo"]:
                    linhas.append(f"✏️ Renomeei na agenda: “{alvo['titulo']}” → “{a.titulo}”.")
                if a.inicio or a.fim:
                    linhas.append(f"📅 Mudei na agenda: {e['titulo']}, {_quando(e['inicio'], e['fim'])}.")
        return linhas, botoes

    def _cancelar_eventos(self, usuario_id, mensagem_id, pedidos):
        """Eventos que o Peraí não criou (D-059): apaga direto, avisa e oferece Desfazer."""
        linhas, algum = [], False
        for c in pedidos:
            try:
                feitos = self.agenda.cancelar_eventos(usuario_id, mensagem_id, c.busca, c.desde, c.ate)
            except Exception as erro:
                log.warning("não consegui cancelar eventos: %s", str(erro)[:300])
                linhas.append(f"⚠️ Não consegui cancelar “{c.busca}” na agenda agora. Nada foi apagado "
                              "depois do erro; tente de novo em instantes.")
                continue
            if not feitos:
                periodo = ""
                if c.desde:
                    periodo = f" em {_dia_mes(c.desde)}" if c.desde == c.ate else f" a partir de {_dia_mes(c.desde)}"
                linhas.append(f"🔎 Não encontrei na agenda eventos com “{c.busca}”{periodo}. Nada foi cancelado.")
                continue
            algum = True
            avulsos = [f for f in feitos if f.get("titulo") and not f["serie"]]
            for f in feitos:
                if f.get("serie"):
                    linhas.append(f"🗑️ Cancelei na agenda: {f['titulo']}, todas as vezes a partir de "
                                  f"{_dia_hora(f['inicio'])}.")
            for f in avulsos[:8]:
                linhas.append(f"🗑️ Tirei da agenda: {f['titulo']} · {_dia_hora(f['inicio'])}")
            if len(avulsos) > 8:
                linhas.append(f"🗑️ … e mais {len(avulsos) - 8} vezes.")
            resto = next((f["restantes"] for f in feitos if f.get("restantes")), 0)
            if resto:
                linhas.append(f"⚠️ Parei por segurança: ainda há {resto} na agenda. Se quiser, peça de novo.")
        return linhas, algum

    def _mudar_eventos(self, usuario_id, mensagem_id, pedidos):
        """Muda horário ou nome de eventos que o Peraí não criou (D-060): a próxima vez; em evento que se
        repete, "todas" leva a mudança para a série, e sem isso o botão oferece (D-058)."""
        linhas, algum = [], False
        self._serie_pendente = False
        for m in pedidos:
            try:
                r = self.agenda.mudar_evento(usuario_id, mensagem_id, m.busca, m.dia, m.novo_dia, m.nova_hora,
                                             m.novo_titulo)
            except Exception as erro:
                log.warning("não consegui mudar o evento: %s", str(erro)[:300])
                linhas.append(f"⚠️ Não consegui mudar “{m.busca}” na agenda agora. Se o evento foi criado por "
                              "outra pessoa, só ela consegue mudar o horário.")
                continue
            if not r or not r.get("achou"):
                quando = f" em {_dia_mes(m.dia)}" if m.dia else " nos próximos 15 dias"
                linhas.append(f"🔎 Não encontrei na agenda “{m.busca}”{quando}. Nada foi mudado.")
                continue
            if r.get("igual"):
                linhas.append(f"👍 “{r['titulo']}” já estava assim na agenda.")
                continue
            if r.get("dia_inteiro") and not r.get("titulo_antigo"):
                linhas.append(f"⚠️ “{r['titulo']}” é um evento de dia inteiro; não mudei o horário.")
                continue
            algum = True
            if r["titulo"] != r["titulo_antigo"]:
                linhas.append(f"✏️ Renomeei na agenda: “{r['titulo_antigo']}” → “{r['titulo']}”.")
            if r["mudou_hora"]:
                linhas.append(f"📅 Mudei na agenda: {r['titulo']}, {_quando(r['inicio'], r['fim'])}.")
            if r["recorrente"]:
                if m.todas and mensagem_id:
                    linhas += self._linhas_serie(self.agenda.mudar_serie(usuario_id, mensagem_id))
                else:
                    linhas.append("🔁 Só esta vez; as outras continuam como estavam.")
                    self._serie_pendente = True
        return linhas, algum

    @staticmethod
    def _linhas_serie(feitos: list[dict]) -> list[str]:
        linhas = []
        for f in feitos:
            if f.get("mudou_dia"):
                linhas.append(f"⚠️ “{f['titulo']}”: mudei só esta vez. Trocar o dia da série inteira ainda não "
                              "consigo; dá para mudar no Google Agenda.")
            elif f.get("igual"):
                linhas.append(f"👍 A série “{f['titulo']}” já estava assim.")
            else:
                linhas.append(f"🔁 Mudei a série toda: {f['titulo']}, das {f['hora']} às {f['fim']}, em todas as vezes.")
        return linhas

    def compartilhar_precos(self, usuario_id: str, resposta_id: str) -> str:
        """Botão "cp:<resposta>" (D-086): os preços citados num texto pronto para o WhatsApp. O Peraí não
        envia nada; a pessoa copia e encaminha."""
        itens = self._precos_resposta.get(usuario_id, {}).get(resposta_id)
        if not itens:
            return "📤 Esse botão expirou. Pergunte de novo pelos preços e toque em Compartilhar na resposta nova."
        return "\n".join(["📤 Pronto para o WhatsApp (toque no bloco para copiar):", "```",
                          precos.texto_compartilhar(itens), "```"])

    def mudar_serie(self, usuario_id: str, mensagem_id: str) -> str:
        """Botão "ms:<mensagem>": leva para a série a mudança feita numa vez (D-058)."""
        if not self.agenda or not _uuid(mensagem_id):
            return "Não encontrei essa mudança."
        try:
            feitos = self.agenda.mudar_serie(usuario_id, mensagem_id)
        except Exception as erro:
            log.warning("não consegui mudar a série: %s", str(erro)[:300])
            return "⚠️ Não consegui mudar a série agora. Esta vez continua mudada; tente de novo em instantes."
        if not feitos:
            return "Não encontrei a série dessa mudança (talvez já tenha sido desfeita)."
        return saida("\n".join(self._linhas_serie(feitos)), [("↩️ Desfazer tudo", f"dc:{mensagem_id}")])

    def desfazer_cancelamento(self, usuario_id: str, mensagem_id: str) -> str:
        """Botão "dc:<mensagem>": devolve à agenda o que aquele pedido cancelou."""
        if not self.agenda or not _uuid(mensagem_id):
            return "Não encontrei esse cancelamento."
        try:
            voltaram = self.agenda.desfazer_cancelamentos(usuario_id, mensagem_id)
        except Exception as erro:
            log.warning("não consegui desfazer o cancelamento: %s", str(erro)[:300])
            return ("⚠️ Não consegui devolver à agenda agora. Tente de novo em instantes; se não der, "
                    "o evento fica na lixeira do Google Agenda por 30 dias e dá para restaurar por lá.")
        if not voltaram:
            return "Isso já tinha voltado para a agenda."
        titulos = list(dict.fromkeys(x["titulo"] for x in voltaram))
        if all(x["tipo"] == "alterado" for x in voltaram):
            return "↩️ Voltou como estava na agenda: " + ", ".join(titulos) + "."
        return "↩️ Voltou para a agenda: " + ", ".join(titulos) + "."

    # -- despesa ligada a compromisso ou cliente (relatório de viagem) ---------------------

    def _pergunta_vinculo(self, usuario_id, despesa_id, d) -> tuple[list[str], list]:
        """Depois da nota (D-112): a que conta, viagem ou compromisso a despesa se liga."""
        if d.contexto in ("familia", "igreja") or d.clientes:
            return [], []
        viagem = self._viagem_do_dia(usuario_id, d.data)
        aviso = [f"🧳 Entra no relatório da viagem: {viagem['nome']}"] if viagem else []
        eventos = self._eventos_da_despesa(usuario_id, d.data, getattr(d, "hora", None))
        contas = self._contas_da_despesa(usuario_id, eventos)
        botoes = [(f"🏢 {c[:34]}", f"vc:{despesa_id}:{i}") for i, c in enumerate(contas, start=1)]
        if not viagem and d.data:
            botoes.append(("🧳 Dia de viagem (vários clientes)", f"vv:{despesa_id}"))
        botoes += [(f"🔗 {e.inicio[11:16]} {e.titulo[:30]}", f"vd:{despesa_id}:{i}")
                   for i, e in enumerate(eventos, start=1)]
        if not botoes:
            return aviso + ["🔗 Foi com algum cliente? Se foi, me diga (ex.: “foi com a Zaeli”)."], []
        botoes.append(("✔️ Pronto / nenhum", f"vd:{despesa_id}:0"))
        return aviso + ["🔗 A que essa despesa se liga? Toque em um ou mais: conta (cliente), dia de viagem ou "
                        "compromisso. Outra conta, é só dizer (“foi com a Zaeli”)."], botoes

    def _viagem_do_dia(self, usuario_id, data: str | None):
        return next((v for v in self.repo.viagens(usuario_id)
                     if data and v["inicio"] <= data <= v["fim"]), None) if data else None

    def _eventos_da_despesa(self, usuario_id, data: str | None, hora: str | None) -> list:
        """Os compromissos do dia da despesa; com a hora, os mais perto dela primeiro (até 5)."""
        if not (self._agenda_conectada(usuario_id) and data):
            return []
        eventos = self.agenda.eventos_em(usuario_id, date.fromisoformat(data))
        if hora:
            minutos = lambda hm: int(hm[:2]) * 60 + int(hm[3:5])
            alvo = minutos(hora)
            eventos = sorted(eventos, key=lambda e: abs(minutos(e.inicio[11:16]) - alvo)
                             if len(e.inicio) >= 16 else 9_999)[:5]
            return sorted(eventos, key=lambda e: e.inicio)
        return eventos[:5]

    def _contas_da_despesa(self, usuario_id, eventos) -> list[str]:
        """Contas (clientes e parceiros) cujo nome aparece nos compromissos do dia (até 3)."""
        if not eventos:
            return []
        contas = [(c["nome"], contas_mod.normalizar(c["nome"])) for c in self.repo.contas_relacionamento(usuario_id)]
        achadas = []
        for e in eventos:   # na ordem dos compromissos do dia
            titulo = contas_mod.normalizar(e.titulo)
            for nome, norm in contas:
                if norm and len(norm) >= 3 and nome not in achadas and re.search(rf"\b{re.escape(norm)}\b", titulo):
                    achadas.append(nome)
        return achadas[:3]

    def vincular_despesa_conta(self, usuario_id: str, alvo: str) -> str:
        """Botão "vc:<despesa>:<n>": liga a despesa à n-ésima conta sugerida (D-112)."""
        despesa_id, _, n = alvo.partition(":")
        d = self.repo.despesa(usuario_id, despesa_id) if _uuid(despesa_id) and n.isdigit() else None
        if not d:
            return "Não encontrei essa despesa."
        contas = self._contas_da_despesa(usuario_id, self._eventos_da_despesa(usuario_id, d["data"], d.get("hora")))
        if not 1 <= int(n) <= len(contas):
            return "Essa conta não está mais nos compromissos do dia. Diga “foi com a …”."
        self.repo.vincular_despesa(usuario_id, despesa_id, "cliente", cliente=contas[int(n) - 1])
        return f"🏢 Liguei a despesa a {contas[int(n) - 1]}. Se teve outra, toque nela; senão, em Pronto."

    def viagem_do_dia(self, usuario_id: str, despesa_id: str) -> str:
        """Botão "vv:<despesa>": o dia da despesa vira uma viagem de um dia (vários clientes, D-112)."""
        d = self.repo.despesa(usuario_id, despesa_id) if _uuid(despesa_id) else None
        if not d or not d["data"]:
            return "Não encontrei essa despesa."
        existente = self._viagem_do_dia(usuario_id, d["data"])
        if existente:
            return f"🧳 Esse dia já está na viagem: {existente['nome']}."
        dia = date.fromisoformat(d["data"])
        nome = viagens.nome_padrao(dia, dia, "Visitas a clientes", [d["local"]] if d.get("local") else [], [])
        self.repo.salvar_viagem(usuario_id, d.get("mensagem_id"), nome, d["data"], d["data"],
                                [d["local"]] if d.get("local") else [], [], "visitas a clientes")
        return (f"🧳 Anotei o dia como viagem: {nome}. Todas as despesas de {dia:%d/%m} entram no relatório "
                f"(/viagem {dia:%d/%m} {dia:%d/%m}). Diga com quem foi cada uma (“o almoço foi com a Zaeli”) "
                "ou toque nas contas.")

    # -- itens da nota: quais entram no lançamento (D-061) -------------------------------

    def _oferecer_itens(self, usuario_id, despesa_id, itens, total, mensagem_id=None) -> tuple[list[str], list]:
        """Nota com 2 ou mais itens: guarda TODOS e mostra para o usuário confirmar o que entra (D-061, D-108).
        Até 12 itens, um botão por item; acima disso, botões em páginas de 10."""
        itens = [(desc, v) for desc, v in (itens or []) if desc][:MAX_ITENS_NOTA]
        if len(itens) < 2:
            return [], []
        self.repo.salvar_itens_despesa(usuario_id, despesa_id, itens, total)
        guardados = self.repo.itens_despesa(usuario_id, despesa_id)
        linhas = [f"📋 A nota tem {len(itens)} itens:"]
        largura = 40 if len(guardados) <= 12 else 28
        linhas += [f"  {i['n']}. {i['descricao'][:largura]} · {despesas.brl(i['valor_centavos'])}"
                   for i in guardados[:MAX_LINHAS_NOTA]]
        if len(guardados) > MAX_LINHAS_NOTA:   # mensagem curta; todos os itens estão nos botões (D-111)
            linhas.append(f"  … e mais {len(guardados) - MAX_LINHAS_NOTA} itens (todos aparecem nos botões).")
        desconto = _desconto_nota(guardados)
        if desconto:
            soma = sum(i["valor_centavos"] for i in guardados)
            linhas.append(f"🏷️ Soma dos itens {despesas.brl(soma)}, desconto {despesas.brl(desconto)}, "
                          f"pago {despesas.brl(total)}. Se só parte entrar, o desconto vai proporcional.")
        linhas.append("Se você lança só parte (ex.: só o combustível), toque nos itens que NÃO entram "
                      "e depois em Confirmar." + (" Os botões estão em páginas de 10." if len(guardados) > 12 else ""))
        baixa = self._propor_baixa_lista(usuario_id, despesa_id, guardados, mensagem_id)
        linhas += baixa
        return linhas, self._botoes_nota(usuario_id, despesa_id, guardados)

    def _botoes_nota(self, usuario_id, despesa_id, itens, pagina: int = 0) -> list:
        """Botões dos itens e, se houver proposta pendente, o de tirar da lista de compras (D-108)."""
        botoes = _botoes_itens(despesa_id, itens, pagina)
        prop = self.repo.config(usuario_id, "baixa_lista") or {}
        if prop.get("despesa_id") == despesa_id and prop.get("itens"):
            botoes.append((f"🛒 Tirar {len(prop['itens'])} da lista de compras", f"bl:{despesa_id}"))
        return botoes

    def escolher_item(self, usuario_id: str, alvo: str):
        """Botão "di:<despesa>:<n>": tira ou põe o item n no lançamento; n = 0 confirma; "p<k>" muda a
        página dos botões (nota com mais de 12 itens)."""
        despesa_id, _, n = alvo.partition(":")
        pagina_pedida = n[1:] if n.startswith("p") else None
        if not _uuid(despesa_id) or not (n.isdigit() or (pagina_pedida or "").isdigit()):
            return "Não encontrei essa despesa."
        d = self.repo.despesa(usuario_id, despesa_id)
        itens = self.repo.itens_despesa(usuario_id, despesa_id) if d else []
        if not itens:
            return "Não encontrei essa despesa."
        if pagina_pedida is not None:
            return saida("", self._botoes_nota(usuario_id, despesa_id, itens, int(pagina_pedida)), trocar=True)
        if n != "0":
            item = next((i for i in itens if i["n"] == int(n)), None)
            if item is None:
                return "Esse item não está na nota."
            pagina = (item["n"] - 1) // ITENS_POR_PAGINA if len(itens) > 12 else 0
            item["incluido"] = not item["incluido"]
            if not any(i["incluido"] for i in itens):
                return saida("Pelo menos um item precisa entrar. Se a despesa toda não vale, diga “lancei errado”.",
                             self._botoes_nota(usuario_id, despesa_id, itens, pagina), trocar=True)
            self.repo.marcar_item_despesa(usuario_id, despesa_id, item["n"], item["incluido"])
            novo = _valor_itens(itens)
            if novo != d["valor_centavos"]:
                self.repo.mudar_despesa(usuario_id, despesa_id, None, valor_centavos=novo)
            estado = "entra" if item["incluido"] else "não entra"
            return saida(f"{item['descricao'][:40]} {estado}. Valor: {despesas.brl(novo)}",
                         self._botoes_nota(usuario_id, despesa_id, itens, pagina), trocar=True)
        dentro = [i for i in itens if i["incluido"]]
        fora = [i for i in itens if not i["incluido"]]
        if len(dentro) == len(itens):
            quais = f"todos os {len(itens)} itens"
        elif len(dentro) <= len(fora):
            quais = f"{len(dentro)} de {len(itens)} itens: " + ", ".join(i["descricao"] for i in dentro)
        else:
            quais = f"{len(dentro)} de {len(itens)} itens (ficaram de fora: " + ", ".join(i["descricao"] for i in fora) + ")"
        desconto = _desconto_nota(itens)
        if desconto and len(dentro) < len(itens):
            quais += ", com o desconto da nota proporcional"
        texto = f"🧾 Lançamento: {despesas.brl(d['valor_centavos'])} · {quais}"
        dd = extracao.Despesa(d["descricao"], d["valor_centavos"], d["data"], d["categoria"],
                              d.get("estabelecimento"), None, None, d.get("contexto"), local=d.get("local"),
                              hora=d.get("hora"))
        pergunta, botoes = self._pergunta_vinculo(usuario_id, despesa_id, dd)
        return saida("\n".join([texto] + pergunta), botoes)

    # -- nota do mercado x lista de compras (D-108) --------------------------------------

    def _propor_baixa_lista(self, usuario_id, despesa_id, itens_nota, mensagem_id) -> list[str]:
        """A IA só liga itens da nota a itens da lista; o código propõe e só tira depois do botão."""
        if not mensagem_id:
            return []
        _, pendencias = self.repo.memoria(usuario_id)
        listas = self._listas_de_compras(pendencias)
        da_lista = [(pend.id, i) for pend in listas for i in itens_da_lista(pend.descricao)[1]]
        if not da_lista:
            return []
        codigos = {f"N{i['n']}": i["descricao"] for i in itens_nota}
        pedido = ("Itens da lista:\n" + "\n".join(f"- {i}" for _, i in da_lista) + "\n\nItens da nota:\n"
                  + "\n".join(f"[{c}] {desc}" for c, desc in codigos.items()))
        try:
            r = self._conversar(usuario_id, "consulta", precos.SISTEMA_BAIXA, pedido)
            dados = extracao.extrair_json(r.texto)
        except (ErroModelo, ValueError, TypeError):
            return []
        comprados = precos.ler_baixa(dados, [i for _, i in da_lista], codigos)
        if not comprados:
            return []
        lista_de = {i: lid for lid, i in da_lista}
        self.repo.definir_config(usuario_id, "baixa_lista", {
            "despesa_id": despesa_id, "mensagem_id": mensagem_id,
            "itens": [{"item": item, "lista": lista_de[item], "nota": nota} for item, nota in comprados]})
        return [f"🛒 Da sua lista de compras, a nota tem {len(comprados)}: "
                + ", ".join(f"{item} ({nota[:24]})" for item, nota in comprados)
                + ". Toque em “Tirar da lista” se já pode riscar."]

    def baixar_lista(self, usuario_id: str, alvo: str) -> str:
        """Botão "bl:<despesa>": tira da lista o que a nota mostra que foi comprado."""
        prop = self.repo.config(usuario_id, "baixa_lista") or {}
        if not alvo or prop.get("despesa_id") != alvo or not prop.get("itens"):
            return "Essa proposta já foi usada ou foi trocada por uma nota mais nova."
        _, pendencias = self.repo.memoria(usuario_id)
        por_id = {p.id: p for p in self._listas_de_compras(pendencias)}
        tirados, concluidas = [], 0
        for lista_id in dict.fromkeys(x["lista"] for x in prop["itens"]):
            lista = por_id.get(lista_id)
            if lista is None:
                continue
            antes = itens_da_lista(lista.descricao)[1]
            tirar = [x["item"] for x in prop["itens"] if x["lista"] == lista_id and x["item"] in antes]
            if not tirar:
                continue
            tirados += tirar
            restam = [i for i in antes if i not in tirar]
            if not restam:
                self.repo.ajustar_pendencia(usuario_id, lista_id, prop["mensagem_id"], situacao="concluida")
                concluidas += 1
            else:
                self.repo.ajustar_pendencia(usuario_id, lista_id, prop["mensagem_id"],
                                            descricao=mudar_lista(lista.descricao, [], tirar))
        self.repo.definir_config(usuario_id, "baixa_lista", {})
        if not tirados:
            return "A lista já não tinha esses itens. Nada mudou."
        texto = f"🛒 Tirei da lista: {', '.join(tirados)}."
        return texto + (" A lista ficou vazia e foi concluída. 🎉" if concluidas else "")

    def vincular_despesa_evento(self, usuario_id: str, alvo: str) -> str:
        """Botão "vd:<despesa>:<n>": liga a despesa ao n-ésimo compromisso do dia (0 = encerrar)."""
        despesa_id, _, n = alvo.partition(":")
        if not _uuid(despesa_id) or not n.isdigit():
            return "Não encontrei essa despesa."
        d = self.repo.despesa(usuario_id, despesa_id)
        if not d:
            return "Não encontrei essa despesa."
        if n == "0":
            ligados = self.repo.vinculos_despesas(usuario_id, [despesa_id]).get(despesa_id, [])
            return ("👍 Despesa sem compromisso ligado." if not ligados else
                    "👍 Pronto: " + ", ".join(v["titulo"] or v["cliente"] for v in ligados) + ".")
        eventos = self._eventos_da_despesa(usuario_id, d["data"], d.get("hora"))
        if int(n) > len(eventos):
            return "Esse compromisso não está mais na agenda."
        e = eventos[int(n) - 1]
        self.repo.vincular_despesa(usuario_id, despesa_id, "evento", e.id, e.titulo, e.inicio)
        return f"🔗 Liguei a despesa a: {e.titulo}. Se teve outro, toque nele; senão, em Pronto."

    # -- viagens planejadas (D-056) -------------------------------------------------------

    def _aplicar_viagens(self, usuario_id, mensagem_id, ext, texto_mensagem: str | None = None):
        linhas, botoes = [], []
        for v in ext.viagens:
            ini, fim = date.fromisoformat(v.inicio), date.fromisoformat(v.fim)
            nome = v.nome or viagens.nome_padrao(ini, fim, v.objetivo, v.cidades, v.acompanhantes)
            vid = self.repo.salvar_viagem(usuario_id, mensagem_id, nome, v.inicio, v.fim, v.cidades,
                                          v.acompanhantes, v.objetivo)
            linhas.append(f"🧳 Viagem anotada: {nome}")
            linhas += self._plano_viagem(usuario_id, ini, fim)
            ja_falou = re.search(r"reuni|visita|encontro|almo[cç]o com", texto_mensagem or "", re.I)
            if fim >= _agora().date() and ini - _agora().date() <= timedelta(days=1) and not ja_falou:
                # Ida para hoje ou amanhã: puxa o que falta para o dia render (reuniões e gastos).
                linhas.append("🗓️ Vai ter reunião ou visita lá? Me conta com quem e a que horas, que eu ponho "
                              "na agenda. E pode mandar os gastos do dia (foto do cupom ou valor): entram no "
                              "relatório desta viagem.")
            if fim >= _agora().date():
                linhas.append("Quer que eu pergunte no fim de cada dia da viagem pelas despesas e "
                              "mostre a agenda do dia seguinte?")
                botoes += [("✔️ Sim, às 19h", f"va:{vid}:1"), ("Não precisa", f"va:{vid}:0")]
            else:
                linhas.append(f"Para o relatório: /viagem {ini:%d/%m} {fim:%d/%m}")
        return linhas, botoes

    def _plano_viagem(self, usuario_id, ini: date, fim: date) -> list[str]:
        """Expectativa da agenda nos dias da viagem (só leitura)."""
        if not self._agenda_conectada(usuario_id) or (fim - ini).days > 14:
            return []
        linhas = []
        d = ini
        while d <= fim:
            eventos = self.agenda.eventos_em(usuario_id, d)
            itens = " · ".join(f"{e.inicio[11:16]} {e.titulo}" for e in eventos[:6]) or "nada na agenda"
            linhas.append(f"🗓️ {viagens.rotulo_dia(d)}: {itens}")
            d += timedelta(days=1)
        return linhas

    def acompanhar_viagem(self, usuario_id: str, alvo: str) -> str:
        viagem_id, _, sim = alvo.partition(":")
        if not _uuid(viagem_id) or not self.repo.viagem(usuario_id, viagem_id):
            return "Não encontrei essa viagem."
        self.repo.mudar_viagem(usuario_id, viagem_id, acompanhar=(sim == "1"))
        return ("👍 Combinado: em cada dia da viagem, às 19h, eu pergunto pelas despesas."
                if sim == "1" else "👍 Sem acompanhamento diário. O relatório continua no /viagem.")

    def checkins_para_enviar(self) -> list[tuple[int, str]]:
        """Acompanhamento diário das viagens que o usuário pediu (D-007): no máximo um por dia."""
        agora = _agora()
        saida = []
        for v in self.repo.viagens_para_checkin(agora):
            if not v.get("telegram_chat_id") or not self.repo.marcar_checkin(v["id"], agora.date()):
                continue
            ini, fim = date.fromisoformat(v["inicio"]), date.fromisoformat(v["fim"])
            n, total = viagens.dia_da_viagem(ini, fim, agora.date())
            hoje = [d for d in self.repo.despesas(v["usuario_id"], agora.date()) if d["data"] == agora.date().isoformat()]
            soma = sum(d["valor_centavos"] for d in hoje if d["valor_centavos"] is not None)
            linhas = [f"🧳 {v['nome']} · dia {n} de {total}",
                      f"🧾 Despesas de hoje: {len(hoje)}" + (f" · {despesas.brl(soma)}" if hoje else "")]
            amanha = agora.date() + timedelta(days=1)
            if amanha <= fim:
                linhas += self._plano_viagem(v["usuario_id"], amanha, amanha)
            linhas.append("Teve mais alguma despesa hoje? Mande a foto, o PDF ou o valor.")
            saida.append((v["telegram_chat_id"], "\n".join(linhas)))
        return saida

    def relatorio_atual(self, usuario_id: str) -> str:
        """/viagem sem datas: a viagem em andamento ou a mais recente; sem viagem, últimos 7 dias."""
        hoje = _agora().date().isoformat()
        lista = self.repo.viagens(usuario_id)
        atual = next((v for v in lista if v["inicio"] <= hoje), None)
        if atual:
            return self.relatorio_viagem(usuario_id, date.fromisoformat(atual["inicio"]),
                                         date.fromisoformat(atual["fim"]))
        d = _agora().date()
        return self.relatorio_viagem(usuario_id, d - timedelta(days=6), d)

    def relatorio_viagem(self, usuario_id: str, inicio: date, fim: date) -> str:
        """Relatório para lançar no Onfly: despesas do período em ordem cronológica, com compromisso e
        cliente. Somas pelo código (nunca pelo modelo)."""
        lista = [d for d in self.repo.despesas(usuario_id, inicio)
                 if d["data"] and d["data"] <= fim.isoformat() and d.get("contexto") not in ("familia", "igreja")]
        if not lista:
            return f"🧾 Não encontrei despesas entre {inicio:%d/%m} e {fim:%d/%m}."
        vinculos = self.repo.vinculos_despesas(usuario_id, [d["id"] for d in lista])
        lista.reverse()  # o banco devolve da mais nova para a mais antiga
        horas = {d["id"]: d.get("hora") or min((v["inicio"][11:16] for v in vinculos.get(d["id"], [])
                                                if v["tipo"] == "evento" and v.get("inicio")), default="99:99")
                 for d in lista}
        lista.sort(key=lambda d: (d["data"], horas[d["id"]]))
        viagem = next((v for v in self.repo.viagens(usuario_id)
                       if v["inicio"] <= fim.isoformat() and v["fim"] >= inicio.isoformat()), None)
        titulo = viagem["nome"] if viagem else f"Relatório de viagem · {viagens.periodo(inicio, fim)}"
        linhas = [f"🧳 {titulo}", ""]
        dia_atual = None
        for d in lista:
            if d["data"] != dia_atual:
                dia_atual = d["data"]
                dt = date.fromisoformat(dia_atual)
                linhas.append(f"📅 {DIAS[dt.weekday()][:3]} {dt:%d/%m}")
            ligado = vinculos.get(d["id"], [])
            com = "; ".join((f"{v['inicio'][11:16]} {v['titulo']}" if v["tipo"] == "evento" else v["cliente"])
                            for v in ligado)
            refeicao = despesas.refeicao(d.get("hora"), d["categoria"])
            quando = (f"{d['hora']} " if d.get("hora") else "")
            linhas.append(f"• {quando}{despesas.brl(d['valor_centavos'])} · "
                          f"{refeicao or despesas.ROTULO_CATEGORIA.get(d['categoria'], d['categoria'])}"
                          f" · {d.get('estabelecimento') or d['descricao']}"
                          + (f" ({d['local']})" if d.get("local") else "")
                          + (f" · 🔗 {com}" if com else " · sem cliente"))
        linhas.append("")
        linhas.append("Totais (calculados pelo Peraí):")
        por_cat: dict[str, int] = {}
        sem_valor = 0
        for d in lista:
            if d["valor_centavos"] is None:
                sem_valor += 1
            else:
                por_cat[d["categoria"]] = por_cat.get(d["categoria"], 0) + d["valor_centavos"]
        for cat, v in sorted(por_cat.items()):
            linhas.append(f"• {despesas.ROTULO_CATEGORIA.get(cat, cat)}: {despesas.brl(v)}")
        linhas.append(f"• Total: {despesas.brl(sum(por_cat.values()))}"
                      + (f" (+{sem_valor} sem valor informado)" if sem_valor else ""))
        linhas.append("")
        linhas.append("📎 Os comprovantes (fotos, PDFs, XMLs) estão guardados no Peraí. Lance no Onfly; "
                      "o Peraí não envia nada para lá sozinho.")
        return "\n".join(linhas)

    # -- CRM de contas (D-089) -----------------------------------------------------------

    def _custos_contas(self, usuario_id: str, inicio: str | None = None, fim: str | None = None) -> list:
        return contas_mod.custos_do_usuario(self.repo, usuario_id, inicio, fim)

    def _conta_parecida(self, usuario_id: str, nome: str) -> str:
        """Conta já conhecida ("Integrado" = "Grupo Integrado") ou uma nova."""
        for c in self.repo.contas_relacionamento(usuario_id):
            if contas_mod.mesma_conta(c["nome"], nome):
                return c["id"]
        return self._conta(usuario_id, nome, "cliente")

    def _aplicar_oportunidades(self, usuario_id, mensagem_id, ext, codigos_o, proprio: bool) -> list[str]:
        """D-090: oportunidades só pelo próprio usuário (encaminhado de terceiros não abre nem muda)."""
        if not (ext.oportunidades or ext.ajustes_oportunidades):
            return []
        if not proprio:
            return ["🔒 Esse conteúdo fala de oportunidade de negócio, mas não veio de você; não abri nem mudei "
                    "nenhuma. Se quiser, me diga com suas palavras."]
        linhas = []
        for o in ext.oportunidades:
            conta_id = self._conta_parecida(usuario_id, o.conta)
            nome = (self._nome_conta(usuario_id, conta_id) or {}).get("nome", o.conta)
            self.repo.salvar_oportunidade(usuario_id, mensagem_id, conta_id, o.titulo, o.etapa, o.valor_centavos,
                                          o.previsao)
            linhas.append(f"📈 Oportunidade: {nome} · {o.titulo} · {ETAPAS[o.etapa]} · "
                          f"{despesas.brl(o.valor_centavos) if o.valor_centavos is not None else 'valor não informado'}"
                          + (f" · previsão {o.previsao[8:10]}/{o.previsao[5:7]}" if o.previsao else ""))
        for a in ext.ajustes_oportunidades:
            alvo = codigos_o.get(a.codigo)
            if alvo is None:
                continue
            campos = {k: v for k, v in (("etapa", a.etapa), ("valor_centavos", a.valor_centavos),
                                         ("previsao", a.previsao), ("titulo", a.titulo)) if v is not None}
            self.repo.mudar_oportunidade(usuario_id, alvo["id"], mensagem_id, campos)
            mudou = []
            if a.etapa and a.etapa != alvo["etapa"]:
                mudou.append(f"{ETAPAS[alvo['etapa']]} → {ETAPAS[a.etapa]}")
            if a.valor_centavos is not None and a.valor_centavos != alvo["valor_centavos"]:
                mudou.append(f"valor {despesas.brl(a.valor_centavos)}")
            if a.previsao:
                mudou.append(f"previsão {a.previsao[8:10]}/{a.previsao[5:7]}")
            if mudou:
                linhas.append(f"📈 {alvo['conta'] or 'Oportunidade'} · {a.titulo or alvo['titulo']}: " + "; ".join(mudou)
                              + (" 🎉" if a.etapa == "ganha" else ""))
        return linhas

    def registrar_proposta(self, usuario_id: str, mensagem_id: str) -> str:
        """Botão "pp:<mensagem>" (D-091): lê a proposta ou o pricing, confere pelo código e liga à conta e
        à oportunidade. A IA só lê os números; contas, somas e diferenças são do código."""
        if not _uuid(mensagem_id):
            return "Não achei essa mensagem."
        if self.passou_do_teto(usuario_id):
            return "⏸️ Cheguei ao limite de uso de hoje. Toque de novo depois da meia-noite."
        caminho = self.repo.arquivo_mensagem(usuario_id, mensagem_id)
        arquivo = Path(caminho) if caminho and Path(caminho).exists() else None
        bruto = propostas_mod.texto_do_arquivo(arquivo) if arquivo else ""
        texto_msg = self.repo.texto_mensagem(usuario_id, mensagem_id) or ""
        nome = arquivo.name if arquivo else "documento"
        try:
            if arquivo is not None and arquivo.suffix.lower() in (".pdf", ".jpg", ".jpeg", ".png", ".webp"):
                r = self._conversar(usuario_id, "proposta_imagem", propostas_mod.SISTEMA_PROPOSTA,
                                    f"Arquivo: {nome}\n\nTexto extraído (pode estar incompleto):\n{bruto[:60_000]}",
                                    imagem=arquivo)
            else:
                r = self._conversar(usuario_id, "consulta", propostas_mod.SISTEMA_PROPOSTA,
                                    f"Arquivo: {nome}\n\n{(bruto or texto_msg)[:120_000]}")
        except ErroModelo:
            return "📑 Não consegui ler o documento agora. Toque de novo em alguns minutos."
        d = propostas_mod.interpretar(r.texto)
        if d is None:
            return "📑 Não consegui tirar os números desse documento. Ele é uma proposta ou um pricing?"
        avisos = propostas_mod.conferir(d, bruto or texto_msg)
        valores = propostas_mod.resumo_valores(d)
        anteriores = [x for x in self.repo.propostas(usuario_id) if x["mensagem_id"] != mensagem_id]
        conta_id = oportunidade_id = None
        if d["tipo"] == "pricing":
            casada, dif = propostas_mod.cruzar_pricing(d, [x for x in anteriores if x["tipo"] == "comercial"])
            avisos += dif
            if casada:
                conta_id, oportunidade_id = casada["conta_id"], casada["oportunidade_id"]
                nome_c = (self._nome_conta(usuario_id, conta_id) or {}).get("nome")
                if d.get("cliente") and nome_c and not contas_mod.mesma_conta(nome_c, d["cliente"]):
                    avisos.append(f"O cabeçalho do pricing diz “{d['cliente']}”, mas os valores batem com a proposta "
                                  f"{casada.get('numero') or ''} de {nome_c}: liguei a {nome_c}.")
        if conta_id is None and d.get("cliente"):
            conta_id = self._conta_parecida(usuario_id, d["cliente"])
        if oportunidade_id is None and conta_id:
            abertas = [o for o in self.repo.oportunidades(usuario_id)
                       if o["conta_id"] == conta_id and o["etapa"] not in ("ganha", "perdida")]
            if abertas:
                oportunidade_id = abertas[0]["id"]
            elif d["tipo"] != "pricing":
                titulo = (d.get("objeto") or f"Proposta {d.get('numero') or ''}").strip()[:160]
                oportunidade_id = self.repo.salvar_oportunidade(usuario_id, mensagem_id, conta_id, titulo,
                                                                "proposta", None, None)
        registro = {"conta_id": conta_id, "oportunidade_id": oportunidade_id, "tipo": d["tipo"],
                    "numero": d.get("numero"), "revisao": d.get("revisao"), "data": d.get("data"),
                    "validade": propostas_mod.validade(d), "dados": d, "conferencia": avisos, **valores}
        self.repo.salvar_proposta(usuario_id, mensagem_id, registro)
        linhas = [self._texto_proposta(registro, (self._nome_conta(usuario_id, conta_id) or {}).get("nome"))]
        if oportunidade_id:
            linhas.append(self._atualizar_valor_oportunidade(usuario_id, oportunidade_id, mensagem_id))
        linhas.append("🔍 Conferência: tudo bate." if not avisos else
                      "🔍 Conferência:\n" + "\n".join(f"• {a}" for a in avisos))
        dolar = cambio.dolar_do_pricing(d)   # D-107: contraponto de câmbio, pelo código
        if dolar is not None:
            ptax = self._ptax(usuario_id)
            if ptax:
                linhas.append(cambio.texto(cambio.comparar(dolar, ptax, registro.get("custo_centavos"),
                                                           registro.get("lucro_centavos")), d.get("numero")))
            else:
                linhas.append(f"💱 O pricing usa US$ 1 = R$ {str(dolar).replace('.', ',')}; não consegui buscar a PTAX "
                              "de hoje para comparar (tente /cambio).")
        return "\n".join(l for l in linhas if l)

    @staticmethod
    def _texto_proposta(p: dict, conta: str | None) -> str:
        rot = {"comercial": "📑 Proposta comercial", "tecnica": "📐 Proposta técnica", "pricing": "🧮 Pricing"}[p["tipo"]]
        cab = f"{rot}{' nº ' + p['numero'] if p.get('numero') else ''}{' rev ' + p['revisao'] if p.get('revisao') else ''}"
        cab += f" · {conta or 'conta não identificada'}"
        if p.get("data"):
            cab += f" · {p['data'][8:10]}/{p['data'][5:7]}/{p['data'][:4]}"
        out = [cab]
        if p.get("valor_centavos") is not None:
            out.append(f"   Valor: {despesas.brl(p['valor_centavos'])}"
                       + (f" · válida até {p['validade'][8:10]}/{p['validade'][5:7]}" if p.get("validade") else ""))
        if p["tipo"] == "pricing":
            out.append(f"   Custo {propostas_mod._brl(p.get('custo_centavos'))} · impostos "
                       f"{propostas_mod._brl(p.get('impostos_centavos'))} · lucro bruto previsto "
                       f"{propostas_mod._brl(p.get('lucro_centavos'))}"
                       + (f" ({p['margem']:.2f}%)" if p.get("margem") is not None else ""))
        return "\n".join(out)

    def _atualizar_valor_oportunidade(self, usuario_id: str, oportunidade_id: str, mensagem_id: str) -> str:
        """Valor da oportunidade = soma das propostas comerciais dela (a revisão mais nova de cada número),
        pelo código; o lucro previsto vem dos pricings ligados."""
        todas = [p for p in self.repo.propostas(usuario_id) if p["oportunidade_id"] == oportunidade_id]
        por_numero: dict[str, dict] = {}
        for p in sorted(todas, key=lambda p: p["criado_em"]):
            if p["tipo"] == "comercial":
                por_numero[p.get("numero") or p["id"]] = p
        comerciais = list(por_numero.values())
        op = next((o for o in self.repo.oportunidades(usuario_id) if o["id"] == oportunidade_id), None)
        if not op:
            return ""
        campos = {}
        valores = [p["valor_centavos"] for p in comerciais]
        if comerciais and None not in valores:
            campos["valor_centavos"] = sum(valores)
        if op["etapa"] == "prospeccao" and comerciais:
            campos["etapa"] = "proposta"
        if campos:
            self.repo.mudar_oportunidade(usuario_id, oportunidade_id, mensagem_id, campos)
        lucro = [p for p in todas if p["tipo"] == "pricing" and p.get("lucro_centavos") is not None]
        total = campos.get("valor_centavos", op["valor_centavos"])
        texto = (f"📈 Oportunidade {op['titulo']} ({op['conta'] or 'sem conta'}): "
                 f"{len(comerciais)} proposta(s) comercial(is) · {propostas_mod._brl(total)}")
        if lucro:
            soma_lucro = sum(p["lucro_centavos"] for p in lucro)
            texto += f" · lucro bruto previsto {despesas.brl(soma_lucro)} ({len(lucro)} pricing(s))"
            if total:
                texto += f" · {soma_lucro * 100 / total:.1f}% do valor"
        return texto

    # -- parcerias, contratos, indicações e comissão (D-092) ------------------------------

    def _config_comissao(self, usuario_id: str) -> comissao.Config:
        return comissao.Config.de_dict(self.repo.config(usuario_id, "comissao"))

    def configurar_impostos(self, usuario_id: str, args: list[str]) -> str:
        cfg = self._config_comissao(usuario_id)
        erro = comissao.aplicar_comando(cfg, args)
        if erro:
            return erro
        if args:
            self.repo.definir_config(usuario_id, "comissao", cfg.para_dict())
        return comissao.texto_config(cfg)

    def registrar_parceria(self, usuario_id: str, mensagem_id: str) -> str:
        """Botão "pc:<mensagem>": o documento vira contrato com composição, parcelas e regra de indicação."""
        if not _uuid(mensagem_id):
            return "Não achei essa mensagem."
        if self.passou_do_teto(usuario_id):
            return "⏸️ Cheguei ao limite de uso de hoje. Toque de novo depois da meia-noite."
        caminho = self.repo.arquivo_mensagem(usuario_id, mensagem_id)
        arquivo = Path(caminho) if caminho and Path(caminho).exists() else None
        bruto = propostas_mod.texto_do_arquivo(arquivo) if arquivo else ""
        texto_msg = self.repo.texto_mensagem(usuario_id, mensagem_id) or ""
        try:
            if arquivo is not None and arquivo.suffix.lower() == ".pdf":
                r = self._conversar(usuario_id, "parceria_imagem", parcerias.SISTEMA_PARCERIA,
                                    f"Arquivo: {arquivo.name}\n\nTexto extraído:\n{bruto[:60_000]}", imagem=arquivo)
            else:
                r = self._conversar(usuario_id, "consulta", parcerias.SISTEMA_PARCERIA, (bruto or texto_msg)[:120_000])
        except ErroModelo:
            return "🤝 Não consegui ler o documento agora. Toque de novo em alguns minutos."
        d = parcerias.interpretar(r.texto)
        if d is None:
            return "🤝 Não consegui tirar as condições da parceria desse documento."
        avisos = parcerias.conferir(d)
        pendentes = propostas_mod.PENDENTE.findall(bruto or texto_msg)
        if pendentes:
            avisos.append(f"Ficaram {len(pendentes)} campo(s) por preencher.")
        conta_id = self._conta_parecida(usuario_id, d["parceiro"])
        registro = {"conta_id": conta_id, "empresa_l8": d.get("empresa_l8"),
                    "titulo": (d.get("titulo") or f"Parceria com {d['parceiro']}")[:160], "numero": d.get("numero"),
                    "data": d.get("data"), "vigencia_ate": d.get("vigencia_ate"),
                    "valor_centavos": propostas_mod.centavos(d.get("valor_global")),
                    "percentual_indicacao": parcerias.pct_indicacao(d), "dados": d, "conferencia": avisos}
        self.repo.salvar_contrato(usuario_id, mensagem_id, registro, parcerias.gerar_parcelas(d, None))
        k = next(x for x in self.repo.contratos(usuario_id) if x["mensagem_id"] == mensagem_id)
        return (parcerias.resumo(k) + ("\n🔍 Conferência: tudo bate." if not avisos else "")
                + "\n\nQuando assinar, me diga a data (ex.: “assinamos o termo do Hub em 20/11”): eu calculo os "
                  "vencimentos e crio as pendências de pagamento. /contratos mostra tudo.")

    def _aplicar_parcerias(self, usuario_id, mensagem_id, ext, codigos_c, proprio: bool) -> list[str]:
        if not (ext.indicacoes or ext.recebimentos_indicacao or ext.ajustes_contratos or ext.acordos_indicacao
                or ext.comissoes_projeto or ext.faturas or ext.pagamentos_cliente):
            return []
        if not proprio:
            return ["🔒 Esse conteúdo fala de contrato ou indicação, mas não veio de você; não mudei nada."]
        linhas = []
        for a in ext.ajustes_contratos:
            k = codigos_c.get(a.codigo)
            if not k:
                continue
            if a.assinatura:
                parcelas = parcerias.gerar_parcelas(k["dados"], a.assinatura)
                self.repo.mudar_contrato(usuario_id, k["id"], a.assinatura, parcelas)
                novas = [extracao.Pendencia(f"P{p['n']}", mensagem_id,
                                            f"Pagar {p['descricao']} · {k['titulo']}: {despesas.brl(p['valor_centavos'])}",
                                            "eu", None, p["vencimento"], None)
                         for p in parcelas if p["tipo"] == "pagar" and p.get("vencimento")]
                if novas:
                    self.repo.salvar_extracao(usuario_id, mensagem_id, extracao.Extracao(mensagem_id, pendencias=novas))
                linhas.append(f"✍️ {k['titulo']}: assinado em {a.assinatura[8:10]}/{a.assinatura[5:7]}. "
                              f"Calculei {len(novas)} vencimento(s) e criei as pendências de pagamento.")
            if a.parcela_paga:
                ok = self.repo.pagar_parcela(usuario_id, k["id"], a.parcela_paga,
                                             a.data_pagamento or _agora().date().isoformat())
                if ok:   # a pendência de pagamento daquela parcela fecha junto
                    parc = next((x for x in k.get("parcelas") or [] if x["n"] == a.parcela_paga), None)
                    _, abertas_p = self.repo.memoria(usuario_id)
                    alvo_p = next((x for x in abertas_p if parc and x.descricao.startswith(
                        f"Pagar {parc['descricao']} · {k['titulo']}")), None)
                    if alvo_p:
                        self.repo.ajustar_pendencia(usuario_id, alvo_p.id, mensagem_id, situacao="concluida")
                linhas.append(f"✅ {k['titulo']}: pagamento {a.parcela_paga} anotado como pago." if ok else
                              f"❓ Não achei o pagamento {a.parcela_paga} em aberto em {k['titulo']}.")
        for ac in ext.acordos_indicacao:
            conta_id = self._conta_parecida(usuario_id, ac.parceiro)
            nome = (self._nome_conta(usuario_id, conta_id) or {}).get("nome", ac.parceiro)
            self.repo.salvar_contrato(usuario_id, mensagem_id, {
                "conta_id": conta_id, "empresa_l8": None if ac.beneficiario == "eu" else "L8",
                "titulo": f"Acordo de indicação · {nome}", "numero": None, "data": _agora().date().isoformat(),
                "vigencia_ate": None, "valor_centavos": None, "percentual_indicacao": ac.percentual,
                "dados": {"beneficiario": ac.beneficiario, "composicao": [],
                          "indicacao": {"percentual": ac.percentual, "base": ac.base}},
                "conferencia": []}, [])
            linhas.append(f"🤝 Acordo de indicação com {nome}: {comissao.fp(ac.percentual)}% "
                          + ("pago direto a você" if ac.beneficiario == "eu" else "pago à L8")
                          + (f" sobre {ac.base}" if ac.base else "") + ".")
        for cp in ext.comissoes_projeto:
            op = self._achar_projeto(usuario_id, cp.projeto)
            if op is None:
                linhas.append(f"❓ Não achei o projeto “{cp.projeto}”. Ele está em /oportunidades?")
                continue
            eu = cp.beneficiario.strip().lower() in ("eu", "mim", "você", "voce", "nelson")
            self.repo.salvar_comissao_projeto(usuario_id, mensagem_id, op["id"], "você" if eu else cp.beneficiario,
                                              eu, cp.percentual, cp.base)
            linhas.append(f"🧾 Comissão no projeto {op['titulo']} ({op.get('conta') or 'sem conta'}): "
                          f"{'você' if eu else cp.beneficiario} {comissao.fp(cp.percentual)}% sobre "
                          f"{'a base líquida' if cp.base == 'liquido' else 'o lucro'}; entra como custo do projeto (/projetos).")
        linhas += self._aplicar_faturas(usuario_id, mensagem_id, ext)
        contratos = self.repo.contratos(usuario_id)

        def contrato_do(parceiro):
            return next((k for k in contratos if k.get("conta") and parceiro
                         and contas_mod.mesma_conta(k["conta"], parceiro)), None)

        for i in ext.indicacoes:
            k = contrato_do(i.parceiro)
            self.repo.salvar_indicacao(usuario_id, mensagem_id, k["id"] if k else None, i.parceiro, i.cliente, i.sentido)
            linhas.append(f"🎯 Indicação: {'você indicou' if i.sentido == 'eu_indiquei' else i.parceiro + ' indicou'} "
                          f"{i.cliente}" + (f" ({'ao ' + i.parceiro if i.sentido == 'eu_indiquei' else 'para a L8'})")
                          + ("" if k else " · sem contrato de parceria registrado para calcular a comissão"))
        if ext.recebimentos_indicacao:
            cfg = self._config_comissao(usuario_id)
            todas = self.repo.indicacoes(usuario_id)
            for rcb in ext.recebimentos_indicacao:
                ind = next((x for x in todas if contas_mod.mesma_conta(x["cliente"], rcb.cliente)
                            and (not rcb.parceiro or contas_mod.mesma_conta(x["parceiro"], rcb.parceiro))), None)
                if ind is None:
                    linhas.append(f"❓ Não achei a indicação de {rcb.cliente}. Diga quem indicou (ex.: “indiquei a "
                                  f"{rcb.cliente} ao Parquetec”) e mande o valor de novo.")
                    continue
                self.repo.salvar_recebimento_indicacao(usuario_id, mensagem_id, ind["id"], rcb.data,
                                                       rcb.valor_centavos, rcb.situacao)
                k = next((x for x in contratos if x["id"] == ind.get("contrato_id")), None)
                pct = Decimal(str(k["percentual_indicacao"])) if k and k.get("percentual_indicacao") is not None else None
                if ind["sentido"] == "eu_indiquei" and k and (k.get("dados") or {}).get("beneficiario") == "eu":
                    pct_d = pct if pct is not None else cfg.percentual_parceiro
                    c = comissao.calcular_direto(rcb.valor_centavos, pct_d, cfg)
                    linhas.append(f"💼 Indicação {ind['cliente']} ({ind['parceiro']}) · "
                                  f"{'recebido' if rcb.situacao == 'recebido' else 'previsto'}:\n"
                                  + comissao.texto_direto(c, pct_d))
                elif ind["sentido"] == "eu_indiquei":
                    c = comissao.calcular(rcb.valor_centavos, cfg, pct)
                    linhas.append(f"💼 Indicação {ind['cliente']} ({ind['parceiro']}) · "
                                  f"{'recebido' if rcb.situacao == 'recebido' else 'previsto'}:\n"
                                  + comissao.texto(c, cfg, pct))
                else:
                    pct_ = pct if pct is not None else cfg.percentual_parceiro
                    linhas.append(f"💸 {ind['cliente']} veio por indicação de {ind['parceiro']}: a L8 deve "
                                  f"{comissao.fp(pct_)}% do líquido recebido = {despesas.brl(comissao._pct(rcb.valor_centavos, pct_))}.")
        return linhas

    # -- resultado por projeto e por conta (D-093) -----------------------------------------

    def _achar_projeto(self, usuario_id: str, nome: str) -> dict | None:
        """Oportunidade pelo título ou pela conta, como dito; prefere as ganhas (projetos)."""
        ops = self.repo.oportunidades(usuario_id)
        achadas = [o for o in ops if contas_mod.mesma_conta(o["titulo"], nome)
                   or (o.get("conta") and contas_mod.mesma_conta(o["conta"], nome))]
        if not achadas:
            alvo = set(consulta._raizes(nome))
            achadas = [o for o in ops if alvo & set(consulta._raizes(f"{o['titulo']} {o.get('conta') or ''}"))]
        achadas.sort(key=lambda o: (o["etapa"] != "ganha", o["etapa"] == "perdida"))
        return achadas[0] if achadas else None

    def _ligar_projeto(self, usuario_id: str, despesa_id: str, nome: str | None) -> list[str]:
        if not nome:
            return []
        op = self._achar_projeto(usuario_id, nome)
        if op is None:
            return [f"❓ Não achei o projeto “{nome}”; a despesa ficou só na conta."]
        self.repo.ligar_despesa_projeto(usuario_id, despesa_id, op["id"])
        return [f"📦 Despesa no projeto {op['titulo']} ({op.get('conta') or 'sem conta'})."]

    def _resultados(self, usuario_id: str) -> tuple[list, list]:
        """(projetos, contas) com o resultado calculado pelo código."""
        ops = self.repo.oportunidades(usuario_id)
        props = self.repo.propostas(usuario_id)
        comis = self.repo.comissoes_projeto(usuario_id)
        por_proj = self.repo.despesas_por_projeto(usuario_id)
        todas_d = {d["id"]: d for d in self.repo.despesas(usuario_id, date(2000, 1, 1))}
        fats = self.repo.faturas(usuario_id)
        hoje = _agora().date().isoformat()
        projetos = [resultado_mod.projeto(o, [p for p in props if p["oportunidade_id"] == o["id"]],
                                          [todas_d[d] for d in por_proj.get(o["id"], []) if d in todas_d],
                                          [c for c in comis if c["oportunidade_id"] == o["id"]],
                                          [f for f in fats if f["oportunidade_id"] == o["id"]], hoje)
                    for o in ops]
        em_projeto = {d for ds in por_proj.values() for d in ds}
        contas_r = []
        for cc in self._custos_contas(usuario_id):
            fora = sum(x["parte"] or 0 for x in cc.despesas if x["despesa"]["id"] not in em_projeto)
            dela = [p for p in projetos if cc.conta_id and p.op.get("conta_id") == cc.conta_id]
            if dela or fora:
                contas_r.append(resultado_mod.conta(cc.nome, dela, fora))
        return projetos, contas_r

    def _aplicar_faturas(self, usuario_id, mensagem_id, ext) -> list[str]:
        """D-094: fatura do projeto e pagamento do cliente; pagamento nunca passa do saldo da fatura."""
        linhas = []
        for f in ext.faturas:
            op = self._achar_projeto(usuario_id, f.projeto)
            if op is None:
                linhas.append(f"❓ Não achei o projeto “{f.projeto}” para a NF {f.numero or ''}. Ele está em /oportunidades?")
                continue
            self.repo.salvar_fatura(usuario_id, mensagem_id, op["id"], f.numero, f.data or _agora().date().isoformat(),
                                    f.vencimento, f.valor_centavos)
            linhas.append(f"🧾 NF {f.numero or 's/n'} de {despesas.brl(f.valor_centavos)} no projeto {op['titulo']} "
                          f"({op.get('conta') or 'sem conta'})" + (f", vence {f.vencimento[8:10]}/{f.vencimento[5:7]}"
                                                                  if f.vencimento else "") + ".")
        if not ext.pagamentos_cliente:
            return linhas
        def chave_nf(n):
            return re.sub(r"\D", "", n or "")
        for pg in ext.pagamentos_cliente:
            todas = self.repo.faturas(usuario_id)
            alvo = None
            if pg.fatura:
                alvo = next((f for f in todas if f.get("numero") and chave_nf(f["numero"]) == chave_nf(pg.fatura)), None)
            if alvo is None and pg.projeto:
                op = self._achar_projeto(usuario_id, pg.projeto)
                abertas = [f for f in todas if op and f["oportunidade_id"] == op["id"] and resultado_mod.saldo_fatura(f) > 0]
                alvo = abertas[0] if abertas else None
            if alvo is None:
                linhas.append("❓ Não achei a nota desse pagamento. Registre a fatura antes (“a L8 faturou a NF 1234 "
                              "de 500 mil para a Câmara”) e mande o pagamento de novo.")
                continue
            saldo = resultado_mod.saldo_fatura(alvo)
            if pg.valor_centavos > saldo:
                linhas.append(f"⚠️ O pagamento de {despesas.brl(pg.valor_centavos)} passa do saldo da NF "
                              f"{alvo.get('numero') or 's/n'} ({despesas.brl(saldo)}). Não registrei; confira o valor.")
                continue
            self.repo.salvar_recebimento(usuario_id, mensagem_id, alvo["id"], pg.data or _agora().date().isoformat(),
                                         pg.valor_centavos)
            resto = saldo - pg.valor_centavos
            linhas.append(f"💵 Recebido {despesas.brl(pg.valor_centavos)} da NF {alvo.get('numero') or 's/n'}"
                          + (f"; falta {despesas.brl(resto)}." if resto else "; nota quitada. ✅")
                          + " Comissões sobre o recebido em /projetos (estimativa).")
        return linhas

    def listar_faturas(self, usuario_id: str) -> str:
        """/faturas: notas em aberto e vencidas, com saldo, pelo código."""
        fats = [f for f in self.repo.faturas(usuario_id) if resultado_mod.saldo_fatura(f) > 0]
        if not fats:
            return "🧾 Nenhuma nota em aberto. Registre com “a L8 faturou a NF 1234 de 500 mil para a Câmara, vence 10/12”."
        ops = {o["id"]: o for o in self.repo.oportunidades(usuario_id)}
        hoje = _agora().date().isoformat()
        fats.sort(key=lambda f: f.get("vencimento") or "9999")
        linhas = ["🧾 Notas em aberto (saldo calculado pelo Peraí)"]
        for f in fats:
            op = ops.get(f["oportunidade_id"]) or {}
            venc = f.get("vencimento")
            marca = "⚠️ vencida" if venc and venc < hoje else "vence"
            linhas.append(f"• NF {f.get('numero') or 's/n'} · {op.get('conta') or 'sem conta'} · {op.get('titulo') or ''}"
                          f" · saldo {despesas.brl(resultado_mod.saldo_fatura(f))} de {despesas.brl(f['valor_centavos'])}"
                          + (f" · {marca} {venc[8:10]}/{venc[5:7]}" if venc else ""))
        total = sum(resultado_mod.saldo_fatura(f) for f in fats)
        vencido = sum(resultado_mod.saldo_fatura(f) for f in fats if f.get("vencimento") and f["vencimento"] < hoje)
        linhas.append(f"\nA receber: {despesas.brl(total)}" + (f" · vencido: {despesas.brl(vencido)}" if vencido else ""))
        return "\n".join(linhas)

    def listar_projetos(self, usuario_id: str) -> str:
        """/projetos: cada projeto com venda, base líquida, lucro previsto, comissões, despesas e resultado."""
        projetos, contas_r = self._resultados(usuario_id)
        projetos = [p for p in projetos if p.op["etapa"] != "perdida"]
        if not projetos:
            return "📦 Nenhum projeto ou oportunidade ainda. Veja /oportunidades."
        projetos.sort(key=lambda p: (p.op["etapa"] != "ganha", -(p.venda or 0)))
        return ("📦 Projetos e resultado previsto (calculado pelo Peraí; estimativa)\n\n"
                + "\n\n".join(resultado_mod.texto_projeto(p) for p in projetos)
                + ("\n\n" + "\n".join(resultado_mod.texto_conta(c) for c in contas_r) if contas_r else ""))

    def listar_contratos(self, usuario_id: str) -> str:
        contratos = self.repo.contratos(usuario_id)
        if not contratos:
            return ("🤝 Nenhum contrato ainda. Mande o termo ou a proposta de parceria (PDF) e toque em "
                    "“🤝 Registrar parceria”.")
        return "🤝 Seus contratos e parcerias\n\n" + "\n\n".join(parcerias.resumo(k) for k in contratos)

    def listar_comissoes(self, usuario_id: str) -> str:
        """/comissoes: indicações, recebimentos e a sua parte, calculados pelo código."""
        cfg = self._config_comissao(usuario_id)
        contratos = {k["id"]: k for k in self.repo.contratos(usuario_id)}
        inds = self.repo.indicacoes(usuario_id)
        if not inds:
            return ("💼 Nenhuma indicação ainda. Conte como faria a alguém: “indiquei a Coamo ao Parquetec”; "
                    "depois, “o Parquetec recebeu 200 mil da Coamo”.\n\n" + comissao.texto_config(cfg))
        blocos = []
        tot = {"recebido": [0, 0, 0, False], "previsto": [0, 0, 0, False]}
        for ind in inds:
            k = contratos.get(ind.get("contrato_id"))
            pct = Decimal(str(k["percentual_indicacao"])) if k and k.get("percentual_indicacao") is not None else None
            cab = (f"🎯 {ind['cliente']} · {'você indicou ao ' + ind['parceiro'] if ind['sentido'] == 'eu_indiquei' else ind['parceiro'] + ' indicou à L8'}")
            if not ind["recebimentos"]:
                blocos.append(cab + "\n   Nenhum valor recebido ainda.")
                continue
            partes = [cab]
            for rcb in ind["recebimentos"]:
                quando = f"{rcb['data'][8:10]}/{rcb['data'][5:7]}" if rcb.get("data") else "sem data"
                direto = bool(k and (k.get("dados") or {}).get("beneficiario") == "eu")
                if ind["sentido"] == "eu_indiquei":
                    pct_d = pct if pct is not None else cfg.percentual_parceiro
                    c = (comissao.calcular_direto(rcb["base_centavos"], pct_d, cfg) if direto
                         else comissao.calcular(rcb["base_centavos"], cfg, pct))
                    corpo = comissao.texto_direto(c, pct_d) if direto else comissao.texto(c, cfg, pct)
                    partes.append(f"   {'✅ Recebido' if rcb['situacao'] == 'recebido' else '⏳ Previsto'} {quando}:\n"
                                  + corpo.replace("\n   ", "\n      ").replace("   ", "      ", 1))
                    t = tot[rcb["situacao"]]
                    t[0] += 0 if direto else c.l8_bruto
                    if c.liquido_min is not None:
                        t[1] += c.liquido_min
                        t[2] += c.liquido_max
                    else:
                        t[3] = True
                else:
                    pct_ = pct if pct is not None else cfg.percentual_parceiro
                    partes.append(f"   {quando}: L8 deve {despesas.brl(comissao._pct(rcb['base_centavos'], pct_))} "
                                  f"({comissao.fp(pct_)}% de {despesas.brl(rcb['base_centavos'])})")
            blocos.append("\n".join(partes))
        rodape = []
        for sit, rot in (("recebido", "Recebido"), ("previsto", "Previsto")):
            t = tot[sit]
            if t[0]:
                rodape.append(f"{rot}: L8 {despesas.brl(t[0])} · você líquido "
                              + ("não informado (faltam alíquotas: /impostos)" if t[3] else
                                 comissao._faixa(t[1], t[2])))
        return ("💼 Indicações e sua comissão (estimativa calculada pelo Peraí; não é orientação contábil)\n\n"
                + "\n\n".join(blocos) + ("\n\n" + "\n".join(rodape) if rodape else ""))

    def listar_oportunidades(self, usuario_id: str) -> str:
        """/oportunidades: o funil por etapa, com totais somados pelo código."""
        lista = self.repo.oportunidades(usuario_id)
        if not lista:
            return ("📈 Nenhuma oportunidade ainda. Conte como faria a alguém: “mandei proposta de 80 mil de "
                    "switches para o Integrado” ou “a Coamo quer cotação de firewall”.")
        return "📈 Suas oportunidades (totais somados pelo Peraí)\n\n" + contas_mod.funil(lista)

    def _aplicar_partes(self, usuario_id: str, despesa_id: str, partes) -> list[str]:
        """Divisão dita pelo usuário (D-089, etapa 2): grava a parte de cada conta e mostra o rateio
        calculado pelo código."""
        if not partes:
            return []
        for cliente, centavos, pct in partes:
            self.repo.vincular_despesa(usuario_id, despesa_id, "cliente", cliente=cliente)
            self.repo.definir_parte_despesa(usuario_id, despesa_id, cliente, centavos, pct)
        d = self.repo.despesa(usuario_id, despesa_id)
        ligados = [v for v in self.repo.vinculos_despesas(usuario_id, [despesa_id]).get(despesa_id, [])
                   if v["tipo"] == "cliente"]
        valores, aviso = contas_mod.alocar(d["valor_centavos"] if d else None, ligados)
        if d is None or d["valor_centavos"] is None:
            return ["🤝 Anotei a divisão; ela vale quando o valor da despesa for informado."]
        return ["🤝 Rateio: " + "; ".join(f"{v['cliente']} {despesas.brl(x)}" for v, x in zip(ligados, valores))
                + "." + (f" ⚠️ {aviso[:1].upper()}{aviso[1:]}." if aviso else "")]

    def listar_contas(self, usuario_id: str, inicio: date | None = None, fim: date | None = None) -> str:
        """/contas [período]: cada cliente ou parceiro com custo, reuniões e próxima atividade."""
        custos = self._custos_contas(usuario_id, inicio.isoformat() if inicio else None,
                                     fim.isoformat() if fim else None)
        periodo = f" de {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}" if inicio and fim else ""
        if periodo:
            custos = [c for c in custos if c.despesas]
            if not custos:
                return f"🏢 Nenhuma despesa ligada a clientes ou parceiros{periodo}."
        if not custos:
            return ("🏢 Ainda não há contas. Elas nascem quando você manda a transcrição de uma reunião "
                    "(📄 Ata da reunião) ou liga uma despesa a um cliente (“almoço com o pessoal da Gazin”).")
        blocos = []
        oportunidades = self.repo.oportunidades(usuario_id)
        try:
            saudes = {x.conta_id: x for x in self._saudes(usuario_id)[0]}   # D-095
        except Exception as erro:
            log.warning("saúde das contas falhou: %s", str(erro)[:200])
            saudes = {}
        for c in sorted(custos, key=lambda c: (-c.total_centavos, c.nome.lower())):
            lista_atas = self.repo.atas_da_conta(usuario_id, c.conta_id) if c.conta_id else []
            conteudos = [a["conteudo"] for a in lista_atas]
            ultima = conteudos[0] if conteudos else None
            proxima = (ultima or {}).get("proxima_atividade")
            bloco = contas_mod.resumo_conta(c, ultima, proxima, len(conteudos))
            ops = contas_mod.resumo_oportunidades([o for o in oportunidades if c.conta_id and o["conta_id"] == c.conta_id])
            sd = saudes.get(c.conta_id)
            if sd is not None:
                bloco += (f"\n   🩺 Saúde: {crm_mod.COR[sd.cor]}" + (f" · {len(sd.sinais)} alerta(s), veja /saude"
                                                                   if sd.sinais else " · em dia"))
            blocos.append(bloco + ("\n" + ops if ops else ""))
        total = sum(c.total_centavos for c in custos)
        return (f"🏢 Suas contas{periodo} (custos somados e rateados pelo Peraí)\n\n" + "\n\n".join(blocos)
                + f"\n\nTotal ligado a clientes e parceiros{periodo}: {despesas.brl(total)}. Despesa com mais de "
                  "uma conta é dividida como você disse (“200 para o Integrado e 100 para a Coamo”) ou, sem "
                  "isso, em partes iguais. Período: /contas 10/2026 ou /contas 01/10 31/10.")

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

    def _responder(self, usuario_id, pergunta, pode_pedir: bool = True, lote: str | None = None,
                   sozinha: bool = False) -> str:
        registros, pendencias = self.repo.memoria(usuario_id)
        tipo = "briefing" if ("prepar" in pergunta.lower() or pergunta.startswith(BRIEFING_AUTOMATICO)) \
            else "consulta"
        # Teste da semana 4 e briefing automático: a pergunta vale sozinha (sem conversa recente).
        conversa = [] if (lote or sozinha) else self._conversa_recente(usuario_id)
        tipo_salvo = "teste" if lote else tipo
        pode_pedir = pode_pedir and not lote
        # Memória grande: só o que tem a ver com a pergunta vai ao modelo (tempo de resposta e custo).
        # Palavras nem sempre batem ("carro" x "Cobalt"): antes, o modelo aponta quais nomes da
        # memória a pergunta pode estar procurando, e eles entram na busca.
        extras = self._termos_relacionados(usuario_id, pergunta, registros) \
            if len(registros) > consulta.LIMITE_REGISTROS else ""
        registros = consulta.selecionar(registros, pergunta, " ".join(conversa[-4:]) + " " + extras,
                                        _agora().date().isoformat())
        memoria = consulta.formatar_memoria(registros, pendencias)
        # Pendências já fechadas também contam o que aconteceu ("Cobalt branco no lava-jato"): entram as que
        # têm a ver com a pergunta (até 40), como histórico citável.
        alvo = consulta._raizes(pergunta) | consulta._raizes(" ".join(conversa[-4:])) | consulta._raizes(extras)
        # Palavras nem sempre batem ("carro" x "Cobalt"): as fechadas nos últimos 30 dias entram sempre.
        todas = self.repo.pendencias_encerradas(usuario_id, _agora() - timedelta(days=120))
        recentes = self.repo.pendencias_encerradas(usuario_id, _agora() - timedelta(days=30))
        ids_recentes = {p["id"] for p in recentes}
        fechadas = (recentes[:40] + [p for p in todas if p["id"] not in ids_recentes
                                     and alvo & consulta._raizes(f"{p['descricao']} {p['quem'] or ''}")])[:60]
        if fechadas:
            memoria = (memoria if registros or pendencias else "") + "\n" + "\n".join(
                f"[{p['id']}] (pendência {'concluída' if p['situacao'] == 'concluida' else 'descartada'}"
                f"{'; esperava ' + p['quem'] if p['responsavel'] == 'aguardando' and p['quem'] else ''}) "
                f"{p['descricao']}" for p in fechadas)
        # Eventos da agenda entram como itens [A1], [A2]... e podem ser citados como fonte.
        eventos = []
        if self.agenda and self._agenda_conectada(usuario_id):
            eventos = self.agenda.eventos_completos(usuario_id)
        elif self.agenda:  # sem Google conectado, a agenda importada (.ics) ainda vale
            hoje = _agora().date()
            eventos = self.agenda.importados(usuario_id, (hoje - timedelta(days=1)).isoformat(),
                                             (hoje + timedelta(days=15)).isoformat())
        codigos_ag = {f"A{i}": e for i, e in enumerate(eventos, start=1)}
        if codigos_ag:
            linhas = [consulta.formatar_evento(c, e) for c, e in codigos_ag.items()]
            memoria = (memoria if registros or pendencias else "") + "\n" + "\n".join(linhas)
        gastos = self.repo.despesas(usuario_id, _agora().date().replace(day=1) - timedelta(days=100))
        codigos_d = {f"D{i}": d for i, d in enumerate(gastos[:60], start=1)}
        codigos_t = {f"T{i}": t for i, t in enumerate(despesas.totais_por_mes(gastos), start=1)}
        if codigos_d:
            memoria = memoria.strip() + "\n" + "\n".join(
                f"[{c}] (despesa; {d['data'] or 'sem data'}{' ' + d['hora'] if d.get('hora') else ''}; "
                f"{d['categoria']}{'; ' + despesas.refeicao(d.get('hora'), d['categoria']) if despesas.refeicao(d.get('hora'), d['categoria']) else ''}"
                f"; {d['contexto'] or 'sem contexto'}{'; ' + d['local'] if d.get('local') else ''}) "
                f"{d['descricao']}: {despesas.brl(d['valor_centavos'])}" for c, d in codigos_d.items())
            memoria += "\n" + "\n".join(f"[{c}] (total calculado pelo Peraí) {t}" for c, t in codigos_t.items())
        # Histórico de preços (D-084): entra quando a pergunta fala de preço ou de um produto registrado.
        codigos_q, listas = {}, []
        historico = self.repo.precos(usuario_id, (_agora().date() - timedelta(days=365)).isoformat())
        if historico:
            alvo_q = precos.chave(pergunta)
            fala_de_preco = bool(precos.INTENCAO.search(pergunta))
            hoje_iso = _agora().date().isoformat()
            # Lista de compras (D-088): "onde está mais barata a minha lista?" procura cada item da lista.
            listas = self._listas_de_compras(pendencias) \
                if precos.FALA_DE_LISTA.search(pergunta) and not precos.pede_ver_lista(pergunta) else []
            for pend in listas:
                alvo_q |= precos.chave(pend.descricao)
                fala_de_preco = True
            escolhidos = [p for p in historico if alvo_q & precos.chave(p["produto"])]
            if fala_de_preco and not escolhidos:   # "quais ofertas valem a pena?": as ofertas vigentes
                vistos = {p["id"] for p in escolhidos}
                escolhidos += [p for p in historico if p["id"] not in vistos and p["origem"] == "oferta"
                               and (p.get("data") or "") >= hoje_iso][:60]
            codigos_q = {f"Q{i}": p for i, p in enumerate(precos.sem_repetidos(escolhidos)[:80], start=1)}
            if codigos_q:
                memoria = memoria.strip() + "\n" + "\n".join(precos.linha(c, p) for c, p in codigos_q.items())
        # CRM de contas (D-089): custo por cliente ou parceiro, somado e rateado pelo código.
        codigos_k = {}
        if FALA_DE_CUSTO.search(pergunta) or any(contas_mod.mesma_conta(c["nome"], pergunta)
                                                 for c in self.repo.contas_relacionamento(usuario_id)):
            custos = [c for c in self._custos_contas(usuario_id) if c.despesas]
            alvo_k = [c for c in custos if contas_mod.normalizar(c.nome)
                      and re.search(rf"\b{re.escape(contas_mod.normalizar(c.nome).split()[0])}",
                                    contas_mod.normalizar(pergunta))]
            codigos_k = {f"K{i}": c for i, c in enumerate((alvo_k or custos)[:20], start=1)}
            if codigos_k:
                memoria = memoria.strip() + "\n" + "\n".join(contas_mod.linha_memoria(c, k)
                                                               for c, k in codigos_k.items())
        # Resultado por projeto e por conta (D-093): [Z]; com valor na pergunta, o peso do investimento.
        codigos_z = {}
        if FALA_DE_RESULTADO.search(pergunta):
            projetos_r, contas_r = self._resultados(usuario_id)
            alvo_z = [c for c in contas_r if contas_mod.mesma_conta(c.nome, pergunta)] or contas_r
            n = 0
            for c in alvo_z[:10]:
                n += 1
                codigos_z[f"Z{n}"] = {"id": c.nome}
                memoria += f"\n[Z{n}] (resultado calculado pelo Peraí) " + resultado_mod.texto_conta(c)
                for p in c.projetos[:8]:
                    n += 1
                    codigos_z[f"Z{n}"] = {"id": p.op["id"]}
                    memoria += f"\n[Z{n}] (projeto; calculado pelo Peraí) " + " · ".join(
                        l.strip() for l in resultado_mod.texto_projeto(p).splitlines())
                valor = resultado_mod.valor_da_pergunta(pergunta)
                if valor:
                    n += 1
                    codigos_z[f"Z{n}"] = {"id": f"investimento:{c.nome}"}
                    memoria += f"\n[Z{n}] (calculado pelo Peraí) " + resultado_mod.peso_investimento(valor, c)
        # Contratos, indicações e comissão (D-092): números do código, citáveis como [M].
        codigos_m = {}
        if FALA_DE_PARCERIA.search(pergunta):
            for i, k in enumerate(self.repo.contratos(usuario_id)[:10], start=1):
                codigos_m[f"M{i}"] = k
                memoria += "\n[M{}] (contrato; calculado pelo Peraí) ".format(i) + " · ".join(
                    l.strip() for l in parcerias.resumo(k).splitlines())
            cfg = self._config_comissao(usuario_id)
            n = len(codigos_m)
            for ind in self.repo.indicacoes(usuario_id)[:20]:
                for rcb in ind["recebimentos"]:
                    n += 1
                    codigos_m[f"M{n}"] = {"id": rcb["id"]}
                    if ind["sentido"] == "eu_indiquei":
                        c = comissao.calcular(rcb["base_centavos"], cfg)
                        conta = " · ".join(l.strip() for l in comissao.texto(c, cfg).splitlines())
                    else:
                        conta = f"L8 deve {despesas.brl(comissao._pct(rcb['base_centavos'], cfg.percentual_parceiro))}"
                    memoria += (f"\n[M{n}] (indicação; calculado pelo Peraí; {rcb['situacao']}; {rcb.get('data') or 'sem data'}) "
                                f"{ind['cliente']} via {ind['parceiro']} ({ind['sentido']}): {conta}")
        # Oportunidades por conta (D-090).
        codigos_o = {}
        lista_o = self.repo.oportunidades(usuario_id)
        if lista_o and (FALA_DE_OPORTUNIDADE.search(pergunta)
                        or any(o["conta"] and contas_mod.mesma_conta(o["conta"], pergunta) for o in lista_o)):
            alvo_o = [o for o in lista_o if o["conta"] and contas_mod.mesma_conta(o["conta"], pergunta)]
            codigos_o = {f"O{i}": o for i, o in enumerate((alvo_o or lista_o)[:40], start=1)}
            memoria = memoria.strip() + "\n" + "\n".join(contas_mod.linha_oportunidade(c, o)
                                                           for c, o in codigos_o.items())
            # Propostas e pricing dessas oportunidades (D-091): valor, custo, lucro previsto e conferência.
            ids_o = {o["id"] for o in codigos_o.values()}
            nomes_c = {c["id"]: c["nome"] for c in self.repo.contas_relacionamento(usuario_id)}
            for i, pr in enumerate([x for x in self.repo.propostas(usuario_id) if x["oportunidade_id"] in ids_o][:30],
                                   start=1):
                codigos_o[f"R{i}"] = pr
                memoria += "\n" + propostas_mod.linha(f"R{i}", pr, nomes_c.get(pr["conta_id"]))
            abertas_o = [o for c, o in codigos_o.items() if c.startswith("O") and o["etapa"] not in ("ganha", "perdida")]
            if abertas_o:
                codigos_o["OT"] = {"total": True}
                memoria += ("\n[OT] (total calculado pelo Peraí) oportunidades em aberto listadas acima: "
                            f"{len(abertas_o)}, somando {contas_mod._soma(abertas_o)}")
        # CRM (D-095 a D-100): atividades [A] e saúde calculada [S] das contas citadas.
        mem_crm, codigos_crm = self.memoria_crm(usuario_id, pergunta)
        if mem_crm:
            memoria = memoria.strip() + mem_crm
        ids = ({x.id for x in registros} | {p.id for p in pendencias} | {p["id"] for p in fechadas}
               | set(codigos_ag) | set(codigos_d) | set(codigos_t) | set(codigos_q)) | set(codigos_k) | set(codigos_o) \
            | set(codigos_m) | set(codigos_z) | set(codigos_crm)
        comparada = self._comparar_lista(usuario_id, listas, codigos_q) if (historico and listas and codigos_q) \
            else None
        # "Podemos comparar minha compra com as ofertas?" (D-109): a compra mais recente x ofertas.
        if precos.pede_comparar_compra(pergunta):
            aviso, comparada_c, codigos_pc = self._comparar_compra(usuario_id, pergunta, historico)
            if aviso:
                self.repo.salvar_resposta(usuario_id, tipo_salvo, pergunta, aviso, [], True, lote=lote)
                return aviso
            if comparada_c:
                comparada = comparada_c
                codigos_q.update(codigos_pc)
                ids |= set(codigos_pc)
        # "Organiza a lista por departamento", "lista completa": mostra a lista, sem comparar preço.
        if precos.pede_ver_lista(pergunta):
            listas_m = self._listas_de_compras(pendencias)
            itens_m = [i for pend in listas_m for i in itens_da_lista(pend.descricao)[1]]
            if itens_m:
                comparada = {"resposta": precos.texto_lista(itens_m),
                             "registros_citados": [pend.id for pend in listas_m], "sem_informacao": False}
        if comparada:
            resp = consulta.interpretar(pergunta, json.dumps(comparada), ids)
        else:
            pedido = consulta.montar_pedido(pergunta, memoria.strip() or "(nenhum item de memória)",
                                            f"{_hoje_extenso()}, agora {_agora():%H:%M}", conversa)
            r = self._conversar(usuario_id, tipo, consulta.SISTEMA_CONSULTA, pedido)
            resp = consulta.interpretar(pergunta, r.texto, ids)
        resp.resposta = consulta.limpar_codigos(resp.resposta)
        if resp.citacoes_invalidas:   # só ids, sem conteúdo: ajuda a achar "não encontrei" indevido
            log.warning("consulta com citações inválidas: %s", resp.citacoes_invalidas[:5])
        especiais = (set(codigos_ag) | set(codigos_d) | set(codigos_t) | set(codigos_q) | set(codigos_k)
                     | set(codigos_o) | set(codigos_m) | set(codigos_z))
        citados = [c for c in resp.registros_citados if c not in especiais]
        externos = ([codigos_ag[c].id if codigos_ag[c].id.startswith(("ics:", "ocupado:")) else
                     f"google:{codigos_ag[c].id}" for c in resp.registros_citados if c in codigos_ag]
                    + [f"despesa:{codigos_d[c]['id']}" for c in resp.registros_citados if c in codigos_d]
                    + [f"total:{c}" for c in resp.registros_citados if c in codigos_t]
                    + [f"preco:{codigos_q[c]['id']}" for c in resp.registros_citados if c in codigos_q]
                    + [f"custo:{codigos_k[c].nome}" for c in resp.registros_citados if c in codigos_k]
                    + [(f"proposta:{codigos_o[c]['id']}" if c.startswith("R") else f"oportunidade:{codigos_o[c]['id']}")
                       if "id" in codigos_o[c] else "total:oportunidades"
                       for c in resp.registros_citados if c in codigos_o]
                    + [f"contrato:{codigos_m[c]['id']}" for c in resp.registros_citados if c in codigos_m]
                    + [f"resultado:{codigos_z[c]['id']}" for c in resp.registros_citados if c in codigos_z])
        # Nada sem fonte (D-040): sem citação válida, a resposta só pode dizer que não sabe.
        if tipo == "briefing" and pode_pedir and resp.json_valido and (
                resp.pedir_informacoes or not (citados or externos) or resp.sem_informacao):
            self._briefing_pendente[usuario_id] = (pergunta, _agora())
            if not (citados or externos) or resp.sem_informacao:
                self.repo.salvar_resposta(usuario_id, tipo_salvo, pergunta, PEDIR_BRIEFING, [], True, lote=lote)
                return PEDIR_BRIEFING
        if not (citados or externos) or resp.sem_informacao or not resp.json_valido:
            texto = NAO_ENCONTREI
            self.repo.salvar_resposta(usuario_id, tipo_salvo, pergunta, texto, [], True, lote=lote)
            return texto
        rid = self.repo.salvar_resposta(usuario_id, tipo_salvo, pergunta, resp.resposta, citados, False, externos,
                                        lote=lote)
        if lote:
            return resp.resposta
        # Avaliação opcional: mede a utilidade do briefing e das respostas no teste de 4 semanas.
        botoes = [("👍 Ajudou", f"av:{rid}:ajudou"), ("😐 Neutro", f"av:{rid}:neutro"),
                  ("👎 Atrapalhou", f"av:{rid}:atrapalhou")]
        citados_q = [codigos_q[c] for c in dict.fromkeys(resp.registros_citados) if c in codigos_q]
        if citados_q:   # D-086
            guardados = self._precos_resposta.setdefault(usuario_id, {})
            guardados[rid] = citados_q
            while len(guardados) > 20:
                guardados.pop(next(iter(guardados)))
            botoes.insert(0, ("📤 Compartilhar preços", f"cp:{rid}"))
        return saida(resp.resposta, botoes)

    @staticmethod
    def _listas_de_compras(pendencias) -> list:
        """Só a lista do mercado (D-088): começa com "Comprar"/"Lista" e tem mais de um item."""
        return [p for p in pendencias if precos.e_lista_de_compras(p.descricao, p.responsavel,
                                                                    len(itens_da_lista(p.descricao)[1]))]

    def _comparar_lista(self, usuario_id, listas, codigos_q) -> dict | None:
        """D-088: a IA só liga cada item da lista aos preços do mesmo produto; quem escolhe a loja mais
        barata (pela referência por kg, litro ou unidade) e escreve a resposta é o código."""
        itens = precos.itens_unicos([i for pend in listas for i in itens_da_lista(pend.descricao)[1]])
        if not itens:
            return None
        pedido = ("Itens da lista:\n" + "\n".join(f"- {i}" for i in itens) + "\n\nPreços:\n"
                  + "\n".join(precos.linha(c, p) for c, p in codigos_q.items()))
        try:
            r = self._conversar(usuario_id, "consulta", precos.SISTEMA_LISTA, pedido)
            dados = extracao.extrair_json(r.texto)
        except (ErroModelo, ValueError, TypeError):
            return None
        mapa = {}
        for d in (dados.get("itens") or []) if isinstance(dados, dict) else []:
            if isinstance(d, dict) and d.get("item") in itens:
                mapa[d["item"]] = [str(c).strip("[] ") for c in (d.get("precos") or [])]
        texto, usados = precos.comparar_lista(itens, mapa, codigos_q, _agora().date().isoformat())
        if not texto:
            return None
        return {"resposta": texto, "registros_citados": [pend.id for pend in listas] + usados,
                "sem_informacao": False}

    def _comparar_compra(self, usuario_id, pergunta, historico) -> tuple[str | None, dict | None, dict]:
        """D-109: a compra é o grupo de preços pagos da mesma nota (mensagem); vale a mais recente, ou a da
        loja citada na pergunta. A IA só liga item a oferta; a comparação e o texto são do código.
        Devolve (aviso sem informação, resposta com citações, códigos citáveis "PC"/"PQ")."""
        compras: dict[str, list[dict]] = {}
        for p in historico or []:
            if p["origem"] == "compra":
                compras.setdefault(p.get("mensagem_id") or "?", []).append(p)
        if not compras:
            return ("🧾 Ainda não tenho o preço pago por item de nenhuma compra. Mande a nota fiscal (XML, PDF ou "
                    "foto) e eu guardo o preço de cada item para comparar com as ofertas."), None, {}
        alvo = precos.chave(pergunta) - {"super", "merca", "compr", "ofert", "nota", "preco"}
        grupos = sorted(compras.values(), key=lambda g: g[0].get("data") or "", reverse=True)
        citada = [g for g in grupos if alvo & (precos.chave(g[0].get("loja") or "") - {"super", "merca"})]
        grupo = (citada or grupos)[0]
        data_compra = grupo[0].get("data") or ""
        ofertas = [p for p in precos.sem_repetidos(historico) if p["origem"] == "oferta"
                   and (not p.get("data") or p["data"] >= data_compra)][:150]
        if not ofertas:
            return (f"🧾 Tenho os preços da sua compra no {grupo[0].get('loja') or 'mercado'}, mas nenhuma oferta "
                    "registrada válida no dia da compra. Mande o panfleto do mercado (foto, PDF ou texto) para eu "
                    "comparar."), None, {}
        codigos_c = {f"C{i}": p for i, p in enumerate(precos.sem_repetidos(grupo)[:120], start=1)}
        codigos_q = {f"Q{i}": p for i, p in enumerate(ofertas, start=1)}
        pedido = ("Itens da compra:\n" + "\n".join(f"[{c}] {p['produto']}" for c, p in codigos_c.items())
                  + "\n\nOfertas:\n" + "\n".join(precos.linha(c, p) for c, p in codigos_q.items()))
        try:
            r = self._conversar(usuario_id, "consulta", precos.SISTEMA_COMPRA, pedido)
            dados = extracao.extrair_json(r.texto)
        except (ErroModelo, ValueError, TypeError):
            return None, None, {}
        mapa = precos.ler_mapa_compra(dados, codigos_c, codigos_q)
        texto, usados = precos.comparar_compra(codigos_c, mapa, codigos_q)
        todos = {f"P{c}": p for c, p in {**codigos_c, **codigos_q}.items()}
        citados = [f"P{c}" for c in usados] or [f"P{c}" for c in codigos_c]   # a própria compra é a fonte
        return None, {"resposta": texto, "registros_citados": citados, "sem_informacao": False}, todos

    def _termos_relacionados(self, usuario_id, pergunta, registros) -> str:
        """Nomes da memória (pessoas, empresas, coisas) que a pergunta pode estar procurando, ditos pelo
        modelo a partir da lista de nomes. Só amplia a busca; a resposta continua presa aos registros."""
        nomes: dict[str, None] = {}
        for r in registros:
            for e in r.entidades:
                nomes.setdefault(e.strip(), None)
            for m in re.findall(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ú-]{2,}", r.conteudo):
                nomes.setdefault(m, None)
        lista = [n for n in nomes if n][-800:]
        try:
            r = self._conversar(usuario_id, "roteamento", SISTEMA_RELACIONADOS,
                                f"Pergunta: {pergunta}\n\nNomes na memória:\n" + "; ".join(lista))
            termos = extracao.extrair_json(r.texto).get("termos") or []
        except Exception:
            return ""
        return " ".join(str(t) for t in termos[:15] if isinstance(t, str))

    # -- lista de pendências (atalho "✅ Pendências", D-067) ------------------------------

    TITULOS_PENDENCIAS = {"atrasadas": "⚠️ Atrasadas", "semana": "📆 Próximos 7 dias", "depois": "🗂️ Mais adiante",
                          "sem": "📥 Sem prazo", "aguardando": "⏳ Aguardando outras pessoas"}

    def _pendencias_ordenadas(self, usuario_id: str) -> list[tuple[str, str, object]]:
        """(grupo, data, pendência) na ordem da lista: atrasadas, próximos 7 dias, mais adiante,
        sem prazo, aguardando; dentro do grupo, por data."""
        _, pendencias = self.repo.memoria(usuario_id)
        hoje = _agora().date()
        semana = (hoje + timedelta(days=7)).isoformat()
        agora = _agora().isoformat(timespec="minutes")
        grupos = {g: [] for g in self.TITULOS_PENDENCIAS}
        for p in pendencias:
            # Com lembrete, vale a data do lembrete (é quando aparece); senão, o prazo.
            prazo = ((p.lembrete_em or "")[:10] or p.prazo) or None
            if p.responsavel == "aguardando":
                grupos["aguardando"].append((prazo or "9999", p))
            elif not prazo:
                grupos["sem"].append(("9999", p))
            elif prazo < hoje.isoformat() or (p.lembrete_em and len(p.lembrete_em) > 10 and p.lembrete_em < agora):
                grupos["atrasadas"].append((prazo, p))
            elif prazo <= semana:
                grupos["semana"].append((prazo, p))
            else:
                grupos["depois"].append((prazo, p))
        return [(g, pr, p) for g, itens in grupos.items() for pr, p in sorted(itens, key=lambda x: x[0])]

    @staticmethod
    def _linha_pendencia(prazo, p) -> str:
        texto = re.sub(r"^(aguardando|esperando)\s+", "", p.descricao.strip(), flags=re.I)
        texto = texto[:1].upper() + texto[1:]
        if p.responsavel == "aguardando":
            quem = p.quem or "alguém"
            texto = f"⏳ {quem} · {texto}" if quem.lower() not in texto.lower() else f"⏳ {texto}"
        elif p.lembrete_em and len(p.lembrete_em) > 10:
            texto = f"⏰ {_dia_mes(p.lembrete_em[:10])} {p.lembrete_em[11:16]} · {texto}"
        else:
            texto = f"📌 {texto}"
        if prazo != "9999" and not (p.lembrete_em and p.responsavel != "aguardando"):
            texto += f" · {_dia_mes(prazo)}"
        return texto

    def listar_pendencias(self, usuario_id: str) -> str:
        """Pendências abertas montadas pelo código direto do banco (rápido, completo, sem IA),
        agrupadas por prazo. Cada linha é uma pendência guardada, com fonte (D-040)."""
        ordem = self._pendencias_ordenadas(usuario_id)
        if not ordem:
            return "✅ Nenhuma pendência em aberto."
        partes = [f"✅ Pendências · {len(ordem)} em aberto"]
        for chave, titulo in self.TITULOS_PENDENCIAS.items():
            itens = [(pr, p) for g, pr, p in ordem if g == chave]
            if itens:
                partes.append(f"\n{titulo} {len(itens)}\n" + "\n".join(self._linha_pendencia(pr, p) for pr, p in itens))
        partes.append("\nPara dar baixa, toque em “Tratar uma a uma”, ou escreva “já fiz …” / “pode descartar …”.")
        return saida("\n".join(partes), [("🛠️ Tratar uma a uma", "pt:0")])

    # -- tratar pendências por botão (D-069) ----------------------------------------------

    def pagina_pendencias(self, usuario_id: str, alvo: str, aviso: str = "") -> str:
        """Página de 8 pendências como botões (na mesma ordem da lista); edita a mensagem no lugar."""
        try:
            pagina = max(0, int(alvo))
        except ValueError:
            pagina = 0
        ordem = self._pendencias_ordenadas(usuario_id)
        if not ordem:
            return saida((aviso + "\n\n" if aviso else "") + "✅ Nenhuma pendência em aberto.", [], editar=True)
        total_paginas = (len(ordem) - 1) // POR_PAGINA + 1
        pagina = min(pagina, total_paginas - 1)
        trecho = ordem[pagina * POR_PAGINA:(pagina + 1) * POR_PAGINA]
        icones = {"atrasadas": "⚠️", "semana": "📆", "depois": "🗂️", "sem": "📥", "aguardando": "⏳"}
        botoes = [(f"{icones[g]} {_curto(p.descricao, 40)}", f"pi:{p.id}:{pagina}") for g, _, p in trecho]
        navegar = []
        if pagina > 0:
            navegar.append(("◀️ Anteriores", f"pt:{pagina - 1}"))
        if pagina + 1 < total_paginas:
            navegar.append(("Próximas ▶️", f"pt:{pagina + 1}"))
        texto = (aviso + "\n\n" if aviso else "") + (
            f"🛠️ Pendências · {len(ordem)} em aberto · página {pagina + 1} de {total_paginas}\n"
            "Toque numa para dar baixa, descartar ou mudar o prazo.")
        return saida(texto, botoes + navegar, editar=True)

    def item_pendencia(self, usuario_id: str, alvo: str) -> str:
        pid, _, pagina = alvo.partition(":")
        item = next(((g, pr, p) for g, pr, p in self._pendencias_ordenadas(usuario_id) if p.id == pid), None)
        if item is None:
            return self.pagina_pendencias(usuario_id, pagina or "0", "Essa pendência já não está aberta.")
        g, pr, p = item
        linhas = [self._linha_pendencia(pr, p), "", self.TITULOS_PENDENCIAS[g]]
        if p.prazo:
            linhas.append(f"Prazo: {_dia_mes(p.prazo)}")
        if p.lembrete_em and len(p.lembrete_em) > 10:
            linhas.append(f"Lembrete: {_dia_mes(p.lembrete_em[:10])} às {p.lembrete_em[11:16]}")
        feito = "✔️ Chegou / resolvido" if p.responsavel == "aguardando" else "✔️ Feito"
        base = f"{p.id}:{pagina or 0}"
        return saida("\n".join(linhas), [
            (feito, f"pa:{base}:f"), ("🗑️ Descartar", f"pa:{base}:d"),
            ("📅 Amanhã", f"pa:{base}:1"), ("📅 Daqui a 1 semana", f"pa:{base}:7"),
            ("↩️ Voltar à lista", f"pt:{pagina or 0}")], editar=True)

    def acao_pendencia(self, usuario_id: str, alvo: str) -> str:
        pid, _, resto = alvo.partition(":")
        pagina, _, acao = resto.partition(":")
        p = self.repo.pendencia(usuario_id, pid) if _uuid(pid) else None
        if not p or p["situacao"] in ("concluida", "descartada"):
            return self.pagina_pendencias(usuario_id, pagina, "Essa pendência já não está aberta.")
        if acao == "f":
            aviso = self.concluir_pendencia(usuario_id, pid)
        elif acao == "d":
            self.repo.ajustar_pendencia(usuario_id, pid, None, situacao="descartada")
            aviso = f"🗑️ Descartada: {p['descricao']}."
        elif acao in ("1", "7"):
            dia = (_agora().date() + timedelta(days=int(acao))).isoformat()
            lembrete = f"{dia}T{p['lembrete_em'][11:16]}" if p.get("lembrete_em") and len(p["lembrete_em"]) > 10 else None
            self.repo.ajustar_pendencia(usuario_id, pid, None, lembrete_em=lembrete, prazo=dia)
            aviso = f"📅 Novo prazo {_dia_mes(dia)}: {p['descricao']}."
        else:
            return self.pagina_pendencias(usuario_id, pagina)
        return self.pagina_pendencias(usuario_id, pagina, aviso)

    # -- teto diário de custo por pessoa (revisão de lançamento) ----------------------

    def passou_do_teto(self, usuario_id: str) -> bool:
        """Gasto do dia (desde a meia-noite de Brasília) acima de PERAI_TETO_DIARIO_USD (padrão
        US$ 1,00): o bot para de chamar o modelo para essa pessoa até o dia virar."""
        try:
            teto = float(os.environ.get("PERAI_TETO_DIARIO_USD", "1.0"))
        except ValueError:
            teto = 1.0
        if teto <= 0:
            return False
        meia_noite = _agora().replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            gasto = self.repo.custo_desde(usuario_id, meia_noite.replace(tzinfo=FUSO).astimezone(timezone.utc))
            return gasto * fator_custo() >= teto
        except Exception as erro:
            log.warning("teto diário: %s", str(erro)[:200])
            return False

    # -- briefing automático (D-068) --------------------------------------------------

    def briefings_para_enviar(self) -> list[tuple[int, str]]:
        """Briefings dos horários ligados com /briefing: um por horário e por dia, só dentro da hora
        marcada (servidor fora do ar por mais de 1 hora = aquele horário é pulado)."""
        agora = _agora()
        saida_ = []
        for usuario_id, chat, horas in self.repo.briefings_ligados():
            for hora in horas:
                if agora.hour != hora or self.passou_do_teto(usuario_id) \
                        or not self.repo.marcar_briefing(usuario_id, agora.date(), hora):
                    continue
                try:
                    texto = self.briefing_automatico(usuario_id, hora)
                    if texto:   # None = sem novidade desde o último briefing (D-074)
                        saida_.append((chat, texto))
                except Exception as erro:
                    log.warning("briefing das %sh falhou: %s", hora, str(erro)[:300])
        return saida_

    def _sinal_briefing(self, usuario_id: str) -> tuple[frozenset, bool]:
        """O que um briefing mostraria (pendências de hoje ou atrasadas, compromissos de hoje e amanhã,
        anotações de hoje) e se há compromisso nas próximas 3 horas."""
        agora = _agora()
        hoje = agora.date().isoformat()
        registros, abertas = self.repo.memoria(usuario_id)
        itens = {("p", p.id, p.prazo, p.lembrete_em) for p in abertas
                 if (p.prazo and p.prazo <= hoje) or (p.lembrete_em or "")[:10] == hoje}
        itens |= {("r", r.id) for r in registros if (getattr(r, "anotado_em", None) or "")[:10] == hoje}
        eventos = []
        if self.agenda and self._agenda_conectada(usuario_id):
            eventos = self.agenda.eventos_completos(usuario_id, dias_antes=0, dias_depois=1)
        elif self.agenda:
            eventos = self.agenda.importados(usuario_id, hoje, (agora.date() + timedelta(days=1)).isoformat())
        urgente = False
        agora = agora if agora.tzinfo else agora.replace(tzinfo=FUSO)
        for e in eventos:
            itens.add(("e", e.id, e.inicio, e.titulo))
            try:
                inicio = datetime.fromisoformat(e.inicio)
                inicio = inicio if inicio.tzinfo else inicio.replace(tzinfo=FUSO)
                urgente = urgente or (not e.dia_inteiro and agora <= inicio <= agora + timedelta(hours=3))
            except (TypeError, ValueError):
                pass
        return frozenset(itens), urgente

    def briefing_automatico(self, usuario_id: str, hora: int) -> str | None:
        """Briefing do horário; à tarde e à noite, None quando nada mudou desde o último (D-074)."""
        sinal, urgente = self._sinal_briefing(usuario_id)
        dia = _agora().date()
        anterior = self._ultimo_briefing.get(usuario_id)
        if hora >= 11 and anterior and anterior[0] == dia and anterior[1] == sinal and not urgente:
            log.info("briefing das %sh pulado: nada novo", hora)
            return None
        self._ultimo_briefing[usuario_id] = (dia, sinal)
        if hora < 11:
            momento, pedido = "manha", ("da manhã: compromissos de hoje; tarefas de hoje ou atrasadas; quem estou "
                                        "aguardando; para cada reunião de hoje, o que já foi tratado com a pessoa")
        elif hora < 17:
            momento, pedido = "tarde", ("da tarde: compromissos que ainda faltam hoje (depois de agora) e tarefas "
                                        "de hoje ou atrasadas ainda abertas")
        else:
            momento, pedido = "noite", ("da noite: resumo do dia de hoje (o que aconteceu e o que ficou para "
                                        "fazer) e os compromissos de amanhã")
        resposta = self._responder(usuario_id, f"{BRIEFING_AUTOMATICO} {pedido}.", pode_pedir=False, sozinha=True)
        if str(resposta) in (NAO_ENCONTREI, PEDIR_BRIEFING):
            resposta = {"manha": "☀️ Bom dia! Nada na agenda nem tarefas para hoje.",
                        "tarde": "🌤️ Boa tarde! Nada mais na agenda nem tarefas para hoje.",
                        "noite": "🌙 Boa noite! Não anotei nada hoje e não há compromissos amanhã."}[momento]
        semanal = ""
        if momento == "manha" and dia.weekday() == 0:   # D-104: segunda-feira, revisão da semana das contas
            try:
                semanal = self.revisao_semana(usuario_id)
            except Exception as erro:
                log.warning("revisão semanal falhou: %s", str(erro)[:200])
        if semanal:
            resposta = _anexar(resposta, "\n\n" + semanal)
        elif momento == "manha":   # D-095: os 3 alertas de conta mais urgentes, calculados pelo código
            alertas_c = self.alertas_crm(usuario_id, 3)
            if alertas_c:
                resposta = _anexar(resposta, "\n\n🩺 Contas que pedem atenção:\n" + "\n".join(alertas_c)
                                   + "\nTudo em /saude")
        return resposta

    # -- teste das 30 perguntas da semana 4 (D-057) ---------------------------------

    def rodar_teste(self, usuario_id: str, perguntas: list[str], avisar=None) -> str:
        """Roda as perguntas como numa conversa nova, guarda numa rodada ("lote") separada do dia a dia
        e devolve o lote. `avisar(n, total)` recebe o andamento."""
        lote = _agora().strftime("teste-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        for n, pergunta in enumerate(perguntas, start=1):
            try:
                self._responder(usuario_id, pergunta, lote=lote)
            except ErroModelo as erro:
                log.warning("teste: pergunta %d falhou: %s", n, str(erro)[:200])
                self.repo.salvar_resposta(usuario_id, "teste", pergunta, "⚠️ O modelo não respondeu.", [], True,
                                          lote=lote)
            if avisar:
                avisar(n, len(perguntas))
        return lote

    def resultado_teste(self, usuario_id: str) -> list[dict]:
        return resultado_teste(self.repo, usuario_id)

    def avaliar(self, usuario_id: str, alvo: str) -> str:
        rid, _, nota = alvo.partition(":")
        if not _uuid(rid) or nota not in ("ajudou", "neutro", "atrapalhou"):
            return "Não encontrei essa resposta."
        if not self.repo.avaliar_resposta(usuario_id, rid, nota):
            return "Não encontrei essa resposta."
        return {"ajudou": "👍 Valeu! Anotado que ajudou.", "neutro": "😐 Anotado.",
                "atrapalhou": "👎 Anotado. Se quiser, me conte o que estava errado: isso vira correção."}[nota]

    def relatorio_metricas(self, usuario_id: str, desde: date) -> str:
        """Painel do teste de 4 semanas (metas do documento MVP e Critérios de Aceite)."""
        m = self.repo.metricas(usuario_id, datetime(desde.year, desde.month, desde.day))
        dias_corridos = (_agora().date() - desde).days + 1
        lat = m["latencias_ms"]
        # O custo só passou a ser calculado em 29/10 (antes, os modelos estavam sem preço).
        dias_custo = (_agora().date() - max(desde, date(2026, 10, 30))).days + 1
        p50 = lat[len(lat) // 2] / 1000 if lat else None
        p90 = lat[int(len(lat) * 0.9) - 1 if len(lat) > 1 else 0] / 1000 if lat else None
        pct_corr = 100 * m["corrigidos"] / m["registros"] if m["registros"] else 0
        av = m["avaliacoes"]
        def conta(tipo):
            return {n: av.get((tipo, n), 0) for n in ("ajudou", "neutro", "atrapalhou")}
        b, c = conta("briefing"), conta("consulta")
        ok = lambda cond: "✅" if cond else "⏳"
        custo = m["custo_usd"] * fator_custo()
        linhas = [f"📊 Teste do MVP · desde {desde:%d/%m} ({dias_corridos} dias)", "",
                  f"{ok(m['dias_com_captura'] >= min(20, dias_corridos * 20 // 28))} Uso espontâneo: "
                  f"{m['dias_com_captura']} dias com captura (meta: 20 de 28)",
                  f"{ok(pct_corr <= 10)} Correções: {m['corrigidos']} de {m['registros']} registros "
                  f"({pct_corr:.0f}%; meta ≤ 10%)",
                  f"{ok(b['ajudou'] >= 3)} Briefings que ajudaram: {b['ajudou']} (meta: 3) · neutro {b['neutro']}"
                  f" · atrapalhou {b['atrapalhou']}",
                  f"• Consultas avaliadas: 👍 {c['ajudou']} · 😐 {c['neutro']} · 👎 {c['atrapalhou']}",
                  f"• Respostas: {m['respostas']} ({m['sem_informacao']} “não encontrei”)",
                  (f"{ok(p90 is not None and p90 < 10)} Tempo de resposta do modelo: metade em até {p50:.1f} s, "
                   f"90% em até {p90:.1f} s (meta < 10 s)") if lat else "• Tempo de resposta: sem consultas ainda",
                  (f"{ok(custo / max(dias_custo, 1) * 30 <= CUSTO_META_USD)} Custo estimado: "
                   f"US$ {custo:.2f} em {dias_custo} dia(s) → US$ {custo / max(dias_custo, 1) * 30:.2f}"
                   f" em 30 dias (meta ≤ R$ 200/mês ≈ US$ {CUSTO_META_USD:.0f}; já com o ajuste de "
                   f"{fator_custo():.1f}× pela cobrança real; confira o saldo no AI Studio)"),
                  "", "A recuperação correta (meta ≥ 90%) e as memórias inventadas (meta zero) são conferidas no "
                  "teste de 30 perguntas da semana 4."]
        return "\n".join(linhas)

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
            "briefing": "briefing", "leitura_imagem": "leitura_imagem",
            "precos_imagem": "leitura_imagem", "proposta_imagem": "leitura_imagem",
            "parceria_imagem": "leitura_imagem"}.get(tarefa, "consulta")


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


def _despesas_para_ajuste(todas: list[dict], texto: str) -> list[dict]:
    """As 10 mais recentes e, além delas, as que a mensagem cita pelo nome do lugar ou pelo valor
    ("as notas do Gnann de R$ 173,80"), mesmo que mais antigas (D-113). Até 30."""
    escolhidas = list(todas[:10])
    palavras = {w for w in re.findall(r"[a-zà-ú]{4,}", _sem_acento(texto or "").lower())}
    valores = {re.sub(r"\D", "", v) for v in re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", texto or "")}
    for d in todas[10:]:
        nome = _sem_acento(f"{d.get('estabelecimento') or ''} {d['descricao']}").lower()
        cita_nome = any(w in nome for w in palavras - {"nota", "notas", "lancei", "errado", "errada", "despesa"})
        cita_valor = d["valor_centavos"] is not None and str(d["valor_centavos"]) in valores
        if cita_nome or cita_valor:
            escolhidas.append(d)
    return escolhidas[:30]


def _linha_despesa_recente(codigo: str, d: dict, ligadas: list[dict]) -> str:
    """Despesa recente para a IA ajustar ou cancelar (D-113): com o que distingue uma da outra."""
    ligada = "; ".join((v.get("cliente") or v.get("titulo") or "") for v in ligadas if v.get("cliente") or v.get("titulo"))
    return " | ".join(x for x in (
        f"[{codigo}] {d['descricao']}", d.get("estabelecimento") or "", despesas.brl(d["valor_centavos"]),
        (d["data"] or "sem data") + (f" {d['hora']}" if d.get("hora") else ""), d["categoria"],
        d["contexto"] or "sem contexto", d.get("local") or "", f"ligada a: {ligada}" if ligada else "") if x)


def _linha_despesa(d) -> str:
    partes = [despesas.brl(d.valor_centavos)]
    if d.estabelecimento:
        partes.append(d.estabelecimento)
    elif d.descricao:
        partes.append(d.descricao)
    partes.append(despesas.data_br(d.data) + (f" {d.hora}" if getattr(d, "hora", None) else ""))
    partes.append(despesas.ROTULO_CATEGORIA.get(d.categoria, d.categoria))
    refeicao = despesas.refeicao(getattr(d, "hora", None), d.categoria)
    if refeicao:
        partes.append(refeicao)
    if getattr(d, "local", None):
        partes.append(f"📍 {d.local}")
    if d.contexto:
        partes.append({"projetos_proprios": "projetos próprios", "familia": "família"}.get(d.contexto, d.contexto))
    return "🧾 Despesa: " + " · ".join(partes)


def _uuid(valor: str) -> bool:
    try:
        uuid.UUID(str(valor))
        return True
    except ValueError:
        return False


BRIEFING_AUTOMATICO = "Briefing automático"

SEM_SENSIVEIS = (
    "\n\nRegra extra deste usuário: ele NÃO autorizou guardar dados sensíveis. Não crie registro, "
    "pendência nem entidade sobre religião, fé, igreja ou saúde (doenças, sintomas, remédios, exames, "
    "terapia, consultas médicas) dele ou de outras pessoas; ignore essas partes da mensagem. Compromisso "
    "na agenda continua valendo, mas com título neutro (\"Compromisso pessoal\")."
)

SISTEMA_RELACIONADOS = (
    "Você ajuda a buscar na memória de uma pessoa. Recebe uma pergunta e a lista de nomes que aparecem "
    "na memória (pessoas, empresas, lugares, coisas). Diga quais nomes da lista a pergunta pode estar "
    "procurando, mesmo sem a palavra igual (\"meu carro\" pode ser \"Cobalt\"; \"o médico\" pode ser "
    "\"Dr Leonardo\"; \"a imobiliária\" pode ser \"Pedro Granado\"). Só nomes que estão na lista, no "
    "máximo 10; nenhum se nada tiver a ver. Responda só JSON: {\"termos\": [str]}"
)


class Saida(str):
    """Texto de resposta que pode levar botões do Telegram: [(rótulo, dados)]."""
    botoes: tuple = ()
    teclado: tuple = ()    # atalhos fixos embaixo do campo de texto (D-067)
    trocar: bool = False   # True: atualiza os botões da própria mensagem em vez de mandar outra
    editar: bool = False   # True: troca o texto e os botões da própria mensagem (D-069)


def resultado_teste(repo, usuario_id: str) -> list[dict]:
    """Última rodada do teste: pergunta, resposta e fontes em texto (para conferir e colar na planilha)."""
    linhas = repo.respostas_teste(usuario_id)
    textos = repo.textos_registros(usuario_id, [c for l in linhas for c in l["registros_citados"]])
    rotulos = {"google": "evento da agenda", "ics": "agenda importada", "ocupado": "livre/ocupado",
               "despesa": "despesa", "total": "total calculado", "preco": "preço registrado"}
    saida_ = []
    for l in linhas:
        fontes = []
        for c in l["registros_citados"]:
            t = textos.get(c)
            if t:
                quem = "" if t["dito_por"] == "eu" else f"{t['dito_por']}: "
                fontes.append(f"[{t['data']}] {quem}{t['conteudo']}")
            elif _uuid(c):   # pendência (aberta ou fechada) também é fonte
                p = repo.pendencia(usuario_id, c)
                if p:
                    fontes.append(f"pendência: {p['descricao']}")
        fontes += [rotulos.get(f.split(":", 1)[0], "fonte externa") for f in l["fontes_externas"]]
        saida_.append({"pergunta": l["pergunta"], "resposta": l["resposta"],
                       "fontes": fontes, "sem_informacao": l["sem_informacao"]})
    return saida_


def _hora_dita(texto: str, inicio: str) -> bool:
    """O horário do compromisso aparece no texto ("17h", "17:00", "às 5 da tarde", "meio-dia")?"""
    try:
        h, m = int(inicio[11:13]), int(inicio[14:16])
    except (ValueError, IndexError):
        return False
    t = texto.lower()
    h12 = h % 12 or 12
    padroes = [rf"\b0?{h}\s*(h\b|hs\b|hrs?\b|horas?\b|:\s*{m:02d})", rf"\b0?{h}h{m:02d}\b",
               rf"\b(às|as|das|até|ate|para as|pras|pra)\s+0?{h}\b",
               rf"\b0?{h12}\s*(h\b|horas?\b)?\s*(da|de)\s+(manhã|manha|tarde|noite)"]
    if h == 12 and m == 0:
        padroes.append(r"meio[- ]dia")
    if h12 != h:
        padroes.append(rf"\b(às|as|das)\s+0?{h12}\b")
    return any(re.search(p, t) for p in padroes)


def _extracao_vazia(ext) -> bool:
    return not any((ext.registros, ext.pendencias, ext.ajustes, ext.compromissos, ext.ajustes_eventos,
                    ext.cancelar_eventos, ext.mudar_eventos, ext.despesas, ext.ajustes_despesas, ext.viagens,
                    ext.duvidas, ext.entidades, ext.oportunidades, ext.ajustes_oportunidades,
                    ext.indicacoes, ext.recebimentos_indicacao, ext.ajustes_contratos,
                    ext.acordos_indicacao, ext.comissoes_projeto, ext.faturas, ext.pagamentos_cliente,
                    not crm_mod.vazio(getattr(ext, "crm", None))))


def _sem_acento(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c)).strip()


def itens_da_lista(descricao: str) -> tuple[str, list[str]]:
    """("Comprar ", ["Ovos", "Banana", "Café"]) de "Comprar Ovos, Banana e Café"."""
    m = re.match(r"^(.*?\b(?:comprar|levar|buscar|pegar|lista(?: de compras)?:?)\s+)(.+)$", descricao.strip(), re.I)
    prefixo, corpo = (m.group(1), m.group(2)) if m else ("", descricao.strip())
    corpo = corpo.rstrip(" .")
    return prefixo, [i.strip() for i in re.split(r",\s*|\s+e\s+(?=[^,]*$)", corpo) if i.strip()]


def mudar_lista(descricao: str, incluir: list[str], retirar: list[str]) -> str:
    """Inclui e retira itens de uma pendência que é lista ("Comprar ovos, banana e café"), sem a IA
    reescrever a descrição: o que estava lá continua igual (D-087)."""
    prefixo, itens = itens_da_lista(descricao)
    tirar = {_sem_acento(t) for t in retirar}
    itens = [i for i in itens if _sem_acento(i) not in tirar
             and not any(t and (t in _sem_acento(i) or _sem_acento(i) in t) for t in tirar)]
    for novo in incluir:
        novo = novo[:1].upper() + novo[1:] if itens and itens[0][:1].isupper() else novo
        itens = precos.itens_unicos(itens + [novo])   # "1 caixinha de creme de leite" junta com "Creme de leite"
    if not itens:
        return descricao
    lista = itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]
    return f"{prefixo}{lista}"


def _limpar_duvida(texto: str) -> str:
    """Tira códigos internos ([E1-E4], [P2]...) que o modelo às vezes deixa escapar."""
    texto = re.sub(r"\s*(?:com (?:o )?(?:ID|código)s?\s*)?\[[PEGADTR]\d+(?:\s*[-–,]\s*[PEGADTR]?\d+)*\]", "", texto)
    return re.sub(r"\s{2,}", " ", texto).strip()


def _dia_mes(dia: str) -> str:
    return f"{dia[8:10]}/{dia[5:7]}"


def _dia_hora(inicio: str | None) -> str:
    """'2026-10-29T19:30' -> '29/10 às 19:30'; '2026-10-29' -> '29/10'."""
    if not inicio:
        return "data não informada"
    return _dia_mes(inicio) + (f" às {inicio[11:16]}" if len(inicio) > 10 else "")


# Consulta pública da Sefaz: alguns sites de governo recusam leitor que não se parece com navegador.
NAVEGADOR = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0 Safari/537.36", "Accept-Language": "pt-BR,pt;q=0.9"}
MAX_ITENS_NOTA = 120      # limite de segurança; nota de mercado comum tem 30 a 60 itens
ITENS_POR_PAGINA = 10
MAX_LINHAS_NOTA = 40       # itens listados na mensagem; o resto fica nos botões


def _desconto_nota(itens: list[dict]) -> int:
    """Soma dos itens acima do total pago = desconto da nota (0 se não dá para saber)."""
    total = itens[0].get("total_nota") if itens else None
    if total is None or any(i["valor_centavos"] is None for i in itens):
        return 0
    return max(0, sum(i["valor_centavos"] for i in itens) - total)


def _valor_itens(itens: list[dict]) -> int | None:
    """Todos os itens: o total da nota (com descontos). Parte: a soma dos itens escolhidos, com o
    desconto da nota distribuído na proporção do valor de cada item (D-108)."""
    dentro = [i for i in itens if i["incluido"]]
    total = itens[0].get("total_nota") if itens else None
    if len(dentro) == len(itens) and total is not None:
        return total
    if any(i["valor_centavos"] is None for i in dentro):
        return None
    parte = sum(i["valor_centavos"] for i in dentro)
    desconto = _desconto_nota(itens)
    if desconto:
        soma = sum(i["valor_centavos"] for i in itens)
        return int((Decimal(parte) * Decimal(total) / Decimal(soma)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return parte


def _botoes_itens(despesa_id: str, itens: list[dict], pagina: int = 0) -> list:
    """Até 12 itens: todos os botões. Acima: páginas de 10, com Anterior/Próxima (D-108)."""
    if len(itens) > 12:
        paginas = (len(itens) + ITENS_POR_PAGINA - 1) // ITENS_POR_PAGINA
        pagina = min(max(pagina, 0), paginas - 1)
        mostrar = itens[pagina * ITENS_POR_PAGINA:(pagina + 1) * ITENS_POR_PAGINA]
    else:
        paginas, mostrar = 1, itens
    num = (lambda i: f"{i['n']}. ") if paginas > 1 else (lambda i: "")
    botoes = [(f"{'✅' if i['incluido'] else '⬜'} {num(i)}{i['descricao'][:26 if paginas == 1 else 22]} · "
               f"{despesas.brl(i['valor_centavos'])}", f"di:{despesa_id}:{i['n']}") for i in mostrar]
    if paginas > 1:
        if pagina > 0:
            botoes.append((f"◀️ Itens anteriores", f"di:{despesa_id}:p{pagina - 1}"))
        if pagina < paginas - 1:
            botoes.append((f"▶️ Próximos itens ({pagina + 2}/{paginas})", f"di:{despesa_id}:p{pagina + 1}"))
    return botoes + [("✔️ Confirmar itens", f"di:{despesa_id}:0")]


def _anexar(resposta, extra: str):
    """Acrescenta texto a uma resposta sem perder os botões dela."""
    nova = Saida(str(resposta) + extra)
    for k in ("botoes", "teclado", "trocar", "editar"):
        setattr(nova, k, getattr(resposta, k, getattr(Saida, k)))
    return nova


def saida(texto: str, botoes=(), trocar: bool = False, editar: bool = False) -> Saida:
    s = Saida(texto)
    s.botoes = tuple(botoes)
    s.trocar = trocar
    s.editar = editar
    return s


POR_PAGINA = 8


def _curto(texto: str, n: int) -> str:
    texto = re.sub(r"^(aguardando|esperando)\s+", "", texto.strip(), flags=re.I)
    return texto if len(texto) <= n else texto[:n - 1].rstrip() + "…"


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
            return "❓ Antes de mexer em algo, uma dúvida: " + _limpar_duvida(ext.duvidas[0])
        return "✅ Anotado."
    linhas = ["✅ Anotado."] if (ext.registros or ext.pendencias) else []
    for r in ext.registros:
        if ajustados and not ext.pendencias and r.natureza == "intencao":
            continue  # o ajuste abaixo já diz o que mudou
        rotulo = NATUREZA_ROTULO.get(r.natureza, "")
        de_outro = r.dito_por and r.dito_por.lower() != "eu"
        if r.natureza == "opiniao":
            rotulo = f"opinião de {r.dito_por}: " if de_outro else "sua opinião: "
        elif de_outro:
            # Quem disse aparece sempre que não foi você (D-044).
            rotulo = (rotulo.rstrip(": ") + f" ({r.dito_por}): ") if rotulo else f"{r.dito_por}: "
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
        if getattr(a, "descricao", None):
            linhas.append(f"📝 Lista atualizada: {a.descricao}.")
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
        linhas.append("❓ Fiquei com uma dúvida: " + _limpar_duvida(ext.duvidas[0]))
    elif any(p.responsavel == "eu" and not p.lembrete_em for p in ext.pendencias):
        linhas.append("⏰ Se quiser lembrete, é só pedir: “me lembra sexta às 9h de…”.")
    return "\n".join(linhas)
