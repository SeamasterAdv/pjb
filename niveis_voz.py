"""Níveis da voz para o painel: a da Beatriz (o áudio que está tocando) e a do microfone.

O voice.py mede e publica aqui (nível de 0 a 1, dB e BANDAS faixas de 90 Hz a 5 kHz, do grave ao
agudo); o painel, em outro processo, lê pela memória compartilhada (multiprocessing.Array) umas 20
vezes por segundo, para a esfera vibrar com a fala dela e a montanha com a sua voz. Só números: nada
do áudio é gravado nem sai do computador.
"""
import math
import multiprocessing
import time

import numpy as np

BANDAS = 16
FREQUENCIA_MINIMA, FREQUENCIA_MAXIMA = 90.0, 5000.0
VALIDADE = 0.35  # segundos: uma medida mais velha que isso vale silêncio
PASSO_DA_FALA = 1 / 30  # segundos entre as medidas do áudio dela (o envelope do arquivo)
QUEM = ("beatriz", "voce")
_TAMANHO_BLOCO = 3 + BANDAS  # quando, nível, dB e as bandas


def _nivel_do_db(db: float) -> float:
    # De -52 dB (silêncio de sala) a -12 dB (voz forte perto do microfone), a mesma escala do laboratório.
    return min(1.0, max(0.0, (db + 52.0) / 40.0))


def medir(amostras, taxa: int) -> tuple[float, float, list[float]]:
    """Nível (0 a 1), dB e as BANDAS faixas (0 a 1) de um pedaço de áudio em float (-1 a 1)."""
    x = np.asarray(amostras, dtype=np.float32).reshape(-1)
    if x.size < 32:
        return 0.0, -math.inf, [0.0] * BANDAS
    db = 20.0 * math.log10(float(np.sqrt(np.mean(x * x))) + 1e-9)
    # Espectro com janela de Hann, normalizado para um seno de amplitude A dar A no pico.
    espectro = np.abs(np.fft.rfft(x * np.hanning(x.size))) / (x.size / 4.0)
    frequencias = np.fft.rfftfreq(x.size, 1.0 / taxa)
    limites = FREQUENCIA_MINIMA * (FREQUENCIA_MAXIMA / FREQUENCIA_MINIMA) ** (np.arange(BANDAS + 1) / BANDAS)
    bandas = []
    for i in range(BANDAS):
        dentro = (frequencias >= limites[i]) & (frequencias < limites[i + 1])
        # Pedaço curto: a faixa grave pode não ter nenhuma frequência; vale a mais próxima.
        valor = float(espectro[dentro].mean()) if dentro.any() else float(espectro[np.argmin(np.abs(frequencias - limites[i]))])
        db_banda = 20.0 * math.log10(valor + 1e-9)
        bandas.append(min(1.0, max(0.0, (db_banda + 78.0) / 56.0)) ** 1.6)
    return _nivel_do_db(db), db, bandas


def envelope_do_arquivo(caminho: str, passo: float = PASSO_DA_FALA) -> list[tuple[float, float, list[float]]]:
    """As medidas de um arquivo de áudio (o mp3 da fala) a cada `passo` segundos, para tocar junto com ele."""
    import av

    taxa = 16000
    reamostrador = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=taxa)
    partes = []
    with av.open(caminho) as entrada:
        for quadro in entrada.decode(audio=0):
            partes += [q.to_ndarray().reshape(-1) for q in reamostrador.resample(quadro)]
    partes += [q.to_ndarray().reshape(-1) for q in reamostrador.resample(None)]
    if not partes:
        return []
    x = np.concatenate(partes)
    tamanho = max(32, int(taxa * passo))
    return [medir(x[i:i + tamanho], taxa) for i in range(0, x.size, tamanho)]


def criar_compartilhado():
    """A memória que o processo principal passa ao painel (um bloco para cada voz)."""
    return multiprocessing.Array("d", len(QUEM) * _TAMANHO_BLOCO)


class NiveisDeVoz:
    """Escreve e lê as últimas medidas de cada voz na memória compartilhada."""

    def __init__(self, compartilhado):
        self._dados = compartilhado

    def publicar(self, quem: str, nivel: float, db: float, bandas) -> None:
        inicio = QUEM.index(quem) * _TAMANHO_BLOCO
        valores = [time.time(), float(nivel), float(db) if math.isfinite(db) else -120.0]
        valores += [float(b) for b in list(bandas)[:BANDAS]]
        valores += [0.0] * (_TAMANHO_BLOCO - len(valores))
        with self._dados.get_lock():
            self._dados[inicio:inicio + _TAMANHO_BLOCO] = valores

    def ler(self) -> dict:
        """{"beatriz": {...} ou None, "voce": {...} ou None}; None quando não há medida recente."""
        with self._dados.get_lock():
            copia = self._dados[:]
        agora, saida = time.time(), {}
        for posicao, quem in enumerate(QUEM):
            bloco = copia[posicao * _TAMANHO_BLOCO:(posicao + 1) * _TAMANHO_BLOCO]
            if agora - bloco[0] > VALIDADE:
                saida[quem] = None
            else:
                saida[quem] = {"nivel": round(bloco[1], 3), "db": round(bloco[2], 1),
                               "bandas": [round(b, 3) for b in bloco[3:]]}
        return saida
