"""Componentes visuais do painel HUD da Beatriz (Tkinter Canvas).

Inspirado em painéis estilo J.A.R.V.I.S.: molduras com cantos chanfrados,
anéis girando, medidores circulares, gráficos de barras e uma rede de nós.
Também reúne as funções que coletam os dados exibidos (CPU/RAM, biblioteca,
memória, métricas), todas tolerantes a falhas.
"""
import ctypes
import json
import math
import os
import tkinter as tk

# Paleta HUD
HUD_FUNDO = "#050B12"
HUD_PAINEL = "#08131D"
HUD_BORDA = "#12405A"
HUD_BORDA_FORTE = "#1E88B4"
HUD_CIANO = "#3FE0FF"
HUD_CIANO_ESCURO = "#0E5872"
HUD_AMBAR = "#FFB547"
HUD_LARANJA = "#FF7A3D"
HUD_VERDE = "#4ADE80"
HUD_VERMELHO = "#FF5C5C"
HUD_TEXTO = "#D9F6FF"
HUD_SUAVE = "#6C93A8"

FONTE_TITULO_HUD = ("Bahnschrift SemiBold", 10)
FONTE_HUD = ("Consolas", 9)
FONTE_HUD_GRANDE = ("Bahnschrift", 16)

PASTA_PROJETO = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------- moldura HUD
class PainelHUD(tk.Frame):
    """Painel com aba de título chanfrada e borda fina; o conteúdo vai em `.corpo`."""

    def __init__(self, pai, titulo, destaque="", cor_destaque=HUD_VERDE, **kwargs):
        super().__init__(pai, bg=HUD_FUNDO, **kwargs)
        self.titulo = titulo
        self.cabecalho = tk.Canvas(self, height=24, bg=HUD_FUNDO, highlightthickness=0)
        self.cabecalho.pack(fill="x")
        self.cabecalho.bind("<Configure>", lambda e: self._desenhar_cabecalho())
        self._destaque = destaque
        self._cor_destaque = cor_destaque

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
        largura = max(c.winfo_width(), 50)
        texto = self.titulo.upper()
        id_texto = c.create_text(12, 13, text=texto, anchor="w", fill=HUD_CIANO, font=FONTE_TITULO_HUD)
        fim_texto = c.bbox(id_texto)[2]
        if self._destaque:
            id_dest = c.create_text(fim_texto + 6, 13, text=self._destaque, anchor="w", fill=self._cor_destaque, font=FONTE_TITULO_HUD)
            fim_texto = c.bbox(id_dest)[2]
        aba = fim_texto + 12
        # Aba chanfrada atrás do título e linha que segue até o fim do painel.
        c.create_polygon(0, 23, 0, 4, 4, 0, aba, 0, aba + 14, 14, largura - 18, 14, largura - 10, 23,
                         outline=HUD_BORDA_FORTE, fill="", width=1)
        c.tag_lower(c.create_polygon(1, 22, 1, 5, 5, 1, aba, 1, aba + 13, 14, aba, 22, fill="#0B2433", outline=""))
        for i in range(4):
            x = largura - 60 + i * 9
            c.create_line(x, 6, x + 5, 6, fill=HUD_CIANO_ESCURO, width=2)


# ------------------------------------------------------------ cabeçalho HUD
class CabecalhoHUD(tk.Canvas):
    """Faixa superior com o nome centralizado, status do sistema e relógio."""

    def __init__(self, pai, nome="B.E.A.T.R.I.Z.", **kwargs):
        super().__init__(pai, height=96, bg=HUD_FUNDO, highlightthickness=0, **kwargs)
        self.nome = nome
        self.status_texto, self.status_cor = "VERIFICANDO", HUD_AMBAR
        self.hora, self.data = "", ""
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
        meio = w / 2
        # Moldura trapezoidal central e linhas laterais com degraus.
        self.create_line(10, 20, meio - 330, 20, meio - 300, 44, meio - 230, 44, fill=HUD_BORDA_FORTE, width=2)
        self.create_line(w - 10, 20, meio + 330, 20, meio + 300, 44, meio + 230, 44, fill=HUD_BORDA_FORTE, width=2)
        self.create_polygon(meio - 230, 6, meio + 230, 6, meio + 205, 62, meio - 205, 62,
                            fill="#071A26", outline=HUD_CIANO_ESCURO, width=1)
        self.create_line(meio - 205, 62, meio + 205, 62, fill=HUD_CIANO, width=2)
        for lado in (-1, 1):
            for i in range(5):
                x = meio + lado * (250 + i * 12)
                self.create_line(x, 30, x + lado * 6, 30, fill=HUD_CIANO_ESCURO, width=3)
        # Nome com brilho (texto deslocado mais escuro por trás).
        self.create_text(meio + 2, 34, text=self.nome, fill=HUD_CIANO_ESCURO, font=("Bahnschrift SemiBold", 26))
        self.create_text(meio, 32, text=self.nome, fill=HUD_CIANO, font=("Bahnschrift SemiBold", 26))
        id_rotulo = self.create_text(meio, 80, text="STATUS DO SISTEMA: ", anchor="e", fill=HUD_CIANO,
                                     font=("Bahnschrift", 13))
        self.create_text(self.bbox(id_rotulo)[2], 80, text=self.status_texto, anchor="w", fill=self.status_cor,
                         font=("Bahnschrift SemiBold", 13))
        self.create_text(w - 24, 50, text=self.hora, anchor="e", fill=HUD_TEXTO, font=("Bahnschrift", 20), tags="hora")
        self.create_text(w - 24, 76, text=self.data, anchor="e", fill=HUD_SUAVE, font=FONTE_HUD, tags="data")
        self.create_text(24, 50, text="ASSISTENTE PESSOAL LOCAL", anchor="w", fill=HUD_SUAVE, font=FONTE_HUD)
        self.create_text(24, 68, text="LLM · RAG · VOZ · VISÃO", anchor="w", fill=HUD_CIANO_ESCURO, font=FONTE_HUD)


# -------------------------------------------------------------- núcleo animado
class NucleoHUD(tk.Canvas):
    """Anéis concêntricos girando em volta de um núcleo pulsante, com legendas ligadas por linhas.

    `atividade` controla a velocidade: 'repouso', 'processando' ou 'ouvindo'.
    """

    CORES_ATIVIDADE = {"repouso": HUD_CIANO, "processando": HUD_AMBAR, "ouvindo": HUD_VERDE}

    def __init__(self, pai, **kwargs):
        super().__init__(pai, bg=HUD_PAINEL, highlightthickness=0, **kwargs)
        self.atividade = "repouso"
        self.legendas = {}  # chave -> (rótulo, valor)
        self._angulo = 0.0
        self._quadro = 0
        self._aneis = []
        self.bind("<Configure>", lambda e: self._montar())
        self.after(40, self._animar)

    def definir_legenda(self, chave, rotulo, valor):
        self.legendas[chave] = (rotulo, str(valor))
        self.itemconfigure(f"valor_{chave}", text=str(valor))

    def _montar(self):
        self.delete("all")
        self._aneis = []
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 50:
            return
        cx, cy = w / 2, h / 2
        r = min(h / 2 - 12, w / 5)
        self._centro, self._raio = (cx, cy), r

        # Grade de fundo discreta.
        for x in range(0, int(w), 28):
            self.create_line(x, 0, x, h, fill="#0A1A26")
        for y in range(0, int(h), 28):
            self.create_line(0, y, w, y, fill="#0A1A26")

        # (raio relativo, largura, extensão do arco, velocidade, cor, quantidade de segmentos)
        especificacao = [
            (1.00, 3, 300, 0.6, HUD_CIANO_ESCURO, 1),
            (0.92, 6, 70, -1.2, HUD_CIANO, 3),
            (0.80, 2, 40, 2.0, HUD_AMBAR, 4),
            (0.70, 10, 25, -0.8, HUD_CIANO, 6),
            (0.56, 3, 120, 1.6, HUD_LARANJA, 2),
            (0.46, 2, 340, -0.4, HUD_CIANO_ESCURO, 1),
        ]
        for rel, largura, extensao, velocidade, cor, segmentos in especificacao:
            raio = r * rel
            for s in range(segmentos):
                inicio = s * 360 / segmentos
                arco = self.create_arc(cx - raio, cy - raio, cx + raio, cy + raio, start=inicio, extent=extensao,
                                       style="arc", outline=cor, width=largura)
                self._aneis.append((arco, inicio, velocidade, cor))
        # Marcas de escala em volta.
        for i in range(72):
            ang = math.radians(i * 5)
            r1, r2 = r * 1.05, r * (1.10 if i % 6 == 0 else 1.07)
            self.create_line(cx + r1 * math.cos(ang), cy + r1 * math.sin(ang), cx + r2 * math.cos(ang),
                             cy + r2 * math.sin(ang), fill=HUD_CIANO_ESCURO)
        # Núcleo.
        self._halo = self.create_oval(cx - r * 0.34, cy - r * 0.34, cx + r * 0.34, cy + r * 0.34, outline=HUD_CIANO, width=2)
        self._miolo = self.create_oval(cx - r * 0.22, cy - r * 0.22, cx + r * 0.22, cy + r * 0.22, fill="#BFF6FF", outline="#7FEAFF", width=4)
        for i in range(10):
            ang = math.radians(i * 36)
            self.create_line(cx + r * 0.25 * math.cos(ang), cy + r * 0.25 * math.sin(ang), cx + r * 0.33 * math.cos(ang),
                             cy + r * 0.33 * math.sin(ang), fill=HUD_CIANO, width=4, tags="raios")
        self.create_text(cx, cy, text="B", fill="#06364A", font=("Bahnschrift SemiBold", max(int(r * 0.18), 10)))

        # Legendas ligadas por linhas, metade de cada lado.
        chaves = list(self.legendas)
        esquerda, direita = chaves[: (len(chaves) + 1) // 2], chaves[(len(chaves) + 1) // 2:]
        for lado, grupo in ((-1, esquerda), (1, direita)):
            for i, chave in enumerate(grupo):
                rotulo, valor = self.legendas[chave]
                fracao = (i + 1) / (len(grupo) + 1)
                ang = math.radians(-60 + 120 * fracao)
                px = cx + lado * r * 0.62 * math.cos(ang)
                py = cy + r * 0.62 * math.sin(ang)
                ly = 14 + fracao * (h - 28)
                lx = cx + lado * (r * 1.25)
                fx = 16 if lado < 0 else w - 16
                self.create_line(px, py, lx, ly, fx, ly, fill=HUD_AMBAR, width=1)
                self.create_oval(px - 3, py - 3, px + 3, py + 3, outline=HUD_AMBAR)
                anc = "w" if lado < 0 else "e"
                self.create_text(fx, ly - 9, text=rotulo.upper(), anchor=anc, fill=HUD_SUAVE, font=FONTE_HUD)
                self.create_text(fx, ly + 10, text=valor, anchor=anc, fill=HUD_TEXTO, font=("Bahnschrift", 13),
                                 tags=f"valor_{chave}")

    def _animar(self):
        try:
            if self._aneis:
                fator = {"repouso": 1.0, "processando": 4.0, "ouvindo": 2.5}.get(self.atividade, 1.0)
                cor_ativa = self.CORES_ATIVIDADE.get(self.atividade, HUD_CIANO)
                self._angulo += fator
                for arco, inicio, velocidade, cor in self._aneis:
                    self.itemconfigure(arco, start=(inicio + self._angulo * velocidade) % 360)
                pulso = (math.sin(self._quadro / 8) + 1) / 2
                cx, cy = self._centro
                r = self._raio * (0.34 + 0.03 * pulso * fator / 2)
                self.coords(self._halo, cx - r, cy - r, cx + r, cy + r)
                self.itemconfigure(self._halo, outline=cor_ativa)
                self.itemconfigure("raios", fill=cor_ativa)
                self._quadro += 1
            self.after(40, self._animar)
        except tk.TclError:
            pass  # janela fechada


# ------------------------------------------------------------ medidor circular
class MedidorCircular(tk.Canvas):
    """Arco de 0 a 100% com o valor no centro (como o '78%' da referência)."""

    def __init__(self, pai, rotulo, tamanho=86, **kwargs):
        super().__init__(pai, width=tamanho, height=tamanho + 16, bg=HUD_PAINEL, highlightthickness=0, **kwargs)
        t = tamanho
        self.create_arc(8, 8, t - 8, t - 8, start=225, extent=-270, style="arc", outline=HUD_CIANO_ESCURO, width=6)
        self._arco = self.create_arc(8, 8, t - 8, t - 8, start=225, extent=0, style="arc", outline=HUD_CIANO, width=6)
        self._texto = self.create_text(t / 2, t / 2, text="--", fill=HUD_TEXTO, font=("Bahnschrift", 14))
        self.create_text(t / 2, t + 6, text=rotulo, fill=HUD_SUAVE, font=FONTE_HUD)

    def definir(self, percentual):
        if percentual is None:
            self.itemconfigure(self._texto, text="--")
            return
        p = max(0.0, min(100.0, percentual))
        cor = HUD_VERMELHO if p >= 90 else HUD_AMBAR if p >= 70 else HUD_CIANO
        self.itemconfigure(self._arco, extent=-270 * p / 100, outline=cor)
        self.itemconfigure(self._texto, text=f"{p:.0f}%")


# --------------------------------------------------------------- gráfico barras
class GraficoBarras(tk.Canvas):
    """Barras verticais com os últimos valores (0 a `maximo`)."""

    def __init__(self, pai, quantidade=24, maximo=100.0, altura=60, cor=HUD_CIANO, **kwargs):
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
            self.create_line(0, y, w, y, fill="#0C2230")
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
            self.create_line(cx, cy, x, y, fill=HUD_CIANO_ESCURO)
            x2, y2, _, _ = posicoes[(i + 1) % len(posicoes)]
            self.create_line(x, y, x2, y2, fill="#0C2C3C", dash=(2, 3))
        self.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, fill=HUD_CIANO, outline="", tags="centro")
        for x, y, nome, qtd in posicoes:
            r = 4 + 8 * math.sqrt(qtd / maior)
            self.create_oval(x - r, y - r, x + r, y + r, fill="#0B3A4E", outline=HUD_CIANO, width=2)
            self.create_text(x, y + r + 8, text=f"{nome} {qtd}", fill=HUD_TEXTO, font=("Consolas", 8))

    def _pulsar(self):
        try:
            self._quadro += 1
            cor = HUD_CIANO if self._quadro % 2 else "#9FF3FF"
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
