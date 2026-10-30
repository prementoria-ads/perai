"""O coração do Peraí, independente do canal (D-002).

Recebe uma mensagem já baixada (texto, áudio, imagem ou conversa exportada), guarda
a original, transforma em texto, decide se é algo para registrar ou uma pergunta, e
devolve a resposta que o canal vai mostrar.
"""

from __future__ import annotations

import logging
import os
import re
import zipfile
import xml.etree.ElementTree as ET
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from perai import consulta, despesas, extracao, ics, recorrencia, viagens
from perai.armazenamento import NovaMensagem, Repositorio
from perai.config import Modelo, Transcritor
from perai.leitura import (EXTENSOES_OFFICE, EXTENSOES_TEXTO, ler_conversa_exportada, ler_imagem, ler_office,
                           ler_pdf, ler_texto)
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
        # Briefing que pediu informações: a próxima mensagem do usuário (até 30 min) refaz o briefing.
        self._briefing_pendente: dict[str, tuple[str, datetime]] = {}
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
        if entrada.tipo == "documento" and entrada.arquivo and entrada.arquivo.suffix.lower() == ".xml":
            return self._nota_xml(usuario_id, entrada)
        if entrada.arquivo and (entrada.arquivo.suffix.lower() == ".ics" or (
                entrada.tipo == "conversa_exportada" and ics.zip_tem_ics(entrada.arquivo))):
            return self._agenda_ics(usuario_id, entrada)
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
                self._briefing_pendente.pop(usuario_id, None)
                return self._responder(usuario_id, texto), texto
            registro = self._registrar(usuario_id, mensagem_id, entrada.tipo, texto)
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
        resumo = despesas.resumo_nota(nota) if nota else ""
        mensagem_id = self.repo.salvar_mensagem(usuario_id, NovaMensagem(
            canal=entrada.canal, tipo="documento", texto_original=entrada.texto, transcricao=resumo or None,
            caminho_arquivo=str(entrada.arquivo), data_situacao=_agora().date().isoformat(),
            modelo_transcricao="leitor-nfe" if nota else None))
        if not nota:
            return "🤔 Guardei o arquivo, mas esse XML não parece uma nota fiscal eletrônica.", ""
        conteudo = f"Compra de {despesas.brl(nota.total_centavos)} em {nota.emitente}"
        ext = extracao.Extracao("1", registros=[extracao.Registro(
            "R1-1", "1", conteudo, "confirmado", "eu", nota.data, [nota.emitente], 1.0, False)])
        mapa = self.repo.salvar_extracao(usuario_id, mensagem_id, ext)
        d = extracao.Despesa(descricao=f"Compra em {nota.emitente}", valor_centavos=nota.total_centavos,
                             data=nota.data, categoria=despesas.categoria_por_itens(nota),
                             estabelecimento=nota.emitente, cnpj=nota.cnpj, forma_pagamento=nota.pagamento,
                             contexto=None)
        despesa_id = self.repo.salvar_despesa(usuario_id, mensagem_id, mapa.get("R1-1"), d, "xml", nota.chave)
        if despesa_id is None:
            return f"🧾 Essa nota ({nota.emitente}, {despesas.brl(nota.total_centavos)}) já estava registrada.", resumo
        linhas = [_linha_despesa(d), "📄 Lida direto do XML da nota, sem IA: valor exato.",
                  "Se for de trabalho, diga “essa é profissional”."]
        itens_linhas, botoes = self._oferecer_itens(usuario_id, despesa_id, nota.itens, nota.total_centavos)
        if not botoes:
            itens_linhas, botoes = self._pergunta_vinculo(usuario_id, despesa_id, d)
        return saida("\n".join(linhas + itens_linhas), botoes), resumo

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
            t = self.cliente.transcrever(self.transcritor, entrada.arquivo)
            self.repo.registrar_chamada(usuario_id, "transcricao", self.transcritor.fornecedor,
                                        self.transcritor.id_api, segundos_audio=t.segundos_audio,
                                        custo_usd=t.custo_usd, latencia_ms=t.latencia_ms)
            return t.texto, t.texto, self.transcritor.id_api
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

    def _registrar(self, usuario_id, mensagem_id, tipo, texto) -> str:
        anteriores, abertas = self.repo.memoria(usuario_id)
        codigos = {f"P{i}": p for i, p in enumerate(abertas[-30:], start=1)}
        conectada = self._agenda_conectada(usuario_id)
        criados = self.agenda.criados_futuros(usuario_id)[:30] if conectada else []
        codigos_ev = {f"E{i}": e for i, e in enumerate(criados, start=1)}
        recentes = self.repo.despesas(usuario_id, _agora().date() - timedelta(days=60))[:10]
        codigos_g = {f"G{i}": d for i, d in enumerate(recentes, start=1)}
        pedido = extracao.montar_pedido(
            "1", texto, _hoje_extenso(), tipo, self.repo.entidades_conhecidas(usuario_id),
            conversa=self._conversa_recente(usuario_id),
            pendencias_abertas=[f"[{c}] {_descrever_pendencia(p)}" for c, p in codigos.items()],
            eventos_criados=[f"[{c}] {e['titulo']} | {e['inicio'].replace('T', ' ')}" for c, e in codigos_ev.items()],
            despesas_recentes=[f"[{c}] {d['descricao']} | {despesas.brl(d['valor_centavos'])} | "
                               f"{d['data'] or 'sem data'} | {d['categoria']} | {d['contexto'] or 'sem contexto'}"
                               for c, d in codigos_g.items()],
        )
        r = self._conversar(usuario_id, "extracao", extracao.SISTEMA_EXTRACAO, pedido)
        ext = extracao.interpretar("1", r.texto)
        if not ext.json_valido:
            return "⚠️ Guardei a mensagem como chegou, mas não consegui organizar agora. Ela não se perde."
        if tipo in ("texto", "audio") and len(texto) < 300 and _extracao_vazia(ext):
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
            if not (a.lembrete_em or a.prazo or a.situacao):
                continue
            self.repo.ajustar_pendencia(usuario_id, alvo.id, mensagem_id, a.lembrete_em, a.prazo, a.situacao)
            if a.situacao == "concluida":
                self._avancar(usuario_id, alvo.id)
            ajustados.append((alvo, a))
        linhas_agenda, botoes = self._aplicar_agenda(usuario_id, mensagem_id, ext, mapa, codigos_ev, conectada)
        linhas_desp, botoes_desp = self._aplicar_despesas(usuario_id, mensagem_id, tipo, ext, mapa, codigos_g)
        linhas_viag, botoes_viag = self._aplicar_viagens(usuario_id, mensagem_id, ext)
        linhas_extra = linhas_agenda + linhas_desp + linhas_viag
        botoes = list(botoes) + botoes_desp + botoes_viag
        texto = _resumo_registro(ext, corrigidos, descartadas, ajustados)
        if linhas_extra:
            sozinho = texto == "✅ Anotado." and not ext.registros and not ext.pendencias
            texto = ("" if sozinho else texto + "\n") + "\n".join(linhas_extra)
        return saida(texto, botoes)

    def _aplicar_despesas(self, usuario_id, mensagem_id, tipo, ext, mapa, codigos_g):
        linhas, botoes = [], []
        registro_id = next((v for k, v in mapa.items() if k.startswith("R")), None)
        origem = {"documento": "pdf"}.get(tipo, tipo)
        salvas = []
        for d in ext.despesas:
            if d.data is None:
                d.data = _agora().date().isoformat()
            despesa_id = self.repo.salvar_despesa(usuario_id, mensagem_id, registro_id, d, origem)
            linhas.append(_linha_despesa(d))
            if d.valor_centavos is None:
                linhas.append("❓ Não consegui ver o valor. Quanto foi?")
            if despesa_id:
                for cliente in d.clientes:
                    self.repo.vincular_despesa(usuario_id, despesa_id, "cliente", cliente=cliente)
                if d.clientes:
                    linhas.append("🤝 Cliente: " + ", ".join(d.clientes))
                salvas.append((despesa_id, d))
        if any(d.contexto == "profissional" for d in ext.despesas):
            linhas.append("💼 Se for da L8, lance também no CRM de Contas: lá é o registro oficial.")
        # Pergunta a qual compromisso a despesa se liga (para o relatório de viagem no Onfly).
        if len(salvas) == 1:
            despesa_id, d = salvas[0]
            pergunta, opcoes = self._oferecer_itens(usuario_id, despesa_id, d.itens, d.valor_centavos)
            if not opcoes:
                pergunta, opcoes = self._pergunta_vinculo(usuario_id, despesa_id, d)
            linhas += pergunta
            botoes += opcoes
        for a in ext.ajustes_despesas:
            alvo = codigos_g.get(a.despesa)
            if alvo is None:
                continue
            if a.cancelar:
                self.repo.mudar_despesa(usuario_id, alvo["id"], mensagem_id, situacao="cancelada")
                linhas.append(f"🗑️ Tirei a despesa: {alvo['descricao']} ({despesas.brl(alvo['valor_centavos'])}).")
                continue
            for cliente in a.clientes:
                self.repo.vincular_despesa(usuario_id, alvo["id"], "cliente", cliente=cliente)
            if a.clientes:
                linhas.append(f"🤝 Liguei “{alvo['descricao']}” a: " + ", ".join(a.clientes) + ".")
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
        return linhas, botoes

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
        """Muda horário ou nome de eventos que o Peraí não criou (D-060): só a próxima vez."""
        linhas, algum = [], False
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
                linhas.append("🔁 Só esta vez; as outras continuam no horário de sempre."
                              + (" Mudar todas de uma vez ainda não consigo: dá para mudar a série no "
                                 "Google Agenda." if m.todas else ""))
        return linhas, algum

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
        viagem = next((v for v in self.repo.viagens(usuario_id)
                       if d.data and v["inicio"] <= d.data <= v["fim"]), None) if d.data else None
        aviso = [f"🧳 Entra no relatório da viagem: {viagem['nome']}"] if viagem else []
        linhas, botoes = self._pergunta_vinculo_agenda(usuario_id, despesa_id, d)
        return aviso + linhas, botoes

    def _pergunta_vinculo_agenda(self, usuario_id, despesa_id, d) -> tuple[list[str], list]:
        if d.contexto in ("familia", "igreja") or d.clientes:
            return [], []
        eventos = []
        if self._agenda_conectada(usuario_id) and d.data:
            eventos = self.agenda.eventos_em(usuario_id, date.fromisoformat(d.data))[:5]
        if eventos:
            botoes = [(f"🔗 {e.inicio[11:16]} {e.titulo[:30]}", f"vd:{despesa_id}:{i}")
                      for i, e in enumerate(eventos, start=1)]
            botoes.append(("✔️ Pronto / nenhum", f"vd:{despesa_id}:0"))
            return ["🔗 Essa despesa tem a ver com algum compromisso? Toque em um ou mais."], botoes
        return ["🔗 Foi com algum cliente? Se foi, me diga (ex.: “foi com a Gazin”)."], []

    # -- itens da nota: quais entram no lançamento (D-061) -------------------------------

    def _oferecer_itens(self, usuario_id, despesa_id, itens, total) -> tuple[list[str], list]:
        """Nota com 2 ou mais itens: mostra os itens para o usuário confirmar o que entra."""
        itens = [(desc, v) for desc, v in (itens or []) if desc][:12]
        if len(itens) < 2:
            return [], []
        self.repo.salvar_itens_despesa(usuario_id, despesa_id, itens, total)
        guardados = self.repo.itens_despesa(usuario_id, despesa_id)
        linhas = [f"📋 A nota tem {len(itens)} itens:"]
        linhas += [f"  {i['n']}. {i['descricao']} · {despesas.brl(i['valor_centavos'])}" for i in guardados]
        linhas.append("Se você lança só parte (ex.: só o combustível), toque nos itens que NÃO entram "
                      "e depois em Confirmar.")
        return linhas, _botoes_itens(despesa_id, guardados)

    def escolher_item(self, usuario_id: str, alvo: str):
        """Botão "di:<despesa>:<n>": tira ou põe o item n no lançamento; n = 0 confirma."""
        despesa_id, _, n = alvo.partition(":")
        if not _uuid(despesa_id) or not n.isdigit():
            return "Não encontrei essa despesa."
        d = self.repo.despesa(usuario_id, despesa_id)
        itens = self.repo.itens_despesa(usuario_id, despesa_id) if d else []
        if not itens:
            return "Não encontrei essa despesa."
        if n != "0":
            item = next((i for i in itens if i["n"] == int(n)), None)
            if item is None:
                return "Esse item não está na nota."
            item["incluido"] = not item["incluido"]
            if not any(i["incluido"] for i in itens):
                return saida("Pelo menos um item precisa entrar. Se a despesa toda não vale, diga “lancei errado”.",
                             _botoes_itens(despesa_id, itens), trocar=True)
            self.repo.marcar_item_despesa(usuario_id, despesa_id, item["n"], item["incluido"])
            novo = _valor_itens(itens)
            if novo != d["valor_centavos"]:
                self.repo.mudar_despesa(usuario_id, despesa_id, None, valor_centavos=novo)
            estado = "entra" if item["incluido"] else "não entra"
            return saida(f"{item['descricao'][:40]} {estado}. Valor: {despesas.brl(novo)}",
                         _botoes_itens(despesa_id, itens), trocar=True)
        dentro = [i for i in itens if i["incluido"]]
        texto = (f"🧾 Lançamento: {despesas.brl(d['valor_centavos'])} · "
                 + (f"todos os {len(itens)} itens" if len(dentro) == len(itens) else
                    f"{len(dentro)} de {len(itens)} itens: " + ", ".join(i["descricao"] for i in dentro)))
        dd = extracao.Despesa(d["descricao"], d["valor_centavos"], d["data"], d["categoria"],
                              d.get("estabelecimento"), None, None, d.get("contexto"))
        pergunta, botoes = self._pergunta_vinculo(usuario_id, despesa_id, dd)
        return saida("\n".join([texto] + pergunta), botoes)

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
        eventos = self.agenda.eventos_em(usuario_id, date.fromisoformat(d["data"]))[:5] if (
            self._agenda_conectada(usuario_id) and d["data"]) else []
        if int(n) > len(eventos):
            return "Esse compromisso não está mais na agenda."
        e = eventos[int(n) - 1]
        self.repo.vincular_despesa(usuario_id, despesa_id, "evento", e.id, e.titulo, e.inicio)
        return f"🔗 Liguei a despesa a: {e.titulo}. Se teve outro, toque nele; senão, em Pronto."

    # -- viagens planejadas (D-056) -------------------------------------------------------

    def _aplicar_viagens(self, usuario_id, mensagem_id, ext):
        linhas, botoes = [], []
        for v in ext.viagens:
            ini, fim = date.fromisoformat(v.inicio), date.fromisoformat(v.fim)
            nome = v.nome or viagens.nome_padrao(ini, fim, v.objetivo, v.cidades, v.acompanhantes)
            vid = self.repo.salvar_viagem(usuario_id, mensagem_id, nome, v.inicio, v.fim, v.cidades,
                                          v.acompanhantes, v.objetivo)
            linhas.append(f"🧳 Viagem anotada: {nome}")
            linhas += self._plano_viagem(usuario_id, ini, fim)
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
        horas = {d["id"]: min((v["inicio"][11:16] for v in vinculos.get(d["id"], [])
                               if v["tipo"] == "evento" and v.get("inicio")), default="99:99") for d in lista}
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
            linhas.append(f"• {despesas.brl(d['valor_centavos'])} · {despesas.ROTULO_CATEGORIA.get(d['categoria'], d['categoria'])}"
                          f" · {d.get('estabelecimento') or d['descricao']}" + (f" · 🔗 {com}" if com else " · sem cliente"))
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

    def _responder(self, usuario_id, pergunta, pode_pedir: bool = True) -> str:
        registros, pendencias = self.repo.memoria(usuario_id)
        tipo = "briefing" if "prepar" in pergunta.lower() else "consulta"
        # Memória grande: só o que tem a ver com a pergunta vai ao modelo (tempo de resposta e custo).
        registros = consulta.selecionar(registros, pergunta, " ".join(self._conversa_recente(usuario_id)[-4:]),
                                        _agora().date().isoformat())
        memoria = consulta.formatar_memoria(registros, pendencias)
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
                f"[{c}] (despesa; {d['data'] or 'sem data'}; {d['categoria']}; {d['contexto'] or 'sem contexto'}) "
                f"{d['descricao']}: {despesas.brl(d['valor_centavos'])}" for c, d in codigos_d.items())
            memoria += "\n" + "\n".join(f"[{c}] (total calculado pelo Peraí) {t}" for c, t in codigos_t.items())
        pedido = consulta.montar_pedido(pergunta, memoria.strip() or "(nenhum item de memória)",
                                        f"{_hoje_extenso()}, agora {_agora():%H:%M}",
                                        self._conversa_recente(usuario_id))
        r = self._conversar(usuario_id, tipo, consulta.SISTEMA_CONSULTA, pedido)
        ids = {x.id for x in registros} | {p.id for p in pendencias} | set(codigos_ag) | set(codigos_d) | set(codigos_t)
        resp = consulta.interpretar(pergunta, r.texto, ids)
        especiais = set(codigos_ag) | set(codigos_d) | set(codigos_t)
        citados = [c for c in resp.registros_citados if c not in especiais]
        externos = ([codigos_ag[c].id if codigos_ag[c].id.startswith(("ics:", "ocupado:")) else
                     f"google:{codigos_ag[c].id}" for c in resp.registros_citados if c in codigos_ag]
                    + [f"despesa:{codigos_d[c]['id']}" for c in resp.registros_citados if c in codigos_d]
                    + [f"total:{c}" for c in resp.registros_citados if c in codigos_t])
        # Nada sem fonte (D-040): sem citação válida, a resposta só pode dizer que não sabe.
        if tipo == "briefing" and pode_pedir and resp.json_valido and (
                resp.pedir_informacoes or not (citados or externos) or resp.sem_informacao):
            self._briefing_pendente[usuario_id] = (pergunta, _agora())
            if not (citados or externos) or resp.sem_informacao:
                self.repo.salvar_resposta(usuario_id, tipo, pergunta, PEDIR_BRIEFING, [], True)
                return PEDIR_BRIEFING
        if not (citados or externos) or resp.sem_informacao or not resp.json_valido:
            texto = NAO_ENCONTREI
            self.repo.salvar_resposta(usuario_id, tipo, pergunta, texto, [], True)
            return texto
        rid = self.repo.salvar_resposta(usuario_id, tipo, pergunta, resp.resposta, citados, False, externos)
        # Avaliação opcional: mede a utilidade do briefing e das respostas no teste de 4 semanas.
        return saida(resp.resposta, [("👍 Ajudou", f"av:{rid}:ajudou"), ("😐 Neutro", f"av:{rid}:neutro"),
                                     ("👎 Atrapalhou", f"av:{rid}:atrapalhou")])

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
                  (f"{ok(m['custo_usd'] / max(dias_custo, 1) * 30 <= CUSTO_META_USD)} Custo estimado: "
                   f"US$ {m['custo_usd']:.2f} em {dias_custo} dia(s) → US$ {m['custo_usd'] / max(dias_custo, 1) * 30:.2f}"
                   f" em 30 dias (meta ≤ R$ 200/mês ≈ US$ {CUSTO_META_USD:.0f}; estimativa: confira o saldo no AI Studio)"),
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


def _linha_despesa(d) -> str:
    partes = [despesas.brl(d.valor_centavos)]
    if d.estabelecimento:
        partes.append(d.estabelecimento)
    elif d.descricao:
        partes.append(d.descricao)
    partes.append(despesas.data_br(d.data))
    partes.append(despesas.ROTULO_CATEGORIA.get(d.categoria, d.categoria))
    if d.contexto:
        partes.append({"projetos_proprios": "projetos próprios", "familia": "família"}.get(d.contexto, d.contexto))
    return "🧾 Despesa: " + " · ".join(partes)


def _uuid(valor: str) -> bool:
    try:
        uuid.UUID(str(valor))
        return True
    except ValueError:
        return False


class Saida(str):
    """Texto de resposta que pode levar botões do Telegram: [(rótulo, dados)]."""
    botoes: tuple = ()
    trocar: bool = False   # True: atualiza os botões da própria mensagem em vez de mandar outra


def _extracao_vazia(ext) -> bool:
    return not any((ext.registros, ext.pendencias, ext.ajustes, ext.compromissos, ext.ajustes_eventos,
                    ext.cancelar_eventos, ext.mudar_eventos, ext.despesas, ext.ajustes_despesas, ext.viagens,
                    ext.duvidas, ext.entidades))


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


def _valor_itens(itens: list[dict]) -> int | None:
    """Todos os itens: o total da nota (com descontos). Parte: a soma dos itens escolhidos."""
    dentro = [i for i in itens if i["incluido"]]
    total = itens[0].get("total_nota") if itens else None
    if len(dentro) == len(itens) and total is not None:
        return total
    if any(i["valor_centavos"] is None for i in dentro):
        return None
    return sum(i["valor_centavos"] for i in dentro)


def _botoes_itens(despesa_id: str, itens: list[dict]) -> list:
    botoes = [(f"{'✅' if i['incluido'] else '⬜'} {i['descricao'][:26]} · {despesas.brl(i['valor_centavos'])}",
               f"di:{despesa_id}:{i['n']}") for i in itens]
    return botoes + [("✔️ Confirmar itens", f"di:{despesa_id}:0")]


def saida(texto: str, botoes=(), trocar: bool = False) -> Saida:
    s = Saida(texto)
    s.botoes = tuple(botoes)
    s.trocar = trocar
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
