"""Testes do modo de escuta "sempre": conversa aberta sem o nome, filtros (voz, ambiente, reunião), oferta de ajuda,
interrupção pela sua voz, comandos para trocar de modo e o aviso de janela de erro. Sem Ollama, microfone ou tela."""
import collections
import time

import pytest

import agente_pastas
import leitura_tela
import main
from biometria_voz import Falante
from leitura_tela import Atividade, Janela


@pytest.fixture
def escuta(monkeypatch):
    """O filtro da escuta no modo "sempre", sem voz, painel nem Ollama. `avaliacoes` guarda o que a IA recebeu."""
    falas, avaliacoes = [], []
    monkeypatch.setitem(main.ESCUTA, "modo", "sempre")
    monkeypatch.setattr(main, "ESTADO_ATIVACAO", main.EstadoAtivacao())
    monkeypatch.setattr(main, "CONFIRMACAO", main.Confirmacao())
    monkeypatch.setattr(main, "_ULTIMA_PERGUNTA", {"em": 0.0})
    monkeypatch.setattr(main, "REGISTRO_CONVERSA", collections.deque(maxlen=600))
    monkeypatch.setattr(main, "EXIGIR_PALAVRA_ATIVACAO", True)
    monkeypatch.setattr(main, "ENTENDER_SEM_NOME", True)
    monkeypatch.setattr(main, "falou_durante", lambda inicio, fim: False)
    monkeypatch.setattr(main, "fim_da_ultima_fala", lambda: 0.0)
    monkeypatch.setattr(main, "falar_async", falas.append)
    monkeypatch.setattr(main, "PAINEL_VOZ", None)
    escuta.resposta = "pedido"

    def avaliar(texto, em_conversa=False, procurar_necessidade=False):
        avaliacoes.append((texto, em_conversa, procurar_necessidade))
        return escuta.resposta

    monkeypatch.setattr(main, "avaliar_fala_sem_nome", avaliar)
    escuta.falas, escuta.avaliacoes = falas, avaliacoes
    return escuta


def dono_cadastrado(monkeypatch):
    """A biometria em uso (o dono cadastrado): `falante` passa a importar."""
    monkeypatch.setattr(main.BIOMETRIA, "_perfis", {"dono": {"papel": "dono", "nome": "Felipe", "vetor": [1.0] + [0.0] * 127}})
    assert main.BIOMETRIA.ativa


DONO = Falante("dono", "Felipe", 0.85)
VISITA = Falante("convidado", "", 0.1)


# ------------------------------------------------------------------ modos e janelas
def test_janela_da_conversa_de_cada_modo(monkeypatch):
    monkeypatch.setattr(main, "JANELA_CONVERSA_SEGUNDOS", 40.0)
    monkeypatch.setattr(main, "JANELA_CONVERSA_SEMPRE", 900.0)
    for modo, janela in (("sempre", 900.0), ("conversa", 40.0), ("nome", 0.0)):
        monkeypatch.setitem(main.ESCUTA, "modo", modo)
        assert main.janela_da_conversa() == janela


def test_conversa_continua_aberta_por_quinze_minutos(monkeypatch):
    monkeypatch.setitem(main.ESCUTA, "modo", "sempre")
    monkeypatch.setattr(main, "JANELA_CONVERSA_SEMPRE", 900.0)
    estado = main.EstadoAtivacao()
    main.abrir_janela_continuacao(estado, 1000.0, "")
    assert estado.em_conversa(1000.0 + 600) and not estado.em_conversa(1000.0 + 1000)
    main.abrir_janela_continuacao(estado, 1500.0, "")  # cada resposta renova
    assert estado.em_conversa(1500.0 + 800)


def test_estado_do_painel_no_modo_sempre(monkeypatch):
    monkeypatch.setitem(main.ESCUTA, "modo", "sempre")
    assert main.estado_escuta() == "escutando sempre"
    monkeypatch.setitem(main.ESCUTA, "modo", "conversa")
    assert main.estado_escuta() in {"escutando", "aguardando o nome"}


@pytest.mark.parametrize("frase", ["só quando eu chamar", "Beatriz, só me atende quando eu te chamar", "não precisa ficar me escutando",
                                   "para de me escutar o tempo todo", "modo nome"])
def test_comando_para_so_responder_quando_chamada(escuta, frase):
    assert "só respondo quando você me chamar" in main.controlar_modo_de_escuta(frase)
    assert main.ESCUTA["modo"] == "nome"


@pytest.mark.parametrize("frase", ["pode ficar escutando", "fica sempre ouvindo", "não precisa mais me chamar", "escuta o tempo todo",
                                   "modo conversa"])
def test_comando_para_voltar_a_escutar_sempre(escuta, frase):
    main.ESCUTA["modo"] = "nome"
    assert "sem você precisar me chamar" in main.controlar_modo_de_escuta(frase)
    assert main.ESCUTA["modo"] == "sempre"


def test_outras_frases_nao_mudam_o_modo(escuta):
    for frase in ("que horas são", "me escuta com atenção", "escuta essa música", "quero ficar sozinho"):
        assert main.controlar_modo_de_escuta(frase) is None
    assert main.ESCUTA["modo"] == "sempre"


def test_modo_nome_ignora_frases_sem_o_nome(escuta):
    main.ESCUTA["modo"] = "nome"
    assert main.filtrar_fala_por_ativacao("que horas são agora") == ""
    assert escuta.avaliacoes == []  # nem pergunta à IA
    assert main.filtrar_fala_por_ativacao("Beatriz, que horas são") == "que horas são"


# ------------------------------------------------------------------ frases sem o nome
def test_sem_o_nome_um_pedido_claro_e_atendido(escuta):
    assert main.filtrar_fala_por_ativacao("qual a previsão do tempo amanhã") == "qual a previsão do tempo amanhã"
    escuta.resposta = "conversa"
    assert main.filtrar_fala_por_ativacao("filho, vem almoçar agora") == ""


def test_fora_da_conversa_a_ia_procura_necessidades_dentro_da_conversa_nao(escuta):
    main.filtrar_fala_por_ativacao("que horas são agora")
    assert escuta.avaliacoes[-1][1:] == (False, True)  # fora da conversa: procura necessidade
    main.abrir_janela_continuacao(main.ESTADO_ATIVACAO, time.time(), "Oi!")
    main.ESTADO_ATIVACAO.fechar()  # passaram os 8 s da janela de uma frase; a conversa (15 min) continua aberta
    main.filtrar_fala_por_ativacao("e amanhã também")
    assert escuta.avaliacoes[-1][1:] == (True, False)


def test_sem_ofertas_a_ia_nem_recebe_o_pedido_de_necessidade(escuta, monkeypatch):
    monkeypatch.setattr(main, "OFERTAS_DE_AJUDA", False)
    main.filtrar_fala_por_ativacao("que horas são agora")
    assert escuta.avaliacoes[-1][2] is False


def test_necessidade_vira_oferta_de_ajuda(escuta, monkeypatch):
    escuta.resposta = "necessidade"
    ofertas = []
    monkeypatch.setattr(main, "oferecer_ajuda", lambda texto: ofertas.append(texto) or True)
    assert main.filtrar_fala_por_ativacao("preciso marcar o dentista") == ""
    assert ofertas == ["preciso marcar o dentista"]


def test_no_meio_da_conversa_necessidade_e_pedido(escuta, monkeypatch):
    escuta.resposta = "necessidade"
    monkeypatch.setattr(main, "oferecer_ajuda", lambda texto: pytest.fail("não devia oferecer: já estão conversando"))
    main.abrir_janela_continuacao(main.ESTADO_ATIVACAO, time.time(), "Oi!")
    assert main.filtrar_fala_por_ativacao("preciso marcar o dentista") == "preciso marcar o dentista"


def test_resposta_a_pergunta_de_pasta_passa_sem_o_nome(escuta, monkeypatch):
    escuta.resposta = "conversa"  # a IA acharia que é papo; mas ela perguntou "qual delas?"
    agente = main.AgentePastas()
    agente.pergunta = agente_pastas.Pergunta([object()], time.time() + 60)
    monkeypatch.setattr(main, "PASTAS", agente)
    main.abrir_janela_continuacao(main.ESTADO_ATIVACAO, time.time(), "Achei 2 pastas. Qual delas?")
    assert main.filtrar_fala_por_ativacao("a segunda") == "a segunda"
    assert escuta.avaliacoes == []


# ------------------------------------------------------------------ voz e ambiente
def test_so_a_voz_do_dono_vale_sem_o_nome(escuta, monkeypatch):
    dono_cadastrado(monkeypatch)
    assert main.filtrar_fala_por_ativacao("que horas são agora", falante=DONO) == "que horas são agora"
    assert main.filtrar_fala_por_ativacao("que horas são agora", falante=VISITA) == ""
    assert len(escuta.avaliacoes) == 1  # a da visita nem foi avaliada: não era a voz dele
    assert main.filtrar_fala_por_ativacao("Beatriz, que horas são", falante=VISITA) == "que horas são"  # com o nome vale


def test_voz_incerta_sem_o_nome_nao_e_atendida(escuta, monkeypatch):
    dono_cadastrado(monkeypatch)
    assert main.filtrar_fala_por_ativacao("que horas são agora", falante=Falante("incerto", "", 0.4)) == ""


def test_outra_pessoa_falando_por_perto_pede_o_nome_de_volta(escuta, monkeypatch):
    dono_cadastrado(monkeypatch)
    main.filtrar_fala_por_ativacao("oi, tudo bem com você", falante=VISITA)  # a visita falou
    assert main.AMBIENTE["outra_voz_em"] > 0
    assert main.filtrar_fala_por_ativacao("que horas são agora", falante=DONO) == ""
    main.AMBIENTE["outra_voz_em"] = time.time() - main.AMBIENTE_SEGUNDOS - 1
    assert main.filtrar_fala_por_ativacao("que horas são agora", falante=DONO) == "que horas são agora"


def test_numa_reuniao_so_atende_com_o_nome(escuta, monkeypatch):
    monkeypatch.setattr(leitura_tela, "atividade_atual", lambda: Atividade("Teams", "Reunião", "numa reunião"))
    assert main.filtrar_fala_por_ativacao("que horas são agora") == ""
    assert main.filtrar_fala_por_ativacao("Beatriz, que horas são") == "que horas são"
    monkeypatch.setattr(leitura_tela, "atividade_atual", lambda: Atividade("Visual Studio Code", "main.py", "programando"))
    assert main.filtrar_fala_por_ativacao("que horas são agora") == "que horas são agora"


def test_sua_voz_interrompe_a_fala_sem_o_nome_e_a_dela_nao(escuta, monkeypatch):
    dono_cadastrado(monkeypatch)
    parou = []
    monkeypatch.setattr(main, "falou_durante", lambda inicio, fim: True)  # ela está falando
    monkeypatch.setattr(main, "parece_eco", lambda texto: False)
    monkeypatch.setattr(main, "parar_fala", lambda: parou.append(True))
    agora = time.time()
    assert main.filtrar_fala_por_ativacao("espera, mostra outro", agora - 1, agora, falante=DONO) == "espera, mostra outro"
    assert parou == [True]
    parou.clear()
    assert main.filtrar_fala_por_ativacao("a previsão do tempo é de sol", agora - 1, agora, falante=VISITA) == ""  # o eco ou outra voz
    assert parou == []


def test_sem_biometria_falar_por_cima_continua_exigindo_o_nome(escuta, monkeypatch):
    monkeypatch.setattr(main, "falou_durante", lambda inicio, fim: True)
    monkeypatch.setattr(main, "parece_eco", lambda texto: False)
    agora = time.time()
    assert main.filtrar_fala_por_ativacao("espera, mostra outro", agora - 1, agora) == ""


# ------------------------------------------------------------------ oferta de ajuda
class IAFalsa:
    def __init__(self, resposta):
        self.resposta, self.prompts = resposta, []

    def chat(self, prompt, **_):
        self.prompts.append(prompt)
        return self.resposta


def preparar_oferta(monkeypatch, resposta):
    falas = []
    ia = IAFalsa(resposta)
    monkeypatch.setattr(main, "llm_clients", ia)
    monkeypatch.setattr(main, "CONFIRMACAO", main.Confirmacao())
    monkeypatch.setattr(main, "falar_async", falas.append)
    monkeypatch.setattr(main, "PAINEL_VOZ", None)
    monkeypatch.setattr(main, "_ULTIMA_PERGUNTA", {"em": 0.0})
    return ia, falas


def test_oferta_de_ajuda_pergunta_e_o_sim_faz_o_pedido(monkeypatch):
    ia, falas = preparar_oferta(monkeypatch, 'Claro: {"oferta": "Quer que eu marque o dentista na agenda?", "comando": "marca dentista"}')
    assert main.oferecer_ajuda("preciso marcar o dentista") is True
    assert falas == ["Quer que eu marque o dentista na agenda?"]
    assert "preciso marcar o dentista" in ia.prompts[0]
    assert main.CONFIRMACAO.comando == "marca dentista"
    monkeypatch.setattr(main, "ESTADO_ATIVACAO", main.EstadoAtivacao())
    monkeypatch.setattr(main, "fim_da_ultima_fala", lambda: 0.0)
    monkeypatch.setattr(main, "falou_durante", lambda inicio, fim: False)
    assert main.filtrar_fala_por_ativacao("sim") == "marca dentista"


def test_oferta_so_a_cada_tres_minutos(monkeypatch):
    ia, falas = preparar_oferta(monkeypatch, '{"oferta": "Quer que eu marque o dentista?", "comando": "marca dentista"}')
    assert main.oferecer_ajuda("preciso marcar o dentista") is True
    main.CONFIRMACAO.limpar()
    assert main.oferecer_ajuda("preciso marcar o médico") is False and len(ia.prompts) == 1
    main.AMBIENTE["ultima_oferta"] -= main.INTERVALO_ENTRE_OFERTAS + 1
    assert main.oferecer_ajuda("preciso marcar o médico") is True


@pytest.mark.parametrize("resposta", ["{}", "não sei", '{"oferta": "Tá calor mesmo!", "comando": "x"}',
                                      '{"oferta": "Quer que eu faça algo?", "comando": ""}', "{isto não é json}"])
def test_sem_oferta_valida_ela_fica_quieta(monkeypatch, resposta):
    ia, falas = preparar_oferta(monkeypatch, resposta)
    assert main.oferecer_ajuda("nossa que calor") is False and falas == [] and not main.CONFIRMACAO.ativa(time.time())


def test_ia_fora_do_ar_nao_derruba_a_oferta(monkeypatch):
    ia, falas = preparar_oferta(monkeypatch, "")
    ia.chat = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Ollama fora do ar"))
    assert main.oferecer_ajuda("preciso marcar o dentista") is False


# ------------------------------------------------------------------ janela de erro
@pytest.fixture
def tela(monkeypatch):
    monkeypatch.setitem(main.ESCUTA, "modo", "sempre")
    monkeypatch.setattr(main, "CONFIRMACAO", main.Confirmacao())
    monkeypatch.setattr(main, "falou_durante", lambda inicio, fim: False)
    monkeypatch.setattr(main, "falar_async", lambda texto: None)
    monkeypatch.setattr(main, "PAINEL_VOZ", None)
    monkeypatch.setattr(main, "MICROFONE_EVENTO", None)
    janelas = {"atual": Janela(1, "chrome", "Erro 404 - Google Chrome", (0, 0, 800, 600))}
    monkeypatch.setattr(leitura_tela, "janela_em_foco", lambda: janelas["atual"])
    return janelas


def test_janela_de_erro_gera_oferta_uma_vez(tela):
    avisados = {}
    assert main.olhar_a_tela_uma_vez(avisados) is True
    assert main.CONFIRMACAO.comando == "olha esse erro na tela"
    main.CONFIRMACAO.limpar()
    main.AMBIENTE["ultima_oferta"] = 0.0
    assert main.olhar_a_tela_uma_vez(avisados) is False  # a mesma janela não é avisada de novo
    assert leitura_tela.interpretar("olha esse erro na tela") is not None  # e o "sim" é entendido pela leitura de tela


def test_titulos_de_arquivo_no_editor_nao_sao_erro(tela):
    tela["atual"] = Janela(1, "code", "erros.py - projeto - Visual Studio Code", (0, 0, 800, 600))
    assert main.olhar_a_tela_uma_vez({}) is False
    tela["atual"] = Janela(1, "code", "Traceback (most recent call last) - Visual Studio Code", (0, 0, 800, 600))
    assert main.olhar_a_tela_uma_vez({}) is True


def test_nao_avisa_de_erro_fora_do_modo_sempre_ou_de_janela_comum(tela, monkeypatch):
    tela["atual"] = Janela(1, "chrome", "Notícias - Google Chrome", (0, 0, 800, 600))
    assert main.olhar_a_tela_uma_vez({}) is False
    tela["atual"] = Janela(1, "chrome", "Erro 404 - Google Chrome", (0, 0, 800, 600))
    monkeypatch.setitem(main.ESCUTA, "modo", "nome")
    assert main.olhar_a_tela_uma_vez({}) is False
    monkeypatch.setitem(main.ESCUTA, "modo", "sempre")
    monkeypatch.setattr(main, "TELA_OBSERVAR", False)
    assert main.olhar_a_tela_uma_vez({}) is False


def test_janela_de_senhas_nao_e_nem_avisada(tela):
    tela["atual"] = Janela(1, "keepassxc", "Erro de sincronização - KeePassXC", (0, 0, 800, 600))
    assert main.olhar_a_tela_uma_vez({}) is False
