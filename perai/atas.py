"""Atas de conversas com clientes e parceiros (D-078).

Da transcrição ou do relato de uma reunião saem, sempre só com o que está no texto (D-040, D-044):
- a ata padrão, mostrada no Telegram;
- o resumo no formato do CRM de Contas, para copiar (D-048: o Peraí não grava no CRM);
- no Google Drive, a ata como Google Doc e o documento mestre da conta, refeito pelo código a
  partir de todas as atas da conta e das pendências abertas que falam dela.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import date

from perai.extracao import extrair_json

SISTEMA_ATA = """Você monta a ata de uma conversa de trabalho do usuário com um cliente ou parceiro,
a partir da transcrição ou do relato que ele mandou. Devolva só um objeto JSON.

Regras:
1. Use SOMENTE o que está no texto. Nada de conhecimento geral, suposição ou complemento.
   O que não aparece fica de fora (lista vazia ou null), nunca "não informado".
2. "conta": o nome da empresa, organização ou pessoa do OUTRO lado da conversa (o cliente ou
   parceiro), como aparece no texto. Nunca o próprio usuário nem a empresa em nome da qual ele fala.
   Sem conta clara, null.
3. "tipo": "cliente", "parceiro" ou "outro", pelo que o texto mostra; na dúvida, "cliente".
4. Opinião, avaliação ou promessa de alguém vai com o autor ("autor": nome como aparece).
   Combinado só quando alguém assumiu fazer algo; intenção vaga vai em próximos passos.
5. Datas em AAAA-MM-DD quando o texto der o dia; senão, como foi dito ("até sexta").
6. Itens curtos, uma ideia por item, em português do Brasil, sem markdown.
7. A ata é documento de trabalho que vai para o Drive e para o CRM: deixe de fora a parte pessoal
   da conversa e qualquer dado sensível de qualquer pessoa (religião, saúde, luto, filhos, família,
   vida pessoal), mesmo que apareça no texto. Nada disso entra em assuntos, contexto, opiniões
   nem participantes.

Formato:
{"conta": str|null, "tipo": "cliente"|"parceiro"|"outro", "assunto": str, "data": str|null,
 "participantes": [{"nome": str, "papel": str|null}],
 "contexto": str|null,
 "assuntos": [str], "decisoes": [str],
 "combinados": [{"quem": str, "o_que": str, "prazo": str|null}],
 "proximos_passos": [str], "oportunidades": [str], "riscos": [str],
 "opinioes": [{"autor": str, "texto": str}],
 "crm": {"resultado": str|null, "proximo_passo": str|null, "despesa": str|null}}"""

SISTEMA_RETORNO = """Você escreve, em nome do usuário, a mensagem de retorno depois de uma reunião com
um cliente ou parceiro, a partir da ata em JSON. Devolva só um objeto JSON:
{"assunto": str, "corpo": str}

Regras:
1. Use SOMENTE o que está na ata: decisões, combinados (quem faz o quê e até quando) e próximos
   passos. Nada de conhecimento geral, promessa nova, preço, data ou nome que não esteja nela.
2. Escreva na primeira pessoa do usuário, cordial e direto, em português do Brasil, como e-mail
   curto: saudação aos participantes do outro lado pelo primeiro nome, uma frase de agradecimento,
   os combinados em lista ("- "), cada um com o responsável e o prazo quando houver, e o próximo passo.
3. Não inclua opiniões, riscos internos, oportunidades comerciais nem a parte pessoal da conversa.
4. Sem assinatura (o usuário assina), sem markdown além dos "- " da lista, até 1.200 caracteres."""

TIPOS = ("cliente", "parceiro", "outro")
PALAVRAS_REUNIAO = re.compile(r"reuni|transcri|\bcall\b|conversa|visita|encontro|alinhamento|apresenta",
                              re.I)
MINIMO_REUNIAO = 800      # caracteres: abaixo disso, não parece uma reunião inteira
MAXIMO_TEXTO = 120_000    # o que vai ao modelo (cerca de 2 horas de reunião transcrita)


def chave(nome: str) -> str:
    t = unicodedata.normalize("NFKD", nome or "").lower()
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def parece_reuniao(tipo: str, texto: str, entidades) -> bool:
    """Vale oferecer a ata? Texto longo, com gente ou empresa, e com cara de reunião."""
    if len(texto or "") < MINIMO_REUNIAO:
        return False
    if not any(getattr(e, "tipo", "") in ("pessoa", "organizacao") for e in entidades or []):
        return False
    return tipo in ("audio", "documento", "conversa_exportada") or bool(PALAVRAS_REUNIAO.search(texto))


def _t(v) -> str | None:
    return str(v).strip() if isinstance(v, (str, int, float)) and str(v).strip() else None


def _lista(v) -> list[str]:
    return [s for s in (_t(x) for x in (v or []) if not isinstance(x, (dict, list))) if s]


def interpretar(resposta: str) -> dict | None:
    """JSON do modelo -> ata normalizada; None se não veio um objeto."""
    try:
        d = extrair_json(resposta)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict):
        return None
    crm = d.get("crm") if isinstance(d.get("crm"), dict) else {}
    data = _t(d.get("data"))
    return {
        "conta": _t(d.get("conta")),
        "tipo": d.get("tipo") if d.get("tipo") in TIPOS else "cliente",
        "assunto": _t(d.get("assunto")) or "Reunião",
        "data": data if data and re.fullmatch(r"\d{4}-\d{2}-\d{2}", data) else None,
        "participantes": [{"nome": _t(p.get("nome")), "papel": _t(p.get("papel"))}
                          for p in (d.get("participantes") or []) if isinstance(p, dict) and _t(p.get("nome"))],
        "contexto": _t(d.get("contexto")),
        "assuntos": _lista(d.get("assuntos")),
        "decisoes": _lista(d.get("decisoes")),
        "combinados": [{"quem": _t(c.get("quem")) or "?", "o_que": _t(c.get("o_que")), "prazo": _t(c.get("prazo"))}
                       for c in (d.get("combinados") or []) if isinstance(c, dict) and _t(c.get("o_que"))],
        "proximos_passos": _lista(d.get("proximos_passos")),
        "oportunidades": _lista(d.get("oportunidades")),
        "riscos": _lista(d.get("riscos")),
        "opinioes": [{"autor": _t(o.get("autor")), "texto": _t(o.get("texto"))}
                     for o in (d.get("opinioes") or [])
                     if isinstance(o, dict) and _t(o.get("autor")) and _t(o.get("texto"))],
        "crm": {"resultado": _t(crm.get("resultado")), "proximo_passo": _t(crm.get("proximo_passo")),
                "despesa": _t(crm.get("despesa"))},
    }


def data_da_ata(ata: dict) -> str:
    """Data para mostrar; avisa quando é só o dia em que a conversa chegou ao Peraí."""
    return data_br(ata.get("data")) + (" (data do envio)" if ata.get("data_do_envio") else "")


def data_br(iso: str | None) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y") if iso else "sem data"
    except ValueError:
        return iso or "sem data"


def _combinado(c: dict) -> str:
    return f"{c['quem']}: {c['o_que']}" + (f" (prazo: {c['prazo']})" if c.get("prazo") else "")


def _blocos(ata: dict) -> list[tuple[str, list[str]]]:
    pessoas = [p["nome"] + (f" ({p['papel']})" if p.get("papel") else "") for p in ata["participantes"]]
    return [
        ("Participantes", pessoas),
        ("Contexto", [ata["contexto"]] if ata.get("contexto") else []),
        ("Assuntos", ata["assuntos"]),
        ("Decisões", ata["decisoes"]),
        ("Combinados", [_combinado(c) for c in ata["combinados"]]),
        ("Próximos passos", ata["proximos_passos"]),
        ("Oportunidades", ata["oportunidades"]),
        ("Riscos", ata["riscos"]),
        ("Opiniões", [f"{o['autor']}: {o['texto']}" for o in ata["opinioes"]]),
    ]


ICONES = {"Participantes": "👥", "Contexto": "🧭", "Assuntos": "🗂️", "Decisões": "✅", "Combinados": "🤝",
          "Próximos passos": "➡️", "Oportunidades": "💡", "Riscos": "⚠️", "Opiniões": "💬"}


def texto_telegram(ata: dict, conta: str | None) -> str:
    linhas = [f"📄 Ata · {conta or 'conta não identificada'} · {data_da_ata(ata)}",
              f"Assunto: {ata['assunto']}"]
    for titulo, itens in _blocos(ata):
        if itens:
            linhas += ["", f"{ICONES[titulo]} {titulo}"] + [f"• {i}" for i in itens]
    linhas += ["", "Só com o que está na conversa que você mandou."]
    return "\n".join(linhas)


def texto_crm(ata: dict, conta: str | None, dono: str | None = None) -> str:
    """Resumo no formato do CRM de Contas (D-048), para copiar e colar."""
    meu = chave(dono or "")
    contatos = [p["nome"] + (f" ({p['papel']})" if p.get("papel") else "") for p in ata["participantes"]
                if not meu or chave(p["nome"]).split()[:1] != meu.split()[:1]]
    crm = ata.get("crm") or {}
    proximo = crm.get("proximo_passo") or (ata["proximos_passos"][0] if ata["proximos_passos"] else None)
    campos = [("Conta", conta), ("Data", data_da_ata(ata)), ("Contatos", ", ".join(contatos) or None),
              ("Assunto", ata["assunto"]), ("Resultado", crm.get("resultado")),
              ("Próximo passo", proximo), ("Despesa citada", crm.get("despesa"))]
    corpo = "\n".join(f"{nome}: {valor}" for nome, valor in campos if valor)
    return "📋 Resumo para o CRM de Contas (toque e segure para copiar):\n\n" + corpo


def interpretar_retorno(resposta: str) -> tuple[str, str] | None:
    try:
        d = extrair_json(resposta)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict) or not _t(d.get("corpo")):
        return None
    return (_t(d.get("assunto")) or "Retorno da nossa reunião")[:150], _t(d.get("corpo"))[:2000]


def ata_para_retorno(ata: dict, conta: str | None) -> dict:
    """Só o que o retorno pode usar: nada de opiniões, riscos ou oportunidades (regra 3)."""
    return {"conta": conta, "assunto": ata["assunto"], "data": ata.get("data"),
            "participantes": ata["participantes"], "decisoes": ata["decisoes"],
            "combinados": ata["combinados"], "proximos_passos": ata["proximos_passos"]}


# -- Google Drive (HTML que o Drive converte em Google Doc) -----------------------------------

_ESTILO = ("<style>body{font-family:Arial,sans-serif;font-size:11pt}h1{font-size:18pt}"
           "h2{font-size:13pt;margin-top:14pt}p.nota{color:#666;font-size:9pt}</style>")


def _e(t) -> str:
    return html.escape(str(t or ""))


def html_ata(ata: dict, conta: str | None, fonte: str) -> str:
    partes = [f"<html><head><meta charset='utf-8'>{_ESTILO}</head><body>",
              f"<h1>{_e(ata['assunto'])}</h1>",
              f"<p><b>Conta:</b> {_e(conta or 'não identificada')} ({_e(ata.get('tipo'))})<br>"
              f"<b>Data:</b> {_e(data_da_ata(ata))}</p>"]
    for titulo, itens in _blocos(ata):
        if itens:
            partes.append(f"<h2>{_e(titulo)}</h2><ul>" + "".join(f"<li>{_e(i)}</li>" for i in itens) + "</ul>")
    partes.append(f"<p class='nota'>Gerado pelo Peraí só com o que está na conversa. Fonte: {_e(fonte)}. "
                  "Para corrigir, corrija no Peraí.</p></body></html>")
    return "".join(partes)


def html_mestre(conta: dict, atas: list[dict], pendencias: list[str], gerado_em: str) -> str:
    """Documento mestre da conta: refeito do zero a cada reunião, a partir das atas (mais nova primeiro)."""
    pessoas: dict[str, str] = {}
    for a in atas:
        for p in a["conteudo"]["participantes"]:
            pessoas.setdefault(chave(p["nome"]), p["nome"] + (f" ({p['papel']})" if p.get("papel") else ""))

    def secao(titulo, itens):
        return (f"<h2>{_e(titulo)}</h2><ul>" + "".join(f"<li>{_e(i)}</li>" for i in itens) + "</ul>") if itens else ""

    def de_atas(campo, fmt=lambda x: x):
        return [f"{data_br(a['conteudo'].get('data'))}: {fmt(x)}" for a in atas for x in a["conteudo"][campo]]

    historico = "".join(
        f"<li>{_e(data_br(a['conteudo'].get('data')))}: "
        + (f"<a href='{_e(a['drive_link'])}'>{_e(a['conteudo']['assunto'])}</a>" if a.get("drive_link")
           else _e(a["conteudo"]["assunto"])) + "</li>" for a in atas)
    partes = [f"<html><head><meta charset='utf-8'>{_ESTILO}</head><body>",
              f"<h1>{_e(conta['nome'])}: documento mestre</h1>",
              f"<p><b>Tipo:</b> {_e(conta.get('tipo') or 'cliente')}<br><b>Reuniões registradas:</b> {len(atas)}</p>",
              secao("Pessoas", list(pessoas.values())),
              f"<h2>Histórico de reuniões</h2><ul>{historico}</ul>" if atas else "",
              secao("Pendências abertas no Peraí", pendencias),
              secao("Combinados", de_atas("combinados", _combinado)),
              secao("Decisões", de_atas("decisoes")),
              secao("Próximos passos", de_atas("proximos_passos")),
              secao("Oportunidades", de_atas("oportunidades")),
              secao("Riscos", de_atas("riscos")),
              secao("Opiniões registradas", de_atas("opinioes", lambda o: f"{o['autor']}: {o['texto']}")),
              f"<p class='nota'>Gerado pelo Peraí em {_e(gerado_em)} a partir das atas das reuniões. É refeito a cada "
              "reunião salva: o que for escrito aqui à mão se perde. Para corrigir, corrija no Peraí.</p>",
              "</body></html>"]
    return "".join(partes)
