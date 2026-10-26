"""Testes da personalidade editável (personalidade.py) e da janela [ personalidade ] do painel, sem a IA de verdade."""
import json

import pytest

import personalidade
from personalidade import ErroDaPersonalidade


def frases_com(**trocas):
    frases = {chave: list(lista) for chave, lista in personalidade.FRASES_PADRAO.items()}
    frases.update(trocas)
    return frases


# ------------------------------------------------------------------ arquivo
def test_sem_arquivo_vale_o_padrao():
    dados = personalidade.atual()
    assert dados["texto"] == personalidade.TEXTO_PADRAO and dados["atualizado"] is None
    assert personalidade.frases("so_nome") == personalidade.FRASES_PADRAO["so_nome"]
    assert "Calma tática" in personalidade.texto() and "Pessoa:" in personalidade.exemplos()


def test_salva_le_e_guarda_a_anterior():
    personalidade.salvar({"texto": "Você é a Beatriz. Primeira versão.", "exemplos": "Pessoa: oi. Beatriz: Na escuta.",
                          "frases": frases_com(obrigado=["Disponha.", "", "Disponha."])})
    assert personalidade.texto() == "Você é a Beatriz. Primeira versão."
    assert personalidade.frases("obrigado") == ["Disponha."]  # sem vazias e sem repetidas
    assert personalidade.atual()["atualizado"]
    personalidade.salvar({"texto": "Segunda versão.", "exemplos": "", "frases": frases_com()})
    assert personalidade.texto() == "Segunda versão."
    assert personalidade.exemplos() == personalidade.EXEMPLOS_PADRAO  # vazio volta ao padrão
    assert personalidade.recuperar_anterior()["texto"] == "Você é a Beatriz. Primeira versão."


def test_voltar_ao_padrao_guarda_a_versao_para_recuperar():
    personalidade.salvar({"texto": "Minha versão.", "exemplos": "", "frases": frases_com()})
    assert personalidade.restaurar_padrao()["texto"] == personalidade.TEXTO_PADRAO
    assert personalidade.recuperar_anterior()["texto"] == "Minha versão."


def test_sem_versao_anterior():
    with pytest.raises(ErroDaPersonalidade, match="Não há uma versão anterior"):
        personalidade.recuperar_anterior()


def test_outro_processo_grava_e_ela_relê():
    assert personalidade.texto() == personalidade.TEXTO_PADRAO
    with open(personalidade.caminho(), "w", encoding="utf-8") as saida:  # o painel, em outro processo
        json.dump({"texto": "Escrito pelo painel.", "frases": {"tchau": ["Até."]}}, saida)
    assert personalidade.texto() == "Escrito pelo painel."
    assert personalidade.frases("tchau") == ["Até."] and personalidade.frases("erro") == personalidade.FRASES_PADRAO["erro"]


def test_arquivo_quebrado_vale_o_padrao():
    with open(personalidade.caminho(), "w", encoding="utf-8") as saida:
        saida.write("{isso não é json")
    assert personalidade.texto() == personalidade.TEXTO_PADRAO


@pytest.mark.parametrize("dados, mensagem", [
    ({"texto": "  "}, "não pode ficar vazio"),
    ({"texto": "x" * 20001}, "passou de 20000"),
    ({"texto": "ok", "frases": {"saudacao": ["{periodo}. Oi, {nome}!"]}}, "{nome} não existe"),
    ({"texto": "ok", "frases": {"tchau": ["Até {logo"]}}, "ficou sozinha"),
    ({"texto": "ok", "frases": {"erro": ["", "  "]}}, "pelo menos uma frase"),
    ({"texto": "ok", "frases": {"obrigado": [f"Frase {i}." for i in range(31)]}}, "mais de 30"),
])
def test_valida_antes_de_gravar(dados, mensagem):
    with pytest.raises(ErroDaPersonalidade, match=mensagem.replace("{", r"\{").replace("}", r"\}")):
        personalidade.salvar(dados)


def test_frases_em_texto_uma_por_linha():
    limpo = personalidade.validar({"texto": "ok", "frases": {"so_nome": "Na escuta.\n\nDiga.\n"}})
    assert limpo["frases"]["so_nome"] == ["Na escuta.", "Diga."]
    assert limpo["frases"]["tchau"] == personalidade.FRASES_PADRAO["tchau"]  # o que não veio fica como estava


def test_o_que_a_janela_recebe():
    janela = personalidade.para_a_janela()
    saudacao = next(f for f in janela["frases"] if f["chave"] == "saudacao")
    assert saudacao["campos"] == ["periodo"] and saudacao["rotulo"] == "Saudação"
    assert janela["personalizada"] is False and janela["tem_anterior"] is False
    assert janela["padrao"]["texto"] == personalidade.TEXTO_PADRAO
    assert personalidade.resultado(personalidade.salvar, {"texto": ""}) == {
        "ok": False, "mensagem": "O texto de quem ela é não pode ficar vazio.", "dados": None}


# ------------------------------------------------------------------ quem usa
def test_frases_prontas_e_ia_usam_a_versao_salva():
    import jeito_de_falar
    import llm_nuvem
    import main
    from llm_clients import llm_clients

    personalidade.salvar({"texto": "Você é a Beatriz editada.", "exemplos": "Pessoa: oi. Beatriz: Na escuta, operador.",
                          "frases": frases_com(tchau=["Encerrando."], saudacao=["{periodo}. Pauta?"])})
    assert jeito_de_falar.frase("tchau") == "Encerrando." and jeito_de_falar.todas("tchau") == ["Encerrando."]
    assert jeito_de_falar.frase("saudacao", periodo="Bom dia") == "Bom dia. Pauta?"
    assert llm_clients._sistema_beatriz().startswith("Você é a Beatriz editada.")
    assert llm_clients._sistema_beatriz(texto_da_personalidade="Rascunho.").startswith("Rascunho.")
    assert llm_nuvem.sistema_beatriz().startswith("Você é a Beatriz editada.")
    assert main.contexto_conversa_falada().endswith("Pessoa: oi. Beatriz: Na escuta, operador.")
    assert main.responder_encerramento("tchau") == "Encerrando."


def test_janela_do_painel(monkeypatch):
    import painel_web as pw
    from llm_clients import llm_clients

    api = pw.ApiPainel(None, {}, None)
    assert api.personalidade()["texto"] == personalidade.TEXTO_PADRAO
    salvo = api.personalidade_salvar({"texto": "Versão da janela.", "exemplos": "", "frases": frases_com()})
    assert salvo["ok"] and salvo["dados"]["texto"] == "Versão da janela." and salvo["dados"]["personalizada"]
    assert not api.personalidade_salvar({"texto": "ok", "frases": {"tchau": ["{x}"]}})["ok"]
    assert api.personalidade_padrao()["dados"]["tem_anterior"]
    assert api.personalidade_anterior()["dados"]["texto"] == "Versão da janela."

    pedidos = []
    monkeypatch.setattr(llm_clients, "is_ollama_available", lambda: True)
    monkeypatch.setattr(llm_clients, "chat", lambda pergunta, system_prompt=None, usar_historico=False, **_:
                        pedidos.append((pergunta, system_prompt, usar_historico)) or "Diagnóstico: tédio.")
    teste = api.personalidade_testar("tô entediado", "Rascunho da tela.", "Pessoa: x. Beatriz: y.")
    assert teste["ok"] and teste["resposta"] == "Diagnóstico: tédio."
    pergunta, sistema, historico = pedidos[0]
    assert pergunta == "tô entediado" and historico is False
    assert sistema.startswith("Rascunho da tela.") and "Pessoa: x. Beatriz: y." in sistema  # o rascunho, sem salvar
    assert not api.personalidade_testar("  ")["ok"]


def test_teste_sem_ia_local_explica(monkeypatch):
    import painel_web as pw
    from llm_clients import llm_clients

    def fora_do_ar(*_, **__):
        raise RuntimeError("Ollama não está disponível.")

    monkeypatch.setattr(llm_clients, "chat", fora_do_ar)
    resposta = pw.ApiPainel(None, {}, None).personalidade_testar("oi")
    assert not resposta["ok"] and resposta["mensagem"].startswith("A IA local não respondeu")
