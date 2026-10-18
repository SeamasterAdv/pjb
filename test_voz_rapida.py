"""Conversa por voz mais rápida: fila de fala, eco, interrupção e janelas de escuta."""
import collections
import threading
import time

import config
import main
import voice


def _fala_falsa(monkeypatch, demora_geracao=0.3, demora_toque=0.05):
    """Troca a geração e a reprodução do áudio por versões falsas e devolve o que foi tocado."""
    tocados = []
    monkeypatch.setattr(voice, "AUDIO_DISPONIVEL", True)
    monkeypatch.setattr(voice, "_FRASES_PRONTAS", {})

    def gerar(texto):
        time.sleep(demora_geracao)
        return f"falso-{texto}.mp3"

    def tocar(arquivo):
        tocados.append(arquivo)
        time.sleep(demora_toque)

    monkeypatch.setattr(voice, "_gerar_arquivo_fala", gerar)
    monkeypatch.setattr(voice, "_tocar", tocar)
    return tocados


def test_trechos_sao_gerados_em_paralelo_e_tocados_em_ordem(monkeypatch):
    tocados = _fala_falsa(monkeypatch, demora_geracao=0.3)
    terminou = threading.Event()
    inicio = time.perf_counter()
    voice._enfileirar(["um", "dois", "três"], terminou)
    assert terminou.wait(5)
    # Em sequência levaria 3 x 0,3 s de geração; em paralelo, pouco mais que uma.
    assert time.perf_counter() - inicio < 0.75
    assert tocados == ["falso-um.mp3", "falso-dois.mp3", "falso-três.mp3"]
    assert not voice.esta_falando()


def test_parar_fala_descarta_o_que_ainda_nao_tocou(monkeypatch):
    tocados = _fala_falsa(monkeypatch, demora_geracao=0.05, demora_toque=0.3)
    terminou = threading.Event()
    voice._enfileirar(["um", "dois", "três"], terminou)
    limite = time.time() + 3
    while not tocados and time.time() < limite:
        time.sleep(0.01)
    voice.parar_fala()
    assert terminou.wait(5)
    assert tocados == ["falso-um.mp3"]


def test_resposta_em_stream_fala_cada_frase_assim_que_chega(monkeypatch):
    enfileirados = []
    monkeypatch.setattr(voice, "AUDIO_DISPONIVEL", True)
    monkeypatch.setattr(voice, "_enfileirar", lambda blocos, evento_fim=None: enfileirados.extend(blocos))
    texto = voice.falar_resposta_em_stream(["Oi! Tudo", " bem? Hoje vai", " fazer **sol**."])
    assert texto == "Oi! Tudo bem? Hoje vai fazer **sol**."
    assert enfileirados == ["Oi!", "Tudo bem?", "Hoje vai fazer sol."]

    # Primeira frase comprida: o começo já vai para a voz na primeira vírgula depois de 5 palavras.
    enfileirados.clear()
    voice.falar_resposta_em_stream(["O céu parece azul por causa", " da luz do sol, que se espalha", " no ar. Fim."])
    assert enfileirados == ["O céu parece azul por causa da luz do sol,", "que se espalha no ar.", "Fim."]


def test_reconhece_quando_ela_falava_e_o_eco_da_propria_voz(monkeypatch):
    monkeypatch.setattr(voice, "_INTERVALOS_FALA", collections.deque([(100.0, 104.0)]))
    monkeypatch.setattr(voice, "_TOCANDO_DESDE", None)
    assert voice.falou_durante(103.0, 106.0)
    assert voice.falou_durante(104.2, 106.0)  # eco logo depois do fim
    assert not voice.falou_durante(105.0, 107.0)

    monkeypatch.setattr(voice, "_TEXTOS_FALADOS", collections.deque([(time.time(), "Hoje vai fazer sol à tarde.")]))
    assert voice.parece_eco("hoje vai fazer sol")
    assert not voice.parece_eco("me lembra de comprar pão")


def test_escuta_continua_entrega_frases_sem_reabrir_o_microfone(monkeypatch):
    aberturas = []

    class MicrofoneFalso:
        def __enter__(self):
            aberturas.append(1)
            return self

        def __exit__(self, *args):
            return False

    um_segundo = voice.sr.AudioData(b"\0" * 32000, 16000, 2)
    frases = [um_segundo, um_segundo]

    def escutar_falso(self, fonte, timeout=None, phrase_time_limit=None):
        if frases:
            return frases.pop()
        time.sleep(0.01)
        raise voice.sr.WaitTimeoutError()

    monkeypatch.setattr(voice.sr, "Microphone", lambda device_index=None: MicrofoneFalso())
    monkeypatch.setattr(voice.sr.Recognizer, "listen", escutar_falso)
    monkeypatch.setattr(voice.sr.Recognizer, "adjust_for_ambient_noise", lambda self, fonte, duration=1: None)
    monkeypatch.setattr(voice, "_transcrever", lambda audio: ("Beatriz, que horas são", 0.92))
    monkeypatch.setattr(voice, "preparar_transcricao", lambda: None)  # não carrega o Whisper de verdade
    monkeypatch.setattr(voice, "microfone_disponivel", lambda: True)

    recebidas = []
    pronto = threading.Event()

    def ao_ouvir(texto, inicio, fim, confianca):
        recebidas.append((texto, round(fim - inicio, 1), confianca))
        if len(recebidas) == 2:
            pronto.set()

    voice.iniciar_escuta_continua(ao_ouvir)
    try:
        assert pronto.wait(5)
    finally:
        voice.parar_escuta_continua()
        voice._ESCUTA.join(3)
    assert recebidas == [("Beatriz, que horas são", 1.0, 0.92)] * 2
    assert aberturas == [1]  # o microfone ficou aberto entre uma frase e outra


def test_deteccao_do_microfone_fica_em_cache(monkeypatch):
    chamadas = []
    monkeypatch.setattr(voice, "_detectar_microfone", lambda: chamadas.append(1) or True)
    voice._invalidar_cache_microfone()
    try:
        assert voice.microfone_disponivel() and voice.microfone_disponivel()
        assert len(chamadas) == 1
        voice.selecionar_microfone(None)  # trocar de microfone vale na hora
        voice.microfone_disponivel()
        assert len(chamadas) == 2
    finally:
        voice._invalidar_cache_microfone()


# ------------------------------------------------------------------ filtro da escuta
def _filtro(monkeypatch, falando, eco=False, avaliacao="conversa"):
    """Prepara o filtro da escuta sem voz, sem painel e sem o Ollama (frases sem o nome = `avaliacao`)."""
    paradas = []
    monkeypatch.setattr(main, "ESTADO_ATIVACAO", main.EstadoAtivacao())
    monkeypatch.setattr(main, "CONFIRMACAO", main.Confirmacao())
    monkeypatch.setattr(main, "_ULTIMA_PERGUNTA", {"em": 0.0})
    monkeypatch.setattr(main, "REGISTRO_CONVERSA", collections.deque(maxlen=600))
    monkeypatch.setattr(main, "EXIGIR_PALAVRA_ATIVACAO", True)
    monkeypatch.setattr(main, "ENTENDER_SEM_NOME", True)
    monkeypatch.setattr(main, "falou_durante", lambda inicio, fim: falando)
    monkeypatch.setattr(main, "parece_eco", lambda texto: eco)
    monkeypatch.setattr(main, "parar_fala", lambda: paradas.append(True))
    monkeypatch.setattr(main, "falar_async", lambda texto: None)
    monkeypatch.setattr(main, "avaliar_fala_sem_nome", lambda texto, em_conversa=False: avaliacao)
    return paradas


def test_eco_da_propria_voz_e_ignorado(monkeypatch):
    paradas = _filtro(monkeypatch, falando=True, eco=True)
    assert main.filtrar_fala_por_ativacao("Beatriz hoje vai fazer sol", 1.0, 2.0) == ""
    assert not paradas


def test_conversa_por_cima_da_resposta_nao_entra_nem_com_a_janela_aberta(monkeypatch):
    paradas = _filtro(monkeypatch, falando=True)
    main.abrir_janela_continuacao(main.ESTADO_ATIVACAO, time.time(), "uma resposta qualquer")
    assert main.filtrar_fala_por_ativacao("vem cá mãe, olha isso", 1.0, 2.0) == ""
    assert not paradas


def test_chamar_pelo_nome_interrompe_a_fala(monkeypatch):
    paradas = _filtro(monkeypatch, falando=True)
    assert main.filtrar_fala_por_ativacao("Beatriz, que horas são?", 1.0, 2.0) == "que horas são?"
    assert paradas


def test_beatriz_para_so_interrompe(monkeypatch):
    paradas = _filtro(monkeypatch, falando=False)
    assert main.filtrar_fala_por_ativacao("Beatriz, para!", 1.0, 2.0) == ""
    assert paradas


def test_depois_de_chamar_o_nome_pode_falar_por_cima_do_pode_falar(monkeypatch):
    paradas = _filtro(monkeypatch, falando=False)
    assert main.filtrar_fala_por_ativacao("Beatriz", 1.0, 2.0) == ""
    monkeypatch.setattr(main, "falou_durante", lambda inicio, fim: True)
    assert main.filtrar_fala_por_ativacao("que horas são", 3.0, 4.0) == "que horas são"
    assert paradas


class _PainelFalso:
    def __init__(self):
        self.eventos = []

    def put_nowait(self, evento):
        self.eventos.append((evento["tipo"], evento["texto"], evento["motivo"]))


def test_painel_mostra_o_que_foi_ouvido_e_por_que_foi_ignorado(monkeypatch):
    _filtro(monkeypatch, falando=False)
    painel = _PainelFalso()
    monkeypatch.setattr(main, "PAINEL_VOZ", painel)

    main.filtrar_fala_por_ativacao("boa noite", 1.0, 2.0)
    main.filtrar_fala_por_ativacao("Beatriz, que horas são", 3.0, 4.0)
    main.filtrar_fala_por_ativacao("Beatriz", 5.0, 6.0)
    assert painel.eventos == [
        ("ignorado", "boa noite", "diga “Beatriz” antes do pedido"),
        ("ouvido", "Beatriz, que horas são", ""),
        ("ouvido", "Beatriz", ""),
        ("resposta", painel.eventos[-1][1], ""),
    ]
    assert painel.eventos[-1][1] in main.todas_as_frases("so_nome")

    # Enquanto ela fala: o eco nem aparece; outra voz aparece como ignorada.
    painel.eventos.clear()
    monkeypatch.setattr(main, "ESTADO_ATIVACAO", main.EstadoAtivacao())
    monkeypatch.setattr(main, "falou_durante", lambda inicio, fim: True)
    monkeypatch.setattr(main, "parece_eco", lambda texto: texto == "eco")
    main.filtrar_fala_por_ativacao("eco", 1.0, 2.0)
    main.filtrar_fala_por_ativacao("vem cá mãe", 1.0, 2.0)
    assert [tipo for tipo, _, _ in painel.eventos] == ["ignorado"]
    assert "eu estava falando" in painel.eventos[0][2]


def test_resposta_a_saudacao_vale_sem_o_nome(monkeypatch):
    _filtro(monkeypatch, falando=False)
    monkeypatch.setattr(main, "fim_da_ultima_fala", lambda: 0.0)
    main.cumprimentar()  # "... Bora começar?"
    assert main.filtrar_fala_por_ativacao("boa noite", 1.0, 2.0) == "boa noite"


def test_janela_de_seguimento_conta_do_fim_real_da_fala(monkeypatch):
    monkeypatch.setattr(main, "JANELA_CONTINUACAO_SEGUNDOS", 8)
    estado = main.EstadoAtivacao()
    main.abrir_janela_continuacao(estado, 100.0)  # a resposta começou a ser falada em 100
    # A voz atrasou e só terminou em 125: a pergunta em 130 ainda é seguimento.
    assert main.extrair_comando_ativado("e depois?", 130.0, estado, exigir=True, fim_fala=125.0) == "e depois?"
    main.abrir_janela_continuacao(estado, 200.0)
    assert main.extrair_comando_ativado("e depois?", 230.0, estado, exigir=True, fim_fala=210.0) is None


# ------------------------------------------------------------------ Ollama
def test_ollama_no_endereco_ipv4():
    # "localhost" custava ~2 s por conexão no Windows; o .env pode trazer localhost.
    assert "localhost" not in config.OLLAMA_HOST


def test_status_do_ollama_fica_em_cache():
    from llm_clients import LLMClients

    class ClienteFalso:
        consultas = 0

        def list(self):
            self.consultas += 1

    cliente = LLMClients.__new__(LLMClients)
    cliente.ollama_client = ClienteFalso()
    cliente._ollama_ok_ate = 0.0
    assert cliente.is_ollama_available() and cliente.is_ollama_available()
    assert cliente.ollama_client.consultas == 1


# ------------------------------------------------------------------ sem o nome, confirmação e resumo
class _Falas:
    """Guarda o que ela falou (falar_async) para os testes conferirem."""

    def __init__(self, monkeypatch):
        self.ditas = []
        monkeypatch.setattr(main, "falar_async", self.ditas.append)


def test_pedido_claro_sem_o_nome_e_atendido(monkeypatch):
    _filtro(monkeypatch, falando=False, avaliacao="pedido")
    painel = _PainelFalso()
    monkeypatch.setattr(main, "PAINEL_VOZ", painel)
    assert main.filtrar_fala_por_ativacao("quais filmes estão em cartaz", 1.0, 2.0, 0.9) == "quais filmes estão em cartaz"
    assert painel.eventos == [("ouvido", "quais filmes estão em cartaz", "sem o nome, mas entendi como pedido")]


def test_na_duvida_pergunta_e_sim_faz_o_pedido(monkeypatch):
    _filtro(monkeypatch, falando=False, avaliacao="talvez")
    falas = _Falas(monkeypatch)
    assert main.filtrar_fala_por_ativacao("acho que vai chover amanhã", 1.0, 2.0) == ""
    assert falas.ditas == [falas.ditas[0]]
    assert falas.ditas[0] in [f.format(texto="acho que vai chover amanhã") for f in main.todas_as_frases("foi_comigo")]
    assert main.filtrar_fala_por_ativacao("sim", 3.0, 4.0) == "acho que vai chover amanhã"
    assert not main.CONFIRMACAO.ativa(time.time())


def test_nao_cancela_ou_troca_o_pedido(monkeypatch):
    _filtro(monkeypatch, falando=False, avaliacao="talvez")
    falas = _Falas(monkeypatch)
    main.filtrar_fala_por_ativacao("e o jogo de ontem", 1.0, 2.0)
    assert main.filtrar_fala_por_ativacao("não", 3.0, 4.0) == ""
    assert falas.ditas[-1] in main.todas_as_frases("deixa_pra_la")

    # "não, ..." com um pedido troca o pedido; com conversa ("não sei, liga pra ele") só cancela.
    monkeypatch.setattr(main, "avaliar_fala_sem_nome", lambda texto, em_conversa=False: "pedido" if "clima" in texto else "conversa")
    main.perguntar_antes("abre o youtube", "Entendi: abre o youtube. É isso?")
    assert main.filtrar_fala_por_ativacao("Não, eu quero saber o clima", 5.0, 6.0) == "eu quero saber o clima"
    main.perguntar_antes("abre o youtube", "Entendi: abre o youtube. É isso?")
    assert main.filtrar_fala_por_ativacao("não sei, liga pra ele", 7.0, 8.0) == ""
    assert falas.ditas[-1] in main.todas_as_frases("deixa_pra_la")


def test_sim_e_nao_fracos_so_valem_em_resposta_curta():
    assert main._sim_ou_nao("faz") == ("sim", "")
    assert main._sim_ou_nao("Beatriz, pode sim") == ("sim", "")
    assert main._sim_ou_nao("faz tempo que não vejo ele")[0] is None
    assert main._sim_ou_nao("deixa eu ver isso aí")[0] is None
    assert main._sim_ou_nao("sim, e aproveita e me fala o clima")[0] == "sim"  # "sim" vale sempre


def test_nao_pergunta_toda_hora_por_conversa_do_ambiente(monkeypatch):
    _filtro(monkeypatch, falando=False, avaliacao="talvez")
    falas = _Falas(monkeypatch)
    main.filtrar_fala_por_ativacao("será que ele vem hoje", 1.0, 2.0)
    main.filtrar_fala_por_ativacao("depois a gente vê isso aí", 3.0, 4.0)
    main.filtrar_fala_por_ativacao("ele disse que talvez venha", 5.0, 6.0)
    assert sum("Entendi:" in fala for fala in falas.ditas) == 1


def test_transcricao_incerta_pede_confirmacao(monkeypatch):
    _filtro(monkeypatch, falando=False)
    falas = _Falas(monkeypatch)
    assert main.filtrar_fala_por_ativacao("Beatriz, abre o spotfy", 1.0, 2.0, confianca=0.41) == ""
    assert falas.ditas == [falas.ditas[0]]
    assert falas.ditas[0] in [f.format(comando="abre o spotfy") for f in main.todas_as_frases("confirmar_ouvido")]
    # A resposta vale mesmo dita por cima da pergunta, e interrompe a fala.
    paradas = []
    monkeypatch.setattr(main, "falou_durante", lambda inicio, fim: True)
    monkeypatch.setattr(main, "parar_fala", lambda: paradas.append(True))
    assert main.filtrar_fala_por_ativacao("isso mesmo", 3.0, 4.0) == "abre o spotfy"
    assert paradas


def test_nao_era_isso_pede_desculpas_e_escuta_de_novo(monkeypatch):
    _filtro(monkeypatch, falando=False)
    falas = _Falas(monkeypatch)
    main.abrir_janela_continuacao(main.ESTADO_ATIVACAO, time.time(), "resposta qualquer")
    assert main.filtrar_fala_por_ativacao("não era isso", 1.0, 2.0) == ""
    assert len(falas.ditas) == 1 and falas.ditas[0] in main.todas_as_frases("entendi_errado")
    assert main.filtrar_fala_por_ativacao("quero os filmes de terror", 3.0, 4.0) == "quero os filmes de terror"


def test_sem_entender_sem_nome_volta_a_ignorar(monkeypatch):
    _filtro(monkeypatch, falando=False, avaliacao="pedido")
    monkeypatch.setattr(main, "ENTENDER_SEM_NOME", False)
    assert main.filtrar_fala_por_ativacao("quais filmes estão em cartaz", 1.0, 2.0) == ""


def test_resumo_do_que_ouviu_usa_so_o_ollama_local(monkeypatch):
    _filtro(monkeypatch, falando=False)
    for frase in ["resume o que você ouviu", "faz um resumo da conversa dos últimos 10 minutos",
                  "Beatriz, o que foi falado na reunião?"]:
        assert main.pede_resumo_da_conversa(frase), frase
    assert not main.pede_resumo_da_conversa("pesquise e me dê um resumo sobre a reforma tributária")
    assert main.minutos_pedidos("resumo dos últimos 10 minutos") == 10
    assert main.minutos_pedidos("resume a última hora de conversa") == 60
    assert main.minutos_pedidos("resume a conversa") == 30

    assert "Ouvi muito pouco" in main.resumir_o_que_ouvi()
    main.filtrar_fala_por_ativacao("amanhã a reunião é às dez", 1.0, 2.0)  # ignorada, mas registrada
    main.avisar_painel("ouvido", "Beatriz, me lembra de levar o relatório")
    main.avisar_painel("resposta", "Combinado, vou lembrar.")
    pedidos = []
    monkeypatch.setattr(main.llm_clients, "chat_beatriz",
                        lambda prompt, contexto=None, usar_historico=True: pedidos.append(prompt) or "Resumo.")
    assert main.resumir_o_que_ouvi(30) == "Resumo."
    assert "Ouvido sem resposta: amanhã a reunião é às dez" in pedidos[0]
    assert "Beatriz: Combinado, vou lembrar." in pedidos[0]


def test_transcricao_traz_a_confianca_do_google(monkeypatch):
    respostas = iter([
        {"alternative": [{"transcript": "que horas são", "confidence": 0.87}], "final": True},
        {"alternative": [{"transcript": "abre o youtube"}], "final": True},  # sem confiança
        [],  # não entendeu nada
    ])
    monkeypatch.setattr(voice, "TRANSCRICAO_VOZ", "google")
    monkeypatch.setattr(voice._TRANSCRITOR, "recognize_google", lambda audio, language, show_all: next(respostas))
    assert voice._transcrever(None) == ("que horas são", 0.87)
    assert voice._transcrever(None) == ("abre o youtube", None)
    assert voice._transcrever(None) == ("", None)
