"""Biblioteca de fontes: organizar à mão os arquivos que a Beatriz lê, pelo app do celular e pela janela do painel.

As pastas são as das fontes: `documentos_assistente` (CAMINHO_DOCUMENTOS) e a vault do Obsidian (VAULT_OBSIDIAN), menos as
pessoais (`geral`, com a memória do usuário, e `diario`) e os arquivos de dados (.json/.jsonl), que não são fontes. Cada pasta e
cada arquivo tem um id relativo ("obsidian/direito/aula 1.pdf"), e todo pedido é conferido aqui (nada de "..", de atalho que
saia das pastas nem das pastas pessoais): nem o app nem a janela conseguem mexer em outra coisa do PC.

Dá para listar (com o estado de cada arquivo na biblioteca), mover, renomear, criar e apagar pastas vazias, adicionar arquivos,
tirar da biblioteca (o arquivo vai para documentos_arquivados/fora_da_biblioteca e para de ser usado) e apagar (vai para a
Lixeira do Windows, dá para recuperar). Depois de cada mudança, `ao_mudar()` pede uma nova varredura do índice.
"""
import json
import logging
import os
import re
import shutil

from config import CAMINHO_DOCUMENTOS, LIMITE_DO_ARQUIVO_MB, PASTA_ARQUIVADOS, PASTAS_OBSIDIAN, VAULT_OBSIDIAN

logger = logging.getLogger(__name__)

EXTENSOES_LIDAS = {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".ppt", ".pptx", ".docx", ".xlsx", ".html", ".htm"}
EXTENSOES_DE_DADOS = (".json", ".jsonl")
PROFUNDIDADE_MAXIMA = 4  # pastas dentro de pastas: até aqui
PASTA_FORA_DA_BIBLIOTECA = os.path.join(PASTA_ARQUIVADOS, "fora_da_biblioteca")
TIPOS = {
    **dict.fromkeys((".pdf",), "pdf"),
    **dict.fromkeys((".doc", ".docx", ".odt", ".rtf"), "documento"),
    **dict.fromkeys((".ppt", ".pptx"), "apresentacao"),
    **dict.fromkeys((".xls", ".xlsx", ".csv"), "planilha"),
    **dict.fromkeys((".png", ".jpg", ".jpeg", ".webp", ".gif"), "imagem"),
    **dict.fromkeys((".md", ".txt"), "nota"),
    **dict.fromkeys((".html", ".htm"), "pagina"),
}


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
                 lixeira=mandar_para_a_lixeira, fora_da_biblioteca: str = PASTA_FORA_DA_BIBLIOTECA,
                 limite_mb: float = LIMITE_DO_ARQUIVO_MB):
        # id da raiz -> (nome que aparece, pasta)
        self.raizes = raizes or {"documentos": ("Documentos", CAMINHO_DOCUMENTOS), "obsidian": ("Obsidian", VAULT_OBSIDIAN)}
        self.pessoais = [p for p in (pessoais if pessoais is not None else
                                     (PASTAS_OBSIDIAN.get("geral"), PASTAS_OBSIDIAN.get("diario"))) if p]
        self.ao_mudar = ao_mudar
        self.estado_do_indice = estado_do_indice
        self.lixeira = lixeira
        self.fora_da_biblioteca = fora_da_biblioteca
        self.limite_mb = limite_mb

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
        raiz = os.path.realpath(self.raizes[chave][1])
        caminho = os.path.realpath(os.path.join(raiz, *resto))
        if not _dentro_de(caminho, raiz) or (resto and self._excluido(caminho)):
            raise ErroDaBiblioteca("Esse item não faz parte da biblioteca de fontes.")
        if pasta and not os.path.isdir(caminho):
            raise ErroDaBiblioteca("Essa pasta não existe mais.")
        if not pasta and not os.path.isfile(caminho):
            raise ErroDaBiblioteca("Esse arquivo não existe mais. Atualize a lista.")
        return caminho

    def _id(self, caminho: str) -> str:
        for chave, (_, raiz) in self.raizes.items():
            if _dentro_de(caminho, raiz):
                relativo = os.path.relpath(os.path.realpath(caminho), os.path.realpath(raiz))
                return chave if relativo == "." else f"{chave}/{relativo.replace(os.sep, '/')}"
        raise ErroDaBiblioteca("Esse item não faz parte da biblioteca de fontes.")

    def _rotulo(self, ident: str) -> str:
        chave, *resto = ident.split("/")
        return " › ".join([self.raizes[chave][0], *resto])

    def _mudou(self):
        if self.ao_mudar:
            try:
                self.ao_mudar()
            except Exception as exc:
                logger.warning("Não consegui pedir a nova leitura da biblioteca: %s", exc)

    # ------------------------------------------------------------ consulta
    def listar(self) -> dict:
        """As pastas (em ordem de árvore) com os arquivos e o estado de cada um na biblioteca."""
        estados = self.estado_do_indice() if self.estado_do_indice else {}
        pastas = []
        for chave, (rotulo, raiz) in self.raizes.items():
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
                    estado = estados.get(_norma(caminho)) or ("ainda não lida" if extensao in EXTENSOES_LIDAS else "não é lida")
                    itens.append({"id": f"{ident}/{nome}", "nome": nome, "tamanho": estat.st_size, "modificado": estat.st_mtime,
                                  "tipo": TIPOS.get(extensao, "arquivo"), "estado": estado})
                pastas.append({"id": ident, "nome": rotulo if relativo == "." else os.path.basename(atual),
                               "caminho": self._rotulo(ident), "profundidade": profundidade, "raiz": relativo == ".",
                               "pode_criar_pasta": profundidade < PROFUNDIDADE_MAXIMA - 1, "arquivos": itens})
        arquivos = [a for p in pastas for a in p["arquivos"]]
        return {"pastas": pastas, "total": len(arquivos), "lidas": sum(a["estado"] == "lida" for a in arquivos)}

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
        return f"Movi “{nome}” para {self._rotulo(pasta_id)}{renomeado}."

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

    def tirar(self, arquivo_id: str) -> str:
        """Tira da biblioteca sem apagar: o arquivo vai para documentos_arquivados/fora_da_biblioteca."""
        origem = self._caminho(arquivo_id)
        os.makedirs(self.fora_da_biblioteca, exist_ok=True)
        destino = _caminho_livre(self.fora_da_biblioteca, os.path.basename(origem))
        shutil.move(origem, destino)
        self._mudou()
        return f"Tirei “{os.path.basename(origem)}” da biblioteca. Ele continua no PC, em documentos_arquivados › fora_da_biblioteca."

    def apagar(self, arquivo_id: str) -> str:
        origem = self._caminho(arquivo_id)
        self.lixeira(origem)
        self._mudou()
        return f"Mandei “{os.path.basename(origem)}” para a Lixeira do Windows. Se foi sem querer, dá para restaurar de lá."

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
        lida = "" if extensao in EXTENSOES_LIDAS else " A biblioteca não lê esse tipo de arquivo, mas ele fica guardado aí."
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
