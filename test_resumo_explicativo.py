"""Resumo explicativo: pedido, tema, fontes e o texto final, sem rede e sem IA."""
import os

import main
import resumo_explicativo as re_

RESPOSTA_DA_IA = """FALA: A fotossíntese é como as plantas fazem o próprio alimento com a luz do sol. Elas usam gás carbônico e água e soltam oxigênio.
O QUE É: Fotossíntese é o processo pelo qual plantas, algas e algumas bactérias transformam luz em energia química.
CONTEXTO: Acontece nos cloroplastos, onde a clorofila capta a luz.
PONTOS PRINCIPAIS:
- Usa luz, água e gás carbônico.
- Produz glicose e oxigênio.
- Sustenta quase toda a cadeia alimentar.
POR QUE IMPORTA: É a origem do oxigênio que respiramos.
EXEMPLO: Uma folha ao sol produz bolhas de oxigênio na água.
NOVIDADES: As fontes não trazem novidades recentes.
PARA LEMBRAR: Luz entra, alimento e oxigênio saem."""


def test_reconhece_pedidos_de_resumo_e_deixa_os_outros_resumos_para_quem_cuida():
    for frase in ["Beatriz, faz um resumo sobre a reforma tributária", "resume pra mim a revolução francesa",
                  "me dá um resumo explicativo da fotossíntese pelo Gemini", "pesquisa e me explica a inflação"]:
        assert re_.pede_resumo_explicativo(frase), frase
    for frase in ["resuma meus e-mails", "qual o resumo do dia", "resume a conversa", "resumo das notícias",
                  "me explica rapidinho o que é fotossíntese", "faz um resumo para a prova de história"]:
        assert not re_.pede_resumo_explicativo(frase), frase


def test_tema_sai_limpo_do_pedido():
    assert re_.tema_do_pedido("Beatriz, faz um resumo sobre a reforma tributária") == "reforma tributária"
    assert re_.tema_do_pedido("resume pra mim a revolução francesa") == "revolução francesa"
    assert re_.tema_do_pedido("me dá um resumo explicativo da fotossíntese") == "fotossíntese"
    assert re_.tema_do_pedido("pesquisa e me explica como funciona a inflação") == "como funciona a inflação"
    assert re_.preparar_pedido_de_resumo("me dá um resumo da guerra fria pelo Gemini") == ("guerra fria", "gemini")
    # "Faz um resumo disso" pega o assunto da última pergunta da conversa.
    historico = [("qual o melhor celular da Samsung?", "O Galaxy S26 Ultra.")]
    assert re_.preparar_pedido_de_resumo("faz um resumo disso", historico) == ("qual o melhor celular da Samsung?", None)
    assert re_.preparar_pedido_de_resumo("faz um resumo disso", []) is None


def test_wikipedia_pula_desambiguacao(monkeypatch):
    def consultar(**parametros):
        if parametros.get("list") == "search":
            return {"query": {"search": [{"title": "Mercúrio"}, {"title": "Mercúrio (planeta)"}]}}
        textos = {"Mercúrio": "Mercúrio pode referir-se a: o planeta, o elemento, o deus.",
                  "Mercúrio (planeta)": "Mercúrio é o menor planeta do Sistema Solar. " * 20}
        return {"query": {"pages": {"1": {"extract": textos[parametros["titles"]]}}}}

    monkeypatch.setattr(re_, "_consultar_wikipedia", consultar)
    artigo = re_.buscar_wikipedia("mercúrio")
    assert artigo["titulo"] == "Mercúrio (planeta)"
    assert artigo["url"] == "https://pt.wikipedia.org/wiki/Merc%C3%BArio_%28planeta%29"


def test_gera_resumo_curto_para_falar_completo_para_ler_e_word(monkeypatch, tmp_path):
    import llm_nuvem

    fontes = {"wikipedia": {"titulo": "Fotossíntese", "texto": "Fotossíntese é o processo...",
                            "url": "https://pt.wikipedia.org/wiki/Fotoss%C3%ADntese"},
              "web": "[Fonte: Brasil Escola]\nURL: https://brasilescola.uol.com.br/fotossintese\ntexto",
              "manchetes": [("Estudo sobre fotossíntese artificial", "", "Notícia publicada em Mon, 13 Oct 2026")]}
    monkeypatch.setattr(re_, "reunir_fontes", lambda tema: fontes)
    monkeypatch.setattr(re_, "PASTA_DOCUMENTOS_GERADOS", str(tmp_path))
    pedidos = []
    monkeypatch.setattr(llm_nuvem, "responder_na_nuvem",
                        lambda prompt, contexto="", opcoes=None, preferir=None, registro=None:
                        pedidos.append((prompt, preferir)) or RESPOSTA_DA_IA)

    resumo = re_.gerar_resumo("fotossíntese", preferir="gemini")
    prompt, preferir = pedidos[0]
    assert preferir == "gemini"
    assert "[Wikipédia: Fotossíntese]" in prompt and "Estudo sobre fotossíntese artificial" in prompt

    assert resumo.fala.startswith("A fotossíntese é como as plantas fazem o próprio alimento")
    assert resumo.fala.endswith("O resumo completo está no painel e num Word, na pasta documentos gerados.")
    assert "Pontos principais\n• Usa luz, água e gás carbônico.\n• Produz glicose e oxigênio." in resumo.texto
    assert "https://brasilescola.uol.com.br/fotossintese" in resumo.texto
    assert os.path.exists(resumo.arquivo) and resumo.arquivo.endswith(".docx")


def test_sem_formato_ou_sem_fontes(monkeypatch, tmp_path):
    import llm_nuvem

    monkeypatch.setattr(re_, "PASTA_DOCUMENTOS_GERADOS", str(tmp_path))
    monkeypatch.setattr(re_, "reunir_fontes", lambda tema: {"wikipedia": None, "web": "", "manchetes": []})
    assert "Não encontrei fontes sobre o tema" in re_.gerar_resumo("o tema").fala

    # A IA ignorou os rótulos: o texto vira o resumo e as primeiras frases viram a fala.
    monkeypatch.setattr(re_, "reunir_fontes", lambda tema: {"wikipedia": None, "web": "texto", "manchetes": []})
    monkeypatch.setattr(llm_nuvem, "responder_na_nuvem",
                        lambda prompt, contexto="", opcoes=None, preferir=None, registro=None:
                        "Primeira frase. Segunda frase. Terceira frase. Quarta frase.")
    resumo = re_.gerar_resumo("algo")
    assert resumo.fala.startswith("Primeira frase. Segunda frase. Terceira frase.")
    assert "O que é\nPrimeira frase." in resumo.texto


def test_ollama_recebe_contexto_maior_quando_a_nuvem_nao_responde(monkeypatch, tmp_path):
    import llm_nuvem

    monkeypatch.setattr(re_, "PASTA_DOCUMENTOS_GERADOS", str(tmp_path))
    monkeypatch.setattr(re_, "reunir_fontes", lambda tema: {"wikipedia": None, "web": "texto", "manchetes": []})
    monkeypatch.setattr(llm_nuvem, "responder_na_nuvem", lambda *a, **k: None)
    opcoes_usadas = []
    monkeypatch.setattr(main.llm_clients, "chat_beatriz",
                        lambda prompt, contexto=None, usar_historico=True, opcoes=None:
                        opcoes_usadas.append(opcoes) or RESPOSTA_DA_IA)
    re_.gerar_resumo("fotossíntese")
    assert opcoes_usadas[0]["num_ctx"] == 8192
