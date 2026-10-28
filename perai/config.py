"""Modelos e preços (D-038).

Preços em US$ por milhão de tokens, consultados em 26/10/2026 em fontes de
terceiros (ver "Arquitetura e Prova de Conceito" na Base Oficial). Confirme nas
páginas oficiais antes de confiar no custo calculado: eles só servem para
comparar os modelos entre si.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Modelo:
    apelido: str          # nome curto usado na linha de comando e no relatório
    fornecedor: str       # anthropic, openai, groq, google, simulado
    id_api: str           # identificador do modelo na API do fornecedor
    entrada_usd_m: float  # US$ por milhão de tokens de entrada
    saida_usd_m: float    # US$ por milhão de tokens de saída
    le_imagem: bool = False


@dataclass(frozen=True)
class Transcritor:
    apelido: str
    fornecedor: str
    id_api: str
    usd_minuto: float


MODELOS: dict[str, Modelo] = {
    m.apelido: m
    for m in [
        Modelo("haiku", "anthropic", "claude-haiku-5-5", 0.10, 0.50, le_imagem=True),
        Modelo("sonnet", "anthropic", "claude-sonnet-5-5", 2.00, 10.00, le_imagem=True),
        Modelo("gpt-oss", "groq", "openai/gpt-oss-120b", 0.15, 0.60),
        Modelo("gemini-flash", "google", "gemini-3.8-flash", 0.75, 3.75, le_imagem=True),
        # Modelo falso, sem rede, para testar o próprio programa.
        Modelo("simulado", "simulado", "simulado", 0.0, 0.0, le_imagem=True),
    ]
}

TRANSCRITORES: dict[str, Transcritor] = {
    t.apelido: t
    for t in [
        Transcritor("gpt-mini-transcribe", "openai", "gpt-4o-mini-transcribe", 0.003),
        Transcritor("whisper-turbo", "groq", "whisper-large-v3-turbo", 0.04 / 60),
        Transcritor("simulado", "simulado", "simulado", 0.0),
    ]
}

# Os quatro modelos comparados na D-038.
MODELOS_PADRAO = ["haiku", "gpt-oss", "gemini-flash", "sonnet"]
TRANSCRITOR_PADRAO = "gpt-mini-transcribe"
# Quem lê prints e fotos na preparação. Fixo, para que todos os modelos
# comparados recebam o mesmo texto.
LEITOR_IMAGEM_PADRAO = "gemini-flash"

def modelo_por_nome(nome: str) -> Modelo:
    """Apelido conhecido ("gemini-flash") ou "fornecedor:id" ("google:gemini-2.5-flash").

    A segunda forma permite trocar de modelo só pela configuração (D-002), sem
    mudar código; o custo fica zerado até o modelo ganhar um apelido com preço.
    """
    if nome in MODELOS:
        return MODELOS[nome]
    fornecedor, _, id_api = nome.partition(":")
    if fornecedor not in ("anthropic", "openai", "groq", "google") or not id_api:
        raise ValueError(f"Modelo desconhecido: {nome!r}. Use um apelido ou fornecedor:id.")
    return Modelo(nome, fornecedor, id_api, 0.0, 0.0, le_imagem=True)


VARIAVEIS_CHAVE = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "google": "GOOGLE_API_KEY",
}


def custo_usd(modelo: Modelo, tokens_entrada: int, tokens_saida: int) -> float:
    entrada = modelo.entrada_usd_m
    saida = modelo.saida_usd_m
    # Haiku 5.5: acima de 100 mil tokens de entrada, o pedido inteiro custa 5x.
    if modelo.id_api == "claude-haiku-5-5" and tokens_entrada > 100_000:
        entrada, saida = entrada * 5, saida * 5
    return (tokens_entrada * entrada + tokens_saida * saida) / 1_000_000
