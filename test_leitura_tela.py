"""Testes da leitura de tela: entender o pedido, montar o texto do OCR, o agente (com captura, OCR e modelos falsos),
o que o convidado e o WhatsApp não podem e o contexto da janela em uso. Nenhum teste fotografa a tela de verdade."""
import pytest

import leitura_tela as lt
import main
from biometria_voz import Falante
from leitura_tela import AgenteTela, Captura, Janela, Linha, OcrIndisponivel, Pedido


@pytest.fixture(autouse=True)
def modelo_de_visao_fraco_por_padrao(monkeypatch):
    """Os testes não dependem do OLLAMA_VISION_MODEL do .env: o padrão aqui é o llava (fraco)."""
    monkeypatch.setattr(lt, "OLLAMA_VISION_MODEL", "llava")


# ------------------------------------------------------------------ entender o pedido
DEVEM = [
    "Beatriz, resume esse PDF", "analisa esse gráfico", "lê a minha tela", "o que tem na tela?", "olha a tela inteira",
    "por que deu esse erro?", "depura esse código", "me explica essa janela", "traduz esse texto", "o que estou vendo?",
    "o que diz esse documento", "revisa meu código", "analisa as duas telas", "lê o que está nessa página",
    "confere esse contrato", "explica essa tabela", "descreve a janela ativa", "que erro é esse", "corrige esse código aberto",
    "resume o artigo que estou lendo", "como eu resolvo esse erro", "o que estou fazendo?",
]
NAO_DEVEM = [
    "que horas são", "abre a janela da agenda", "me conta uma piada", "resume o PDF da OAB que está na pasta",
    "faz um PDF sobre o clima", "toca uma música", "qual a previsão do tempo", "imprime minha agenda",
    "explica o artigo 5 da constituição", "escreve um código em python que soma dois números", "o que é um gráfico de pizza",
    "liga a tela do computador", "o que você acha disso", "obrigado", "pesquisa o preço do dólar", "resume o artigo 500 do código civil",
]


@pytest.mark.parametrize("frase", DEVEM)
def test_reconhece_pedidos_de_olhar_a_tela(frase):
    assert lt.interpretar(frase) is not None, frase


@pytest.mark.parametrize("frase", NAO_DEVEM)
def test_deixa_passar_o_que_nao_e_a_tela(frase):
    assert lt.interpretar(frase) is None, frase


def test_escopo_e_tipo_do_pedido():
    assert lt.interpretar("resume esse PDF") == Pedido("resume esse PDF", escopo="janela", tipo="leitura")
    assert lt.interpretar("analisa esse gráfico").tipo == "grafico"
    assert lt.interpretar("por que deu esse erro").tipo == "codigo"
    assert lt.interpretar("o que tem na tela?").escopo == "tela"  # o monitor todo
    assert lt.interpretar("lê a minha tela").escopo == "tela"
    assert lt.interpretar("o que tem nessa janela?").escopo == "janela"
    assert lt.interpretar("lê a tela inteira").escopo == "tela"
    assert lt.interpretar("analisa todas as telas").escopo == "todas"
    assert lt.interpretar("o que estou fazendo?").tipo == "atividade"


def test_perguntas_seguintes_so_valem_com_a_sessao_ativa():
    for frase in ("explica melhor", "e o segundo item?", "como eu corrijo?", "e esse erro aqui", "olha de novo"):
        assert lt.interpretar(frase) is None, frase
        assert lt.interpretar(frase, sessao_ativa=True).seguimento, frase
    assert lt.interpretar("olha de novo", sessao_ativa=True).refazer
    assert lt.interpretar("me conta uma piada", sessao_ativa=True) is None


# ------------------------------------------------------------------ texto do OCR
def test_texto_comum_mantem_a_ordem_do_ocr():
    assert lt.montar_texto([Linha("Título"), Linha("corpo do texto")]) == "Título\ncorpo do texto"


def test_codigo_refaz_recuo_e_junta_pedacos_da_mesma_linha():
    # o OCR separou "total", "=" e "0" da linha 2 e recuou 4 letras (4 x 10 px) em relação ao "def"
    linhas = [
        Linha("def media(v):", x=100, largura_letra=10, y=10, altura=20, direita=230),
        Linha("total", x=140, largura_letra=10, y=40, altura=20, direita=190),
        Linha("=", x=200, largura_letra=10, y=41, altura=20, direita=210),
        Linha("0", x=220, largura_letra=10, y=40, altura=20, direita=230),
        Linha("return total", x=140, largura_letra=10, y=70, altura=20, direita=260),
    ]
    assert lt.montar_texto(linhas, recuo=True) == "def media(v):\n    total = 0\n    return total"


def test_cortar_texto_guarda_comeco_e_fim():
    texto = "A" * 100 + "B" * 100
    cortado, foi = lt.cortar_texto(texto, 60)
    assert foi and cortado.startswith("A" * 30) and cortado.endswith("B" * 30) and "cortado" in cortado
    assert lt.cortar_texto("curto", 60) == ("curto", False)


def test_separar_fala_e_detalhes():
    assert lt.separar("FALA: Troque valor por valores.\nDETALHES: na linha 5\nreturn x") == ("Troque valor por valores.", "na linha 5\nreturn x")
    assert lt.separar("Só isso.") == ("Só isso.", "")
    assert lt.separar("FALA: curta") == ("curta", "")


# ------------------------------------------------------------------ janelas
def janela(programa, titulo="", caixa=(0, 0, 800, 600)):
    return Janela(1, programa, titulo, caixa)


def test_atividade_limpa_o_titulo_e_diz_o_que_a_pessoa_faz():
    a = lt.atividade_de(janela("code", "main.py - B.E.A.T.R.I.Z - Visual Studio Code"))
    assert (a.programa, a.titulo, a.fazendo) == ("Visual Studio Code", "main.py", "programando")
    assert lt.atividade_de(janela("chrome", "contrato.pdf - Google Chrome")).fazendo == "lendo um PDF"
    assert lt.atividade_de(janela("programa_novo", "x")).programa == "Programa Novo"


def test_gerenciador_de_senhas_e_lista_do_usuario_nunca_sao_lidos(monkeypatch):
    assert lt.bloqueada(janela("keepassxc", "Cofre")) and lt.bloqueada(janela("chrome", "Bitwarden Vault"))
    assert lt.atividade_de(janela("1password", "Cofre")) is None
    assert not lt.bloqueada(janela("code", "main.py"))
    monkeypatch.setattr(lt, "TELA_IGNORAR", "meu banco, diario")
    assert lt.bloqueada(janela("chrome", "Meu Banco - Extrato")) and lt.bloqueada(janela("notepad", "diario.txt"))


def test_contexto_da_atividade_para_a_conversa(monkeypatch):
    monkeypatch.setattr(lt, "janelas_visiveis", lambda: [janela("code", "calc.py - Visual Studio Code")])
    contexto = lt.contexto_da_atividade()
    assert "Visual Studio Code" in contexto and "programando" in contexto and "calc.py" in contexto
    monkeypatch.setattr(lt, "janelas_visiveis", lambda: [])
    assert lt.contexto_da_atividade() == ""


# ------------------------------------------------------------------ o agente
class LLMFalso:
    def __init__(self, resposta="FALA: Resposta curta.\nDETALHES: Passo 1."):
        self.resposta, self.prompts = resposta, []

    def chat_beatriz(self, prompt, contexto=None, usar_historico=True, **_):
        self.prompts.append((prompt, contexto, usar_historico))
        return self.resposta


class Imagem:
    """Só o que o agente usa de uma imagem do Pillow."""
    size = (1000, 600)

    def copy(self):
        return self

    def thumbnail(self, tamanho):
        pass

    def save(self, destino, formato):
        destino.write(b"PNG-FALSO")


class Cenario:
    def __init__(self, programa="code", titulo="calc.py - Visual Studio Code", texto="def a():\n    return 1" * 10, bloqueada="",
                 resposta=None, visao="Um gráfico de barras crescente."):
        self.janela = Janela(7, programa, titulo, (0, 0, 800, 600))
        self.texto, self.bloqueada, self.visao_resposta = texto, bloqueada, visao
        self.capturas, self.vistos, self.relogio, self.avisos = [], [], [1000.0], []
        self.llm = LLMFalso(resposta) if resposta else LLMFalso()
        self.agente = AgenteTela(
            llm=self.llm, avisar=self.avisos.append, capturar_fn=self.capturar, ocr_fn=self.ocr, visao_fn=self.visao,
            relogio=lambda: self.relogio[0], janela_fn=lambda: self.janela,
        )

    def capturar(self, escopo):
        self.capturas.append(escopo)
        return Captura(None if self.bloqueada else Imagem(), self.janela, escopo, self.bloqueada)

    def ocr(self, imagem):
        if self.texto is None:
            raise OcrIndisponivel("sem OCR")
        return [Linha(l, y=i * 30, altura=20, largura_letra=10, direita=100) for i, l in enumerate(self.texto.split("\n"))]

    def visao(self, prompt, png):
        self.vistos.append((prompt, png))
        if self.visao_resposta is None:
            raise RuntimeError("sem modelo de visão")
        return self.visao_resposta


def test_le_a_janela_e_responde_com_o_modelo_de_texto():
    c = Cenario(texto="def media(v):\n    return total / len(valor)\nNameError: name 'valor' is not defined")
    resposta = c.agente.responder("por que deu esse erro?")
    assert resposta.fala == "Resposta curta." and resposta.painel == "Passo 1."
    prompt, contexto, historico = c.llm.prompts[0]
    assert "NameError: name 'valor'" in prompt and "code — calc.py" in prompt and "por que deu esse erro?" in prompt
    assert "OCR" in contexto and historico is False  # não usa o histórico da conversa
    assert c.capturas == ["janela"] and c.vistos == []
    assert c.avisos == ["Deixa eu olhar a janela do Visual Studio Code."]


def test_tela_inteira_e_todas_as_telas():
    c = Cenario()
    c.agente.responder("o que tem na tela?")
    c.agente.responder("analisa todas as telas")
    assert c.capturas == ["tela", "todas"]
    assert c.avisos[0] == "Deixa eu olhar a tela."


def test_visao_fraca_so_ajuda_e_quem_responde_e_o_modelo_de_texto():
    # llava (o padrão) inventa detalhes: a descrição vai marcada como aproximada, em cima do texto lido
    c = Cenario(programa="chrome", titulo="Vendas - Google Chrome", texto="Vendas por trimestre (R$ mil)\n1T 2T 3T 4T\n120 180 260 340 " * 2)
    resposta = c.agente.responder("analisa esse gráfico")
    assert resposta.fala == "Resposta curta."
    prompt_visao, png = c.vistos[0]
    assert png == b"PNG-FALSO" and "briefly" in prompt_visao  # pedido curto, em inglês, sem ler números
    prompt = c.llm.prompts[0][0]
    assert "Vendas por trimestre" in prompt and "DESCRIÇÃO VISUAL APROXIMADA" in prompt and "gráfico de barras crescente" in prompt


def test_visao_forte_responde_com_dados_fala_e_detalhes(monkeypatch):
    monkeypatch.setattr(lt, "OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
    resposta_visao = ("DADOS: Tabela de estoque\nCaneta 120 x 2,50 = 300\nMochila 12 x 129 = 1548\n"
                      "FALA: A mochila é o item que mais vale no estoque.\nDETALHES: Caneta 300; Mochila 1548.")
    c = Cenario(programa="excel", titulo="estoque.xlsx - Excel", visao=resposta_visao)
    resposta = c.agente.responder("explica essa tabela, qual item vale mais no estoque?")
    assert resposta.fala == "A mochila é o item que mais vale no estoque." and resposta.painel == "Caneta 300; Mochila 1548."
    prompt_visao, _ = c.vistos[0]
    assert "FALA:" in prompt_visao and "DETALHES:" in prompt_visao and "contas" in prompt_visao and "qual item vale mais" in prompt_visao
    assert "DADOS:" not in prompt_visao  # mais rótulos fazem o modelo de 7B repetir as instruções
    assert c.llm.prompts == []  # a visão responde sozinha: sem recarregar o modelo de texto


def test_visao_forte_sem_rotulos_usa_o_texto_todo_como_fala(monkeypatch):
    monkeypatch.setattr(lt, "OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
    c = Cenario(programa="chrome", titulo="Vendas", texto=None, visao="Um gráfico de barras crescente.")
    assert c.agente.responder("lê a tela").fala == "Um gráfico de barras crescente."


def test_secoes_da_resposta_da_visao():
    secoes = lt.separar_secoes("DADOS: a\nb\nFALA: c\nDETALHES: d")
    assert secoes == {"DADOS": "a\nb", "FALA": "c", "DETALHES": "d"}
    assert lt.separar("DADOS: x\nFALA: Falei.\nDETALHES: y") == ("Falei.", "y")


def test_tela_quase_sem_texto_tambem_usa_a_visao():
    c = Cenario(texto="ok")
    assert c.agente.responder("o que tem na tela?").fala == "Resposta curta."
    assert len(c.vistos) == 1 and "gráfico de barras crescente" in c.llm.prompts[0][0]


def test_sem_modelo_de_visao_e_com_texto_segue_pelo_texto():
    c = Cenario(visao=None)
    resposta = c.agente.responder("analisa esse gráfico")
    assert resposta.fala == "Resposta curta." and len(c.llm.prompts) == 1
    assert "DESCRIÇÃO VISUAL" not in c.llm.prompts[0][0]


def test_sem_ocr_usa_a_descricao_e_sem_nada_avisa():
    c = Cenario(texto=None)
    assert c.agente.responder("lê a tela").fala == "Resposta curta."
    assert "(nenhum texto legível)" in c.llm.prompts[0][0] and "avise que é uma descrição aproximada" in c.llm.prompts[0][0]
    c = Cenario(texto=None, visao=None)
    assert "Não consegui ler nada" in c.agente.responder("lê a tela").fala


def test_janela_de_senhas_nao_e_lida():
    c = Cenario(programa="keepassxc", titulo="Cofre", bloqueada="Cofre")
    resposta = c.agente.responder("lê a tela")
    assert "prefiro não ler" in resposta.fala and c.llm.prompts == [] and c.vistos == []


def test_erro_do_ollama_vira_mensagem_e_nao_excecao():
    c = Cenario()
    c.llm.chat_beatriz = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Ollama fora do ar"))
    assert "Não consegui olhar a tela" in c.agente.responder("resume esse PDF").fala


def test_o_que_estou_fazendo_nao_fotografa_nem_usa_llm():
    c = Cenario()
    resposta = c.agente.responder("Beatriz, o que estou fazendo?")
    assert "Visual Studio Code" in resposta.fala and "calc.py" in resposta.fala and "programando" in resposta.fala
    assert c.capturas == [] and c.llm.prompts == []
    c.janela = None
    assert "nenhuma janela" in c.agente.responder("o que estou fazendo?").fala


def test_pergunta_seguinte_usa_o_que_ja_foi_lido_sem_fotografar_de_novo():
    c = Cenario(texto="return total / len(valor)\nNameError: name 'valor'" * 3)
    c.agente.responder("por que deu esse erro?")
    c.relogio[0] += 60
    resposta = c.agente.responder("e como eu corrijo?")
    assert resposta.fala == "Resposta curta." and c.capturas == ["janela"]  # uma captura só
    prompt = c.llm.prompts[1][0]
    assert "NameError: name 'valor'" in prompt and "RESPOSTA ANTERIOR" in prompt and "Resposta curta." in prompt


def test_olha_de_novo_fotografa_outra_vez():
    c = Cenario()
    c.agente.responder("resume esse PDF")
    c.agente.responder("olha de novo")
    assert c.capturas == ["janela", "janela"]
    assert "resume esse PDF" in c.llm.prompts[1][0]  # repete a pergunta de antes
    c.agente.responder("lê a tela inteira")
    c.agente.responder("olha de novo")
    assert c.capturas[-2:] == ["tela", "tela"]


def test_a_sessao_expira_e_acaba_quando_o_assunto_muda():
    c = Cenario()
    c.agente.responder("resume esse PDF")
    c.relogio[0] += lt.TELA_CONTEXTO_SEGUNDOS + 1
    assert c.agente.responder("explica melhor") is None  # passou o tempo

    c.agente.responder("resume esse PDF")
    assert c.agente.responder("que horas são") is None  # falaram de outra coisa
    assert c.agente.sessao is None
    assert c.agente.responder("explica melhor") is None


def test_pergunta_seguinte_de_grafico_reaproveita_o_que_foi_visto(monkeypatch):
    c = Cenario(programa="chrome", titulo="Vendas")  # visão fraca: usa a descrição guardada, sem olhar de novo
    c.agente.responder("analisa esse gráfico")
    c.agente.responder("e o segundo item?")
    assert len(c.vistos) == 1 and c.capturas == ["janela"]
    assert "gráfico de barras crescente" in c.llm.prompts[1][0] and "RESPOSTA ANTERIOR" in c.llm.prompts[1][0]

    monkeypatch.setattr(lt, "OLLAMA_VISION_MODEL", "qwen2.5vl:7b")  # visão forte: olha a imagem guardada de novo
    c = Cenario(programa="chrome", titulo="Vendas", visao="DADOS: 1T 120\nFALA: Sobe.\nDETALHES: x")
    c.agente.responder("analisa esse gráfico")
    resposta = c.agente.responder("e o segundo trimestre?")
    assert len(c.vistos) == 2 and c.capturas == ["janela"] and resposta.fala == "Sobe."
    assert "Sua resposta anterior" in c.vistos[1][0] and "Sobe." in c.vistos[1][0]


# ------------------------------------------------------------------ main
def test_convidado_nao_chega_a_tela_do_dono():
    for pedido in ("lê a minha tela", "o que tem na janela", "resume esse pdf"):
        assert main.RE_SO_DO_DONO.search(pedido), pedido


def test_conversa_por_voz_leva_a_janela_em_uso(monkeypatch):
    contextos = []
    monkeypatch.setitem(main.CANAL, "atual", "voz")
    monkeypatch.setattr(main, "TELA_CONTEXTO_AUTOMATICO", True)
    monkeypatch.setattr(main.leitura_tela, "contexto_da_atividade", lambda: "Agora o usuário está em: Visual Studio Code.")
    monkeypatch.setattr(main.llm_clients, "chat_beatriz_stream", lambda texto, contexto=None, **_: contextos.append(contexto) or iter(["Oi."]))
    monkeypatch.setattr(main, "falar_resposta_em_stream", lambda stream, limpar=None: "".join(stream))
    monkeypatch.setattr(main, "registrar_resposta", lambda *a, **k: None)
    monkeypatch.setattr(main, "PAINEL_VOZ", None)
    main.responder_conversa_em_stream("como faço um loop?")
    assert contextos[0].startswith(main.CONTEXTO_CONVERSA_FALADA) and "Visual Studio Code" in contextos[0]

    monkeypatch.setattr(main, "TELA_CONTEXTO_AUTOMATICO", False)
    main.responder_conversa_em_stream("como faço um loop?")
    assert contextos[1] == main.CONTEXTO_CONVERSA_FALADA


def test_resposta_da_tela_nao_vai_para_o_historico(monkeypatch):
    falas, painel = [], []
    monkeypatch.setattr(main, "falar_async", falas.append)
    monkeypatch.setattr(main, "avisar_painel", lambda tipo, texto, *a, **k: painel.append((tipo, texto)))
    monkeypatch.setattr(main, "registrar_resposta", lambda *a, **k: (_ for _ in ()).throw(AssertionError("gravou no histórico")))
    monkeypatch.setattr(main, "registrar_conversa", lambda *a, **k: (_ for _ in ()).throw(AssertionError("gravou no histórico")))
    main.responder_tela(lt.RespostaTela("Troque valor por valores.", "Linha 5."))
    assert falas == ["Troque valor por valores."]
    assert painel == [("resposta", "Troque valor por valores.\n\nLinha 5.")]


def test_o_agente_do_main_reconhece_o_pedido_pelo_whatsapp():
    assert main.TELA.reconhece("resume esse PDF") and not main.TELA.reconhece("que horas são")
