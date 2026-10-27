"""Apps da ArtCraft (apps_craft.py e agente_craft.py) com programas falsos: nenhum teste roda os apps de verdade (o conftest.py
proíbe) nem abre arquivos no PC."""
import json
import os
import subprocess

import pytest

import busca_arquivos
from agente_craft import AgenteCraft, RespostaCraft, ler_edicao_de_foto, ler_edicao_de_video, normalizar, texto_de_tempo
from apps_craft import APPS, ARTCRAFT, LAUNCHER, POR_CHAVE, AppsCraft, EdicaoDeFoto, EdicaoDeVideo, ErroCraft, app_citado


class Programas:
    """Imita os `*-cli.exe`: guarda cada comando e grava o arquivo de saída que o comando pede (o roteiro do FilmCraft é
    lido e guardado em `roteiros`)."""

    def __init__(self, paginas: int = 1, falhar: str = "", duracao: float = 60.0):
        self.comandos, self.iniciados, self.roteiros, self.tempos = [], [], [], []
        self.paginas, self.falhar, self.duracao = paginas, falhar, duracao

    def rodar(self, comando, tempo=None):
        self.comandos.append(comando)
        self.tempos.append(tempo)
        programa = os.path.basename(comando[0])
        acao = next(a for a in comando[1:] if not a.startswith("--"))
        if self.falhar and self.falhar in " ".join(comando):
            return subprocess.CompletedProcess(comando, 1, "", f"{programa[:-4]}: {acao}: arquivo estragado\n")
        if acao == "info":
            return subprocess.CompletedProcess(comando, 0, json.dumps({"pages": self.paginas}), "")
        if acao == "text":
            return subprocess.CompletedProcess(comando, 0, "página 1\fpágina 2", "")
        if acao == "probe":
            return subprocess.CompletedProcess(comando, 0, json.dumps({"duration": int(self.duracao * 254016000000)}), "")
        if programa == "filmcraft-cli.exe" and acao == "run":
            linhas = [json.loads(l) for l in open(comando[-1], encoding="utf-8").read().splitlines()]
            self.roteiros.append(linhas)
            exportar = linhas[-1]["params"]
            saida = exportar["path"]
            if exportar["format"] == "jpg":  # o FilmCraft numera os quadros
                saida = os.path.join(os.path.dirname(saida), "quadro000.jpg")
        elif programa == "lightcraft-cli.exe" and acao == "run":
            saida = next(a for a in comando if a.startswith("path="))[5:]
        else:
            saida = comando[comando.index("--out") + 1] if "--out" in comando else comando[-1]
        with open(saida, "w", encoding="utf-8") as arquivo:
            arquivo.write(acao)
        return subprocess.CompletedProcess(comando, 0, f"wrote {saida}", "")

    def iniciar(self, comando, pasta):
        self.iniciados.append(comando)

    def acoes(self):
        return [(os.path.basename(c[0]), c[1]) for c in self.comandos]


def instalar(raiz, *apps):
    """Põe executáveis vazios onde os instaladores poriam (só para serem achados)."""
    for app in apps:
        for relativo in (app.exe, app.cli):
            if relativo:
                caminho = os.path.join(raiz, relativo)
                os.makedirs(os.path.dirname(caminho), exist_ok=True)
                open(caminho, "wb").close()


def arquivo(pasta, nome, conteudo=b"x"):
    os.makedirs(pasta, exist_ok=True)
    caminho = os.path.join(pasta, nome)
    with open(caminho, "wb") as f:
        f.write(conteudo)
    return caminho


@pytest.fixture
def mundo(tmp_path):
    class Mundo:
        pass

    m = Mundo()
    m.programas = Programas()
    m.raiz = str(tmp_path / "Program Files")
    instalar(m.raiz, *APPS, ARTCRAFT, LAUNCHER)
    m.apps = AppsCraft(raizes=[m.raiz], rodar=m.programas.rodar, iniciar=m.programas.iniciar)
    m.pasta_docs = str(tmp_path / "documentos")
    m.saida = str(tmp_path / "gerados")
    m.recebidos, m.abertos, m.avisos = [], [], []
    m.indice = busca_arquivos.IndiceDeArquivos(raizes=lambda: [(m.pasta_docs, 3, "Documentos")])
    m.agente = AgenteCraft(apps=m.apps, recebidos=lambda texto: list(m.recebidos), arquivos=m.indice, pasta_saida=m.saida,
                           abrir_arquivo=m.abertos.append, relogio=lambda: 1_790_000_000,
                           avisar=lambda resposta, canal: m.avisos.append((resposta, canal)), em_segundo_plano=lambda f: f())
    m.diz = lambda frase, canal="voz": m.agente.responder(frase, canal)
    return m


# ------------------------------------------------------------------ apps_craft
@pytest.mark.parametrize("frase, chave", [
    ("abre o photo craft", "photocraft"), ("abre o foto craft", "photocraft"), ("abre o photocraft", "photocraft"),
    ("abre o pdf craft", "pdfcraft"), ("abre o p d f craft", "pdfcraft"), ("abre o word craft", "wordcraft"),
    ("liga o filme craft", "filmcraft"), ("abre o vetor craft", "vectorcraft"), ("abre o cad craft", "cadcraft"),
    ("abre o art craft", "artcraft"), ("abre o artcraft launcher", "launcher"), ("abre o launcher do art craft", "launcher"),
])
def test_app_citado_entende_o_nome_como_a_transcricao_escreve(frase, chave):
    assert app_citado(frase).chave == chave


@pytest.mark.parametrize("frase", ["abre o word", "abre o pdf da aula", "aumenta o som da tv", "abre o figma"])
def test_sem_craft_nao_e_um_app_da_artcraft(frase):
    assert app_citado(frase) is None


def test_acha_os_instalados_em_qualquer_raiz(tmp_path):
    programas, usuario = str(tmp_path / "pf"), str(tmp_path / "local")
    instalar(programas, POR_CHAVE["wordcraft"])
    instalar(usuario, ARTCRAFT)
    apps = AppsCraft(raizes=[programas, usuario])
    assert [a.chave for a in apps.instalados()] == ["wordcraft", "artcraft"]
    assert apps.linha_de_comando(POR_CHAVE["wordcraft"]).endswith(os.path.join("WordCraft", "wordcraft-cli.exe"))
    assert apps.linha_de_comando(ARTCRAFT) is None  # o ArtCraft não tem linha de comando


def test_abrir_um_app_que_nao_esta_instalado_explica(tmp_path):
    with pytest.raises(ErroCraft, match="não está instalado.*ArtCraft Launcher"):
        AppsCraft(raizes=[str(tmp_path)]).abrir(POR_CHAVE["filmcraft"])


def test_converte_word_em_pdf_pelo_wordcraft(mundo, tmp_path):
    docx = arquivo(mundo.pasta_docs, "Prova 2.docx")
    feitos = mundo.apps.converter(docx, ".pdf", mundo.saida)
    assert feitos == [os.path.join(mundo.saida, "Prova 2.pdf")] and os.path.isfile(feitos[0])
    assert mundo.programas.comandos[0][1:] == ["convert", docx, feitos[0]]
    assert os.path.basename(mundo.programas.comandos[0][0]) == "wordcraft-cli.exe"
    # Já existe: não sobrescreve.
    assert mundo.apps.converter(docx, ".pdf", mundo.saida) == [os.path.join(mundo.saida, "Prova 2 (1).pdf")]


def test_pdf_vira_uma_imagem_por_pagina_numa_subpasta(mundo):
    mundo.programas.paginas = 3
    pdf = arquivo(mundo.pasta_docs, "aula.pdf")
    feitos = mundo.apps.converter(pdf, ".png", mundo.saida)
    assert [os.path.basename(f) for f in feitos] == ["aula - p01.png", "aula - p02.png", "aula - p03.png"]
    assert os.path.basename(os.path.dirname(feitos[0])) == "aula (imagens)"
    assert mundo.programas.acoes() == [("pdfcraft-cli.exe", "info")] + [("pdfcraft-cli.exe", "render")] * 3
    assert mundo.programas.comandos[1][3:5] == ["--page", "1"]  # as páginas começam em 1


def test_word_vira_imagem_passando_pelo_pdf(mundo):
    docx = arquivo(mundo.pasta_docs, "ficha.docx")
    feitos = mundo.apps.converter(docx, ".png", mundo.saida)
    assert [os.path.basename(f) for f in feitos] == ["ficha.png"]  # uma página só: sem subpasta
    assert mundo.programas.acoes() == [("wordcraft-cli.exe", "convert"), ("pdfcraft-cli.exe", "info"),
                                       ("pdfcraft-cli.exe", "render")]
    assert not os.path.exists(mundo.programas.comandos[0][-1])  # o PDF do meio foi apagado


def test_pdf_vira_texto(mundo):
    pdf = arquivo(mundo.pasta_docs, "contrato.pdf")
    [txt] = mundo.apps.converter(pdf, ".txt", mundo.saida)
    assert open(txt, encoding="utf-8").read() == "página 1\n\npágina 2"


@pytest.mark.parametrize("nome, extensao, motivo", [
    ("aula.pdf", ".docx", "De PDF eu só passo para imagem ou texto"),
    ("aula.pdf", ".pdf", "já está em PDF"),
    ("musica.mp3", ".pdf", "Não sei converter arquivos .mp3"),
    ("slides.pptx", ".xlsx", "O DeckCraft não converte PowerPoint em Excel"),
])
def test_conversoes_que_nao_existem_explicam(mundo, nome, extensao, motivo):
    with pytest.raises(ErroCraft, match=motivo):
        mundo.apps.converter(arquivo(mundo.pasta_docs, nome), extensao, mundo.saida)
    assert mundo.programas.comandos == []


def test_erro_do_programa_vira_mensagem(mundo):
    mundo.programas.falhar = "convert"
    with pytest.raises(ErroCraft, match="^O WordCraft não conseguiu: arquivo estragado$"):
        mundo.apps.converter(arquivo(mundo.pasta_docs, "x.docx"), ".pdf", mundo.saida)


def test_programa_que_demora_demais_para(mundo):
    def demora(comando, tempo=None):
        raise subprocess.TimeoutExpired(comando, 180)

    mundo.apps.rodar = demora
    with pytest.raises(ErroCraft, match="passou de 3 minutos"):
        mundo.apps.converter(arquivo(mundo.pasta_docs, "x.docx"), ".pdf", mundo.saida)


def test_juntar_converte_o_que_nao_e_pdf_e_mantem_a_ordem(mundo):
    a = arquivo(mundo.pasta_docs, "a.pdf")
    b = arquivo(mundo.pasta_docs, "b.docx")
    c = arquivo(mundo.pasta_docs, "c.png")
    destino = mundo.apps.juntar([a, b, c], mundo.saida, "juntos.pdf")
    assert destino == os.path.join(mundo.saida, "juntos.pdf") and os.path.isfile(destino)
    combinar = mundo.programas.comandos[-1]
    assert combinar[1] == "combine" and combinar[2] == a
    assert [os.path.basename(p) for p in combinar[3:5]] == ["b.pdf", "c.pdf"] and combinar[5:] == ["--out", destino]
    assert [x for x, _ in mundo.programas.acoes()[:2]] == ["wordcraft-cli.exe", "photocraft-cli.exe"]


def test_juntar_um_arquivo_so_nao_da(mundo):
    with pytest.raises(ErroCraft, match="pelo menos dois"):
        mundo.apps.juntar([arquivo(mundo.pasta_docs, "a.pdf")], mundo.saida, "x.pdf")


def test_sem_o_programa_de_linha_de_comando_explica(tmp_path):
    apps = AppsCraft(raizes=[str(tmp_path)], rodar=lambda c, tempo=None: pytest.fail("não roda nada"))
    with pytest.raises(ErroCraft, match="Para isso eu uso o WordCraft, que não está instalado"):
        apps.converter(arquivo(str(tmp_path), "x.docx"), ".pdf", str(tmp_path / "saida"))


# ------------------------------------------------------------------ agente: listar, explicar e abrir
def test_lista_os_apps_pela_voz_e_por_escrito(mundo):
    voz = mundo.diz("quais apps da ArtCraft você tem?").texto
    assert voz.startswith("Tenho 13 apps da ArtCraft: WordCraft, PdfCraft") and voz.count("•") == 0
    escrito = mundo.diz("quais são os aplicativos da artcraft", "whatsapp").texto
    assert "• PhotoCraft: fotos e imagens, como o Photoshop" in escrito


def test_pra_que_serve(mundo, tmp_path):
    assert mundo.diz("pra que serve o FigCraft?").texto == "O FigCraft é para telas e protótipos, como o Figma."
    sem_nada = AgenteCraft(apps=AppsCraft(raizes=[str(tmp_path / "vazio")]), pasta_saida=str(tmp_path))
    assert "não está instalado" in sem_nada.responder("o que é o CADCraft?").texto


def test_abre_o_app(mundo):
    assert mundo.diz("Beatriz, abre o PhotoCraft").texto == "Abri o PhotoCraft."
    [comando] = mundo.programas.iniciados
    assert comando == [os.path.join(mundo.raiz, "PhotoCraft", "photocraft.exe")]


def test_abre_o_arquivo_que_chegou_no_app_pedido(mundo):
    pdf = arquivo(mundo.pasta_docs, "contrato.pdf")
    mundo.recebidos.append(pdf)
    assert mundo.diz("abre esse PDF no PdfCraft").texto == "Abri “contrato.pdf” no PdfCraft."
    assert mundo.programas.iniciados[-1][1:] == [pdf]


def test_abre_um_arquivo_pelo_nome_no_app(mundo):
    foto = arquivo(mundo.pasta_docs, "praia de copacabana.jpg")
    assert mundo.diz("abre a foto da praia no PhotoCraft").texto == "Abri “praia de copacabana.jpg” no PhotoCraft."
    assert mundo.programas.iniciados[-1][1:] == [foto]


def test_pelo_celular_nao_abre_programas_no_pc(mundo):
    assert "não abro programas no PC" in mundo.diz("abre o word craft", "whatsapp").texto
    assert mundo.programas.iniciados == []


# ------------------------------------------------------------------ agente: converter
def test_converte_o_word_que_chegou_e_abre_no_pc(mundo):
    mundo.recebidos.extend([arquivo(mundo.pasta_docs, "foto.jpg"), arquivo(mundo.pasta_docs, "Prova 2.docx")])
    resposta = mundo.diz("converte esse Word em PDF")
    assert resposta.texto == ("Pronto: “Prova 2.docx” virou PDF, “Prova 2.pdf”, em documentos gerados. Abri pra você.")
    assert resposta.arquivos == []
    assert mundo.abertos == [os.path.join(mundo.saida, "2026-09-21_conversoes", "Prova 2.pdf")]


def test_pelo_whatsapp_o_convertido_volta_sem_abrir_no_pc(mundo):
    mundo.recebidos.append(arquivo(mundo.pasta_docs, "planilha.xlsx"))
    resposta = mundo.diz("passa essa planilha pra PDF", "whatsapp")
    assert resposta.arquivos == [os.path.join(mundo.saida, "2026-09-21_conversoes", "planilha.pdf")]
    assert "Abri" not in resposta.texto and mundo.abertos == []
    assert mundo.programas.acoes() == [("gridcraft-cli.exe", "convert")]


def test_pela_voz_e_me_manda_vai_para_o_whatsapp(mundo):
    mundo.recebidos.append(arquivo(mundo.pasta_docs, "ficha.docx"))
    resposta = mundo.diz("converte esse word em pdf e me manda no zap")
    assert len(resposta.arquivos) == 1 and mundo.abertos == []


def test_converte_pelo_nome_aproximado(mundo):
    docx = arquivo(mundo.pasta_docs, "05 - Prova 02 - Processo Penal.docx")
    arquivo(mundo.pasta_docs, "05 - Prova 02 - Processo Penal.pdf")  # o PDF que já existe não é a origem
    arquivo(mundo.pasta_docs, "receita de bolo.docx")
    resposta = mundo.diz("converte a prova dois de processo penal em PDF")
    assert resposta.texto.startswith("Pronto: “05 - Prova 02 - Processo Penal.docx” virou PDF")
    assert mundo.programas.comandos[0][2] == docx


def test_dois_arquivos_parecidos_pede_mais_do_nome(mundo):
    arquivo(mundo.pasta_docs, "aula 1 penal.docx")
    arquivo(os.path.join(mundo.pasta_docs, "outra"), "aula 1 penal.docx")
    assert mundo.diz("converte o word da aula 1 de penal em pdf").texto.startswith("Achei mais de um:")
    assert mundo.programas.comandos == []


def test_pdf_em_imagens_diz_quantas(mundo):
    mundo.programas.paginas = 4
    mundo.recebidos.append(arquivo(mundo.pasta_docs, "aula.pdf"))
    resposta = mundo.diz("transforma esse PDF em imagem")
    assert resposta.texto.startswith("Pronto: “aula.pdf” virou 4 imagens, uma por página.")
    assert mundo.abertos == [os.path.join(mundo.saida, "2026-09-21_conversoes", "aula (imagens)")]  # abre a pasta


def test_converte_explicito_que_nao_existe_explica(mundo):
    mundo.recebidos.append(arquivo(mundo.pasta_docs, "aula.pdf"))
    assert mundo.diz("converte esse PDF em Word").texto.startswith("De PDF eu só passo para imagem ou texto")


def test_converte_sem_arquivo_pergunta_qual(mundo):
    assert mundo.diz("converte o word em pdf").texto.startswith("Qual arquivo?")


@pytest.mark.parametrize("frase", [
    "transforma esse PDF numa apresentação",  # os agentes fazem os slides
    "salva essa resposta em PDF",  # não fala de um arquivo
    "faz um resumo em PDF sobre a reforma tributária",
    "passa pra mim a previsão do tempo",
    "vira fonte",
    "abre o word",
    "junta dinheiro pra viagem",
    "combinado, me manda esses arquivos",
])
def test_pedidos_que_nao_sao_dos_apps(mundo, frase):
    mundo.recebidos.append(arquivo(mundo.pasta_docs, "aula.pdf"))
    assert mundo.diz(frase) is None
    assert mundo.programas.comandos == [] and mundo.programas.iniciados == []


# ------------------------------------------------------------------ agente: juntar
def test_junta_os_arquivos_que_chegaram_na_ordem_em_que_chegaram(mundo):
    mundo.programas.paginas = 7
    a = arquivo(mundo.pasta_docs, "capa.pdf")
    b = arquivo(mundo.pasta_docs, "texto.docx")
    mundo.recebidos.extend([a, b])
    resposta = mundo.diz("junta esses arquivos num PDF só")
    assert resposta.texto == ("Pronto: juntei 2 arquivos num PDF só, “Juntos - capa e mais 1.pdf”, com 7 páginas, "
                              "em documentos gerados. Abri pra você.")
    combinar = next(c for c in mundo.programas.comandos if c[1] == "combine")
    assert combinar[2] == a and os.path.basename(combinar[3]) == "texto.pdf"  # o Word não foi filtrado pelo "num PDF"


def test_junta_as_aulas_pelo_nome_em_ordem_natural(mundo):
    pasta = os.path.join(mundo.pasta_docs, "direito tributario completo")
    for nome in ("10_aula_Recurso_Tributário.pdf", "2_aula_Conceitos.pdf", "1_aula_Introdução_ao_Tributário.pdf"):
        arquivo(pasta, nome)
    arquivo(mundo.pasta_docs, "receita de bolo.pdf")
    resposta = mundo.diz("junta os PDFs das aulas de tributário", "app")
    assert resposta.texto.startswith("Pronto: juntei 3 arquivos") and len(resposta.arquivos) == 1
    combinar = mundo.programas.comandos[-2]  # o último é o "info" das páginas
    assert [os.path.basename(p) for p in combinar[2:5]] == [
        "1_aula_Introdução_ao_Tributário.pdf", "2_aula_Conceitos.pdf", "10_aula_Recurso_Tributário.pdf"]


def test_juntar_um_so_ou_nenhum_explica(mundo):
    assert mundo.diz("junta esses PDFs").texto.startswith("Não achei os arquivos para juntar")
    mundo.recebidos.append(arquivo(mundo.pasta_docs, "a.pdf"))
    assert "preciso de pelo menos dois" in mundo.diz("junta esses PDFs").texto


# ------------------------------------------------------------------ ligação com as fontes, o main e os painéis
def test_recebidos_do_pedido_vem_na_ordem_em_que_chegaram(tmp_path):
    from fontes import AgenteFontes, Registro

    agora = [1000.0]
    registro = Registro(str(tmp_path / "recebidos.json"), relogio=lambda: agora[0])
    agente = AgenteFontes(registro, str(tmp_path / "recebidos"), str(tmp_path / "arquivados"), relogio=lambda: agora[0])
    assert agente.recebidos_do_pedido("junta esses") == []
    for nome in ("primeiro.pdf", "segundo.pdf"):
        registro.registrar(arquivo(str(tmp_path), nome), nome, "whatsapp")
        agora[0] += 10
    assert [os.path.basename(c) for c in agente.recebidos_do_pedido("junta esses")] == ["primeiro.pdf", "segundo.pdf"]
    assert [os.path.basename(c) for c in agente.recebidos_do_pedido("converte o último")] == ["segundo.pdf"]


def test_main_responde_pelo_canal_e_manda_os_arquivos(monkeypatch):
    import main

    enviados, falas = [], []

    class Canal:
        def enviar(self, texto, arquivos=()):
            enviados.append((texto, list(arquivos)))

    monkeypatch.setattr(main, "avisar_painel", lambda *a, **k: None)
    monkeypatch.setattr(main, "CANAL_APP", Canal())
    monkeypatch.setitem(main.CANAL, "atual", "app")
    main.responder_craft(RespostaCraft("Pronto: virou PDF.", ["x.pdf"]))
    assert enviados == [("Pronto: virou PDF.", ["x.pdf"])]
    # Pela voz, "... e me manda" vai pelo WhatsApp, e a resposta é falada.
    monkeypatch.setitem(main.CANAL, "atual", "voz")
    monkeypatch.setattr(main, "mandar_pelo_whatsapp", lambda arquivos, texto="": enviados.append(("zap", arquivos)))
    monkeypatch.setattr(main, "responder_fontes", lambda texto, canal="voz": falas.append(texto))
    main.responder_craft(RespostaCraft("Pronto: virou PDF.", ["y.pdf"]))
    assert enviados[-1] == ("zap", ["y.pdf"]) and falas == ["Pronto: virou PDF. Mandei no seu WhatsApp."]
    assert main.CRAFT.recebidos == main.FONTES.recebidos_do_pedido and main.CRAFT.arquivos is main.PASTAS.arquivos


def test_painel_web_atende_antes_das_fontes(mundo, monkeypatch):
    import painel_web as pw

    api = pw.ApiPainel(None, {}, None)
    monkeypatch.setattr(pw, "falar_async", lambda texto: None)
    monkeypatch.setattr(api, "_craft", mundo.agente)
    monkeypatch.setattr(api._fontes, "responder", lambda *a, **k: pytest.fail("os apps da ArtCraft vêm antes das fontes"))
    api._processar("abre o DeckCraft")
    assert any(e.get("texto") == "Abri o DeckCraft." for e in api.eventos())


# ------------------------------------------------------------------ editar fotos (LightCraft)
def test_editar_foto_monta_os_comandos_na_ordem(mundo):
    foto = arquivo(mundo.pasta_docs, "praia.jpg")
    edicao = EdicaoDeFoto(filtro="pb", automatico=True, luz=0.6, nitidez=70, girar=3, espelhar=True, recorte="1x1", tamanho=1080)
    destino = mundo.apps.editar_foto(foto, edicao, mundo.saida)
    assert destino == os.path.join(mundo.saida, "praia (editada).jpg") and os.path.isfile(destino)
    comando = mundo.programas.comandos[0]
    assert os.path.basename(comando[0]) == "lightcraft-cli.exe" and comando[1:4] == ["run", "--import", foto]
    assert comando[4:] == [
        "preset.apply", "id=lc.bw-soft", "develop.auto", "develop.set",
        'values={"light.exposure": 0.6, "detail.sharpenAmount": 70}', "photo.rotateLeft", "photo.flipHorizontal",
        "crop.aspect", 'aspect="1x1"', "app.export", f"path={destino}", "longEdge=1080", "quality=92"]


def test_editar_foto_gira_para_a_direita_e_grava_em_png(mundo):
    foto = arquivo(mundo.pasta_docs, "a.png")
    destino = mundo.apps.editar_foto(foto, EdicaoDeFoto(girar=2, recorte="9x16", formato=".png"), mundo.saida)
    comando = mundo.programas.comandos[0]
    assert comando.count("photo.rotateRight") == 2 and "aspect=[9,16]" in comando and destino.endswith("a (editada).png")


def test_editar_foto_recusa_o_que_nao_e_foto(mundo):
    with pytest.raises(ErroCraft, match="O LightCraft não abre arquivos .pdf"):
        mundo.apps.editar_foto(arquivo(mundo.pasta_docs, "x.pdf"), EdicaoDeFoto(filtro="pb"), mundo.saida)


def test_erro_do_lightcraft_traz_a_frase_do_comando_que_falhou(mundo):
    def falha(comando, tempo=None):
        saida = '{"command":"preset.apply","ok":true}\n{"command":"app.export","error":"disco cheio","ok":false}\n'
        return subprocess.CompletedProcess(comando, 1, saida, "lightcraft-cli: 1 command(s) failed\n")

    mundo.apps.rodar = falha
    with pytest.raises(ErroCraft, match="^O LightCraft não conseguiu: disco cheio$"):
        mundo.apps.editar_foto(arquivo(mundo.pasta_docs, "a.jpg"), EdicaoDeFoto(filtro="pb"), mundo.saida)


def test_edicao_de_foto_do_app_fica_dentro_dos_limites():
    edicao = EdicaoDeFoto.do_app({"filtro": "inventado", "luz": 9, "contraste": "abc", "cor": -500, "girar": 7, "recorte": "3x2",
                                  "tamanho": 99999, "formato": ".exe", "espelhar": 1})
    assert edicao == EdicaoDeFoto(luz=2, cor=-100, girar=3, espelhar=True, tamanho=8000)
    assert EdicaoDeFoto.do_app({}).vazia() and not EdicaoDeFoto.do_app({"filtro": "sepia"}).vazia()


# ------------------------------------------------------------------ editar vídeos (FilmCraft)
def test_editar_video_monta_o_roteiro(mundo):
    video = arquivo(mundo.pasta_docs, "festa.mp4")
    edicao = EdicaoDeVideo(inicio=10, fim=30, sem_som=True, metade=True, velocidade=200, filtro="pb")
    destino = mundo.apps.editar_video(video, edicao, mundo.saida)
    assert destino == os.path.join(mundo.saida, "festa (editado).mp4") and os.path.isfile(destino)
    [roteiro] = mundo.programas.roteiros
    assert [l["id"] for l in roteiro] == ["file.import", "file.newSequenceFromClip", "edit.selectAll", "clip.speedDuration",
                                          "lumetri.applyPreset", "file.exportMedia"]
    assert roteiro[0]["params"] == {"paths": [video]} and roteiro[4]["params"] == {"name": "Neutral Mono"}
    # Com o dobro da velocidade, o trecho de 10 a 30 s do original fica de 5 a 15 s na sequência
    assert roteiro[-1]["params"] == {"path": destino, "format": "h264", "range": "custom", "startSeconds": 5.0, "endSeconds": 15.0,
                                     "scale": 0.5, "audio": False, "wait": True}
    assert mundo.programas.tempos[-1] == 1200  # vídeo tem mais tempo que um documento


def test_imagem_de_um_momento_do_video(mundo):
    video = arquivo(mundo.pasta_docs, "festa.mov")
    destino = mundo.apps.editar_video(video, EdicaoDeVideo(saida=".jpg", inicio=75), mundo.saida)
    assert os.path.basename(destino) == "festa - imagem em 1m15s.jpg" and os.path.isfile(destino)
    exportar = mundo.programas.roteiros[0][-1]["params"]
    assert exportar["format"] == "jpg" and exportar["startSeconds"] == 75 and exportar["endSeconds"] == pytest.approx(75.02)


def test_so_o_som_e_gif(mundo):
    video = arquivo(mundo.pasta_docs, "festa.mp4")
    som = mundo.apps.editar_video(video, EdicaoDeVideo(saida=".aac", metade=True), mundo.saida)
    gif = mundo.apps.editar_video(video, EdicaoDeVideo(saida=".gif", metade=True), mundo.saida)
    assert som.endswith("festa (som).aac") and gif.endswith("festa (editado).gif")
    assert "scale" not in mundo.programas.roteiros[0][-1]["params"] and mundo.programas.roteiros[1][-1]["params"]["scale"] == 0.5


def test_os_ultimos_segundos_usam_a_duracao(mundo):
    mundo.programas.duracao = 40
    video = arquivo(mundo.pasta_docs, "festa.mp4")
    mundo.apps.editar_video(video, EdicaoDeVideo(ultimos=10), mundo.saida)
    mundo.apps.editar_video(video, EdicaoDeVideo(sem_os_ultimos=5), mundo.saida)
    mundo.apps.editar_video(video, EdicaoDeVideo(inicio=12), mundo.saida)
    trechos = [(r[-1]["params"]["startSeconds"], r[-1]["params"]["endSeconds"]) for r in mundo.programas.roteiros]
    assert trechos == [(30, 40), (0, 35), (12, 40)]


@pytest.mark.parametrize("edicao, motivo", [
    (EdicaoDeVideo(filtro="vivo"), "é só para fotos"),
    (EdicaoDeVideo(inicio=30, fim=10), "O fim do trecho .0:10. tem de vir depois do começo .0:30."),
    (EdicaoDeVideo(sem_os_ultimos=90), "não sobra nada"),
])
def test_edicoes_de_video_impossiveis_explicam(mundo, edicao, motivo):
    with pytest.raises(ErroCraft, match=motivo):
        mundo.apps.editar_video(arquivo(mundo.pasta_docs, "festa.mp4"), edicao, mundo.saida)
    assert mundo.programas.roteiros == []


def test_converter_video_em_gif_passa_pelo_filmcraft(mundo):
    [gif] = mundo.apps.converter(arquivo(mundo.pasta_docs, "festa.mp4"), ".gif", mundo.saida)
    assert gif.endswith("festa (editado).gif") and mundo.programas.roteiros[0][-1]["params"]["format"] == "gif"


def test_edicao_de_video_do_app():
    assert EdicaoDeVideo.do_app({"inicio": 0, "fim": 0, "velocidade": 900, "saida": ".exe", "filtro": "x"}) == EdicaoDeVideo(velocidade=400)
    assert EdicaoDeVideo.do_app({"inicio": 0, "saida": ".jpg"}).inicio == 0  # a imagem do começo
    assert EdicaoDeVideo.do_app({"inicio": 2.5, "fim": 8, "sem_som": True}) == EdicaoDeVideo(inicio=2.5, fim=8, sem_som=True)


# ------------------------------------------------------------------ o que a pessoa pede
@pytest.mark.parametrize("frase, descricao", [
    ("deixa essa foto preto e branco e mais clara", ["em preto e branco", "mais clara"]),
    ("gira essa foto pra esquerda e deixa quadrada", ["girada para a esquerda", "quadrada"]),
    ("melhora essa foto", ["melhorada no automático"]),
    ("diminui essa foto pra mandar", ["menor (1600 px no lado maior)"]),
    ("espelha a selfie", ["espelhada"]),
    ("deixa essa imagem mais nítida e tira o granulado", ["mais nítida", "sem granulado"]),
    ("diminui o brilho da foto", ["mais escura"]),
    ("foto da praia com cores vivas e 1200 px", ["com cores vivas", "menor (1200 px no lado maior)"]),
    ("deixa a foto com cara de antiga", ["com cara de antiga"]),
])
def test_le_os_pedidos_de_foto(frase, descricao):
    assert ler_edicao_de_foto(normalizar(frase))[0].descricao() == descricao


@pytest.mark.parametrize("frase, descricao", [
    ("corta esse vídeo do 10 ao 30", ["cortado de 0:10 a 0:30"]),
    ("corta o vídeo de 0:10 a 1:05 e tira o som", ["cortado de 0:10 a 1:05", "sem som"]),
    ("deixa só os primeiros 15 segundos do vídeo", ["cortado de 0:00 a 0:15"]),
    ("tira os primeiros cinco segundos do vídeo", ["a partir de 0:05"]),
    ("tira os últimos 3 segundos do vídeo", ["sem os últimos 3 segundos"]),
    ("só os últimos dez segundos do vídeo", ["só os últimos 10 segundos"]),
    ("acelera esse vídeo 4x", ["4 vezes mais rápido"]),
    ("deixa o vídeo em câmera lenta e preto e branco", ["em câmera lenta", "em preto e branco"]),
    ("transforma esse vídeo em gif", ["em GIF"]),
    ("extrai o áudio desse vídeo", ["só o som"]),
    ("tira um print do vídeo no segundo 12", ["a imagem de 0:12"]),
    ("deixa o vídeo menor", ["com metade do tamanho"]),
    ("corta o vídeo do minuto 1 e meio ao minuto 2", ["cortado de 1:30 a 2:00"]),
    ("corta o video entre 5 e 20 segundos", ["cortado de 0:05 a 0:20"]),
])
def test_le_os_pedidos_de_video(frase, descricao):
    assert ler_edicao_de_video(frase, normalizar(frase))[0].descricao() == descricao


def test_texto_de_tempo():
    assert texto_de_tempo("Do minuto 1 e meio até dois minutos e 10 segundos") == "do 1:30 ate 2:10"
    assert texto_de_tempo("os primeiros vinte e cinco segundos") == "os primeiros 25"


# ------------------------------------------------------------------ agente: editar
def test_edita_a_foto_que_chegou_e_abre_no_pc(mundo):
    mundo.recebidos.append(arquivo(mundo.pasta_docs, "praia.jpg"))
    resposta = mundo.diz("deixa essa foto preto e branco")
    assert resposta.texto == ("Pronto: a foto ficou em preto e branco, “praia (editada).jpg”, em documentos gerados. "
                              "Abri pra você.")
    assert mundo.abertos == [os.path.join(mundo.saida, "2026-09-21_conversoes", "praia (editada).jpg")]


def test_pelo_whatsapp_a_foto_editada_volta(mundo):
    mundo.recebidos.append(arquivo(mundo.pasta_docs, "praia.jpg"))
    resposta = mundo.diz("gira isso pra direita", "whatsapp")
    assert len(resposta.arquivos) == 1 and mundo.abertos == [] and "girada para a direita" in resposta.texto


def test_edita_a_foto_pelo_nome(mundo):
    foto = arquivo(mundo.pasta_docs, "aniversario da vovo.jpg")
    arquivo(mundo.pasta_docs, "aniversario da vovo.pdf")
    assert mundo.diz("deixa a foto do aniversário da vovó em sépia").texto.startswith("Pronto: a foto ficou em sépia")
    assert mundo.programas.comandos[0][3] == foto


def test_video_editado_em_segundo_plano_avisa_no_fim(mundo):
    mundo.recebidos.append(arquivo(mundo.pasta_docs, "festa.mp4"))
    resposta = mundo.diz("corta esse vídeo do 10 ao 30 e tira o som", "app")
    assert resposta.texto == "Editando “festa.mp4” no FilmCraft: cortado de 0:10 a 0:30 e sem som. Aviso quando ficar pronto."
    [(pronto, canal)] = mundo.avisos
    assert canal == "app" and pronto.texto.startswith("Pronto: o vídeo ficou cortado de 0:10 a 0:30 e sem som")
    assert pronto.arquivos == [os.path.join(mundo.saida, "2026-09-21_conversoes", "festa (editado).mp4")]


def test_converte_esse_video_em_gif(mundo):
    mundo.recebidos.append(arquivo(mundo.pasta_docs, "festa.mp4"))
    assert mundo.diz("converte esse vídeo em GIF").texto.startswith("Editando “festa.mp4” no FilmCraft: em GIF")
    assert mundo.avisos[0][0].texto.startswith("Pronto: o vídeo ficou em GIF")


def test_falha_do_video_tambem_avisa(mundo):
    mundo.programas.falhar = "run"
    mundo.recebidos.append(arquivo(mundo.pasta_docs, "festa.mp4"))
    mundo.diz("acelera esse vídeo")
    assert mundo.avisos[0][0].texto.startswith("Não deu para editar “festa.mp4”: O FilmCraft não conseguiu")


def test_sem_foto_pergunta_qual(mundo):
    assert mundo.diz("deixa a foto preto e branco").texto.startswith("Qual foto? Me mande a foto antes")
    assert mundo.diz("corta o vídeo do 10 ao 20").texto.startswith("Qual vídeo?")


@pytest.mark.parametrize("frase", [
    "deixa isso mais claro",  # é pedir uma explicação melhor, não clarear a foto que chegou
    "melhora isso",
    "deixa a TV mais clara",
    "aumenta o brilho",
    "fala mais devagar",
    "resume esse vídeo",
    "tira um print da tela",
])
def test_frases_parecidas_que_nao_sao_edicao(mundo, frase):
    mundo.recebidos.append(arquivo(mundo.pasta_docs, "praia.jpg"))
    assert mundo.diz(frase) is None
    assert mundo.programas.comandos == []


def test_isso_com_um_pedido_inequivoco_e_edicao(mundo):
    mundo.recebidos.append(arquivo(mundo.pasta_docs, "praia.jpg"))
    assert mundo.diz("deixa isso preto e branco").texto.startswith("Pronto: a foto ficou em preto e branco")


# ------------------------------------------------------------------ a tela "Editar" do celular
def test_editar_do_app_foto_volta_pronta(mundo):
    foto = arquivo(mundo.pasta_docs, "praia.jpg")
    situacao = mundo.agente.editar_do_app(foto, {"tipo": "foto", "filtro": "quente", "luz": 0.5})
    assert situacao == {"pronto": True, "ok": True, "texto": "Pronto: a foto ficou com tom quente e mais clara.",
                        "arquivos": [os.path.join(mundo.saida, "2026-09-21_conversoes", "praia (editada).jpg")]}
    assert mundo.agente.editar_do_app(foto, {"tipo": "foto"})["texto"] == "Escolha pelo menos um ajuste."


def test_editar_do_app_video_vira_tarefa(mundo):
    video = arquivo(mundo.pasta_docs, "festa.mp4")
    inicio = mundo.agente.editar_do_app(video, {"tipo": "video", "inicio": 2, "fim": 6, "velocidade": 200})
    assert inicio["pronto"] is False and inicio["texto"].startswith("Editando “festa.mp4”")
    situacao = mundo.agente.situacao(inicio["tarefa"])
    assert situacao["pronto"] and situacao["ok"] and situacao["arquivos"][0].endswith("festa (editado).mp4")
    assert mundo.avisos[0][1] == "app"  # o vídeo pronto também aparece na conversa do app
    assert mundo.agente.situacao("nao-existe") is None


def test_editar_do_app_converte(mundo):
    docx = arquivo(mundo.pasta_docs, "prova.docx")
    situacao = mundo.agente.editar_do_app(docx, {"tipo": "converter", "formato": ".pdf"})
    assert situacao["ok"] and situacao["texto"] == "Pronto: virou PDF." and situacao["arquivos"][0].endswith("prova.pdf")
    assert mundo.agente.editar_do_app(docx, {"tipo": "converter", "formato": ".exe"})["texto"] == "Formato desconhecido."
    erro = mundo.agente.editar_do_app(arquivo(mundo.pasta_docs, "a.pdf"), {"tipo": "converter", "formato": ".docx"})
    assert erro["ok"] is False and erro["texto"].startswith("De PDF eu só passo")
    video = mundo.agente.editar_do_app(arquivo(mundo.pasta_docs, "festa.mp4"), {"tipo": "converter", "formato": ".gif"})
    assert video["pronto"] is False and mundo.agente.situacao(video["tarefa"])["ok"]


def test_rota_de_edicao_do_app(tmp_path, monkeypatch):
    import app_celular
    from fastapi.testclient import TestClient

    recebidos, gerados = tmp_path / "recebidos", tmp_path / "gerados"
    recebidos.mkdir()
    gerados.mkdir()
    monkeypatch.setattr(app_celular, "PASTAS_QUE_SAEM", (str(gerados), str(recebidos)))
    chamadas = []

    def editar(caminho, pedido):
        chamadas.append((open(caminho, "rb").read(), pedido))
        pronto = gerados / "foto (editada).jpg"
        pronto.write_bytes(b"jpg")
        return {"pronto": True, "ok": True, "texto": "Pronto.", "arquivos": [str(pronto)]}

    canal, aparelhos = app_celular.CanalApp(), app_celular.Aparelhos(str(tmp_path / "aparelhos.json"))
    cliente = TestClient(app_celular.criar_servidor(
        canal, aparelhos, pasta_recebidos=str(recebidos), editar=editar, limite_da_edicao_mb=0.001,
        situacao_da_edicao=lambda tarefa: {"pronto": False, "texto": "Editando…", "arquivos": []} if tarefa == "t1" else None))
    codigo = aparelhos.novo_codigo()
    chave = {app_celular.CABECALHO_DA_CHAVE: cliente.post("/api/parear", json={"codigo": codigo}).json()["chave"]}
    cabecalhos = {**chave, "X-Nome": "foto%20praia.jpg", "X-Edicao": "%7B%22tipo%22%3A%22foto%22%2C%22filtro%22%3A%22pb%22%7D"}
    resposta = cliente.post("/api/editar", content=b"conteudo", headers=cabecalhos)
    assert resposta.status_code == 200
    assert chamadas == [(b"conteudo", {"tipo": "foto", "filtro": "pb"})]
    [anexo] = resposta.json()["arquivos"]
    assert anexo["nome"] == "foto (editada).jpg" and cliente.get(f"/api/arquivo/{anexo['id']}", headers=chave).content == b"jpg"
    assert cliente.get("/api/editar/t1", headers=chave).json()["texto"] == "Editando…"
    assert cliente.get("/api/editar/t2", headers=chave).status_code == 404
    # Grande demais (o limite do teste é 1 KB), programa, pedido estragado e sem a chave
    assert cliente.post("/api/editar", content=b"x" * 2000, headers=cabecalhos).status_code == 413
    assert cliente.post("/api/editar", content=b"x", headers={**cabecalhos, "X-Nome": "virus.exe"}).status_code == 415
    assert cliente.post("/api/editar", content=b"x", headers={**cabecalhos, "X-Edicao": "%5B1"}).status_code == 400
    assert cliente.post("/api/editar", content=b"x", headers={"X-Nome": "a.jpg"}).status_code == 401
    assert len(os.listdir(next(recebidos.iterdir()))) == 1  # o grande demais foi apagado
