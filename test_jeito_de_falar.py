"""Testes do jeito de falar (frases variadas) e da conversa que flui depois do "Beatriz"."""
import collections
import time

import main
from jeito_de_falar import FRASES, frase, tipo_de_encerramento


# ------------------------------------------------------------------ frases
def test_frase_nunca_repete_a_ultima():
    anteriores = [frase("obrigado") for _ in range(60)]
    assert all(a != b for a, b in zip(anteriores, anteriores[1:]))
    assert set(anteriores) == set(FRASES["obrigado"])  # e usa todas as variações


def test_frase_preenche_os_campos():
    assert frase("foi_comigo", texto="toca um rock").endswith("Entendi: toca um rock.")
    assert frase("saudacao", periodo="Bom dia").count("Bom dia") == 1


def test_frases_curtas_para_o_audio_pronto():
    assert all(len(texto) <= 25 for texto in FRASES["so_nome"])


def test_reconhece_agradecimento_e_despedida():
    for texto in ("valeu", "Muito obrigada!", "brigado mesmo", "obrigado pela ajuda", "Beatriz, valeu"):
        assert tipo_de_encerramento(texto) == "obrigado", texto
    for texto in ("tchau", "Tchau, Beatriz!", "tchau tchau", "era só isso", "obrigado, era só isso",
                  "valeu, até mais", "pode descansar", "falou"):
        assert tipo_de_encerramento(texto) == "tchau", texto
    for texto in ("obrigado, e qual o clima amanhã?", "valeu pela dica do filme, e o horário?", "tchau não, espera",
                  "que horas são"):
        assert tipo_de_encerramento(texto) is None, texto


# ---------------------------------------------------- respostas do main
def test_tchau_encerra_a_conversa_mas_nao_desliga():
    assert not main.comando_solicita_encerramento("tchau", main.PALAVRAS_DESLIGAR)
    assert not main.comando_solicita_encerramento("até logo", main.PALAVRAS_DESLIGAR)
    assert not main.comando_solicita_encerramento("parar", main.PALAVRAS_DESLIGAR)
    assert main.comando_solicita_encerramento("desligar", main.PALAVRAS_DESLIGAR)
    assert main.comando_solicita_encerramento("pode desligar", main.PALAVRAS_DESLIGAR)
    assert main.responder_encerramento("tchau") in FRASES["tchau"]
    assert main.responder_encerramento("valeu!") in FRASES["obrigado"]
    assert main.responder_encerramento("me conta uma piada") is None


def test_categoria_aceita_a_descricao_no_lugar_do_rotulo():
    # Respostas reais do llama3.1:8b e do qwen2.5:14b que antes viravam None (e o papo ia para a biblioteca).
    assert main.categoria_da_resposta("conversa") == "simples"
    # Desde out/2026, conhecimento geral é uma categoria à parte (vai para a nuvem): veja test_conversa_nuvem.py.
    assert main.categoria_da_resposta("Conhecimento geral está") == "conhecimento"
    assert main.categoria_da_resposta("Simples.") == "simples"
    assert main.categoria_da_resposta("pesquisa") == "pesquisa"
    assert main.categoria_da_resposta("Notícias") == "pesquisa"
    assert main.categoria_da_resposta("ferramentas") == "ferramentas"
    assert main.categoria_da_resposta("Pedro, você") is None


def test_risada_escrita_e_outros_alfabetos_saem_da_fala():
    assert main.limpar_texto_para_fala("Ahah, que bom! Hahaha essa foi boa, kkkk.") == "Que bom! essa foi boa."
    assert main.limpar_texto_para_fala("Excesso de problemas. Risos. Outra?") == "Excesso de problemas. Outra?"
    assert main.limpar_texto_para_fala("Que legal! 这是 tá ótimo, o 1º lugar") == "Que legal! tá ótimo, o 1º lugar."


def test_saudacao_inicial_animada_com_a_agenda():
    texto = main.saudacao_inicial("Hoje você ainda tem 1 compromisso.")
    assert "Todos os sistemas estão online" not in texto
    assert "Hoje você ainda tem 1 compromisso." in texto
    assert any(texto.endswith(convite) for convite in FRASES["convite"])
    assert any(abertura in texto for abertura in FRASES["abertura"])


def test_responde_saudacao_com_variacao():
    assert main.responder_saudacao("oi") in [f.format(periodo=main.periodo_do_dia(time.localtime().tm_hour))
                                             for f in FRASES["saudacao"]]


# ------------------------------------------------- conversa sem o nome
def _escuta(monkeypatch, avaliacao):
    """Filtro da escuta sem voz, painel nem Ollama; frases sem o nome são classificadas como `avaliacao`."""
    falas, avaliadas = [], []
    monkeypatch.setattr(main, "ESTADO_ATIVACAO", main.EstadoAtivacao())
    monkeypatch.setattr(main, "CONFIRMACAO", main.Confirmacao())
    monkeypatch.setattr(main, "_ULTIMA_PERGUNTA", {"em": 0.0})
    monkeypatch.setattr(main, "REGISTRO_CONVERSA", collections.deque(maxlen=600))
    monkeypatch.setattr(main, "EXIGIR_PALAVRA_ATIVACAO", True)
    monkeypatch.setattr(main, "ENTENDER_SEM_NOME", True)
    monkeypatch.setattr(main, "JANELA_CONVERSA_SEGUNDOS", 40.0)
    monkeypatch.setattr(main, "falou_durante", lambda inicio, fim: False)
    monkeypatch.setattr(main, "fim_da_ultima_fala", lambda: 0.0)
    monkeypatch.setattr(main, "falar_async", falas.append)
    monkeypatch.setattr(main, "PAINEL_VOZ", None)

    def avaliar(texto, em_conversa=False):
        avaliadas.append((texto, em_conversa))
        return avaliacao

    monkeypatch.setattr(main, "avaliar_fala_sem_nome", avaliar)
    return falas, avaliadas


def _conversa_de_20_segundos_atras():
    """Ela respondeu há 20 s: a janela de uma frase (8 s) fechou, a conversa (40 s) continua."""
    main.abrir_janela_continuacao(main.ESTADO_ATIVACAO, time.time() - 20, "Hoje faz sol!")


def test_no_meio_da_conversa_o_duvidoso_e_com_ela(monkeypatch):
    falas, avaliadas = _escuta(monkeypatch, "talvez")
    _conversa_de_20_segundos_atras()
    assert main.filtrar_fala_por_ativacao("sério? e amanhã também?", 1.0, 2.0) == "sério? e amanhã também?"
    assert avaliadas == [("sério? e amanhã também?", True)]
    assert falas == []  # sem o "Foi comigo?"


def test_conversa_da_casa_continua_ignorada(monkeypatch):
    falas, _ = _escuta(monkeypatch, "conversa")
    _conversa_de_20_segundos_atras()
    assert main.filtrar_fala_por_ativacao("filho, vem almoçar", 1.0, 2.0) == ""


def test_valeu_no_meio_da_conversa_nem_passa_pela_ia(monkeypatch):
    _, avaliadas = _escuta(monkeypatch, "conversa")
    _conversa_de_20_segundos_atras()
    assert main.filtrar_fala_por_ativacao("valeu!", 1.0, 2.0) == "valeu!"
    assert avaliadas == []


def test_depois_do_tchau_volta_a_perguntar(monkeypatch):
    falas, avaliadas = _escuta(monkeypatch, "talvez")
    _conversa_de_20_segundos_atras()
    main.ESTADO_ATIVACAO.encerrar()
    assert main.filtrar_fala_por_ativacao("e amanhã, vai chover?", 1.0, 2.0) == ""
    assert avaliadas == [("e amanhã, vai chover?", False)]
    assert len(falas) == 1 and "Entendi: e amanhã, vai chover?" in falas[0]


def test_conversa_acaba_sozinha_depois_do_silencio(monkeypatch):
    falas, _ = _escuta(monkeypatch, "talvez")
    main.abrir_janela_continuacao(main.ESTADO_ATIVACAO, time.time() - 90, "Hoje faz sol!")
    assert main.filtrar_fala_por_ativacao("e amanhã, vai chover?", 1.0, 2.0) == ""
    assert len(falas) == 1  # perguntou "Foi comigo?"


def test_conversa_desligada_com_zero(monkeypatch):
    falas, _ = _escuta(monkeypatch, "talvez")
    monkeypatch.setattr(main, "JANELA_CONVERSA_SEGUNDOS", 0.0)
    _conversa_de_20_segundos_atras()
    assert not main.ESTADO_ATIVACAO.em_conversa(time.time())
