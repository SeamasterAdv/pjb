"""Testes da biometria vocal: perfis, identificação, cadastro, comandos falados e o que um convidado pode pedir.

Um codificador falso troca o modelo ECAPA: o "áudio" é um vetor cujo primeiro valor diz de quem é a voz.
"""
import json

import numpy as np
import pytest

import biometria_voz
import main
from biometria_voz import AgenteBiometria, Biometria, Falante

TAXA = biometria_voz.TAXA


class CodificadorFalso:
    """Voz `n` = vetor unitário na direção n (vozes diferentes são ortogonais); o segundo valor mistura vozes."""

    def __init__(self):
        self.preparou = False

    def preparar(self):
        self.preparou = True

    def vetor(self, audio, espera=0.0):
        voz, mistura = int(audio[0]), float(audio[1])
        v = np.zeros(8, dtype=np.float32)
        v[voz] = 1.0
        v[(voz + 1) % 8] = mistura  # `mistura` aproxima a voz da seguinte (nota intermediária)
        return v / np.linalg.norm(v)


def audio(voz, segundos=3.0, mistura=0.0):
    a = np.zeros(int(segundos * TAXA), dtype=np.float32)
    a[0], a[1] = voz, mistura
    return a


@pytest.fixture
def bio(tmp_path, monkeypatch):
    return Biometria(caminho=str(tmp_path / "vozes.json"), codificador=CodificadorFalso(), habilitada=True)


def cadastrar_dono(bio, voz=0, nome="Felipe"):
    falas = []
    bio.cadastro.iniciar("dono", nome, "dono", falas.append, alvo=20.0)
    for _ in range(8):
        bio.receber_amostra(audio(voz, 3.0))
    bio.cadastro.cancelar()
    return falas


# ------------------------------------------------------------------ perfis e identificação
def test_sem_ninguem_cadastrado_todo_mundo_e_o_dono(bio):
    assert not bio.ativa
    assert bio.identificar(audio(3)) is None
    assert bio.pode_tudo(None) and bio.pode_tudo(Falante("convidado"))


def test_cadastro_guarda_o_dono_e_depois_reconhece(bio):
    falas = cadastrar_dono(bio)
    assert bio.tem_dono() and bio.ativa
    assert any("guardei a sua voz" in f for f in falas)
    assert any("Metade" in f for f in falas)
    assert bio.identificar(audio(0)).tipo == "dono"
    convidado = bio.identificar(audio(3))
    assert convidado.tipo == "convidado" and not bio.pode_tudo(convidado)
    assert bio.pode_tudo(bio.identificar(audio(0)))


def test_perfil_sobrevive_a_reabrir(bio, tmp_path):
    cadastrar_dono(bio)
    outro = Biometria(caminho=bio.caminho, codificador=CodificadorFalso(), habilitada=True)
    assert outro.ativa and outro.identificar(audio(0)).tipo == "dono"
    assert "vetor" in open(bio.caminho, encoding="utf-8").read()  # só números: nenhum áudio é guardado


def test_frase_curta_ou_nota_do_meio_e_incerta(bio):
    cadastrar_dono(bio)
    assert bio.identificar(audio(0, segundos=0.5)).tipo == "incerto"
    meio = bio.identificar(audio(0, mistura=1.7))  # cosseno ~0,5: perto do limiar
    assert meio.tipo in {"incerto", "dono"}
    entre = bio.identificar(audio(0, mistura=3.0))  # cosseno ~0,32 < limiar de convidado
    assert entre.tipo == "convidado"


def test_incerto_herda_quem_falou_ha_pouco(bio):
    cadastrar_dono(bio)
    dono = bio.resolver(bio.identificar(audio(0)), agora=100.0)
    curta = bio.resolver(bio.identificar(audio(0, segundos=0.4)), agora=110.0)
    assert curta.tipo == "dono" and curta.herdado
    longe = bio.resolver(Falante("incerto"), agora=100.0 + biometria_voz.BIOMETRIA_MANTER_SEGUNDOS + 1)
    assert longe.tipo == "incerto" and not bio.pode_tudo(longe)
    assert dono.tipo == "dono"


def test_outra_pessoa_cadastrada_vira_conhecida(bio):
    cadastrar_dono(bio)
    bio.cadastro.iniciar("maria", "Maria", "conhecido", lambda t: None, alvo=10.0)
    for _ in range(4):
        bio.receber_amostra(audio(2, 3.0))
    maria = bio.identificar(audio(2))
    assert maria.tipo == "conhecido" and maria.nome == "Maria" and not bio.pode_tudo(maria)
    assert bio.identificar(audio(0)).tipo == "dono"


def test_cadastro_com_varias_vozes_misturadas_e_recusado(bio):
    falas = []
    bio.cadastro.iniciar("dono", "Felipe", "dono", falas.append, alvo=12.0)
    for voz in (0, 2, 4, 6):  # quatro pessoas diferentes: nenhuma voz é consistente
        bio.receber_amostra(audio(voz, 3.0))
    assert not bio.tem_dono()
    assert any("mesma voz" in f for f in falas)


def test_cadastro_ignora_frases_curtas_demais(bio):
    bio.cadastro.iniciar("dono", "Felipe", "dono", lambda t: None, alvo=3.0)
    bio.receber_amostra(audio(0, 1.0))
    assert bio.cadastro.ativo and bio.cadastro.segundos == 0
    bio.cadastro.cancelar()


def test_so_ha_um_dono_cadastrar_de_novo_substitui(bio):
    cadastrar_dono(bio, voz=0)
    cadastrar_dono(bio, voz=5, nome="Outro")
    assert [p for p in bio.perfis() if p[1] == "dono"] == [("Outro", "dono")]
    assert bio.identificar(audio(5)).tipo == "dono" and bio.identificar(audio(0)).tipo == "convidado"


# ------------------------------------------------------------------ comandos falados
@pytest.fixture
def agente(bio):
    falas = []
    agente = AgenteBiometria(bio, falas.append)
    agente.falas = falas
    return agente


def test_comando_cadastra_minha_voz_inicia_o_cadastro(agente, bio):
    resposta = agente.responder("Beatriz, cadastra minha voz, eu sou o Felipe", None)
    assert "Felipe" in resposta and "20 segundos" in resposta
    assert bio.cadastro.ativo and bio.cadastro.papel == "dono" and bio.cadastro.nome == "Felipe"
    bio.cadastro.cancelar()


def test_so_o_dono_cadastra_outras_vozes_e_troca_a_propria(agente, bio):
    cadastrar_dono(bio)
    convidado = Falante("convidado", nota=0.1)
    assert agente.responder("cadastra minha voz", convidado) == biometria_voz.NEGADO
    assert agente.responder("cadastra a voz da Maria", convidado) == biometria_voz.NEGADO
    assert not bio.cadastro.ativo
    resposta = agente.responder("cadastra a voz da Maria", Falante("dono", "Felipe", 0.8))
    assert "Maria" in resposta and bio.cadastro.papel == "conhecido"
    bio.cadastro.cancelar()


def test_cadastro_pedido_pelo_whatsapp_escuta_no_microfone(agente, bio):
    # Saída para quando ela não reconhece o dono: pela voz, "cadastra minha voz" seria recusado.
    cadastrar_dono(bio)
    assert agente.responder("cadastra minha voz", Falante("convidado", nota=0.2)) == biometria_voz.NEGADO
    resposta = agente.responder("cadastra minha voz", None, pelo_microfone=False)  # WhatsApp: é o dono
    assert "no microfone do computador" in resposta and "Vou juntar" in resposta
    assert bio.cadastro.ativo and bio.cadastro.papel == "dono"
    bio.cadastro.cancelar()


def test_cadastrar_de_novo_soma_as_amostras(bio):
    cadastrar_dono(bio)
    falas = cadastrar_dono(bio)
    assert any("juntei com o que eu já tinha da sua voz: agora são 14 amostras" in f for f in falas)
    with open(bio.caminho, encoding="utf-8") as arquivo:
        perfil = json.load(arquivo)["perfis"]["dono"]
    assert perfil["amostras"] == 14 and len(perfil["amostras_vetores"]) == 14
    assert perfil["segundos"] == 42.0  # 7 frases de 3 s em cada cadastro (antes ficava 0)


def test_segundo_cadastro_de_outro_jeito_passa_a_reconhecer(bio):
    # O primeiro cadastro foi falando de um jeito (voz 0 "pura"); no dia a dia a voz chega diferente (misturada):
    # a nota fica no meio e ela não reconhece. Um cadastro nessas condições se soma ao primeiro e resolve.
    cadastrar_dono(bio)
    no_dia_a_dia = audio(0, mistura=2.0)  # cosseno ~0,45 com o primeiro cadastro
    assert bio.identificar(no_dia_a_dia).tipo != "dono"
    falas = []
    bio.cadastro.iniciar("dono", "Felipe", "dono", falas.append, alvo=20.0)
    for _ in range(7):
        bio.receber_amostra(audio(0, 3.0, mistura=2.0))
    assert any("juntei" in f for f in falas)
    assert bio.identificar(no_dia_a_dia).tipo == "dono"
    assert bio.identificar(audio(0)).tipo == "dono"  # o jeito do primeiro cadastro continua valendo
    assert bio.identificar(audio(3)).tipo == "convidado"


def test_perfil_antigo_so_com_a_media_tambem_soma(bio, tmp_path):
    vetor = [1.0] + [0.0] * 7
    with open(bio.caminho, "w", encoding="utf-8") as arquivo:
        json.dump({"versao": 1, "perfis": {"dono": {"nome": "Felipe", "papel": "dono", "vetor": vetor,
                                                    "segundos": 0.0, "amostras": 4}}}, arquivo)
    antigo = Biometria(caminho=bio.caminho, codificador=CodificadorFalso(), habilitada=True)
    cadastrar_dono(antigo)
    assert dict(antigo.perfis()) == {"Felipe": "dono"}
    assert antigo._perfis["dono"]["amostras"] == 4 + 7


def test_amostras_do_perfil_tem_limite(bio, monkeypatch):
    monkeypatch.setattr(biometria_voz, "MAX_AMOSTRAS_PERFIL", 10)
    cadastrar_dono(bio)
    cadastrar_dono(bio)
    assert bio._perfis["dono"]["amostras"] == 10


def test_quem_esta_falando(agente, bio):
    assert "ainda não tenho a sua voz" in agente.responder("quem está falando?", None)
    cadastrar_dono(bio)
    assert "É você, Felipe" in agente.responder("Beatriz, quem está falando?", Falante("dono", "Felipe", 0.81))
    assert "0,81" in agente.responder("quem está falando", Falante("dono", "Felipe", 0.81))
    assert "convidado" in agente.responder("quem está falando", Falante("convidado", nota=0.12))
    assert "Maria" in agente.responder("quem está falando", Falante("conhecido", "Maria", 0.7))


def test_esquecer_e_listar_vozes(agente, bio):
    cadastrar_dono(bio)
    bio.cadastro.iniciar("maria", "Maria", "conhecido", lambda t: None, alvo=6.0)
    for _ in range(2):
        bio.receber_amostra(audio(2, 3.0))
    dono = Falante("dono", "Felipe", 0.9)
    assert "Felipe, que é o dono" in agente.responder("quais vozes você conhece?", dono) and "Maria" in agente.responder("quais vozes você conhece", dono)
    assert agente.responder("esquece a voz da Maria", Falante("convidado")) == biometria_voz.NEGADO
    assert "esqueci a voz de Maria" in agente.responder("esquece a voz da Maria", dono)
    assert bio.perfis() == [("Felipe", "dono")]
    assert "esqueci a sua voz" in agente.responder("apaga minha voz", dono)
    assert not bio.tem_dono()


def test_ligar_e_desligar_o_reconhecimento(agente, bio):
    cadastrar_dono(bio)
    dono = Falante("dono", "Felipe", 0.9)
    assert agente.responder("desliga o reconhecimento de voz", Falante("convidado")) == biometria_voz.NEGADO
    assert "Desliguei" in agente.responder("desliga o reconhecimento de voz", dono)
    assert not bio.ativa and bio.identificar(audio(3)) is None
    assert "ligado" in agente.responder("liga o reconhecimento de voz", None)
    assert bio.ativa


def test_outras_frases_passam_direto(agente):
    for frase in ("que horas são", "toca uma música", "qual a voz do narrador do filme", "me explica o artigo 5"):
        assert agente.responder(frase, None) is None, frase


# ------------------------------------------------------------------ o que o main faz com cada voz
@pytest.fixture
def casa(bio, monkeypatch):
    """O main usa a biometria falsa com o dono cadastrado; tudo o que ela fala vai para `falas`."""
    cadastrar_dono(bio)
    falas = []
    monkeypatch.setattr(main, "BIOMETRIA", bio)
    monkeypatch.setattr(main, "falar_async", falas.append)
    monkeypatch.setattr(main, "PAINEL_VOZ", None)
    monkeypatch.setattr(main, "_AVISO_CONVIDADO", {"em": 0.0})
    return falas


def proibido(*args, **kwargs):
    raise AssertionError("o convidado chegou a uma função do dono")


def test_convidado_nao_mexe_em_nada_do_dono(casa, monkeypatch):
    for nome in ("notelooklm_responder", "responder_com_pesquisa_web", "tentar_executar_ferramenta"):
        monkeypatch.setattr(main, nome, proibido)
    monkeypatch.setattr(main.llm_clients, "chat_beatriz_stream", proibido)
    for pedido in ("imprime minha agenda", "manda um e-mail para o João", "abre o youtube", "o que tenho amanhã na agenda",
                   "desliga o computador", "faz um PDF sobre o clima", "pesquisa o preço do dólar"):
        casa.clear()
        main.responder_ao_convidado(pedido, Falante("convidado", nota=0.1))
        assert casa == [biometria_voz.NEGADO], pedido


def test_convidado_pode_ver_a_hora_o_tempo_e_conversar(casa, monkeypatch):
    monkeypatch.setattr(main, "obter_previsao_tempo", lambda cidade, dia: f"tempo {cidade or 'padrão'} {dia}")
    main.responder_ao_convidado("que horas são?", Falante("convidado", nota=0.1))
    assert casa[-1].startswith("Agora são")
    main.responder_ao_convidado("qual a previsão do tempo amanhã em Curitiba", Falante("convidado", nota=0.1))
    assert casa[-1].lower().startswith("tempo curitiba amanha")

    contextos = []

    def conversa(texto, contexto=None, usar_historico=True):
        contextos.append((contexto, usar_historico))
        return iter(["Claro! ", "Lá vai uma piada."])

    monkeypatch.setattr(main.llm_clients, "chat_beatriz_stream", conversa)
    monkeypatch.setattr(main, "falar_resposta_em_stream", lambda stream, limpar=None: "".join(stream))
    main.responder_ao_convidado("me conta uma piada", Falante("convidado", nota=0.1))
    contexto, historico = contextos[0]
    assert "NÃO é o dono" in contexto and historico is False  # sem o histórico (nem a memória) do dono


def test_convidado_conhecido_e_chamado_pelo_nome(casa, monkeypatch):
    main.responder_ao_convidado("bom dia", Falante("conhecido", "Maria", 0.7))
    assert casa[-1].startswith("Maria!")
    main.responder_ao_convidado("imprime isso", Falante("conhecido", "Maria", 0.7))
    assert "Maria" in casa[-1] and biometria_voz.NEGADO in casa[-1]


def test_convidado_desconhecido_ouve_o_aviso_so_uma_vez(casa):
    main.responder_ao_convidado("oi", Falante("convidado", nota=0.1))
    main.responder_ao_convidado("oi", Falante("convidado", nota=0.1))
    assert "Não conheço a sua voz" in casa[0] and "Não conheço a sua voz" not in casa[1]


def test_voz_incerta_pede_para_repetir(casa):
    main.responder_ao_convidado("imprime a agenda", Falante("incerto", nota=0.42))
    assert "repetir" in casa[-1]


def test_tchau_do_convidado_nao_encerra_a_conversa_do_dono(casa, monkeypatch):
    estado = main.EstadoAtivacao(conversando=True, conversa_ate=time_futuro())
    monkeypatch.setattr(main, "ESTADO_ATIVACAO", estado)
    main.responder_ao_convidado("tchau", Falante("convidado", nota=0.1))
    assert estado.conversando


def time_futuro():
    import time

    return time.time() + 100


def test_sim_de_convidado_nao_confirma_a_acao_do_dono(casa, monkeypatch):
    monkeypatch.setattr(main, "ESTADO_ATIVACAO", main.EstadoAtivacao())
    monkeypatch.setattr(main, "CONFIRMACAO", main.Confirmacao())
    monkeypatch.setattr(main, "falou_durante", lambda inicio, fim: False)
    monkeypatch.setattr(main, "fim_da_ultima_fala", lambda: 0.0)
    monkeypatch.setattr(main, "avaliar_fala_sem_nome", lambda texto, em_conversa=False: "conversa")
    main.perguntar_antes("manda a mensagem", "Mando a mensagem?")
    assert main.filtrar_fala_por_ativacao("sim", falante=Falante("convidado", nota=0.1)) == ""
    assert main.CONFIRMACAO.ativa(__import__("time").time())  # a pergunta continua esperando o dono
    assert main.filtrar_fala_por_ativacao("sim", falante=Falante("dono", "Felipe", 0.9)) == "manda a mensagem"


def test_o_falante_chega_ao_comando(casa, monkeypatch):
    monkeypatch.setattr(main, "ESTADO_ATIVACAO", main.EstadoAtivacao())
    monkeypatch.setattr(main, "falou_durante", lambda inicio, fim: False)
    monkeypatch.setattr(main, "fim_da_ultima_fala", lambda: 0.0)
    main.filtrar_fala_por_ativacao("Beatriz, que horas são", falante=Falante("convidado", nota=0.1))
    assert main.FALANTE["atual"].tipo == "convidado"
    main.filtrar_fala_por_ativacao("Beatriz, que horas são")
    assert main.FALANTE["atual"] is None
