"""Agente da impressora: entende os pedidos em português e cuida da confirmação antes de imprimir.

Exemplos: "imprime o PDF", "imprime o arquivo da fotossíntese em preto e branco", "imprime só a
página 2, duas cópias", "imprime o boleto que eu baixei", "imprime isso" (a última resposta),
"imprime: comprar pão, leite e ovos", "imprime minha agenda da semana", "a impressora está ligada?",
"já imprimiu?", "cancela a impressão", "pausa a impressora", "liga/desliga a impressora".

Por regras, sem LLM, como a agenda. `AgenteImpressora.responder(texto)` devolve a resposta ou None
(o roteamento segue). A impressão em si roda em segundo plano; só um erro volta por `avisar`.
"""
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from agenda_linguagem import NUMEROS, preparar
from impressora import (EXTENSOES_EXCEL, EXTENSOES_IMAGEM, EXTENSOES_IMPRIMIVEIS, EXTENSOES_POWERPOINT,
                        EXTENSOES_TEXTO, EXTENSOES_WORD, ErroImpressora, nome_curto)

logger = logging.getLogger(__name__)

SEGUNDOS_PARA_CONFIRMAR = 120.0
MAXIMO_COPIAS = 20
MAXIMO_ARQUIVOS_VARRIDOS = 6000

RE_CHAMADA = re.compile(r"^(?:beatriz|beatris|biatriz)\s*[,!.]?\s*")
RE_POLIDEZ = re.compile(
    r"^(?:por favor|pfv|me|pra mim|voce pode|voce poderia|vc pode|pode|poderia|consegue|quero que voce|"
    r"preciso que voce|eu quero|quero|da pra|tem como)\s+"
)
RE_VERBO_IMPRIMIR = re.compile(
    r"\b(?:imprim(?:e|a|ir|am|irem)|manda(?:r)? (?:pra|para a|pra a) impressora|manda(?:r)? imprimir|"
    r"bota(?:r)? (?:pra|para) imprimir|coloca(?:r)? (?:pra|para) imprimir)\b"
)
RE_CRIAR_DOCUMENTO = re.compile(
    r"\b(?:faz|faca|fazer|cria|crie|criar|gera|gere|gerar|monta|monte|montar|escreve|escreva|prepara|prepare|"
    r"elabora|elabore)\s+(?:um|uma|uns|umas|me um|me uma)\s+(?:\w+\s+){0,2}?"
    r"(?:pdf|documento|apresentac\w+|slides?|planilha|resumo|relatorio|apostila|flashcards?|aulas?|trabalho|word|"
    r"material|curso|simulado|questoes)\b"
)
RE_LIGAR = re.compile(r"\b(?:liga|ligue|ligar|acende|acenda|acender)\s+(?:a |minha |essa )?impressora\b")
RE_DESLIGAR = re.compile(r"\b(?:desliga|desligue|desligar|apaga|apague|apagar)\s+(?:a |minha |essa )?impressora\b")
RE_CANCELAR = re.compile(
    r"\b(?:cancel\w*|interromp\w*|limp[ae]r?|para de|pare de|parar de)\b.{0,25}"
    r"\b(?:impress(?:ao|oes)|imprimir|imprimindo|fila (?:de|da) impress\w*|fila da impressora)\b"
)
RE_PAUSAR = re.compile(r"\bpaus[ae]\w*\b.{0,20}\b(?:impressora|impress(?:ao|oes)|fila)\b")
RE_RETOMAR = re.compile(r"\b(?:retom\w*|continu[ae]\w*|despaus\w*|liber[ae]\w*)\b.{0,20}\b(?:impressora|impress(?:ao|oes)|fila)\b")
RE_FILA = re.compile(
    r"\bfila (?:de|da) impress\w*|\bfila da impressora\b|\b(?:ja )?imprimiu\b|\bterminou de imprimir\b|"
    r"\bo que (?:esta|ta) imprimindo\b|\btem (?:algo|alguma coisa|coisa) (?:na fila|pra imprimir|para imprimir)\b"
)
RE_STATUS = re.compile(
    r"\bimpressora\b.{0,30}\b(?:ligada|desligada|ligou|funcionando|pronta|online|offline|conectada|status|estado|"
    r"situacao|tinta|papel|ok|funciona)\b|\b(?:status|estado|situacao)\b.{0,15}\bimpressora\b|"
    r"\bcomo (?:esta|ta) a impressora\b"
)
RE_LISTAR = re.compile(r"\bquais (?:sao as )?impressoras\b|\bqual (?:e a )?impressora\b|\bque impressora\b")

RE_SIM = re.compile(
    r"^(?:sim|pode|pode sim|pode imprimir|imprime|imprima|imprimir|manda|manda ver|confirma|confirmo|ok|okay|isso|claro|vai|"
    r"beleza|com certeza|por favor|quero|faz|faca|positivo|aham|uhum|bora|manda bala)\b"
)
RE_NAO = re.compile(r"^(?:nao|cancela|deixa|esquece|para|pare|negativo|agora nao|melhor nao)\b")

RE_PRETO_E_BRANCO = re.compile(
    r"\bpreto e branco\b|\bp ?& ?b\b|\bpb\b|\bsem cor\b|\bmonocrom\w*|\btons? de cinza\b|\bem cinza\b|"
    r"\bso (?:com )?(?:a )?tinta preta\b"
)
RE_COLORIDO = re.compile(r"\bcolorid[oa]\b|\bem cores\b|\ba cores\b|\bcom cor(?:es)?\b")
_NUMERO = r"(\d+|" + "|".join(sorted(NUMEROS, key=len, reverse=True)) + r")"
RE_COPIAS = re.compile(_NUMERO + r"\s+(?:copias?|vias?|vezes)\b")
ORDINAIS = {"primeira": 1, "segunda": 2, "terceira": 3, "quarta": 4, "quinta": 5, "sexta": 6, "setima": 7,
            "oitava": 8, "nona": 9, "decima": 10, "ultima": -1}
RE_PAGINAS_INTERVALO = re.compile(r"\bpaginas?\s+(\d+)\s*(?:ate a|ate|a|-)\s*(\d+)\b")
RE_PAGINAS_DESDE = re.compile(r"\b(?:da|a partir da) pagina\s+(\d+)(?:\s+em diante|\s+ate o fim|\s+pra frente)?\b")
RE_PAGINAS_LISTA = re.compile(r"\bpaginas?\s+((?:\d+\s*(?:,|e)\s*)+\d+)\b")
RE_PAGINA = re.compile(r"\bpagina\s+(\d+)\b")
RE_PAGINA_ORDINAL = re.compile(r"\b(" + "|".join(ORDINAIS) + r") pagina\b")

RE_DITADO = re.compile(
    r"^\s*(?:(?:o |um |uma |esse |este |essa |esta )?"
    r"(texto|bilhete|recado|nota|frase|aviso|cartaz|placa|mensagem|lista(?: de [a-z]+(?: [a-z]+)?)?)"
    r"\s*(?::|,|com|dizendo|escrito|que diz|falando)\s+|:\s*|escrito\s+)(.+)$"
)
ARTIGOS_DITADO = {"bilhete": "o bilhete", "recado": "o recado", "nota": "a nota", "aviso": "o aviso",
                  "cartaz": "o cartaz", "placa": "a placa", "mensagem": "a mensagem", "frase": "a frase"}
RE_ULTIMA_RESPOSTA = re.compile(
    r"^\s*(?:isso|isto|isso ai|isso aqui|essa resposta|esta resposta|a (?:sua )?(?:ultima )?resposta|"
    r"o que (?:voce|vc) (?:disse|falou|respondeu|escreveu|acabou de (?:dizer|falar))|essa explicacao|esse texto|"
    r"essa receita|essa lista|esse resultado|esse resumo que (?:voce|vc) (?:disse|falou))\b"
)
RE_AGENDA = re.compile(r"\b(?:minha|a|da) agenda\b|\bmeus compromissos\b|\bminha rotina\b")
RE_BAIXADOS = re.compile(r"\b(?:baixei|baixado|baixada|downloads?|que eu salvei da internet)\b")
RE_AREA_DE_TRABALHO = re.compile(r"\barea de trabalho\b|\bdesktop\b")
RE_GERADOS = re.compile(r"\bque (?:voce|vc) (?:fez|criou|gerou|preparou|montou)\b")
RE_CAMINHO = re.compile(r"[A-Za-z]:\\[^\"'\n]+")

# Palavras do pedido que dizem o tipo de arquivo.
TIPOS_ARQUIVO = (
    (re.compile(r"\bpdfs?\b"), {".pdf"}, "PDF"),
    (re.compile(r"\bword\b|\bdocx\b"), EXTENSOES_WORD, "documento do Word"),
    (re.compile(r"\bplanilhas?\b|\bexcel\b|\bxlsx\b"), EXTENSOES_EXCEL, "planilha"),
    (re.compile(r"\bapresentac(?:ao|oes)\b|\bslides?\b|\bpower ?point\b|\bpptx?\b"), EXTENSOES_POWERPOINT, "apresentação"),
    (re.compile(r"\bfotos?\b|\bimage(?:m|ns)\b|\bfiguras?\b"), EXTENSOES_IMAGEM, "imagem"),
)
NOMES_POR_EXTENSAO = [({".pdf"}, "o PDF"), (EXTENSOES_WORD, "o documento"), (EXTENSOES_EXCEL, "a planilha"),
                      (EXTENSOES_POWERPOINT, "a apresentação"), (EXTENSOES_IMAGEM, "a imagem"),
                      (EXTENSOES_TEXTO, "o texto")]
PALAVRAS_VAZIAS = set("""
o a os as um uma uns umas de do da dos das no na nos nas em pra para por com sem sobre que e eu me meu minha
meus minhas seu sua voce vc ele ela esse essa este esta isso aquele aquela ultimo ultima mais recente novo nova
arquivo arquivos documento documentos pdf pdfs word docx planilha planilhas excel apresentacao slides slide
powerpoint foto fotos imagem imagens figura chamado chamada nome fiz fez criou gerou preparou montou baixei
baixado baixada download downloads salvei agora ai la aqui favor so somente apenas copia copias via vias vezes
pagina paginas preto branco cor cores colorido colorida cinza tons tinta impressora pode imprime imprima imprimir
manda mandar bota coloca area trabalho desktop internet tambem todo toda inteiro inteira hoje ontem
sim ja logo entao vai bora beleza claro okay
""".split()) | set(NUMEROS) | set(ORDINAIS)
NOMES_EXCLUIDOS = {"fontes.md", "relatorio.md"}

DIAS_SEMANA = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]


@dataclass
class Documento:
    descricao: str  # 'o PDF "Relatório fotossíntese"'
    pdf: str
    paginas: int
    temporario: bool = False  # PDF criado aqui (texto, conversão): apagado depois de imprimir


@dataclass
class Opcoes:
    copias: int = 1
    colorido: bool = True
    paginas: list = field(default_factory=list)  # [(início, fim)] a partir de 1; -1 = a última página

    def indices(self, total: int) -> list[int] | None:
        """Índices a partir de 0 das páginas pedidas (None = todas)."""
        if not self.paginas:
            return None
        indices = []
        for inicio, fim in self.paginas:
            inicio = total if inicio == -1 else inicio
            fim = total if fim == -1 else fim
            for numero in range(inicio, fim + 1):
                if 1 <= numero <= total and numero - 1 not in indices:
                    indices.append(numero - 1)
        return indices


@dataclass
class Pendencia:
    tipo: str  # confirmar | o_que
    ate: float
    documentos: list = field(default_factory=list)
    opcoes: Opcoes = field(default_factory=Opcoes)


def _texto_preparado(texto: str) -> tuple[str, str]:
    """(original, sem acentos) sem o nome e sem 'por favor', 'pode'... no começo (mesmo tamanho)."""
    original, t = preparar(texto.strip())
    for padrao in (RE_CHAMADA, RE_POLIDEZ, RE_POLIDEZ):
        achado = padrao.match(t)
        if achado:
            original, t = original[achado.end():], t[achado.end():]
    return original.strip(), t.strip()


def _numero(palavra: str) -> int:
    return int(palavra) if palavra.isdigit() else NUMEROS.get(palavra, 1)


def ler_opcoes(t: str, base: Opcoes | None = None) -> tuple[Opcoes, bool]:
    """Cópias, páginas e cor ditas no pedido (texto sem acentos). Devolve (opções, achou alguma)."""
    opcoes = Opcoes(**vars(base)) if base else Opcoes()
    opcoes.paginas = list(opcoes.paginas)
    achou = False
    copias = RE_COPIAS.search(t)
    if copias:
        opcoes.copias, achou = max(1, min(MAXIMO_COPIAS, _numero(copias.group(1)))), True
    if RE_PRETO_E_BRANCO.search(t):
        opcoes.colorido, achou = False, True
    elif RE_COLORIDO.search(t):
        opcoes.colorido, achou = True, True
    paginas = []
    if (intervalo := RE_PAGINAS_INTERVALO.search(t)):
        paginas = [(int(intervalo.group(1)), int(intervalo.group(2)))]
    elif (desde := RE_PAGINAS_DESDE.search(t)) and re.search(r"em diante|ate o fim|pra frente|a partir", t):
        paginas = [(int(desde.group(1)), -1)]
    elif (lista := RE_PAGINAS_LISTA.search(t)):
        paginas = [(int(n), int(n)) for n in re.findall(r"\d+", lista.group(1))]
    elif (uma := RE_PAGINA.search(t)):
        paginas = [(int(uma.group(1)), int(uma.group(1)))]
    elif (ordinal := RE_PAGINA_ORDINAL.search(t)):
        numero = ORDINAIS[ordinal.group(1)]
        paginas = [(numero, numero)]
    if paginas:
        opcoes.paginas, achou = paginas, True
    return opcoes, achou


def _apagar_temporarios(documentos):
    for documento in documentos:
        if documento.temporario:
            try:
                os.remove(documento.pdf)
            except OSError:
                pass


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _nome_legivel(caminho: str) -> str:
    base = os.path.splitext(os.path.basename(caminho))[0]
    base = re.sub(r"^\d{4}-\d{2}-\d{2}[_ -]?", "", base)  # data das pastas/arquivos dos agentes
    return re.sub(r"[_]+", " ", base).strip() or os.path.basename(caminho)


def descrever_arquivo(caminho: str) -> str:
    extensao = os.path.splitext(caminho)[1].lower()
    tipo = next((nome for extensoes, nome in NOMES_POR_EXTENSAO if extensao in extensoes), "o arquivo")
    return f'{tipo} "{_nome_legivel(caminho)}"'


def texto_da_agenda(agente_agenda, t: str) -> tuple[str, str]:
    """(título, texto) da agenda local para imprimir: hoje, amanhã ou os próximos 7 dias."""
    hoje = agente_agenda.agora().date()
    if re.search(r"\bsemana\b|\bproximos dias\b", t):
        dias, titulo = [hoje + timedelta(days=i) for i in range(7)], "Agenda da semana"
    elif re.search(r"\bamanha\b", t):
        dias, titulo = [hoje + timedelta(days=1)], "Agenda de amanhã"
    else:
        dias, titulo = [hoje], "Agenda de hoje"
    linhas = []
    for dia in dias:
        linhas.append(f"# {DIAS_SEMANA[dia.weekday()]}, {dia.day} de {MESES[dia.month - 1]}")
        ocorrencias = agente_agenda.agenda.ocorrencias_do_dia(dia)
        for o in ocorrencias:
            if o.evento.dia_inteiro:
                quando = "o dia todo"
            elif o.evento.tipo == "lembrete" or not o.evento.duracao:
                quando = o.inicio.strftime("%H:%M")
            else:
                quando = f"{o.inicio:%H:%M} às {o.fim:%H:%M}"
            local = f" ({o.evento.local})" if o.evento.local else ""
            linhas.append(f"- {quando}: {o.evento.titulo}{local}")
        if not ocorrencias:
            linhas.append("Nada marcado.")
        linhas.append("")
    tarefas = [tarefa for tarefa in agente_agenda.agenda.tarefas() if tarefa.prazo is None or tarefa.prazo <= dias[-1]]
    if tarefas:
        linhas.append("# Tarefas")
        for tarefa in tarefas:
            prazo = f" (até {tarefa.prazo.day:02d}/{tarefa.prazo.month:02d})" if isinstance(tarefa.prazo, date) else ""
            linhas.append(f"- [ ] {tarefa.titulo}{prazo}")
    return titulo, "\n".join(linhas)


def pede_impressao(comando: str) -> bool:
    """O pedido de tarefa já diz para imprimir o resultado ("faz um PDF sobre X e imprime")?"""
    _, t = preparar(comando or "")
    return bool(re.search(r"\be (?:depois |ja |tambem )?(?:imprim[ae]|manda (?:pra|para a) impressora)\b", t))


class AgenteImpressora:
    def __init__(self, impressora, pastas_busca=(), pasta_gerados: str = "", confirmar: bool = True,
                 respostas_recentes=None, texto_agenda=None, avisar=None, executar=None, relogio=time.time):
        self.impressora = impressora  # ImpressoraWindows (ou um falso nos testes)
        self.pastas_busca = [p for p in pastas_busca if p]
        self.pasta_gerados = pasta_gerados
        self.confirmar = confirmar
        self.respostas_recentes = respostas_recentes or (lambda: [])  # mais nova primeiro
        self.texto_agenda = texto_agenda  # função(t) -> (título, texto)
        self.avisar = avisar or (lambda texto: None)  # erro na impressão em segundo plano
        self.executar = executar or (lambda funcao: threading.Thread(target=funcao, daemon=True,
                                                                      name="impressao").start())
        self.relogio = relogio
        self.pendencia: Pendencia | None = None
        self._minhas_respostas: list[str] = []
        self._trava = threading.RLock()

    # ================================================================== entrada
    def responder(self, texto: str) -> str | None:
        if not (texto or "").strip():
            return None
        with self._trava:
            try:
                resposta = self._responder(texto)
            except ErroImpressora as exc:
                self._descartar_pendencia()
                resposta = f"Não consegui: {exc}."
            except ModuleNotFoundError as exc:
                self._descartar_pendencia()
                logger.warning("Impressão sem o módulo %s", exc.name)
                resposta = ("Para imprimir falta instalar o pywin32: "
                            ".venv312\\Scripts\\python.exe -m pip install pywin32")
            except Exception as exc:
                self._descartar_pendencia()
                logger.exception("Falha no agente da impressora")
                resposta = f"Tive um problema com a impressora: {exc}"
            if resposta:
                self._minhas_respostas = (self._minhas_respostas + [resposta])[-10:]
            return resposta

    def _descartar_pendencia(self):
        """Esquece a pergunta guardada e apaga os PDFs temporários dela, que não vão mais ser impressos."""
        pendencia, self.pendencia = self.pendencia, None
        if pendencia:
            _apagar_temporarios(pendencia.documentos)

    def _responder(self, texto: str) -> str | None:
        original, t = _texto_preparado(texto)
        if not t:
            return None
        if self.pendencia and self.pendencia.ate > self.relogio():
            resposta = self._continuar(original, t)
            if resposta is not None:
                return resposta
        self._descartar_pendencia()

        if RE_LIGAR.search(t):
            return self._ligar()
        if RE_DESLIGAR.search(t):
            return self._desligar()
        if RE_CANCELAR.search(t):
            return self._cancelar()
        if RE_PAUSAR.search(t):
            return self._pausar(True)
        if RE_RETOMAR.search(t):
            return self._pausar(False)
        if RE_FILA.search(t):
            return self._fila()
        if RE_LISTAR.search(t):
            return self._listar()
        verbo = RE_VERBO_IMPRIMIR.search(t)
        if verbo:
            if RE_CRIAR_DOCUMENTO.search(t):
                return None  # "faz um PDF sobre X e imprime": os agentes criam, e a impressão vem depois
            return self._pedido(original[verbo.end():], t[verbo.end():], t)
        if RE_STATUS.search(t):
            return self._status()
        return None

    def _continuar(self, original: str, t: str) -> str | None:
        pendencia = self.pendencia
        if pendencia.tipo == "o_que":
            if RE_NAO.match(t):
                self._descartar_pendencia()
                return "Tudo bem, não imprimo nada."
            if RE_VERBO_IMPRIMIR.search(t):
                return None  # pedido completo: segue o caminho normal
            return self._pedido(original, t, t, perguntar_se_vazio=False)

        # "Imprime" também é um "sim", mas "imprime: comprar pão" no lugar da resposta é um pedido novo.
        verbo = RE_VERBO_IMPRIMIR.search(t)
        if verbo:
            depois = t[verbo.end():]
            if (RE_DITADO.match(depois) or RE_ULTIMA_RESPOSTA.match(depois) or RE_AGENDA.search(depois)
                    or self._termos(depois) or self._tipo_pedido(depois)[1]):
                return None
        opcoes, mudou = ler_opcoes(t, pendencia.opcoes)
        if RE_SIM.match(t) and not RE_NAO.match(t):
            pendencia.opcoes = opcoes
            self.pendencia = None
            return self._imprimir(pendencia.documentos, opcoes)
        if mudou:  # "em preto e branco", "não, só a página 2": refaz a pergunta com a mudança
            pendencia.opcoes = opcoes
            pendencia.ate = self.relogio() + SEGUNDOS_PARA_CONFIRMAR
            return self._pergunta(pendencia.documentos, opcoes)
        if RE_NAO.match(t):
            self._descartar_pendencia()
            resto = re.sub(r"^(?:nao|cancela|deixa|esquece)[\s,.!]*", "", t)
            if RE_VERBO_IMPRIMIR.search(resto):
                return None  # "não, imprime o outro": vira um pedido novo
            return "Tudo bem, não imprimi."
        return None

    # ================================================================ pedidos
    def _pedido(self, original: str, resto: str, t: str, perguntar_se_vazio: bool = True) -> str | None:
        opcoes, _ = ler_opcoes(t)
        documentos = self._documentos_do_pedido(original, resto, t)
        if documentos is None:
            if not perguntar_se_vazio:
                self._descartar_pendencia()
                return None
            self.pendencia = Pendencia("o_que", self.relogio() + SEGUNDOS_PARA_CONFIRMAR)
            return ("O que você quer imprimir? Pode ser um arquivo (diga o nome), o último PDF que eu fiz, "
                    "a minha última resposta ou um texto que você ditar.")
        if isinstance(documentos, str):
            return documentos  # explicação (não achei o arquivo...)
        if not self.confirmar:
            return self._imprimir(documentos, opcoes)
        self.pendencia = Pendencia("confirmar", self.relogio() + SEGUNDOS_PARA_CONFIRMAR, documentos, opcoes)
        return self._pergunta(documentos, opcoes)

    def _documentos_do_pedido(self, original: str, resto: str, t: str):
        """Lista de Documento, uma explicação (str) ou None quando o pedido não diz o que imprimir."""
        ditado = RE_DITADO.match(resto)
        if ditado:
            tipo, inicio = ditado.group(1), ditado.start(2)
            return [self._documento_de_texto(self._titulo_do_ditado(tipo, original), original[inicio:].strip(), tipo)]

        if RE_ULTIMA_RESPOSTA.match(resto):
            ultima = next((r for r in self.respostas_recentes() if r and r not in self._minhas_respostas), "")
            if not ultima:
                return "Ainda não tenho uma resposta minha para imprimir."
            pdf = self.impressora.preparar_texto("", ultima)
            return [Documento("a minha última resposta", pdf, self.impressora.contar_paginas(pdf), True)]

        if RE_AGENDA.search(resto) and self.texto_agenda:
            titulo, texto = self.texto_agenda(t)
            pdf = self.impressora.preparar_texto(titulo, texto)
            return [Documento(f"a {titulo[0].lower()}{titulo[1:]}", pdf, self.impressora.contar_paginas(pdf), True)]

        caminho_dito = RE_CAMINHO.search(original)
        if caminho_dito and os.path.isfile(caminho_dito.group(0).strip()):
            return [self._documento_de_arquivo(caminho_dito.group(0).strip())]

        extensoes, tipo = self._tipo_pedido(resto)
        termos = self._termos(resto)
        if not termos and tipo is None and not re.search(r"\b(?:arquivo|documento|ultimo|ultima)\b", resto):
            return None
        caminho = self._procurar_arquivo(termos, extensoes, resto)
        if not caminho:
            onde = "nos seus downloads" if RE_BAIXADOS.search(resto) else "nas suas pastas"
            o_que = f"{tipo or 'arquivo'} com \"{' '.join(termos)}\"" if termos else (tipo or "arquivo")
            return f"Não achei nenhum {o_que} {onde}. Diga uma parte do nome do arquivo."
        return [self._documento_de_arquivo(caminho)]

    @staticmethod
    def _titulo_do_ditado(tipo: str, original: str) -> str:
        if not tipo or tipo in {"texto", "frase", "mensagem"}:
            return ""
        return tipo[0].upper() + tipo[1:] if tipo.startswith("lista") else tipo.capitalize()

    def _documento_de_texto(self, titulo: str, texto: str, tipo: str) -> Documento:
        if tipo and tipo.startswith("lista") and "\n" not in texto:
            itens = [item.strip(" .") for item in re.split(r",|;|\s+e\s+", texto) if item.strip(" .")]
            texto = "\n".join(f"- {item}" for item in itens)
        pdf = self.impressora.preparar_texto(titulo, texto)
        resumo = re.sub(r"\s+", " ", texto.replace("- ", "")).strip()
        resumo = resumo if len(resumo) <= 50 else resumo[:47].rstrip() + "..."
        if titulo.startswith("Lista"):
            nome = "a " + titulo.lower()
        else:
            nome = ARTIGOS_DITADO.get(tipo or "", "o texto")
        descricao = f'{nome} "{resumo}"'
        return Documento(descricao, pdf, self.impressora.contar_paginas(pdf), True)

    def _documento_de_arquivo(self, caminho: str) -> Documento:
        pdf = self.impressora.preparar(caminho)
        return Documento(descrever_arquivo(caminho), pdf, self.impressora.contar_paginas(pdf), pdf != caminho)

    def achar_arquivo(self, resto: str, original: str = "") -> str | None:
        """O arquivo que um pedido descreve ("o pdf da oab dos downloads"), ou None. `resto` vem em minúsculas e sem acentos."""
        resto = (resto or "").lower()
        extensoes, tipo = self._tipo_pedido(resto)
        termos = self._termos(resto)
        if not termos and tipo is None:
            return None
        with self._trava:
            return self._procurar_arquivo(termos, extensoes, resto)

    def imprimir_arquivos(self, caminhos, texto: str = "") -> str:
        """Pede a impressão de arquivos que já se conhecem (os que chegaram pelo WhatsApp ou foram escaneados). As opções ditas
        ("2 cópias", "só a página 1", "preto e branco") valem; pergunta antes, como sempre, e o "sim" imprime."""
        with self._trava:
            try:
                _, t = _texto_preparado(texto or "")
                opcoes, _ = ler_opcoes(t)
                documentos = [self._documento_de_arquivo(c) for c in caminhos if os.path.isfile(c)]
                if not documentos:
                    return "Não achei o arquivo para imprimir."
                if not self.confirmar:
                    return self._imprimir(documentos, opcoes)
                self._descartar_pendencia()
                self.pendencia = Pendencia("confirmar", self.relogio() + SEGUNDOS_PARA_CONFIRMAR * 2, documentos, opcoes)
                resposta = self._pergunta(documentos, opcoes)
                self._minhas_respostas = (self._minhas_respostas + [resposta])[-10:]
                return resposta
            except ErroImpressora as exc:
                return f"Não consegui: {exc}."
            except Exception as exc:
                logger.exception("Falha ao preparar a impressão de arquivos recebidos")
                return f"Não consegui preparar a impressão: {exc}"

    def oferecer_arquivos(self, arquivos) -> str | None:
        """Depois de uma tarefa pedida com "e imprime": pergunta se imprime os arquivos (ou imprime direto)."""
        with self._trava:
            imprimiveis = [a for a in arquivos or () if os.path.splitext(a)[1].lower() in EXTENSOES_IMPRIMIVEIS
                           and os.path.basename(a).lower() not in NOMES_EXCLUIDOS and not a.endswith("_anki.txt")]
            if not imprimiveis:
                return None
            # Do mesmo conteúdo em PDF e Word, imprime o PDF.
            pdfs = [a for a in imprimiveis if a.lower().endswith(".pdf")]
            escolhidos = pdfs or imprimiveis[:1]
            try:
                documentos = [self._documento_de_arquivo(a) for a in escolhidos]
            except Exception as exc:
                logger.warning("Não preparei a impressão da tarefa: %s", exc)
                return f"Não consegui preparar a impressão: {exc}."
            if not self.confirmar:
                return self._imprimir(documentos, Opcoes())
            self._descartar_pendencia()
            self.pendencia = Pendencia("confirmar", self.relogio() + SEGUNDOS_PARA_CONFIRMAR * 2, documentos)
            resposta = self._pergunta(documentos, Opcoes())
            self._minhas_respostas = (self._minhas_respostas + [resposta])[-10:]
            return resposta

    # ------------------------------------------------------------ arquivos
    @staticmethod
    def _tipo_pedido(resto: str):
        for padrao, extensoes, nome in TIPOS_ARQUIVO:
            if padrao.search(resto):
                return set(extensoes), nome
        return set(EXTENSOES_IMPRIMIVEIS), None

    @staticmethod
    def _termos(resto: str) -> list[str]:
        sem_opcoes = RE_PRETO_E_BRANCO.sub(" ", RE_COPIAS.sub(" ", resto))
        sem_opcoes = re.sub(r"\bpaginas?\s+\d+(?:\s*(?:,|e|a|ate|-)\s*\d+)*", " ", sem_opcoes)
        palavras = re.findall(r"[a-z0-9]+", sem_opcoes)
        return [p for p in palavras if p not in PALAVRAS_VAZIAS and len(p) >= 3 and not p.isdigit()]

    def _pastas(self, resto: str) -> list[tuple[str, int]]:
        """(pasta, profundidade) onde procurar, conforme o pedido."""
        casa = os.path.expanduser("~")
        downloads, desktop = os.path.join(casa, "Downloads"), os.path.join(casa, "Desktop")
        if RE_BAIXADOS.search(resto):
            return [(downloads, 2)]
        if RE_AREA_DE_TRABALHO.search(resto):
            return [(desktop, 2)]
        if RE_GERADOS.search(resto) and self.pasta_gerados:
            return [(self.pasta_gerados, 3)]
        pastas = [(self.pasta_gerados, 3)] if self.pasta_gerados else []
        pastas += [(p, 4) for p in self.pastas_busca]
        pastas += [(downloads, 2), (desktop, 2), (os.path.join(casa, "Documents"), 2)]
        return pastas

    @staticmethod
    def _arquivos(pastas):
        """(pasta, caminho) dos arquivos das pastas de busca, até MAXIMO_ARQUIVOS_VARRIDOS."""
        vistos = 0
        for pasta, profundidade in pastas:
            if not os.path.isdir(pasta):
                continue
            base = pasta.rstrip("\\/").count(os.sep)
            for raiz, subpastas, arquivos in os.walk(pasta):
                if raiz.count(os.sep) - base >= profundidade:
                    subpastas[:] = []
                subpastas[:] = [s for s in subpastas if not s.startswith(".") and s != "beatriz_impressao"]
                for nome in arquivos:
                    vistos += 1
                    if vistos > MAXIMO_ARQUIVOS_VARRIDOS:
                        return
                    yield pasta, os.path.join(raiz, nome)

    @staticmethod
    def _arquivo_interno(nome: str, resto: str) -> bool:
        """Arquivos de apoio das tarefas (relatorio.md, fontes.md, cartões do Anki), só se pedidos pelo nome."""
        nome = nome.lower()
        if nome == "fontes.md":
            return "fontes" not in resto
        if nome == "relatorio.md":
            return not re.search(r"\brelatorio d[ao]s? (?:tarefa|agentes?)\b", resto)
        if nome.endswith("_anki.txt"):
            return not re.search(r"\banki\b", resto)
        return False

    def _procurar_arquivo(self, termos: list[str], extensoes: set, resto: str) -> str | None:
        # "Imprime o PDF", sem nome nem lugar: o último que ela mesma fez, antes dos downloads e afins.
        if not termos and self.pasta_gerados and not (RE_BAIXADOS.search(resto) or RE_AREA_DE_TRABALHO.search(resto)):
            achado = self._melhor_arquivo(termos, extensoes, resto, [(self.pasta_gerados, 3)])
            if achado:
                return achado
        return self._melhor_arquivo(termos, extensoes, resto, self._pastas(resto))

    def _melhor_arquivo(self, termos: list[str], extensoes: set, resto: str, pastas) -> str | None:
        candidatos = []  # (acertos, prioridade, data, caminho)
        for pasta, caminho in self._arquivos(pastas):
            nome = os.path.basename(caminho)
            extensao = os.path.splitext(nome)[1].lower()
            if extensao not in extensoes or nome.startswith("~$") or self._arquivo_interno(nome, resto):
                continue
            # O nome das pastas conta: as tarefas dos agentes ficam em documentos_gerados/<data>_<tema>/.
            texto_busca = preparar(os.path.relpath(caminho, pasta))[1]
            acertos = sum(1 for termo in termos if termo in texto_busca or termo.rstrip("s") in texto_busca)
            if termos and acertos * 2 < len(termos):
                continue
            try:
                data = os.path.getmtime(caminho)
            except OSError:
                continue
            prioridade = 0 if extensao in EXTENSOES_TEXTO else 1  # entre iguais, o documento de verdade
            candidatos.append((acertos, prioridade, data, caminho))
        if not candidatos:
            return None
        return max(candidatos)[3]

    # ============================================================== impressão
    def _pergunta(self, documentos: list, opcoes: Opcoes) -> str:
        folhas = self._folhas(documentos, opcoes)
        if len(documentos) == 1:
            o_que = documentos[0].descricao
        else:
            o_que = f"{len(documentos)} arquivos ({', '.join(d.descricao for d in documentos[:3])}" + \
                    (", ..." if len(documentos) > 3 else "") + ")"
        detalhes = [_plural(folhas, "página", "páginas")]
        if opcoes.copias > 1:
            detalhes.append(_plural(opcoes.copias, "cópia", "cópias"))
        if not opcoes.colorido:
            detalhes.append("em preto e branco")
        aviso = self._aviso_estado()
        return f"Vou imprimir {o_que}: {', '.join(detalhes)}, na {self._nome()}.{aviso} Posso?"

    def _folhas(self, documentos: list, opcoes: Opcoes) -> int:
        total = 0
        for documento in documentos:
            indices = opcoes.indices(documento.paginas) if len(documentos) == 1 else None
            total += len(indices) if indices is not None else documento.paginas
        return total * max(1, opcoes.copias)

    def _aviso_estado(self) -> str:
        try:
            estado = self.impressora.estado()
        except Exception as exc:
            logger.debug("Sem o estado da impressora: %s", exc)
            return ""
        if estado.ligada is False:
            return " Ela parece desligada: ligue antes, senão a impressão espera na fila."
        if estado.problemas:
            return f" Atenção: ela {estado.problemas[0]}."
        if estado.pausada:
            return " A fila está pausada: a impressão só sai quando retomar."
        return ""

    def _imprimir(self, documentos: list, opcoes: Opcoes) -> str:
        if len(documentos) == 1 and opcoes.indices(documentos[0].paginas) == []:
            return (f"Esse documento só tem {_plural(documentos[0].paginas, 'página', 'páginas')}; "
                    "as páginas pedidas não existem.")
        folhas = self._folhas(documentos, opcoes)

        def trabalho():
            for documento in documentos:
                try:
                    indices = opcoes.indices(documento.paginas) if len(documentos) == 1 else None
                    self.impressora.imprimir_pdf(documento.pdf, copias=opcoes.copias, paginas=indices,
                                                 colorido=opcoes.colorido, titulo=documento.descricao)
                except Exception as exc:
                    logger.exception("Falha ao imprimir %s", documento.pdf)
                    self.avisar(f"Não consegui imprimir {documento.descricao}: {exc}")
                finally:
                    _apagar_temporarios([documento])

        self.executar(trabalho)
        o_que = documentos[0].descricao if len(documentos) == 1 else f"{len(documentos)} arquivos"
        return f"Mandei para a impressora {o_que}: {_plural(folhas, 'página', 'páginas')}."

    # ========================================================= estado e fila
    def _nome(self) -> str:
        try:
            return nome_curto(self.impressora.nome())
        except Exception:
            return "impressora"

    def _status(self) -> str:
        estado = self.impressora.estado()
        nome = nome_curto(estado.nome)
        if estado.ligada is False:
            partes = [f"A {nome} está desligada (ou com o cabo USB solto)."]
            if estado.trabalhos:
                partes.append(f"Tem {_plural(estado.trabalhos, 'impressão esperando', 'impressões esperando')}: "
                              "saem quando você ligar.")
            return " ".join(partes)
        inicio = f"A {nome} está ligada" if estado.ligada else f"A {nome} está instalada (não consigo ver se está ligada)"
        fila = (", sem nada na fila" if not estado.trabalhos
                else f", com {_plural(estado.trabalhos, 'impressão', 'impressões')} na fila")
        partes = [inicio + fila + "."]
        if estado.problemas:
            partes.append(f"Ela {' e '.join(estado.problemas)}.")
        if estado.pausada:
            partes.append("A fila está pausada.")
        return " ".join(partes)

    def _fila(self) -> str:
        trabalhos = self.impressora.trabalhos()
        if not trabalhos:
            return "A fila está vazia: já saiu tudo."
        itens = [f"{(t.documento or 'sem nome').replace('Beatriz - ', '')} ({t.situacao})" for t in trabalhos[:3]]
        mais = f" e mais {len(trabalhos) - 3}" if len(trabalhos) > 3 else ""
        return f"Na fila: {'; '.join(itens)}{mais}."

    def _cancelar(self) -> str:
        cancelados = self.impressora.cancelar_todos()
        if not cancelados:
            return "Não tem nada na fila para cancelar."
        return (f"Cancelei {_plural(cancelados, 'impressão', 'impressões')}. "
                "O que já estava dentro da impressora ainda pode terminar de sair.")

    def _pausar(self, pausar: bool) -> str:
        nome = self._nome()
        try:
            if pausar:
                self.impressora.pausar()
                return f"Pausei a fila da {nome}: nada sai até você pedir para retomar."
            self.impressora.retomar()
            return f"Retomei a fila da {nome}."
        except ErroImpressora as exc:
            # Sem o painel aberto como administrador, o Windows não deixa pausar a fila.
            dica = " Para parar o que está saindo, diga: cancela a impressão." if pausar else ""
            return f"Não consegui: {exc}.{dica}"

    def _listar(self) -> str:
        nome = self.impressora.nome()
        outras = [n for n in self.impressora.listar() if n != nome]
        texto = f"Eu imprimo na {nome_curto(nome)}."
        if outras:
            texto += f" Também estão instaladas: {', '.join(outras)}."
        return texto

    def _ligar(self) -> str:
        estado = self.impressora.estado()
        nome = nome_curto(estado.nome)
        if estado.ligada:
            return f"A {nome} já está ligada."
        texto = (f"Não consigo ligar a {nome} daqui: ela está no USB e não liga por comando do computador. "
                 "Aperte o botão de energia dela.")
        if estado.trabalhos:
            texto += f" Quando ela ligar, sai o que está na fila ({_plural(estado.trabalhos, 'impressão', 'impressões')})."
        return texto

    def _desligar(self) -> str:
        estado = self.impressora.estado()
        nome = nome_curto(estado.nome)
        if estado.ligada is False:
            return f"A {nome} já está desligada."
        texto = (f"Não consigo desligar a {nome} pelo computador: aperte o botão de energia dela. "
                 "Evite tirar da tomada com ela ligada, para a cabeça de impressão não secar.")
        if estado.trabalhos:
            texto += f" Antes, veja que ainda tem {_plural(estado.trabalhos, 'impressão', 'impressões')} na fila."
        return texto


def criar_agente_impressora(respostas_recentes=None, texto_agenda=None, avisar=None) -> AgenteImpressora:
    """Agente com a impressora e as pastas do .env (config.py)."""
    from config import (CAMINHO_DOCUMENTOS, IMPRESSORA_CONFIRMAR, IMPRESSORA_DPI, IMPRESSORA_NOME,
                        PASTA_DOCUMENTOS_GERADOS)
    from impressora import ImpressoraWindows

    return AgenteImpressora(ImpressoraWindows(IMPRESSORA_NOME, IMPRESSORA_DPI), pastas_busca=[CAMINHO_DOCUMENTOS],
                            pasta_gerados=PASTA_DOCUMENTOS_GERADOS, confirmar=IMPRESSORA_CONFIRMAR,
                            respostas_recentes=respostas_recentes, texto_agenda=texto_agenda, avisar=avisar)
