"""Configuração comum dos testes: a agenda de verdade (banco_memoria/agenda.sqlite3) nunca é usada."""
import atexit
import os
import shutil
import sys
import tempfile

import pytest

# A biblioteca (ChromaDB) dos testes fica numa pasta temporária, definida antes de qualquer teste importar o memory.
# O ChromaDB não aguenta dois processos gravando no mesmo banco: em out/2026 os testes abriram o banco de verdade
# com a Beatriz aberta e o índice corrompeu (ela caía logo depois do login).
os.environ.pop("BEATRIZ_BIBLIOTECA_REMOTA", None)  # rodando de dentro de uma Beatriz aberta: os testes têm o banco deles
if not os.environ.get("BEATRIZ_BANCO_MEMORIA"):
    os.environ["BEATRIZ_BANCO_MEMORIA"] = tempfile.mkdtemp(prefix="beatriz_banco_teste_")
    atexit.register(shutil.rmtree, os.environ["BEATRIZ_BANCO_MEMORIA"], True)


@pytest.fixture(autouse=True)
def agenda_temporaria(tmp_path, monkeypatch):
    """main.AGENDA e o agente das ferramentas passam a usar um banco temporário em cada teste."""
    from agenda_local import Agenda
    from agente_agenda import AgenteAgenda

    caminho = str(tmp_path / "agenda_teste.sqlite3")
    if "main" in sys.modules:
        monkeypatch.setattr(sys.modules["main"].AGENDA, "agenda", Agenda(caminho))
    if "tools" in sys.modules:
        monkeypatch.setattr(sys.modules["tools"], "_AGENTE_AGENDA", AgenteAgenda(Agenda(caminho)))
    yield


@pytest.fixture(autouse=True)
def sem_internet_para_as_leis(monkeypatch):
    """A conferência da lei seca e o agente de leis nunca baixam o site do Planalto nos testes."""
    import agente_leis

    def offline(url):
        raise OSError("sem internet nos testes")

    monkeypatch.setattr(agente_leis.TextoDasLeis, "_baixar_da_internet", staticmethod(offline))
    yield


@pytest.fixture(autouse=True)
def vade_mecum_isolado(tmp_path, monkeypatch):
    """O vade-mécum de cada teste é um banco temporário, e nada é baixado do Planalto nem dos tribunais."""
    import config
    import vade_mecum

    monkeypatch.setattr(config, "CAMINHO_VADE_MECUM", str(tmp_path / "vade_mecum_teste.sqlite3"))

    def proibido(*args, **kwargs):
        raise OSError("sem internet nos testes")

    for metodo in ("bytes", "texto", "json"):
        monkeypatch.setattr(vade_mecum.Baixador, metodo, proibido)
    yield


@pytest.fixture(autouse=True)
def escuta_no_modo_conversa(monkeypatch):
    """Os testes de escuta foram escritos para a janela de 40 s ("conversa"); o padrão do programa é "sempre"."""
    import sys

    if "main" in sys.modules:
        monkeypatch.setitem(sys.modules["main"].ESCUTA, "modo", "conversa")
        monkeypatch.setitem(sys.modules["main"].AMBIENTE, "outra_voz_em", 0.0)
        monkeypatch.setitem(sys.modules["main"].AMBIENTE, "ultima_oferta", 0.0)
    yield


@pytest.fixture(autouse=True)
def biometria_isolada(tmp_path, monkeypatch):
    """Os testes nunca leem as vozes cadastradas de verdade nem carregam o modelo ECAPA."""
    import biometria_voz

    bio = biometria_voz.BIOMETRIA

    def proibido(*args, **kwargs):
        raise AssertionError("um teste tentou carregar o modelo de voz de verdade")

    monkeypatch.setattr(bio, "caminho", str(tmp_path / "vozes_teste.json"))
    monkeypatch.setattr(bio, "_perfis", {})
    monkeypatch.setattr(bio, "_ultimo", None)
    monkeypatch.setattr(bio, "_ligada", True)
    monkeypatch.setattr(bio.codificador, "preparar", proibido)
    yield


@pytest.fixture(autouse=True)
def pastas_sem_varredura(monkeypatch):
    """O aquecimento do índice (thread da abertura) não varre as pastas do PC nos testes, e a busca de arquivos só olha as
    pastas que o teste der (sem raízes, o índice padrão fica vazio)."""
    import agente_pastas
    import busca_arquivos

    monkeypatch.setattr(agente_pastas.AgentePastas, "aquecer", lambda self: None)
    monkeypatch.setattr(busca_arquivos, "raizes_padrao", lambda: [])
    yield


@pytest.fixture(autouse=True)
def tela_intocada(monkeypatch):
    """Nenhum teste fotografa a tela de verdade, lê o OCR do Windows ou chama o modelo de visão."""
    import leitura_tela

    def proibido(*args, **kwargs):
        raise AssertionError("um teste tentou usar a tela de verdade")

    monkeypatch.setattr(leitura_tela, "capturar", proibido)
    monkeypatch.setattr(leitura_tela, "ler_texto", proibido)
    monkeypatch.setattr(leitura_tela, "janelas_visiveis", lambda: [])  # nenhuma janela: sem contexto de atividade
    monkeypatch.setattr(leitura_tela.AgenteTela, "_ver_com_ollama", proibido)
    yield


@pytest.fixture(autouse=True)
def voz_escolhida_isolada(tmp_path, monkeypatch):
    """Os testes nunca leem nem gravam a voz escolhida de verdade (banco_memoria/voz_escolhida.json)."""
    import voice

    monkeypatch.setattr(voice, "CAMINHO_VOZ_ESCOLHIDA", str(tmp_path / "voz_escolhida_teste.json"))
    monkeypatch.setattr(voice, "_VOZ_ESCOLHIDA", {"perfil": voice.PERFIL_PADRAO, "modificado": None})
    monkeypatch.setattr(voice, "_TEXTOS_PREPARADOS", set())
    yield


@pytest.fixture(autouse=True)
def aparelhos_isolados(tmp_path, monkeypatch):
    """Os testes nunca leem nem gravam os celulares pareados de verdade (banco_memoria/app_celular.json)."""
    if "main" in sys.modules:
        import app_celular

        monkeypatch.setattr(sys.modules["main"], "APARELHOS", app_celular.Aparelhos(str(tmp_path / "aparelhos_teste.json")))
    yield


@pytest.fixture(autouse=True)
def controle_remoto_intocado(tmp_path, monkeypatch):
    """Nenhum teste manda infravermelho, procura o controle na rede ou fala com a nuvem da Tuya, e os códigos e o estado
    de verdade (banco_memoria/controle_remoto.json) nunca são lidos nem gravados."""
    import controle_remoto

    def proibido(*args, **kwargs):
        raise AssertionError("um teste tentou usar o controle remoto de verdade")

    monkeypatch.setattr(controle_remoto.ControleLocalTuya, "_aparelho", proibido)
    monkeypatch.setattr(controle_remoto.ControleLocalTuya, "_aparelho_simples", proibido)
    monkeypatch.setattr(controle_remoto.ControleLocalTuya, "_descobrir_ip", proibido)
    monkeypatch.setattr(controle_remoto.ControleNuvemTuya, "_nuvem", proibido)
    if "main" in sys.modules:
        controle = sys.modules["main"].CONTROLE
        monkeypatch.setattr(controle, "registro", controle_remoto.RegistroDoControle(str(tmp_path / "controle_teste.json")))
        monkeypatch.setattr(controle, "pendencia", None)
    yield


@pytest.fixture(autouse=True)
def impressora_intocada(monkeypatch):
    """Nenhum teste imprime, cancela ou pausa nada na fila do Windows de verdade."""
    import impressora

    def proibido(*args, **kwargs):
        raise AssertionError("um teste tentou mexer na impressora de verdade")

    for metodo in ("imprimir_pdf", "cancelar_todos", "pausar", "retomar"):
        monkeypatch.setattr(impressora.ImpressoraWindows, metodo, proibido)
    yield
