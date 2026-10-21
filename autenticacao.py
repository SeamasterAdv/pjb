"""Login da Beatriz: rosto + senha digitada, tudo no computador.

- `ContaLocal`: o login (usuário, senha com scrypt, vetores do rosto, código de recuperação e as tentativas erradas) em
  `CAMINHO_LOGIN`. Depois de `LOGIN_TENTATIVAS` erros seguidos a conta trava por 30 s, 60 s, 120 s... (até 15 min).
- `ReconhecedorFacial`: detector YuNet e reconhecedor SFace do OpenCV (modelos pequenos, baixados uma vez do OpenCV Zoo
  e conferidos por hash). Cada rosto vira um vetor de 128 números; o que fica guardado são esses números, nunca fotos.
- `VerificacaoDeRosto`: reconhece o rosto de frente e depois pede para virar a cabeça para um lado sorteado. Quem
  segura uma foto parada não passa. Não vence um vídeo ou uma máscara: por isso a senha digitada também é exigida.
- `CadastroDeRosto`: guarda as amostras do rosto, aprende para que lado fica a "esquerda" da sua câmera (algumas
  câmeras espelham a imagem) e faz um teste antes de salvar, para você não se trancar do lado de fora.
- `FluxoDeEntrada`: junta os fatores conforme `LOGIN_MODO`: "rosto+senha" (padrão), "rosto_ou_senha" ou "senha".

É um cadeado de conveniência para a assistente, não segurança do Windows: quem pode editar os arquivos do projeto
(`.env`, `login.json`) consegue desligá-lo.
"""
import hashlib
import hmac
import json
import logging
import os
import random
import secrets
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field

import numpy as np

from config import (CAMERA_INDICE, CAMINHO_LOGIN, CAMINHO_MODELOS_ROSTO, LOGIN_LIMIAR_ROSTO, LOGIN_MODO,
                    LOGIN_TENTATIVAS)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ modelos de rosto (OpenCV Zoo)
MODELOS_ROSTO = {
    "face_detection_yunet_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "face_recognition_sface_2021dec.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}


class ModelosIndisponiveis(RuntimeError):
    pass


def _sha256(caminho: str) -> str:
    resumo = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1 << 20), b""):
            resumo.update(bloco)
    return resumo.hexdigest()


def garantir_modelos(pasta: str = CAMINHO_MODELOS_ROSTO, baixar=None, ao_progredir=None) -> tuple[str, str]:
    """(caminho do detector, caminho do reconhecedor). Baixa o que faltar e confere o hash de tudo."""
    os.makedirs(pasta, exist_ok=True)
    baixar = baixar or (lambda url, destino: urllib.request.urlretrieve(url, destino))
    caminhos = []
    for nome, (url, esperado) in MODELOS_ROSTO.items():
        destino = os.path.join(pasta, nome)
        if not (os.path.exists(destino) and _sha256(destino) == esperado):
            if ao_progredir:
                ao_progredir(f"Baixando {nome}...")
            temporario = destino + ".baixando"
            try:
                baixar(url, temporario)
                if _sha256(temporario) != esperado:
                    raise ModelosIndisponiveis(f"{nome}: o arquivo baixado não confere com o esperado")
                os.replace(temporario, destino)
            except ModelosIndisponiveis:
                raise
            except Exception as exc:
                raise ModelosIndisponiveis(f"Não consegui baixar {nome}: {exc}") from exc
            finally:
                if os.path.exists(temporario):
                    os.remove(temporario)
        caminhos.append(destino)
    return caminhos[0], caminhos[1]


# ------------------------------------------------------------------ senha
SENHAS_FRACAS = {"12345678", "123456789", "1234567890", "password", "senha123", "qwertyui", "qwerty123", "11111111",
                 "abcd1234", "12341234", "senhasenha", "beatriz123", "123mudar"}
PARAMETROS_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


def hash_segredo(segredo: str, sal: bytes | None = None) -> dict:
    """scrypt com sal aleatório; guarda os parâmetros junto para poder mudá-los no futuro."""
    sal = sal or secrets.token_bytes(16)
    resumo = hashlib.scrypt(segredo.encode("utf-8"), salt=sal, maxmem=128 * 1024 * 1024, dklen=32, **PARAMETROS_SCRYPT)
    return {"alg": "scrypt", **PARAMETROS_SCRYPT, "sal": sal.hex(), "hash": resumo.hex()}


def conferir_segredo(segredo: str, registro: dict) -> bool:
    try:
        parametros = {k: registro[k] for k in ("n", "r", "p")}
        resumo = hashlib.scrypt(segredo.encode("utf-8"), salt=bytes.fromhex(registro["sal"]), maxmem=128 * 1024 * 1024,
                                dklen=32, **parametros)
        return hmac.compare_digest(resumo.hex(), registro["hash"])
    except Exception:
        return False


def validar_senha_nova(senha: str, usuario: str = "") -> str:
    """Mensagem do problema da senha, ou "" se serve."""
    if len(senha) < 8:
        return "A senha precisa ter pelo menos 8 caracteres."
    if senha.lower() in SENHAS_FRACAS or len(set(senha)) < 4:
        return "Essa senha é fácil demais de adivinhar."
    if usuario and usuario.lower() in senha.lower():
        return "A senha não pode conter o nome de usuário."
    return ""


def normalizar_usuario(usuario: str) -> str:
    return " ".join((usuario or "").split()).lower()


def gerar_codigo_recuperacao() -> str:
    """20 letras e números (sem 0/O/1/I) em 4 grupos: guarde num lugar seguro, vale para uma vez."""
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    letras = "".join(secrets.choice(alfabeto) for _ in range(20))
    return "-".join(letras[i:i + 5] for i in range(0, 20, 5))


def _limpar_codigo(codigo: str) -> str:
    return "".join(c for c in (codigo or "").upper() if c.isalnum())


# ------------------------------------------------------------------ conta
class ContaLocal:
    """O login salvo em disco. Cada operação lê e grava o arquivo (o painel e o launcher são processos diferentes)."""

    def __init__(self, caminho: str = CAMINHO_LOGIN, relogio=time.time, tentativas: int = LOGIN_TENTATIVAS):
        self.caminho = caminho
        self.relogio = relogio
        self.tentativas = tentativas

    # -- disco
    def _ler(self) -> dict:
        try:
            with open(self.caminho, encoding="utf-8") as arquivo:
                return json.load(arquivo)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.error("Não consegui ler o arquivo do login: %s", exc)
            return {}

    def _gravar(self, dados: dict):
        os.makedirs(os.path.dirname(self.caminho) or ".", exist_ok=True)
        descritor, temporario = tempfile.mkstemp(dir=os.path.dirname(self.caminho) or ".", suffix=".tmp")
        try:
            with os.fdopen(descritor, "w", encoding="utf-8") as arquivo:
                json.dump(dados, arquivo, ensure_ascii=False)
            os.replace(temporario, self.caminho)
        finally:
            if os.path.exists(temporario):
                os.remove(temporario)

    # -- estado
    def existe(self) -> bool:
        dados = self._ler()
        return bool(dados.get("usuario") and dados.get("senha"))

    @property
    def usuario(self) -> str:
        return self._ler().get("usuario", "")

    def tem_rosto(self) -> bool:
        return bool(self._ler().get("rosto", {}).get("vetores"))

    def rosto(self) -> tuple[list, int]:
        """(vetores do rosto, sinal da esquerda: +1 ou -1)"""
        dados = self._ler().get("rosto", {})
        return [np.asarray(v, dtype=np.float32) for v in dados.get("vetores", [])], int(dados.get("sinal_esquerda", 1))

    def criar(self, usuario: str, senha: str, vetores: list, sinal_esquerda: int, codigo: str | None = None) -> str:
        """Cria (ou substitui) o login e devolve o código de recuperação, que só aparece esta vez. A tela pode gerar o
        código antes (`gerar_codigo_recuperacao`) para a pessoa anotá-lo antes de o login existir."""
        usuario = normalizar_usuario(usuario)
        if not usuario:
            raise ValueError("O usuário não pode ficar vazio.")
        problema = validar_senha_nova(senha, usuario)
        if problema:
            raise ValueError(problema)
        codigo = codigo or gerar_codigo_recuperacao()
        self._gravar({
            "versao": 1, "usuario": usuario, "senha": hash_segredo(senha), "codigo": hash_segredo(_limpar_codigo(codigo)),
            "rosto": {"vetores": [[float(x) for x in v] for v in vetores], "sinal_esquerda": 1 if sinal_esquerda >= 0 else -1,
                      "modelo": "sface-2021dec", "criado": time.strftime("%Y-%m-%d %H:%M")},
            "seguranca": {"falhas": 0, "bloqueado_ate": 0.0},
        })
        return codigo

    def refazer_rosto(self, vetores: list, sinal_esquerda: int):
        dados = self._ler()
        dados["rosto"] = {"vetores": [[float(x) for x in v] for v in vetores], "sinal_esquerda": 1 if sinal_esquerda >= 0 else -1,
                          "modelo": "sface-2021dec", "criado": time.strftime("%Y-%m-%d %H:%M")}
        self._gravar(dados)

    def trocar_senha(self, nova: str) -> str:
        """Troca a senha (quem chama já confirmou a antiga). Devolve a mensagem de erro ou ""."""
        dados = self._ler()
        problema = validar_senha_nova(nova, dados.get("usuario", ""))
        if problema:
            return problema
        dados["senha"] = hash_segredo(nova)
        self._gravar(dados)
        return ""

    def novo_codigo(self, codigo: str | None = None) -> str:
        dados = self._ler()
        codigo = codigo or gerar_codigo_recuperacao()
        dados["codigo"] = hash_segredo(_limpar_codigo(codigo))
        self._gravar(dados)
        return codigo

    # -- tentativas erradas
    def segundos_de_bloqueio(self) -> float:
        return max(0.0, float(self._ler().get("seguranca", {}).get("bloqueado_ate", 0.0)) - self.relogio())

    def falhas(self) -> int:
        return int(self._ler().get("seguranca", {}).get("falhas", 0))

    def registrar_falha(self):
        dados = self._ler()
        if not dados:
            return
        seguranca = dados.setdefault("seguranca", {"falhas": 0, "bloqueado_ate": 0.0})
        seguranca["falhas"] = int(seguranca.get("falhas", 0)) + 1
        excesso = seguranca["falhas"] - self.tentativas
        if excesso >= 0:
            seguranca["bloqueado_ate"] = self.relogio() + min(900.0, 30.0 * 2 ** excesso)
        self._gravar(dados)

    def registrar_sucesso(self):
        dados = self._ler()
        if dados:
            dados["seguranca"] = {"falhas": 0, "bloqueado_ate": 0.0}
            self._gravar(dados)

    # -- conferências
    def verificar_senha(self, usuario: str, senha: str) -> bool:
        """Usuário e senha. Faz sempre o scrypt, com usuário certo ou errado, para o tempo não revelar qual errou."""
        dados = self._ler()
        registro = dados.get("senha") or hash_segredo("sem conta")
        senha_certa = conferir_segredo(senha or "", registro)
        usuario_certo = hmac.compare_digest(normalizar_usuario(usuario).encode(), dados.get("usuario", "").encode())
        return bool(dados) and senha_certa and usuario_certo

    def verificar_codigo(self, codigo: str) -> bool:
        registro = self._ler().get("codigo")
        return bool(registro) and conferir_segredo(_limpar_codigo(codigo), registro)


# ------------------------------------------------------------------ rosto
@dataclass
class Rosto:
    caixa: tuple  # x, y, largura, altura
    pontos: np.ndarray  # 5 pontos: olho direito, olho esquerdo (da pessoa), nariz, boca direita, boca esquerda
    nota: float = 1.0
    bruto: object = None  # a linha do detector, que o reconhecedor precisa para alinhar o rosto

    @property
    def largura(self) -> float:
        return float(self.caixa[2])

    @property
    def giro(self) -> float:
        """Para onde o nariz está entre os olhos, em distâncias entre os olhos: ~0 de frente; cresce quando a cabeça vira
        para a direita da imagem (o lado esquerdo da pessoa, numa câmera sem espelho)."""
        olho_a, olho_b, nariz = self.pontos[0], self.pontos[1], self.pontos[2]
        distancia = abs(float(olho_b[0] - olho_a[0])) or 1.0
        return float(nariz[0] - (olho_a[0] + olho_b[0]) / 2.0) / distancia


def _unitario(vetor) -> np.ndarray:
    vetor = np.asarray(vetor, dtype=np.float32).reshape(-1)
    norma = float(np.linalg.norm(vetor))
    return vetor / norma if norma else vetor


def semelhanca(vetor, modelos: list) -> float:
    """Cosseno médio com as 3 amostras mais parecidas do cadastro (uma amostra ruim sozinha não decide)."""
    if not modelos:
        return -1.0
    v = _unitario(vetor)
    notas = sorted((float(v @ _unitario(m)) for m in modelos), reverse=True)
    return float(np.mean(notas[:3]))


class ReconhecedorFacial:
    """YuNet (detecta o rosto e 5 pontos) + SFace (vetor de 128 números). Funções injetadas servem para os testes."""

    def __init__(self, detectar_fn=None, vetor_fn=None, pasta: str = CAMINHO_MODELOS_ROSTO):
        self._detectar_fn, self._vetor_fn, self._pasta = detectar_fn, vetor_fn, pasta
        self._detector = self._reconhecedor = None

    def preparar(self, ao_progredir=None):
        if self._detectar_fn and self._vetor_fn:
            return
        import cv2

        caminho_detector, caminho_reconhecedor = garantir_modelos(self._pasta, ao_progredir=ao_progredir)
        self._detector = cv2.FaceDetectorYN.create(caminho_detector, "", (320, 320), 0.9, 0.3, 5000)
        self._reconhecedor = cv2.FaceRecognizerSF.create(caminho_reconhecedor, "")

    def detectar(self, quadro) -> Rosto | None:
        """O maior rosto do quadro, ou None."""
        if self._detectar_fn:
            return self._detectar_fn(quadro)
        altura, largura = quadro.shape[:2]
        self._detector.setInputSize((largura, altura))
        _, rostos = self._detector.detect(quadro)
        if rostos is None or len(rostos) == 0:
            return None
        linha = max(rostos, key=lambda r: r[2] * r[3])
        return Rosto((float(linha[0]), float(linha[1]), float(linha[2]), float(linha[3])),
                     np.asarray(linha[4:14], dtype=np.float32).reshape(5, 2), float(linha[14]), linha)

    def vetor(self, quadro, rosto: Rosto) -> np.ndarray:
        if self._vetor_fn:
            return _unitario(self._vetor_fn(quadro, rosto))
        return _unitario(self._reconhecedor.feature(self._reconhecedor.alignCrop(quadro, rosto.bruto)))


# ------------------------------------------------------------------ verificar o rosto (com prova de vida)
LARGURA_MINIMA_ROSTO = 100  # pixels (câmera de 640x480): mais longe que isso o vetor fica ruim
GIRO_DE_FRENTE = 0.20  # |giro| até aqui conta como olhando de frente
GIRO_MINIMO = 0.28  # quanto o nariz precisa andar, em relação à posição de frente, para valer como "virou"
ACERTOS_DE_FRENTE = 5
QUADROS_VIRADOS = 3
LIMIAR_VIRADO = 0.30  # de lado o vetor perde qualidade: basta ser a mesma pessoa que já vinha sendo vista
TEMPO_DA_VERIFICACAO = 15.0  # sem reconhecer em 15 s, desiste (e o login pede a senha)
TEMPO_PARA_VIRAR = 10.0
ERROS_PARA_DESISTIR = 30  # quadros de frente seguidos que não são você (~1 a 2 s): outra pessoa, não vale esperar o tempo todo


@dataclass
class EstadoDoRosto:
    fase: str  # "procurar", "virar", "ok" ou "falhou"
    mensagem: str
    progresso: float = 0.0
    rosto: Rosto | None = None
    nota: float | None = None


class VerificacaoDeRosto:
    """Reconhece de frente e depois exige virar a cabeça para o lado sorteado. Um quadro por chamada de `passo`."""

    def __init__(self, reconhecedor: ReconhecedorFacial, modelos: list, sinal_esquerda: int = 1,
                 limiar: float = LOGIN_LIMIAR_ROSTO, relogio=time.time, direcao: str | None = None):
        self.reconhecedor, self.modelos, self.sinal_esquerda, self.limiar, self.relogio = (
            reconhecedor, modelos, 1 if sinal_esquerda >= 0 else -1, limiar, relogio)
        self.direcao = direcao or random.choice(("esquerda", "direita"))
        self.fase = "procurar"
        self._inicio = relogio()
        self._acertos, self._erros, self._viradas = 0, 0, 0  # _erros: quadros seguidos que não eram você
        self.viu_rosto = False  # algum rosto apareceu? (câmera vazia não conta como tentativa errada)
        self._girosde_frente: list[float] = []
        self._base = 0.0
        self._inicio_da_virada = 0.0

    def passo(self, quadro) -> EstadoDoRosto:
        if self.fase in ("ok", "falhou"):
            return EstadoDoRosto(self.fase, "")
        agora = self.relogio()
        if self.fase == "procurar" and agora - self._inicio > TEMPO_DA_VERIFICACAO:
            return self._falhar("Não reconheci o seu rosto.")
        if self.fase == "virar" and agora - self._inicio_da_virada > TEMPO_PARA_VIRAR:
            return self._falhar("Não vi a cabeça virar. Tente de novo.")
        rosto = self.reconhecedor.detectar(quadro)
        self.viu_rosto = self.viu_rosto or rosto is not None
        if rosto is None:
            self._acertos = self._viradas = 0
            return EstadoDoRosto(self.fase, "Procurando o seu rosto..." if self.fase == "procurar" else
                                 "Não estou vendo o seu rosto.", self._progresso())
        if rosto.largura < LARGURA_MINIMA_ROSTO:
            self._acertos = self._viradas = 0
            return EstadoDoRosto(self.fase, "Chegue um pouco mais perto da câmera.", self._progresso(), rosto)
        return self._frente(quadro, rosto) if self.fase == "procurar" else self._virada(quadro, rosto)

    # -- fase 1: de frente e é você
    def _frente(self, quadro, rosto: Rosto) -> EstadoDoRosto:
        if abs(rosto.giro) > GIRO_DE_FRENTE:
            self._acertos = 0
            return EstadoDoRosto("procurar", "Olhe de frente para a câmera.", self._progresso(), rosto)
        nota = semelhanca(self.reconhecedor.vetor(quadro, rosto), self.modelos)
        if nota >= self.limiar:
            self._acertos += 1
            self._erros = 0
            self._girosde_frente.append(rosto.giro)
            if self._acertos >= ACERTOS_DE_FRENTE:
                self._base = float(np.median(self._girosde_frente[-ACERTOS_DE_FRENTE:]))
                self.fase, self._inicio_da_virada = "virar", self.relogio()
                return EstadoDoRosto("virar", self._pedido_de_virada(), 0.5, rosto, nota)
            return EstadoDoRosto("procurar", "Confirmando o seu rosto...", self._progresso(), rosto, nota)
        self._acertos = 0
        self._erros += 1
        if self._erros >= ERROS_PARA_DESISTIR:
            return self._falhar("Não reconheci o seu rosto.")
        mensagem = "Não reconheço este rosto." if self._erros >= 8 else "Confirmando o seu rosto..."
        return EstadoDoRosto("procurar", mensagem, self._progresso(), rosto, nota)

    # -- fase 2: a mesma pessoa, virando para o lado pedido
    def _virada(self, quadro, rosto: Rosto) -> EstadoDoRosto:
        sentido = self.sinal_esquerda if self.direcao == "esquerda" else -self.sinal_esquerda
        deslocamento = (rosto.giro - self._base) * sentido
        if deslocamento >= GIRO_MINIMO:
            nota = semelhanca(self.reconhecedor.vetor(quadro, rosto), self.modelos)
            if nota >= LIMIAR_VIRADO:
                self._viradas += 1
                if self._viradas >= QUADROS_VIRADOS:
                    self.fase = "ok"
                    return EstadoDoRosto("ok", "Rosto confirmado.", 1.0, rosto, nota)
            else:
                self._viradas = 0  # outro rosto entrou no lugar durante a virada
        else:
            self._viradas = 0
        return EstadoDoRosto("virar", self._pedido_de_virada(), 0.5 + 0.5 * min(1.0, max(0.0, deslocamento / GIRO_MINIMO)) * 0.9,
                             rosto)

    def _pedido_de_virada(self) -> str:
        lado = "esquerdo" if self.direcao == "esquerda" else "direito"
        return f"Agora vire a cabeça devagar para o seu lado {lado}."

    def _progresso(self) -> float:
        return 0.5 * min(1.0, self._acertos / ACERTOS_DE_FRENTE)

    def _falhar(self, mensagem: str) -> EstadoDoRosto:
        self.fase = "falhou"
        return EstadoDoRosto("falhou", mensagem)


# ------------------------------------------------------------------ cadastrar o rosto
AMOSTRAS_DO_ROSTO = 8
INTERVALO_ENTRE_AMOSTRAS = 0.35


class CadastroDeRosto:
    """1) amostras de frente, 2) vira para a ESQUERDA (descobre o sentido da sua câmera), 3) teste completo."""

    def __init__(self, reconhecedor: ReconhecedorFacial, relogio=time.time):
        self.reconhecedor, self.relogio = reconhecedor, relogio
        self.fase = "frente"
        self.vetores: list = []
        self.sinal_esquerda = 1
        self._ultima = 0.0
        self._base: list[float] = []
        self._viradas, self._sentido = 0, 0
        self._teste: VerificacaoDeRosto | None = None

    def passo(self, quadro) -> EstadoDoRosto:
        if self.fase == "teste":
            estado = self._teste.passo(quadro)
            if estado.fase == "ok":
                self.fase = "pronto"
                return EstadoDoRosto("pronto", "Tudo certo, o seu rosto foi cadastrado.", 1.0, estado.rosto, estado.nota)
            if estado.fase == "falhou":
                self.__init__(self.reconhecedor, self.relogio)  # recomeça do zero
                return EstadoDoRosto("frente", "O teste falhou. Vamos refazer o cadastro: olhe para a câmera.")
            return EstadoDoRosto("teste", estado.mensagem, 0.85 + 0.15 * estado.progresso, estado.rosto, estado.nota)
        if self.fase == "pronto":
            return EstadoDoRosto("pronto", "Tudo certo, o seu rosto foi cadastrado.", 1.0)
        rosto = self.reconhecedor.detectar(quadro)
        if rosto is None:
            self._viradas = 0
            return EstadoDoRosto(self.fase, "Procurando o seu rosto...", self._progresso())
        if rosto.largura < LARGURA_MINIMA_ROSTO:
            return EstadoDoRosto(self.fase, "Chegue um pouco mais perto da câmera.", self._progresso(), rosto)
        return self._frente(quadro, rosto) if self.fase == "frente" else self._esquerda(rosto)

    def _frente(self, quadro, rosto: Rosto) -> EstadoDoRosto:
        if abs(rosto.giro) > GIRO_DE_FRENTE:
            return EstadoDoRosto("frente", "Olhe de frente para a câmera.", self._progresso(), rosto)
        agora = self.relogio()
        if agora - self._ultima >= INTERVALO_ENTRE_AMOSTRAS:
            self._ultima = agora
            self.vetores.append(self.reconhecedor.vetor(quadro, rosto))
            self._base.append(rosto.giro)
        if len(self.vetores) >= AMOSTRAS_DO_ROSTO:
            self.fase = "esquerda"
            return EstadoDoRosto("esquerda", "Agora vire a cabeça devagar para o SEU lado esquerdo.", 0.5, rosto)
        return EstadoDoRosto("frente", "Olhe para a câmera e fique parado.", self._progresso(), rosto)

    def _esquerda(self, rosto: Rosto) -> EstadoDoRosto:
        deslocamento = rosto.giro - float(np.median(self._base))
        if abs(deslocamento) >= GIRO_MINIMO:
            sentido = 1 if deslocamento > 0 else -1
            self._viradas = self._viradas + 1 if sentido == self._sentido else 1
            self._sentido = sentido
        else:
            self._viradas = 0
        if self._viradas >= QUADROS_VIRADOS:
            self.sinal_esquerda = self._sentido
            self.fase = "teste"
            self._teste = VerificacaoDeRosto(self.reconhecedor, self.vetores, self.sinal_esquerda, relogio=self.relogio)
            return EstadoDoRosto("teste", "Ótimo. Agora um teste: olhe de frente para a câmera.", 0.85, rosto)
        return EstadoDoRosto("esquerda", "Vire a cabeça devagar para o SEU lado esquerdo.", self._progresso(), rosto)

    def _progresso(self) -> float:
        if self.fase == "frente":
            return 0.5 * min(1.0, len(self.vetores) / AMOSTRAS_DO_ROSTO)
        return 0.5 + 0.35 * min(1.0, self._viradas / QUADROS_VIRADOS)


# ------------------------------------------------------------------ juntar os fatores
@dataclass
class Resultado:
    ok: bool
    mensagem: str = ""
    bloqueio: float = 0.0  # segundos até poder tentar de novo
    refazer_rosto: bool = False  # entrou pelo código de recuperação: o rosto deve ser cadastrado de novo


class FluxoDeEntrada:
    """Conforme `LOGIN_MODO`, diz o que falta e confere. A tela só mostra e chama estes métodos."""

    def __init__(self, conta: ContaLocal, modo: str = LOGIN_MODO):
        self.conta, self.modo = conta, modo if modo in ("rosto+senha", "rosto_ou_senha", "senha") else "rosto+senha"
        self.rosto_ok = False
        self.codigo_ok = False

    @property
    def usa_rosto(self) -> bool:
        return self.modo != "senha" and self.conta.tem_rosto()

    @property
    def entra_so_com_o_rosto(self) -> bool:
        return self.modo == "rosto_ou_senha" and self.usa_rosto

    @property
    def senha_liberada(self) -> bool:
        """Se a tela já pode pedir a senha (em "rosto+senha", só depois do rosto ou do código)."""
        return self.modo != "rosto+senha" or not self.usa_rosto or self.rosto_ok or self.codigo_ok

    def verificacao(self, reconhecedor: ReconhecedorFacial) -> VerificacaoDeRosto:
        modelos, sinal = self.conta.rosto()
        return VerificacaoDeRosto(reconhecedor, modelos, sinal)

    def bloqueio(self) -> float:
        return self.conta.segundos_de_bloqueio()

    def rosto_confirmado(self) -> Resultado:
        """A verificação do rosto passou. Em "rosto_ou_senha" isso já basta para entrar."""
        if self.bloqueio():
            return self._bloqueado()
        self.rosto_ok = True
        if self.modo == "rosto_ou_senha":
            self.conta.registrar_sucesso()
            return Resultado(True, "Bem-vindo.")
        return Resultado(False, "Rosto confirmado. Agora digite a senha.")

    def rosto_falhou(self, viu_rosto: bool = True) -> Resultado:
        """O rosto não passou. Só conta como tentativa errada se algum rosto apareceu: câmera vazia não é invasão."""
        if viu_rosto:
            self.conta.registrar_falha()
        return self._bloqueado() if self.bloqueio() else Resultado(False, "Não reconheci o seu rosto.")

    def usar_codigo(self, codigo: str) -> Resultado:
        """Código de recuperação no lugar do rosto (câmera quebrada, rosto mudou). A senha continua sendo exigida."""
        if self.bloqueio():
            return self._bloqueado()
        if self.conta.verificar_codigo(codigo):
            self.codigo_ok = True
            return Resultado(False, "Código aceito. Agora digite a senha.")
        self.conta.registrar_falha()
        return self._bloqueado() if self.bloqueio() else Resultado(False, "Código de recuperação incorreto.")

    def entrar(self, usuario: str, senha: str) -> Resultado:
        if self.bloqueio():
            return self._bloqueado()
        if not self.senha_liberada:
            return Resultado(False, "Confirme o rosto primeiro.")
        if not self.conta.verificar_senha(usuario, senha):
            self.conta.registrar_falha()
            return self._bloqueado() if self.bloqueio() else Resultado(False, "Usuário ou senha incorretos.")
        self.conta.registrar_sucesso()
        if self.codigo_ok:  # entrou sem o rosto: a tela refaz o rosto e só então troca o código (o novo é mostrado lá)
            return Resultado(True, "Bem-vindo.", refazer_rosto=True)
        return Resultado(True, "Bem-vindo.")

    def _bloqueado(self) -> Resultado:
        segundos = self.bloqueio()
        return Resultado(False, f"Muitas tentativas erradas. Tente de novo em {int(segundos) + 1} s.", segundos)


# ------------------------------------------------------------------ câmera
class Camera:
    """A webcam do Windows (MSMF primeiro: abre em ~1,5 s; o DirectShow chega a levar 6 s)."""

    def __init__(self, indice: int = CAMERA_INDICE):
        self.indice = indice
        self._captura = None

    def abrir(self) -> bool:
        import cv2

        for api in (cv2.CAP_MSMF, cv2.CAP_DSHOW, cv2.CAP_ANY):
            captura = cv2.VideoCapture(self.indice, api)
            if captura.isOpened():
                captura.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                captura.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self._captura = captura
                return True
            captura.release()
        return False

    def ler(self):
        if self._captura is None:
            return None
        ok, quadro = self._captura.read()
        return quadro if ok else None

    def fechar(self):
        if self._captura is not None:
            self._captura.release()
            self._captura = None


# ------------------------------------------------------------------ chamado pelo main
def exigir_login(arquivo_da_tela: str | None = None) -> bool:
    """Abre a tela de login (um processo à parte, só com o que ela precisa) e devolve se a pessoa entrou.
    Com LOGIN_FACIAL=false não pergunta nada. Sem login criado, a tela abre o cadastro."""
    import subprocess
    import sys

    from config import LOGIN_FACIAL

    if not LOGIN_FACIAL:
        return True
    pasta = os.path.dirname(os.path.abspath(__file__))
    tela = arquivo_da_tela or os.path.join(pasta, "login_tela.py")
    try:
        return subprocess.run([sys.executable, tela, "--entrar"], cwd=pasta).returncode == 0
    except Exception as exc:
        logger.error("Não consegui abrir a tela de login: %s", exc)
        return False
