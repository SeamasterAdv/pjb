"""Filmes em cartaz, sinopses e os melhores cinemas da cidade.

A programação vem do Ingresso.com. Ele não tem uma API pública documentada: estes são os
mesmos endereços que o site usa (api-content.ingresso.com), então podem mudar sem aviso;
nesse caso a Beatriz avisa que não conseguiu consultar. Os "melhores cinemas" são os de
salas especiais (IMAX, 4DX, VIP...), vistas nos formatos das sessões de cada cinema; os
guias da internet não serviram, porque a busca devolvia páginas que nem eram de cinema.
"""
import difflib
import logging
import re
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import requests

from config import CIDADE_PADRAO

logger = logging.getLogger(__name__)

API_INGRESSO = "https://api-content.ingresso.com/v0"
_CABECALHOS = {"User-Agent": "Beatriz/1.0 (assistente pessoal)"}
VALIDADE_CACHE = 30 * 60  # a programação muda pouco ao longo do dia
_CACHE = {}  # (caminho, parâmetros) -> (validade, dados)

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
APELIDOS_CIDADES = {"sp": "sao paulo", "sampa": "sao paulo", "rio": "rio de janeiro", "rj": "rio de janeiro",
                    "bh": "belo horizonte", "poa": "porto alegre", "brasilia df": "brasilia"}
# Palavras do pedido que indicam um gênero ("filmes de terror", "desenho para criança").
APELIDOS_GENEROS = {"desenho": "animacao", "desenhos": "animacao", "crianca": "infantil", "criancas": "infantil",
                    "comedias": "comedia", "romantico": "romance", "romanticos": "romance"}
PALAVRAS_DE_TEMPO = {"hoje", "agora", "amanha", "semana", "essa", "esta", "nessa", "nesta", "neste", "fim", "de"}
FILMES_FALADOS = 8


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", (texto or "").lower())
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", " ", sem_acento)).strip()


def _buscar(caminho: str, validade: float = VALIDADE_CACHE, **parametros):
    chave = (caminho, tuple(sorted(parametros.items())))
    agora = time.monotonic()
    guardado = _CACHE.get(chave)
    if guardado and guardado[0] > agora:
        return guardado[1]
    resposta = requests.get(f"{API_INGRESSO}/{caminho}", params={"partnership": "home", **parametros},
                            headers=_CABECALHOS, timeout=10)
    resposta.raise_for_status()
    dados = resposta.json()
    _CACHE[chave] = (agora + validade, dados)
    return dados


# ------------------------------------------------------------------ o que foi pedido
RE_PERGUNTA = re.compile(r"\b(quais|qual|que|o que|tem|lista\w*|mostr\w*|diz|diga|fala|conta|sabe|estao|esta|tao|ta|indica\w*|recomend\w*)\b")
RE_SINOPSE = re.compile(r"\b(sinopse|do que se trata|sobre o que e|historia d[oa] filme)\b")
RE_MELHOR = re.compile(r"\b(melhor\w*|bons?|boas?|recomend\w*|indic\w*)\b")


def identificar_pedido(texto: str) -> str | None:
    """"filmes", "sinopse" ou "cinemas"; None quando o pedido não é sobre cinema."""
    t = _normalizar(texto)
    if RE_SINOPSE.search(t) and ("filme" in t or "sinopse" in t):
        return "sinopse"
    tem_cinema = re.search(r"\bcinemas?\b", t)
    if tem_cinema and RE_MELHOR.search(t):
        return "cinemas"
    if "em cartaz" in t:
        return "filmes"
    if not RE_PERGUNTA.search(t):
        return None  # "assisti um filme no cinema ontem" é conversa, não pedido
    if re.search(r"\b(passando|tem|estreias?) (no|nos|do|de) cinemas?\b", t):
        return "filmes"
    if re.search(r"\bfilmes?\b", t) and re.search(r"\b(passando|cinemas?|estreia\w*|pre ?venda|lancamento\w*)\b", t):
        return "filmes"
    if tem_cinema and re.search(r"\b(quais|que|onde) cinemas?\b|\bcinemas? (em|de|da|do|perto)\b", t):
        return "cinemas"
    return None


def encontrar_cidade(nome: str):
    """Cidade do Ingresso.com pelo nome ("são paulo", "sp", "rio"); None se não existir."""
    alvo = _normalizar(nome)
    alvo = APELIDOS_CIDADES.get(alvo, alvo)
    cidades = {_normalizar(c["name"]): c for estado in _buscar("states") for c in estado.get("cities", [])}
    if alvo in cidades:
        return cidades[alvo]
    parecida = difflib.get_close_matches(alvo, list(cidades), n=1, cutoff=0.85)
    return cidades[parecida[0]] if parecida else None


def _cidade_do_pedido(texto: str):
    """Cidade dita no fim do pedido ("... em Campinas hoje"), conferida na lista do Ingresso.com."""
    palavras = _normalizar(texto).split()
    while palavras and palavras[-1] in PALAVRAS_DE_TEMPO:
        palavras.pop()
    cidades = {_normalizar(c["name"]) for estado in _buscar("states") for c in estado.get("cities", [])}
    for tamanho in range(min(5, len(palavras)), 0, -1):
        candidata = " ".join(palavras[-tamanho:])
        candidata = APELIDOS_CIDADES.get(candidata, candidata)
        if candidata in cidades:
            return encontrar_cidade(candidata)
    return None


# ------------------------------------------------------------------ filmes
def _estreia(filme) -> date | None:
    data = filme.get("premiereDate")
    data = data.get("localDate") if isinstance(data, dict) else data
    try:
        return datetime.fromisoformat(str(data)[:10]).date()
    except ValueError:
        return None


def _na_cidade(cidade: str) -> str:
    """"em São Paulo", mas "no Rio de Janeiro"."""
    return f"no {cidade}" if cidade.startswith("Rio ") else f"em {cidade}"


def _data_falada(dia: date) -> str:
    return f"{dia.day} de {MESES[dia.month - 1]}"


def separar_filmes(filmes, hoje: date | None = None):
    """(em cartaz, em pré-venda), cada lista com os filmes em mais cinemas primeiro."""
    hoje = hoje or date.today()
    ordenados = sorted(filmes, key=lambda f: -(f.get("countIsPlaying") or 0))
    em_cartaz, pre_venda = [], []
    for filme in ordenados:
        estreia = _estreia(filme)
        futuro = filme.get("isComingSoon") or (estreia is not None and estreia > hoje)
        (pre_venda if futuro else em_cartaz).append(filme)
    return em_cartaz, pre_venda


def _genero_pedido(texto: str, filmes) -> str | None:
    t = _normalizar(texto)
    palavras = {APELIDOS_GENEROS.get(p, p) for p in t.split()}
    for genero in sorted({g for f in filmes for g in f.get("genres") or []}, key=len, reverse=True):
        raiz = _normalizar(genero)[:5]
        if any(p.startswith(raiz) for p in palavras):
            return genero
    return None


def _descrever(filme, hoje: date, com_genero: bool = True) -> str:
    generos = filme.get("genres") or []
    texto = filme.get("title", "")
    if generos and com_genero:
        texto += f", {generos[0].lower()}"
    if _estreia(filme) == hoje:
        texto += ", que estreia hoje"
    return texto


def selecionar_filmes(filmes, texto_pedido: str, hoje: date):
    """(em cartaz, pré-venda, gênero pedido, só estreias?) conforme o pedido ("de terror", "estreias")."""
    em_cartaz, pre_venda = separar_filmes(filmes, hoje)
    genero = _genero_pedido(texto_pedido, filmes)
    if genero:
        em_cartaz = [f for f in em_cartaz if genero in (f.get("genres") or [])]
        pre_venda = [f for f in pre_venda if genero in (f.get("genres") or [])]
    so_estreias = bool(re.search(r"\b(estreia\w*|lancamento\w*|novidade\w*)\b", _normalizar(texto_pedido)))
    if so_estreias:
        em_cartaz = [f for f in em_cartaz if _estreia(f) and (hoje - _estreia(f)).days <= 7]
    return em_cartaz, pre_venda, genero, so_estreias


def resumo_filmes(filmes, cidade: str, texto_pedido: str = "", hoje: date | None = None) -> str:
    """Texto falado com os filmes em cartaz (opcionalmente de um gênero ou só as estreias)."""
    hoje = hoje or date.today()
    em_cartaz, pre_venda, genero, so_estreias = selecionar_filmes(filmes, texto_pedido, hoje)

    de_genero = f" de {genero.lower()}" if genero else ""
    if not em_cartaz and not pre_venda:
        return f"Não encontrei filmes{de_genero} em cartaz {_na_cidade(cidade)} agora."

    frases = []
    if em_cartaz:
        nomes = "; ".join(_descrever(f, hoje, com_genero=not genero) for f in em_cartaz[:FILMES_FALADOS])
        if so_estreias:
            frases.append(f"Estreias da semana{de_genero} {_na_cidade(cidade)}: {nomes}.")
        else:
            total = len(em_cartaz)
            onde = _na_cidade(cidade)
            inicio = f"{onde[0].upper()}{onde[1:]} há {total} filme{'s' if total != 1 else ''}{de_genero} em cartaz."
            frases.append(f"{inicio} Os que estão em mais cinemas: {nomes}." if total > 1 else f"{inicio} É {nomes}.")
    if pre_venda and not so_estreias:
        proximos = [f"{f.get('title')}, que estreia em {_data_falada(_estreia(f))}" if _estreia(f) else f.get("title", "")
                    for f in pre_venda[:3]]
        frases.append("Em pré-venda: " + "; ".join(proximos) + ".")
    frases.append("Se quiser saber de algum, peça a sinopse.")
    return " ".join(frases)


def sinopse(filmes, texto_pedido: str) -> str:
    """Sinopse do filme citado no pedido, procurado entre os que estão em cartaz."""
    t = _normalizar(texto_pedido)
    por_titulo = {_normalizar(f.get("title", "")): f for f in filmes if f.get("title")}
    citados = [titulo for titulo in por_titulo if titulo and re.search(rf"\b{re.escape(titulo)}\b", t)]
    trecho = re.split(r"\b(?:sinopse|filme|trata)\b(?: d[eoa]s?)?", t)[-1].strip()
    # Nome parcial ("Vingadores" para "Vingadores: Doutor Destino"): o mais exibido entre os que contêm o trecho.
    contendo = [titulo for titulo in por_titulo if trecho and re.search(rf"\b{re.escape(trecho)}\b", titulo)]
    if citados:
        filme = por_titulo[max(citados, key=len)]
    elif contendo:
        filme = max((por_titulo[titulo] for titulo in contendo), key=lambda f: f.get("countIsPlaying") or 0)
    else:
        parecido = difflib.get_close_matches(trecho, list(por_titulo), n=1, cutoff=0.6) if trecho else []
        if not parecido:
            return "Não achei esse filme entre os que estão em cartaz. Pode repetir o nome?"
        filme = por_titulo[parecido[0]]

    detalhes = [g.lower() for g in (filme.get("genres") or [])[:2]]
    if filme.get("duration"):
        detalhes.append(f"{filme['duration']} minutos")
    classificacao = filme.get("contentRating") or ""
    if classificacao and "verifique" not in classificacao.lower():  # sem classificação vem "Verifique a Classificação"
        detalhes.append(f"classificação {classificacao.lower()}")
    texto = re.sub(r"\s+", " ", filme.get("synopsis") or "").strip()
    if len(texto) > 450:  # longa demais para ouvir: fica até a última frase inteira
        frases = re.split(r"(?<=[.!?])\s+", texto[:450])
        texto = " ".join(frases[:-1]) if len(frases) > 1 else frases[0].rsplit(" ", 1)[0] + "..."
    cabecalho = f"{filme.get('title')}" + (f", {', '.join(detalhes)}" if detalhes else "") + "."
    return f"{cabecalho} {texto}" if texto else f"{cabecalho} O Ingresso.com não traz a sinopse dele."


# ------------------------------------------------------------------ cinemas
# Formatos de sessão que fazem um cinema se destacar, com o peso de cada um na escolha.
# Os comuns (3D, "Infinity Vision", dublado, legendado) não contam.
FORMATOS_ESPECIAIS = {
    "imax": ("IMAX", 5), "4dx": ("4DX", 3), "macro xe": ("Macro XE", 3), "dolby atmos": ("Dolby Atmos", 3),
    "xd": ("XD", 2), "d-box": ("D-Box", 2), "vip": ("sala VIP", 2), "prime": ("sala Prime", 2),
    "platinum": ("sala Platinum", 2), "laser": ("projeção a laser", 2), "xplus": ("XPLUS", 2), "cinepic": ("CINEPIC", 2),
}
VALIDADE_FORMATOS = 6 * 3600  # os formatos de cada cinema quase não mudam
CINEMAS_FALADOS = 5


def _juntar(itens) -> str:
    itens = list(itens)
    return " e ".join([", ".join(itens[:-1]), itens[-1]]) if len(itens) > 1 else "".join(itens)


def _formatos_do_cinema(cidade_id, cinema) -> Counter:
    """Quantas sessões de cada formato o cinema tem nos próximos dias (vazio se a consulta falhar)."""
    formatos = Counter()
    try:
        dias = _buscar(f"sessions/city/{cidade_id}/theater/{cinema['id']}", validade=VALIDADE_FORMATOS)
    except (requests.RequestException, ValueError) as exc:
        logger.debug("Sessões de %s indisponíveis: %s", cinema.get("name"), exc)
        return formatos
    for dia in dias or []:
        for filme in dia.get("movies") or []:
            for sala in filme.get("rooms") or []:
                for sessao in sala.get("sessions") or []:
                    formatos.update((tipo.get("name") or "").lower() for tipo in sessao.get("types") or [])
    # Alguns recursos só aparecem no nome das salas ("Sala 1 - LASER").
    for sala in cinema.get("rooms") or []:
        nome = (sala.get("fullName") or sala.get("name") or "").lower()
        formatos.update(chave for chave in FORMATOS_ESPECIAIS if chave in nome)
    return formatos


def melhores_cinemas(cidade_id, cinemas, cidade: str) -> str:
    """Os cinemas com as salas mais especiais e, à parte, os de filmes de arte (sem sessões dubladas)."""
    with ThreadPoolExecutor(max_workers=6) as executor:
        formatos = list(executor.map(lambda c: _formatos_do_cinema(cidade_id, c), cinemas))

    pontuados = []
    for cinema, encontrados in zip(cinemas, formatos):
        especiais = [FORMATOS_ESPECIAIS[chave] for chave in FORMATOS_ESPECIAIS if encontrados.get(chave)]
        pontos = sum(peso for _, peso in especiais)
        if pontos:
            pontuados.append((pontos, cinema.get("totalRooms") or 0, cinema, [nome for nome, _ in especiais]))
    pontuados.sort(key=lambda item: (item[0], item[1]), reverse=True)

    def onde(cinema):
        bairro = cinema.get("neighborhood")
        return f"{cinema.get('name')}, em {bairro}" if bairro else cinema.get("name", "")

    frases = []
    if pontuados:
        nomes = "; ".join(f"{onde(c)}, com {_juntar(recursos)}" for _, _, c, recursos in pontuados[:CINEMAS_FALADOS])
        frases.append(f"Pelos recursos das salas, os melhores cinemas {_na_cidade(cidade)} são: {nomes}.")
    arte = sorted(
        ((sum(f.values()), c) for c, f in zip(cinemas, formatos) if f and not f.get("dublado") and f.get("legendado")),
        key=lambda item: item[0], reverse=True,
    )
    if arte:
        frases.append(f"Para filmes de arte e legendados: {_juntar(onde(c) for _, c in arte[:2])}.")
    if not frases:
        maiores = sorted(cinemas, key=lambda c: c.get("totalRooms") or 0, reverse=True)[:3]
        return (f"{cidade} tem {len(cinemas)} cinema{'s' if len(cinemas) != 1 else ''} no Ingresso.com, mas nenhum "
                f"com sala especial como IMAX ou VIP. Os maiores são: {_juntar(onde(c) for c in maiores)}.")
    return " ".join(frases)


# ------------------------------------------------------------------ entrada
def responder_pedido_de_cinema(texto: str) -> str | None:
    """Responde sobre filmes em cartaz, sinopses e melhores cinemas; None se o pedido não for sobre cinema."""
    tipo = identificar_pedido(texto)
    if not tipo:
        return None
    try:
        cidade = _cidade_do_pedido(texto) or encontrar_cidade(CIDADE_PADRAO)
        if not cidade:
            return f"Não encontrei {CIDADE_PADRAO} no Ingresso.com. Diga a cidade no pedido, por exemplo: filmes em cartaz em Campinas."
        nome = cidade["name"]
        if tipo == "cinemas":
            return melhores_cinemas(cidade["id"], _buscar(f"theaters/city/{cidade['id']}"), nome)
        filmes = _buscar(f"events/city/{cidade['id']}", isPlaying="true")
        if tipo == "sinopse":
            return sinopse(filmes, texto)
        return resumo_filmes(filmes, nome, texto)
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.warning("Programação de cinema indisponível: %s", exc)
        return "Não consegui consultar a programação dos cinemas agora. Tente de novo em instantes."


def cartao_de_filmes(texto: str, hoje: date | None = None):
    """Dados do cartão de filmes do painel (pôster, gênero, estreia); None se o pedido não for de filmes.

    Usa a mesma programação já guardada em cache pela resposta falada, então não consulta de novo.
    """
    if identificar_pedido(texto) != "filmes":
        return None
    hoje = hoje or date.today()
    try:
        cidade = _cidade_do_pedido(texto) or encontrar_cidade(CIDADE_PADRAO)
        if not cidade:
            return None
        filmes = _buscar(f"events/city/{cidade['id']}", isPlaying="true")
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.debug("Cartão de filmes indisponível: %s", exc)
        return None
    em_cartaz, pre_venda, genero, so_estreias = selecionar_filmes(filmes, texto, hoje)

    def item(filme):
        estreia = _estreia(filme)
        return {
            "titulo": filme.get("title", ""),
            "genero": ((filme.get("genres") or [""])[0] or "").lower(),
            "poster": next((i.get("url", "") for i in filme.get("images") or [] if i.get("type") == "PosterPortrait"), ""),
            "estreia": _data_falada(estreia) if estreia else "",
            "estreia_hoje": estreia == hoje,
            "duracao": filme.get("duration") or 0,
            "classificacao": filme.get("contentRating") or "",
        }

    return {"cidade": cidade["name"], "genero": (genero or "").lower(), "so_estreias": so_estreias,
            "filmes": [item(f) for f in em_cartaz[:12]],
            "pre_venda": [] if so_estreias else [item(f) for f in pre_venda[:4]]}
