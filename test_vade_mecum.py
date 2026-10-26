"""Testes do vade-mécum (vade_mecum.py) e do agente de leis com ele: banco temporário, páginas falsas, IA falsa."""
import pytest

import agente_leis
import vade_mecum
from agente_leis import AgenteLeis, TextoDasLeis, linhas_do_html, norma_por_texto, pedido_de_busca, pedido_de_sumula
from agenda_linguagem import preparar
from vade_mecum import VadeMecum

PLANALTO = "https://www.planalto.gov.br/ccivil_03"

PAGINA_CP = """<html><body>
<p>CÓDIGO PENAL</p>
<p>Art. 23 - Não há crime quando o agente pratica o fato:</p>
<p>I - em estado de necessidade;</p>
<p>II - em legítima defesa;</p>
<p>Art. 25 - Entende-se em legítima defesa quem, usando moderadamente dos meios necessários, repele injusta agressão.</p>
<p>TÍTULO V</p>
<p>Art. 65 - São circunstâncias que sempre atenuam a pena:</p>
<p>III - ter o agente: d) confessado espontaneamente, perante a autoridade, a autoria do crime;</p>
<p>Art. 121. Matar alguém: Pena - reclusão, de seis a vinte anos.</p>
<p>Art. 121-A. Regra incluída depois.</p>
</body></html>""".encode("cp1252")

PAGINA_CF = """<html><body>
<p>CONSTITUIÇÃO DA REPÚBLICA FEDERATIVA DO BRASIL</p>
<p>Art. 5º Todos são iguais perante a lei, sem distinção de qualquer natureza.</p>
<p>Art. 37. A administração pública obedecerá aos princípios de legalidade e eficiência.</p>
<p>ATO DAS DISPOSIÇÕES CONSTITUCIONAIS TRANSITÓRIAS</p>
<p>Art. 1º O Presidente da República prestará o compromisso.</p>
<p>Art. 5º Não se aplicam às eleições previstas para 1988 as regras do art. 16.</p>
</body></html>""".encode("utf-8")

LISTA_STF = """<div class="sumula-item"><a target="_blank" href="sumariosumulas.asp?base=26&sumula=1185"> Súmula Vinculante&nbsp;1 </a></div>
<div class="sumula-item"><a target="_blank" href="sumariosumulas.asp?base=26&sumula=1220"> Súmula Vinculante 11 </a></div>
<div class="sumula-item"><a target="_blank" href="sumariosumulas.asp?base=26&sumula=1300"> Súmula Vinculante 30 <em>(cance​lada)</em></a></div>"""

LISTA_STF_COMUNS = """<div class="sumula-item"><a target="_blank" href="sumariosumulas.asp?base=30&sumula=1451"> Súmula 1 </a></div>"""

PAGINA_SV11 = """<section><div class="titulo">
	Súmula Vinculante 11
</div><div class="parCOM"><p>
	Só é lícito o uso de algemas em casos de resistência e de fundado receio de fuga.</p>
</div><div class="titulo">
	Precedentes Representativos
</div><div class="parCOM"><p>
	O uso legítimo de algemas não é arbitrário, sendo de natureza excepcional.<br />
	[<strong><a href="x">HC&nbsp;89.429</a></strong>, rel. min. Cármen Lúcia, 1ª&nbsp;T, j. 22-8-2006, <em>DJ</em> de 2-2-2007.]</p>
</div><div class="titulo">
	Jurisprudência selecionada
</div><div class="parCOM"><div>
	<em><strong>Condições adicionais para o uso de algemas por menor</strong></em></div>
<div>
	A turma fixou condições para o uso de algemas por menor apreendido.</div>
<div>
	[<a href="y"><strong>Rcl 61.876</strong></a>, rel. min. <strong>Cármen Lúcia</strong>, 1ª T, j. 7-5-2024.]</div>
</div><div class="titulo">
	Observação
</div><div class="parCOM"><p>
	&#9679; A PSV 13 foi rejeitada.<br />
	Para informações adicionais,&nbsp;<a href="z">clique aqui</a>.</p>
</div></section><footer>rodapé</footer>"""

PAGINA_SUMULA_1 = """<div class="titulo"> Súmula 1 </div><div class="parCOM"><p>É vedada a expulsão de estrangeiro casado
com brasileira.</p></div><div class="titulo"> Jurisprudência selecionada </div><div class="parCOM"><p>
<strong>&#9679; Condições para expulsão</strong><br />Recurso não provido.<br />[RHC 122.682, rel. min. Dias Toffoli, 1ª T, j. 30-9-2014.]</p></div>"""

TEXTO_STJ = """ \n G SÚMULA 676\nVEJA MAIS\nEm razão da Lei n. 13.964/2019, não é mais possível ao juiz, de ofício, decretar
ou converter prisão em flagrante em prisão preventiva. (TERCEIRA SEÇÃO, julgado em 11/12/2024, DJe de 17/12/2024)
scon.stj.jus.br/SCON/sumstj/\n66\n\nG SÚMULA 545 (SÚMULA ALTERADA)\nVEJA MAIS\nA confissão do autor possibilita a
atenuação da pena prevista no art. 65, III, d, do Código Penal. (TERCEIRA SEÇÃO, julgado em 10/9/2025, DJEN de 2/12/2025)
SÚMULA REVISADA: A Terceira Seção deliberou pela REVISÃO do enunciado.
G SÚMULA 152 (SÚMULA CANCELADA)\nVEJA MAIS\nNa venda pelo segurador, de bens salvados de sinistros, incide o ICMS.
(SÚMULA 152, PRIMEIRA SEÇÃO, DJ 14/03/1996, p. 7115) SÚMULA CANCELADA: A Primeira Seção determinou o CANCELAMENTO.
G SÚMULA 37\nVEJA MAIS\nSão cumuláveis as indenizações por dano material e dano moral oriundos do mesmo fato.
(SÚMULA 37, CORTE ESPECIAL, julgado em 12/03/1992, DJ 17/03/1992, p. 3172)"""


def registro_tst(tipo, numero, tese, situacao="CRIADA", orgao="", titulo="TÍTULO."):
    return {"registro": {"tipo": {"codigoTipoJurisprudencia": tipo}, "numero": numero, "titulo": titulo, "tese": tese,
                         "situacao": {"descricao": situacao}, "observacao": "(mantida)", "dtaPublicacao": "2003-11-21",
                         "orgaoJudicante": {"sigla": orgao}, "precedentes": [
                             {"numeracao": "RR - 1-00.2000.5.00.0000", "orgaoJudicante": {"descricao": "2ª Turma"},
                              "relator": {"nome": "Fulano"}, "dtaPublicacao": "2001-01-01"}]}}


class BaixadorFalso:
    def __init__(self, paginas=None, json=None):
        self.paginas = paginas or {}
        self.respostas = json or {}
        self.pedidos = []

    def bytes(self, url, referer=""):
        self.pedidos.append(url)
        if url not in self.paginas:
            raise OSError("404")
        conteudo = self.paginas[url]
        return conteudo.encode("utf-8") if isinstance(conteudo, str) else conteudo

    def texto(self, url, referer=""):
        return self.bytes(url, referer).decode("utf-8")

    def json(self, url, corpo):
        self.pedidos.append(url)
        return self.respostas[(corpo["tipos"][0], url.rsplit("/", 2)[-2])]


@pytest.fixture
def vade(tmp_path):
    return VadeMecum(str(tmp_path / "vade.sqlite3"))


def vade_com_sumulas(vade):
    vade.guardar_sumula("stj", "sumula", 37, "São cumuláveis as indenizações por dano material e dano moral oriundos do "
                        "mesmo fato.")
    vade.guardar_sumula("stj", "sumula", 545, "A confissão do autor possibilita a atenuação da pena prevista no art. 65, "
                        "III, d, do Código Penal.")
    vade.guardar_sumula("stj", "sumula", 227, "A pessoa jurídica pode sofrer dano moral.")
    vade.guardar_sumula("stj", "sumula", 61, "O seguro de vida cobre o suicídio não premeditado sem dano moral.",
                        situacao="cancelada")
    vade.guardar_sumula("stf", "vinculante", 11, "Só é lícito o uso de algemas em casos de resistência.", julgados=[
        {"secao": "Precedentes", "titulo": "", "texto": "O uso legítimo de algemas não é arbitrário.",
         "referencia": "HC 89.429, rel. min. Cármen Lúcia, 1ª T, j. 22-8-2006"}])
    vade.guardar_sumula("tst", "sumula", 331, "I - A contratação de trabalhadores por empresa interposta é ilegal. "
                        "II - A contratação irregular não gera vínculo.", titulo="CONTRATO DE PRESTAÇÃO DE SERVIÇOS.")
    vade.guardar_sumula("tst", "oj", 191, "Diante da inexistência de previsão legal, o dono da obra não responde.",
                        orgao="SDI1")
    return vade


def agente_com(vade, respostas=None, paginas=None):
    pedidos = []

    def explicar(prompt, contexto):
        pedidos.append(prompt)
        return (respostas or ["Resposta {}."])[min(len(pedidos), len(respostas or [1])) - 1].format(len(pedidos))

    baixados = []

    def baixar(url):
        baixados.append(url)
        if paginas and url in paginas:
            return paginas[url]
        raise OSError("sem internet")

    agente = AgenteLeis(textos=TextoDasLeis(baixar=baixar), explicador=explicar, vade=vade)
    return agente, pedidos, baixados


# ------------------------------------------------------------- leitura das fontes
def test_artigos_da_norma_e_a_letra_colada():
    linhas = ["Art. 136 - A época da concessão das férias.", "Art. 136-A. Regra nova.", "§ 1º Parágrafo.",
              "CAPÍTULO II", "Texto solto do capítulo.", "Art. 1.228. O proprietário."]
    assert [(n, t) for n, t in vade_mecum.artigos_da_norma(linhas)] == [
        ("136", ["Art. 136 - A época da concessão das férias."]), ("136-A", ["Art. 136-A. Regra nova.", "§ 1º Parágrafo."]),
        ("1.228", ["Art. 1.228. O proprietário."])]


def test_constituicao_separada_do_adct():
    constituicao, adct = vade_mecum.separar_constituicao(linhas_do_html(PAGINA_CF))
    assert constituicao[-1].startswith("Art. 37.") and adct[0].startswith("ATO DAS DISPOSIÇÕES")
    assert adct[-1].startswith("Art. 5º Não se aplicam")


def test_paginas_em_utf16_e_artigo_com_espaco_antes_do_ponto():
    pagina = "<html><p>Art . 1º O cheque contém:</p><p>Art. 2º Outro.</p></html>".encode("utf-16")
    assert linhas_do_html(pagina) == ["Art. 1º O cheque contém:", "Art. 2º Outro."]


def test_listagem_e_pagina_do_stf():
    assert vade_mecum.itens_da_listagem_do_stf(LISTA_STF, 26) == [("1185", 1, "vigente"), ("1220", 11, "vigente"),
                                                                  ("1300", 30, "cancelada")]
    dados = vade_mecum.sumula_da_pagina_do_stf(PAGINA_SV11)
    assert dados["enunciado"] == "Só é lícito o uso de algemas em casos de resistência e de fundado receio de fuga."
    assert dados["observacao"] == "● A PSV 13 foi rejeitada."  # sem o "clique aqui"
    primeiro, segundo = dados["julgados"]
    assert primeiro == {"secao": "Precedentes Representativos", "titulo": "",
                        "texto": "O uso legítimo de algemas não é arbitrário, sendo de natureza excepcional.",
                        "referencia": "HC 89.429, rel. min. Cármen Lúcia, 1ª T, j. 22-8-2006, DJ de 2-2-2007."}
    assert segundo["titulo"] == "Condições adicionais para o uso de algemas por menor"  # o <div> dentro do bloco
    assert segundo["referencia"] == "Rcl 61.876, rel. min. Cármen Lúcia, 1ª T, j. 7-5-2024."


def test_pdf_dos_verbetes_do_stj():
    sumulas = {s["numero"]: s for s in vade_mecum.sumulas_do_pdf_do_stj(TEXTO_STJ)}
    assert set(sumulas) == {37, 152, 545, 676}
    assert sumulas[676]["texto"].startswith("Em razão da Lei n. 13.964/2019") and sumulas[676]["data"] == "11/12/2024"
    assert sumulas[545]["situacao"] == "vigente" and "SÚMULA REVISADA" in sumulas[545]["observacao"]
    assert sumulas[152]["situacao"] == "cancelada" and sumulas[152]["data"] == "14/03/1996"  # sem "julgado em"
    assert "scon.stj" not in sumulas[676]["texto"]


def test_texto_do_pdf():
    import pymupdf

    documento = pymupdf.open()
    documento.new_page().insert_text((72, 72), "G SUMULA 1\nVEJA MAIS\nTexto. (PRIMEIRA SECAO, julgado em 01/01/1990)")
    assert "VEJA MAIS" in vade_mecum.texto_do_pdf(documento.tobytes())


def test_registro_do_tst():
    oj = vade_mecum.sumula_do_registro_do_tst(registro_tst("OJ", 191, "O dono da obra não responde.", orgao="SDI1")["registro"])
    assert (oj["tipo"], oj["numero"], oj["orgao"], oj["situacao"]) == ("oj", 191, "SDI1", "vigente")
    assert oj["julgados"][0]["referencia"] == "RR - 1-00.2000.5.00.0000, 2ª Turma, rel. Fulano, publicado em 2001-01-01"
    cancelado = vade_mecum.sumula_do_registro_do_tst(registro_tst("PN", 1, "Não se concede.", situacao="CANCELADA")["registro"])
    assert (cancelado["tipo"], cancelado["orgao"], cancelado["situacao"]) == ("pn", "", "cancelada")
    # as transitórias da SDI-1 (OJT) não podem cobrir as súmulas nem as OJs de mesmo número
    transitoria = vade_mecum.sumula_do_registro_do_tst(registro_tst("OJT", 1, "FGTS.", orgao="SDI1")["registro"])
    assert (transitoria["tipo"], transitoria["orgao"]) == ("oj", "SDI1T")
    assert vade_mecum.sumula_do_registro_do_tst(registro_tst("XYZ", 1, "Novo tipo.")["registro"])["tipo"] == ""
    convertida = registro_tst("OJ", 4, "", situacao="CANCELADA", orgao="SDI1", titulo="ADICIONAL DE INSALUBRIDADE.")
    convertida["registro"]["observacao"] = "(cancelada em decorrência da sua conversão na Súmula nº 448)"
    convertida = vade_mecum.sumula_do_registro_do_tst(convertida["registro"])
    assert (convertida["texto"], convertida["situacao"]) == ("ADICIONAL DE INSALUBRIDADE.", "cancelada")  # sem a tese


# ------------------------------------------------------------- o banco
def test_guarda_a_norma_e_busca_pelo_assunto(vade):
    vade.guardar_norma("cp", "o Código Penal", "url", linhas_do_html(PAGINA_CP))
    assert vade.linhas("cp")[0][1].startswith("Art. 23")
    assert vade.total()["normas"] == 1 and vade.total()["artigos"] == 5
    achados = vade.buscar("legítima defesa", tipos=("artigos",))
    assert [a["numero"] for a in achados][:1] == ["25"] and achados[0]["rotulo"] == "o artigo 25 do Código Penal"
    assert vade.buscar("legitima defesa", tipos=("artigos",), norma="cc") == []
    # nenhum artigo tem todas as palavras: vale o que tem a maioria
    assert vade.buscar("legítima defesa xyzabc", tipos=("artigos",))[0]["numero"] in ("23", "25")


def test_guarda_a_sumula_com_os_julgados_e_busca(vade):
    vade_com_sumulas(vade)
    sv = vade.sumula("stf", "vinculante", 11)
    assert sv["julgados"][0]["referencia"].startswith("HC 89.429")
    assert vade.sumula("tst", "oj", 191)["orgao"] == "SDI1"  # sem dizer o órgão
    achados = vade.buscar("dano moral", tipos=("sumulas",))
    assert sorted(a["numero"] for a in achados[:2]) == [37, 227]
    assert (achados[-1]["numero"], achados[-1]["situacao"]) == (61, "cancelada")  # a cancelada no fim
    assert [a["numero"] for a in vade.buscar("dano moral", tipos=("sumulas",), tribunal="stf")] == []
    assert vade.total()["sumulas"] == {"stf_vinculante": 1, "stj_sumula": 4, "tst_oj": 1, "tst_sumula": 1}
    assert [s["numero"] for s in vade.sumulas_que_citam("65")] == [545]


def test_exemplos_e_atualizacoes(vade):
    vade.guardar_exemplo("artigo:cp:121", "Imagina que...")
    assert vade.exemplo("artigo:cp:121") == "Imagina que..." and vade.exemplo("outro") == ""
    assert vade_mecum.precisa_atualizar(vade, 15) == list(vade_mecum.PARTES)
    vade.registrar_atualizacao("stf", 799, [])
    vade.registrar_atualizacao("stj", 0, ["STJ: 503"])
    assert vade_mecum.precisa_atualizar(vade, 15) == ["normas", "tst", "stj"]  # o do STJ veio vazio


def test_descrever_total():
    total = {"normas": 113, "artigos": 15850,
             "sumulas": {"stf_sumula": 736, "stf_vinculante": 63, "stj_sumula": 676, "tst_sumula": 463, "tst_oj": 709}}
    assert vade_mecum.descrever_total(total) == ("113 normas (15.850 artigos), 799 súmulas do STF, 676 do STJ e 1.172 "
                                                 "súmulas, OJs e precedentes do TST")


# ------------------------------------------------------------- downloads
def test_atualiza_tudo_com_um_baixador_falso(vade):
    from agente_leis import NORMAS

    normas = [n for n in NORMAS if n.chave in ("cf", "cp")]
    paginas = {f"{PLANALTO}/constituicao/constituicao.htm": PAGINA_CF,
               # o endereço do catálogo saiu do ar: vale o provável
               f"{PLANALTO}/decreto-lei/del2848.htm": PAGINA_CP,
               f"{vade_mecum.STF_PORTAL}?base=30": LISTA_STF_COMUNS, f"{vade_mecum.STF_PORTAL}?base=26": LISTA_STF,
               f"{vade_mecum.STF_PORTAL}?base=30&sumula=1451": PAGINA_SUMULA_1,
               f"{vade_mecum.STF_PORTAL}?base=26&sumula=1185": PAGINA_SUMULA_1.replace("Súmula 1", "Súmula Vinculante 1"),
               f"{vade_mecum.STF_PORTAL}?base=26&sumula=1220": PAGINA_SV11}
    respostas = {("SUM", "1"): {"totalRegistros": 1, "registros": [registro_tst("SUM", 331, "I - Ilegal.")]},
                 ("OJ", "1"): {"totalRegistros": 2, "registros": [registro_tst("OJ", 191, "Não responde.", orgao="SDI1")]},
                 ("OJ", "2"): {"totalRegistros": 2, "registros": [registro_tst("OJ", 1, "Cautelar.", orgao="SDI2")]},
                 ("PN", "1"): {"totalRegistros": 0, "registros": []}}
    baixador = BaixadorFalso(paginas, respostas)
    import pymupdf

    documento = pymupdf.open()
    pagina = documento.new_page(width=900, height=1200)
    pagina.insert_text((20, 40), TEXTO_STJ.replace("Ú", "U").replace("Ç", "C").replace("Ã", "A"), fontsize=6)
    baixador.paginas[vade_mecum.STJ_VERBETES] = documento.tobytes()

    assert vade_mecum.atualizar_normas(vade, baixador, normas=normas) == (2, [])
    assert {n["chave"] for n in vade.normas()} == {"cf", "adct", "cp"}
    assert vade.linhas("adct")[0][-1].startswith("Art. 5º Não se aplicam")

    feitas, erros = vade_mecum.atualizar_stf(vade, baixador)
    assert feitas == 3 and erros == ["STF Súmula Vinculante 30: 404"]
    assert vade.sumula("stf", "vinculante", 11)["julgados"][1]["titulo"].startswith("Condições adicionais")

    assert vade_mecum.atualizar_tst(vade, baixador, por_pagina=1) == (3, [])
    assert vade.sumula("tst", "oj", 1, "SDI2")["texto"] == "Cautelar."

    feitas, erros = vade_mecum.atualizar_stj(vade, baixador)
    assert feitas == 4 and vade.sumula("stj", "sumula", 152)["situacao"] == "cancelada"


def test_atualizar_nao_roda_duas_vezes_e_registra(vade, monkeypatch):
    chamadas = []
    monkeypatch.setitem(vade_mecum.PARTES, "stj", lambda v, b, avisar=None: chamadas.append(1) or (5, ["um erro"]))
    resumo = vade_mecum.atualizar(vade, ("stj",), baixador=BaixadorFalso())
    assert resumo == {"stj": {"total": 5, "erros": ["um erro"]}}
    assert vade.atualizacoes()["stj"]["total"] == 5
    with vade_mecum._ATUALIZANDO:
        assert vade_mecum.atualizar(vade, ("stj",), baixador=BaixadorFalso()) is None
    assert chamadas == [1]


# ------------------------------------------------------------- pedidos
@pytest.mark.parametrize("frase, esperado", [
    ("súmula 7 do STJ", ("stj", "sumula", 7, "")),
    ("me explica a súmula vinculante 11", ("stf", "vinculante", 11, "")),
    ("a súmula 331 do TST ainda vale?", ("tst", "sumula", 331, "")),
    ("OJ 191 da SDI-1", ("tst", "oj", 191, "SDI1")),
    ("orientação jurisprudencial 1 da SBDI-2", ("tst", "oj", 1, "SDI2")),
    ("OJ transitória 1 da SDI-1", ("tst", "oj", 1, "SDI1T")),
    ("precedente normativo 119", ("tst", "pn", 119, "")),
    ("súmula 473", ("", "sumula", 473, "")),
])
def test_pedido_de_sumula(frase, esperado):
    pedido = pedido_de_sumula(preparar(frase)[1])
    assert (pedido["tribunal"], pedido["tipo"], pedido["numero"], pedido["orgao"]) == esperado


@pytest.mark.parametrize("frase, esperado", [
    ("tem súmula sobre dano moral?", ("dano moral", ("sumulas",), "", None)),
    ("quais súmulas do STJ falam de plano de saúde", ("plano de saude", ("sumulas",), "stj", None)),
    ("qual artigo do código civil fala de usucapião?", ("usucapiao", ("artigos",), "", "cc")),
    ("em que artigo está a legítima defesa no código penal", ("legitima defesa", ("artigos",), "", "cp")),
    ("procura no vade-mécum prisão em flagrante", ("prisao em flagrante", ("sumulas", "artigos"), "", None)),
])
def test_pedido_de_busca(frase, esperado):
    pedido = pedido_de_busca(preparar(frase)[1])
    assert (pedido["assunto"], pedido["tipos"], pedido["tribunal"], pedido["norma"] and pedido["norma"].chave) == esperado


@pytest.mark.parametrize("frase", ["faz um PDF sobre a súmula 7 do STJ", "súmula 7 do STJ", "explica o artigo 5 da CF",
                                   "qual a previsão do tempo"])
def test_nao_e_busca(frase):
    assert pedido_de_busca(preparar(frase)[1]) is None


@pytest.mark.parametrize("texto, chave", [
    ("código de processo penal militar", "cppm"), ("código penal militar", "cpm"), ("código de processo penal", "cpp"),
    ("Lei Complementar 123", "microempresa"), ("LC 101", "responsabilidade_fiscal"), ("Lei 8.112/90", "servidores"),
    ("artigo 5 do ADCT", "adct"), ("Decreto 20.910/32", "prescricao_fazenda"),
])
def test_norma_citada(texto, chave):
    assert norma_por_texto(texto).chave == chave


def test_lei_fora_do_catalogo_com_ano_de_dois_digitos():
    norma = norma_por_texto("Lei 9.999/98")
    assert (norma.chave, norma.ano) == ("lei_9999", "1998")
    assert norma_por_texto("a lei 13 é antiga") is None  # número curto solto


# ------------------------------------------------------------- o agente
def test_le_a_lei_do_banco_sem_internet(vade):
    vade.guardar_norma("cp", "o Código Penal", "url", linhas_do_html(PAGINA_CP))
    agente, _, baixados = agente_com(vade)
    resposta = agente.responder("dita o artigo 25 do código penal")
    assert "Entende-se em legítima defesa" in resposta.leitura and baixados == []


def test_baixa_a_constituicao_e_guarda_o_adct_a_parte(vade):
    agente, _, baixados = agente_com(vade, paginas={f"{PLANALTO}/constituicao/constituicao.htm": PAGINA_CF})
    assert "Não se aplicam às eleições" in agente.responder("lê o artigo 5 do ADCT").leitura
    assert "Todos são iguais" in agente.responder("lê o artigo 5 da constituição").leitura
    assert len(baixados) == 1 and {n["chave"] for n in vade.normas()} == {"cf", "adct"}


def test_le_a_sumula_e_explica_com_os_casos_reais(vade):
    agente, pedidos, _ = agente_com(vade_com_sumulas(vade))
    resposta = agente.responder("Beatriz, me explica a súmula vinculante 11")
    assert resposta.leitura.startswith("A Súmula Vinculante 11 diz: Só é lícito o uso de algemas")
    assert "Casos em que foi aplicada:\n- HC 89.429" in resposta.painel
    assert resposta.explicar() == "Resposta 1."
    assert "fonte: portal do STF" in pedidos[0] and "O uso legítimo de algemas não é arbitrário" in pedidos[0]


def test_sumula_cancelada_avisa_e_responde_se_ainda_vale(vade):
    agente, _, _ = agente_com(vade_com_sumulas(vade))
    assert agente.responder("súmula 61 do STJ").leitura.endswith("Atenção: essa súmula foi cancelada.")
    assert agente.responder("a súmula 61 do STJ ainda vale?").leitura == "Não: a Súmula 61 do STJ foi cancelada."
    assert agente.responder("a súmula 37 do STJ ainda vale?").leitura == "Sim, a Súmula 37 do STJ continua valendo."


def test_sumula_sem_tribunal_pergunta_qual(vade):
    agente, _, _ = agente_com(vade_com_sumulas(vade))
    assert agente.responder("o que diz a súmula 331?").leitura == "A Súmula 331 de qual tribunal? Do STF, do STJ ou do TST?"
    assert agente.responder("do TST").leitura.startswith("A Súmula 331 do TST diz: Item 1: A contratação")


def test_sumula_que_falta(vade):
    agente, _, _ = agente_com(vade)
    assert "Diga \"atualiza o vade-mécum\"" in agente.responder("súmula 7 do STJ").leitura  # banco vazio
    vade_com_sumulas(vade)
    assert agente.responder("súmula 999 do STJ").leitura == "Não achei a Súmula 999 do STJ no vade-mécum. Confere o número pra mim?"


def test_busca_e_escolhe_da_lista(vade):
    agente, pedidos, _ = agente_com(vade_com_sumulas(vade))
    resposta = agente.responder("tem súmula sobre dano moral?")
    achadas = [r["numero"] for r in agente.busca.resultados]
    assert sorted(achadas[:2]) == [37, 227] and achadas[2] == 61  # a cancelada por último
    assert resposta.leitura.startswith(f"Achei 3 súmulas sobre dano moral. Primeira, a Súmula {achadas[0]} do STJ: ")
    assert "explica a primeira" in resposta.leitura and f"1. A Súmula {achadas[0]} do STJ" in resposta.painel
    assert "3. A Súmula 61 do STJ (cancelada)" in resposta.painel
    assert agente.e_seguimento("explica a segunda")
    segunda = agente.responder("explica a segunda")
    assert segunda.leitura.startswith(f"A Súmula {achadas[1]} do STJ diz: ")
    assert segunda.explicar() == "Resposta 1."


def test_busca_de_artigo_pelo_assunto_e_escolha(vade):
    vade.guardar_norma("cp", "o Código Penal", "url", linhas_do_html(PAGINA_CP))
    agente, _, _ = agente_com(vade)
    resposta = agente.responder("em que artigo do código penal está a legítima defesa?")
    assert resposta.leitura.startswith("Achei 2 artigos no Código Penal sobre legítima defesa. Primeiro, o artigo 25 do "
                                       "Código Penal: Entende-se em legítima defesa")  # a expressão inteira antes
    assert "explica o primeiro" in resposta.leitura
    assert "Entende-se em legítima defesa" in agente.responder("lê o primeiro").leitura


def test_oj_cancelada_diz_para_onde_foi(vade):
    vade.guardar_sumula("tst", "oj", 4, "ADICIONAL DE INSALUBRIDADE.", orgao="SDI1", situacao="cancelada",
                        observacao="(cancelada em decorrência da sua conversão na Súmula nº 448)")
    vade.guardar_sumula("tst", "pn", 1, "Não se concede antecipação salarial.", situacao="cancelada")
    agente, _, _ = agente_com(vade)
    assert agente.responder("a OJ 4 da SDI-1 ainda vale?").leitura == (
        "Não: a OJ 4 da SDI-1 do TST foi cancelada. (cancelada em decorrência da sua conversão na Súmula nº 448).")
    assert agente.responder("precedente normativo 1").leitura.endswith("Atenção: esse precedente foi cancelado.")


def test_artigo_mostra_as_sumulas_que_o_citam(vade):
    vade.guardar_norma("cp", "o Código Penal", "url", linhas_do_html(PAGINA_CP))
    agente, pedidos, _ = agente_com(vade_com_sumulas(vade))
    resposta = agente.responder("explica o artigo 65 do código penal")
    assert "Súmulas que citam este artigo:\n- A Súmula 545 do STJ" in resposta.painel
    resposta.explicar()
    assert "Súmulas dos tribunais que citam este dispositivo" in pedidos[0] and "Súmula 545" in pedidos[0]
    assert agente._sumulas_do_artigo(agente_leis.NORMA_CF, "65") == []  # o art. 65 citado é o do Código Penal


def test_exemplo_de_artigo_fica_guardado(vade):
    vade.guardar_norma("cp", "o Código Penal", "url", linhas_do_html(PAGINA_CP))
    agente, pedidos, _ = agente_com(vade, ["Imagina que a Ana..."])
    resposta = agente.responder("me dá um exemplo do artigo 121 do código penal")
    assert resposta.leitura in agente_leis.FRASES_EXEMPLO
    assert resposta.explicar() == "Imagina que a Ana..." and "exemplo prático, hipotético" in pedidos[0]
    assert vade.exemplo("artigo:cp:121") == "Imagina que a Ana..."
    outro, outros_pedidos, _ = agente_com(vade)  # outra conversa: o guardado, sem chamar a IA
    assert outro.responder("me dá um exemplo do artigo 121 do código penal").explicar() == "Imagina que a Ana..."
    assert outros_pedidos == []
    assert outro.e_seguimento("me dá outro exemplo")
    assert outro.responder("me dá outro exemplo").explicar() == "Resposta 1."
    assert "Você já deu estes exemplos" in outros_pedidos[0]


def test_exemplo_de_sumula_comeca_pelo_caso_real(vade):
    agente, pedidos, _ = agente_com(vade_com_sumulas(vade),
                                    ["Explicação.", "No HC 89.429, de 2006, o STF...", "Imagina que..."])
    agente.responder("súmula vinculante 11").explicar()
    assert agente.responder("me dá um exemplo").explicar() == "Um caso real: No HC 89.429, de 2006, o STF..."
    assert "decisão real do tribunal" in pedidos[1] and "HC 89.429" in pedidos[1]
    assert agente.responder("outro exemplo").explicar() == "Imagina que..."
    assert "exemplo prático, hipotético" in pedidos[2]  # acabaram os casos reais


def test_ficha_de_estudo_confere_as_sumulas_citadas(vade, tmp_path):
    import unicodedata

    import pymupdf

    from agentes import ficha_estudo

    vade_com_sumulas(vade)
    ficha = ficha_estudo.normalizar_ficha({"titulo": "Dano moral", "area": "direito", "topicos": [{
        "titulo": "Cumulação", "regra": "Pode cumular.", "jurisprudencia": [
            {"referencia": "Súmula 37 do STJ", "tese": "Cumula dano material e moral.", "conferir": True},
            {"referencia": "Súmula 999 do STJ", "tese": "Número inventado."},
            {"referencia": "STJ, Súmula 61", "tese": "Seguro cobre suicídio."},
            {"referencia": "Tema 940 do STF", "tese": "Ação contra o Estado."},
            {"referencia": "Súmula 7 do STM", "tese": "Tribunal fora do banco."}]}]})
    assert ficha_estudo.conferir_jurisprudencia(ficha, vade) == {"conferidas": 2, "nao_encontradas": 1, "canceladas": 1}
    certa, inventada, cancelada, tema, outro = ficha["topicos"][0]["jurisprudencia"]
    assert certa["conferir"] is False and certa["texto_oficial"].startswith("São cumuláveis")
    assert inventada["conferir"] is True and inventada["conferido"] is False
    assert cancelada["situacao"] == "cancelada"
    assert "conferido" not in tema and "conferido" not in outro
    assert ficha_estudo.normalizar_ficha(ficha)["topicos"][0]["jurisprudencia"][0]["texto_oficial"]  # sobrevive ao ficha.json
    with pymupdf.open(ficha_estudo.gerar_pdf_ficha(ficha, str(tmp_path / "ficha.pdf"))) as documento:
        texto = unicodedata.normalize("NFKC", " ".join(pagina.get_text() for pagina in documento))
    assert "Texto oficial:" in texto and "SÚMULA CANCELADA" in texto and "CONFERIR O NÚMERO" in texto
    assert "(súmula cancelada)" in ficha_estudo._item_juris_docx(cancelada)


def test_main_agenda_o_download_na_abertura(monkeypatch):
    import config
    import main

    agendados = []

    class Temporizador:
        def __init__(self, segundos, funcao, args=()):
            agendados.append((segundos, funcao, args))

        def start(self):
            pass

    monkeypatch.setattr(main.threading, "Timer", Temporizador)
    monkeypatch.setattr(config, "VADE_MECUM_AUTOMATICO", False)
    assert main.iniciar_vade_mecum() is False and agendados == []
    monkeypatch.setattr(config, "VADE_MECUM_AUTOMATICO", True)
    assert main.iniciar_vade_mecum() is True
    segundos, funcao, (vade, partes) = agendados[0]
    assert funcao is vade_mecum.atualizar_em_segundo_plano and partes == list(vade_mecum.PARTES)
    assert vade.caminho == config.CAMINHO_VADE_MECUM  # o banco temporário do teste


def test_atualiza_e_conta_o_que_tem(vade, monkeypatch):
    agente, _, _ = agente_com(vade)
    avisos, iniciadas = [], []
    agente.avisar = avisos.append
    monkeypatch.setattr(vade_mecum, "atualizar_em_segundo_plano",
                        lambda v, ao_terminar=None: iniciadas.append(ao_terminar) or True)
    assert agente.responder("o que tem no vade-mécum?").leitura.startswith("O vade-mécum ainda está vazio")
    assert agente.responder("atualiza o vade mecum").leitura.startswith("Bora! Vou baixar o vade-mécum")
    vade_com_sumulas(vade)
    iniciadas[0]({"stj": {"total": 4, "erros": []}})
    assert avisos == ["Pronto, o vade-mécum está atualizado: 0 normas (0 artigos), 1 súmulas do STF, 4 do STJ e 2 "
                      "súmulas, OJs e precedentes do TST."]
    vade.registrar_atualizacao("stj", 4, [])
    assert agente.responder("o que tem no vade-mécum?").leitura.startswith("No vade-mécum do PC tenho 0 normas")
