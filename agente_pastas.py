"""Abrir pastas por voz: "abre a pasta da OAB", "abre meus downloads", "mostra a pasta de estudos".

- Índice das pastas do PC (Área de Trabalho, Documentos, Downloads, Imagens, Vídeos, Músicas, a pasta do projeto e o que
  estiver em `PASTAS_RAIZ`), refeito a cada 10 min ou quando a busca não acha nada. Não olha dentro de arquivos.
- A busca entende siglas nos dois sentidos (`siglas.interpretacoes`): "OAB" acha a pasta "Ordem dos Advogados" e
  "ordem dos advogados" acha a pasta "OAB"; "oab" também acha "Chamada OAB até passar" e "OAB2025".
- Uma candidata clara abre na hora. Se duas ou mais empatam ("OAB" em Documentos e em Downloads), ela diz quais são e
  pergunta qual ("a primeira", "a segunda", "a de downloads"). O que ela abre sai sempre do índice (nunca de um caminho
  ditado), e `os.startfile` numa pasta só abre o Explorador.
"""
import ctypes
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field

from rapidfuzz import fuzz

import siglas
from config import NOTELOOKLM_PATH, PALAVRAS_ATIVACAO, PASTAS_RAIZ

logger = logging.getLogger(__name__)

IGNORAR = {"appdata", "node_modules", "__pycache__", "site-packages", "windows", "program files", "program files (x86)",
           "programdata", "system volume information", "$recycle.bin", "venv", ".git", "chroma", "banco_memoria"}
LIMITE_DE_PASTAS = 40000
VALIDADE_DO_INDICE = 600.0
VALIDADE_DA_PERGUNTA = 120.0
FOLGA_DE_EMPATE = 0.12  # a melhor só abre sozinha se passar a segunda por mais que isto
GUIAS_DO_WINDOWS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "documentos": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B", "imagens": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC", "musicas": "4BD8D571-6D19-48D3-BE97-422220080E43",
}
PALAVRAS_DE_LIGACAO = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "meu", "minha", "meus", "minhas", "para", "pra", "pro",
                       "mim", "por", "favor", "ai", "aqui", "la", "no", "na", "nos", "nas", "em", "um", "uma", "uns", "umas", "que",
                       "eu", "me", "explorador", "arquivos", "computador", "pc", "agora", "pasta", "pastas", "diretorio", "diretorios",
                       "folder", "chamada", "chamado", "nome", "com", "tenho", "esta", "fica", "onde", "e", "ver", "o", "so",
                       *[siglas.sem_acento(nome) for nome in PALAVRAS_ATIVACAO]}  # "Beatriz, abre a pasta..." digitado


# ------------------------------------------------------------------ pastas do Windows
class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]


def pasta_do_windows(nome: str) -> str:
    """Caminho de uma pasta conhecida ("desktop", "documentos", "downloads"...), mesmo movida para o OneDrive."""
    try:
        u = uuid.UUID(GUIAS_DO_WINDOWS[nome])
        guia = _GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        ponteiro = ctypes.c_void_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guia), 0, None, ctypes.byref(ponteiro)) == 0:
            caminho = ctypes.wstring_at(ponteiro.value)
            ctypes.windll.ole32.CoTaskMemFree(ponteiro)
            if os.path.isdir(caminho):
                return caminho
    except Exception as exc:
        logger.debug("Pasta %s do Windows indisponível: %s", nome, exc)
    padrao = {"desktop": "Desktop", "documentos": "Documents", "downloads": "Downloads", "imagens": "Pictures",
              "videos": "Videos", "musicas": "Music"}[nome]
    return os.path.join(os.path.expanduser("~"), padrao)


def lugares_conhecidos() -> dict[str, tuple[str, str]]:
    """apelido falado (sem acento) -> (nome para falar, caminho)."""
    projeto = NOTELOOKLM_PATH
    lugares = {
        ("downloads", "download", "baixados", "baixei"): ("Downloads", pasta_do_windows("downloads")),
        ("documentos", "meus documentos"): ("Documentos", pasta_do_windows("documentos")),
        ("area de trabalho", "desktop", "tela inicial"): ("Área de Trabalho", pasta_do_windows("desktop")),
        ("imagens", "fotos", "minhas fotos", "figuras"): ("Imagens", pasta_do_windows("imagens")),
        ("videos", "meus videos"): ("Vídeos", pasta_do_windows("videos")),
        ("musicas", "musica", "minhas musicas"): ("Músicas", pasta_do_windows("musicas")),
        ("projeto", "projeto da beatriz", "beatriz", "assistente", "pasta da beatriz"): ("a pasta da Beatriz", projeto),
        ("documentos gerados", "arquivos gerados", "gerados", "pdfs gerados"): ("Documentos gerados", os.path.join(projeto, "documentos_gerados")),
        ("biblioteca", "documentos da assistente", "base de conhecimento"): ("a biblioteca da Beatriz", os.path.join(projeto, "documentos_assistente")),
        ("obsidian", "vault", "cofre", "notas"): ("o Obsidian", os.path.join(projeto, "obsidian")),
        ("lixeira",): ("a Lixeira", "shell:RecycleBinFolder"),
    }
    return {apelido: (nome, caminho) for apelidos, (nome, caminho) in lugares.items() for apelido in apelidos}


# ------------------------------------------------------------------ índice e busca
@dataclass
class Pasta:
    caminho: str
    nome: str
    tokens: list[str]
    profundidade: int

    @property
    def junto(self) -> str:
        return "".join(self.tokens)


def tokens_do_nome(nome: str) -> list[str]:
    """"Chamada OAB até passar" -> chamada oab ate passar; "OAB2025" -> oab 2025."""
    return re.findall(r"[a-z]+|\d+", siglas.sem_acento(nome))


def nota_da_palavra(q: str, pasta: Pasta) -> float:
    """O quanto uma palavra da busca aparece no nome da pasta (0 a 1)."""
    if q in pasta.tokens:
        return 1.0
    if len(q) >= 3 and any(len(f) >= 3 and (f.startswith(q) or q.startswith(f)) for f in pasta.tokens):
        return 0.85
    if len(q) >= 3 and q in pasta.junto:
        return 0.8  # "oab" dentro de "chamadaoab"
    if len(q) >= 4 and any(len(f) >= 4 and fuzz.ratio(q, f) >= 85 for f in pasta.tokens):
        return 0.75  # "estudo" e "estudos", "advogado" e "advogados"
    return 0.0


def pontuar(palavras: list[str], pasta: Pasta) -> float:
    melhor = 0.0
    for interpretacao in siglas.interpretacoes(palavras):
        notas = [nota_da_palavra(q, pasta) for q in interpretacao]
        if not notas or min(notas) < 0.6:
            continue
        base = sum(notas) / len(notas)
        cobertura = min(1.0, len(interpretacao) / max(1, len(pasta.tokens)))  # nome curto e parecido com a busca ganha
        nota = base * (0.7 + 0.3 * cobertura) - 0.02 * pasta.profundidade
        if pasta.tokens == interpretacao:
            nota += 0.2
        melhor = max(melhor, nota)
    return melhor


class IndiceDePastas:
    def __init__(self, raizes=None, relogio=time.time):
        self._raizes_fn = raizes or self._raizes_padrao
        self.relogio = relogio
        self.pastas: list[Pasta] = []
        self.feito_em = 0.0

    @staticmethod
    def _raizes_padrao() -> list[tuple[str, int]]:
        raizes = [(pasta_do_windows("desktop"), 4), (pasta_do_windows("documentos"), 5), (pasta_do_windows("downloads"), 3),
                  (pasta_do_windows("imagens"), 2), (pasta_do_windows("videos"), 2), (pasta_do_windows("musicas"), 2),
                  (NOTELOOKLM_PATH, 3), (os.path.expanduser("~"), 1)]
        raizes += [(pasta, 4) for pasta in PASTAS_RAIZ]
        return raizes

    def atualizar(self):
        achadas: dict[str, Pasta] = {}
        for raiz, profundidade in self._raizes_fn():
            if not os.path.isdir(raiz):
                continue
            pilha = [(raiz, 0)]
            while pilha and len(achadas) < LIMITE_DE_PASTAS:
                atual, nivel = pilha.pop()
                try:
                    entradas = list(os.scandir(atual))
                except OSError:
                    continue
                for entrada in entradas:
                    try:
                        if not entrada.is_dir(follow_symlinks=False) or entrada.name.startswith((".", "$")):
                            continue
                        if entrada.name.lower() in IGNORAR or os.path.isjunction(entrada.path):
                            continue
                    except OSError:
                        continue
                    caminho = os.path.normcase(os.path.abspath(entrada.path))
                    if caminho not in achadas:
                        achadas[caminho] = Pasta(os.path.abspath(entrada.path), entrada.name, tokens_do_nome(entrada.name), nivel + 1)
                    if nivel + 1 < profundidade:
                        pilha.append((entrada.path, nivel + 1))
        self.pastas, self.feito_em = list(achadas.values()), self.relogio()

    def garantir(self, forcar: bool = False):
        if forcar or not self.feito_em or self.relogio() - self.feito_em > VALIDADE_DO_INDICE:
            self.atualizar()

    def buscar(self, palavras: list[str], maximo: int = 5) -> list[tuple[float, Pasta]]:
        pontuadas = [(pontuar(palavras, pasta), pasta) for pasta in self.pastas]
        pontuadas = [(nota, pasta) for nota, pasta in pontuadas if nota > 0]
        return sorted(pontuadas, key=lambda item: (-item[0], item[1].profundidade, item[1].caminho))[:maximo]


# ------------------------------------------------------------------ o agente
# Ao responder "qual delas?", só artigos e preposições saem: "a da chamada" precisa da palavra "chamada".
LIGACAO_DA_RESPOSTA = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "pra", "para", "por", "favor",
                       "ai", "aqui", "um", "uma", "eu", "me", "pasta", "pastas", "quero", "que",
                       *[siglas.sem_acento(nome) for nome in PALAVRAS_ATIVACAO]}
RE_VERBO = re.compile(r"\b(abr\w*|mostr\w*|exib\w*|me leva\w*|leva\w* (?:eu|me)|ir para|vai para|va para|acess\w*|navega\w*|"
                      r"quero ver|localiz\w*|procur\w*|ach(?:e|ar|a)|encontr\w*|onde (?:esta|fica))\b")
RE_PASTA = re.compile(r"\b(pastas?|diretorios?|folders?)\b")
RE_ONDE = re.compile(r"\bonde (?:esta|fica|ficam)\b")
ORDINAIS = {"primeira": 0, "primeiro": 0, "um": 0, "1": 0, "segunda": 1, "segundo": 1, "dois": 1, "2": 1, "terceira": 2, "terceiro": 2,
            "tres": 2, "3": 2, "quarta": 3, "quarto": 3, "quatro": 3, "4": 3, "ultima": -1, "ultimo": -1}
RE_CANCELAR = re.compile(r"\b(nenhum\w*|cancela\w*|deixa\w*|esquece\w*|nao quero|nao precisa|nao e nenhuma)\b")


@dataclass
class Pergunta:
    candidatas: list = field(default_factory=list)  # pastas entre as quais escolher; vazio = esperando o nome
    ate: float = 0.0
    chave: str = ""  # a busca, para lembrar a escolha
    aberta: Pasta | None = None  # abriu a preferida sozinha: só "não" ou "a outra" mudam
    acao: object = None  # em vez de abrir a pasta escolhida, faz isto com ela (arquivar um arquivo, por exemplo)


class AgentePastas:
    """`responder(texto)` devolve a resposta falada ou None (o roteamento segue)."""

    def __init__(self, indice: IndiceDePastas | None = None, abrir=None, lugares=None, relogio=time.time,
                 preferencias: str | None = None):
        self.indice = indice or IndiceDePastas()
        self._abrir = abrir or os.startfile
        self._lugares = lugares
        self.relogio = relogio
        self.pergunta: Pergunta | None = None
        self._arquivo_de_preferencias = preferencias or os.path.join(NOTELOOKLM_PATH, "banco_memoria", "pastas_preferidas.json")
        self._preferidas = self._ler_preferencias()

    # -- a escolha de cada busca ("oab" -> a pasta do Desktop) fica salva para não perguntar de novo
    def _ler_preferencias(self) -> dict:
        try:
            with open(self._arquivo_de_preferencias, encoding="utf-8") as arquivo:
                return dict(json.load(arquivo))
        except (OSError, ValueError):
            return {}

    def _lembrar(self, chave: str, pasta: Pasta):
        if not chave:
            return
        self._preferidas[chave] = pasta.caminho
        try:
            os.makedirs(os.path.dirname(self._arquivo_de_preferencias), exist_ok=True)
            with open(self._arquivo_de_preferencias, "w", encoding="utf-8") as arquivo:
                json.dump(self._preferidas, arquivo, ensure_ascii=False)
        except OSError as exc:
            logger.debug("Não consegui salvar a pasta preferida: %s", exc)

    def esperando_resposta(self) -> bool:
        """Se ela acabou de perguntar "qual delas?" (ou "qual pasta?") e a resposta ainda vale."""
        return self.pergunta is not None and self.relogio() <= self.pergunta.ate and self.pergunta.aberta is None

    def aquecer(self):
        """Monta o índice antes do primeiro pedido (chamado em segundo plano na abertura)."""
        try:
            self.indice.garantir()
        except Exception as exc:
            logger.warning("Índice de pastas indisponível: %s", exc)

    # -- entrada
    def responder(self, texto: str) -> str | None:
        c = siglas.sem_acento(siglas.corrigir_siglas(texto))
        if self.pergunta and self.relogio() > self.pergunta.ate:
            self.pergunta = None
        pedido = self._pedido(c)
        if pedido is None and self.pergunta is not None:
            return self._responder_pergunta(c)
        if pedido is None:
            return None
        self.pergunta = None
        palavras, onde = pedido
        lugar = self._lugar(palavras)
        if lugar:
            return self._abrir_lugar(*lugar)
        if not palavras:
            self.pergunta = Pergunta([], self.relogio() + VALIDADE_DA_PERGUNTA)
            return "Qual pasta você quer abrir?"
        return self._abrir_busca(palavras, onde)

    def _pedido(self, c: str) -> tuple[list[str], bool] | None:
        """(palavras da busca, se perguntou "onde está") ou None se não é um pedido de abrir pasta."""
        tem_pasta = bool(RE_PASTA.search(c))
        tem_verbo = bool(RE_VERBO.search(c))
        lugar_dito = any(re.search(rf"\b{re.escape(apelido)}\b", c) for apelido in self._todos_os_lugares()
                         if apelido not in {"beatriz", "assistente", "notas", "projeto", "cofre", "gerados"})
        if not tem_verbo or not (tem_pasta or (lugar_dito and re.search(r"\b(abr\w*|mostr\w*|me leva\w*)\b", c))):
            return None
        tokens = re.findall(r"[a-z0-9]+", c)
        palavras = [t for t in tokens if t not in PALAVRAS_DE_LIGACAO and not RE_VERBO.fullmatch(t)]
        return palavras, bool(RE_ONDE.search(c))

    # -- lugares conhecidos (downloads, documentos...)
    def _todos_os_lugares(self) -> dict:
        return self._lugares if self._lugares is not None else lugares_conhecidos()

    def _lugar(self, palavras: list[str]) -> tuple[str, str] | None:
        if not palavras:
            return None
        lugares = self._todos_os_lugares()
        for apelido, destino in lugares.items():
            if set(apelido.split()) == set(palavras) or " ".join(palavras) == apelido:
                return destino
        return None

    def _abrir_lugar(self, nome: str, caminho: str) -> str:
        if not caminho.startswith("shell:") and not os.path.isdir(caminho):
            return f"Não achei {nome} neste computador."
        self._abrir(caminho)
        return f"Abri {nome}."

    def escolher(self, palavras: list[str], acao) -> tuple[Pasta | None, str]:
        """Acha a pasta para uma ação que precisa dela ("arquiva na pasta da OAB"): (pasta, "") se uma é clara; senão (None, texto),
        com o aviso ou a pergunta "qual delas?". Perguntando, a resposta chama `acao(pasta)` e fala o que ela devolver."""
        lugar = self._lugar(palavras)
        if lugar:
            nome, caminho = lugar
            if caminho.startswith("shell:") or not os.path.isdir(caminho):
                return None, f"Não dá para guardar nada em {nome}."
            return Pasta(caminho, nome, tokens_do_nome(nome), 0), ""
        self.indice.garantir()
        achadas = self.indice.buscar(palavras)
        if not achadas and self.relogio() - self.indice.feito_em > 20:
            self.indice.garantir(forcar=True)
            achadas = self.indice.buscar(palavras)
        if not achadas:
            return None, f"Não achei nenhuma pasta com “{' '.join(palavras)}”."
        melhor = achadas[0][0]
        empatadas = [pasta for nota, pasta in achadas if melhor - nota < FOLGA_DE_EMPATE][:4]
        chave = " ".join(sorted(palavras))
        preferida = next((p for p in empatadas if p.caminho == self._preferidas.get(chave)), None)
        if len(empatadas) == 1 or preferida is not None:
            return (preferida or empatadas[0]), ""
        self.pergunta = Pergunta(empatadas, self.relogio() + VALIDADE_DA_PERGUNTA, chave, None, acao)
        nomes = [self.descrever(p, empatadas) for p in empatadas]
        return None, f"Achei {len(nomes)} pastas: " + "; ".join(nomes[:-1]) + f" e {nomes[-1]}. Em qual delas?"

    # -- busca
    def _abrir_busca(self, palavras: list[str], onde: bool) -> str:
        self.indice.garantir()
        achadas = self.indice.buscar(palavras)
        if not achadas and self.relogio() - self.indice.feito_em > 20:
            self.indice.garantir(forcar=True)  # pode ser uma pasta criada agora há pouco
            achadas = self.indice.buscar(palavras)
        pedido = " ".join(palavras)
        if not achadas:
            return f"Não achei nenhuma pasta com “{pedido}”."
        melhor = achadas[0][0]
        empatadas = [pasta for nota, pasta in achadas if melhor - nota < FOLGA_DE_EMPATE][:4]
        if len(empatadas) == 1:
            return self._abrir_pasta(empatadas[0], onde)
        chave = " ".join(sorted(palavras))
        preferida = next((p for p in empatadas if p.caminho == self._preferidas.get(chave)), None)
        if preferida is not None:  # da outra vez você escolheu esta: abre sem perguntar e deixa corrigir
            self._abrir(preferida.caminho)
            self.pergunta = Pergunta(empatadas, self.relogio() + VALIDADE_DA_PERGUNTA, chave, preferida)
            return f"Abri a pasta {preferida.nome}, a que você escolheu da outra vez. Se não era essa, diga “a outra”."
        self.pergunta = Pergunta(empatadas, self.relogio() + VALIDADE_DA_PERGUNTA, chave)
        nomes = [self.descrever(p, empatadas) for p in empatadas]
        return f"Achei {len(nomes)} pastas: " + "; ".join(nomes[:-1]) + f" e {nomes[-1]}. Qual delas?"

    def _abrir_pasta(self, pasta: Pasta, onde: bool = False) -> str:
        self._abrir(pasta.caminho)
        lugar = os.path.basename(os.path.dirname(pasta.caminho))
        return f"A pasta {pasta.nome} fica em {lugar}. Já abri para você." if onde else f"Abri a pasta {pasta.nome}."

    @staticmethod
    def descrever(pasta: Pasta, irmas: list) -> str:
        """O nome para falar; se outra candidata tem o mesmo nome, diz onde fica."""
        repetido = sum(1 for outra in irmas if outra.nome.lower() == pasta.nome.lower()) > 1
        return f"{pasta.nome} em {os.path.basename(os.path.dirname(pasta.caminho))}" if repetido else pasta.nome

    # -- resposta à pergunta "qual delas?"
    def _responder_pergunta(self, c: str) -> str | None:
        pergunta = self.pergunta
        palavras = [t for t in re.findall(r"[a-z0-9]+", c) if t not in LIGACAO_DA_RESPOSTA]
        if not pergunta.candidatas:  # esperava o nome da pasta
            self.pergunta = None
            if not palavras or RE_CANCELAR.search(c):
                return "Tudo bem, não abri nenhuma."
            return self._abrir_busca(palavras, False)
        if pergunta.aberta is not None:  # abriu a preferida: só reage a "não", "a outra", "errada"
            if not re.search(r"\b(nao|outra|outro|errada|errado)\b", c):
                return None
            restantes = [p for p in pergunta.candidatas if p is not pergunta.aberta]
            pergunta = self.pergunta = Pergunta(restantes, pergunta.ate, pergunta.chave)
            palavras = [p for p in palavras if p not in {"nao", "outra", "outro", "errada", "errado", "era", "essa", "esse", "isso", "nada"}]
            if not palavras:
                if len(restantes) == 1:
                    self.pergunta = None
                    self._lembrar(pergunta.chave, restantes[0])
                    return self._abrir_pasta(restantes[0])
                nomes = [self.descrever(p, restantes) for p in restantes]
                return "Então são estas: " + "; ".join(nomes[:-1]) + f" e {nomes[-1]}. Qual delas?"
        if RE_CANCELAR.search(c):
            self.pergunta = None
            return "Tudo bem, deixei como estava." if pergunta.acao is not None else "Tudo bem, não abri nenhuma."
        escolhida = None
        for palavra in palavras:
            if palavra in ORDINAIS and len(palavras) <= 3:
                posicao = ORDINAIS[palavra]
                if -len(pergunta.candidatas) <= posicao < len(pergunta.candidatas):
                    escolhida = pergunta.candidatas[posicao]
                break
        if escolhida is None and palavras:
            pontuadas = sorted(((pontuar(palavras, p) + self._bonus_do_lugar(palavras, p), p) for p in pergunta.candidatas),
                               key=lambda item: -item[0])
            if pontuadas[0][0] > 0 and (len(pontuadas) == 1 or pontuadas[0][0] - pontuadas[1][0] >= 0.05):
                escolhida = pontuadas[0][1]
        if escolhida is None:
            return None  # não era resposta: a conversa segue e a pergunta espera até vencer
        self.pergunta = None
        self._lembrar(pergunta.chave, escolhida)
        if pergunta.acao is not None:
            return pergunta.acao(escolhida)
        return self._abrir_pasta(escolhida)

    @staticmethod
    def _bonus_do_lugar(palavras: list[str], pasta: Pasta) -> float:
        """"a de downloads": a palavra bate com a pasta onde a candidata está."""
        pai = tokens_do_nome(os.path.basename(os.path.dirname(pasta.caminho)))
        return 0.5 if any(p in pai for p in palavras) else 0.0
