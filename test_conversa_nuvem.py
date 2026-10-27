"""A conversa e a nuvem: "usa o Claude pra…", "pesquisa…" e as perguntas de conhecimento geral (sem nuvem de verdade)."""
import conversa_nuvem
import llm_nuvem
import main
import painel_web as pw
from llm_nuvem import extrair_ia_pedida


def test_ia_pedida_com_usa_pergunta_e_pede():
    # Até out/2026 só "pelo/no/com o" contavam: "USA o Claude pra produzir uma peça" ia para o PC.
    assert extrair_ia_pedida("USA o Claude pra produzir uma peça correspondente") == ("produzir uma peça correspondente", "claude")
    assert extrair_ia_pedida("pergunta pro Gemini o que é usucapião") == ("o que é usucapião", "gemini")
    assert extrair_ia_pedida("pede pro Claude fazer um resumo") == ("fazer um resumo", "claude")
    assert extrair_ia_pedida("me explica usando o Claude o que é dolo") == ("me explica o que é dolo", "claude")
    assert extrair_ia_pedida("pesquise pelo Cláudio Castro")[1] is None
    assert extrair_ia_pedida("vou usar a cabeça")[1] is None


def test_categoria_conhecimento_separada_do_papo():
    assert conversa_nuvem.categoria_da_resposta("conhecimento") == "conhecimento"
    assert conversa_nuvem.categoria_da_resposta("Conhecimento geral") == "conhecimento"
    assert conversa_nuvem.categoria_da_resposta("Explicação") == "conhecimento"
    assert conversa_nuvem.categoria_da_resposta("simples") == "simples"
    assert conversa_nuvem.categoria_da_resposta("conversa") == "simples"


def test_pergunta_com_algo_pessoal_fica_no_pc():
    assert conversa_nuvem.pode_ir_para_a_nuvem("me explica o que é usucapião")
    assert conversa_nuvem.pode_ir_para_a_nuvem("explica pra mim a teoria da relatividade")
    assert not conversa_nuvem.pode_ir_para_a_nuvem("o que cai na minha prova de amanhã?")
    assert not conversa_nuvem.pode_ir_para_a_nuvem("eu tenho direito a férias depois de 6 meses?")


def test_responder_manda_so_a_pergunta_e_avisa_quem_respondeu():
    pedidos = []

    def nuvem(prompt, contexto, **opcoes):
        pedidos.append((prompt, opcoes))
        opcoes["registro"].append("Gemini")
        return "Usucapião é a aquisição da propriedade pela posse prolongada."

    texto = conversa_nuvem.responder("o que é usucapião?", nuvem=nuvem)
    assert texto.startswith("Usucapião")
    prompt, opcoes = pedidos[0]
    assert prompt.startswith("Pergunta: o que é usucapião?") and "2 a 4 frases" in prompt
    assert opcoes["funcao"] == "pesquisa" and opcoes["preferir"] is None

    # O Claude foi pedido, mas quem respondeu foi o Gemini: a resposta diz isso.
    texto = conversa_nuvem.responder("o que é dolo?", preferir="claude", nuvem=nuvem, falada=False)
    assert texto.startswith("O Claude não pôde responder agora; respondi pelo Gemini.")
    assert "três parágrafos" in pedidos[-1][0]


def test_sem_nenhuma_ia_da_nuvem_devolve_none():
    assert conversa_nuvem.responder("o que é dolo?", nuvem=lambda *a, **k: None) is None
    assert conversa_nuvem.responder("o que é dolo?", nuvem=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rede"))) is None


def test_pesquisa_falada_vai_para_a_pesquisa_com_a_ia_pedida(monkeypatch):
    pedidos = []
    monkeypatch.setattr(main, "responder_com_pesquisa_web",
                        lambda consulta, salvar=False, preferir=None, resumo=False: pedidos.append((consulta, preferir)) or "ok")
    # Antes virava a hora ("hoje") ou o clima ("previsão").
    assert main.tentar_executar_ferramenta("Pesquisa no Gemini a previsão do tempo de hoje à noite") == (True, "ok")
    assert pedidos[-1] == ("a previsão do tempo de hoje à noite", "gemini")
    main.tentar_executar_ferramenta("procura o preço do dólar")
    assert pedidos[-1] == ("o preço do dólar", None)
    main.tentar_executar_ferramenta("pesquisar sobre a reforma tributária")
    assert pedidos[-1] == ("sobre a reforma tributária", None)


def test_painel_mostra_a_pausa_de_qualquer_instancia():
    class Falsa:
        def __init__(self, nome, estado):
            self.nome, self.estado = nome, estado

        def status(self):
            return ("limite atingido · 30 min", "pausado") if self.estado == "pausado" else ("3 usos hoje", "ativo")

    original = (llm_nuvem.claude, llm_nuvem.gemini)
    try:
        llm_nuvem.claude, llm_nuvem.gemini = Falsa("Claude", "ativo"), Falsa("Gemini", "ativo")
        status = llm_nuvem.status_para_o_painel(Falsa("Claude", "pausado"))
    finally:
        llm_nuvem.claude, llm_nuvem.gemini = original
    assert status == [{"nome": "Claude", "texto": "limite atingido · 30 min", "estado": "pausado"},
                      {"nome": "Gemini", "texto": "3 usos hoje", "estado": "ativo"}]


def test_painel_le_o_estado_da_nuvem_do_processo_principal(monkeypatch):
    monkeypatch.setattr(pw.ApiPainel, "_consultar_ollama", staticmethod(lambda: {"online": True}))
    monkeypatch.setattr(pw.ApiPainel, "_contar_biblioteca", staticmethod(lambda: {}))
    nuvem = [{"nome": "Claude", "texto": "limite atingido · 5 min", "estado": "pausado"}]
    assert pw.ApiPainel(None, {"nuvem": nuvem}, None).estado()["nuvem"] == nuvem


def test_chat_do_painel_manda_conhecimento_para_a_nuvem(monkeypatch):
    api = pw.ApiPainel(None, {}, None)
    monkeypatch.setattr(pw, "falar_async", lambda texto: None)
    monkeypatch.setattr(pw.conversa_nuvem, "classificar", lambda comando, llm: "conhecimento")
    pedidos = []
    monkeypatch.setattr(pw.conversa_nuvem, "responder",
                        lambda pergunta, preferir=None, falada=True: pedidos.append((pergunta, preferir)) or "Resposta da nuvem.")
    monkeypatch.setattr(pw, "notelooklm_responder", lambda comando: (_ for _ in ()).throw(AssertionError("não é RAG")))
    api._processar("qual a diferença entre dolo e culpa?")
    assert api.eventos()[-1]["texto"] == "Resposta da nuvem."
    api._processar("pergunta pro Claude quem foi Rui Barbosa")
    assert pedidos[-1] == ("quem foi Rui Barbosa", "claude")


def test_chat_do_painel_papo_continua_na_biblioteca_local(monkeypatch):
    api = pw.ApiPainel(None, {}, None)
    monkeypatch.setattr(pw, "falar_async", lambda texto: None)
    monkeypatch.setattr(pw.conversa_nuvem, "classificar", lambda comando, llm: "simples")
    monkeypatch.setattr(pw.conversa_nuvem, "responder", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não é nuvem")))
    monkeypatch.setattr(pw, "notelooklm_responder", lambda comando: "Resposta local.")
    api._processar("tô cansado hoje")
    assert api.eventos()[-1]["texto"] == "Resposta local."
