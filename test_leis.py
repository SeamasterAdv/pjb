"""Testes do agente de leis com uma página do Planalto falsa e uma IA falsa (sem internet e sem nuvem)."""
import pytest

import agente_leis
from agente_leis import (AgenteLeis, TextoDasLeis, ano_estimado, candidatos_planalto, interpretar, linhas_do_html,
                         texto_para_falar, trecho_do_artigo, trecho_do_inciso)
from agenda_linguagem import preparar

# Parecida com o HTML do Planalto: Windows-1252, tags malformadas, quebras de linha no meio das
# frases e o texto revogado riscado.
PAGINA = """<html><head><meta charset="iso-8859-1"><title>L8078</title></head><body>
<p align="center"><b>CÓDIGO DE TESTE</b></p>
<p>Dispõe sobre a proteção do consumidor e dá outras providências.</p>
<b><p></b><font>Art. 1º Este código estabelece normas de proteção
e defesa do consumidor.</font></p>
<p>Art. 13 - O resultado depende da ação.</p>
<p><strike>Art. 49. O consumidor pode desistir em 3 dias.</strike></p>
<p>Art. 49. O consumidor pode desistir do contrato, no prazo de 7 dias a contar de sua
assinatura. (Redação dada pela Lei nº 9.999, de 2020)</p>
<p>Parágrafo único. Os valores pagos serão devolvidos.</p>
<p>Art. 49-A. Regra incluída depois. (Incluído pela Lei nº 9.999, de 2020)</p>
<p>CAPÍTULO II</p>
<p>Art. 50. A garantia contratual é complementar à legal.</p>
<p>Art. 51. São nulas as cláusulas:</p>
<p>I – que impossibilitem a indenização;</p>
<p>XI – que permitam ao fornecedor cancelar o contrato sozinho;</p>
<p>§ 1º Presume-se exagerada a vantagem que ofende os princípios.</p>
<p>Art. 52. Pena – reclusão, de um a dois anos.</p>
</body></html>""".encode("cp1252")


def agente_falso(explicacoes=None, relogio=None):
    pedidos = []

    def explicar(prompt, contexto):
        pedidos.append(prompt)
        return (explicacoes or ["Explicação número {}."])[min(len(pedidos), len(explicacoes or [1])) - 1].format(len(pedidos))

    baixados = []

    def baixar(url):
        baixados.append(url)
        if "l8078" not in url and "constituicao" not in url and "l14181" not in url:
            raise RuntimeError("404")
        return PAGINA

    agente = AgenteLeis(textos=TextoDasLeis(baixar=baixar), explicador=explicar, **({"relogio": relogio} if relogio else {}))
    return agente, pedidos, baixados


# ------------------------------------------------------------- página
def test_le_a_pagina_sem_o_texto_riscado_e_sem_quebrar_frases():
    linhas = linhas_do_html(PAGINA)
    assert "Art. 1º Este código estabelece normas de proteção e defesa do consumidor." in linhas
    assert not any("3 dias" in linha for linha in linhas)  # revogado (riscado)
    assert "I – que impossibilitem a indenização;" in linhas  # travessão do Windows-1252


def test_recorta_o_artigo_certo():
    linhas = linhas_do_html(PAGINA)
    artigo = trecho_do_artigo(linhas, "49")
    assert artigo[0].startswith("Art. 49. O consumidor pode desistir do contrato")
    assert artigo[-1] == "Parágrafo único. Os valores pagos serão devolvidos."  # para antes do 49-A
    assert trecho_do_artigo(linhas, "49-A") == ["Art. 49-A. Regra incluída depois. (Incluído pela Lei nº 9.999, de 2020)"]
    assert trecho_do_artigo(linhas, "13") == ["Art. 13 - O resultado depende da ação."]
    assert trecho_do_artigo(linhas, "50") == ["Art. 50. A garantia contratual é complementar à legal."]  # para no CAPÍTULO
    assert trecho_do_artigo(linhas, "5") is None
    assert trecho_do_inciso(trecho_do_artigo(linhas, "51"), "11") == ["XI – que permitam ao fornecedor cancelar o contrato sozinho;"]


def test_texto_para_falar():
    linhas = linhas_do_html(PAGINA)
    fala = texto_para_falar(trecho_do_artigo(linhas, "49"))
    assert fala.startswith("Artigo 49. O consumidor pode desistir")
    assert "Redação dada" not in fala
    falado = texto_para_falar(trecho_do_artigo(linhas, "51"))
    assert "Inciso 11: que permitam" in falado and "Parágrafo 1º Presume-se" in falado
    assert texto_para_falar(trecho_do_artigo(linhas, "52")) == "Artigo 52. Pena, reclusão, de um a dois anos."


# ------------------------------------------------------------ pedidos
@pytest.mark.parametrize("frase, esperado", [
    ("me explica o artigo 5 da constituição", ("cf", "5", "", False, True)),
    ("dita o artigo 49 do código de defesa do consumidor", ("cdc", "49", "", True, True)),
    ("lê o inciso XI do artigo quinto da constituição", ("cf", "5", "xi", True, True)),
    ("o que diz o artigo 121-A do código penal?", ("cp", "121-A", "", True, True)),
    ("explica o artigo 1.228 do código civil", ("cc", "1.228", "", False, True)),
    ("o que diz a LGPD?", ("lgpd", "", "", False, True)),
    ("explica o artigo 7 da lei 13.709", ("lgpd", "7", "", False, True)),
    ("explica o artigo 1 da lei 14.181 de 2021", ("lei_14181", "1", "", False, True)),
])
def test_interpreta_os_pedidos(frase, esperado):
    pedido = interpretar(preparar(frase)[1])
    assert (pedido.norma.chave, pedido.artigo, pedido.inciso, pedido.ler, pedido.explicar) == esperado


@pytest.mark.parametrize("frase", ["faz um PDF sobre o artigo 5 da constituição", "a CLT é muito antiga",
                                   "qual a previsão do tempo", "faz um resumo da lei maria da penha"])
def test_ignora_o_que_nao_e_dele(frase):
    assert interpretar(preparar(frase)[1]) is None


def test_endereco_das_leis_fora_do_catalogo():
    assert 2020 <= ano_estimado(14181) <= 2022
    assert "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14181.htm" in candidatos_planalto("14181")
    assert candidatos_planalto("14181", "2021") == ["https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14181.htm"]
    assert candidatos_planalto("9099")[-1] == "https://www.planalto.gov.br/ccivil_03/leis/l9099.htm"
    assert "https://www.planalto.gov.br/ccivil_03/leis/2003/l10.741.htm" in candidatos_planalto("10741", "2003")


# ------------------------------------------------------------- agente
def test_dita_e_depois_explica_com_a_ia():
    agente, pedidos, _ = agente_falso()
    resposta = agente.responder("Beatriz, dita o artigo 49 do código de defesa do consumidor")
    assert resposta.leitura.startswith("O artigo 49 do Código de Defesa do Consumidor, segundo o texto oficial: Artigo 49.")
    assert resposta.explicar() == "Explicação número 1."
    assert "Texto oficial de o artigo 49 do Código de Defesa do Consumidor" in pedidos[0]
    assert "7 dias" in pedidos[0] and "Redação dada" not in pedidos[0]  # a IA recebe o texto limpo


def test_explicar_sem_ler_fala_uma_frase_curta_enquanto_a_ia_escreve():
    agente, _, _ = agente_falso()
    resposta = agente.responder("me explica o artigo 50 do código de defesa do consumidor")
    assert "artigo 50 do Código de Defesa do Consumidor" in resposta.leitura and len(resposta.leitura) < 70
    assert "Art. 50. A garantia contratual" in resposta.painel
    assert resposta.explicar()


def test_nao_entendi_explica_de_outro_jeito_sem_repetir():
    agente, pedidos, _ = agente_falso(["Primeira explicação.", "Segunda, com analogia.", "Terceira, historinha."])
    agente.responder("explica o artigo 49 do cdc").explicar()
    segunda = agente.responder("não entendi")
    assert segunda.explicar() == "Segunda, com analogia."
    assert "Primeira explicação." in pedidos[1] and "Não repita" in pedidos[1]
    assert agente_leis.ESTRATEGIAS[1] in pedidos[1]
    terceira = agente.responder("ainda não entendi, explica de outro jeito")
    assert terceira.explicar() == "Terceira, historinha."
    assert agente_leis.ESTRATEGIAS[2] in pedidos[2] and "Segunda, com analogia." in pedidos[2]


def test_outra_conversa_no_meio_desliga_o_nao_entendi():
    agente, _, _ = agente_falso()
    agente.responder("explica o artigo 49 do cdc").explicar()
    assert agente.responder("qual a previsão do tempo?") is None  # outra rota respondeu
    assert agente.responder("não entendi") is None
    assert not agente.e_seguimento("não entendi")


def test_assunto_expira():
    agora = [1000.0]
    agente, _, _ = agente_falso(relogio=lambda: agora[0])
    agente.responder("explica o artigo 49 do cdc").explicar()
    assert agente.e_seguimento("não entendi")
    agora[0] += 16 * 60
    assert agente.responder("não entendi") is None


def test_artigo_longo_le_aos_poucos(monkeypatch):
    monkeypatch.setattr(agente_leis, "LIMITE_LEITURA", 60)
    agente, _, _ = agente_falso()
    primeira = agente.responder("lê o artigo 51 do cdc")
    assert "Diga continua" in primeira.leitura and "Inciso 11" not in primeira.leitura
    assert agente.e_seguimento("continua")
    segunda = agente.responder("continua")
    assert "Inciso" in segunda.leitura and segunda.explicar is None
    while "Diga continua" in agente.responder("continua").leitura:
        pass
    assert not agente.e_seguimento("continua")  # acabou o texto
    assert agente.responder("lê de novo").leitura.startswith("O artigo 51")


def test_artigo_sem_lei_pergunta_qual():
    agente, _, _ = agente_falso()
    assert agente.responder("explica o artigo 49").leitura.startswith("O artigo 49 de qual lei?")
    assert "artigo 49 do Código de Defesa do Consumidor" in agente.responder("do código de defesa do consumidor").leitura


def test_artigo_sem_lei_usa_a_do_assunto():
    agente, _, _ = agente_falso()
    agente.responder("explica o artigo 49 do cdc").explicar()
    assert "artigo 50 do Código de Defesa do Consumidor" in agente.responder("e o artigo 50?").leitura


def test_lei_fora_do_catalogo_tenta_os_enderecos():
    agente, _, baixados = agente_falso()
    resposta = agente.responder("explica o artigo 1 da lei 14.181")
    assert "artigo 1 da Lei 14.181" in resposta.leitura
    assert baixados[-1].endswith("/2021/lei/l14181.htm")


def test_artigo_que_nao_existe():
    agente, _, _ = agente_falso()
    assert agente.responder("explica o artigo 999 do cdc").leitura == \
        "Não achei o artigo 999 no Código de Defesa do Consumidor. Confere o número pra mim?"


def test_main_le_primeiro_e_depois_explica(monkeypatch):
    import main

    agente, _, _ = agente_falso()
    monkeypatch.setattr(main, "LEIS", agente)
    falas = []
    monkeypatch.setattr(main, "falar_e_registrar",
                        lambda pergunta, resposta, texto_painel="", canal=None: falas.append((resposta, texto_painel)))
    main.responder_lei("dita o artigo 49 do cdc", main.LEIS.responder("dita o artigo 49 do cdc"))
    assert falas[0][0].startswith("O artigo 49 do Código de Defesa do Consumidor")
    assert falas[0][1].startswith("Art. 49.")  # o painel (e o WhatsApp) recebe o texto oficial inteiro
    assert falas[1] == ("Explicação número 1.", "")


def test_nao_entendi_no_meio_da_conversa_nem_passa_pela_ia_local(monkeypatch):
    import collections
    import time

    import main

    agente, _, _ = agente_falso()
    agente.responder("explica o artigo 49 do cdc").explicar()
    avaliadas = []
    monkeypatch.setattr(main, "LEIS", agente)
    monkeypatch.setattr(main, "ESTADO_ATIVACAO", main.EstadoAtivacao())
    monkeypatch.setattr(main, "CONFIRMACAO", main.Confirmacao())
    monkeypatch.setattr(main, "REGISTRO_CONVERSA", collections.deque(maxlen=600))
    monkeypatch.setattr(main, "EXIGIR_PALAVRA_ATIVACAO", True)
    monkeypatch.setattr(main, "JANELA_CONVERSA_SEGUNDOS", 40.0)
    monkeypatch.setattr(main, "falou_durante", lambda inicio, fim: False)
    monkeypatch.setattr(main, "fim_da_ultima_fala", lambda: 0.0)
    monkeypatch.setattr(main, "PAINEL_VOZ", None)
    monkeypatch.setattr(main, "avaliar_fala_sem_nome", lambda texto, em_conversa=False: avaliadas.append(texto) or "conversa")
    main.abrir_janela_continuacao(main.ESTADO_ATIVACAO, time.time() - 20, "explicação")
    assert main.filtrar_fala_por_ativacao("não entendi", 1.0, 2.0) == "não entendi"
    assert avaliadas == []


def test_sem_internet_explica_o_problema():
    agente = AgenteLeis(textos=TextoDasLeis(baixar=lambda url: (_ for _ in ()).throw(OSError("sem rede"))),
                        explicador=lambda p, c: "")
    assert agente.responder("explica o artigo 49 do cdc").leitura.startswith("Não consegui pegar o texto da lei agora")
