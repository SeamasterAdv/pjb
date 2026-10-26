"""Testes das camadas de personalidade (camadas_personalidade.py): guardar, juntar, prompt, aprender e pedidos diretos.
Sem a IA de verdade: um LLM falso devolve o JSON."""
import json

import pytest

import camadas_personalidade as cp
import personalidade


class LLMFalso:
    def __init__(self, *respostas, erro=None):
        self.respostas = list(respostas)
        self.erro = erro
        self.pedidos = []

    def chat(self, prompt, system_prompt=None, usar_historico=False, formato=None, **_):
        self.pedidos.append((prompt, system_prompt, formato))
        if self.erro:
            raise self.erro
        resposta = self.respostas.pop(0) if self.respostas else {}
        return json.dumps(resposta, ensure_ascii=False)


def historico(tmp_path, trocas):
    arquivo = tmp_path / "historico.jsonl"
    with open(arquivo, "w", encoding="utf-8") as saida:
        for i, (pergunta, resposta) in enumerate(trocas):
            saida.write(json.dumps({"pergunta": pergunta, "resposta": resposta, "timestamp": 1000.0 + i}, ensure_ascii=False) + "\n")
    return str(arquivo)


# ------------------------------------------------------------------ guardar e juntar
def test_cria_reforca_e_junta_as_parecidas():
    primeira = cp.adicionar("estilo", "respostas mais curtas", "pedido")
    assert primeira["nova"] and primeira["camada"]["texto"] == "Respostas mais curtas."  # maiúscula e ponto
    de_novo = cp.adicionar("estilo", "Respostas mais curtas, por favor.", "conversa")
    assert not de_novo["nova"] and de_novo["camada"]["vezes"] == 2
    assert de_novo["camada"]["texto"] == "Respostas mais curtas."  # a da conversa não troca o seu pedido
    assert len(cp.ler()["camadas"]) == 1


def test_negacao_e_chave_nao_se_misturam():
    cp.adicionar("estilo", 'Chame o operador de "Rani"', "pedido", chave="chamar")
    cp.adicionar("estilo", 'Não chame o operador de "operador"', "pedido", chave="nao_chamar:operador")
    cp.adicionar("estilo", 'Chame o operador de "Rafa"', "pedido", chave="chamar")  # troca o nome
    textos = sorted(c["texto"] for c in cp.ler()["camadas"])
    assert textos == ['Chame o operador de "Rafa".', 'Não chame o operador de "operador".']
    assert not cp.parecidas("Use ironia sutil.", "Não use ironia sutil.")
    # a mesma forma com outro sentido são duas camadas (no teste real, "informais" apagou "curtas")
    assert not cp.parecidas("Respostas mais curtas.", "Respostas mais informais.")
    assert cp.parecidas("Respostas mais curtas.", "Responda de forma mais curta, por favor.")
    assert cp.parecidas("Quando o operador adiar o estudo, cobre o estudo primeiro.",
                        "Se o operador adiar o estudo, cobre o estudo antes de tudo.")


def test_editar_apagar_e_erros():
    camada = cp.adicionar("gosto", "O operador gosta de notícias de tecnologia", "manual")["camada"]
    assert cp.editar(camada["id"], texto="Gosta de notícias de ciência", ativa=False, fixada=True, tipo="piada")["tipo"] == "piada"
    guardada = cp.ler()["camadas"][0]
    assert (guardada["texto"], guardada["ativa"], guardada["fixada"]) == ("Gosta de notícias de ciência.", False, True)
    with pytest.raises(cp.ErroDasCamadas):
        cp.editar(camada["id"], texto="   ")
    with pytest.raises(cp.ErroDasCamadas):
        cp.adicionar("estilo", "x" * 301)
    cp.apagar(camada["id"])
    with pytest.raises(cp.ErroDasCamadas, match="não existe mais"):
        cp.apagar(camada["id"])


def test_guarda_no_maximo_as_mais_uteis(monkeypatch):
    monkeypatch.setattr(cp, "MAX_CAMADAS", 3)
    fixa = cp.adicionar("estilo", "Regra fixa número zero sobre tamanho", "pedido")["camada"]
    cp.editar(fixa["id"], fixada=True)
    for i, assunto in enumerate(["futebol aos domingos", "astronomia e planetas", "culinária japonesa", "jazz antigo"]):
        cp.adicionar("gosto", f"O operador gosta de {assunto}", "conversa", relogio=lambda i=i: 100.0 + i)
    camadas = cp.ler()["camadas"]
    assert len(camadas) == 3 and any(c["id"] == fixa["id"] for c in camadas)
    assert not any("futebol" in c["texto"] for c in camadas)  # a mais antiga e menos vista saiu


# ------------------------------------------------------------------ no prompt
def test_prompt_local_e_da_nuvem():
    cp.adicionar("estilo", "Respostas mais curtas", "conversa")
    cp.adicionar("habito", "O operador estuda à noite", "conversa")
    pedido = cp.adicionar("estilo", 'Chame o operador de "Rani"', "pedido", chave="chamar")["camada"]
    desligada = cp.adicionar("gosto", "O operador gosta de jazz", "conversa")["camada"]
    cp.editar(desligada["id"], ativa=False)
    local = cp.bloco_do_prompt(True)
    assert local.startswith("CAMADAS ADAPTATIVAS") and "estuda à noite" in local and "jazz" not in local
    assert local.index("Rani") < local.index("curtas")  # o pedido antes do aprendido
    nuvem = cp.bloco_do_prompt(False)
    assert "Rani" in nuvem and "estuda à noite" not in nuvem  # hábito não vai para a nuvem
    assert pedido["id"] in cp.para_a_janela()["na_nuvem"]


def test_personalidade_soma_as_camadas_em_todo_lugar():
    import llm_nuvem
    from llm_clients import llm_clients

    assert personalidade.sistema() == personalidade.TEXTO_PADRAO  # sem camadas, só o texto
    cp.adicionar("estilo", "Respostas mais curtas", "pedido")
    cp.adicionar("habito", "O operador estuda à noite", "conversa")
    assert personalidade.sistema().endswith("- [Hábito] O operador estuda à noite.")
    assert "Respostas mais curtas." in llm_clients._sistema_beatriz()
    assert llm_clients._sistema_beatriz(texto_da_personalidade="Rascunho.").startswith("Rascunho.\n\nCAMADAS")
    nuvem = llm_nuvem.sistema_beatriz()
    assert "Respostas mais curtas." in nuvem and "estuda à noite" not in nuvem


def test_arquivo_quebrado_nao_cala_a_beatriz():
    with open(cp.caminho(), "w", encoding="utf-8") as saida:
        saida.write("{quebrado")
    assert personalidade.sistema() == personalidade.TEXTO_PADRAO


# ------------------------------------------------------------------ aprender com a conversa
def test_le_so_o_fim_do_historico_e_o_que_e_novo(tmp_path):
    arquivo = historico(tmp_path, [(f"pergunta {i}", f"resposta {i}") for i in range(40)])
    with open(arquivo, "a", encoding="utf-8") as saida:
        saida.write("{linha cortada\n")
    trocas = cp.trocas_recentes(1030.0, limite=24, arquivo=arquivo)
    assert [t["pergunta"] for t in trocas] == [f"pergunta {i}" for i in range(31, 40)]
    assert len(cp.trocas_recentes(0, limite=5, arquivo=arquivo)) == 5


def test_aprende_so_o_que_tem_confianca_e_nao_tira_funcoes(tmp_path):
    arquivo = historico(tmp_path, [("vou só arrumar minhas pastas antes de estudar", "Ah, sim: prioridade nacional."),
                                   ("haha boa", "Copiado."), ("quais filmes estão em cartaz?", "Em Brasília há 30 filmes.")])
    existente = cp.adicionar("estilo", "Respostas mais curtas", "pedido")["camada"]
    llm = LLMFalso({"novas": [
        {"tipo": "habito", "texto": "Quando o operador adiar o estudo, cobre o estudo primeiro.", "evidencia": "pastas", "confianca": "alta"},
        {"tipo": "gosto", "texto": "O operador gosta de filmes de terror.", "evidencia": "filmes", "confianca": "media"},
        {"tipo": "estilo", "texto": "Evite responder sobre filmes em cartaz, direcione para um site.", "confianca": "alta"},
    ], "reforcar": [existente["id"], "id-que-nao-existe"]})
    avisos = []
    aprendiz = cp.Aprendiz(llm=llm, avisar=avisos.append, arquivo_historico=arquivo)
    novas = aprendiz.aprender_agora()
    assert [c["texto"] for c in novas] == ["Quando o operador adiar o estudo, cobre o estudo primeiro."]
    assert avisos == ["Camada nova (hábito): Quando o operador adiar o estudo, cobre o estudo primeiro."]
    dados = cp.ler()
    assert dados["analisado_ate"] == 1002.0
    assert next(c for c in dados["camadas"] if c["id"] == existente["id"])["vezes"] == 2
    assert "vou só arrumar minhas pastas" in llm.pedidos[0][0] and llm.pedidos[0][2] == "json"
    assert existente["id"] in llm.pedidos[0][0]  # a IA vê as camadas que já existem
    assert aprendiz.aprender_agora() == [] and len(llm.pedidos) == 1  # nada novo: nem chama a IA


def test_ia_fora_do_ar_nao_marca_como_lido(tmp_path):
    arquivo = historico(tmp_path, [("oi", "Na escuta.")])
    assert cp.Aprendiz(llm=LLMFalso(erro=RuntimeError("Ollama fora")), arquivo_historico=arquivo).aprender_agora() == []
    assert cp.ler()["analisado_ate"] == 0.0


# ------------------------------------------------------------------ pedidos diretos
def test_nome_e_o_que_nao_e_pedido_sobre_ela():
    agente = cp.AgenteCamadas(llm=LLMFalso())
    assert agente.responder("Beatriz, me chama de Raní") == "Parâmetro atualizado. Daqui pra frente, Raní."
    assert agente.responder("para de me chamar de operador") == 'Copiado. "operador" saiu do meu vocabulário.'
    for frase in ("me chama de manhã às 7", "fala mais devagar", "fala mais sobre a reforma tributária",
                  "me chama de novo daqui a pouco", "qual a previsão do tempo?", "para de falar"):
        assert agente.responder(frase) is None, frase
    textos = sorted(c["texto"] for c in cp.ler()["camadas"])
    assert textos == ['Chame o operador de "Raní".', 'Não chame o operador de "operador".']


def test_pedido_de_estilo_vira_camada_pela_ia():
    llm = LLMFalso({"camada": "Responda de forma mais curta e direta.", "tipo": "estilo",
                    "confirmacao": "Claro, daqui pra frente, direto ao ponto."},
                   {"camada": "", "tipo": "estilo", "confirmacao": ""})
    agente = cp.AgenteCamadas(llm=llm, ultima_resposta=lambda: "Uma resposta longa demais.")
    assert agente.responder("fala mais curto, por favor") == "Daqui pra frente, direto ao ponto."  # sem o "Claro,"
    assert 'Última resposta da Beatriz: "Uma resposta longa demais."' in llm.pedidos[0][0]
    assert cp.ler()["camadas"][0]["origem"] == "pedido"
    assert agente.responder("gostei quando você respondeu a pergunta da prova") is None  # a IA disse que não é sobre o jeito


def test_sem_ia_guarda_o_pedido_como_veio():
    agente = cp.AgenteCamadas(llm=LLMFalso(erro=RuntimeError("Ollama fora")))
    assert agente.responder("seja menos formal comigo") == "Copiado. Ajuste registrado."
    assert cp.ler()["camadas"][0]["texto"] == 'O operador pediu: "seja menos formal comigo".'


def test_listar_esquecer_e_ligar_o_aprendizado():
    agente = cp.AgenteCamadas(llm=LLMFalso())
    assert agente.responder("o que você aprendeu sobre mim?").startswith("Ainda não tenho camadas sobre você.")
    cp.adicionar("estilo", "Respostas mais curtas", "pedido", relogio=lambda: 1.0)
    cp.adicionar("gosto", "O operador gosta de astronomia", "conversa", relogio=lambda: 2.0)
    assert agente.responder("o que você aprendeu sobre mim?") == (
        "Tenho 2 camadas ativas. Respostas mais curtas. O operador gosta de astronomia.")
    assert agente.responder("esquece a última camada") == "Camada removida: O operador gosta de astronomia."
    assert agente.responder("para de aprender sozinha").startswith("Aprendizado automático em pausa")
    assert cp.ler()["aprender"] is False
    assert agente.responder("pode voltar a aprender").startswith("Aprendizado automático religado")
    assert cp.ler()["aprender"] is True


# ------------------------------------------------------------------ onde está
def test_main_e_o_painel(monkeypatch):
    import main
    import painel_web as pw

    assert isinstance(main.CAMADAS, cp.AgenteCamadas) and isinstance(main.APRENDIZ, cp.Aprendiz)
    api = pw.ApiPainel(None, {}, None)
    criada = api.camadas_nova("habito", "O operador estuda à noite")
    assert criada["ok"] and criada["dados"]["camadas"][0]["origem"] == "manual"
    camada_id = criada["dados"]["camadas"][0]["id"]
    editada = api.camadas_editar(camada_id, {"ativa": False, "origem": "pedido"})  # "origem" não se muda pela janela
    assert not editada["dados"]["camadas"][0]["ativa"] and editada["dados"]["camadas"][0]["origem"] == "manual"
    assert api.camadas_aprendizado(False)["dados"]["aprender"] is False
    monkeypatch.setattr(api._aprendiz, "aprender_agora", lambda forcar=False: [{"texto": "x"}] if forcar else [])
    assert api.camadas_aprender()["mensagem"] == "1 camada nova."
    assert api.camadas_apagar(camada_id)["dados"]["camadas"] == []
    assert not api.camadas_apagar(camada_id)["ok"]
