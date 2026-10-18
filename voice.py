# voice.py
import os
import asyncio
import queue
import threading
import re
import logging
import math
import subprocess
import tempfile
import time
import unicodedata
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor

import pygame
import edge_tts
import speech_recognition as sr

from config import (LIMITE_FRASE_SEGUNDOS, PAUSA_FIM_DE_FRASE, TOM_VOZ, TRANSCRICAO_VOZ, VELOCIDADE_VOZ,
                    VOZ_BEATRIZ, WHISPER_MODELO_VOZ)

logger = logging.getLogger(__name__)

AUDIO_DISPONIVEL = False
try:
    pygame.mixer.init()
    AUDIO_DISPONIVEL = True
except Exception as e:
    logger.warning("Áudio indisponível no sistema: %s", e)
    AUDIO_DISPONIVEL = False

_MICROFONE_INDICE = None
_MICROFONE_NOME = ""
_MICROFONE_MANUAL = False
_MICROFONE_ATIVO = True
_SAIDA_AUDIO_NOME = ""
_LOCK_AUDIO = threading.Lock()

# Se a voz online falhar (ex.: o serviço da Microsoft recusar com 403), usa a voz
# offline do Windows por um tempo antes de tentar a online de novo.
PAUSA_VOZ_ONLINE_SEGUNDOS = 600
_VOZ_ONLINE_FALHOU_EM = 0.0

_SCRIPT_VOZ_WINDOWS = (
    "Add-Type -AssemblyName System.Speech;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "$v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'pt-BR' } | Select-Object -First 1;"
    "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) };"
    "$s.Rate = 1;"
    "$s.SetOutputToWaveFile($env:BEATRIZ_ARQUIVO_FALA);"
    "$s.Speak($env:BEATRIZ_TEXTO_FALA);"
    "$s.Dispose()"
)


async def gerar_audio(texto, arquivo):
    if not texto:
        return
    communicate = edge_tts.Communicate(texto, VOZ_BEATRIZ, rate=VELOCIDADE_VOZ, pitch=TOM_VOZ)
    await communicate.save(arquivo)


def _gerar_audio_windows(texto: str, arquivo: str):
    """Voz offline do Windows (SAPI, ex.: Microsoft Maria). O texto vai por variável de ambiente."""
    ambiente = dict(os.environ, BEATRIZ_TEXTO_FALA=texto, BEATRIZ_ARQUIVO_FALA=arquivo)
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT_VOZ_WINDOWS],
        env=ambiente,
        check=True,
        capture_output=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _gerar_arquivo_fala(texto: str) -> str:
    """Gera o áudio com a voz online (Thalita) e recorre à voz do Windows se ela falhar."""
    global _VOZ_ONLINE_FALHOU_EM
    if time.time() - _VOZ_ONLINE_FALHOU_EM > PAUSA_VOZ_ONLINE_SEGUNDOS:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as arquivo_temporario:
            arquivo = arquivo_temporario.name
        try:
            asyncio.run(gerar_audio(texto, arquivo))
            if os.path.getsize(arquivo) > 0:
                return arquivo
            raise RuntimeError("áudio vazio")
        except Exception as exc:
            _VOZ_ONLINE_FALHOU_EM = time.time()
            logger.warning("Voz online indisponível (%s); usando a voz do Windows.", str(exc)[:120])
            _remover_arquivo(arquivo)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as arquivo_temporario:
        arquivo = arquivo_temporario.name
    try:
        _gerar_audio_windows(texto, arquivo)
    except Exception:
        _remover_arquivo(arquivo)
        raise
    return arquivo


def _remover_arquivo(caminho):
    try:
        if caminho and os.path.exists(caminho):
            os.remove(caminho)
    except OSError as exc:
        logger.debug("Não foi possível remover o áudio temporário: %s", exc)


def _quebrar_texto_para_fala(texto: str, max_chars: int = 110) -> list[str]:
    # Mantém a pontuação: ela dá as pausas da voz e separa as frases nos blocos.
    texto = re.sub(r"[^\wÀ-ÿ\s.,!?;:%$-]", " ", texto or "", flags=re.UNICODE)
    texto = re.sub(r"\s+", " ", texto).strip()
    if not texto:
        return []
    palavras = texto.split()
    if len(palavras) <= 12:
        return [texto]

    primeiro_bloco = " ".join(palavras[:10])
    texto_restante = " ".join(palavras[10:])
    partes = []
    buffer = ""
    for frase in re.split(r"(?<=[.!?])\s+", texto_restante):
        frase = frase.strip()
        if not frase:
            continue
        if not buffer:
            buffer = frase
            continue
        if len(buffer) + 1 + len(frase) <= max_chars:
            buffer = f"{buffer} {frase}"
        else:
            partes.append(buffer)
            buffer = frase

    if buffer:
        partes.append(buffer)
    return [primeiro_bloco, *partes]


# ------------------------------------------------------------------ fala em fila
# A voz online leva ~2 s para gerar cada trecho, seja qual for o tamanho (quase tudo é
# abrir a conexão). Os trechos são gerados em paralelo e tocados em ordem, então o
# próximo já está pronto quando o atual termina e a fala sai sem pausas no meio.
_SINTESE = ThreadPoolExecutor(max_workers=3, thread_name_prefix="sintese-voz")
_FILA_FALA = queue.Queue()  # (geração, futuro com o arquivo, apagar depois?, evento de fim ou None)
_LOCK_FILA = threading.Lock()
_REPRODUTOR = None
_GERACAO = 0  # parar_fala() avança a geração, e o que ainda estava na fila é descartado
_PENDENTES = 0  # trechos na fila ou tocando
_TOCANDO_DESDE = None  # início do trecho que está tocando agora
_INTERVALOS_FALA = deque(maxlen=50)  # (início, fim) dos trechos já tocados
_FIM_ULTIMA_FALA = 0.0
_TEXTOS_FALADOS = deque(maxlen=20)  # (quando, texto) para reconhecer o eco da própria voz
_FRASES_PRONTAS = {}  # texto -> arquivo de áudio guardado (frases curtas e frequentes)


def _arquivo_pronto(caminho: str) -> Future:
    futuro = Future()
    futuro.set_result(caminho)
    return futuro


def _enfileirar(blocos, evento_fim=None):
    """Começa a gerar os trechos agora e os põe na fila para tocar em ordem."""
    global _PENDENTES, _REPRODUTOR
    with _LOCK_FILA:
        geracao = _GERACAO
        _PENDENTES += len(blocos)
        if _REPRODUTOR is None or not _REPRODUTOR.is_alive():
            _REPRODUTOR = threading.Thread(target=_reproduzir_fila, name="reprodutor-voz", daemon=True)
            _REPRODUTOR.start()
    for posicao, bloco in enumerate(blocos):
        fim = evento_fim if posicao == len(blocos) - 1 else None
        pronto = _FRASES_PRONTAS.get(bloco)
        if pronto and os.path.exists(pronto):
            _FILA_FALA.put((geracao, _arquivo_pronto(pronto), False, bloco, fim))
        else:
            _FILA_FALA.put((geracao, _SINTESE.submit(_gerar_arquivo_fala, bloco), True, bloco, fim))


def _reproduzir_fila():
    global _PENDENTES
    while True:
        geracao, futuro, apagar, texto, evento_fim = _FILA_FALA.get()
        try:
            if geracao != _GERACAO:
                # Fala interrompida: não espera a geração terminar, só apaga o arquivo depois.
                if apagar:
                    futuro.add_done_callback(lambda f: None if f.exception() else _remover_arquivo(f.result()))
                continue
            arquivo = futuro.result()
            try:
                if geracao == _GERACAO:
                    _TEXTOS_FALADOS.append((time.time(), texto))
                    _tocar(arquivo)
            finally:
                if apagar:
                    _remover_arquivo(arquivo)
        except Exception as exc:
            logger.error("Erro na fala: %s", str(exc)[:200])
        finally:
            with _LOCK_FILA:
                _PENDENTES -= 1
            if evento_fim is not None:
                evento_fim.set()


def _tocar(arquivo: str):
    global _TOCANDO_DESDE, _FIM_ULTIMA_FALA
    with _LOCK_AUDIO:
        _TOCANDO_DESDE = time.time()
        try:
            pygame.mixer.music.load(arquivo)
            pygame.mixer.music.play()
            relogio = pygame.time.Clock()
            while pygame.mixer.music.get_busy():
                relogio.tick(20)
            pygame.mixer.music.unload()
        finally:
            fim = time.time()
            _INTERVALOS_FALA.append((_TOCANDO_DESDE, fim))
            _TOCANDO_DESDE = None
            _FIM_ULTIMA_FALA = fim


def parar_fala():
    """Interrompe a fala atual e descarta os trechos que ainda não tocaram."""
    global _GERACAO
    with _LOCK_FILA:
        _GERACAO += 1
    try:
        pygame.mixer.music.stop()
    except pygame.error:
        pass


def esta_falando() -> bool:
    return _PENDENTES > 0


def fim_da_ultima_fala() -> float:
    """Quando (time.time()) terminou o último trecho falado; 0 se ela ainda não falou."""
    return _FIM_ULTIMA_FALA


def falou_durante(inicio: float, fim: float, folga: float = 0.4) -> bool:
    """Se a Beatriz falava entre `inicio` e `fim`: o microfone pode ter captado a própria voz.

    A folga cobre o eco da sala logo depois de cada trecho.
    """
    tocando_desde = _TOCANDO_DESDE
    if tocando_desde is not None and tocando_desde <= fim:
        return True
    return any(a <= fim and b + folga >= inicio for a, b in list(_INTERVALOS_FALA))


def _palavras(texto: str) -> list[str]:
    sem_acento = unicodedata.normalize("NFKD", (texto or "").lower())
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.findall(r"\w+", sem_acento)


def parece_eco(texto: str, segundos: float = 60.0) -> bool:
    """Se a frase ouvida é, na maior parte, algo que a própria Beatriz acabou de falar."""
    ouvidas = _palavras(texto)
    if not ouvidas:
        return False
    limite = time.time() - segundos
    faladas = set()
    for quando, falado in list(_TEXTOS_FALADOS):
        if quando >= limite:
            faladas.update(_palavras(falado))
    if not faladas:
        return False
    return sum(palavra in faladas for palavra in ouvidas) / len(ouvidas) >= 0.7


def preparar_frases(frases):
    """Gera de antemão o áudio de frases curtas e frequentes ("Pode falar."), para tocarem na hora."""
    def gerar(frase):
        try:
            _FRASES_PRONTAS[frase] = _gerar_arquivo_fala(frase)
        except Exception as exc:
            logger.debug("Não foi possível preparar a frase %r: %s", frase, exc)

    for frase in frases:
        if frase and frase not in _FRASES_PRONTAS:
            _SINTESE.submit(gerar, frase)


def falar(texto, exibir=True):
    """Fala o texto e espera terminar."""
    if not texto:
        return
    if exibir:
        print(f"\nBeatriz: {texto}")
    if not AUDIO_DISPONIVEL:
        return
    blocos = _quebrar_texto_para_fala(texto, max_chars=110)
    if not blocos:
        return
    terminou = threading.Event()
    _enfileirar(blocos, terminou)
    terminou.wait()


def falar_async(texto):
    """Exibe o texto imediatamente e fala sem bloquear: a geração da voz começa aqui mesmo."""
    if not texto:
        return
    print(f"\nBeatriz: {texto}")
    if not AUDIO_DISPONIVEL:
        return
    blocos = _quebrar_texto_para_fala(texto, max_chars=110)
    if blocos:
        _enfileirar(blocos)


def falar_resposta_em_stream(resposta_stream, limpar=None):
    """Fala cada frase assim que ela chega do LLM e devolve o texto completo.

    `resposta_stream` traz pedaços de texto (ou objetos com `.text`); `limpar` prepara
    cada frase para a voz. Não espera a fala terminar.
    """
    resposta_completa = []
    buffer = ""
    primeira = True

    def soltar(frase):
        frase = limpar(frase) if limpar else frase.replace("*", "").replace("#", "")
        frase = frase.strip()
        if frase and AUDIO_DISPONIVEL:
            _enfileirar([frase])

    for trecho in resposta_stream:
        texto = trecho if isinstance(trecho, str) else (getattr(trecho, "text", "") or "")
        if not texto:
            continue
        resposta_completa.append(texto)
        buffer += texto
        *frases, buffer = re.split(r"(?<=[.!?])\s+", buffer)
        if primeira and not frases:
            # O primeiro trecho sai antes de a frase acabar: trecho curto fica pronto mais
            # rápido, e o resto é gerado enquanto ele toca.
            corte = _corte_do_primeiro_trecho(buffer)
            if corte:
                frases.append(buffer[:corte])
                buffer = buffer[corte:].lstrip()
        primeira = primeira and not frases
        if len(buffer) > 180:  # frase comprida sem ponto: corta na última vírgula
            corte = buffer.rfind(", ", 0, 180)
            if corte > 40:
                frases.append(buffer[:corte + 1])
                buffer = buffer[corte + 2:]
        for frase in frases:
            soltar(frase)
    soltar(buffer)
    return "".join(resposta_completa)


def _corte_do_primeiro_trecho(buffer: str) -> int:
    """Onde cortar o começo da resposta: na primeira vírgula depois de 5 palavras ou na 10ª palavra."""
    palavras = list(re.finditer(r"\S+", buffer))
    for numero, palavra in enumerate(palavras[:-1], start=1):  # a última pode estar pela metade
        if numero >= 5 and palavra.group().endswith(","):
            return palavra.end()
        if numero == 10:
            return palavra.end()
    return 0


# ------------------------------------------------------------------ microfone
_CACHE_MICROFONE = {"ate": 0.0, "resultado": False}
VALIDADE_CACHE_MICROFONE = 10.0  # cada detecção abre o PyAudio e lista todos os dispositivos


def _invalidar_cache_microfone():
    _CACHE_MICROFONE["ate"] = 0.0


def listar_microfones():
    """Lista os dispositivos de entrada reconhecidos pelo PyAudio."""
    try:
        import pyaudio

        audio = pyaudio.PyAudio()
        try:
            dispositivos = []
            for indice in range(audio.get_device_count()):
                try:
                    dispositivo = audio.get_device_info_by_index(indice)
                except Exception:
                    continue
                if int(dispositivo.get("maxInputChannels", 0)) <= 0:
                    continue
                dispositivos.append({
                    "indice": int(dispositivo.get("index", indice)),
                    "nome": str(dispositivo.get("name", "Dispositivo de áudio")),
                })
            return dispositivos
        finally:
            audio.terminate()
    except Exception as exc:
        logger.warning("Não foi possível detectar um microfone de entrada: %s", exc)
        return []


def selecionar_microfone(indice):
    """Seleciona uma entrada específica ou retorna ao dispositivo automático."""
    global _MICROFONE_INDICE, _MICROFONE_NOME, _MICROFONE_MANUAL
    _invalidar_cache_microfone()
    if indice is None:
        _MICROFONE_INDICE = None
        _MICROFONE_NOME = ""
        _MICROFONE_MANUAL = False
        return True

    dispositivo = next(
        (item for item in listar_microfones() if item["indice"] == int(indice)),
        None,
    )
    if dispositivo is None:
        return False
    _MICROFONE_INDICE = dispositivo["indice"]
    _MICROFONE_NOME = dispositivo["nome"]
    _MICROFONE_MANUAL = True
    return True


def microfone_selecionado():
    return _MICROFONE_INDICE if _MICROFONE_MANUAL else None


def listar_saidas_audio():
    """Lista as saídas reconhecidas pelo SDL usado pelo pygame."""
    try:
        from pygame._sdl2.audio import get_audio_device_names
        return get_audio_device_names(False)
    except Exception as exc:
        logger.warning("Não foi possível listar as saídas de áudio: %s", exc)
        return []


def saida_audio_selecionada():
    return _SAIDA_AUDIO_NOME


def selecionar_saida_audio(nome):
    """Troca a saída do mixer do pygame para o dispositivo escolhido."""
    global AUDIO_DISPONIVEL, _SAIDA_AUDIO_NOME
    nome = str(nome or "")
    if nome and nome not in listar_saidas_audio():
        return False

    with _LOCK_AUDIO:
        try:
            pygame.mixer.quit()
            if nome:
                pygame.mixer.init(devicename=nome)
            else:
                pygame.mixer.init()
            AUDIO_DISPONIVEL = True
            _SAIDA_AUDIO_NOME = nome
            return True
        except Exception as exc:
            logger.warning("Não foi possível selecionar a saída de áudio %s: %s", nome, exc)
            try:
                pygame.mixer.init()
                AUDIO_DISPONIVEL = True
            except Exception:
                AUDIO_DISPONIVEL = False
            return False


def microfone_disponivel():
    """Confirma a entrada selecionada ou detecta automaticamente uma entrada válida.

    O resultado vale por alguns segundos; trocar de microfone invalida na hora.
    """
    agora = time.monotonic()
    if agora < _CACHE_MICROFONE["ate"]:
        return _CACHE_MICROFONE["resultado"]
    resultado = _detectar_microfone()
    _CACHE_MICROFONE.update(ate=agora + VALIDADE_CACHE_MICROFONE, resultado=resultado)
    return resultado


def _detectar_microfone():
    global _MICROFONE_INDICE, _MICROFONE_NOME
    dispositivos = listar_microfones()
    if _MICROFONE_MANUAL:
        selecionado = next(
            (item for item in dispositivos if item["indice"] == _MICROFONE_INDICE),
            None,
        )
        if selecionado is None:
            return False
        _MICROFONE_NOME = selecionado["nome"]
        return True

    if not dispositivos:
        _MICROFONE_INDICE = None
        _MICROFONE_NOME = ""
        return False

    try:
        import pyaudio
        audio = pyaudio.PyAudio()
        try:
            indice_padrao = int(audio.get_default_input_device_info().get("index", -1))
        except Exception:
            indice_padrao = None
        finally:
            audio.terminate()
    except Exception:
        indice_padrao = None

    dispositivos.sort(key=lambda item: (
        0 if item["indice"] == indice_padrao else 1,
        0 if any(token in item["nome"].lower() for token in ["microfone", "mic", "audio", "input", "usb", "webcam"]) else 1,
        item["indice"],
    ))
    _MICROFONE_INDICE = dispositivos[0]["indice"]
    _MICROFONE_NOME = dispositivos[0]["nome"]
    return True


def microfone_ativo():
    """Informa se a captura de voz está habilitada pelo usuário."""
    return _MICROFONE_ATIVO


def definir_microfone_ativo(ativo: bool):
    """Habilita ou pausa a captura de voz sem perder o dispositivo selecionado."""
    global _MICROFONE_ATIVO
    _MICROFONE_ATIVO = bool(ativo)


def _microfone_falhou():
    global _MICROFONE_INDICE, _MICROFONE_NOME
    _invalidar_cache_microfone()
    if not _MICROFONE_MANUAL:
        _MICROFONE_INDICE = None
        _MICROFONE_NOME = ""


# ------------------------------------------------------------------ escuta
def _novo_reconhecedor():
    r = sr.Recognizer()
    r.pause_threshold = PAUSA_FIM_DE_FRASE  # silêncio que encerra a frase
    r.non_speaking_duration = min(0.4, PAUSA_FIM_DE_FRASE)
    r.phrase_threshold = 0.2
    r.dynamic_energy_threshold = True
    return r


# ------------------------------------------------------------------ transcrição
# A frase é transcrita aqui mesmo, pelo Whisper (faster-whisper na CPU), e o áudio da casa
# não sai do computador. O Google (API gratuita e não oficial) fica de reserva no modo
# "auto". Medido num i7-12700K com o modelo small: ~1,1 s por frase, e o Google ~1 s.
LIMITE_SEM_FALA = 0.6  # no_speech_prob acima disto: o Whisper provavelmente inventou a frase
ESPERA_WHISPER_SEGUNDOS = 30  # no modo "whisper", quanto uma frase espera o modelo carregar
# Frases que o Whisper costuma "ouvir" em ruído ou silêncio (vêm das legendas do treino).
_ALUCINACOES_WHISPER = re.compile(
    r"amara\.org|legendas? pela comunidade|inscreva-se no canal|obrigad[oa] por assistir|adriana zanotto",
    re.IGNORECASE,
)


def _criar_whisper():
    from faster_whisper import WhisperModel

    # Mais de 8 threads quase não acelera (8: 1,12 s por frase; 4: 1,20 s).
    return WhisperModel(WHISPER_MODELO_VOZ, device="cpu", compute_type="int8",
                        cpu_threads=min(8, os.cpu_count() or 4))


class _WhisperLocal:
    """Carrega o modelo uma vez só, em segundo plano, para a abertura não esperar (~2 s; na
    primeira vez, o download de ~480 MB do small)."""

    def __init__(self):
        self.modelo = None
        self.pronto = threading.Event()  # terminou de carregar, com ou sem sucesso
        self._iniciado = False
        self._lock = threading.Lock()

    def preparar(self):
        with self._lock:
            if self._iniciado:
                return
            self._iniciado = True
        threading.Thread(target=self._carregar, name="carregar-whisper", daemon=True).start()

    def _carregar(self):
        inicio = time.perf_counter()
        try:
            self.modelo = _criar_whisper()
            logger.info("Transcrição local pronta (Whisper %s, %.1f s).", WHISPER_MODELO_VOZ,
                        time.perf_counter() - inicio)
        except Exception as exc:
            reserva = "usando o Google" if TRANSCRICAO_VOZ == "auto" else "a voz não será transcrita"
            logger.warning("Whisper indisponível (%s); %s.", str(exc)[:200], reserva)
        finally:
            self.pronto.set()

    def obter(self, espera: float = 0.0):
        """O modelo, se já carregou (espera até `espera` segundos); None se não carregou."""
        self.preparar()
        if espera:
            self.pronto.wait(espera)
        return self.modelo


_WHISPER = _WhisperLocal()


def preparar_transcricao():
    """Começa a carregar o Whisper em segundo plano, se a transcrição local estiver em uso."""
    if TRANSCRICAO_VOZ != "google":
        _WHISPER.preparar()


def _transcrever(audio):
    """(texto, confiança de 0 a 1) da frase; ("", None) se não deu para entender.

    Usa o Whisper local ou o Google, conforme TRANSCRICAO_VOZ. No modo "auto", o Google só
    entra enquanto o modelo carrega ou se o Whisper falhar; silêncio ou ruído não vão para ele.
    """
    if TRANSCRICAO_VOZ == "google":
        return _transcrever_google(audio)
    modelo = _WHISPER.obter(espera=ESPERA_WHISPER_SEGUNDOS if TRANSCRICAO_VOZ == "whisper" else 0)
    if modelo is not None:
        try:
            return _transcrever_whisper(modelo, audio)
        except Exception as exc:
            logger.error("Erro na transcrição local: %s", exc)
    if TRANSCRICAO_VOZ == "auto":
        return _transcrever_google(audio)
    return "", None


def _audio_para_whisper(audio):
    """O áudio do microfone em 16 kHz mono float32, o formato que o faster-whisper aceita (sem arquivo)."""
    import numpy as np

    bruto = audio.get_raw_data(convert_rate=16000, convert_width=2)
    return np.frombuffer(bruto, dtype=np.int16).astype(np.float32) / 32768.0


def _transcrever_whisper(modelo, audio):
    """A confiança é a probabilidade média dos tokens (exp de avg_logprob): ~0,9 numa frase
    clara, ~0,6 numa duvidosa, a mesma escala da confiança do Google."""
    segmentos, _ = modelo.transcribe(
        _audio_para_whisper(audio),
        language="pt",
        vad_filter=True,  # corta o silêncio antes, onde o Whisper mais inventa frases
        condition_on_previous_text=False,
        without_timestamps=True,
    )
    textos, logprobs = [], []
    for segmento in segmentos:  # o gerador só transcreve ao ser percorrido
        texto = segmento.text.strip()
        if not texto or segmento.no_speech_prob > LIMITE_SEM_FALA or _ALUCINACOES_WHISPER.search(texto):
            continue
        textos.append(texto)
        logprobs.append(segmento.avg_logprob)
    if not textos:
        return "", None
    # Média ponderada pelo tamanho de cada trecho: um trecho curto e incerto pesa menos.
    pesos = [len(texto) for texto in textos]
    media = sum(peso * logprob for peso, logprob in zip(pesos, logprobs)) / sum(pesos)
    return " ".join(textos), math.exp(media)


_TRANSCRITOR = sr.Recognizer()
_TRANSCRITOR.operation_timeout = 8  # sem isto, uma rede lenta prende a transcrição


def _transcrever_google(audio):
    """(texto, confiança) da frase pelo Google, em pt-BR; ("", None) se não deu para entender.

    A confiança (0 a 1) é None quando o Google não a informa (a biblioteca inventaria 0,5).
    """
    try:
        bruto = _TRANSCRITOR.recognize_google(audio, language="pt-BR", show_all=True)
    except sr.UnknownValueError:
        return "", None
    except sr.RequestError as exc:
        logger.warning("Reconhecimento de voz indisponível: %s", exc)
        return "", None
    except Exception as exc:
        logger.error("Erro no reconhecimento: %s", exc)
        return "", None
    alternativas = bruto.get("alternative") if isinstance(bruto, dict) else None
    if not alternativas:
        return "", None
    melhor = alternativas[0]
    return (melhor.get("transcript") or "").strip(), melhor.get("confidence")


def ouvir():
    """Captura uma frase do microfone e retorna o texto. Se não houver microfone, retorna vazio."""
    if not _MICROFONE_ATIVO:
        return ""
    if not microfone_disponivel():
        logger.warning("Nenhum microfone detectado no sistema.")
        return ""

    r = _novo_reconhecedor()
    try:
        with sr.Microphone(device_index=_MICROFONE_INDICE) as source:
            print(f"\n[Ouvindo pelo microfone: {_MICROFONE_NOME}]")
            r.adjust_for_ambient_noise(source, duration=0.3)
            audio = r.listen(source, timeout=5, phrase_time_limit=LIMITE_FRASE_SEGUNDOS)
    except OSError as e:
        logger.warning("Microfone indisponível: %s", e)
        _microfone_falhou()
        return ""
    except sr.WaitTimeoutError:
        return ""
    texto, _ = _transcrever(audio)
    if texto:
        print(f"Você: {texto}")
    return texto


# O microfone fica aberto o tempo todo numa thread: abrir e fechar a cada frase perdia o
# começo da fala e recalibrava o ruído toda vez. A transcrição roda em outra thread, para
# a escuta não parar enquanto a frase é transcrita.
_ESCUTA = None
_PARAR_ESCUTA = threading.Event()
_RECONHECIMENTO = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reconhecimento-voz")


def iniciar_escuta_continua(ao_ouvir, pode_escutar=None):
    """Escuta em segundo plano e chama `ao_ouvir(texto, inicio, fim, confianca)` a cada frase entendida.

    `inicio` e `fim` (time.time()) delimitam a frase, para quem recebe saber se ela
    coincidiu com a fala da Beatriz; `confianca` vem da transcrição (ou None). Quando
    `pode_escutar()` devolve False, a escuta pausa.
    """
    global _ESCUTA
    if escuta_continua_ativa():
        return
    preparar_transcricao()
    _PARAR_ESCUTA.clear()
    _ESCUTA = threading.Thread(
        target=_laco_escuta, args=(ao_ouvir, pode_escutar or (lambda: True)), name="escuta-voz", daemon=True
    )
    _ESCUTA.start()


def parar_escuta_continua():
    _PARAR_ESCUTA.set()


def escuta_continua_ativa() -> bool:
    return _ESCUTA is not None and _ESCUTA.is_alive()


def _laco_escuta(ao_ouvir, pode_escutar):
    reconhecedor = _novo_reconhecedor()
    sem_microfone_avisado = False

    def liberada():
        return not _PARAR_ESCUTA.is_set() and _MICROFONE_ATIVO and pode_escutar()

    while not _PARAR_ESCUTA.is_set():
        if not liberada():
            time.sleep(0.3)
            continue
        if not microfone_disponivel():
            if not sem_microfone_avisado:  # avisa uma vez, não a cada tentativa
                logger.warning("Nenhum microfone detectado no sistema; vou tentar de novo em segundo plano.")
                sem_microfone_avisado = True
            time.sleep(3)
            continue
        sem_microfone_avisado = False
        indice = _MICROFONE_INDICE
        try:
            with sr.Microphone(device_index=indice) as fonte:
                logger.info("Escutando pelo microfone: %s", _MICROFONE_NOME)
                reconhecedor.adjust_for_ambient_noise(fonte, duration=0.5)
                while liberada() and indice == _MICROFONE_INDICE:
                    try:
                        audio = reconhecedor.listen(fonte, timeout=1, phrase_time_limit=LIMITE_FRASE_SEGUNDOS)
                    except sr.WaitTimeoutError:
                        continue
                    fim = time.time()
                    duracao = len(audio.frame_data) / (audio.sample_rate * audio.sample_width)
                    try:
                        _RECONHECIMENTO.submit(_entregar_frase, audio, fim - duracao, fim, ao_ouvir)
                    except RuntimeError:
                        # O programa está fechando: o Python já encerrou o executor, e esta
                        # thread (daemon) ainda escutava. Não há mais quem transcreva.
                        logger.debug("Escuta encerrada junto com o programa.")
                        return
        except OSError as exc:
            logger.warning("Microfone indisponível: %s", exc)
            _microfone_falhou()
            time.sleep(1)
        except Exception as exc:
            logger.error("Erro na escuta: %s", exc)
            time.sleep(1)


def _entregar_frase(audio, inicio, fim, ao_ouvir):
    texto, confianca = _transcrever(audio)
    if not texto:
        return
    print(f"Você: {texto}")
    try:
        ao_ouvir(texto, inicio, fim, confianca)
    except Exception as exc:
        logger.error("Erro ao entregar a frase ouvida: %s", exc)
