"""Aulas da matéria: plano do curso e uma aula completa por PDF (IAs falsas: não gasta o plano)."""
import json
import os
import re
import threading

import pymupdf
import pytest

from agentes import Orquestrador, planejar, texto_da_entrega
from agentes.especialistas import AgenteBiblioteca, documento_da_aula, problemas_da_aula
from llm_nuvem import LLMComReserva
from test_estudos_claude import AULA, IAFalsa, busca_falsa, ler_falso

PARAGRAFO = ("O controle difuso permite que qualquer juiz deixe de aplicar uma lei inconstitucional no caso concreto, "
             "e a decisão vale só para as partes do processo, sem tirar a lei do ordenamento.")


def plano_falso(aulas=3):
    return {
        "titulo": "Direito Constitucional", "apresentacao": "O curso explica como as leis são conferidas com a Constituição.",
        "aulas": [{"titulo": f"Tema {i}", "objetivos": [f"Entender o tema {i}"], "conteudo": [f"Parte {i}.1", f"Parte {i}.2"],
                   "importante": [f"O ponto que mais cai no tema {i}"]} for i in range(1, aulas + 1)],
        "topicos_importantes": ["Controle difuso", "Controle concentrado", "Efeitos da decisão"],
        "como_estudar": ["Uma aula por dia, com os exercícios"],
    }


def aula_falsa(numero, rasa=False):
    secoes = [{
        "titulo": f"Parte {numero}.{s}", "paragrafos": [PARAGRAFO] * 5, "topicos": ["Qualquer juiz pode fazer o controle difuso"],
        "exemplo": "Um juiz de primeira instância deixa de aplicar uma lei municipal.", "importante": "Cai muito em prova.",
        "tabela": {"colunas": ["Difuso", "Concentrado"], "linhas": [["Qualquer juiz", "Só o STF"]]} if s == 1 else None,
    } for s in range(1, 5)]
    if rasa:
        secoes = [{"titulo": "Parte única", "paragrafos": ["Curto demais."]}]
    return {
        "titulo": f"Tema {numero}", "introducao": f"Nesta aula, o tema {numero}.", "objetivos": ["Saber aplicar o controle difuso"],
        "secoes": secoes, "pontos_importantes": ["O efeito do controle difuso é entre as partes"],
        "exercicios": [] if rasa else [{"enunciado": "Um juiz pode afastar uma lei?", "resolucao": "Sim, no caso concreto, porque..."}],
        "questoes": [{"enunciado": "Quem faz o controle concentrado?", "alternativas": ["Qualquer juiz", "O STF", "O Senado", "O TCU"],
                      "correta": "B", "explicacao": "Só o STF julga a ADI."}],
        "resumo": "O controle difuso é feito no caso concreto.", "para_revisar": ["Qual o efeito do controle difuso?"],
        "fora_das_fontes": [],
    }


class ProfessorFalso(IAFalsa):
    """Responde como a IA: o plano, cada aula (algumas rasas ou quebradas, se pedido), o crítico e o relator."""

    def __init__(self, nome="Claude", ativo=True, aulas=3, rasas=(), quebradas=()):
        super().__init__(nome, ativo)
        self.aulas, self.rasas, self.quebradas = aulas, set(rasas), set(quebradas)
        self._trava = threading.Lock()

    def chat(self, prompt, system_prompt=None, model=None, **kwargs):
        with self._trava:
            self.recebido.append((system_prompt, prompt))
        if "PLANO DO CURSO de uma matéria" in system_prompt:
            return json.dumps(plano_falso(self.aulas), ensure_ascii=False)
        if "UMA AULA COMPLETA" in system_prompt:
            numero = int(re.search(r"Escreva agora a aula (\d+)", prompt).group(1))
            if numero in self.quebradas:
                return "desculpe, não consegui"
            rasa = numero in self.rasas and "foi reprovada" not in prompt
            return json.dumps(aula_falsa(numero, rasa=rasa), ensure_ascii=False)
        return super().chat(prompt, system_prompt=system_prompt, model=model, **kwargs)

    def prompts_de_aula(self):
        return [p for s, p in self.recebido if "UMA AULA COMPLETA" in s]


def montar(tmp_path, professor=None, local=None, publicar=None):
    professor, gemini = professor or ProfessorFalso(), IAFalsa("Gemini")
    local = local or ProfessorFalso("Ollama")
    orq = Orquestrador(str(tmp_path / "saida"), llm=local, abrir_ao_concluir=False, publicar=publicar,
                       llm_nuvem=LLMComReserva([IAFalsa("Claude"), gemini], local),
                       llm_fontes=LLMComReserva([professor], local))
    orq.agentes["biblioteca"] = AgenteBiblioteca(buscar=busca_falsa(tmp_path), ler_arquivo=ler_falso, limite_fontes=200000)
    return orq, professor, gemini, local


def executar(orq, comando):
    pronto = threading.Event()
    tarefa = orq.enviar(comando, ao_concluir=lambda t: pronto.set())
    assert tarefa is not None
    inicio = orq.mensagem_inicio(tarefa)
    assert pronto.wait(30)
    return tarefa, inicio


def texto_do_pdf(caminho):
    # Sem preservar ligaduras: "ficou" sai como "ficou", não como "ﬁcou".
    with pymupdf.open(caminho) as documento:
        return "\n".join(pagina.get_text(flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
                         for pagina in documento)


# ------------------------------------------------------------ reconhecimento
@pytest.mark.parametrize("frase, tema, aulas", [
    ("Beatriz, crie aulas da matéria de direito constitucional", "direito constitucional", 0),
    ("quero aulas de direito penal em pdf", "direito penal", 0),
    ("me ensina toda a matéria de processo civil", "processo civil", 0),
    ("me ensina tudo sobre controle de constitucionalidade", "controle de constitucionalidade", 0),
    ("quero estudar toda a matéria de história do Brasil", "história do Brasil", 0),
    ("crie 5 aulas sobre cálculo 1", "cálculo 1", 5),
    ("crie uma aula sobre a teoria do crime", "a teoria do crime", 1),
    ("monta um curso completo de direito administrativo pra mim", "direito administrativo", 0),
    ("me dá aulas de penal com base nas minhas fontes e me manda pelo zap", "penal", 0),
    ("crie aulas com as fontes que eu coloquei", "suas fontes recentes", 0),
])
def test_reconhece_pedido_de_aulas(frase, tema, aulas):
    tarefa = planejar(frase)
    assert tarefa.tipo == "aulas" and tarefa.plano[0] == "biblioteca" and tarefa.materia
    assert (tarefa.tema, tarefa.quantidades.get("aulas", 0)) == (tema, aulas)
    assert tarefa.fontes_recentes == (tema == "suas fontes recentes")


@pytest.mark.parametrize("frase", ["crie aulas de inglês", "quero cancelar a aula de amanhã", "preciso ir pra aula",
                                   "quero fazer um curso de culinária", "me ensina a fazer bolo"])
def test_frases_parecidas_nao_viram_aulas(frase):
    tarefa = planejar(frase)
    assert tarefa is None or tarefa.tipo != "aulas"


def test_resumo_da_materia_continua_sendo_resumo():
    assert planejar("faz um resumo da matéria de direito constitucional").tipo == "estudos"


# ------------------------------------------------------------ o curso
def test_curso_com_plano_e_uma_aula_por_pdf(tmp_path):
    andamento = []
    orq, professor, gemini, local = montar(tmp_path, publicar=lambda resumo: andamento.append(resumo["etapa"]))

    tarefa, inicio = executar(orq, "crie aulas da matéria de direito constitucional")

    assert tarefa.status == "concluída" and tarefa.avaliacao.cumprido, tarefa.avaliacao.problemas
    assert "com o Claude" in inicio and "plano do curso" in inicio
    nomes = [os.path.basename(a) for a in tarefa.arquivos]
    assert nomes[0] == "00_plano_do_curso.pdf"
    assert [n[:9].lower() for n in nomes[1:]] == ["01_aula_t", "02_aula_t", "03_aula_t"]
    # Só o Claude das fontes trabalhou, com a mesma base (fontes e plano) em todas as aulas.
    assert gemini.chamadas == 0 and not local.recebido
    aulas = professor.prompts_de_aula()
    assert len(aulas) == 3
    bases = {p.split("Escreva agora a aula")[0] for p in aulas}
    assert len(bases) == 1 and AULA in bases.pop()
    assert all("briguei com meu chefe" not in p for _, p in professor.recebido)
    # O PDF da aula ensina: explicação, tabela, destaques, exercícios e questões com gabarito.
    aula1 = texto_do_pdf(tarefa.arquivos[1])
    for trecho in ("Aula 1: Tema 1", "Nesta aula você vai aprender", "Importante: Cai muito em prova.",
                   "Exercício resolvido 1", "Gabarito comentado", "Para revisar", "Só o STF"):
        assert trecho in aula1
    plano = texto_do_pdf(tarefa.arquivos[0])
    assert "Os tópicos mais importantes da matéria" in plano and "Controle concentrado" in plano
    assert "3 aulas (uma por arquivo)" in tarefa.mensagem_final
    assert any(e.startswith("escrevendo as aulas (3 de 3 prontas)") for e in andamento)
    assert "Agente Professor" in open(tarefa.relatorio, encoding="utf-8").read()


def test_quantidade_de_aulas_pedida(tmp_path):
    orq, professor, *_ = montar(tmp_path, ProfessorFalso(aulas=5))
    tarefa, inicio = executar(orq, "crie 2 aulas sobre controle de constitucionalidade")
    assert "2 aulas" in inicio
    plano = next(p for s, p in professor.recebido if "PLANO DO CURSO de uma matéria" in s)
    assert "Quantidade de aulas: exatamente 2." in plano
    assert len(tarefa.contexto["conteudo"]["aulas"]) == 2 and len(tarefa.arquivos) == 3


def test_so_as_aulas_fracas_sao_reescritas(tmp_path):
    orq, professor, *_ = montar(tmp_path, ProfessorFalso(rasas={2}))

    tarefa, _ = executar(orq, "crie aulas da matéria de direito constitucional")

    primeira = tarefa.avaliacoes[0]
    assert not primeira.cumprido and any(d.startswith("Aula 2:") and not ok for d, ok in primeira.verificacoes)
    aulas = professor.prompts_de_aula()
    assert len(aulas) == 4 and "foi reprovada" in aulas[-1] and "Escreva agora a aula 2" in aulas[-1]
    assert tarefa.avaliacao.cumprido and not problemas_da_aula(tarefa.contexto["conteudo"]["aulas"][1])


def test_aula_que_falha_nao_derruba_o_curso(tmp_path):
    orq, professor, *_ = montar(tmp_path, ProfessorFalso(quebradas={3}))

    tarefa, _ = executar(orq, "crie aulas da matéria de direito constitucional")

    assert tarefa.status == "concluída" and not tarefa.avaliacao.cumprido
    assert len(tarefa.arquivos) == 3  # plano + aulas 1 e 2
    assert "Esta aula não ficou pronta" in texto_do_pdf(tarefa.arquivos[0])
    assert "com ressalvas" in tarefa.mensagem_final


def test_sem_claude_as_aulas_saem_no_computador_sem_o_gemini(tmp_path):
    orq, professor, gemini, local = montar(tmp_path, ProfessorFalso(ativo=False))
    tarefa, inicio = executar(orq, "crie aulas da matéria de direito constitucional")
    assert "aqui no computador" in inicio
    assert professor.chamadas == 0 and gemini.chamadas == 0 and local.prompts_de_aula()
    assert "O Claude não pôde escrever agora" in tarefa.mensagem_final


def test_mensagem_do_whatsapp_lista_as_aulas_e_os_topicos(tmp_path):
    orq, *_ = montar(tmp_path)
    tarefa, _ = executar(orq, "crie aulas da matéria de direito constitucional")
    texto = texto_da_entrega(tarefa)
    assert "*Aulas:*\n1. Tema 1\n2. Tema 2\n3. Tema 3" in texto and "• Controle difuso" in texto


def test_documento_da_aula():
    documento = documento_da_aula({**aula_falsa(1), "numero": 1}, "Direito Constitucional", ["Biblioteca: aula1.md"])
    titulos = [s["titulo"] for s in documento["secoes"]]
    assert titulos[0] == "Nesta aula você vai aprender" and "Parte 1.1" in titulos
    assert {"Pontos importantes (os que mais caem)", "Exercício resolvido 1", "Questão 1", "Gabarito comentado",
            "Resumo da aula", "Para revisar"} <= set(titulos)
    assert documento["titulo"] == "Aula 1: Tema 1" and documento["subtitulo"] == "Direito Constitucional"
    assert problemas_da_aula(aula_falsa(1)) == []
    assert len(problemas_da_aula(aula_falsa(1, rasa=True))) == 3
