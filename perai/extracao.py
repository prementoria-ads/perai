"""Extração: de uma mensagem para entidades, registros e pendências.

As regras do prompt vêm das decisões aprovadas: nada sem fonte (D-040),
natureza explícita, quem disse e opinião separada de fato (D-044), quatro
contextos (D-045), dados sensíveis marcados e lembrete só com aceite (D-007).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from perai import recorrencia

NATUREZAS = {"confirmado", "intencao", "hipotese", "opiniao"}
CONTEXTOS = {"familia", "profissional", "projetos_proprios", "igreja"}
TIPOS_ENTIDADE = {"pessoa", "organizacao", "projeto", "evento", "local", "assunto"}

SISTEMA_EXTRACAO = """Você é o módulo de memória do Peraí, um assistente pessoal brasileiro.
Recebe uma mensagem do usuário (texto, transcrição de áudio ou texto lido de um print ou foto)
e devolve SOMENTE um objeto JSON com o que ela contém.

Regras, em ordem de importância:
1. Nunca invente. Registre só o que está na mensagem. Não complete nomes, sobrenomes,
   empresas, valores ou datas que não foram ditos. Não expanda siglas sem fonte.
2. Natureza de cada registro: "confirmado" (fato), "intencao" (algo que alguém pretende ou
   combinou fazer), "hipotese" (ideia, possibilidade, "talvez", "acredito que") ou
   "opiniao" (julgamento de alguém sobre algo ou alguém). Opinião nunca vira fato.
3. Quem disse: "eu" quando é o próprio usuário; senão, o nome de quem disse, como aparece.
   Em prints de conversa, cada fala tem um autor ("Eu" = balões do dono do celular); uma
   sugestão do usuário é registro dele, uma de outra pessoa é dela. Trecho cortado ("[...]",
   "...") ou transcrição de áudio pela metade: confiança baixa e anote a dúvida, sem completar.
4. Pendência: algo a fazer. "responsavel" é "eu" quando o usuário fará, ou "aguardando"
   quando ele espera outra pessoa (preencha "quem"). Promessa de outra pessoa ("já te ligo",
   "te mando amanhã", "vou ver e te falo") é pendência "aguardando" daquela pessoa. Pedido de outra pessoa que o usuário
   não assumiu não é pendência dele: registre como fato. Quando a mensagem conta um fato e uma espera ("mandei lavar
   o Cobalt branco, me avisam quando ficar pronto"), registre os dois: o fato e a pendência.
5. Datas: converta datas relativas ("sexta", "amanhã") usando a data da situação. Guarde
   também como foi dito. Prazo vago ("antes de março") fica vago: "prazo" null.
6. Contexto de cada entidade: "familia", "profissional" (trabalho na L8 Group),
   "projetos_proprios" (negócios pessoais do usuário fora da L8) ou "igreja". Se não der
   para saber, use null.
7. "sensivel": true para saúde e finanças pessoais.
8. Mensagem ambígua ("falar com ele sobre aquilo"): registre com confiança baixa e anote
   a dúvida em "duvidas". Nunca adivinhe quem é "ele".
9. Correção ("não foi o Wagner, foi o Marcos"): registre o fato corrigido e preencha
   "corrige" com o que está sendo corrigido.
10. Fotos: identifique pessoas só pela legenda do usuário, nunca pela aparência.
11. Lembrete: só quando o usuário pede explicitamente ("me lembra sexta às 9h de...").
    Preencha "lembrete_em" com data e hora; sem hora dita, use 09:00. Sem pedido, null.
12. "Conversa recente" e "Pendências abertas", quando vierem, são só contexto para entender
    referências ("muda para as 11h", "isso", "já fiz", "pode cancelar"). Não registre de novo
    nada que já esteja nelas e nunca crie pendência ou registro a partir delas.
13. Mudança numa pendência que já existe (outro horário ou dia do lembrete, novo prazo,
    "já fiz", "pode cancelar"): use "ajustes" com o código da pendência ([P1], [P2]...) e não
    crie pendência nova para a mesma coisa. Se não estiver claro qual pendência é, não ajuste:
    anote a dúvida. Quando a mensagem só pede a mudança, não crie registro sobre o pedido
    ("desejo de mudar o horário..."): a mudança já fica guardada no histórico. Ação que encerra
    uma espera ("vou buscar o carro" com pendência aguardando o carro ficar pronto; "o Jaime
    mandou a autorização") conclui essa pendência. Relato de que a própria tarefa foi feita
    ("fui a Campo Mourão", "estou em Campo Mourão na reunião", "mandei o relatório") conclui a
    pendência aberta dessa tarefa, além de virar registro. Resposta curta ("sim", "isso", "pode") a uma
    dúvida do Peraí na conversa recente confirma o que a dúvida propunha: aplique. "Respondendo à
    mensagem" mostra a mensagem a que o usuário respondeu no Telegram: use para saber do que ele fala.
14. Escreva "conteudo" e "descricao" como o fato ou a tarefa em si ("Levar o lixo amanhã às 9h"),
    nunca como relato do pedido ("O usuário pediu para..."). Nunca escreva "o usuário" nem "do
    usuário": refira-se ao dono da memória como "você" ("reunião com você"), nunca pelo nome dele ("enviar a
    você", não "enviar a Nelson"). Se a mensagem completa a anterior
    da conversa recente ("sobre os projetos de CyberArk"), escreva um registro completo e
    autossuficiente ("Falar com o Walter Maria, da TCP, sobre os projetos de CyberArk"), nunca
    "a conversa sobre X também é sobre Y".
15. "quem" só com um nome dito na mensagem ou na conversa recente; senão null. Não use nomes
    da lista de entidades já registradas para completar o que não foi dito.
16. Compromisso de agenda: só quando o próprio usuário terá um compromisso com DIA e HORA ditos
    ("reunião no Technopark quinta às 14h", "consulta dia 5 às 9h30"). Sem hora dita, não é
    compromisso: vira registro ou pendência. Faixa de horário aceita pelo outro ("sexta entre
    5 e 7" — "perfeito") é compromisso do início ao fim da faixa (17:00 a 19:00), não até o
    limite que o usuário disse aguentar ("até as 7 e meia eu consigo"). Proposta sem resposta
    não é compromisso: vira pendência "aguardando" de quem vai confirmar. Título curto, sem
    nomes que não foram ditos; "fim" só se a duração ou o horário final foi dito; "local" só se
    dito ("call" ou "videochamada" não é local). Pedido de lembrete
    ("me lembra...") não é compromisso. Nunca liste convidados.
17. "Eventos que o Peraí criou", quando vierem, aceitam ajustes com o código [E1], [E2]...:
    "inicio"/"fim" novos ("muda a reunião para as 15h"), "titulo" novo ("o nome certo é CRTech":
    troque só o que mudou no título) ou "situacao": "cancelado" ("a reunião foi cancelada").
    Não crie compromisso novo para o mesmo evento. Compromisso criado agora com uma pessoa ou
    assunto que tem pendência aberta de "agendar"/"marcar" o mesmo encontro: ajuste essa
    pendência para "concluida".
18. Repetição: só quando dita ("todo dia 1", "toda segunda", "todo dia", "todo ano em 15/03").
    "recorrencia": "mensal:1" (dia do mês), "semanal:0" (0 = segunda ... 6 = domingo),
    "diaria" ou "anual:03-15". "prazo" é a próxima vez. Lembrete continua só se pedido; se pedido
    com repetição, "lembrete_em" é a próxima vez, na hora dita.
19. Despesa: só gasto que o usuário já fez ou pagou ("gastei 180 de combustível", cupom, recibo,
    nota). Preço citado, orçamento ou "vou gastar" não é despesa. "valor" exatamente como
    aparece ("182,40"); sem valor dito ou legível, null (nunca 0). "data" da compra, ou a data da
    situação se for de hoje. "categoria": alimentacao, combustivel, transporte, hospedagem,
    saude, mercado, casa, educacao, lazer, servicos, impostos ou outros. Nunca registre CPF.
    Cupom, nota ou recibo com itens legíveis: "itens" com cada item e o valor total da linha,
    como impressos ("GASOLINA COMUM", "182,40"); "valor" continua sendo o total da nota.
20. "Despesas recentes", quando vierem, aceitam ajustes com o código [G1], [G2]...: novo "valor",
    "categoria", "data", "contexto" ou "situacao": "cancelada" ("o valor era 192", "essa foi pessoal",
    "lancei errado"). Não crie despesa nova para a mesma compra. "Foi com a Gazin e a Romagnole"
    sobre uma despesa recente: ajuste com "clientes": ["Gazin", "Romagnole"].
21. Despesa ligada a cliente ou empresa citada ("almoço com o pessoal da Gazin"): "clientes" com
    os nomes ditos, como aparecem; sem nome dito, lista vazia.
22. Viagem: quando o usuário fala de uma viagem com período ("semana que vem vou a Foz, Maringá
    e Londrina com o Júlio, de 5 a 7", "a viagem de 5 a 7/10 foi prospecção comercial"): "viagens"
    com "inicio" e "fim" (AAAA-MM-DD), "cidades", "acompanhantes" e "objetivo" como ditos, e
    "nome" só se o usuário deu um nome. Sem período dito, não é viagem. Ida a outra cidade num dia
    dito ("hoje vou a Campo Mourão", "amanhã estou em Maringá") é viagem de um dia: "inicio" e "fim"
    iguais; nesse caso não crie também intenção ou pendência só para dizer que vai.
23. Cancelar evento da agenda que não está em "Eventos que o Peraí criou" ("cancela todos os eventos
    do Rotary Umuarama", "tira da agenda a reunião de amanhã com o Akira", "apaga a academia de
    sexta"): "cancelar_eventos" com "busca" (as palavras do título como ditas: "Rotary Umuarama",
    "Akira", "academia"), e "desde"/"ate" (AAAA-MM-DD) quando um dia ou período foi dito (um dia
    só: os dois iguais); "todos"/"todas as próximas" sem período: os dois null. Só quando o usuário
    pede para cancelar, apagar ou tirar da agenda. Nesse caso não crie registro, intenção nem
    pendência sobre o pedido: o cancelamento já fica guardado.
24. Mudar horário, dia ou nome de evento da agenda que não está em "Eventos que o Peraí criou"
    ("mude o inglês para 7h", "passa a academia de amanhã para as 18h", "o Rotary de quinta
    agora é às 20h"): "mudar_eventos" com "busca" (palavras do título como ditas), "dia"
    (AAAA-MM-DD, só se o dia do evento foi dito; senão null = a próxima vez), "novo_dia"
    (AAAA-MM-DD ou null), "nova_hora" ("HH:MM" ou null), "novo_titulo" (ou null) e "todas":
    true só se o usuário disse que é para todas as vezes ("todo dia", "a partir de agora").
    Não crie registro, intenção nem pendência sobre o pedido.
25. Conversa longa (transcrição de reunião, conversa exportada): inclua também o que o próprio
    usuário disse que vai fazer ("vou montar uma apresentação rápida", "vou pedir um usuário de
    POC") como pendência "eu". O dono da conversa é "eu"; nunca escreva "o usuário". Datas sem
    mês dito ("de 5 a 7") ficam vagas: não ligue a viagens, eventos ou datas já registrados.
    Transcrição automática erra nomes de lugar e de empresa: na dúvida, confiança baixa.
26. Texto colado que é análise, relatório ou resumo feito por outra pessoa ou ferramenta (ex.: "Minha
    avaliação é...", notas de 1 a 5, "recomendo", plano de ação sugerido): avaliações, notas e
    recomendações são "opiniao" com "dito_por" igual ao autor dito (ou "análise colada" se não dito),
    nunca fato. Só viram "confirmado" os fatos da reunião que o texto relata de forma objetiva (quem
    estava, falas entre aspas, combinados), com confiança 0,7. Recomendações do plano de ação não são
    pendência do usuário, a menos que ele diga que vai fazer; promessas de terceiros citadas viram
    pendência "aguardando". Prefira poucos registros completos a muitos fragmentados.
27. Nomes de pessoas e empresas com a grafia que o usuário escreveu ("crtech" → "CRTech"); não
    troque letras nem "corrija" para outro nome parecido.
28. "duvidas" são perguntas curtas para o usuário, em português simples. Nunca cite códigos
    ([P1], [E1], [G1]...), ids ou termos internos ("lista de eventos gerenciáveis").

29. Contato dito (e-mail, telefone, site de uma pessoa ou empresa): registro "confirmado" com o
    contato e de quem é ("E-mail do Marcos Silva, da Hardware Import: corporativo@..."). Ajuste
    numa pendência só quando a mensagem atual fala dela (o que foi feito, mudado ou cancelado);
    nunca conclua uma pendência nem crie compromisso por causa da conversa recente.
Formato:
{"entidades": [{"nome": str, "tipo": "pessoa|organizacao|projeto|evento|local|assunto",
                "contexto": str|null, "descricao": str|null}],
 "registros": [{"conteudo": str, "natureza": str, "dito_por": str, "data_fato": "AAAA-MM-DD"|null,
                "entidades": [nomes], "confianca": 0.0-1.0, "sensivel": bool, "corrige": str|null}],
 "pendencias": [{"descricao": str, "responsavel": "eu|aguardando", "quem": str|null,
                 "prazo": "AAAA-MM-DD"|null, "prazo_como_dito": str|null,
                 "lembrete_em": "AAAA-MM-DDTHH:MM"|null,
                 "recorrencia": "diaria"|"semanal:0-6"|"mensal:1-31"|"anual:MM-DD"|null}],
 "ajustes": [{"pendencia": "P1", "lembrete_em": "AAAA-MM-DDTHH:MM"|null, "prazo": "AAAA-MM-DD"|null,
              "situacao": "concluida|descartada"|null}],
 "compromissos": [{"titulo": str, "inicio": "AAAA-MM-DDTHH:MM", "fim": "AAAA-MM-DDTHH:MM"|null,
                   "local": str|null}],
 "ajustes_eventos": [{"evento": "E1", "inicio": "AAAA-MM-DDTHH:MM"|null, "fim": "AAAA-MM-DDTHH:MM"|null,
                      "titulo": str|null, "situacao": "cancelado"|null}],
 "mudar_eventos": [{"busca": str, "dia": "AAAA-MM-DD"|null, "novo_dia": "AAAA-MM-DD"|null,
                    "nova_hora": "HH:MM"|null, "novo_titulo": str|null, "todas": bool}],
 "despesas": [{"descricao": str, "valor": str|null, "data": "AAAA-MM-DD"|null, "categoria": str,
               "estabelecimento": str|null, "cnpj": str|null, "forma_pagamento": str|null,
               "contexto": str|null, "local": str|null, "clientes": [str],
               "itens": [{"descricao": str, "valor": str|null}]}],
 "ajustes_despesas": [{"despesa": "G1", "valor": str|null, "categoria": str|null,
                       "data": "AAAA-MM-DD"|null, "contexto": str|null, "clientes": [str],
                       "situacao": "cancelada"|null}],
 "cancelar_eventos": [{"busca": str, "desde": "AAAA-MM-DD"|null, "ate": "AAAA-MM-DD"|null}],
 "viagens": [{"nome": str|null, "inicio": "AAAA-MM-DD", "fim": "AAAA-MM-DD", "cidades": [str],
              "acompanhantes": [str], "objetivo": str|null}],
 "duvidas": [str]}"""


@dataclass
class Entidade:
    nome: str
    tipo: str
    contexto: str | None = None
    descricao: str | None = None


@dataclass
class Registro:
    id: str
    mensagem: str          # Nº da mensagem de origem (fonte obrigatória)
    conteudo: str
    natureza: str
    dito_por: str
    data_fato: str | None
    entidades: list[str]
    confianca: float
    sensivel: bool
    corrige: str | None = None
    anotado_em: str | None = None   # AAAA-MM-DD em que o Peraí guardou (para "resuma o dia")


@dataclass
class Pendencia:
    id: str
    mensagem: str
    descricao: str
    responsavel: str
    quem: str | None
    prazo: str | None
    prazo_como_dito: str | None
    lembrete_em: str | None = None  # só quando o usuário pediu (D-007)
    recorrencia: str | None = None  # "mensal:1", "semanal:0"... (perai/recorrencia.py)


@dataclass
class Ajuste:
    """Mudança pedida numa pendência que já existe (código P1, P2... do pedido)."""
    pendencia: str
    lembrete_em: str | None = None
    prazo: str | None = None
    situacao: str | None = None  # concluida, descartada


@dataclass
class Compromisso:
    """Compromisso com dia e hora ditos: vira evento na agenda (D-051)."""
    titulo: str
    inicio: str                 # AAAA-MM-DDTHH:MM
    fim: str | None = None
    local: str | None = None


@dataclass
class AjusteEvento:
    evento: str                 # E1, E2... do pedido
    inicio: str | None = None
    fim: str | None = None
    cancelar: bool = False
    titulo: str | None = None


@dataclass
class MudarEvento:
    busca: str
    dia: str | None = None          # dia atual do evento, se dito
    novo_dia: str | None = None
    nova_hora: str | None = None    # HH:MM
    novo_titulo: str | None = None
    todas: bool = False


@dataclass
class CancelarEventos:
    busca: str                  # palavras do título, como ditas
    desde: str | None = None    # AAAA-MM-DD
    ate: str | None = None


@dataclass
class Despesa:
    descricao: str
    valor_centavos: int | None          # None = não informado
    data: str | None
    categoria: str
    estabelecimento: str | None = None
    cnpj: str | None = None
    forma_pagamento: str | None = None
    contexto: str | None = None
    local: str | None = None
    clientes: list[str] = field(default_factory=list)
    itens: list[tuple[str, int | None]] = field(default_factory=list)   # da nota, como impressos


@dataclass
class AjusteDespesa:
    despesa: str                        # G1, G2... do pedido
    valor_centavos: int | None = None
    categoria: str | None = None
    data: str | None = None
    contexto: str | None = None
    cancelar: bool = False
    clientes: list[str] = field(default_factory=list)


@dataclass
class Viagem:
    inicio: str
    fim: str
    nome: str | None = None
    cidades: list[str] = field(default_factory=list)
    acompanhantes: list[str] = field(default_factory=list)
    objetivo: str | None = None


@dataclass
class Extracao:
    mensagem: str
    entidades: list[Entidade] = field(default_factory=list)
    registros: list[Registro] = field(default_factory=list)
    pendencias: list[Pendencia] = field(default_factory=list)
    duvidas: list[str] = field(default_factory=list)
    ajustes: list[Ajuste] = field(default_factory=list)
    compromissos: list[Compromisso] = field(default_factory=list)
    ajustes_eventos: list[AjusteEvento] = field(default_factory=list)
    cancelar_eventos: list[CancelarEventos] = field(default_factory=list)
    mudar_eventos: list[MudarEvento] = field(default_factory=list)
    despesas: list[Despesa] = field(default_factory=list)
    ajustes_despesas: list[AjusteDespesa] = field(default_factory=list)
    viagens: list[Viagem] = field(default_factory=list)
    json_valido: bool = True
    problemas: list[str] = field(default_factory=list)


def montar_pedido(numero: str, texto: str, data_situacao: str | None, tipo: str,
                  entidades_conhecidas: list[str], conversa: list[str] | None = None,
                  pendencias_abertas: list[str] | None = None,
                  eventos_criados: list[str] | None = None,
                  despesas_recentes: list[str] | None = None, dono: str | None = None) -> str:
    partes = [f"Mensagem nº {numero} ({tipo})."]
    if dono:
        partes.append(f"Dono desta memória: {dono}. O que ele diz, escreve ou fala em áudio é dito por \"eu\" "
                      "(inclusive em legendas como \"Áudio de " + dono + "\").")
    partes.append(f"Data da situação: {data_situacao or 'não informada'}.")
    if entidades_conhecidas:
        partes.append(
            "Entidades já registradas (reuse o mesmo nome se for a mesma): "
            + "; ".join(sorted(set(entidades_conhecidas))[:200])
        )
    if pendencias_abertas:
        partes.append("Pendências abertas (só para ajustes):\n" + "\n".join(pendencias_abertas))
    if eventos_criados:
        partes.append("Eventos que o Peraí criou na agenda (só para ajustes):\n" + "\n".join(eventos_criados))
    if despesas_recentes:
        partes.append("Despesas recentes (só para ajustes):\n" + "\n".join(despesas_recentes))
    if conversa:
        partes.append("Conversa recente (só contexto, não registre de novo):\n" + "\n".join(conversa))
    partes.append("Conteúdo:\n" + texto.strip())
    return "\n".join(partes)


def extrair_json(texto: str) -> dict:
    """Aceita JSON puro, cercado por ``` ou com texto antes e depois."""
    limpo = re.sub(r"^```(?:json)?|```$", "", texto.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(limpo)
    except json.JSONDecodeError:
        inicio, fim = limpo.find("{"), limpo.rfind("}")
        if inicio == -1 or fim <= inicio:
            raise
        return json.loads(limpo[inicio : fim + 1])


def _texto(valor) -> str | None:
    if valor is None:
        return None
    valor = str(valor).strip()
    return valor or None


def _data(valor) -> str | None:
    """Só aceita AAAA-MM-DD; qualquer outra coisa vira None em vez de data inventada."""
    valor = _texto(valor)
    return valor if valor and re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor) else None


def _data_hora(valor) -> str | None:
    valor = _texto(valor)
    if not valor:
        return None
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?(?::\d{2})?", valor)
    if not m:
        return None
    return f"{m.group(1)}T{m.group(2) or '09:00'}"


def _nomes(valor) -> list[str]:
    if not isinstance(valor, list):
        return []
    vistos, saida = set(), []
    for n in valor:
        n = _texto(n)
        if n and n.lower() not in vistos:
            vistos.add(n.lower())
            saida.append(n[:80])
    return saida[:10]


def _cnpj(valor) -> str | None:
    digitos = re.sub(r"\D", "", str(valor or ""))
    return digitos if len(digitos) == 14 else None  # CPF (11 dígitos) nunca passa


def _data_hora_exata(valor) -> str | None:
    """Como _data_hora, mas exige a hora: um compromisso sem hora não é inventado às 09:00."""
    valor = _texto(valor)
    if not valor:
        return None
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::\d{2})?", valor)
    return f"{m.group(1)}T{m.group(2)}" if m else None


def interpretar(numero: str, resposta: str) -> Extracao:
    resultado = Extracao(mensagem=numero)
    try:
        dados = extrair_json(resposta)
    except (json.JSONDecodeError, ValueError):
        resultado.json_valido = False
        resultado.problemas.append("resposta não é JSON")
        return resultado
    if not isinstance(dados, dict):
        resultado.json_valido = False
        resultado.problemas.append("JSON não é um objeto")
        return resultado

    for e in dados.get("entidades") or []:
        if not isinstance(e, dict) or not _texto(e.get("nome")):
            resultado.problemas.append("entidade sem nome")
            continue
        tipo = e.get("tipo") if e.get("tipo") in TIPOS_ENTIDADE else "assunto"
        contexto = e.get("contexto") if e.get("contexto") in CONTEXTOS else None
        resultado.entidades.append(Entidade(_texto(e["nome"]), tipo, contexto, _texto(e.get("descricao"))))

    for i, r in enumerate(dados.get("registros") or [], start=1):
        if not isinstance(r, dict) or not _texto(r.get("conteudo")):
            resultado.problemas.append("registro sem conteúdo")
            continue
        natureza = r.get("natureza")
        if natureza not in NATUREZAS:
            resultado.problemas.append(f"natureza inválida: {natureza!r}")
            natureza = "hipotese"  # na dúvida, nunca vira fato
        try:
            confianca = min(max(float(r.get("confianca", 0.5)), 0.0), 1.0)
        except (TypeError, ValueError):
            confianca = 0.5
        resultado.registros.append(
            Registro(
                id=f"R{numero}-{i}",
                mensagem=numero,
                conteudo=_texto(r["conteudo"]),
                natureza=natureza,
                dito_por=_texto(r.get("dito_por")) or "eu",
                data_fato=_data(r.get("data_fato")),
                entidades=[str(n) for n in (r.get("entidades") or []) if _texto(n)],
                confianca=confianca,
                sensivel=bool(r.get("sensivel", False)),
                corrige=_texto(r.get("corrige")),
            )
        )

    for i, p in enumerate(dados.get("pendencias") or [], start=1):
        if not isinstance(p, dict) or not _texto(p.get("descricao")):
            resultado.problemas.append("pendência sem descrição")
            continue
        responsavel = p.get("responsavel") if p.get("responsavel") in ("eu", "aguardando") else "eu"
        resultado.pendencias.append(
            Pendencia(
                id=f"P{numero}-{i}",
                mensagem=numero,
                descricao=_texto(p["descricao"]),
                responsavel=responsavel,
                quem=_texto(p.get("quem")),
                prazo=_data(p.get("prazo")),
                prazo_como_dito=_texto(p.get("prazo_como_dito")),
                lembrete_em=_data_hora(p.get("lembrete_em")),
                recorrencia=recorrencia.valida(p.get("recorrencia")),
            )
        )

    for a in dados.get("ajustes") or []:
        if not isinstance(a, dict):
            continue
        codigo = (_texto(a.get("pendencia")) or "").strip("[] ").upper()
        if not re.fullmatch(r"P\d+", codigo):
            resultado.problemas.append(f"ajuste sem pendência válida: {codigo!r}")
            continue
        situacao = a.get("situacao") if a.get("situacao") in ("concluida", "descartada") else None
        ajuste = Ajuste(codigo, _data_hora(a.get("lembrete_em")), _data(a.get("prazo")), situacao)
        if ajuste.lembrete_em or ajuste.prazo or ajuste.situacao:
            resultado.ajustes.append(ajuste)

    for c in dados.get("compromissos") or []:
        if not isinstance(c, dict) or not _texto(c.get("titulo")):
            continue
        inicio = _data_hora_exata(c.get("inicio"))
        if not inicio:  # sem hora dita não vira evento
            resultado.problemas.append("compromisso sem dia e hora")
            continue
        fim = _data_hora_exata(c.get("fim"))
        resultado.compromissos.append(Compromisso(_texto(c["titulo"]), inicio,
                                                  fim if fim and fim > inicio else None, _texto(c.get("local"))))

    for a in dados.get("ajustes_eventos") or []:
        if not isinstance(a, dict):
            continue
        codigo = (_texto(a.get("evento")) or "").strip("[] ").upper()
        if not re.fullmatch(r"E\d+", codigo):
            continue
        ajuste = AjusteEvento(codigo, _data_hora_exata(a.get("inicio")), _data_hora_exata(a.get("fim")),
                              a.get("situacao") == "cancelado", _texto(a.get("titulo")))
        if ajuste.inicio or ajuste.fim or ajuste.cancelar or ajuste.titulo:
            resultado.ajustes_eventos.append(ajuste)

    for c in dados.get("cancelar_eventos") or []:
        busca = _texto(c.get("busca")) if isinstance(c, dict) else None
        if not busca or len(busca.strip()) < 3:
            continue
        desde, ate = _data(c.get("desde")), _data(c.get("ate"))
        if ate and not desde:
            desde = ate
        if desde and ate and ate < desde:
            desde, ate = ate, desde
        resultado.cancelar_eventos.append(CancelarEventos(busca.strip(), desde, ate))

    for m in dados.get("mudar_eventos") or []:
        busca = _texto(m.get("busca")) if isinstance(m, dict) else None
        if not busca or len(busca) < 3:
            continue
        hora = _texto(m.get("nova_hora"))
        hora = hora if hora and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", hora) else None
        mudanca = MudarEvento(busca, _data(m.get("dia")), _data(m.get("novo_dia")), hora,
                              _texto(m.get("novo_titulo")), m.get("todas") is True)
        if mudanca.novo_dia or mudanca.nova_hora or mudanca.novo_titulo:
            resultado.mudar_eventos.append(mudanca)

    from perai import despesas as _desp
    for d in dados.get("despesas") or []:
        if not isinstance(d, dict) or not _texto(d.get("descricao")):
            continue
        contexto = d.get("contexto") if d.get("contexto") in CONTEXTOS else None
        resultado.despesas.append(Despesa(
            descricao=_texto(d["descricao"]), valor_centavos=_desp.valor_em_centavos(d.get("valor")),
            data=_data(d.get("data")), categoria=_desp.categoria_valida(d.get("categoria")),
            estabelecimento=_texto(d.get("estabelecimento")),
            cnpj=_cnpj(d.get("cnpj")),
            forma_pagamento=_texto(d.get("forma_pagamento")), contexto=contexto, local=_texto(d.get("local")),
            clientes=_nomes(d.get("clientes")),
            itens=[(_texto(i.get("descricao")), _desp.valor_em_centavos(i.get("valor")))
                   for i in (d.get("itens") or [])[:30] if isinstance(i, dict) and _texto(i.get("descricao"))],
        ))
    for a in dados.get("ajustes_despesas") or []:
        if not isinstance(a, dict):
            continue
        codigo = (_texto(a.get("despesa")) or "").strip("[] ").upper()
        if not re.fullmatch(r"G\d+", codigo):
            continue
        cat = a.get("categoria") if a.get("categoria") in _desp.CATEGORIAS else None
        ctx = a.get("contexto") if a.get("contexto") in CONTEXTOS else None
        ajuste = AjusteDespesa(codigo, _desp.valor_em_centavos(a.get("valor")), cat, _data(a.get("data")),
                               ctx, a.get("situacao") == "cancelada", _nomes(a.get("clientes")))
        if (ajuste.valor_centavos is not None or ajuste.categoria or ajuste.data or ajuste.contexto
                or ajuste.cancelar or ajuste.clientes):
            resultado.ajustes_despesas.append(ajuste)

    for v in dados.get("viagens") or []:
        if not isinstance(v, dict):
            continue
        inicio, fim = _data(v.get("inicio")), _data(v.get("fim"))
        if not inicio:
            continue
        fim = fim if fim and fim >= inicio else inicio
        resultado.viagens.append(Viagem(inicio, fim, _texto(v.get("nome")), _nomes(v.get("cidades")),
                                        _nomes(v.get("acompanhantes")), _texto(v.get("objetivo"))))

    resultado.duvidas = [str(d) for d in (dados.get("duvidas") or []) if _texto(d)]
    return resultado
