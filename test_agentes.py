import json
import os
import threading

import pytest

from agentes import Orquestrador, PainelCompartilhado, planejar
from agentes import geradores
from agentes.critico import verificar_profundidade
from agentes.especialistas import AgentePesquisa


SLIDE = {"titulo": "Benefícios", "topicos": ["Painéis reduzem a conta de luz da casa em até metade",
                                             "A energia do sol é renovável e não polui o ar"],
         "destaque": "Economia e sustentabilidade", "notas": "Explique a economia."}


class LLMFalso:
    """Responde conforme o agente (pelo prompt de sistema) e registra as chamadas."""

    def __init__(self, notas_critico=(8,), slides=6):
        self.notas = list(notas_critico)
        self.slides = slides
        self.chamadas = []

    def chat(self, prompt, system_prompt=None, model=None, usar_historico=False, opcoes=None, formato=None):
        self.chamadas.append(system_prompt.split(".")[0])
        if "Agente de Apresentações" in system_prompt:
            slides = [dict(SLIDE, titulo=f"Slide {i}") for i in range(1, self.slides + 1)]
            return json.dumps({"titulo": "Energia Solar", "subtitulo": "Visão geral", "slides": slides})
        if "Agente de Documentos" in system_prompt:
            paragrafo = "A energia solar converte a luz do sol em eletricidade usando painéis fotovoltaicos instalados no telhado da casa."
            return json.dumps({"titulo": "Energia Solar", "secoes": [
                {"titulo": f"Seção {i}", "paragrafos": [paragrafo] * 3, "topicos": SLIDE["topicos"]} for i in range(3)
            ], "conclusao": "Vale a pena."})
        if "Agente Crítico" in system_prompt:
            nota = self.notas.pop(0) if len(self.notas) > 1 else self.notas[0]
            return json.dumps({"nota": nota, "pontos_fortes": ["Tema certo"], "problemas": [] if nota >= 7 else ["Conteúdo raso"],
                               "correcao": "Aprofunde os tópicos."})
        if "Agente Relator" in system_prompt:
            return "Os agentes montaram a apresentação e o crítico aprovou o resultado."
        return "{}"


def executar(orquestrador, comando):
    pronto = threading.Event()
    tarefa = orquestrador.enviar(comando, ao_concluir=lambda t: pronto.set())
    assert tarefa is not None
    assert pronto.wait(30)
    return tarefa


# ------------------------------------------------------------ reconhecimento
@pytest.mark.parametrize("frase, plano, formatos, slides", [
    ("Beatriz, crie uma apresentação sobre IA na educação com 8 slides", ["apresentacao"], ["pptx"], 8),
    ("faz um pdf sobre a história do Brasil", ["documento"], ["pdf"], 0),
    ("quero um documento em Word e PDF sobre direito do consumidor", ["documento"], ["docx", "pdf"], 0),
    ("gere um relatório com as notícias de hoje sobre economia", ["pesquisa", "documento"], ["docx"], 0),
    ("monte uma apresentação com base nos meus documentos sobre petição inicial", ["biblioteca", "apresentacao"], ["pptx"], 0),
    ("faça uma pesquisa completa sobre carros elétricos", ["pesquisa"], [], 0),
    ("prepare dez slides sobre energia solar", ["apresentacao"], ["pptx"], 10),
])
def test_reconhece_pedidos_de_entrega(frase, plano, formatos, slides):
    tarefa = planejar(frase)
    assert tarefa.plano == plano
    assert tarefa.formatos == formatos
    assert tarefa.quantidade_slides == slides


@pytest.mark.parametrize("frase", [
    "abra o pdf do contrato", "que horas são", "pesquise receitas de bolo",
    "me explica o que é um pdf", "estou cansado do trabalho hoje",
])
def test_conversa_normal_nao_vira_tarefa(frase):
    assert planejar(frase) is None


def test_tema_e_consulta_de_pesquisa():
    tarefa = planejar("faz um pdf sobre as notícias de hoje sobre carros elétricos no Brasil")
    assert AgentePesquisa.consulta(tarefa) == "carros elétricos no Brasil"
    assert planejar("crie uma apresentação sobre energia solar em pdf com 5 slides").tema == "energia solar"


# ------------------------------------------------------------ fluxo completo
def test_fluxo_completo_com_critico_e_relator(tmp_path):
    llm = LLMFalso(notas_critico=(8,), slides=6)
    publicados = {}
    orq = Orquestrador(str(tmp_path), llm=llm, publicar=PainelCompartilhado(publicados), abrir_ao_concluir=False)

    tarefa = executar(orq, "crie uma apresentação com 6 slides sobre energia solar")

    assert tarefa.status == "concluída"
    assert [e.agente for e in tarefa.etapas] == ["apresentacao", "critico", "relator"]
    assert tarefa.arquivos[0].endswith(".pptx") and os.path.exists(tarefa.arquivos[0])
    assert tarefa.avaliacao.cumprido and tarefa.avaliacao.nota == 8
    relatorio = open(tarefa.relatorio, encoding="utf-8").read()
    assert "Agente de Apresentações" in relatorio and "nota do crítico 8/10" in relatorio
    assert "Pronto!" in tarefa.mensagem_final
    painel = PainelCompartilhado.listar(publicados)
    assert painel[0]["status"] == "concluída" and painel[0]["nota"] == 8


def test_critico_reprova_e_agente_corrige(tmp_path):
    llm = LLMFalso(notas_critico=(4, 8))
    orq = Orquestrador(str(tmp_path), llm=llm, abrir_ao_concluir=False, max_revisoes=1)

    tarefa = executar(orq, "faça um documento em word sobre energia solar")

    assert [e.agente for e in tarefa.etapas] == ["documento", "critico", "documento", "critico", "relator"]
    assert [a.cumprido for a in tarefa.avaliacoes] == [False, True]
    assert "depois de 1 revisão" in tarefa.mensagem_final


def test_quantidade_de_slides_e_garantida(tmp_path):
    llm = LLMFalso(slides=9)  # o modelo passou do pedido
    orq = Orquestrador(str(tmp_path), llm=llm, abrir_ao_concluir=False)

    tarefa = executar(orq, "crie uma apresentação com 5 slides sobre energia solar")

    slides = tarefa.contexto["conteudo"]["slides"]
    assert len(slides) == 5
    assert slides[-1]["titulo"] == "Slide 9"  # mantém o slide de conclusão
    assert tarefa.avaliacao.cumprido


def test_profundidade_aceita_topicos_em_texto_e_reprova_palavras_soltas():
    rasos = verificar_profundidade({"slides": [{"titulo": "A", "topicos": ["Baterias", "Inversores"]}]})
    assert rasos and not rasos[0][1]
    # O modelo às vezes devolve um texto onde deveria vir uma lista: conta palavras, não letras.
    em_texto = verificar_profundidade({"slides": [{"titulo": "A", "topicos": "Baterias guardam a energia do dia para a noite"}]})
    assert em_texto[0][1]


def test_comandos_de_gerenciamento(tmp_path):
    abertos = []
    orq = Orquestrador(str(tmp_path), llm=LLMFalso(), abrir=abertos.append, abrir_ao_concluir=False)
    assert orq.responder_gerenciamento("abra o relatório") == "Ainda não há relatório de tarefa concluída."

    tarefa = executar(orq, "crie uma apresentação sobre energia solar")

    assert "energia solar" in orq.responder_gerenciamento("qual o status das tarefas?")
    assert orq.responder_gerenciamento("abra o último documento").startswith("Abrindo")
    assert orq.responder_gerenciamento("abra o relatório").startswith("Abrindo o relatório")
    assert abertos == [tarefa.arquivos[0], tarefa.relatorio]
    assert orq.responder_gerenciamento("abra o documento do contrato de aluguel") is None


def test_geradores_criam_os_tres_formatos(tmp_path):
    conteudo = {"titulo": "Teste de Acentuação", "secoes": [{"titulo": "Seção", "paragrafos": ["Olá, ação!"],
                "tabela": {"colunas": ["A", "B"], "linhas": [["1", "2"]]}}], "fontes": ["https://exemplo.com"]}
    for formato in ("docx", "pdf"):
        assert os.path.getsize(geradores.gerar_arquivo(formato, conteudo, str(tmp_path))) > 1000
    apresentacao = {"titulo": "Teste", "slides": [SLIDE]}
    assert os.path.getsize(geradores.gerar_arquivo("pptx", apresentacao, str(tmp_path))) > 1000


def test_json_cortado_no_fim_aproveita_a_parte_completa():
    from agentes.base import extrair_json

    cortado = '{"titulo": "Energia", "secoes": [{"titulo": "A", "paragrafos": ["um", "dois"]}, {"titulo": "B", "paragr'
    dados = extrair_json(cortado)
    assert dados["titulo"] == "Energia"
    assert [s["titulo"] for s in dados["secoes"]] == ["A"]


def test_correcao_que_falha_mantem_a_primeira_versao(tmp_path):
    class LLMQueQuebraNaCorrecao(LLMFalso):
        def chat(self, prompt, system_prompt=None, **kwargs):
            if "Agente de Documentos" in system_prompt and "REPROVADA" in prompt:
                return '{"titulo": '  # resposta inutilizável na correção
            return super().chat(prompt, system_prompt=system_prompt, **kwargs)

    orq = Orquestrador(str(tmp_path), llm=LLMQueQuebraNaCorrecao(notas_critico=(4,)), abrir_ao_concluir=False)

    tarefa = executar(orq, "faça um documento em word sobre energia solar")

    assert tarefa.status == "concluída"
    assert os.path.exists(tarefa.arquivos[0])
    assert tarefa.contexto["conteudo"]["titulo"] == "Energia Solar"
    assert "mantida a primeira versão" in tarefa.avaliacao.problemas[-1]
    assert "com ressalvas" in tarefa.mensagem_final
