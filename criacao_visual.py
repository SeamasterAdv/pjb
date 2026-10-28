"""Imagens e modelos 3D feitos pelo Claude, mostrados no centro do painel no lugar da esfera.

- "cria uma imagem de um farol à noite", "desenha um gato astronauta", "faz um logo pra minha banca de doces";
- "cria um modelo 3D de uma casa", "modela um foguete em 3D", "quero ver um dinossauro em 3D";
- depois, "muda a cor do fundo para azul", "deixa ele vermelho", "refaz a imagem" (até 10 min depois);
- "fecha a imagem", "volta a esfera", "mostra a imagem de novo".

O Claude não gera fotos (pixels): a imagem é um **SVG** (desenho vetorial, com gradientes, sombras e até animação) e o modelo
3D é uma **cena em JSON** feita de formas (caixas, esferas, cilindros, perfis de torno, extrusões, tubos...), que o painel monta
com o Three.js (`interface_web/criacao_3d.js`) e devolve como `.glb`. Só o pedido vai para a nuvem: nada da memória.
Claude primeiro e o Gemini de reserva (o modelo do PC não desenha bem). O SVG é limpo aqui (sem scripts, eventos nem nada
externo) e o painel o mostra num <img>, onde nada roda; a cena 3D é conferida campo a campo (`normalizar_modelo`).
Arquivos em `documentos_gerados/AAAA-MM-DD_criacoes`.
"""
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime

from config import PASTA_DOCUMENTOS_GERADOS

logger = logging.getLogger(__name__)

SEGUNDOS_PARA_AJUSTAR = 600  # "muda a cor do fundo" vale até 10 min depois da última criação
LIMITE_SVG = 400_000  # caracteres
LIMITE_ELEMENTOS_SVG = 20_000
MAX_PARTES = 400
MAX_PONTOS = 200
MAX_PROFUNDIDADE = 5
LIMITE_COORDENADA = 100.0
LIMITE_TAMANHO = 50.0

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


class ErroDeCriacao(ValueError):
    """A resposta da IA não serve (sem SVG, JSON quebrado, cena vazia)."""


# ------------------------------------------------------------------ o pedido
def sem_acentos(texto: str) -> str:
    """Minúsculas e sem acentos, com o mesmo tamanho do texto (para recortar o original pelas posições)."""
    return "".join(unicodedata.normalize("NFD", letra)[0] for letra in (texto or "").lower())


_VERBO = (r"(?:ger[ae]r?|cri[ae]r?|fa(?:z|ca|zer)|desenh[ae]r?|ilustr[ae]r?|mont[ae]r?|model[ae]r?|produz(?:a|ir)?|"
          r"pint[ae]r?|quero|queria|me\s+d[ae])")  # "manda a imagem" é mandar um arquivo que já existe
_PARA_MIM = r"(?:(?:me\s+)?(?:pra|para)\s+mim\s+|ai\s+)?"
_ARTIGO = r"(?:(?:um|uma|o|a|outr[ao]|nov[ao])\s+)?"
_OBJ_3D = r"(?:(?:modelo|objeto|cena|escultura|peca|boneco)\s+(?:em\s+)?3\s?d|modelo\s+tridimensional|3\s?d)"
_OBJ_IMAGEM = (r"(?:imagem|desenho|ilustracao|figura|pintura|arte|logo(?:tipo|marca)?|icone|wallpaper|papel\s+de\s+parede|"
               r"poster|cartaz|vetor|avatar|emblema|mascote)")
_OBJ_VIDEO = r"(?:video(?:zinho)?(?:\s+animado)?|animacao|reels?|tiktok|clipe)"
RE_CRIAR_VIDEO = re.compile(r"\b" + _VERBO + r"\s+" + _PARA_MIM + _ARTIGO + _OBJ_VIDEO + r"\b")
RE_CRIAR_3D = re.compile(r"\b" + _VERBO + r"\s+" + _PARA_MIM + _ARTIGO + _OBJ_3D + r"\b")
RE_CRIAR_IMAGEM = re.compile(r"\b" + _VERBO + r"\s+" + _PARA_MIM + r"(?P<objeto>" + _ARTIGO + r"(?P<nome>" + _OBJ_IMAGEM +
                             r"))\b")
# "faz um logo pra minha banca": o nome fica no assunto ("um logo pra minha banca"); "uma imagem de um farol" vira "um farol".
OBJETOS_GENERICOS = {"imagem", "desenho", "ilustracao", "figura", "pintura", "arte", "vetor"}
RE_DESENHA = re.compile(r"\b(?:me\s+)?desenh[ae]\s+" + _PARA_MIM + r"(?P<artigo>um|uma|o|a|uns|umas)\s+(?=\w)")
RE_EM_3D = re.compile(r"\b(?:model[ae]r?|mostr[ae]r?|quero\s+ver|cri[ae]r?|fa(?:z|ca))\b.{1,80}?\bem\s+3\s?d\b")
RE_SO_EM_3D = re.compile(r"^\s*(?:em\s+)?3\s?d\b|\s*\b(?:em|no\s+formato)\s+3\s?d\b", re.IGNORECASE)
# "faz uma imagem desse PDF" é conversão (apps da ArtCraft), e "como fazer um modelo 3D no Blender?" é pergunta.
RE_DE_ARQUIVO = re.compile(r"^\s*(?:d(?:ess|est|aquel)[ea]s?|disso|disto|da\s+tela|do\s+(?:video|pdf|arquivo|word)|"
                           r"com\s+(?:ess[ae]s|est[ae]s|as|os|minhas|meus)\s+(?:fotos|imagens|videos))\b")
# As opções do vídeo ditas no pedido valem mais que o estilo: formato, narração, legendas e duração.
RE_EM_PE = re.compile(r"\b(?:em\s+pe|vertical|reels?|tiktok|stories|shorts?|pro\s+status|9\s?(?::|x|por)\s?16)\b")
RE_DEITADO = re.compile(r"\b(?:deitado|horizontal|pro\s+youtube|16\s?(?::|x|por)\s?9)\b")
RE_QUADRADO = re.compile(r"\b(?:quadrado|1\s?(?::|x|por)\s?1)\b")
RE_SEM_NARRACAO = re.compile(r"\bsem\s+(?:narracao|voz|locucao|fala)\b")
RE_SEM_LEGENDA = re.compile(r"\bsem\s+legendas?\b")
RE_DURACAO = re.compile(r"\b(?:de|com|em)\s+(\d{1,3}|um|uma|dois|meio)\s*(segundos?|s|minutos?|min)(\s+e\s+meio)?\b")
# "Faz um vídeo curto sobre…": logo depois do objeto, o tamanho vira a duração.
RE_TAMANHO_DO_VIDEO = re.compile(r"^\s*(curtinho|curto|rapidinho|rapido|medio|longo|grande)\b")
SEGUNDOS_DO_TAMANHO = {"curtinho": 15, "curto": 20, "rapidinho": 15, "rapido": 20, "medio": 45, "longo": 90, "grande": 90}
RE_TRECHO_DAS_OPCOES = re.compile(
    r"^\s*(?:curtinho|curto|rapidinho|rapido|medio|longo|grande)\b|"
    r"[\s,]*\b(?:(?:na\s+|no\s+formato\s+)?(?:em\s+pe|vertical|horizontal|deitado|quadrado)|pro\s+(?:reels|tiktok|status|youtube|"
    r"stories)|sem\s+(?:narracao|voz|locucao|fala|legendas?)|"
    r"(?:de|com|em)\s+(?:\d{1,3}|um|uma|dois|meio)\s*(?:segundos?|s|minutos?|min)(?:\s+e\s+meio)?)\b")


def opcoes_do_video(base: str) -> dict:
    """"Em pé", "sem narração", "sem legenda", "de 30 segundos" ditos no pedido (texto sem acentos)."""
    opcoes = {}
    if RE_EM_PE.search(base):
        opcoes["formato"] = "9x16"
    elif RE_QUADRADO.search(base):
        opcoes["formato"] = "1x1"
    elif RE_DEITADO.search(base):
        opcoes["formato"] = "16x9"
    if RE_SEM_NARRACAO.search(base):
        opcoes["narracao"] = "nenhuma"
    if RE_SEM_LEGENDA.search(base):
        opcoes["legendas"] = "nenhuma"
    duracao = RE_DURACAO.search(base)
    if duracao:
        numero = {"um": 1, "uma": 1, "dois": 2, "meio": 0.5}.get(duracao.group(1)) or int(duracao.group(1))
        if duracao.group(3):
            numero += 0.5
        segundos = numero * 60 if duracao.group(2).startswith("m") else numero
        opcoes["segundos"] = int(max(8, min(180, segundos)))
    return opcoes


def _sem_os_trechos(original: str, base: str, padrao) -> str:
    """O texto original sem os trechos que o padrão acha na cópia sem acentos (de mesmo tamanho)."""
    partes, ultimo = [], 0
    for achado in padrao.finditer(base):
        partes.append(original[ultimo:achado.start()])
        ultimo = achado.end()
    partes.append(original[ultimo:])
    return re.sub(r"\s{2,}", " ", "".join(partes))
RE_PERGUNTA = re.compile(r"^\s*(?:como|o\s+que|qual|quais|por\s*que|onde|quanto|sera\s+que|voce\s+(?:sabe|consegue|pode))\b")
RE_INICIO_DO_ASSUNTO = re.compile(r"^\s*(?:,|:|-)?\s*(?:de|do|da|dos|das|sobre|com|mostrando|representando|que\s+mostr[ae])?\s*",
                                  re.IGNORECASE)
RE_ENVIO = re.compile(r"\s*,?\s*\be\s+(?:me\s+)?(?:manda|envia)\b.*$", re.IGNORECASE)

RE_FECHAR = re.compile(
    r"\b(?:fech[ae]r?|tir[ae]r?|escond[ae]r?|some\s+com|apag[ae]r?\s+da\s+tela)\s+(?:(?:a|o|essa|esse|com\s+[ao])\s+)?"
    r"(?:imagem|desenho|ilustracao|modelo(?:\s+3\s?d)?|3\s?d|criacao|video)\b"
    r"|\b(?:volt[ae]|traz(?:\s+de\s+volta)?|mostr[ae])\s+(?:(?:a|com\s+a)\s+)?(?:sua\s+)?esfera\b")
RE_DE_NOVO = re.compile(
    r"\b(?:mostr[ae]|abr[ae]|volt[ae]|traz)\s+(?:(?:a|o)\s+)?(?:ultim[ao]\s+)?(?:imagem|desenho|modelo(?:\s+3\s?d)?|3\s?d|video)"
    r"(?:\s+(?:que\s+voce\s+(?:fez|criou|desenhou))|\s+de\s+novo|\s+outra\s+vez)")
RE_AJUSTE_VERBO = re.compile(
    r"^\s*(?:agora\s+|e\s+)?(?:muda|mude|troca|troque|deixa|deixe|coloca|coloque|poe|ponha|bota|acrescenta|adiciona|tira|"
    r"remove|aumenta|diminui|refaz|refaca|melhora|capricha|gira|vira)\b")
RE_AJUSTE_REFERENCIA = re.compile(
    r"\b(?:imagem|desenho|ilustracao|modelo|3\s?d|nele|nela|no\s+fundo|o\s+fundo|a\s+cor|as\s+cores|criacao|video|cena|"
    r"narracao|legenda)\b")
# "Refaz o vídeo em pé", "aplica meu estilo novo no vídeo", "deixa o vídeo sem narração": o mesmo roteiro, montado de novo
# no estilo (ou com a opção dita), sem pedir nada ao Claude.
RE_REESTILO_VERBO = re.compile(r"\b(?:refaz\w*|refa[cç]a|monta\w*|aplica\w*|passa\w*|poe|ponha|bota|deixa|deixe|transforma|"
                               r"muda|troca|faz|faca)\b")
RE_O_VIDEO = re.compile(r"\b(?:o|esse|este|aquele|o\s+ultimo|o\s+mesmo)\s+video\b|\bnele\b|^\s*agora\b")
RE_ELE_COM_QUALIDADE = re.compile(
    r"\b(?:ele|ela)\s+(?:mais\s+|menos\s+|bem\s+)?(?:azul|vermelh|verde|amarel|roxo|rox|rosa|pret|branc|dourad|prateado|"
    r"cinza|laranja|marrom|maior|menor|colorid|realista|fof|brilhante|escur|clar|detalhad|simples)")


@dataclass
class PedidoDeCriacao:
    formato: str  # "imagem", "3d" ou "video"
    assunto: str
    ia: str = None  # "claude" ou "gemini", se dita na frase
    mandar: bool = False  # "... e me manda"
    opcoes: dict = field(default_factory=dict)  # do vídeo, ditas no pedido: formato, narracao, legendas, segundos


def interpretar(texto: str):
    """O pedido de criar uma imagem ou um modelo 3D, ou None. Assunto vazio vem como "" (ela pergunta de quê)."""
    from llm_nuvem import extrair_ia_pedida

    original, ia = extrair_ia_pedida(texto or "")
    original = re.sub(r"^\s*beatriz\W+", "", original.strip(), flags=re.IGNORECASE)
    mandar = bool(RE_ENVIO.search(original))
    original = RE_ENVIO.sub("", original)
    base = sem_acentos(original)
    if RE_PERGUNTA.search(base):
        return None
    achado = RE_CRIAR_VIDEO.search(base)
    if achado:
        resto = original[achado.end():]
        if RE_DE_ARQUIVO.search(sem_acentos(resto)):
            return None  # "faz um vídeo desse PDF", "cria um vídeo com essas fotos"
        opcoes = opcoes_do_video(base)
        tamanho = RE_TAMANHO_DO_VIDEO.search(sem_acentos(resto))
        if tamanho and "segundos" not in opcoes:
            opcoes["segundos"] = SEGUNDOS_DO_TAMANHO[tamanho.group(1)]
        assunto = _sem_os_trechos(resto, sem_acentos(resto), RE_TRECHO_DAS_OPCOES)
        assunto = RE_INICIO_DO_ASSUNTO.sub("", assunto, count=1).strip(" .,!?:;")
        return PedidoDeCriacao("video", assunto, ia, mandar, opcoes)
    formato, achado = None, RE_CRIAR_3D.search(base)
    if achado:
        formato = "3d"
    else:
        achado = RE_CRIAR_IMAGEM.search(base) or RE_DESENHA.search(base)
        if achado:
            formato = "imagem"
        else:
            achado = RE_EM_3D.search(base)
            if not achado:
                return None
            formato = "3d"
            inicio = re.match(r"\b\w+\s+(?:ver\s+)?", base[achado.start():]).end() + achado.start()
            assunto = RE_SO_EM_3D.sub("", original[inicio:]).strip(" .,!?:;")
            return PedidoDeCriacao("3d", RE_INICIO_DO_ASSUNTO.sub("", assunto, count=1).strip(" .,!?"), ia, mandar)
    resto = original[achado.end():]
    if RE_DE_ARQUIVO.search(sem_acentos(resto)):
        return None
    if formato == "imagem" and RE_SO_EM_3D.search(sem_acentos(resto)):
        formato = "3d"
    resto = RE_SO_EM_3D.sub("", resto)
    if achado.re is RE_DESENHA:  # "desenha um gato": o artigo fica no assunto
        assunto = original[achado.start("artigo"):achado.end()] + resto
    elif achado.re is RE_CRIAR_IMAGEM and achado.group("nome") not in OBJETOS_GENERICOS:
        assunto = original[achado.start("objeto"):achado.end()] + resto
    else:
        assunto = RE_INICIO_DO_ASSUNTO.sub("", resto, count=1)
    return PedidoDeCriacao(formato, assunto.strip(" .,!?:;"), ia, mandar)


# ------------------------------------------------------------------ o SVG
_PROIBIDOS_SVG = {"script", "foreignobject", "iframe", "embed", "object", "image", "audio", "video", "handler", "listener"}
RE_URL_EXTERNA = re.compile(r"url\(\s*(['\"]?)(?!#)[^)]*\1\s*\)", re.IGNORECASE)


def _local(nome: str) -> str:
    return nome.rsplit("}", 1)[-1].lower()


def _limpar_estilo(texto: str) -> str:
    texto = re.sub(r"@import[^;]*;?", "", texto or "", flags=re.IGNORECASE)
    return RE_URL_EXTERNA.sub("none", texto)


def limpar_svg(resposta: str) -> str:
    """O SVG da resposta da IA, sem nada que rode ou busque algo fora (scripts, eventos, links, imagens, fontes externas)."""
    achado = re.search(r"<svg\b.*</svg\s*>", resposta or "", re.DOTALL | re.IGNORECASE)
    if not achado:
        raise ErroDeCriacao("a resposta não trouxe um SVG")
    svg = achado.group(0)
    if len(svg) > LIMITE_SVG:
        raise ErroDeCriacao("o SVG ficou grande demais")
    if re.search(r"<!ENTITY|<!DOCTYPE", svg, re.IGNORECASE):
        raise ErroDeCriacao("o SVG declara entidades")
    cabeca = svg[:svg.index(">") + 1]
    if "xmlns=" not in cabeca:
        svg = svg.replace("<svg", f'<svg xmlns="{SVG_NS}"', 1)
    if "xlink:" in svg and "xmlns:xlink" not in cabeca:
        svg = svg.replace("<svg", f'<svg xmlns:xlink="{XLINK_NS}"', 1)
    svg = svg.replace("&nbsp;", "&#160;")
    try:
        raiz = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ErroDeCriacao(f"o SVG veio quebrado ({exc})") from exc
    if _local(raiz.tag) != "svg":
        raise ErroDeCriacao("a raiz não é <svg>")

    pais = {filho: pai for pai in raiz.iter() for filho in pai}
    elementos = list(raiz.iter())
    if len(elementos) > LIMITE_ELEMENTOS_SVG:
        raise ErroDeCriacao("o SVG tem elementos demais")
    for elemento in elementos:
        if _local(elemento.tag) in _PROIBIDOS_SVG:
            pais[elemento].remove(elemento)
            continue
        for nome in list(elemento.attrib):
            local, valor = _local(nome), elemento.attrib[nome]
            if local.startswith("on") or "javascript:" in valor.lower():
                del elemento.attrib[nome]
            elif local == "href" and not valor.strip().startswith("#"):
                del elemento.attrib[nome]
            elif local == "style" or "url(" in valor.lower():
                elemento.attrib[nome] = _limpar_estilo(valor)
        if _local(elemento.tag) == "style" and elemento.text:
            elemento.text = _limpar_estilo(elemento.text)

    if not raiz.get("viewBox"):
        largura, altura = (re.match(r"[\d.]+", raiz.get(lado, "") or "") for lado in ("width", "height"))
        raiz.set("viewBox", f"0 0 {largura.group(0)} {altura.group(0)}" if largura and altura else "0 0 1024 1024")
    for lado in ("width", "height"):  # o tamanho é o do painel; a proporção vem do viewBox
        raiz.attrib.pop(lado, None)
    return ET.tostring(raiz, encoding="unicode")


def titulo_do_svg(svg: str) -> str:
    achado = re.search(r"<(?:\w+:)?title[^>]*>(.*?)</(?:\w+:)?title>", svg or "", re.DOTALL | re.IGNORECASE)
    return re.sub(r"\s+", " ", achado.group(1)).strip()[:80] if achado else ""


# ------------------------------------------------------------------ a cena 3D
FORMAS = {"caixa", "esfera", "cilindro", "cone", "toro", "capsula", "plano", "disco", "anel", "tetraedro", "octaedro",
          "dodecaedro", "icosaedro", "torno", "extrusao", "tubo", "grupo"}
MATERIAIS = {"fosco", "brilhante", "metal", "vidro", "emissivo"}
MEDIDAS = ("largura", "altura", "profundidade", "raio", "raio_topo", "raio_base", "raio_interno", "raio_externo", "tubo",
           "comprimento", "arco", "chanfro")
RE_COR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _numero(valor, padrao=0.0, minimo=-LIMITE_COORDENADA, maximo=LIMITE_COORDENADA) -> float:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return padrao
    if not math.isfinite(numero):
        return padrao
    return max(minimo, min(maximo, numero))


def _vetor(valor, padrao, minimo=-LIMITE_COORDENADA, maximo=LIMITE_COORDENADA) -> list:
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        valor = [valor] * 3
    if not isinstance(valor, (list, tuple)) or len(valor) != 3:
        return list(padrao)
    return [round(_numero(v, p, minimo, maximo), 4) for v, p in zip(valor, padrao)]


def _pontos(valor, dimensoes: int) -> list:
    if not isinstance(valor, list):
        return []
    pontos = []
    for ponto in valor[:MAX_PONTOS]:
        if isinstance(ponto, (list, tuple)) and len(ponto) == dimensoes:
            pontos.append([round(_numero(v), 4) for v in ponto])
    return pontos


def _cor(valor, padrao="#c8c8c8") -> str:
    valor = str(valor or "").strip()
    if re.match(r"^#[0-9a-fA-F]{3}$", valor):
        valor = "#" + "".join(letra * 2 for letra in valor[1:])
    return valor.lower() if RE_COR.match(valor) else padrao


def _parte(dados, profundidade: int, contagem: list):
    if not isinstance(dados, dict) or contagem[0] >= MAX_PARTES:
        return None
    forma = sem_acentos(str(dados.get("forma") or "")).strip()
    if forma not in FORMAS:
        return None
    parte = {
        "forma": forma,
        "nome": str(dados.get("nome") or "")[:40],
        "posicao": _vetor(dados.get("posicao"), [0, 0, 0]),
        "rotacao": _vetor(dados.get("rotacao"), [0, 0, 0], -720, 720),
        "escala": _vetor(dados.get("escala"), [1, 1, 1], 0.001, LIMITE_TAMANHO),
    }
    giro = dados.get("gira")
    if giro is not None:
        parte["gira"] = _vetor(giro, [0, 0, 0], -720, 720)
    if forma == "grupo":
        if profundidade >= MAX_PROFUNDIDADE:
            return None
        contagem[0] += 1  # o grupo conta antes das filhas; vazio, devolve a vaga
        parte["partes"] = [p for p in (_parte(d, profundidade + 1, contagem) for d in dados.get("partes") or []) if p]
        if not parte["partes"]:
            contagem[0] -= 1
            return None
        return parte
    for medida in MEDIDAS:
        if medida in dados:
            parte[medida] = round(_numero(dados[medida], 1.0, 0.0005 if medida != "arco" else 1, 360 if medida == "arco"
                                          else LIMITE_TAMANHO), 4)
    if forma == "torno":
        parte["pontos"] = _pontos(dados.get("pontos"), 2)
        if len(parte["pontos"]) < 2:
            return None
    elif forma == "extrusao":
        parte["pontos"] = _pontos(dados.get("pontos"), 2)
        if len(parte["pontos"]) < 3:
            return None
    elif forma == "tubo":
        parte["pontos"] = _pontos(dados.get("pontos"), 3)
        if len(parte["pontos"]) < 2:
            return None
    material = sem_acentos(str(dados.get("material") or "fosco")).strip()
    parte["material"] = material if material in MATERIAIS else "fosco"
    parte["cor"] = _cor(dados.get("cor"))
    parte["opacidade"] = round(_numero(dados.get("opacidade", 1), 1.0, 0.05, 1.0), 3)
    contagem[0] += 1
    return parte


def extrair_json(resposta: str) -> dict:
    texto = re.sub(r"^```(?:json)?|```$", "", (resposta or "").strip(), flags=re.MULTILINE)
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim <= inicio:
        raise ErroDeCriacao("a resposta não trouxe um JSON")
    try:
        dados = json.loads(texto[inicio:fim + 1])
    except json.JSONDecodeError as exc:
        raise ErroDeCriacao(f"o JSON veio quebrado ({exc})") from exc
    if not isinstance(dados, dict):
        raise ErroDeCriacao("o JSON não é um objeto")
    return dados


def normalizar_modelo(dados: dict) -> dict:
    """A cena 3D só com formas, medidas, cores e materiais conhecidos, e números dentro dos limites."""
    contagem = [0]
    partes = [p for p in (_parte(d, 0, contagem) for d in (dados.get("partes") or [])) if p]
    if not partes:
        raise ErroDeCriacao("a cena 3D veio sem nenhuma parte válida")
    return {
        "titulo": str(dados.get("titulo") or "")[:80],
        "descricao": str(dados.get("descricao") or "")[:300],
        "girar_sozinho": dados.get("girar_sozinho") is not False,
        "partes": partes,
        "total_de_partes": contagem[0],
    }


# ------------------------------------------------------------------ as instruções para a IA
INSTRUCOES_SVG = """Você é uma ilustradora vetorial de primeira linha. Responda APENAS com um documento SVG completo, que começa \
em <svg e termina em </svg>, sem nenhum texto antes ou depois e sem blocos de código.

Regras:
- xmlns="http://www.w3.org/2000/svg" e um viewBox (0 0 1024 1024 por padrão; 1600 900 para paisagens e cenas largas; \
900 1600 para retratos em pé). Sem width e height.
- Primeiro elemento: <title> com o nome da imagem em português (até 6 palavras).
- Nada externo nem executável: sem <image>, sem <script>, sem <foreignObject>, sem atributos on*, sem links, sem fontes da \
internet. Texto só se pedido, com font-family sans-serif.
- Capriche: composição pensada, cena completa com fundo próprio (a tela onde aparece é preta), luz e sombra, volume com \
linearGradient e radialGradient, filtros (feGaussianBlur, feDropShadow) com moderação, detalhes e texturas com formas. \
Pode ter centenas de elementos, mas no máximo uns 150 KB.
- Se combinar com o pedido, uma animação discreta e em laço com <animate> ou <animateTransform> (chamas, água, estrelas).
- Logo, ícone ou emblema: fundo transparente, formas limpas e poucas cores."""

INSTRUCOES_3D = """Você é uma modeladora 3D. Monte o objeto pedido com formas simples e responda APENAS com um objeto JSON, \
sem texto antes ou depois e sem blocos de código, neste formato:

{"titulo": "nome curto em português", "descricao": "uma frase sobre o que foi montado", "girar_sozinho": true,
 "partes": [ {"nome": "...", "forma": "...", ...medidas..., "posicao": [x, y, z], "rotacao": [graus x, y, z],
              "escala": [x, y, z], "cor": "#rrggbb", "material": "fosco", "opacidade": 1, "gira": [graus por segundo x, y, z]} ]}

Formas e medidas (unidades em metros; o eixo Y é para cima; cada forma nasce centrada na própria posição):
- "caixa": largura (X), altura (Y), profundidade (Z), chanfro opcional (arredonda as quinas)
- "esfera": raio (use "escala" para elipsoides)
- "cilindro": raio_topo, raio_base, altura (em pé, no eixo Y)
- "cone": raio, altura (ponta para cima)
- "capsula": raio, comprimento (em pé)
- "toro": raio, tubo, arco opcional em graus (deitado no plano XY; gire 90 em X para deitar no chão)
- "plano" (largura, altura) e "disco" (raio): de frente para +Z
- "anel": raio_interno, raio_externo (de frente para +Z)
- "tetraedro", "octaedro", "dodecaedro", "icosaedro": raio
- "torno": pontos [[raio, y], ...] de baixo para cima, girados em volta do eixo Y (vasos, garrafas, taças, troncos, cúpulas)
- "extrusao": pontos [[x, y], ...] do contorno no plano XY e profundidade (em Z) (asas, letras, folhas, perfis)
- "tubo": pontos [[x, y, z], ...] por onde passa uma curva suave, e raio (alças, cabos, rabos, galhos, chifres)
- "grupo": "partes": [...]; a posição, a rotação e a escala do grupo valem para todas (rodas, braços, pernas)
Materiais: "fosco", "brilhante", "metal", "vidro" (transparente) e "emissivo" (brilha com a própria cor: luzes, telas, \
fogo). "gira" é opcional, para hélices, rodas, planetas.

Capriche: o objeto apoiado no chão (o ponto mais baixo em y = 0), centrado em x e z, com 2 a 4 metros no maior lado; \
proporções certas e muitos detalhes (de 30 a 200 partes: janelas, olhos, parafusos, faróis, folhas); cores harmônicas \
e realistas. Sem chão, céu ou cenário em volta, a não ser que peçam uma cena."""

INSTRUCAO_DE_AJUSTE = ("Esta é a versão atual. Faça só a mudança pedida, mantendo o resto, e devolva o documento inteiro no "
                       "mesmo formato.")

INSTRUCOES_VIDEO = """Você é roteirista e ilustradora de vídeos animados curtos. Responda APENAS com um objeto JSON, sem texto \
antes ou depois e sem blocos de código, neste formato:

{"titulo": "até 6 palavras", "descricao": "uma frase curta", "cenas": [
  {"layout": "lado", "titulo": "até 6 palavras", "texto": "até 14 palavras (opcional)", "itens": ["até 4 tópicos curtos"],
   "narracao": "o que a voz fala nesta cena", "ilustracao": "<svg ...>...</svg>", "fundo": "#rrggbb (opcional)"}]}

Layouts (varie entre as cenas):
- "lado": a ilustração ao lado do texto (em cima, no vídeo em pé);
- "centro": a ilustração em cima e o texto embaixo, centralizado;
- "tela_cheia": a ilustração cobre a tela toda e o texto fica por cima, embaixo;
- "lista": só texto, com o título e os itens (tópicos); sem ilustração;
- "titulo": uma frase grande no meio (abertura de um bloco, número de impacto, conclusão); sem ilustração.

Regras:
- A primeira cena prende a atenção; a última fecha com uma conclusão ou chamada. Fatos corretos, em português do Brasil.
- Na tela, pouco texto: a narração explica e a tela destaca. "itens" só no layout "lista".
- Ilustre de metade a três quartos das cenas. Cada ilustração é um SVG completo com xmlns e viewBox, com ASPAS SIMPLES nos \
atributos (ela está dentro de uma string JSON), sem texto dentro, sem <image>, <script>, <foreignObject>, links ou fontes \
externas; desenho flat caprichado (degradês, sombras suaves, formas limpas) e leve (até uns 6 KB).
- "lado" e "centro": viewBox 0 0 800 600, fundo transparente (só o objeto ou a cena recortada). "tela_cheia": a proporção \
do vídeo (0 0 1600 900 deitado, 0 0 900 1600 em pé, 0 0 1000 1000 quadrado), com fundo, e a parte de baixo mais simples \
(o texto entra ali)."""


@dataclass
class Criacao:
    formato: str
    assunto: str
    titulo: str
    conteudo: object  # o SVG (texto), a cena 3D (dict) ou o vídeo ({"roteiro", "estilo", "duracao", "cenas"})
    bruto: str  # o que a IA devolveu, para um ajuste depois
    arquivo: str
    ia: str
    quando: float = field(default_factory=time.time)


_CLAUDE_LONGO = {"cliente": None}


def _provedores_padrao(longo: bool = False) -> list:
    """O Claude da assinatura (o mesmo da conversa, com a mesma pausa por limite) e o Gemini de reserva. O roteiro de um
    vídeo, com as ilustrações, passa dos 5 min da conversa: ganha um Claude com 15 min, como os das tarefas."""
    from llm_nuvem import ClienteClaudeCode, claude, gemini

    if not longo:
        return [claude, gemini]
    if _CLAUDE_LONGO["cliente"] is None:
        _CLAUDE_LONGO["cliente"] = ClienteClaudeCode(executavel=claude.executavel, tempo_limite=900)
    return [_CLAUDE_LONGO["cliente"], gemini]


def _nome_de_arquivo(texto: str) -> str:
    nome = re.sub(r"[^a-z0-9]+", "_", sem_acentos(texto)).strip("_")[:50] or "criacao"
    return f"{nome}_{datetime.now().strftime('%H%M%S')}"


def _rotulo(formato: str) -> str:
    return {"3d": "o modelo 3D", "video": "o vídeo"}.get(formato, "a imagem")


def _curto(valor, limite: int) -> str:
    return re.sub(r"\s+", " ", str(valor or "")).strip()[:limite] if isinstance(valor, (str, int, float)) else ""


def normalizar_roteiro(dados: dict) -> dict:
    """O roteiro do vídeo só com campos conhecidos, textos limitados e as ilustrações limpas (a que não abre sai)."""
    from video_animado import LAYOUTS, MAXIMO_DE_CENAS

    cenas = []
    for cena in (dados.get("cenas") or [])[:MAXIMO_DE_CENAS]:
        if not isinstance(cena, dict):
            continue
        itens = cena.get("itens") if isinstance(cena.get("itens"), list) else []
        limpa = {
            "layout": cena.get("layout") if cena.get("layout") in LAYOUTS else "",
            "titulo": _curto(cena.get("titulo"), 80), "texto": _curto(cena.get("texto"), 160),
            "itens": [i for i in (_curto(item, 90) for item in itens[:5]) if i],
            "narracao": _curto(cena.get("narracao"), 500),
        }
        fundo = _cor(cena.get("fundo"), "")
        if fundo:
            limpa["fundo"] = fundo
        if isinstance(cena.get("duracao"), (int, float)) and not isinstance(cena.get("duracao"), bool):
            limpa["duracao"] = round(_numero(cena["duracao"], 0, 0, 20), 2)
        if isinstance(cena.get("ilustracao"), str) and "<svg" in cena["ilustracao"]:
            try:
                limpa["ilustracao"] = limpar_svg(cena["ilustracao"])
            except ErroDeCriacao as exc:
                logger.info("Ilustração de uma cena descartada: %s", exc)
        if limpa["titulo"] or limpa["texto"] or limpa["itens"] or limpa["narracao"]:
            cenas.append(limpa)
    if not cenas:
        raise ErroDeCriacao("o roteiro veio sem nenhuma cena")
    return {"titulo": _curto(dados.get("titulo"), 80), "descricao": _curto(dados.get("descricao"), 200), "cenas": cenas}


def _pasta_das_criacoes() -> str:
    return os.path.join(PASTA_DOCUMENTOS_GERADOS, datetime.now().strftime("%Y-%m-%d") + "_criacoes")


def ultimo_roteiro_salvo() -> str:
    """O roteiro (.roteiro.json) do vídeo mais recente, em qualquer pasta de criações: o painel refaz o último vídeo com o
    estilo novo mesmo que ele tenha sido pedido pela voz (outro processo) ou antes de reiniciar."""
    import glob

    roteiros = glob.glob(os.path.join(PASTA_DOCUMENTOS_GERADOS, "*_criacoes", "*.roteiro.json"))
    return max(roteiros, key=os.path.getmtime) if roteiros else ""


class AgenteCriacao:
    """Responde aos pedidos de criar, ajustar, mostrar de novo e fechar. A criação roda numa thread (de 30 s a uns minutos):
    `mostrar(evento)` manda ao painel o começo ("criacao_iniciada"), o andamento do vídeo ("criacao_andamento"), o resultado
    ("criacao") e o fechar ("criacao_fechar"); `avisar(texto, canal, arquivos)` dá a resposta final no canal do pedido.
    `montar(roteiro, estilo, destino, ao_progredir)` grava o vídeo (video_animado.renderizar; trocável nos testes)."""

    def __init__(self, mostrar=None, avisar=None, provedores=None, pasta=None, em_segundo_plano=True, montar=None):
        self._mostrar = mostrar or (lambda evento: None)
        self._avisar = avisar or (lambda texto, canal, arquivos: None)
        self._provedores = provedores
        self._pasta = pasta
        self._em_segundo_plano = em_segundo_plano
        self._montar = montar
        self.ultima = None
        self.criando = None  # o assunto que está sendo feito agora
        self._trava = threading.Lock()

    # ------------------------------------------------------------------ os pedidos
    def responder(self, texto: str, canal: str = "voz"):
        base = sem_acentos(texto)
        if self.ultima and RE_FECHAR.search(base):
            self._mostrar({"tipo": "criacao_fechar", "texto": "fechar"})
            return f"Pronto, guardei {_rotulo(self.ultima.formato)}. A esfera voltou."
        if self.ultima and RE_DE_NOVO.search(base):
            self._mostrar(self._evento(self.ultima))
            return f"Aí está {_rotulo(self.ultima.formato)} de novo: {self.ultima.titulo}."
        reestilo = self._reestilo(base)
        if reestilo is not None:
            return self.refazer_video(canal=canal, opcoes=reestilo)
        pedido = interpretar(texto)
        if pedido is None:
            if not self._ajuste(base):
                return None
            pedido = PedidoDeCriacao(self.ultima.formato, self.ultima.assunto, None, False,
                                     dict(self.ultima.conteudo.get("opcoes") or {}) if self.ultima.formato == "video" else {})
            return self._comecar(pedido, canal, mudanca=texto.strip())
        if not pedido.assunto:
            exemplo = {"3d": "cria um modelo 3D de um foguete", "video": "faz um vídeo sobre a fotossíntese"}.get(
                pedido.formato, "cria uma imagem de um farol à noite")
            nome = {"3d": "Um modelo 3D", "video": "Um vídeo"}.get(pedido.formato, "Uma imagem")
            return f"{nome} de quê? Diga, por exemplo: \"{exemplo}\"."
        return self._comecar(pedido, canal)

    def tem_video_recente(self) -> bool:
        """Um vídeo animado feito há pouco (o agente dos apps deixa "aplica meu estilo no vídeo" para cá)."""
        return bool(self.ultima and self.ultima.formato == "video" and time.time() - self.ultima.quando <= SEGUNDOS_PARA_AJUSTAR)

    def criar_video(self, assunto: str, canal: str = "painel", opcoes: dict = None):
        """Um vídeo novo pedido pela janela [ vídeos ] ou pela tela Estúdio do app (o assunto e as opções escolhidas lá)."""
        assunto = _curto(assunto, 300)
        if not assunto:
            return "Escreva sobre o que é o vídeo."
        validas = {k: v for k, v in (opcoes or {}).items() if (k, v) in {("formato", "16x9"), ("formato", "9x16"), (
            "formato", "1x1"), ("narracao", "nenhuma"), ("legendas", "nenhuma")}}
        return self._comecar(PedidoDeCriacao("video", assunto, None, False, validas), canal)

    def _ajuste(self, base: str) -> bool:
        if not self.ultima or time.time() - self.ultima.quando > SEGUNDOS_PARA_AJUSTAR:
            return False
        if not RE_AJUSTE_VERBO.search(base):
            return False
        return bool(RE_AJUSTE_REFERENCIA.search(base) or RE_ELE_COM_QUALIDADE.search(base) or base.lstrip().startswith("agora"))

    def _reestilo(self, base: str):
        """As opções para montar o último vídeo de novo sem o Claude ("refaz o vídeo em pé", "aplica meu estilo no vídeo"),
        ou None se o pedido não for isso."""
        if not self.ultima or self.ultima.formato != "video" or not RE_REESTILO_VERBO.search(base) or not RE_O_VIDEO.search(base):
            return None
        opcoes = opcoes_do_video(base)
        opcoes.pop("segundos", None)  # a duração muda o roteiro: é com o Claude
        if opcoes or re.search(r"\bestilo\b", base):
            return opcoes
        return None

    def _comecar(self, pedido: PedidoDeCriacao, canal: str, mudanca: str = ""):
        with self._trava:
            if self.criando:
                return f"Ainda estou terminando {self.criando}; assim que ficar pronto, aparece no centro da tela."
            self.criando = pedido.assunto
        anterior = self.ultima if mudanca else None
        quem = "o Gemini" if pedido.ia == "gemini" else "o Claude"
        self._mostrar({"tipo": "criacao_iniciada", "texto": pedido.assunto, "formato": pedido.formato, "ia": quem[2:]})
        self._em_thread(self._trabalhar, pedido, canal, anterior, mudanca)
        if mudanca:
            return f"Mexendo {'n' + _rotulo(pedido.formato)} com {quem}. Já mostro."
        if pedido.formato == "3d":
            return f"Modelando {pedido.assunto} em 3D com {quem}. Leva por volta de um minuto; aparece no centro da tela."
        if pedido.formato == "video":
            return (f"Fazendo um vídeo sobre {pedido.assunto} com {quem}, no seu estilo: o roteiro leva de 1 a 3 minutos e a "
                    "montagem mais alguns segundos. Aparece no centro da tela.")
        return f"Desenhando {pedido.assunto} com {quem}. Leva por volta de um minuto; aparece no centro da tela."

    def _em_thread(self, funcao, *argumentos):
        if self._em_segundo_plano:
            threading.Thread(target=funcao, args=argumentos, name="criacao", daemon=True).start()
        else:
            funcao(*argumentos)

    def refazer_video(self, canal: str = "voz", opcoes: dict = None, roteiro: str = None):
        """Monta de novo o último vídeo (ou o roteiro salvo em `roteiro`) no estilo de agora, com as opções ditas, sem o
        Claude. Devolve a resposta imediata."""
        if roteiro is None and (not self.ultima or self.ultima.formato != "video"):
            roteiro = ultimo_roteiro_salvo()
            if not roteiro:
                return "Ainda não fiz nenhum vídeo para montar de novo. Peça um: \"faz um vídeo sobre…\"."
        if roteiro:
            try:
                with open(roteiro, encoding="utf-8") as arquivo:
                    salvo = json.load(arquivo)
                base = Criacao("video", salvo.get("assunto") or "o vídeo", salvo["roteiro"].get("titulo") or "vídeo",
                               {"roteiro": salvo["roteiro"], "opcoes": salvo.get("opcoes") or {}},
                               json.dumps(salvo["roteiro"], ensure_ascii=False), roteiro, salvo.get("ia") or "Claude")
            except (OSError, ValueError, KeyError, AttributeError):
                return "Não consegui abrir o roteiro do último vídeo."
        else:
            base = self.ultima
        with self._trava:
            if self.criando:
                return f"Ainda estou terminando {self.criando}; assim que ficar pronto, aparece no centro da tela."
            self.criando = base.assunto
        juntas = {**(base.conteudo.get("opcoes") or {}), **(opcoes or {})}
        pedido = PedidoDeCriacao("video", base.assunto, None, False, juntas)
        self._mostrar({"tipo": "criacao_iniciada", "texto": base.assunto, "formato": "video", "ia": ""})
        self._em_thread(self._remontar, pedido, base, canal)
        return "Montando o vídeo de novo, no seu estilo de agora. Leva alguns segundos."

    # ------------------------------------------------------------------ o trabalho
    def _trabalhar(self, pedido: PedidoDeCriacao, canal: str, anterior, mudanca: str):
        try:
            criacao = self._criar(pedido, anterior, mudanca)
        except Exception as exc:
            logger.warning("A criação de %s falhou: %s", pedido.assunto, exc)
            self._mostrar({"tipo": "criacao_falhou", "texto": pedido.assunto})
            self._avisar(self._texto_da_falha(exc, pedido), canal, [])
            return
        finally:
            self.criando = None
        self._entregar(criacao, pedido, canal, bool(mudanca))

    def _remontar(self, pedido: PedidoDeCriacao, base, canal: str):
        try:
            criacao = self._gravar_video(pedido, base.conteudo["roteiro"], base.ia)
        except Exception as exc:
            logger.warning("O vídeo não foi montado de novo: %s", exc)
            self._mostrar({"tipo": "criacao_falhou", "texto": pedido.assunto})
            self._avisar(self._texto_da_falha(exc, pedido), canal, [])
            return
        finally:
            self.criando = None
        self._entregar(criacao, pedido, canal, True)

    def _entregar(self, criacao, pedido: PedidoDeCriacao, canal: str, ajuste: bool):
        self.ultima = criacao
        self._mostrar(self._evento(criacao))
        # O .json da cena 3D não serve fora do painel; a imagem e o vídeo voltam pelo celular (ou "... e me manda").
        manda = criacao.formato != "3d" and (canal in ("whatsapp", "app") or pedido.mandar)
        self._avisar(self._texto_pronto(criacao, canal, ajuste, pedido.ia), canal, [criacao.arquivo] if manda else [])

    def _estilo_do_pedido(self, pedido: PedidoDeCriacao) -> dict:
        import estilo_video

        estilo = estilo_video.atual()
        estilo.update({k: v for k, v in pedido.opcoes.items() if k in ("formato", "narracao", "legendas")})
        return estilo

    def _criar(self, pedido: PedidoDeCriacao, anterior, mudanca: str) -> Criacao:
        import estilo_video

        formato = pedido.formato
        if formato == "video":
            estilo = self._estilo_do_pedido(pedido)
            sistema = INSTRUCOES_VIDEO + "\n\n" + estilo_video.bloco_do_prompt(estilo, pedido.opcoes.get("segundos"))
        else:
            sistema = INSTRUCOES_3D if formato == "3d" else INSTRUCOES_SVG
        if anterior and mudanca:
            prompt = (f"Pedido original: {anterior.assunto}\n\n{INSTRUCAO_DE_AJUSTE}\n\n{anterior.bruto}\n\n"
                      f"Mudança pedida: {mudanca}")
        else:
            prompt = {"3d": "Modele em 3D", "video": "Faça o roteiro do vídeo animado sobre"}.get(formato, "Desenhe")
            prompt = f"{prompt}: {pedido.assunto}"
        em_json = formato in ("3d", "video")
        ultimo_erro = None
        for tentativa in range(2):
            pergunta = prompt if not ultimo_erro else (
                f"{prompt}\n\nA resposta anterior não serviu ({ultimo_erro}). Responda de novo, só com o "
                f"{'JSON' if em_json else 'SVG'} completo.")
            bruto, ia = self._pedir(pergunta, sistema, em_json, pedido.ia, longo=formato == "video")
            try:
                if formato == "video":
                    conteudo = normalizar_roteiro(extrair_json(bruto))
                    titulo = conteudo["titulo"] or pedido.assunto
                elif formato == "3d":
                    conteudo = normalizar_modelo(extrair_json(bruto))
                    titulo = conteudo["titulo"] or pedido.assunto
                else:
                    conteudo = limpar_svg(bruto)
                    titulo = titulo_do_svg(conteudo) or pedido.assunto
                break
            except ErroDeCriacao as exc:
                ultimo_erro = exc
                logger.info("Criação de %s, tentativa %d: %s", pedido.assunto, tentativa + 1, exc)
        else:
            raise ultimo_erro
        if formato == "video":
            return self._gravar_video(pedido, conteudo, ia)
        arquivo = self._salvar(pedido, conteudo, formato == "3d")
        return Criacao(formato, pedido.assunto, titulo, conteudo, json.dumps(conteudo, ensure_ascii=False)
                       if formato == "3d" else conteudo, arquivo, ia)

    def _gravar_video(self, pedido: PedidoDeCriacao, roteiro: dict, ia: str) -> Criacao:
        """Monta o MP4 do roteiro no estilo (com as opções do pedido) e guarda o roteiro ao lado, para refazer depois."""
        estilo = self._estilo_do_pedido(pedido)
        pasta = self._pasta or _pasta_das_criacoes()
        os.makedirs(pasta, exist_ok=True)
        base = os.path.join(pasta, _nome_de_arquivo(pedido.assunto))
        montar = self._montar
        if montar is None:
            from video_animado import renderizar as montar
        info = montar(roteiro, estilo, base + ".mp4",
                      lambda texto: self._mostrar({"tipo": "criacao_andamento", "texto": texto, "formato": "video"}))
        with open(base + ".roteiro.json", "w", encoding="utf-8") as arquivo:
            json.dump({"assunto": pedido.assunto, "opcoes": pedido.opcoes, "ia": ia, "roteiro": roteiro}, arquivo,
                      ensure_ascii=False, indent=1)
        conteudo = {"roteiro": roteiro, "opcoes": dict(pedido.opcoes), "duracao": info.get("duracao"),
                    "cenas": len(roteiro["cenas"]), "formato": estilo["formato"]}
        return Criacao("video", pedido.assunto, roteiro.get("titulo") or pedido.assunto, conteudo,
                       json.dumps(roteiro, ensure_ascii=False), base + ".mp4", ia)

    def _pedir(self, prompt: str, sistema: str, em_json: bool, preferida: str = None, longo: bool = False):
        from llm_nuvem import NuvemIndisponivel, ordenar_por_preferencia

        provedores = list(self._provedores) if self._provedores is not None else _provedores_padrao(longo)
        recusas = []
        for provedor in ordenar_por_preferencia(provedores, preferida):
            try:
                return provedor.chat(prompt, system_prompt=sistema, formato="json" if em_json else None), provedor.nome
            except NuvemIndisponivel as exc:
                recusas.append(f"{provedor.nome}: {exc}")
                logger.info("Criação: %s não respondeu (%s).", provedor.nome, exc)
        raise RuntimeError("nenhuma IA respondeu (" + "; ".join(recusas) + ")")

    def _salvar(self, pedido: PedidoDeCriacao, conteudo, tres_d: bool) -> str:
        pasta = self._pasta or _pasta_das_criacoes()
        os.makedirs(pasta, exist_ok=True)
        caminho = os.path.join(pasta, _nome_de_arquivo(pedido.assunto) + (".json" if tres_d else ".svg"))
        with open(caminho, "w", encoding="utf-8") as arquivo:
            if tres_d:
                json.dump(conteudo, arquivo, ensure_ascii=False, indent=1)
            else:
                arquivo.write('<?xml version="1.0" encoding="UTF-8"?>\n' + conteudo)
        return caminho

    @staticmethod
    def _evento(criacao: Criacao) -> dict:
        evento = {"tipo": "criacao", "texto": criacao.titulo, "formato": criacao.formato, "titulo": criacao.titulo,
                  "arquivo": criacao.arquivo, "ia": criacao.ia}
        if criacao.formato == "video":  # o painel busca o MP4 pelo caminho (é grande demais para o evento)
            evento.update(duracao=criacao.conteudo.get("duracao"), cenas=criacao.conteudo.get("cenas"),
                          proporcao=criacao.conteudo.get("formato"))
        else:
            evento["modelo" if criacao.formato == "3d" else "svg"] = criacao.conteudo
        return evento

    @staticmethod
    def _texto_pronto(criacao: Criacao, canal: str, ajuste: bool, preferida: str = None) -> str:
        if criacao.formato == "video":
            segundos = round(criacao.conteudo.get("duracao") or 0)
            como = f"tem {segundos} segundos e {criacao.conteudo.get('cenas')} cenas"
            onde = "Está no centro do painel do PC; mandei o vídeo." if canal in ("whatsapp", "app") else "Está no centro da tela."
            texto = (f"Pronto, montei o vídeo de novo: {como}. {onde}" if ajuste
                     else f"Pronto: o vídeo “{criacao.titulo}” {como}. {onde}")
        elif canal in ("whatsapp", "app"):
            texto = f"Pronto: {criacao.titulo}. Está no centro do painel do PC"
            texto += "; mandei o arquivo." if criacao.formato == "imagem" else "."
        elif ajuste:
            texto = f"Pronto, mudei {_rotulo(criacao.formato)}."
        else:
            texto = f"Pronto: {criacao.titulo}. Está no centro da tela."
        if criacao.formato == "3d" and isinstance(criacao.conteudo, dict) and criacao.conteudo.get("descricao") and not ajuste:
            texto += " " + criacao.conteudo["descricao"].rstrip(".") + "."
        pedida = {"claude": "Claude", "gemini": "Gemini"}.get(preferida or "claude")
        if criacao.ia != pedida and not (criacao.formato == "video" and ajuste):
            texto += f" (O {pedida} não respondeu agora; quem fez foi o {criacao.ia}.)"
        return texto

    @staticmethod
    def _texto_da_falha(exc: Exception, pedido: PedidoDeCriacao) -> str:
        if isinstance(exc, RuntimeError) and "nenhuma IA" in str(exc):
            return (f"Não consegui criar {pedido.assunto}: o Claude e o Gemini não responderam agora "
                    "(limite do plano ou sem internet). Tente daqui a pouco.")
        if pedido.formato == "video" and not isinstance(exc, ErroDeCriacao):
            return f"Não consegui montar o vídeo sobre {pedido.assunto}: {exc}"
        return f"Não consegui criar {pedido.assunto}: a IA devolveu um desenho que não abre. Peça de novo, que eu tento outra vez."
