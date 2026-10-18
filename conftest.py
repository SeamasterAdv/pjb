"""Configuração comum dos testes: a agenda de verdade (banco_memoria/agenda.sqlite3) nunca é usada."""
import sys

import pytest


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
def impressora_intocada(monkeypatch):
    """Nenhum teste imprime, cancela ou pausa nada na fila do Windows de verdade."""
    import impressora

    def proibido(*args, **kwargs):
        raise AssertionError("um teste tentou mexer na impressora de verdade")

    for metodo in ("imprimir_pdf", "cancelar_todos", "pausar", "retomar"):
        monkeypatch.setattr(impressora.ImpressoraWindows, metodo, proibido)
    yield
