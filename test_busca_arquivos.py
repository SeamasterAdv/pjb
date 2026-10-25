"""Busca de arquivos pelo nome aproximado (busca_arquivos.py) e os pedidos que a usam: abrir pela voz, imprimir e mandar."""
import os
import time

import pytest

import busca_arquivos as b
from agente_pastas import AgentePastas, IndiceDePastas

# Nomes de verdade da biblioteca do usuário (out/2026), com os erros, as cópias e os números romanos que eles têm.
NOMES = {
    "referencias": ["Aula 02 Embargos à exeução.pdf", "Aula 03 - embargos de terceiro.pdf", "AULA 1 ATO ADMINISTRATIVO (1).pptx",
                    "AULA 2 ATO ADMINISTRATIVO.pptx", "AULA 3 ATOS ADMINISTRATIVOS.pptx", "AULA 2 ADMINISTRAÇÃO PÚBLICA.pptx",
                    "Aula 2 Mandato.pptx", "AULA 4 ATOS ADMINISTRATIVOS.pptx", "AULA 4 ATOS ADMINISTRATIVOS (1).pptx",
                    "DPT I  - 23 - RECURSOS - disponibilizar ALUNOS.pdf"],
    "direito": ["pessoa natural comorientes.pdf", "transcricao-pessoa-natural-vii-20-10-2026.pdf",
                "trabalho de tcc direito internacional.docx", "01. DIREITO TRIBUTÁRIO - UNIDADE I - 2026.pdf"],
}


def _arquivos():
    return [b.Arquivo(os.path.join("C:/fontes", pasta, nome), nome, b.palavras_do_nome(nome), [pasta], 1000.0 + i, pasta)
            for pasta, nomes in NOMES.items() for i, nome in enumerate(nomes)]


def _melhor(pedido, extensoes=None):
    arquivos = [a for a in _arquivos() if not extensoes or os.path.splitext(a.nome)[1].lower() in extensoes]
    achados = b.ordenar(b.palavras_do_pedido(pedido), arquivos)
    return [a.nome for a in b.empatados(achados)]


def test_palavras_do_nome_sem_copia_com_numeros_e_romanos():
    assert b.palavras_do_nome("AULA 1 ATO ADMINISTRATIVO (1).pptx") == ["aula", "1", "ato", "administrativo"]
    assert b.palavras_do_nome("Aula 02 Embargos à exeução.pdf") == ["aula", "2", "embargos", "exeucao"]
    assert b.palavras_do_nome("transcricao-pessoa-natural-vii-20-10-2026.pdf") == ["transcricao", "pessoa", "natural", "7", "20",
                                                                                   "10", "2026"]
    assert b.palavras_do_nome("01. DIREITO TRIBUTÁRIO - UNIDADE I - 2026.pdf") == ["1", "direito", "tributario", "unidade", "1", "2026"]
    assert b.palavras_do_nome("TrabalhoFinalTCC - Cópia.docx") == ["trabalho", "final", "tcc"]


def test_palavras_do_pedido_com_numero_por_extenso():
    assert b.palavras_do_pedido("abre a aula dois de ato administrativo") == ["aula", "2", "ato", "administrativo"]
    assert b.palavras_do_pedido("a segunda aula de mandato") == ["2", "aula", "mandato"]
    assert b.palavras_do_pedido("aquele pdf que o professor mandou sobre embargos de terceiro") == ["embargos", "terceiro"]
    assert b.palavras_do_pedido("duas aulas") == ["duas", "aulas"]  # quantidade, não número de aula


@pytest.mark.parametrize("pedido, esperado", [
    ("aula dois de ato administrativo", ["AULA 2 ATO ADMINISTRATIVO.pptx"]),
    ("segunda aula de administração pública", ["AULA 2 ADMINISTRAÇÃO PÚBLICA.pptx"]),
    ("aquele que o professor mandou sobre embargos de terceiro", ["Aula 03 - embargos de terceiro.pdf"]),
    ("embargos do professor Ricardo", ["Aula 03 - embargos de terceiro.pdf", "Aula 02 Embargos à exeução.pdf"]),  # "Ricardo" não ajuda
    ("aula de culinária", []),  # sem culinária, sobraria "qualquer aula"
    ("embargos à execução", ["Aula 02 Embargos à exeução.pdf"]),  # o nome tem um erro de digitação
    ("comoriência", ["pessoa natural comorientes.pdf"]),  # mesma raiz
    ("transcrição de pessoa natural 7", ["transcricao-pessoa-natural-vii-20-10-2026.pdf"]),  # romano
    ("tcc", ["trabalho de tcc direito internacional.docx"]),
    ("unidade um de tributário", ["01. DIREITO TRIBUTÁRIO - UNIDADE I - 2026.pdf"]),
    ("aula 4 de atos", ["AULA 4 ATOS ADMINISTRATIVOS (1).pptx"]),  # a cópia conta como um só (a mais nova)
    ("embargos", ["Aula 03 - embargos de terceiro.pdf", "Aula 02 Embargos à exeução.pdf"]),  # empate: perguntar
    ("aula 9 de ato administrativo", []),  # o número não bate com nenhum: não abre "qualquer aula"
    ("contratos de aluguel", []),
])
def test_acha_pelo_nome_aproximado(pedido, esperado):
    assert _melhor(pedido) == esperado


def test_tipo_dito_filtra_as_extensoes():
    assert _melhor("aula 2", {".pdf"}) == ["Aula 02 Embargos à exeução.pdf"]


def test_indice_ignora_programas_pastas_ocultas_e_fundo_demais(tmp_path):
    for caminho in ("fontes/aula.pdf", "fontes/instalar aula.exe", "fontes/.obsidian/aula.md", "fontes/a/b/c/aula funda.pdf",
                    "fontes/~$aula.docx", "fontes/node_modules/aula.pdf"):
        (tmp_path / caminho).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / caminho).write_text("x")
    indice = b.IndiceDeArquivos(raizes=lambda: [(str(tmp_path / "fontes"), 3, "Fontes")])
    indice.atualizar()
    assert sorted(a.nome for a in indice.arquivos) == ["aula.pdf"]
    assert indice.arquivos[0].lugar == "Fontes"


# ------------------------------------------------------------------ abrir pela voz
@pytest.fixture
def cenario(tmp_path):
    for pasta, nomes in NOMES.items():
        for i, nome in enumerate(nomes):
            caminho = tmp_path / "fontes" / pasta / nome
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_text("x")
            os.utime(caminho, (time.time() - 1000 + i, time.time() - 1000 + i))
    (tmp_path / "Documentos").mkdir()
    abertos, mostrados = [], []
    agente = AgentePastas(indice=IndiceDePastas(raizes=lambda: [(str(tmp_path), 2)]), abrir=abertos.append,
                          mostrar=mostrados.append, preferencias=str(tmp_path / "prefs.json"),
                          lugares={"documentos": ("Documentos", str(tmp_path / "Documentos"))},
                          arquivos=b.IndiceDeArquivos(raizes=lambda: [(str(tmp_path / "fontes"), 3, "Fontes")]))
    agente.abertos, agente.mostrados = abertos, mostrados
    return agente


def test_abre_o_arquivo_pelo_nome_aproximado(cenario):
    assert cenario.responder("Beatriz, abre o slide da aula dois de ato administrativo") == "Abri “AULA 2 ATO ADMINISTRATIVO”."
    assert os.path.basename(cenario.abertos[-1]) == "AULA 2 ATO ADMINISTRATIVO.pptx"
    assert cenario.responder("abre a aula de mandato") == "Abri “Aula 2 Mandato”."  # sem dizer o tipo também vale


def test_empate_pergunta_qual_e_lembra_a_escolha(cenario):
    pergunta = cenario.responder("abre o pdf de embargos")
    assert pergunta.startswith("Achei 2 arquivos:") and pergunta.endswith("Qual deles?") and not cenario.abertos
    assert cenario.esperando_resposta()
    assert cenario.responder("o de terceiro") == "Abri “Aula 03 - embargos de terceiro”."
    # da próxima vez abre o escolhido e deixa trocar
    assert "da outra vez" in cenario.responder("abre o pdf de embargos")
    assert cenario.responder("não, o outro") == "Abri “Aula 02 Embargos à exeução”."


def test_onde_esta_mostra_no_explorador(cenario):
    assert cenario.responder("onde está o documento do tcc?").startswith("“trabalho de tcc direito internacional” fica na pasta direito")
    assert os.path.basename(cenario.mostrados[-1]) == "trabalho de tcc direito internacional.docx" and not cenario.abertos


def test_o_que_nao_e_pedido_de_arquivo_segue(cenario):
    assert cenario.responder("abre o pdf de contratos de aluguel") == "Não achei nenhum PDF com “contratos aluguel”."
    assert cenario.responder("abre meus documentos") == "Abri Documentos."  # o lugar, não um arquivo
    for frase in ("abre o word", "abre o youtube", "resume esse pdf", "abre a aula de culinária", "abre o arquivo relatorio.pdf"):
        assert cenario.responder(frase) is None, frase
    assert len(cenario.abertos) == 1


# ------------------------------------------------------------------ imprimir e mandar
def test_impressora_acha_a_aula_pelo_numero(tmp_path, monkeypatch):
    from test_impressora import ImpressoraFalsa
    from agente_impressora import AgenteImpressora

    for nome in ("AULA 1 ATO ADMINISTRATIVO (1).pptx", "AULA 2 ATO ADMINISTRATIVO.pptx", "AULA 3 ATOS ADMINISTRATIVOS.pptx"):
        (tmp_path / "fontes" / nome).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "fontes" / nome).write_text("x")
    monkeypatch.setattr(os.path, "expanduser", lambda caminho: str(tmp_path / "casa") if caminho == "~" else caminho)
    agente = AgenteImpressora(ImpressoraFalsa(), pastas_busca=[str(tmp_path / "fontes")], executar=lambda f: f())
    assert '"AULA 2 ATO ADMINISTRATIVO"' in agente.responder("imprime a apresentação da aula dois de ato administrativo")


def test_manda_o_arquivo_descrito_e_nao_o_da_ultima_tarefa(tmp_path, monkeypatch):
    import main
    from test_whatsapp import CanalFalso, OrquestradorFalso

    (tmp_path / "fontes").mkdir()
    (tmp_path / "fontes" / "Aula 03 - embargos de terceiro.pdf").write_text("x")
    canal = CanalFalso()
    monkeypatch.setattr(main, "CANAL_WHATSAPP", canal)
    monkeypatch.setitem(main.CANAL, "atual", "whatsapp")
    monkeypatch.setattr(main, "avisar_painel", lambda *a, **k: None)
    monkeypatch.setattr(main, "registrar_conversa", lambda *a, **k: None)
    monkeypatch.setattr(main.PASTAS, "arquivos", b.IndiceDeArquivos(raizes=lambda: [(str(tmp_path / "fontes"), 2, "Fontes")]))
    assert main.mandar_arquivo_pedido("manda o pdf da aula de embargos de terceiro", OrquestradorFalso())
    assert canal.enviados == [("Aqui está: Aula 03 - embargos de terceiro.pdf.",
                               [str(tmp_path / "fontes" / "Aula 03 - embargos de terceiro.pdf")])]
    assert main.mandar_arquivo_pedido("manda o pdf de contratos", OrquestradorFalso())
    assert canal.enviados[-1] == ("Não achei nenhum arquivo com “contratos”. Diga uma parte do nome do arquivo.", [])
