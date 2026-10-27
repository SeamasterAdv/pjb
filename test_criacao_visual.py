"""Imagens e modelos 3D pelo Claude (criacao_visual.py): o pedido, a limpeza do SVG, a cena 3D e o agente, com IAs falsas."""
import base64
import json
import os
import xml.etree.ElementTree as ET

import pytest

import criacao_visual
from criacao_visual import AgenteCriacao, ErroDeCriacao, interpretar, limpar_svg, normalizar_modelo
from llm_nuvem import NuvemIndisponivel

SVG_BOM = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><title>Farol à noite</title>'
           '<defs><linearGradient id="g"><stop offset="0" stop-color="#123"/></linearGradient></defs>'
           '<rect width="100" height="100" fill="url(#g)"/></svg>')
CENA_BOA = {"titulo": "Casinha", "descricao": "Uma casa com telhado vermelho.",
            "partes": [{"nome": "paredes", "forma": "caixa", "largura": 2, "altura": 1.5, "profundidade": 2, "posicao": [0, 0.75, 0],
                        "cor": "#eeddcc"},
                       {"nome": "telhado", "forma": "cone", "raio": 1.6, "altura": 1, "posicao": [0, 2, 0], "cor": "#aa2222",
                        "material": "brilhante"}]}


class IAFalsa:
    def __init__(self, nome, respostas):
        self.nome = nome
        self.respostas = list(respostas)
        self.pedidos = []

    def chat(self, prompt, system_prompt=None, formato=None, **kwargs):
        self.pedidos.append({"prompt": prompt, "sistema": system_prompt, "formato": formato})
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def agente(tmp_path, *provedores):
    eventos, avisos = [], []
    criacao = AgenteCriacao(mostrar=eventos.append, avisar=lambda texto, canal, arquivos: avisos.append((texto, canal, arquivos)),
                            provedores=list(provedores), pasta=str(tmp_path / "criacoes"), em_segundo_plano=False)
    return criacao, eventos, avisos


# ------------------------------------------------------------------ o pedido
@pytest.mark.parametrize("frase, formato, assunto", [
    ("Beatriz, cria uma imagem de um farol à noite", "imagem", "um farol à noite"),
    ("desenha um gato astronauta", "imagem", "um gato astronauta"),
    ("me desenha uma árvore de natal", "imagem", "uma árvore de natal"),
    ("faz um logo pra minha banca de doces", "imagem", "um logo pra minha banca de doces"),
    ("me dá uma ilustração de um dragão", "imagem", "um dragão"),
    ("cria um modelo 3D de uma casa", "3d", "uma casa"),
    ("modela um foguete em 3D", "3d", "um foguete"),
    ("quero ver um dinossauro em 3D", "3d", "um dinossauro"),
    ("gera uma imagem 3D de um carro", "3d", "um carro"),
    ("monta um objeto 3D de uma cadeira", "3d", "uma cadeira"),
])
def test_reconhece_os_pedidos_de_criacao(frase, formato, assunto):
    pedido = interpretar(frase)
    assert (pedido.formato, pedido.assunto) == (formato, assunto)


@pytest.mark.parametrize("frase", [
    "faz uma imagem desse PDF",  # conversão: apps da ArtCraft
    "converte essa imagem em PDF",
    "manda a imagem do gato",  # mandar um arquivo que já existe
    "como fazer um modelo 3D no Blender?",
    "faz um PDF com uma imagem de gato",  # documento dos agentes
    "faz uma apresentação sobre impressão 3D",
    "qual a capital da França?",
])
def test_ignora_o_que_nao_e_criacao(frase):
    assert interpretar(frase) is None


def test_ia_pedida_e_envio_saem_do_assunto():
    pedido = interpretar("cria uma imagem pelo Gemini de um robô e me manda no zap")
    assert (pedido.assunto, pedido.ia, pedido.mandar) == ("um robô", "gemini", True)


# ------------------------------------------------------------------ o SVG
def test_limpar_svg_tira_o_que_roda_ou_busca_fora():
    sujo = ('<p>Aqui está:</p><svg width="800" height="600" onload="alert(1)"><script>alert(2)</script>'
            '<foreignObject><div>x</div></foreignObject><image href="https://x.com/a.png"/>'
            '<a xlink:href="javascript:alert(3)"><circle r="5" onclick="x()"/></a>'
            '<style>@import url(https://fonts.x/a.css); .c{fill:url(https://x.com/f)}</style>'
            '<use href="#g"/><rect style="fill:url(#g)"/></svg> pronto!')
    limpo = limpar_svg(sujo)
    raiz = ET.fromstring(limpo)
    texto = limpo.lower()
    for proibido in ("script", "foreignobject", "<image", "onload", "onclick", "javascript", "https://", "@import"):
        assert proibido not in texto
    assert raiz.get("viewBox") == "0 0 800 600" and raiz.get("width") is None
    assert 'href="#g"' in limpo and "url(#g)" in limpo  # as referências internas (gradientes, <use>) ficam


@pytest.mark.parametrize("resposta", ["não sei desenhar", "<svg><rect></svg>", '<svg><!ENTITY a "b"><text>&a;</text></svg>'])
def test_limpar_svg_recusa_o_que_nao_serve(resposta):
    with pytest.raises(ErroDeCriacao):
        limpar_svg(resposta)


def test_limpar_svg_descarta_o_que_vem_fora_do_svg():
    limpo = limpar_svg('<!DOCTYPE svg [<!ENTITY a "b">]><svg viewBox="0 0 10 10"><rect/></svg>')
    assert "DOCTYPE" not in limpo and ET.fromstring(limpo).get("viewBox") == "0 0 10 10"


# ------------------------------------------------------------------ a cena 3D
def test_normalizar_modelo_confere_cada_campo():
    dados = {"titulo": "Teste", "girar_sozinho": False, "partes": [
        {"forma": "Caixa", "largura": 1e9, "posicao": [1, "x", float("nan")], "cor": "#ABC", "material": "ouro"},
        {"forma": "nave_espacial"},
        {"forma": "torno", "pontos": [[0.5, 0]]},  # um ponto só não faz um torno
        {"forma": "grupo", "posicao": [0, 1, 0], "partes": [{"forma": "esfera", "raio": 0.3, "gira": 90}]},
        {"forma": "tubo", "pontos": [[0, 0, 0], [1, 1, 1], [2, 0, 0]], "raio": 0.1, "opacidade": 7},
    ]}
    modelo = normalizar_modelo(dados)
    caixa, grupo, tubo = modelo["partes"]
    assert caixa["largura"] == criacao_visual.LIMITE_TAMANHO and caixa["posicao"] == [1, 0, 0]
    assert (caixa["cor"], caixa["material"]) == ("#aabbcc", "fosco")
    assert grupo["partes"][0]["gira"] == [90, 90, 90]
    assert tubo["opacidade"] == 1.0
    assert modelo["girar_sozinho"] is False and modelo["total_de_partes"] == 4


def test_normalizar_modelo_limita_as_partes_e_recusa_cena_vazia():
    muitas = {"partes": [{"forma": "esfera"}] * (criacao_visual.MAX_PARTES + 50)}
    assert len(normalizar_modelo(muitas)["partes"]) == criacao_visual.MAX_PARTES
    with pytest.raises(ErroDeCriacao):
        normalizar_modelo({"partes": [{"forma": "dragao"}]})


# ------------------------------------------------------------------ o agente
def test_cria_a_imagem_e_manda_ao_painel(tmp_path):
    claude = IAFalsa("Claude", ["```svg\n" + SVG_BOM + "\n```"])
    criacao, eventos, avisos = agente(tmp_path, claude)
    resposta = criacao.responder("cria uma imagem de um farol à noite")
    assert resposta.startswith("Desenhando um farol à noite com o Claude")
    assert [e["tipo"] for e in eventos] == ["criacao_iniciada", "criacao"]
    final = eventos[-1]
    assert final["formato"] == "imagem" and final["titulo"] == "Farol à noite" and final["svg"].startswith("<svg")
    assert final["arquivo"].endswith(".svg") and os.path.isfile(final["arquivo"])
    assert avisos == [("Pronto: Farol à noite. Está no centro da tela.", "voz", [])]
    assert claude.pedidos[0]["prompt"] == "Desenhe: um farol à noite" and claude.pedidos[0]["formato"] is None


def test_cria_o_modelo_3d(tmp_path):
    claude = IAFalsa("Claude", [json.dumps(CENA_BOA)])
    criacao, eventos, avisos = agente(tmp_path, claude)
    assert criacao.responder("cria um modelo 3D de uma casa").startswith("Modelando uma casa em 3D")
    final = eventos[-1]
    assert final["formato"] == "3d" and len(final["modelo"]["partes"]) == 2 and final["arquivo"].endswith(".json")
    assert claude.pedidos[0]["formato"] == "json"
    assert "Uma casa com telhado vermelho." in avisos[0][0]


def test_resposta_que_nao_serve_e_pedida_de_novo(tmp_path):
    claude = IAFalsa("Claude", ["Desculpe, não consigo.", SVG_BOM])
    criacao, eventos, avisos = agente(tmp_path, claude)
    criacao.responder("desenha um farol")
    assert len(claude.pedidos) == 2 and "não serviu" in claude.pedidos[1]["prompt"]
    assert eventos[-1]["tipo"] == "criacao"


def test_sem_o_claude_o_gemini_faz_e_ela_avisa(tmp_path):
    claude = IAFalsa("Claude", [NuvemIndisponivel("limite")])
    gemini = IAFalsa("Gemini", [SVG_BOM])
    criacao, eventos, avisos = agente(tmp_path, claude, gemini)
    criacao.responder("desenha um farol")
    assert eventos[-1]["ia"] == "Gemini" and "quem fez foi o Gemini" in avisos[0][0]


def test_sem_nenhuma_ia_a_criacao_falha_com_aviso(tmp_path):
    criacao, eventos, avisos = agente(tmp_path, IAFalsa("Claude", [NuvemIndisponivel("x")]), IAFalsa("Gemini", [NuvemIndisponivel("y")]))
    criacao.responder("desenha um farol")
    assert eventos[-1]["tipo"] == "criacao_falhou" and "não responderam" in avisos[0][0]
    assert criacao.criando is None  # dá para pedir de novo


def test_ajuste_leva_a_versao_atual_e_a_mudanca(tmp_path):
    claude = IAFalsa("Claude", [SVG_BOM, SVG_BOM.replace("#123", "#00f")])
    criacao, eventos, avisos = agente(tmp_path, claude)
    criacao.responder("cria uma imagem de um farol")
    assert criacao.responder("muda a cor do fundo para azul").startswith("Mexendo na imagem")
    pedido = claude.pedidos[1]["prompt"]
    assert "Pedido original: um farol" in pedido and "#123" in pedido and "Mudança pedida: muda a cor do fundo para azul" in pedido
    assert avisos[-1][0] == "Pronto, mudei a imagem."
    assert criacao.responder("coloca ela na minha agenda amanhã") is None  # outro assunto


def test_ajuste_so_vale_logo_depois(tmp_path, monkeypatch):
    criacao, _, _ = agente(tmp_path, IAFalsa("Claude", [SVG_BOM]))
    assert criacao.responder("deixa ele azul") is None  # nada foi criado ainda
    criacao.responder("desenha um farol")
    criacao.ultima.quando -= criacao_visual.SEGUNDOS_PARA_AJUSTAR + 1
    assert criacao.responder("deixa ele azul") is None


def test_fechar_e_mostrar_de_novo(tmp_path):
    criacao, eventos, _ = agente(tmp_path, IAFalsa("Claude", [SVG_BOM]))
    assert criacao.responder("fecha a imagem") is None  # nada mostrado: o pedido segue (pode ser outro programa)
    criacao.responder("desenha um farol")
    assert "esfera voltou" in criacao.responder("fecha a imagem")
    assert eventos[-1]["tipo"] == "criacao_fechar"
    assert criacao.responder("mostra a imagem de novo").startswith("Aí está a imagem de novo")
    assert eventos[-1]["tipo"] == "criacao" and eventos[-1]["svg"]
    assert "esfera voltou" in criacao.responder("volta a esfera")


def test_pelo_celular_o_arquivo_volta_junto(tmp_path):
    criacao, eventos, avisos = agente(tmp_path, IAFalsa("Claude", [SVG_BOM]))
    criacao.responder("desenha um farol", canal="app")
    texto, canal, arquivos = avisos[0]
    assert canal == "app" and arquivos == [eventos[-1]["arquivo"]] and "painel do PC" in texto


def test_assunto_vazio_pergunta_de_que(tmp_path):
    criacao, eventos, _ = agente(tmp_path)
    assert criacao.responder("cria uma imagem").startswith("Uma imagem de quê?")
    assert eventos == []


def test_painel_grava_o_glb_so_na_pasta_das_criacoes(tmp_path, monkeypatch):
    import painel_web

    monkeypatch.setattr(painel_web, "PASTA_DOCUMENTOS_GERADOS", str(tmp_path))
    pasta = tmp_path / "2026-10-27_criacoes"
    pasta.mkdir()
    cena = pasta / "casa.json"
    cena.write_text("{}", encoding="utf-8")
    glb = base64.b64encode(b"glTF" + b"\0" * 20).decode()
    destino = painel_web.ApiPainel.criacao_glb(None, str(cena), glb)
    assert destino.endswith("casa.glb") and open(destino, "rb").read().startswith(b"glTF")
    fora = tmp_path / "outra.json"
    fora.write_text("{}", encoding="utf-8")
    assert painel_web.ApiPainel.criacao_glb(None, str(fora), glb) == ""
    assert painel_web.ApiPainel.criacao_glb(None, str(cena), base64.b64encode(b"MZ executavel").decode()) == ""
