"""OCR local para fotos e PDFs escaneados, com o reconhecimento de texto do Windows (o mesmo da leitura de tela).

A biblioteca (`memory.py`) lia imagens só pelo pytesseract (que não vem instalado) e PDFs só pela camada de texto, então
uma foto ou um escaneamento virava fonte "vazia". Aqui o texto sai por OCR, no PC, sem nuvem. Cada página leva ~0,2 a 0,5 s.
"""
import logging
import os
import threading
from collections import OrderedDict

logger = logging.getLogger(__name__)

LADO_MAXIMO = 4000  # o OCR do Windows aceita até 10000 px; fotos de celular de 12 MP ficam bem lidas em 4000
DPI_DA_PAGINA = 170  # página de PDF desenhada para o OCR: texto de 10 pt sai com ~24 px de altura
LIMITE_DE_PAGINAS = 80  # um PDF escaneado de 300 páginas não trava a indexação por minutos


# Ler a mesma página duas vezes custa tempo: contar as palavras da fonte e indexá-la passam pelo mesmo arquivo. A chave é o
# conteúdo "de fora" (nome, tamanho e data), que a cópia para a biblioteca preserva (shutil.copy2).
_CACHE: OrderedDict = OrderedDict()
_TRAVA = threading.Lock()


def _chave(caminho: str, extra=""):
    try:
        estado = os.stat(caminho)
        return (os.path.basename(caminho), estado.st_size, estado.st_mtime_ns, extra)
    except OSError:
        return None


def _guardado(chave, calcular):
    if chave is None:
        return calcular()
    with _TRAVA:
        if chave in _CACHE:
            _CACHE.move_to_end(chave)
            return _CACHE[chave]
    valor = calcular()
    with _TRAVA:
        _CACHE[chave] = valor
        while len(_CACHE) > 12:
            _CACHE.popitem(last=False)
    return valor


def texto_da_imagem(imagem) -> str:
    """O texto de uma imagem do Pillow ("" se não leu nada ou se o OCR do Windows não está disponível)."""
    from leitura_tela import OcrIndisponivel, ler_texto, montar_texto

    try:
        if max(imagem.size) > LADO_MAXIMO:
            imagem = imagem.copy()
            imagem.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
        return montar_texto(ler_texto(imagem.convert("RGB"))).strip()
    except OcrIndisponivel as exc:
        logger.warning("OCR do Windows indisponível: %s", exc)
        return ""
    except Exception as exc:
        logger.warning("OCR falhou: %s", exc)
        return ""


def texto_do_arquivo_de_imagem(caminho: str) -> str:
    """OCR de um arquivo de imagem (jpg, png, webp, bmp), já girado como o celular o gravou (EXIF)."""
    from PIL import Image, ImageOps

    def ler():
        try:
            with Image.open(caminho) as imagem:
                return texto_da_imagem(ImageOps.exif_transpose(imagem))
        except Exception as exc:
            logger.warning("Não consegui abrir a imagem %s: %s", os.path.basename(caminho), exc)
            return ""

    return _guardado(_chave(caminho, "imagem"), ler)


def texto_da_pagina_do_pdf(pagina, dpi: int = DPI_DA_PAGINA) -> str:
    """OCR de uma página do PyMuPDF (`fitz.Page`), desenhada em `dpi`."""
    from PIL import Image

    try:
        pixels = pagina.get_pixmap(dpi=dpi)
        imagem = Image.frombytes("RGB", (pixels.width, pixels.height), pixels.samples) if pixels.n == 3 else \
            Image.frombytes("RGBA", (pixels.width, pixels.height), pixels.samples).convert("RGB")
        return texto_da_imagem(imagem)
    except Exception as exc:
        logger.warning("OCR da página falhou: %s", exc)
        return ""


def paginas_sem_texto(caminho_pdf: str, textos: list[str]) -> list[tuple[int, str]]:
    """Faz OCR das páginas do PDF cujo texto veio vazio. `textos` é o que a camada de texto deu, página a página.
    Devolve [(número da página a partir de 1, texto do OCR)], no máximo LIMITE_DE_PAGINAS páginas."""
    vazias = [indice for indice, texto in enumerate(textos) if not (texto or "").strip()][:LIMITE_DE_PAGINAS]
    if not vazias:
        return []

    def ler():
        import fitz

        resultado = []
        with fitz.open(caminho_pdf) as documento:
            for indice in vazias:
                if indice < len(documento):
                    lido = texto_da_pagina_do_pdf(documento[indice])
                    if lido:
                        resultado.append((indice + 1, lido))
        return resultado

    return list(_guardado(_chave(caminho_pdf, tuple(vazias)), ler))
