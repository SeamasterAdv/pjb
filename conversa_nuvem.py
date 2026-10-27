"""A conversa e a nuvem: perguntas de conhecimento geral e a IA pedida na própria frase ("usa o Claude pra…").

Escolha do usuário (out/2026): explicações e conhecimento geral, sem nada pessoal, vão para o Gemini e depois o Claude
(ORDEM_IA_PESQUISA); papo, sentimentos e o que é da vida da pessoa continuam no PC, onde a voz responde em ~1 s. Só a
pergunta vai para a nuvem: nada da memória, do histórico nem dos documentos. Sem nenhuma IA da nuvem, quem chama responde
pelo Ollama, como antes.

O classificador é o da conversa (antes em `main.classificar_com_ollama`), agora com a categoria "conhecimento", para o
painel usar o mesmo sem importar o `main`. Medido com o qwen2.5:14b: 31 de 31 frases de exemplo certas (14 de papo).
"""
import logging
import re

logger = logging.getLogger(__name__)

INSTRUCOES_DO_CLASSIFICADOR = (
    "Classifique o comando do usuário. Responda com UMA palavra: simples, conhecimento, pesquisa, ferramentas ou sistema.\n"
    "simples = conversa, cumprimento, piada, opinião, sentimentos, desabafo, algo sobre a vida da pessoa ou sobre a "
    "própria assistente.\n"
    "conhecimento = explicar um assunto, conceito, definição, como algo funciona ou conhecimento geral que não muda "
    "(ciência, história, geografia, direito, matemática, idiomas, saúde em geral).\n"
    "pesquisa = fatos atuais: notícias, preços, cotações, resultados de jogos, clima, pessoas ou dados que mudam.\n"
    "ferramentas = abrir, enviar, criar, agendar ou controlar algo.\n"
    "sistema = configuração ou estado da própria assistente.\n"
    "Exemplos: 'me conta uma piada' -> simples; 'tô cansado' -> simples; 'qual a capital da França' -> "
    "conhecimento; 'me explica o que é usucapião' -> conhecimento; 'quanto tá o dólar' -> pesquisa; "
    "'abre o youtube' -> ferramentas."
)

# Os modelos pequenos às vezes respondem com a descrição ("conversa", "conhecimento geral") em vez do
# rótulo; sem isso, papo e piada caíam na rota da biblioteca e saíam com cara de relatório.
SINONIMOS_CATEGORIA = (
    ("simples", ("simples", "conversa", "cumpriment", "piada", "opini", "sentiment")),
    ("conhecimento", ("conhecimento", "explica", "conceito", "definic")),
    ("pesquisa", ("pesquisa", "fatos atuais", "noticia", "preco", "cotac", "resultado", "clima")),
    ("ferramentas", ("ferramenta", "abrir", "enviar", "criar", "agendar", "controlar")),
    ("sistema", ("sistema", "configurac")),
)


def _sem_acentos(texto: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii").lower()


def categoria_da_resposta(resposta: str) -> str | None:
    texto = _sem_acentos(resposta)
    for categoria, _ in SINONIMOS_CATEGORIA:  # o rótulo certo primeiro
        if categoria in texto:
            return categoria
    for categoria, pistas in SINONIMOS_CATEGORIA:
        if any(pista in texto for pista in pistas):
            return categoria
    return None


def classificar(comando: str, llm) -> str | None:
    """simples, conhecimento, pesquisa, ferramentas, sistema ou None (o Ollama fora do ar ou uma resposta sem sentido)."""
    if not llm.is_ollama_available():
        return None
    try:
        resposta = llm.chat(f"Comando: {comando}\nCategoria:", system_prompt=INSTRUCOES_DO_CLASSIFICADOR,
                            opcoes={"temperature": 0, "num_predict": 5})
    except Exception as exc:
        logger.warning("Falha ao classificar com Ollama: %s", exc)
        return None
    return categoria_da_resposta(resposta)


# Pergunta com algo da vida da pessoa ("minha prova", "eu tenho"): fica no PC, mesmo sendo uma explicação.
RE_PESSOAL = re.compile(
    r"\b(meu|minha|meus|minhas|eu (?:tenho|tinha|estou|to|fui|vou|preciso|fiz|sou)|to com|tou com)\b"
)


def pode_ir_para_a_nuvem(pergunta: str) -> bool:
    return not RE_PESSOAL.search(_sem_acentos(pergunta))


FORMATO_FALADO = ("Responda em português do Brasil, em 2 a 4 frases, como numa conversa falada: direto ao ponto, com um "
                  "exemplo curto se ajudar. Sem markdown, listas, títulos ou emojis.")
FORMATO_ESCRITO = ("Responda em português do Brasil, direto ao ponto, em até três parágrafos curtos (uma lista curta só se "
                   "ajudar). Sem títulos nem emojis.")
CONTEXTO = ("A pergunta veio da conversa com o operador. Responda pelo seu conhecimento consolidado; se a resposta depender "
            "de dados atuais que você não tem, diga isso numa frase, sem inventar.")


def responder(pergunta: str, preferir: str = None, falada: bool = True, nuvem=None):
    """A resposta da primeira IA da nuvem que conseguir (a pedida primeiro), ou None se nenhuma puder.

    `nuvem(prompt, contexto, **opcoes)` troca o `llm_nuvem.responder_na_nuvem` (testes). Se a IA pedida não respondeu,
    a resposta começa dizendo quem respondeu no lugar.
    """
    from llm_nuvem import NOMES_IA, responder_na_nuvem

    nuvem = nuvem or responder_na_nuvem
    prompt = f"Pergunta: {pergunta.strip()}\n\n{FORMATO_FALADO if falada else FORMATO_ESCRITO}"
    quem = []
    try:
        texto = nuvem(prompt, CONTEXTO, opcoes={"temperature": 0.4}, preferir=preferir, registro=quem, funcao="pesquisa")
    except Exception as exc:  # a nuvem nunca derruba a conversa: quem chama responde pelo PC
        logger.warning("A nuvem falhou na conversa: %s", exc)
        return None
    if not texto:
        return None
    if preferir in NOMES_IA and NOMES_IA[preferir] not in quem:
        texto = f"O {NOMES_IA[preferir]} não pôde responder agora; respondi pelo {quem[0] if quem else 'outro'}. {texto}"
    logger.info("Conversa respondida na nuvem por %s.", ", ".join(quem) or "?")
    return texto.strip()
