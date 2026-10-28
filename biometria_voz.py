"""Biometria vocal: reconhece quem está falando pelo timbre da voz, tudo no computador.

- `CodificadorEcapa`: o modelo ECAPA-TDNN da SpeechBrain transforma ~1 s ou mais de fala num vetor de 192 números
  (a "impressão" da voz). Fica em `CAMINHO_MODELO_VOZ` (baixa uma vez). Roda pelo onnxruntime, com o front-end em NumPy
  (sem PyTorch no programa: ele custava ~420 MB de RAM); o ONNX é gerado uma vez por `exportar_onnx` (que usa o PyTorch,
  num processo à parte). Sem o ONNX e sem como gerá-lo, cai no caminho antigo, pelo PyTorch.
- `Biometria`: guarda os perfis em `CAMINHO_VOZES` (só os vetores; o áudio nunca é gravado), compara cada frase
  por cosseno e devolve um `Falante`: "dono", "conhecido" (outra voz cadastrada), "convidado" ou "incerto".
- `Cadastro`: junta a fala de quem está cadastrando a voz (ou a de alguém de casa).
- `AgenteBiometria`: os comandos falados ("cadastra minha voz", "quem está falando?", "esquece a voz da Maria").

Sem nenhum dono cadastrado nada muda: todo mundo é tratado como o dono. A decisão do que cada tipo pode fazer
fica no `main.py` (convidados só conversam, veem a hora, o tempo e os filmes). Importante: é conveniência e
privacidade, não um cofre. Uma gravação da sua voz pode enganar o reconhecimento.
"""
import json
import logging
import os
import re
import threading
import time
import unicodedata
import warnings
from dataclasses import dataclass, replace

from config import (BIOMETRIA_APRENDER, BIOMETRIA_APRENDER_SEGUNDOS, BIOMETRIA_CADASTRO_SEGUNDOS,
                    BIOMETRIA_LIMIAR_CONVIDADO, BIOMETRIA_LIMIAR_DONO,
                    BIOMETRIA_MANTER_SEGUNDOS, BIOMETRIA_MINIMO_SEGUNDOS, BIOMETRIA_VOZ, CAMINHO_MODELO_VOZ,
                    CAMINHO_VOZES)

logger = logging.getLogger(__name__)

TAXA = 16000  # o áudio chega em 16 kHz mono float32 (voice._audio_para_whisper)
MINIMO_AMOSTRA_SEGUNDOS = 1.5  # frases mais curtas não entram no cadastro
CONSISTENCIA_MINIMA = 0.35  # semelhança média com as outras amostras; abaixo disso a amostra sai do cadastro
PRAZO_SEM_FALA_SEGUNDOS = 60.0
# Cadastrar de novo soma as amostras às que já havia (até este número; depois saem as mais antigas). Medido com o ECAPA
# e vozes sintéticas em sala com eco e ruído: um segundo cadastro, de comandos curtos, levou a nota mediana dos
# comandos do dono de 0,54 para 0,62 (reconhecidos a 0,50: 7 de 12 -> 10 de 12). Antes, cada cadastro apagava o anterior.
MAX_AMOSTRAS_PERFIL = 60
# As frases aprendidas com a confirmação (`Biometria.identificar`) ficam à parte das do cadastro, até este número (saem as
# mais antigas), para nunca empurrarem o cadastro para fora: em out/2026 o dono caía em "incerto" (0,38 a 0,48) em
# quase metade das frases, com um perfil de 4 amostras. Um cadastro novo recomeça a lista.
MAX_APRENDIDAS = 20


@dataclass(frozen=True)
class Falante:
    """Quem falou. `tipo`: "dono", "conhecido", "convidado" ou "incerto"; `nota` é a semelhança com o melhor perfil."""
    tipo: str
    nome: str = ""
    nota: float | None = None
    herdado: bool = False  # frase curta ou duvidosa: vale quem falava há pouco
    provavel: bool = False  # voz incerta que chamou "Beatriz": atendida como o dono, menos nos pedidos sensíveis

    @property
    def e_dono(self) -> bool:
        return self.tipo == "dono"


def _vetor_unitario(vetor):
    import numpy as np

    vetor = np.asarray(vetor, dtype=np.float32)
    norma = float(np.linalg.norm(vetor))
    return vetor / norma if norma else vetor


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


# ------------------------------------------------------------------ modelo
ARQUIVO_ONNX = "ecapa.onnx"
ARQUIVO_FRONTEND = "ecapa_frontend.npz"
THREADS_DA_VOZ = 4  # a identificação não pode tomar os núcleos do Whisper


def caracteristicas(audio, janela, mel):
    """O front-end do ECAPA (Fbank da SpeechBrain) em NumPy: janela de Hamming de 400 amostras, passo de 160, espectro de
    potência, 80 filtros mel, escala em dB com corte de 80 dB e média tirada por frase. Devolve (1, quadros, 80)."""
    import numpy as np

    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    n_fft, passo = len(janela), 160
    audio = np.pad(audio, (n_fft // 2, n_fft // 2))  # center=True, completando com zeros
    quadros = 1 + (len(audio) - n_fft) // passo
    indices = np.arange(n_fft)[None, :] + passo * np.arange(quadros)[:, None]
    espectro = np.abs(np.fft.rfft(audio[indices] * janela[None, :], n=n_fft, axis=1)) ** 2
    energia = espectro @ mel
    db = 10.0 * np.log10(np.maximum(energia, 1e-10))
    db = np.maximum(db, db.max() - 80.0)
    return (db - db.mean(axis=0, keepdims=True)).astype(np.float32)[None]


def exportar_onnx(pasta: str = CAMINHO_MODELO_VOZ) -> bool:
    """Gera `ecapa.onnx` (a rede) e `ecapa_frontend.npz` (janela e filtros mel) a partir do modelo da SpeechBrain.
    Usa o PyTorch: rode num processo à parte (`python biometria_voz.py --exportar`). True se deu certo."""
    import numpy as np
    import torch
    from speechbrain.inference.speaker import EncoderClassifier
    from speechbrain.utils.fetching import LocalStrategy

    modelo = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb", savedir=pasta,
                                            run_opts={"device": "cpu"}, local_strategy=LocalStrategy.COPY)
    modelo.eval()
    fbank = modelo.mods.compute_features
    janela = fbank.compute_STFT.window.detach().numpy().astype(np.float32)
    filtros = fbank.compute_fbanks
    guardado = filtros.log_mel
    filtros.log_mel = False  # a matriz sai de graça passando uma identidade pelo filtro
    with torch.inference_mode():
        mel = filtros(torch.eye(len(janela) // 2 + 1)[None])[0].numpy().astype(np.float32)
    filtros.log_mel = guardado

    class Rede(torch.nn.Module):
        def __init__(self, rede):
            super().__init__()
            self.rede = rede

        def forward(self, caracteristicas_):
            return self.rede(caracteristicas_)

    amostra = torch.from_numpy(caracteristicas(np.random.randn(48000).astype(np.float32) * 0.1, janela, mel))
    torch.onnx.export(Rede(modelo.mods.embedding_model).eval(), (amostra,), os.path.join(pasta, ARQUIVO_ONNX),
                      input_names=["caracteristicas"], output_names=["vetor"], dynamic_axes={"caracteristicas": {1: "quadros"}},
                      opset_version=17, dynamo=False)
    np.savez(os.path.join(pasta, ARQUIVO_FRONTEND), janela=janela, mel=mel)

    # confere contra o caminho original (PyTorch) em áudios de 1, 3 e 5 s
    import onnxruntime as ort

    sessao = ort.InferenceSession(os.path.join(pasta, ARQUIVO_ONNX), providers=["CPUExecutionProvider"])
    for segundos in (1, 3, 5):
        som = (np.random.randn(16000 * segundos) * 0.1).astype(np.float32)
        with torch.inference_mode():
            referencia = modelo.encode_batch(torch.from_numpy(som)[None]).squeeze().numpy()
        obtido = sessao.run(None, {"caracteristicas": caracteristicas(som, janela, mel)})[0].reshape(-1)
        cosseno = float(referencia @ obtido / (np.linalg.norm(referencia) * np.linalg.norm(obtido)))
        if cosseno < 0.999:
            logger.error("O ECAPA em ONNX não bate com o original (cosseno %.4f em %d s); apagando.", cosseno, segundos)
            for arquivo in (ARQUIVO_ONNX, ARQUIVO_FRONTEND):
                try:
                    os.remove(os.path.join(pasta, arquivo))
                except OSError:
                    pass
            return False
    return True


class CodificadorEcapa:
    """Carrega o ECAPA-TDNN em segundo plano na primeira necessidade e devolve o vetor de uma frase."""

    def __init__(self, pasta: str = CAMINHO_MODELO_VOZ):
        self.pasta = pasta
        self.modelo = None  # caminho antigo, pelo PyTorch
        self.sessao = None  # caminho novo, pelo onnxruntime
        self.erro = ""
        self.pronto = threading.Event()
        self._iniciado = False
        self._lock = threading.Lock()  # carga e uso entram um de cada vez

    def preparar(self):
        with self._lock:
            if self._iniciado:
                return
            self._iniciado = True
        threading.Thread(target=self._carregar, name="biometria-voz", daemon=True).start()

    def _carregar_onnx(self) -> bool:
        import numpy as np
        import onnxruntime as ort

        onnx, frontend = os.path.join(self.pasta, ARQUIVO_ONNX), os.path.join(self.pasta, ARQUIVO_FRONTEND)
        if not (os.path.exists(onnx) and os.path.exists(frontend)):
            if not self._gerar_onnx():
                return False
        opcoes = ort.SessionOptions()
        opcoes.intra_op_num_threads, opcoes.inter_op_num_threads = THREADS_DA_VOZ, 1
        opcoes.enable_cpu_mem_arena = False  # devolve a memória a cada frase em vez de guardar um reservatório
        self.sessao = ort.InferenceSession(onnx, opcoes, providers=["CPUExecutionProvider"])
        dados = np.load(frontend)
        self._janela, self._mel = dados["janela"], dados["mel"]
        return True

    def _gerar_onnx(self) -> bool:
        """Cria o ONNX num processo à parte (o PyTorch fica fora deste, e a memória volta ao terminar)."""
        import subprocess
        import sys

        try:
            os.makedirs(self.pasta, exist_ok=True)
            resultado = subprocess.run([sys.executable, os.path.abspath(__file__), "--exportar", self.pasta],
                                       capture_output=True, timeout=600)
            return resultado.returncode == 0
        except Exception as exc:
            logger.warning("Não consegui gerar o ECAPA em ONNX: %s", exc)
            return False

    def _carregar(self):
        with self._lock:
            inicio = time.perf_counter()
            try:
                os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
                try:
                    if self._carregar_onnx():
                        logger.info("Biometria vocal pronta (ECAPA-TDNN em ONNX, %.1f s).", time.perf_counter() - inicio)
                        return
                except Exception as exc:
                    logger.warning("ECAPA em ONNX indisponível (%s); usando o PyTorch.", str(exc)[:160])
                self._carregar_torch()
                logger.info("Biometria vocal pronta (ECAPA-TDNN no PyTorch, %.1f s).", time.perf_counter() - inicio)
            except Exception as exc:
                self.erro = str(exc)[:200]
                logger.warning("Biometria vocal indisponível (%s); todos serão tratados como o dono.", self.erro)
            finally:
                self.pronto.set()

    def _carregar_torch(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from speechbrain.inference.speaker import EncoderClassifier
            from speechbrain.utils.fetching import LocalStrategy
            import torch

            torch.set_num_threads(THREADS_DA_VOZ)
            os.makedirs(self.pasta, exist_ok=True)
            self.modelo = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb", savedir=self.pasta,
                run_opts={"device": "cpu"}, local_strategy=LocalStrategy.COPY,
            )

    def vetor(self, audio, espera: float = 0.0):
        """Vetor unitário da frase (numpy float32 em 16 kHz); None se o modelo não carregou."""
        self.preparar()
        if espera:
            self.pronto.wait(espera)
        if not self.pronto.is_set():
            return None
        with self._lock:
            if self.sessao is not None:
                saida = self.sessao.run(None, {"caracteristicas": caracteristicas(audio, self._janela, self._mel)})[0]
                return _vetor_unitario(saida.reshape(-1))
            if self.modelo is None:
                return None
            import torch

            with torch.inference_mode():
                saida = self.modelo.encode_batch(torch.from_numpy(audio).float().unsqueeze(0))
            return _vetor_unitario(saida.squeeze().cpu().numpy())


# ------------------------------------------------------------------ perfis
class Biometria:
    """Perfis de voz salvos em disco e a identificação de cada frase."""

    def __init__(self, caminho: str = CAMINHO_VOZES, codificador=None, habilitada: bool = BIOMETRIA_VOZ):
        self.caminho = caminho
        self.codificador = codificador or CodificadorEcapa()
        self.habilitada = habilitada
        self.cadastro = Cadastro(self)
        self._lock = threading.RLock()
        self._perfis: dict[str, dict] = {}
        self._ligada = True  # "desliga o reconhecimento de voz" (fica salvo)
        self._ultimo: tuple[Falante, float] | None = None
        self._incerta: tuple[object, float] | None = None  # (vetor, quando) da última frase incerta, para aprender
        self.aprender = BIOMETRIA_APRENDER
        self._carregar_perfis()

    # -- disco
    def _carregar_perfis(self):
        try:
            with open(self.caminho, encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
            self._perfis = dict(dados.get("perfis") or {})
            self._ligada = bool(dados.get("ligada", True))
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("Não consegui ler os perfis de voz (%s); começando sem nenhum.", exc)

    def _salvar(self):
        os.makedirs(os.path.dirname(self.caminho) or ".", exist_ok=True)
        temporario = self.caminho + ".tmp"
        with open(temporario, "w", encoding="utf-8") as arquivo:
            json.dump({"versao": 1, "modelo": "ecapa-voxceleb", "ligada": self._ligada, "perfis": self._perfis},
                      arquivo, ensure_ascii=False)
        os.replace(temporario, self.caminho)

    # -- estado
    def tem_dono(self) -> bool:
        return any(p.get("papel") == "dono" for p in self._perfis.values())

    def tem_perfil(self, chave: str) -> bool:
        return chave in self._perfis

    @property
    def ativa(self) -> bool:
        """Só vale quando há um dono cadastrado e o recurso não foi desligado."""
        return self.habilitada and self._ligada and self.tem_dono()

    def ligar(self, ligada: bool):
        with self._lock:
            self._ligada = ligada
            self._salvar()

    def perfis(self) -> list[tuple[str, str]]:
        """[(nome, papel)], o dono primeiro."""
        with self._lock:
            itens = [(p.get("nome") or chave, p.get("papel", "conhecido")) for chave, p in self._perfis.items()]
        return sorted(itens, key=lambda item: item[1] != "dono")

    def nome_do_dono(self) -> str:
        return next((p.get("nome", "") for p in self._perfis.values() if p.get("papel") == "dono"), "")

    def preparar(self):
        """Carrega o modelo em segundo plano, se já há alguém cadastrado."""
        if self.habilitada and self._perfis:
            self.codificador.preparar()

    def guardar_perfil(self, chave: str, nome: str, papel: str, vetor, segundos: float, amostras: int,
                       amostras_vetores=None):
        with self._lock:
            if papel == "dono":  # há um dono só: o perfil novo fica no lugar de qualquer outro dono
                self._perfis = {k: p for k, p in self._perfis.items() if p.get("papel") != "dono"}
            self._perfis[chave] = {"nome": nome, "papel": papel, "vetor": [float(x) for x in _vetor_unitario(vetor)],
                                   "segundos": round(segundos, 1), "amostras": amostras,
                                   "criado": time.strftime("%Y-%m-%d %H:%M")}
            if amostras_vetores is not None:
                self._perfis[chave]["amostras_vetores"] = [[round(float(x), 5) for x in v] for v in amostras_vetores]
            self._salvar()

    def _amostras(self, chave: str) -> list:
        """As amostras guardadas do perfil. Os perfis antigos só tinham a média: ela conta pelo número de amostras."""
        import numpy as np

        perfil = self._perfis.get(chave)
        if not perfil:
            return []
        if perfil.get("amostras_vetores"):
            return [np.asarray(v, dtype=np.float32) for v in perfil["amostras_vetores"]]
        return [np.asarray(perfil["vetor"], dtype=np.float32)] * max(1, int(perfil.get("amostras") or 1))

    def juntar_ao_perfil(self, chave: str, nome: str, papel: str, vetores, segundos: float) -> tuple[str, int]:
        """Guarda as amostras de um cadastro. Se a pessoa já tinha perfil, soma ("somou"); se a voz nova não se parece
        com a guardada (outra pessoa, ou outro microfone), começa de novo ("trocou"); sem perfil, "novo".
        Devolve (o que aconteceu, quantas amostras o perfil tem agora)."""
        import numpy as np

        with self._lock:
            antigas = self._amostras(chave) if chave in self._perfis and self._perfis[chave].get("papel") == papel else []
            situacao = "novo"
            if antigas:
                parecido = float(_vetor_unitario(np.mean(antigas, axis=0)) @ _vetor_unitario(np.mean(vetores, axis=0)))
                situacao = "somou" if parecido >= CONSISTENCIA_MINIMA else "trocou"
            if situacao != "somou":
                antigas = []
            todas = (antigas + list(vetores))[-MAX_AMOSTRAS_PERFIL:]
            self._incerta = None
            if situacao == "somou":
                segundos += float(self._perfis[chave].get("segundos") or 0)
            self.guardar_perfil(chave, nome, papel, np.mean(todas, axis=0), segundos, len(todas), todas)
            self._ultimo = None
        return situacao, len(todas)

    def remover(self, chave: str) -> bool:
        with self._lock:
            removido = self._perfis.pop(chave, None) is not None
            if removido:
                self._salvar()
                self._ultimo = None
            return removido

    def chave_de(self, nome: str) -> str | None:
        """A chave do perfil pelo nome falado ("maria" -> "maria"); "dono" para o dono."""
        alvo = _sem_acento(nome).strip()
        with self._lock:
            for chave, perfil in self._perfis.items():
                if alvo in {chave, _sem_acento(perfil.get("nome", ""))}:
                    return chave
        return None

    # -- identificação
    def identificar(self, audio) -> Falante | None:
        """Quem falou nesta frase (numpy float32, 16 kHz). None se a biometria não está em uso ou falhou."""
        if not self.ativa or audio is None:
            return None
        if len(audio) / TAXA < BIOMETRIA_MINIMO_SEGUNDOS:
            return Falante("incerto")
        try:
            vetor = self.codificador.vetor(audio)
        except Exception as exc:
            logger.warning("A biometria vocal falhou nesta frase: %s", exc)
            return None
        if vetor is None:  # modelo ainda carregando ou ausente: não trava ninguém
            return None
        melhor_nome, melhor_papel, melhor_nota = "", "", -1.0
        with self._lock:
            for chave, perfil in self._perfis.items():
                nota = float(_vetor_unitario(perfil["vetor"]) @ vetor)
                if nota > melhor_nota:
                    melhor_nome, melhor_papel, melhor_nota = perfil.get("nome") or chave, perfil.get("papel"), nota
        nota = round(melhor_nota, 2)
        if melhor_nota >= BIOMETRIA_LIMIAR_DONO:
            falante = Falante("dono" if melhor_papel == "dono" else "conhecido", melhor_nome, nota)
        elif melhor_nota < BIOMETRIA_LIMIAR_CONVIDADO:
            falante = Falante("convidado", "", nota)
        else:
            falante = Falante("incerto", "", nota)
        logger.info("Voz identificada: %s %s (nota %.2f)", falante.tipo, falante.nome, nota)
        self._aprender_com_a_confirmacao(falante, vetor)
        return falante

    def _aprender_com_a_confirmacao(self, falante: Falante, vetor, agora: float | None = None):
        """Guarda a frase incerta; se a próxima frase identificada é o dono com certeza, logo depois, a incerta era ele
        (dita mais longe do microfone, mais baixo...) e entra no perfil. Outra voz no meio desfaz."""
        agora = time.time() if agora is None else agora
        if not self.aprender:
            return
        if falante.tipo == "incerto":
            self._incerta = (vetor, agora)
            return
        incerta, self._incerta = self._incerta, None
        if falante.tipo != "dono" or incerta is None or agora - incerta[1] > BIOMETRIA_APRENDER_SEGUNDOS:
            return
        self.aprender_amostra(incerta[0])

    def aprender_amostra(self, vetor):
        """Soma uma frase confirmada às aprendidas do dono e refaz a média com as do cadastro."""
        import numpy as np

        with self._lock:
            chave = next((k for k, p in self._perfis.items() if p.get("papel") == "dono"), None)
            if chave is None:
                return
            perfil = self._perfis[chave]
            cadastro = self._amostras(chave)
            if not perfil.get("amostras_vetores"):  # perfil antigo (só a média): o cadastro fica guardado antes de mudar
                perfil["amostras_vetores"] = [[round(float(x), 5) for x in v] for v in cadastro]
            aprendidas = (perfil.get("aprendidas") or []) + [[round(float(x), 5) for x in _vetor_unitario(vetor)]]
            perfil["aprendidas"] = aprendidas[-MAX_APRENDIDAS:]
            todas = cadastro + [np.asarray(v, dtype=np.float32) for v in perfil["aprendidas"]]
            perfil["vetor"] = [float(x) for x in _vetor_unitario(np.mean(todas, axis=0))]
            self._salvar()
        logger.info("Aprendi uma frase da voz do dono pela confirmação (%d aprendidas).", len(perfil["aprendidas"]))

    def resolver(self, falante: Falante | None, agora: float | None = None) -> Falante | None:
        """Frase curta ou duvidosa ("sim", "para") vale quem falou com certeza há pouco; sem isso, fica incerta."""
        if falante is None:
            return None
        agora = time.time() if agora is None else agora
        if falante.tipo != "incerto":
            self._ultimo = (falante, agora)
            return falante
        if self._ultimo and agora - self._ultimo[1] <= BIOMETRIA_MANTER_SEGUNDOS:
            return replace(self._ultimo[0], nota=falante.nota, herdado=True)
        return falante

    def pode_tudo(self, falante: Falante | None) -> bool:
        """O dono (ou qualquer um, enquanto a biometria não está em uso) pode usar tudo."""
        return not self.ativa or falante is None or falante.e_dono

    # -- cadastro (a voz chama estes dois)
    def cadastrando(self) -> bool:
        return self.cadastro.ativo

    def receber_amostra(self, audio):
        self.cadastro.receber(audio)


class Cadastro:
    """Junta a fala de quem está cadastrando a voz: ~20 s de fala, em várias frases."""

    def __init__(self, biometria: Biometria):
        self.biometria = biometria
        self._lock = threading.Lock()
        self._zerar()

    def _zerar(self):
        self.ativo = False
        self.chave = self.nome = self.papel = ""
        self.vetores: list = []
        self.segundos = 0.0
        self.alvo = BIOMETRIA_CADASTRO_SEGUNDOS
        self.avisar = lambda texto: None
        self._avisou_metade = False
        self._relogio = None

    def iniciar(self, chave: str, nome: str, papel: str, avisar, alvo: float | None = None):
        with self._lock:
            self._zerar()
            self.ativo, self.chave, self.nome, self.papel, self.avisar = True, chave, nome, papel, avisar
            if alvo:
                self.alvo = alvo
            self.biometria.codificador.preparar()
            self._rearmar_relogio()

    def cancelar(self):
        with self._lock:
            self._parar_relogio()
            self._zerar()

    def _parar_relogio(self):
        if self._relogio:
            self._relogio.cancel()

    def _rearmar_relogio(self):
        self._parar_relogio()
        self._relogio = threading.Timer(PRAZO_SEM_FALA_SEGUNDOS, self._acabou_o_tempo)
        self._relogio.daemon = True
        self._relogio.start()

    def _acabou_o_tempo(self):
        with self._lock:
            if not self.ativo:
                return
            avisar = self.avisar
            self._zerar()
        avisar("Não ouvi você falar o suficiente, então cancelei o cadastro da voz. Peça de novo quando quiser.")

    def receber(self, audio):
        """Uma frase falada durante o cadastro. Muito curta ou sem modelo é ignorada."""
        duracao = len(audio) / TAXA
        if not self.ativo or duracao < MINIMO_AMOSTRA_SEGUNDOS:
            return
        try:
            vetor = self.biometria.codificador.vetor(audio, espera=30.0)
        except Exception as exc:
            logger.warning("Amostra de voz descartada: %s", exc)
            return
        if vetor is None:
            self._falhou("Não consegui carregar o modelo de reconhecimento de voz, então não deu para cadastrar.")
            return
        with self._lock:
            if not self.ativo:
                return
            self.vetores.append(vetor)
            self.segundos += duracao
            self._rearmar_relogio()
            terminou = self.segundos >= self.alvo
            metade = not terminou and not self._avisou_metade and self.segundos >= self.alvo / 2
            if metade:
                self._avisou_metade = True
                avisar = self.avisar
        if metade:
            avisar("Metade. Continua falando.")
        if terminou:
            self._concluir()

    def _falhou(self, mensagem: str):
        with self._lock:
            avisar = self.avisar
            self._parar_relogio()
            self._zerar()
        avisar(mensagem)

    def _concluir(self):
        import numpy as np

        with self._lock:
            vetores, chave, nome, papel, avisar = self.vetores, self.chave, self.nome, self.papel, self.avisar
            segundos = self.segundos  # antes de zerar (o perfil era salvo com 0 segundos)
            self._parar_relogio()
            self._zerar()
        # Cada amostra precisa se parecer com as outras (a mesma voz fica em ~0,6 a 0,8; vozes diferentes, abaixo
        # de ~0,35). Comparar com o centro não pega o caso de várias pessoas falando.
        matriz = np.array(vetores) @ np.array(vetores).T
        parecido = (matriz.sum(axis=1) - 1.0) / max(1, len(vetores) - 1)
        bons = [v for v, nota in zip(vetores, parecido) if nota >= CONSISTENCIA_MINIMA]
        if len(bons) < max(2, len(vetores) // 2 + 1):
            avisar("As amostras não pareceram da mesma voz, talvez tenha mais gente falando. Tenta de novo, só você falando.")
            return
        situacao, total = self.biometria.juntar_ao_perfil(chave, nome, papel, bons, segundos)
        de_quem = "da sua voz" if papel == "dono" else f"da voz de {nome}"
        if situacao == "somou":
            avisar(f"Pronto, juntei com o que eu já tinha {de_quem}: agora são {total} amostras. Cada cadastro, "
                   "de um jeito ou lugar diferente, me ajuda a reconhecer melhor.")
        elif papel == "dono":
            avisar("Pronto, guardei a sua voz. Daqui em diante eu sei quando é você falando."
                   + (" Ficou bem diferente do cadastro anterior, então troquei pelo novo." if situacao == "trocou" else ""))
        else:
            avisar(f"Pronto, guardei a voz de {nome}. Agora eu sei quando é a pessoa falando.")


BIOMETRIA = Biometria()


# ------------------------------------------------------------------ comandos falados
RE_CADASTRAR = re.compile(r"\b(cadastr\w*|registr\w*|grav\w*|aprend\w*|memoriz\w*|salv\w*|guard\w*)\b.{0,25}\bvoz\b")
RE_APAGAR = re.compile(r"\b(esquec\w*|apag\w*|remov\w*|exclu\w*|delet\w*)\b.{0,25}\bvoz\b")
RE_LISTAR = re.compile(r"\b(quais|que|quem)\b.{0,30}\b(vozes|pessoas)\b.{0,30}\b(conhece|reconhece|cadastrad\w*)")
RE_QUEM = re.compile(r"\bquem (esta|ta) (falando|ai|comigo)\b|\bquem sou eu\b|\bvoce (me )?reconhece\b"
                     r"|\breconhece (a )?minha voz\b|\bsabe quem (eu sou|esta falando)\b")
RE_LIGAR = re.compile(r"\b(lig\w*|ativ\w*|desativ\w*|deslig\w*|para de usar)\b.{0,25}"
                      r"\b(reconhecimento de voz|biometria|reconhecer (a )?(minha )?voz|identificacao de voz)\b")
RE_DE_QUEM = re.compile(r"\bvoz d[aoe]s?\s+(?:meu\s+|minha\s+|nosso\s+|nossa\s+)?([a-zà-ÿ]+)")
RE_EU_SOU = re.compile(r"\b(?:eu sou|meu nome [eé]|me chamo)\s+(?:o\s+|a\s+)?([a-zà-ÿ]+)")
RE_DESLIGAR = re.compile(r"\b(deslig\w*|desativ\w*|para de usar)\b")

NEGADO = "Isso eu só faço para o dono da casa."


class AgenteBiometria:
    """Comandos falados sobre as vozes. `responder` devolve a resposta ou None (o roteamento segue)."""

    def __init__(self, biometria: Biometria, avisar):
        self.biometria = biometria
        self.avisar = avisar  # fala fora da vez (o cadastro termina depois que a frase já foi respondida)

    def responder(self, texto: str, falante: Falante | None = None, pelo_microfone: bool = True) -> str | None:
        c = _sem_acento(texto)
        original = (texto or "").lower()  # os nomes mantêm o acento
        if "voz" not in c and "quem sou eu" not in c and "quem esta" not in c and "quem ta" not in c \
                and "biometria" not in c:
            return None
        bio = self.biometria
        dono = bio.pode_tudo(falante)

        if RE_QUEM.search(c):
            return self._quem_fala(falante)
        if RE_LISTAR.search(c):
            return self._listar(dono)
        if RE_LIGAR.search(c):
            if not dono:
                return NEGADO
            ligar = not RE_DESLIGAR.search(c)
            bio.ligar(ligar)
            return ("Reconhecimento de voz ligado." if ligar
                    else "Desliguei o reconhecimento de voz: volto a atender qualquer pessoa como se fosse você.")
        if RE_APAGAR.search(c):
            return self._apagar(c, original, dono)
        if RE_CADASTRAR.search(c):
            return self._cadastrar(c, original, dono, pelo_microfone)
        return None

    def _quem_fala(self, falante: Falante | None) -> str:
        bio = self.biometria
        if not bio.ativa:
            if not bio.tem_dono():
                return "Eu ainda não tenho a sua voz. Diga “Beatriz, cadastra minha voz” e eu aprendo."
            return "O reconhecimento de voz está desligado."
        if falante is None:
            return "Ainda estou carregando o reconhecimento de voz, tenta de novo daqui a pouco."
        nota = "" if falante.nota is None else f" (semelhança de {str(falante.nota).replace('.', ',')})"
        if falante.herdado:
            return "Essa frase foi curta ou duvidosa para eu ter certeza, então presumo que continue sendo quem falava antes."
        if falante.tipo == "dono":
            return f"É você{', ' + falante.nome if falante.nome else ''}{nota}."
        if falante.tipo == "conhecido":
            return f"Parece a voz de {falante.nome}{nota}."
        if falante.tipo == "convidado":
            return f"Não reconheço essa voz{nota}. Para mim, é um convidado."
        return f"Não tenho certeza de quem é{nota}."

    def _listar(self, dono: bool) -> str:
        if not dono:
            return NEGADO
        perfis = self.biometria.perfis()
        if not perfis:
            return "Ainda não conheço nenhuma voz."
        partes = [f"{nome}, que é o dono" if papel == "dono" else nome for nome, papel in perfis]
        return "Conheço a voz de " + ", ".join(partes[:-1]) + (" e " if len(partes) > 1 else "") + partes[-1] + "."

    def _apagar(self, c: str, original: str, dono: bool) -> str:
        bio = self.biometria
        if not dono:
            return NEGADO
        de_quem = RE_DE_QUEM.search(original)
        if not de_quem or "minha voz" in c:
            if bio.remover("dono"):
                return "Pronto, esqueci a sua voz. Sem o dono cadastrado, eu atendo qualquer pessoa."
            return "Eu não tinha a sua voz guardada."
        chave = bio.chave_de(de_quem.group(1))
        if chave and bio.remover(chave):
            return f"Pronto, esqueci a voz de {de_quem.group(1).capitalize()}."
        return f"Eu não conheço a voz de {de_quem.group(1).capitalize()}."

    def _cadastrar(self, c: str, original: str, dono: bool, pelo_microfone: bool) -> str:
        bio = self.biometria
        # Fora do microfone (o WhatsApp, que só o dono usa) o pedido vale e o cadastro escuta no microfone: é a saída
        # para quando ela não reconhece a voz do dono e por isso recusaria o "cadastra minha voz" falado.
        if not pelo_microfone and not dono:
            return NEGADO
        microfone = "" if pelo_microfone else " no microfone do computador"
        if not bio.habilitada:
            return "O reconhecimento de voz está desligado nas configurações (BIOMETRIA_VOZ)."
        de_quem = RE_DE_QUEM.search(original)
        if de_quem and "minha voz" not in c:
            if not dono:
                return NEGADO
            nome = de_quem.group(1)
            chave, papel, quem = _sem_acento(nome), "conhecido", nome.capitalize()
            abertura = f"Certo. Peça para {quem} falar normalmente comigo{microfone} por uns {int(bio.cadastro.alvo)} segundos."
        else:
            if bio.tem_dono() and not dono:
                return NEGADO  # só o dono troca a própria voz; senão qualquer um tomaria o lugar dele
            eu_sou = RE_EU_SOU.search(original)
            nome = eu_sou.group(1).capitalize() if eu_sou else bio.nome_do_dono()
            chave, papel, quem = "dono", "dono", nome
            # Os pedidos do dia a dia são frases curtas: amostras parecidas com eles reconhecem melhor.
            abertura = (f"Certo{', ' + nome if nome else ''}. Fala comigo{microfone} por uns {int(bio.cadastro.alvo)} "
                        "segundos, do lugar onde você costuma falar: pode ser do jeito que você me pede as coisas, "
                        "em frases curtas, ou sobre o seu dia.")
        if bio.tem_perfil(chave):
            abertura += " Vou juntar com o que eu já tenho dessa voz."
        bio.cadastro.iniciar(chave, quem, papel, self.avisar)
        return abertura


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "--exportar":
        pasta_do_modelo = sys.argv[2] if len(sys.argv) > 2 else CAMINHO_MODELO_VOZ
        logging.basicConfig(level=logging.INFO)
        sys.exit(0 if exportar_onnx(pasta_do_modelo) else 1)
    print("Uso: python biometria_voz.py --exportar [pasta do modelo]")
