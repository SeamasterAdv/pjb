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
    assert gemini.chamadas >= 2  # apresentação e crítico na nuvem
    assert local.chamadas == ["Você é o Agente Relator da Beatriz"]  # o relatório de 3 a 5 frases sai do PC
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

    monkeypatch.setattr(llm_nuvem, "provedores_nuvem", lambda funcao=None: [GeminiQueResponde()])
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
    monkeypatch.setattr(llm_nuvem, "provedores_nuvem", lambda funcao=None: [claude, gemini])
    registro = []

    llm_nuvem.responder_na_nuvem("Você é o Agente Relator da Beatriz.", preferir="gemini", registro=registro)

    assert gemini.chamadas == 1 and claude.chamadas == 0
    assert registro == ["Gemini"]


def test_exclusivo_nao_passa_o_conteudo_para_a_outra_ia(monkeypatch):
    class GeminiSemResposta(GeminiFalso):
        def chat(self, *args, **kwargs):
            self.chamadas += 1
            raise NuvemIndisponivel("Gemini ocupado")

    claude, gemini = ClaudeFalso(), GeminiSemResposta()
    monkeypatch.setattr(llm_nuvem, "provedores_nuvem", lambda funcao=None: [claude, gemini])
    registro = []

    assert llm_nuvem.responder_na_nuvem("Leia o contrato.", preferir="gemini", exclusivo=True, registro=registro) is None
    assert gemini.chamadas == 1 and claude.chamadas == 0 and registro == []  # sem exclusivo, o Claude responderia

    assert llm_nuvem.responder_na_nuvem("Leia o contrato.", preferir="gemini", registro=registro) is not None
    assert claude.chamadas == 1 and registro == ["Claude"]


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
    monkeypatch.setattr(llm_nuvem, "provedores_nuvem", lambda funcao=None: [claude, gemini])  # o Claude na frente da lista
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


def test_preco_do_dolar_segue_com_resposta_direta_na_ordem_da_pesquisa(monkeypatch):
    claude = IAQueGuarda("Claude", "não deveria responder")
    gemini = IAQueGuarda("Gemini", "Segundo o G1, o dólar fechou a R$ 5,10.")
    ordens = {"pesquisa": [gemini, claude], None: [claude, gemini]}
    monkeypatch.setattr(llm_nuvem, "provedores_nuvem", lambda funcao=None: ordens[funcao])
    fontes_falsas(monkeypatch)

    _, resposta = main.tentar_executar_ferramenta("pesquise o preço do dólar")

    assert resposta == "Segundo o G1, o dólar fechou a R$ 5,10."
    assert gemini.chamadas == 1 and claude.chamadas == 0  # a pesquisa rápida vai primeiro ao Gemini
    prompt = gemini.recebido[0][0]
    assert "duas a quatro frases" in prompt and "Pergunta: o preço do dólar" in prompt


def test_cada_funcao_tem_a_sua_ordem(monkeypatch):
    claude, gemini = ClaudeFalso(), GeminiFalso()
    for provedor in (claude, gemini):
        provedor.ativo, provedor.configurado = True, lambda: True
    monkeypatch.setattr(llm_nuvem, "claude", claude)
    monkeypatch.setattr(llm_nuvem, "gemini", gemini)
    monkeypatch.setattr(llm_nuvem, "ORDEM_IA_NUVEM", ["claude", "gemini"])
    monkeypatch.setattr(llm_nuvem, "ORDENS_POR_FUNCAO", {"pesquisa": ["gemini", "claude"], "agentes": ["claude", "gemini"]})

    assert llm_nuvem.provedores_nuvem("pesquisa") == [gemini, claude]
    assert llm_nuvem.provedores_nuvem("agentes") == [claude, gemini]
    assert llm_nuvem.provedores_nuvem() == [claude, gemini]
    registro = []
    llm_nuvem.responder_na_nuvem("Você é o Agente Relator da Beatriz.", funcao="pesquisa", registro=registro)
    assert registro == ["Gemini"] and claude.chamadas == 0
    # "pelo Claude" na frase ainda passa na frente da ordem da função.
    llm_nuvem.responder_na_nuvem("Você é o Agente Relator da Beatriz.", funcao="pesquisa", preferir="claude", registro=registro)
    assert registro == ["Gemini", "Claude"]


def ordens_do_config(ordem_geral):
    """(ORDEM_IA_PESQUISA, ORDEM_IA_AGENTES) lidas por um config novo, num processo à parte e sem o .env do usuário."""
    import os
    import subprocess
    import sys

    ambiente = {k: v for k, v in os.environ.items() if not k.startswith("ORDEM_IA_")}
    ambiente["ORDEM_IA_NUVEM"] = ordem_geral
    codigo = ("import dotenv; dotenv.load_dotenv = lambda *a, **k: None; import config; "
              "print(','.join(config.ORDEM_IA_PESQUISA) + '|' + ','.join(config.ORDEM_IA_AGENTES))")
    saida = subprocess.run([sys.executable, "-c", codigo], env=ambiente, capture_output=True, text=True, timeout=60,
                           cwd=os.path.dirname(os.path.abspath(__file__)))
    assert saida.returncode == 0, saida.stderr
    return saida.stdout.strip().splitlines()[-1]


def test_ordem_da_pesquisa_respeita_a_ordem_geral():
    """Sem ORDEM_IA_PESQUISA no .env: Gemini e depois Claude, mas uma IA tirada da ORDEM_IA_NUVEM não volta."""
    assert ordens_do_config("claude,gemini") == "gemini,claude|claude,gemini"
    assert ordens_do_config("claude") == "claude|claude"


def test_ia_pedida_no_limite_passa_para_a_outra_e_avisa(monkeypatch):
    claude = IAQueGuarda("Claude", "A reforma unifica tributos, segundo a Agência Brasil.")
    gemini = IAQueGuarda("Gemini", "", no_limite=True)
    monkeypatch.setattr(llm_nuvem, "provedores_nuvem", lambda funcao=None: [claude, gemini])
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
    assert gemini.chamadas >= 2 and claude.chamadas == 0
    assert "Gemini (gemini-teste)" in open(tarefa.relatorio, encoding="utf-8").read()

    # A tarefa seguinte, sem pedir IA, volta para a ordem padrão (Claude primeiro).
    chamadas_gemini = gemini.chamadas
    executar(orq, "crie uma apresentação com 6 slides sobre energia eólica")
    assert claude.chamadas >= 2 and gemini.chamadas == chamadas_gemini


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


# ------------------------------------------------------------------ cada IA onde rende mais
from agentes.especialistas import LIMITE_CONTEXTO_WEB, LIMITE_FICHA_NO_PROMPT, AgenteBiblioteca, AgenteEstudos
from test_agentes_novos import LLMNovos


class IAQueLe(GeminiFalso):
    """Responde como o LLM falso e guarda o que recebeu."""

    def __init__(self, nome, ativo=True):
        super().__init__()
        self.nome, self.modelo, self.ativo, self.recebido = nome, f"{nome.lower()}-teste", ativo, []

    def disponivel(self):
        return self.ativo

    def chat(self, prompt, system_prompt=None, model=None, **kwargs):
        self.recebido.append((system_prompt, prompt))
        return super().chat(prompt, system_prompt=system_prompt, model=model, **kwargs)


def orquestrador_com_fontes(tmp_path, tipos=None):
    claude_fontes, claude, gemini, local = IAQueLe("Claude"), IAQueLe("Claude"), IAQueLe("Gemini"), LLMFalso()
    extras = {"tipos_fontes_no_claude": tipos} if tipos is not None else {}
    orq = Orquestrador(str(tmp_path), llm=local, abrir_ao_concluir=False, llm_nuvem=LLMComReserva([claude, gemini], local),
                       llm_fontes=LLMComReserva([claude_fontes], local), **extras)
    orq.agentes["biblioteca"] = AgenteBiblioteca(
        buscar=lambda consulta: "[Fonte: contrato.pdf | documento_local | p. 1 | c:/x/contrato.pdf]\nTrecho do contrato",
        ler_arquivo=lambda caminho, limite: "Contrato inteiro: aluguel de R$ 2.300,00 por 30 meses."[:limite],
        limite_fontes=200000)
    return orq, claude_fontes, claude, gemini, local


def test_documento_com_meus_documentos_so_o_claude_le(tmp_path):
    orq, claude_fontes, claude, gemini, local = orquestrador_com_fontes(tmp_path)
    pronto = threading.Event()
    tarefa = orq.enviar("monte uma apresentação com base nos meus documentos sobre meu contrato", ao_concluir=lambda t: pronto.set())
    inicio = orq.mensagem_inicio(tarefa)
    assert pronto.wait(30)

    assert tarefa.status == "concluída" and tarefa.fontes_no_claude
    assert "Seus documentos vão só para o Claude." in inicio
    assert gemini.chamadas == 0 and claude.chamadas == 0  # nem a cadeia comum nem o Gemini
    _, prompt = next(r for r in claude_fontes.recebido if "Agente de Apresentações" in r[0])
    assert "Contrato inteiro: aluguel de R$ 2.300,00 por 30 meses." in prompt  # o arquivo inteiro, não só o trecho
    assert local.chamadas == ["Você é o Agente Relator da Beatriz"]
    assert "Claude (claude-teste), que leu as suas fontes" in open(tarefa.relatorio, encoding="utf-8").read()


def test_documentos_com_claude_desligado_ficam_no_pc(tmp_path):
    orq, claude_fontes, claude, gemini, local = orquestrador_com_fontes(tmp_path, tipos=("estudos", "aulas"))
    tarefa = executar(orq, "monte uma apresentação com base nos meus documentos sobre meu contrato")
    assert tarefa.status == "concluída" and not tarefa.fontes_no_claude
    assert claude_fontes.chamadas == claude.chamadas == gemini.chamadas == 0


def test_documento_com_meus_documentos_pelo_gemini_vai_so_ao_claude(tmp_path):
    orq, claude_fontes, _, gemini, _ = orquestrador_com_fontes(tmp_path)
    pronto = threading.Event()
    tarefa = orq.enviar("monte uma apresentação com base nos meus documentos sobre meu contrato pelo Gemini",
                        ao_concluir=lambda t: pronto.set())
    assert "O Gemini fica de fora: suas fontes vão só para o Claude." in orq.mensagem_inicio(tarefa)
    assert pronto.wait(30)
    assert gemini.chamadas == 0 and claude_fontes.chamadas


WEB_LONGA = "[Fonte: Portal Solar]\nURL: https://exemplo.com.br\n" + "Painéis solares geram energia. " * 600 + "FIM_DAS_FONTES"


def tarefa_com_pesquisa(tmp_path, nuvem_ativa):
    claude, local = IAQueLe("Claude", ativo=nuvem_ativa), LLMNovos()
    orq = Orquestrador(str(tmp_path), llm=local, abrir_ao_concluir=False, llm_nuvem=LLMComReserva([claude], local))
    for grupo in (orq.agentes, orq.agentes_nuvem):
        grupo["pesquisa"].pesquisar = lambda assunto, limite=5: WEB_LONGA
        grupo["pesquisa"].buscar_manchetes = lambda assunto: []
    tarefa = executar(orq, "crie um documento em PDF pesquisando na internet sobre energia solar")
    assert tarefa.status == "concluída" and tarefa.plano == ["pesquisa", "documento"]
    return claude, local


def test_na_nuvem_o_documento_e_o_critico_recebem_toda_a_pesquisa(tmp_path):
    claude, _ = tarefa_com_pesquisa(tmp_path, nuvem_ativa=True)
    assert len(WEB_LONGA) > LIMITE_CONTEXTO_WEB
    for agente in ("Agente de Documentos", "Agente Crítico"):
        _, prompt = next(r for r in claude.recebido if agente in r[0])
        assert "FIM_DAS_FONTES" in prompt, agente


def test_no_ollama_a_pesquisa_vai_cortada(tmp_path):
    claude, local = tarefa_com_pesquisa(tmp_path, nuvem_ativa=False)
    assert claude.recebido == []
    _, prompt = next(r for r in local.prompts if "Agente de Documentos" in r[0])
    assert "FIM_DAS_FONTES" not in prompt and WEB_LONGA[:LIMITE_CONTEXTO_WEB] in prompt


def test_ficha_vai_inteira_para_a_nuvem_e_enxuta_para_o_ollama():
    ficha = {"titulo": "Responsabilidade civil", "topicos": [
        {"titulo": f"Tópico {i}", "regra": "A regra geral.", "explicacao": ["Explicação longa do instituto. " * 60]}
        for i in range(10)]}
    na_nuvem = AgenteEstudos(llm=LLMComReserva([IAQueLe("Claude")], LLMFalso()), conferir_lei=None, buscar_lei=None)
    em_pausa = AgenteEstudos(llm=LLMComReserva([IAQueLe("Claude", ativo=False)], LLMFalso()), conferir_lei=None, buscar_lei=None)
    so_local = AgenteEstudos(llm=LLMFalso(), conferir_lei=None, buscar_lei=None)

    inteira = na_nuvem._ficha_no_prompt(ficha)
    assert LIMITE_FICHA_NO_PROMPT["local"] < len(inteira) <= LIMITE_FICHA_NO_PROMPT["nuvem"]
    assert "Explicação longa" in inteira  # a explicação vai junto para o teste de estresse
    for agente in (em_pausa, so_local):
        enxuta = agente._ficha_no_prompt(ficha)
        assert len(enxuta) <= LIMITE_FICHA_NO_PROMPT["local"] and "Explicação longa" not in enxuta


def test_criar_orquestrador_usa_um_claude_de_15_minutos_e_a_ordem_de_cada_funcao(monkeypatch, tmp_path):
    import agentes
    import config

    claude_da_conversa = cliente_claude(tmp_path, ExecutorFalso())
    gemini = GeminiFalso()
    gemini.ativo, gemini.configurado = True, lambda: True
    monkeypatch.setattr(llm_nuvem, "claude", claude_da_conversa)
    monkeypatch.setattr(llm_nuvem, "gemini", gemini)
    monkeypatch.setattr(llm_nuvem, "ORDENS_POR_FUNCAO", {"pesquisa": ["gemini", "claude"], "agentes": ["claude", "gemini"]})
    monkeypatch.setattr(config, "ESTUDOS_COM_CLAUDE", True)
    monkeypatch.setattr(config, "DOCUMENTOS_COM_CLAUDE", True)
    monkeypatch.setattr(config, "PASTA_DOCUMENTOS_GERADOS", str(tmp_path))

    orq = agentes.criar_orquestrador()

    claude_das_tarefas, segunda = orq.llm_nuvem.principais
    assert claude_das_tarefas is not claude_da_conversa and segunda is gemini
    assert claude_das_tarefas.tempo_limite == agentes.TEMPO_LIMITE_CLAUDE_AGENTES == 900
    assert orq.llm_fontes.principais == [claude_das_tarefas]  # a mesma pausa na cadeia comum e na das fontes
    assert orq.llm_publico.principais == [gemini, claude_da_conversa]  # manchetes do resumo do dia: Gemini primeiro
    assert orq.agentes["resumo_dia"].llm_publico is orq.llm_publico
    assert set(orq.tipos_fontes_no_claude) == {"estudos", "aulas", "documento", "apresentacao", "planilha"}
    assert "relator" not in orq.agentes_nuvem and "relator" not in orq.agentes_fontes

    monkeypatch.setattr(config, "DOCUMENTOS_COM_CLAUDE", False)
    assert set(agentes.criar_orquestrador().tipos_fontes_no_claude) == {"estudos", "aulas"}
    monkeypatch.setattr(config, "ESTUDOS_COM_CLAUDE", False)
    assert agentes.criar_orquestrador().llm_fontes is None


def test_explicacao_de_lei_vai_na_ordem_da_pesquisa(monkeypatch):
    import agente_leis

    pedidos = []
    monkeypatch.setattr(llm_nuvem, "responder_na_nuvem", lambda prompt, contexto="", **k: pedidos.append(k) or "Explicação.")
    assert agente_leis.explicar_com_ia("Explique o artigo 5.", "contexto") == "Explicação."
    assert pedidos[0]["funcao"] == "pesquisa" and "preferir" not in pedidos[0]


def test_pesquisa_rapida_pede_a_ordem_da_pesquisa(monkeypatch):
    pedidos = []
    monkeypatch.setattr(main, "responder_na_nuvem", lambda prompt, contexto="", **k: pedidos.append(k) or "Resposta.")
    fontes_falsas(monkeypatch)
    assert main.responder_com_pesquisa_web("o preço do dólar") == "Resposta."
    assert pedidos[0]["funcao"] == "pesquisa"
