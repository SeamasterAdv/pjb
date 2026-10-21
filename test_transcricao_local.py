"""Transcrição local da voz (Whisper) com um modelo falso: nada é baixado nem enviado ao Google."""
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest

import voice


def _segmento(texto, avg_logprob=-0.1, no_speech_prob=0.02):
    return SimpleNamespace(text=texto, avg_logprob=avg_logprob, no_speech_prob=no_speech_prob)


class _WhisperFalso:
    """Imita o WhisperModel do faster-whisper: devolve os segmentos dados e guarda cada chamada."""

    def __init__(self, *segmentos, erro=None):
        self.segmentos = segmentos
        self.erro = erro
        self.chamadas = []

    def transcribe(self, audio, **opcoes):
        self.chamadas.append((audio, opcoes))
        if self.erro:
            raise self.erro
        return iter(self.segmentos), SimpleNamespace(language="pt")


def _um_segundo(taxa=44100):
    """Um segundo de um tom de 440 Hz como o microfone entrega: PCM 16 bits, mono, 44,1 kHz."""
    tempo = np.arange(taxa) / taxa
    amostras = (np.sin(2 * np.pi * 440 * tempo) * 0.5 * 32767).astype(np.int16)
    return voice.sr.AudioData(amostras.tobytes(), taxa, 2)


def _whisper_carregado(monkeypatch, modelo, modo="auto"):
    estado = voice._WhisperLocal()
    estado.modelo = modelo
    estado._iniciado = True
    estado.pronto.set()
    monkeypatch.setattr(voice, "_WHISPER", estado)
    monkeypatch.setattr(voice, "TRANSCRICAO_VOZ", modo)


def _whisper_carregando(monkeypatch, modo):
    estado = voice._WhisperLocal()
    estado._iniciado = True  # carregando em segundo plano, ainda sem modelo
    monkeypatch.setattr(voice, "_WHISPER", estado)
    monkeypatch.setattr(voice, "TRANSCRICAO_VOZ", modo)
    return estado


def _google_proibido(monkeypatch):
    def falhar(audio):
        raise AssertionError("o áudio não podia ir para o Google")

    monkeypatch.setattr(voice, "_transcrever_google", falhar)


def test_audio_do_microfone_vira_16khz_mono_float():
    amostras = voice._audio_para_whisper(_um_segundo())
    assert isinstance(amostras, np.ndarray) and amostras.dtype == np.float32
    assert abs(len(amostras) - 16000) <= 1  # 1 s em 16 kHz
    assert 0.45 < np.abs(amostras).max() <= 0.51  # o volume se mantém, na escala de -1 a 1


def test_frase_clara_vem_com_o_texto_e_a_confianca(monkeypatch):
    modelo = _WhisperFalso(_segmento(" Beatriz, que horas são?", avg_logprob=-0.08))
    _whisper_carregado(monkeypatch, modelo, "whisper")
    _google_proibido(monkeypatch)
    texto, confianca = voice._transcrever(_um_segundo())
    assert texto == "Beatriz, que horas são?"
    assert confianca == pytest.approx(math.exp(-0.08))  # ~0,92
    audio, opcoes = modelo.chamadas[0]
    assert isinstance(audio, np.ndarray) and audio.dtype == np.float32
    assert opcoes["language"] == "pt" and opcoes["vad_filter"] is True


def test_confianca_conversa_com_o_minimo_da_confirmacao(monkeypatch):
    # Frase clara passa direto; frase duvidosa faz ela perguntar "Entendi: ... É isso?"
    # (0,6 é o padrão de CONFIANCA_MINIMA_VOZ).
    for logprob, pergunta_antes in [(-0.1, False), (-0.3, False), (-0.9, True)]:
        _whisper_carregado(monkeypatch, _WhisperFalso(_segmento("abre o spotify", avg_logprob=logprob)))
        _, confianca = voice._transcrever(_um_segundo())
        assert 0 < confianca <= 1
        assert (confianca < 0.6) == pergunta_antes, logprob


def test_silencio_e_frase_inventada_sao_descartados_sem_ir_ao_google(monkeypatch):
    _google_proibido(monkeypatch)
    casos = [
        [],  # o VAD cortou tudo
        [_segmento("Obrigado.", avg_logprob=-0.4, no_speech_prob=0.85)],
        [_segmento("Legendas pela comunidade Amara.org", avg_logprob=-0.3, no_speech_prob=0.2)],
    ]
    for segmentos in casos:
        _whisper_carregado(monkeypatch, _WhisperFalso(*segmentos), "auto")
        assert voice._transcrever(_um_segundo()) == ("", None), segmentos


def test_so_o_trecho_inventado_sai_e_a_confianca_pesa_cada_trecho(monkeypatch):
    _whisper_carregado(monkeypatch, _WhisperFalso(
        _segmento("Beatriz, abre o YouTube.", avg_logprob=-0.2),
        _segmento("Tchau.", avg_logprob=-0.2, no_speech_prob=0.9),
    ))
    assert voice._transcrever(_um_segundo()) == ("Beatriz, abre o YouTube.", pytest.approx(math.exp(-0.2)))

    _whisper_carregado(monkeypatch, _WhisperFalso(
        _segmento("Beatriz, me lembra de comprar pão", avg_logprob=-0.1),  # 33 letras
        _segmento("amanhã", avg_logprob=-0.8),  # 6 letras
    ))
    texto, confianca = voice._transcrever(_um_segundo())
    assert texto == "Beatriz, me lembra de comprar pão amanhã"
    assert confianca == pytest.approx(math.exp((33 * -0.1 + 6 * -0.8) / 39))


def test_modo_auto_usa_o_google_so_de_reserva(monkeypatch):
    monkeypatch.setattr(voice, "_transcrever_google", lambda audio: ("pelo Google", 0.8))
    carregando = _whisper_carregando(monkeypatch, "auto")
    assert voice._transcrever(_um_segundo()) == ("pelo Google", 0.8)  # não espera o modelo
    carregando.pronto.set()  # terminou sem modelo: o Whisper falhou ao carregar
    assert voice._transcrever(_um_segundo()) == ("pelo Google", 0.8)
    _whisper_carregado(monkeypatch, _WhisperFalso(erro=RuntimeError("sem memória")), "auto")
    assert voice._transcrever(_um_segundo()) == ("pelo Google", 0.8)


def test_modo_whisper_nunca_manda_o_audio_ao_google(monkeypatch):
    _google_proibido(monkeypatch)
    monkeypatch.setattr(voice, "ESPERA_WHISPER_SEGUNDOS", 0.05)
    _whisper_carregando(monkeypatch, "whisper")
    assert voice._transcrever(_um_segundo()) == ("", None)  # esperou o modelo e desistiu
    _whisper_carregado(monkeypatch, _WhisperFalso(erro=RuntimeError("falhou")), "whisper")
    assert voice._transcrever(_um_segundo()) == ("", None)


def test_modelo_carrega_uma_vez_so_em_segundo_plano(monkeypatch):
    criados = []
    liberar = threading.Event()
    modelo = _WhisperFalso(_segmento("que horas são"))

    def criar():
        criados.append(threading.current_thread().name)
        liberar.wait(5)  # um modelo que demora a carregar
        return modelo

    monkeypatch.setattr(voice, "_criar_whisper", criar)
    monkeypatch.setattr(voice, "_WHISPER", voice._WhisperLocal())
    monkeypatch.setattr(voice, "TRANSCRICAO_VOZ", "whisper")
    inicio = time.perf_counter()
    voice.preparar_transcricao()
    voice.preparar_transcricao()
    assert time.perf_counter() - inicio < 0.5  # a abertura não espera o modelo
    threading.Timer(0.2, liberar.set).start()
    assert voice._transcrever(_um_segundo())[0] == "que horas são"  # a frase esperou o modelo
    assert criados == ["carregar-whisper"]


def test_modo_google_nem_carrega_o_whisper(monkeypatch):
    monkeypatch.setattr(voice, "_WHISPER", voice._WhisperLocal())
    monkeypatch.setattr(voice, "TRANSCRICAO_VOZ", "google")
    monkeypatch.setattr(voice, "_transcrever_google", lambda audio: ("oi", 0.9))
    voice.preparar_transcricao()
    assert voice._transcrever(_um_segundo()) == ("oi", 0.9)
    assert not voice._WHISPER._iniciado


def test_escuta_termina_sem_erro_quando_o_programa_fecha(monkeypatch, caplog):
    # Ao fechar, o Python encerra o executor da transcrição enquanto a escuta (daemon) ainda
    # roda; antes, cada frase nova dava "cannot schedule new futures after shutdown".
    encerrado = ThreadPoolExecutor(max_workers=1)
    encerrado.shutdown()
    monkeypatch.setattr(voice, "_RECONHECIMENTO", encerrado)
    monkeypatch.setattr(voice, "_PARAR_ESCUTA", threading.Event())

    class MicrofoneFalso:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(voice.sr, "Microphone", lambda device_index=None: MicrofoneFalso())
    monkeypatch.setattr(voice.sr.Recognizer, "listen", lambda self, fonte, timeout=None, phrase_time_limit=None: _um_segundo())
    monkeypatch.setattr(voice.sr.Recognizer, "adjust_for_ambient_noise", lambda self, fonte, duration=1: None)
    monkeypatch.setattr(voice, "microfone_disponivel", lambda: True)

    escuta = threading.Thread(target=voice._laco_escuta, args=(lambda *args: None, lambda: True), daemon=True)
    with caplog.at_level(logging.ERROR, logger="voice"):
        escuta.start()
        escuta.join(3)
    voice._PARAR_ESCUTA.set()  # se ainda estiver rodando, para
    assert not escuta.is_alive()
    assert not [registro.getMessage() for registro in caplog.records if registro.levelno >= logging.ERROR]


def test_voz_do_celular_so_vai_para_o_whisper_local(monkeypatch):
    # A voz gravada no app do celular nunca vai para o Google, nem no modo "google" nem enquanto o modelo carrega.
    _google_proibido(monkeypatch)
    um_segundo = np.zeros(16000, dtype=np.float32)
    modelo = _WhisperFalso(_segmento("abre a pasta da o a b", avg_logprob=-0.1))
    for modo in ("auto", "google"):
        _whisper_carregado(monkeypatch, modelo, modo)
        texto, confianca = voice.transcrever_amostras(um_segundo)
        assert "OAB" in texto and confianca == pytest.approx(math.exp(-0.1)), modo
    assert modelo.chamadas[0][0] is um_segundo  # as amostras vão direto, sem conversão
    monkeypatch.setattr(voice, "ESPERA_WHISPER_SEGUNDOS", 0.05)
    _whisper_carregando(monkeypatch, "auto")
    assert voice.transcrever_amostras(um_segundo) == ("", None)
