"""Resumo da matéria com o Claude lendo as fontes do usuário (sem gastar o plano: IAs falsas)."""
import json
import os
import threading
import time

import pytest

import config
import memory
from agentes import Orquestrador, planejar
from agentes.especialistas import (
    LIMITE_POR_FONTE,
    TRECHOS_MATERIA,
    AgenteBiblioteca,
    documento_de_estudo,
    fonte_pessoal,
    fontes_recentes,
)
from llm_nuvem import LLMComReserva
from test_agentes_novos import LLMNovos


def material_da_materia(topicos=6, cartoes=20, questoes=10):
    return {
        "titulo": "Controle de Constitucionalidade",
        "resumo": "Visão geral de como o Judiciário confere se as leis respeitam a Constituição.",
        "topicos": [{
            "titulo": f"Tópico {i}",
            "paragrafos": ["O controle difuso é feito por qualquer juiz no caso concreto, com efeito só entre as partes.",
                           "Ele existe porque todo juiz deve aplicar a Constituição antes de uma lei que a contrarie."],
            "pontos_chave": ["Qualquer juiz pode afastar uma lei inconstitucional no caso concreto"],
            "exemplo": "Um juiz de primeira instância deixa de aplicar uma lei municipal.",
            "atencao": "Não confundir com o controle concentrado, feito pelo STF.",
            "fonte": "aula1.md",
        } for i in range(1, topicos + 1)],
        "glossario": [{"termo": "ADI", "definicao": "Ação direta de inconstitucionalidade."}],
        "flashcards": [{"frente": f"Pergunta {i}", "verso": f"Resposta {i}"} for i in range(cartoes)],
        "questoes": [{"enunciado": f"Questão {i}?", "alternativas": ["um", "dois", "três", "quatro"],
                      "correta": "C", "explicacao": "Porque a C está nas fontes."} for i in range(questoes)],
        "dicas": ["Revise os tópicos em voz alta"],
        "fora_das_fontes": ["Histórico do controle no Brasil"],
    }


class IAFalsa:
    """Imita um provedor da nuvem: responde pelos agentes e guarda tudo o que recebeu."""

    def __init__(self, nome="Claude", ativo=True, topicos=6):
        self.nome, self.modelo, self.ativo, self.topicos = nome, f"{nome.lower()}-teste", ativo, topicos
        self.recebido = []

    @property
    def chamadas(self):
        return len(self.recebido)

    def disponivel(self):
        return self.ativo

    def chat(self, prompt, system_prompt=None, model=None, **kwargs):
        self.recebido.append((system_prompt, prompt))
        if "Agente de Estudos" in system_prompt:
            return json.dumps(material_da_materia(self.topicos), ensure_ascii=False)
        if "Agente Crítico" in system_prompt:
            return json.dumps({"nota": 9, "pontos_fortes": ["Completo"], "problemas": [], "correcao": ""})
        if "Agente Relator" in system_prompt:
            return "Os agentes leram as fontes e prepararam o resumo da matéria."
        return "{}"


AULA = "Aula 1. Controle difuso: qualquer juiz, no caso concreto, pode deixar de aplicar uma lei inconstitucional."
TEXTOS = {"aula1.md": AULA, "livro.pdf": "x" * (LIMITE_POR_FONTE + 10), "segredo.md": "Hoje briguei com meu chefe."}


def ler_falso(caminho, limite):
    return TEXTOS.get(os.path.basename(caminho), "")[:limite]


def busca_falsa(tmp_path):
    diario = os.path.join(config.PASTAS_OBSIDIAN["diario"], "segredo.md")
    blocos = [
        ("aula1.md", "documento_local", "documento", str(tmp_path / "aula1.md"), AULA),
        ("livro.pdf", "documento_local", "páginas 40 a 41", str(tmp_path / "livro.pdf"), "Trecho do livro sobre a ADI."),
        ("segredo.md", "obsidian", "documento", diario, "Hoje briguei com meu chefe."),
        ("historico_conversas.jsonl", "historico", "registro 3", "c:/beatriz/historico_conversas.jsonl", "Pergunta antiga minha."),
    ]
    resultado = "\n\n".join(f"[Fonte: {a} | {t} | {l} | {c}]\n{texto}" for a, t, l, c, texto in blocos)
    return lambda consulta: resultado


def montar(tmp_path, claude_fontes=None, com_fontes=True, **biblioteca):
    local, claude_nuvem, gemini = LLMNovos(), IAFalsa("Claude"), IAFalsa("Gemini")
    claude_fontes = claude_fontes or IAFalsa("Claude")
    orq = Orquestrador(
        str(tmp_path / "saida"), llm=local, abrir_ao_concluir=False,
        llm_nuvem=LLMComReserva([claude_nuvem, gemini], local),
        llm_fontes=LLMComReserva([claude_fontes], local) if com_fontes else None,
    )
    biblioteca.setdefault("buscar", busca_falsa(tmp_path))
    orq.agentes["biblioteca"] = AgenteBiblioteca(ler_arquivo=ler_falso, limite_fontes=200000, **biblioteca)
    return orq, claude_fontes, claude_nuvem, gemini, local


def executar(orq, comando):
    pronto = threading.Event()
    tarefa = orq.enviar(comando, ao_concluir=lambda t: pronto.set())
    assert tarefa is not None
    inicio = orq.mensagem_inicio(tarefa)
    assert pronto.wait(30)
    return tarefa, inicio


# ------------------------------------------------------------ reconhecimento
@pytest.mark.parametrize("frase, tema, recentes", [
    ("Beatriz, faz um resumo da matéria de direito constitucional", "direito constitucional", False),
    ("resume toda a matéria de história do Brasil pra eu estudar", "história do Brasil", False),
    ("quero estudar a matéria de cálculo 1, faz um resumo completo", "cálculo 1", False),
    ("resumo detalhado da matéria de processo civil com base nas minhas fontes", "processo civil", False),
    ("resume as fontes que eu coloquei sobre direito penal", "direito penal", False),
    ("faz um resumo das minhas fontes de direito penal e me manda pelo zap", "direito penal", False),
    ("resume as fontes que eu coloquei", "suas fontes recentes", True),
    ("analisa os arquivos que eu mandei e faz um resumo", "suas fontes recentes", True),
])
def test_reconhece_resumo_da_materia(frase, tema, recentes):
    tarefa = planejar(frase)
    assert tarefa.plano == ["biblioteca", "estudos"] and tarefa.materia
    assert (tarefa.tema, tarefa.fontes_recentes) == (tema, recentes)
    assert (tarefa.quantidade_estudo("flashcards"), tarefa.quantidade_estudo("questoes")) == (20, 10)


@pytest.mark.parametrize("frase", ["resume essa matéria do G1", "faz um resumo da matéria aberta no navegador",
                                   "faz um resumo sobre fotossíntese", "manda as fontes que eu coloquei"])
def test_noticias_e_outros_resumos_seguem_o_caminho_de_antes(frase):
    tarefa = planejar(frase)
    assert tarefa is None or not tarefa.materia


def test_estudo_sem_materia_continua_igual():
    tarefa = planejar("me ajude a estudar direito constitucional para a prova")
    assert tarefa.plano == ["estudos"] and not tarefa.materia
    assert tarefa.quantidade_estudo("flashcards") == 12


# ------------------------------------------------------------ fontes
def test_fontes_pessoais_nunca_entram():
    assert fonte_pessoal(config.ARQUIVO_MEMORIA_USUARIO)
    assert fonte_pessoal(os.path.join(config.PASTAS_OBSIDIAN["diario"], "2026-10-14.md"))
    assert fonte_pessoal("c:/beatriz/historico_conversas.jsonl")
    assert not fonte_pessoal(os.path.join(config.PASTAS_OBSIDIAN["direito"], "aula1.md"))


def test_fontes_recentes_pela_chegada_na_pasta(tmp_path):
    for nome in ("aula1.pdf", "aula2.docx", "estado.json", "rascunho.exe"):
        (tmp_path / nome).write_text("x", encoding="utf-8")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "config.md").write_text("x", encoding="utf-8")
    antigo = time.time() - 400 * 86400
    os.utime(tmp_path / "aula1.pdf", (antigo, antigo))  # a cópia mantém a data do original; a chegada é hoje

    nomes = {os.path.basename(c) for c in fontes_recentes([str(tmp_path)])}
    assert nomes == {"aula1.pdf", "aula2.docx"}
    # Nada na última semana: os mais novos, até três.
    assert len(fontes_recentes([str(tmp_path)], agora=time.time() + 30 * 86400)) == 2


def test_busca_da_biblioteca_aceita_mais_trechos(monkeypatch):
    chamadas = []
    monkeypatch.setattr(memory, "buscar_nos_documentos_modo", lambda consulta, modo=None, quantidade_trechos=None: chamadas.append(quantidade_trechos) or "")
    biblioteca = AgenteBiblioteca()
    biblioteca._buscar_para_materia("controle de constitucionalidade")
    assert chamadas == [TRECHOS_MATERIA]


def test_quantidade_de_trechos_da_busca(monkeypatch):
    class Colecao:
        def count(self):
            return 100

        def query(self, query_texts, n_results, where_document=None):
            ids = [f"t{i}" for i in range(n_results)]
            return {"ids": [ids], "documents": [[f"Trecho {i} sobre controle de constitucionalidade" for i in range(n_results)]],
                    "metadatas": [[{"source_id": i, "hash": i, "arquivo": f"{i}.md", "caminho": memory.CAMINHO_DOCUMENTOS} for i in ids]],
                    "distances": [[0.1 * i for i in range(n_results)]]}

    monkeypatch.setattr(memory, "colecao_docs", Colecao())
    monkeypatch.setattr(memory, "deve_consultar_documentos", lambda pergunta, modo=None: True)
    monkeypatch.setattr(memory, "_registrar_auditoria", lambda *a, **k: None)
    padrao = memory.buscar_nos_documentos_modo("controle de constitucionalidade", modo="padrão")
    amplo = memory.buscar_nos_documentos_modo("controle de constitucionalidade", modo="padrão", quantidade_trechos=12)
    assert padrao.count("[Fonte:") == memory.TRECHOS_POR_RESPOSTA and amplo.count("[Fonte:") == 12


# ------------------------------------------------------------ a tarefa
def test_resumo_da_materia_so_o_claude_le_as_fontes(tmp_path):
    orq, claude_fontes, claude_nuvem, gemini, local = montar(tmp_path)

    tarefa, inicio = executar(orq, "faz um resumo da matéria de direito constitucional")

    assert tarefa.status == "concluída" and tarefa.avaliacao.cumprido, tarefa.avaliacao.problemas
    assert "com o Claude" in inicio and "glossário" in inicio
    # Só o Claude das fontes trabalhou: nem o Gemini nem a cadeia comum receberam nada.
    assert gemini.chamadas == 0 and claude_nuvem.chamadas == 0 and not local.prompts
    sistema, prompt = next(r for r in claude_fontes.recebido if "Agente de Estudos" in r[0])
    assert "resumo completo de uma matéria" in sistema
    assert AULA in prompt and "[Fonte: aula1.md | arquivo inteiro]" in prompt
    assert "[Fonte: livro.pdf | páginas 40 a 41]" in prompt and "xxxx" not in prompt  # grande demais: só os trechos
    assert "briguei com meu chefe" not in prompt and "Pergunta antiga minha" not in prompt
    assert prompt.index("[Fonte: aula1.md") < prompt.index("Pedido do usuário")  # fontes antes do pedido
    # O material: PDF, Anki, glossário e o que veio de fora das fontes.
    pdf, anki = tarefa.arquivos
    assert pdf.endswith(".pdf") and anki.endswith("_flashcards_anki.txt")
    assert "Claude (claude-teste), que leu as suas fontes" in open(tarefa.relatorio, encoding="utf-8").read()
    assert "6 tópicos, 20 flashcards e 10 questões" in tarefa.mensagem_final


def test_resume_as_fontes_que_eu_coloquei(tmp_path):
    recentes = [str(tmp_path / "aula1.md"), os.path.join(config.PASTAS_OBSIDIAN["diario"], "segredo.md")]
    orq, claude_fontes, *_ = montar(tmp_path, listar_recentes=lambda: recentes)

    tarefa, inicio = executar(orq, "resume as fontes que eu coloquei")

    assert tarefa.status == "concluída"
    assert "resumir suas fontes mais recentes (aula1.md)" in inicio  # o diário ficou de fora
    prompt = next(p for s, p in claude_fontes.recebido if "Agente de Estudos" in s)
    assert AULA in prompt and "acabou de colocar" in prompt and "briguei" not in prompt


def test_sem_fontes_recentes_explica_como_colocar(tmp_path):
    orq, claude_fontes, *_ = montar(tmp_path, listar_recentes=lambda: [])
    tarefa, inicio = executar(orq, "resume as fontes que eu coloquei")
    assert "vou procurar as fontes" in inicio
    assert tarefa.status == "não realizada" and "Anexe os arquivos no painel" in tarefa.mensagem_final
    assert claude_fontes.chamadas == 0


def test_claude_em_pausa_faz_no_computador_sem_o_gemini(tmp_path):
    orq, claude_fontes, claude_nuvem, gemini, local = montar(tmp_path, claude_fontes=IAFalsa("Claude", ativo=False))

    tarefa, inicio = executar(orq, "faz um resumo da matéria de direito constitucional")

    assert "aqui no computador" in inicio
    assert claude_fontes.chamadas == 0 and gemini.chamadas == 0 and claude_nuvem.chamadas == 0
    assert local.prompts and tarefa.status == "concluída"
    prompt = next(p for s, p in local.prompts if "Agente de Estudos" in s)
    assert len(prompt) < 6000  # o modelo local recebe só os trechos que cabem nele
    assert "O Claude não pôde escrever agora" in tarefa.mensagem_final


def test_com_estudos_com_claude_desligado_tudo_fica_local(tmp_path):
    orq, claude_fontes, claude_nuvem, gemini, local = montar(tmp_path, com_fontes=False)
    tarefa, _ = executar(orq, "faz um resumo da matéria de direito constitucional")
    assert gemini.chamadas == 0 and claude_nuvem.chamadas == 0 and local.prompts
    assert not tarefa.fontes_no_claude


def test_gemini_pedido_fica_de_fora_das_fontes(tmp_path):
    orq, claude_fontes, claude_nuvem, gemini, _ = montar(tmp_path)
    tarefa, inicio = executar(orq, "faz um resumo da matéria de direito penal pelo Gemini")
    assert "O Gemini fica de fora: suas fontes vão só para o Claude." in inicio
    assert gemini.chamadas == 0 and claude_fontes.chamadas


def test_material_raso_reprova_no_critico(tmp_path):
    orq, *_ = montar(tmp_path, claude_fontes=IAFalsa("Claude", topicos=3))
    tarefa, _ = executar(orq, "faz um resumo da matéria de direito constitucional")
    assert any("Matéria em 3 tópicos (mínimo 5)" in d and not ok for d, ok in tarefa.avaliacoes[0].verificacoes)


def test_documento_da_materia_tem_glossario_pegadinhas_e_complementos():
    documento = documento_de_estudo(material_da_materia(), materia=True)
    titulos = [s["titulo"] for s in documento["secoes"]]
    assert {"Glossário", "Flashcards", "Gabarito comentado", "Complementado além das suas fontes"} <= set(titulos)
    primeiro = documento["secoes"][0]
    assert len(primeiro["paragrafos"]) == 5  # dois parágrafos + exemplo, atenção e fonte
    assert primeiro["paragrafos"][3].startswith("Atenção: ")
    assert documento["subtitulo"].startswith("Resumo da matéria")
