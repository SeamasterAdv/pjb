"""Orquestrador: reconhece pedidos de entrega, monta o plano e coordena os agentes.

Fluxo de cada tarefa (em segundo plano, uma por vez):
    [biblioteca] → [pesquisa] → documento | apresentação → crítico (→ correção) → relator
"""
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from agentes.base import Etapa, Tarefa, normalizar
from agentes.critico import AgenteCritico
from agentes.especialistas import AgenteApresentacao, AgenteBiblioteca, AgenteDocumento, AgentePesquisa
from agentes.geradores import nome_arquivo_seguro
from agentes.relator import AgenteRelator

logger = logging.getLogger(__name__)

# ------------------------------------------------------------ reconhecimento
_VERBOS_CRIAR = (
    "crie", "cria", "criar", "faca", "faz", "fazer", "gere", "gera", "gerar", "monte", "monta", "montar",
    "prepare", "prepara", "preparar", "elabore", "elabora", "elaborar", "escreva", "escreve", "escrever",
    "produza", "produzir", "redija", "redigir", "desenvolva", "desenvolver", "quero", "preciso",
)
RE_CRIAR = re.compile(r"\b(" + "|".join(_VERBOS_CRIAR) + r")\b")
RE_ABRIR = re.compile(r"^(abr[ae]|abrir|leia|ler|mostre|mostra|feche|fechar|apague|apagar|delete)\b")
RE_PPTX = re.compile(r"\b(apresentac\w*|slides?|power ?point|pptx?)\b")
RE_PDF = re.compile(r"\bpdf\b")
RE_DOCX = re.compile(r"\b(word|docx)\b")
RE_DOCUMENTO = re.compile(r"\b(documento|relatorio|apostila|artigo|ebook|e-book|manual|guia|dossie|trabalho escolar)\b")
RE_WEB = re.compile(r"\b(pesquis\w*|atual|atuais|atualizad\w*|hoje|noticia\w*|ultim\w*|recente\w*|internet|web|cotac\w*|preco\w*|20[2-9]\d)\b")
RE_BIBLIOTECA = re.compile(r"\b(meus documentos|minhas notas|minha biblioteca|biblioteca|obsidian|meus arquivos|minhas anotacoes|minha memoria|com base no que eu)\b")
RE_SO_PESQUISA = re.compile(r"\b(pesquisa (completa|detalhada|aprofundada|profunda)|pesquis\w* a fundo|investigue|fa(ca|z|zer) uma pesquisa)\b")
RE_TEMA = re.compile(r"\b(?:sobre|a respeito d[eoa]s?|acerca d[eoa]s?|com o tema|explicando|falando d[eoa]s?)\s+(.+)", re.IGNORECASE)
RE_CORTE_TEMA = re.compile(
    r"[\s,.;]*\b(?:em (?:pdf|word|docx|power ?point|pptx|slides?)|com \d+ (?:slides?|p[áa]ginas?)|"
    r"com (?:tr[êe]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|quinze|vinte) slides?|"
    r"e (?:salve|salva|me mande|mande|envie|abra)|no formato|para (?:apresentar|a reuni[ãa]o|amanh[ãa]))\b",
    re.IGNORECASE,
)
NUMEROS = {"tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
           "onze": 11, "doze": 12, "quinze": 15, "vinte": 20}

ROTULOS_ETAPA = {
    "biblioteca": "consultando sua biblioteca",
    "pesquisa": "pesquisando na internet",
    "documento": "escrevendo o documento",
    "apresentacao": "montando os slides",
    "critico": "revisando o resultado",
    "corrigindo": "corrigindo após a crítica",
    "relator": "escrevendo o relatório",
}


def extrair_tema(pedido: str) -> str:
    achado = RE_TEMA.search(pedido)
    tema = achado.group(1) if achado else pedido
    tema = RE_CORTE_TEMA.split(tema)[0]
    if not achado:
        # Sem "sobre": tira o verbo e o tipo de arquivo do começo ("crie um pdf de receitas" -> "receitas").
        tema = re.sub(
            r"^.*?\b(?:documento|relat[óo]rio|apostila|artigo|pdf|word|docx|apresenta[çc][ãa]o|slides?|power ?point|pptx?)\b"
            r"(?:\s+(?:em|no|de|do|da|dos|das|com|para))?\s*",
            "", tema, count=1, flags=re.IGNORECASE,
        ) or tema
    return tema.strip(" .,!?:;\"'") or pedido


def extrair_quantidade_slides(texto_normalizado: str) -> int:
    achado = re.search(r"\b(\d{1,2}|" + "|".join(NUMEROS) + r")\s+(?:slides?|laminas?|telas?)\b", texto_normalizado)
    if not achado:
        return 0
    valor = achado.group(1)
    quantidade = int(valor) if valor.isdigit() else NUMEROS[valor]
    return max(2, min(20, quantidade))


def planejar(comando: str):
    """Transforma um pedido em Tarefa com o plano de agentes, ou None se não for um pedido de entrega."""
    texto = normalizar(comando)
    texto = re.sub(r"^(beatriz|beatris|biatriz)[\s,]+", "", texto)
    texto = re.sub(r"^(por favor|me|pra mim)\s+", "", texto)
    if not texto or RE_ABRIR.match(texto):
        return None

    formatos = []
    if RE_PPTX.search(texto):
        formatos.append("pptx")
    if RE_DOCX.search(texto):
        formatos.append("docx")
    if RE_PDF.search(texto):
        formatos.append("pdf")
    quer_documento = bool(formatos) or bool(RE_DOCUMENTO.search(texto))
    so_pesquisa = not quer_documento and bool(RE_SO_PESQUISA.search(texto))

    if not so_pesquisa and not (quer_documento and RE_CRIAR.search(texto)):
        return None
    if quer_documento and not formatos:
        formatos = ["docx"]

    plano = []
    if RE_BIBLIOTECA.search(texto):
        plano.append("biblioteca")
    if so_pesquisa or RE_WEB.search(texto):
        plano.append("pesquisa")
    if quer_documento:
        plano.append("apresentacao" if "pptx" in formatos else "documento")

    return Tarefa(
        pedido=comando.strip(),
        plano=plano,
        formatos=[] if so_pesquisa else formatos,
        tema=extrair_tema(comando.strip()),
        quantidade_slides=extrair_quantidade_slides(texto),
    )


# ------------------------------------------------------------ painel
class PainelCompartilhado:
    """Publica o andamento das tarefas num dicionário (pode ser um Manager().dict entre processos)."""

    PREFIXO = "tarefa:"

    def __init__(self, dados, limite: int = 12):
        self.dados = dados
        self.limite = limite

    def __call__(self, resumo: dict):
        try:
            self.dados[self.PREFIXO + resumo["id"]] = resumo
            antigas = sorted(self.listar(self.dados), key=lambda r: r.get("criada_em", 0))[:-self.limite]
            for antiga in antigas:
                self.dados.pop(self.PREFIXO + antiga["id"], None)
        except Exception as exc:  # o gerenciador pode ter sido encerrado junto com o programa
            logger.debug("Não foi possível publicar o andamento da tarefa: %s", exc)

    @classmethod
    def listar(cls, dados) -> list:
        try:
            itens = [dados[chave] for chave in list(dados.keys()) if str(chave).startswith(cls.PREFIXO)]
        except Exception:
            return []
        return sorted(itens, key=lambda r: r.get("criada_em", 0), reverse=True)


class TarefaCancelada(Exception):
    pass


# ------------------------------------------------------------ orquestrador
class Orquestrador:
    def __init__(self, pasta_saida: str, llm=None, modelo=None, publicar=None, abrir=None,
                 max_revisoes: int = 1, abrir_ao_concluir: bool = True, agentes: dict = None, llm_nuvem=None):
        self.pasta_saida = pasta_saida
        opcoes = {"llm": llm, "modelo": modelo}
        self.agentes = agentes or {
            "biblioteca": AgenteBiblioteca(**opcoes),
            "pesquisa": AgentePesquisa(**opcoes),
            "documento": AgenteDocumento(**opcoes),
            "apresentacao": AgenteApresentacao(**opcoes),
            "critico": AgenteCritico(**opcoes),
            "relator": AgenteRelator(**opcoes),
        }
        # Versões dos agentes que escrevem com o modelo da nuvem (com o local de reserva).
        # A biblioteca não entra aqui: tarefas com seus documentos ficam inteiras no Ollama.
        self.llm_nuvem = llm_nuvem
        self.agentes_nuvem = {}
        if llm_nuvem is not None:
            nuvem = {"llm": llm_nuvem, "modelo": modelo}
            self.agentes_nuvem = {
                "pesquisa": AgentePesquisa(**nuvem),
                "documento": AgenteDocumento(**nuvem),
                "apresentacao": AgenteApresentacao(**nuvem),
                "critico": AgenteCritico(**nuvem),
                "relator": AgenteRelator(**nuvem),
            }
        self.publicar = publicar or (lambda resumo: None)
        self.abrir = abrir or getattr(os, "startfile", lambda caminho: None)
        self.max_revisoes = max_revisoes
        self.abrir_ao_concluir = abrir_ao_concluir
        self.tarefas = []
        self._lock = threading.Lock()
        # Um trabalhador só: as tarefas usam o mesmo modelo na placa de vídeo, então em fila é mais rápido.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agentes")

    # --------------------------------------------------------- envio
    def enviar(self, comando: str, ao_concluir=None):
        """Se o comando for um pedido de entrega, coloca a tarefa na fila e devolve a Tarefa; senão None."""
        tarefa = planejar(comando)
        if tarefa is None:
            return None
        carimbo = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        tarefa.pasta = os.path.join(self.pasta_saida, f"{carimbo}_{nome_arquivo_seguro(tarefa.tema, 40)}")
        with self._lock:
            self.tarefas.append(tarefa)
            self.tarefas = self.tarefas[-20:]
        self._publicar(tarefa)
        self._executor.submit(self._executar, tarefa, ao_concluir)
        return tarefa

    def mensagem_inicio(self, tarefa: Tarefa) -> str:
        if "pptx" in tarefa.formatos:
            entrega = "a apresentação"
        elif tarefa.formatos:
            entrega = "o " + " e o ".join("Word" if f == "docx" else f.upper() for f in tarefa.formatos)
        else:
            entrega = "uma pesquisa completa"
        extras = []
        if "biblioteca" in tarefa.plano:
            extras.append("consultar seus documentos")
        if "pesquisa" in tarefa.plano and tarefa.formatos:
            extras.append("pesquisar na internet")
        passos = f" Vou {' e '.join(extras)} antes." if extras else ""
        na_fila = sum(1 for t in self.tarefas if t.status in ("na fila", "executando", "revisando", "relatando")) - 1
        fila = f" Há {na_fila} tarefa(s) na frente desta." if na_fila > 0 else ""
        return f"Certo, vou preparar {entrega} sobre {tarefa.tema}.{passos} Te aviso quando terminar.{fila}"

    # --------------------------------------------------------- execução
    def _publicar(self, tarefa: Tarefa):
        self.publicar(tarefa.resumo())

    def _agente(self, tarefa: Tarefa, nome: str):
        """Agente da nuvem quando a tarefa só usa conteúdo público; senão, o local."""
        if nome in self.agentes_nuvem and "biblioteca" not in tarefa.plano:
            return self.agentes_nuvem[nome]
        return self.agentes[nome]

    def _rodar_etapa(self, tarefa: Tarefa, nome: str, rotulo: str = None):
        if tarefa.cancelada:
            raise TarefaCancelada()
        agente = self._agente(tarefa, nome)
        tarefa.etapa_atual = ROTULOS_ETAPA.get(rotulo or nome, nome)
        self._publicar(tarefa)
        etapa = Etapa(agente=nome, inicio=time.time())
        try:
            etapa.descricao = agente.executar(tarefa)
        except Exception as exc:
            etapa.sucesso = False
            etapa.descricao = f"Falhou: {exc}"
            raise
        finally:
            etapa.fim = time.time()
            tarefa.etapas.append(etapa)
            logger.info("Tarefa %s · %s: %s (%.1fs)", tarefa.id, nome, etapa.descricao, etapa.duracao)

    def _modelos_usados(self, tarefa: Tarefa) -> list:
        registro = getattr(self.llm_nuvem, "registro", None)
        if registro is None or "biblioteca" in tarefa.plano:
            return ["Ollama (local)"]
        return list(dict.fromkeys(registro))

    def _executar(self, tarefa: Tarefa, ao_concluir):
        if getattr(self.llm_nuvem, "registro", None) is not None:
            self.llm_nuvem.registro.clear()  # uma tarefa por vez: o registro passa a ser desta
        try:
            tarefa.status = "executando"
            for nome in tarefa.plano:
                self._rodar_etapa(tarefa, nome)

            tarefa.status = "revisando"
            for tentativa in range(self.max_revisoes + 1):
                self._rodar_etapa(tarefa, "critico")
                if tarefa.avaliacao.cumprido or tentativa == self.max_revisoes:
                    break
                versao_anterior = (dict(tarefa.contexto), list(tarefa.arquivos))
                try:
                    self._rodar_etapa(tarefa, tarefa.plano[-1], rotulo="corrigindo")
                except TarefaCancelada:
                    raise
                except Exception as exc:
                    # A correção falhou, mas a versão anterior já existe: entrega ela, com ressalvas.
                    logger.warning("Correção da tarefa %s falhou; mantendo a versão anterior: %s", tarefa.id, exc)
                    tarefa.contexto, tarefa.arquivos = versao_anterior
                    tarefa.avaliacao.problemas.append(f"A tentativa de correção falhou ({exc}); foi mantida a primeira versão.")
                    break

            tarefa.status = "relatando"
            tarefa.contexto["modelos"] = self._modelos_usados(tarefa)
            self._rodar_etapa(tarefa, "relator")
            tarefa.status = "concluída"
            tarefa.mensagem_final = self._mensagem_final(tarefa)
            if self.abrir_ao_concluir and tarefa.arquivos:
                self._abrir(tarefa.arquivos[0])
        except TarefaCancelada:
            tarefa.status = "cancelada"
            tarefa.mensagem_final = f"Tarefa sobre {tarefa.tema} cancelada."
        except Exception as exc:
            logger.exception("Tarefa %s falhou", tarefa.id)
            tarefa.status = "falhou"
            tarefa.erro = str(exc)
            tarefa.mensagem_final = f"Não consegui concluir a tarefa sobre {tarefa.tema}: {exc}"
            tarefa.contexto["modelos"] = self._modelos_usados(tarefa)
            try:
                self.agentes["relator"].executar(tarefa)  # registra o que deu errado
            except Exception:
                pass
        finally:
            tarefa.etapa_atual = ""
            tarefa.concluida_em = time.time()
            self._publicar(tarefa)
            if ao_concluir:
                try:
                    ao_concluir(tarefa)
                except Exception as exc:
                    logger.warning("Falha ao avisar a conclusão da tarefa: %s", exc)

    def _mensagem_final(self, tarefa: Tarefa) -> str:
        avaliacao = tarefa.avaliacao
        revisoes = len(tarefa.avaliacoes) - 1
        nota = f"O crítico deu nota {avaliacao.nota} de 10" + (f", depois de {revisoes} revisão." if revisoes else ".")
        if not tarefa.arquivos:
            resposta = tarefa.contexto.get("resposta", "")
            return f"{resposta}\n\n{nota} O relatório da pesquisa ficou salvo."
        nomes_formato = {"pptx": "PowerPoint", "docx": "Word", "pdf": "PDF"}
        formatos = " e ".join(nomes_formato.get(os.path.splitext(a)[1].lstrip("."), a) for a in tarefa.arquivos)
        titulo = (tarefa.contexto.get("conteudo") or {}).get("titulo") or tarefa.tema
        entrega = f"A apresentação '{titulo}' está pronta" if "pptx" in tarefa.formatos else f"O documento '{titulo}' está pronto"
        if avaliacao.cumprido:
            return f"Pronto! {entrega} em {formatos}. {nota} O relatório explicando o que foi feito está na mesma pasta."
        problema = avaliacao.problemas[0] if avaliacao.problemas else "alguns pontos não ficaram como pedido"
        problema = problema.replace("Verificação automática falhou: ", "").rstrip(".")
        return f"{entrega} em {formatos}, mas com ressalvas: {problema}. {nota} Veja os detalhes no relatório."

    def _abrir(self, caminho: str):
        try:
            self.abrir(caminho)
        except Exception as exc:
            logger.warning("Não foi possível abrir %s: %s", caminho, exc)

    # --------------------------------------------------------- gerenciamento
    def _ultima(self, apenas_concluidas=True):
        with self._lock:
            for tarefa in reversed(self.tarefas):
                if not apenas_concluidas or tarefa.status == "concluída":
                    return tarefa
        return None

    def responder_gerenciamento(self, comando: str):
        """Comandos sobre as tarefas: status, abrir resultado, abrir relatório, cancelar. None se não for isso."""
        texto = normalizar(comando)
        if re.search(r"\b(status|andamento) d[ao]s? tarefas?\b|\bquais (sao as )?tarefas\b|\bcomo (esta|anda) a tarefa\b|\bo que voce esta (fazendo|preparando)\b", texto):
            return self.descrever_tarefas()
        if re.search(r"\bcancel\w* (a |essa |esta )?tarefa\b", texto):
            with self._lock:
                ativas = [t for t in self.tarefas if t.status in ("na fila", "executando", "revisando")]
            if not ativas:
                return "Não há nenhuma tarefa em andamento para cancelar."
            ativas[-1].cancelada = True
            return f"Cancelando a tarefa sobre {ativas[-1].tema}. Ela para ao fim da etapa atual."
        if re.search(r"\babr\w* (a )?pasta d[eo]s? (documentos|arquivos) gerad", texto):
            os.makedirs(self.pasta_saida, exist_ok=True)
            self._abrir(self.pasta_saida)
            return "Abrindo a pasta dos documentos gerados."
        if re.search(r"\babr\w* (o )?(ultimo )?relatorio\b", texto):
            tarefa = self._ultima()
            if not tarefa or not tarefa.relatorio:
                return "Ainda não há relatório de tarefa concluída."
            self._abrir(tarefa.relatorio)
            return f"Abrindo o relatório da tarefa sobre {tarefa.tema}."
        abrir_resultado = re.search(
            r"\babr\w* (o |a )?(ultim[oa] )?(documento|apresentacao|arquivo|pdf|word|slides|resultado)"
            r"( (que voce (fez|criou|gerou)|gerad[oa]|criad[oa]))?$", texto,
        )
        if abrir_resultado and (abrir_resultado.group(2) or abrir_resultado.group(4) or len(texto.split()) <= 4):
            tarefa = self._ultima()
            if not tarefa or not tarefa.arquivos:
                return None  # deixa o comando seguir para a ferramenta normal de abrir arquivos
            self._abrir(tarefa.arquivos[0])
            return f"Abrindo {os.path.basename(tarefa.arquivos[0])}."
        return None

    def descrever_tarefas(self) -> str:
        with self._lock:
            tarefas = list(self.tarefas)
        if not tarefas:
            return "Nenhuma tarefa de agentes foi feita nesta sessão."
        ativas = [t for t in tarefas if t.status in ("na fila", "executando", "revisando", "relatando")]
        partes = []
        for tarefa in ativas:
            partes.append(f"A tarefa sobre {tarefa.tema} está {tarefa.etapa_atual or tarefa.status}.")
        ultima = next((t for t in reversed(tarefas) if t.status not in ("na fila", "executando", "revisando", "relatando")), None)
        if ultima:
            nota = f", nota {ultima.avaliacao.nota}" if ultima.avaliacao else ""
            partes.append(f"A última concluída foi sobre {ultima.tema}: {ultima.status}{nota}.")
        return " ".join(partes) or "Nenhuma tarefa em andamento."
