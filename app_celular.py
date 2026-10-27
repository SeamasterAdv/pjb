"""App do celular: um app web instalável (PWA, interface_app/) que conversa com a Beatriz de qualquer lugar.

- `CanalApp`: o canal do app, com a mesma cara do CanalWhatsApp (`receber`, `enviar`, `status`). O laço do main.py pega os
  pedidos do app como pega os do WhatsApp, e as respostas voltam por escrito para o celular, sem falar em casa.
- `Aparelhos`: o pareamento. "Beatriz, conecta meu celular" gera um código de 6 números (vale 5 min e uma tentativa certa);
  o app troca o código por uma chave longa e aleatória, que fica no celular. No PC fica só o hash (sha256) de cada chave.
- `criar_servidor`: o FastAPI com as rotas do app e a página. Escuta só em 127.0.0.1: o celular chega pelo Tailscale
  (`tailscale serve --bg 8770`), que dá HTTPS (o iPhone exige para usar o microfone) e só deixa entrar os seus aparelhos.
- `responder_pedido_do_app`: "conecta meu celular", "desconecta o celular", "o celular está conectado?".

Segurança, em camadas: a porta só existe no próprio PC; o Tailscale cifra tudo e só os seus aparelhos chegam até ela; cada
chamada precisa da chave do aparelho; os arquivos só saem pelo id que a Beatriz criou (nunca por caminho), e os que chegam
passam pelas regras do WhatsApp (sem programas, até LIMITE_DO_ARQUIVO_MB). A voz gravada no celular só é transcrita pelo
Whisper local.
"""
import hashlib
import hmac
import io
import json
import logging
import mimetypes
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime
from urllib.parse import unquote

from config import (APP_HOST, APP_PORTA, CAMINHO_APP_CELULAR, CAMINHO_DOCUMENTOS, LIMITE_DO_ARQUIVO_MB, PASTA_ARQUIVADOS,
                    PASTA_DOCUMENTOS_GERADOS, PASTA_RECEBIDOS, PASTAS_OBSIDIAN, VAULT_OBSIDIAN)

logger = logging.getLogger(__name__)

PASTA_APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "interface_app")
PASTA_VISUAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "interface_web")
ARQUIVOS_DO_VISUAL = {"visual_rede.js", "visual_ajustes.json", "vendor/three/build/three.module.min.js"}
VALIDADE_DO_CODIGO = 300.0
TENTATIVAS_DO_CODIGO = 5
CONECTADO_SEGUNDOS = 45.0  # o app busca mensagens sem parar; sem busca há mais que isto, ele está fechado
ESPERA_DAS_MENSAGENS = 25.0  # cada busca espera até isto por uma mensagem nova (long polling)
MENSAGENS_GUARDADAS = 200
LIMITE_DA_VOZ_MB = 15.0
LIMITE_DA_EDICAO_MB = 1024.0  # a tela "Editar" recebe vídeos do celular (gravados direto no disco, sem passar pela memória)
FALAS_GUARDADAS = 12  # áudios das respostas (gerados quando o app pede para falar) que ficam no disco
CABECALHO_DA_CHAVE = "X-Chave-Beatriz"
# Só arquivos destas pastas podem ser oferecidos ao celular: os que a Beatriz gerou, os que chegaram e os da biblioteca (fontes e
# só arquivos, como na janela da biblioteca), nunca as notas pessoais nem os arquivos de dados.
PASTAS_QUE_SAEM = (PASTA_DOCUMENTOS_GERADOS, PASTA_RECEBIDOS, CAMINHO_DOCUMENTOS, VAULT_OBSIDIAN, PASTA_ARQUIVADOS)
PASTAS_QUE_NAO_SAEM = tuple(p for p in (PASTAS_OBSIDIAN.get("geral"), PASTAS_OBSIDIAN.get("diario")) if p)

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")


# Imagens que o PC aceita buscar para os cartões (os pôsteres do Ingresso.com). Só por https e só destes lugares.
HOSTS_DE_IMAGEM = ("akamaihd.net", "ingresso.com")
IMAGENS_GUARDADAS = 80


def _imagem_permitida(endereco: str) -> bool:
    from urllib.parse import urlparse

    partes = urlparse(endereco)
    host = (partes.hostname or "").lower()
    return partes.scheme == "https" and any(host == h or host.endswith("." + h) for h in HOSTS_DE_IMAGEM)


def _hash(chave: str) -> str:
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()


def pode_sair(caminho: str) -> bool:
    """Se o arquivo pode ir para o celular (veja PASTAS_QUE_SAEM)."""
    return (_dentro_de(caminho, PASTAS_QUE_SAEM) and not _dentro_de(caminho, PASTAS_QUE_NAO_SAEM)
            and not caminho.lower().endswith((".json", ".jsonl")))


def _dentro_de(caminho: str, pastas) -> bool:
    real = os.path.realpath(caminho)
    for pasta in pastas:
        try:
            if os.path.commonpath([real, os.path.realpath(pasta)]) == os.path.realpath(pasta):
                return True
        except ValueError:  # outro disco
            continue
    return False


# ------------------------------------------------------------------ pareamento
class Aparelhos:
    """Os aparelhos pareados (só o hash das chaves) e o código de pareamento da vez."""

    def __init__(self, caminho: str = CAMINHO_APP_CELULAR, relogio=time.time):
        self.caminho = caminho
        self.relogio = relogio
        self._lock = threading.Lock()
        self._codigo = None  # (código, válido até, tentativas erradas)
        self._lista = self._ler()

    def _ler(self) -> list:
        try:
            with open(self.caminho, encoding="utf-8") as arquivo:
                return list(json.load(arquivo).get("aparelhos") or [])
        except FileNotFoundError:
            return []
        except Exception as exc:
            logger.warning("Não consegui ler os aparelhos pareados (%s); começando sem nenhum.", exc)
            return []

    def _salvar(self):
        os.makedirs(os.path.dirname(self.caminho) or ".", exist_ok=True)
        temporario = self.caminho + ".tmp"
        with open(temporario, "w", encoding="utf-8") as arquivo:
            json.dump({"versao": 1, "aparelhos": self._lista}, arquivo, ensure_ascii=False, indent=1)
        os.replace(temporario, self.caminho)

    def novo_codigo(self) -> str:
        codigo = f"{secrets.randbelow(10 ** 6):06d}"
        with self._lock:
            self._codigo = (codigo, self.relogio() + VALIDADE_DO_CODIGO, 0)
        return codigo

    def parear(self, codigo: str, nome: str = "") -> str | None:
        """Troca o código pela chave do aparelho (que só o celular guarda). None se o código não vale."""
        codigo = re.sub(r"\D", "", codigo or "")
        with self._lock:
            if self._codigo is None or self.relogio() > self._codigo[1]:
                self._codigo = None
                return None
            certo, ate, erros = self._codigo
            if not hmac.compare_digest(codigo, certo):
                erros += 1
                self._codigo = None if erros >= TENTATIVAS_DO_CODIGO else (certo, ate, erros)
                return None
            self._codigo = None  # vale uma vez só
            chave = secrets.token_urlsafe(32)
            nome = re.sub(r"[^\w .()-]", "", nome or "")[:40].strip() or "Celular"
            self._lista.append({"id": secrets.token_hex(4), "nome": nome, "hash": _hash(chave),
                                "criado": datetime.fromtimestamp(self.relogio()).strftime("%Y-%m-%d %H:%M"), "visto": 0})
            self._salvar()
        return chave

    def autorizado(self, chave: str) -> dict | None:
        if not chave:
            return None
        procurado = _hash(chave)
        with self._lock:
            for aparelho in self._lista:
                if hmac.compare_digest(aparelho.get("hash", ""), procurado):
                    aparelho["visto"] = self.relogio()
                    return dict(aparelho)
        return None

    def esquecer(self, aparelho_id: str) -> bool:
        with self._lock:
            antes = len(self._lista)
            self._lista = [a for a in self._lista if a.get("id") != aparelho_id]
            if len(self._lista) == antes:
                return False
            self._salvar()
        return True

    def esquecer_todos(self) -> int:
        with self._lock:
            quantos = len(self._lista)
            self._lista = []
            self._codigo = None
            self._salvar()
        return quantos

    def lista(self) -> list:
        with self._lock:
            return [dict(a) for a in self._lista]


# ------------------------------------------------------------------ canal
class CanalApp:
    """O canal do app: pedidos e arquivos que chegam do celular, respostas e arquivos que voltam para ele."""

    def __init__(self, relogio=time.time):
        self.relogio = relogio
        self._entrada = queue.Queue()  # comandos (texto) ou arquivos (dict), como o CanalWhatsApp
        self._mensagens = deque(maxlen=MENSAGENS_GUARDADAS)
        self._proximo = 1
        self._mudou = threading.Condition()
        self._arquivos = {}  # id -> caminho (só o que a Beatriz mandou ou ofereceu)
        self._imagens = {}  # id -> endereço (pôsteres dos cartões: o PC busca e repassa)
        self._visto = 0.0

    # -- o lado do main.py (o mesmo do CanalWhatsApp)
    def receber(self):
        """Próxima coisa que chegou do app: o texto de um pedido, ou um dict {"tipo": "arquivo", ...}. "" se não houver."""
        try:
            return self._entrada.get_nowait()
        except queue.Empty:
            return ""

    def enviar(self, texto: str = "", arquivos=(), tipo: str = "resposta"):
        anexos = [self.oferecer(caminho) for caminho in arquivos]
        self._publicar({"tipo": tipo, "texto": texto or "", "arquivos": [a for a in anexos if a]})

    def status(self) -> dict:
        conectado = self.relogio() - self._visto < CONECTADO_SEGUNDOS
        return {"status": "conectado" if conectado else "desconectado"}

    def cartao(self, tipo: str, dados: dict):
        """Um cartão visual na conversa ("clima", "filmes", "resumo"), além da resposta em texto.

        Os pôsteres viram ids de /api/imagem (o PC busca e repassa) e o Word do resumo vira um anexo."""
        dados = json.loads(json.dumps(dados, default=str))  # só tipos simples, cópia independente
        for filme in dados.get("filmes", []) + dados.get("pre_venda", []):
            filme["poster"] = self._registrar_imagem(filme.get("poster", ""))
        arquivo = dados.pop("arquivo", "") if tipo == "resumo" else ""
        anexo = self.oferecer(arquivo) if arquivo else None
        self._publicar({"tipo": "cartao", "cartao": tipo, "dados": dados, "texto": "", "arquivos": [anexo] if anexo else []})

    def _registrar_imagem(self, endereco: str) -> str:
        if not endereco or not _imagem_permitida(endereco):
            return ""
        with self._mudou:
            existente = next((i for i, e in self._imagens.items() if e == endereco), None)
            imagem_id = existente or secrets.token_urlsafe(9)
            self._imagens[imagem_id] = endereco
        return imagem_id

    def endereco_da_imagem(self, imagem_id: str) -> str | None:
        with self._mudou:
            return self._imagens.get(imagem_id)

    # -- o lado do celular
    def pedido(self, texto: str, por_voz: bool = False):
        self._publicar({"tipo": "voce", "texto": texto, "por_voz": por_voz, "arquivos": []})
        self._entrada.put(texto)

    def arquivo_recebido(self, caminho: str, nome: str, legenda: str = ""):
        self._publicar({"tipo": "voce", "texto": legenda, "arquivos": [self.oferecer(caminho, nome)]})
        self._entrada.put({"tipo": "arquivo", "caminho": caminho, "nome": nome, "legenda": legenda, "origem": "celular"})

    def mensagens(self, depois: int = 0, espera: float = 0.0) -> list:
        """As mensagens com id maior que `depois`; espera até `espera` segundos por uma nova (long polling)."""
        self._visto = self.relogio()
        limite = time.monotonic() + espera
        with self._mudou:
            while True:
                novas = [dict(m) for m in self._mensagens if m["id"] > depois]
                restante = limite - time.monotonic()
                if novas or restante <= 0:
                    self._visto = self.relogio()
                    return novas
                self._mudou.wait(min(restante, 1.0))

    def mensagem(self, mensagem_id: int) -> dict | None:
        with self._mudou:
            return next((dict(m) for m in self._mensagens if m["id"] == mensagem_id), None)

    def oferecer(self, caminho: str, nome: str = "") -> dict | None:
        """Registra um arquivo que pode ser baixado pelo celular (só das pastas permitidas) e devolve {id, nome, tamanho}."""
        caminho = str(caminho)
        if not os.path.isfile(caminho) or not pode_sair(caminho):
            logger.warning("Arquivo fora das pastas permitidas não vai para o celular: %s", caminho)
            return None
        with self._mudou:
            existente = next((i for i, c in self._arquivos.items() if c == caminho), None)
            arquivo_id = existente or secrets.token_urlsafe(12)
            self._arquivos[arquivo_id] = caminho
        return {"id": arquivo_id, "nome": nome or os.path.basename(caminho), "tamanho": os.path.getsize(caminho)}

    def caminho_do_arquivo(self, arquivo_id: str) -> str | None:
        with self._mudou:
            return self._arquivos.get(arquivo_id)

    def _publicar(self, mensagem: dict):
        with self._mudou:
            mensagem.update(id=self._proximo, quando=self.relogio())
            self._proximo += 1
            self._mensagens.append(mensagem)
            self._mudou.notify_all()


# ------------------------------------------------------------------ áudio
def decodificar_audio(dados: bytes):
    """O áudio que o celular gravou (mp4/AAC no iPhone, webm em outros) em 16 kHz mono float32, sem gravar em disco."""
    import av
    import numpy as np

    reamostrador = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=16000)
    partes = []
    with av.open(io.BytesIO(dados)) as entrada:
        for quadro in entrada.decode(audio=0):
            partes += [q.to_ndarray().reshape(-1) for q in reamostrador.resample(quadro)]
    partes += [q.to_ndarray().reshape(-1) for q in reamostrador.resample(None)]
    return np.concatenate(partes).astype(np.float32) if partes else np.zeros(0, dtype=np.float32)


# ------------------------------------------------------------------ recursos do PC
_MONITOR = {"objeto": None}


def recursos_do_pc() -> dict:
    """CPU, memória e disco em %, pela API do Windows (o mesmo medidor do painel; sem o psutil, que não vem instalado)."""
    valores = {}
    try:
        if _MONITOR["objeto"] is None:
            from painel_hud import MonitorRecursos

            _MONITOR["objeto"] = MonitorRecursos()
        monitor = _MONITOR["objeto"]
        cpu = monitor.cpu()
        ram = monitor.ram()[0]
        if cpu is not None:
            valores["cpu"] = round(cpu)
        if ram is not None:
            valores["ram"] = round(ram)
    except Exception as exc:
        logger.debug("Recursos do PC indisponíveis: %s", exc)
    try:
        disco = shutil.disk_usage(os.path.dirname(os.path.abspath(__file__)))
        valores["disco"] = round(100 * disco.used / disco.total)
    except OSError:
        pass
    return valores


# ------------------------------------------------------------------ Tailscale
_TAILSCALE = {"ate": 0.0, "valor": None}


def _executavel_tailscale() -> str | None:
    candidatos = [shutil.which("tailscale"), os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Tailscale", "tailscale.exe")]
    return next((c for c in candidatos if c and os.path.isfile(c)), None)


def situacao_tailscale() -> dict:
    """{"instalado", "endereco" (https://pc.tailnet.ts.net ou ""), "servindo" (se o `tailscale serve` aponta para o app)}.
    Guardado por 1 min: cada consulta roda o tailscale.exe."""
    if time.monotonic() < _TAILSCALE["ate"] and _TAILSCALE["valor"] is not None:
        return _TAILSCALE["valor"]
    valor = {"instalado": False, "endereco": "", "servindo": False}
    executavel = _executavel_tailscale()
    if executavel:
        valor["instalado"] = True
        opcoes = {"capture_output": True, "text": True, "timeout": 5, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
        try:
            estado = json.loads(subprocess.run([executavel, "status", "--json"], **opcoes).stdout or "{}")
            nome = ((estado.get("Self") or {}).get("DNSName") or "").rstrip(".")
            valor["endereco"] = f"https://{nome}" if nome else ""
            servindo = subprocess.run([executavel, "serve", "status", "--json"], **opcoes).stdout or ""
            valor["servindo"] = f":{APP_PORTA}" in servindo
        except Exception as exc:
            logger.debug("Não consegui consultar o Tailscale: %s", exc)
    _TAILSCALE.update(ate=time.monotonic() + 60, valor=valor)
    return valor


# ------------------------------------------------------------------ pedidos falados
def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", (texto or "").lower())
    return " ".join(re.findall(r"\w+", "".join(c for c in sem_acento if not unicodedata.combining(c))))


APARELHO = r"(o |meu |o meu |um )?(iphone|celular|telefone|app|aplicativo)\b"
RE_CONECTAR = re.compile(r"\b(conect\w*|vincul\w*|parei\w*|parea\w*|instal\w*|configur\w*)\b (o |a )?(app |aplicativo )?(no |pro |para o )?" + APARELHO)
RE_DESCONECTAR = re.compile(r"\b(desconect\w*|desvincul\w*|esquec\w*|remov\w*|bloquei\w*)\b (o |a )?" + APARELHO)
RE_SITUACAO = re.compile(r"\b(iphone|celular|app|aplicativo)\b.{0,20}\b(conectad\w*|paread\w*|online)\b")


def _codigo_falado(codigo: str) -> str:
    return f"{codigo[:3]} {codigo[3:]}"


def responder_pedido_do_app(texto: str, aparelhos: Aparelhos, canal: CanalApp | None = None) -> str | None:
    """"Conecta meu celular" (gera o código), "desconecta o celular" (esquece todos), "o celular está conectado?"."""
    c = _normalizar(texto)
    if RE_DESCONECTAR.search(c):
        quantos = aparelhos.esquecer_todos()
        return (f"Pronto, desconectei {'o aparelho' if quantos == 1 else f'os {quantos} aparelhos'}: para usar o app de novo, "
                "peça um código novo." if quantos else "Nenhum celular estava conectado.")
    if RE_CONECTAR.search(c):
        codigo = aparelhos.novo_codigo()
        tailscale = situacao_tailscale()
        if not tailscale["instalado"]:
            return (f"O código é {_codigo_falado(codigo)} e vale 5 minutos. Mas antes o iPhone precisa chegar até o PC com segurança: "
                    "instale o Tailscale no PC e no iPhone, entre com a mesma conta e rode no PC o comando "
                    f"tailscale serve --bg {APP_PORTA}. Depois peça o código de novo que eu digo o endereço.")
        endereco = tailscale["endereco"] or "o endereço do seu PC no Tailscale"
        falta_servir = "" if tailscale["servindo"] else f" Antes, rode uma vez no PC: tailscale serve --bg {APP_PORTA}."
        return (f"No iPhone, abra {endereco} no Safari e digite o código {_codigo_falado(codigo)}. Ele vale 5 minutos."
                f"{falta_servir} Depois toque em Compartilhar e em Adicionar à Tela de Início.")
    if RE_SITUACAO.search(c):
        lista = aparelhos.lista()
        if not lista:
            return "Nenhum celular está pareado. Diga “conecta meu celular” para parear."
        aberto = canal is not None and canal.status().get("status") == "conectado"
        nomes = ", ".join(a["nome"] for a in lista)
        return f"Pareado{'s' if len(lista) > 1 else ''}: {nomes}. O app {'está aberto agora' if aberto else 'não está aberto agora'}."
    return None


# ------------------------------------------------------------------ servidor
class _Falas:
    """Áudio das respostas, gerado só quando o app pede (com a voz escolhida), e guardado para não gerar duas vezes."""

    def __init__(self, gerar, limpar):
        self.gerar, self.limpar = gerar, limpar
        self._feitas = {}  # id da mensagem -> caminho
        self._lock = threading.Lock()

    def caminho(self, mensagem: dict) -> str:
        with self._lock:
            pronto = self._feitas.get(mensagem["id"])
            if pronto and os.path.exists(pronto):
                return pronto
        caminho = self.gerar(self.limpar(mensagem["texto"]))
        with self._lock:
            self._feitas[mensagem["id"]] = caminho
            for antigo in sorted(self._feitas)[:-FALAS_GUARDADAS]:
                velho = self._feitas.pop(antigo)
                try:
                    os.remove(velho)
                except OSError:
                    pass
        return caminho


def _buscar_imagem(endereco: str) -> tuple[str, bytes]:
    """(tipo, conteúdo) de uma imagem permitida; sem seguir redirecionamentos e com até 5 MB."""
    import requests

    if not _imagem_permitida(endereco):
        raise ValueError("endereço não permitido")
    resposta = requests.get(endereco, timeout=8, allow_redirects=False)
    resposta.raise_for_status()
    tipo = resposta.headers.get("Content-Type", "").split(";")[0].strip()
    if not tipo.startswith("image/") or len(resposta.content) > 5 * 1024 * 1024:
        raise ValueError("não é uma imagem")
    return tipo, resposta.content


def criar_servidor(canal: CanalApp, aparelhos: Aparelhos, dados=None, transcrever=None, gerar_fala=None,
                   limpar=lambda texto: texto, pasta_recebidos: str = PASTA_RECEBIDOS, limite_mb: float = LIMITE_DO_ARQUIVO_MB,
                   agenda_semana=None, buscar_imagem=_buscar_imagem, biblioteca=None, recursos=recursos_do_pc, controle=None,
                   editar=None, situacao_da_edicao=None, limite_da_edicao_mb: float = LIMITE_DA_EDICAO_MB):
    """O app FastAPI. `dados()` -> dict do painel do celular; `transcrever(amostras)` -> (texto, confiança);
    `gerar_fala(texto)` -> caminho do áudio; `agenda_semana(deslocamento)` -> a semana da agenda;
    `buscar_imagem(endereço)` -> (tipo, bytes) dos pôsteres dos cartões; `controle` -> o AgenteControle (TV e ar);
    `editar(caminho, pedido)` e `situacao_da_edicao(tarefa)` -> a tela "Editar" (AgenteCraft.editar_do_app e .situacao)."""
    from collections import OrderedDict

    from fastapi import Depends, FastAPI, Header, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse, Response
    from fastapi.staticfiles import StaticFiles

    from fontes import EXTENSOES_BLOQUEADAS, caminho_livre, nome_seguro

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    falas = _Falas(gerar_fala, limpar) if gerar_fala else None
    imagens = OrderedDict()  # id -> (tipo, conteúdo), as últimas IMAGENS_GUARDADAS

    @app.middleware("http")
    async def cabecalhos(request: Request, chamar):
        resposta = await chamar(request)
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["Referrer-Policy"] = "no-referrer"
        resposta.headers["X-Frame-Options"] = "DENY"
        resposta.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob: data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        if "cache-control" not in resposta.headers:  # os pôsteres pedem para ficar guardados no celular
            resposta.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
        return resposta

    def aparelho(chave: str = Header("", alias=CABECALHO_DA_CHAVE)) -> dict:
        encontrado = aparelhos.autorizado(chave)
        if not encontrado:
            raise HTTPException(401, "Este aparelho não está pareado. Diga “Beatriz, conecta meu celular” para ganhar um código.")
        return encontrado

    @app.post("/api/parear")
    async def parear(request: Request):
        corpo = await request.json()
        chave = aparelhos.parear(str(corpo.get("codigo", "")), str(corpo.get("nome", "")))
        if not chave:
            raise HTTPException(403, "Código errado ou vencido. Peça um novo: “Beatriz, conecta meu celular”.")
        logger.info("Novo aparelho pareado com o app do celular.")
        return {"chave": chave}

    @app.post("/api/esquecer")
    def esquecer(quem: dict = Depends(aparelho)):
        """"Desconectar este celular": a chave dele deixa de valer."""
        aparelhos.esquecer(quem["id"])
        return {"ok": True}

    @app.get("/api/painel")
    def painel(quem: dict = Depends(aparelho)):
        resultado = dict(dados() if dados else {})
        for tarefa in resultado.get("tarefas", []):
            tarefa["arquivos"] = [a for a in (canal.oferecer(c) for c in tarefa.get("arquivos", [])) if a]
        resultado["aparelho"] = quem.get("nome", "")
        return resultado

    @app.get("/api/agenda")
    def agenda(deslocamento: int = 0, quem: dict = Depends(aparelho)):
        if agenda_semana is None:
            raise HTTPException(503, "A agenda não está disponível.")
        return agenda_semana(max(-52, min(52, deslocamento)))

    @app.post("/api/pedido")
    async def pedido(request: Request, quem: dict = Depends(aparelho)):
        texto = str((await request.json()).get("texto", "")).strip()[:2000]
        if not texto:
            raise HTTPException(400, "Pedido vazio.")
        canal.pedido(texto)
        return {"ok": True}

    @app.get("/api/mensagens")
    def mensagens(depois: int = 0, espera: float = 0.0, quem: dict = Depends(aparelho)):
        return {"mensagens": canal.mensagens(depois, max(0.0, min(espera, ESPERA_DAS_MENSAGENS)))}

    @app.post("/api/voz")
    async def voz(request: Request, quem: dict = Depends(aparelho)):
        dados_audio = await request.body()
        if not dados_audio or len(dados_audio) > LIMITE_DA_VOZ_MB * 1024 * 1024:
            raise HTTPException(413, "Áudio vazio ou grande demais.")
        if transcrever is None:
            raise HTTPException(503, "A transcrição não está disponível.")
        try:
            amostras = decodificar_audio(dados_audio)
        except Exception as exc:
            logger.warning("Áudio do celular ilegível: %s", exc)
            raise HTTPException(400, "Não consegui ler esse áudio.")
        from starlette.concurrency import run_in_threadpool

        texto, confianca = await run_in_threadpool(transcrever, amostras)
        texto = (texto or "").strip()
        if texto:
            canal.pedido(texto, por_voz=True)
        return {"texto": texto, "confianca": confianca}

    @app.post("/api/arquivo")
    async def arquivo(request: Request, quem: dict = Depends(aparelho)):
        nome = nome_seguro(unquote(request.headers.get("X-Nome", "")) or "arquivo")
        legenda = unquote(request.headers.get("X-Legenda", ""))[:500]
        if os.path.splitext(nome)[1].lower() in EXTENSOES_BLOQUEADAS:
            raise HTTPException(415, f"Não guardo arquivos {os.path.splitext(nome)[1].lower()} (podem ser programas).")
        conteudo = await request.body()
        if not conteudo:
            raise HTTPException(400, "O arquivo chegou vazio.")
        if len(conteudo) > limite_mb * 1024 * 1024:
            raise HTTPException(413, f"O arquivo passa de {limite_mb:g} MB.")
        destino = caminho_livre(os.path.join(pasta_recebidos, datetime.now().strftime("%Y-%m-%d")), nome)
        with open(destino, "wb") as saida:
            saida.write(conteudo)
        canal.arquivo_recebido(destino, os.path.basename(destino), legenda)
        return {"ok": True, "nome": os.path.basename(destino)}

    @app.get("/api/arquivo/{arquivo_id}")
    def baixar(arquivo_id: str, quem: dict = Depends(aparelho)):
        caminho = canal.caminho_do_arquivo(arquivo_id)
        if not caminho or not os.path.isfile(caminho) or not pode_sair(caminho):
            raise HTTPException(404, "Esse arquivo não está disponível.")
        return FileResponse(caminho, filename=os.path.basename(caminho))

    @app.get("/api/imagem/{imagem_id}")
    def imagem(imagem_id: str, quem: dict = Depends(aparelho)):
        """Pôster de um cartão: o PC busca (só os endereços que ele mesmo pôs no cartão) e repassa."""
        if imagem_id not in imagens:
            endereco = canal.endereco_da_imagem(imagem_id)
            if not endereco:
                raise HTTPException(404, "Essa imagem não está disponível.")
            try:
                imagens[imagem_id] = buscar_imagem(endereco)
            except Exception as exc:
                logger.debug("Pôster indisponível: %s", exc)
                raise HTTPException(502, "Não consegui buscar a imagem.")
            while len(imagens) > IMAGENS_GUARDADAS:
                imagens.popitem(last=False)
        tipo, conteudo = imagens[imagem_id]
        return Response(conteudo, media_type=tipo, headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/recursos")
    def recursos_agora(quem: dict = Depends(aparelho)):
        """CPU, memória e disco: a tela Sistema pergunta a cada poucos segundos para desenhar o gráfico."""
        return recursos() if recursos else {}

    # -- biblioteca de fontes (organizar à mão): as mesmas regras da janela do painel (biblioteca_fontes.py)
    @app.get("/api/biblioteca")
    def biblioteca_listar(quem: dict = Depends(aparelho)):
        if biblioteca is None:
            raise HTTPException(503, "A biblioteca não está disponível.")
        return biblioteca.listar()

    @app.post("/api/biblioteca/{acao}")
    async def biblioteca_acao(acao: str, request: Request, quem: dict = Depends(aparelho)):
        from starlette.concurrency import run_in_threadpool

        from biblioteca_fontes import resultado

        if biblioteca is None:
            raise HTTPException(503, "A biblioteca não está disponível.")
        if acao == "adicionar":
            conteudo = await request.body()
            nome, pasta = unquote(request.headers.get("X-Nome", "")), unquote(request.headers.get("X-Pasta", ""))
            return await run_in_threadpool(resultado, biblioteca.adicionar, nome, pasta, conteudo=conteudo)
        try:
            corpo = await request.json()
            pedidos = {
                "mover": (biblioteca.mover, corpo.get("arquivo"), corpo.get("pasta")),
                "renomear": (biblioteca.renomear, corpo.get("arquivo"), corpo.get("nome")),
                "pasta": (biblioteca.criar_pasta, corpo.get("pasta"), corpo.get("nome")),
                "apagar_pasta": (biblioteca.apagar_pasta, corpo.get("pasta")),
                "so_arquivo": (biblioteca.so_arquivo, corpo.get("arquivo")),
                "tirar": (biblioteca.so_arquivo, corpo.get("arquivo")),  # nome antigo
                "virar_fonte": (biblioteca.virar_fonte, corpo.get("arquivo")),
                "apagar": (biblioteca.apagar, corpo.get("arquivo")),
            }
            if acao == "lote":  # {"acao", "arquivos": [ids], "pasta"}
                ids = corpo.get("arquivos")
                if not isinstance(ids, list):
                    raise ValueError
                return await run_in_threadpool(resultado, biblioteca.em_lote, str(corpo.get("acao") or ""),
                                               [str(i) for i in ids], str(corpo.get("pasta") or ""))
        except (ValueError, AttributeError):
            raise HTTPException(400, "Pedido inválido.")
        if acao not in pedidos:
            raise HTTPException(404, "Ação desconhecida.")
        funcao, *argumentos = pedidos[acao]
        return await run_in_threadpool(resultado, funcao, *(str(a or "") for a in argumentos))

    @app.get("/api/biblioteca-arquivo")
    def biblioteca_baixar(id: str = "", quem: dict = Depends(aparelho)):
        from biblioteca_fontes import ErroDaBiblioteca

        if biblioteca is None:
            raise HTTPException(503, "A biblioteca não está disponível.")
        try:
            caminho = biblioteca.caminho_para_abrir(id)
        except ErroDaBiblioteca as exc:
            raise HTTPException(404, str(exc))
        return FileResponse(caminho, filename=os.path.basename(caminho))

    # -- a tela "Editar": uma foto, um vídeo ou um arquivo para converter e o que fazer com ele (apps da ArtCraft, no PC)
    def _com_anexos(situacao: dict) -> dict:
        situacao = dict(situacao)
        situacao["arquivos"] = [a for a in (canal.oferecer(c) for c in situacao.get("arquivos", [])) if a]
        return situacao

    @app.post("/api/editar")
    async def editar_no_pc(request: Request, quem: dict = Depends(aparelho)):
        from starlette.concurrency import run_in_threadpool

        if editar is None:
            raise HTTPException(503, "A edição não está disponível.")
        nome = nome_seguro(unquote(request.headers.get("X-Nome", "")) or "arquivo")
        if os.path.splitext(nome)[1].lower() in EXTENSOES_BLOQUEADAS:
            raise HTTPException(415, f"Não guardo arquivos {os.path.splitext(nome)[1].lower()} (podem ser programas).")
        try:
            pedido = json.loads(unquote(request.headers.get("X-Edicao", "")) or "{}")
        except ValueError:
            raise HTTPException(400, "Pedido de edição inválido.")
        if not isinstance(pedido, dict):
            raise HTTPException(400, "Pedido de edição inválido.")
        destino = caminho_livre(os.path.join(pasta_recebidos, datetime.now().strftime("%Y-%m-%d")), nome)
        tamanho, limite = 0, limite_da_edicao_mb * 1024 * 1024
        with open(destino, "wb") as saida:  # direto no disco: um vídeo do celular pode ter centenas de MB
            async for parte in request.stream():
                tamanho += len(parte)
                if tamanho > limite:
                    break
                saida.write(parte)
        if not tamanho or tamanho > limite:
            os.remove(destino)
            raise HTTPException(413 if tamanho else 400,
                                f"O arquivo passa de {limite_da_edicao_mb:g} MB." if tamanho else "O arquivo chegou vazio.")
        return _com_anexos(await run_in_threadpool(editar, destino, pedido))

    @app.get("/api/editar/{tarefa}")
    def situacao_da_edicao_no_pc(tarefa: str, quem: dict = Depends(aparelho)):
        situacao = situacao_da_edicao(tarefa) if situacao_da_edicao else None
        if situacao is None:
            raise HTTPException(404, "Essa edição não existe (o PC pode ter reiniciado).")
        return _com_anexos(situacao)

    # -- controle remoto (TV e ar pelo Ekaza): os botões da tela vão direto ao agente, sem passar pela conversa
    @app.get("/api/controle")
    def controle_painel(quem: dict = Depends(aparelho)):
        if controle is None:
            raise HTTPException(503, "O controle remoto não está disponível.")
        return controle.painel_do_app()

    @app.post("/api/controle")
    async def controle_comando(request: Request, quem: dict = Depends(aparelho)):
        from starlette.concurrency import run_in_threadpool

        if controle is None:
            raise HTTPException(503, "O controle remoto não está disponível.")
        try:
            corpo = await request.json()
        except ValueError:
            raise HTTPException(400, "Pedido inválido.")
        if not isinstance(corpo, dict):
            raise HTTPException(400, "Pedido inválido.")
        return await run_in_threadpool(controle.comando_do_app, corpo)  # o aparelho pode levar alguns segundos

    @app.get("/api/fala/{mensagem_id}")
    def fala(mensagem_id: int, quem: dict = Depends(aparelho)):
        mensagem = canal.mensagem(mensagem_id)
        if falas is None or not mensagem or mensagem.get("tipo") == "voce" or not mensagem.get("texto"):
            raise HTTPException(404, "Nada para falar.")
        try:
            caminho = falas.caminho(mensagem)
        except Exception as exc:
            logger.warning("Não consegui gerar a fala para o celular: %s", exc)
            raise HTTPException(503, "Não consegui gerar a fala agora.")
        return FileResponse(caminho, media_type="audio/wav" if caminho.lower().endswith(".wav") else "audio/mpeg")

    @app.exception_handler(HTTPException)
    async def erro(request: Request, exc: HTTPException):
        return JSONResponse({"erro": exc.detail}, status_code=exc.status_code)

    # O visual em rede do painel (a esfera e a montanha): só estes arquivos de interface_web, pelo mesmo caminho
    # relativo que o visual_rede.js usa para achar o Three.js.
    @app.get("/visual/{caminho:path}")
    def visual(caminho: str):
        if caminho not in ARQUIVOS_DO_VISUAL:
            raise HTTPException(404, "Não encontrado.")
        tipo = "application/json" if caminho.endswith(".json") else "text/javascript"
        return FileResponse(os.path.join(PASTA_VISUAL, *caminho.split("/")), media_type=tipo)

    app.mount("/", StaticFiles(directory=PASTA_APP, html=True), name="app")
    return app


def iniciar_servidor(app, host: str = APP_HOST, porta: int = APP_PORTA):
    """Sobe o servidor numa thread (daemon). Se a porta estiver ocupada, só avisa."""
    import uvicorn

    servidor = uvicorn.Server(uvicorn.Config(app, host=host, port=porta, log_level="warning", access_log=False))

    def rodar():
        try:
            servidor.run()
        except SystemExit:
            logger.warning("O app do celular não subiu: a porta %s já está em uso (APP_PORTA no .env).", porta)
        except Exception as exc:
            logger.warning("O app do celular parou: %s", exc)

    threading.Thread(target=rodar, name="app-celular", daemon=True).start()
    logger.info("App do celular em http://%s:%s (no iPhone, pelo Tailscale).", host, porta)
    return servidor


# ------------------------------------------------------------------ demonstração
def _controle_de_mentira(pasta: str):
    """O agente do controle remoto com uma TV Sony e um ar Daikin de mentira (só esperam um pouco), para a tela de controle."""
    from agente_controle import AgenteControle
    from controle_remoto import RegistroDoControle

    class Local:
        def enviar(self, codigos, intervalo=0):
            time.sleep(0.25)

        def conferir(self):
            return "192.168.0.50"

        def tipo_conhecido(self):
            return True

    class Nuvem:
        def controles(self):
            return [{"remote_id": "tv", "remote_name": "TV", "category_id": 2, "brand_name": "Sony"},
                    {"remote_id": "ar", "remote_name": "Ar", "category_id": 5, "brand_name": "Daikin"}]

        def teclas(self, remote_id):
            return [{"key": k, "key_id": i} for i, k in enumerate(["Power", "OK", "Up", "Down", "Left", "Right", "Back", "Home",
                                                                   "input", "Menu", "play"])]

        def faixas_do_ar(self, remote_id):
            com_temperatura = {"temps": [18, 30], "ventos": [0, 1, 2, 3]}
            return {"0": com_temperatura, "1": com_temperatura, "2": com_temperatura, "3": {"temps": None, "ventos": [0, 1, 2, 3]},
                    "4": {"temps": None, "ventos": [0]}}

        def apertar(self, remote_id, category_id, tecla, vezes=1):
            time.sleep(0.6)

        def ar(self, remote_id, ligado, modo="frio", temperatura=23, ventilacao="auto", com_temperatura=True):
            time.sleep(0.8)

    return AgenteControle(RegistroDoControle(os.path.join(pasta, "controle.json")), local=Local(), nuvem=Nuvem())


def _demonstracao(porta: int):
    """Sobe o app com uma Beatriz de mentira (ecoa os pedidos), para ver e ajustar a interface no navegador."""
    import tempfile

    pasta = tempfile.mkdtemp(prefix="beatriz_app_demo_")
    canal, aparelhos = CanalApp(), Aparelhos(os.path.join(pasta, "aparelhos.json"))
    agora = time.time()

    clima = {"lugar": "Plano Piloto", "temperatura": 28, "sensacao": 30, "codigo": 2, "descricao": "parcialmente nublado",
             "umidade": 41, "vento": 9,
             "dias": [{"data": time.strftime("%Y-%m-%d", time.localtime(agora + 86400 * k)), "codigo": c, "minima": mi, "maxima": ma,
                       "chuva": ch} for k, (c, mi, ma, ch) in enumerate([(2, 18, 29, 20), (61, 17, 25, 80), (0, 16, 30, 0)])]}

    def dados():
        return {
            "nome": "Felipe",
            "clima": clima,
            "hoje": {"titulo": "Hoje", "itens": [{"hora": "14h", "titulo": "Dentista", "categoria": "saude"},
                                                 {"hora": "19h30", "titulo": "Revisão OAB: processo civil", "categoria": "estudo"}],
                     "tarefas": ["Pagar o condomínio"]},
            "proximo": "dentista às 14h",
            "tarefas": [{"id": "a1", "pedido": "ficha de estudo de responsabilidade civil do Estado", "status": "executando",
                         "etapa": "escrevendo as fichas (3 de 8 prontas)", "etapa_indice": 1,
                         "plano": ["biblioteca", "estudos", "critico", "relator"],
                         "arquivos": [], "criada_em": agora - 120},
                        {"id": "a2", "pedido": "PDF sobre a reforma tributária", "status": "concluída", "etapa": "relator",
                         "plano": ["pesquisa", "documento", "critico", "relator"], "arquivos": [], "criada_em": agora - 3600}],
            "sistemas": {"ia_local": "online", "whatsapp": "conectado", "biblioteca": "pronta", "cpu": 23, "ram": 61, "disco": 71,
                         "voz": "Thalita +15%", "escuta": "sempre", "fontes": {"lidas": 2, "total": 6},
                         "nuvem": [{"nome": "Claude", "texto": "3 usos hoje", "estado": "ativo"},
                                   {"nome": "Gemini", "texto": "em pausa · 2 min", "estado": "pausado"}]},
        }

    def responder():
        while True:
            pedido = canal.receber()
            if isinstance(pedido, dict):
                canal.enviar(f"Recebi “{pedido['nome']}”. O que faço com ele? Posso virar fonte, arquivar, imprimir ou ler.")
            elif pedido:
                time.sleep(0.8)
                texto = pedido.lower()
                if "tempo" in texto or "clima" in texto:
                    canal.cartao("clima", clima)
                    canal.enviar("Agora no Plano Piloto: 28°C, parcialmente nublado. Amanhã deve chover à tarde.")
                elif "filme" in texto or "cartaz" in texto:
                    titulos = [("Verity", "drama"), ("Street Fighter", "ação"), ("Tadeo", "animação"), ("Outra Mamãe", "terror")]
                    canal.cartao("filmes", {"cidade": "Brasília", "filmes": [
                        {"titulo": t, "genero": g, "poster": f"https://ingresso-a.akamaihd.net/demo/{i}.jpg", "estreia_hoje": i == 1}
                        for i, (t, g) in enumerate(titulos)], "pre_venda": [{"titulo": "Vingadores: Doutor Destino", "estreia": "16 de dezembro"}]})
                    canal.enviar("Em Brasília há 41 filmes em cartaz. Destaque para Street Fighter, que estreia hoje.")
                elif "resum" in texto:
                    canal.cartao("resumo", {"tema": "reforma tributária", "fala": "A reforma troca cinco impostos sobre o consumo por dois.",
                                            "secoes": {"o que e": "Uma emenda que simplifica os impostos sobre o consumo.",
                                                       "contexto": "Aprovada em 2023, com transição de 2026 a 2033.",
                                                       "pontos principais": "- CBS federal\n- IBS de estados e municípios\n- Imposto seletivo"},
                                            "fontes": ["Wikipédia", "Agência Senado"]})
                    canal.enviar("Pronto, o resumo está aí em cima.")
                else:
                    canal.enviar(f"(Demonstração) Você pediu: {pedido}. Na Beatriz de verdade, isto passa pelo mesmo caminho do WhatsApp.")
            time.sleep(0.2)

    def poster_de_mentira(endereco):
        from PIL import Image, ImageDraw

        indice = int(endereco.rsplit("/", 1)[-1].split(".")[0])
        cores = [(106, 68, 166), (27, 79, 140), (140, 42, 58), (201, 143, 30)]
        imagem = Image.new("RGB", (200, 300), cores[indice % 4])
        desenho = ImageDraw.Draw(imagem)
        for y in range(0, 300, 6):
            desenho.line([(0, y), (200, y)], fill=tuple(min(255, c + y // 6) for c in cores[indice % 4]))
        saida = io.BytesIO()
        imagem.save(saida, "PNG")
        return "image/png", saida.getvalue()

    def semana(deslocamento):
        from datetime import date, timedelta

        segunda = date.today() - timedelta(days=date.today().weekday()) + timedelta(weeks=deslocamento)
        exemplos = {0: [("7h", "Academia", True)], 2: [("14h", "Dentista", False), ("19h30", "Revisão OAB", False)],
                    4: [("10h", "Reunião do escritório", False)]}
        dias = [{"data": (segunda + timedelta(days=k)).isoformat(), "nome": nome, "numero": (segunda + timedelta(days=k)).day,
                 "hoje": segunda + timedelta(days=k) == date.today(), "passado": segunda + timedelta(days=k) < date.today(),
                 "itens": [{"hora": h, "titulo": t, "repete": r, "dia_inteiro": False, "passou": False, "agora": False}
                           for h, t, r in exemplos.get(k, [])]}
                for k, nome in enumerate(["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"])]
        return {"titulo": f"{segunda:%d/%m} a {segunda + timedelta(days=6):%d/%m}", "dias": dias,
                "tarefas": [{"titulo": "Pagar o condomínio", "prazo_texto": "hoje", "atrasada": False, "feita": False},
                            {"titulo": "Mandar a petição", "prazo_texto": "ontem", "atrasada": True, "feita": False}]}

    threading.Thread(target=responder, daemon=True).start()
    from biblioteca_fontes import BibliotecaDeFontes

    raiz_demo = os.path.join(pasta, "fontes")
    exemplos = {"obsidian/direito": ["Processo civil - aula 1.pdf", "Responsabilidade civil.pptx", "Resumo CDC.md"],
                "obsidian/direito/OAB": ["Simulado 2ª fase.pdf"], "obsidian/filosofia": ["Kant - crítica.pdf"],
                "obsidian/referencias": ["Contrato aluguel.jpg"], "documentos/psicologia": []}
    for pasta_relativa, nomes in exemplos.items():
        os.makedirs(os.path.join(raiz_demo, *pasta_relativa.split("/")), exist_ok=True)
        for nome in nomes:
            with open(os.path.join(raiz_demo, *pasta_relativa.split("/"), nome), "wb") as arquivo:
                arquivo.write(b"x" * (40000 + 9000 * len(nome)))
    lidos = {os.path.normcase(os.path.realpath(os.path.join(raiz_demo, "obsidian", "direito", n)))
             for n in ("Processo civil - aula 1.pdf", "Responsabilidade civil.pptx")}
    biblioteca = BibliotecaDeFontes(
        raizes={"documentos": ("Documentos", os.path.join(raiz_demo, "documentos")), "obsidian": ("Obsidian", os.path.join(raiz_demo, "obsidian"))},
        pessoais=[], estado_do_indice=lambda: {c: "lida" for c in lidos},
        lixeira=lambda caminho: shutil.move(caminho, os.path.join(pasta, os.path.basename(caminho))),
        arquivos=os.path.join(raiz_demo, "arquivados"), recebidos=os.path.join(raiz_demo, "recebidos"))
    import random

    amostra = os.path.join(os.path.dirname(os.path.abspath(__file__)), "documentos_gerados", "amostras_voz", "1_atual_thalita.mp3")

    def fala_de_mentira(texto):  # a amostra da voz dela (uma cópia: o app apaga as falas antigas)
        if not os.path.exists(amostra):
            raise FileNotFoundError("sem a amostra da voz")
        copia = os.path.join(pasta, f"fala_{time.time_ns()}.mp3")
        shutil.copyfile(amostra, copia)
        return copia

    # A tela "Editar" usa os apps da ArtCraft de verdade (se estiverem instalados); o resultado fica em documentos gerados.
    from agente_craft import AgenteCraft

    craft = AgenteCraft(avisar=lambda resposta, canal_do_pedido: canal.enviar(resposta.texto, resposta.arquivos))
    app = criar_servidor(canal, aparelhos, dados=dados, transcrever=lambda amostras: ("que horas são?", 0.9), agenda_semana=semana,
                         gerar_fala=fala_de_mentira,
                         buscar_imagem=poster_de_mentira, biblioteca=biblioteca, controle=_controle_de_mentira(pasta),
                         recursos=lambda: {"cpu": random.randint(12, 48), "ram": random.randint(58, 66), "disco": 71},
                         editar=craft.editar_do_app, situacao_da_edicao=craft.situacao, pasta_recebidos=os.path.join(pasta, "recebidos"))
    codigo = aparelhos.novo_codigo()
    print(f"Demonstração em http://127.0.0.1:{porta}  |  código de pareamento: {codigo} (vale 5 min)", flush=True)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=porta, log_level="warning")


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "--demo":
        _demonstracao(int(sys.argv[2]) if len(sys.argv) > 2 else 8772)
    else:
        print("Uso: python app_celular.py --demo [porta]   (o app de verdade sobe junto com a Beatriz)")
