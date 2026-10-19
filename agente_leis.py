"""Artigos de lei: lê o texto oficial (site do Planalto) e explica com o Gemini, de outro jeito se não entender.

Exemplos: "dita o artigo 5º da Constituição", "o que diz o artigo 49 do Código de Defesa do Consumidor?",
"me explica o artigo 121 do Código Penal", "do que se trata a LGPD?", "lê o inciso XI do artigo 5º",
"artigo 7 da lei 13.709". Depois: "não entendi" / "explica de outro jeito" (nova explicação, com outra
abordagem a cada vez), "continua" (segue lendo um artigo longo), "lê de novo".

O texto da lei é público, então pode ir para a nuvem: a explicação sai do Gemini (o Claude e o Ollama
ficam de reserva). Nada da memória, das notas ou do histórico vai junto.
`AgenteLeis.responder(texto)` devolve uma RespostaLei (o que ler agora e a explicação, que pode
demorar alguns segundos) ou None (o roteamento segue).
"""
import html as html_util
import logging
import random
import re
import threading
import time
from dataclasses import dataclass, field

from agenda_linguagem import preparar

logger = logging.getLogger(__name__)

PLANALTO = "https://www.planalto.gov.br/ccivil_03"
CABECALHOS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0 Safari/537.36"}
LIMITE_LEITURA = 1200  # caracteres lidos de cada vez (~1 min e meio de fala); o resto vem com "continua"
SEGUNDOS_DO_ASSUNTO = 15 * 60  # "não entendi" vale até 15 min depois da última explicação
SEGUNDOS_CACHE = 6 * 3600


@dataclass(frozen=True)
class Norma:
    chave: str
    nome: str  # como ela fala: "a Constituição Federal"
    url: str
    apelidos: str  # regex no texto sem acentos
    numero: str = ""  # "8078" (para "lei 8.078")
    ano: str = ""  # só nas leis fora do catálogo, quando a pessoa diz ("lei 14.181 de 2021")
    tipo: str = "lei"  # lei | decreto-lei


NORMAS = [
    Norma("cf", "a Constituição Federal", f"{PLANALTO}/constituicao/constituicao.htm",
          r"\bconstituic\w*(?: federal| da republica| brasileira)?\b|\bcf\b|\bcrfb\b|\bcarta magna\b"),
    Norma("cp", "o Código Penal", f"{PLANALTO}/decreto-lei/del2848compilado.htm", r"\bcodigo penal\b|\bcp\b", "2848"),
    Norma("cc", "o Código Civil", f"{PLANALTO}/leis/2002/l10406compilada.htm", r"\bcodigo civil\b|\bcc\b", "10406"),
    Norma("cpc", "o Código de Processo Civil", f"{PLANALTO}/_ato2015-2018/2015/lei/l13105.htm",
          r"\bcodigo de processo civil\b|\bcpc\b", "13105"),
    Norma("cpp", "o Código de Processo Penal", f"{PLANALTO}/decreto-lei/del3689compilado.htm",
          r"\bcodigo de processo penal\b|\bcpp\b", "3689"),
    Norma("clt", "a CLT", f"{PLANALTO}/decreto-lei/del5452.htm",
          r"\bclt\b|\bconsolidacao das leis do trabalho\b|\bleis? trabalhistas?\b", "5452"),
    Norma("cdc", "o Código de Defesa do Consumidor", f"{PLANALTO}/leis/l8078compilado.htm",
          r"\bcodigo de defesa do consumidor\b|\bcodigo do consumidor\b|\bcdc\b", "8078"),
    Norma("eca", "o Estatuto da Criança e do Adolescente", f"{PLANALTO}/leis/l8069.htm",
          r"\beca\b|\bestatuto da crianca\w*(?: e do adolescente)?\b", "8069"),
    Norma("ctb", "o Código de Trânsito Brasileiro", f"{PLANALTO}/leis/l9503compilado.htm",
          r"\bcodigo de transito\w*(?: brasileiro)?\b|\bctb\b", "9503"),
    Norma("ctn", "o Código Tributário Nacional", f"{PLANALTO}/leis/l5172compilado.htm",
          r"\bcodigo tributario\w*(?: nacional)?\b|\bctn\b", "5172"),
    Norma("lgpd", "a LGPD", f"{PLANALTO}/_ato2015-2018/2018/lei/l13709.htm",
          r"\blgpd\b|\blei geral de protecao de dados\b", "13709"),
    Norma("maria_da_penha", "a Lei Maria da Penha", f"{PLANALTO}/_ato2004-2006/2006/lei/l11340.htm",
          r"\bmaria da penha\b", "11340"),
    Norma("idoso", "o Estatuto da Pessoa Idosa", f"{PLANALTO}/leis/2003/l10.741.htm",
          r"\bestatuto do idoso\b|\bestatuto da pessoa idosa\b", "10741"),
    Norma("drogas", "a Lei de Drogas", f"{PLANALTO}/_ato2004-2006/2006/lei/l11343.htm",
          r"\blei de drogas\b|\blei antidrogas\b", "11343"),
    Norma("marco_civil", "o Marco Civil da Internet", f"{PLANALTO}/_ato2011-2014/2014/lei/l12965.htm",
          r"\bmarco civil\w*(?: da internet)?\b", "12965"),
    Norma("licitacoes", "a Lei de Licitações", f"{PLANALTO}/_ato2019-2022/2021/lei/l14133.htm",
          r"\blei de licitac\w*\b", "14133"),
    Norma("inquilinato", "a Lei do Inquilinato", f"{PLANALTO}/leis/l8245.htm", r"\blei do inquilinato\b", "8245"),
]

# Abordagens das explicações, uma diferente a cada "não entendi".
ESTRATEGIAS = [
    "de forma simples e direta, com um exemplo do dia a dia",
    "com uma comparação com uma situação comum e bem concreta, diferente do exemplo anterior",
    "contando uma historinha curta com personagens (por exemplo, a Ana e o vizinho), mostrando a regra acontecendo",
    "passo a passo, em partes bem curtas, uma ideia por frase",
    "como se explicasse para uma criança de 10 anos, com palavras bem simples",
    "respondendo, em ordem, a quem a regra vale, o que ela manda, quando vale e o que acontece se não cumprir",
]

ORDINAIS = {"primeiro": 1, "segundo": 2, "terceiro": 3, "quarto": 4, "quinto": 5, "sexto": 6, "setimo": 7,
            "oitavo": 8, "nono": 9, "decimo": 10}
RE_ARTIGO = re.compile(
    r"\b(?:artigo|art)\.?\s*(?:n(?:umero|o|º)?\.?\s*)?"
    r"(\d{1,2}\.\d{3}|\d{1,4}|" + "|".join(ORDINAIS) + r")(?:\s*[ºo°])?(?:\s*-\s*([a-z])\b)?"
)
RE_INCISO = re.compile(r"\binciso\s+([ivxlc]+|\d{1,3})\b")
RE_PARAGRAFO = re.compile(r"\bparagrafo\s+(unico|\d{1,2}|" + "|".join(ORDINAIS) + r")\b")
RE_LEI_NUMERO = re.compile(r"\b(lei|decreto(?:-lei)?|lei complementar)\s*(?:n(?:umero|o|º)?\.?\s*)?(\d{1,2}\.?\d{3})"
                           r"(?:\s*(?:/|de)\s*(\d{4}))?\b")
RE_LER = re.compile(r"\b(?:dit[ae]\w*|le|leia|ler|lendo|recit\w*|qual (?:e )?o texto|fala o texto|me fala o texto)\b")
RE_EXPLICAR = re.compile(
    r"\b(?:explic\w*|do que (?:se )?trata\w*|o que (?:diz|fala|significa|quer dizer|determina|garante)|resum\w*|"
    r"me ajuda a entender|entender|sobre o que e)\b"
)
RE_CRIAR = re.compile(r"\b(?:faz|faca|fazer|cria|crie|gera|gere|monta|monte|prepara|prepare)\s+(?:um|uma|me um|me uma)\b")
RE_REEXPLICAR = re.compile(
    r"\b(?:nao entendi|nao ficou claro|nao to entendendo|nao estou entendendo|ainda (?:nao|to sem) entend\w*|"
    r"como assim|de outr[oa] (?:jeito|forma|maneira)|de (?:um )?(?:jeito|forma|modo) diferente|"
    r"(?:explica|fala|diz)\w* (?:de novo|melhor|outra vez|diferente|mais simples|com (?:um )?exemplo)|"
    r"simplifica\w*|(?:me )?da um exemplo|tenta de novo)\b"
)
FRASES_DE_NOVO = ["Tranquilo, vou tentar de outro jeito.", "Sem problema, deixa eu explicar diferente.",
                  "Bora de novo, agora de um jeito mais fácil."]
FRASES_INICIO = ["Deixa comigo: {rotulo}.", "Bora lá, {rotulo}.", "Olha só, {rotulo}."]
RE_CONTINUAR = re.compile(r"^(?:continua\w*|pode continuar|segue|siga|le o resto|e o resto|resto|mais|"
                          r"continua lendo|termina de ler|sim|pode)\b")
RE_REPETIR = re.compile(r"\b(?:le|leia|dita|repete|repita) (?:de novo|outra vez|novamente)\b|\brepete o (?:artigo|texto)\b")
RE_ANOTACAO = re.compile(
    r"\s*\((?:Reda[çc][ãa]o dada|Inclu[íi]d[oa]|Vide|Revogad[oa]|Regulamento|Vig[êe]ncia|Produ[çc][ãa]o de efeito|"
    r"Renumerad[oa]|Acrescid[oa]|Alterad[oa]|Promulga[çc][ãa]o|Express[ãa]o)[^)]*\)", re.IGNORECASE
)
ROMANOS = [(100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]


@dataclass
class RespostaLei:
    leitura: str = ""  # o que falar agora (o texto da lei, ou uma pergunta)
    painel: str = ""  # o texto completo para mostrar
    explicar: object = None  # função sem argumentos que devolve a explicação (chama a IA)
    titulo: str = ""


@dataclass
class Assunto:
    norma: Norma
    rotulo: str  # "o artigo 49 do Código de Defesa do Consumidor"
    texto: list  # linhas do trecho (com as anotações de redação)
    pedido: str
    foco: str = ""
    explicacoes: list = field(default_factory=list)
    lido_ate: int = 0  # quantas linhas já foram lidas
    quando: float = 0.0


@dataclass
class Pendencia:
    tipo: str  # qual_lei
    dados: dict
    ate: float


# ------------------------------------------------------------------ texto da lei
def linhas_do_html(conteudo: bytes) -> list[str]:
    """Linhas de texto da página, sem o que está riscado (revogado) e sem as tags.

    O HTML do Planalto é malformado (<b><p></b>...), então vai por expressões regulares em vez de
    árvore. As páginas antigas dizem ISO-8859-1 mas são Windows-1252 (travessão, aspas).
    """
    try:
        texto = conteudo.decode("utf-8")
    except UnicodeDecodeError:
        texto = conteudo.decode("cp1252", "replace")
    texto = re.sub(r"(?is)<(strike|del)\b[^>]*>.*?</\1\s*>", " ", texto)
    texto = re.sub(r"(?is)<(script|style|head)\b.*?</\1\s*>", " ", texto)
    texto = re.sub(r"\s+", " ", texto)  # as quebras de linha do HTML caem no meio das frases
    texto = re.sub(r"(?i)<br\s*/?>|</p\s*>|</h\d\s*>|</div\s*>|</tr\s*>|</li\s*>|</table\s*>", "\n", texto)
    texto = html_util.unescape(re.sub(r"(?s)<[^>]+>", " ", texto))
    # Páginas antigas escrevem o ordinal com um "o" sobrescrito à parte: "§ 1 o" / "Art. 2 o" -> "§ 1º".
    texto = re.sub(r"((?:§|Art\.?)\s*\d+)\s+o\b", r"\1º", texto)
    return [linha for linha in (re.sub(r"\s+", " ", parte).strip() for parte in texto.split("\n")) if linha]


def _numero_do_artigo(numero: str, letra: str = "") -> str:
    numero = str(numero).replace(".", "")
    if len(numero) > 3:
        numero = f"{numero[:-3]}.{numero[-3:]}"  # 1228 -> 1.228, como no texto da lei
    return f"{numero}-{letra.upper()}" if letra else numero


def trecho_do_artigo(linhas: list[str], numero: str) -> list[str] | None:
    """As linhas do artigo (do "Art. 5º" até o próximo artigo ou título), ou None."""
    inicio = re.compile(rf"^Art\.?\s*{re.escape(numero)}(?!\d)(?!-[A-Z]\b)\s*(?:º|°|o\b|\.|-|–|—|\s)")
    proximo = re.compile(r"^(?:Art\.?\s*\d|CAP[IÍ]TULO|T[IÍ]TULO|SE[ÇC][ÃA]O\b|Se[çc][ãa]o\s+[IVXL]|SUBSE[ÇC][ÃA]O|LIVRO|"
                         r"PARTE\s+(?:GERAL|ESPECIAL)|ATO DAS DISPOSI)")
    i = next((i for i, linha in enumerate(linhas) if inicio.match(linha)), None)
    if i is None:
        return None
    fim = next((j for j in range(i + 1, len(linhas)) if proximo.match(linhas[j])), len(linhas))
    return linhas[i:min(fim, i + 400)]


def inicio_da_lei(linhas: list[str], limite: int = 3500) -> list[str]:
    """Ementa e primeiros artigos (para "do que se trata a LGPD?")."""
    i = next((i for i, linha in enumerate(linhas) if re.match(r"^(?:Art\.?\s*1\s*(?:º|°|o\b|\.))", linha)), None)
    if i is None:
        return linhas[:40]
    comeco = max(0, i - 6)  # a ementa ("Dispõe sobre...") vem logo antes do art. 1º
    saida, total = [], 0
    for linha in linhas[comeco:]:
        saida.append(linha)
        total += len(linha)
        if total > limite:
            break
    return saida


def _romano(numero: int) -> str:
    saida = ""
    for valor, letras in ROMANOS:
        while numero >= valor:
            saida, numero = saida + letras, numero - valor
    return saida


def trecho_do_inciso(linhas: list[str], inciso: str) -> list[str] | None:
    romano = (_romano(int(inciso)) if inciso.isdigit() else inciso).upper()
    i = next((i for i, linha in enumerate(linhas) if re.match(rf"^{romano}\s*[-–—]", linha)), None)
    if i is None:
        return None
    fim = next((j for j in range(i + 1, len(linhas)) if re.match(r"^(?:[IVXLC]+\s*[-–—]|§|Parágrafo único)", linhas[j])),
               len(linhas))
    return linhas[i:fim]


def sem_anotacoes(texto: str) -> str:
    return re.sub(r"\s{2,}", " ", RE_ANOTACAO.sub("", texto)).strip()


def texto_para_falar(linhas: list[str]) -> str:
    """O texto da lei como se fala: sem as notas de redação, "Art." vira "Artigo", "§ 1º" vira "Parágrafo 1º"."""
    falas = []
    for linha in linhas:
        linha = sem_anotacoes(linha)
        if not linha:
            continue
        linha = re.sub(r"^Art\.?\s*", "Artigo ", linha)
        linha = re.sub(r"§§", "Parágrafos", linha)
        linha = re.sub(r"§\s*", "Parágrafo ", linha)
        linha = re.sub(r"^([IVXLC]+)\s*[-–—]\s*", lambda m: f"Inciso {_valor_romano(m.group(1))}: ", linha)
        linha = re.sub(r"^([a-z])\)\s*", r"Alínea \1: ", linha)
        linha = re.sub(r"\bn[º°]\s*", "número ", linha)
        linha = re.sub(r"\bPena\s*[-–—]\s*", "Pena, ", linha)
        falas.append(linha if linha.endswith((".", ":", ";", "?", "!", ",")) else linha + ".")
    return " ".join(falas)


def _valor_romano(romano: str) -> int:
    valores = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    for atual, seguinte in zip(romano, romano[1:] + " "):
        valor = valores[atual]
        total += -valor if seguinte in valores and valores[seguinte] > valor else valor
    return total


class TextoDasLeis:
    """Baixa e guarda (por 6 h) as páginas do Planalto, já em linhas."""

    def __init__(self, baixar=None):
        self._baixar = baixar or self._baixar_da_internet
        self._cache: dict[str, tuple[float, list]] = {}
        self._trava = threading.Lock()

    @staticmethod
    def _baixar_da_internet(url: str) -> bytes:
        import requests

        resposta = requests.get(url, headers=CABECALHOS, timeout=20)
        resposta.raise_for_status()
        return resposta.content

    def linhas(self, url: str) -> list[str]:
        with self._trava:
            guardado = self._cache.get(url)
            if guardado and time.time() - guardado[0] < SEGUNDOS_CACHE:
                return guardado[1]
        linhas = linhas_do_html(self._baixar(url))
        with self._trava:
            self._cache[url] = (time.time(), linhas)
        return linhas


# Leis conhecidas (número, ano): o número cresce com o tempo, então dá para estimar o ano de outra lei.
# (A busca do Bing devolvia resultados genéricos para buscas automáticas; o Planalto responde 404 rápido.)
MARCOS_DAS_LEIS = [(8078, 1990), (9099, 1995), (10406, 2002), (10741, 2003), (11340, 2006), (12965, 2014),
                   (13105, 2015), (13709, 2018), (14133, 2021), (14181, 2021), (15191, 2025), (15388, 2026)]
# Pastas do Planalto para as leis a partir de 2004 (_ato2019-2022/2021/lei/l14181.htm).
PERIODOS_PLANALTO = [(2004, 2006), (2007, 2010), (2011, 2014), (2015, 2018), (2019, 2022), (2023, 2026),
                     (2027, 2030), (2031, 2034)]


def ano_estimado(numero: int) -> int:
    for (n1, a1), (n2, a2) in zip(MARCOS_DAS_LEIS, MARCOS_DAS_LEIS[1:]):
        if n1 <= numero <= n2:
            return round(a1 + (a2 - a1) * (numero - n1) / (n2 - n1))
    if numero < MARCOS_DAS_LEIS[0][0]:
        return 1990
    n, a = MARCOS_DAS_LEIS[-1]
    return a + (numero - n) // 400  # ~400 leis por ano nos últimos anos


def candidatos_planalto(numero: str, ano: str = "", tipo: str = "lei") -> list[str]:
    """Endereços prováveis da norma no Planalto, do mais para o menos provável."""
    n = int(numero)
    if tipo == "decreto":  # os antigos ficam em decreto/antigos (o 20.910/1932, da prescrição contra a Fazenda)
        return [f"{PLANALTO}/decreto/antigos/d{n}.htm", f"{PLANALTO}/decreto/d{n}.htm", f"{PLANALTO}/decreto/D{n}.htm"]
    if tipo == "decreto-lei":
        return [f"{PLANALTO}/decreto-lei/del{n}compilado.htm", f"{PLANALTO}/decreto-lei/del{n}.htm"]
    if n < 10400:  # até 2001, numa pasta só (a 8.112 é "l8112cons.htm")
        return [f"{PLANALTO}/leis/l{n}compilado.htm", f"{PLANALTO}/leis/l{n}cons.htm", f"{PLANALTO}/leis/l{n}.htm"]
    estimado = int(ano) if ano else ano_estimado(n)
    anos = [estimado] if ano else [estimado, estimado + 1, estimado - 1, estimado + 2, estimado - 2]
    urls = []
    for a in anos:
        if a <= 2003:
            urls += [f"{PLANALTO}/leis/{a}/l{n}.htm", f"{PLANALTO}/leis/{a}/l{n // 1000}.{n % 1000:03d}.htm"]
        else:
            periodo = next((f"_ato{i}-{f}" for i, f in PERIODOS_PLANALTO if i <= a <= f), None)
            if periodo:
                urls.append(f"{PLANALTO}/{periodo}/{a}/lei/l{n}.htm")
    return urls


# ---------------------------------------------------------------- pedido falado
@dataclass
class PedidoLei:
    norma: Norma | None
    artigo: str = ""  # "5", "1.228", "121-A"; vazio = a lei inteira
    inciso: str = ""
    paragrafo: str = ""
    ler: bool = False
    explicar: bool = False


def _norma_pelo_numero(lei_numero: re.Match, normas=NORMAS) -> Norma:
    """A norma de "lei 13.709" (do catálogo, se estiver nele; senão, uma achada pelo endereço provável)."""
    numero = lei_numero.group(2).replace(".", "")
    ano = lei_numero.group(3) or ""
    palavra = lei_numero.group(1)
    tipo = "decreto-lei" if "lei" in palavra and palavra.startswith("decreto") else "decreto" if palavra.startswith("decreto") else "lei"
    nome = f"{ {'decreto-lei': 'o Decreto-Lei', 'decreto': 'o Decreto'}.get(tipo, 'a Lei') } {_numero_do_artigo(numero)}"
    return next((n for n in normas if n.numero == numero), None) or Norma(
        f"{tipo}_{numero}", nome + (f", de {ano}" if ano else ""), "", "", numero, ano, tipo)


def norma_por_texto(texto: str, normas=NORMAS) -> Norma | None:
    """A norma citada num texto qualquer ("CPC", "CF/88", "Lei 8.112/90"), ou None."""
    _, t = preparar(texto or "")
    norma = next((n for n in normas if re.search(n.apelidos, t)), None)
    if norma:
        return norma
    achado = RE_LEI_NUMERO.search(t)
    return _norma_pelo_numero(achado, normas) if achado else None


def texto_oficial_citado(frase: str, textos=None) -> tuple[str, str] | None:
    """('o artigo 500 do Código Civil', texto oficial) quando a frase cita um artigo de uma lei achável; senão None.

    Usado pela ficha de estudo ("faz um resumo do artigo 500 do Código Civil"), para partir da lei de verdade.
    """
    _, t = preparar(frase or "")
    artigo, norma = RE_ARTIGO.search(t), norma_por_texto(frase)
    if not (artigo and norma):
        return None
    numero = _numero_do_artigo(ORDINAIS.get(artigo.group(1), artigo.group(1)), artigo.group(2) or "")
    agente = AgenteLeis(textos=textos or TextoDasLeis(), explicador=lambda *_: "")
    trecho = trecho_do_artigo(agente._linhas(norma), numero)
    if not trecho:
        return None
    return f"o artigo {numero} {_de(norma.nome)}", "\n".join(sem_anotacoes(linha) for linha in trecho)


def interpretar(texto: str, normas=NORMAS) -> PedidoLei | None:
    """O pedido sobre uma lei (texto já sem acentos), ou None se não for isso."""
    if RE_CRIAR.search(texto):
        return None  # "faz um PDF sobre o artigo 5": fica com os agentes
    artigo = RE_ARTIGO.search(texto)
    norma = next((n for n in normas if re.search(n.apelidos, texto)), None)
    lei_numero = RE_LEI_NUMERO.search(texto)
    if norma is None and lei_numero:
        norma = _norma_pelo_numero(lei_numero, normas)
    if not artigo and norma is None:
        return None
    if not artigo and not (RE_LER.search(texto) or RE_EXPLICAR.search(texto)):
        return None  # cita a lei de passagem ("a CLT é antiga")
    pedido = PedidoLei(norma=norma)
    if artigo:
        bruto = artigo.group(1)
        pedido.artigo = _numero_do_artigo(ORDINAIS.get(bruto, bruto), artigo.group(2) or "")
    inciso, paragrafo = RE_INCISO.search(texto), RE_PARAGRAFO.search(texto)
    pedido.inciso = inciso.group(1) if inciso else ""
    if paragrafo:
        valor = paragrafo.group(1)
        pedido.paragrafo = "único" if valor == "unico" else str(ORDINAIS.get(valor, valor))
    # "O que diz o artigo 49?" pede o texto; "o que diz a LGPD?" (a lei inteira) pede só a explicação.
    pedido.ler = bool(RE_LER.search(texto)) or (bool(artigo) and bool(re.search(r"\bo que (?:diz|fala)\b", texto)))
    pedido.explicar = bool(RE_EXPLICAR.search(texto)) or not pedido.ler
    if pedido.ler and not RE_EXPLICAR.search(texto):
        pedido.explicar = True  # "dita o artigo": lê e, depois, explica
    return pedido


# ------------------------------------------------------------------- o agente
class AgenteLeis:
    def __init__(self, textos: TextoDasLeis | None = None, explicador=None, candidatos=candidatos_planalto,
                 relogio=time.time):
        self.textos = textos or TextoDasLeis()
        self.explicador = explicador or explicar_com_ia  # função(prompt, contexto) -> texto
        self.candidatos = candidatos  # endereços prováveis das leis fora do catálogo
        self.relogio = relogio
        self.assunto: Assunto | None = None
        self.pendencia: Pendencia | None = None
        # Outra conversa depois da explicação: "não entendi" passa a ser sobre ela, não sobre a lei.
        self._interrompido = False
        self._trava = threading.RLock()

    def e_seguimento(self, texto: str) -> bool:
        """'Não entendi', 'continua'... sobre o artigo de agora (para aceitar sem o nome no meio da conversa)."""
        if not self._assunto_ativo():
            return False
        _, t = preparar(texto or "")
        t = re.sub(r"^(?:beatriz|beatris|biatriz)\s*[,!.]?\s*", "", t).strip()
        falta_ler = self.assunto.lido_ate < len(self.assunto.texto)
        return bool(RE_REEXPLICAR.search(t) or (falta_ler and RE_CONTINUAR.match(t)) or RE_REPETIR.search(t))

    def responder(self, texto: str) -> RespostaLei | None:
        if not (texto or "").strip():
            return None
        with self._trava:
            try:
                resposta = self._responder(texto)
                self._interrompido = resposta is None
                return resposta
            except Exception as exc:
                logger.exception("Falha no agente de leis")
                return RespostaLei(leitura=f"Não consegui pegar o texto da lei agora: {exc}")

    def _assunto_ativo(self) -> bool:
        return (bool(self.assunto) and not self._interrompido
                and self.relogio() - self.assunto.quando < SEGUNDOS_DO_ASSUNTO)

    def _responder(self, texto: str) -> RespostaLei | None:
        _, t = preparar(texto)
        t = re.sub(r"^(?:beatriz|beatris|biatriz)\s*[,!.]?\s*", "", t).strip()
        if not t:
            return None

        if self.pendencia and self.pendencia.ate > self.relogio():
            pendencia, self.pendencia = self.pendencia, None
            if pendencia.tipo == "qual_lei":
                norma = next((n for n in NORMAS if re.search(n.apelidos, t)), None)
                if norma:
                    return self._atender(PedidoLei(norma=norma, **pendencia.dados))
        self.pendencia = None

        pedido = interpretar(t)
        if pedido:
            if pedido.norma is None:  # "explica o artigo 5": de qual lei?
                if self._assunto_ativo():
                    pedido.norma = self.assunto.norma
                else:
                    self.pendencia = Pendencia("qual_lei", {k: v for k, v in vars(pedido).items() if k != "norma"},
                                               self.relogio() + 120)
                    return RespostaLei(leitura=f"O artigo {pedido.artigo} de qual lei? Da Constituição, do Código "
                                               "Penal, do Código Civil, da CLT...?")
            return self._atender(pedido)

        if self._assunto_ativo():
            if RE_REPETIR.search(t):
                self.assunto.lido_ate = 0
                return self._ler(self.assunto)
            if RE_CONTINUAR.match(t) and self.assunto.lido_ate < len(self.assunto.texto):
                return self._ler(self.assunto)
            if RE_REEXPLICAR.search(t):
                return self._reexplicar(texto)
        return None

    # ------------------------------------------------------------- atender
    def _linhas(self, norma: Norma) -> list[str]:
        if norma.url:
            return self.textos.linhas(norma.url)
        for url in self.candidatos(norma.numero, norma.ano, norma.tipo):
            try:
                return self.textos.linhas(url)
            except Exception as exc:  # 404: tenta o próximo endereço provável
                logger.debug("Lei fora do endereço %s: %s", url, exc)
        raise LookupError(f"não achei {norma.nome} no site do Planalto")

    def _atender(self, pedido: PedidoLei) -> RespostaLei:
        norma = pedido.norma
        linhas = self._linhas(norma)
        if pedido.artigo:
            trecho = trecho_do_artigo(linhas, pedido.artigo)
            if not trecho:
                return RespostaLei(leitura=f"Não achei o artigo {pedido.artigo} {_em(norma.nome)}. Confere o número pra mim?")
            rotulo = f"o artigo {pedido.artigo} {_de(norma.nome)}"
            foco = ""
            if pedido.inciso:
                inciso = trecho_do_inciso(trecho, pedido.inciso)
                if inciso:
                    rotulo = f"o inciso {pedido.inciso.upper()} do artigo {pedido.artigo} {_de(norma.nome)}"
                    foco = f"A pessoa perguntou especificamente do inciso {pedido.inciso.upper()}."
                    trecho = [trecho[0]] + inciso  # o começo do artigo dá o contexto
            elif pedido.paragrafo:
                foco = f"A pessoa perguntou especificamente do parágrafo {pedido.paragrafo}."
        else:
            trecho = inicio_da_lei(linhas)
            rotulo = norma.nome
            foco = "A pessoa quer saber do que trata a lei como um todo: explique o objetivo e as ideias principais."
        self.assunto = Assunto(norma=norma, rotulo=rotulo, texto=trecho, pedido=pedido, foco=foco, quando=self.relogio())

        if pedido.ler:
            resposta = self._ler(self.assunto)
            if pedido.explicar:
                resposta.explicar = lambda assunto=self.assunto: self._explicar(assunto)
            return resposta
        painel = "\n".join(trecho)
        return RespostaLei(leitura=random.choice(FRASES_INICIO).format(rotulo=rotulo),  # enquanto a IA escreve
                           painel=f"{_maiuscula(rotulo)} (texto oficial do Planalto):\n{painel}",
                           explicar=lambda assunto=self.assunto: self._explicar(assunto), titulo=rotulo)

    def _ler(self, assunto: Assunto) -> RespostaLei:
        """O próximo pedaço do texto (até LIMITE_LEITURA caracteres, sem cortar uma linha)."""
        inicio = assunto.lido_ate
        fim, total = inicio, 0
        while fim < len(assunto.texto):
            tamanho = len(sem_anotacoes(assunto.texto[fim]))
            if fim > inicio and total + tamanho > LIMITE_LEITURA:
                break
            total += tamanho
            fim += 1
        assunto.lido_ate = fim
        assunto.quando = self.relogio()
        fala = texto_para_falar(assunto.texto[inicio:fim])
        if inicio == 0:
            fala = f"{_maiuscula(assunto.rotulo)}, segundo o texto oficial: {fala}"
        if fim < len(assunto.texto):
            fala += " Tem mais texto, que está todo no painel. Diga continua que eu sigo lendo."
        painel = "\n".join(assunto.texto) if inicio == 0 else "\n".join(assunto.texto[inicio:fim])
        return RespostaLei(leitura=fala, painel=painel, titulo=assunto.rotulo)

    # ------------------------------------------------------------ explicar
    def _prompt(self, assunto: Assunto, estrategia: str, pedido_agora: str = "") -> str:
        texto = "\n".join(sem_anotacoes(linha) for linha in assunto.texto)
        partes = [f"Texto oficial de {assunto.rotulo} (fonte: site do Planalto):\n{texto[:12000]}"]
        if assunto.foco:
            partes.append(assunto.foco)
        if assunto.explicacoes:
            anteriores = "\n".join(f"- {e}" for e in assunto.explicacoes[-3:])
            partes.append(f"Você já explicou assim e a pessoa não entendeu:\n{anteriores}\n"
                          "Não repita essas frases nem esses exemplos.")
        if pedido_agora:
            partes.append(f'O que a pessoa disse agora: "{pedido_agora}"')
        partes.append(
            f"Explique {estrategia}. Use de 4 a 7 frases curtas, para serem faladas em voz alta: sem markdown, sem "
            "listas e sem juridiquês (se usar um termo técnico, explique). Diga o que a regra determina, para que "
            "serve e como isso aparece na vida real. Baseie-se só no texto acima; se ele não responder algo, diga "
            "que o texto não fala disso. Não leia o texto de novo, ele já foi lido."
        )
        return "\n\n".join(partes)

    def _explicar(self, assunto: Assunto, pedido_agora: str = "") -> str:
        estrategia = ESTRATEGIAS[len(assunto.explicacoes) % len(ESTRATEGIAS)]
        explicacao = self.explicador(self._prompt(assunto, estrategia, pedido_agora), CONTEXTO_EXPLICACAO).strip()
        with self._trava:
            assunto.explicacoes.append(explicacao)
            assunto.quando = self.relogio()
        return explicacao

    def _reexplicar(self, texto: str) -> RespostaLei:
        assunto = self.assunto
        aviso = random.choice(FRASES_DE_NOVO) if assunto.explicacoes else ""
        return RespostaLei(leitura=aviso, titulo=assunto.rotulo,
                           explicar=lambda: self._explicar(assunto, pedido_agora=texto))


CONTEXTO_EXPLICACAO = (
    "Você está explicando um trecho de lei brasileira para alguém que não é da área, numa conversa falada, "
    "com o seu jeito leve e acolhedor. Seja fiel ao texto: não invente prazos, penas ou exceções."
)


def explicar_com_ia(prompt: str, contexto: str) -> str:
    """Gemini primeiro (o texto da lei é público); sem ele, o Claude e, por último, o Ollama local."""
    from llm_nuvem import responder_na_nuvem

    registro = []
    texto = responder_na_nuvem(prompt, contexto=contexto, preferir="gemini", registro=registro)
    if texto:
        if registro and registro[0].lower() != "gemini":
            logger.info("Gemini indisponível; a explicação da lei veio de %s.", registro[0])
        return texto
    from llm_clients import llm_clients

    return llm_clients.chat_beatriz(prompt, contexto=contexto, usar_historico=False)


def _de(nome: str) -> str:
    """'o Código Penal' -> 'do Código Penal'; 'a CLT' -> 'da CLT'."""
    if nome.startswith("o "):
        return "do " + nome[2:]
    if nome.startswith("a "):
        return "da " + nome[2:]
    return "de " + nome


def _em(nome: str) -> str:
    """'o Código Penal' -> 'no Código Penal'; 'a Lei 14.181' -> 'na Lei 14.181'."""
    if nome.startswith("o "):
        return "no " + nome[2:]
    if nome.startswith("a "):
        return "na " + nome[2:]
    return "em " + nome


def _maiuscula(texto: str) -> str:
    return texto[:1].upper() + texto[1:]
