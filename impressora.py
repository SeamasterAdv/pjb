"""Impressão pelo Windows: impressoras instaladas, se estão ligadas, a fila e a impressão em si.

Tudo passa pela fila do Windows (spooler) com o pywin32. O PDF é desenhado página por página
(PyMuPDF -> imagem -> GDI), então não depende do leitor de PDF instalado nem abre janela nenhuma.
Word, Excel e PowerPoint viram PDF pelo próprio Office (COM) antes; texto e imagem viram PDF aqui.

Ligar e desligar: uma impressora no USB não liga nem desliga por comando do computador. Aqui só
se descobre se ela está ligada (o Windows só enxerga o aparelho USB quando ele está ligado).
"""
import html
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

EXTENSOES_PDF = {".pdf"}
EXTENSOES_IMAGEM = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
EXTENSOES_TEXTO = {".txt", ".md", ".log", ".csv"}
EXTENSOES_WORD = {".docx", ".doc", ".rtf", ".odt"}
EXTENSOES_EXCEL = {".xlsx", ".xls", ".xlsm", ".ods"}
EXTENSOES_POWERPOINT = {".pptx", ".ppt", ".odp"}
EXTENSOES_HTML = {".html", ".htm"}
EXTENSOES_IMPRIMIVEIS = (EXTENSOES_PDF | EXTENSOES_IMAGEM | EXTENSOES_TEXTO | EXTENSOES_WORD | EXTENSOES_EXCEL
                         | EXTENSOES_POWERPOINT | EXTENSOES_HTML)

# Bits de PRINTER_INFO_2.Status (winspool.h) que viram aviso.
PROBLEMAS_STATUS = {
    0x00000002: "está com erro",
    0x00000008: "está com papel enroscado",
    0x00000010: "está sem papel",
    0x00000040: "está com problema no papel",
    0x00000080: "está desconectada",
    0x00000800: "está com a bandeja de saída cheia",
    0x00001000: "não está disponível",
    0x00020000: "está com pouca tinta",
    0x00040000: "está sem tinta",
    0x00100000: "precisa de atenção",
    0x00400000: "está com a tampa aberta",
}
STATUS_PAUSADA = 0x00000001
STATUS_OFFLINE = 0x00000080
ATRIBUTO_OFFLINE = 0x00000400  # PRINTER_ATTRIBUTE_WORK_OFFLINE ("usar impressora offline")
PORTAS_VIRTUAIS = ("PORTPROMPT:", "NUL:", "FILE:", "XPS", "ONENOTE", "SHRFAX")

# JOB_INFO_1.Status (winspool.h), em ordem de prioridade para descrever.
STATUS_TRABALHO = (
    (0x0002, "com erro"),
    (0x0040, "sem papel"),
    (0x0020, "parado: a impressora está desligada ou desconectada"),
    (0x0400, "esperando você mexer na impressora"),
    (0x0001, "pausado"),
    (0x0004, "sendo cancelado"),
    (0x0010, "imprimindo"),
    (0x0008, "sendo enviado"),
)


class ErroImpressora(Exception):
    """Falha com uma mensagem pronta para a pessoa."""


@dataclass
class EstadoImpressora:
    nome: str
    ligada: bool | None  # None = não dá para saber (impressora de rede)
    virtual: bool = False
    pausada: bool = False
    trabalhos: int = 0
    problemas: list = field(default_factory=list)


@dataclass
class TrabalhoImpressao:
    id: int
    documento: str
    situacao: str
    paginas: int = 0


def nome_curto(nome: str) -> str:
    """'EPSON L395 Series' -> 'Epson L395'."""
    nome = (nome or "").replace(" Series", "").replace(" series", "").strip()
    partes = nome.split()
    if partes and partes[0].isupper() and len(partes[0]) > 2:
        partes[0] = partes[0].capitalize()
    return " ".join(partes) or "impressora"


def _texto_para_html(titulo: str, texto: str) -> str:
    """Parágrafos e listas ('- item', '• item', '1. item') de um texto simples."""
    e = html.escape
    partes = [f"<h1>{e(titulo)}</h1>"] if titulo else []
    lista = []

    def fechar_lista():
        if lista:
            partes.append("<ul>" + "".join(f"<li>{e(item)}</li>" for item in lista) + "</ul>")
            lista.clear()

    for linha in (texto or "").splitlines():
        limpa = linha.strip()
        if not limpa:
            fechar_lista()
            continue
        if limpa[:2] in ("- ", "* ", "• "):
            lista.append(limpa[2:].strip())
            continue
        fechar_lista()
        if limpa.startswith("#"):
            partes.append(f"<h2>{e(limpa.lstrip('#').strip())}</h2>")
        else:
            partes.append(f"<p>{e(limpa)}</p>")
    fechar_lista()
    return "".join(partes)


_CSS_TEXTO = """
body { font-family: sans-serif; font-size: 12pt; color: #111; line-height: 1.45; }
h1 { font-size: 20pt; margin-bottom: 8pt; }
h2 { font-size: 14pt; margin-top: 10pt; }
p { margin: 4pt 0; }
li { margin: 3pt 0; }
"""


def texto_para_pdf(titulo: str, texto: str, caminho: str) -> str:
    """Um texto (ditado, resposta, arquivo .txt/.md) num PDF A4 simples, pronto para imprimir."""
    import pymupdf

    historia = pymupdf.Story(html=_texto_para_html(titulo, texto), user_css=_CSS_TEXTO)
    pagina = pymupdf.paper_rect("a4")
    area = pagina + (56, 56, -56, -56)
    escritor = pymupdf.DocumentWriter(caminho)
    mais = True
    while mais:
        dispositivo = escritor.begin_page(pagina)
        mais, _ = historia.place(area)
        historia.draw(dispositivo)
        escritor.end_page()
    escritor.close()
    return caminho


def html_para_pdf(caminho_html: str, caminho: str) -> str:
    import pymupdf

    with open(caminho_html, encoding="utf-8", errors="ignore") as arquivo:
        historia = pymupdf.Story(html=arquivo.read())
    pagina = pymupdf.paper_rect("a4")
    escritor = pymupdf.DocumentWriter(caminho)
    mais = True
    while mais:
        dispositivo = escritor.begin_page(pagina)
        mais, _ = historia.place(pagina + (40, 40, -40, -40))
        historia.draw(dispositivo)
        escritor.end_page()
    escritor.close()
    return caminho


def contar_paginas(caminho_pdf: str) -> int:
    import pymupdf

    with pymupdf.open(caminho_pdf) as documento:
        return documento.page_count


def _office_para_pdf(caminho: str, destino: str) -> str:
    """Word, Excel ou PowerPoint exportam o próprio arquivo como PDF (precisa do Office instalado)."""
    import pythoncom
    import win32com.client

    extensao = os.path.splitext(caminho)[1].lower()
    caminho, destino = os.path.abspath(caminho), os.path.abspath(destino)
    # Sem CoUninitialize no fim: com objetos do Office ainda na memória, ele gera avisos de COM.
    pythoncom.CoInitialize()
    try:
        if extensao in EXTENSOES_WORD:
            app = win32com.client.DispatchEx("Word.Application")
            app.Visible = False
            app.DisplayAlerts = 0
            try:
                documento = app.Documents.Open(caminho, ReadOnly=True, AddToRecentFiles=False)
                documento.ExportAsFixedFormat(destino, 17)  # wdExportFormatPDF
                documento.Close(False)
            finally:
                app.Quit()
        elif extensao in EXTENSOES_EXCEL:
            app = win32com.client.DispatchEx("Excel.Application")
            app.Visible = False
            app.DisplayAlerts = False
            try:
                pasta = app.Workbooks.Open(caminho, ReadOnly=True)
                pasta.ExportAsFixedFormat(0, destino)  # xlTypePDF
                pasta.Close(False)
            finally:
                app.Quit()
        elif extensao in EXTENSOES_POWERPOINT:
            app = win32com.client.DispatchEx("PowerPoint.Application")
            try:
                apresentacao = app.Presentations.Open(caminho, ReadOnly=True, WithWindow=False)
                apresentacao.SaveAs(destino, 32)  # ppSaveAsPDF
                apresentacao.Close()
            finally:
                app.Quit()
        else:
            raise ErroImpressora(f"não sei converter arquivos {extensao}")
    except ErroImpressora:
        raise
    except Exception as exc:
        logger.warning("Office não converteu %s: %s", caminho, exc)
        raise ErroImpressora(f"o Office não conseguiu abrir {os.path.basename(caminho)}") from exc
    if not os.path.exists(destino):
        raise ErroImpressora(f"o Office não gerou o PDF de {os.path.basename(caminho)}")
    return destino


def arquivo_para_pdf(caminho: str, pasta_temporaria: str) -> str:
    """O PDF que vai para a impressora: o próprio arquivo, ou uma cópia convertida na pasta temporária."""
    extensao = os.path.splitext(caminho)[1].lower()
    if extensao in EXTENSOES_PDF:
        return caminho
    os.makedirs(pasta_temporaria, exist_ok=True)
    base = os.path.splitext(os.path.basename(caminho))[0]
    destino = os.path.join(pasta_temporaria, f"{base}_{int(time.time() * 1000)}.pdf")
    if extensao in EXTENSOES_IMAGEM:
        import pymupdf

        with pymupdf.open(caminho) as imagem:
            dados = imagem.convert_to_pdf()
        with open(destino, "wb") as arquivo:
            arquivo.write(dados)
        return destino
    if extensao in EXTENSOES_TEXTO:
        with open(caminho, encoding="utf-8", errors="ignore") as arquivo:
            texto = arquivo.read()
        return texto_para_pdf("", texto, destino)
    if extensao in EXTENSOES_HTML:
        return html_para_pdf(caminho, destino)
    if extensao in EXTENSOES_WORD | EXTENSOES_EXCEL | EXTENSOES_POWERPOINT:
        return _office_para_pdf(caminho, destino)
    raise ErroImpressora(f"não sei imprimir arquivos {extensao or 'sem extensão'}")


class ImpressoraWindows:
    """A impressora escolhida (`nome`) ou a padrão do Windows."""

    def __init__(self, nome: str = "", dpi: int = 300):
        self.nome_configurado = (nome or "").strip()
        self.dpi = dpi
        self.pasta_temporaria = os.path.join(tempfile.gettempdir(), "beatriz_impressao")

    # ------------------------------------------------------------ consultas
    def nome(self) -> str:
        """A configurada; senão a padrão do Windows, desde que seja uma impressora de verdade.

        O Windows 11 costuma trocar a padrão pela última usada ("Microsoft Print to PDF" depois de
        salvar um PDF), e aí cada impressão abriria a janela de "Salvar como". Nesse caso vale a
        primeira impressora física instalada.
        """
        import win32print

        if self.nome_configurado:
            return self.nome_configurado
        try:
            padrao = win32print.GetDefaultPrinter()
        except Exception:
            padrao = ""
        if padrao and not self._virtual(padrao):
            return padrao
        fisica = next((nome for nome in self.listar() if not self._virtual(nome)), "")
        if fisica or padrao:
            return fisica or padrao
        raise ErroImpressora("não há impressora instalada no Windows")

    @staticmethod
    def _virtual(nome: str) -> bool:
        """Impressora que grava arquivo (PDF, XPS, OneNote, fax) em vez de pôr no papel."""
        import win32print

        try:
            identificador = win32print.OpenPrinter(nome)
            try:
                porta = (win32print.GetPrinter(identificador, 2)["pPortName"] or "").upper()
            finally:
                win32print.ClosePrinter(identificador)
        except Exception:
            return False
        return not porta or porta.startswith(PORTAS_VIRTUAIS)

    def listar(self) -> list[str]:
        import win32print

        tipos = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        return [info[2] for info in win32print.EnumPrinters(tipos, None, 1)]

    def _abrir(self, nome: str = None):
        import win32print

        nome = nome or self.nome()
        try:
            return win32print.OpenPrinter(nome)
        except Exception as exc:
            raise ErroImpressora(f"não encontrei a impressora {nome} no Windows") from exc

    def estado(self) -> EstadoImpressora:
        import win32print

        nome = self.nome()
        identificador = self._abrir(nome)
        try:
            info = win32print.GetPrinter(identificador, 2)
        finally:
            win32print.ClosePrinter(identificador)
        status, atributos, porta = info["Status"], info["Attributes"], (info["pPortName"] or "").upper()
        virtual = porta.startswith(PORTAS_VIRTUAIS) or not porta
        if virtual:
            ligada = True
        elif porta.startswith("USB"):
            ligada = self._aparelho_usb_presente(porta)
        else:
            ligada = None if not (status & STATUS_OFFLINE or atributos & ATRIBUTO_OFFLINE) else False
        problemas = [texto for bit, texto in PROBLEMAS_STATUS.items() if status & bit and bit != STATUS_OFFLINE]
        return EstadoImpressora(nome=nome, ligada=ligada, virtual=virtual, pausada=bool(status & STATUS_PAUSADA),
                                trabalhos=info["cJobs"], problemas=problemas)

    @staticmethod
    def _aparelho_usb_presente(porta: str) -> bool | None:
        """O Windows só lista o aparelho USB (USBPRINT\\...&USB002) enquanto a impressora está ligada."""
        try:
            import pythoncom
            import win32com.client

            # Sem CoUninitialize: os objetos do WMI são soltos depois, e o COM fica iniciado na thread.
            pythoncom.CoInitialize()
            wmi = win32com.client.GetObject("winmgmts:")
            consulta = "SELECT PNPDeviceID FROM Win32_PnPEntity WHERE PNPDeviceID LIKE 'USBPRINT%'"
            ids = [str(aparelho.PNPDeviceID).upper() for aparelho in wmi.ExecQuery(consulta)]
            return any(identificador.endswith(porta) for identificador in ids)
        except Exception as exc:
            logger.debug("Não consegui consultar os aparelhos USB: %s", exc)
            return None

    def trabalhos(self) -> list[TrabalhoImpressao]:
        import win32print

        identificador = self._abrir()
        try:
            lista = win32print.EnumJobs(identificador, 0, 100, 1)
        finally:
            win32print.ClosePrinter(identificador)
        resultado = []
        for job in lista:
            situacao = next((texto for bit, texto in STATUS_TRABALHO if job["Status"] & bit), "na fila")
            resultado.append(TrabalhoImpressao(id=job["JobId"], documento=job["pDocument"] or "",
                                               situacao=situacao, paginas=job.get("TotalPages") or 0))
        return resultado

    # ---------------------------------------------------------------- fila
    def cancelar_todos(self) -> int:
        import win32print

        identificador = self._abrir()
        cancelados = 0
        try:
            for job in win32print.EnumJobs(identificador, 0, 100, 1):
                try:
                    win32print.SetJob(identificador, job["JobId"], 0, None, win32print.JOB_CONTROL_DELETE)
                    cancelados += 1
                except Exception as exc:
                    logger.warning("Não cancelei o trabalho %s: %s", job["JobId"], exc)
        finally:
            win32print.ClosePrinter(identificador)
        return cancelados

    def _controlar_fila(self, comando: int):
        import pywintypes
        import win32print

        nome = self.nome()
        try:
            identificador = win32print.OpenPrinter(nome, {"DesiredAccess": win32print.PRINTER_ACCESS_ADMINISTER})
        except pywintypes.error as exc:
            if exc.winerror == 5:  # acesso negado: pausar a fila é coisa de administrador
                raise ErroImpressora("o Windows só deixa pausar a fila com permissão de administrador") from exc
            raise ErroImpressora(f"não encontrei a impressora {nome} no Windows") from exc
        try:
            win32print.SetPrinter(identificador, 0, None, comando)
        finally:
            win32print.ClosePrinter(identificador)

    def pausar(self):
        import win32print

        self._controlar_fila(win32print.PRINTER_CONTROL_PAUSE)

    def retomar(self):
        import win32print

        self._controlar_fila(win32print.PRINTER_CONTROL_RESUME)

    # ------------------------------------------------------------ impressão
    def preparar(self, caminho: str) -> str:
        """O PDF do arquivo (convertido se preciso). Pode levar alguns segundos com o Office."""
        if not os.path.isfile(caminho):
            raise ErroImpressora(f"o arquivo {os.path.basename(caminho)} não existe mais")
        return arquivo_para_pdf(caminho, self.pasta_temporaria)

    def preparar_texto(self, titulo: str, texto: str) -> str:
        os.makedirs(self.pasta_temporaria, exist_ok=True)
        destino = os.path.join(self.pasta_temporaria, f"texto_{int(time.time() * 1000)}.pdf")
        return texto_para_pdf(titulo, texto, destino)

    def contar_paginas(self, caminho_pdf: str) -> int:
        return contar_paginas(caminho_pdf)

    def imprimir_pdf(self, caminho_pdf: str, copias: int = 1, paginas: list[int] | None = None,
                     colorido: bool = True, titulo: str = "", arquivo_saida: str | None = None) -> int:
        """Manda o PDF para a fila do Windows e devolve quantas folhas foram enviadas.

        `paginas` são índices a partir de 0 (None = todas). `arquivo_saida` só serve para impressoras
        que gravam arquivo (Microsoft Print to PDF), sem a janela de "Salvar como".
        """
        import pymupdf
        import win32con
        import win32gui
        import win32print
        import win32ui
        from PIL import Image, ImageWin

        nome = self.nome()
        identificador = self._abrir(nome)
        try:
            modo = win32print.GetPrinter(identificador, 2)["pDevMode"]
        finally:
            win32print.ClosePrinter(identificador)
        if modo is not None:
            modo.Copies = 1  # as cópias saem daqui, intercaladas, para não depender do driver
            modo.Color = 2 if colorido else 1  # DMCOLOR_COLOR / DMCOLOR_MONOCHROME (só tinta preta)

        dc = win32ui.CreateDCFromHandle(win32gui.CreateDC("WINSPOOL", nome, modo))
        enviadas = 0
        try:
            largura, altura = dc.GetDeviceCaps(win32con.HORZRES), dc.GetDeviceCaps(win32con.VERTRES)
            dpi = min(dc.GetDeviceCaps(win32con.LOGPIXELSX) or self.dpi, self.dpi)
            espaco = pymupdf.csRGB if colorido else pymupdf.csGRAY
            with pymupdf.open(caminho_pdf) as documento:
                indices = [i for i in (paginas if paginas is not None else range(documento.page_count))
                           if 0 <= i < documento.page_count]
                if not indices:
                    raise ErroImpressora("as páginas pedidas não existem no documento")
                dc.StartDoc(f"Beatriz - {titulo or os.path.basename(caminho_pdf)}", arquivo_saida)
                try:
                    for _ in range(max(1, copias)):
                        for indice in indices:
                            pagina = documento[indice]
                            pix = pagina.get_pixmap(dpi=dpi, colorspace=espaco, alpha=False)
                            imagem = Image.frombytes("RGB" if colorido else "L", (pix.width, pix.height), pix.samples)
                            if (imagem.width > imagem.height) != (largura > altura):
                                imagem = imagem.rotate(90, expand=True)  # página deitada em papel em pé
                            escala = min(largura / imagem.width, altura / imagem.height)
                            w, h = int(imagem.width * escala), int(imagem.height * escala)
                            x, y = (largura - w) // 2, (altura - h) // 2
                            dc.StartPage()
                            ImageWin.Dib(imagem).draw(dc.GetHandleOutput(), (x, y, x + w, y + h))
                            dc.EndPage()
                            enviadas += 1
                    dc.EndDoc()
                except Exception:
                    dc.AbortDoc()
                    raise
        finally:
            dc.DeleteDC()
        return enviadas
