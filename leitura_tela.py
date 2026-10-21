"""Leitura de tela (screen awareness): captura a janela ativa ou a tela inteira, lê o texto e responde sobre ela.

"Beatriz, resume esse PDF", "analisa esse gráfico", "por que deu esse erro?", "o que estou vendo?" e
"o que estou fazendo?". Tudo no computador: a imagem fica só na memória (nunca em arquivo), o texto vem do OCR do
Windows (pt-BR) e quem responde é o Ollama local; nada da tela vai para a nuvem nem para o histórico/biblioteca.

Caminho de cada pedido:
  1. `janela_em_foco`: a janela mais à frente que não é da própria Beatriz (o painel dela roda em outro processo e
     pode estar em primeiro plano quando você digita);
  2. `capturar`: a janela, o monitor dela ou todos os monitores (em pixels reais, mesmo com escala de 125%);
  3. `ler_texto`: OCR do Windows, com o recuo das linhas refeito para código;
  4. texto + pergunta vão para o modelo de texto (rápido, bom em português e em código). Gráficos, imagens e telas sem
     texto vão para o modelo de visão (`OLLAMA_VISION_MODEL`), com o texto lido como ajuda;
  5. a sessão fica guardada por alguns minutos para as perguntas seguintes ("explica melhor", "como corrijo?").

Janelas de gerenciadores de senha (e o que estiver em `TELA_IGNORAR`) nunca são lidas.
"""
import asyncio
import ctypes
import io
import logging
import os
import re
import statistics
import threading
import time
import unicodedata
from contextlib import contextmanager
from ctypes import wintypes as wt
from dataclasses import dataclass, field, replace

from config import OLLAMA_VISION_MODEL, TELA_CONTEXTO_SEGUNDOS, TELA_IGNORAR, TELA_MAX_CARACTERES

logger = logging.getLogger(__name__)

LADO_MAXIMO_VISAO = 1344  # o llava reduz a imagem a isso de qualquer jeito
# Modelos de visão que leem texto, tabelas e gráficos de verdade e respondem sozinhos (trocar para o modelo de texto
# custa ~20 s de recarga na placa de vídeo e ele não enxerga as tendências de um gráfico). Os outros (llava) inventam
# detalhes com muita confiança: a descrição deles vai marcada como aproximada e o texto lido pelo OCR manda.
VISAO_FORTE = ("qwen2.5vl", "qwen2-vl", "qwen3-vl", "minicpm-v", "llama3.2-vision", "gemma3", "mistral-small3", "granite3.2-vision")
PROGRAMAS_DE_SENHA = ("keepass", "bitwarden", "1password", "lastpass", "dashlane", "enpass", "nordpass", "roboform")
CLASSES_DO_SISTEMA = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow",
                      "XamlExplorerHostIslandWindow", "TopLevelWindowForOverflowXamlIsland"}

# nome do executável (sem .exe) -> (nome falado, o que a pessoa costuma estar fazendo)
PROGRAMAS = {
    "code": ("Visual Studio Code", "programando"), "cursor": ("Cursor", "programando"),
    "devenv": ("Visual Studio", "programando"), "pycharm64": ("PyCharm", "programando"),
    "idea64": ("IntelliJ", "programando"), "notepad++": ("Notepad++", "programando"),
    "sublime_text": ("Sublime Text", "programando"), "windowsterminal": ("Terminal", "no terminal"),
    "cmd": ("Prompt de Comando", "no terminal"), "powershell": ("PowerShell", "no terminal"),
    "pwsh": ("PowerShell", "no terminal"), "acrord32": ("Adobe Acrobat", "lendo um PDF"),
    "acrobat": ("Adobe Acrobat", "lendo um PDF"), "sumatrapdf": ("SumatraPDF", "lendo um PDF"),
    "foxitreader": ("Foxit Reader", "lendo um PDF"), "winword": ("Word", "editando um documento"),
    "excel": ("Excel", "mexendo numa planilha"), "powerpnt": ("PowerPoint", "mexendo numa apresentação"),
    "chrome": ("Google Chrome", "navegando"), "msedge": ("Microsoft Edge", "navegando"),
    "firefox": ("Firefox", "navegando"), "brave": ("Brave", "navegando"), "opera": ("Opera", "navegando"),
    "teams": ("Teams", "numa reunião"), "zoom": ("Zoom", "numa reunião"),
    "whatsapp": ("WhatsApp", "conversando"), "discord": ("Discord", "conversando"),
    "telegram": ("Telegram", "conversando"), "slack": ("Slack", "conversando"),
    "explorer": ("Explorador de Arquivos", "mexendo em arquivos"), "notepad": ("Bloco de Notas", "editando um texto"),
    "claude": ("Claude", "conversando com a IA"), "spotify": ("Spotify", "ouvindo música"), "vlc": ("VLC", "vendo um vídeo"),
}
EDITORES = {"code", "cursor", "devenv", "pycharm64", "idea64", "notepad++", "sublime_text", "windowsterminal", "cmd",
            "powershell", "pwsh", "notepad"}
LEITORES = {"acrord32", "acrobat", "sumatrapdf", "foxitreader"}


# ------------------------------------------------------------------ janelas e captura (Windows)
@dataclass(frozen=True)
class Janela:
    hwnd: int
    programa: str  # "code" (executável sem .exe, em minúsculas)
    titulo: str
    caixa: tuple  # (esquerda, topo, direita, base), em pixels reais


@dataclass
class Captura:
    imagem: object  # PIL.Image
    janela: Janela | None
    escopo: str  # "janela", "tela" ou "todas"
    bloqueada: str = ""  # por que não pode ser lida (gerenciador de senhas...), vazio se pode


@contextmanager
def _pixels_reais():
    """Nesta thread, o Windows devolve as medidas em pixels reais (sem a escala de 125%, 150%...)."""
    funcao = ctypes.windll.user32.SetThreadDpiAwarenessContext
    funcao.argtypes, funcao.restype = [ctypes.c_void_p], ctypes.c_void_p
    anterior = None
    try:
        anterior = funcao(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        pass
    try:
        yield
    finally:
        if anterior:
            try:
                funcao(ctypes.c_void_p(anterior))
            except Exception:
                pass


def _imagem_do_processo(pid: int) -> str:
    processo = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not processo:
        return ""
    try:
        tamanho = wt.DWORD(1024)
        caminho = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(processo, 0, caminho, ctypes.byref(tamanho)):
            return caminho.value.lower()
        return ""
    finally:
        ctypes.windll.kernel32.CloseHandle(processo)


def _caixa_da_janela(hwnd: int) -> tuple:
    """A área visível da janela (sem a sombra invisível que o Windows 10/11 desenha em volta)."""
    caixa = wt.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), 9, ctypes.byref(caixa), ctypes.sizeof(caixa)) != 0:
        ctypes.windll.user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(caixa))
    return caixa.left, caixa.top, caixa.right, caixa.bottom


def janelas_visiveis() -> list[Janela]:
    """As janelas abertas e visíveis, da mais à frente para a mais ao fundo, sem as da própria Beatriz."""
    import win32gui
    import win32process

    proprio = _imagem_do_processo(os.getpid())
    user32 = ctypes.windll.user32
    achadas: list[Janela] = []

    def ao_achar(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                return True
            titulo = win32gui.GetWindowText(hwnd)
            if not titulo or win32gui.GetClassName(hwnd) in CLASSES_DO_SISTEMA:
                return True
            if user32.GetWindowLongW(wt.HWND(hwnd), -20) & 0x80:  # WS_EX_TOOLWINDOW: dicas, paletas...
                return True
            escondida = ctypes.c_int(0)
            ctypes.windll.dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), 14, ctypes.byref(escondida), 4)  # DWMWA_CLOAKED
            if escondida.value:
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            imagem = _imagem_do_processo(pid)
            if pid == os.getpid() or (proprio and imagem == proprio):
                return True  # o painel, a mascote e o resto da Beatriz
            caixa = _caixa_da_janela(hwnd)
            if caixa[2] - caixa[0] < 80 or caixa[3] - caixa[1] < 60:
                return True
            programa = os.path.splitext(os.path.basename(imagem))[0]
            achadas.append(Janela(hwnd, programa, titulo, caixa))
        except Exception as exc:
            logger.debug("Janela ignorada: %s", exc)
        return True

    win32gui.EnumWindows(ao_achar, None)
    return achadas


def janela_em_foco() -> Janela | None:
    """A janela em que a pessoa está trabalhando: a mais à frente que não é da Beatriz."""
    with _pixels_reais():
        janelas = janelas_visiveis()
    return janelas[0] if janelas else None


def _monitor_de(janela: Janela | None) -> tuple:
    import win32api

    if janela is not None:
        monitor = win32api.MonitorFromWindow(janela.hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    else:
        monitor = win32api.MonitorFromPoint(win32api.GetCursorPos(), 2)
    return tuple(win32api.GetMonitorInfo(monitor)["Monitor"])


def capturar(escopo: str = "janela") -> Captura:
    """Imagem da janela ativa, do monitor dela ("tela") ou de todos os monitores ("todas"). Só na memória."""
    from PIL import ImageGrab

    with _pixels_reais():
        todas = janelas_visiveis()
        janela = todas[0] if todas else None
        if escopo == "janela" and janela is not None:
            caixa = janela.caixa
        elif escopo == "todas":
            caixa = None
        else:
            caixa = _monitor_de(janela)
        bloqueada = _motivo_de_bloqueio(todas if escopo != "janela" else todas[:1], caixa)
        if bloqueada:
            return Captura(None, janela, escopo, bloqueada)
        imagem = ImageGrab.grab(bbox=caixa, all_screens=True) if caixa else ImageGrab.grab(all_screens=True)
    return Captura(imagem.convert("RGB"), janela, escopo)


def _motivo_de_bloqueio(janelas: list[Janela], caixa: tuple | None) -> str:
    """Se alguma janela visível na área é de um gerenciador de senhas (ou da lista TELA_IGNORAR)."""
    for janela in janelas:
        if caixa is not None and not _cruza(janela.caixa, caixa):
            continue
        if bloqueada(janela):
            return janela.titulo or janela.programa
    return ""


def _cruza(a: tuple, b: tuple) -> bool:
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def bloqueada(janela: Janela) -> bool:
    """Janelas que ela nunca lê: gerenciadores de senha e o que o usuário pôs em TELA_IGNORAR."""
    alvo = f"{janela.programa} {janela.titulo}".lower()
    extras = [t.strip().lower() for t in TELA_IGNORAR.split(",") if t.strip()]
    return any(nome in alvo for nome in (*PROGRAMAS_DE_SENHA, *extras))


# ------------------------------------------------------------------ OCR do Windows
class OcrIndisponivel(RuntimeError):
    pass


@dataclass
class Linha:
    texto: str
    x: float = 0.0
    largura_letra: float = 0.0  # largura média de uma letra nesta linha, para refazer o recuo do código
    y: float = 0.0
    altura: float = 0.0
    direita: float = 0.0


_MOTOR_OCR = {}


async def _reconhecer(imagem):
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    except ImportError as exc:
        raise OcrIndisponivel("faltam os pacotes winrt (pip install winrt-Windows.Media.Ocr ...)") from exc
    if "motor" not in _MOTOR_OCR:
        _MOTOR_OCR["motor"] = (OcrEngine.try_create_from_language(Language("pt-BR"))
                               or OcrEngine.try_create_from_user_profile_languages())
    motor = _MOTOR_OCR["motor"]
    if motor is None:
        raise OcrIndisponivel("o Windows não tem um idioma de OCR instalado")
    if max(imagem.size) > OcrEngine.max_image_dimension:
        imagem = imagem.copy()
        imagem.thumbnail((OcrEngine.max_image_dimension,) * 2)
    buffer = io.BytesIO()
    imagem.save(buffer, "PNG")
    fluxo = InMemoryRandomAccessStream()
    escritor = DataWriter(fluxo)
    escritor.write_bytes(buffer.getvalue())
    await escritor.store_async()
    await escritor.flush_async()
    escritor.detach_stream()
    fluxo.seek(0)
    decodificador = await BitmapDecoder.create_async(fluxo)
    resultado = await motor.recognize_async(await decodificador.get_software_bitmap_async())
    linhas = []
    for linha in resultado.lines:
        palavras = list(linha.words)
        if not palavras:
            continue
        letras = [p.bounding_rect.width / len(p.text) for p in palavras if len(p.text) >= 3]
        caixas = [p.bounding_rect for p in palavras]
        topo = min(c.y for c in caixas)
        linhas.append(Linha(linha.text, min(c.x for c in caixas), statistics.median(letras) if letras else 0.0, topo,
                            max(c.y + c.height for c in caixas) - topo, max(c.x + c.width for c in caixas)))
    return linhas


def ler_texto(imagem) -> list[Linha]:
    """OCR do Windows (pt-BR), ~0,5 s numa tela dupla. Levanta OcrIndisponivel se não houver como ler."""
    return asyncio.run(_reconhecer(imagem))


def montar_texto(linhas: list[Linha], recuo: bool = False) -> str:
    """As linhas lidas em texto. Com `recuo` (código, terminal), junta os pedaços que o OCR separou na mesma linha da
    tela e refaz a indentação e os espaços pela posição de cada um."""
    if not recuo:
        return "\n".join(l.texto for l in linhas)
    larguras = [l.largura_letra for l in linhas if l.largura_letra]
    if not larguras or not any(l.altura for l in linhas):
        return "\n".join(l.texto for l in linhas)
    letra = statistics.median(larguras)
    origem = min(l.x for l in linhas)
    filas: list[list[Linha]] = []
    for linha in sorted(linhas, key=lambda l: l.y):
        centro = linha.y + linha.altura / 2
        if filas:
            anterior = filas[-1][0]
            if abs(centro - (anterior.y + anterior.altura / 2)) < 0.6 * max(linha.altura, anterior.altura):
                filas[-1].append(linha)
                continue
        filas.append([linha])
    saida = []
    for fila in filas:
        fila.sort(key=lambda l: l.x)
        texto = " " * min(40, max(0, round((fila[0].x - origem) / letra))) + fila[0].texto
        for anterior, atual in zip(fila, fila[1:]):
            vao = atual.x - anterior.direita
            texto += " " * (max(1, round(vao / letra)) if vao > 0.35 * letra else 0) + atual.texto
        saida.append(texto)
    return "\n".join(saida)


def cortar_texto(texto: str, limite: int = TELA_MAX_CARACTERES) -> tuple[str, bool]:
    """Cabe no limite: guarda o começo e o fim (o fim de um terminal é onde está o erro). (texto, foi_cortado)"""
    if len(texto) <= limite:
        return texto, False
    metade = limite // 2
    return texto[:metade] + "\n[... trecho do meio cortado ...]\n" + texto[-metade:], True


# ------------------------------------------------------------------ o que a pessoa está fazendo
@dataclass(frozen=True)
class Atividade:
    programa: str  # nome falado: "Visual Studio Code"
    titulo: str  # título da janela, sem o nome do programa no fim
    fazendo: str  # "programando", "lendo um PDF", ... ("" se não se sabe)

    def texto(self) -> str:
        return f"{self.programa}" + (f" ({self.titulo})" if self.titulo else "")


def atividade_de(janela: Janela | None) -> Atividade | None:
    if janela is None or bloqueada(janela):
        return None
    nome, fazendo = PROGRAMAS.get(janela.programa, (janela.programa.replace("_", " ").title(), ""))
    titulo = janela.titulo.replace("​", "").strip()
    titulo = re.sub(r"\s[-–—]\s(?:[^-–—]+\s[-–—]\s)?(?:Google Chrome|Microsoft.?Edge|Mozilla Firefox|Brave|Visual Studio Code|"
                    r"Cursor|Adobe Acrobat.*|Word|Excel|PowerPoint)$", "", titulo).strip()
    if janela.programa in {"chrome", "msedge", "firefox", "brave"} and titulo.lower().endswith(".pdf"):
        fazendo = "lendo um PDF"
    return Atividade(nome, titulo, fazendo)


def atividade_atual() -> Atividade | None:
    """O que está em primeiro plano agora (sem tirar foto da tela). None se não der para saber."""
    try:
        return atividade_de(janela_em_foco())
    except Exception as exc:
        logger.debug("Atividade da tela indisponível: %s", exc)
        return None


def contexto_da_atividade() -> str:
    """Frase para o prompt da conversa: em que a pessoa está, para as respostas se ajustarem a isso."""
    atividade = atividade_atual()
    if atividade is None:
        return ""
    fazendo = f", {atividade.fazendo}" if atividade.fazendo else ""
    return (f"Agora o usuário está em: {atividade.texto()}{fazendo}. Use isso só para ajustar a resposta (por exemplo, "
            "se está programando, dê exemplos na linguagem do arquivo). Não comente a janela a menos que ajude.")


# ------------------------------------------------------------------ entender o pedido
def sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", (texto or "").lower())
    limpo = "".join(c for c in decomposto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s+]", " ", limpo)).strip()


@dataclass(frozen=True)
class Pedido:
    pergunta: str
    escopo: str = "janela"  # "janela", "tela" ou "todas"
    tipo: str = "geral"  # "geral", "leitura", "grafico", "codigo" ou "atividade"
    seguimento: bool = False  # pergunta sobre o que já foi lido
    refazer: bool = False  # "olha de novo": captura outra vez


_DEM = r"(?:esse|essa|este|esta|isso|isto|desse|dessa|deste|desta|nesse|nessa|neste|nesta|aquele|aquela)"
_OBJ_LEITURA = r"(?:pdf|documento|artigo|pagina|texto|paragrafo|capitulo|e mail|email|site|noticia|contrato|lei|slide|apresentacao)"
_OBJ_GRAFICO = r"(?:grafico|tabela|diagrama|dashboard|planilha|imagem|foto|figura|mapa|infografico)"
_OBJ_CODIGO = r"(?:codigo|erro|bug|script|funcao|trecho|traceback|exception|log|programa|classe|query|comando)"
_OBJETOS = rf"(?:{_OBJ_LEITURA}|{_OBJ_GRAFICO}|{_OBJ_CODIGO})"
_VERBO = (r"(?:le|leia|ler|olh\w+|ve|veja|ver|enxerg\w+|analis\w+|confer\w+|examin\w+|resum\w+|explic\w+|descrev\w+|"
          r"interpret\w+|traduz\w*|revis\w+|depur\w+|debug\w+|corrig\w+|conserta\w*|arrum\w+)")

RE_ATIVIDADE = re.compile(r"\bo que (?:eu )?(?:estou|to) fazendo\b|\b(?:em|no) que (?:eu )?(?:estou|to) trabalhando\b"
                          r"|\bsabe o que (?:eu )?(?:estou|to) fazendo\b")
RE_VENDO = re.compile(r"\bo que (?:eu )?(?:estou|to) (?:vendo|olhando|lendo)\b")
RE_VERBO_TELA = re.compile(rf"\b{_VERBO}\b.{{0,40}}\b(?:telas?|janelas?|monitor(?:es)?)\b|\b(?:telas?|janelas?)\b.{{0,25}}\b{_VERBO}\b"
                           r"|\bo que (?:tem|ha|esta|aparece|mostra) (?:na|nessa|nesta|aqui na) (?:tela|janela)\b"
                           r"|\b(?:screenshot|print) da tela\b")
# Com "esse/essa/isso" qualquer objeto vale; com "o/meu" só gráfico e código; um PDF ou artigo precisa de "aberto", "aqui"...
_OBJ_SEM_DEM = r"(?:grafico|tabela|diagrama|dashboard|codigo|erro|bug|script|traceback|exception)"
RE_VERBO_OBJETO = re.compile(
    rf"\b{_VERBO}\b.{{0,25}}\b{_DEM}\s+{_OBJETOS}\b"
    rf"|\b{_VERBO}\b.{{0,25}}\b(?:o|a|meu|minha)\s+{_OBJ_SEM_DEM}\b"
    rf"|\b{_VERBO}\b.{{0,25}}\b(?:o|a)\s+{_OBJ_LEITURA}\s+(?:aberto|aberta|aqui|da tela|que (?:eu )?(?:estou|to) \w+)\b"
)
RE_DIZ_OBJETO = re.compile(rf"\bo que (?:diz|fala|mostra|significa|quer dizer) {_DEM}\s+{_OBJETOS}\b")
RE_ERRO = re.compile(rf"\bpor que (?:deu|da|esta dando|apareceu|aparece) {_DEM}\s+(?:erro|bug)\b"
                     rf"|\bque erro e (?:esse|este)\b|\bcomo (?:eu )?(?:resolvo|corrijo|arrumo|conserto) {_DEM}\s+(?:erro|bug)\b")
RE_REFAZER = re.compile(r"\b(?:olha|olhe|ve|veja|le|leia|analisa|analise) (?:de novo|outra vez|agora)\b|\b(?:de novo|outra vez) (?:a|na) (?:tela|janela)\b"
                        r"|\batualiza (?:a )?(?:leitura|tela)\b")
RE_SEGUIMENTO = re.compile(
    r"^(?:e |entao |mas |ai )?(?:explica|detalha|resume|traduz|corrige|conserta|mostra|me mostra|reescreve|escreve|simplifica) "
    r"(?:melhor|de novo|isso|esse|essa|o erro|a correcao|o codigo|como ficaria|mais simples|de outro jeito|em ingles|mais curto)"
    r"|\b(?:como (?:eu )?(?:corrijo|conserto|arrumo|resolvo)|qual (?:e|seria) a (?:correcao|solucao)|me mostra a correcao"
    r"|o que (?:mais|isso significa)|e agora|faz sentido)\b"
    r"|^(?:e )?(?:o|a|esse|essa|aquele|aquela) (?:primeiro|segundo|terceiro|ultimo|proximo|penultimo)\b"
    r"|^(?:e )?(?:esse|essa|aquele|aquela) (?:erro|item|ponto|trecho|parte|numero|valor|linha|grafico|campo)\b"
)
RE_TODAS = re.compile(r"\btodas as telas\b|\btodos os monitores\b|\bas duas telas\b|\bos dois monitores\b")
RE_TELA_INTEIRA = re.compile(r"\btela (?:inteira|toda|completa)\b|\btoda a tela\b|\ba tela inteira\b|\bmonitor (?:todo|inteiro)\b|\btodo o monitor\b")


def interpretar(texto: str, sessao_ativa: bool = False) -> Pedido | None:
    """Reconhece o pedido de olhar a tela (regras, sem LLM). None se o pedido é de outra coisa."""
    c = sem_acento(texto)
    if not c:
        return None
    if RE_ATIVIDADE.search(c):
        return Pedido(texto, tipo="atividade")
    olhar = bool(RE_VENDO.search(c) or RE_VERBO_TELA.search(c) or RE_VERBO_OBJETO.search(c)
                 or RE_DIZ_OBJETO.search(c) or RE_ERRO.search(c))
    if not olhar:
        if sessao_ativa and RE_REFAZER.search(c):
            return Pedido(texto, refazer=True, seguimento=True)
        if sessao_ativa and RE_SEGUIMENTO.search(c):
            return Pedido(texto, seguimento=True)
        return None

    if re.search(_OBJ_GRAFICO, c):
        tipo = "grafico"
    elif re.search(_OBJ_CODIGO, c):
        tipo = "codigo"
    elif re.search(_OBJ_LEITURA, c) or re.search(r"\b(?:le|leia|ler|resum\w+|traduz\w*)\b", c):
        tipo = "leitura"
    else:
        tipo = "geral"
    if RE_TODAS.search(c):
        escopo = "todas"
    elif RE_TELA_INTEIRA.search(c) or (re.search(r"\btela\b", c) and not re.search(r"\bjanela\b|\bativa\b", c)
                                       and not re.search(_OBJETOS, c)):
        escopo = "tela"  # "lê a minha tela" olha o monitor todo; "esse PDF" ou "essa janela" só a janela
    else:
        escopo = "janela"
    return Pedido(texto, escopo=escopo, tipo=tipo, refazer=bool(RE_REFAZER.search(c)))


# ------------------------------------------------------------------ o agente
@dataclass
class RespostaTela:
    fala: str
    painel: str = ""  # versão completa para ler no painel (vazio: igual à fala)


@dataclass
class Sessao:
    pedido: Pedido
    janela: Janela | None
    texto: str
    imagem_png: bytes | None
    fala: str
    quando: float = field(default_factory=time.time)
    visual: str = ""  # descrição visual aproximada (modelo de visão fraco)


PERSONALIDADE_TELA = (
    "Você está olhando a tela do usuário e vai ajudar com o que ele está fazendo. O texto da tela foi lido por OCR: "
    "pode ter erros de leitura (l/1/I, O/0, letras trocadas) e linhas fora de ordem; releia com bom senso e nunca "
    "invente o que não está lá. Se faltar informação para responder, diga o que falta."
)

INSTRUCOES_FALA = {
    "geral": "o que está na tela e o que a pessoa parece estar fazendo, respondendo ao pedido",
    "leitura": "o ponto principal do que está escrito, respondendo ao pedido (resumo, explicação ou tradução)",
    "codigo": "o que está errado e a correção provável, citando a função, a linha ou a mensagem; se não houver erro "
              "visível, diga o que o código faz",
    "grafico": "o tipo de gráfico ou tabela, o que mostra, as tendências e os valores que se destacam",
}


def separar(resposta: str) -> tuple[str, str]:
    """Divide a resposta do modelo em (fala, detalhes) pelos rótulos FALA: e DETALHES:. Sem rótulos, tudo é fala."""
    texto = (resposta or "").strip()
    secoes = separar_secoes(texto)
    if "FALA" not in secoes:
        return texto, ""
    return secoes["FALA"], secoes.get("DETALHES", "")


def separar_secoes(resposta: str) -> dict:
    """{"DADOS": ..., "FALA": ..., "DETALHES": ...} pelos rótulos da resposta (só os que vieram, sem o rótulo)."""
    achados = list(re.finditer(r"^\s*(DADOS|FALA|DETALHES)\s*:\s*", resposta or "", re.MULTILINE | re.IGNORECASE))
    secoes = {}
    for i, achado in enumerate(achados):
        fim = achados[i + 1].start() if i + 1 < len(achados) else len(resposta)
        secoes[achado.group(1).upper()] = resposta[achado.end():fim].strip()
    return secoes


class AgenteTela:
    """`responder(texto)` devolve a `RespostaTela` ou None (o roteamento segue). O aviso "olhando..." sai por `avisar`."""

    def __init__(self, llm=None, avisar=None, capturar_fn=None, ocr_fn=None, visao_fn=None, relogio=time.time,
                 janela_fn=None):
        self.llm = llm
        self.avisar = avisar or (lambda texto: None)
        # Sem função injetada, usa a do módulo na hora de usar (assim os testes trocam a captura em um lugar só).
        self._capturar_fn, self._ocr_fn, self._janela_fn = capturar_fn, ocr_fn, janela_fn
        self._visao = visao_fn or self._ver_com_ollama
        self.relogio = relogio
        self.sessao: Sessao | None = None

    def _capturar(self, escopo):
        return (self._capturar_fn or capturar)(escopo)

    def _ocr(self, imagem):
        return (self._ocr_fn or ler_texto)(imagem)

    def _janela(self):
        return (self._janela_fn or janela_em_foco)()

    # -- entrada
    def sessao_ativa(self) -> bool:
        return self.sessao is not None and self.relogio() - self.sessao.quando <= TELA_CONTEXTO_SEGUNDOS

    def e_seguimento(self, texto: str) -> bool:
        pedido = interpretar(texto, self.sessao_ativa())
        return bool(pedido and pedido.seguimento)

    def reconhece(self, texto: str) -> bool:
        """Se o texto é um pedido para olhar a tela (ou uma pergunta sobre o que já foi lido), sem fazer nada."""
        return interpretar(texto, self.sessao_ativa()) is not None

    def responder(self, texto: str) -> RespostaTela | None:
        pedido = interpretar(texto, self.sessao_ativa())
        if pedido is None:
            self.sessao = None  # falaram de outra coisa: "explica melhor" já não é sobre a tela
            return None
        try:
            if pedido.tipo == "atividade":
                return self._atividade()
            if pedido.seguimento and not pedido.refazer:
                return self._seguir(pedido)
            if pedido.refazer and pedido.seguimento and self.sessao is not None:
                antes = self.sessao.pedido  # "olha de novo": mesma pergunta, mesmo escopo
                pedido = replace(antes, seguimento=False, refazer=False)
            return self._olhar(pedido)
        except OcrIndisponivel as exc:
            logger.warning("OCR indisponível: %s", exc)
            return RespostaTela("Não consegui ler o texto da tela: o reconhecimento de texto do Windows não está disponível.")
        except Exception as exc:
            logger.warning("Falha ao olhar a tela: %s", exc, exc_info=True)
            return RespostaTela("Não consegui olhar a tela agora. Veja se o Ollama está ligado e tente de novo.")

    # -- "o que estou fazendo?"
    def _atividade(self) -> RespostaTela:
        atividade = atividade_de(self._janela())
        if atividade is None:
            return RespostaTela("Não estou vendo nenhuma janela aberta para dizer em que você está.")
        fazendo = f", {atividade.fazendo}" if atividade.fazendo else ""
        titulo = f", com “{atividade.titulo}” na frente" if atividade.titulo else ""
        return RespostaTela(f"Você está no {atividade.programa}{titulo}{fazendo}. Quer que eu dê uma olhada no que está na tela?")

    # -- olhar de verdade
    @staticmethod
    def visao_forte() -> bool:
        return any(nome in OLLAMA_VISION_MODEL.lower() for nome in VISAO_FORTE)

    def _olhar(self, pedido: Pedido) -> RespostaTela:
        alvo = self._janela()
        atividade = atividade_de(alvo)
        if pedido.escopo == "janela" and atividade is not None:
            self.avisar(f"Deixa eu olhar a janela do {atividade.programa}.")
        else:
            self.avisar("Deixa eu olhar a tela.")
        captura = self._capturar(pedido.escopo)
        if captura.bloqueada:
            return RespostaTela("Tem uma janela de senhas ou algo que você pediu para eu não olhar na tela, então prefiro não ler agora.")
        janela = captura.janela or alvo
        tipo = pedido.tipo if pedido.tipo != "geral" else self._tipo_pelo_programa(janela)
        try:
            linhas = self._ocr(captura.imagem)
        except OcrIndisponivel:
            linhas = None
        texto = montar_texto(linhas, recuo=tipo == "codigo" or (janela is not None and janela.programa in EDITORES)) if linhas else ""
        png = self._reduzir(captura.imagem)

        # Gráficos, imagens e telas sem texto precisam do modelo de visão.
        visual = ""
        if png is not None and (tipo == "grafico" or len(texto.strip()) < 60):
            descricao = self._descrever_imagem(pedido, janela, texto, png, tipo)
            if descricao and self.visao_forte():
                secoes = separar_secoes(descricao)
                fala = secoes.get("FALA") or descricao.strip()
                painel = secoes.get("DETALHES") or secoes.get("DADOS", "")
                self._guardar(pedido, janela, texto, png, fala, secoes.get("DADOS", ""))
                return RespostaTela(fala, painel)
            visual = descricao or ""
        if not texto.strip() and not visual:
            return RespostaTela("Não consegui ler nada na tela: o OCR não está disponível e o modelo de visão não respondeu.")
        fala, painel = self._responder_com_texto(pedido, tipo, janela, texto, visual=visual)
        self._guardar(pedido, janela, texto, png, fala, visual)
        return RespostaTela(fala, painel)

    def _tipo_pelo_programa(self, janela: Janela | None) -> str:
        if janela is None:
            return "geral"
        if janela.programa in EDITORES:
            return "codigo"
        if janela.programa in LEITORES or janela.titulo.lower().endswith(".pdf"):
            return "leitura"
        return "geral"

    def _responder_com_texto(self, pedido: Pedido, tipo: str, janela: Janela | None, texto: str,
                             anterior: str = "", visual: str = "") -> tuple[str, str]:
        recorte, cortado = cortar_texto(texto)
        onde = f"{janela.programa} — {janela.titulo}" if janela is not None else "tela inteira"
        instrucao = INSTRUCOES_FALA.get(tipo, INSTRUCOES_FALA["geral"])
        historico = f"\nRESPOSTA ANTERIOR (que você deu): {anterior}\n" if anterior else ""
        if visual:
            descricao = ("\nDESCRIÇÃO VISUAL APROXIMADA (feita por um modelo de imagem fraco, em inglês: pode errar textos, "
                         "números e detalhes; use só para o formato geral e, se divergir do texto lido, confie no texto lido. "
                         f"Se for a única fonte, avise que é uma descrição aproximada):\n{visual}\n")
        else:
            descricao = ""
        prompt = (
            f"JANELA: {onde}\nPEDIDO DO USUÁRIO: {pedido.pergunta}\n{historico}\n"
            f"TEXTO LIDO NA TELA{' (o meio foi cortado por ser longo)' if cortado else ''}:\n\"\"\"\n{recorte or '(nenhum texto legível)'}\n\"\"\"\n{descricao}\n"
            "Responda em duas partes, com estes rótulos:\n"
            f"FALA: {instrucao}, em até 4 frases curtas, para ser falada em voz alta, em português, sem listas, markdown nem código.\n"
            "DETALHES: o que for útil para ler na tela (passos, o trecho corrigido, itens), em texto simples. Pode ficar vazio."
        )
        resposta = self.llm.chat_beatriz(prompt, contexto=PERSONALIDADE_TELA, usar_historico=False)
        fala, detalhes = separar(resposta)
        return fala or "Não consegui tirar nada útil do que está na tela.", detalhes

    def _descrever_imagem(self, pedido: Pedido, janela: Janela | None, texto: str, png: bytes,
                          tipo: str = "grafico", anterior: str = "") -> str | None:
        """O que o modelo de visão diz da imagem; None se ele não está instalado ou falha. Um modelo forte responde ao
        pedido inteiro (FALA: e DETALHES:, só dois rótulos: com mais o modelo de 7B repete as instruções); o llava só dá
        uma descrição aproximada do formato."""
        if self.visao_forte():
            onde = f"{janela.programa} — {janela.titulo}" if janela is not None else "a tela inteira"
            ajuda = f"\nTexto que o OCR do Windows leu (pode ter erros):\n{cortar_texto(texto, 2500)[0]}\n" if texto.strip() else ""
            antes = f"\nSua resposta anterior sobre esta mesma imagem: {anterior}\n" if anterior else ""
            prompt = (
                f"Esta é a captura de {onde}. Pedido do usuário: {pedido.pergunta}\n{ajuda}{antes}\n"
                "Responda em português, com estes dois rótulos:\n"
                f"FALA: {INSTRUCOES_FALA.get(tipo, INSTRUCOES_FALA['geral'])}, em até 4 frases curtas, para ser falada em voz alta, "
                "sem listas, markdown nem código.\n"
                "DETALHES: os valores que você leu (um por linha) e, se o pedido envolver contas (total, média, comparação), as "
                "contas feitas passo a passo.\n"
                "Só diga o que consegue ver; não invente números nem textos."
            )
        else:  # o llava rende mais em inglês e só deve dizer o formato geral
            prompt = (
                "Describe this image briefly, in at most 4 short sentences: what kind of content it is (bar chart, line chart, "
                "pie chart, table, photo, screenshot, diagram...), its main colors and shapes, and the overall trend if it is "
                "a chart. Do not try to read small text or numbers."
            )
        try:
            return self._visao(prompt, png)
        except Exception as exc:
            logger.warning("Modelo de visão indisponível (%s).", exc)
            return None

    def _ver_com_ollama(self, prompt: str, png: bytes) -> str:
        try:
            resposta = self.llm.ollama_client.chat(
                model=OLLAMA_VISION_MODEL,
                messages=[{"role": "user", "content": prompt, "images": [png]}],
                options={"temperature": 0.1, "num_ctx": 8192, "num_predict": 900},  # a imagem sozinha ocupa mais de 1500 tokens
                keep_alive=0,  # sai da placa de vídeo na hora: o modelo de texto (10 GB de 12) precisa dela para responder
            )
            return resposta["message"]["content"].strip()
        finally:
            # O modelo de conversa foi expulso da placa de vídeo: recarrega já, enquanto ela fala a resposta (leva ~8 s),
            # em vez de a próxima pergunta esperar por isso.
            aquecer = getattr(self.llm, "aquecer", None)
            if aquecer is not None:
                threading.Thread(target=aquecer, name="recarregar-modelo", daemon=True).start()

    # -- perguntas seguintes
    def _seguir(self, pedido: Pedido) -> RespostaTela:
        sessao = self.sessao  # não fotografa de novo: usa o que foi lido e a imagem guardada
        if sessao.imagem_png and self.visao_forte() and (sessao.pedido.tipo == "grafico" or len(sessao.texto.strip()) < 60):
            resposta = self._descrever_imagem(pedido, sessao.janela, sessao.texto, sessao.imagem_png, "grafico", sessao.fala)
            if resposta:
                secoes = separar_secoes(resposta)
                fala = secoes.get("FALA") or resposta.strip()
                self._guardar(pedido, sessao.janela, sessao.texto, sessao.imagem_png, fala, secoes.get("DADOS", ""))
                return RespostaTela(fala, secoes.get("DETALHES") or secoes.get("DADOS", ""))
        tipo = sessao.pedido.tipo if sessao.pedido.tipo != "geral" else self._tipo_pelo_programa(sessao.janela)
        fala, painel = self._responder_com_texto(pedido, tipo, sessao.janela, sessao.texto, anterior=sessao.fala,
                                                 visual=sessao.visual)
        sessao.fala, sessao.quando = fala, self.relogio()
        return RespostaTela(fala, painel)

    def _guardar(self, pedido, janela, texto, png, fala, visual=""):
        self.sessao = Sessao(pedido, janela, texto, png, fala, self.relogio(), visual)

    @staticmethod
    def _reduzir(imagem) -> bytes | None:
        """PNG menor para o modelo de visão (fica só na memória)."""
        if imagem is None:
            return None
        copia = imagem.copy()
        copia.thumbnail((LADO_MAXIMO_VISAO, LADO_MAXIMO_VISAO))
        buffer = io.BytesIO()
        copia.save(buffer, "PNG")
        return buffer.getvalue()
