"""CRM de contas, etapas 8 a 13 (D-095 a D-100), como métodos do serviço principal (Perai herda esta
classe). Grava o que a extração trouxe em "crm" e monta /atividades, /saude, /cadencia, /parceiros,
/meddpicc, /mapa, /maturidade, /riscos e /segmento. Toda conta é do código (perai.crm)."""

from __future__ import annotations

import re
from datetime import date

from perai import contas as contas_mod, crm


def _agora():
    from perai.servico import _agora as agora   # import tardio: servico importa este módulo
    return agora()


def _hoje() -> str:
    return _agora().date().isoformat()


class CRMMixin:
    # -- gravar o que a extração trouxe ----------------------------------------------------------

    def _conta_tipo(self, usuario_id: str, nome: str, tipo: str) -> str:
        for c in self.repo.contas_relacionamento(usuario_id):
            if contas_mod.mesma_conta(c["nome"], nome):
                return c["id"]
        return self.repo.criar_conta_relacionamento(usuario_id, nome.strip()[:120], tipo)

    def _aplicar_crm(self, usuario_id: str, mensagem_id: str, ext, codigos_o: dict) -> list[str]:
        dados = getattr(ext, "crm", None)
        if crm.vazio(dados):
            return []
        hoje = _hoje()
        linhas = []
        nome_de = lambda cid: (self._nome_conta(usuario_id, cid) or {}).get("nome", "?")
        for a in dados["atividades"]:
            conta_id = self._conta_parecida(usuario_id, a["conta"])
            if a["pelo_parceiro"]:
                self._conta_tipo(usuario_id, a["pelo_parceiro"], "parceiro")
            op = codigos_o.get(a["oportunidade"]) if a["oportunidade"] else None
            data = a["data"] or (hoje if a["situacao"] == "feita" else None)
            if a["situacao"] == "feita":   # a marcada que aconteceu deixa de cobrar "como foi"
                for x in self.repo.crm(usuario_id, "atividades"):
                    if (x["conta_id"] == conta_id and x["situacao"] == "agendada" and x.get("data")
                            and data and x["data"] <= data):
                        self.repo.mudar_crm(usuario_id, "atividades", x["id"], {"situacao": "substituida"})
            self.repo.salvar_crm(usuario_id, "atividades", {
                "conta_id": conta_id, "oportunidade_id": op["id"] if op else None, "mensagem_id": mensagem_id,
                "tipo": a["tipo"], "fase": a["fase"], "data": data, "situacao": a["situacao"], "resumo": a["resumo"],
                "proximo_passo": a["proximo_passo"], "participantes": a["participantes"],
                "pelo_parceiro": a["pelo_parceiro"], "spin": a["spin"]})
            linhas.append(("🗓️ Marcado" if a["situacao"] == "agendada" else "📞 Atividade")
                          + f": {crm.TIPOS[a['tipo']]} · {nome_de(conta_id)} · {crm._dia(data)}"
                          + (f" · {crm.FASES[a['fase']]}" if a["fase"] else "")
                          + (f" · pelo parceiro {a['pelo_parceiro']}" if a["pelo_parceiro"] else "")
                          + (f"\n   ➡️ {a['proximo_passo']}" if a["proximo_passo"] else ""))
            perguntas = crm.lacunas_spin(a["spin"])
            if perguntas and a["situacao"] == "feita":
                linhas.append("   💬 Para a próxima conversa (SPIN): " + " · ".join(perguntas))
        abertos = self.repo.crm(usuario_id, "sinais_conta")
        for s in dados["sinais"]:
            conta_id = self._conta_parecida(usuario_id, s["conta"])
            if s["resolvido"]:
                fechados = [x for x in abertos if x["conta_id"] == conta_id and x["situacao"] == "aberto"
                            and x["tipo"] == s["tipo"]]
                for x in fechados:
                    self.repo.mudar_crm(usuario_id, "sinais_conta", x["id"], {"situacao": "resolvido",
                                                                              "resolvido_em": hoje})
                linhas.append(f"✅ {nome_de(conta_id)}: {crm.TIPOS_SINAL[s['tipo']]} resolvido"
                              + ("" if fechados else " (não havia alerta aberto)") + ".")
                continue
            self.repo.salvar_crm(usuario_id, "sinais_conta", {
                "conta_id": conta_id, "mensagem_id": mensagem_id, "tipo": s["tipo"], "descricao": s["descricao"],
                "dito_por": s["dito_por"], "situacao": "aberto"})
            quem = "" if s["dito_por"].lower() == "eu" else f" (segundo {s['dito_por']})"
            linhas.append(f"{'👏' if s['tipo'] == 'elogio' else '⚠️'} {nome_de(conta_id)}: "
                          f"{crm.TIPOS_SINAL[s['tipo']]} — {s['descricao'][:120]}{quem}")
        for m in dados["maturidade"]:
            conta_id = self._conta_parecida(usuario_id, m["conta"])
            self.repo.salvar_crm(usuario_id, "maturidade_cliente", {
                "conta_id": conta_id, "mensagem_id": mensagem_id, "dominio": m["dominio"], "nivel": m["nivel"],
                "nota": m["nota"], "dito_por": m["dito_por"]})
            linhas.append(f"📶 {nome_de(conta_id)}: {crm.DOMINIOS[m['dominio']]} nível {m['nivel']} "
                          f"({crm.NIVEIS[m['nivel']]}).")
        for m in dados["mapa"]:
            conta_id = self._conta_parecida(usuario_id, m["conta"])
            self.repo.salvar_crm(usuario_id, "mapa_ofertas", {
                "conta_id": conta_id, "mensagem_id": mensagem_id, "oferta": m["oferta"], "situacao": m["situacao"],
                "concorrente": m["concorrente"], "dito_por": m["dito_por"]})
            linhas.append(f"🗺️ {nome_de(conta_id)}: {m['oferta']} — {crm.SITUACOES_MAPA[m['situacao']]}"
                          + (f" ({m['concorrente']})" if m["concorrente"] else "") + ".")
        for m in dados["meddpicc"]:
            op = codigos_o.get(m["oportunidade"])
            if not op:
                continue
            self.repo.salvar_crm(usuario_id, "meddpicc", {
                "oportunidade_id": op["id"], "mensagem_id": mensagem_id, "elemento": m["elemento"], "nota": m["nota"],
                "evidencia": m["evidencia"], "dito_por": m["dito_por"]})
            linhas.append(f"🎯 {op['titulo']}: {crm.MEDDPICC[m['elemento']][0]} "
                          f"{'confirmado pelo cliente' if m['nota'] == 2 else 'citado'} (/meddpicc).")
        for p in dados["parceiros_projeto"]:
            op = self._achar_projeto(usuario_id, p["projeto"])
            if op is None:
                linhas.append(f"❓ Não achei o projeto “{p['projeto']}” para o parceiro {p['parceiro']}.")
                continue
            pid = self._conta_tipo(usuario_id, p["parceiro"], "parceiro")
            self.repo.salvar_crm(usuario_id, "parceiros_projeto", {
                "oportunidade_id": op["id"], "parceiro_conta_id": pid, "mensagem_id": mensagem_id,
                "parceiro": nome_de(pid), "papel": p["papel"]})
            linhas.append(f"🤝 {nome_de(pid)} {crm.PAPEIS_PARCEIRO[p['papel']]} o projeto {op['titulo']} "
                          f"({op.get('conta') or 'sem conta'}).")
        if dados["fatores"]:
            existentes = self.repo.crm(usuario_id, "fatores_risco")
        for f in dados["fatores"]:
            igual = next((x for x in existentes if contas_mod.normalizar(x["titulo"]) == contas_mod.normalizar(f["titulo"])),
                         None)
            if igual:
                mudou = {k: f[k] for k in ("situacao", "intensidade") if f[k] != igual[k]}
                if mudou:
                    self.repo.mudar_crm(usuario_id, "fatores_risco", igual["id"], mudou)
                    linhas.append(f"🧭 Fator atualizado: {igual['titulo']} ({', '.join(mudou.values())}).")
                continue
            self.repo.salvar_crm(usuario_id, "fatores_risco", {"mensagem_id": mensagem_id, **f})
            linhas.append(f"🧭 Fator no radar: {f['titulo']} · {'ameaça' if f['direcao'] == 'ameaca' else 'oportunidade'} "
                          f"{f['intensidade']} · {', '.join(f['segmentos']) or 'segmento não informado'} (/riscos).")
        for s in dados["segmentos"]:
            conta_id = self._conta_parecida(usuario_id, s["conta"])
            self.repo.salvar_crm(usuario_id, "segmentos_conta", {"conta_id": conta_id, "mensagem_id": mensagem_id,
                                                                 "segmento": s["segmento"]})
            linhas.append(f"🏷️ {nome_de(conta_id)}: segmento {s['segmento']}.")
        return linhas

    # -- leitura comum ---------------------------------------------------------------------------

    def _segmentos(self, usuario_id: str) -> dict[str, str]:
        return {k[0]: v["segmento"] for k, v in crm.ultima_por(self.repo.crm(usuario_id, "segmentos_conta"),
                                                               "conta_id").items() if k[0]}

    def _cadencia(self, usuario_id: str) -> dict:
        return crm.cadencia(self.repo.config(usuario_id, "cadencia"))

    def _achar_conta(self, usuario_id: str, nome: str) -> dict | None:
        nome = (nome or "").strip()
        if not nome:
            return None
        contas = self.repo.contas_relacionamento(usuario_id)
        return next((c for c in contas if contas_mod.mesma_conta(c["nome"], nome)), None)

    def _saudes(self, usuario_id: str) -> tuple[list, int]:
        """(saúde de cada conta com algum registro de CRM, quantas contas ficaram de fora sem registro)."""
        hoje = _hoje()
        contas = self.repo.contas_relacionamento(usuario_id)
        ativs = self.repo.crm(usuario_id, "atividades")
        sinais = self.repo.crm(usuario_id, "sinais_conta")
        fatores = self.repo.crm(usuario_id, "fatores_risco")
        parceiros = {x["parceiro_conta_id"] for x in self.repo.crm(usuario_id, "parceiros_projeto")}
        ops = self.repo.oportunidades(usuario_id)
        props = self.repo.propostas(usuario_id)
        fats = self.repo.faturas(usuario_id)
        _, pend = self.repo.memoria(usuario_id)
        segs = self._segmentos(usuario_id)
        _, contas_r = self._resultados(usuario_id)
        por_nome = {r.nome: r for r in contas_r}
        cad = self._cadencia(usuario_id)
        saida, fora = [], 0
        for c in contas:
            a_c = sorted((a for a in ativs if a["conta_id"] == c["id"] and a["situacao"] != "substituida"),
                         key=lambda a: a.get("data") or "")
            o_c = [o for o in ops if o["conta_id"] == c["id"]]
            s_c = [s for s in sinais if s["conta_id"] == c["id"]]
            if not (a_c or o_c or s_c or c["id"] in parceiros):
                fora += 1
                continue
            ids_o = {o["id"] for o in o_c}
            saida.append(crm.saude(c, hoje, cad, a_c, o_c, [p for p in props if p.get("conta_id") == c["id"]
                                                            or p.get("oportunidade_id") in ids_o],
                                   [f for f in fats if f["oportunidade_id"] in ids_o], pend, s_c, fatores,
                                   segs.get(c["id"]), por_nome.get(c["nome"])))
        saida.sort(key=lambda s: (-crm.ORDEM[s.cor], -(s.dias_sem_contato or 0), s.nome.lower()))
        return saida, fora

    def alertas_crm(self, usuario_id: str, n: int = 3) -> list[str]:
        try:
            saudes, _ = self._saudes(usuario_id)
        except Exception:
            return []
        return crm.alertas(saudes, n)

    # -- comandos --------------------------------------------------------------------------------

    def listar_atividades(self, usuario_id: str, conta: str = "") -> str:
        ativs = [a for a in self.repo.crm(usuario_id, "atividades") if a["situacao"] != "substituida"]
        nomes = {c["id"]: c["nome"] for c in self.repo.contas_relacionamento(usuario_id)}
        alvo = self._achar_conta(usuario_id, conta) if conta else None
        if conta and alvo is None:
            return f"Não achei a conta “{conta}”. Veja os nomes em /contas."
        if alvo:
            ativs = [a for a in ativs if a["conta_id"] == alvo["id"]]
        if not ativs:
            return ("📞 Nenhuma atividade registrada" + (f" com {alvo['nome']}" if alvo else "") + ". Conte do seu "
                    "jeito: “visitei a Câmara de Maringá com o Rafael, apresentei o Legislativo Digital, ficou de "
                    "mandar a proposta até sexta”.")
        hoje = _hoje()
        futuras = sorted((a for a in ativs if a["situacao"] == "agendada" and (a.get("data") or "9") >= hoje),
                         key=lambda a: a.get("data") or "")
        sem_registro = [a for a in ativs if a["situacao"] == "agendada" and a not in futuras]
        feitas = sorted((a for a in ativs if a["situacao"] == "feita"), key=lambda a: a.get("data") or "", reverse=True)
        mostrar_conta = alvo is None
        linhas = ["📞 Atividades" + (f" · {alvo['nome']}" if alvo else " (últimas)")]
        if futuras:
            linhas.append("\nMarcadas:")
            linhas += [crm.linha_atividade(a, nomes.get(a["conta_id"]) if mostrar_conta else None) for a in futuras[:10]]
        if sem_registro:
            linhas.append("\nMarcadas que já passaram, sem registro de como foi (conte e eu registro):")
            linhas += [crm.linha_atividade(a, nomes.get(a["conta_id"]) if mostrar_conta else None)
                       for a in sem_registro[:10]]
        if not feitas:
            return "\n".join(linhas)
        linhas.append("\nFeitas:")
        linhas += [crm.linha_atividade(a, nomes.get(a["conta_id"]) if mostrar_conta else None) for a in feitas[:15]]
        if len(feitas) > 15:
            linhas.append(f"… e mais {len(feitas) - 15}. Use /atividades <conta> para ver uma conta.")
        return "\n".join(linhas)

    def saude_contas(self, usuario_id: str, conta: str = "") -> str:
        saudes, fora = self._saudes(usuario_id)
        if conta:
            alvo = self._achar_conta(usuario_id, conta)
            saudes = [s for s in saudes if alvo and s.conta_id == alvo["id"]]
            if not saudes:
                return f"Não achei atividade nem negócio da conta “{conta}”."
        if not saudes:
            return ("🩺 Ainda não há contas com atividades ou negócios registrados. Conte uma call, visita ou "
                    "reunião e a saúde da conta começa a ser calculada.")
        cores = {k: sum(1 for s in saudes if s.cor == k) for k in ("vermelho", "amarelo", "verde")}
        linhas = [f"🩺 Saúde das contas (calculada pelo Peraí): 🔴 {cores['vermelho']} · 🟡 {cores['amarelo']} · "
                  f"🟢 {cores['verde']}", ""]
        linhas += [crm.texto_saude(s) for s in saudes]
        if fora and not conta:
            linhas.append(f"\n{fora} conta(s) sem nenhuma atividade nem negócio registrado ficaram de fora.")
        linhas.append("\nCadência de contato: /cadencia · O que oferecer: /mapa <conta> · Riscos: /riscos")
        return "\n".join(linhas)

    def mudar_cadencia(self, usuario_id: str, args: list[str]) -> str:
        atual = self._cadencia(usuario_id)
        if not args:
            return crm.texto_cadencia(atual)
        nova, erro = crm.aplicar_cadencia(atual, args)
        if erro:
            return erro
        self.repo.definir_config(usuario_id, "cadencia", nova)
        return "✅ Cadência atualizada.\n\n" + crm.texto_cadencia(nova)

    def listar_parceiros(self, usuario_id: str) -> str:
        pp = self.repo.crm(usuario_id, "parceiros_projeto")
        contas = self.repo.contas_relacionamento(usuario_id)
        ops = {o["id"]: o for o in self.repo.oportunidades(usuario_id)}
        sinais = self.repo.crm(usuario_id, "sinais_conta")
        ativs = self.repo.crm(usuario_id, "atividades")
        contratos = self.repo.contratos(usuario_id)
        ids = {x["parceiro_conta_id"] for x in pp} | {c["id"] for c in contas if c.get("tipo") == "parceiro"}
        parceiros = [c for c in contas if c["id"] in ids]
        if not parceiros:
            return ("🤝 Nenhum parceiro registrado. Diga, por exemplo: “a MW Consulting vai entregar o projeto da "
                    "Câmara” ou “a Altatech indicou a Coamo”.")
        linhas = ["🤝 Parceiros"]
        for p in sorted(parceiros, key=lambda c: c["nome"].lower()):
            linhas.append(f"\n{p['nome']}")
            projs = [x for x in pp if x["parceiro_conta_id"] == p["id"]]
            ultimos = crm.ultima_por(projs, "oportunidade_id")
            clientes = set()
            for x in ultimos.values():
                op = ops.get(x["oportunidade_id"])
                if op:
                    clientes.add(op.get("conta") or "sem conta")
                    linhas.append(f"   📦 {crm.PAPEIS_PARCEIRO[x['papel']]}: {op['titulo']} · {op.get('conta') or 'sem conta'}"
                                  f" · {contas_mod.ROTULO_ETAPA.get(op['etapa'], op['etapa'])}")
            pelo = [a for a in ativs if a.get("pelo_parceiro") and contas_mod.mesma_conta(a["pelo_parceiro"], p["nome"])]
            if pelo:
                linhas.append(f"   📞 {len(pelo)} atividade(s) com clientes feitas pelo parceiro")
            nomes_clientes = {c["nome"]: c["id"] for c in contas}
            problemas = [s for s in sinais if s["situacao"] == "aberto" and s["tipo"] == "entrega"
                         and (contas_mod.mesma_conta(p["nome"], s["descricao"])
                              or s["conta_id"] in {nomes_clientes.get(n) for n in clientes})]
            for s in problemas:
                linhas.append(f"   ⚠️ Entrega: {s['descricao'][:100]} (desde {crm._dia(s['criado_em'][:10])})")
            k = [x for x in contratos if x.get("conta") and contas_mod.mesma_conta(x["conta"], p["nome"])]
            if k:
                linhas.append(f"   🧾 {len(k)} contrato(s) ou acordo(s) de indicação (/contratos)")
            if not (ultimos or pelo or problemas or k):
                linhas.append("   Sem projetos nem atividades registrados ainda.")
        return "\n".join(linhas)

    def listar_meddpicc(self, usuario_id: str, alvo: str = "") -> str:
        ops = [o for o in self.repo.oportunidades(usuario_id) if o["etapa"] not in ("ganha", "perdida")]
        if alvo:
            ops = [o for o in ops if contas_mod.mesma_conta(o["titulo"], alvo)
                   or (o.get("conta") and contas_mod.mesma_conta(o["conta"], alvo))]
        if not ops:
            return ("🎯 Nenhuma oportunidade em aberto" + (f" para “{alvo}”" if alvo else "") + ". O MEDDPICC é "
                    "calculado para cada oportunidade aberta (/oportunidades).")
        ev = self.repo.crm(usuario_id, "meddpicc")
        placares = [crm.placar(o, [e for e in ev if e["oportunidade_id"] == o["id"]]) for o in ops]
        placares.sort(key=lambda p: p.total)
        linhas = ["🎯 MEDDPICC (0 nada · 1 citado · 2 confirmado pelo cliente; máximo 16). Só conta o que foi "
                  "registrado com fonte.", ""]
        if alvo or len(placares) <= 3:
            linhas += [crm.texto_placar(p) + "\n" for p in placares]
        else:
            for p in placares:
                faltam = ", ".join(crm.MEDDPICC[k][0] for k in p.lacunas[:3])
                linhas.append(f"• {p.op['titulo']} · {p.op.get('conta') or 'sem conta'}: {p.total}/16"
                              + (f" · falta: {faltam}" if faltam else ""))
            linhas.append("\nDetalhe de uma: /meddpicc <conta ou título>")
        return "\n".join(linhas).rstrip()

    def mostrar_mapa(self, usuario_id: str, conta: str = "") -> str:
        cat = crm.catalogo(self.repo.config(usuario_id, "catalogo_ofertas"))
        mapa = self.repo.crm(usuario_id, "mapa_ofertas")
        ops = self.repo.oportunidades(usuario_id)
        mat = self.repo.crm(usuario_id, "maturidade_cliente")
        saudes = {s.conta_id: s for s in self._saudes(usuario_id)[0]}
        _, contas_r = self._resultados(usuario_id)
        saldo_de = {r.nome: r.saldo for r in contas_r}
        contas = self.repo.contas_relacionamento(usuario_id)
        if conta:
            alvo = self._achar_conta(usuario_id, conta)
            if alvo is None:
                return f"Não achei a conta “{conta}”. Veja os nomes em /contas."
            alvos = [alvo]
        else:
            alvos = [c for c in contas if c.get("tipo") != "parceiro" and (
                any(m["conta_id"] == c["id"] for m in mapa) or any(o["conta_id"] == c["id"] for o in ops))]
        blocos = []
        for c in alvos:
            g = crm.grade(cat, [m for m in mapa if m["conta_id"] == c["id"]],
                          [o for o in ops if o["conta_id"] == c["id"]])
            niveis = {k: v["nivel"] for k, v in crm.maturidade_conta([m for m in mat if m["conta_id"] == c["id"]]).items()}
            sug = crm.sugestoes(cat, g, niveis, saudes.get(c["id"]), saldo_de.get(c["nome"]))
            blocos.append((c, g, sug))
        if conta:
            c, g, sug = blocos[0]
            return crm.texto_mapa(c["nome"], cat, g, sug)
        if not blocos:
            return ("🗺️ Ainda não há mapa. Ele se preenche com as oportunidades e com o que você contar: “a Câmara "
                    "usa Fortinet”, “a prefeitura não tem câmeras”. Para uma conta: /mapa <conta>.")
        linhas = ["🗺️ Melhores oportunidades de expansão na carteira (calculado pelo Peraí)"]
        com = [(c, s) for c, _, s in blocos if s and not s[-1].startswith("⚠️")]
        for c, sug in com[:5]:
            linhas.append(f"\n{c['nome']}:\n" + "\n".join(f"   • {x}" for x in sug))
        if not com:
            linhas.append("\nNenhuma sugestão ainda: falta saber o que os clientes têm. Conte o que viu nas visitas.")
        linhas.append("\nGrade completa de uma conta: /mapa <conta>")
        return "\n".join(linhas)

    def mostrar_maturidade(self, usuario_id: str, conta: str = "") -> str:
        mat = self.repo.crm(usuario_id, "maturidade_cliente")
        if conta:
            alvo = self._achar_conta(usuario_id, conta)
            if alvo is None:
                return f"Não achei a conta “{conta}”. Veja os nomes em /contas."
            return crm.texto_maturidade(alvo["nome"], crm.maturidade_conta([m for m in mat if m["conta_id"] == alvo["id"]]))
        ops = self.repo.oportunidades(usuario_id)
        ev = self.repo.crm(usuario_id, "meddpicc")
        placares = [crm.placar(o, [e for e in ev if e["oportunidade_id"] == o["id"]]) for o in ops]
        contas = self.repo.contas_relacionamento(usuario_id)
        p = crm.processo([c["id"] for c in contas if c.get("tipo") != "parceiro"],
                         [a for a in self.repo.crm(usuario_id, "atividades") if a["situacao"] != "substituida"],
                         ops, placares, _hoje())
        nomes = {c["id"]: c["nome"] for c in contas}
        avaliadas = sorted({m["conta_id"] for m in mat if m["conta_id"]}, key=lambda i: nomes.get(i, ""))
        linhas = [crm.texto_processo(p), ""]
        if avaliadas:
            linhas.append("Clientes com maturidade registrada: " + ", ".join(nomes.get(i, "?") for i in avaliadas)
                          + ". Detalhe: /maturidade <conta>")
        else:
            linhas.append("Nenhum cliente com maturidade de TI registrada. Conte o que viu: “a prefeitura não tem "
                          "firewall e o data center é improvisado”.")
        return "\n".join(linhas)

    def listar_riscos(self, usuario_id: str) -> str:
        fatores = self.repo.crm(usuario_id, "fatores_risco")
        segs = self._segmentos(usuario_id)
        contas = self.repo.contas_relacionamento(usuario_id)
        nomes = {c["id"]: c["nome"] for c in contas}
        _, contas_r = self._resultados(usuario_id)
        por_id = {}
        for r in contas_r:
            cid = next((c["id"] for c in contas if c["nome"] == r.nome), None)
            if cid:
                por_id[cid] = r
        exps = crm.exposicao(fatores, segs, por_id)
        total = sum(r.faturamento for r in por_id.values())
        clientes = {o["conta_id"] for o in self.repo.oportunidades(usuario_id) if o["conta_id"]}
        sem = [nomes[c] for c in sorted(clientes, key=lambda i: nomes.get(i, "")) if c not in segs and c in nomes]
        return crm.texto_riscos(exps, crm.concentracao(por_id, nomes, segs), nomes, total, sem)

    def definir_segmento(self, usuario_id: str, args: list[str]) -> str:
        if len(args) < 2:
            return ("Use: /segmento <conta> <segmento>. Ex.: /segmento Betnova apostas\nSegmentos: "
                    + ", ".join(crm.SEGMENTOS))
        seg = crm.segmento(args[-1])
        if seg is None:
            return "Não conheço esse segmento. Use um destes: " + ", ".join(crm.SEGMENTOS)
        nome = " ".join(args[:-1])
        alvo = self._achar_conta(usuario_id, nome)
        if alvo is None:
            return f"Não achei a conta “{nome}”. Veja os nomes em /contas."
        self.repo.salvar_crm(usuario_id, "segmentos_conta", {"conta_id": alvo["id"], "mensagem_id": None,
                                                             "segmento": seg})
        return f"🏷️ {alvo['nome']}: segmento {seg}. O radar (/riscos) já considera."

    # -- para a consulta livre -------------------------------------------------------------------

    def memoria_crm(self, usuario_id: str, pergunta: str) -> tuple[str, dict]:
        """Itens [A] (atividades) e [S] (saúde calculada) das contas citadas na pergunta, ou da carteira."""
        contas = self.repo.contas_relacionamento(usuario_id)
        citadas = [c for c in contas if contas_mod.mesma_conta(c["nome"], pergunta)]
        if not (citadas or FALA_DE_CRM.search(pergunta)):
            return "", {}
        ids = {c["id"] for c in citadas}
        nomes = {c["id"]: c["nome"] for c in contas}
        ativs = [a for a in self.repo.crm(usuario_id, "atividades") if a["situacao"] != "substituida"
                 and (not ids or a["conta_id"] in ids)]
        ativs.sort(key=lambda a: a.get("data") or "", reverse=True)
        codigos, memoria = {}, ""
        for i, a in enumerate(ativs[:25], start=1):
            codigos[f"A{i}"] = a
            memoria += f"\n[A{i}] (atividade) " + " · ".join(
                l.strip() for l in crm.linha_atividade(a, nomes.get(a["conta_id"])).splitlines())
        saudes, _ = self._saudes(usuario_id)
        for i, s in enumerate([s for s in saudes if not ids or s.conta_id in ids][:15], start=1):
            codigos[f"S{i}"] = {"id": f"saude:{s.conta_id}"}
            memoria += f"\n[S{i}] (saúde calculada pelo Peraí) " + " · ".join(
                l.strip() for l in crm.texto_saude(s).splitlines())
        return memoria, codigos


FALA_DE_CRM = re.compile(r"\bfalei\b|falamos|contato|visit|reuni|\bcall\b|liga[cç]|atividade|sa[uú]de da|"
                         r"conta[s]? (?:mais )?(?:esquecid|parad|abandonad)|risco|maturidade|meddpicc|spin|"
                         r"cross|upsell|oferecer|mapa|parceir|segmento|aten[cç][aã]o", re.I)
