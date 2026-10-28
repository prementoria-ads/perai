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
    def lembretes_vencidos(self, agora: datetime) -> list[Lembrete]: ...
    def pendencia(self, usuario_id: str, pendencia_id: str) -> dict | None: ...
    def criar_seguinte(self, usuario_id: str, pendencia_id: str, prazo: str,
                       lembrete_em: str | None) -> str | None: ...
    def recorrentes_vencidas(self, hoje) -> list[tuple[str, str]]: ...
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
        self.b.mensagens[i] = {"usuario_id": usuario_id, **m.__dict__}
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
                               "fontes_externas": fontes_externas, "sem_informacao": sem_informacao}
        return i

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

    def recorrentes_vencidas(self, hoje):
        linhas = self._todos("recorrentes_vencidas", hoje)
        self._confirmar()
        return [(str(i), str(u)) for i, u in linhas]

    def registrar_chamada(self, usuario_id, finalidade, fornecedor, modelo, tokens_entrada=0,
                          tokens_saida=0, segundos_audio=0, custo_usd=0, latencia_ms=0, erro=None):
        self._um("registrar_chamada", usuario_id, finalidade, fornecedor, modelo, tokens_entrada,
                 tokens_saida, segundos_audio, custo_usd, latencia_ms, erro)
        self._confirmar()
