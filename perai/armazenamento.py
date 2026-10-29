"""Gravação e leitura da memória (esquema em db/schema.sql).

Duas implementações com a mesma interface:
- RepositorioMemoria: em memória, para testes e para rodar sem banco.
- RepositorioPostgres: o banco de verdade (precisa do pacote psycopg).

Regras que valem nas duas: toda linha tem usuario_id (D-004); todo registro aponta
para a mensagem de origem (D-040); correção não apaga o original (D-003).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from perai.extracao import Extracao, Pendencia, Registro


@dataclass
class NovaMensagem:
    canal: str                  # telegram, web, teste
    tipo: str                   # texto, audio, print, foto, conversa_exportada
    texto_original: str | None = None
    transcricao: str | None = None
    caminho_arquivo: str | None = None
    data_situacao: str | None = None
    modelo_transcricao: str | None = None


@dataclass
class Lembrete:
    pendencia_id: str
    usuario_id: str
    telegram_chat_id: int | None
    descricao: str


class Repositorio(Protocol):
    def usuario_por_telegram(self, chat_id: int) -> str | None: ...
    def criar_usuario(self, nome: str, telegram_chat_id: int | None) -> str: ...
    def nome_usuario(self, usuario_id: str) -> str | None: ...
    def salvar_mensagem(self, usuario_id: str, m: NovaMensagem) -> str: ...
    def salvar_extracao(self, usuario_id: str, mensagem_id: str, ext: Extracao) -> dict[str, str]: ...
    def entidades_conhecidas(self, usuario_id: str) -> list[str]: ...
    def memoria(self, usuario_id: str) -> tuple[list[Registro], list[Pendencia]]: ...
    def corrigir_registro(self, usuario_id: str, antigo_id: str, novo_id: str, mensagem_id: str) -> list[str]: ...
    def ajustar_pendencia(self, usuario_id: str, pendencia_id: str, mensagem_id: str,
                          lembrete_em: str | None = None, prazo: str | None = None,
                          situacao: str | None = None) -> None: ...
    def salvar_resposta(self, usuario_id: str, tipo: str, pergunta: str, resposta: str,
                        citados: list[str], sem_informacao: bool,
                        fontes_externas: list[str] | None = None) -> str: ...
    def telegram_de(self, usuario_id: str) -> int | None: ...
    def avaliar_resposta(self, usuario_id: str, resposta_id: str, avaliacao: str) -> bool: ...
    def metricas(self, usuario_id: str, desde: datetime) -> dict: ...
    # Google Agenda (D-051)
    def criar_conexao_google(self, usuario_id: str, token_hash: str, expira_em: datetime) -> None: ...
    def conexao_google(self, token_hash: str, agora: datetime) -> str | None: ...
    def usar_conexao_google(self, token_hash: str, agora: datetime) -> None: ...
    def salvar_conta_google(self, usuario_id: str, email: str, refresh_cifrado: str, escopos: str) -> None: ...
    def conta_google(self, usuario_id: str) -> tuple[str, str, str] | None: ...
    def desconectar_google(self, usuario_id: str, quando: datetime) -> None: ...
    def salvar_evento(self, usuario_id: str, mensagem_id: str | None, registro_id: str | None,
                      google_id: str, titulo: str, inicio: str, fim: str, local: str | None) -> str: ...
    def evento(self, usuario_id: str, evento_id: str) -> dict | None: ...
    def eventos_criados(self, usuario_id: str, desde: datetime) -> list[dict]: ...
    def mudar_evento(self, usuario_id: str, evento_id: str, mensagem_id: str | None, **campos) -> None: ...
    # Eventos de fora cancelados a pedido (D-059)
    def salvar_cancelamento(self, usuario_id: str, mensagem_id: str | None, google_id: str, titulo: str,
                            inicio: str | None, tipo: str, recorrencia_anterior: list[str] | None) -> str: ...
    def cancelamentos(self, usuario_id: str, mensagem_id: str) -> list[dict]: ...
    def marcar_cancelamento_desfeito(self, usuario_id: str, cancelamento_id: str, quando: datetime) -> None: ...
    def lembretes_vencidos(self, agora: datetime) -> list[Lembrete]: ...
    def pendencia(self, usuario_id: str, pendencia_id: str) -> dict | None: ...
    def criar_seguinte(self, usuario_id: str, pendencia_id: str, prazo: str,
                       lembrete_em: str | None) -> str | None: ...
    def recorrentes_vencidas(self, hoje) -> list[tuple[str, str]]: ...
    # Despesas (D-052)
    def salvar_despesa(self, usuario_id: str, mensagem_id: str, registro_id: str | None, d,
                       origem: str, chave_nfe: str | None = None) -> str | None: ...
    def despesas(self, usuario_id: str, desde) -> list[dict]: ...
    def despesa(self, usuario_id: str, despesa_id: str) -> dict | None: ...
    def mudar_despesa(self, usuario_id: str, despesa_id: str, mensagem_id: str | None, **campos) -> None: ...
    def vincular_despesa(self, usuario_id: str, despesa_id: str, tipo: str, google_id: str | None = None,
                         titulo: str | None = None, inicio: str | None = None,
                         cliente: str | None = None) -> bool: ...
    def vinculos_despesas(self, usuario_id: str, despesa_ids: list[str]) -> dict[str, list[dict]]: ...
    # Viagens (D-056)
    def salvar_viagem(self, usuario_id: str, mensagem_id: str | None, nome: str, inicio: str, fim: str,
                      cidades: list[str], acompanhantes: list[str], objetivo: str | None) -> str: ...
    def viagens(self, usuario_id: str) -> list[dict]: ...
    def viagem(self, usuario_id: str, viagem_id: str) -> dict | None: ...
    def mudar_viagem(self, usuario_id: str, viagem_id: str, **campos) -> None: ...
    def viagens_para_checkin(self, agora: datetime) -> list[dict]: ...
    def marcar_checkin(self, viagem_id: str, dia) -> bool: ...
    def marcar_lembrete_enviado(self, pendencia_id: str, quando: datetime) -> None: ...
    def registrar_chamada(self, usuario_id: str | None, finalidade: str, fornecedor: str, modelo: str,
                          tokens_entrada: int = 0, tokens_saida: int = 0, segundos_audio: float = 0,
                          custo_usd: float = 0, latencia_ms: int = 0, erro: str | None = None) -> None: ...


def _novo_id() -> str:
    return str(uuid.uuid4())


def _chave(nome: str) -> str:
    return " ".join(nome.lower().split())


# --- Em memória ------------------------------------------------------------------

@dataclass
class _Banco:
    usuarios: dict[str, dict] = field(default_factory=dict)
    mensagens: dict[str, dict] = field(default_factory=dict)
    entidades: dict[str, dict] = field(default_factory=dict)
    registros: dict[str, dict] = field(default_factory=dict)
    registro_entidades: list[tuple[str, str]] = field(default_factory=list)
    pendencias: dict[str, dict] = field(default_factory=dict)
    correcoes: list[dict] = field(default_factory=list)
    respostas: dict[str, dict] = field(default_factory=dict)
    chamadas: list[dict] = field(default_factory=list)
    google_conexoes: dict[str, dict] = field(default_factory=dict)
    google_contas: dict[str, dict] = field(default_factory=dict)
    eventos: dict[str, dict] = field(default_factory=dict)
    despesas: dict[str, dict] = field(default_factory=dict)
    vinculos: list[dict] = field(default_factory=list)
    viagens: dict[str, dict] = field(default_factory=dict)
    checkins: set = field(default_factory=set)
    cancelamentos: dict[str, dict] = field(default_factory=dict)


class RepositorioMemoria:
    def __init__(self):
        self.b = _Banco()

    def usuario_por_telegram(self, chat_id):
        return next((i for i, u in self.b.usuarios.items() if u["telegram_chat_id"] == chat_id), None)

    def criar_usuario(self, nome, telegram_chat_id):
        i = _novo_id()
        self.b.usuarios[i] = {"nome": nome, "telegram_chat_id": telegram_chat_id}
        return i

    def nome_usuario(self, usuario_id):
        u = self.b.usuarios.get(usuario_id)
        return u["nome"] if u else None

    def salvar_mensagem(self, usuario_id, m):
        i = _novo_id()
        self.b.mensagens[i] = {"usuario_id": usuario_id, **m.__dict__, "recebida_em": datetime.now()}
        return i

    def _entidade(self, usuario_id, nome, tipo="pessoa", contexto=None, descricao=None):
        for i, e in self.b.entidades.items():
            if e["usuario_id"] == usuario_id and _chave(e["nome"]) == _chave(nome):
                if contexto and not e["contexto"]:
                    e["contexto"] = contexto
                return i
        i = _novo_id()
        self.b.entidades[i] = {"usuario_id": usuario_id, "nome": nome, "tipo": tipo,
                               "contexto": contexto, "notas": descricao}
        return i

    def salvar_extracao(self, usuario_id, mensagem_id, ext):
        mapa: dict[str, str] = {}
        for e in ext.entidades:
            self._entidade(usuario_id, e.nome, e.tipo, e.contexto, e.descricao)
        for r in ext.registros:
            i = _novo_id()
            dito_por = None if r.dito_por.lower() == "eu" else self._entidade(usuario_id, r.dito_por)
            self.b.registros[i] = {"usuario_id": usuario_id, "mensagem_id": mensagem_id,
                                   "conteudo": r.conteudo, "natureza": r.natureza,
                                   "dito_por_id": dito_por, "data_fato": r.data_fato,
                                   "confianca": r.confianca, "sensivel": r.sensivel,
                                   "situacao": "ativo", "substituido_por": None,
                                   "criado_em": datetime.now()}
            for nome in r.entidades:
                self.b.registro_entidades.append((i, self._entidade(usuario_id, nome)))
            mapa[r.id] = i
        for p in ext.pendencias:
            i = _novo_id()
            origem = next(iter(mapa.values()), None)
            if origem is None:  # pendência precisa de registro de origem: cria um
                origem = _novo_id()
                self.b.registros[origem] = {"usuario_id": usuario_id, "mensagem_id": mensagem_id,
                                            "conteudo": p.descricao, "natureza": "intencao",
                                            "dito_por_id": None, "data_fato": None, "confianca": 0.8,
                                            "sensivel": False, "situacao": "ativo",
                                            "substituido_por": None, "criado_em": datetime.now()}
            self.b.pendencias[i] = {"usuario_id": usuario_id, "registro_id": origem,
                                    "descricao": p.descricao, "responsavel": p.responsavel,
                                    "quem": p.quem, "prazo": p.prazo,
                                    "prazo_como_dito": p.prazo_como_dito, "situacao": "aberta",
                                    "lembrete_em": p.lembrete_em,
                                    "lembrete_aceito": p.lembrete_em is not None,
                                    "lembrete_enviado_em": None, "recorrencia": p.recorrencia,
                                    "anterior_id": None}
            mapa[p.id] = i
        return mapa

    def entidades_conhecidas(self, usuario_id):
        return [e["nome"] for e in self.b.entidades.values() if e["usuario_id"] == usuario_id]

    def _nome(self, entidade_id):
        return self.b.entidades[entidade_id]["nome"] if entidade_id else "eu"

    def memoria(self, usuario_id):
        registros = [
            Registro(i, r["mensagem_id"], r["conteudo"], r["natureza"], self._nome(r["dito_por_id"]),
                     r["data_fato"], [], float(r["confianca"]), r["sensivel"])
            for i, r in self.b.registros.items()
            if r["usuario_id"] == usuario_id and r["situacao"] == "ativo" and not self._intencao_encerrada(i, r)
        ]
        pendencias = [
            Pendencia(i, self.b.registros[p["registro_id"]]["mensagem_id"], p["descricao"],
                      p["responsavel"], p["quem"], p["prazo"], p["prazo_como_dito"], p["lembrete_em"],
                      p.get("recorrencia"))
            for i, p in self.b.pendencias.items()
            if p["usuario_id"] == usuario_id and p["situacao"] in ("aberta", "adiada")
        ]
        return registros, pendencias

    def _intencao_encerrada(self, registro_id, r):
        """Intenção cuja pendência já foi feita ou cancelada não volta como coisa a fazer."""
        if r["natureza"] != "intencao":
            return False
        ligadas = [p for p in self.b.pendencias.values() if p["registro_id"] == registro_id]
        return bool(ligadas) and not any(p["situacao"] in ("aberta", "adiada") for p in ligadas)

    def corrigir_registro(self, usuario_id, antigo_id, novo_id, mensagem_id):
        antigo = self.b.registros[antigo_id]
        if antigo["usuario_id"] != usuario_id:
            raise PermissionError("registro de outro usuário")
        antigo["situacao"] = "corrigido"
        antigo["substituido_por"] = novo_id
        self.b.correcoes.append({"usuario_id": usuario_id, "tabela": "registros", "linha_id": antigo_id,
                                 "campo": "conteudo", "valor_anterior": antigo["conteudo"],
                                 "valor_novo": self.b.registros[novo_id]["conteudo"],
                                 "mensagem_id": mensagem_id})
        # Pendências que nasceram do registro corrigido deixam de valer (sem apagar).
        descartadas = []
        for i, p in self.b.pendencias.items():
            if p["registro_id"] == antigo_id and p["situacao"] in ("aberta", "adiada"):
                self._mudar(usuario_id, i, "situacao", "descartada", mensagem_id)
                descartadas.append(p["descricao"])
        return descartadas

    def _mudar(self, usuario_id, pendencia_id, campo, valor, mensagem_id):
        p = self.b.pendencias[pendencia_id]
        self.b.correcoes.append({"usuario_id": usuario_id, "tabela": "pendencias", "linha_id": pendencia_id,
                                 "campo": campo, "valor_anterior": None if p[campo] is None else str(p[campo]),
                                 "valor_novo": valor, "mensagem_id": mensagem_id})
        p[campo] = valor
        if campo == "lembrete_em":
            p["lembrete_aceito"] = True
            p["lembrete_enviado_em"] = None

    def ajustar_pendencia(self, usuario_id, pendencia_id, mensagem_id, lembrete_em=None, prazo=None,
                          situacao=None):
        p = self.b.pendencias.get(pendencia_id)
        if p is None or p["usuario_id"] != usuario_id:
            raise PermissionError("pendência inexistente ou de outro usuário")
        for campo, valor in (("lembrete_em", lembrete_em), ("prazo", prazo), ("situacao", situacao)):
            if valor is not None:
                self._mudar(usuario_id, pendencia_id, campo, valor, mensagem_id)

    def salvar_resposta(self, usuario_id, tipo, pergunta, resposta, citados, sem_informacao,
                        fontes_externas=None):
        fontes_externas = list(fontes_externas or [])
        if not citados and not fontes_externas and not sem_informacao:
            raise ValueError("resposta sem fonte só pode dizer que não há informação")
        i = _novo_id()
        self.b.respostas[i] = {"usuario_id": usuario_id, "tipo": tipo, "pergunta": pergunta,
                               "resposta": resposta, "registros_citados": citados,
                               "fontes_externas": fontes_externas, "sem_informacao": sem_informacao,
                               "avaliacao": None, "criado_em": datetime.now()}
        return i

    def avaliar_resposta(self, usuario_id, resposta_id, avaliacao):
        r = self.b.respostas.get(resposta_id)
        if not r or r["usuario_id"] != usuario_id:
            return False
        r["avaliacao"] = avaliacao
        return True

    def metricas(self, usuario_id, desde):
        msgs = [m for m in self.b.mensagens.values() if m["usuario_id"] == usuario_id and m["recebida_em"] >= desde]
        regs = [r for r in self.b.registros.values() if r["usuario_id"] == usuario_id and r["criado_em"] >= desde]
        resp = [r for r in self.b.respostas.values() if r["usuario_id"] == usuario_id and r["criado_em"] >= desde]
        lat = sorted(c["latencia_ms"] for c in self.b.chamadas if c["usuario_id"] == usuario_id
                     and c["finalidade"] in ("consulta", "briefing") and c["latencia_ms"] and not c["erro"])
        aval = {}
        for r in resp:
            if r["avaliacao"]:
                aval[(r["tipo"], r["avaliacao"])] = aval.get((r["tipo"], r["avaliacao"]), 0) + 1
        return {"dias_com_captura": len({m["recebida_em"].date() for m in msgs}), "mensagens": len(msgs),
                "registros": len(regs), "corrigidos": sum(1 for r in regs if r["situacao"] == "corrigido"),
                "respostas": len(resp), "sem_informacao": sum(1 for r in resp if r["sem_informacao"]),
                "briefings": sum(1 for r in resp if r["tipo"] == "briefing"), "avaliacoes": aval,
                "latencias_ms": lat, "custo_usd": sum(c["custo_usd"] or 0 for c in self.b.chamadas
                                                      if c["usuario_id"] == usuario_id)}

    def telegram_de(self, usuario_id):
        u = self.b.usuarios.get(usuario_id)
        return u["telegram_chat_id"] if u else None

    def criar_conexao_google(self, usuario_id, token_hash, expira_em):
        self.b.google_conexoes[token_hash] = {"usuario_id": usuario_id, "expira_em": expira_em, "usado_em": None}

    def conexao_google(self, token_hash, agora):
        c = self.b.google_conexoes.get(token_hash)
        if c and c["usado_em"] is None and c["expira_em"] > agora:
            return c["usuario_id"]
        return None

    def usar_conexao_google(self, token_hash, agora):
        self.b.google_conexoes[token_hash]["usado_em"] = agora

    def salvar_conta_google(self, usuario_id, email, refresh_cifrado, escopos):
        self.b.google_contas[usuario_id] = {"email": email, "refresh": refresh_cifrado, "escopos": escopos,
                                            "agenda_id": "primary", "desconectada_em": None}

    def conta_google(self, usuario_id):
        c = self.b.google_contas.get(usuario_id)
        if not c or c["desconectada_em"]:
            return None
        return c["email"], c["refresh"], c["agenda_id"]

    def desconectar_google(self, usuario_id, quando):
        if usuario_id in self.b.google_contas:
            self.b.google_contas[usuario_id]["desconectada_em"] = quando

    def salvar_evento(self, usuario_id, mensagem_id, registro_id, google_id, titulo, inicio, fim, local):
        i = _novo_id()
        self.b.eventos[i] = {"id": i, "usuario_id": usuario_id, "mensagem_id": mensagem_id,
                             "registro_id": registro_id, "google_evento_id": google_id, "titulo": titulo,
                             "inicio": inicio, "fim": fim, "local": local, "situacao": "criado"}
        return i

    def evento(self, usuario_id, evento_id):
        e = self.b.eventos.get(evento_id)
        return dict(e) if e and e["usuario_id"] == usuario_id else None

    def eventos_criados(self, usuario_id, desde):
        return [dict(e) for e in sorted(self.b.eventos.values(), key=lambda e: e["inicio"])
                if e["usuario_id"] == usuario_id and e["situacao"] in ("criado", "alterado")
                and datetime.fromisoformat(e["inicio"]) >= desde]

    def mudar_evento(self, usuario_id, evento_id, mensagem_id, **campos):
        e = self.b.eventos.get(evento_id)
        if e is None or e["usuario_id"] != usuario_id:
            raise PermissionError("evento inexistente ou de outro usuário")
        for campo, valor in campos.items():
            self.b.correcoes.append({"usuario_id": usuario_id, "tabela": "eventos_agenda", "linha_id": evento_id,
                                     "campo": campo, "valor_anterior": None if e[campo] is None else str(e[campo]),
                                     "valor_novo": valor, "mensagem_id": mensagem_id})
            e[campo] = valor

    def salvar_cancelamento(self, usuario_id, mensagem_id, google_id, titulo, inicio, tipo, recorrencia_anterior):
        i = _novo_id()
        self.b.cancelamentos[i] = {"id": i, "usuario_id": usuario_id, "mensagem_id": mensagem_id,
                                   "google_evento_id": google_id, "titulo": titulo, "inicio": inicio, "tipo": tipo,
                                   "recorrencia_anterior": recorrencia_anterior, "desfeito_em": None}
        return i

    def cancelamentos(self, usuario_id, mensagem_id):
        return [dict(x) for x in self.b.cancelamentos.values()
                if x["usuario_id"] == usuario_id and x["mensagem_id"] == mensagem_id and not x["desfeito_em"]]

    def marcar_cancelamento_desfeito(self, usuario_id, cancelamento_id, quando):
        x = self.b.cancelamentos.get(cancelamento_id)
        if x and x["usuario_id"] == usuario_id:
            x["desfeito_em"] = quando

    def lembretes_vencidos(self, agora):
        saida = []
        for i, p in self.b.pendencias.items():
            if (p["lembrete_aceito"] and p["lembrete_em"] and not p["lembrete_enviado_em"]
                    and p["situacao"] in ("aberta", "adiada")
                    and datetime.fromisoformat(p["lembrete_em"]) <= agora):
                u = self.b.usuarios[p["usuario_id"]]
                saida.append(Lembrete(i, p["usuario_id"], u["telegram_chat_id"], p["descricao"]))
        return saida

    def marcar_lembrete_enviado(self, pendencia_id, quando):
        self.b.pendencias[pendencia_id]["lembrete_enviado_em"] = quando

    def pendencia(self, usuario_id, pendencia_id):
        p = self.b.pendencias.get(pendencia_id)
        return {"id": pendencia_id, **p} if p and p["usuario_id"] == usuario_id else None

    def criar_seguinte(self, usuario_id, pendencia_id, prazo, lembrete_em):
        p = self.b.pendencias.get(pendencia_id)
        if not p or p["usuario_id"] != usuario_id:
            return None
        if any(q.get("anterior_id") == pendencia_id for q in self.b.pendencias.values()):
            return None
        i = _novo_id()
        self.b.pendencias[i] = {**p, "prazo": prazo, "lembrete_em": lembrete_em,
                                "lembrete_aceito": lembrete_em is not None, "lembrete_enviado_em": None,
                                "situacao": "aberta", "anterior_id": pendencia_id}
        return i

    def salvar_despesa(self, usuario_id, mensagem_id, registro_id, d, origem, chave_nfe=None):
        if chave_nfe and any(x["usuario_id"] == usuario_id and x.get("chave_nfe") == chave_nfe
                             and x["situacao"] == "ativa" for x in self.b.despesas.values()):
            return None  # a mesma nota mandada de novo
        i = _novo_id()
        self.b.despesas[i] = {"id": i, "usuario_id": usuario_id, "mensagem_id": mensagem_id,
                              "registro_id": registro_id, "descricao": d.descricao,
                              "valor_centavos": d.valor_centavos, "data": d.data, "categoria": d.categoria,
                              "estabelecimento": d.estabelecimento, "cnpj": d.cnpj,
                              "forma_pagamento": d.forma_pagamento, "contexto": d.contexto, "local": d.local,
                              "origem": origem, "chave_nfe": chave_nfe, "situacao": "ativa"}
        return i

    def despesas(self, usuario_id, desde):
        lista = [(n, dict(x)) for n, x in enumerate(self.b.despesas.values())
                 if x["usuario_id"] == usuario_id and x["situacao"] == "ativa"
                 and (x["data"] is None or desde is None or x["data"] >= desde.isoformat())]
        # Como no banco: mais nova primeiro (data, depois ordem de criação).
        return [x for _, x in sorted(lista, key=lambda t: (t[1]["data"] or "", t[0]), reverse=True)]

    def despesa(self, usuario_id, despesa_id):
        x = self.b.despesas.get(despesa_id)
        return dict(x) if x and x["usuario_id"] == usuario_id else None

    def mudar_despesa(self, usuario_id, despesa_id, mensagem_id, **campos):
        x = self.b.despesas.get(despesa_id)
        if x is None or x["usuario_id"] != usuario_id:
            raise PermissionError("despesa inexistente ou de outro usuário")
        for campo, valor in campos.items():
            self.b.correcoes.append({"usuario_id": usuario_id, "tabela": "despesas", "linha_id": despesa_id,
                                     "campo": campo, "valor_anterior": None if x[campo] is None else str(x[campo]),
                                     "valor_novo": None if valor is None else str(valor), "mensagem_id": mensagem_id})
            x[campo] = valor

    def vincular_despesa(self, usuario_id, despesa_id, tipo, google_id=None, titulo=None, inicio=None,
                         cliente=None):
        if not self.despesa(usuario_id, despesa_id):
            return False
        for v in self.b.vinculos:
            if v["despesa_id"] == despesa_id and v["tipo"] == tipo and (
                    (tipo == "evento" and v["google_evento_id"] == google_id)
                    or (tipo == "cliente" and v["cliente"].lower() == (cliente or "").lower())):
                return False
        self.b.vinculos.append({"usuario_id": usuario_id, "despesa_id": despesa_id, "tipo": tipo,
                                "google_evento_id": google_id, "titulo": titulo, "inicio": inicio,
                                "cliente": cliente})
        return True

    def vinculos_despesas(self, usuario_id, despesa_ids):
        saida: dict[str, list[dict]] = {}
        for v in self.b.vinculos:
            if v["usuario_id"] == usuario_id and v["despesa_id"] in despesa_ids:
                saida.setdefault(v["despesa_id"], []).append(dict(v))
        return saida

    def salvar_viagem(self, usuario_id, mensagem_id, nome, inicio, fim, cidades, acompanhantes, objetivo):
        i = _novo_id()
        self.b.viagens[i] = {"id": i, "usuario_id": usuario_id, "mensagem_id": mensagem_id, "nome": nome,
                             "inicio": inicio, "fim": fim, "cidades": list(cidades),
                             "acompanhantes": list(acompanhantes), "objetivo": objetivo, "acompanhar": False,
                             "hora_checkin": "19:00", "situacao": "ativa"}
        return i

    def viagens(self, usuario_id):
        return sorted((dict(v) for v in self.b.viagens.values()
                       if v["usuario_id"] == usuario_id and v["situacao"] == "ativa"),
                      key=lambda v: v["inicio"], reverse=True)

    def viagem(self, usuario_id, viagem_id):
        v = self.b.viagens.get(viagem_id)
        return dict(v) if v and v["usuario_id"] == usuario_id else None

    def mudar_viagem(self, usuario_id, viagem_id, **campos):
        v = self.b.viagens.get(viagem_id)
        if not v or v["usuario_id"] != usuario_id:
            raise PermissionError("viagem inexistente ou de outro usuário")
        v.update(campos)

    def viagens_para_checkin(self, agora):
        hoje, hora = agora.date().isoformat(), agora.strftime("%H:%M")
        saida = []
        for v in self.b.viagens.values():
            if (v["acompanhar"] and v["situacao"] == "ativa" and v["inicio"] <= hoje <= v["fim"]
                    and v["hora_checkin"] <= hora and (v["id"], hoje) not in self.b.checkins):
                saida.append({**v, "telegram_chat_id": self.b.usuarios[v["usuario_id"]]["telegram_chat_id"]})
        return saida

    def marcar_checkin(self, viagem_id, dia):
        chave = (viagem_id, dia.isoformat())
        if chave in self.b.checkins:
            return False
        self.b.checkins.add(chave)
        return True

    def recorrentes_vencidas(self, hoje):
        return [(i, p["usuario_id"]) for i, p in self.b.pendencias.items()
                if p.get("recorrencia") and p["situacao"] in ("aberta", "adiada") and p["prazo"]
                and p["prazo"] < hoje.isoformat()
                and not any(q.get("anterior_id") == i for q in self.b.pendencias.values())]

    def registrar_chamada(self, usuario_id, finalidade, fornecedor, modelo, tokens_entrada=0,
                          tokens_saida=0, segundos_audio=0, custo_usd=0, latencia_ms=0, erro=None):
        self.b.chamadas.append(dict(usuario_id=usuario_id, finalidade=finalidade, fornecedor=fornecedor,
                                    modelo=modelo, tokens_entrada=tokens_entrada, tokens_saida=tokens_saida,
                                    segundos_audio=segundos_audio, custo_usd=custo_usd,
                                    latencia_ms=latencia_ms, erro=erro))


# --- PostgreSQL ------------------------------------------------------------------

SQL = {
    "usuario_por_telegram": "SELECT id FROM usuarios WHERE telegram_chat_id = %s",
    "nome_usuario": "SELECT nome FROM usuarios WHERE id = %s",
    "criar_usuario": "INSERT INTO usuarios (nome, telegram_chat_id) VALUES (%s, %s) RETURNING id",
    "salvar_mensagem": (
        "INSERT INTO mensagens (usuario_id, canal, tipo, texto_original, transcricao, caminho_arquivo,"
        " data_situacao, modelo_transcricao) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id"
    ),
    "buscar_entidade": "SELECT id FROM entidades WHERE usuario_id = %s AND lower(nome) = lower(%s) LIMIT 1",
    "criar_entidade": (
        "INSERT INTO entidades (usuario_id, tipo, nome, contexto, notas)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id"
    ),
    "completar_contexto": "UPDATE entidades SET contexto = %s WHERE id = %s AND contexto IS NULL",
    "salvar_registro": (
        "INSERT INTO registros (usuario_id, mensagem_id, tipo, conteudo, data_fato, dito_por_id,"
        " natureza, confianca, sensivel) VALUES (%s, %s, 'registro', %s, %s, %s, %s, %s, %s) RETURNING id"
    ),
    "ligar_entidade": (
        "INSERT INTO registro_entidades (registro_id, entidade_id, usuario_id) VALUES (%s, %s, %s)"
        " ON CONFLICT DO NOTHING"
    ),
    "salvar_pendencia": (
        "INSERT INTO pendencias (usuario_id, registro_id, descricao, responsavel, quem, prazo,"
        " prazo_como_dito, lembrete_em, lembrete_aceito, recorrencia)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id"
    ),
    "pendencia": (
        "SELECT id, descricao, prazo, lembrete_em, situacao, recorrencia FROM pendencias"
        " WHERE id = %s AND usuario_id = %s"
    ),
    "criar_seguinte": (
        "INSERT INTO pendencias (usuario_id, registro_id, descricao, responsavel, quem, prazo,"
        " prazo_como_dito, lembrete_em, lembrete_aceito, recorrencia, anterior_id)"
        " SELECT usuario_id, registro_id, descricao, responsavel, quem, %s, prazo_como_dito, %s, %s,"
        " recorrencia, id FROM pendencias WHERE id = %s AND usuario_id = %s"
        " ON CONFLICT DO NOTHING RETURNING id"
    ),
    "salvar_despesa": (
        "INSERT INTO despesas (usuario_id, mensagem_id, registro_id, descricao, valor_centavos, data, categoria,"
        " estabelecimento, cnpj, forma_pagamento, contexto, local, origem, chave_nfe)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s::categoria_despesa, %s, %s, %s, %s::contexto, %s, %s, %s)"
        " ON CONFLICT DO NOTHING RETURNING id"
    ),
    "despesas": (
        "SELECT id, descricao, valor_centavos, data, categoria, estabelecimento, contexto, forma_pagamento,"
        " local, origem, situacao FROM despesas WHERE usuario_id = %s AND situacao = 'ativa'"
        " AND (data IS NULL OR data >= %s) ORDER BY data DESC NULLS LAST, criado_em DESC LIMIT 500"
    ),
    "despesa": (
        "SELECT id, descricao, valor_centavos, data, categoria, estabelecimento, contexto, forma_pagamento,"
        " local, origem, situacao FROM despesas WHERE id = %s AND usuario_id = %s"
    ),
    "mudar_despesa_valor_centavos": "UPDATE despesas SET valor_centavos = %s WHERE id = %s AND usuario_id = %s",
    "mudar_despesa_categoria": (
        "UPDATE despesas SET categoria = %s::categoria_despesa WHERE id = %s AND usuario_id = %s"
    ),
    "mudar_despesa_data": "UPDATE despesas SET data = %s WHERE id = %s AND usuario_id = %s",
    "mudar_despesa_contexto": "UPDATE despesas SET contexto = %s::contexto WHERE id = %s AND usuario_id = %s",
    "mudar_despesa_situacao": "UPDATE despesas SET situacao = %s WHERE id = %s AND usuario_id = %s",
    "vincular_despesa": (
        "INSERT INTO despesa_vinculos (usuario_id, despesa_id, tipo, google_evento_id, titulo, inicio, cliente)"
        " SELECT %s, d.id, %s, %s, %s, %s, %s FROM despesas d WHERE d.id = %s AND d.usuario_id = %s"
        " ON CONFLICT DO NOTHING RETURNING id"
    ),
    "vinculos_despesas": (
        "SELECT despesa_id, tipo, google_evento_id, titulo, inicio, cliente FROM despesa_vinculos"
        " WHERE usuario_id = %s AND despesa_id = ANY(%s::uuid[]) ORDER BY criado_em"
    ),
    "salvar_viagem": (
        "INSERT INTO viagens (usuario_id, mensagem_id, nome, inicio, fim, cidades, acompanhantes, objetivo)"
        " VALUES (%s, %s, %s, %s, %s, %s::text[], %s::text[], %s) RETURNING id"
    ),
    "viagens": (
        "SELECT id, nome, inicio, fim, cidades, acompanhantes, objetivo, acompanhar, hora_checkin, situacao"
        " FROM viagens WHERE usuario_id = %s AND situacao = 'ativa' ORDER BY inicio DESC LIMIT 50"
    ),
    "viagem": (
        "SELECT id, nome, inicio, fim, cidades, acompanhantes, objetivo, acompanhar, hora_checkin, situacao"
        " FROM viagens WHERE id = %s AND usuario_id = %s"
    ),
    "mudar_viagem_acompanhar": "UPDATE viagens SET acompanhar = %s WHERE id = %s AND usuario_id = %s",
    "mudar_viagem_nome": "UPDATE viagens SET nome = %s WHERE id = %s AND usuario_id = %s",
    "mudar_viagem_situacao": "UPDATE viagens SET situacao = %s WHERE id = %s AND usuario_id = %s",
    "viagens_para_checkin": (
        "SELECT v.id, v.nome, v.inicio, v.fim, v.cidades, v.acompanhantes, v.objetivo, v.acompanhar,"
        " v.hora_checkin, v.situacao, v.usuario_id, u.telegram_chat_id FROM viagens v"
        " JOIN usuarios u ON u.id = v.usuario_id"
        " WHERE v.acompanhar AND v.situacao = 'ativa' AND %s BETWEEN v.inicio AND v.fim"
        " AND v.hora_checkin <= %s::time"
        " AND NOT EXISTS (SELECT 1 FROM viagem_checkins c WHERE c.viagem_id = v.id AND c.dia = %s)"
    ),
    "marcar_checkin": (
        "INSERT INTO viagem_checkins (viagem_id, dia) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING viagem_id"
    ),
    "recorrentes_vencidas": (
        "SELECT p.id, p.usuario_id FROM pendencias p WHERE p.recorrencia IS NOT NULL"
        " AND p.situacao IN ('aberta', 'adiada') AND p.prazo < %s"
        " AND NOT EXISTS (SELECT 1 FROM pendencias s WHERE s.anterior_id = p.id)"
    ),
    "entidades_conhecidas": "SELECT nome FROM entidades WHERE usuario_id = %s ORDER BY criado_em DESC LIMIT 300",
    "memoria_registros": (
        "SELECT r.id, r.mensagem_id, r.conteudo, r.natureza, coalesce(e.nome, 'eu'), r.data_fato,"
        " r.confianca, r.sensivel FROM registros r LEFT JOIN entidades e ON e.id = r.dito_por_id"
        " WHERE r.usuario_id = %s AND r.situacao = 'ativo'"
        # Intenção cuja pendência já foi feita ou cancelada não volta como coisa a fazer.
        " AND NOT (r.natureza = 'intencao' AND EXISTS (SELECT 1 FROM pendencias p WHERE p.registro_id = r.id)"
        " AND NOT EXISTS (SELECT 1 FROM pendencias p WHERE p.registro_id = r.id"
        " AND p.situacao IN ('aberta', 'adiada')))"
        " ORDER BY r.criado_em"
    ),
    "memoria_pendencias": (
        "SELECT p.id, r.mensagem_id, p.descricao, p.responsavel, p.quem, p.prazo, p.prazo_como_dito,"
        " p.lembrete_em, p.recorrencia FROM pendencias p JOIN registros r ON r.id = p.registro_id"
        " WHERE p.usuario_id = %s AND p.situacao IN ('aberta', 'adiada') ORDER BY p.criado_em"
    ),
    "corrigir_registro": (
        "UPDATE registros SET situacao = 'corrigido', substituido_por = %s"
        " WHERE id = %s AND usuario_id = %s RETURNING conteudo"
    ),
    "conteudo_registro": "SELECT conteudo FROM registros WHERE id = %s",
    "salvar_correcao": (
        "INSERT INTO correcoes (usuario_id, tabela, linha_id, campo, valor_anterior, valor_novo, mensagem_id)"
        " VALUES (%s, 'registros', %s, 'conteudo', %s, %s, %s)"
    ),
    "salvar_correcao_campo": (
        "INSERT INTO correcoes (usuario_id, tabela, linha_id, campo, valor_anterior, valor_novo, mensagem_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)"
    ),
    "descartar_pendencias_do_registro": (
        "UPDATE pendencias SET situacao = 'descartada' WHERE registro_id = %s AND usuario_id = %s"
        " AND situacao IN ('aberta', 'adiada') RETURNING id, descricao, situacao"
    ),
    "ler_pendencia": (
        "SELECT lembrete_em, prazo, situacao FROM pendencias WHERE id = %s AND usuario_id = %s FOR UPDATE"
    ),
    "ajustar_lembrete": (
        "UPDATE pendencias SET lembrete_em = %s, lembrete_aceito = true, lembrete_enviado_em = NULL"
        " WHERE id = %s AND usuario_id = %s"
    ),
    "ajustar_prazo": "UPDATE pendencias SET prazo = %s WHERE id = %s AND usuario_id = %s",
    "ajustar_situacao": (
        "UPDATE pendencias SET situacao = %s::situacao_pendencia WHERE id = %s AND usuario_id = %s"
    ),
    "salvar_resposta": (
        "INSERT INTO respostas (usuario_id, tipo, pergunta, resposta, registros_citados, sem_informacao,"
        " fontes_externas) VALUES (%s, %s, %s, %s, %s::uuid[], %s, %s::text[]) RETURNING id"
    ),
    "telegram_de": "SELECT telegram_chat_id FROM usuarios WHERE id = %s",
    "avaliar_resposta": "UPDATE respostas SET avaliacao = %s WHERE id = %s AND usuario_id = %s RETURNING id",
    "metricas_mensagens": (
        "SELECT count(DISTINCT (recebida_em AT TIME ZONE 'America/Sao_Paulo')::date), count(*)"
        " FROM mensagens WHERE usuario_id = %s AND recebida_em >= %s"
    ),
    "metricas_registros": (
        "SELECT count(*), count(*) FILTER (WHERE situacao = 'corrigido') FROM registros"
        " WHERE usuario_id = %s AND criado_em >= %s"
    ),
    "metricas_respostas": (
        "SELECT tipo::text, sem_informacao, avaliacao FROM respostas WHERE usuario_id = %s AND criado_em >= %s"
    ),
    "metricas_latencias": (
        "SELECT latencia_ms FROM chamadas_modelo WHERE usuario_id = %s AND criado_em >= %s"
        " AND finalidade IN ('consulta', 'briefing') AND erro IS NULL AND latencia_ms > 0 ORDER BY latencia_ms"
    ),
    "metricas_custo": (
        "SELECT coalesce(sum(custo_usd), 0) FROM chamadas_modelo WHERE usuario_id = %s AND criado_em >= %s"
    ),
    "criar_conexao_google": (
        "INSERT INTO google_conexoes (token_hash, usuario_id, expira_em) VALUES (%s, %s, %s)"
    ),
    "conexao_google": (
        "SELECT usuario_id FROM google_conexoes WHERE token_hash = %s AND usado_em IS NULL AND expira_em > %s"
    ),
    "usar_conexao_google": "UPDATE google_conexoes SET usado_em = %s WHERE token_hash = %s",
    "salvar_conta_google": (
        "INSERT INTO google_contas (usuario_id, email, refresh_token_cifrado, escopos) VALUES (%s, %s, %s, %s)"
        " ON CONFLICT (usuario_id) DO UPDATE SET email = EXCLUDED.email,"
        " refresh_token_cifrado = EXCLUDED.refresh_token_cifrado, escopos = EXCLUDED.escopos,"
        " conectada_em = now(), desconectada_em = NULL"
    ),
    "conta_google": (
        "SELECT email, refresh_token_cifrado, agenda_id FROM google_contas"
        " WHERE usuario_id = %s AND desconectada_em IS NULL"
    ),
    "desconectar_google": "UPDATE google_contas SET desconectada_em = %s WHERE usuario_id = %s",
    "salvar_evento": (
        "INSERT INTO eventos_agenda (usuario_id, mensagem_id, registro_id, google_evento_id, titulo, inicio,"
        " fim, local) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id"
    ),
    "evento": (
        "SELECT id, google_evento_id, titulo, inicio, fim, local, situacao FROM eventos_agenda"
        " WHERE id = %s AND usuario_id = %s"
    ),
    "eventos_criados": (
        "SELECT id, google_evento_id, titulo, inicio, fim, local, situacao FROM eventos_agenda"
        " WHERE usuario_id = %s AND situacao IN ('criado', 'alterado') AND inicio >= %s ORDER BY inicio"
    ),
    "salvar_cancelamento": (
        "INSERT INTO cancelamentos_agenda (usuario_id, mensagem_id, google_evento_id, titulo, inicio, tipo,"
        " recorrencia_anterior) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id"
    ),
    "cancelamentos": (
        "SELECT id, google_evento_id, titulo, inicio, tipo, recorrencia_anterior FROM cancelamentos_agenda"
        " WHERE usuario_id = %s AND mensagem_id = %s AND desfeito_em IS NULL ORDER BY criado_em"
    ),
    "marcar_cancelamento_desfeito": (
        "UPDATE cancelamentos_agenda SET desfeito_em = %s WHERE id = %s AND usuario_id = %s"
    ),
    "mudar_evento_inicio": "UPDATE eventos_agenda SET inicio = %s WHERE id = %s AND usuario_id = %s",
    "mudar_evento_fim": "UPDATE eventos_agenda SET fim = %s WHERE id = %s AND usuario_id = %s",
    "mudar_evento_situacao": (
        "UPDATE eventos_agenda SET situacao = %s::situacao_evento WHERE id = %s AND usuario_id = %s"
    ),
    "lembretes_vencidos": (
        "SELECT p.id, p.usuario_id, u.telegram_chat_id, p.descricao FROM pendencias p"
        " JOIN usuarios u ON u.id = p.usuario_id WHERE p.lembrete_aceito AND p.lembrete_em <= %s"
        " AND p.lembrete_enviado_em IS NULL AND p.situacao IN ('aberta', 'adiada')"
    ),
    "marcar_lembrete": "UPDATE pendencias SET lembrete_enviado_em = %s WHERE id = %s",
    "registrar_chamada": (
        "INSERT INTO chamadas_modelo (usuario_id, finalidade, fornecedor, modelo, tokens_entrada,"
        " tokens_saida, segundos_audio, custo_usd, latencia_ms, erro)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    ),
}


class RepositorioPostgres:
    """Banco de verdade. `url`: postgresql://usuario:senha@host:5432/perai"""

    def __init__(self, url: str):
        import psycopg  # instalado no servidor (pip install "psycopg[binary]")

        self.conn = psycopg.connect(url, autocommit=False)
        # Datas e horas dos lembretes no fuso do usuário zero (D-008); por usuário depois.
        with self.conn.cursor() as c:
            c.execute("SET TIME ZONE 'America/Sao_Paulo'")
        self.conn.commit()

    def _um(self, sql, *args):
        try:
            with self.conn.cursor() as c:
                c.execute(SQL[sql], args)
                return c.fetchone() if c.description else None
        except Exception:
            # Sem isso, um erro deixaria a conexão presa numa transação abortada.
            self.conn.rollback()
            raise

    def _todos(self, sql, *args):
        try:
            with self.conn.cursor() as c:
                c.execute(SQL[sql], args)
                return c.fetchall()
        except Exception:
            self.conn.rollback()
            raise

    def _confirmar(self):
        self.conn.commit()

    def usuario_por_telegram(self, chat_id):
        linha = self._um("usuario_por_telegram", chat_id)
        return str(linha[0]) if linha else None

    def criar_usuario(self, nome, telegram_chat_id):
        i = str(self._um("criar_usuario", nome, telegram_chat_id)[0])
        self._confirmar()
        return i

    def nome_usuario(self, usuario_id):
        linha = self._um("nome_usuario", usuario_id)
        return linha[0] if linha else None

    def salvar_mensagem(self, usuario_id, m):
        i = str(self._um("salvar_mensagem", usuario_id, m.canal, m.tipo, m.texto_original, m.transcricao,
                         m.caminho_arquivo, m.data_situacao, m.modelo_transcricao)[0])
        self._confirmar()
        return i

    def _entidade(self, usuario_id, nome, tipo="pessoa", contexto=None, descricao=None):
        linha = self._um("buscar_entidade", usuario_id, nome)
        if linha:
            if contexto:
                self._um("completar_contexto", contexto, linha[0])
            return str(linha[0])
        return str(self._um("criar_entidade", usuario_id, tipo, nome, contexto, descricao)[0])

    def salvar_extracao(self, usuario_id, mensagem_id, ext):
        mapa: dict[str, str] = {}
        try:
            for e in ext.entidades:
                self._entidade(usuario_id, e.nome, e.tipo, e.contexto, e.descricao)
            for r in ext.registros:
                dito_por = None if r.dito_por.lower() == "eu" else self._entidade(usuario_id, r.dito_por)
                i = str(self._um("salvar_registro", usuario_id, mensagem_id, r.conteudo, r.data_fato,
                                 dito_por, r.natureza, r.confianca, r.sensivel)[0])
                for nome in r.entidades:
                    self._um("ligar_entidade", i, self._entidade(usuario_id, nome), usuario_id)
                mapa[r.id] = i
            for p in ext.pendencias:
                origem = next(iter(mapa.values()), None)
                if origem is None:
                    origem = str(self._um("salvar_registro", usuario_id, mensagem_id, p.descricao, None,
                                          None, "intencao", 0.8, False)[0])
                mapa[p.id] = str(self._um("salvar_pendencia", usuario_id, origem, p.descricao, p.responsavel,
                                          p.quem, p.prazo, p.prazo_como_dito, p.lembrete_em,
                                          p.lembrete_em is not None, p.recorrencia)[0])
            self._confirmar()
        except Exception:
            self.conn.rollback()
            raise
        return mapa

    def entidades_conhecidas(self, usuario_id):
        return [l[0] for l in self._todos("entidades_conhecidas", usuario_id)]

    def memoria(self, usuario_id):
        registros = [
            Registro(str(i), str(m), c, n, d, str(df) if df else None, [], float(conf), s)
            for i, m, c, n, d, df, conf, s in self._todos("memoria_registros", usuario_id)
        ]
        pendencias = [
            Pendencia(str(i), str(m), desc, resp, quem, str(pz) if pz else None, dito,
                      le.strftime("%Y-%m-%dT%H:%M") if le else None, rec)
            for i, m, desc, resp, quem, pz, dito, le, rec in self._todos("memoria_pendencias", usuario_id)
        ]
        return registros, pendencias

    def corrigir_registro(self, usuario_id, antigo_id, novo_id, mensagem_id):
        linha = self._um("corrigir_registro", novo_id, antigo_id, usuario_id)
        if not linha:
            self.conn.rollback()
            raise PermissionError("registro inexistente ou de outro usuário")
        novo = self._um("conteudo_registro", novo_id)[0]
        self._um("salvar_correcao", usuario_id, antigo_id, linha[0], novo, mensagem_id)
        descartadas = []
        for pid, descricao, _ in self._todos("descartar_pendencias_do_registro", antigo_id, usuario_id):
            self._um("salvar_correcao_campo", usuario_id, "pendencias", pid, "situacao", "aberta",
                     "descartada", mensagem_id)
            descartadas.append(descricao)
        self._confirmar()
        return descartadas

    def ajustar_pendencia(self, usuario_id, pendencia_id, mensagem_id, lembrete_em=None, prazo=None,
                          situacao=None):
        try:
            atual = self._um("ler_pendencia", pendencia_id, usuario_id)
            if not atual:
                raise PermissionError("pendência inexistente ou de outro usuário")
            antes = {"lembrete_em": atual[0], "prazo": atual[1], "situacao": atual[2]}
            for campo, valor in (("lembrete_em", lembrete_em), ("prazo", prazo), ("situacao", situacao)):
                if valor is None:
                    continue
                self._um(f"ajustar_{'lembrete' if campo == 'lembrete_em' else campo}",
                         valor, pendencia_id, usuario_id)
                anterior = antes[campo]
                self._um("salvar_correcao_campo", usuario_id, "pendencias", pendencia_id, campo,
                         None if anterior is None else str(anterior), valor, mensagem_id)
            self._confirmar()
        except Exception:
            self.conn.rollback()
            raise

    def salvar_resposta(self, usuario_id, tipo, pergunta, resposta, citados, sem_informacao,
                        fontes_externas=None):
        i = str(self._um("salvar_resposta", usuario_id, tipo, pergunta, resposta, citados, sem_informacao,
                         list(fontes_externas or []))[0])
        self._confirmar()
        return i

    def telegram_de(self, usuario_id):
        linha = self._um("telegram_de", usuario_id)
        return linha[0] if linha else None

    def avaliar_resposta(self, usuario_id, resposta_id, avaliacao):
        return self._gravar("avaliar_resposta", avaliacao, resposta_id, usuario_id) is not None

    def metricas(self, usuario_id, desde):
        dias, msgs = self._um("metricas_mensagens", usuario_id, desde)
        regs, corrigidos = self._um("metricas_registros", usuario_id, desde)
        resp = self._todos("metricas_respostas", usuario_id, desde)
        lat = [l[0] for l in self._todos("metricas_latencias", usuario_id, desde)]
        custo = self._um("metricas_custo", usuario_id, desde)[0]
        self._confirmar()
        aval = {}
        for tipo, _, a in resp:
            if a:
                aval[(tipo, a)] = aval.get((tipo, a), 0) + 1
        return {"dias_com_captura": dias, "mensagens": msgs, "registros": regs, "corrigidos": corrigidos,
                "respostas": len(resp), "sem_informacao": sum(1 for r in resp if r[1]),
                "briefings": sum(1 for r in resp if r[0] == "briefing"), "avaliacoes": aval,
                "latencias_ms": lat, "custo_usd": float(custo)}

    def _gravar(self, sql, *args):
        try:
            linha = self._um(sql, *args)
            self._confirmar()
            return linha
        except Exception:
            self.conn.rollback()
            raise

    def criar_conexao_google(self, usuario_id, token_hash, expira_em):
        self._gravar("criar_conexao_google", token_hash, usuario_id, expira_em)

    def conexao_google(self, token_hash, agora):
        linha = self._um("conexao_google", token_hash, agora)
        self._confirmar()
        return str(linha[0]) if linha else None

    def usar_conexao_google(self, token_hash, agora):
        self._gravar("usar_conexao_google", agora, token_hash)

    def salvar_conta_google(self, usuario_id, email, refresh_cifrado, escopos):
        self._gravar("salvar_conta_google", usuario_id, email, refresh_cifrado, escopos)

    def conta_google(self, usuario_id):
        linha = self._um("conta_google", usuario_id)
        self._confirmar()
        return tuple(linha) if linha else None

    def desconectar_google(self, usuario_id, quando):
        self._gravar("desconectar_google", quando, usuario_id)

    def salvar_cancelamento(self, usuario_id, mensagem_id, google_id, titulo, inicio, tipo, recorrencia_anterior):
        return str(self._gravar("salvar_cancelamento", usuario_id, mensagem_id, google_id, titulo, inicio, tipo,
                                recorrencia_anterior)[0])

    def cancelamentos(self, usuario_id, mensagem_id):
        linhas = self._todos("cancelamentos", usuario_id, mensagem_id)
        self._confirmar()
        return [{"id": str(i), "google_evento_id": g, "titulo": t, "inicio": ini, "tipo": tipo,
                 "recorrencia_anterior": list(r) if r else None} for i, g, t, ini, tipo, r in linhas]

    def marcar_cancelamento_desfeito(self, usuario_id, cancelamento_id, quando):
        self._gravar("marcar_cancelamento_desfeito", quando, cancelamento_id, usuario_id)

    def salvar_evento(self, usuario_id, mensagem_id, registro_id, google_id, titulo, inicio, fim, local):
        return str(self._gravar("salvar_evento", usuario_id, mensagem_id, registro_id, google_id, titulo,
                                inicio, fim, local)[0])

    @staticmethod
    def _evento(linha):
        i, gid, titulo, ini, fim, local, situacao = linha
        fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M") if hasattr(d, "strftime") else d
        return {"id": str(i), "google_evento_id": gid, "titulo": titulo, "inicio": fmt(ini),
                "fim": fmt(fim), "local": local, "situacao": situacao}

    def evento(self, usuario_id, evento_id):
        linha = self._um("evento", evento_id, usuario_id)
        self._confirmar()
        return self._evento(linha) if linha else None

    def eventos_criados(self, usuario_id, desde):
        linhas = self._todos("eventos_criados", usuario_id, desde)
        self._confirmar()
        return [self._evento(l) for l in linhas]

    def mudar_evento(self, usuario_id, evento_id, mensagem_id, **campos):
        try:
            atual = self._um("evento", evento_id, usuario_id)
            if not atual:
                raise PermissionError("evento inexistente ou de outro usuário")
            antes = self._evento(atual)
            for campo, valor in campos.items():
                self._um(f"mudar_evento_{campo}", valor, evento_id, usuario_id)
                self._um("salvar_correcao_campo", usuario_id, "eventos_agenda", evento_id, campo,
                         None if antes[campo] is None else str(antes[campo]), valor, mensagem_id)
            self._confirmar()
        except Exception:
            self.conn.rollback()
            raise

    def lembretes_vencidos(self, agora):
        return [Lembrete(str(i), str(u), chat, d) for i, u, chat, d in self._todos("lembretes_vencidos", agora)]

    def marcar_lembrete_enviado(self, pendencia_id, quando):
        self._um("marcar_lembrete", quando, pendencia_id)
        self._confirmar()

    def pendencia(self, usuario_id, pendencia_id):
        linha = self._um("pendencia", pendencia_id, usuario_id)
        self._confirmar()
        if not linha:
            return None
        i, desc, prazo, lembrete, situacao, rec = linha
        return {"id": str(i), "descricao": desc, "prazo": str(prazo) if prazo else None,
                "lembrete_em": lembrete.strftime("%Y-%m-%dT%H:%M") if lembrete else None,
                "situacao": situacao, "recorrencia": rec}

    def criar_seguinte(self, usuario_id, pendencia_id, prazo, lembrete_em):
        linha = self._gravar("criar_seguinte", prazo, lembrete_em, lembrete_em is not None, pendencia_id, usuario_id)
        return str(linha[0]) if linha else None

    def salvar_despesa(self, usuario_id, mensagem_id, registro_id, d, origem, chave_nfe=None):
        linha = self._gravar("salvar_despesa", usuario_id, mensagem_id, registro_id, d.descricao, d.valor_centavos,
                             d.data, d.categoria, d.estabelecimento, d.cnpj, d.forma_pagamento, d.contexto,
                             d.local, origem, chave_nfe)
        return str(linha[0]) if linha else None

    @staticmethod
    def _despesa(linha):
        i, desc, valor, data, cat, estab, ctx, pag, local, origem, situacao = linha
        return {"id": str(i), "descricao": desc, "valor_centavos": valor, "data": str(data) if data else None,
                "categoria": cat, "estabelecimento": estab, "contexto": ctx, "forma_pagamento": pag,
                "local": local, "origem": origem, "situacao": situacao}

    def despesas(self, usuario_id, desde):
        linhas = self._todos("despesas", usuario_id, desde)
        self._confirmar()
        return [self._despesa(l) for l in linhas]

    def despesa(self, usuario_id, despesa_id):
        linha = self._um("despesa", despesa_id, usuario_id)
        self._confirmar()
        return self._despesa(linha) if linha else None

    def mudar_despesa(self, usuario_id, despesa_id, mensagem_id, **campos):
        try:
            atual = self._um("despesa", despesa_id, usuario_id)
            if not atual:
                raise PermissionError("despesa inexistente ou de outro usuário")
            antes = self._despesa(atual)
            for campo, valor in campos.items():
                self._um(f"mudar_despesa_{campo}", valor, despesa_id, usuario_id)
                self._um("salvar_correcao_campo", usuario_id, "despesas", despesa_id, campo,
                         None if antes[campo] is None else str(antes[campo]),
                         None if valor is None else str(valor), mensagem_id)
            self._confirmar()
        except Exception:
            self.conn.rollback()
            raise

    def vincular_despesa(self, usuario_id, despesa_id, tipo, google_id=None, titulo=None, inicio=None,
                         cliente=None):
        return self._gravar("vincular_despesa", usuario_id, tipo, google_id, titulo, inicio, cliente,
                            despesa_id, usuario_id) is not None

    def vinculos_despesas(self, usuario_id, despesa_ids):
        if not despesa_ids:
            return {}
        saida: dict[str, list[dict]] = {}
        for d_id, tipo, gid, titulo, inicio, cliente in self._todos("vinculos_despesas", usuario_id, list(despesa_ids)):
            saida.setdefault(str(d_id), []).append({
                "tipo": tipo, "google_evento_id": gid, "titulo": titulo, "cliente": cliente,
                "inicio": inicio.strftime("%Y-%m-%dT%H:%M") if hasattr(inicio, "strftime") else inicio})
        self._confirmar()
        return saida

    def salvar_viagem(self, usuario_id, mensagem_id, nome, inicio, fim, cidades, acompanhantes, objetivo):
        return str(self._gravar("salvar_viagem", usuario_id, mensagem_id, nome, inicio, fim, list(cidades),
                                list(acompanhantes), objetivo)[0])

    @staticmethod
    def _viagem(linha):
        i, nome, ini, fim, cidades, acomp, obj, acompanhar, hora, situacao = linha[:10]
        return {"id": str(i), "nome": nome, "inicio": str(ini), "fim": str(fim), "cidades": list(cidades or []),
                "acompanhantes": list(acomp or []), "objetivo": obj, "acompanhar": acompanhar,
                "hora_checkin": hora.strftime("%H:%M") if hasattr(hora, "strftime") else str(hora)[:5],
                "situacao": situacao}

    def viagens(self, usuario_id):
        linhas = self._todos("viagens", usuario_id)
        self._confirmar()
        return [self._viagem(l) for l in linhas]

    def viagem(self, usuario_id, viagem_id):
        linha = self._um("viagem", viagem_id, usuario_id)
        self._confirmar()
        return self._viagem(linha) if linha else None

    def mudar_viagem(self, usuario_id, viagem_id, **campos):
        try:
            for campo, valor in campos.items():
                self._um(f"mudar_viagem_{campo}", valor, viagem_id, usuario_id)
            self._confirmar()
        except Exception:
            self.conn.rollback()
            raise

    def viagens_para_checkin(self, agora):
        linhas = self._todos("viagens_para_checkin", agora.date(), agora.strftime("%H:%M"), agora.date())
        self._confirmar()
        return [{**self._viagem(l), "usuario_id": str(l[10]), "telegram_chat_id": l[11]} for l in linhas]

    def marcar_checkin(self, viagem_id, dia):
        return self._gravar("marcar_checkin", viagem_id, dia) is not None

    def recorrentes_vencidas(self, hoje):
        linhas = self._todos("recorrentes_vencidas", hoje)
        self._confirmar()
        return [(str(i), str(u)) for i, u in linhas]

    def registrar_chamada(self, usuario_id, finalidade, fornecedor, modelo, tokens_entrada=0,
                          tokens_saida=0, segundos_audio=0, custo_usd=0, latencia_ms=0, erro=None):
        self._um("registrar_chamada", usuario_id, finalidade, fornecedor, modelo, tokens_entrada,
                 tokens_saida, segundos_audio, custo_usd, latencia_ms, erro)
        self._confirmar()
