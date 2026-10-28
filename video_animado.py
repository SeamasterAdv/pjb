"""O vídeo animado montado no PC, no estilo do operador (estilo_video.py), e a edição dos vídeos dele nesse estilo.

**Vídeo animado** (`renderizar`): o roteiro vem do Claude (criacao_visual.py): cenas com título, texto, tópicos, narração e
uma ilustração em SVG. Aqui:
1. a narração de cada cena vira áudio na voz escolhida (edge-tts, como a fala dela; `narrar`);
2. as ilustrações são desenhadas pelo Chromium do Playwright, sem janela (`rasterizar_svgs`: o PyMuPDF não desenha
   degradês nem filtros, e as ilustrações do Claude vivem deles);
3. cada quadro é composto com o Pillow (fundo em degradê, ilustração com movimento, textos entrando, legendas e a transição
   entre as cenas) e gravado com o PyAV em MP4 (H.264 + AAC), a 30 quadros por segundo.
A duração de cada cena é a maior entre o ritmo, o tempo de leitura e a narração.

**Seus vídeos** (`estilizar_video`): enquadra no formato do estilo (cortando ou com o fundo desfocado), põe o título na
abertura, as legendas (das palavras que o Whisper ouviu, no PC) e a assinatura no fim, nas suas cores e na sua letra.
O filtro de cor é do FilmCraft, antes (agente_craft.py).
"""
import functools
import logging
import math
import os
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import estilo_video

logger = logging.getLogger(__name__)

FPS = 30
TAMANHOS = {"16x9": (1280, 720), "9x16": (720, 1280), "1x1": (1080, 1080)}
DURACAO_BASE = {"calmo": 6.0, "normal": 4.5, "rapido": 3.0}
DURACAO_DA_TRANSICAO = {"calmo": 0.9, "normal": 0.6, "rapido": 0.35}
DURACAO_DA_ENTRADA = {"calmo": 0.9, "normal": 0.6, "rapido": 0.4}
ATRASO_ENTRE_TEXTOS = {"calmo": 0.35, "normal": 0.25, "rapido": 0.15}
DURACAO_DA_ABERTURA = 2.6
DURACAO_DA_ASSINATURA = 2.4
ATRASO_DA_NARRACAO = 0.35  # a narração começa depois de a transição assentar
TAXA = 44100
MAXIMO_DE_CENAS = 20
NARRACOES_AO_MESMO_TEMPO = 3
DURACAO_MAXIMA_DA_CENA = 22.0
LAYOUTS = ("lado", "centro", "tela_cheia", "lista", "titulo")
PASTA_DAS_FONTES = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


class ErroDoVideo(RuntimeError):
    """Não deu para montar o vídeo (sem cenas, falha ao gravar)."""


# ------------------------------------------------------------------ cores, letras e textos
def rgb(cor: str, alfa: int = None) -> tuple:
    valores = tuple(int(cor[i:i + 2], 16) for i in (1, 3, 5))
    return valores if alfa is None else valores + (alfa,)


@functools.lru_cache(maxsize=64)
def fonte(arquivo: str, tamanho: int):
    try:
        return ImageFont.truetype(os.path.join(PASTA_DAS_FONTES, arquivo), tamanho)
    except OSError:
        try:
            return ImageFont.truetype("arial.ttf", tamanho)
        except OSError:
            return ImageFont.load_default(tamanho)


def quebrar(texto: str, letra, largura: float) -> list:
    """As linhas do texto que cabem na largura (uma palavra grande demais fica sozinha na linha)."""
    linhas, atual = [], ""
    for palavra in (texto or "").split():
        tentativa = f"{atual} {palavra}".strip()
        if atual and letra.getlength(tentativa) > largura:
            linhas.append(atual)
            atual = palavra
        else:
            atual = tentativa
    if atual:
        linhas.append(atual)
    return linhas


def _com_sombra(imagem: Image.Image, raio: int) -> Image.Image:
    """O texto com uma sombra macia em volta, para ler sobre qualquer ilustração."""
    alfa = imagem.getchannel("A").filter(ImageFilter.GaussianBlur(raio))
    sombra = Image.new("RGBA", imagem.size, (0, 0, 0, 0))
    sombra.putalpha(alfa.point(lambda v: min(255, int(v * 0.85))))
    sombra.alpha_composite(imagem)
    return sombra


class Bloco:
    """Um texto na tela: título, texto, tópico (com o marcador) ou a legenda. `imagem(parte)` dá o texto inteiro ou só as
    primeiras `parte` letras (ou palavras), para o "digitar" e o "palavra por palavra"."""

    def __init__(self, texto, letra, cor, largura, alinhar="esquerda", marcador=None, sombra=True, maximo_de_linhas=None):
        self.texto = re.sub(r"\s+", " ", str(texto or "")).strip()
        self.letra, self.cor, self.alinhar, self.marcador, self.sombra = letra, cor, alinhar, marcador, sombra
        self.recuo = int(letra.size * 0.9) if marcador else 0
        self.largura = int(largura)
        self.linhas = quebrar(self.texto, letra, self.largura - self.recuo)
        if maximo_de_linhas and len(self.linhas) > maximo_de_linhas:
            self.linhas = self.linhas[:maximo_de_linhas]
            self.linhas[-1] = self.linhas[-1].rstrip(".,;:") + "…"
        self.altura_da_linha = int(letra.size * 1.22)
        self.margem = max(4, letra.size // 6)
        self.tamanho = (self.largura + 2 * self.margem, len(self.linhas) * self.altura_da_linha + 2 * self.margem)
        self._cache = {}

    @property
    def letras(self) -> int:
        return sum(len(linha) for linha in self.linhas)

    @property
    def palavras(self) -> int:
        return sum(len(linha.split()) for linha in self.linhas)

    def imagem(self, letras: int = None, palavras: int = None) -> Image.Image:
        chave = (letras, palavras)
        if chave in self._cache:
            return self._cache[chave]
        imagem = Image.new("RGBA", self.tamanho, (0, 0, 0, 0))
        desenho = ImageDraw.Draw(imagem)
        resto_letras, resto_palavras = letras, palavras
        for numero, linha in enumerate(self.linhas):
            if resto_letras is not None:
                linha, resto_letras = linha[:max(0, resto_letras)], resto_letras - len(linha)
            if resto_palavras is not None:
                partes = linha.split()
                linha, resto_palavras = " ".join(partes[:max(0, resto_palavras)]), resto_palavras - len(partes)
            y = self.margem + numero * self.altura_da_linha
            if numero == 0 and self.marcador:
                lado = max(6, self.letra.size // 3)
                topo = y + (self.altura_da_linha - lado) // 2
                desenho.rectangle((self.margem, topo, self.margem + lado, topo + lado), fill=self.marcador)
            if not linha:
                continue
            largura = self.letra.getlength(linha)
            x = self.margem + self.recuo
            if self.alinhar == "centro":
                x = self.margem + (self.largura - largura) / 2
            desenho.text((x, y), linha, font=self.letra, fill=self.cor)
        if self.sombra:
            imagem = _com_sombra(imagem, max(2, self.letra.size // 10))
        if len(self._cache) < 400:
            self._cache[chave] = imagem
        return imagem


def _degrade(tamanho, cor_cima: str, cor_baixo: str) -> Image.Image:
    largura, altura = tamanho
    cima, baixo = np.array(rgb(cor_cima), dtype=np.float32), np.array(rgb(cor_baixo), dtype=np.float32)
    passo = np.linspace(0, 1, altura, dtype=np.float32)[:, None]
    coluna = (cima * (1 - passo) + baixo * passo).astype(np.uint8)
    return Image.fromarray(np.repeat(coluna[:, None, :], largura, axis=1), "RGB").convert("RGBA")


def _suave(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


def _saida_cubica(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1 - (1 - p) ** 3


def _salto(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1 + 2.70158 * (p - 1) ** 3 + 1.70158 * (p - 1) ** 2


def _com_alfa(imagem: Image.Image, quanto: float) -> Image.Image:
    if quanto >= 0.999:
        return imagem
    copia = imagem.copy()
    copia.putalpha(imagem.getchannel("A").point(lambda v: int(v * max(0.0, quanto))))
    return copia


# ------------------------------------------------------------------ legendas
def partes_da_legenda(texto: str, palavras_por_parte: int = 7, maximo_de_letras: int = 44) -> list:
    """A narração em pedaços curtos para a legenda (quebra nas pontuações quando dá)."""
    partes, atual = [], []
    for palavra in (texto or "").split():
        atual.append(palavra)
        cheio = len(atual) >= palavras_por_parte or len(" ".join(atual)) >= maximo_de_letras
        if cheio or (len(atual) >= 3 and palavra[-1:] in ".,;:!?"):
            partes.append(" ".join(atual))
            atual = []
    if atual:
        partes.append(" ".join(atual))
    return partes


def legendas_no_tempo(texto: str, inicio: float, duracao: float) -> list:
    """[(início, fim, texto)] repartindo a duração pelo número de letras de cada pedaço."""
    partes = partes_da_legenda(texto)
    total = sum(len(p) for p in partes) or 1
    legendas, momento = [], inicio
    for parte in partes:
        fim = momento + duracao * len(parte) / total
        legendas.append((momento, fim, parte))
        momento = fim
    return legendas


def legendas_das_palavras(palavras: list, maximo: int = 7, pausa: float = 0.6) -> list:
    """[(início, fim, texto)] a partir das palavras com tempo que o Whisper ouviu: quebra a cada `maximo` palavras, numa
    pausa maior que `pausa` ou depois de uma pontuação."""
    legendas, grupo = [], []
    for inicio, fim, palavra in palavras:
        if grupo and (len(grupo) >= maximo or inicio - grupo[-1][1] > pausa or grupo[-1][2][-1:] in ".!?"):
            legendas.append((grupo[0][0], grupo[-1][1], " ".join(p for _, _, p in grupo)))
            grupo = []
        grupo.append((inicio, fim, palavra.strip()))
    if grupo:
        legendas.append((grupo[0][0], grupo[-1][1], " ".join(p for _, _, p in grupo)))
    return [(a, max(b, a + 0.6), t) for a, b, t in legendas if t]


class Legendas:
    """Desenha a legenda do momento: embaixo, numa faixa, ou grande no meio (estilo Reels)."""

    TAMANHOS = {"pequena": 0.038, "media": 0.046, "grande": 0.056}

    def __init__(self, estilo: dict, tamanho, cores: dict):
        self.posicao = estilo["legendas"]
        self.largura, self.altura = tamanho
        base = min(tamanho) * self.TAMANHOS[estilo["tamanho_legenda"]] * (1.35 if self.posicao == "centro" else 1)
        _, arquivo_titulo, arquivo_texto = estilo_video.LETRAS[estilo["letra"]]
        self.letra = fonte(arquivo_titulo if self.posicao == "centro" else arquivo_texto, int(base))
        self.cores = cores
        self._cache = {}

    @property
    def ligadas(self) -> bool:
        return self.posicao != "nenhuma"

    def zona(self) -> int:
        """A altura reservada embaixo para a legenda (os textos da cena ficam acima)."""
        return int(self.altura * 0.17) if self.posicao == "embaixo" else 0

    def _imagem(self, texto: str) -> Image.Image:
        if texto in self._cache:
            return self._cache[texto]
        largura_maxima = self.largura * (0.82 if self.posicao == "embaixo" else 0.78)
        if self.posicao == "centro":
            bloco = Bloco(texto.upper(), self.letra, rgb(self.cores["destaque"]), largura_maxima, "centro", sombra=True,
                          maximo_de_linhas=3)
            imagem = bloco.imagem()
        else:
            bloco = Bloco(texto, self.letra, rgb(self.cores["texto"]), largura_maxima, "centro", sombra=False, maximo_de_linhas=2)
            texto_img = bloco.imagem()
            folga = int(self.letra.size * 0.45)
            linhas = [bloco.letra.getlength(linha) for linha in bloco.linhas] or [0]
            largura_util = int(max(linhas)) + 2 * folga
            imagem = Image.new("RGBA", (largura_util, texto_img.height + folga), (0, 0, 0, 0))
            ImageDraw.Draw(imagem).rounded_rectangle((0, 0, imagem.width - 1, imagem.height - 1), radius=folga,
                                                     fill=rgb(self.cores["fundo"], 200))
            imagem.alpha_composite(texto_img, ((imagem.width - texto_img.width) // 2, folga // 2))
        self._cache[texto] = imagem
        return imagem

    def desenhar(self, quadro: Image.Image, legendas: list, momento: float):
        if not self.ligadas:
            return
        for inicio, fim, texto in legendas:
            if inicio <= momento < fim:
                imagem = self._imagem(texto)
                aparecer = _suave((momento - inicio) / 0.15)
                x = (self.largura - imagem.width) // 2
                if self.posicao == "centro":
                    y = int(self.altura * 0.5 - imagem.height / 2 + (1 - _salto(min(1, (momento - inicio) / 0.25))) * 20)
                else:
                    y = self.altura - int(self.altura * 0.06) - imagem.height
                quadro.alpha_composite(_com_alfa(imagem, aparecer), (x, y))
                return


# ------------------------------------------------------------------ a narração e as ilustrações
def narrar(texto: str, voz: str) -> str:
    """O áudio da narração (mp3) na voz escolhida; a voz do Windows se a online falhar. O arquivo fica com quem pediu."""
    import asyncio
    import tempfile

    import edge_tts

    from voice import PERFIS_DE_VOZ, gerar_arquivo_de_fala

    import time
    import unicodedata

    perfil = PERFIS_DE_VOZ.get(voz) or PERFIS_DE_VOZ["thalita"]
    texto = unicodedata.normalize("NFKC", texto)  # "CO₂" -> "CO2"
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as arquivo:
        caminho = arquivo.name
    # A voz online às vezes devolve "No audio was received" com vários pedidos juntos: tenta de novo antes de trocar de
    # voz (uma cena na voz do Windows no meio das outras soa estranho; medido em out/2026).
    for tentativa in range(3):
        try:
            asyncio.run(edge_tts.Communicate(texto, perfil["voz"], rate=perfil["velocidade"], pitch=perfil["tom"]).save(caminho))
            if os.path.getsize(caminho) > 0:
                return caminho
        except Exception as exc:
            logger.info("Narração online, tentativa %d: %s", tentativa + 1, str(exc)[:120])
        time.sleep(1.0 + tentativa)
    logger.warning("Narração online falhou três vezes; usando a voz do Windows nesta cena.")
    os.remove(caminho)
    return gerar_arquivo_de_fala(texto)


def ler_audio(caminho: str) -> np.ndarray:
    """O áudio em estéreo float32 a 44,1 kHz, forma (2, amostras)."""
    import av

    partes = []
    with av.open(caminho) as entrada:
        if not entrada.streams.audio:
            return np.zeros((2, 0), dtype=np.float32)
        conversor = av.AudioResampler(format="fltp", layout="stereo", rate=TAXA)
        for quadro in entrada.decode(audio=0):
            for convertido in conversor.resample(quadro):
                partes.append(convertido.to_ndarray())
        for convertido in conversor.resample(None):
            partes.append(convertido.to_ndarray())
    return np.concatenate(partes, axis=1).astype(np.float32) if partes else np.zeros((2, 0), dtype=np.float32)


def svg_no_tamanho(svg: str, largura: int, altura: int, cobrir: bool) -> str:
    """O SVG com o tamanho em pixels e o jeito de caber (cobrir corta as sobras; senão cabe inteiro)."""
    fim = svg.index(">")
    cabeca = re.sub(r"""\s(?:width|height|preserveAspectRatio)\s*=\s*(?:"[^"]*"|'[^']*')""", "", svg[:fim])
    proporcao = "xMidYMid slice" if cobrir else "xMidYMid meet"
    return cabeca + f' width="{largura}" height="{altura}" preserveAspectRatio="{proporcao}"' + svg[fim:]


def rasterizar_svgs(itens: list) -> list:
    """[(svg, largura, altura, cobrir)] -> [Image RGBA] pelo Chromium do Playwright, sem janela (fiel a degradês e filtros).
    Com `cobrir`, a ilustração preenche a área inteira (corta as sobras); senão, cabe inteira, com transparência em volta."""
    if not itens:
        return []
    import io

    from playwright.sync_api import sync_playwright

    imagens = []
    with sync_playwright() as playwright:
        navegador = playwright.chromium.launch(headless=True)
        try:
            pagina = navegador.new_page()
            # Nada de rede: a ilustração já vem limpa, mas o navegador não busca nada mesmo que viesse.
            pagina.route("**/*", lambda rota: rota.abort() if not rota.request.url.startswith("data:") else rota.continue_())
            for svg, largura, altura, cobrir in itens:
                pagina.set_viewport_size({"width": int(largura), "height": int(altura)})
                svg = svg_no_tamanho(svg, int(largura), int(altura), cobrir)
                pagina.set_content(f'<html><body style="margin:0;background:transparent;overflow:hidden">{svg}</body></html>')
                imagens.append(Image.open(io.BytesIO(pagina.screenshot(omit_background=True))).convert("RGBA"))
        finally:
            navegador.close()
    return imagens


# ------------------------------------------------------------------ as cenas
class Cena:
    """Uma cena pronta para desenhar: o fundo, a ilustração e onde ela fica, os textos com a hora de entrar, as legendas e
    a narração."""

    def __init__(self, duracao, fundo, blocos, ilustracao=None, caixa=None, legendas=(), audio=None, escurecer=False):
        self.duracao, self.fundo, self.blocos = duracao, fundo, blocos
        self.ilustracao, self.caixa, self.legendas, self.audio, self.escurecer = ilustracao, caixa, list(legendas), audio, escurecer


class Montador:
    """Monta e grava o vídeo a partir do roteiro e do estilo. `narrar(texto, voz)` e `rasterizar(itens)` são trocáveis nos
    testes (sem rede e sem navegador)."""

    def __init__(self, estilo: dict, narrar_com=None, rasterizar=None, formato: str = None):
        self.estilo = dict(estilo)
        if formato:
            self.estilo["formato"] = formato
        self.tamanho = TAMANHOS[self.estilo["formato"]]
        self.cores = estilo_video.paleta(self.estilo)
        self.narrar = narrar_com or narrar
        self.rasterizar = rasterizar or rasterizar_svgs
        self.legendas = Legendas(self.estilo, self.tamanho, self.cores)
        ritmo = self.estilo["ritmo"]
        self.transicao = 0.0 if self.estilo["transicao"] == "corte" else DURACAO_DA_TRANSICAO[ritmo]
        self.entrada, self.atraso = DURACAO_DA_ENTRADA[ritmo], ATRASO_ENTRE_TEXTOS[ritmo]
        _, self.arquivo_titulo, self.arquivo_texto = estilo_video.LETRAS[self.estilo["letra"]]
        lado = min(self.tamanho)
        self.margem = int(lado * 0.07)
        self.letra_titulo = fonte(self.arquivo_titulo, int(lado * 0.082))
        self.letra_texto = fonte(self.arquivo_texto, int(lado * 0.046))
        self.letra_item = fonte(self.arquivo_texto, int(lado * 0.043))
        self.letra_capa = fonte(self.arquivo_titulo, int(lado * 0.11))
        self._sombra = None

    # -------------------------------------------------- planejamento
    def _layout(self, cena: dict) -> str:
        layout = cena.get("layout")
        if layout in LAYOUTS:
            return "lista" if layout in ("lado", "centro", "tela_cheia") and not cena.get("ilustracao") else layout
        if cena.get("ilustracao"):
            return "lado"
        return "lista" if cena.get("itens") else "titulo"

    def _areas(self, layout: str):
        """(caixa da ilustração, caixa dos textos) para o layout, como (x, y, largura, altura)."""
        largura, altura = self.tamanho
        m, rodape = self.margem, self.legendas.zona()
        util = altura - rodape
        em_pe = altura > largura * 1.2
        if layout == "tela_cheia":
            return (0, 0, largura, altura), (m, int(util * 0.52), largura - 2 * m, int(util * 0.46))
        if layout == "lado":
            if em_pe:
                return (m, m, largura - 2 * m, int(util * 0.48)), (m, int(util * 0.52), largura - 2 * m, int(util * 0.46))
            meio = int(largura * 0.5)
            return (meio, m, largura - meio - m, util - 2 * m), (m, m, meio - int(1.5 * m), util - 2 * m)
        if layout == "centro":
            return (m, m, largura - 2 * m, int(util * 0.56)), (m, int(util * 0.6), largura - 2 * m, int(util * 0.38))
        return None, (m, m, largura - 2 * m, util - 2 * m)

    def _blocos(self, cena: dict, layout: str, caixa_texto) -> list:
        """[(Bloco, x, y)] dos textos da cena, empilhados na caixa (no meio dela, na vertical)."""
        x, y, largura, altura = caixa_texto
        centro = layout in ("centro", "titulo")
        alinhar = "centro" if centro else "esquerda"
        destaque, texto = rgb(self.cores["destaque"]), rgb(self.cores["texto"])
        letra_titulo = self.letra_capa if layout == "titulo" else self.letra_titulo
        candidatos = []
        if cena.get("titulo"):
            candidatos.append(Bloco(cena["titulo"], letra_titulo, destaque if layout != "titulo" else texto, largura, alinhar,
                                    maximo_de_linhas=3))
        if cena.get("texto"):
            candidatos.append(Bloco(cena["texto"], self.letra_texto, destaque if layout == "titulo" else texto, largura,
                                    alinhar, maximo_de_linhas=4))
        for item in (cena.get("itens") or [])[:5]:
            candidatos.append(Bloco(item, self.letra_item, texto, largura, "esquerda", marcador=destaque, maximo_de_linhas=2))
        espaco = int(self.letra_texto.size * 0.5)
        total = sum(b.tamanho[1] for b in candidatos) + espaco * max(0, len(candidatos) - 1)
        topo = y + max(0, (altura - total) // 2) if layout != "tela_cheia" else y + max(0, altura - total)
        blocos = []
        for bloco in candidatos:
            bx = x + (largura - bloco.tamanho[0]) // 2 if alinhar == "centro" and not bloco.marcador else x
            blocos.append((bloco, bx - bloco.margem, topo))
            topo += bloco.tamanho[1] + espaco
        return blocos

    def preparar(self, roteiro: dict, ao_progredir=None) -> list:
        """As cenas prontas (com a narração e as ilustrações), na ordem, com a abertura e a assinatura do estilo."""
        cenas = [c for c in (roteiro.get("cenas") or []) if isinstance(c, dict)][:MAXIMO_DE_CENAS]
        if not cenas:
            raise ErroDoVideo("o roteiro veio sem cenas")
        voz = self.estilo["narracao"]

        def narrar_cena(cena):
            if voz == "nenhuma" or not (cena.get("narracao") or "").strip():
                return None
            caminho = self.narrar(cena["narracao"].strip(), voz)
            try:
                return ler_audio(caminho)
            finally:
                try:
                    os.remove(caminho)
                except OSError:
                    pass

        if ao_progredir and voz != "nenhuma":
            ao_progredir("narrando")
        # Uma voz online por cena leva de 3 a 7 s: três de cada vez (com mais, ela recusa mais vezes).
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=NARRACOES_AO_MESMO_TEMPO) as narradores:
            audios = list(narradores.map(narrar_cena, cenas))

        layouts = [self._layout(c) for c in cenas]
        pedidos, indices = [], []
        for numero, (cena, layout) in enumerate(zip(cenas, layouts)):
            caixa, _ = self._areas(layout)
            if caixa and cena.get("ilustracao"):
                folga = 1.0 if self.estilo["movimento"] == "parado" else 1.1
                pedidos.append((cena["ilustracao"], caixa[2] * folga, caixa[3] * folga, layout == "tela_cheia"))
                indices.append(numero)
        if pedidos and ao_progredir:
            ao_progredir("desenhando as ilustrações")
        ilustracoes = {}
        try:
            for numero, imagem in zip(indices, self.rasterizar(pedidos)):
                ilustracoes[numero] = imagem
        except Exception as exc:  # sem o navegador, o vídeo sai com os textos
            logger.warning("As ilustrações do vídeo não foram desenhadas: %s", exc)

        prontas = []
        if self.estilo.get("abertura") and roteiro.get("titulo"):
            prontas.append(self._capa(roteiro["titulo"], roteiro.get("descricao", "")[:90], DURACAO_DA_ABERTURA))
        for numero, (cena, layout, audio) in enumerate(zip(cenas, layouts, audios)):
            prontas.append(self._cena(cena, layout, audio, ilustracoes.get(numero)))
        if self.estilo.get("assinatura"):
            prontas.append(self._capa(self.estilo["assinatura"], roteiro.get("titulo", ""), DURACAO_DA_ASSINATURA))
        return prontas

    def _duracao(self, cena: dict, audio) -> float:
        palavras = len(" ".join([cena.get("titulo") or "", cena.get("texto") or "", *(cena.get("itens") or [])]).split())
        leitura = palavras / 3.0 + 1.2
        base = DURACAO_BASE[self.estilo["ritmo"]]
        pedida = cena.get("duracao") if isinstance(cena.get("duracao"), (int, float)) else 0
        falada = audio.shape[1] / TAXA + ATRASO_DA_NARRACAO + 0.5 if audio is not None and audio.size else 0
        return min(DURACAO_MAXIMA_DA_CENA, max(2.0, base * 0.8, leitura, min(pedida, 12), falada))

    def _cena(self, cena: dict, layout: str, audio, ilustracao) -> Cena:
        duracao = self._duracao(cena, audio)
        cor = cena.get("fundo") if isinstance(cena.get("fundo"), str) and estilo_video.RE_COR.match(cena["fundo"]) else None
        fundo = _degrade(self.tamanho, cor or self.cores["fundo"], estilo_video.misturar(cor, self.cores["fundo2"], 0.6)
                         if cor else self.cores["fundo2"])
        if ilustracao is None and layout in ("lado", "centro", "tela_cheia"):  # a ilustração não saiu: só os textos
            layout = "lista" if cena.get("itens") else "titulo"
        caixa, caixa_texto = self._areas(layout)
        blocos = self._blocos(cena, layout, caixa_texto)
        narracao = (cena.get("narracao") or "").strip()
        legendas = []
        if narracao:
            if audio is not None and audio.size:
                legendas = legendas_no_tempo(narracao, ATRASO_DA_NARRACAO, audio.shape[1] / TAXA)
            else:  # sem narração, a frase fica como legenda no tempo da cena
                legendas = legendas_no_tempo(narracao, ATRASO_DA_NARRACAO, max(1.0, duracao - ATRASO_DA_NARRACAO - 0.4))
        return Cena(duracao, fundo, blocos, ilustracao, caixa if ilustracao is not None else None, legendas, audio,
                    escurecer=layout == "tela_cheia" and ilustracao is not None)

    def _capa(self, titulo: str, subtitulo: str, duracao: float) -> Cena:
        _, caixa_texto = self._areas("titulo")
        fundo = _degrade(self.tamanho, self.cores["fundo"], self.cores["fundo2"])
        return Cena(duracao, fundo, self._blocos({"titulo": titulo, "texto": subtitulo}, "titulo", caixa_texto))

    # -------------------------------------------------- desenho
    def _ilustracao(self, cena: Cena, momento: float) -> tuple:
        """A ilustração no quadro com o movimento do estilo: (imagem, posição)."""
        imagem, (x, y, largura, altura) = cena.ilustracao, cena.caixa
        movimento = self.estilo["movimento"]
        progresso = momento / max(cena.duracao, 0.1)
        if movimento == "parado":
            return imagem, (x + (largura - imagem.width) // 2, y + (altura - imagem.height) // 2)
        # A imagem veio 10% maior: o recorte anda (passear) ou fecha (aproximar) dentro dela.
        escala = 1 / 1.1 * (1 - 0.08 * progresso) if movimento == "aproximar" else 1 / 1.1
        janela_l, janela_a = imagem.width * escala, imagem.height * escala
        cx = imagem.width / 2 + ((progresso - 0.5) * 0.08 * imagem.width if movimento == "passear" else 0)
        cy = imagem.height / 2
        recorte = (cx - janela_l / 2, cy - janela_a / 2, cx + janela_l / 2, cy + janela_a / 2)
        destino = (int(imagem.width / 1.1), int(imagem.height / 1.1))
        pedaco = imagem.resize(destino, Image.BILINEAR, box=recorte)
        return pedaco, (x + (largura - pedaco.width) // 2, y + (altura - pedaco.height) // 2)

    def _textos(self, quadro: Image.Image, cena: Cena, momento: float):
        entrada = self.estilo["entrada"]
        for ordem, (bloco, x, y) in enumerate(cena.blocos):
            inicio = 0.15 + self.transicao * 0.6 + ordem * self.atraso
            if momento < inicio:
                continue
            decorrido = momento - inicio
            if entrada == "digitar":
                duracao = max(self.entrada, bloco.letras * 0.028)
                quadro.alpha_composite(bloco.imagem(letras=int(bloco.letras * min(1, decorrido / duracao))), (x, y))
                continue
            if entrada == "palavra":
                duracao = max(self.entrada, bloco.palavras * 0.16)
                quadro.alpha_composite(bloco.imagem(palavras=math.ceil(bloco.palavras * min(1, decorrido / duracao))), (x, y))
                continue
            p = decorrido / self.entrada
            imagem = bloco.imagem()
            if entrada == "pop":
                escala = 0.82 + 0.18 * _salto(p)
                if escala != 1:
                    novo = (max(1, int(imagem.width * escala)), max(1, int(imagem.height * escala)))
                    deslocada = imagem.resize(novo, Image.BILINEAR)
                    x2, y2 = x + (imagem.width - novo[0]) // 2, y + (imagem.height - novo[1]) // 2
                    quadro.alpha_composite(_com_alfa(deslocada, min(1, p * 2.5)), (x2, y2))
                    continue
                quadro.alpha_composite(imagem, (x, y))
                continue
            andar = 1 - _saida_cubica(p)
            dx = -int(andar * self.tamanho[0] * 0.08) if entrada == "deslizar" else 0
            dy = int(andar * self.tamanho[1] * 0.04) if entrada == "surgir" else 0
            quadro.alpha_composite(_com_alfa(imagem, _saida_cubica(p)), (max(0, x + dx), y + dy))

    def quadro(self, cena: Cena, momento: float) -> Image.Image:
        """A cena no momento dado (em segundos desde o começo dela)."""
        quadro = cena.fundo.copy()
        if cena.ilustracao is not None:
            imagem, posicao = self._ilustracao(cena, momento)
            quadro.alpha_composite(imagem, (max(-imagem.width, posicao[0]), max(-imagem.height, posicao[1])))
            if cena.escurecer:  # tela cheia: escurece embaixo para o texto aparecer
                if self._sombra is None:
                    self._sombra = self._sombra_de_baixo()
                quadro.alpha_composite(self._sombra)
        self._textos(quadro, cena, momento)
        self.legendas.desenhar(quadro, cena.legendas, momento)
        return quadro

    def _sombra_de_baixo(self) -> Image.Image:
        largura, altura = self.tamanho
        alfa = (np.clip((np.linspace(0, 1, altura) - 0.35) / 0.65, 0, 1) ** 1.3 * 215).astype(np.uint8)
        mascara = Image.fromarray(np.repeat(alfa[:, None], largura, axis=1), "L")
        sombra = Image.new("RGBA", self.tamanho, rgb(self.cores["fundo"]) + (0,))
        sombra.putalpha(mascara)
        return sombra

    def transicao_entre(self, antes: Image.Image, depois: Image.Image, p: float) -> Image.Image:
        tipo, p = self.estilo["transicao"], _suave(p)
        largura, altura = self.tamanho
        if tipo == "dissolver":
            return Image.blend(antes, depois, p)
        if tipo == "deslizar":
            quadro = Image.new("RGBA", self.tamanho)
            deslocamento = int(p * largura)
            quadro.paste(antes, (-deslocamento, 0))
            quadro.paste(depois, (largura - deslocamento, 0))
            return quadro
        if tipo == "zoom":
            escala = 1.25 - 0.25 * p
            novo = (int(largura * escala), int(altura * escala))
            perto = depois.resize(novo, Image.BILINEAR).crop(((novo[0] - largura) // 2, (novo[1] - altura) // 2,
                                                              (novo[0] - largura) // 2 + largura, (novo[1] - altura) // 2 + altura))
            return Image.blend(antes, perto, p)
        if tipo == "cortina":
            quadro = antes.copy()
            borda = int(p * largura)
            if borda > 0:
                quadro.paste(depois.crop((0, 0, borda, altura)), (0, 0))
            return quadro
        return depois

    # -------------------------------------------------- gravação
    def gravar(self, cenas: list, destino: str, ao_progredir=None) -> dict:
        """Grava as cenas em MP4 (H.264 + AAC). Devolve a duração e o número de quadros."""
        inicios, momento = [], 0.0
        for cena in cenas:
            inicios.append(momento)
            momento += cena.duracao
        total = momento
        audio = np.zeros((2, int(total * TAXA) + TAXA), dtype=np.float32)
        for inicio, cena in zip(inicios, cenas):
            if cena.audio is not None and cena.audio.size:
                comeco = int((inicio + ATRASO_DA_NARRACAO) * TAXA)
                trecho = cena.audio[:, :audio.shape[1] - comeco]
                audio[:, comeco:comeco + trecho.shape[1]] += trecho
        quadros = int(math.ceil(total * FPS))

        def quadro_no(indice: int) -> Image.Image:
            tempo = indice / FPS
            atual = max(i for i, inicio in enumerate(inicios) if inicio <= tempo + 1e-9)
            local = tempo - inicios[atual]
            imagem = self.quadro(cenas[atual], local)
            if atual > 0 and self.transicao and local < self.transicao:
                anterior = self.quadro(cenas[atual - 1], cenas[atual - 1].duracao + local)
                imagem = self.transicao_entre(anterior, imagem, local / self.transicao)
            return imagem

        gravar_mp4(destino, self.tamanho, FPS, (quadro_no(i) for i in range(quadros)), audio[:, :int(total * TAXA)],
                   ao_progredir=ao_progredir, total=quadros)
        return {"duracao": round(total, 2), "quadros": quadros, "cenas": len(cenas)}


def gravar_mp4(destino: str, tamanho, fps, quadros, audio: np.ndarray = None, ao_progredir=None, total: int = 0):
    """Grava os quadros (imagens do Pillow) e o áudio (estéreo float32 a 44,1 kHz) num MP4, num arquivo temporário que só
    vira o destino no fim (um vídeo pela metade nunca fica com o nome final)."""
    import av

    temporario = destino + ".parcial.mp4"
    saida = av.open(temporario, "w")
    try:
        video = saida.add_stream("libx264", rate=fps)
        video.width, video.height = tamanho
        video.pix_fmt = "yuv420p"
        video.options = {"crf": "20", "preset": "veryfast", "movflags": "+faststart"}
        som = None
        if audio is not None and audio.size:
            som = saida.add_stream("aac", rate=TAXA)
            som.layout = "stereo"
        ultimo_aviso = -1
        for numero, imagem in enumerate(quadros):
            for pacote in video.encode(av.VideoFrame.from_image(imagem.convert("RGB"))):
                saida.mux(pacote)
            if ao_progredir and total:
                porcento = int(100 * (numero + 1) / total) // 10 * 10
                if porcento != ultimo_aviso:
                    ultimo_aviso = porcento
                    ao_progredir(f"montando {porcento}%")
        for pacote in video.encode():
            saida.mux(pacote)
        if som is not None:
            np.clip(audio, -1, 1, out=audio)
            pts = 0
            for comeco in range(0, audio.shape[1], 1024):
                quadro = av.AudioFrame.from_ndarray(np.ascontiguousarray(audio[:, comeco:comeco + 1024]), format="fltp",
                                                    layout="stereo")
                quadro.sample_rate, quadro.pts = TAXA, pts
                pts += quadro.samples
                for pacote in som.encode(quadro):
                    saida.mux(pacote)
            for pacote in som.encode():
                saida.mux(pacote)
    except Exception:
        saida.close()
        os.remove(temporario)
        raise
    saida.close()
    os.replace(temporario, destino)


def renderizar(roteiro: dict, estilo: dict, destino: str, ao_progredir=None, narrar_com=None, rasterizar=None,
               formato: str = None) -> dict:
    """O roteiro do Claude vira o MP4 em `destino`, no estilo dado. Devolve {"duracao", "quadros", "cenas"}."""
    montador = Montador(estilo, narrar_com=narrar_com, rasterizar=rasterizar, formato=formato)
    cenas = montador.preparar(roteiro, ao_progredir)
    return montador.gravar(cenas, destino, ao_progredir)


# ------------------------------------------------------------------ os seus vídeos, no seu estilo
def _enquadrar(imagem: Image.Image, tamanho, modo: str) -> Image.Image:
    largura, altura = tamanho
    if modo == "preencher":
        escala = max(largura / imagem.width, altura / imagem.height)
        nova = imagem.resize((max(1, round(imagem.width * escala)), max(1, round(imagem.height * escala))), Image.BILINEAR)
        x, y = (nova.width - largura) // 2, (nova.height - altura) // 2
        return nova.crop((x, y, x + largura, y + altura))
    # encaixar: o vídeo inteiro, com ele mesmo desfocado e ampliado atrás
    fundo = _enquadrar(imagem.resize((max(1, imagem.width // 8), max(1, imagem.height // 8)), Image.BILINEAR),
                       (max(1, largura // 8), max(1, altura // 8)), "preencher")
    fundo = fundo.filter(ImageFilter.GaussianBlur(3)).resize(tamanho, Image.BILINEAR)
    fundo = Image.blend(fundo, Image.new("RGB", tamanho, (0, 0, 0)), 0.35)
    escala = min(largura / imagem.width, altura / imagem.height)
    frente = imagem.resize((max(1, round(imagem.width * escala)), max(1, round(imagem.height * escala))), Image.BILINEAR)
    fundo.paste(frente, ((largura - frente.width) // 2, (altura - frente.height) // 2))
    return fundo


def _tamanho_par(largura: int, altura: int, maior: int = 1920) -> tuple:
    escala = min(1.0, maior / max(largura, altura))
    return (max(2, int(largura * escala) // 2 * 2), max(2, int(altura * escala) // 2 * 2))


def audio_para_whisper(caminho: str) -> np.ndarray:
    """O som do vídeo em 16 kHz mono float32, o formato que o faster-whisper aceita."""
    import av

    partes = []
    with av.open(caminho) as entrada:
        if not entrada.streams.audio:
            return np.zeros(0, dtype=np.float32)
        conversor = av.AudioResampler(format="flt", layout="mono", rate=16000)
        for quadro in entrada.decode(audio=0):
            for convertido in conversor.resample(quadro):
                partes.append(convertido.to_ndarray().reshape(-1))
        for convertido in conversor.resample(None):
            partes.append(convertido.to_ndarray().reshape(-1))
    return np.concatenate(partes).astype(np.float32) if partes else np.zeros(0, dtype=np.float32)


def estilizar_video(entrada: str, estilo: dict, destino: str, titulo: str = "", transcrever=None, ao_progredir=None) -> dict:
    """O seu vídeo no seu estilo: enquadrado no formato (ou como está), com o título entrando no começo, as legendas do que
    foi falado (`transcrever(amostras_16k)` -> [(início, fim, palavra)], o Whisper do PC) e a assinatura no fim."""
    import av

    edicao = estilo.get("edicao") or {}
    with av.open(entrada) as fonte_video:
        fluxo = fonte_video.streams.video[0]
        fps = float(fluxo.average_rate or 30)
        fps = min(60.0, fps if fps > 1 else 30.0)
        largura, altura = fluxo.codec_context.width, fluxo.codec_context.height
        for quadro in fonte_video.decode(fluxo):
            if getattr(quadro, "rotation", 0) in (90, -90, 270, -270):
                largura, altura = altura, largura
            break
    if edicao.get("enquadrar", "original") == "original":
        tamanho = _tamanho_par(largura, altura)
    else:
        tamanho = TAMANHOS[estilo["formato"]]
    montador = Montador({**estilo, "abertura": False}, narrar_com=lambda *a: None, rasterizar=lambda itens: [])
    montador.tamanho = tamanho
    montador.legendas = Legendas(estilo, tamanho, montador.cores)
    legendas = []
    if edicao.get("legendas", True) and estilo["legendas"] != "nenhuma" and transcrever is not None:
        if ao_progredir:
            ao_progredir("ouvindo as falas")
        amostras = audio_para_whisper(entrada)
        if amostras.size:
            legendas = legendas_das_palavras(transcrever(amostras))
    titulo_blocos = []
    if titulo:
        lado = min(tamanho)
        bloco = Bloco(titulo, fonte(montador.arquivo_titulo, int(lado * 0.075)), rgb(montador.cores["destaque"]),
                      tamanho[0] - 2 * montador.margem, maximo_de_linhas=2)
        titulo_blocos = [(bloco, montador.margem, tamanho[1] - montador.legendas.zona() - bloco.tamanho[1] - montador.margem)]
    audio = ler_audio(entrada)
    final = montador._capa(estilo["assinatura"], titulo, DURACAO_DA_ASSINATURA) if estilo.get("assinatura") else None
    modo = edicao.get("enquadrar", "original")

    def quadros():
        imagem = Image.new("RGBA", tamanho, rgb(montador.cores["fundo"]) + (255,))
        with av.open(entrada) as fonte_video:
            numero = 0
            for quadro in fonte_video.decode(video=0):
                imagem = quadro.to_image()
                rotacao = getattr(quadro, "rotation", 0) or 0
                if rotacao:
                    imagem = imagem.rotate(rotacao, expand=True)
                imagem = (_enquadrar(imagem, tamanho, modo) if modo != "original" else imagem.resize(tamanho, Image.BILINEAR))
                imagem = imagem.convert("RGBA")
                momento = numero / fps
                if titulo_blocos and momento < 4.0:
                    sumir = 1 - _suave((momento - 3.4) / 0.6)
                    camada = Image.new("RGBA", tamanho, (0, 0, 0, 0))
                    montador._textos(camada, Cena(4.0, None, titulo_blocos), momento)
                    imagem.alpha_composite(_com_alfa(camada, sumir))
                montador.legendas.desenhar(imagem, legendas, momento)
                numero += 1
                yield imagem
            if final is not None:
                ultimo = imagem
                for indice in range(int(final.duracao * fps)):
                    local = indice / fps
                    capa = montador.quadro(final, local)
                    yield montador.transicao_entre(ultimo, capa, local / montador.transicao) if (
                        montador.transicao and local < montador.transicao) else capa

    if final is not None:
        audio = np.concatenate([audio, np.zeros((2, int(final.duracao * TAXA)), dtype=np.float32)], axis=1)
    with av.open(entrada) as fonte_video:
        total = fonte_video.streams.video[0].frames or 0
    gravar_mp4(destino, tamanho, round(fps), quadros(), audio, ao_progredir=ao_progredir, total=total)
    return {"legendas": len(legendas), "tamanho": tamanho}
