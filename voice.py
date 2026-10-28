# voice.py
import os
import asyncio
import json
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

from concurrent.futures import ThreadPoolExecutor

import biometria_voz
import niveis_voz
import siglas
from config import (CAMINHO_VOZ_ESCOLHIDA, WHISPER_THREADS, LIMITE_FRASE_SEGUNDOS, PAUSA_FIM_DE_FRASE, TOM_VOZ,
                    TRANSCRICAO_VOZ, VELOCIDADE_VOZ, VOZ_BEATRIZ, WHISPER_MODELO_VOZ)

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

# Quem desenha a voz (o painel): funções (quem, nivel, db, bandas), com quem = "beatriz" (o áudio que
# está tocando) ou "voce" (o microfone). Sem ninguém aqui, nada é medido.
_OUVINTES_DE_NIVEL = []


def ao_medir_nivel(funcao):
    """Passa a chamar `funcao(quem, nivel, db, bandas)` a cada medida da voz (veja niveis_voz)."""
    if funcao not in _OUVINTES_DE_NIVEL:
        _OUVINTES_DE_NIVEL.append(funcao)


def _publicar_nivel(quem: str, medida):
    for funcao in list(_OUVINTES_DE_NIVEL):
        try:
            funcao(quem, *medida)
        except Exception as exc:
            logger.debug("Nível da voz não publicado: %s", exc)

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


# ------------------------------------------------------------------ vozes
# A voz da Siri é da Apple e não existe fora dos aparelhos dela. A "siri" daqui é a voz online
# que mais se parece com o jeito dela (calma e neutra): o usuário a escolheu ouvindo as amostras
# de documentos_gerados/amostras_voz (5_siri_francisca_neutra.mp3).
PERFIS_DE_VOZ = {
    "thalita": {"nome": "da Thalita", "voz": VOZ_BEATRIZ, "velocidade": VELOCIDADE_VOZ, "tom": TOM_VOZ},
    "siri": {"nome": "parecida com a da Siri", "voz": "pt-BR-FranciscaNeural", "velocidade": "+5%", "tom": "+0Hz"},
}
PERFIL_PADRAO = "thalita"
_VOZ_ESCOLHIDA = {"perfil": PERFIL_PADRAO, "modificado": None}  # "modificado": quando o arquivo da escolha mudou
_TEXTOS_PREPARADOS = set()  # as frases de preparar_frases, refeitas na voz nova quando ela muda


def perfil_de_voz() -> str:
    """O perfil de voz em uso. Relê o arquivo da escolha quando ele muda, porque a troca pode ter
    sido feita no outro processo (o painel fala no dele)."""
    try:
        modificado = os.path.getmtime(CAMINHO_VOZ_ESCOLHIDA)
    except OSError:
        modificado = None
    if modificado != _VOZ_ESCOLHIDA["modificado"]:
        perfil = PERFIL_PADRAO
        if modificado is not None:
            try:
                with open(CAMINHO_VOZ_ESCOLHIDA, encoding="utf-8") as arquivo:
                    perfil = json.load(arquivo).get("perfil", PERFIL_PADRAO)
            except (OSError, ValueError, AttributeError) as exc:
                logger.warning("Não consegui ler a voz escolhida: %s", exc)
        _aplicar_perfil(perfil, modificado)
    return _VOZ_ESCOLHIDA["perfil"]


def _aplicar_perfil(perfil: str, modificado):
    anterior = _VOZ_ESCOLHIDA["perfil"]
    _VOZ_ESCOLHIDA.update(perfil=perfil if perfil in PERFIS_DE_VOZ else PERFIL_PADRAO, modificado=modificado)
    if _VOZ_ESCOLHIDA["perfil"] != anterior:
        preparar_frases(list(_TEXTOS_PREPARADOS))


def descricao_da_voz() -> str:
    """A voz em uso, curta, para o painel ("Thalita +15%", "Siri: Francisca +5%")."""
    perfil = perfil_de_voz()
    dados = PERFIS_DE_VOZ[perfil]
    voz = f"{dados['voz'].replace('pt-BR-', '').replace('Neural', '')} {dados['velocidade']}"
    return voz if perfil == PERFIL_PADRAO else f"{perfil.capitalize()}: {voz}"


def trocar_voz(perfil: str) -> bool:
    """Passa a falar com outro perfil de PERFIS_DE_VOZ e guarda a escolha."""
    if perfil not in PERFIS_DE_VOZ:
        return False
    try:
        os.makedirs(os.path.dirname(CAMINHO_VOZ_ESCOLHIDA), exist_ok=True)
        temporario = CAMINHO_VOZ_ESCOLHIDA + ".tmp"
        with open(temporario, "w", encoding="utf-8") as arquivo:
            json.dump({"perfil": perfil}, arquivo)
        os.replace(temporario, CAMINHO_VOZ_ESCOLHIDA)
        _aplicar_perfil(perfil, os.path.getmtime(CAMINHO_VOZ_ESCOLHIDA))
    except OSError as exc:
        logger.warning("Não consegui guardar a voz escolhida: %s", exc)
        return False
    return True


RE_VOZ = re.compile(r"\bvoz(es)?\b")
# Assuntos de outras funções: a biometria ("cadastra minha voz") e mensagens de áudio.
RE_OUTRA_VOZ = re.compile(r"\bminha voz\b|\bmensage\w* de voz\b|\baudio\b|\breconhec\w*|\bcadastr\w*|\bbiometria\b")
RE_QUAIS_VOZES = re.compile(r"\b(quais|que) (sao as )?vozes\b|\bque voz (e essa|voce (esta|ta) usando)\b"
                            r"|\bqual (e )?(a )?(sua )?voz\b")
# Com o nome da voz, qualquer um destes verbos troca ("usa a voz da Siri", "consegue falar com a voz da Siri?").
RE_TROCAR_VOZ = re.compile(r"\b(us[aeo]r?|mud\w*|troc\w*|coloc\w*|poe|bota|volt\w*|pass\w*|quero|prefiro|fal[ae]r?"
                           r"|ativ\w*|escolh\w*|consegue|pode)\b")
# Sem o nome, só um pedido claro de troca ("pode abaixar a voz" não é).
RE_TROCAR_SEM_NOME = re.compile(r"\b(troc\w*|mud\w*) (de |a |sua |essa |tua )?voz\b|\boutra voz\b")
RE_VOLTAR_A_VOZ = re.compile(r"\bvolt\w* (a |pra |para |para a |pra a )?(sua |tua )?voz\b")
RE_DEIXAR_DE_USAR = re.compile(r"\b(para|pare|parar|chega) de (usar|falar)\b|\bnao (usa|use|usar|quero)\b|\bsem a voz\b")
NOMES_DOS_PERFIS = {
    "siri": (r"siri", r"ciri"),
    "thalita": (r"thalita", r"talita", r"normal", r"de sempre", r"de antes", r"antiga", r"padrao", r"original"),
}


def _perfil_citado(c: str) -> str | None:
    """A voz citada na frase; se citar duas ("tira a da Siri e volta pra normal"), a última."""
    achados = [(achado.start(), perfil) for perfil, nomes in NOMES_DOS_PERFIS.items() for nome in nomes
               for achado in re.finditer(rf"\b{nome}\b", c)]
    return max(achados)[1] if achados else None


def responder_troca_de_voz(texto: str) -> str | None:
    """"Usa a voz da Siri", "volta pra voz da Thalita", "troca de voz", "quais vozes você tem?".

    Devolve a resposta (já falada na voz nova) ou None, se o pedido não for sobre a voz dela.
    """
    c = " ".join(_palavras(texto))
    if not RE_VOZ.search(c) or RE_OUTRA_VOZ.search(c):
        return None
    pedido = _perfil_citado(c)
    atual = perfil_de_voz()
    if RE_QUAIS_VOZES.search(c) and not pedido:
        return ("Tenho duas vozes: a da Thalita, que é a de sempre, e uma parecida com a da Siri. "
                f"Agora tô com a voz {PERFIS_DE_VOZ[atual]['nome']}. Pra trocar, é só dizer “usa a voz da Siri” "
                "ou “volta pra voz da Thalita”.")
    if pedido is None:
        if RE_VOLTAR_A_VOZ.search(c):
            pedido = PERFIL_PADRAO  # "volta a sua voz"
        elif RE_TROCAR_SEM_NOME.search(c):
            pedido = next(perfil for perfil in PERFIS_DE_VOZ if perfil != atual)  # "troca de voz": com duas, a outra
        else:
            return None
    elif not RE_TROCAR_VOZ.search(c):
        return None
    elif RE_DEIXAR_DE_USAR.search(c):
        pedido = PERFIL_PADRAO if pedido != PERFIL_PADRAO else "siri"  # "para de usar a voz da Siri"
    if pedido == atual:
        return f"Já tô com a voz {PERFIS_DE_VOZ[atual]['nome']}!"
    if not trocar_voz(pedido):
        return "Não consegui trocar a voz agora, tenta de novo daqui a pouco."
    if pedido == "siri":
        return ("Prontinho, agora tô com uma voz parecida com a da Siri! "
                "Se quiser a de antes, é só dizer “volta pra voz da Thalita”.")
    return "Voltei pra minha voz de sempre, a da Thalita! Pra trocar de novo, diga “usa a voz da Siri”."


async def gerar_audio(texto, arquivo):
    if not texto:
        return
    perfil = PERFIS_DE_VOZ[perfil_de_voz()]
    communicate = edge_tts.Communicate(texto, perfil["voz"], rate=perfil["velocidade"], pitch=perfil["tom"])
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
    """Gera o áudio com a voz online escolhida e recorre à voz do Windows se ela falhar."""
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


def gerar_arquivo_de_fala(texto: str) -> str:
    """O áudio do texto na voz escolhida (mp3; wav se cair na voz do Windows), num arquivo temporário que fica com quem
    pediu. Usado pelo app do celular para falar a resposta lá."""
    return _gerar_arquivo_fala(texto)


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
_FRASES_PRONTAS = {}  # (perfil de voz, texto) -> arquivo de áudio guardado (frases curtas e frequentes)


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
    perfil = perfil_de_voz()
    for posicao, bloco in enumerate(blocos):
        fim = evento_fim if posicao == len(blocos) - 1 else None
        pronto = _FRASES_PRONTAS.get((perfil, bloco))
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


def _envelope_da_fala(arquivo: str) -> list:
    """As medidas do trecho, para o painel acompanhar a fala (~30 ms por trecho); [] sem painel."""
    if not _OUVINTES_DE_NIVEL:
        return []
    try:
        return niveis_voz.envelope_do_arquivo(arquivo)
    except Exception as exc:
        logger.debug("Sem o envelope da fala: %s", exc)
        return []


def _tocar(arquivo: str):
    global _TOCANDO_DESDE, _FIM_ULTIMA_FALA
    envelope = _envelope_da_fala(arquivo)
    with _LOCK_AUDIO:
        _TOCANDO_DESDE = time.time()
        try:
            pygame.mixer.music.load(arquivo)
            pygame.mixer.music.play()
            relogio = pygame.time.Clock()
            while pygame.mixer.music.get_busy():
                if envelope:  # a medida do ponto em que o áudio está, para a esfera vibrar junto
                    posicao = int(pygame.mixer.music.get_pos() / 1000 / niveis_voz.PASSO_DA_FALA)
                    _publicar_nivel("beatriz", envelope[min(max(posicao, 0), len(envelope) - 1)])
                relogio.tick(30 if envelope else 20)
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
    """Gera de antemão o áudio de frases curtas e frequentes ("Pode falar."), para tocarem na hora.

    Quando a voz muda, elas são geradas de novo na voz nova (as da outra voz ficam guardadas).
    """
    perfil = perfil_de_voz()

    def gerar(frase):
        try:
            _FRASES_PRONTAS[(perfil, frase)] = _gerar_arquivo_fala(frase)
        except Exception as exc:
            logger.debug("Não foi possível preparar a frase %r: %s", frase, exc)

    for frase in frases:
        if not frase:
            continue
        _TEXTOS_PREPARADOS.add(frase)
        if (perfil, frase) not in _FRASES_PRONTAS:
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
                        cpu_threads=min(WHISPER_THREADS, os.cpu_count() or 4))


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
    """Começa a carregar o Whisper (e o modelo da voz, se há alguém cadastrado) em segundo plano."""
    if TRANSCRICAO_VOZ != "google":
        _WHISPER.preparar()
    biometria_voz.BIOMETRIA.preparar()


def _transcrever(audio):
    """(texto, confiança de 0 a 1) da frase, com as siglas no lugar ("o a b" -> "OAB"); ("", None) se não entendeu."""
    texto, confianca = _transcrever_bruto(audio)
    return siglas.corrigir_siglas(texto), confianca


def _transcrever_bruto(audio):
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


def transcrever_amostras(amostras) -> tuple[str, float | None]:
    """Transcreve um áudio já em 16 kHz mono float32 (a voz gravada no app do celular). Só pelo Whisper local, mesmo no
    modo "auto": é a sua voz de fora de casa, e não vai para o Google nem enquanto o modelo carrega."""
    if TRANSCRICAO_VOZ == "google":
        logger.info("O áudio do celular é transcrito só pelo Whisper local, mesmo com TRANSCRICAO_VOZ=google.")
    modelo = _WHISPER.obter(espera=ESPERA_WHISPER_SEGUNDOS)
    if modelo is None:
        return "", None
    texto, confianca = _whisper_nas_amostras(modelo, amostras)
    return siglas.corrigir_siglas(texto), confianca


def _transcrever_whisper(modelo, audio):
    return _whisper_nas_amostras(modelo, _audio_para_whisper(audio))


def transcrever_palavras(amostras, espera: float = 120.0) -> list:
    """[(início, fim, palavra)] do áudio (16 kHz mono float32), pelo Whisper do PC, nunca pelo Google: as legendas de "edita
    esse vídeo no meu estilo". Vazio sem o modelo. Trechos sem fala e as alucinações conhecidas ficam de fora."""
    modelo = _WHISPER.obter(espera=espera)
    if modelo is None:
        return []
    segmentos, _ = modelo.transcribe(amostras, language="pt", vad_filter=True, word_timestamps=True,
                                     condition_on_previous_text=False, hotwords=siglas.vocabulario_de_voz())
    palavras = []
    for segmento in segmentos:
        if segmento.no_speech_prob > LIMITE_SEM_FALA or _ALUCINACOES_WHISPER.search(segmento.text or ""):
            continue
        palavras.extend((p.start, p.end, siglas.corrigir_siglas(p.word.strip())) for p in segmento.words or [] if p.word.strip())
    return palavras


def _whisper_nas_amostras(modelo, amostras):
    """A confiança é a probabilidade média dos tokens (exp de avg_logprob): ~0,9 numa frase
    clara, ~0,6 numa duvidosa, a mesma escala da confiança do Google."""
    segmentos, _ = modelo.transcribe(
        amostras,
        language="pt",
        vad_filter=True,  # corta o silêncio antes, onde o Whisper mais inventa frases
        condition_on_previous_text=False,
        without_timestamps=True,
        hotwords=siglas.vocabulario_de_voz(),  # OAB, STF... (sem isto "OAB" saía "OBE")
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


class _FluxoMedido:
    """Repassa as leituras do microfone e mede o nível delas para o painel (até ~25 vezes por segundo)."""

    INTERVALO = 0.04
    AMORTECER_ECO = 0.35  # enquanto ela fala, o microfone capta a própria voz dela: a montanha quase não mexe

    def __init__(self, fluxo, taxa: int, largura: int):
        self._fluxo, self._taxa, self._largura = fluxo, taxa, largura
        self._ultima = 0.0

    def read(self, tamanho):
        dados = self._fluxo.read(tamanho)
        agora = time.monotonic()
        if _OUVINTES_DE_NIVEL and self._largura == 2 and agora - self._ultima >= self.INTERVALO:
            self._ultima = agora
            try:
                import numpy as np

                nivel, db, bandas = niveis_voz.medir(np.frombuffer(dados, dtype=np.int16) / 32768.0, self._taxa)
                if _TOCANDO_DESDE is not None:
                    nivel, bandas = nivel * self.AMORTECER_ECO, [b * self.AMORTECER_ECO for b in bandas]
                _publicar_nivel("voce", (nivel, db, bandas))
            except Exception as exc:
                logger.debug("Nível do microfone não medido: %s", exc)
        return dados

    def __getattr__(self, nome):
        return getattr(self._fluxo, nome)


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
                if getattr(fonte, "stream", None) is not None:  # mede o que passa, para a montanha do painel
                    fonte.stream = _FluxoMedido(fonte.stream, fonte.SAMPLE_RATE, fonte.SAMPLE_WIDTH)
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


_IDENTIFICACAO = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quem-fala")


def _entregar_frase(audio, inicio, fim, ao_ouvir):
    bio = biometria_voz.BIOMETRIA
    if bio.cadastrando():
        # Cadastrando a voz: a frase não é pedido, só amostra (menos o que ela mesma falou).
        if not falou_durante(inicio, fim):
            try:
                bio.receber_amostra(_audio_para_whisper(audio))
            except Exception as exc:
                logger.warning("Amostra de voz descartada: %s", exc)
        return
    # A voz é identificada ao mesmo tempo que o Whisper transcreve (o ONNX não disputa a CPU: 4 threads, e o Whisper tem 12).
    identificacao = None
    if bio.ativa:
        try:
            identificacao = _IDENTIFICACAO.submit(bio.identificar, _audio_para_whisper(audio))
        except Exception as exc:
            logger.warning("Não consegui identificar quem falou: %s", exc)
    texto, confianca = _transcrever(audio)
    if not texto:
        return
    print(f"Você: {texto}")
    falante = None
    if identificacao is not None:
        try:
            falante = identificacao.result(timeout=10)
        except Exception as exc:
            logger.warning("Não consegui identificar quem falou: %s", exc)
    try:
        if falante is None:
            ao_ouvir(texto, inicio, fim, confianca)
        else:
            ao_ouvir(texto, inicio, fim, confianca, falante=falante)
    except Exception as exc:
        logger.error("Erro ao entregar a frase ouvida: %s", exc)
