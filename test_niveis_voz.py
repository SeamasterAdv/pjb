"""Níveis da voz para o painel: medida, memória compartilhada, envelope da fala e o microfone medido."""
import math
import wave

import numpy as np

import niveis_voz
import painel_web as pw
import voice


def _seno(frequencia, amplitude=0.3, segundos=1 / 30, taxa=16000):
    t = np.arange(int(taxa * segundos)) / taxa
    return (amplitude * np.sin(2 * np.pi * frequencia * t)).astype(np.float32)


def test_medir_acha_a_faixa_do_tom_e_o_nivel():
    nivel, db, bandas = niveis_voz.medir(_seno(1000), 16000)
    assert len(bandas) == niveis_voz.BANDAS
    assert -16 < db < -9  # seno de amplitude 0,3: RMS ~0,21
    assert nivel > 0.8
    limites = niveis_voz.FREQUENCIA_MINIMA * (niveis_voz.FREQUENCIA_MAXIMA / niveis_voz.FREQUENCIA_MINIMA) ** (
        np.arange(niveis_voz.BANDAS + 1) / niveis_voz.BANDAS)
    faixa_do_tom = int(np.searchsorted(limites, 1000) - 1)
    assert int(np.argmax(bandas)) == faixa_do_tom


def test_silencio_e_pedaco_curto_valem_zero():
    nivel, db, bandas = niveis_voz.medir(np.zeros(533, dtype=np.float32), 16000)
    assert nivel == 0 and max(bandas) == 0
    nivel, db, bandas = niveis_voz.medir(np.zeros(10, dtype=np.float32), 16000)
    assert nivel == 0 and db == -math.inf and bandas == [0.0] * niveis_voz.BANDAS


def test_publicar_e_ler_pela_memoria_compartilhada(monkeypatch):
    niveis = niveis_voz.NiveisDeVoz(niveis_voz.criar_compartilhado())
    assert niveis.ler() == {"beatriz": None, "voce": None}
    niveis.publicar("voce", 0.5, -32.0, [0.1] * niveis_voz.BANDAS)
    lido = niveis.ler()
    assert lido["beatriz"] is None
    assert lido["voce"] == {"nivel": 0.5, "db": -32.0, "bandas": [0.1] * niveis_voz.BANDAS}
    niveis.publicar("beatriz", 0.7, -math.inf, [0.2] * 3)  # dB infinito e bandas a menos não quebram
    assert niveis.ler()["beatriz"]["db"] == -120.0
    # Medida velha vale silêncio (a fala acabou, o microfone pausou).
    agora = niveis_voz.time.time()
    monkeypatch.setattr(niveis_voz.time, "time", lambda: agora + niveis_voz.VALIDADE + 1)
    assert niveis.ler() == {"beatriz": None, "voce": None}


def test_envelope_do_arquivo_acompanha_o_audio(tmp_path):
    caminho = tmp_path / "fala.wav"
    taxa = 22050
    sinal = np.concatenate([_seno(300, 0.4, 0.5, taxa), np.zeros(taxa // 2, dtype=np.float32)])
    with wave.open(str(caminho), "wb") as arquivo:
        arquivo.setnchannels(1)
        arquivo.setsampwidth(2)
        arquivo.setframerate(taxa)
        arquivo.writeframes((sinal * 32767).astype(np.int16).tobytes())
    envelope = niveis_voz.envelope_do_arquivo(str(caminho))
    assert 29 <= len(envelope) <= 32  # 1 s em passos de 1/30 s
    assert envelope[5][0] > 0.6  # meio segundo de som...
    assert envelope[-3][0] == 0  # ...e meio de silêncio


class _FluxoFalso:
    def __init__(self, dados):
        self.dados = dados
        self.fechado = False

    def read(self, tamanho):
        return self.dados

    def close(self):
        self.fechado = True


def test_microfone_medido_repassa_os_dados_e_publica(monkeypatch):
    publicados = []
    monkeypatch.setattr(voice, "_OUVINTES_DE_NIVEL", [lambda quem, nivel, db, bandas: publicados.append((quem, nivel))])
    monkeypatch.setattr(voice, "_TOCANDO_DESDE", None)
    dados = (_seno(400, 0.3, 0.025, 44100) * 32767).astype(np.int16).tobytes()
    fluxo = voice._FluxoMedido(_FluxoFalso(dados), 44100, 2)
    assert fluxo.read(1024) == dados
    assert fluxo.read(1024) == dados  # logo depois: não mede de novo (no máximo ~25 vezes por segundo)
    assert len(publicados) == 1 and publicados[0][0] == "voce" and publicados[0][1] > 0.7
    fluxo.close()  # o resto passa direto para o fluxo de verdade
    assert fluxo._fluxo.fechado


def test_microfone_amortece_enquanto_ela_fala(monkeypatch):
    publicados = []
    monkeypatch.setattr(voice, "_OUVINTES_DE_NIVEL", [lambda quem, nivel, db, bandas: publicados.append(nivel)])
    dados = (_seno(400, 0.3, 0.025, 44100) * 32767).astype(np.int16).tobytes()
    monkeypatch.setattr(voice, "_TOCANDO_DESDE", None)
    voice._FluxoMedido(_FluxoFalso(dados), 44100, 2).read(1024)
    monkeypatch.setattr(voice, "_TOCANDO_DESDE", 1.0)
    voice._FluxoMedido(_FluxoFalso(dados), 44100, 2).read(1024)
    assert publicados[1] == publicados[0] * voice._FluxoMedido.AMORTECER_ECO


def test_sem_ouvintes_nada_e_medido(monkeypatch, tmp_path):
    monkeypatch.setattr(voice, "_OUVINTES_DE_NIVEL", [])
    chamadas = []
    monkeypatch.setattr(niveis_voz, "medir", lambda *a: chamadas.append(a))
    voice._FluxoMedido(_FluxoFalso(b"\0\0" * 1024), 44100, 2).read(1024)
    assert chamadas == []
    assert voice._envelope_da_fala(str(tmp_path / "nao_existe.mp3")) == []


def test_painel_le_os_niveis(monkeypatch):
    monkeypatch.setattr(voice, "_OUVINTES_DE_NIVEL", [])
    assert pw.ApiPainel(None, {}, None).niveis() == {}
    compartilhado = niveis_voz.criar_compartilhado()
    api = pw.ApiPainel(None, {}, None, compartilhado)
    # A fala do próprio painel (chat digitado) também publica na mesma memória.
    assert voice._OUVINTES_DE_NIVEL == [api._niveis.publicar]
    niveis_voz.NiveisDeVoz(compartilhado).publicar("beatriz", 0.4, -30.0, [0.3] * niveis_voz.BANDAS)
    assert api.niveis()["beatriz"]["nivel"] == 0.4
