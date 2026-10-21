"""Um processo só no banco: os outros processos da Beatriz pedem ao principal as buscas e leituras da biblioteca.

O servidor roda neste processo (pipe local de verdade); um teste abre um processo filho de verdade em modo remoto.
"""
import json
import os
import subprocess
import sys

import pytest

import memory
from test_biblioteca import _escrever_fontes, biblioteca  # noqa: F401  (fixture)


@pytest.fixture
def servidor(monkeypatch):
    """O servidor da biblioteca neste processo; o ambiente e o estado voltam ao normal no fim."""
    monkeypatch.setattr(memory, "_SERVIDOR_DA_BIBLIOTECA", {"valor": ""})
    monkeypatch.setenv("BEATRIZ_BIBLIOTECA_REMOTA", "")
    valor = memory.servir_biblioteca()
    monkeypatch.setattr(memory, "BIBLIOTECA_REMOTA", valor)  # o repasse lê o endereço daqui
    return valor


def test_busca_e_leitura_das_fontes_vao_pelo_processo_principal(biblioteca, servidor):  # noqa: F811
    fontes, _, colecao = biblioteca
    _escrever_fontes(fontes)
    indexar = memory._no_processo_principal("indexar_pasta_documentos")
    buscar = memory._no_processo_principal("buscar_nos_documentos_modo")
    assert indexar().startswith("3 fontes")
    assert colecao.count() > 0  # quem gravou foi o "principal", na coleção dele
    pergunta = "multa por rescisão do contrato de aluguel"
    assert buscar(pergunta, modo="padrão") == memory.buscar_nos_documentos_modo(pergunta, modo="padrão")
    assert memory._no_processo_principal("total_de_trechos")() == colecao.count()


def test_sem_o_principal_ou_com_a_chave_errada_cada_funcao_tem_a_sua_resposta(servidor, monkeypatch):
    endereco, chave = servidor.rsplit("|", 1)
    monkeypatch.setattr(memory, "BIBLIOTECA_REMOTA", f"{endereco}|{'00' * 32}")  # chave errada: não é da Beatriz
    assert memory._no_processo_principal("buscar_nos_documentos_modo")("qualquer coisa") == "Nenhuma informação encontrada."
    monkeypatch.setattr(memory, "BIBLIOTECA_REMOTA", r"\\.\pipe\beatriz-que-nao-existe|" + chave)  # principal fechado
    assert memory._no_processo_principal("total_de_trechos")() is None
    assert memory._no_processo_principal("agendar_indexacao_em_background")() is False
    assert "indisponível" in memory._no_processo_principal("indexar_pasta_documentos")()


def test_o_servidor_so_atende_as_funcoes_do_banco(servidor, monkeypatch):
    from multiprocessing.connection import Client

    endereco, chave = servidor.rsplit("|", 1)
    with Client(endereco, authkey=bytes.fromhex(chave)) as conexao:
        conexao.send(("atualizar_memoria_usuario", ("apaga tudo",), {}))
        certo, mensagem = conexao.recv()
    assert certo is False and "fora da biblioteca" in mensagem


def test_processo_filho_nao_abre_o_banco_e_busca_pelo_principal(biblioteca, servidor, tmp_path):  # noqa: F811
    """Um processo de verdade em modo remoto (como o painel): sem cliente do ChromaDB, e a busca chega pelo pipe."""
    fontes, _, _ = biblioteca
    _escrever_fontes(fontes)
    memory.indexar_pasta_documentos()
    codigo = (
        "import json, memory; "
        "print(json.dumps({'cliente': memory.chroma_client is None and memory.colecao_docs is None, "
        "'trechos': memory.total_de_trechos(), "
        "'busca': memory.buscar_nos_documentos_modo('multa por rescisão do contrato de aluguel', modo='padrão')}))"
    )
    ambiente = {**os.environ, "BEATRIZ_BIBLIOTECA_REMOTA": servidor, "BEATRIZ_BANCO_MEMORIA": str(tmp_path / "banco_do_filho"),
                "PYTHONIOENCODING": "utf-8"}
    saida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, encoding="utf-8", env=ambiente,
                           cwd=os.path.dirname(os.path.abspath(memory.__file__)), timeout=180)
    assert saida.returncode == 0, saida.stderr[-2000:]
    resultado = json.loads(saida.stdout.strip().splitlines()[-1])
    assert resultado["cliente"] is True  # o filho nem abriu o banco
    assert resultado["trechos"] == memory.total_de_trechos() and resultado["trechos"] > 0
    assert "aluguel.md" in resultado["busca"]
    assert not any((tmp_path / "banco_do_filho").glob("*.sqlite3"))


def test_segundo_processo_nao_abre_o_banco_travado(tmp_path):
    """O processo dos testes trava a pasta do banco dele; outro processo com a mesma pasta (uma segunda Beatriz,
    um script de fora) não abre o ChromaDB e recebe as respostas de reserva."""
    assert memory._TRAVA_DO_BANCO is not None and not memory.BANCO_OCUPADO  # este processo tem a trava
    codigo = (
        "import json, memory; "
        "print(json.dumps({'ocupado': memory.BANCO_OCUPADO, 'cliente': memory.chroma_client is None, "
        "'busca': memory.buscar_nos_documentos_modo('qualquer coisa', modo='padrão'), "
        "'servidor': memory.servir_biblioteca()}))"
    )
    ambiente = {**os.environ, "BEATRIZ_BANCO_MEMORIA": memory.banco_memoria_path, "PYTHONIOENCODING": "utf-8"}
    ambiente.pop("BEATRIZ_BIBLIOTECA_REMOTA", None)
    saida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, encoding="utf-8", env=ambiente,
                           cwd=os.path.dirname(os.path.abspath(memory.__file__)), timeout=180)
    assert saida.returncode == 0, saida.stderr[-2000:]
    resultado = json.loads(saida.stdout.strip().splitlines()[-1])
    assert resultado == {"ocupado": True, "cliente": True, "busca": "Nenhuma informação encontrada.", "servidor": ""}


def test_trava_e_solta_com_o_arquivo(tmp_path):
    primeira = memory._travar_banco(str(tmp_path))
    assert primeira is not None
    assert memory._travar_banco(str(tmp_path)) is None  # já travado (mesmo no mesmo processo, outro arquivo aberto)
    primeira.close()  # o processo acabou: a trava sai junto
    segunda = memory._travar_banco(str(tmp_path))
    assert segunda is not None
    segunda.close()
