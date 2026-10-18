"""Testes do agente da impressora com uma impressora falsa: nada vai para a fila do Windows."""
import os
import time

import pytest

from agente_impressora import AgenteImpressora, Opcoes, ler_opcoes, pede_impressao, texto_da_agenda
from impressora import EstadoImpressora, TrabalhoImpressao, nome_curto, texto_para_pdf


class ImpressoraFalsa:
    def __init__(self, ligada=True, trabalhos=(), paginas=3):
        self.ligada = ligada
        self.fila = list(trabalhos)
        self.paginas = paginas
        self.impressos = []  # (pdf, copias, paginas, colorido)
        self.textos = []  # (titulo, texto)
        self.preparados = []
        self.pausada = False

    def nome(self):
        return "EPSON L395 Series"

    def listar(self):
        return ["EPSON L395 Series", "Microsoft Print to PDF"]

    def estado(self):
        return EstadoImpressora(nome=self.nome(), ligada=self.ligada, pausada=self.pausada, trabalhos=len(self.fila))

    def trabalhos(self):
        return list(self.fila)

    def cancelar_todos(self):
        cancelados, self.fila = len(self.fila), []
        return cancelados

    def pausar(self):
        self.pausada = True

    def retomar(self):
        self.pausada = False

    def preparar(self, caminho):
        self.preparados.append(caminho)
        return caminho if caminho.endswith(".pdf") else caminho + ".pdf"

    def preparar_texto(self, titulo, texto):
        self.textos.append((titulo, texto))
        return f"texto_{len(self.textos)}.pdf"

    def contar_paginas(self, pdf):
        return 1 if pdf.startswith("texto_") else self.paginas

    def imprimir_pdf(self, pdf, copias=1, paginas=None, colorido=True, titulo="", arquivo_saida=None):
        self.impressos.append((pdf, copias, paginas, colorido))
        return len(paginas or range(self.paginas)) * copias


@pytest.fixture
def pastas(tmp_path, monkeypatch):
    gerados = tmp_path / "documentos_gerados"
    documentos = tmp_path / "documentos_assistente"
    casa = tmp_path / "casa"
    for pasta in (gerados, documentos, casa / "Downloads", casa / "Desktop", casa / "Documents"):
        pasta.mkdir(parents=True)
    monkeypatch.setattr(os.path, "expanduser", lambda caminho: str(casa) if caminho == "~" else caminho)
    return {"gerados": gerados, "documentos": documentos, "downloads": casa / "Downloads"}


def criar(caminho, idade=0):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text("conteúdo", encoding="utf-8")
    momento = time.time() - idade
    os.utime(caminho, (momento, momento))
    return str(caminho)


def agente_com(pastas, impressora=None, respostas=(), **kwargs):
    impressora = impressora or ImpressoraFalsa()
    agente = AgenteImpressora(impressora, pastas_busca=[str(pastas["documentos"])], pasta_gerados=str(pastas["gerados"]),
                              respostas_recentes=lambda: list(respostas), executar=lambda funcao: funcao(), **kwargs)
    return agente, impressora


# ------------------------------------------------------------------ opções
def test_le_copias_cor_e_paginas():
    opcoes, achou = ler_opcoes("imprime duas copias em preto e branco so a pagina 3")
    assert achou and opcoes.copias == 2 and opcoes.colorido is False
    assert opcoes.indices(5) == [2]
    assert ler_opcoes("paginas 2 a 4")[0].indices(10) == [1, 2, 3]
    assert ler_opcoes("da pagina 3 em diante")[0].indices(5) == [2, 3, 4]
    assert ler_opcoes("paginas 1, 3 e 5")[0].indices(5) == [0, 2, 4]
    assert ler_opcoes("so a ultima pagina")[0].indices(7) == [6]
    assert ler_opcoes("imprime o pdf")[1] is False
    assert Opcoes().indices(4) is None


def test_nome_curto_da_impressora():
    assert nome_curto("EPSON L395 Series") == "Epson L395"
    assert nome_curto("Microsoft Print to PDF") == "Microsoft Print to PDF"


# ---------------------------------------------------------------- arquivos
def test_imprime_o_ultimo_pdf_gerado_depois_de_confirmar(pastas):
    criar(pastas["gerados"] / "2026-10-17_clima" / "clima.pdf", idade=3600)
    novo = criar(pastas["gerados"] / "2026-10-18_fotossintese" / "fotossintese.pdf")
    criar(pastas["gerados"] / "2026-10-18_fotossintese" / "relatorio.md")  # arquivo de apoio: fica de fora
    agente, impressora = agente_com(pastas)

    pergunta = agente.responder("Beatriz, imprime o PDF")
    assert pergunta.startswith('Vou imprimir o PDF "fotossintese": 3 páginas, na Epson L395.')
    assert pergunta.endswith("Posso?")
    assert impressora.impressos == []

    resposta = agente.responder("sim")
    assert "Mandei para a impressora" in resposta
    assert impressora.impressos == [(novo, 1, None, True)]


def test_pdf_sem_nome_prefere_o_que_ela_fez(pastas):
    dela = criar(pastas["gerados"] / "2026-10-18_clima" / "clima.pdf", idade=86400)
    criar(pastas["downloads"] / "8cb1acacbebc2d9f.pdf")  # mais novo, mas baixado da internet
    agente, impressora = agente_com(pastas)
    agente.responder("imprime o pdf")
    agente.responder("sim")
    assert impressora.impressos[0][0] == dela


def test_acha_arquivo_pelo_nome_com_opcoes(pastas):
    criar(pastas["documentos"] / "direito" / "Contrato_de_aluguel.pdf", idade=7200)
    criar(pastas["gerados"] / "outro.pdf")
    agente, impressora = agente_com(pastas)

    pergunta = agente.responder("imprime o contrato de aluguel em preto e branco, duas cópias, só a página 2")
    assert '"Contrato de aluguel"' in pergunta
    assert "2 páginas, 2 cópias, em preto e branco" in pergunta
    agente.responder("pode")
    pdf, copias, paginas, colorido = impressora.impressos[0]
    assert pdf.endswith("Contrato_de_aluguel.pdf") and copias == 2 and paginas == [1] and colorido is False


def test_boleto_baixado_vem_dos_downloads(pastas):
    boleto = criar(pastas["downloads"] / "boleto_energia_outubro.pdf")
    criar(pastas["gerados"] / "boleto_modelo.pdf")  # mais novo, mas não está nos downloads
    agente, impressora = agente_com(pastas)

    assert "boleto energia outubro" in agente.responder("imprime o boleto que eu baixei")
    agente.responder("sim")
    assert impressora.impressos[0][0] == boleto


def test_word_e_convertido_antes(pastas):
    documento = criar(pastas["gerados"] / "2026-10-18_receitas" / "receitas.docx")
    agente, impressora = agente_com(pastas)

    assert agente.responder("imprime o documento de receitas").startswith('Vou imprimir o documento "receitas"')
    assert impressora.preparados == [documento]


def test_arquivo_que_nao_existe(pastas):
    agente, impressora = agente_com(pastas)
    resposta = agente.responder("imprime o pdf da reforma tributária")
    assert resposta.startswith('Não achei nenhum PDF com "reforma tributaria"')
    assert agente.pendencia is None


def test_mudanca_na_confirmacao_refaz_a_pergunta(pastas):
    criar(pastas["gerados"] / "aula.pdf")
    agente, impressora = agente_com(pastas)
    agente.responder("imprime o pdf")

    pergunta = agente.responder("não, em preto e branco")
    assert "em preto e branco" in pergunta and pergunta.endswith("Posso?")
    agente.responder("isso")
    assert impressora.impressos[0][3] is False


def test_pedido_novo_no_lugar_do_sim_nao_imprime_o_anterior(pastas):
    criar(pastas["gerados"] / "aula.pdf")
    agente, impressora = agente_com(pastas)
    agente.responder("imprime o pdf")
    assert agente.responder("imprime: comprar pão").startswith('Vou imprimir o texto "comprar pão"')
    assert impressora.impressos == []
    for resposta in ("imprime sim", "pode imprimir em preto e branco"):
        agente.responder("imprime o pdf")
        agente.responder(resposta)
    assert [pdf for pdf, *_ in impressora.impressos] == [str(pastas["gerados"] / "aula.pdf")] * 2
    assert impressora.impressos[1][3] is False


def test_nao_cancela(pastas):
    criar(pastas["gerados"] / "aula.pdf")
    agente, impressora = agente_com(pastas)
    agente.responder("imprime o pdf")
    assert agente.responder("não") == "Tudo bem, não imprimi."
    assert impressora.impressos == [] and agente.pendencia is None


def test_confirmacao_expira(pastas):
    criar(pastas["gerados"] / "aula.pdf")
    agora = [1000.0]
    agente, impressora = agente_com(pastas, relogio=lambda: agora[0])
    agente.responder("imprime o pdf")
    agora[0] += 500
    assert agente.responder("sim") is None
    assert impressora.impressos == []


def test_sem_confirmacao_imprime_direto(pastas):
    criar(pastas["gerados"] / "aula.pdf")
    agente, impressora = agente_com(pastas, confirmar=False)
    assert agente.responder("imprime o pdf").startswith("Mandei para a impressora")
    assert len(impressora.impressos) == 1


def test_pergunta_o_que_imprimir(pastas):
    criar(pastas["documentos"] / "lista_de_exercicios.pdf")
    agente, impressora = agente_com(pastas)
    assert agente.responder("imprime").startswith("O que você quer imprimir?")
    assert '"lista de exercicios"' in agente.responder("a lista de exercícios")


def test_avisa_quando_a_impressora_parece_desligada(pastas):
    criar(pastas["gerados"] / "aula.pdf")
    agente, _ = agente_com(pastas, impressora=ImpressoraFalsa(ligada=False))
    assert "parece desligada" in agente.responder("imprime o pdf")


def test_erro_na_impressao_avisa(pastas):
    criar(pastas["gerados"] / "aula.pdf")
    avisos = []

    class Quebrada(ImpressoraFalsa):
        def imprimir_pdf(self, *args, **kwargs):
            raise RuntimeError("sem papel")

    agente, _ = agente_com(pastas, impressora=Quebrada(), confirmar=False, avisar=avisos.append)
    agente.responder("imprime o pdf")
    assert avisos == ['Não consegui imprimir o PDF "aula": sem papel']


# ------------------------------------------------------------ textos
def test_imprime_texto_ditado(pastas):
    agente, impressora = agente_com(pastas)
    pergunta = agente.responder("imprime um bilhete dizendo Volto às 18h, a chave está com a vizinha")
    assert pergunta.startswith('Vou imprimir o bilhete "Volto às 18h, a chave está com a vizinha": 1 página')
    assert impressora.textos == [("Bilhete", "Volto às 18h, a chave está com a vizinha")]
    assert agente.responder("imprime: comprar pão").startswith('Vou imprimir o texto "comprar pão"')


def test_cancelar_apaga_o_pdf_temporario(pastas, tmp_path):
    temporario = tmp_path / "texto.pdf"

    class ComArquivo(ImpressoraFalsa):
        def preparar_texto(self, titulo, texto):
            temporario.write_bytes(b"%PDF")
            return str(temporario)

    agente, impressora = agente_com(pastas, impressora=ComArquivo())
    agente.responder("imprime: lista do mercado")
    assert temporario.exists()
    assert agente.responder("não") == "Tudo bem, não imprimi."
    assert not temporario.exists()


def test_lista_vira_topicos(pastas):
    agente, impressora = agente_com(pastas)
    pergunta = agente.responder("imprime uma lista de compras com arroz, feijão, café e pão")
    assert pergunta.startswith('Vou imprimir a lista de compras "arroz feijão café pão"')
    assert impressora.textos == [("Lista de compras", "- arroz\n- feijão\n- café\n- pão")]


def test_imprime_a_ultima_resposta_e_nao_a_propria_pergunta(pastas):
    agente, impressora = agente_com(pastas, respostas=["A receita leva 3 ovos e 200 g de farinha."])
    pergunta = agente.responder("imprime isso")
    assert pergunta.startswith("Vou imprimir a minha última resposta")
    assert impressora.textos == [("", "A receita leva 3 ovos e 200 g de farinha.")]

    sem_resposta, _ = agente_com(pastas)
    assert sem_resposta.responder("imprime o que você disse") == "Ainda não tenho uma resposta minha para imprimir."


def test_imprime_a_agenda(pastas):
    agente, impressora = agente_com(pastas, texto_agenda=lambda t: ("Agenda de amanhã", "- 09:00: Dentista"))
    assert agente.responder("imprime minha agenda de amanhã").startswith("Vou imprimir a agenda de amanhã")
    assert impressora.textos == [("Agenda de amanhã", "- 09:00: Dentista")]


def test_texto_da_agenda_local(tmp_path):
    from datetime import datetime

    from agenda_local import Agenda
    from agente_agenda import AgenteAgenda

    agente_agenda = AgenteAgenda(Agenda(str(tmp_path / "agenda.sqlite3")), relogio=lambda: datetime(2026, 10, 18, 8, 0))
    agente_agenda.responder("marca dentista amanhã às 14h")
    titulo, texto = texto_da_agenda(agente_agenda, "imprime minha agenda de amanha")
    assert titulo == "Agenda de amanhã"
    assert "# Segunda-feira, 19 de outubro" in texto and "14:00 às 15:00: " in texto and "entista" in texto


def test_texto_para_pdf_de_verdade(tmp_path):
    from impressora import contar_paginas

    caminho = texto_para_pdf("Lista", "- arroz\n- feijão\n\nObservação final.", str(tmp_path / "lista.pdf"))
    assert contar_paginas(caminho) == 1


# ---------------------------------------------------- estado, fila, energia
def test_status_e_fila(pastas):
    fila = [TrabalhoImpressao(7, "Beatriz - o PDF \"aula\"", "imprimindo", 3)]
    agente, impressora = agente_com(pastas, impressora=ImpressoraFalsa(trabalhos=fila))
    assert agente.responder("a impressora está ligada?") == "A Epson L395 está ligada, com 1 impressão na fila."
    assert agente.responder("já imprimiu?") == 'Na fila: o PDF "aula" (imprimindo).'
    assert agente.responder("cancela a impressão").startswith("Cancelei 1 impressão.")
    assert agente.responder("o que tem na fila de impressão?") == "A fila está vazia: já saiu tudo."


def test_ligar_e_desligar_explicam_o_limite(pastas):
    desligada, _ = agente_com(pastas, impressora=ImpressoraFalsa(ligada=False))
    assert desligada.responder("liga a impressora").startswith("Não consigo ligar a Epson L395 daqui")
    assert desligada.responder("desliga a impressora") == "A Epson L395 já está desligada."
    ligada, _ = agente_com(pastas)
    assert ligada.responder("liga a impressora") == "A Epson L395 já está ligada."
    assert ligada.responder("desliga a impressora").startswith("Não consigo desligar a Epson L395 pelo computador")


def test_pausar_e_retomar(pastas):
    agente, impressora = agente_com(pastas)
    assert agente.responder("pausa a impressora").startswith("Pausei a fila da Epson L395")
    assert impressora.pausada
    agente.responder("retoma a impressão")
    assert not impressora.pausada


def test_pausar_sem_permissao_explica(pastas):
    from impressora import ErroImpressora

    class SemAdmin(ImpressoraFalsa):
        def pausar(self):
            raise ErroImpressora("o Windows só deixa pausar a fila com permissão de administrador")

    agente, _ = agente_com(pastas, impressora=SemAdmin())
    resposta = agente.responder("pausa a impressora")
    assert resposta.startswith("Não consegui: o Windows só deixa pausar") and "cancela a impressão" in resposta


def test_padrao_virtual_cede_lugar_a_impressora_fisica(monkeypatch):
    win32print = pytest.importorskip("win32print")
    from impressora import ImpressoraWindows

    virtuais = {"Microsoft Print to PDF", "OneNote (Desktop)"}
    monkeypatch.setattr(ImpressoraWindows, "_virtual", staticmethod(lambda nome: nome in virtuais))
    monkeypatch.setattr(ImpressoraWindows, "listar", lambda self: ["OneNote (Desktop)", "EPSON L395 Series"])
    monkeypatch.setattr(win32print, "GetDefaultPrinter", lambda: "Microsoft Print to PDF")
    assert ImpressoraWindows().nome() == "EPSON L395 Series"
    assert ImpressoraWindows("Microsoft Print to PDF").nome() == "Microsoft Print to PDF"  # escolhida no .env
    monkeypatch.setattr(win32print, "GetDefaultPrinter", lambda: "HP Laser")
    assert ImpressoraWindows().nome() == "HP Laser"


def test_ignora_o_que_nao_e_da_impressora(pastas):
    agente, impressora = agente_com(pastas)
    for frase in ("qual a previsão do tempo?", "tenho a impressão de que vai chover", "faz um PDF sobre clima e imprime"):
        assert agente.responder(frase) is None, frase


# ------------------------------------------------------- tarefas dos agentes
def test_pede_impressao():
    assert pede_impressao("faz um PDF sobre fotossíntese e imprime")
    assert pede_impressao("cria uma apresentação de vendas e depois imprima")
    assert not pede_impressao("faz um PDF sobre impressoras")


def test_tema_da_tarefa_sem_o_pedido_de_impressao():
    from agentes.orquestrador import extrair_tema

    assert extrair_tema("faz um PDF sobre fotossíntese e imprime") == "fotossíntese"


def test_oferece_imprimir_o_resultado_da_tarefa(pastas):
    pdf = criar(pastas["gerados"] / "t" / "fotossintese.pdf")
    docx = criar(pastas["gerados"] / "t" / "fotossintese.docx")
    agente, impressora = agente_com(pastas)
    pergunta = agente.oferecer_arquivos([docx, pdf, str(pastas["gerados"] / "t" / "relatorio.md")])
    assert pergunta.startswith('Vou imprimir o PDF "fotossintese"')
    agente.responder("sim")
    assert impressora.impressos[0][0] == pdf


def test_sem_pywin32_explica(pastas):
    class SemModulo(ImpressoraFalsa):
        def estado(self):
            raise ModuleNotFoundError("No module named 'win32print'", name="win32print")

    agente, _ = agente_com(pastas, impressora=SemModulo())
    assert agente.responder("a impressora está ligada?").startswith("Para imprimir falta instalar o pywin32")
