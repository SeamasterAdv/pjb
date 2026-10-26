"""Testes das melhorias do próprio código (melhoria_codigo.py) num repositório git temporário de verdade, com um
Claude falso que edita os arquivos e o pytest de verdade rodando na cópia separada."""
import json
import os
import subprocess
import sys

import pytest

import melhoria_codigo as mc


def git(pasta, *argumentos):
    return subprocess.run(["git", *argumentos], cwd=pasta, capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    pasta = tmp_path / "beatriz"
    pasta.mkdir()
    (pasta / "conta.py").write_text("def soma(a, b):\n    return a + b\n", encoding="utf-8")
    (pasta / "test_conta.py").write_text("from conta import soma\n\n\ndef test_soma():\n    assert soma(2, 3) == 5\n",
                                         encoding="utf-8")
    git(pasta, "init", "-q", "-b", "main")
    git(pasta, "config", "user.name", "Teste")
    git(pasta, "config", "user.email", "teste@localhost")
    git(pasta, "add", "-A")
    git(pasta, "commit", "-q", "-m", "início")
    return pasta


def claude_falso(edicao=None, resposta=None, codigo=0):
    """Um subprocess.run que faz o papel do Claude Code (edita a cópia separada) e deixa o pytest rodar de verdade."""
    chamadas = []

    def executar(comando, **kwargs):
        if comando[0] != "claude-falso":
            return subprocess.run(comando, **{k: v for k, v in kwargs.items() if k != "creationflags"})
        chamadas.append({"comando": comando, "pasta": kwargs["cwd"], "pedido": kwargs["input"]})
        if edicao:
            edicao(kwargs["cwd"])
        final = resposta if resposta is not None else {"mudou": True, "resumo": "Criei a função dobro, com teste.",
                                                         "testes": "2 passaram"}
        saida = json.dumps({"result": f"Pronto.\n{json.dumps(final, ensure_ascii=False)}", "is_error": codigo != 0})
        return subprocess.CompletedProcess(comando, codigo, stdout=saida, stderr="")

    executar.chamadas = chamadas
    return executar


def criar_dobro(pasta):
    with open(os.path.join(pasta, "conta.py"), "a", encoding="utf-8") as arquivo:
        arquivo.write("\n\ndef dobro(x):\n    return 2 * x\n")
    with open(os.path.join(pasta, "test_dobro.py"), "w", encoding="utf-8") as arquivo:
        arquivo.write("from conta import dobro\n\n\ndef test_dobro():\n    assert dobro(4) == 8\n")


def quebrar(pasta):
    with open(os.path.join(pasta, "conta.py"), "w", encoding="utf-8") as arquivo:
        arquivo.write("def soma(a, b):\n    return a - b\n")


def agente(repo, tmp_path, executar, **opcoes):
    avisos = []
    laboratorio = mc.Laboratorio(repo=str(repo), pasta_evolucao=str(tmp_path / "evolucao"), python=sys.executable,
                                 claude="claude-falso", executar=executar)
    beatriz = mc.AgenteMelhoria(laboratorio=laboratorio, registro=mc.Registro(str(tmp_path / "melhorias.json")),
                                avisar=lambda texto, canal: avisos.append((texto, canal)), em_segundo_plano=False,
                                contexto=lambda: "Operador: que dia é hoje?\nBeatriz: Hoje é segunda.", **opcoes)
    return beatriz, avisos


# ------------------------------------------------------------------ o caminho todo
def test_prepara_testa_aplica_e_desfaz(repo, tmp_path):
    executar = claude_falso(criar_dobro)
    beatriz, avisos = agente(repo, tmp_path, executar)
    resposta = beatriz.responder("Beatriz, se melhora: cria uma função que dobra um número", "whatsapp")
    assert resposta.startswith("Diagnóstico aceito.")
    chamada = executar.chamadas[0]
    assert chamada["pasta"] != str(repo) and "evolucao" in chamada["pasta"]  # o Claude roda na cópia separada
    assert "--permission-mode" in chamada["comando"] and "dontAsk" in chamada["comando"]
    assert f"Bash({sys.executable.replace(chr(92), '/')} -m pytest:*)" in chamada["comando"]
    assert "dobra um número" in chamada["pedido"] and "Contexto" not in chamada["pedido"]
    texto, canal = avisos[0]
    assert canal == "whatsapp" and texto.startswith("Melhoria pronta e testada: Criei a função dobro, com teste. Mudei 2 arquivos")
    assert "2 passaram" in texto and texto.endswith("Aplico?")
    assert "dobro" not in (repo / "conta.py").read_text(encoding="utf-8")  # nada mudou na pasta em uso
    assert not os.path.exists(chamada["pasta"])  # a cópia sai; o ramo fica até a resposta
    assert beatriz.esperando_resposta()

    assert beatriz.responder("sim").startswith("Aplicado no meu código.")
    assert "dobro" in (repo / "conta.py").read_text(encoding="utf-8")
    assert git(repo, "log", "-1", "--format=%s").startswith("Evolução: Beatriz, se melhora")
    assert "evolucao/" not in git(repo, "branch")
    assert beatriz.responder("o que mudou?").startswith("Criei a função dobro, com teste. Arquivos: conta.py, test_dobro.py.")

    assert beatriz.responder("desfaz a última melhoria").startswith("Desfeito:")
    assert "dobro" not in (repo / "conta.py").read_text(encoding="utf-8")
    assert beatriz.registro.ultima()["status"] == "desfeita"


def test_pedido_sobre_isso_leva_a_ultima_troca(repo, tmp_path):
    executar = claude_falso(criar_dobro)
    beatriz, _ = agente(repo, tmp_path, executar)
    beatriz.responder("corrige isso no seu código")
    assert "Contexto (o que acabou de acontecer):\nOperador: que dia é hoje?" in executar.chamadas[0]["pedido"]


def test_testes_falhando_descartam_tudo(repo, tmp_path):
    beatriz, avisos = agente(repo, tmp_path, claude_falso(quebrar))
    beatriz.pedir("muda a soma")
    assert avisos[0][0].startswith("Preparei a mudança, mas a conferência não passou (1 falharam)")
    melhoria = beatriz.registro.ultima()
    assert melhoria["status"] == "falhou" and "evolucao/" not in git(repo, "branch")
    assert (repo / "conta.py").read_text(encoding="utf-8").endswith("return a + b\n")


def test_claude_que_nao_muda_nada(repo, tmp_path):
    beatriz, avisos = agente(repo, tmp_path, claude_falso(resposta={"mudou": False, "motivo": "isso não é sobre o código"}))
    beatriz.pedir("se melhora: faz um café")
    assert avisos[0][0] == "Não mudei nada: isso não é sobre o código"


def test_claude_com_erro(repo, tmp_path):
    beatriz, avisos = agente(repo, tmp_path, claude_falso(resposta={}, codigo=1))
    beatriz.pedir("se melhora em algo")
    assert avisos[0][0].startswith("A melhoria não deu certo: o Claude não terminou") and "Nada mudou" in avisos[0][0]


def test_descartar_e_aplicar_sozinha(repo, tmp_path):
    beatriz, _ = agente(repo, tmp_path, claude_falso(criar_dobro))
    beatriz.pedir("se melhora: dobro")
    assert beatriz.responder("não") == "Descartada. Nada mudou no meu código."
    assert "evolucao/" not in git(repo, "branch")
    sozinha, avisos = agente(repo, tmp_path / "outra", claude_falso(criar_dobro), aplicar_sozinha=True)
    sozinha.pedir("se melhora: dobro")
    assert "Aplicado no meu código." in avisos[0][0] and "Aplico?" not in avisos[0][0]
    assert "dobro" in (repo / "conta.py").read_text(encoding="utf-8")


def test_conflito_com_o_codigo_que_andou_nao_muda_nada(repo, tmp_path):
    beatriz, _ = agente(repo, tmp_path, claude_falso(criar_dobro))
    beatriz.pedir("se melhora: dobro")
    (repo / "conta.py").write_text("def soma(a, b):\n    return b + a\n\n\ndef dobro(x):\n    return x + x\n", encoding="utf-8")
    git(repo, "commit", "-qam", "mudança feita à mão")
    antes = git(repo, "rev-parse", "HEAD")
    assert beatriz.responder("aplica a melhoria").startswith("Não apliquei: o código mudou desde que preparei")
    assert git(repo, "rev-parse", "HEAD") == antes and git(repo, "status", "--porcelain") == ""


def test_mudancas_nao_commitadas_ficam_intactas(repo, tmp_path):
    beatriz, _ = agente(repo, tmp_path, claude_falso(criar_dobro))
    beatriz.pedir("se melhora: dobro")
    (repo / "conta.py").write_text("def soma(a, b):\n    return a + b  # mexido à mão\n", encoding="utf-8")
    resposta = beatriz.responder("sim")
    assert resposta.startswith("Não apliquei: os mesmos arquivos têm mudanças que ainda não foram para o git")
    assert (repo / "conta.py").read_text(encoding="utf-8").endswith("# mexido à mão\n")  # o seu trabalho não se perde
    assert git(repo, "log", "-1", "--format=%s") == "início"


def test_o_claude_so_le_e_escreve_na_copia_separada(repo, tmp_path):
    executar = claude_falso(criar_dobro)
    beatriz, _ = agente(repo, tmp_path, executar)
    beatriz.pedir("se melhora: dobro")
    comando = executar.chamadas[0]["comando"]
    permitidas = comando[comando.index("--allowedTools") + 1:comando.index("--append-system-prompt")]
    assert {"Read(./**)", "Edit(./**)", "Write(./**)"} <= set(permitidas)
    assert not {"Read", "Edit", "Write"} & set(permitidas)  # sem o "./**", ele leria o .env da pasta em uso


def test_uma_de_cada_vez_e_situacao(repo, tmp_path):
    beatriz, _ = agente(repo, tmp_path, claude_falso(criar_dobro))
    assert beatriz.situacao().startswith("Nenhuma melhoria ainda.")
    beatriz.registro.guardar({"id": "x", "pedido": "algo demorado", "status": "preparando", "ramo": "evolucao/x"})
    assert beatriz.pedir("se melhora: outra coisa").startswith("Já estou trabalhando numa melhoria (algo demorado)")
    assert beatriz.situacao() == "Trabalhando em \"algo demorado\": o Claude está mexendo no código."


def test_melhoria_da_madrugada_espera_a_manha(repo, tmp_path):
    beatriz, avisos = agente(repo, tmp_path, claude_falso(criar_dobro))
    beatriz.pedir(mc.pedido_de_correcao({"vezes": 3, "erro": "KeyError: '…'", "quadros": ["conta.py:2 em soma"]}),
                  origem="noite")
    assert avisos == []  # de madrugada não fala
    saudacao = beatriz.saudacao_pendente()
    assert saudacao.startswith("Esta madrugada preparei uma correção preventiva.") and saudacao.endswith("Aplico?")
    assert beatriz.responder("pode aplicar").startswith("Aplicado")


@pytest.mark.parametrize("frase", ["Beatriz, se melhora: quando eu disser bom dia, fala a previsão",
                                   "melhora o seu código para abrir o Spotify", "corrige isso no seu código",
                                   "acha um jeito de você lembrar das minhas provas"])
def test_reconhece_o_pedido(frase):
    assert mc.RE_PEDIR.search(mc._sem_acentos(frase))


@pytest.mark.parametrize("frase", ["melhora o volume", "a internet melhorou", "corrige o meu texto",
                                   "aplica a pomada", "sim"])
def test_nao_e_pedido_de_melhoria(frase, repo, tmp_path):
    beatriz, _ = agente(repo, tmp_path, claude_falso())
    assert beatriz.responder(frase) is None


# ------------------------------------------------------------------ o log e os testes
def test_erros_recorrentes_do_log(tmp_path):
    projeto = str(tmp_path / "beatriz")
    log = tmp_path / "beatriz.log"
    bloco = ('Traceback (most recent call last):\n  File "{p}/main.py", line 10, in laco\n    x()\n'
             '  File "{p}/agente.py", line 42, in responder\n    dados["chave"]\n'
             '  File "C:/venv/site-packages/lib.py", line 5, in f\n    pass\n'
             "KeyError: 'o que a pessoa falou'\n").format(p=projeto.replace("\\", "/"))
    outro = ('Traceback (most recent call last):\n  File "{p}/voz.py", line 7, in falar\n    tocar()\n'
             "OSError: sem áudio\n").format(p=projeto.replace("\\", "/"))
    log.write_text("início\n" + bloco + "meio\n" + bloco + outro, encoding="utf-8")
    erros, lido = mc.erros_recorrentes(str(log), projeto=projeto)
    assert len(erros) == 1 and erros[0]["vezes"] == 2
    assert erros[0]["chave"] == "agente.py:42:KeyError" and erros[0]["erro"] == "KeyError: '…'"  # sem a fala
    assert erros[0]["quadros"] == ["main.py:10 em laco", "agente.py:42 em responder"]
    assert lido == log.stat().st_size
    assert mc.erros_recorrentes(str(log), desde_bytes=lido, projeto=projeto)[0] == []
    assert "agente.py:42 em responder" in mc.pedido_de_correcao(erros[0])


@pytest.mark.parametrize("saida, esperado", [
    ("...\n1131 passed in 113.10s (0:01:53)\n", (True, "1131 passaram")),
    ("F..\n2 failed, 1129 passed in 100s\n", (False, "2 falharam, 1129 passaram")),
    ("ERROR coleta\n1 error in 2s\n", (False, "1 falharam")),
    ("", (False, "a suíte não rodou até o fim")),
])
def test_resumo_dos_testes(saida, esperado):
    assert mc.resumo_dos_testes(saida) == esperado


def test_main_tem_o_agente_e_o_sim_sem_nome():
    import main

    assert isinstance(main.MELHORIA, mc.AgenteMelhoria) and not main.MELHORIA.esperando_resposta()
    assert main.MELHORIA.aplicar_sozinha is False  # o padrão: pergunta antes de aplicar
