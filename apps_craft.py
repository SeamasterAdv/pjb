"""Os apps da ArtCraft (a suíte "Crafting Apps", instalada pelo ArtCraft Launcher): achar, abrir e usar sem janela.

Cada app tem o programa com janela (`photocraft.exe`) e o de linha de comando (`photocraft-cli.exe`), em
`C:\\Program Files\\<Nome>`. A Beatriz usa os de linha de comando para converter (Word, slides, planilhas, imagens,
vetores e desenhos de CAD), juntar PDFs, editar fotos (LightCraft) e editar vídeos (FilmCraft), tudo no PC e sem abrir
janela. O Claude Code usa os mesmos programas como servidores MCP (veja o `.mcp.json`). O ArtCraft em si (o estúdio de
imagem e vídeo com IA, na nuvem da Storyteller) e o Launcher só abrem: não têm linha de comando.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

TEMPO_LIMITE = 180  # segundos por comando (um Word de 60 páginas vira PDF em 0,2 s; slides pesados, em alguns segundos)
TEMPO_LIMITE_VIDEO = 1200  # um vídeo exporta a ~150 quadros por segundo (medido): 20 min dão para vídeos longos
TIQUES_POR_SEGUNDO = 254016000000  # a unidade de tempo do FilmCraft
LIMITE_DE_IMAGENS = 40  # páginas que viram imagem de uma vez
SEM_JANELA = 0x08000000  # CREATE_NO_WINDOW
SOLTO = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: o app continua aberto sem a Beatriz


@dataclass(frozen=True)
class App:
    chave: str
    nome: str
    para_que: str
    falado: str  # o começo do nome como a transcrição escreve ("photo|foto" + "craft")
    exe: str  # relativo a uma das raízes de programas
    cli: str = ""


def _app(chave: str, nome: str, para_que: str, falado: str) -> App:
    return App(chave, nome, para_que, falado, os.path.join(nome, f"{chave}.exe"), os.path.join(nome, f"{chave}-cli.exe"))


APPS = (
    _app("wordcraft", "WordCraft", "textos, como o Word", r"word|uord|wordi"),
    _app("pdfcraft", "PdfCraft", "ler, organizar e proteger PDFs", r"pdf|p\W?d\W?f"),
    _app("deckcraft", "DeckCraft", "apresentações, como o PowerPoint", r"deck|dek|dec"),
    _app("gridcraft", "GridCraft", "planilhas, como o Excel", r"grid|gridi|gred"),
    _app("designcraft", "DesignCraft", "diagramação de páginas", r"design|desig|dizain|desain"),
    _app("figcraft", "FigCraft", "telas e protótipos, como o Figma", r"fig|figi|figma"),
    _app("photocraft", "PhotoCraft", "fotos e imagens, como o Photoshop", r"photo|foto"),
    _app("vectorcraft", "VectorCraft", "ilustração vetorial, como o Illustrator", r"vector|vetor|vektor"),
    _app("lightcraft", "LightCraft", "biblioteca de fotos e revelação de raw", r"light|lait|laite"),
    _app("filmcraft", "FilmCraft", "edição de vídeo", r"film|filme"),
    _app("effectcraft", "EffectCraft", "animação e efeitos visuais", r"effect|efect|efeito|efeitos"),
    _app("soundcraft", "SoundCraft", "áudio e música", r"sound|saund|som"),
    _app("cadcraft", "CADCraft", "desenho técnico (CAD)", r"cad|cadi|kad"),
)
LAUNCHER = App("launcher", "ArtCraft Launcher", "instalar e atualizar os apps da ArtCraft", r"art|arte|arti",
               os.path.join("ArtCraft Launcher", "artcraft-launcher.exe"))
ARTCRAFT = App("artcraft", "ArtCraft", "imagens e vídeos com IA, na nuvem da Storyteller", r"art|arte|arti",
               os.path.join("ArtCraft", "artcraft.exe"))
TODOS = APPS + (LAUNCHER, ARTCRAFT)
POR_CHAVE = {app.chave: app for app in TODOS}

CRAFT = r"(?:craft|crafti|crafte|kraft|craf)"
RE_LAUNCHER = re.compile(rf"\b(?:art\W?{CRAFT}\W+launcher|launcher\W+d[oa]\W+art\W?{CRAFT}|launcher)\b")
RE_DO_APP = {app.chave: re.compile(rf"\b(?:{app.falado})\W?{CRAFT}\b") for app in APPS + (ARTCRAFT,)}

# Extensões de entrada de cada conversor e o que ele grava. Documentos viram imagem passando pelo PDF (uma imagem por
# página, desenhada pelo PdfCraft); fotos, vetores e desenhos de CAD viram imagem direto.
WORD = (".docx", ".doc", ".odt", ".rtf", ".md", ".html", ".htm", ".txt")
IMAGENS = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".psd", ".bmp")
CONVERSORES = {
    "wordcraft": (WORD, (".pdf", ".docx", ".odt", ".rtf", ".html", ".md", ".txt")),
    "deckcraft": ((".pptx",), (".pdf", ".pptx", ".md", ".txt", ".gif")),
    "gridcraft": ((".xlsx", ".csv", ".tsv"), (".pdf", ".xlsx", ".csv", ".tsv", ".html", ".json")),
    "photocraft": (IMAGENS, (".png", ".jpg", ".webp", ".tif", ".psd", ".pdf")),
    "vectorcraft": ((".svg", ".ai", ".eps"), (".pdf", ".svg", ".png", ".jpg", ".webp")),
    "cadcraft": ((".dxf", ".dwg"), (".pdf", ".svg", ".png", ".dxf", ".dwg")),
}
VIDEOS = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".3gp")
CONVERSORES["filmcraft"] = (VIDEOS, (".mp4", ".gif", ".aac"))  # feito por editar_video (importar, sequência, exportar)
# O que o LightCraft revela: as fotos comuns e os raw das câmeras.
FOTOS = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".avif", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf",
         ".orf", ".rw2")
FORMATO_FALADO = {".pdf": "PDF", ".docx": "Word", ".odt": "ODT", ".rtf": "RTF", ".html": "HTML", ".md": "Markdown",
                  ".txt": "texto", ".pptx": "PowerPoint", ".gif": "GIF", ".xlsx": "Excel", ".csv": "CSV", ".tsv": "TSV",
                  ".json": "JSON", ".png": "imagem", ".jpg": "JPG", ".webp": "WebP", ".tif": "TIFF", ".psd": "PSD",
                  ".svg": "SVG", ".dxf": "DXF", ".dwg": "DWG", ".mp4": "MP4", ".aac": "áudio (AAC)"}

# Os filtros: (como a frase diz, o preset do LightCraft para fotos, o do FilmCraft para vídeos; None = não tem).
FILTROS = {
    "pb": ("em preto e branco", "lc.bw-soft", "Neutral Mono"),
    "sepia": ("em sépia", "lc.bw-sepia", "Sepia Tone"),
    "vintage": ("com cara de antiga", "lc.instant-70s", "Faded Print"),
    "cinema": ("com cara de cinema", "lc.cinematic", "Teal Shadows, Warm Skin"),
    "quente": ("com tom quente", "lc.warm-glow", "Golden Dusk"),
    "frio": ("com tom frio", "lc.cool-morning", "Steel Blue"),
    "vivo": ("com cores vivas", "lc.vivid-pop", None),
    "dramatico": ("com clima dramático", "lc.moody", "Hard Sun"),
    "suave": ("com cores suaves", "lc.soft-pastel", None),
}
RECORTES = {"1x1": ("quadrada", '"1x1"'), "9x16": ("no formato dos stories", "[9,16]"), "16x9": ("deitada em 16:9", '"16x9"'),
            "4x5": ("no formato 4:5", '"4x5"')}


def _numero(valor, minimo: float, maximo: float, padrao: float = 0.0) -> float:
    try:
        return max(minimo, min(maximo, float(valor)))
    except (TypeError, ValueError):
        return padrao


def _tempo(segundos: float) -> str:
    """75.5 -> "1:15"."""
    segundos = max(0, int(round(segundos)))
    return f"{segundos // 60}:{segundos % 60:02d}"


@dataclass
class EdicaoDeFoto:
    """O que fazer com uma foto, em valores do LightCraft. `girar` em quartos de volta para a direita (3 = esquerda)."""
    filtro: str = ""
    automatico: bool = False
    luz: float = 0.0  # exposição, em pontos (-2 a 2)
    contraste: float = 0.0  # -100 a 100
    cor: float = 0.0  # saturação, -100 a 100
    nitidez: float = 0.0  # 0 a 150
    ruido: float = 0.0  # redução de ruído, 0 a 100
    girar: int = 0
    espelhar: bool = False
    recorte: str = ""  # chave de RECORTES
    tamanho: int = 0  # o lado maior em pixels; 0 = o original
    formato: str = ".jpg"

    @classmethod
    def do_app(cls, dados: dict) -> "EdicaoDeFoto":
        """A edição que a tela "Editar" do celular mandou, conferida e dentro dos limites."""
        return cls(filtro=str(dados.get("filtro") or "") if dados.get("filtro") in FILTROS else "",
                   automatico=bool(dados.get("automatico")), luz=_numero(dados.get("luz"), -2, 2),
                   contraste=_numero(dados.get("contraste"), -100, 100), cor=_numero(dados.get("cor"), -100, 100),
                   nitidez=_numero(dados.get("nitidez"), 0, 150), ruido=_numero(dados.get("ruido"), 0, 100),
                   girar=int(_numero(dados.get("girar"), 0, 3)), espelhar=bool(dados.get("espelhar")),
                   recorte=dados.get("recorte") if dados.get("recorte") in RECORTES else "",
                   tamanho=int(_numero(dados.get("tamanho"), 0, 8000)),
                   formato=dados.get("formato") if dados.get("formato") in (".jpg", ".png", ".webp", ".tif") else ".jpg")

    def vazia(self) -> bool:
        return self == EdicaoDeFoto(formato=self.formato)

    def descricao(self) -> list[str]:
        partes = []
        if self.filtro:
            partes.append(FILTROS[self.filtro][0])
        if self.automatico:
            partes.append("melhorada no automático")
        for valor, mais, menos in ((self.luz, "mais clara", "mais escura"), (self.contraste, "com mais contraste", "com menos contraste"),
                                   (self.cor, "com mais cor", "com menos cor")):
            if valor:
                partes.append(mais if valor > 0 else menos)
        if self.nitidez:
            partes.append("mais nítida")
        if self.ruido:
            partes.append("sem granulado")
        if self.girar:
            partes.append({1: "girada para a direita", 2: "de cabeça para baixo", 3: "girada para a esquerda"}[self.girar])
        if self.espelhar:
            partes.append("espelhada")
        if self.recorte:
            partes.append(RECORTES[self.recorte][0])
        if self.tamanho:
            partes.append(f"menor ({self.tamanho} px no lado maior)")
        return partes


@dataclass
class EdicaoDeVideo:
    """O que fazer com um vídeo. `inicio`/`fim` em segundos do vídeo original; `saida` = ".mp4", ".gif", ".aac" (só o som)
    ou ".jpg" (a imagem do momento `inicio`)."""
    inicio: float | None = None
    fim: float | None = None
    sem_som: bool = False
    metade: bool = False
    velocidade: int = 100  # por cento
    filtro: str = ""
    saida: str = ".mp4"
    ultimos: float = 0.0  # "só os últimos 10 segundos": resolvido com a duração do vídeo
    sem_os_ultimos: float = 0.0  # "tira os últimos 5 segundos": idem

    @classmethod
    def do_app(cls, dados: dict) -> "EdicaoDeVideo":
        inicio = _numero(dados.get("inicio"), 0, 86400, -1)
        fim = _numero(dados.get("fim"), 0, 86400, -1)
        return cls(inicio=inicio if inicio > 0 or (inicio == 0 and dados.get("saida") == ".jpg") else None,
                   fim=fim if fim > 0 else None, sem_som=bool(dados.get("sem_som")), metade=bool(dados.get("metade")),
                   velocidade=int(_numero(dados.get("velocidade"), 25, 400, 100)),
                   filtro=dados.get("filtro") if dados.get("filtro") in FILTROS else "",
                   saida=dados.get("saida") if dados.get("saida") in (".mp4", ".gif", ".aac", ".jpg") else ".mp4")

    def vazia(self) -> bool:
        return self == EdicaoDeVideo()

    def descricao(self) -> list[str]:
        partes = []
        if self.saida == ".jpg":
            return [f"a imagem de {_tempo(self.inicio or 0)}"]
        if self.ultimos:
            partes.append(f"só os últimos {self.ultimos:g} segundos")
        elif self.sem_os_ultimos:
            partes.append(f"sem os últimos {self.sem_os_ultimos:g} segundos")
        elif self.fim is not None:
            partes.append(f"cortado de {_tempo(self.inicio or 0)} a {_tempo(self.fim)}")
        elif self.inicio is not None:
            partes.append(f"a partir de {_tempo(self.inicio)}")
        if self.velocidade > 100:
            partes.append(f"{self.velocidade / 100:g} vezes mais rápido")
        elif self.velocidade < 100:
            partes.append("em câmera lenta")
        if self.filtro:
            partes.append(FILTROS[self.filtro][0])
        if self.sem_som and self.saida != ".aac":
            partes.append("sem som")
        if self.metade:
            partes.append("com metade do tamanho")
        if self.saida == ".gif":
            partes.append("em GIF")
        if self.saida == ".aac":
            partes.append("só o som")
        return partes


class ErroCraft(Exception):
    """Mensagem para o usuário, em português."""


def raizes_padrao() -> list[str]:
    """Onde os instaladores põem os apps: os da suíte em Program Files, o Launcher e o ArtCraft no AppData do usuário."""
    local = os.environ.get("LOCALAPPDATA", "")
    raizes = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", ""),
              os.path.join(local, "Programs") if local else "", local]
    return [r for r in raizes if r]


def app_citado(texto_normalizado: str) -> App | None:
    """O app que a frase cita ("abre o photo craft" -> PhotoCraft), no texto sem acentos e em minúsculas."""
    if RE_LAUNCHER.search(texto_normalizado):
        return LAUNCHER
    for app in APPS + (ARTCRAFT,):
        if RE_DO_APP[app.chave].search(texto_normalizado):
            return app
    return None


def conversor_de(extensao: str) -> App | None:
    for chave, (entradas, _) in CONVERSORES.items():
        if extensao in entradas:
            return POR_CHAVE[chave]
    return None


def _caminho_livre(pasta: str, nome: str) -> str:
    """O caminho em `pasta` com esse nome, ou "nome (1).ext", "nome (2).ext"... se já existir."""
    base, extensao = os.path.splitext(nome)
    caminho, n = os.path.join(pasta, nome), 1
    while os.path.exists(caminho):
        caminho, n = os.path.join(pasta, f"{base} ({n}){extensao}"), n + 1
    return caminho


def _rodar_de_verdade(comando: list[str], tempo: float = TEMPO_LIMITE) -> subprocess.CompletedProcess:
    return subprocess.run(comando, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=tempo, creationflags=SEM_JANELA, stdin=subprocess.DEVNULL)


def _motivo_do_erro(chave: str, saida: str) -> str:
    """A frase do erro: a de uma linha JSON com "ok": false (LightCraft e FilmCraft dizem um comando por linha) ou a última."""
    linhas = [l.strip() for l in saida.splitlines() if l.strip()]
    for linha in linhas:
        if linha.startswith("{"):
            try:
                dados = json.loads(linha)
            except ValueError:
                continue
            if isinstance(dados, dict) and dados.get("ok") is False and dados.get("error"):
                return str(dados["error"])
    motivo = linhas[-1] if linhas else "sem mensagem"
    return re.sub(rf"^{chave}-cli:\s*(?:\w+:\s*)?", "", motivo)


def _iniciar_de_verdade(comando: list[str], pasta: str):
    subprocess.Popen(comando, cwd=pasta, creationflags=SOLTO, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class AppsCraft:
    """Os apps instalados e o que dá para fazer com eles. `rodar` e `iniciar` trocam os processos nos testes."""

    def __init__(self, raizes=None, rodar=None, iniciar=None):
        self._raizes = raizes  # None = raizes_padrao(), lidas a cada uso (instalar um app não pede reiniciar)
        self.rodar, self.iniciar = rodar, iniciar  # None = os processos de verdade (o conftest.py os proíbe nos testes)

    # ------------------------------------------------------------------ onde estão
    def _achar(self, relativo: str) -> str | None:
        if not relativo:
            return None
        for raiz in (self._raizes if self._raizes is not None else raizes_padrao()):
            caminho = os.path.join(raiz, relativo)
            if os.path.isfile(caminho):
                return caminho
        return None

    def executavel(self, app: App) -> str | None:
        return self._achar(app.exe)

    def linha_de_comando(self, app: App) -> str | None:
        return self._achar(app.cli)

    def instalados(self) -> list[App]:
        return [app for app in TODOS if self.executavel(app)]

    # ------------------------------------------------------------------ abrir
    def abrir(self, app: App, arquivo: str | None = None):
        exe = self.executavel(app)
        if not exe:
            raise ErroCraft(f"O {app.nome} não está instalado. Ele vem pelo ArtCraft Launcher.")
        (self.iniciar or _iniciar_de_verdade)([exe] + ([arquivo] if arquivo else []), os.path.dirname(exe))

    # ------------------------------------------------------------------ linha de comando
    def _cli(self, chave: str, *argumentos: str, tempo: float = TEMPO_LIMITE) -> str:
        app = POR_CHAVE[chave]
        cli = self.linha_de_comando(app)
        if not cli:
            raise ErroCraft(f"Para isso eu uso o {app.nome}, que não está instalado. Ele vem pelo ArtCraft Launcher.")
        try:
            resultado = (self.rodar or _rodar_de_verdade)([cli, *argumentos], tempo=tempo)
        except subprocess.TimeoutExpired:
            raise ErroCraft(f"O {app.nome} passou de {int(tempo // 60)} minutos e eu parei.") from None
        except OSError as exc:
            raise ErroCraft(f"Não consegui rodar o {app.nome}: {exc}") from None
        if resultado.returncode != 0:
            raise ErroCraft(f"O {app.nome} não conseguiu: {_motivo_do_erro(chave, f'{resultado.stderr}{chr(10)}{resultado.stdout}')}")
        return resultado.stdout

    def paginas(self, pdf: str) -> int:
        """Quantas páginas o PDF tem (pelo PdfCraft); 0 se não der para saber."""
        try:
            return int(json.loads(self._cli("pdfcraft", "info", pdf)).get("pages") or 0)
        except (ErroCraft, ValueError, AttributeError):
            return 0

    def converter(self, entrada: str, extensao: str, pasta: str) -> list[str]:
        """Converte `entrada` para o formato da `extensao` (".pdf", ".png"...) em `pasta`; devolve os arquivos feitos.
        Documento para imagem dá uma imagem por página (numa subpasta, se forem várias)."""
        origem = os.path.splitext(entrada)[1].lower()
        extensao = ".jpg" if extensao == ".jpeg" else extensao
        if origem == extensao or (origem in (".jpg", ".jpeg") and extensao == ".jpg"):
            raise ErroCraft(f"“{os.path.basename(entrada)}” já está em {FORMATO_FALADO.get(extensao, extensao)}.")
        os.makedirs(pasta, exist_ok=True)
        base = os.path.splitext(os.path.basename(entrada))[0]
        if origem == ".pdf":
            if extensao in (".png", ".jpg"):
                return self._paginas_em_imagens(entrada, base, extensao, pasta)
            if extensao == ".txt":
                destino = _caminho_livre(pasta, base + ".txt")
                with open(destino, "w", encoding="utf-8") as arquivo:
                    arquivo.write(self._cli("pdfcraft", "text", entrada).replace("\f", "\n\n"))
                return [destino]
            raise ErroCraft(f"De PDF eu só passo para imagem ou texto: o PdfCraft não converte PDF em "
                            f"{FORMATO_FALADO.get(extensao, extensao)}.")
        app = conversor_de(origem)
        if app is None:
            raise ErroCraft(f"Não sei converter arquivos {origem or 'sem extensão'}.")
        saidas = CONVERSORES[app.chave][1]
        if app.chave == "filmcraft" and extensao in saidas:
            return [self.editar_video(entrada, EdicaoDeVideo(saida=extensao), pasta)]
        if extensao in saidas:
            destino = _caminho_livre(pasta, base + extensao)
            self._cli(app.chave, "convert", entrada, destino)
            return [destino]
        if extensao in (".png", ".jpg") and ".pdf" in saidas:  # Word, slides e planilhas: PDF primeiro, depois as páginas
            temporaria = tempfile.mkdtemp(prefix="beatriz_craft_")
            try:
                pdf = os.path.join(temporaria, base + ".pdf")
                self._cli(app.chave, "convert", entrada, pdf)
                return self._paginas_em_imagens(pdf, base, extensao, pasta)
            finally:
                shutil.rmtree(temporaria, ignore_errors=True)
        raise ErroCraft(f"O {app.nome} não converte {FORMATO_FALADO.get(origem, origem)} em "
                        f"{FORMATO_FALADO.get(extensao, extensao)}.")

    def _paginas_em_imagens(self, pdf: str, base: str, extensao: str, pasta: str) -> list[str]:
        total = self.paginas(pdf) or 1
        if total > 1:
            pasta = _caminho_livre(pasta, f"{base} (imagens)")
            os.makedirs(pasta, exist_ok=True)
        feitos = []
        for pagina in range(1, min(total, LIMITE_DE_IMAGENS) + 1):
            nome = f"{base}{extensao}" if total == 1 else f"{base} - p{pagina:02d}{extensao}"
            destino = _caminho_livre(pasta, nome)
            self._cli("pdfcraft", "render", pdf, "--page", str(pagina), "--dpi", "150", "--out", destino)
            feitos.append(destino)
        return feitos

    def juntar(self, entradas: list[str], pasta: str, nome: str) -> str:
        """Junta os arquivos, na ordem, num PDF só (o que não for PDF vira PDF antes); devolve o caminho."""
        if len(entradas) < 2:
            raise ErroCraft("Para juntar, preciso de pelo menos dois arquivos.")
        os.makedirs(pasta, exist_ok=True)
        temporaria = tempfile.mkdtemp(prefix="beatriz_craft_")
        try:
            pdfs = []
            for i, entrada in enumerate(entradas):
                if entrada.lower().endswith(".pdf"):
                    pdfs.append(entrada)
                else:
                    pdfs.extend(self.converter(entrada, ".pdf", os.path.join(temporaria, str(i))))
            destino = _caminho_livre(pasta, nome)
            self._cli("pdfcraft", "combine", *pdfs, "--out", destino)
            return destino
        finally:
            shutil.rmtree(temporaria, ignore_errors=True)

    # ------------------------------------------------------------------ editar fotos (LightCraft) e vídeos (FilmCraft)
    def editar_foto(self, entrada: str, edicao: EdicaoDeFoto, pasta: str) -> str:
        """Revela a foto com a edição numa sessão do LightCraft sem janela (`run --import`) e exporta; devolve o caminho.
        A ordem conta: o filtro primeiro (um preset mexe na luz e na cor), os ajustes por cima, depois girar e recortar."""
        if os.path.splitext(entrada)[1].lower() not in FOTOS:
            raise ErroCraft(f"O LightCraft não abre arquivos {os.path.splitext(entrada)[1].lower() or 'sem extensão'}.")
        os.makedirs(pasta, exist_ok=True)
        comandos = []
        if edicao.filtro:
            comandos += ["preset.apply", f"id={FILTROS[edicao.filtro][1]}"]
        if edicao.automatico:
            comandos.append("develop.auto")
        valores = {controle: valor for controle, valor in (
            ("light.exposure", edicao.luz), ("light.contrast", edicao.contraste), ("color.saturation", edicao.cor),
            ("detail.sharpenAmount", edicao.nitidez), ("detail.nrLuminance", edicao.ruido), ("detail.nrColor", edicao.ruido)) if valor}
        if valores:
            comandos += ["develop.set", "values=" + json.dumps(valores)]
        comandos += (["photo.rotateLeft"] if edicao.girar == 3 else ["photo.rotateRight"] * edicao.girar)
        if edicao.espelhar:
            comandos.append("photo.flipHorizontal")
        if edicao.recorte:
            comandos += ["crop.aspect", f"aspect={RECORTES[edicao.recorte][1]}"]
        base = os.path.splitext(os.path.basename(entrada))[0]
        destino = _caminho_livre(pasta, f"{base} (editada){edicao.formato}")
        comandos += ["app.export", f"path={destino}", f"longEdge={edicao.tamanho}", "quality=92"]
        self._cli("lightcraft", "run", "--import", entrada, *comandos)
        if not os.path.isfile(destino):
            raise ErroCraft("O LightCraft não gravou a foto editada.")
        return destino

    def duracao_do_video(self, video: str) -> float:
        """A duração em segundos (pelo FilmCraft); 0 se não der para saber."""
        try:
            return float(json.loads(self._cli("filmcraft", "probe", video, "--compact")).get("duration") or 0) / TIQUES_POR_SEGUNDO
        except (ErroCraft, ValueError, AttributeError, TypeError):
            return 0.0

    def editar_video(self, entrada: str, edicao: EdicaoDeVideo, pasta: str) -> str:
        """Importa o vídeo num projeto do FilmCraft sem janela, faz a sequência dele, aplica velocidade e filtro e exporta o
        trecho pedido; devolve o caminho. Os tempos são do vídeo original (com a velocidade trocada, a conta é refeita)."""
        if os.path.splitext(entrada)[1].lower() not in VIDEOS:
            raise ErroCraft(f"O FilmCraft não abre arquivos {os.path.splitext(entrada)[1].lower() or 'sem extensão'}.")
        if edicao.filtro and not FILTROS[edicao.filtro][2]:
            raise ErroCraft(f"Esse filtro ({FILTROS[edicao.filtro][0]}) é só para fotos; para vídeo tenho preto e branco, "
                            "sépia, antigo, cinema, quente, frio e dramático.")
        inicio, fim = edicao.inicio, edicao.fim
        if edicao.ultimos or edicao.sem_os_ultimos:
            duracao = self.duracao_do_video(entrada)
            if not duracao:
                raise ErroCraft("Não consegui saber a duração do vídeo para mexer no fim dele.")
            if edicao.ultimos:
                inicio = max(0.0, duracao - edicao.ultimos)
            else:
                fim = duracao - edicao.sem_os_ultimos
                if fim <= (inicio or 0):
                    raise ErroCraft(f"O vídeo tem só {_tempo(duracao)}: tirando {edicao.sem_os_ultimos:g} segundos, não sobra nada.")
        if inicio is not None and fim is None and edicao.saida != ".jpg":  # "a partir do 1:30": o trecho vai até o fim
            fim = self.duracao_do_video(entrada)
            if not fim:
                raise ErroCraft("Não consegui saber a duração do vídeo para cortar até o fim.")
        if inicio is not None and fim is not None and fim <= inicio:
            raise ErroCraft(f"O fim do trecho ({_tempo(fim)}) tem de vir depois do começo ({_tempo(inicio)}).")
        os.makedirs(pasta, exist_ok=True)
        linhas = [{"id": "file.import", "params": {"paths": [entrada]}},
                  {"id": "file.newSequenceFromClip", "params": {"items": [1]}}]  # o primeiro item de um projeto novo
        if edicao.velocidade != 100 or edicao.filtro:
            linhas.append({"id": "edit.selectAll", "params": {}})
        if edicao.velocidade != 100:
            linhas.append({"id": "clip.speedDuration", "params": {"speed": edicao.velocidade, "ripple": True}})
        if edicao.filtro:
            linhas.append({"id": "lumetri.applyPreset", "params": {"name": FILTROS[edicao.filtro][2]}})
        fator = 100 / edicao.velocidade
        base = os.path.splitext(os.path.basename(entrada))[0]
        temporaria = tempfile.mkdtemp(prefix="beatriz_craft_")
        if edicao.saida == ".jpg":
            momento = (inicio or 0.0) * fator
            saida_do_app = os.path.join(temporaria, "quadro.jpg")
            destino = _caminho_livre(pasta, f"{base} - imagem em {_tempo(inicio or 0).replace(':', 'm')}s.jpg")
            exportar = {"path": saida_do_app, "format": "jpg", "range": "custom", "startSeconds": momento,
                        "endSeconds": momento + 0.02}
        else:
            nome = {".gif": f"{base} (editado).gif", ".aac": f"{base} (som).aac"}.get(edicao.saida, f"{base} (editado).mp4")
            destino = saida_do_app = _caminho_livre(pasta, nome)
            exportar = {"path": destino, "format": {".gif": "gif", ".aac": "aac"}.get(edicao.saida, "h264")}
            if inicio is not None or fim is not None:
                exportar.update(range="custom", startSeconds=(inicio or 0.0) * fator, endSeconds=fim * fator)
            if edicao.metade and edicao.saida != ".aac":
                exportar["scale"] = 0.5
            if edicao.sem_som and edicao.saida == ".mp4":
                exportar["audio"] = False
        exportar["wait"] = True
        linhas.append({"id": "file.exportMedia", "params": exportar})
        try:
            roteiro = os.path.join(temporaria, "roteiro.jsonl")
            with open(roteiro, "w", encoding="utf-8") as arquivo:
                arquivo.write("\n".join(json.dumps(linha, ensure_ascii=False) for linha in linhas))
            self._cli("filmcraft", "--compact", "run", roteiro, tempo=TEMPO_LIMITE_VIDEO)
            if edicao.saida == ".jpg":  # o FilmCraft numera os quadros ("quadro000.jpg")
                feitos = sorted(f for f in os.listdir(temporaria) if f.lower().endswith(".jpg"))
                if not feitos:
                    raise ErroCraft("O FilmCraft não gravou a imagem desse momento.")
                shutil.move(os.path.join(temporaria, feitos[0]), destino)
        finally:
            shutil.rmtree(temporaria, ignore_errors=True)
        if not os.path.isfile(destino):
            raise ErroCraft("O FilmCraft não gravou o vídeo editado.")
        return destino
