"""Biblioteca de fontes (organizar à mão): pastas temporárias, lixeira falsa e um índice de mentira."""
import os

import pytest

from biblioteca_fontes import BibliotecaDeFontes, ErroDaBiblioteca, resultado


@pytest.fixture
def bib(tmp_path):
    documentos, obsidian = tmp_path / "documentos_assistente", tmp_path / "obsidian"
    for pasta in (documentos / "direito", obsidian / "direito", obsidian / "geral", obsidian / "diario", obsidian / ".obsidian"):
        pasta.mkdir(parents=True)
    (obsidian / "direito" / "aula 1.pdf").write_bytes(b"%PDF")
    (obsidian / "direito" / "resumo.md").write_text("texto")
    (obsidian / "geral" / "memoria_usuario.md").write_text("pessoal")
    (obsidian / "diario" / "hoje.md").write_text("pessoal")
    (obsidian / "idiomas.json").write_text("{}")
    (obsidian / ".obsidian" / "app.json").write_text("{}")
    (documentos / "direito" / "lei.docx").write_bytes(b"docx")
    lixo, mudancas = [], []
    biblioteca = BibliotecaDeFontes(
        raizes={"documentos": ("Documentos", str(documentos)), "obsidian": ("Obsidian", str(obsidian))},
        pessoais=[str(obsidian / "geral"), str(obsidian / "diario")],
        ao_mudar=lambda: mudancas.append(1),
        estado_do_indice=lambda: {os.path.normcase(os.path.realpath(obsidian / "direito" / "aula 1.pdf")): "lida"},
        lixeira=lambda caminho: (lixo.append(os.path.basename(caminho)), os.remove(caminho)),
        fora_da_biblioteca=str(tmp_path / "fora"),
        limite_mb=1,
    )
    biblioteca.lixo, biblioteca.mudancas, biblioteca.base = lixo, mudancas, tmp_path
    return biblioteca


def _arquivos(bib):
    return {a["id"]: a for p in bib.listar()["pastas"] for a in p["arquivos"]}


def test_lista_as_fontes_sem_as_pastas_pessoais_nem_dados(bib):
    lista = bib.listar()
    ids = [p["id"] for p in lista["pastas"]]
    assert ids == ["documentos", "documentos/direito", "obsidian", "obsidian/direito"]
    arquivos = _arquivos(bib)
    assert set(arquivos) == {"documentos/direito/lei.docx", "obsidian/direito/aula 1.pdf", "obsidian/direito/resumo.md"}
    assert arquivos["obsidian/direito/aula 1.pdf"]["estado"] == "lida"
    assert arquivos["obsidian/direito/resumo.md"]["estado"] == "ainda não lida"
    assert (lista["total"], lista["lidas"]) == (3, 1)
    assert lista["pastas"][3]["caminho"] == "Obsidian › direito"


@pytest.mark.parametrize("ident", ["obsidian/../segredo.txt", "obsidian/geral/memoria_usuario.md", "obsidian/diario/hoje.md",
                                   "obsidian/idiomas.json", "c:/windows/win.ini", "outra/coisa.pdf", "obsidian/.obsidian/app.json"])
def test_nada_fora_das_fontes_pode_ser_tocado(bib, ident):
    (bib.base / "segredo.txt").write_text("x")
    for acao in (lambda: bib.apagar(ident), lambda: bib.mover(ident, "obsidian"), lambda: bib.renomear(ident, "x"),
                 lambda: bib.caminho_para_abrir(ident)):
        with pytest.raises(ErroDaBiblioteca):
            acao()
    assert not bib.lixo and (bib.base / "segredo.txt").exists() and (bib.base / "obsidian" / "geral" / "memoria_usuario.md").exists()


def test_mover_renomear_e_pastas(bib):
    assert "Criei a pasta Obsidian › direito › OAB" in bib.criar_pasta("obsidian/direito", "OAB")
    assert bib.mover("obsidian/direito/aula 1.pdf", "obsidian/direito/OAB").startswith("Movi “aula 1.pdf” para Obsidian › direito › OAB")
    assert "obsidian/direito/OAB/aula 1.pdf" in _arquivos(bib)
    # Renomear mantém o tipo do arquivo, mesmo sem a extensão no nome novo.
    assert bib.renomear("obsidian/direito/OAB/aula 1.pdf", "Aula 01 - processo civil") == \
        "Renomeei “aula 1.pdf” para “Aula 01 - processo civil.pdf”."
    (bib.base / "obsidian" / "direito" / "outro.md").write_text("x")
    with pytest.raises(ErroDaBiblioteca, match="Já existe"):
        bib.renomear("obsidian/direito/resumo.md", "outro")  # não sobrescreve
    assert bib.renomear("obsidian/direito/resumo.md", "resumo.md") == "O nome continua o mesmo."
    with pytest.raises(ErroDaBiblioteca, match="não está vazia"):
        bib.apagar_pasta("obsidian/direito/OAB")
    with pytest.raises(ErroDaBiblioteca, match="principais"):
        bib.apagar_pasta("obsidian")
    assert len(bib.mudancas) == 2  # mover e renomear pedem nova leitura; criar pasta não


def test_mover_para_onde_ja_tem_um_igual_nao_sobrescreve(bib):
    (bib.base / "documentos_assistente" / "direito" / "resumo.md").write_text("outro")
    assert "como “resumo (1).md”" in bib.mover("obsidian/direito/resumo.md", "documentos/direito")
    assert (bib.base / "documentos_assistente" / "direito" / "resumo.md").read_text() == "outro"


def test_tirar_da_biblioteca_e_apagar_para_a_lixeira(bib):
    assert "Tirei “resumo.md” da biblioteca" in bib.tirar("obsidian/direito/resumo.md")
    assert (bib.base / "fora" / "resumo.md").exists()
    assert "Lixeira" in bib.apagar("documentos/direito/lei.docx")
    assert bib.lixo == ["lei.docx"] and not (bib.base / "documentos_assistente" / "direito" / "lei.docx").exists()
    assert set(_arquivos(bib)) == {"obsidian/direito/aula 1.pdf"}


def test_adicionar_do_celular_e_do_pc(bib, tmp_path):
    assert bib.adicionar("Contrato.pdf", "obsidian/direito", conteudo=b"%PDF") == "Guardei “Contrato.pdf” em Obsidian › direito."
    origem = tmp_path / "escolhido.pptx"
    origem.write_bytes(b"pptx")
    assert "Guardei “escolhido.pptx”" in bib.adicionar(str(origem), "documentos", origem=str(origem))
    assert "não lê esse tipo" in bib.adicionar("planilha.ods", "documentos", conteudo=b"x")
    for nome, conteudo, erro in [("virus.exe", b"MZ", "programas"), ("dados.json", b"{}", "dados"),
                                 ("grande.pdf", b"0" * (1024 * 1024 + 1), "passa de")]:
        with pytest.raises(ErroDaBiblioteca, match=erro):
            bib.adicionar(nome, "documentos", conteudo=conteudo)
    with pytest.raises(ErroDaBiblioteca):
        bib.adicionar("x.pdf", "obsidian/geral", conteudo=b"x")  # pasta pessoal não recebe nada


def test_resultado_vira_mensagem():
    def falha():
        raise ErroDaBiblioteca("Não deu.")

    assert resultado(lambda: "Feito.") == {"ok": True, "mensagem": "Feito."}
    assert resultado(falha) == {"ok": False, "mensagem": "Não deu."}
