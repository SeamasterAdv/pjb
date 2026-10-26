"""Vade-mécum local: as leis, os códigos, a Constituição e as súmulas num banco do PC, com busca e exemplos.

O que guarda (SQLite em `banco_memoria/vade_mecum.sqlite3`, `CAMINHO_VADE_MECUM`, fora do git):
- **normas**: o texto oficial do Planalto de cada norma do catálogo (`agente_leis.NORMAS`), inteiro (as linhas, para o
  agente de leis ler sem internet) e separado por artigo (para a busca: "qual artigo fala de usucapião?");
- **súmulas**: do STF (as 736 e as vinculantes, pelo portal "Aplicação das Súmulas no STF"), do STJ (o PDF oficial dos
  verbetes, publicado pelo próprio STJ) e do TST (súmulas, orientações jurisprudenciais e precedentes normativos, pela API
  pública que o site de jurisprudência dele usa), com a situação (vigente, cancelada, superada...);
- **julgados**: os trechos de decisões reais que o STF põe em cada súmula ("Jurisprudência selecionada", "Precedentes
  representativos") e as referências dos precedentes do TST: são os exemplos de casos reais;
- **exemplos**: os exemplos práticos que a IA já escreveu (cada um uma vez só).

Os downloads são educados (um pedido por vez, com pausa) e rodam em segundo plano: na primeira abertura, depois a cada
`VADE_MECUM_DIAS` dias, ou quando a pessoa pede ("atualiza o vade-mécum"). O STJ protege a busca (scon) com uma verificação
contra robôs; ela não é contornada: as súmulas dele vêm do PDF que o próprio STJ publica para download.

Cada operação abre e fecha a conexão (WAL), porque o processo principal e os painéis usam o mesmo arquivo.
"""
import html as html_util
import json
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import closing

logger = logging.getLogger(__name__)

CABECALHOS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0 Safari/537.36"}
ESPERA_ENTRE_PEDIDOS = 0.6  # segundos entre dois pedidos ao mesmo site
STF_PORTAL = "https://portal.stf.jus.br/jurisprudencia/sumariosumulas.asp"
STF_BASES = {"sumula": 30, "vinculante": 26}  # base=30: as 736 súmulas; base=26: as vinculantes
TST_API = "https://jurisprudencia-backend.tst.jus.br/rest/pesquisa-textual"
TST_TIPOS = {"SUM": "sumula", "OJ": "oj", "PN": "pn"}
STJ_VERBETES = "https://www.stj.jus.br/docs_internet/jurisprudencia/tematica/download/SU/Verbetes/VerbetesSTJ.pdf"
JULGADOS_POR_SUMULA = 12
TAMANHO_DO_JULGADO = 3000
NOMES_DOS_TRIBUNAIS = {"stf": "STF", "stj": "STJ", "tst": "TST"}
NOMES_DOS_TIPOS = {"sumula": "Súmula", "vinculante": "Súmula Vinculante", "oj": "OJ", "pn": "Precedente Normativo"}
# Os órgãos das OJs do TST, como a API os chama e como se fala.
ORGAOS_DO_TST = {"SDI1": "SDI-1", "SDI1T": "SDI-1 (transitória)", "SDI2": "SDI-2", "SDC": "SDC", "TP": "Tribunal Pleno",
                 "OE": "Órgão Especial"}

ESQUEMA = """
create table if not exists normas (chave text primary key, nome text, url text, linhas text, artigos integer,
                                   atualizado real);
create table if not exists artigos (norma text, numero text, ordem integer, texto text, primary key (norma, ordem));
create virtual table if not exists artigos_busca using fts5(norma unindexed, numero unindexed, texto,
                                                            tokenize='unicode61 remove_diacritics 2');
create table if not exists sumulas (tribunal text, tipo text, orgao text, numero integer, titulo text, texto text,
                                    situacao text, observacao text, data text, fonte text,
                                    primary key (tribunal, tipo, orgao, numero));
create virtual table if not exists sumulas_busca using fts5(chave unindexed, texto,
                                                            tokenize='unicode61 remove_diacritics 2');
create table if not exists julgados (tribunal text, tipo text, orgao text, numero integer, ordem integer, secao text,
                                     titulo text, texto text, referencia text,
                                     primary key (tribunal, tipo, orgao, numero, ordem));
create table if not exists exemplos (chave text primary key, texto text, ia text, criado real);
create table if not exists atualizacoes (parte text primary key, quando real, total integer, erros text);
"""

PALAVRAS_VAZIAS = set("""
a o as os um uma uns umas de do da dos das no na nos nas em por para pra pro com sem sobre que e ou se ao aos
qual quais tem ha existe alguma algum artigo artigos sumula sumulas lei leis norma normas fala falam diz dizem trata
tratam me mostra fale diga isso esse essa sobre stf stj tst codigo codigos jurisprudencia
""".split())


def _sem_acentos(texto: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", texto or "") if not unicodedata.combining(c)).lower()


def _texto_do_html(fragmento: str) -> str:
    """O texto de um pedaço de HTML: <br> e </p> viram quebra de linha, sem as tags, com os caracteres resolvidos."""
    texto = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", " ", fragmento or "")
    texto = re.sub(r"\s+", " ", texto)
    texto = re.sub(r"(?i)<br\s*/?>|</p\s*>|</div\s*>|</li\s*>", "\n", texto)
    texto = html_util.unescape(re.sub(r"(?s)<[^>]+>", " ", texto)).replace("\xa0", " ").replace("​", "")
    texto = re.sub(r"[ \t]+([,.;:!?)\]])", r"\1", texto)  # o espaço da tag tirada antes da vírgula: "Toffoli ,"
    texto = re.sub(r"([(\[])[ \t]+", r"\1", texto)
    linhas = [re.sub(r"[ \t]+", " ", linha).strip() for linha in texto.split("\n")]
    return "\n".join(linhas).strip()


# ====================================================================== artigos de uma norma
# A letra vem colada ("Art. 121-A."); com espaços é o travessão da CLT ("Art. 136 - A época das férias...").
RE_INICIO_DO_ARTIGO = re.compile(r"^Art\.?\s*(\d{1,2}\.\d{3}|\d{1,4})\s*(?:º|°|o\b)?(?:-([A-Z]{1,2})\b)?")
RE_ESTRUTURA = re.compile(r"^(?:CAP[IÍ]TULO|T[IÍ]TULO|SE[ÇC][ÃA]O\b|Se[çc][ãa]o\s+[IVXL]|SUBSE[ÇC][ÃA]O|LIVRO|"
                          r"PARTE\s+(?:GERAL|ESPECIAL)|ATO DAS DISPOSI)")
RE_ADCT = re.compile(r"^ATO DAS DISPOSI[ÇC][ÕO]ES CONSTITUCIONAIS TRANSIT[ÓO]RIAS")


def numero_do_artigo(numero: str, letra: str = "") -> str:
    """"1228" -> "1.228", como no texto da lei; com a letra: "121-A"."""
    numero = str(numero).replace(".", "")
    if len(numero) > 3:
        numero = f"{numero[:-3]}.{numero[-3:]}"
    return f"{numero}-{letra.upper()}" if letra else numero


def artigos_da_norma(linhas: list[str]) -> list[tuple[str, list[str]]]:
    """[(número, linhas do artigo)] na ordem do texto, do "Art. N" até o próximo artigo ou título."""
    artigos, atual = [], None
    for linha in linhas:
        inicio = RE_INICIO_DO_ARTIGO.match(linha)
        if inicio:
            atual = (numero_do_artigo(inicio.group(1), inicio.group(2) or ""), [linha])
            artigos.append(atual)
        elif atual is not None and RE_ESTRUTURA.match(linha):
            atual = None
        elif atual is not None:
            atual[1].append(linha)
    return artigos


def separar_constituicao(linhas: list[str]) -> tuple[list[str], list[str]]:
    """(a Constituição, o ADCT): a página do Planalto traz os dois, e o ADCT recomeça a contagem dos artigos."""
    indice = next((i for i, linha in enumerate(linhas) if RE_ADCT.match(linha)), None)
    if indice is None:
        return linhas, []
    return linhas[:indice], linhas[indice:]


# ====================================================================== o banco
class VadeMecum:
    def __init__(self, caminho: str):
        self.caminho = caminho
        self._trava = threading.RLock()
        self._pronto = False

    def _conexao(self) -> sqlite3.Connection:
        pasta = os.path.dirname(self.caminho)
        if pasta:
            os.makedirs(pasta, exist_ok=True)
        conexao = sqlite3.connect(self.caminho, timeout=30)
        conexao.row_factory = sqlite3.Row
        if not self._pronto:
            with self._trava:
                conexao.execute("pragma journal_mode=wal")
                conexao.executescript(ESQUEMA)
                self._pronto = True
        return conexao

    # ------------------------------------------------------------ normas
    def guardar_norma(self, chave: str, nome: str, url: str, linhas: list[str]) -> int:
        """Guarda a norma inteira e separada por artigo (para a busca). Devolve quantos artigos ela tem."""
        from agente_leis import sem_anotacoes

        artigos = artigos_da_norma(linhas)
        with self._trava, closing(self._conexao()) as conexao, conexao:
            conexao.execute("insert or replace into normas values (?, ?, ?, ?, ?, ?)",
                            (chave, nome, url, json.dumps(linhas, ensure_ascii=False), len(artigos), time.time()))
            conexao.execute("delete from artigos where norma = ?", (chave,))
            conexao.execute("delete from artigos_busca where norma = ?", (chave,))
            for ordem, (numero, trecho) in enumerate(artigos):
                texto = "\n".join(sem_anotacoes(linha) for linha in trecho)
                conexao.execute("insert into artigos values (?, ?, ?, ?)", (chave, numero, ordem, texto))
                conexao.execute("insert into artigos_busca values (?, ?, ?)", (chave, numero, texto))
        return len(artigos)

    def linhas(self, chave: str) -> tuple[list[str], float] | None:
        """(linhas, quando foi baixada) da norma guardada, ou None."""
        with closing(self._conexao()) as conexao:
            linha = conexao.execute("select linhas, atualizado from normas where chave = ?", (chave,)).fetchone()
        return (json.loads(linha["linhas"]), linha["atualizado"]) if linha else None

    def normas(self) -> list[dict]:
        with closing(self._conexao()) as conexao:
            return [dict(l) for l in conexao.execute("select chave, nome, url, artigos, atualizado from normas order by chave")]

    # ------------------------------------------------------------ súmulas
    def guardar_sumula(self, tribunal: str, tipo: str, numero: int, texto: str, orgao: str = "", titulo: str = "",
                       situacao: str = "vigente", observacao: str = "", data: str = "", fonte: str = "",
                       julgados=()) -> None:
        chave = chave_da_sumula(tribunal, tipo, numero, orgao)
        busca = " ".join(p for p in (titulo, texto, observacao) if p)
        with self._trava, closing(self._conexao()) as conexao, conexao:
            conexao.execute("insert or replace into sumulas values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (tribunal, tipo, orgao, int(numero), titulo, texto, situacao, observacao, data, fonte))
            conexao.execute("delete from sumulas_busca where chave = ?", (chave,))
            conexao.execute("insert into sumulas_busca values (?, ?)", (chave, busca))
            conexao.execute("delete from julgados where tribunal = ? and tipo = ? and orgao = ? and numero = ?",
                            (tribunal, tipo, orgao, int(numero)))
            for ordem, julgado in enumerate(list(julgados)[:JULGADOS_POR_SUMULA]):
                conexao.execute("insert into julgados values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (tribunal, tipo, orgao, int(numero), ordem, julgado.get("secao", ""), julgado.get("titulo", ""),
                                 (julgado.get("texto") or "")[:TAMANHO_DO_JULGADO], julgado.get("referencia", "")))

    def sumula(self, tribunal: str, tipo: str, numero: int, orgao: str = "") -> dict | None:
        """A súmula com os julgados, ou None. Sem o órgão (OJ), a primeira que existir com esse número (SDI-1 antes)."""
        with closing(self._conexao()) as conexao:
            if orgao or tipo != "oj":
                linha = conexao.execute("select * from sumulas where tribunal = ? and tipo = ? and orgao = ? and numero = ?",
                                        (tribunal, tipo, orgao, int(numero))).fetchone()
            else:
                linha = conexao.execute(
                    "select * from sumulas where tribunal = ? and tipo = ? and numero = ? "
                    "order by case orgao when 'SDI1' then 0 when 'SDI2' then 1 when 'SDC' then 2 else 3 end",
                    (tribunal, tipo, int(numero))).fetchone()
            if not linha:
                return None
            sumula = dict(linha)
            sumula["julgados"] = [dict(j) for j in conexao.execute(
                "select secao, titulo, texto, referencia from julgados where tribunal = ? and tipo = ? and orgao = ? "
                "and numero = ? order by ordem", (tribunal, tipo, sumula["orgao"], int(numero)))]
        return sumula

    def total(self) -> dict:
        """Quantas normas, artigos e súmulas (por tribunal e tipo) estão guardados."""
        with closing(self._conexao()) as conexao:
            normas = conexao.execute("select count(*), coalesce(sum(artigos), 0) from normas").fetchone()
            sumulas = {f"{t}_{tp}": n for t, tp, n in conexao.execute(
                "select tribunal, tipo, count(*) from sumulas group by tribunal, tipo")}
        return {"normas": normas[0], "artigos": normas[1], "sumulas": sumulas}

    # ------------------------------------------------------------ busca
    def buscar(self, consulta: str, limite: int = 8, tipos=("sumulas", "artigos"), tribunal: str = "",
               norma: str = "") -> list[dict]:
        """O que fala do assunto, pela busca de texto (sem acentos; todas as palavras, com a raiz: "usucapi*").
        [{"tipo": "sumula"|"artigo", "rotulo", "texto", ...}], as súmulas vigentes antes. Com mais de uma palavra,
        quem tem a expressão inteira ("legítima defesa", "prisão em flagrante") vem antes de quem só tem as palavras."""
        palavras = re.findall(r"[a-z0-9]+", _sem_acentos(consulta))
        termos = [p for p in palavras if p not in PALAVRAS_VAZIAS and len(p) > 2]
        if not termos:
            return []
        # A raiz de cada palavra ("indenizacao" -> "indenizac"*: pega o plural); todas precisam aparecer.
        raizes = [f'"{t[:max(5, len(t) - 2)]}"*' for t in termos[:8]]
        resultados = self._buscar(" ".join(raizes), limite, tipos, tribunal, norma)
        if len(termos) > 1:
            inteira = self._buscar(f'"{" ".join(palavras[:12])}"', limite, tipos, tribunal, norma)
            vistos = {(r.get("norma"), r.get("tribunal"), r.get("tipo"), r.get("orgao"), r["numero"]) for r in inteira}
            resultados = inteira + [r for r in resultados if (r.get("norma"), r.get("tribunal"), r.get("tipo"),
                                                               r.get("orgao"), r["numero"]) not in vistos]
            resultados.sort(key=lambda r: r.get("situacao", "vigente") not in ("vigente", ""))  # as canceladas no fim
            resultados = resultados[:limite]
        if not resultados and len(raizes) > 1:  # nenhum com todas: os que têm a maioria
            resultados = self._buscar(" OR ".join(raizes), limite, tipos, tribunal, norma)
        return resultados

    def _buscar(self, consulta_fts: str, limite: int, tipos, tribunal: str, norma: str) -> list[dict]:
        resultados = []
        with closing(self._conexao()) as conexao:
            if "sumulas" in tipos:
                for linha in conexao.execute(
                        "select chave, bm25(sumulas_busca) as nota from sumulas_busca where sumulas_busca match ? "
                        "order by nota limit ?", (consulta_fts, limite * 3)):
                    tribunal_s, tipo, orgao, numero = linha["chave"].split(":")
                    if tribunal and tribunal_s != tribunal:
                        continue
                    sumula = conexao.execute("select * from sumulas where tribunal = ? and tipo = ? and orgao = ? and numero = ?",
                                             (tribunal_s, tipo, orgao, int(numero))).fetchone()
                    if sumula:
                        item = dict(sumula, tipo_resultado="sumula", rotulo=rotulo_da_sumula(tribunal_s, tipo, int(numero), orgao),
                                    nota=linha["nota"])
                        resultados.append(item)
            if "artigos" in tipos:
                sql = ("select norma, numero, texto, bm25(artigos_busca) as nota from artigos_busca where artigos_busca match ?"
                       + (" and norma = ?" if norma else "") + " order by nota limit ?")
                argumentos = (consulta_fts, norma, limite * 2) if norma else (consulta_fts, limite * 2)
                nomes = {n["chave"]: n["nome"] for n in conexao.execute("select chave, nome from normas")}
                for linha in conexao.execute(sql, argumentos):
                    nome = nomes.get(linha["norma"], linha["norma"])
                    resultados.append({"tipo_resultado": "artigo", "norma": linha["norma"], "numero": linha["numero"],
                                       "texto": linha["texto"], "nota": linha["nota"],
                                       "rotulo": f"o artigo {linha['numero']} {_de(nome)}"})
        # bm25: quanto menor, melhor; as súmulas canceladas e superadas vão para o fim
        resultados.sort(key=lambda r: (r.get("situacao", "vigente") not in ("vigente", ""), r["nota"]))
        return resultados[:limite]

    def sumulas_que_citam(self, frase: str, limite: int = 300) -> list[dict]:
        """As súmulas cujo texto tem a frase ("1 228" acha "art. 1.228"): os candidatos, a conferência é de quem chama."""
        frase = " ".join(re.findall(r"[a-z0-9]+", _sem_acentos(frase)))
        if not frase:
            return []
        with closing(self._conexao()) as conexao:
            chaves = [l["chave"] for l in conexao.execute(
                "select chave from sumulas_busca where sumulas_busca match ? limit ?", (f'"{frase}"', limite))]
            sumulas = []
            for chave in chaves:
                tribunal, tipo, orgao, numero = chave.split(":")
                linha = conexao.execute("select * from sumulas where tribunal = ? and tipo = ? and orgao = ? and numero = ?",
                                        (tribunal, tipo, orgao, int(numero))).fetchone()
                if linha:
                    sumulas.append(dict(linha, rotulo=rotulo_da_sumula(tribunal, tipo, int(numero), orgao)))
        return sumulas

    # ------------------------------------------------------------ exemplos e atualizações
    def exemplo(self, chave: str) -> str:
        with closing(self._conexao()) as conexao:
            linha = conexao.execute("select texto from exemplos where chave = ?", (chave,)).fetchone()
        return linha["texto"] if linha else ""

    def guardar_exemplo(self, chave: str, texto: str, ia: str = "") -> None:
        with self._trava, closing(self._conexao()) as conexao, conexao:
            conexao.execute("insert or replace into exemplos values (?, ?, ?, ?)", (chave, texto, ia, time.time()))

    def registrar_atualizacao(self, parte: str, total: int, erros: list[str]) -> None:
        with self._trava, closing(self._conexao()) as conexao, conexao:
            conexao.execute("insert or replace into atualizacoes values (?, ?, ?, ?)",
                            (parte, time.time(), total, json.dumps(erros[:30], ensure_ascii=False)))

    def atualizacoes(self) -> dict:
        with closing(self._conexao()) as conexao:
            return {l["parte"]: {"quando": l["quando"], "total": l["total"], "erros": json.loads(l["erros"] or "[]")}
                    for l in conexao.execute("select * from atualizacoes")}


def chave_da_sumula(tribunal: str, tipo: str, numero: int, orgao: str = "") -> str:
    return f"{tribunal}:{tipo}:{orgao}:{int(numero)}"


def rotulo_da_sumula(tribunal: str, tipo: str, numero: int, orgao: str = "") -> str:
    """"a Súmula 7 do STJ", "a Súmula Vinculante 11", "a OJ 191 da SDI-1 do TST"."""
    if tipo == "vinculante":
        return f"a Súmula Vinculante {numero}"
    if tipo == "oj":
        return f"a OJ {numero} da {ORGAOS_DO_TST.get(orgao, orgao)} do TST"
    if tipo == "pn":
        return f"o Precedente Normativo {numero} do TST"
    return f"a Súmula {numero} do {NOMES_DOS_TRIBUNAIS.get(tribunal, tribunal.upper())}"


def _de(nome: str) -> str:
    if nome.startswith("o "):
        return "do " + nome[2:]
    if nome.startswith("a "):
        return "da " + nome[2:]
    return "de " + nome


# ====================================================================== STF
def itens_da_listagem_do_stf(pagina: str, base: int) -> list[tuple[str, int, str]]:
    """[(id da página, número, situação)] da listagem do portal ("Súmula 3 <em>(superada)</em>")."""
    itens = []
    for pagina_id, rotulo in re.findall(rf'<div class="sumula-item"><a[^>]*href="sumariosumulas\.asp\?base={base}&sumula=(\d+)"[^>]*>(.*?)</a>',
                                        pagina, re.S):
        texto = _texto_do_html(rotulo)
        numero = re.search(r"(\d+)", texto)
        if not numero:
            continue
        situacao = re.search(r"\(([^)]+)\)", texto)
        itens.append((pagina_id, int(numero.group(1)), situacao.group(1).strip().lower() if situacao else "vigente"))
    return itens


def julgados_do_bloco(fragmento: str, secao: str) -> list[dict]:
    """Os julgados de uma seção do STF: os parágrafos até a referência entre colchetes ("[RHC 122.682, rel. min. ...]")."""
    julgados, titulo, partes = [], "", []
    # O título de cada grupo vem com "●" ou em itálico e negrito ("<em><strong>Condições adicionais...</strong></em>").
    fragmento = re.sub(r"(?is)<em>\s*<strong>(.*?)</strong>\s*</em>", r"<br>● \1<br>", fragmento)
    for paragrafo in (p.strip() for p in _texto_do_html(fragmento).split("\n")):
        if not paragrafo:
            continue
        if paragrafo.startswith("●"):
            titulo = paragrafo.lstrip("● ").strip()
            continue
        referencia = re.match(r"^\[(.+)\]\.?$", paragrafo) or re.search(r"\s\[([^\[\]]{8,300})\]\.?$", paragrafo)
        if referencia and re.match(r"^\[", paragrafo):
            julgados.append({"secao": secao, "titulo": titulo, "texto": "\n".join(partes).strip(),
                             "referencia": referencia.group(1).strip()})
            partes = []
        elif referencia:  # o texto e a referência no mesmo parágrafo
            partes.append(paragrafo[:referencia.start()].strip())
            julgados.append({"secao": secao, "titulo": titulo, "texto": "\n".join(partes).strip(),
                             "referencia": referencia.group(1).strip()})
            partes = []
        else:
            partes.append(paragrafo)
    if partes and not julgados:
        julgados.append({"secao": secao, "titulo": titulo, "texto": "\n".join(partes).strip(), "referencia": ""})
    return [j for j in julgados if j["texto"]]


def sumula_da_pagina_do_stf(pagina: str) -> dict:
    """{"enunciado", "observacao", "julgados"} da página de uma súmula do STF (blocos "titulo" + "parCOM").

    O "parCOM" pode ter <div> dentro, então cada bloco vai até o próximo "titulo" (o último, até o fim da seção).
    """
    resultado = {"enunciado": "", "observacao": "", "julgados": []}
    for indice, pedaco in enumerate(pagina.split('<div class="titulo">')[1:]):
        titulo, _, resto = pedaco.partition("</div>")
        conteudo = re.split(r"(?i)</section>|<footer\b", resto.split('<div class="parCOM">', 1)[-1])[0]
        titulo = _texto_do_html(titulo)
        if indice == 0:
            resultado["enunciado"] = re.sub(r"\s*\n\s*", " ", _texto_do_html(conteudo)).strip()
        elif titulo.lower().startswith("observa"):
            observacao = re.sub(r"\s*\n\s*", " ", _texto_do_html(conteudo)).strip()
            observacao = re.sub(r"Para (?:informações adicionais|pesquisar menções)[^.]*?clique aqui\.?", "", observacao)
            resultado["observacao"] = re.sub(r"\s{2,}", " ", observacao).strip()
        elif titulo.lower().startswith(("jurisprud", "precedente")):
            resultado["julgados"] += julgados_do_bloco(conteudo, titulo)
    return resultado


# ====================================================================== STJ
RE_CABECA_STJ = re.compile(r"\bG\s+S[ÚU]MULA\s+(\d{1,4})\b\s*(?:\(S[ÚU]MULA\s+([A-ZÁÉÍÓÚÇÃÕ]+)\))?\s*VEJA MAIS")
RE_DADOS_STJ = re.compile(r"\((S[ÚU]MULA\s+\d+,[^()]*|[^()]*?julgad[oa]\s+em[^()]*)\)")
SITUACOES_DO_STJ = {"CANCELADA": "cancelada", "REVOGADA": "revogada", "ALTERADA": "vigente", "REVISADA": "vigente"}


def sumulas_do_pdf_do_stj(texto: str) -> list[dict]:
    """As súmulas do PDF oficial dos verbetes do STJ, em blocos que começam com o ícone ("G SÚMULA 676 VEJA MAIS"):
    o enunciado, os dados entre parênteses ("(TERCEIRA SEÇÃO, julgado em ...)") e, nas alteradas e canceladas, a nota
    ("SÚMULA CANCELADA: A Primeira Seção, na sessão de ...")."""
    texto = re.sub(r"scon\.stj\.jus\.br/SCON/sumstj/\s*\d*\s*", " ", texto)  # o rodapé de cada página
    cabecas = list(RE_CABECA_STJ.finditer(texto))
    sumulas = {}
    for cabeca, seguinte in zip(cabecas, cabecas[1:] + [None]):
        bloco = re.sub(r"\s+", " ", texto[cabeca.end():seguinte.start() if seguinte else len(texto)]).strip()
        dados = RE_DADOS_STJ.search(bloco)
        if not dados:
            continue
        numero = int(cabeca.group(1))
        enunciado = bloco[:dados.start()].strip()
        nota = bloco[dados.end():].strip()
        referencia = re.sub(r"^S[ÚU]MULA\s+\d+,\s*", "", dados.group(1)).strip()
        data = re.search(r"julgad[oa]\s+em\s+([\d/]+)", referencia) or re.search(r"\bDJe?\s+([\d/]+)", referencia)
        observacao = " ".join(p for p in (referencia + ".", nota) if p.strip(". "))
        sumulas.setdefault(numero, {"numero": numero, "texto": enunciado, "orgao_julgador": referencia.split(",")[0].strip(),
                                    "data": data.group(1) if data else "", "observacao": observacao,
                                    "situacao": SITUACOES_DO_STJ.get(cabeca.group(2) or "", "vigente")})
    return sorted(sumulas.values(), key=lambda s: s["numero"])


def texto_do_pdf(conteudo: bytes) -> str:
    import pymupdf

    with pymupdf.open(stream=conteudo, filetype="pdf") as documento:
        return "\n".join(pagina.get_text() for pagina in documento)


# ====================================================================== TST
def corpo_da_pesquisa_do_tst(tipo: str) -> dict:
    """O pedido que o site de jurisprudência do TST manda à API dele (todos os campos vazios, só o tipo)."""
    return {"ou": "", "e": "", "termoExato": "", "naoContem": "", "ementa": "", "dispositivo": "",
            "numeracaoUnica": {"numero": "", "digito": "", "ano": "", "orgao": "5", "tribunal": "", "vara": ""},
            "orgaosJudicantes": [], "ministros": [], "convocados": [], "classesProcessuais": [], "codigosClassesPrecedentes": [],
            "indicadores": [], "assuntos": [], "tipos": [tipo], "orgao": "TST", "publicacaoInicial": "", "publicacaoFinal": "",
            "julgamentoInicial": "", "julgamentoFinal": "", "ordenacao": "numero"}


def sumula_do_registro_do_tst(registro: dict) -> dict:
    """Uma súmula, OJ ou PN do TST, do registro da API."""
    codigo = (registro.get("tipo") or {}).get("codigoTipoJurisprudencia", "")
    # As OJs transitórias da SDI-1 vêm com outro código (OJT) e recomeçam a numeração: "SDI1T" para não cobrir as outras.
    tipo = "oj" if codigo == "OJT" else TST_TIPOS.get(codigo, "")
    situacao = ((registro.get("situacao") or {}).get("descricao") or "").lower()
    observacao = registro.get("observacao") or ""
    if situacao == "cancelada" or re.search(r"\(cancelad", observacao, re.I):
        situacao = "cancelada"
    else:
        situacao = "vigente"
    orgao = ((registro.get("orgaoJudicante") or {}).get("sigla") or "").strip() if tipo == "oj" else ""
    if codigo == "OJT":
        orgao += "T"
    julgados = []
    for precedente in (registro.get("precedentes") or [])[:6]:
        partes = [precedente.get("numeracao", "")]
        if (precedente.get("orgaoJudicante") or {}).get("descricao"):
            partes.append(precedente["orgaoJudicante"]["descricao"])
        if (precedente.get("relator") or {}).get("nome"):
            partes.append(f"rel. {precedente['relator']['nome']}")
        if precedente.get("dtaPublicacao"):
            partes.append(f"publicado em {precedente['dtaPublicacao']}")
        julgados.append({"secao": "Precedentes", "titulo": "", "texto": "", "referencia": ", ".join(p for p in partes if p)})
    # As canceladas e convertidas vêm sem a tese: fica o título, e a observação diz para onde foram ("convertida na Súmula 448").
    texto = (registro.get("tese") or "").strip() or (registro.get("titulo") or "").strip()
    return {"tipo": tipo, "numero": int(registro.get("numero") or 0), "orgao": orgao, "titulo": registro.get("titulo") or "",
            "texto": texto, "situacao": situacao, "observacao": observacao,
            "data": registro.get("dtaPublicacao") or "", "julgados": [j for j in julgados if j["referencia"]]}


# ====================================================================== downloads
class Baixador:
    """Os pedidos à internet, um por vez e com pausa (os testes trocam por um falso)."""

    def __init__(self, espera: float = ESPERA_ENTRE_PEDIDOS):
        self.espera = espera
        self._ultimo = 0.0
        self._sessao = None

    def _pausa(self):
        falta = self.espera - (time.monotonic() - self._ultimo)
        if falta > 0:
            time.sleep(falta)
        self._ultimo = time.monotonic()

    def _sessao_http(self):
        if self._sessao is None:
            import requests

            self._sessao = requests.Session()
            self._sessao.headers.update(CABECALHOS)
        return self._sessao

    def bytes(self, url: str, referer: str = "") -> bytes:
        self._pausa()
        resposta = self._sessao_http().get(url, timeout=60, headers={"Referer": referer} if referer else None)
        resposta.raise_for_status()
        return resposta.content

    def texto(self, url: str, referer: str = "") -> str:
        conteudo = self.bytes(url, referer)
        try:
            return conteudo.decode("utf-8")
        except UnicodeDecodeError:
            return conteudo.decode("cp1252", "replace")

    def json(self, url: str, corpo: dict) -> dict:
        self._pausa()
        resposta = self._sessao_http().post(url, json=corpo, timeout=60, headers={
            "Origin": "https://jurisprudencia.tst.jus.br", "Referer": "https://jurisprudencia.tst.jus.br/"})
        resposta.raise_for_status()
        return resposta.json()


def atualizar_normas(vade: VadeMecum, baixador: Baixador, normas=None, avisar=None) -> tuple[int, list[str]]:
    """Baixa do Planalto cada norma do catálogo (`agente_leis.NORMAS`) e guarda. A Constituição sai separada do ADCT."""
    from agente_leis import NORMAS, candidatos_planalto, linhas_do_html

    feitas, erros = 0, []
    for norma in normas if normas is not None else NORMAS:
        if norma.chave == "adct":
            continue  # sai junto com a Constituição
        urls = [norma.url] if norma.url else []
        if norma.numero:  # se o endereço do catálogo sair do ar, os prováveis
            urls += [u for u in candidatos_planalto(norma.numero, norma.ano, norma.tipo) if u not in urls]
        for url in urls:
            try:
                linhas = linhas_do_html(baixador.bytes(url))
            except Exception as exc:
                logger.debug("Norma fora do endereço %s: %s", url, exc)
                continue
            if not any(RE_INICIO_DO_ARTIGO.match(linha) for linha in linhas):
                logger.debug("Página sem artigos em %s", url)  # uma página de erro com status 200
                continue
            if norma.chave == "cf":
                constituicao, adct = separar_constituicao(linhas)
                vade.guardar_norma("cf", norma.nome, url, constituicao)
                if adct:
                    vade.guardar_norma("adct", "o ADCT", url, adct)
            else:
                vade.guardar_norma(norma.chave, norma.nome, url, linhas)
            feitas += 1
            if avisar:
                avisar(f"{norma.nome}: ok")
            break
        else:
            erros.append(f"{norma.nome}: não achei no Planalto")
    return feitas, erros


def atualizar_stf(vade: VadeMecum, baixador: Baixador, avisar=None, ja_tem=None) -> tuple[int, list[str]]:
    """As súmulas e as vinculantes do STF, com a jurisprudência selecionada de cada uma."""
    feitas, erros = 0, []
    for tipo, base in STF_BASES.items():
        listagem = f"{STF_PORTAL}?base={base}"
        try:
            itens = itens_da_listagem_do_stf(baixador.texto(listagem), base)
        except Exception as exc:
            erros.append(f"STF ({NOMES_DOS_TIPOS[tipo]}): {exc}")
            continue
        for pagina_id, numero, situacao in itens:
            if ja_tem and ja_tem("stf", tipo, numero):
                continue
            try:
                dados = sumula_da_pagina_do_stf(baixador.texto(f"{listagem}&sumula={pagina_id}", referer=listagem))
            except Exception as exc:
                erros.append(f"STF {NOMES_DOS_TIPOS[tipo]} {numero}: {exc}")
                continue
            if not dados["enunciado"]:
                erros.append(f"STF {NOMES_DOS_TIPOS[tipo]} {numero}: página sem o enunciado")
                continue
            vade.guardar_sumula("stf", tipo, numero, dados["enunciado"], situacao=situacao, observacao=dados["observacao"],
                                fonte=f"{listagem}&sumula={pagina_id}", julgados=dados["julgados"])
            feitas += 1
        if avisar:
            avisar(f"STF ({NOMES_DOS_TIPOS[tipo]}): {len(itens)}")
    return feitas, erros


def atualizar_tst(vade: VadeMecum, baixador: Baixador, avisar=None, por_pagina: int = 100) -> tuple[int, list[str]]:
    """Súmulas, OJs e precedentes normativos do TST, pela API pública do site de jurisprudência dele."""
    feitas, erros = 0, []
    for codigo in TST_TIPOS:
        inicio, total = 1, None
        while total is None or inicio <= total:
            try:
                dados = baixador.json(f"{TST_API}/{inicio}/{por_pagina}", corpo_da_pesquisa_do_tst(codigo))
            except Exception as exc:
                erros.append(f"TST {codigo} a partir de {inicio}: {exc}")
                break
            total = int(dados.get("totalRegistros") or 0)
            registros = dados.get("registros") or []
            for item in registros:
                sumula = sumula_do_registro_do_tst(item.get("registro") or {})
                if not sumula["tipo"] or not sumula["numero"] or not sumula["texto"]:
                    continue  # um tipo novo da API (não vira súmula por engano) ou um registro sem a tese
                vade.guardar_sumula("tst", sumula["tipo"], sumula["numero"], sumula["texto"], orgao=sumula["orgao"],
                                    titulo=sumula["titulo"], situacao=sumula["situacao"], observacao=sumula["observacao"],
                                    data=sumula["data"], fonte="jurisprudencia.tst.jus.br", julgados=sumula["julgados"])
                feitas += 1
            if not registros:
                break
            inicio += por_pagina
        if avisar:
            avisar(f"TST ({codigo}): {total}")
    return feitas, erros


def atualizar_stj(vade: VadeMecum, baixador: Baixador, avisar=None) -> tuple[int, list[str]]:
    """As súmulas do STJ pelo PDF oficial dos verbetes (traz também as canceladas e as alteradas, com a nota)."""
    try:
        sumulas = sumulas_do_pdf_do_stj(texto_do_pdf(baixador.bytes(STJ_VERBETES)))
    except Exception as exc:
        return 0, [f"STJ: {exc}"]
    for sumula in sumulas:
        vade.guardar_sumula("stj", "sumula", sumula["numero"], sumula["texto"], situacao=sumula["situacao"],
                            observacao=sumula["observacao"], data=sumula["data"], fonte=STJ_VERBETES)
    if avisar:
        avisar(f"STJ: {len(sumulas)}")
    return len(sumulas), []


PARTES = {"normas": atualizar_normas, "stf": atualizar_stf, "tst": atualizar_tst, "stj": atualizar_stj}
_ATUALIZANDO = threading.Lock()


def atualizar(vade: VadeMecum, partes=tuple(PARTES), baixador: Baixador | None = None, avisar=None) -> dict | None:
    """Baixa as partes pedidas e guarda. None se já houver uma atualização em andamento neste processo."""
    if not _ATUALIZANDO.acquire(blocking=False):
        return None
    try:
        baixador = baixador or Baixador()
        resumo = {}
        for parte in partes:
            inicio = time.time()
            try:
                feitas, erros = PARTES[parte](vade, baixador, avisar=avisar)
            except Exception as exc:
                logger.exception("Falha ao atualizar o vade-mécum (%s)", parte)
                feitas, erros = 0, [str(exc)]
            vade.registrar_atualizacao(parte, feitas, erros)
            resumo[parte] = {"total": feitas, "erros": erros}
            logger.info("Vade-mécum: %s com %d itens em %.0f s (%d erros)", parte, feitas, time.time() - inicio, len(erros))
        return resumo
    finally:
        _ATUALIZANDO.release()


def atualizando() -> bool:
    return _ATUALIZANDO.locked()


def precisa_atualizar(vade: VadeMecum, dias: float) -> list[str]:
    """As partes que nunca foram baixadas ou que têm mais de `dias` dias."""
    feitas = vade.atualizacoes()
    agora = time.time()
    return [parte for parte in PARTES if parte not in feitas or agora - feitas[parte]["quando"] > dias * 86400
            or (feitas[parte]["total"] == 0)]


def atualizar_em_segundo_plano(vade: VadeMecum, partes=None, avisar=None, ao_terminar=None) -> bool:
    """Roda `atualizar` numa thread. False se já houver uma em andamento."""
    if atualizando():
        return False

    def trabalho():
        resumo = atualizar(vade, tuple(partes or PARTES), avisar=avisar)
        if ao_terminar and resumo is not None:
            ao_terminar(resumo)

    threading.Thread(target=trabalho, name="vade-mecum", daemon=True).start()
    return True


def descrever_total(total: dict) -> str:
    """"84 normas (12.430 artigos), 798 súmulas do STF, 676 do STJ e 1.292 do TST"."""
    sumulas = total.get("sumulas", {})
    stf = sumulas.get("stf_sumula", 0) + sumulas.get("stf_vinculante", 0)
    stj = sumulas.get("stj_sumula", 0)
    tst = sumulas.get("tst_sumula", 0) + sumulas.get("tst_oj", 0) + sumulas.get("tst_pn", 0)

    def mil(n):
        return f"{n:,}".replace(",", ".")

    return (f"{mil(total.get('normas', 0))} normas ({mil(total.get('artigos', 0))} artigos), {mil(stf)} súmulas do STF, "
            f"{mil(stj)} do STJ e {mil(tst)} súmulas, OJs e precedentes do TST")


_PADRAO = {"objeto": None}


def vade_padrao() -> VadeMecum:
    """O vade-mécum do `CAMINHO_VADE_MECUM` (os testes trocam o caminho pelo conftest)."""
    import config

    if _PADRAO["objeto"] is None or _PADRAO["objeto"].caminho != config.CAMINHO_VADE_MECUM:
        _PADRAO["objeto"] = VadeMecum(config.CAMINHO_VADE_MECUM)
    return _PADRAO["objeto"]


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    vade = vade_padrao()
    if "--baixar" in sys.argv:
        partes = [p for p in sys.argv[2:] if p in PARTES] or list(PARTES)
        print(json.dumps(atualizar(vade, partes, avisar=print), ensure_ascii=False, indent=1))
    print(descrever_total(vade.total()))
