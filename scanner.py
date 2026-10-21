"""Scanner físico (o da Epson L395 e outros) pelo WIA do Windows, e a montagem do PDF.

O scanner só aparece para o Windows com o aparelho ligado (a L395 liga no botão): sem ele, `dispositivos()` vem vazio e
o erro explica isso. Cada página é uma folha A4 no vidro. Nada sai do PC.

    from scanner import ScannerWia, paginas_para_pdf
    png = ScannerWia().escanear("pagina1.png", dpi=200, cor="cor")
    paginas_para_pdf(["pagina1.png", "pagina2.png"], "documento.pdf")
"""
import logging
import os
import threading

logger = logging.getLogger(__name__)

TIPO_SCANNER = 1  # WiaDeviceType: ScannerDeviceType
FORMATO_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"
# Propriedades do item do scanner (wiadef.h)
INTENCAO, RESOLUCAO_X, RESOLUCAO_Y, INICIO_X, INICIO_Y, TAMANHO_X, TAMANHO_Y = 6146, 6147, 6148, 6149, 6150, 6151, 6152
INTENCOES = {"cor": 1, "cinza": 2, "pb": 4}  # WIA_INTENT_IMAGE_TYPE_COLOR / GRAYSCALE / TEXT (preto e branco)
A4_EM_POLEGADAS = (8.27, 11.69)
TEMPO_MAXIMO = 120.0  # segundos para uma página (o vidro leva ~15 a 30 s a 200 dpi)


class ErroDoScanner(RuntimeError):
    """Mensagem pronta para a pessoa ("o scanner está desligado...")."""


def _gerente_wia():
    import win32com.client

    return win32com.client.Dispatch("WIA.DeviceManager")


def _propriedades(objeto) -> dict:
    return {p.Name: p.Value for p in objeto.Properties}


class ScannerWia:
    def __init__(self, gerente=None, tempo_maximo: float = TEMPO_MAXIMO):
        self._gerente = gerente  # função que devolve o WIA.DeviceManager (ou um falso nos testes)
        self.tempo_maximo = tempo_maximo

    def _abrir_gerente(self):
        try:
            return (self._gerente or _gerente_wia)()
        except Exception as exc:
            raise ErroDoScanner(f"O Windows não deixou usar o scanner (WIA): {exc}") from exc

    def dispositivos(self) -> list[tuple[int, str]]:
        """[(posição, nome)] dos scanners que o Windows enxerga agora."""
        infos = self._abrir_gerente().DeviceInfos
        achados = []
        for posicao in range(1, infos.Count + 1):
            info = infos.Item(posicao)
            if info.Type == TIPO_SCANNER:
                achados.append((posicao, str(_propriedades(info).get("Name", "scanner"))))
        return achados

    def nome(self) -> str:
        achados = self.dispositivos()
        return achados[0][1] if achados else ""

    def escanear(self, destino_png: str, dpi: int = 200, cor: str = "cor") -> str:
        """Escaneia uma folha A4 do vidro e grava o PNG. Devolve o caminho. Levanta ErroDoScanner com a explicação."""
        resultado, erro = {}, []

        def trabalhar():
            try:
                try:
                    import pythoncom

                    pythoncom.CoInitialize()  # o COM precisa ser iniciado em cada thread
                except ImportError:
                    pass
                resultado["caminho"] = self._escanear(destino_png, dpi, cor)
            except Exception as exc:
                erro.append(exc)

        linha = threading.Thread(target=trabalhar, name="scanner-wia", daemon=True)
        linha.start()
        linha.join(self.tempo_maximo)
        if linha.is_alive():
            raise ErroDoScanner("O scanner demorou demais e eu desisti. Veja se a folha está no vidro e tente de novo.")
        if erro:
            if isinstance(erro[0], ErroDoScanner):
                raise erro[0]
            logger.warning("Falha no scanner: %s", erro[0], exc_info=erro[0])
            raise ErroDoScanner(self._explicar(erro[0])) from erro[0]
        return resultado["caminho"]

    @staticmethod
    def _explicar(exc: Exception) -> str:
        texto = str(exc)
        if "0x80210015" in texto or "0x80210005" in texto or "0x80210006" in texto:
            return "O scanner está ocupado ou desligado. Ligue a impressora no botão e espere ela ficar pronta."
        if "0x80210003" in texto:
            return "Não achei papel no scanner. Ponha a folha no vidro, com o lado escrito para baixo."
        return f"O scanner deu erro: {texto[:160]}"

    def _escanear(self, destino_png: str, dpi: int, cor: str) -> str:
        achados = self.dispositivos()
        if not achados:
            raise ErroDoScanner("Não achei nenhum scanner. A impressora com o scanner está ligada? "
                                "Ela liga no botão, e só aparece para o Windows depois de pronta.")
        info = self._abrir_gerente().DeviceInfos.Item(achados[0][0])
        item = info.Connect().Items.Item(1)
        self._definir(item, INTENCAO, INTENCOES.get(cor, 1))
        for propriedade in (RESOLUCAO_X, RESOLUCAO_Y):
            self._definir(item, propriedade, dpi)
        self._definir(item, INICIO_X, 0)
        self._definir(item, INICIO_Y, 0)
        self._definir(item, TAMANHO_X, int(A4_EM_POLEGADAS[0] * dpi), limitar=True)
        self._definir(item, TAMANHO_Y, int(A4_EM_POLEGADAS[1] * dpi), limitar=True)
        imagem = item.Transfer(FORMATO_PNG)
        if os.path.exists(destino_png):
            os.remove(destino_png)  # o WIA se recusa a sobrescrever
        os.makedirs(os.path.dirname(os.path.abspath(destino_png)), exist_ok=True)
        imagem.SaveFile(destino_png)
        if not os.path.isfile(destino_png) or os.path.getsize(destino_png) == 0:
            raise ErroDoScanner("O scanner não devolveu a imagem. Tente de novo.")
        return destino_png

    @staticmethod
    def _definir(item, identificador: int, valor: int, limitar: bool = False):
        """Muda uma propriedade do item. Com `limitar`, respeita o máximo que o aparelho aceita (o vidro nem sempre é A4)."""
        for propriedade in item.Properties:
            if propriedade.PropertyID == identificador:
                if limitar:
                    maximo = getattr(propriedade, "SubTypeMax", 0) or 0
                    if maximo:
                        valor = min(valor, int(maximo))
                propriedade.Value = valor
                return


def paginas_para_pdf(paginas: list[str], destino_pdf: str) -> str:
    """Junta imagens (uma por página) num PDF A4, cada imagem ajustada à folha sem esticar."""
    import fitz

    if not paginas:
        raise ErroDoScanner("Não há nenhuma página para montar o PDF.")
    documento = fitz.open()
    largura, altura = fitz.paper_size("a4")
    for caminho in paginas:
        pagina = documento.new_page(width=largura, height=altura)
        pagina.insert_image(pagina.rect, filename=caminho, keep_proportion=True)
    os.makedirs(os.path.dirname(os.path.abspath(destino_pdf)), exist_ok=True)
    documento.save(destino_pdf, deflate=True, garbage=3)
    documento.close()
    return destino_pdf
