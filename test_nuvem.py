import threading
import time

import llm_nuvem
import main
from agentes import Orquestrador
from llm_nuvem import ClienteGemini, LLMComReserva, NuvemIndisponivel
from test_agentes import LLMFalso


class GeminiFalso:
    """Imita o ClienteGemini: responde como o LLM falso ou finge ter batido no limite."""

    nome = "Gemini"
    modelo = "gemini-teste"

    def __init__(self, no_limite=False):
        self.no_limite = no_limite
        self.chamadas = 0
        self.respostas = LLMFalso()

    def disponivel(self):
        return True

    def chat(self, prompt, system_prompt=None, model=None, **kwargs):
        self.chamadas += 1
        if self.no_limite:
            raise NuvemIndisponivel("Gemini recusou (429)")
        return self.respostas.chat(prompt, system_prompt=system_prompt, model=model, **kwargs)


def executar(orquestrador, comando):
    pronto = threading.Event()
    tarefa = orquestrador.enviar(comando, ao_concluir=lambda t: pronto.set())
    assert pronto.wait(30)
    return tarefa


def test_sem_chave_o_gemini_fica_fora():
    cliente = ClienteGemini(chave="")
    assert not cliente.disponivel()
    assert cliente.status() == ("sem chave", "sem_chave")


def test_limite_do_plano_gratuito_pausa_cada_vez_mais(tmp_path):
    cliente = ClienteGemini(chave="x", arquivo_uso=str(tmp_path / "uso.json"))
    erro = type("Erro429", (), {"code": 429})()
    cliente._registrar_falha(erro)
    primeira = cliente._pausado_ate - time.time()
    cliente._registrar_falha(erro)
    segunda = cliente._pausado_ate - time.time()
    assert not cliente.disponivel()
    assert 50 < primeira <= 60 and 110 < segunda <= 120
    assert cliente.status()[1] == "pausado"


def test_reserva_no_ollama_quando_o_gemini_falha():
    local = LLMFalso()
    llm = LLMComReserva(GeminiFalso(no_limite=True), local)
    resposta = llm.chat("oi", system_prompt="Você é o Agente Relator da Beatriz.", model="llama3.1:8b")
    assert "agentes" in resposta
    assert llm.registro == ["Ollama (llama3.1:8b)"]


def test_tarefa_publica_usa_o_gemini_e_o_relatorio_diz_isso(tmp_path):
    gemini, local = GeminiFalso(), LLMFalso()
    orq = Orquestrador(str(tmp_path), llm=local, abrir_ao_concluir=False, llm_nuvem=LLMComReserva(gemini, local))

    tarefa = executar(orq, "crie uma apresentação com 6 slides sobre energia solar")

    assert tarefa.status == "concluída"
    assert gemini.chamadas >= 3 and not local.chamadas  # apresentação, crítico e relator na nuvem
    assert "Gemini (gemini-teste)" in open(tarefa.relatorio, encoding="utf-8").read()


def test_tarefa_com_meus_documentos_nunca_vai_para_a_nuvem(tmp_path):
    gemini, local = GeminiFalso(), LLMFalso()
    orq = Orquestrador(str(tmp_path), llm=local, abrir_ao_concluir=False, llm_nuvem=LLMComReserva(gemini, local))
    orq.agentes["biblioteca"].buscar = lambda consulta: "[Fonte: contrato.pdf | documento_local | p. 1 | c:/x]\nTrecho pessoal"

    tarefa = executar(orq, "monte uma apresentação com base nos meus documentos sobre meu contrato")

    assert tarefa.status == "concluída"
    assert gemini.chamadas == 0 and local.chamadas
    assert "Ollama (local)" in open(tarefa.relatorio, encoding="utf-8").read()


def test_pesquisa_da_conversa_usa_o_gemini_sem_historico(monkeypatch):
    recebido = {}

    class GeminiQueResponde(GeminiFalso):
        def chat(self, prompt, system_prompt=None, model=None, **kwargs):
            recebido.update(prompt=prompt, sistema=system_prompt)
            return "Segundo o G1, o mais vendido foi o modelo X."

    monkeypatch.setattr(llm_nuvem, "provedores_nuvem", lambda: [GeminiQueResponde()])
    monkeypatch.setattr(main, "abrir_navegador", lambda consulta: None)
    monkeypatch.setattr(main, "buscar_manchetes", lambda consulta: [("Modelo X lidera vendas de elétricos - G1", "", "Notícia publicada em Thu, 08 Oct 2026")])
    monkeypatch.setattr(main, "pesquisar_web_autonoma", lambda consulta: "[Fonte: G1]\nURL: https://g1.globo.com\nModelo X lidera.")
    monkeypatch.setattr(main.llm_clients, "chat_beatriz", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria usar o Ollama")))

    resposta = main.responder_com_pesquisa_web("qual o carro elétrico mais vendido?")

    assert resposta.startswith("Segundo o G1")
    assert "Modelo X lidera" in recebido["prompt"]
    assert "Beatriz" in recebido["sistema"]


# ------------------------------------------------------------------ Claude Code
import json
import types

from llm_nuvem import ClienteClaudeCode, _versao, localizar_claude_code


class ExecutorFalso:
    """Imita o subprocess.run do `claude -p`, sem gastar o plano."""

    def __init__(self, resultado="Brasília é a capital.", erro=False, codigo=0):
        self.resultado, self.erro, self.codigo = resultado, erro, codigo
        self.chamadas = []

    def __call__(self, comando, **kwargs):
        self.chamadas.append((comando, kwargs))
        saida = json.dumps({"type": "result", "is_error": self.erro, "result": self.resultado})
        return types.SimpleNamespace(returncode=self.codigo, stdout=saida, stderr="")


def cliente_claude(tmp_path, executor, **kwargs):
    return ClienteClaudeCode(executavel="claude.exe", ativo=True, arquivo_uso=str(tmp_path / "uso.json"),
                             executar=executor, **kwargs)


def test_claude_usa_a_assinatura_sem_ferramentas_e_sem_chave_de_api(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teste")
    executor = ExecutorFalso()
    claude = cliente_claude(tmp_path, executor)

    resposta = claude.chat("Qual a capital do Brasil?", system_prompt="Você é a Beatriz.", formato="json")

    assert resposta == "Brasília é a capital."
    comando, kwargs = executor.chamadas[0]
    assert comando[:4] == ["claude.exe", "-p", "--output-format", "json"]
    assert comando[comando.index("--tools") + 1] == ""          # nenhuma ferramenta
    assert "ANTHROPIC_API_KEY" not in kwargs["env"]               # nunca a API paga
    assert kwargs["input"] == "Qual a capital do Brasil?"        # a pergunta vai pela entrada, não pela linha de comando
    assert "Responda apenas com o objeto JSON" in comando[comando.index("--system-prompt") + 1]
    assert claude.uso_hoje() == 1


def test_claude_no_limite_do_plano_pausa_e_passa_para_o_gemini(tmp_path):
    claude = cliente_claude(tmp_path, ExecutorFalso("Claude AI usage limit reached|1760000000", erro=True, codigo=1))
    gemini = GeminiFalso()
    llm = LLMComReserva([claude, gemini], LLMFalso())

    resposta = llm.chat("oi", system_prompt="Você é o Agente Relator da Beatriz.")

    assert "agentes" in resposta and gemini.chamadas == 1
    assert llm.registro == ["Gemini (gemini-teste)"]
    assert not claude.disponivel()
    texto, estado = claude.status()
    assert estado == "pausado" and "limite" in texto
    assert 800 < claude._pausado_ate - time.time() <= 900


def test_claude_sem_login_avisa_no_painel(tmp_path):
    claude = cliente_claude(tmp_path, ExecutorFalso("Invalid API key · Please run /login", erro=True, codigo=1))
    try:
        claude.chat("oi")
    except NuvemIndisponivel:
        pass
    assert claude.status()[0].startswith("faça login")


def test_acha_a_versao_mais_nova_do_claude_code_do_app(tmp_path, monkeypatch):
    for versao in ("2.1.9", "2.1.295", "2.1.30"):
        pasta = tmp_path / "Claude" / "claude-code" / versao / "abc"
        pasta.mkdir(parents=True)
        (pasta / "claude.exe").write_bytes(b"")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(llm_nuvem.shutil, "which", lambda nome: None)
    monkeypatch.setattr(llm_nuvem.os.path, "expanduser", lambda caminho: str(tmp_path / "sem_home"))
    assert _versao(localizar_claude_code("")) == (2, 1, 295)


# ------------------------------------------------------------------ IA escolhida na frase e resumo
from agentes import planejar
from llm_nuvem import extrair_ia_pedida, ordenar_por_preferencia


class ClaudeFalso(GeminiFalso):
    nome = "Claude"
    modelo = "claude-teste"


def test_reconhece_a_ia_pedida_e_tira_da_frase():
    assert extrair_ia_pedida("pesquise pelo Gemini e me dê um resumo sobre a reforma tributária") == (
        "pesquise e me dê um resumo sobre a reforma tributária", "gemini")
    assert extrair_ia_pedida("procure no Claude o preço do dólar") == ("procure o preço do dólar", "claude")
    assert extrair_ia_pedida("pesquise a reforma tributária com o gêmini") == ("pesquise a reforma tributária", "gemini")
    assert extrair_ia_pedida("pesquise o preço do dólar") == ("pesquise o preço do dólar", None)
    assert extrair_ia_pedida("pesquise pelo Cláudio Castro")[1] is None  # nome de pessoa não é o Claude


def test_ia_preferida_vai_na_frente_e_as_outras_ficam_de_reserva():
    claude, gemini = ClaudeFalso(), GeminiFalso()
    assert ordenar_por_preferencia([claude, gemini], "gemini") == [gemini, claude]
    assert ordenar_por_preferencia([claude, gemini], None) == [claude, gemini]
    assert ordenar_por_preferencia([claude], "gemini") == [claude]  # Gemini desligado: segue sem ele


def test_responder_na_nuvem_tenta_a_ia_pedida_primeiro(monkeypatch):
    claude, gemini = ClaudeFalso(), GeminiFalso()
    monkeypatch.setattr(llm_nuvem, "provedores_nuvem", lambda: [claude, gemini])
    registro = []

    llm_nuvem.responder_na_nuvem("Você é o Agente Relator da Beatriz.", preferir="gemini", registro=registro)

    assert gemini.chamadas == 1 and claude.chamadas == 0
    assert registro == ["Gemini"]


def comando_de_pesquisa(monkeypatch, comando):
    """Roda o comando falado e devolve como a pesquisa foi chamada, sem abrir navegador nem acessar a rede."""
    chamadas = []
    monkeypatch.setattr(main, "responder_com_pesquisa_web", lambda alvo, **kwargs: chamadas.append((alvo, kwargs)) or "ok")
    executada, _ = main.tentar_executar_ferramenta(comando)
    assert executada and len(chamadas) == 1
    return chamadas[0]


def test_frase_de_pesquisa_com_ia_e_resumo(monkeypatch):
    # Como chega pela voz: o nome sai, e o pedido não vira tarefa dos agentes.
    falado = "Beatriz, pesquise pelo Gemini e me dê um resumo sobre a reforma tributária"
    comando = main.extrair_comando_ativado(falado, 100.0, main.EstadoAtivacao(), exigir=True)
    assert planejar(comando) is None

    alvo, opcoes = comando_de_pesquisa(monkeypatch, comando)
    assert alvo == "a reforma tributária"
    assert opcoes == {"salvar": False, "preferir": "gemini", "resumo": True}

    alvo, opcoes = comando_de_pesquisa(monkeypatch, "pesquise a reforma tributária e resuma pelo Claude")
    assert (alvo, opcoes["preferir"], opcoes["resumo"]) == ("a reforma tributária", "claude", True)

    alvo, opcoes = comando_de_pesquisa(monkeypatch, "pesquise e me explique como funciona o Pix")
    assert (alvo, opcoes["preferir"], opcoes["resumo"]) == ("como funciona o pix", None, True)


def test_pesquisa_comum_continua_como_antes(monkeypatch):
    alvo, opcoes = comando_de_pesquisa(monkeypatch, "pesquise o preço do dólar")
    assert alvo == "o preço do dólar"
    assert opcoes == {"salvar": False, "preferir": None, "resumo": False}

    # Sem mais nada na frase, o Gemini é o assunto da pesquisa, não a IA escolhida.
    alvo, opcoes = comando_de_pesquisa(monkeypatch, "pesquise pelo Gemini")
    assert "gemini" in alvo and opcoes["preferir"] is None


def fontes_falsas(monkeypatch):
    monkeypatch.setattr(main, "abrir_navegador", lambda consulta: None)
    monkeypatch.setattr(main, "buscar_manchetes", lambda consulta: [("Reforma tributária entra na fase de transição - G1", "", "Notícia publicada em Mon, 12 Oct 2026")])
    monkeypatch.setattr(main, "pesquisar_web_autonoma", lambda consulta: "[Fonte: Agência Brasil]\nURL: https://agenciabrasil.ebc.com.br\nO IVA dual substitui cinco tributos.")
    monkeypatch.setattr(main.llm_clients, "chat_beatriz", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria usar o Ollama")))


class IAQueGuarda(GeminiFalso):
    """Guarda o que recebeu e responde um texto fixo."""

    def __init__(self, nome, resposta, no_limite=False):
        super().__init__(no_limite=no_limite)
        self.nome, self.resposta, self.recebido = nome, resposta, []

    def chat(self, prompt, system_prompt=None, model=None, **kwargs):
        self.chamadas += 1
        if self.no_limite:
            raise NuvemIndisponivel(f"{self.nome} recusou (429)")
        self.recebido.append((prompt, system_prompt))
        return self.resposta


def test_resumo_pelo_gemini_usa_o_gemini_primeiro_e_o_prompt_de_resumo(monkeypatch):
    resumo = ("Segundo a Agência Brasil, a reforma tributária troca cinco tributos por um IVA dual.\n\n"
              "De acordo com o G1, a transição já começou e vai até 2033.")
    claude = IAQueGuarda("Claude", "não deveria responder")
    gemini = IAQueGuarda("Gemini", resumo)
    monkeypatch.setattr(llm_nuvem, "provedores_nuvem", lambda: [claude, gemini])  # ordem padrão: Claude primeiro
    fontes_falsas(monkeypatch)

    _, resposta = main.tentar_executar_ferramenta("pesquise pelo Gemini e me dê um resumo sobre a reforma tributária")

    assert resposta == resumo
    assert gemini.chamadas == 1 and claude.chamadas == 0
    prompt, sistema = gemini.recebido[0]
    assert "resumo falado" in prompt and "dois parágrafos" in prompt
    assert "Tema: a reforma tributária" in prompt
    assert "IVA dual" in prompt and "fase de transição" in prompt  # só a pergunta e as fontes públicas
    assert "Beatriz" in sistema
    # Na fala, os parágrafos viram frases seguidas, sem quebras de linha.
    assert "\n" not in main.limpar_texto_para_fala(resposta)


def test_preco_do_dolar_segue_com_resposta_direta_e_ordem_padrao(monkeypatch):
    claude = IAQueGuarda("Claude", "Segundo o G1, o dólar fechou a R$ 5,10.")
    gemini = IAQueGuarda("Gemini", "não deveria responder")
    monkeypatch.setattr(llm_nuvem, "provedores_nuvem", lambda: [claude, gemini])
    fontes_falsas(monkeypatch)

    _, resposta = main.tentar_executar_ferramenta("pesquise o preço do dólar")

    assert resposta == "Segundo o G1, o dólar fechou a R$ 5,10."
    assert claude.chamadas == 1 and gemini.chamadas == 0
    prompt = claude.recebido[0][0]
    assert "duas a quatro frases" in prompt and "Pergunta: o preço do dólar" in prompt


def test_ia_pedida_no_limite_passa_para_a_outra_e_avisa(monkeypatch):
    claude = IAQueGuarda("Claude", "A reforma unifica tributos, segundo a Agência Brasil.")
    gemini = IAQueGuarda("Gemini", "", no_limite=True)
    monkeypatch.setattr(llm_nuvem, "provedores_nuvem", lambda: [claude, gemini])
    fontes_falsas(monkeypatch)

    resposta = main.responder_com_pesquisa_web("a reforma tributária", preferir="gemini", resumo=True)

    assert gemini.chamadas == 1 and claude.chamadas == 1
    assert resposta.startswith("O Gemini não pôde responder agora; usei o Claude.")


def test_pesquisa_completa_pelo_gemini():
    tarefa = planejar("faça uma pesquisa completa pelo Gemini sobre a reforma tributária")
    assert tarefa.plano == ["pesquisa"]
    assert tarefa.tema == "a reforma tributária"
    assert tarefa.ia_preferida == "gemini"
    assert planejar("faça uma pesquisa completa sobre a reforma tributária").ia_preferida == ""


def test_agentes_usam_a_ia_pedida_so_naquela_tarefa(tmp_path):
    claude, gemini, local = ClaudeFalso(), GeminiFalso(), LLMFalso()
    orq = Orquestrador(str(tmp_path), llm=local, abrir_ao_concluir=False, llm_nuvem=LLMComReserva([claude, gemini], local))

    tarefa = executar(orq, "crie pelo Gemini uma apresentação com 6 slides sobre energia solar")
    assert tarefa.status == "concluída" and tarefa.tema == "energia solar"
    assert gemini.chamadas >= 3 and claude.chamadas == 0
    assert "Gemini (gemini-teste)" in open(tarefa.relatorio, encoding="utf-8").read()

    # A tarefa seguinte, sem pedir IA, volta para a ordem padrão (Claude primeiro).
    chamadas_gemini = gemini.chamadas
    executar(orq, "crie uma apresentação com 6 slides sobre energia eólica")
    assert claude.chamadas >= 3 and gemini.chamadas == chamadas_gemini


def test_tarefa_com_meus_documentos_pelo_gemini_continua_no_computador(tmp_path):
    gemini, local = GeminiFalso(), LLMFalso()
    orq = Orquestrador(str(tmp_path), llm=local, abrir_ao_concluir=False, llm_nuvem=LLMComReserva(gemini, local))
    orq.agentes["biblioteca"].buscar = lambda consulta: "[Fonte: contrato.pdf | documento_local | p. 1 | c:/x]\nTrecho pessoal"

    pronto = threading.Event()
    tarefa = orq.enviar("monte uma apresentação com base nos meus documentos sobre meu contrato pelo Gemini",
                        ao_concluir=lambda t: pronto.set())
    assert "o Gemini fica de fora" in orq.mensagem_inicio(tarefa)
    assert pronto.wait(30)

    assert tarefa.status == "concluída"
    assert gemini.chamadas == 0 and local.chamadas
