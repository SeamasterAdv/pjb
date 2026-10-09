import main
import memory
import voice
from main import comando_solicita_encerramento, resposta_sem_contexto, deve_pesquisar_web
from memory import deve_consultar_documentos


def test_relevant_questions_use_documents():
    assert deve_consultar_documentos("Como devo responder com clareza?", modo="padrão") is True
    assert deve_consultar_documentos("Me ajude com uma resposta direta", modo="padrão") is True


def test_simple_system_questions_do_not_require_documents():
    assert deve_consultar_documentos("qual a hora agora?", modo="padrão") is False
    assert deve_consultar_documentos("qual é a data de hoje?", modo="padrão") is False
    assert deve_consultar_documentos("me lembre de amanhã", modo="padrão") is False
    assert deve_consultar_documentos("quais são meus compromissos?", modo="padrão") is False


def test_explicit_disable_still_works():
    assert deve_consultar_documentos("Não use documentos para responder", modo="padrão") is False


def test_busca_documentos_nao_percorre_colecao_inteira(monkeypatch):
    pasta_direito = memory.MODOS_PASTAS["direito"]

    class ColecaoBuscaLimitada:
        def count(self):
            return 5000

        def query(self, query_texts, n_results, where_document=None):
            assert query_texts == ["competência tributária"]
            assert n_results <= 24
            return {
                "documents": [["Conteúdo geral", "Conteúdo sobre competência tributária"]],
                "metadatas": [[
                    {"source_id": "geral", "hash": "h1", "arquivo": "geral.pdf", "tipo_fonte": "documento_local", "localizador": "página 1", "caminho": memory.CAMINHO_DOCUMENTOS},
                    {"source_id": "direito", "hash": "h2", "arquivo": "tributario.pdf", "tipo_fonte": "documento_local", "localizador": "página 2", "caminho": pasta_direito},
                ]],
            }

        def get(self, **kwargs):
            raise AssertionError("A busca não deve carregar a coleção inteira")

    monkeypatch.setattr(memory, "colecao_docs", ColecaoBuscaLimitada())
    monkeypatch.setattr(memory, "deve_consultar_documentos", lambda pergunta, modo=None: True)
    monkeypatch.setattr(memory, "_registrar_auditoria", lambda *args, **kwargs: None)

    resposta = memory.buscar_nos_documentos_modo("competência tributária", modo="direito")

    assert "tributario.pdf" in resposta
    assert "geral.pdf" not in resposta


def test_web_search_fallback_triggers_on_missing_context():
    assert resposta_sem_contexto("Ainda não encontrei contexto suficiente para responder.") is True
    assert deve_pesquisar_web("Posso ajudar, mas ainda não encontrei contexto suficiente para responder com segurança.") is True
    assert deve_pesquisar_web("") is True


def test_web_search_opens_browser_before_search(monkeypatch):
    chamadas = []

    def fake_abrir_navegador(consulta):
        chamadas.append(("abrir", consulta))
        return f"Navegador aberto para {consulta}"

    def fake_pesquisar_web_autonoma(consulta):
        chamadas.append(("pesquisa", consulta))
        return "Fonte: teste\nConteúdo de teste"

    def fake_chat_beatriz(prompt, contexto=None):
        return "Resposta resumida com base na fonte."

    monkeypatch.setattr(main, "abrir_navegador", fake_abrir_navegador)
    monkeypatch.setattr(main, "pesquisar_web_autonoma", fake_pesquisar_web_autonoma)
    monkeypatch.setattr(main.llm_clients, "chat_beatriz", fake_chat_beatriz)

    resposta = main.responder_com_pesquisa_web("qual é a capital do Brasil?")

    assert "Resposta resumida" in resposta
    assert ("abrir", "qual é a capital do Brasil?") in chamadas
    assert ("pesquisa", "qual é a capital do Brasil?") in chamadas


def test_comando_de_pesquisa_nao_desliga_o_sistema():
    palavras_desligar = ["desligar", "encerrar", "sair", "tchau", "fechar", "até logo", "adeus", "finalizar", "parar", "terminar"]

    assert not comando_solicita_encerramento("pesquise novidades sobre inteligência artificial", palavras_desligar)
    assert comando_solicita_encerramento("até logo", palavras_desligar)
    assert not comando_solicita_encerramento("pesquise como parar de procrastinar", palavras_desligar)


def test_microfone_manual_nao_e_substituido_pela_deteccao_automatica(monkeypatch):
    dispositivos = [
        {"indice": 0, "nome": "Microfone padrão"},
        {"indice": 7, "nome": "Headset escolhido"},
    ]
    monkeypatch.setattr(voice, "listar_microfones", lambda: dispositivos)

    assert voice.selecionar_microfone(7)
    assert voice.microfone_disponivel()
    assert voice._MICROFONE_INDICE == 7
    assert voice._MICROFONE_NOME == "Headset escolhido"
    voice.selecionar_microfone(None)


def test_inicio_automatico_de_fontes_esta_desativado_por_padrao(monkeypatch):
    chamadas = []

    def fake_submit(func, *args, **kwargs):
        chamadas.append(("submit", func.__name__))
        return object()

    class FakeExecutor:
        def submit(self, func, *args, **kwargs):
            return fake_submit(func, *args, **kwargs)

    def fake_observer():
        chamadas.append(("observer", "start"))
        return "Observador não iniciado"

    monkeypatch.setattr(main, "EXECUTOR", FakeExecutor())
    monkeypatch.setattr(main, "agendar_indexacao_em_background", lambda: True)
    monkeypatch.setattr(main, "iniciar_observador_memoria", fake_observer)

    resultado = main.inicializar_biblioteca_automatica()

    assert resultado is True
    assert ("observer", "start") in chamadas


def test_indexacao_ignora_falhas_do_chromadb(monkeypatch, tmp_path):
    arquivo = tmp_path / "documento_teste.txt"
    arquivo.write_text("Este texto existe para verificar que a indexação não derruba o app.", encoding="utf-8")

    manifesto = tmp_path / "manifesto.json"
    manifesto.write_text("{}", encoding="utf-8")

    class ColecaoFalha:
        upsert_chamado = False
        delete_chamado = False

        def delete(self, *args, **kwargs):
            self.delete_chamado = True

        def upsert(self, *args, **kwargs):
            self.upsert_chamado = True
            raise RuntimeError("falha simulada do ChromaDB")

    colecao = ColecaoFalha()
    monkeypatch.setattr(memory, "CAMINHO_MANIFESTO_MEMORIA", str(manifesto))
    monkeypatch.setattr(memory, "_fontes_raiz", lambda: [(str(arquivo), "documento_local")])
    monkeypatch.setattr(memory, "colecao_docs", colecao)
    memory._INDEXACAO_EM_ANDAMENTO = False
    memory._INDEXACAO_PENDENTE = False
    memory._atualizar_status_indexacao("inativa", "Teste")

    resultado = memory.indexar_pasta_documentos()

    assert isinstance(resultado, str)
    assert colecao.upsert_chamado
    assert colecao.delete_chamado
    assert memory.status_indexacao()["status"] == "falhou"


def test_texto_para_fala_mantem_pontuacao_e_remove_markdown():
    texto = "**Resposta:** O dólar fechou em alta.\n- Segundo o G1, subiu 1,2%.\nVeja https://g1.globo.com/x"
    limpo = main.limpar_texto_para_fala(texto)
    assert "*" not in limpo and "http" not in limpo
    assert "1,2%" in limpo
    assert limpo.endswith(".")
    assert "alta." in limpo


def test_pergunta_de_continuacao_e_reescrita_com_o_historico(monkeypatch):
    import collections
    import threading
    from llm_clients import LLMClients

    # Cria o cliente sem conectar ao Ollama.
    cliente = LLMClients.__new__(LLMClients)
    cliente._historico = collections.deque(maxlen=6)
    cliente._historico_lock = threading.Lock()

    assert cliente.reescrever_consulta("e quanto custa?") == "e quanto custa?"

    cliente.registrar_turno("qual o melhor celular da Samsung?", "Hoje é o Galaxy S26 Ultra.")
    monkeypatch.setattr(cliente, "chat", lambda *args, **kwargs: "Quanto custa o Galaxy S26 Ultra?")
    assert cliente.parece_continuacao("e quanto custa?")
    assert cliente.reescrever_consulta("e quanto custa?") == "Quanto custa o Galaxy S26 Ultra?"
    assert not cliente.parece_continuacao("qual a previsão do tempo em São Paulo amanhã de manhã")


def test_link_do_duckduckgo_e_convertido_para_o_site_real():
    import tools

    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fg1.globo.com%2Fnoticia&rut=abc"
    assert tools._url_real_duckduckgo(href) == "https://g1.globo.com/noticia"
    assert tools._url_real_duckduckgo("https://exemplo.com") == "https://exemplo.com"


def test_classificador_aceita_resposta_com_texto_extra(monkeypatch):
    monkeypatch.setattr(main.llm_clients, "is_ollama_available", lambda: True)
    monkeypatch.setattr(main.llm_clients, "chat", lambda *args, **kwargs: "Categoria: Pesquisa.")
    assert main.classificar_com_ollama("quem ganhou o jogo ontem?") == "pesquisa"
    monkeypatch.setattr(main.llm_clients, "chat", lambda *args, **kwargs: "não sei")
    assert main.classificar_com_ollama("xyz") is None


def test_palavras_chave_ignoram_acentos_plurais_e_palavras_vazias():
    assert memory.extrair_palavras_chave("Qual é a minha memória sobre as reuniões?") == ["memoria", "reuni"]
    assert memory._raiz("reunião") == memory._raiz("reuniões")
    assert memory._raiz("informações") == memory._raiz("informação")
    import re
    padrao = memory._regex_palavra("memoria").replace("(?i)", "")
    assert re.search(padrao, "As Memórias da Beatriz", re.IGNORECASE)


def test_busca_de_documentos_filtra_pelas_palavras_chave(monkeypatch):
    chamadas = []

    class Colecao:
        def count(self):
            return 5000

        def query(self, query_texts, n_results, where_document=None):
            chamadas.append(where_document)
            if where_document is None:
                return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
            return {
                "ids": [["a", "b", "c"]],
                "documents": [["Texto sobre contrato de aluguel", "Contrato de aluguel e multa por rescisão", "Aluguel"]],
                "metadatas": [[
                    {"source_id": s, "hash": s, "arquivo": f"{s}.md", "caminho": memory.CAMINHO_DOCUMENTOS}
                    for s in ("a", "b", "c")
                ]],
                "distances": [[0.5, 0.9, 0.4]],
            }

        def get(self, **kwargs):
            raise AssertionError("A busca não deve carregar a coleção inteira")

    monkeypatch.setattr(memory, "colecao_docs", Colecao())
    monkeypatch.setattr(memory, "deve_consultar_documentos", lambda pergunta, modo=None: True)
    monkeypatch.setattr(memory, "_registrar_auditoria", lambda *args, **kwargs: None)

    resposta = memory.buscar_nos_documentos_modo("multa na rescisão do contrato de aluguel", modo="padrão")

    assert "$or" in chamadas[0]
    # O trecho com todas as palavras-chave vem primeiro, mesmo sendo o menos parecido semanticamente.
    assert resposta.index("b.md") < resposta.index("a.md")


def test_historico_e_memoria_usam_indice_incremental(monkeypatch, tmp_path):
    import json

    historico = tmp_path / "historico.jsonl"
    historico.write_text(
        json.dumps({"pergunta": "qual meu time?", "resposta": "Você torce para o Flamengo."}) + "\n"
        + json.dumps({"pergunta": "receita de bolo", "resposta": "Use farinha e ovos."}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(memory, "ARQUIVO_HISTORICO_CONVERSAS", str(historico))
    assert "Flamengo" in memory.buscar_historico_conversa("e o Flamengo jogou?")
    assert "bolo" not in memory.buscar_historico_conversa("e o Flamengo jogou?")

    with historico.open("a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps({"pergunta": "bolos de chocolate", "resposta": "Leva cacau."}) + "\n")
    assert "cacau" in memory.buscar_historico_conversa("quero fazer um bolo")

    fatos = tmp_path / "memoria.md"
    fatos.write_text("# Memória\n\n- 2026-01-01: Meu aniversário é em 30 de agosto\n- 2026-01-02: Gosto de café\n", encoding="utf-8")
    monkeypatch.setattr(memory, "ARQUIVO_MEMORIA_USUARIO", str(fatos))
    resposta = memory.buscar_memoria("quando é meu aniversário?")
    assert "30 de agosto" in resposta and "café" not in resposta
