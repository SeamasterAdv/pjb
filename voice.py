# voice.py
import os
import asyncio
import queue
import threading
import re
import logging
import tempfile

import pygame
import edge_tts
import speech_recognition as sr

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
VOZ_BEATRIZ = "pt-BR-ThalitaNeural"
VELOCIDADE_VOZ = "+15%"

async def gerar_audio(texto, arquivo):
    if not texto:
        return
    communicate = edge_tts.Communicate(texto, VOZ_BEATRIZ, rate=VELOCIDADE_VOZ)
    await communicate.save(arquivo)


def _reproduzir_bloco(bloco: str):
    """Reproduz um único bloco de fala em áudio, sem bloquear a continuação da resposta."""
    arquivo_audio = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as arquivo_temporario:
            arquivo_audio = arquivo_temporario.name

        asyncio.run(gerar_audio(bloco, arquivo_audio))
        pygame.mixer.music.load(arquivo_audio)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
        pygame.mixer.music.unload()
    except Exception as exc:
        logger.error("Erro na fala: %s", exc)
    finally:
        if arquivo_audio:
            try:
                if os.path.exists(arquivo_audio):
                    os.remove(arquivo_audio)
            except OSError as exc:
                logger.debug("Não foi possível remover o áudio temporário: %s", exc)


def _quebrar_texto_para_fala(texto: str, max_chars: int = 110) -> list[str]:
    texto = re.sub(r"[^\wÀ-ÿ\s]", " ", texto or "", flags=re.UNICODE)
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


def falar(texto, exibir=True):
    """Fala em blocos curtos e mais rápidos para reduzir a latência de respostas longas."""
    if not texto:
        return
    if exibir:
        print(f"\nBeatriz: {texto}")
    if not AUDIO_DISPONIVEL:
        return

    blocos = _quebrar_texto_para_fala(texto, max_chars=110)
    if not blocos:
        return

    primeiro_bloco = blocos[0]
    blocos_restantes = blocos[1:]

    def continuar_fala():
        for bloco in blocos_restantes:
            with _LOCK_AUDIO:
                _reproduzir_bloco(bloco)

    with _LOCK_AUDIO:
        _reproduzir_bloco(primeiro_bloco)

    if blocos_restantes:
        threading.Thread(target=continuar_fala, daemon=True).start()

def falar_async(texto):
    """Exibe o texto imediatamente e fala em paralelo, sem bloquear a resposta."""
    if not texto:
        return
    print(f"\nBeatriz: {texto}")
    threading.Thread(target=falar, args=(texto,), kwargs={"exibir": False}, daemon=True).start()

def falar_resposta_em_stream(resposta_stream):
    """Fala frases conforme vão chegando do stream."""
    fila_falas = queue.Queue()
    resposta_completa = []

    def reproduzir_falas():
        while True:
            frase = fila_falas.get()
            if frase is None:
                fila_falas.task_done()
                break
            with _LOCK_AUDIO:
                _reproduzir_bloco(frase)
            fila_falas.task_done()

    thread_voz = threading.Thread(target=reproduzir_falas, daemon=True)
    thread_voz.start()
    buffer = ""
    primeiro_bloco_enviado = False

    for trecho in resposta_stream:
        texto = getattr(trecho, "text", "") or ""
        if not texto:
            continue
        resposta_completa.append(texto)
        buffer += texto

        if not primeiro_bloco_enviado and len(buffer.split()) >= 10:
            palavras = buffer.split()
            fila_falas.put(" ".join(palavras[:10]))
            buffer = " ".join(palavras[10:])
            primeiro_bloco_enviado = True

        partes = re.split(r"(?<=[.!?])\s+", buffer)
        buffer = partes.pop()

        for parte in partes:
            parte = parte.replace("*", "").replace("#", "").strip()
            if parte:
                fila_falas.put(parte)

    buffer = buffer.replace("*", "").replace("#", "").strip()
    if buffer:
        fila_falas.put(buffer)

    fila_falas.put(None)
    fila_falas.join()
    thread_voz.join()
    return "".join(resposta_completa)

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
    """Confirma a entrada selecionada ou detecta automaticamente uma entrada válida."""
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


def ouvir():
    """Captura áudio do microfone e retorna texto. Se não houver microfone, retorna vazio."""
    global _MICROFONE_INDICE, _MICROFONE_NOME
    if not _MICROFONE_ATIVO:
        return ""
    if not microfone_disponivel():
        logger.warning("Nenhum microfone detectado no sistema.")
        return ""

    r = sr.Recognizer()
    r.pause_threshold = 2.2
    r.non_speaking_duration = 0.5
    r.phrase_threshold = 0.2
    try:
        with sr.Microphone(device_index=_MICROFONE_INDICE) as source:
            print(f"\n[Ouvindo pelo microfone: {_MICROFONE_NOME}]")
            r.adjust_for_ambient_noise(source, duration=0.3)
            audio = r.listen(source, timeout=1, phrase_time_limit=45)
            texto = r.recognize_google(audio, language="pt-BR")
            print(f"Você: {texto}")
            return texto
    except OSError as e:
        logger.warning("Microfone indisponível: %s", e)
        if not _MICROFONE_MANUAL:
            _MICROFONE_INDICE = None
            _MICROFONE_NOME = ""
        return ""
    except sr.WaitTimeoutError:
        return ""
    except sr.UnknownValueError:
        return ""
    except Exception as e:
        logger.error(f"Erro no reconhecimento: {e}")
        return ""