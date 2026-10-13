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
