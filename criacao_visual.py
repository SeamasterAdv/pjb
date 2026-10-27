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
RE_CRIAR_3D = re.compile(r"\b" + _VERBO + r"\s+" + _PARA_MIM + _ARTIGO + _OBJ_3D + r"\b")
RE_CRIAR_IMAGEM = re.compile(r"\b" + _VERBO + r"\s+" + _PARA_MIM + r"(?P<objeto>" + _ARTIGO + r"(?P<nome>" + _OBJ_IMAGEM +
                             r"))\b")
# "faz um logo pra minha banca": o nome fica no assunto ("um logo pra minha banca"); "uma imagem de um farol" vira "um farol".
OBJETOS_GENERICOS = {"imagem", "desenho", "ilustracao", "figura", "pintura", "arte", "vetor"}
RE_DESENHA = re.compile(r"\b(?:me\s+)?desenh[ae]\s+" + _PARA_MIM + r"(?P<artigo>um|uma|o|a|uns|umas)\s+(?=\w)")
RE_EM_3D = re.compile(r"\b(?:model[ae]r?|mostr[ae]r?|quero\s+ver|cri[ae]r?|fa(?:z|ca))\b.{1,80}?\bem\s+3\s?d\b")
RE_SO_EM_3D = re.compile(r"^\s*(?:em\s+)?3\s?d\b|\s*\b(?:em|no\s+formato)\s+3\s?d\b", re.IGNORECASE)
# "faz uma imagem desse PDF" é conversão (apps da ArtCraft), e "como fazer um modelo 3D no Blender?" é pergunta.
RE_DE_ARQUIVO = re.compile(r"^\s*(?:d(?:ess|est|aquel)[ea]s?|disso|disto|da\s+tela|do\s+(?:video|pdf|arquivo|word))\b")
RE_PERGUNTA = re.compile(r"^\s*(?:como|o\s+que|qual|quais|por\s*que|onde|quanto|sera\s+que|voce\s+(?:sabe|consegue|pode))\b")
RE_INICIO_DO_ASSUNTO = re.compile(r"^\s*(?:,|:|-)?\s*(?:de|do|da|dos|das|sobre|com|mostrando|representando|que\s+mostr[ae])?\s*",
                                  re.IGNORECASE)
RE_ENVIO = re.compile(r"\s*,?\s*\be\s+(?:me\s+)?(?:manda|envia)\b.*$", re.IGNORECASE)

RE_FECHAR = re.compile(
    r"\b(?:fech[ae]r?|tir[ae]r?|escond[ae]r?|some\s+com|apag[ae]r?\s+da\s+tela)\s+(?:(?:a|o|essa|esse|com\s+[ao])\s+)?"
    r"(?:imagem|desenho|ilustracao|modelo(?:\s+3\s?d)?|3\s?d|criacao)\b"
    r"|\b(?:volt[ae]|traz(?:\s+de\s+volta)?|mostr[ae])\s+(?:(?:a|com\s+a)\s+)?(?:sua\s+)?esfera\b")
RE_DE_NOVO = re.compile(
    r"\b(?:mostr[ae]|abr[ae]|volt[ae]|traz)\s+(?:(?:a|o)\s+)?(?:ultim[ao]\s+)?(?:imagem|desenho|modelo(?:\s+3\s?d)?|3\s?d)"
    r"(?:\s+(?:que\s+voce\s+(?:fez|criou|desenhou))|\s+de\s+novo|\s+outra\s+vez)")
RE_AJUSTE_VERBO = re.compile(
    r"^\s*(?:agora\s+|e\s+)?(?:muda|mude|troca|troque|deixa|deixe|coloca|coloque|poe|ponha|bota|acrescenta|adiciona|tira|"
    r"remove|aumenta|diminui|refaz|refaca|melhora|capricha|gira|vira)\b")
RE_AJUSTE_REFERENCIA = re.compile(
    r"\b(?:imagem|desenho|ilustracao|modelo|3\s?d|nele|nela|no\s+fundo|o\s+fundo|a\s+cor|as\s+cores|criacao)\b")
RE_ELE_COM_QUALIDADE = re.compile(
    r"\b(?:ele|ela)\s+(?:mais\s+|menos\s+|bem\s+)?(?:azul|vermelh|verde|amarel|roxo|rox|rosa|pret|branc|dourad|prateado|"
    r"cinza|laranja|marrom|maior|menor|colorid|realista|fof|brilhante|escur|clar|detalhad|simples)")


@dataclass
class PedidoDeCriacao:
    formato: str  # "imagem" ou "3d"
    assunto: str
    ia: str = None  # "claude" ou "gemini", se dita na frase
    mandar: bool = False  # "... e me manda"


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


@dataclass
class Criacao:
    formato: str
    assunto: str
    titulo: str
    conteudo: object  # o SVG (texto) ou a cena 3D (dict)
    bruto: str  # o que a IA devolveu, para um ajuste depois
    arquivo: str
    ia: str
    quando: float = field(default_factory=time.time)


def _provedores_padrao() -> list:
    """O Claude da assinatura (o mesmo da conversa, com a mesma pausa por limite) e o Gemini de reserva."""
    from llm_nuvem import claude, gemini

    return [claude, gemini]


def _nome_de_arquivo(texto: str) -> str:
    nome = re.sub(r"[^a-z0-9]+", "_", sem_acentos(texto)).strip("_")[:50] or "criacao"
    return f"{nome}_{datetime.now().strftime('%H%M%S')}"


def _rotulo(formato: str) -> str:
    return "o modelo 3D" if formato == "3d" else "a imagem"


class AgenteCriacao:
    """Responde aos pedidos de criar, ajustar, mostrar de novo e fechar. A criação roda numa thread (leva de 30 s a 2 min):
    `mostrar(evento)` manda ao painel o começo ("criacao_iniciada"), o resultado ("criacao") e o fechar ("criacao_fechar");
    `avisar(texto, canal, arquivos)` dá a resposta final no canal do pedido."""

    def __init__(self, mostrar=None, avisar=None, provedores=None, pasta=None, em_segundo_plano=True):
        self._mostrar = mostrar or (lambda evento: None)
        self._avisar = avisar or (lambda texto, canal, arquivos: None)
        self._provedores = provedores
        self._pasta = pasta
        self._em_segundo_plano = em_segundo_plano
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
        pedido = interpretar(texto)
        if pedido is None:
            ajuste = self._ajuste(base)
            if not ajuste:
                return None
            pedido = PedidoDeCriacao(self.ultima.formato, self.ultima.assunto, None, False)
            return self._comecar(pedido, canal, mudanca=texto.strip())
        if not pedido.assunto:
            exemplo = "cria um modelo 3D de um foguete" if pedido.formato == "3d" else "cria uma imagem de um farol à noite"
            return f"{'Um modelo 3D' if pedido.formato == '3d' else 'Uma imagem'} de quê? Diga, por exemplo: \"{exemplo}\"."
        return self._comecar(pedido, canal)

    def _ajuste(self, base: str) -> bool:
        if not self.ultima or time.time() - self.ultima.quando > SEGUNDOS_PARA_AJUSTAR:
            return False
        if not RE_AJUSTE_VERBO.search(base):
            return False
        return bool(RE_AJUSTE_REFERENCIA.search(base) or RE_ELE_COM_QUALIDADE.search(base) or base.lstrip().startswith("agora"))

    def _comecar(self, pedido: PedidoDeCriacao, canal: str, mudanca: str = ""):
        with self._trava:
            if self.criando:
                return f"Ainda estou terminando {self.criando}; assim que ficar pronto, aparece no centro da tela."
            self.criando = pedido.assunto
        anterior = self.ultima if mudanca else None
        quem = "o Gemini" if pedido.ia == "gemini" else "o Claude"
        self._mostrar({"tipo": "criacao_iniciada", "texto": pedido.assunto, "formato": pedido.formato, "ia": quem[2:]})
        if self._em_segundo_plano:
            threading.Thread(target=self._trabalhar, args=(pedido, canal, anterior, mudanca), name="criacao", daemon=True).start()
        else:
            self._trabalhar(pedido, canal, anterior, mudanca)
        if mudanca:
            return f"Mexendo {'no modelo 3D' if pedido.formato == '3d' else 'na imagem'} com {quem}. Já mostro."
        if pedido.formato == "3d":
            return f"Modelando {pedido.assunto} em 3D com {quem}. Leva por volta de um minuto; aparece no centro da tela."
        return f"Desenhando {pedido.assunto} com {quem}. Leva por volta de um minuto; aparece no centro da tela."

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
        self.ultima = criacao
        self._mostrar(self._evento(criacao))
        manda = criacao.formato == "imagem" and (canal in ("whatsapp", "app") or pedido.mandar)  # o .json da cena não serve fora
        self._avisar(self._texto_pronto(criacao, canal, bool(mudanca), pedido.ia), canal, [criacao.arquivo] if manda else [])

    def _criar(self, pedido: PedidoDeCriacao, anterior, mudanca: str) -> Criacao:
        tres_d = pedido.formato == "3d"
        sistema = INSTRUCOES_3D if tres_d else INSTRUCOES_SVG
        if anterior and mudanca:
            prompt = (f"Pedido original: {anterior.assunto}\n\n{INSTRUCAO_DE_AJUSTE}\n\n{anterior.bruto}\n\n"
                      f"Mudança pedida: {mudanca}")
        else:
            prompt = f"{'Modele em 3D' if tres_d else 'Desenhe'}: {pedido.assunto}"
        ultimo_erro = None
        for tentativa in range(2):
            pergunta = prompt if not ultimo_erro else (
                f"{prompt}\n\nA resposta anterior não serviu ({ultimo_erro}). Responda de novo, só com o "
                f"{'JSON' if tres_d else 'SVG'} completo.")
            bruto, ia = self._pedir(pergunta, sistema, tres_d, pedido.ia)
            try:
                if tres_d:
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
        arquivo = self._salvar(pedido, conteudo, tres_d)
        return Criacao(pedido.formato, pedido.assunto, titulo, conteudo, json.dumps(conteudo, ensure_ascii=False)
                       if tres_d else conteudo, arquivo, ia)

    def _pedir(self, prompt: str, sistema: str, tres_d: bool, preferida: str = None):
        from llm_nuvem import NuvemIndisponivel, ordenar_por_preferencia

        provedores = list(self._provedores) if self._provedores is not None else _provedores_padrao()
        recusas = []
        for provedor in ordenar_por_preferencia(provedores, preferida):
            try:
                return provedor.chat(prompt, system_prompt=sistema, formato="json" if tres_d else None), provedor.nome
            except NuvemIndisponivel as exc:
                recusas.append(f"{provedor.nome}: {exc}")
                logger.info("Criação: %s não respondeu (%s).", provedor.nome, exc)
        raise RuntimeError("nenhuma IA respondeu (" + "; ".join(recusas) + ")")

    def _salvar(self, pedido: PedidoDeCriacao, conteudo, tres_d: bool) -> str:
        pasta = self._pasta or os.path.join(PASTA_DOCUMENTOS_GERADOS, datetime.now().strftime("%Y-%m-%d") + "_criacoes")
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
        evento["modelo" if criacao.formato == "3d" else "svg"] = criacao.conteudo
        return evento

    @staticmethod
    def _texto_pronto(criacao: Criacao, canal: str, ajuste: bool, preferida: str = None) -> str:
        if canal in ("whatsapp", "app"):
            texto = f"Pronto: {criacao.titulo}. Está no centro do painel do PC"
            texto += "; mandei o arquivo." if criacao.formato == "imagem" else "."
        elif ajuste:
            texto = f"Pronto, mudei {_rotulo(criacao.formato)}."
        else:
            texto = f"Pronto: {criacao.titulo}. Está no centro da tela."
        if criacao.formato == "3d" and isinstance(criacao.conteudo, dict) and criacao.conteudo.get("descricao") and not ajuste:
            texto += " " + criacao.conteudo["descricao"].rstrip(".") + "."
        pedida = {"claude": "Claude", "gemini": "Gemini"}.get(preferida or "claude")
        if criacao.ia != pedida:
            texto += f" (O {pedida} não respondeu agora; quem fez foi o {criacao.ia}.)"
        return texto

    @staticmethod
    def _texto_da_falha(exc: Exception, pedido: PedidoDeCriacao) -> str:
        if isinstance(exc, RuntimeError) and "nenhuma IA" in str(exc):
            return (f"Não consegui criar {pedido.assunto}: o Claude e o Gemini não responderam agora "
                    "(limite do plano ou sem internet). Tente daqui a pouco.")
        return f"Não consegui criar {pedido.assunto}: a IA devolveu um desenho que não abre. Peça de novo, que eu tento outra vez."
