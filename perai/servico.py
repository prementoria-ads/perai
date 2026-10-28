"""O coração do Peraí, independente do canal (D-002).

Recebe uma mensagem já baixada (texto, áudio, imagem ou conversa exportada), guarda
a original, transforma em texto, decide se é algo para registrar ou uma pergunta, e
devolve a resposta que o canal vai mostrar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from perai import consulta, extracao
from perai.armazenamento import NovaMensagem, Repositorio
from perai.config import Modelo, Transcritor
from perai.leitura import ler_conversa_exportada, ler_imagem
from perai.llm import Cliente, ErroModelo

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
                 transcritor: Transcritor, leitor_imagem: Modelo, pasta_temporaria: Path):
        self.repo = repo
        self.cliente = cliente
        self.modelo = modelo
        self.transcritor = transcritor
        self.leitor_imagem = leitor_imagem
        self.pasta_temporaria = pasta_temporaria

    # -- chamadas aos modelos, sempre registradas em chamadas_modelo ------------------

    def _conversar(self, usuario_id, tarefa, sistema, pedido, **kw):
        try:
            r = self.cliente.conversar(self.modelo if tarefa != "leitura_imagem" else self.leitor_imagem,
                                       tarefa, sistema, pedido, **kw)
        except ErroModelo as erro:
            self.repo.registrar_chamada(usuario_id, _finalidade(tarefa), self.modelo.fornecedor,
                                        self.modelo.id_api, erro=str(erro)[:500])
            raise
        self.repo.registrar_chamada(usuario_id, _finalidade(tarefa), self.modelo.fornecedor, self.modelo.id_api,
                                    r.tokens_entrada, r.tokens_saida, 0, r.custo_usd, r.latencia_ms)
        return r

    # -- entrada ---------------------------------------------------------------------

    def receber(self, usuario_id: str, entrada: Entrada) -> str:
        texto, transcricao, modelo_transcricao = self._para_texto(usuario_id, entrada)
        mensagem_id = self.repo.salvar_mensagem(usuario_id, NovaMensagem(
            canal=entrada.canal, tipo=entrada.tipo, texto_original=entrada.texto,
            transcricao=transcricao,
            caminho_arquivo=str(entrada.arquivo) if entrada.arquivo else None,
            data_situacao=_agora().date().isoformat(), modelo_transcricao=modelo_transcricao,
        ))
        if not texto.strip():
            return "Recebi, mas não consegui ler nada aqui. Pode mandar de novo ou escrever?"
        try:
            if entrada.tipo in ("texto", "audio") and self._e_pergunta(usuario_id, texto):
                return self._responder(usuario_id, texto)
            return self._registrar(usuario_id, mensagem_id, entrada.tipo, texto)
        except ErroModelo:
            return ("Guardei a sua mensagem original, mas o modelo de IA não respondeu agora. "
                    "Ela continua salva; tente de novo em instantes.")

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
        if not _parece_pergunta(texto) and not texto.lower().startswith("me lembra"):
            return False
        # Casos ambíguos ("me lembra..."): o modelo decide.
        r = self._conversar(usuario_id, "roteamento", SISTEMA_ROTEADOR, texto)
        try:
            tipo = extracao.extrair_json(r.texto).get("tipo")
        except (ValueError, AttributeError):
            tipo = None
        return tipo == "pergunta" if tipo in ("pergunta", "registro") else _parece_pergunta(texto)

    # -- registrar -----------------------------------------------------------------

    def _registrar(self, usuario_id, mensagem_id, tipo, texto) -> str:
        pedido = extracao.montar_pedido("1", texto, _agora().date().isoformat(), tipo,
                                        self.repo.entidades_conhecidas(usuario_id))
        r = self._conversar(usuario_id, "extracao", extracao.SISTEMA_EXTRACAO, pedido)
        ext = extracao.interpretar("1", r.texto)
        if not ext.json_valido:
            return "Guardei a mensagem como chegou, mas não consegui organizar agora. Ela não se perde."
        anteriores, _ = self.repo.memoria(usuario_id)
        mapa = self.repo.salvar_extracao(usuario_id, mensagem_id, ext)
        corrigidos = self._aplicar_correcoes(usuario_id, mensagem_id, ext, mapa, anteriores)
        return _resumo_registro(ext, corrigidos)

    def _aplicar_correcoes(self, usuario_id, mensagem_id, ext, mapa, anteriores) -> list[str]:
        """Corrige o registro anterior mais parecido com o que o usuário disse que mudou."""
        feitos = []
        for r in ext.registros:
            if not r.corrige:
                continue
            alvo = _mais_parecido(r.corrige + " " + r.conteudo, anteriores)
            if alvo is None:
                continue
            self.repo.corrigir_registro(usuario_id, alvo.id, mapa[r.id], mensagem_id)
            feitos.append(alvo.conteudo)
        return feitos

    # -- responder -----------------------------------------------------------------

    def _responder(self, usuario_id, pergunta) -> str:
        registros, pendencias = self.repo.memoria(usuario_id)
        tipo = "briefing" if "prepar" in pergunta.lower() else "consulta"
        memoria = consulta.formatar_memoria(registros, pendencias)
        pedido = consulta.montar_pedido(pergunta, memoria, _agora().date().isoformat())
        r = self._conversar(usuario_id, tipo, consulta.SISTEMA_CONSULTA, pedido)
        ids = {x.id for x in registros} | {p.id for p in pendencias}
        resp = consulta.interpretar(pergunta, r.texto, ids)
        # Nada sem fonte (D-040): sem citação válida, a resposta só pode dizer que não sabe.
        if not resp.registros_citados or resp.sem_informacao or not resp.json_valido:
            texto = "Não encontrei isso nos seus registros."
            self.repo.salvar_resposta(usuario_id, tipo, pergunta, texto, [], True)
            return texto
        self.repo.salvar_resposta(usuario_id, tipo, pergunta, resp.resposta, resp.registros_citados, False)
        return resp.resposta

    # -- lembretes -----------------------------------------------------------------

    def lembretes_para_enviar(self):
        """Lembretes vencidos. Só existem os que o usuário pediu (D-007)."""
        return self.repo.lembretes_vencidos(_agora())

    def lembrete_enviado(self, pendencia_id: str) -> None:
        self.repo.marcar_lembrete_enviado(pendencia_id, _agora())


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


def _resumo_registro(ext: extracao.Extracao, corrigidos: list[str]) -> str:
    linhas = ["Anotado."]
    for r in ext.registros:
        rotulo = NATUREZA_ROTULO.get(r.natureza, "")
        if r.natureza == "opiniao":
            rotulo = f"opinião de {r.dito_por}: " if r.dito_por.lower() != "eu" else "sua opinião: "
        linhas.append(f"• {rotulo}{r.conteudo}")
    for p in ext.pendencias:
        quem = "Você" if p.responsavel == "eu" else f"Aguardando {p.quem or 'alguém'}"
        linha = f"• {quem}: {p.descricao}{_formatar_prazo(p)}"
        if p.lembrete_em:
            data, hora = p.lembrete_em.split("T")
            a, m, d = data.split("-")
            linha += f" (lembrete {d}/{m} às {hora})"
        linhas.append(linha)
    for antigo in corrigidos:
        linhas.append(f"Corrigi o registro anterior: “{antigo}”.")
    if ext.duvidas:
        linhas.append("Fiquei com uma dúvida: " + ext.duvidas[0])
    elif any(p.responsavel == "eu" and not p.lembrete_em for p in ext.pendencias):
        linhas.append("Se quiser lembrete, é só pedir: “me lembra sexta às 9h de…”.")
    return "\n".join(linhas)
