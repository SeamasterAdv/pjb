"""Testes do scanner (WIA falso: a impressora do usuário liga no botão), do PDF montado a partir das folhas, do OCR local e
de como a biblioteca passa a ler fotos e PDFs escaneados (OCR falso, sem o Windows)."""
import os
import time

import pytest
from PIL import Image

import ocr_local
import scanner
from scanner import ErroDoScanner, ScannerWia, paginas_para_pdf
from test_biblioteca import biblioteca  # noqa: F401  (o fixture com a coleção em RAM)

import memory


# ------------------------------------------------------------------ WIA falso
class Propriedade:
    def __init__(self, identificador, valor=0, maximo=None):
        self.PropertyID, self.Value, self.SubTypeMax = identificador, valor, maximo


class Imagem:
    def __init__(self, tamanho=(120, 160)):
        self.tamanho = tamanho

    def SaveFile(self, caminho):
        if os.path.exists(caminho):
            raise OSError("o WIA não sobrescreve")  # como o de verdade
        Image.new("RGB", self.tamanho, "white").save(caminho)


class Item:
    def __init__(self, maximo_x=None, maximo_y=None, demora=0.0, erro=None):
        self.Properties = [Propriedade(i) for i in (scanner.INTENCAO, scanner.RESOLUCAO_X, scanner.RESOLUCAO_Y, scanner.INICIO_X, scanner.INICIO_Y)]
        self.Properties += [Propriedade(scanner.TAMANHO_X, maximo=maximo_x), Propriedade(scanner.TAMANHO_Y, maximo=maximo_y)]
        self.demora, self.erro, self.formato = demora, erro, None

    def Transfer(self, formato):
        self.formato = formato
        time.sleep(self.demora)
        if self.erro:
            raise self.erro
        return Imagem()

    def valor(self, identificador):
        return next(p.Value for p in self.Properties if p.PropertyID == identificador)


class Dispositivo:
    def __init__(self, item):
        self.Items = type("Itens", (), {"Item": staticmethod(lambda posicao: item)})()


class Info:
    def __init__(self, tipo, nome, item):
        self.Type, self.Properties, self._item = tipo, [type("P", (), {"Name": "Name", "Value": nome})()], item

    def Connect(self):
        return Dispositivo(self._item)


class Gerente:
    def __init__(self, *infos):
        self._infos = infos
        self.DeviceInfos = type("Infos", (), {"Count": len(infos), "Item": staticmethod(lambda i: infos[i - 1])})()


def scanner_com(item, tipo=scanner.TIPO_SCANNER, **kw):
    return ScannerWia(gerente=lambda: Gerente(Info(0, "Câmera do celular", Item()), Info(tipo, "EPSON L395 Series", item)), **kw)


# ------------------------------------------------------------------ scanner
def test_lista_so_scanners(tmp_path):
    assert scanner_com(Item()).dispositivos() == [(2, "EPSON L395 Series")]
    assert scanner_com(Item()).nome() == "EPSON L395 Series"
    assert ScannerWia(gerente=lambda: Gerente()).dispositivos() == []


def test_escaneia_a4_em_png_e_configura_o_aparelho(tmp_path):
    item = Item()
    caminho = scanner_com(item).escanear(str(tmp_path / "pagina.png"), dpi=300, cor="pb")
    assert os.path.isfile(caminho) and item.formato == scanner.FORMATO_PNG
    assert item.valor(scanner.INTENCAO) == 4 and item.valor(scanner.RESOLUCAO_X) == item.valor(scanner.RESOLUCAO_Y) == 300
    assert (item.valor(scanner.INICIO_X), item.valor(scanner.INICIO_Y)) == (0, 0)
    assert (item.valor(scanner.TAMANHO_X), item.valor(scanner.TAMANHO_Y)) == (int(8.27 * 300), int(11.69 * 300))  # A4 a 300 dpi


def test_o_vidro_menor_que_a4_limita_a_area(tmp_path):
    item = Item(maximo_x=1500, maximo_y=2000)
    scanner_com(item).escanear(str(tmp_path / "p.png"), dpi=200)
    assert (item.valor(scanner.TAMANHO_X), item.valor(scanner.TAMANHO_Y)) == (1500, 2000)


def test_cores_e_arquivo_existente_e_substituido(tmp_path):
    destino = tmp_path / "p.png"
    destino.write_bytes(b"velho")
    for cor, esperado in (("cor", 1), ("cinza", 2), ("pb", 4)):
        item = Item()
        scanner_com(item).escanear(str(destino), cor=cor)
        assert item.valor(scanner.INTENCAO) == esperado
    assert destino.stat().st_size > 100  # o PNG novo, não o "velho"


def test_sem_scanner_explica_que_a_impressora_liga_no_botao(tmp_path):
    with pytest.raises(ErroDoScanner, match="liga no botão"):
        ScannerWia(gerente=lambda: Gerente()).escanear(str(tmp_path / "p.png"))


def test_erros_do_wia_viram_explicacoes(tmp_path):
    ocupado = scanner_com(Item(erro=OSError("(-2145386475, 'erro', 0x80210015)")))
    with pytest.raises(ErroDoScanner, match="ocupado ou desligado"):
        ocupado.escanear(str(tmp_path / "p.png"))
    sem_papel = scanner_com(Item(erro=OSError("falhou 0x80210003")))
    with pytest.raises(ErroDoScanner, match="Não achei papel"):
        sem_papel.escanear(str(tmp_path / "p.png"))
    outro = scanner_com(Item(erro=ValueError("coisa estranha")))
    with pytest.raises(ErroDoScanner, match="O scanner deu erro: coisa estranha"):
        outro.escanear(str(tmp_path / "p.png"))


def test_scanner_que_trava_nao_prende_o_programa(tmp_path):
    lento = scanner_com(Item(demora=1.5), tempo_maximo=0.2)
    inicio = time.time()
    with pytest.raises(ErroDoScanner, match="demorou demais"):
        lento.escanear(str(tmp_path / "p.png"))
    assert time.time() - inicio < 1.0


def test_wia_indisponivel_no_windows(tmp_path):
    def quebra():
        raise OSError("classe não registrada")

    with pytest.raises(ErroDoScanner, match="não deixou usar o scanner"):
        ScannerWia(gerente=quebra).dispositivos()


# ------------------------------------------------------------------ PDF das folhas
def test_folhas_viram_um_pdf_a4_com_uma_pagina_cada(tmp_path):
    import fitz

    pngs = []
    for numero, tamanho in enumerate(((1654, 2339), (800, 600)), start=1):  # uma folha cheia e uma imagem deitada
        caminho = tmp_path / f"p{numero}.png"
        Image.new("RGB", tamanho, "white").save(caminho)
        pngs.append(str(caminho))
    pdf = paginas_para_pdf(pngs, str(tmp_path / "saida" / "escaneado.pdf"))
    with fitz.open(pdf) as documento:
        assert len(documento) == 2
        assert all(abs(p.rect.width - 595) < 2 and abs(p.rect.height - 842) < 2 for p in documento)
        assert all(p.get_images() for p in documento)
    with pytest.raises(ErroDoScanner):
        paginas_para_pdf([], str(tmp_path / "vazio.pdf"))


# ------------------------------------------------------------------ OCR local
class LinhaFalsa:
    def __init__(self, texto):
        self.texto, self.x, self.largura_letra, self.y, self.altura, self.direita = texto, 0, 10, 0, 10, 100


def ocr_falso(monkeypatch, texto="Texto lido por OCR"):
    chamadas = []

    def ler(imagem):
        chamadas.append(imagem.size)
        return [LinhaFalsa(linha) for linha in texto.split("\n")]

    import leitura_tela

    monkeypatch.setattr(leitura_tela, "ler_texto", ler)
    ocr_local._CACHE.clear()
    return chamadas


def test_imagem_e_lida_pelo_ocr_e_a_leitura_e_guardada(monkeypatch, tmp_path):
    chamadas = ocr_falso(monkeypatch, "CONTRATO\nValor R$ 1.500")
    foto = tmp_path / "foto.jpg"
    Image.new("RGB", (600, 400), "white").save(foto)
    assert ocr_local.texto_do_arquivo_de_imagem(str(foto)) == "CONTRATO\nValor R$ 1.500"
    assert ocr_local.texto_do_arquivo_de_imagem(str(foto)) == "CONTRATO\nValor R$ 1.500" and len(chamadas) == 1  # a segunda vem do cache


def test_imagem_enorme_e_reduzida_antes_do_ocr(monkeypatch):
    chamadas = ocr_falso(monkeypatch)
    ocr_local.texto_da_imagem(Image.new("RGB", (9000, 6000), "white"))
    assert max(chamadas[0]) <= ocr_local.LADO_MAXIMO


def test_ocr_indisponivel_devolve_vazio_sem_quebrar(monkeypatch):
    import leitura_tela

    def sem_ocr(imagem):
        raise leitura_tela.OcrIndisponivel("sem idioma")

    monkeypatch.setattr(leitura_tela, "ler_texto", sem_ocr)
    assert ocr_local.texto_da_imagem(Image.new("RGB", (100, 100), "white")) == ""


def _pdf_com_uma_pagina_em_branco(caminho):
    import fitz

    documento = fitz.open()
    documento.new_page().insert_text((72, 100), "Pagina com texto de verdade")
    documento.new_page()  # escaneada: sem camada de texto
    documento.new_page().insert_text((72, 100), "Outra pagina de texto")
    documento.save(str(caminho))
    documento.close()


def test_so_as_paginas_sem_texto_passam_por_ocr(monkeypatch, tmp_path):
    chamadas = ocr_falso(monkeypatch, "texto do escaneamento")
    pdf = tmp_path / "misto.pdf"
    _pdf_com_uma_pagina_em_branco(pdf)
    achados = ocr_local.paginas_sem_texto(str(pdf), ["texto", "", "texto"])
    assert achados == [(2, "texto do escaneamento")] and len(chamadas) == 1
    assert ocr_local.paginas_sem_texto(str(pdf), ["texto", "", "texto"]) == achados and len(chamadas) == 1  # cache
    assert ocr_local.paginas_sem_texto(str(pdf), ["a", "b", "c"]) == []  # tudo tem texto: nenhum OCR


def test_ocr_tem_limite_de_paginas(monkeypatch, tmp_path):
    import fitz

    chamadas = ocr_falso(monkeypatch)
    monkeypatch.setattr(ocr_local, "LIMITE_DE_PAGINAS", 3)
    documento = fitz.open()
    for _ in range(6):
        documento.new_page()
    pdf = tmp_path / "grande.pdf"
    documento.save(str(pdf))
    assert [n for n, _ in ocr_local.paginas_sem_texto(str(pdf), [""] * 6)] == [1, 2, 3] and len(chamadas) == 3


# ------------------------------------------------------------------ a biblioteca lê fotos e escaneamentos
def test_pdf_escaneado_vira_texto_pesquisavel_na_biblioteca(biblioteca, monkeypatch):  # noqa: F811
    fontes, _, colecao = biblioteca
    ocr_falso(monkeypatch, "Cláusula quinta: o inquilino pagará multa de três aluguéis na rescisão antecipada.")
    _pdf_com_uma_pagina_em_branco(fontes / "escaneado.pdf")
    unidades = memory._extrair_unidades(str(fontes / "escaneado.pdf"))
    assert [localizador for _, localizador in unidades] == ["página 1", "página 2", "página 3"]
    assert "inquilino" in unidades[1][0] and "texto de verdade" in unidades[0][0]  # a página 2 veio do OCR
    assert memory.indexar_pasta_documentos().startswith("1 fonte")
    resposta = memory.buscar_nos_documentos_modo("multa de três aluguéis na rescisão antecipada", modo="padrão")
    assert "escaneado.pdf" in resposta and "páginas 1 a 3" in resposta and "inquilino pagará multa" in resposta  # a página 2 veio do OCR


def test_foto_com_texto_entra_na_biblioteca_pelo_ocr(biblioteca, monkeypatch):  # noqa: F811
    fontes, _, _ = biblioteca
    ocr_falso(monkeypatch, "Recibo de pagamento número 4521 da mensalidade de outubro")
    Image.new("RGB", (800, 600), "white").save(fontes / "recibo.jpg")
    assert "Recibo de pagamento número 4521" in memory.extrair_texto_arquivo_otimizado(str(fontes / "recibo.jpg"))
    assert memory.indexar_pasta_documentos().startswith("1 fonte")
    assert "recibo.jpg" in memory.buscar_nos_documentos_modo("recibo da mensalidade de outubro 4521", modo="padrão")


def test_fonte_que_ficou_vazia_antes_do_ocr_e_tentada_de_novo_uma_vez(biblioteca, monkeypatch):  # noqa: F811
    import json

    fontes, _, _ = biblioteca
    Image.new("RGB", (300, 200), "white").save(fontes / "antiga.jpg")
    tentativas = []
    original = memory._extrair_unidades
    monkeypatch.setattr(memory, "_extrair_unidades", lambda caminho, limite=50000: tentativas.append(caminho) or [("", "documento")])
    memory.indexar_pasta_documentos()
    manifesto = json.load(open(memory.CAMINHO_MANIFESTO_MEMORIA, encoding="utf-8"))
    registro = next(iter(manifesto.values()))
    assert registro["vazio"] and registro["ocr"] == memory.VERSAO_OCR and len(tentativas) == 1
    memory.indexar_pasta_documentos()
    assert len(tentativas) == 1  # já passou pelo OCR atual e continua vazia: não repete a cada varredura
    del registro["ocr"]  # um manifesto de antes do OCR do Windows: a fonte "vazia" pode ter sido só a falta de OCR
    json.dump(manifesto, open(memory.CAMINHO_MANIFESTO_MEMORIA, "w", encoding="utf-8"))
    memory.indexar_pasta_documentos()
    assert len(tentativas) == 2
    monkeypatch.setattr(memory, "_extrair_unidades", original)
