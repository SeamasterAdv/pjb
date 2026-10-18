"""Resumo explicativo: a Beatriz pesquisa o assunto e escreve um resumo que ensina.

Fontes, buscadas em paralelo: o artigo da Wikipédia em português (a base para explicar o
conceito), as páginas achadas na busca da web e manchetes recentes do Google Notícias. Uma
IA da nuvem escreve (Claude, Gemini ou a pedida na frase); sem nuvem, o Ollama. Para a nuvem
vão só o tema e conteúdo público da internet.

Em voz ela fala a versão curta; o resumo completo vai para o chat do painel e para um Word
em documentos_gerados.
"""
import logging
import os
import re
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

import requests

from config import PASTA_DOCUMENTOS_GERADOS

logger = logging.getLogger(__name__)

API_WIKIPEDIA = "https://pt.wikipedia.org/w/api.php"
_CABECALHOS = {"User-Agent": "Beatriz/1.0 (assistente pessoal local)"}
LIMITE_WIKIPEDIA = 8000  # começo do artigo: a introdução e as primeiras seções
LIMITE_WEB = 6000

# Rótulos que a IA usa para separar as partes do resumo, e o título de cada parte no texto final.
SECOES = {
    "fala": None, "o que e": "O que é", "contexto": "Contexto", "pontos principais": "Pontos principais",
    "por que importa": "Por que importa", "exemplo": "Exemplo", "novidades": "Novidades",
    "para lembrar": "Para lembrar",
}
RE_ROTULO = re.compile(
    r"^\s*(FALA|O QUE [ÉE]|CONTEXTO|PONTOS PRINCIPAIS|POR QUE IMPORTA|EXEMPLO|NOVIDADES|PARA LEMBRAR)\s*:\s*",
    re.MULTILINE | re.IGNORECASE,
)


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", (texto or "").lower())
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", " ", sem_acento)).strip()


# ------------------------------------------------------------------ o pedido
RE_RESUMO = re.compile(r"\bresum(?:o|os|a|e|ir|indo)\b")
RE_EXPLICAR = re.compile(r"\b(?:explic\w*|expliqu\w*)\b")
# Resumos que têm caminho próprio: e-mails e resumo do dia (agentes), notícias (ferramenta),
# conversa ouvida (main.resumir_o_que_ouvi), material de prova (agente de estudos).
RE_OUTROS_RESUMOS = re.compile(
    r"\b(?:e-?mails?|caixa de entrada|noticias?|manchetes|(?:do|de|pro|para o) (?:meu )?dia|de hoje|"
    r"conversa\w*|ouviu|ouvido|falad[oa]s?|falamos|reuniao|prova)\b"
)
RE_ANTES_DO_TEMA = re.compile(r"\b(?:sobre|a respeito d[eoa]s?|acerca d[eoa]s?|do tema|do assunto)\s+", re.IGNORECASE)
RE_VERBO_RESUMO = re.compile(r"\b(?:resum\w*|explic\w*|expliqu\w*)\s*", re.IGNORECASE)
PALAVRAS_DE_PEDIDO = {"pra", "para", "mim", "me", "um", "uma", "bom", "boa", "breve", "completo", "explicativo",
                      "detalhado", "resumo", "resumido", "e", "de", "do", "da", "dos", "das", "o", "a", "os", "as",
                      "sobre", "por", "favor", "isso", "aqui"}
TEMAS_VAGOS = {"", "isso", "isto", "aquilo", "esse assunto", "este assunto", "o assunto", "disso", "esse tema"}


def pede_resumo_explicativo(texto: str) -> bool:
    """Pedido de resumo (ou de pesquisa explicada) de um assunto, fora os resumos que têm caminho próprio."""
    t = _normalizar(texto)
    if RE_OUTROS_RESUMOS.search(t):
        return False
    return bool(RE_RESUMO.search(t) or (re.search(r"\bpesquis\w*", t) and RE_EXPLICAR.search(t)))


def _sem_palavras_de_pedido(texto: str) -> str:
    palavras = re.sub(r"[?!.;:]+", " ", texto).replace(",", " ").split()
    while palavras and _normalizar(palavras[0]) in PALAVRAS_DE_PEDIDO:
        palavras.pop(0)
    while palavras and _normalizar(palavras[-1]) in PALAVRAS_DE_PEDIDO:
        palavras.pop()
    return " ".join(palavras)


def tema_do_pedido(texto: str) -> str:
    """'Beatriz, me dá um resumo explicativo da fotossíntese' -> 'fotossíntese'."""
    texto = re.sub(r"^\s*(?:beatriz|beatris|biatriz)[\s,]+", "", texto or "", flags=re.IGNORECASE)
    depois = RE_ANTES_DO_TEMA.search(texto) or RE_VERBO_RESUMO.search(texto)
    if depois:
        texto = texto[depois.end():]
    texto = re.sub(r"^(?:pesquis\w*|procur\w*|busc\w*)\s+(?:e\s+)?", "", texto.strip(), flags=re.IGNORECASE)
    return _sem_palavras_de_pedido(texto)


def preparar_pedido_de_resumo(texto: str, historico=None):
    """(tema, IA preferida) quando o pedido é de resumo explicativo; None nos outros casos.

    "Faz um resumo disso" usa o assunto da última pergunta da conversa (`historico`, como
    em llm_clients.historico_recente()).
    """
    if not pede_resumo_explicativo(texto):
        return None
    from llm_nuvem import extrair_ia_pedida

    sem_ia, ia = extrair_ia_pedida(texto)
    tema = tema_do_pedido(sem_ia)
    if _normalizar(tema) in TEMAS_VAGOS:
        tema = historico[-1][0] if historico else ""
    return (tema, ia) if tema else None


# ------------------------------------------------------------------ fontes
def _consultar_wikipedia(**parametros) -> dict:
    resposta = requests.get(API_WIKIPEDIA, params={"format": "json", "utf8": 1, **parametros},
                            headers=_CABECALHOS, timeout=10)
    resposta.raise_for_status()
    return resposta.json()


def _cortar(texto: str, limite: int) -> str:
    """Corta no fim de um parágrafo, para não terminar no meio de uma frase."""
    if len(texto) <= limite:
        return texto
    corte = texto.rfind("\n", 0, limite)
    return texto[:corte if corte > limite // 2 else limite]


def buscar_wikipedia(tema: str):
    """{"titulo", "texto", "url"} do artigo da Wikipédia em português, ou None (páginas de desambiguação ficam de fora)."""
    busca = _consultar_wikipedia(action="query", list="search", srsearch=tema, srlimit=3, srprop="")
    for resultado in busca.get("query", {}).get("search", []):
        titulo = resultado["title"]
        paginas = _consultar_wikipedia(action="query", prop="extracts", explaintext=1, titles=titulo, redirects=1)
        texto = next(iter(paginas.get("query", {}).get("pages", {}).values()), {}).get("extract", "")
        if len(texto) < 300 or "pode referir-se a" in texto[:400].lower():
            continue
        url = "https://pt.wikipedia.org/wiki/" + urllib.parse.quote(titulo.replace(" ", "_"))
        return {"titulo": titulo, "texto": _cortar(texto, LIMITE_WIKIPEDIA), "url": url}
    return None


def reunir_fontes(tema: str) -> dict:
    """Wikipédia, web e manchetes ao mesmo tempo; a fonte que falhar fica vazia."""
    from tools import buscar_manchetes, pesquisar_web_autonoma

    def seguro(funcao, padrao):
        try:
            return funcao(tema)
        except Exception as exc:
            logger.warning("Fonte do resumo indisponível (%s): %s", getattr(funcao, "__name__", funcao), exc)
            return padrao

    with ThreadPoolExecutor(max_workers=3) as executor:
        wikipedia = executor.submit(seguro, buscar_wikipedia, None)
        web = executor.submit(seguro, pesquisar_web_autonoma, "")
        manchetes = executor.submit(seguro, buscar_manchetes, [])
    texto_web = web.result() or ""
    if texto_web.startswith(("Nenhuma fonte", "Não foi possível")):
        texto_web = ""
    return {"wikipedia": wikipedia.result(), "web": texto_web, "manchetes": manchetes.result() or []}


# ------------------------------------------------------------------ o resumo
@dataclass
class Resumo:
    tema: str
    fala: str  # versão curta, para ouvir
    texto: str  # resumo completo, para ler no painel
    secoes: dict = field(default_factory=dict)
    fontes: list = field(default_factory=list)
    arquivo: str = ""


def _prompt(tema: str, fontes: dict) -> str:
    partes = []
    if fontes["wikipedia"]:
        partes.append(f"[Wikipédia: {fontes['wikipedia']['titulo']}]\n{fontes['wikipedia']['texto']}")
    if fontes["web"]:
        partes.append(f"[Páginas da web]\n{fontes['web'][:LIMITE_WEB]}")
    if fontes["manchetes"]:
        linhas = "\n".join(f"- {titulo} ({data.replace('Notícia publicada em ', '')})" for titulo, _, data in fontes["manchetes"])
        partes.append(f"[Manchetes recentes do Google Notícias]\n{linhas}")
    return (
        f'Escreva em português do Brasil um resumo explicativo sobre "{tema}", para alguém que quer entender '
        "o assunto. Use as fontes abaixo como base: a Wikipédia para explicar o conceito, a web e as manchetes "
        "para o que é recente. Ignore as fontes que não tratarem do tema. Não invente: se algo importante não "
        "estiver nas fontes, diga isso. Explique os termos técnicos com palavras simples.\n\n"
        "Responda exatamente neste formato, com estes rótulos em maiúsculas, cada um no começo de uma linha:\n"
        "FALA: três ou quatro frases curtas que explicam o essencial, para serem ditas em voz alta.\n"
        "O QUE É: um parágrafo.\n"
        "CONTEXTO: um parágrafo sobre a origem, a história ou como funciona.\n"
        "PONTOS PRINCIPAIS:\n- de três a cinco tópicos, uma frase cada.\n"
        "POR QUE IMPORTA: um parágrafo curto.\n"
        "EXEMPLO: um exemplo concreto.\n"
        "NOVIDADES: o que há de recente segundo as manchetes, ou que as fontes não trazem novidades.\n"
        "PARA LEMBRAR: uma frase.\n"
        "Sem markdown, negrito ou emojis.\n\n"
        "Fontes:\n" + "\n\n".join(partes) + "\n\nResumo:"
    )


def separar_secoes(texto: str) -> dict:
    """{"fala": ..., "o que e": ..., ...} a partir dos rótulos da resposta da IA."""
    partes = RE_ROTULO.split(texto or "")
    secoes = {}
    for rotulo, conteudo in zip(partes[1::2], partes[2::2]):
        chave = _normalizar(rotulo)
        if chave in SECOES and conteudo.strip():
            secoes[chave] = conteudo.strip()
    return secoes


def _topicos(conteudo: str) -> list[str]:
    return [re.sub(r"^\s*[-•*\d.)]+\s*", "", linha).strip() for linha in conteudo.splitlines() if linha.strip()]


def _fontes_usadas(fontes: dict) -> list[str]:
    lista = []
    if fontes["wikipedia"]:
        lista.append(f"Wikipédia, {fontes['wikipedia']['titulo']}: {fontes['wikipedia']['url']}")
    lista += [url for url in re.findall(r"^URL: (\S+)", fontes["web"], re.MULTILINE)]
    if fontes["manchetes"]:
        lista.append("Manchetes do Google Notícias")
    return lista


def _texto_do_painel(tema: str, secoes: dict, fontes: list[str]) -> str:
    blocos = [f"Resumo: {tema}"]
    for chave, titulo in SECOES.items():
        if titulo and secoes.get(chave):
            conteudo = secoes[chave]
            if chave == "pontos principais":
                conteudo = "\n".join(f"• {topico}" for topico in _topicos(conteudo))
            blocos.append(f"{titulo}\n{conteudo}")
    if fontes:
        blocos.append("Fontes\n" + "\n".join(fontes))
    return "\n\n".join(blocos)


def salvar_word(resumo: Resumo) -> str:
    """Salva o resumo num Word em documentos_gerados; devolve o caminho ("" se não der)."""
    from agentes.geradores import gerar_docx, nome_arquivo_seguro

    pasta = os.path.join(PASTA_DOCUMENTOS_GERADOS,
                         f"{datetime.now():%Y-%m-%d_%H%M%S}_{nome_arquivo_seguro(resumo.tema, 40)}")
    secoes = []
    for chave, titulo in SECOES.items():
        if titulo and resumo.secoes.get(chave):
            if chave == "pontos principais":
                secoes.append({"titulo": titulo, "topicos": _topicos(resumo.secoes[chave])})
            else:
                secoes.append({"titulo": titulo, "paragrafos": [resumo.secoes[chave]]})
    conteudo = {"titulo": f"Resumo: {resumo.tema}", "subtitulo": "Resumo explicativo",
                "resumo": resumo.secoes.get("fala", ""), "secoes": secoes, "fontes": resumo.fontes}
    try:
        os.makedirs(pasta, exist_ok=True)
        return gerar_docx(conteudo, os.path.join(pasta, f"resumo_{nome_arquivo_seguro(resumo.tema)}.docx"))
    except Exception as exc:
        logger.warning("Não foi possível salvar o resumo em Word: %s", exc)
        return ""


def gerar_resumo(tema: str, preferir: str = None) -> Resumo:
    """Pesquisa o tema e escreve o resumo explicativo (curto para falar, completo para ler e no Word)."""
    from llm_clients import llm_clients
    from llm_nuvem import responder_na_nuvem

    fontes = reunir_fontes(tema)
    if not (fontes["wikipedia"] or fontes["web"] or fontes["manchetes"]):
        aviso = f"Não encontrei fontes sobre {tema} agora. Pode ser a internet; tente de novo em instantes."
        return Resumo(tema=tema, fala=aviso, texto=aviso)

    prompt = _prompt(tema, fontes)
    contexto = "Resumo explicativo feito com fontes da internet, no tom natural da Beatriz."
    texto = responder_na_nuvem(prompt, contexto, opcoes={"temperature": 0.3}, preferir=preferir)
    if not texto:
        # O Ollama corta o que passa do contexto padrão; as fontes pedem um contexto maior.
        texto = llm_clients.chat_beatriz(prompt, contexto=contexto, usar_historico=False,
                                         opcoes={"temperature": 0.3, "num_ctx": 8192})

    secoes = separar_secoes(texto)
    if not secoes:  # a IA não seguiu o formato: o texto inteiro vira o resumo
        secoes = {"o que e": texto.strip()}
    fala = secoes.get("fala") or " ".join(re.split(r"(?<=[.!?])\s+", texto.strip())[:3])
    lista_fontes = _fontes_usadas(fontes)
    resumo = Resumo(tema=tema, fala=fala, texto=_texto_do_painel(tema, secoes, lista_fontes),
                    secoes=secoes, fontes=lista_fontes)
    resumo.arquivo = salvar_word(resumo)
    resumo.fala += (" O resumo completo está no painel e num Word, na pasta documentos gerados."
                    if resumo.arquivo else " O resumo completo está no painel.")
    return resumo
