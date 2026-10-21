"""Fontes e arquivos recebidos: mande um arquivo pelo WhatsApp (ou escaneie uma folha) e peça o que fazer com ele.

    "vira fonte" / "adiciona como fonte"   copia para a biblioteca (Vault) e indexa; fotos e PDFs escaneados são lidos por OCR
    "arquiva" / "arquiva na pasta da OAB"  copia para a pasta de arquivados (por mês) ou para a pasta que você disser
    "imprime" (com cópias, páginas...)     pergunta e imprime pela fila do Windows (agente_impressora)
    "escaneia"                             com um arquivo recente, lê e ajuda; sem ele (ou com "scanner", "folha", "papel"),
                                           escaneia a folha do vidro da impressora multifuncional (scanner.py)
    "resume", "explica", "traduz", "lê"    responde sobre o arquivo com o Ollama local (o conteúdo não sai do PC); com "pelo Gemini"
                                           ou "pelo Claude" (ou FONTES_IA no .env) quem lê e formula a resposta é essa IA da nuvem
    "me manda no zap"                      devolve o arquivo (ou o escaneamento) pelo WhatsApp

"Isso", "esse arquivo", "o que eu mandei" e "todos" falam do que chegou nos últimos 20 minutos (a rajada mais recente).
Os arquivos recebidos ficam em `PASTA_RECEBIDOS`, por dia, e o registro (`arquivos_recebidos.json`) é compartilhado entre
o processo principal, onde o WhatsApp roda, e os painéis.
"""
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

VALIDADE_DOS_RECENTES = 20 * 60  # "isso" é o que chegou até 20 min atrás
VALIDADE_DA_LEITURA = 5 * 60  # "resume esse PDF" é o arquivo se ele chegou há menos de 5 min; depois, é o que está na tela
JANELA_DA_RAJADA = 90  # arquivos recebidos com até 90 s de diferença são "o mesmo lote"
VALIDADE_DA_PERGUNTA = 5 * 60
VALIDADE_DO_SEGUIMENTO = 3 * 60  # depois de ler um arquivo, uma pergunta sem o nome dele ainda pode ser sobre ele
LIMITE_DO_TEXTO = 7000  # caracteres do arquivo que vão ao modelo de conversa (cabe nos 8192 tokens com a resposta)
LIMITE_DO_TEXTO_NUVEM = 60000  # o Claude e o Gemini leem muito mais de uma vez
CONTEXTO_DO_ARQUIVO = "Você está ajudando com um arquivo que o usuário mandou. Não invente."
NOMES_DA_IA = {"claude": "Claude", "gemini": "Gemini", "nuvem": "a nuvem"}
PEDINDO_A = {"claude": "ao Claude", "gemini": "ao Gemini", "nuvem": "à IA da nuvem"}  # o Gemini leva de 15 a 40 s: ela avisa antes

EXTENSOES_BLOQUEADAS = {
    ".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".lnk", ".scr", ".dll",
    ".jar", ".reg", ".hta", ".cpl", ".msc", ".pif", ".apk", ".sh", ".py", ".pyw",
}
TIPOS = {
    **dict.fromkeys((".pdf",), "PDF"),
    **dict.fromkeys((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"), "foto"),
    **dict.fromkeys((".doc", ".docx", ".odt", ".rtf"), "documento"),
    **dict.fromkeys((".xls", ".xlsx", ".csv", ".ods"), "planilha"),
    **dict.fromkeys((".ppt", ".pptx", ".odp"), "apresentação"),
    **dict.fromkeys((".txt", ".md", ".json", ".html", ".htm", ".xml"), "texto"),
}


# ------------------------------------------------------------------ utilidades
def separar_ia(texto: str) -> tuple[str, str | None]:
    """"resume isso pelo Gemini" -> ("resume isso", "gemini"). Também "pelo Claude", "na nuvem" (a ordem de ORDEM_IA_NUVEM) e
    "só no PC" (local). Sem nada disso, (texto, None) e vale o padrão (FONTES_IA)."""
    from llm_nuvem import extrair_ia_pedida

    texto = texto or ""
    def limpar(restante: str) -> str:
        return re.sub(r"\s+([,.;:!?])", r"\1", re.sub(r"\s{2,}", " ", restante)).strip()

    sem_ia, ia = extrair_ia_pedida(texto)
    if ia:
        return limpar(sem_ia), ia
    for padrao, valor in ((RE_SO_NO_PC, "local"), (RE_NA_NUVEM, "nuvem")):
        achado = padrao.search(texto)
        if achado:
            return limpar(texto[:achado.start()] + " " + texto[achado.end():]), valor
    return texto, None


def cortar_texto(texto: str, limite: int) -> tuple[str, bool]:
    """O que passa do limite perde o meio (o começo e o fim dizem mais sobre o arquivo)."""
    if len(texto) <= limite:
        return texto, False
    metade = limite // 2
    return texto[:metade] + "\n[... meio do arquivo cortado ...]\n" + texto[-metade:], True


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento e só letras e números separados por espaço."""
    decomposto = unicodedata.normalize("NFKD", (texto or "").lower())
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", sem_acento).strip()


def nome_seguro(nome: str) -> str:
    """Só o nome do arquivo (sem caminho), sem caracteres proibidos no Windows e com tamanho razoável."""
    nome = os.path.basename((nome or "").replace("\\", "/")).strip().strip(".")
    nome = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nome)
    base, extensao = os.path.splitext(nome)
    return (base[:100].rstrip() or "arquivo") + extensao[:10].lower()


def caminho_livre(pasta: str, nome: str) -> str:
    """Caminho em `pasta` que ainda não existe: "x.pdf", "x (1).pdf", "x (2).pdf"..."""
    os.makedirs(pasta, exist_ok=True)
    base, extensao = os.path.splitext(nome)
    candidato, numero = os.path.join(pasta, nome), 1
    while os.path.exists(candidato):
        candidato = os.path.join(pasta, f"{base} ({numero}){extensao}")
        numero += 1
    return candidato


def _dentro_de(caminho: str, pasta: str) -> bool:
    try:
        pasta = os.path.normcase(os.path.abspath(pasta))
        return os.path.commonpath([os.path.normcase(os.path.abspath(caminho)), pasta]) == pasta
    except ValueError:  # discos diferentes
        return False


def tipo_do_arquivo(caminho: str) -> str:
    return TIPOS.get(os.path.splitext(caminho)[1].lower(), "arquivo")


def validar_arquivo(caminho: str, limite_mb: float) -> str:
    """"" se o arquivo pode ser aceito; senão, o motivo."""
    extensao = os.path.splitext(caminho)[1].lower()
    if extensao in EXTENSOES_BLOQUEADAS:
        return f"Não guardo arquivos {extensao} (podem ser programas)."
    if not os.path.isfile(caminho):
        return "O arquivo não chegou inteiro."
    if os.path.getsize(caminho) > limite_mb * 1024 * 1024:
        return f"O arquivo passa de {limite_mb:g} MB."
    return ""


def descrever(caminho: str) -> str:
    """"PDF de 3 páginas", "foto", "planilha"..."""
    tipo = tipo_do_arquivo(caminho)
    if tipo == "PDF":
        try:
            import fitz

            with fitz.open(caminho) as documento:
                paginas = len(documento)
            return f"PDF de {paginas} página{'s' if paginas != 1 else ''}"
        except Exception:
            return "PDF"
    return tipo


# ------------------------------------------------------------------ registro compartilhado
@dataclass
class Recebido:
    id: str
    caminho: str
    nome: str
    origem: str  # "whatsapp", "scanner" ou "pc"
    recebido_em: float
    legenda: str = ""
    acoes: list = field(default_factory=list)
    lido_em: float = 0.0  # a última vez que ela leu e respondeu sobre o arquivo (perguntas seguintes valem por pouco tempo)


class Registro:
    """Lista dos arquivos recebidos, em JSON (lido e gravado a cada uso: o WhatsApp roda no processo principal e os painéis,
    em outros)."""

    def __init__(self, caminho: str, relogio=time.time, maximo: int = 300):
        self.caminho, self.relogio, self.maximo = caminho, relogio, maximo
        self._trava = threading.Lock()

    def _ler(self) -> list[dict]:
        try:
            with open(self.caminho, encoding="utf-8") as arquivo:
                return list(json.load(arquivo))
        except (OSError, ValueError):
            return []

    def _gravar(self, itens: list[dict]):
        os.makedirs(os.path.dirname(self.caminho) or ".", exist_ok=True)
        descritor, temporario = tempfile.mkstemp(dir=os.path.dirname(self.caminho) or ".", suffix=".tmp")
        try:
            with os.fdopen(descritor, "w", encoding="utf-8") as arquivo:
                json.dump(itens[-self.maximo:], arquivo, ensure_ascii=False)
            os.replace(temporario, self.caminho)
        finally:
            if os.path.exists(temporario):
                os.remove(temporario)

    def registrar(self, caminho: str, nome: str, origem: str, legenda: str = "") -> Recebido:
        item = Recebido(uuid.uuid4().hex[:10], caminho, nome, origem, self.relogio(), legenda or "")
        with self._trava:
            itens = self._ler()
            itens.append(item.__dict__)
            self._gravar(itens)
        return item

    def todos(self) -> list[Recebido]:
        with self._trava:
            return [Recebido(**{k: v for k, v in i.items() if k in Recebido.__dataclass_fields__}) for i in self._ler()]

    def recentes(self, validade: float = VALIDADE_DOS_RECENTES) -> list[Recebido]:
        """Os recebidos há menos de `validade` segundos cujo arquivo ainda existe, do mais novo para o mais antigo."""
        agora = self.relogio()
        achados = [r for r in self.todos() if agora - r.recebido_em <= validade and os.path.isfile(r.caminho)]
        return sorted(achados, key=lambda r: -r.recebido_em)

    def atualizar(self, identificador: str, acao: str = "", caminho: str = ""):
        with self._trava:
            itens = self._ler()
            for item in itens:
                if item.get("id") == identificador:
                    if acao:
                        item.setdefault("acoes", []).append(acao)
                        if acao == "lido":
                            item["lido_em"] = self.relogio()
                    if caminho:
                        item["caminho"] = caminho
            self._gravar(itens)


# ------------------------------------------------------------------ o que a pessoa pediu
VERBOS_DE_ADICIONAR = (r"(?:vira\w*|vire|transforma\w*|adiciona\w*|adicione|coloca\w*|bota\w*|salva\w*|guarda\w*|usa\w*|use|"
                       r"importa\w*|incorpora\w*|inclui\w*|cadastra\w*|poe)")
RE_FONTE = re.compile(rf"\b{VERBOS_DE_ADICIONAR}\b(?:\W+\w+){{0,8}}?\W+(?:fontes?|biblioteca)\b|\bcomo\W+(?:uma\W+)?fontes?\b|^(?:uma\W+)?fontes?$")
RE_FONTE_JA_COLOCADA = re.compile(r"\bfontes?\W+(?:que|q)\W+(?:eu\W+)?(?:coloquei|adicionei|mandei|enviei|importei|salvei|guardei|te passei)\b")
RE_ARQUIVAR = re.compile(r"\barquiv(?:a|e|ar|ei|em|ando|ado|ada)\b|\b(?:guarda\w*|salva\w*|move\w*|mover|coloca\w*|bota\w*|copia\w*|manda\w*|poe)\b(?:\W+\w+){0,8}?"
                         r"\W+(?:na|em|para\W+a|pra|para\W+o|no)\W+(?:minha\W+)?pasta\b|^(?:guarda\w*|salva\w*)(?:\W+(?:isso|esse arquivo|ele|ela))?$")
RE_IMPRIMIR = re.compile(r"\bimprim\w+|\bimprima\b|\bmanda\w*\W+(?:pra|para\W+a)\W+impressora\b")
RE_ESCANEAR = re.compile(r"\b(?:escane\w+|scane\w+|digitaliz\w+)\b")
RE_LER = re.compile(r"\b(?:le|leia|ler|resum\w+|explic\w+|traduz\w*|transcrev\w+|analis\w+|revis\w+|confer\w+|extrai\w*|"
                    r"o\W+que\W+(?:diz|tem|ha|esta\W+escrito|fala)|me\W+(?:ajuda\w*|conta|diz)|responde\w*|corrig\w+|"
                    r"faz\w*\W+(?:um\W+)?resumo)\b")
RE_ENVIAR = re.compile(r"\bme\W+(?:manda\w*|envia\w*|mande|envie|passa\w*|devolve\w*)\b|"
                       r"\b(?:manda\w*|envia\w*)\b(?:\W+\w+){0,4}?\W+(?:pro|para\W+o|pra|no)\W+(?:meu\W+)?(?:zap|whatsapp|celular)\b")
RE_LISTAR = re.compile(r"\b(?:quais|lista\w*|mostra\w*|o\W+que)\b(?:\W+\w+){0,4}?\W+(?:arquivos?|fontes?)\W+(?:(?:que\W+)?(?:eu\W+)?"
                       r"(?:te\W+)?(?:recebi|mandei|enviei|passei)|recebid\w+|recentes?)\b|\bo\W+que\W+(?:eu\W+)?(?:te\W+)?(?:mandei|enviei|passei)\b")
RE_MAIS_UMA = re.compile(r"\bmais\W+uma\b|\b(?:outra|proxima|seguinte)\W+(?:pagina|folha)\b")
RE_PRONTO = re.compile(r"^(?:pronto|terminei|acabou|fim|e\W+so\W+isso|so\W+isso|nao\W+tem\W+mais|ja\W+foi|era\W+so\W+isso)$")
RE_CANCELAR = re.compile(r"^(?:nao|deixa|deixa\W+pra\W+la|nada|esquece|cancela|nenhum\w*|nao\W+quero|por\W+enquanto\W+nada)$")
# Só os demonstrativos: "o PDF da OAB" não é "o que acabei de mandar"; "o PDF" sozinho, sem mais nada, é (veja `fala_do_recebido`).
RE_REFERE = re.compile(r"\b(?:isso|isto|esse|essa|este|esta|desse|dessa|deste|desta|nesse|nessa|neste|nesta|nele|nela|dele|dela|ele|ela|eles|elas|o\W+que\W+(?:eu\W+)?(?:mandei|enviei|acabei\W+de\W+\w+)|"
                       r"recebid[oa]s?|todos|todas|tudo|ultim[oa]|aqui)\b")
RE_TIPO = re.compile(r"\b(?:pdf|word|docx?|planilha|excel|xlsx?|powerpoint|pptx?|apresentacao|foto|fotos|imagem|imagens|print|arquivos?|"
                     r"documentos?|texto)\b")
# Pergunta sobre o conteúdo do arquivo: "qual o valor do aluguel e a multa?", "quando vence?", "tem cláusula de reajuste?"
RE_PERGUNTA = re.compile(r"^(?:qual|quais|quanto|quantos|quantas|quando|quem|onde|como|o\W+que|por\W+que|porque|tem|existe|ha|e\W+o|e\W+a|"
                         r"me\W+(?:diz|diga|fala|fale|mostra)|diz|diga)\b")
# Quem deve ler: "pelo Gemini" / "pelo Claude" (llm_nuvem.extrair_ia_pedida), "na nuvem" e "só no PC" / "localmente" / "no Ollama".
RE_SO_NO_PC = re.compile(r"[\s,]*\b(?:s[oó]\s+)?(?:localmente|offline|sem\s+(?:a\s+)?nuvem|(?:no|pelo|com\s+o)\s+ollama)\b"
                         r"|[\s,]*\bs[oó]\s+(?:no\s+(?:pc|computador)|local)\b", re.IGNORECASE)
RE_NA_NUVEM = re.compile(r"[\s,]*\b(?:na|pela|usando\s+a)\s+nuvem\b", re.IGNORECASE)
# Sobra só isto depois de tirar "pelo Claude": é "refaz com essa IA" ("e pelo Claude?", "agora pelo Gemini", "de novo no PC").
PALAVRAS_DE_REFAZER = {"e", "agora", "ai", "entao", "refaz", "refaca", "refazer", "faz", "faca", "de", "novo", "outra", "vez", "mais", "uma",
                       "tenta", "tente", "responde", "responda", "a", "resposta", "isso", "mesmo", "so", "apenas", "pede", "pedir",
                       "pergunta", "perguntar", "pra", "para", "ele", "ela", "la", "tambem", "quero", "queria", "prefiro", "usa", "use"}
RE_TODOS = re.compile(r"\b(?:todos|todas|tudo|os\W+dois|as\W+duas|os\W+tres|as\W+tres)\b")
RE_ULTIMO = re.compile(r"\bultim[oa]\b")
RE_FISICO = re.compile(r"\b(?:scanner|escaner|vidro|folha|papel|pagina|impressora|multifuncional)\b")
RE_PRETO_E_BRANCO = re.compile(r"\bpreto\W+e\W+branco\b|\bp\W*b\b")
RE_CINZA = re.compile(r"\bcinza\b")
RE_ALTA = re.compile(r"\b(?:alta\W+qualidade|600|300)\b")


RE_PALAVRA_DE_ACAO = re.compile(rf"^(?:{VERBOS_DE_ADICIONAR}|arquiv\w+|imprim\w+|imprima|escane\w+|scane\w+|digitaliz\w+|le|leia|ler|resum\w+|"
                                r"explic\w+|traduz\w*|transcrev\w+|analis\w+|revis\w+|confer\w+|extrai\w*|responde\w*|corrig\w+|manda\w*|"
                                r"envia\w*|mande|envie|passa\w*|devolve\w*|move\w*|mover|copia\w*|diz|tem|ha|fala|escrito|conta|ajuda\w*)$")
LIGACAO = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "pra", "para", "por", "favor", "ai", "agora",
           "como", "uma", "um", "nova", "novo", "fonte", "fontes", "biblioteca", "que", "eu", "me", "meu", "minha", "copia", "copias",
           "vez", "vezes", "so", "pagina", "paginas", "pasta", "tambem", "depois", "tudo", "aqui", "zap", "whatsapp", "celular", "mesmo",
           "mais", "volta", "resumo", "fazer", "faz", "faca", "pode", "poderia", "voce", "pro", "te", "se"}


@dataclass
class SessaoDeEscaneamento:
    paginas: list = field(default_factory=list)
    pdf: str = ""
    identificador: str = ""
    ate: float = 0.0


class AgenteFontes:
    """`responder(texto, canal)` devolve a resposta ou None (o roteamento segue); `receber(...)` registra um arquivo que chegou."""

    def __init__(self, registro: Registro, pasta_recebidos: str, pasta_arquivados: str, importar_fonte=None, impressora=None,
                 pastas=None, scanner=None, extrair_texto=None, llm=None, enviar_zap=None, avisar=None, relogio=time.time,
                 em_segundo_plano=None, limite_mb: float = 50.0, categoria: str = "fontes_recebidas", nuvem=None,
                 ia_padrao: str = "local", pessoal=None):
        self.registro, self.pasta_recebidos, self.pasta_arquivados = registro, pasta_recebidos, pasta_arquivados
        self.importar_fonte, self.impressora, self.pastas, self.scanner = importar_fonte, impressora, pastas, scanner
        self.extrair_texto, self.llm, self.enviar_zap = extrair_texto, llm, enviar_zap
        self.avisar = avisar or (lambda texto, canal: None)  # resposta que sai depois (o escaneamento leva ~20 s)
        self.relogio, self.limite_mb, self.categoria = relogio, limite_mb, categoria
        self.em_segundo_plano = em_segundo_plano or (lambda funcao: threading.Thread(target=funcao, daemon=True, name="fontes").start())
        # `nuvem(prompt, contexto=, preferir=, exclusivo=, registro=)` devolve o texto da IA da nuvem ou None (llm_nuvem.responder_na_nuvem)
        self.nuvem, self.ia_padrao, self.pessoal = nuvem, ia_padrao, pessoal
        self._pergunta_ate = 0.0
        self._sessao: SessaoDeEscaneamento | None = None
        self._ultima: dict | None = None  # a última leitura: "e pelo Claude?" refaz o mesmo pedido com outra IA
        self._ia_seguinte: tuple[str, float] | None = None  # a IA da última leitura vale também para as perguntas seguintes

    # ============================================================== chegada de arquivos
    def receber(self, caminho: str, nome: str = "", legenda: str = "", origem: str = "whatsapp", canal: str = "whatsapp") -> str:
        """Um arquivo chegou (WhatsApp, scanner ou pedido na voz): guarda na pasta de recebidos, registra e responde."""
        nome = nome_seguro(nome or os.path.basename(caminho))
        problema = validar_arquivo(caminho, self.limite_mb)
        if problema:
            return f"Recebi “{nome}”, mas não guardei: {problema}"
        caminho = self._para_a_pasta_de_recebidos(caminho, nome)
        anteriores = [r for r in self.registro.recentes(JANELA_DA_RAJADA)]
        recebido = self.registro.registrar(caminho, nome, origem, legenda)
        agora = self.relogio()
        ack = f"Recebi “{nome}” ({descrever(caminho)})."
        if (legenda or "").strip():
            resposta = self.responder(legenda, canal, _recebido=recebido)
            if resposta:
                return f"{ack} {resposta}"
        self._pergunta_ate = agora + VALIDADE_DA_PERGUNTA
        if anteriores:  # um lote: não repete a pergunta a cada arquivo
            return f"{ack} Já são {len(anteriores) + 1} arquivos."
        return f"{ack} O que faço com ele? Posso virar fonte, arquivar, imprimir, escanear (ler) ou te ajudar com o que você pedir."

    def _para_a_pasta_de_recebidos(self, caminho: str, nome: str) -> str:
        if _dentro_de(caminho, self.pasta_recebidos):
            return caminho
        destino = caminho_livre(os.path.join(self.pasta_recebidos, datetime.fromtimestamp(self.relogio()).strftime("%Y-%m-%d")), nome)
        shutil.copy2(caminho, destino)
        return destino

    def reconhece(self, texto: str) -> bool:
        """Se a frase pede algo que este agente faz com arquivos ("vira fonte", "arquiva", "imprime", "escaneia"...). Não faz nada."""
        return bool(self._acoes(normalizar(texto)))

    def esperando_resposta(self) -> bool:
        """Ela acabou de perguntar "o que faço com ele?" ou "tem mais uma página?" e a resposta ainda vale."""
        agora = self.relogio()
        return agora <= self._pergunta_ate or (self._sessao is not None and agora <= self._sessao.ate)

    # ============================================================== pedidos
    def responder(self, texto: str, canal: str = "voz", _recebido: Recebido | None = None) -> str | None:
        texto, ia = separar_ia(texto)
        t = normalizar(texto)
        if ia and all(palavra in PALAVRAS_DE_REFAZER for palavra in t.split()):
            return self._refazer(ia, canal)  # "e pelo Claude?": o mesmo pedido, com outra IA
        if not t:
            return None
        agora = self.relogio()
        if self._sessao is not None and agora > self._sessao.ate:
            self._encerrar_sessao()
        if RE_CANCELAR.match(t) and self.esperando_resposta():
            self._pergunta_ate = 0.0
            self._encerrar_sessao()
            return "Beleza, deixei guardado em documentos recebidos, é só pedir depois."
        if self._sessao is not None and agora <= self._sessao.ate:
            if RE_PRONTO.match(t):
                paginas = len(self._sessao.paginas)
                self._encerrar_sessao()
                return f"Fechado: o PDF ficou com {paginas} página{'s' if paginas != 1 else ''}."
            if RE_MAIS_UMA.search(t) and not RE_ESCANEAR.search(t) and len(t.split()) <= 6:
                return self._escanear_em_segundo_plano(t, canal, [], continuar=True)
        acoes = self._acoes(t)
        if not acoes:
            return self._perguntar_sobre_o_arquivo(t, texto, canal, ia)
        if "listar" in acoes:
            return self._listar()
        if _recebido is not None:
            alvos, aviso = [_recebido], ""
            fisico = False
            acoes = list(dict.fromkeys("ler" if a == "escanear" else a for a in acoes))  # legenda "escaneia": ler o arquivo
        else:
            alvos, aviso, fisico = self._alvos(t, acoes)
            if alvos is None and not fisico:
                return aviso or None
        if fisico:
            return self._escanear_em_segundo_plano(t, canal, [a for a in acoes if a != "escanear"], ia=ia)
        return self._aplicar(acoes, alvos, texto, t, canal, ia)

    def _refazer(self, ia: str, canal: str) -> str | None:
        """"E pelo Claude?" logo depois de uma leitura: repete o pedido com essa IA. Sem leitura recente, o pedido segue o caminho normal."""
        ultima = self._ultima
        if not ultima or self.relogio() > ultima["ate"]:
            return None
        alvos = [r for r in self.registro.recentes(VALIDADE_DOS_RECENTES) if r.id in ultima["ids"] and os.path.isfile(r.caminho)]
        if not alvos:
            return None
        return self._ler(alvos, ultima["pedido"], canal, ia=ia) or None

    def _perguntar_sobre_o_arquivo(self, t: str, texto: str, canal: str, ia: str | None = None) -> str | None:
        """"Qual o valor do aluguel?" logo depois de ler (ou citando "esse contrato"): responde com o arquivo. Se a pergunta não for
        sobre ele, devolve None e o pedido segue o caminho normal."""
        if not (RE_PERGUNTA.match(t) or texto.strip().endswith("?")):
            return None
        agora = self.relogio()
        recentes = [r for r in self.registro.recentes(VALIDADE_DA_LEITURA) if os.path.isfile(r.caminho)]
        if not recentes:
            return None
        lidos = [r for r in recentes if agora - r.lido_em <= VALIDADE_DO_SEGUIMENTO]
        cita_o_arquivo = bool(RE_REFERE.search(t) or RE_TIPO.search(t))
        if cita_o_arquivo and not lidos:
            alvos, tentativa = self._rajada(t, recentes), False
        elif lidos:
            alvos, tentativa = [max(lidos, key=lambda r: r.lido_em)], not cita_o_arquivo  # sem citar: o modelo confere se é sobre ele
        else:
            return None
        return self._ler(alvos, texto, canal, confirmar_assunto=tentativa, ia=ia) or None

    def _acoes(self, t: str) -> list[str]:
        acoes = []
        if RE_LISTAR.search(t):
            return ["listar"]
        if RE_ESCANEAR.search(t):
            acoes.append("escanear")
        if RE_LER.search(t):
            acoes.append("ler")
        if (RE_FONTE.search(t) and not RE_FONTE_JA_COLOCADA.search(t)) or t in {"como fonte", "fonte", "fontes"}:
            acoes.append("fonte")
        if RE_IMPRIMIR.search(t):
            acoes.append("imprimir")
        if RE_ARQUIVAR.search(t):
            acoes.append("arquivar")
        if RE_ENVIAR.search(t):
            acoes.append("enviar")
        return acoes

    def _alvos(self, t: str, acoes: list[str]):
        """(arquivos, aviso, escanear_a_folha): de quem é o pedido. `alvos` None com aviso vazio = não é para mim."""
        recentes = self.registro.recentes(VALIDADE_DOS_RECENTES)
        resto = self._resto(t)
        # "isso", "esse", "todos"... ou nada além do tipo ("o PDF", "a foto"): fala do que acabou de chegar. "o PDF da OAB", não.
        fala_do_recebido = bool(RE_REFERE.search(t)) or not RE_TIPO.sub(" ", resto).strip()
        refere = sem_objeto = fala_do_recebido
        so_ler = set(acoes) <= {"ler"}
        recentes_para_ler = [r for r in recentes if self.relogio() - r.recebido_em <= VALIDADE_DA_LEITURA]

        if "escanear" in acoes:
            if RE_FISICO.search(t) or not recentes_para_ler:
                return None, "", True
            acoes[:] = [a if a != "escanear" else "ler" for a in acoes]
            acoes[:] = list(dict.fromkeys(acoes))
        if so_ler and not (recentes_para_ler and (refere or sem_objeto)):
            return None, "", False  # "resume esse PDF" sem arquivo novo: é da leitura de tela
        if set(acoes) == {"enviar"} and not (recentes and (RE_REFERE.search(t) or "volta" in t)):
            return None, "", False  # "me manda o PDF no zap" é do pedido de arquivo já existente
        if set(acoes) == {"imprimir"} and not (recentes and (refere or sem_objeto)):
            return None, "", False  # a impressora entende sozinha ("imprime o contrato.pdf", "imprime minha agenda")

        if recentes and (refere or sem_objeto):
            lote = self._rajada(t, recentes)
            if not RE_TODOS.search(t):  # "arquiva" de novo no lote: só o que ainda não foi arquivado
                novos = [r for r in lote if not self._ja_feito(r, acoes)]
                lote = novos or lote
            return lote, "", False
        if resto and self.impressora is not None and hasattr(self.impressora, "achar_arquivo"):
            caminho = self.impressora.achar_arquivo(resto, resto)
            if caminho:
                return [self.registro.registrar(caminho, os.path.basename(caminho), "pc")], "", False
            return None, f"Não achei nenhum arquivo para isso. Diga uma parte do nome ou mande o arquivo pelo WhatsApp.", False
        if sem_objeto and set(acoes) & {"fonte", "arquivar", "ler"}:
            return None, ("Não tenho nenhum arquivo recente. Mande o arquivo pelo WhatsApp ou diga qual (por exemplo, "
                          "“adiciona o PDF da OAB dos downloads como fonte”)."), False
        return None, "", False

    @staticmethod
    def _ja_feito(recebido: Recebido, acoes: list[str]) -> bool:
        marcas = {"fonte": "fonte", "arquivar": "arquivado", "imprimir": "impressão", "ler": "lido"}
        pedidas = [marcas[a] for a in acoes if a in marcas]
        return bool(pedidas) and all(any(feito.startswith(marca) for feito in recebido.acoes) for marca in pedidas)

    def _rajada(self, t: str, recentes: list[Recebido]) -> list[Recebido]:
        """O lote mais recente (arquivos com até 90 s de diferença), ou só o último, ou todos os recentes."""
        if RE_ULTIMO.search(t):
            return recentes[:1]
        if RE_TODOS.search(t):
            return recentes[:10]
        lote = [recentes[0]]
        for r in recentes[1:]:
            if lote[-1].recebido_em - r.recebido_em <= JANELA_DA_RAJADA and len(lote) < 10:
                lote.append(r)
            else:
                break
        return lote

    @staticmethod
    def _resto(t: str) -> str:
        """O que sobra do pedido depois de tirar as palavras de ação, a pasta de destino e as de ligação: nome e tipo do arquivo
        ("adiciona o pdf da oab dos downloads como fonte" -> "pdf oab downloads")."""
        antes, _, _ = t.partition(" pasta ")
        for padrao in (RE_REFERE, RE_FISICO, RE_PRETO_E_BRANCO, RE_CINZA, RE_ALTA):
            antes = padrao.sub(" ", antes)
        return " ".join(p for p in antes.split() if p not in LIGACAO and not RE_PALAVRA_DE_ACAO.match(p) and not p.isdigit())

    # ============================================================== execução
    def _aplicar(self, acoes: list[str], alvos: list[Recebido], original: str, t: str, canal: str, ia: str | None = None) -> str:
        falas, pendentes = [], []
        if "ler" in acoes:
            falas.append(self._ler(alvos, original, canal, ia=ia))
        if "fonte" in acoes:
            falas.extend(self._virar_fonte(a) for a in alvos)
        if "enviar" in acoes:
            falas.append(self._enviar(alvos))
        if "imprimir" in acoes:
            pendentes.append(self._imprimir(alvos, original))
        if "arquivar" in acoes:
            pendentes.append(self._arquivar(alvos, t))
        self._pergunta_ate = 0.0
        return " ".join(f for f in falas + pendentes if f)

    def _ler(self, alvos: list[Recebido], pedido: str, canal: str, confirmar_assunto: bool = False, ia: str | None = None) -> str:
        escolhida = self._qual_ia(ia)
        partes, lidos = [], []
        for alvo in alvos[:3]:
            texto = self._texto(alvo.caminho)
            if not texto.strip():
                partes.append(f"Não consegui ler texto em “{alvo.nome}”. Parece uma imagem sem letras legíveis ou um arquivo vazio.")
                continue
            tamanho = "em até 4 frases curtas, para serem faladas" if canal == "voz" else "de forma direta, com no máximo 12 linhas"
            comando = re.sub(r"(?i)\b(?:escaneia|escaneie|scaneia)\b", "leia", pedido).strip()
            resposta, quem, cortado, nota = self._pensar(alvo, texto, comando, tamanho, escolhida, confirmar_assunto, canal)
            if resposta is None:
                partes.append(nota)
                continue
            if confirmar_assunto and resposta.upper().startswith("FORA_DO_ARQUIVO"):
                return ""  # não era sobre o arquivo: quem chamou deixa o pedido seguir
            self.registro.atualizar(alvo.id, "lido")
            lidos.append(alvo.id)
            extras = ""
            if cortado:
                extras += " (Li só o começo e o fim: o arquivo é grande. Vire fonte para eu consultar tudo.)"
            if quem:
                extras += f" (Resposta do {quem}.)"
            partes.append(resposta + extras + (f" {nota}" if nota else ""))
        if lidos:
            agora = self.relogio()
            self._ultima = {"ids": lidos, "pedido": pedido, "ate": agora + VALIDADE_DA_LEITURA}
            self._ia_seguinte = (escolhida, agora + VALIDADE_DO_SEGUIMENTO)
        if len(alvos) > 3:
            partes.append(f"Li só os 3 primeiros dos {len(alvos)} arquivos.")
        return "\n\n".join(partes)

    def _qual_ia(self, ia: str | None) -> str:
        """A IA dita agora; senão a da leitura de pouco antes (as perguntas seguintes ficam com ela); senão o padrão (FONTES_IA)."""
        if ia:
            return ia
        if self._ia_seguinte and self.relogio() <= self._ia_seguinte[1]:
            return self._ia_seguinte[0]
        return self.ia_padrao

    def _pensar(self, alvo: Recebido, texto: str, comando: str, tamanho: str, ia: str, confirmar_assunto: bool, canal: str = "voz"):
        """Quem lê e responde: (resposta, quem respondeu da nuvem, se o texto foi cortado, aviso). `resposta` None = ninguém conseguiu.

        Na nuvem vai só este arquivo e o pedido, e só a IA dita (o Gemini não repassa para o Claude, nem o contrário); arquivos
        pessoais e quem não respondeu ficam com o modelo do PC.
        """
        nota = ""
        if ia != "local" and self.nuvem is not None:
            if self._pessoal(alvo.caminho):
                nota = "Esse arquivo é pessoal demais para sair do PC, então li só com o modelo local."
            elif confirmar_assunto and not self._e_sobre_o_arquivo(alvo, texto, comando):
                return "FORA_DO_ARQUIVO", "", False, ""  # conferido no PC: o arquivo nem chega à nuvem
            else:
                para_nuvem, cortado = cortar_texto(texto, LIMITE_DO_TEXTO_NUVEM)
                registro: list = []
                try:
                    self.avisar(f"Pedindo {PEDINDO_A.get(ia, 'à nuvem')}, leva uns segundos.", canal)
                except Exception:
                    logger.debug("Não consegui avisar que estava pedindo à nuvem", exc_info=True)
                try:
                    resposta = self.nuvem(self._prompt(alvo, para_nuvem, cortado, comando, tamanho, False), contexto=CONTEXTO_DO_ARQUIVO,
                                          preferir=None if ia == "nuvem" else ia, exclusivo=ia != "nuvem", registro=registro)
                except Exception as exc:
                    logger.warning("A nuvem (%s) falhou ao ler %s: %s", ia, alvo.nome, exc)
                    resposta = None
                if resposta and resposta.strip():
                    return resposta.strip(), (registro[0] if registro else NOMES_DA_IA.get(ia, "Gemini")), cortado, ""
                nota = f"Não consegui falar com {NOMES_DA_IA.get(ia, 'a nuvem')} agora, então li com o modelo local."
        para_o_pc, cortado = cortar_texto(texto, LIMITE_DO_TEXTO)
        try:
            resposta = self.llm.chat_beatriz(self._prompt(alvo, para_o_pc, cortado, comando, tamanho, confirmar_assunto),
                                             contexto=CONTEXTO_DO_ARQUIVO, usar_historico=False)
        except Exception as exc:
            logger.warning("Não consegui ler %s com o modelo: %s", alvo.nome, exc)
            return None, "", cortado, f"Li “{alvo.nome}”, mas o modelo não respondeu agora ({exc})."
        return (resposta or "").strip(), "", cortado, nota

    def _prompt(self, alvo: Recebido, texto: str, cortado: bool, comando: str, tamanho: str, confirmar_assunto: bool) -> str:
        return (f"ARQUIVO: {alvo.nome} ({tipo_do_arquivo(alvo.caminho)})\nPEDIDO DO USUÁRIO: {comando}\n\n"
                f"CONTEÚDO (lido do arquivo; se veio de foto ou escaneamento pode ter erros de OCR){' (o meio foi cortado)' if cortado else ''}:\n"
                f"\"\"\"\n{texto}\n\"\"\"\n\n"
                f"Responda ao pedido usando só esse conteúdo, em português, {tamanho}. Se o pedido for só ler, faça um resumo do que importa. "
                "Se faltar informação, diga o que falta em vez de inventar."
                + (" ATENÇÃO: se o pedido não tiver nada a ver com este arquivo (por exemplo, uma pergunta sobre o tempo, a hora "
                   "ou um assunto geral), responda apenas: FORA_DO_ARQUIVO" if confirmar_assunto else ""))

    def _e_sobre_o_arquivo(self, alvo: Recebido, texto: str, pergunta: str) -> bool:
        """Antes de mandar o arquivo para a nuvem numa pergunta que nem cita o arquivo: o modelo do PC confere o assunto
        com o começo do texto. Na dúvida (ou se o modelo falhar), não manda."""
        prompt = (f"ARQUIVO: {alvo.nome}\nCOMEÇO DO CONTEÚDO:\n\"\"\"\n{texto[:1500]}\n\"\"\"\n\nPERGUNTA DO USUÁRIO: {pergunta}\n\n"
                  "A pergunta é sobre este arquivo? Responda só SIM ou NAO.")
        try:
            resposta = self.llm.chat_beatriz(prompt, contexto=CONTEXTO_DO_ARQUIVO, usar_historico=False)
        except Exception as exc:
            logger.warning("Não consegui conferir o assunto de %s: %s", alvo.nome, exc)
            return False
        return normalizar(resposta or "").startswith("sim")

    def _pessoal(self, caminho: str) -> bool:
        """Diário, memória, histórico e arquivos de dados nunca saem do PC (mesma regra do material de estudo)."""
        try:
            if self.pessoal is not None:
                return bool(self.pessoal(caminho))
            from agentes.especialistas import fonte_pessoal

            return fonte_pessoal(caminho)
        except Exception:
            return True

    def _texto(self, caminho: str) -> str:
        try:
            return self.extrair_texto(caminho) or ""
        except Exception as exc:
            logger.warning("Não consegui extrair texto de %s: %s", caminho, exc)
            return ""

    def _virar_fonte(self, alvo: Recebido) -> str:
        try:
            resultado = self.importar_fonte(alvo.caminho, self.categoria)
        except Exception as exc:
            return f"Não consegui adicionar “{alvo.nome}” como fonte: {exc}."
        if str(resultado).startswith("Erro") or "não encontrado" in str(resultado).lower():
            return f"Não consegui adicionar “{alvo.nome}” como fonte: {resultado}"
        self.registro.atualizar(alvo.id, "fonte")
        palavras = len(self._texto(alvo.caminho).split())
        if palavras == 0:
            return (f"“{alvo.nome}” foi para a biblioteca, mas não achei texto nele para consultar (parece uma imagem sem letras "
                    "legíveis).")
        return (f"“{alvo.nome}” virou fonte ({palavras:,} palavras).".replace(",", ".") +
                " Pode me perguntar sobre ele ou pedir “resume as fontes que eu coloquei”.")

    def _arquivar(self, alvos: list[Recebido], t: str) -> str:
        _, _, depois = t.partition(" pasta ")
        palavras = [p for p in depois.split() if p not in {"de", "da", "do", "das", "dos", "a", "o", "as", "os", "minha", "meu", "chamada",
                                                            "chamado", "por", "favor", "pra", "para", "ai", "e", "fonte", "fontes"}]
        if palavras and self.pastas is not None:
            pasta, pergunta = self.pastas.escolher(palavras, lambda escolhida: self._copiar_para(alvos, escolhida.caminho, escolhida.nome))
            if pasta is None:
                return pergunta
            return self._copiar_para(alvos, pasta.caminho, pasta.nome)
        destino = os.path.join(self.pasta_arquivados, datetime.fromtimestamp(self.relogio()).strftime("%Y-%m"))
        return self._copiar_para(alvos, destino, "arquivados do mês")

    def _copiar_para(self, alvos: list[Recebido], pasta: str, nome_da_pasta: str) -> str:
        guardados = []
        for alvo in alvos:
            try:
                destino = caminho_livre(pasta, os.path.basename(alvo.caminho))
                shutil.copy2(alvo.caminho, destino)
                self.registro.atualizar(alvo.id, f"arquivado em {pasta}")
                guardados.append(alvo.nome)
            except OSError as exc:
                return f"Não consegui arquivar “{alvo.nome}” em {nome_da_pasta}: {exc}."
        if len(guardados) == 1:
            return f"Arquivei “{guardados[0]}” em {nome_da_pasta}."
        return f"Arquivei {len(guardados)} arquivos em {nome_da_pasta}."

    def _imprimir(self, alvos: list[Recebido], original: str) -> str:
        if self.impressora is None or not hasattr(self.impressora, "imprimir_arquivos"):
            return "A impressora não está disponível agora."
        resposta = self.impressora.imprimir_arquivos([a.caminho for a in alvos], original)
        for alvo in alvos:
            self.registro.atualizar(alvo.id, "impressão pedida")
        return resposta or ""

    def _enviar(self, alvos: list[Recebido]) -> str:
        if self.enviar_zap is None:
            return "Daqui eu não consigo mandar para o WhatsApp; peça pela voz ou pelo próprio WhatsApp."
        try:
            self.enviar_zap([a.caminho for a in alvos], "")
        except Exception as exc:
            return f"Não consegui mandar pelo WhatsApp: {exc}."
        return f"Mandei {'o arquivo' if len(alvos) == 1 else f'os {len(alvos)} arquivos'} para o seu WhatsApp."

    def _listar(self) -> str:
        recentes = self.registro.recentes(24 * 3600)
        if not recentes:
            return "Não recebi nenhum arquivo nas últimas 24 horas."
        linhas = [f"{indice}. {r.nome} ({descrever(r.caminho)}), {datetime.fromtimestamp(r.recebido_em):%H:%M}"
                  + (f" — {', '.join(r.acoes)}" if r.acoes else "") for indice, r in enumerate(recentes[:8], start=1)]
        return "Arquivos recentes: " + "; ".join(linhas) + "."

    def _encerrar_sessao(self):
        """Fim do escaneamento de várias folhas: apaga os PNGs temporários (o PDF fica nos recebidos)."""
        sessao, self._sessao = self._sessao, None
        for png in (sessao.paginas if sessao else []):
            try:
                os.remove(png)
            except OSError:
                pass

    # ============================================================== escaneamento da folha
    def _escanear_em_segundo_plano(self, t: str, canal: str, depois: list[str], continuar: bool = False, ia: str | None = None) -> str:
        if self.scanner is None:
            return "O scanner não está disponível neste computador."
        dpi = 300 if RE_ALTA.search(t) else 200
        cor = "pb" if RE_PRETO_E_BRANCO.search(t) else "cinza" if RE_CINZA.search(t) else "cor"
        pagina = (len(self._sessao.paginas) + 1) if continuar and self._sessao else 1
        self.em_segundo_plano(lambda: self._escanear(t, canal, depois, dpi, cor, continuar, ia))
        return f"Escaneando a página {pagina}, uns 20 segundos. Não mexa na tampa."

    def _escanear(self, t: str, canal: str, depois: list[str], dpi: int, cor: str, continuar: bool, ia: str | None = None):
        from scanner import ErroDoScanner, paginas_para_pdf

        try:
            agora = datetime.fromtimestamp(self.relogio())
            pasta = os.path.join(self.pasta_recebidos, agora.strftime("%Y-%m-%d"))
            if continuar and self._sessao is not None:
                sessao = self._sessao
            else:
                self._encerrar_sessao()
                sessao = SessaoDeEscaneamento(pdf=caminho_livre(pasta, f"escaneado_{agora:%H%M%S}.pdf"))
            png = os.path.join(tempfile.gettempdir(), f"beatriz_scan_{uuid.uuid4().hex[:8]}.png")
            self.scanner.escanear(png, dpi=dpi, cor=cor)
            sessao.paginas.append(png)
            paginas_para_pdf(sessao.paginas, sessao.pdf)
        except ErroDoScanner as exc:
            self.avisar(str(exc), canal)
            return
        except Exception as exc:
            logger.exception("Escaneamento falhou")
            self.avisar(f"Não consegui escanear: {exc}", canal)
            return
        if continuar and self._sessao is not None:
            sessao.ate = self.relogio() + 5 * 60
            recebido = next((r for r in self.registro.todos() if r.id == sessao.identificador), None)
            if recebido is None:
                recebido = self.registro.registrar(sessao.pdf, os.path.basename(sessao.pdf), "scanner")
        else:
            recebido = self.registro.registrar(sessao.pdf, os.path.basename(sessao.pdf), "scanner")
            sessao.identificador = recebido.id
        sessao.ate = self.relogio() + 5 * 60
        self._sessao = sessao
        n = len(sessao.paginas)
        resultado = f"Escaneei {'a página' if n == 1 else 'mais uma página'}: o PDF tem {n} página{'s' if n != 1 else ''}."
        if depois:
            acoes = [a for a in depois if a in {"ler", "fonte", "arquivar", "imprimir", "enviar"}]
            extra = self._aplicar(acoes, [recebido], t, t, canal, ia)
            self.avisar(f"{resultado} {extra}".strip(), canal)
            return
        self._pergunta_ate = self.relogio() + VALIDADE_DA_PERGUNTA
        self.avisar(f"{resultado} Quer virar fonte, arquivar, imprimir (uma cópia), ler ou que eu te mande no zap? "
                    "Se tiver outra folha, diga “mais uma”; ao terminar, “pronto”.", canal)


# ------------------------------------------------------------------ montagem com o resto da Beatriz
def criar_agente_fontes(avisar=None, enviar_zap=None, impressora=None, pastas=None, llm=None, **sobrescritas) -> AgenteFontes:
    """O agente com as peças de verdade: biblioteca, impressora, pastas, scanner e Ollama. Os testes passam as suas."""
    from config import CATEGORIA_DAS_FONTES, FONTES_IA, LIMITE_DO_ARQUIVO_MB, NOTELOOKLM_PATH, PASTA_ARQUIVADOS, PASTA_RECEBIDOS

    def importar(caminho, categoria):
        from memory import importar_fonte_local

        return importar_fonte_local(caminho, categoria)

    def extrair(caminho):
        from memory import extrair_texto_arquivo_otimizado

        return extrair_texto_arquivo_otimizado(caminho, limite=60000)

    def nuvem(prompt, contexto="", **opcoes):
        from llm_nuvem import responder_na_nuvem

        return responder_na_nuvem(prompt, contexto, **opcoes)

    if llm is None:
        from llm_clients import llm_clients as llm
    argumentos = dict(
        registro=Registro(os.path.join(NOTELOOKLM_PATH, "banco_memoria", "arquivos_recebidos.json")),
        pasta_recebidos=PASTA_RECEBIDOS, pasta_arquivados=PASTA_ARQUIVADOS, importar_fonte=importar, impressora=impressora,
        pastas=pastas, extrair_texto=extrair, llm=llm, enviar_zap=enviar_zap, avisar=avisar, limite_mb=LIMITE_DO_ARQUIVO_MB,
        categoria=CATEGORIA_DAS_FONTES, nuvem=nuvem, ia_padrao=FONTES_IA,
    )
    if "scanner" not in sobrescritas:
        from scanner import ScannerWia

        argumentos["scanner"] = ScannerWia()
    argumentos.update(sobrescritas)
    return AgenteFontes(**argumentos)
