"""Importar vídeo (memory.importar_video_transcricao) com um Whisper falso: nada é baixado.

Os vídeos são gerados na hora com o PyAV (mp4 com MPEG-4 e AAC, codificadores do próprio FFmpeg).
"""
import fractions
from types import SimpleNamespace

import av
import faster_whisper
import numpy as np
import pytest

import memory
import voice


class _WhisperFalso:
    """Imita o WhisperModel do faster-whisper: devolve os textos dados e guarda cada chamada."""

    def __init__(self, *textos):
        self.textos = textos
        self.chamadas = []

    def transcribe(self, audio, **opcoes):
        self.chamadas.append((audio, opcoes))
        segmentos = [SimpleNamespace(text=texto) for texto in self.textos]
        return iter(segmentos), SimpleNamespace(language=opcoes.get("language"))


def _gravar_video(caminho, segundos=2, com_audio=True, taxa=44100):
    """Um mp4 pequeno: quadros pretos e, se pedido, um tom de 440 Hz em estéreo a 44,1 kHz."""
    with av.open(str(caminho), "w") as saida:
        video = saida.add_stream("mpeg4", rate=10)
        video.width, video.height, video.pix_fmt = 160, 120, "yuv420p"
        audio = saida.add_stream("aac", rate=taxa) if com_audio else None
        if audio:
            audio.layout = "stereo"
        for _ in range(segundos * 10):
            quadro = av.VideoFrame.from_ndarray(np.zeros((120, 160, 3), np.uint8), format="rgb24")
            for pacote in video.encode(quadro):
                saida.mux(pacote)
        if audio:
            tempo = np.arange(taxa * segundos) / taxa
            tom = (0.3 * np.sin(2 * np.pi * 440 * tempo)).astype(np.float32)
            estereo = np.stack([tom, tom])
            for inicio in range(0, estereo.shape[1], 1024):
                quadro = av.AudioFrame.from_ndarray(
                    np.ascontiguousarray(estereo[:, inicio:inicio + 1024]), format="fltp", layout="stereo")
                quadro.sample_rate, quadro.pts, quadro.time_base = taxa, inicio, fractions.Fraction(1, taxa)
                for pacote in audio.encode(quadro):
                    saida.mux(pacote)
            for pacote in audio.encode(None):
                saida.mux(pacote)
        for pacote in video.encode(None):
            saida.mux(pacote)
    return str(caminho)


@pytest.fixture
def vault(monkeypatch, tmp_path):
    """A Vault numa pasta temporária, sem indexar a biblioteca de verdade."""
    indexacoes = []
    monkeypatch.setattr(memory, "_resolver_pasta_obsidian", lambda pasta=None: str(tmp_path / "vault" / pasta))
    monkeypatch.setattr(memory, "indexar_pasta_documentos", lambda *a, **k: indexacoes.append(True))
    monkeypatch.setattr(memory, "_WHISPER_VIDEO", None)
    return SimpleNamespace(pasta=tmp_path / "vault", indexacoes=indexacoes)


def _whisper_da_voz(monkeypatch, modelo, nome="small"):
    estado = voice._WhisperLocal()
    estado.modelo = modelo
    estado._iniciado = True
    estado.pronto.set()
    monkeypatch.setattr(voice, "_WHISPER", estado)
    monkeypatch.setattr(voice, "WHISPER_MODELO_VOZ", nome)
    monkeypatch.setenv("WHISPER_MODEL", nome)


def _proibir_novo_whisper(monkeypatch):
    def proibido(*args, **kwargs):
        raise AssertionError("não podia carregar outro WhisperModel")

    monkeypatch.setattr(faster_whisper, "WhisperModel", proibido)


def test_video_e_transcrito_com_o_whisper_da_voz(monkeypatch, tmp_path, vault):
    modelo = _WhisperFalso(" Bom dia. ", "  ", "Esta é a reunião de orçamento.")
    _whisper_da_voz(monkeypatch, modelo)
    _proibir_novo_whisper(monkeypatch)
    caminho = _gravar_video(tmp_path / "reuniao.mp4", segundos=2)

    resposta = memory.importar_video_transcricao(caminho)

    assert resposta.startswith("Vídeo transcrito e indexado"), resposta
    # O áudio chega decodificado (16 kHz, mono, float32), não o caminho do arquivo: assim o
    # faster_whisper.decode_audio, que quebra com o PyAV 19, não é usado.
    (audio, opcoes), = modelo.chamadas
    assert isinstance(audio, np.ndarray) and audio.dtype == np.float32 and audio.ndim == 1
    assert abs(len(audio) - 2 * 16000) < 1600
    assert 0.1 < float(np.abs(audio).max()) <= 1.0
    assert opcoes["language"] == "pt"
    nota = (vault.pasta / "referencias" / "reuniao.md").read_text(encoding="utf-8")
    assert "idioma: pt" in nota
    assert nota.rstrip().endswith("Bom dia.\nEsta é a reunião de orçamento.")
    assert vault.indexacoes == [True]


def test_video_sem_audio_avisa_sem_transcrever(monkeypatch, tmp_path, vault):
    modelo = _WhisperFalso("não devia aparecer")
    _whisper_da_voz(monkeypatch, modelo)
    caminho = _gravar_video(tmp_path / "mudo.mp4", com_audio=False)

    resposta = memory.importar_video_transcricao(caminho)

    assert resposta == "Erro ao transcrever vídeo: o arquivo não tem trilha de áudio."
    assert modelo.chamadas == []
    assert vault.indexacoes == []


def test_modelo_diferente_do_da_voz_carrega_uma_vez_so(monkeypatch, tmp_path, vault):
    _whisper_da_voz(monkeypatch, _WhisperFalso("da voz"), nome="small")
    monkeypatch.setenv("WHISPER_MODEL", "medium")
    criados = []

    def criar(nome, **opcoes):
        criados.append((nome, opcoes))
        return _WhisperFalso("do vídeo")

    monkeypatch.setattr(faster_whisper, "WhisperModel", criar)
    primeiro = _gravar_video(tmp_path / "um.mp4", segundos=1)
    segundo = _gravar_video(tmp_path / "dois.mp4", segundos=1)

    assert memory.importar_video_transcricao(primeiro).startswith("Vídeo transcrito")
    assert memory.importar_video_transcricao(segundo).startswith("Vídeo transcrito")

    assert criados == [("medium", {"device": "cpu", "compute_type": "int8"})]
    assert "do vídeo" in (vault.pasta / "referencias" / "dois.md").read_text(encoding="utf-8")


def test_voz_sem_whisper_carregado_cai_num_modelo_proprio(monkeypatch, tmp_path, vault):
    estado = voice._WhisperLocal()
    estado._iniciado = True
    estado.pronto.set()  # terminou de carregar, mas falhou: sem modelo
    monkeypatch.setattr(voice, "_WHISPER", estado)
    monkeypatch.setattr(voice, "WHISPER_MODELO_VOZ", "small")
    monkeypatch.setenv("WHISPER_MODEL", "small")
    monkeypatch.setattr(faster_whisper, "WhisperModel", lambda nome, **opcoes: _WhisperFalso("reserva"))

    resposta = memory.importar_video_transcricao(_gravar_video(tmp_path / "aula.mp4", segundos=1))

    assert resposta.startswith("Vídeo transcrito"), resposta
    assert "reserva" in (vault.pasta / "referencias" / "aula.md").read_text(encoding="utf-8")
