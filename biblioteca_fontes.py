"""Biblioteca: organizar à mão os arquivos da Beatriz, pelo app do celular e pela janela do painel, separando o que é fonte do
que é só arquivo.

Três áreas, cada uma com as suas pastas:
- **Fontes** (`documentos_assistente` = CAMINHO_DOCUMENTOS e a vault do Obsidian = VAULT_OBSIDIAN): a Beatriz lê e usa nas
  respostas, nos resumos e nas fichas. Ficam de fora as pastas pessoais (`geral`, com a memória do usuário, e `diario`) e os
  arquivos de dados (.json/.jsonl).
- **Só arquivos** (`documentos_arquivados` = PASTA_ARQUIVADOS): guardados e organizados, mas ela não lê; acha pelo nome quando
  você pede ("abre", "imprime", "manda"). O "arquiva" do WhatsApp cai aqui.
- **Recebidos** (`documentos_recebidos` = PASTA_RECEBIDOS): o que chegou pelo WhatsApp, pelo celular ou pelo scanner, para você
  decidir se vira fonte ou só arquivo.

Cada pasta e cada arquivo tem um id relativo ("obsidian/direito/aula 1.pdf", "arquivos/direito/trabalho.docx"), e todo pedido é
conferido aqui (nada de "..", de atalho que saia das pastas nem das pastas pessoais): nem o app nem a janela conseguem mexer em
outra coisa do PC.

Dá para listar (com o estado de cada arquivo e as cópias repetidas), mover (também de uma área para outra), renomear, criar e
apagar pastas vazias, adicionar arquivos, trocar de área mantendo a pasta do assunto (`so_arquivo`: "obsidian/direito/x.pdf" vai
para "arquivos/direito/x.pdf"; `virar_fonte` faz o caminho de volta) e apagar (vai para a Lixeira do Windows, dá para recuperar),
um arquivo ou vários (`em_lote`). Depois de cada mudança, `ao_mudar()` pede uma nova varredura do índice (o que saiu das fontes
sai da biblioteca; o que entrou é lido).
"""
import hashlib
import json
import logging
import os
import re
import shutil
from datetime import datetime

from config import (CAMINHO_DOCUMENTOS, CATEGORIA_DAS_FONTES, LIMITE_DO_ARQUIVO_MB, PASTA_ARQUIVADOS, PASTA_RECEBIDOS,
                    PASTAS_OBSIDIAN, VAULT_OBSIDIAN)

logger = logging.getLogger(__name__)

EXTENSOES_LIDAS = {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".ppt", ".pptx", ".docx", ".xlsx", ".html", ".htm"}
EXTENSOES_DE_DADOS = (".json", ".jsonl")
PROFUNDIDADE_MAXIMA = 4  # pastas dentro de pastas: até aqui
# id da área -> (nome, o que quer dizer)
AREAS = {
    "fontes": ("Fontes", "a Beatriz lê e usa nas respostas"),
    "arquivos": ("Só arquivos", "guardados: ela não lê, mas acha pelo nome"),
    "recebidos": ("Recebidos", "chegaram pelo WhatsApp, pelo celular ou pelo scanner"),
}
ESTADO_DA_AREA = {"arquivos": "só arquivo", "recebidos": "recebido"}
TIPOS = {
    **dict.fromkeys((".pdf",), "pdf"),
    **dict.fromkeys((".doc", ".docx", ".odt", ".rtf"), "documento"),
    **dict.fromkeys((".ppt", ".pptx"), "apresentacao"),
    **dict.fromkeys((".xls", ".xlsx", ".csv"), "planilha"),
    **dict.fromkeys((".png", ".jpg", ".jpeg", ".webp", ".gif"), "imagem"),
    **dict.fromkeys((".md", ".txt"), "nota"),
    **dict.fromkeys((".html", ".htm"), "pagina"),
}
_RESUMOS: dict[str, tuple[int, int, str]] = {}  # caminho -> (modificado, tamanho, sha1): para achar cópias sem reler tudo


class ErroDaBiblioteca(Exception):
    """Pedido que não dá para fazer; a mensagem vai para a pessoa como está."""


def _norma(caminho: str) -> str:
    return os.path.normcase(os.path.realpath(caminho))


def _dentro_de(caminho: str, pasta: str) -> bool:
    try:
        return os.path.commonpath([_norma(caminho), _norma(pasta)]) == _norma(pasta)
    except ValueError:  # outro disco
        return False


def _nome_limpo(nome: str, limite: int = 100) -> str:
    nome = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", (nome or "").strip()).strip(" .")
    return nome[:limite].rstrip(" .")


def _caminho_livre(pasta: str, nome: str) -> str:
    base, extensao = os.path.splitext(nome)
    candidato, numero = os.path.join(pasta, nome), 1
    while os.path.exists(candidato):
        candidato = os.path.join(pasta, f"{base} ({numero}){extensao}")
        numero += 1
    return candidato


def _resumo_do_conteudo(caminho: str, estat: os.stat_result) -> str:
    chave = _norma(caminho)
    guardado = _RESUMOS.get(chave)
    if guardado and guardado[:2] == (estat.st_mtime_ns, estat.st_size):
        return guardado[2]
    soma = hashlib.sha1()
    with open(caminho, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1 << 20), b""):
            soma.update(bloco)
    _RESUMOS[chave] = (estat.st_mtime_ns, estat.st_size, soma.hexdigest())
    return soma.hexdigest()


def mandar_para_a_lixeira(caminho: str):
    """Manda o arquivo para a Lixeira do Windows (SHFileOperationW com desfazer), sem perguntar nada na tela."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    operacao = SHFILEOPSTRUCTW(hwnd=None, wFunc=FO_DELETE, pFrom=os.path.abspath(caminho) + "\0", pTo=None,
                               fFlags=FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI)
    resultado = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operacao))
    if resultado != 0 or operacao.fAnyOperationsAborted or os.path.exists(caminho):
        raise ErroDaBiblioteca("O Windows não deixou mandar esse arquivo para a Lixeira (ele pode estar aberto em outro programa).")


def _estado_do_indice() -> dict:
    """{caminho normalizado: "lida" | "sem texto"} pelo manifesto da biblioteca (o mesmo arquivo nos dois processos)."""
    try:
        import memory

        with open(memory.CAMINHO_MANIFESTO_MEMORIA, encoding="utf-8") as arquivo:
            manifesto = json.load(arquivo)
    except Exception:
        return {}
    return {_norma(r["caminho"]): ("sem texto" if r.get("vazio") else "lida") for r in manifesto.values() if r.get("caminho")}


class BibliotecaDeFontes:
    def __init__(self, raizes: dict | None = None, pessoais=None, ao_mudar=None, estado_do_indice=_estado_do_indice,
                 lixeira=mandar_para_a_lixeira, arquivos: str | None = PASTA_ARQUIVADOS,
                 recebidos: str | None = PASTA_RECEBIDOS, pasta_das_fontes: str | None = None,
                 limite_mb: float = LIMITE_DO_ARQUIVO_MB, relogio=datetime.now):
        fontes = raizes or {"documentos": ("Documentos", CAMINHO_DOCUMENTOS), "obsidian": ("Obsidian", VAULT_OBSIDIAN)}
        # id da raiz -> (nome que aparece, pasta, área)
        self.raizes = {chave: (rotulo, pasta, "fontes") for chave, (rotulo, pasta) in fontes.items()}
        if arquivos:
            self.raizes["arquivos"] = ("Só arquivos", arquivos, "arquivos")
        if recebidos:
            self.raizes["recebidos"] = ("Recebidos", recebidos, "recebidos")
        self.pessoais = [p for p in (pessoais if pessoais is not None else
                                     (PASTAS_OBSIDIAN.get("geral"), PASTAS_OBSIDIAN.get("diario"))) if p]
        # "virar fonte" sem pasta de assunto (o que chegou pelo WhatsApp): a mesma do "vira fonte" falado
        raiz_das_fontes = fontes.get("obsidian", next(iter(fontes.values())))[1]
        self.pasta_das_fontes = pasta_das_fontes or os.path.join(raiz_das_fontes, CATEGORIA_DAS_FONTES)
        self.ao_mudar = ao_mudar
        self.estado_do_indice = estado_do_indice
        self.lixeira = lixeira
        self.limite_mb = limite_mb
        self.relogio = relogio
        self._em_lote = False

    # ------------------------------------------------------------ ids e conferência
    def _excluido(self, caminho: str) -> bool:
        nome = os.path.basename(caminho)
        if nome.startswith(".") or nome.lower().endswith(EXTENSOES_DE_DADOS):
            return True
        return any(_dentro_de(caminho, pessoal) for pessoal in self.pessoais)

    def _caminho(self, ident: str, pasta: bool = False) -> str:
        partes = (ident or "").replace("\\", "/").strip("/").split("/")
        chave, resto = partes[0], partes[1:]
        if chave not in self.raizes or any(p in ("", ".", "..") for p in resto):
            raise ErroDaBiblioteca("Não achei esse item na biblioteca.")
        if self.raizes[chave][2] != "fontes":
            os.makedirs(self.raizes[chave][1], exist_ok=True)  # "Só arquivos" e "Recebidos" existem mesmo antes do 1º arquivo
        raiz = os.path.realpath(self.raizes[chave][1])
        caminho = os.path.realpath(os.path.join(raiz, *resto))
        if not _dentro_de(caminho, raiz) or (resto and self._excluido(caminho)):
            raise ErroDaBiblioteca("Esse item não faz parte da biblioteca de fontes.")
        if pasta and not os.path.isdir(caminho):
            raise ErroDaBiblioteca("Essa pasta não existe mais.")
        if not pasta and not os.path.isfile(caminho):
            raise ErroDaBiblioteca("Esse arquivo não existe mais. Atualize a lista.")
        return caminho

    def _chave(self, caminho: str) -> str:
        # a raiz mais funda primeiro (a pasta das fontes pode estar dentro de outra raiz)
        for chave, (_, raiz, _) in sorted(self.raizes.items(), key=lambda item: -len(_norma(item[1][1]))):
            if _dentro_de(caminho, raiz):
                return chave
        raise ErroDaBiblioteca("Esse item não faz parte da biblioteca de fontes.")

    def _id(self, caminho: str) -> str:
        chave = self._chave(caminho)
        relativo = os.path.relpath(os.path.realpath(caminho), os.path.realpath(self.raizes[chave][1]))
        return chave if relativo == "." else f"{chave}/{relativo.replace(os.sep, '/')}"

    def _rotulo(self, ident: str) -> str:
        chave, *resto = ident.split("/")
        return " › ".join([self.raizes[chave][0], *resto])

    def _area(self, ident: str) -> str:
        return self.raizes[ident.split("/")[0]][2]

    def _mudou(self):
        if self.ao_mudar and not self._em_lote:
            try:
                self.ao_mudar()
            except Exception as exc:
                logger.warning("Não consegui pedir a nova leitura da biblioteca: %s", exc)

    # ------------------------------------------------------------ consulta
    def listar(self) -> dict:
        """As áreas e as pastas (em ordem de árvore), com os arquivos, o estado de cada um e as cópias repetidas."""
        estados = self.estado_do_indice() if self.estado_do_indice else {}
        pastas, todos = [], []
        for chave, (rotulo, raiz, area) in self.raizes.items():
            if area != "fontes":
                os.makedirs(raiz, exist_ok=True)  # a área aparece mesmo vazia, para receber arquivos
            if not os.path.isdir(raiz):
                continue
            for atual, subpastas, arquivos in os.walk(raiz):
                relativo = os.path.relpath(atual, raiz)
                profundidade = 0 if relativo == "." else relativo.count(os.sep) + 1
                subpastas[:] = sorted((d for d in subpastas if not self._excluido(os.path.join(atual, d))), key=str.lower)
                if profundidade >= PROFUNDIDADE_MAXIMA:
                    subpastas[:] = []
                ident = chave if relativo == "." else f"{chave}/{relativo.replace(os.sep, '/')}"
                itens = []
                for nome in sorted(arquivos, key=str.lower):
                    caminho = os.path.join(atual, nome)
                    if self._excluido(caminho):
                        continue
                    try:
                        estat = os.stat(caminho)
                    except OSError:
                        continue
                    extensao = os.path.splitext(nome)[1].lower()
                    if area == "fontes":
                        estado = estados.get(_norma(caminho)) or ("ainda não lida" if extensao in EXTENSOES_LIDAS else "não é lida")
                    else:
                        estado = ESTADO_DA_AREA[area]
                    item = {"id": f"{ident}/{nome}", "nome": nome, "tamanho": estat.st_size, "modificado": estat.st_mtime,
                            "tipo": TIPOS.get(extensao, "arquivo"), "estado": estado, "area": area, "repetido": []}
                    itens.append(item)
                    todos.append((item, caminho, estat))
                pastas.append({"id": ident, "nome": rotulo if relativo == "." else os.path.basename(atual),
                               "caminho": self._rotulo(ident), "profundidade": profundidade, "raiz": relativo == ".",
                               "area": area, "pode_criar_pasta": profundidade < PROFUNDIDADE_MAXIMA - 1, "arquivos": itens})
        repetidos = self._marcar_repetidos(todos)
        arquivos = [item for item, _, _ in todos]
        contas = {area: sum(a["area"] == area for a in arquivos) for area in AREAS}
        areas = [{"id": area, "nome": nome, "descricao": descricao, "arquivos": contas[area]}
                 for area, (nome, descricao) in AREAS.items() if any(r[2] == area for r in self.raizes.values())]
        return {"areas": areas, "pastas": pastas, "total": len(arquivos), "fontes": contas["fontes"],
                "lidas": sum(a["estado"] == "lida" for a in arquivos), "so_arquivos": contas["arquivos"],
                "recebidos": contas["recebidos"], "repetidos": repetidos}

    @staticmethod
    def _marcar_repetidos(todos) -> int:
        """Põe em cada arquivo os ids das cópias com o mesmo conteúdo (mesmo tamanho e mesmo sha1); devolve quantos têm cópia."""
        por_tamanho: dict[int, list] = {}
        for item, caminho, estat in todos:
            if estat.st_size > 0:
                por_tamanho.setdefault(estat.st_size, []).append((item, caminho, estat))
        marcados = 0
        for grupo in por_tamanho.values():
            if len(grupo) < 2:
                continue
            por_conteudo: dict[str, list] = {}
            for item, caminho, estat in grupo:
                try:
                    por_conteudo.setdefault(_resumo_do_conteudo(caminho, estat), []).append(item)
                except OSError:
                    continue
            for iguais in por_conteudo.values():
                if len(iguais) > 1:
                    for item in iguais:
                        item["repetido"] = [outro["id"] for outro in iguais if outro is not item]
                        marcados += 1
        return marcados

    def caminho_para_abrir(self, arquivo_id: str) -> str:
        return self._caminho(arquivo_id)

    # ------------------------------------------------------------ mudanças
    def mover(self, arquivo_id: str, pasta_id: str) -> str:
        origem = self._caminho(arquivo_id)
        pasta = self._caminho(pasta_id, pasta=True)
        nome = os.path.basename(origem)
        if _norma(os.path.dirname(origem)) == _norma(pasta):
            return f"“{nome}” já está em {self._rotulo(pasta_id)}."
        destino = _caminho_livre(pasta, nome)
        shutil.move(origem, destino)
        self._mudou()
        renomeado = "" if os.path.basename(destino) == nome else f" (como “{os.path.basename(destino)}”, porque já havia um igual)"
        return f"Movi “{nome}” para {self._rotulo(pasta_id)}{renomeado}.{self._aviso_de_area(arquivo_id, pasta_id)}"

    def _aviso_de_area(self, de_id: str, para_id: str) -> str:
        de, para = self._area(de_id), self._area(para_id)
        if de == para:
            return ""
        if para == "fontes":
            return " Agora ele é fonte: a Beatriz vai lê-lo."
        if de == "fontes":
            return " Agora ele é só arquivo: a Beatriz não usa mais o conteúdo dele."
        return ""

    def _pasta_na_area(self, origem: str, area: str) -> str:
        """Para onde o arquivo vai ao mudar de área, mantendo a pasta do assunto: "obsidian/direito" -> "arquivos/direito" (e
        de volta para a fonte que tiver uma pasta "direito"). Sem assunto (veio do WhatsApp, estava na raiz), as fontes vão para
        a pasta das fontes recebidas e os arquivos, para a pasta do mês, como o "arquiva" falado."""
        chave = self._chave(origem)
        _, raiz, area_de_origem = self.raizes[chave]
        relativo = os.path.relpath(os.path.dirname(os.path.realpath(origem)), os.path.realpath(raiz))
        assunto = "" if area_de_origem == "recebidos" or relativo == "." else relativo
        destinos = [pasta for _, pasta, a in self.raizes.values() if a == area]
        if not destinos:
            raise ErroDaBiblioteca("Essa área não existe neste computador.")
        if assunto:
            for raiz_destino in destinos:
                alvo = os.path.join(raiz_destino, assunto)
                if os.path.isdir(alvo) and not self._excluido(alvo):
                    return alvo
            if area == "arquivos":
                return os.path.join(destinos[0], assunto)
        if area == "fontes":
            return self.pasta_das_fontes
        dia = os.path.basename(os.path.dirname(origem))
        mes = dia[:7] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dia) else self.relogio().strftime("%Y-%m")
        return os.path.join(destinos[0], mes if area_de_origem == "recebidos" else "")

    def _trocar_de_area(self, arquivo_id: str, area: str) -> str:
        origem = self._caminho(arquivo_id)
        nome = os.path.basename(origem)
        if self._area(arquivo_id) == area:
            return f"“{nome}” já é {'fonte' if area == 'fontes' else 'só arquivo'}."
        pasta = os.path.normpath(self._pasta_na_area(origem, area))
        os.makedirs(pasta, exist_ok=True)
        destino = _caminho_livre(pasta, nome)
        shutil.move(origem, destino)
        self._mudou()
        onde = self._rotulo(self._id(pasta))
        if area == "fontes":
            return f"“{nome}” virou fonte, em {onde}: a Beatriz vai lê-lo."
        return f"“{nome}” agora é só arquivo, em {onde}: a Beatriz não usa o conteúdo dele, mas acha pelo nome."

    def so_arquivo(self, arquivo_id: str) -> str:
        """Tira das fontes sem apagar: vai para "Só arquivos", na pasta do mesmo assunto."""
        return self._trocar_de_area(arquivo_id, "arquivos")

    tirar = so_arquivo  # nome antigo (app do celular de antes)

    def virar_fonte(self, arquivo_id: str) -> str:
        return self._trocar_de_area(arquivo_id, "fontes")

    def renomear(self, arquivo_id: str, novo_nome: str) -> str:
        origem = self._caminho(arquivo_id)
        antigo = os.path.basename(origem)
        extensao = os.path.splitext(antigo)[1]
        novo = _nome_limpo(novo_nome)
        if not novo:
            raise ErroDaBiblioteca("Escolha um nome com letras ou números.")
        if os.path.splitext(novo)[1].lower() != extensao.lower():
            novo += extensao  # o tipo do arquivo não muda pelo nome
        if novo == antigo:
            return "O nome continua o mesmo."
        destino = os.path.join(os.path.dirname(origem), novo)
        if os.path.exists(destino) and _norma(destino) != _norma(origem):
            raise ErroDaBiblioteca(f"Já existe “{novo}” nessa pasta.")
        os.rename(origem, destino)
        self._mudou()
        return f"Renomeei “{antigo}” para “{novo}”."

    def criar_pasta(self, pai_id: str, nome: str) -> str:
        pai = self._caminho(pai_id, pasta=True)
        nome = _nome_limpo(nome, 60)
        if not nome:
            raise ErroDaBiblioteca("Escolha um nome para a pasta.")
        profundidade = 0 if "/" not in pai_id else pai_id.count("/")
        if profundidade >= PROFUNDIDADE_MAXIMA - 1:
            raise ErroDaBiblioteca("Essa pasta já está funda demais; crie a nova num nível acima.")
        caminho = os.path.join(pai, nome)
        if self._excluido(caminho):
            raise ErroDaBiblioteca("Esse nome é reservado.")
        if os.path.exists(caminho):
            raise ErroDaBiblioteca(f"Já existe uma pasta “{nome}” aí.")
        os.makedirs(caminho)
        return f"Criei a pasta {self._rotulo(pai_id)} › {nome}."

    def apagar_pasta(self, pasta_id: str) -> str:
        pasta = self._caminho(pasta_id, pasta=True)
        if "/" not in pasta_id.strip("/"):
            raise ErroDaBiblioteca("As pastas principais da biblioteca não podem ser apagadas.")
        if os.listdir(pasta):
            raise ErroDaBiblioteca("A pasta não está vazia: mova ou apague os arquivos dela antes.")
        os.rmdir(pasta)
        return f"Apaguei a pasta vazia {self._rotulo(pasta_id)}."

    def apagar(self, arquivo_id: str) -> str:
        origem = self._caminho(arquivo_id)
        self.lixeira(origem)
        self._mudou()
        return f"Mandei “{os.path.basename(origem)}” para a Lixeira do Windows. Se foi sem querer, dá para restaurar de lá."

    def em_lote(self, acao: str, ids, destino: str = "") -> str:
        """A mesma ação em vários arquivos ("mover", "so_arquivo", "virar_fonte", "apagar"), com uma nova leitura só no fim."""
        funcoes = {"mover": lambda i: self.mover(i, destino), "so_arquivo": self.so_arquivo, "virar_fonte": self.virar_fonte,
                   "apagar": self.apagar}
        if acao not in funcoes:
            raise ErroDaBiblioteca("Não sei fazer isso com vários arquivos.")
        ids = [i for i in dict.fromkeys(ids or []) if isinstance(i, str)][:500]
        if not ids:
            raise ErroDaBiblioteca("Escolha ao menos um arquivo.")
        feitos, erros = 0, []
        self._em_lote = True
        try:
            for ident in ids:
                try:
                    funcoes[acao](ident)
                    feitos += 1
                except (ErroDaBiblioteca, OSError) as exc:
                    erros.append(f"{ident.rsplit('/', 1)[-1]}: {exc}")
        finally:
            self._em_lote = False
        if feitos:
            self._mudou()
        um = feitos == 1
        plural = "arquivo" if um else "arquivos"
        if not feitos:
            feito = "Não mudei nada."
        elif acao == "mover":
            feito = f"Movi {feitos} {plural} para {self._rotulo(destino)}."
        elif acao == "so_arquivo":
            feito = f"{feitos} {plural} agora {'é' if um else 'são'} só arquivo: a Beatriz não usa o conteúdo, mas acha pelo nome."
        elif acao == "virar_fonte":
            feito = f"{feitos} {plural} {'virou' if um else 'viraram'} fonte: a Beatriz vai ler."
        else:
            feito = f"Mandei {feitos} {plural} para a Lixeira do Windows."
        if erros:
            feito += f" Não deu em {len(erros)}: " + "; ".join(erros[:3]) + ("…" if len(erros) > 3 else "")
        return feito

    def adicionar(self, nome: str, pasta_id: str, conteudo: bytes | None = None, origem: str | None = None) -> str:
        """Um arquivo novo na pasta escolhida: os bytes (do celular) ou uma cópia de `origem` (escolhido no PC)."""
        from fontes import EXTENSOES_BLOQUEADAS

        pasta = self._caminho(pasta_id, pasta=True)
        nome = _nome_limpo(os.path.basename((nome or "").replace("\\", "/"))) or "arquivo"
        extensao = os.path.splitext(nome)[1].lower()
        if extensao in EXTENSOES_BLOQUEADAS:
            raise ErroDaBiblioteca(f"Não guardo arquivos {extensao} (podem ser programas).")
        if extensao in EXTENSOES_DE_DADOS:
            raise ErroDaBiblioteca("Arquivos .json/.jsonl são dados, não fontes.")
        tamanho = len(conteudo) if conteudo is not None else os.path.getsize(origem)
        if tamanho > self.limite_mb * 1024 * 1024:
            raise ErroDaBiblioteca(f"O arquivo passa de {self.limite_mb:g} MB.")
        destino = _caminho_livre(pasta, nome)
        if conteudo is not None:
            with open(destino, "wb") as saida:
                saida.write(conteudo)
        else:
            shutil.copy2(origem, destino)
        self._mudou()
        lida = ""
        if self._area(pasta_id) != "fontes":
            lida = " Ele fica guardado como só arquivo (a Beatriz não lê; para virar fonte, use “virar fonte”)."
        elif extensao not in EXTENSOES_LIDAS:
            lida = " A biblioteca não lê esse tipo de arquivo, mas ele fica guardado aí."
        return f"Guardei “{os.path.basename(destino)}” em {self._rotulo(pasta_id)}.{lida}"


def resultado(funcao, *args, **kwargs) -> dict:
    """{"ok", "mensagem"} para a janela e o app: os erros esperados viram mensagem, os outros também (sem derrubar nada)."""
    try:
        return {"ok": True, "mensagem": funcao(*args, **kwargs)}
    except ErroDaBiblioteca as exc:
        return {"ok": False, "mensagem": str(exc)}
    except OSError as exc:
        logger.warning("Biblioteca: %s", exc)
        return {"ok": False, "mensagem": "Não deu: o arquivo pode estar aberto em outro programa."}
