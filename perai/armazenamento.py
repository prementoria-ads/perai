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
    def salvar_mensagem(self, usuario_id: str, m: NovaMensagem) -> str: ...
    def salvar_extracao(self, usuario_id: str, mensagem_id: str, ext: Extracao) -> dict[str, str]: ...
    def entidades_conhecidas(self, usuario_id: str) -> list[str]: ...
    def memoria(self, usuario_id: str) -> tuple[list[Registro], list[Pendencia]]: ...
    def corrigir_registro(self, usuario_id: str, antigo_id: str, novo_id: str, mensagem_id: str) -> None: ...
    def salvar_resposta(self, usuario_id: str, tipo: str, pergunta: str, resposta: str,
                        citados: list[str], sem_informacao: bool) -> str: ...
    def lembretes_vencidos(self, agora: datetime) -> list[Lembrete]: ...
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


class RepositorioMemoria:
    def __init__(self):
        self.b = _Banco()

    def usuario_por_telegram(self, chat_id):
        return next((i for i, u in self.b.usuarios.items() if u["telegram_chat_id"] == chat_id), None)

    def criar_usuario(self, nome, telegram_chat_id):
        i = _novo_id()
        self.b.usuarios[i] = {"nome": nome, "telegram_chat_id": telegram_chat_id}
        return i

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
                                    "lembrete_enviado_em": None}
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
            if r["usuario_id"] == usuario_id and r["situacao"] == "ativo"
        ]
        pendencias = [
            Pendencia(i, self.b.registros[p["registro_id"]]["mensagem_id"], p["descricao"],
                      p["responsavel"], p["quem"], p["prazo"], p["prazo_como_dito"], p["lembrete_em"])
            for i, p in self.b.pendencias.items()
            if p["usuario_id"] == usuario_id and p["situacao"] in ("aberta", "adiada")
        ]
        return registros, pendencias

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

    def salvar_resposta(self, usuario_id, tipo, pergunta, resposta, citados, sem_informacao):
        if not citados and not sem_informacao:
            raise ValueError("resposta sem fonte só pode dizer que não há informação")
        i = _novo_id()
        self.b.respostas[i] = {"usuario_id": usuario_id, "tipo": tipo, "pergunta": pergunta,
                               "resposta": resposta, "registros_citados": citados,
                               "sem_informacao": sem_informacao}
        return i

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

    def registrar_chamada(self, usuario_id, finalidade, fornecedor, modelo, tokens_entrada=0,
                          tokens_saida=0, segundos_audio=0, custo_usd=0, latencia_ms=0, erro=None):
        self.b.chamadas.append(dict(usuario_id=usuario_id, finalidade=finalidade, fornecedor=fornecedor,
                                    modelo=modelo, tokens_entrada=tokens_entrada, tokens_saida=tokens_saida,
                                    segundos_audio=segundos_audio, custo_usd=custo_usd,
                                    latencia_ms=latencia_ms, erro=erro))


# --- PostgreSQL ------------------------------------------------------------------

SQL = {
    "usuario_por_telegram": "SELECT id FROM usuarios WHERE telegram_chat_id = %s",
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
        " prazo_como_dito, lembrete_em, lembrete_aceito) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id"
    ),
    "entidades_conhecidas": "SELECT nome FROM entidades WHERE usuario_id = %s ORDER BY criado_em DESC LIMIT 300",
    "memoria_registros": (
        "SELECT r.id, r.mensagem_id, r.conteudo, r.natureza, coalesce(e.nome, 'eu'), r.data_fato,"
        " r.confianca, r.sensivel FROM registros r LEFT JOIN entidades e ON e.id = r.dito_por_id"
        " WHERE r.usuario_id = %s AND r.situacao = 'ativo' ORDER BY r.criado_em"
    ),
    "memoria_pendencias": (
        "SELECT p.id, r.mensagem_id, p.descricao, p.responsavel, p.quem, p.prazo, p.prazo_como_dito,"
        " p.lembrete_em FROM pendencias p JOIN registros r ON r.id = p.registro_id"
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
    "salvar_resposta": (
        "INSERT INTO respostas (usuario_id, tipo, pergunta, resposta, registros_citados, sem_informacao)"
        " VALUES (%s, %s, %s, %s, %s::uuid[], %s) RETURNING id"
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
        with self.conn.cursor() as c:
            c.execute(SQL[sql], args)
            linha = c.fetchone() if c.description else None
        return linha

    def _todos(self, sql, *args):
        with self.conn.cursor() as c:
            c.execute(SQL[sql], args)
            return c.fetchall()

    def _confirmar(self):
        self.conn.commit()

    def usuario_por_telegram(self, chat_id):
        linha = self._um("usuario_por_telegram", chat_id)
        return str(linha[0]) if linha else None

    def criar_usuario(self, nome, telegram_chat_id):
        i = str(self._um("criar_usuario", nome, telegram_chat_id)[0])
        self._confirmar()
        return i

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
                                          p.lembrete_em is not None)[0])
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
                      le.isoformat(timespec="minutes") if le else None)
            for i, m, desc, resp, quem, pz, dito, le in self._todos("memoria_pendencias", usuario_id)
        ]
        return registros, pendencias

    def corrigir_registro(self, usuario_id, antigo_id, novo_id, mensagem_id):
        linha = self._um("corrigir_registro", novo_id, antigo_id, usuario_id)
        if not linha:
            self.conn.rollback()
            raise PermissionError("registro inexistente ou de outro usuário")
        novo = self._um("conteudo_registro", novo_id)[0]
        self._um("salvar_correcao", usuario_id, antigo_id, linha[0], novo, mensagem_id)
        self._confirmar()

    def salvar_resposta(self, usuario_id, tipo, pergunta, resposta, citados, sem_informacao):
        i = str(self._um("salvar_resposta", usuario_id, tipo, pergunta, resposta, citados, sem_informacao)[0])
        self._confirmar()
        return i

    def lembretes_vencidos(self, agora):
        return [Lembrete(str(i), str(u), chat, d) for i, u, chat, d in self._todos("lembretes_vencidos", agora)]

    def marcar_lembrete_enviado(self, pendencia_id, quando):
        self._um("marcar_lembrete", quando, pendencia_id)
        self._confirmar()

    def registrar_chamada(self, usuario_id, finalidade, fornecedor, modelo, tokens_entrada=0,
                          tokens_saida=0, segundos_audio=0, custo_usd=0, latencia_ms=0, erro=None):
        self._um("registrar_chamada", usuario_id, finalidade, fornecedor, modelo, tokens_entrada,
                 tokens_saida, segundos_audio, custo_usd, latencia_ms, erro)
        self._confirmar()
