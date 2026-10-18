"""A Beatriz andando pela tela: uma janela pequena, sem moldura, transparente e sempre por cima.

Ela anda pela borda de baixo da área de trabalho (em cima da barra de tarefas), perto da janela
que você está usando; para quando ouve o nome, pensa ou fala; cochila quando você fica muito
tempo sem mexer no computador; e some quando um programa está em tela cheia (vídeo, jogo).

Para ficar perto do que você faz e dar sugestões ("quer um resumo desta notícia?"), ela olha o
programa e o título da janela em primeiro plano. Isso fica só no computador: nada vai para a
nuvem, a não ser que você aceite uma sugestão (aí é um pedido como outro qualquer).

A janela é do pywebview, no mesmo processo do painel web. O fundo some com a "cor-chave" do
Windows (TransparencyKey): os pixels dessa cor ficam transparentes e deixam os cliques passarem.
"""
import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import queue
import random
import re
import threading
import time

from config import SUGESTOES_DA_MASCOTE

logger = logging.getLogger(__name__)

PAGINA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "interface_web", "mascote.html")
LARGURA, ALTURA = 240, 310  # a mascote ocupa a parte de baixo; o balão de fala, a de cima
VELOCIDADE = 55  # pixels por segundo
QUADROS_POR_SEGUNDO = 60  # a janela anda em passos menores: o movimento fica liso
COCHILO_SEGUNDOS = 300  # sem mexer no mouse nem no teclado
INTERVALO_MINIMO_SUGESTOES = 120
COR_CHAVE = (13, 10, 18)  # escura, para a borda suavizada do desenho parecer um contorno

NAVEGADORES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
EDITORES = {"code.exe", "cursor.exe", "devenv.exe", "pycharm64.exe", "idea64.exe", "notepad++.exe", "sublime_text.exe"}
RE_NOTICIA = re.compile(
    r"(?i)\b(g1|uol|folha|estad[aã]o|cnn|bbc|globo|terra|poder360|metr[oó]poles|infomoney|valor|exame|"
    r"wikip[eé]dia|not[ií]cias?)\b"
)
RE_SUFIXO_NAVEGADOR = re.compile(r"\s*[-–—|]\s*(Google Chrome|Microsoft\W*Edge|Mozilla Firefox|Brave|Opera|Vivaldi)\s*$", re.I)


# ------------------------------------------------------------------ Windows
class _Monitor(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


class _UltimaEntrada(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def area_de_trabalho() -> tuple:
    """(esquerda, topo, direita, base) da área útil da tela principal, sem a barra de tarefas."""
    caixa = wt.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(caixa), 0)  # SPI_GETWORKAREA
    return caixa.left, caixa.top, caixa.right, caixa.bottom


def segundos_ocioso() -> float:
    entrada = _UltimaEntrada(ctypes.sizeof(_UltimaEntrada), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(entrada)):
        return 0.0
    return max(0.0, (ctypes.windll.kernel32.GetTickCount() - entrada.dwTime) / 1000)


def _nome_do_programa(pid: int) -> str:
    processo = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not processo:
        return ""
    try:
        tamanho = wt.DWORD(512)
        caminho = ctypes.create_unicode_buffer(512)
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(processo, 0, caminho, ctypes.byref(tamanho)):
            return os.path.basename(caminho.value).lower()
        return ""
    finally:
        ctypes.windll.kernel32.CloseHandle(processo)


def contexto_da_tela() -> dict:
    """O que está em primeiro plano: programa, título, posição e se está em tela cheia."""
    user32 = ctypes.windll.user32
    contexto = {"area": area_de_trabalho(), "ocioso": segundos_ocioso()}
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return contexto
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return contexto  # o próprio painel ou a mascote
    classe = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, classe, 256)
    if classe.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return contexto  # área de trabalho ou barra de tarefas
    titulo = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, titulo, 512)
    caixa = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(caixa))
    monitor = _Monitor(ctypes.sizeof(_Monitor))
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(monitor))
    tela = monitor.rcMonitor
    contexto.update(
        programa=_nome_do_programa(pid.value), titulo=titulo.value,
        janela=(caixa.left, caixa.top, caixa.right, caixa.bottom),
        tela_cheia=caixa.left <= tela.left and caixa.top <= tela.top and caixa.right >= tela.right and caixa.bottom >= tela.bottom,
    )
    return contexto


def preparar_janela_windows(janela, cor=COR_CHAVE):
    """Fundo transparente pela cor-chave e fora da barra de tarefas e do Alt+Tab."""
    import clr  # noqa: F401  (pythonnet, já carregado pelo pywebview)
    from System import Action
    from System.Drawing import Color

    def aplicar():
        chave = Color.FromArgb(255, *cor)
        formulario = janela.native
        formulario.BackColor = chave
        formulario.TransparencyKey = chave
        hwnd = formulario.Handle.ToInt64()
        estilo = ctypes.windll.user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        formulario.Hide()
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, (estilo | 0x80) & ~0x40000)  # WS_EX_TOOLWINDOW, sem APPWINDOW
        formulario.Show()

    janela.native.Invoke(Action(aplicar))


def assunto_do_titulo(titulo: str) -> str:
    """'(3) Reforma tributária: o que muda - g1 - Google Chrome' -> 'Reforma tributária: o que muda'."""
    assunto = RE_SUFIXO_NAVEGADOR.sub("", titulo or "")
    assunto = re.sub(r"^\(\d+\)\s*", "", assunto)
    assunto = re.sub(r"\s*[-–—|]\s*Wikip[eé]dia\b.*$", "", assunto, flags=re.I)
    # O último trecho depois de um traço costuma ser o nome do site ("- g1", "| Folha").
    sem_site = re.sub(r"\s*[-–—|]\s*[^-–—|]{1,30}$", "", assunto)
    return (sem_site if len(sem_site) >= 8 else assunto).strip()


# ------------------------------------------------------------------ a mascote
class ApiMascote:
    """Métodos que a página da mascote chama."""

    def __init__(self, ao_aceitar, abrir_painel):
        self._eventos = queue.Queue()
        self._ao_aceitar = ao_aceitar
        self._abrir_painel = abrir_painel

    def eventos(self) -> list:
        novos = []
        try:
            while len(novos) < 20:
                novos.append(self._eventos.get_nowait())
        except queue.Empty:
            pass
        return novos

    def aceitar_sugestao(self, comando: str) -> bool:
        if comando:
            self._ao_aceitar(comando)
            self._abrir_painel()
        return True

    def abrir_painel(self) -> bool:
        self._abrir_painel()
        return True


class MascoteDaTela:
    """Decide para onde a mascote anda e quando para, cochila, some ou sugere algo."""

    def __init__(self, enviar_pedido, janela_painel=None, sugestoes=SUGESTOES_DA_MASCOTE):
        self.api = ApiMascote(enviar_pedido, self._mostrar_painel)
        self.janela_painel = janela_painel
        self.janela = None
        self.sugestoes = sugestoes
        self.x = None
        self.y = None
        self.direcao = -1
        self.modo = ""
        self._ate = 0.0  # até quando o modo atual dura
        self._atenta_ate = 0.0  # parada porque ouviu, pensa ou fala
        self._escondida = False
        self._contexto = {}
        self._feitas = {}  # sugestão -> quando foi feita
        self._ultima_sugestao = 0.0
        self._ativa_desde = time.time()

    # ---------------------------------------------------------------- janela
    def criar_janela(self):
        import webview

        esquerda, _, direita, base = area_de_trabalho()
        self.x, self.y = direita - LARGURA - 60, base - ALTURA
        self.janela = webview.create_window(
            "Beatriz", url=PAGINA, js_api=self.api, width=LARGURA, height=ALTURA, x=self.x, y=self.y,
            frameless=True, transparent=True, on_top=True, focus=False, easy_drag=False, resizable=False,
        )
        self.janela.events.shown += self._ao_mostrar
        return self.janela

    def _ao_mostrar(self):
        try:
            preparar_janela_windows(self.janela)
        except Exception as exc:
            logger.warning("Não foi possível deixar a mascote transparente: %s", exc)
        threading.Thread(target=self._viver, name="mascote-anda", daemon=True).start()

    def _mostrar_painel(self):
        if self.janela_painel is None:
            return
        for acao in ("restore", "show"):
            try:
                getattr(self.janela_painel, acao)()
            except Exception:
                pass

    # ---------------------------------------------------------------- reações
    def ao_evento(self, evento: dict):
        """Eventos da voz e do painel: a página anima; aqui ela para de andar enquanto conversa."""
        self.api._eventos.put(evento)
        tipo = evento.get("tipo")
        if tipo == "resposta":
            self._atenta_ate = time.time() + min(40.0, len(evento.get("texto", "")) / 14 + 2)
        elif tipo in ("ouvido", "voce", "pensando", "resumo", "filmes"):
            self._atenta_ate = time.time() + 8

    def _sugerir(self, chave: str, texto: str, comando: str = "", intervalo: float | None = None):
        agora = time.time()
        feita = self._feitas.get(chave)
        if feita is not None and (intervalo is None or agora - feita < intervalo):
            return False
        if agora - self._ultima_sugestao < INTERVALO_MINIMO_SUGESTOES:
            return False
        self._feitas[chave] = self._ultima_sugestao = agora
        self._atenta_ate = agora + 10
        self.api._eventos.put({"tipo": "sugestao", "texto": texto, "comando": comando})
        return True

    def pensar_em_sugestao(self, contexto: dict):
        """Poucas sugestões e sem repetir: notícia aberta, programação longa, muito tempo sem pausa."""
        if not self.sugestoes:
            return
        agora = time.time()
        if contexto.get("ocioso", 0) > 300:
            self._ativa_desde = agora  # parou um pouco: conta de novo
        programa, titulo = contexto.get("programa", ""), contexto.get("titulo", "")
        if programa in NAVEGADORES and RE_NOTICIA.search(titulo) and "YouTube" not in titulo:
            assunto = assunto_do_titulo(titulo)
            if len(assunto) >= 4:
                self._sugerir(f"noticia:{assunto}", f"Quer um resumo sobre “{assunto}”?", f"faz um resumo sobre {assunto}")
                return
        if programa in EDITORES:
            if self._sugerir("codigo", "Programando? Se travar em alguma coisa, é só me chamar.", intervalo=3 * 3600):
                return
        if agora - self._ativa_desde > 3600:
            self._sugerir("pausa", "Você está há mais de uma hora no computador. Que tal uma pausa de cinco minutos?",
                          intervalo=3600)

    # ---------------------------------------------------------------- movimento
    def _definir(self, modo: str, segundos: float = 0.0, agora: float | None = None):
        mudou = modo != self.modo
        self.modo = modo
        self._ate = (agora if agora is not None else time.monotonic()) + segundos
        if mudou:
            self._enviar_pose()

    def _enviar_pose(self):
        if self.janela is not None:
            try:
                self.janela.evaluate_js(f"definirPose({json.dumps(self.modo)}, {self.direcao})")
            except Exception as exc:
                logger.debug("Mascote não recebeu a pose: %s", exc)

    @staticmethod
    def faixa(contexto: dict) -> tuple:
        """De onde até onde ela anda: embaixo da janela em uso, ou na tela toda."""
        esquerda, _, direita, _ = contexto["area"]
        minimo, maximo = esquerda, direita - LARGURA
        janela = contexto.get("janela")
        if janela:
            perto_min, perto_max = max(minimo, janela[0]), min(maximo, janela[2] - LARGURA)
            if perto_max - perto_min >= 80:
                return perto_min, perto_max
        return minimo, maximo

    def passo(self, agora: float, dt: float, contexto: dict) -> str:
        """Avança um quadro e devolve o modo: andando, parada, dormindo ou escondida."""
        if contexto.get("tela_cheia"):
            return "escondida"
        if contexto.get("ocioso", 0) > COCHILO_SEGUNDOS:
            if self.modo != "dormindo":
                self._definir("dormindo", agora=agora)
            return self.modo
        if time.time() < self._atenta_ate:
            if self.modo != "parada":
                self._definir("parada", 1, agora=agora)
            return self.modo
        minimo, maximo = self.faixa(contexto)
        if self.modo in ("", "dormindo") or (self.modo == "parada" and agora >= self._ate):
            if self.x < minimo or self.x > maximo:
                self.direcao = 1 if self.x < minimo else -1  # volta para perto da janela em uso
            else:
                self.direcao = random.choice((-1, 1))
            self._definir("andando", random.uniform(3, 9), agora=agora)
            self._enviar_pose()
        elif self.modo == "andando":
            antes = self.x
            self.x += self.direcao * VELOCIDADE * dt
            if (self.direcao < 0 and self.x <= minimo) or (self.direcao > 0 and self.x >= maximo):
                if minimo <= antes <= maximo:
                    self.x = min(max(self.x, minimo), maximo)  # estava na faixa: para na borda
                self.direcao *= -1  # fora da faixa, só vira e caminha até ela
                self._enviar_pose()
            if agora >= self._ate:
                self._definir("parada", random.uniform(2, 7), agora=agora)
        esquerda, _, direita, _ = contexto["area"]
        self.x = min(max(self.x, esquerda), direita - LARGURA)
        return self.modo

    def _viver(self):
        anterior = time.monotonic()
        proximo_contexto = 0.0
        posicao = None
        while True:
            agora = time.monotonic()
            dt, anterior = min(agora - anterior, 0.2), agora
            try:
                if agora >= proximo_contexto:
                    self._contexto = contexto_da_tela()
                    self.pensar_em_sugestao(self._contexto)
                    proximo_contexto = agora + 1.0
                modo = self.passo(agora, dt, self._contexto)
                if modo == "escondida" and not self._escondida:
                    self.janela.hide()
                    self._escondida = True
                elif modo != "escondida":
                    if self._escondida:
                        self.janela.show()
                        self._escondida = False
                    nova = (int(self.x), self._contexto["area"][3] - ALTURA)
                    if nova != posicao:
                        self.janela.move(*nova)
                        posicao = nova
            except Exception as exc:
                logger.debug("Mascote tropeçou: %s", exc)
            time.sleep(1 / QUADROS_POR_SEGUNDO)
