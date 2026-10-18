"""Componentes visuais do painel da Beatriz (Tkinter Canvas).

Painéis simples em roxo e dourado, o sol da Beatriz girando no centro, medidores
circulares, gráficos de barras e uma rede de nós. Também reúne as funções que
coletam os dados exibidos (CPU/RAM, biblioteca, memória, métricas), todas
tolerantes a falhas.
"""
import ctypes
import json
import math
import os
import tkinter as tk

from PIL import ImageTk

from identidade_visual import (
    CREME,
    DOURADO,
    DOURADO_CLARO,
    LARANJA,
    LILAS,
    LILAS_SUAVE,
    ROXO_BORDA,
    ROXO_CARTAO,
    ROXO_NOITE,
    ROXO_PAINEL,
    ROXO_TRILHA,
    VERDE,
    VERMELHO,
    imagem_sol,
)

# Paleta do painel
HUD_FUNDO = ROXO_NOITE
HUD_PAINEL = ROXO_PAINEL
HUD_CARTAO = ROXO_CARTAO
HUD_BORDA = ROXO_BORDA
HUD_TRILHA = ROXO_TRILHA
HUD_DESTAQUE = DOURADO
HUD_DESTAQUE_CLARO = DOURADO_CLARO
HUD_LILAS = LILAS
HUD_AMBAR = LARANJA  # avisos e "processando"
HUD_VERDE = VERDE
HUD_VERMELHO = VERMELHO
HUD_TEXTO = CREME
HUD_SUAVE = LILAS_SUAVE

FONTE_TITULO_HUD = ("Segoe UI Semibold", 10)
FONTE_HUD = ("Segoe UI", 9)

PASTA_PROJETO = os.path.dirname(os.path.abspath(__file__))


def foto_sol(tamanho, giro=0.0):
    """Sol suavizado pronto para o Tk. Guarde a referência: o Tk não segura a imagem sozinho."""
    return ImageTk.PhotoImage(imagem_sol(tamanho, giro=giro, superamostragem=4))


def _pilula(canvas, x1, y1, x2, y2, cor):
    """Retângulo de pontas redondas (o canvas do Tk não tem um pronto)."""
    r = (y2 - y1) / 2
    canvas.create_oval(x1, y1, x1 + 2 * r, y2, fill=cor, outline="")
    canvas.create_oval(x2 - 2 * r, y1, x2, y2, fill=cor, outline="")
    canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=cor, outline="")


# -------------------------------------------------------------------- painel
class PainelHUD(tk.Frame):
    """Painel com título e borda fina; o conteúdo vai em `.corpo`."""

    def __init__(self, pai, titulo, destaque="", cor_destaque=HUD_VERDE, **kwargs):
        super().__init__(pai, bg=HUD_FUNDO, **kwargs)
        self.titulo = titulo
        self.cabecalho = tk.Canvas(self, height=24, bg=HUD_FUNDO, highlightthickness=0)
        self.cabecalho.pack(fill="x")
        self._destaque = destaque
        self._cor_destaque = cor_destaque
        self._desenhar_cabecalho()

        borda = tk.Frame(self, bg=HUD_BORDA, padx=1, pady=1)
        borda.pack(fill="both", expand=True)
        self.corpo = tk.Frame(borda, bg=HUD_PAINEL, padx=10, pady=8)
        self.corpo.pack(fill="both", expand=True)

    def definir_destaque(self, texto, cor=HUD_VERDE):
        self._destaque, self._cor_destaque = texto, cor
        self._desenhar_cabecalho()

    def _desenhar_cabecalho(self):
        c = self.cabecalho
        c.delete("all")
        id_texto = c.create_text(2, 12, text=self.titulo, anchor="w", fill=HUD_DESTAQUE, font=FONTE_TITULO_HUD)
        if self._destaque:
            c.create_text(c.bbox(id_texto)[2] + 6, 12, text=self._destaque, anchor="w", fill=self._cor_destaque,
                          font=FONTE_HUD)


# ------------------------------------------------------------------ cabeçalho
class CabecalhoHUD(tk.Canvas):
    """Faixa superior com o sol e o nome, o status no centro e o relógio."""

    def __init__(self, pai, nome="Beatriz", **kwargs):
        super().__init__(pai, height=84, bg=HUD_FUNDO, highlightthickness=0, **kwargs)
        self.nome = nome
        self.status_texto, self.status_cor = "verificando", HUD_AMBAR
        self.hora, self.data = "", ""
        self._sol = foto_sol(60)
        self.bind("<Configure>", lambda e: self.desenhar())

    def definir_status(self, texto, cor):
        self.status_texto, self.status_cor = texto, cor
        self.desenhar()

    def definir_relogio(self, hora, data):
        self.hora, self.data = hora, data
        self.itemconfigure("hora", text=hora)
        self.itemconfigure("data", text=data)

    def desenhar(self):
        self.delete("all")
        w = max(self.winfo_width(), 400)
        self.create_image(46, 42, image=self._sol)
        self.create_text(84, 34, text=self.nome, anchor="w", fill=HUD_DESTAQUE_CLARO, font=("Segoe UI Semibold", 22))
        self.create_text(86, 62, text="sua assistente pessoal", anchor="w", fill=HUD_SUAVE, font=("Segoe UI", 10))
        # Status numa pílula no centro.
        id_status = self.create_text(w / 2, 42, text=f"●  {self.status_texto}", fill=self.status_cor,
                                     font=("Segoe UI Semibold", 11))
        x1, y1, x2, y2 = self.bbox(id_status)
        _pilula(self, x1 - 16, y1 - 6, x2 + 16, y2 + 6, HUD_CARTAO)
        self.tag_raise(id_status)
        self.create_text(w - 24, 34, text=self.hora, anchor="e", fill=HUD_TEXTO, font=("Segoe UI Light", 22), tags="hora")
        self.create_text(w - 24, 62, text=self.data, anchor="e", fill=HUD_SUAVE, font=FONTE_HUD, tags="data")


# -------------------------------------------------------------- sol animado
class NucleoHUD(tk.Canvas):
    """O sol da Beatriz girando devagar no centro, com legendas dos dois lados.

    `atividade` muda a velocidade e a cor do halo: 'repouso', 'processando' ou 'ouvindo'.
    Os quadros da rotação são desenhados uma vez, suavizados, e depois só trocados;
    com a janela minimizada a animação para.
    """

    CORES_ATIVIDADE = {"repouso": HUD_TRILHA, "processando": HUD_DESTAQUE, "ouvindo": HUD_VERDE}
    VELOCIDADES = {"repouso": 0.35, "processando": 2.0, "ouvindo": 1.0}  # quadros por passo
    QUADROS = 30  # o sol se repete a cada 45 graus: 30 quadros de 1,5 grau fecham a volta
    INTERVALO_MS = 70

    def __init__(self, pai, **kwargs):
        super().__init__(pai, bg=HUD_PAINEL, highlightthickness=0, **kwargs)
        self.atividade = "repouso"
        self.legendas = {}  # chave -> (rótulo, valor)
        self._fase = 0.0
        self._passo = 0
        self._quadros = {}  # índice -> PhotoImage
        self._tamanho_sol = 0
        self._sol = None
        self.bind("<Configure>", lambda e: self._montar())
        self.after(self.INTERVALO_MS, self._animar)

    def definir_legenda(self, chave, rotulo, valor):
        nova = chave not in self.legendas
        self.legendas[chave] = (rotulo, str(valor))
        if nova:
            self._montar()  # uma legenda nova precisa de posição no desenho
        else:
            self.itemconfigure(f"valor_{chave}", text=str(valor))

    def _quadro(self, indice):
        if indice not in self._quadros:
            self._quadros[indice] = foto_sol(self._tamanho_sol, giro=indice * 45 / self.QUADROS)
        return self._quadros[indice]

    def _montar(self):
        self.delete("all")
        self._sol = None
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 50:
            return
        cx, cy = w / 2, h / 2
        tamanho = int(min(h - 12, w / 3))
        if tamanho != self._tamanho_sol:
            self._tamanho_sol, self._quadros = tamanho, {}
        self._centro = (cx, cy)
        self._raio = tamanho * 0.45  # ponta dos raios longos (45% do quadrado da imagem)

        # Brilho atrás do sol e um anel fino que pulsa com a cor da atividade.
        r = self._raio * 0.8
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=HUD_CARTAO, outline="")
        self._halo = self.create_oval(cx, cy, cx, cy, outline=HUD_TRILHA, width=2)
        self._sol = self.create_image(cx, cy, image=self._quadro(int(self._fase)))

        # Legendas, metade de cada lado.
        chaves = list(self.legendas)
        esquerda, direita = chaves[: (len(chaves) + 1) // 2], chaves[(len(chaves) + 1) // 2:]
        for lado, grupo in ((-1, esquerda), (1, direita)):
            for i, chave in enumerate(grupo):
                rotulo, valor = self.legendas[chave]
                y = 14 + (i + 1) / (len(grupo) + 1) * (h - 28)
                x = 20 if lado < 0 else w - 20
                ancora = "w" if lado < 0 else "e"
                self.create_text(x, y - 10, text=rotulo, anchor=ancora, fill=HUD_SUAVE, font=FONTE_HUD)
                self.create_text(x, y + 9, text=valor, anchor=ancora, fill=HUD_TEXTO, font=("Segoe UI Semibold", 13),
                                 tags=f"valor_{chave}")

    def _animar(self):
        try:
            if self._sol is not None and self.winfo_viewable():
                self._fase = (self._fase + self.VELOCIDADES.get(self.atividade, 0.35)) % self.QUADROS
                self.itemconfigure(self._sol, image=self._quadro(int(self._fase)))
                self._passo += 1
                pulso = (math.sin(self._passo / 6) + 1) / 2
                cx, cy = self._centro
                r = self._raio * (1.03 + 0.05 * pulso)
                self.coords(self._halo, cx - r, cy - r, cx + r, cy + r)
                self.itemconfigure(self._halo, outline=self.CORES_ATIVIDADE.get(self.atividade, HUD_TRILHA))
            self.after(self.INTERVALO_MS, self._animar)
        except tk.TclError:
            pass  # janela fechada


# ------------------------------------------------------------ medidor circular
class MedidorCircular(tk.Canvas):
    """Arco de 0 a 100% com o valor no centro."""

    def __init__(self, pai, rotulo, tamanho=86, **kwargs):
        super().__init__(pai, width=tamanho, height=tamanho + 16, bg=HUD_PAINEL, highlightthickness=0, **kwargs)
        t = tamanho
        self.create_arc(8, 8, t - 8, t - 8, start=225, extent=-270, style="arc", outline=HUD_TRILHA, width=7)
        self._arco = self.create_arc(8, 8, t - 8, t - 8, start=225, extent=0, style="arc", outline=HUD_DESTAQUE, width=7)
        self._texto = self.create_text(t / 2, t / 2, text="--", fill=HUD_TEXTO, font=("Segoe UI Semibold", 13))
        self.create_text(t / 2, t + 6, text=rotulo, fill=HUD_SUAVE, font=FONTE_HUD)

    def definir(self, percentual):
        if percentual is None:
            self.itemconfigure(self._texto, text="--")
            return
        p = max(0.0, min(100.0, percentual))
        cor = HUD_VERMELHO if p >= 90 else HUD_AMBAR if p >= 70 else HUD_DESTAQUE
        self.itemconfigure(self._arco, extent=-270 * p / 100, outline=cor)
        self.itemconfigure(self._texto, text=f"{p:.0f}%")


# --------------------------------------------------------------- gráfico barras
class GraficoBarras(tk.Canvas):
    """Barras verticais com os últimos valores (0 a `maximo`)."""

    def __init__(self, pai, quantidade=24, maximo=100.0, altura=60, cor=HUD_DESTAQUE, **kwargs):
        super().__init__(pai, height=altura, bg=HUD_PAINEL, highlightthickness=0, **kwargs)
        self.quantidade, self.maximo, self.cor = quantidade, maximo, cor
        self.valores = []
        self.bind("<Configure>", lambda e: self.desenhar())

    def adicionar(self, valor):
        self.valores = (self.valores + [valor])[-self.quantidade:]
        self.desenhar()

    def definir_valores(self, valores):
        self.valores = list(valores)[-self.quantidade:]
        self.desenhar()

    def desenhar(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            return
        for y in (h * 0.25, h * 0.5, h * 0.75):
            self.create_line(0, y, w, y, fill=HUD_BORDA)
        passo = w / self.quantidade
        maximo = self.maximo or max(self.valores or [1]) or 1
        deslocamento = self.quantidade - len(self.valores)
        for i, valor in enumerate(self.valores):
            altura = max(2, (h - 4) * min(valor / maximo, 1))
            x = (i + deslocamento) * passo
            cor = HUD_AMBAR if valor / maximo > 0.8 else self.cor
            self.create_rectangle(x + 2, h - altura, x + passo - 2, h, fill=cor, outline="")


# ------------------------------------------------------------ rede de nós
class RedeBiblioteca(tk.Canvas):
    """Nós ligados representando as fontes da biblioteca; o tamanho segue a quantidade."""

    def __init__(self, pai, altura=130, **kwargs):
        super().__init__(pai, height=altura, bg=HUD_PAINEL, highlightthickness=0, **kwargs)
        self.grupos = {}
        self._quadro = 0
        self.bind("<Configure>", lambda e: self.desenhar())
        self.after(120, self._pulsar)

    def definir_grupos(self, grupos):
        if grupos != self.grupos:
            self.grupos = dict(grupos)
            self.desenhar()

    def desenhar(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or not self.grupos:
            self.create_text(w / 2, h / 2, text="sem fontes indexadas", fill=HUD_SUAVE, font=FONTE_HUD)
            return
        cx, cy = w / 2, h / 2
        maior = max(self.grupos.values()) or 1
        itens = list(self.grupos.items())
        posicoes = []
        for i, (nome, qtd) in enumerate(itens):
            ang = 2 * math.pi * i / len(itens) - math.pi / 2
            posicoes.append((cx + (w / 2 - 46) * math.cos(ang), cy + (h / 2 - 22) * math.sin(ang), nome, qtd))
        for i, (x, y, _, _) in enumerate(posicoes):
            self.create_line(cx, cy, x, y, fill=HUD_TRILHA)
            x2, y2, _, _ = posicoes[(i + 1) % len(posicoes)]
            self.create_line(x, y, x2, y2, fill=HUD_BORDA, dash=(2, 3))
        self.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, fill=HUD_DESTAQUE, outline="", tags="centro")
        for x, y, nome, qtd in posicoes:
            r = 4 + 8 * math.sqrt(qtd / maior)
            self.create_oval(x - r, y - r, x + r, y + r, fill=HUD_CARTAO, outline=HUD_DESTAQUE, width=2)
            self.create_text(x, y + r + 8, text=f"{nome} {qtd}", fill=HUD_TEXTO, font=("Segoe UI", 8))

    def _pulsar(self):
        try:
            self._quadro += 1
            cor = HUD_DESTAQUE if self._quadro % 2 else HUD_DESTAQUE_CLARO
            self.itemconfigure("centro", fill=cor)
            self.after(700, self._pulsar)
        except tk.TclError:
            pass


# ------------------------------------------------------------------- dados
class _FILETIME(ctypes.Structure):
    _fields_ = [("baixo", ctypes.c_ulong), ("alto", ctypes.c_ulong)]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class MonitorRecursos:
    """CPU e RAM do Windows via API nativa (sem depender do psutil)."""

    def __init__(self):
        self._anterior = self._tempos()

    @staticmethod
    def _tempos():
        try:
            ocioso, kernel, usuario = _FILETIME(), _FILETIME(), _FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(ocioso), ctypes.byref(kernel), ctypes.byref(usuario)):
                return None
            valor = lambda t: (t.alto << 32) | t.baixo
            return valor(ocioso), valor(kernel), valor(usuario)
        except Exception:
            return None

    def cpu(self):
        atual = self._tempos()
        if not atual or not self._anterior:
            self._anterior = atual
            return None
        d_ocioso, d_kernel, d_usuario = (a - b for a, b in zip(atual, self._anterior))
        self._anterior = atual
        total = d_kernel + d_usuario  # o tempo de kernel já inclui o ocioso
        return None if total <= 0 else 100.0 * (1 - d_ocioso / total)

    @staticmethod
    def ram():
        try:
            estado = _MEMORYSTATUSEX()
            estado.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado)):
                usada = (estado.ullTotalPhys - estado.ullAvailPhys) / 1024 ** 3
                return float(estado.dwMemoryLoad), usada, estado.ullTotalPhys / 1024 ** 3
        except Exception:
            pass
        return None, 0.0, 0.0


def ler_metricas_analise():
    """Totais gravados pelo processo principal em beatriz_analise.json."""
    try:
        with open(os.path.join(PASTA_PROJETO, "beatriz_analise.json"), "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except Exception:
        return {}
    tempos = [t for t in dados.get("tempo_resposta", []) if t]
    return {
        "interacoes": dados.get("total_interacoes", 0),
        "erros": dados.get("total_erros", 0),
        "sucesso": dados.get("taxa_sucesso", 100.0),
        "tempo_medio": sum(tempos[-50:]) / len(tempos[-50:]) if tempos else 0.0,
        "tempos": tempos[-24:],
    }


def ler_fatos_memoria(caminho, ultimos=3):
    try:
        with open(caminho, "r", encoding="utf-8") as arquivo:
            fatos = [linha.strip().lstrip("- ").strip() for linha in arquivo if linha.lstrip().startswith("-")]
    except OSError:
        return 0, []
    recentes = []
    for fato in fatos[-ultimos:][::-1]:
        # Remove o carimbo de data "2026-01-01T10:00:00: ".
        recentes.append(fato.split(": ", 1)[1] if fato[:4].isdigit() and ": " in fato else fato)
    return len(fatos), recentes


def contar_linhas(caminho):
    try:
        with open(caminho, "rb") as arquivo:
            return sum(1 for _ in arquivo)
    except OSError:
        return 0


def grupos_da_biblioteca(caminho_manifesto, raizes):
    """Quantidade de fontes indexadas por assunto, lida do manifesto da indexação.

    `raizes` liga o tipo da fonte à sua pasta ({"obsidian": VAULT, ...}); os arquivos
    são agrupados pela primeira subpasta (direito, filosofia...).
    """
    try:
        with open(caminho_manifesto, "r", encoding="utf-8") as arquivo:
            manifesto = json.load(arquivo)
    except Exception:
        return {}
    nomes = {"documento_local": "docs", "obsidian": "obsidian", "historico": "histórico"}
    grupos = {}
    for registro in manifesto.values():
        if registro.get("vazio"):
            continue
        tipo = registro.get("tipo", "outros")
        nome = nomes.get(tipo, tipo)
        raiz = raizes.get(tipo)
        if raiz:
            try:
                partes = os.path.relpath(registro.get("caminho", ""), raiz).split(os.sep)
            except ValueError:  # outra unidade de disco
                partes = []
            if len(partes) > 1 and not partes[0].startswith(".."):
                nome = partes[0]
        grupos[nome] = grupos.get(nome, 0) + 1
    # Mantém os 7 maiores para o desenho não poluir.
    return dict(sorted(grupos.items(), key=lambda item: item[1], reverse=True)[:7])
