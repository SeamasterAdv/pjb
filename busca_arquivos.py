"""Achar um arquivo pelo que a pessoa diz, sem o nome exato: "abre o PDF de embargos de terceiro", "imprime a aula dois de
ato administrativo", "manda o slide de comoriência".

- O nome vira palavras sem acento: "AULA 2 ATO ADMINISTRATIVO (1).pptx" -> aula 2 ato administrativo (o "(1)" de cópia sai);
  "TrabalhoFinal" -> trabalho final; números romanos viram algarismos ("pessoa natural VII", "unidade I").
- No pedido, número por extenso vira algarismo quando acompanha uma aula, unidade, capítulo... ("aula dois", "segunda aula").
- Cada palavra do pedido ganha a nota do melhor pedaço do nome: igual 1,0; começo de palavra 0,9 ("adm" e "administrativo");
  plural 0,85 ("atos" e "ato"); mesma raiz 0,8 ("comoriencia" e "comorientes"); dentro do nome colado 0,8; parecida 0,75 (erro
  de digitação ou de transcrição: "execucao" e "exeucao"). O nome das pastas vale 0,7 da nota.
- Palavras que não aparecem em nenhum arquivo ("o PDF que o professor mandou") saem da conta: não ajudam a escolher. Entre
  as que sobram, as raras pesam mais que as comuns ("embargos" pesa mais que "aula").
- Número dito que não bate com o número do nome derruba a nota: "aula 2" não é a "aula 3".

`IndiceDeArquivos` guarda os arquivos das pastas da Beatriz (fontes, arquivos guardados, recebidos, gerados) e das pastas do
Windows (Área de Trabalho, Documentos, Downloads), refeito a cada 2 min ou quando a busca não acha. Programas e scripts
nunca entram (nada que se abra por voz roda um programa).
"""
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

import siglas
from fontes import EXTENSOES_BLOQUEADAS

logger = logging.getLogger(__name__)

NOTA_MINIMA = 0.5
FOLGA_DE_EMPATE = 0.08  # duas candidatas mais perto que isto: perguntar qual
VALIDADE_DO_INDICE = 120.0
LIMITE_DE_ARQUIVOS = 30000
PESO_DA_PASTA = 0.7
ACERTO = 0.7  # nota a partir da qual a palavra "achou" o arquivo
GENERICA = 5  # palavra que acha mais arquivos que isto não basta sozinha quando outra do pedido não achou nada

DIRETORIOS_IGNORADOS = {"appdata", "node_modules", "__pycache__", "site-packages", "windows", "program files",
                        "program files (x86)", "programdata", "system volume information", "$recycle.bin", "venv", ".venv",
                        ".venv312", ".git", "chroma", "banco_memoria", "beatriz_impressao", ".obsidian", ".trash"}
EXTENSOES_IGNORADAS = EXTENSOES_BLOQUEADAS | {".json", ".jsonl", ".ini", ".db", ".sqlite", ".sqlite3", ".log", ".tmp",
                                             ".crdownload", ".part", ".lock", ".url", ".sys"}

NUMEROS_POR_EXTENSO = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
                       "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "quatorze": 14, "catorze": 14,
                       "quinze": 15, "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20}
ORDINAIS = {f"{raiz}{fim}": n for n, raiz in enumerate(("primeir", "segund", "terceir", "quart", "quint", "sext", "setim",
                                                        "oitav", "non", "decim"), start=1) for fim in "ao"}
# Coisas que têm número: "aula dois", "segunda unidade", "capítulo 3".
NUMERADOS = {"aula", "aulas", "atividade", "atividades", "unidade", "unidades", "capitulo", "parte", "encontro", "modulo",
             "semana", "lista", "prova", "exercicio", "exercicios", "questao", "licao", "volume", "tomo", "livro", "episodio",
             "semestre", "periodo", "bimestre", "trimestre", "etapa", "fase", "simulado", "avaliacao", "tema", "topico",
             "versao", "edicao", "turma", "av", "ap", "numero", "n"}
# Palavras de ligação que somem do nome do arquivo (no pedido, as de VAZIAS também).
LIGACAO = set(siglas.STOP) | {"e", "ou", "à", "ao", "aos", "pelo", "pela"}
VAZIAS = LIGACAO | set("""
o a os as um uma uns umas de do da dos das no na nos nas em pra pro para por com sem sobre que e ou eu me mim meu minha meus
minhas seu sua voce vc ele ela esse essa este esta isso aquele aquela aquilo ai la aqui ali favor ultimo ultima mais
recente novo nova antigo antiga velho velha todo toda tudo arquivo arquivos documento documentos doc pdf pdfs word docx
planilha planilhas excel xlsx apresentacao apresentacoes slide slides powerpoint ppt pptx foto fotos imagem imagens figura
figuras texto abre abra abrir abri mostra mostre mostrar exibe exibir acha ache achar encontra encontre encontrar procura
procure procurar localiza localize manda mande mandar envia envie enviar imprime imprima imprimir le ler leia ver veja
quero queria preciso pode podia tem tinha tenho fala falando falava chama chamado chamada nome onde fica ficou cade
pasta pastas fiz fez criou gerou preparou montou baixei baixado baixada salvei recebi recebido mandou mandaram enviou
passou beatriz agora hoje ontem so somente apenas professor professora prof faculdade colega colegas
grupo sala passada passado comentou disse falou pediu entregar entreguei estudei estudando aberto aberta
""".split())

RE_COPIA = re.compile(r"(?:\s*[(\[]\s*\d{1,3}\s*[)\]])+|\s*-\s*c[oó]pia\b.*$|\bcopy\b", re.IGNORECASE)
RE_CAMEL = re.compile(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Ý])")
RE_ROMANO = re.compile(r"x{0,3}(?:ix|iv|v?i{0,3})")
VALOR_ROMANO = {"i": 1, "v": 5, "x": 10}


def _romano(texto: str) -> int:
    total = 0
    for i, letra in enumerate(texto):
        valor = VALOR_ROMANO[letra]
        total += -valor if i + 1 < len(texto) and VALOR_ROMANO[texto[i + 1]] > valor else valor
    return total


def palavras_do_nome(nome: str) -> list[str]:
    """"AULA 02 - Embargos à exeução (1).pdf" -> ["aula", "2", "embargos", "exeucao"]."""
    base, extensao = os.path.splitext(nome)
    if not extensao or len(extensao) > 6 or " " in extensao:
        base = nome
    base = RE_COPIA.sub(" ", RE_CAMEL.sub(" ", base))
    tokens = re.findall(r"[a-z]+|\d+", siglas.sem_acento(base))
    resultado = []
    for i, token in enumerate(tokens):
        if token.isdigit():
            token = str(int(token)) if len(token) <= 6 else token
        elif RE_ROMANO.fullmatch(token) and (len(token) >= 2 or (i and tokens[i - 1] in NUMERADOS)):
            token = str(_romano(token))
        if token not in LIGACAO:
            resultado.append(token)
    return resultado


def palavras_do_pedido(texto: str, vazias=()) -> list[str]:
    """As palavras que descrevem o arquivo, sem acento: "abre a aula dois de ato administrativo" -> aula 2 ato administrativo.
    `vazias` são palavras a mais que quem chama quer tirar."""
    tokens = re.findall(r"[a-z]+|\d+", siglas.sem_acento(texto))
    convertidos = []
    for i, token in enumerate(tokens):
        antes = next((t for t in reversed(tokens[:i]) if t not in {"de", "numero", "n"}), "")
        depois = tokens[i + 1] if i + 1 < len(tokens) else ""
        if token in NUMEROS_POR_EXTENSO and antes in NUMERADOS:  # "aula dois" (mas "duas aulas" é quantidade)
            token = str(NUMEROS_POR_EXTENSO[token])
        elif token in ORDINAIS and (antes in NUMERADOS or depois in NUMERADOS):  # "segunda aula", "aula segunda"
            token = str(ORDINAIS[token])
        elif token.isdigit() and len(token) <= 6:
            token = str(int(token))
        convertidos.append(token)
    # Número por extenso que sobrou fica como palavra ("embargos de terceiro"); se nenhum arquivo a tiver, a busca a ignora.
    extras = set(vazias) - set(NUMEROS_POR_EXTENSO) - set(ORDINAIS)
    return [t for t in convertidos if t.isdigit() or (t not in VAZIAS and t not in extras and len(t) >= 2)]


@dataclass
class Arquivo:
    caminho: str
    nome: str
    palavras: list[str]
    pastas: list[str] = field(default_factory=list)  # palavras das pastas entre a raiz e o arquivo
    modificado: float = 0.0
    lugar: str = ""  # onde fica, para falar ("Downloads", "Obsidian › referencias")

    @property
    def junto(self) -> str:
        return "".join(self.palavras)

    @property
    def titulo(self) -> str:
        return os.path.splitext(self.nome)[0]


def arquivo_de(caminho: str, raiz: str = "", lugar: str = "") -> Arquivo:
    relativo = os.path.relpath(os.path.dirname(caminho), raiz) if raiz else ""
    pastas = [] if relativo in ("", ".") else palavras_do_nome(relativo.replace(os.sep, " "))
    try:
        modificado = os.path.getmtime(caminho)
    except OSError:
        modificado = 0.0
    nome = os.path.basename(caminho)
    return Arquivo(caminho, nome, palavras_do_nome(nome), pastas, modificado, lugar or os.path.basename(os.path.dirname(caminho)))


# ------------------------------------------------------------------ notas
def nota_da_palavra(q: str, t: str) -> float:
    """O quanto a palavra `q` do pedido bate com a palavra `t` do nome (0 a 1)."""
    if q == t:
        return 1.0
    if q.isdigit() or t.isdigit():
        return 0.0
    if len(q) >= 3 and t.startswith(q):
        return 0.9
    if len(t) >= 3 and q.startswith(t) and len(q) - len(t) <= 3:
        return 0.85
    comum = len(os.path.commonprefix([q, t]))
    if comum >= 5 and comum >= 0.7 * min(len(q), len(t)):
        return 0.8
    if min(len(q), len(t)) >= 4 and fuzz.ratio(q, t) >= 85:
        return 0.75
    return 0.0


def _notas_no_vocabulario(q: str, vocabulario: list[str]) -> dict[str, float]:
    """{palavra do vocabulário: nota} só com as que batem; o parecido vem do rapidfuzz (rápido mesmo com milhares)."""
    if q.isdigit():
        return {q: 1.0} if q in vocabulario else {}
    notas = {}
    for t in vocabulario:
        if t[:1] == q[:1]:  # começo, plural e raiz pedem a mesma primeira letra
            nota = nota_da_palavra(q, t)
            if nota:
                notas[t] = nota
    if len(q) >= 4:
        for t, _, _ in process.extract(q, vocabulario, scorer=fuzz.ratio, score_cutoff=85, limit=None):
            if len(t) >= 4 and not t.isdigit():
                notas.setdefault(t, 0.75)
    return notas


def _nota_no_arquivo(q: str, arquivo: Arquivo, notas_q: dict) -> float:
    no_nome = max((notas_q.get(t, 0.0) for t in arquivo.palavras), default=0.0)
    if no_nome < 0.8 and len(q) >= 4 and not q.isdigit() and q in arquivo.junto:
        no_nome = 0.8  # "oab" dentro de "chamadaoab"
    na_pasta = max((notas_q.get(t, 0.0) for t in arquivo.pastas), default=0.0) * PESO_DA_PASTA
    return max(no_nome, na_pasta)


def ordenar(termos: list[str], arquivos: list[Arquivo], minimo: float = NOTA_MINIMA) -> list[tuple[float, Arquivo]]:
    """[(nota, arquivo)] dos que batem com o pedido, do melhor para o pior (no empate, o mais novo primeiro)."""
    if not termos or not arquivos:
        return []
    vocabulario = sorted({t for a in arquivos for t in (*a.palavras, *a.pastas)})
    melhores: dict[str, tuple[float, Arquivo]] = {}
    for interpretacao in siglas.interpretacoes(termos) or [termos]:
        tabelas = {q: _notas_no_vocabulario(q, vocabulario) for q in set(interpretacao)}
        notas = [[_nota_no_arquivo(q, a, tabelas[q]) for q in interpretacao] for a in arquivos]
        # Número dito sempre conta (para "aula 9" não virar "qualquer aula"); palavra que nenhum arquivo tem pesa pouco.
        uteis = [i for i, q in enumerate(interpretacao) if q.isdigit() or any(n[i] >= ACERTO for n in notas)]
        palavras_uteis = [i for i in uteis if not interpretacao[i].isdigit()]
        if not palavras_uteis:
            continue
        achados_por_termo = {i: sum(n[i] >= ACERTO for n in notas) for i in uteis}
        desconhecidas = len(interpretacao) - len(uteis)
        if desconhecidas and all(achados_por_termo[i] > GENERICA for i in palavras_uteis):
            continue  # "a aula de culinária": sem culinária, sobra só "aula", que é qualquer aula
        pesos = {i: 1 + math.log(len(arquivos) / max(1, achados_por_termo[i])) for i in uteis}
        total = sum(pesos.values()) + desconhecidas * 0.5 * min(pesos.values())
        acertadas = {t for i in uteis for t, nota in tabelas[interpretacao[i]].items() if nota >= 0.75}
        numeros = {interpretacao[i] for i in uteis if interpretacao[i].isdigit()}
        for arquivo, notas_do_arquivo in zip(arquivos, notas):
            cobertura = sum(pesos[i] * notas_do_arquivo[i] for i in uteis) / total
            if cobertura <= 0:
                continue
            precisao = sum(t in acertadas for t in arquivo.palavras) / max(1, len(arquivo.palavras))
            nota = 0.85 * cobertura + 0.15 * precisao
            numeros_do_nome = {t for t in arquivo.palavras if t.isdigit()}
            if numeros and numeros_do_nome and not numeros & (numeros_do_nome | set(arquivo.pastas)):
                nota *= 0.55  # "aula 2" e o arquivo é a "aula 3"
            if nota >= minimo and nota > melhores.get(arquivo.caminho, (0.0,))[0]:
                melhores[arquivo.caminho] = (nota, arquivo)
    return sorted(melhores.values(), key=lambda item: (-round(item[0], 3), -item[1].modificado))


def empatados(achados: list[tuple[float, Arquivo]], folga: float = FOLGA_DE_EMPATE, maximo: int = 4) -> list[Arquivo]:
    """Os que estão perto do melhor (um só = está claro qual é)."""
    if not achados:
        return []
    melhor, vistos, resultado = achados[0][0], set(), []
    for nota, arquivo in achados:
        # Cópias na mesma pasta ("aula 4.pptx" e "aula 4 (1).pptx") contam como uma: fica a mais nova (vem antes).
        chave = (tuple(arquivo.palavras), os.path.splitext(arquivo.nome)[1].lower(), os.path.dirname(arquivo.caminho))
        if melhor - nota < folga and chave not in vistos:
            vistos.add(chave)
            resultado.append(arquivo)
    return resultado[:maximo]


# ------------------------------------------------------------------ índice
def raizes_padrao() -> list[tuple[str, int, str]]:
    """(pasta, profundidade, nome para falar): as pastas da Beatriz e as do Windows onde as pessoas guardam arquivos."""
    from agente_pastas import pasta_do_windows
    from config import CAMINHO_DOCUMENTOS, PASTA_ARQUIVADOS, PASTA_DOCUMENTOS_GERADOS, PASTA_RECEBIDOS, VAULT_OBSIDIAN

    return [(CAMINHO_DOCUMENTOS, 5, "Documentos da biblioteca"), (VAULT_OBSIDIAN, 5, "Obsidian"),
            (PASTA_ARQUIVADOS, 5, "Arquivos guardados"), (PASTA_RECEBIDOS, 3, "Recebidos"),
            (PASTA_DOCUMENTOS_GERADOS, 3, "Documentos gerados"), (pasta_do_windows("desktop"), 3, "Área de Trabalho"),
            (pasta_do_windows("documentos"), 4, "Documentos"), (pasta_do_windows("downloads"), 2, "Downloads")]


def arquivo_ignorado(nome: str) -> bool:
    baixo = nome.lower()
    return (baixo.startswith(("~$", ".")) or os.path.splitext(baixo)[1] in EXTENSOES_IGNORADAS
            or baixo in {"desktop.ini", "thumbs.db"})


class IndiceDeArquivos:
    def __init__(self, raizes=None, relogio=time.time, limite: int = LIMITE_DE_ARQUIVOS):
        self._raizes_fn = raizes  # None = raizes_padrao(), lida a cada varredura
        self.relogio = relogio
        self.limite = limite
        self.arquivos: list[Arquivo] = []
        self.feito_em = 0.0

    def atualizar(self):
        achados: dict[str, Arquivo] = {}
        for raiz, profundidade, rotulo in (self._raizes_fn or raizes_padrao)():
            if not os.path.isdir(raiz):
                continue
            pilha = [(raiz, 0)]
            while pilha and len(achados) < self.limite:
                atual, nivel = pilha.pop()
                try:
                    entradas = list(os.scandir(atual))
                except OSError:
                    continue
                for entrada in entradas:
                    try:
                        if entrada.is_dir(follow_symlinks=False):
                            if (nivel + 1 < profundidade and not entrada.name.startswith((".", "$"))
                                    and entrada.name.lower() not in DIRETORIOS_IGNORADOS and not os.path.isjunction(entrada.path)):
                                pilha.append((entrada.path, nivel + 1))
                            continue
                        if not entrada.is_file() or arquivo_ignorado(entrada.name):
                            continue
                        chave = os.path.normcase(os.path.abspath(entrada.path))
                        if chave in achados:
                            continue
                        relativo = os.path.relpath(atual, raiz)
                        lugar = rotulo if relativo == "." else f"{rotulo} › {relativo.replace(os.sep, ' › ')}"
                        pastas = [] if relativo == "." else palavras_do_nome(relativo.replace(os.sep, " "))
                        achados[chave] = Arquivo(os.path.abspath(entrada.path), entrada.name, palavras_do_nome(entrada.name),
                                                 pastas, entrada.stat().st_mtime, lugar)
                    except OSError:
                        continue
        self.arquivos, self.feito_em = list(achados.values()), self.relogio()

    def garantir(self, forcar: bool = False):
        if forcar or not self.feito_em or self.relogio() - self.feito_em > VALIDADE_DO_INDICE:
            self.atualizar()

    def buscar(self, termos: list[str], extensoes=None, maximo: int = 5) -> list[tuple[float, Arquivo]]:
        """Os melhores para o pedido; se não achar nada e o índice tiver mais de 20 s, refaz (pode ser um arquivo novo)."""
        self.garantir()
        achados = self._buscar(termos, extensoes)
        if not achados and self.relogio() - self.feito_em > 20:
            self.garantir(forcar=True)
            achados = self._buscar(termos, extensoes)
        return achados[:maximo]

    def _buscar(self, termos, extensoes):
        candidatos = [a for a in self.arquivos if not extensoes or os.path.splitext(a.nome)[1].lower() in extensoes]
        return ordenar(termos, candidatos)
