"""Estruturas comuns dos agentes: tarefa, etapas, avaliação e a classe base Agente."""
import json
import logging
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

# Contexto maior que o padrão do Ollama: os agentes recebem fontes e trechos longos.
OPCOES_AGENTES = {"temperature": 0.4, "num_ctx": 8192}


def normalizar(texto: str) -> str:
    """Minúsculas e sem acentos, para comparar comandos falados ou digitados."""
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", texto.lower()).strip()


@dataclass
class Etapa:
    agente: str
    descricao: str = ""
    inicio: float = 0.0
    fim: float = 0.0
    sucesso: bool = True

    @property
    def duracao(self) -> float:
        return max(0.0, (self.fim or time.time()) - self.inicio)


@dataclass
class Avaliacao:
    cumprido: bool
    nota: int
    pontos_fortes: list = field(default_factory=list)
    problemas: list = field(default_factory=list)
    correcao: str = ""
    verificacoes: list = field(default_factory=list)  # (descrição, passou)


@dataclass
class Tarefa:
    pedido: str
    plano: list                      # nomes dos agentes executores, em ordem
    formatos: list = field(default_factory=list)
    tema: str = ""
    quantidade_slides: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: str = "na fila"          # na fila, executando, revisando, relatando, concluída, falhou, cancelada
    etapa_atual: str = ""
    etapas: list = field(default_factory=list)
    contexto: dict = field(default_factory=dict)
    arquivos: list = field(default_factory=list)
    avaliacoes: list = field(default_factory=list)
    pasta: str = ""
    relatorio: str = ""
    mensagem_final: str = ""
    erro: str = ""
    cancelada: bool = False
    criada_em: float = field(default_factory=time.time)
    concluida_em: float = 0.0

    @property
    def avaliacao(self):
        return self.avaliacoes[-1] if self.avaliacoes else None

    def resumo(self) -> dict:
        """Versão simples (só tipos básicos) para mostrar no painel e compartilhar entre processos."""
        avaliacao = self.avaliacao
        return {
            "id": self.id,
            "pedido": self.pedido[:90],
            "status": self.status,
            "etapa": self.etapa_atual,
            "plano": list(self.plano),
            "arquivos": list(self.arquivos),
            "pasta": self.pasta,
            "relatorio": self.relatorio,
            "nota": avaliacao.nota if avaliacao else None,
            "criada_em": self.criada_em,
            "atualizado_em": time.time(),
        }


def reparar_json_truncado(texto: str):
    """Fecha um JSON cortado no meio (o modelo bateu no limite de tamanho).

    Volta até o último valor completo e fecha as chaves e colchetes abertos, perdendo só o
    trecho incompleto do fim. Devolve o objeto ou None se não houver o que aproveitar.
    """
    inicio = texto.find("{")
    if inicio < 0:
        return None
    texto = texto[inicio:]
    pilha, dentro_de_texto, escapado, cortes = [], False, False, []
    for posicao, caractere in enumerate(texto):
        if dentro_de_texto:
            if escapado:
                escapado = False
            elif caractere == "\\":
                escapado = True
            elif caractere == '"':
                dentro_de_texto = False
            continue
        if caractere == '"':
            dentro_de_texto = True
        elif caractere in "{[":
            pilha.append("}" if caractere == "{" else "]")
        elif caractere in "}]" and pilha:
            pilha.pop()
            cortes.append((posicao + 1, list(pilha)))  # fim de um valor completo
        elif caractere == "," and pilha:
            cortes.append((posicao, list(pilha)))  # antes da vírgula, o item anterior está completo
    for fim, abertos in reversed(cortes):
        try:
            return _sem_item_incompleto(json.loads(texto[:fim] + "".join(reversed(abertos))))
        except json.JSONDecodeError:
            continue
    return None


def _sem_item_incompleto(valor):
    """Num JSON reparado, o último objeto de uma lista com menos campos que os outros foi cortado: sai."""
    if isinstance(valor, dict):
        return {chave: _sem_item_incompleto(v) for chave, v in valor.items()}
    if isinstance(valor, list):
        itens = [_sem_item_incompleto(v) for v in valor]
        if len(itens) > 1 and all(isinstance(i, dict) for i in itens):
            if len(itens[-1]) < max(len(i) for i in itens[:-1]):
                itens.pop()
        return itens
    return valor


def extrair_json(texto: str) -> dict:
    """Lê o JSON devolvido pelo modelo, tolerando texto em volta e respostas cortadas no fim."""
    texto = (texto or "").strip()
    try:
        return json.loads(texto)
    except json.JSONDecodeError as erro:
        inicio, fim = texto.find("{"), texto.rfind("}")
        if inicio >= 0 and fim > inicio:
            try:
                return json.loads(texto[inicio:fim + 1])
            except json.JSONDecodeError:
                pass
        reparado = reparar_json_truncado(texto)
        if isinstance(reparado, dict):
            logger.warning("JSON veio cortado no fim; aproveitando a parte completa.")
            return reparado
        raise erro


class Agente:
    """Um agente é um papel com instruções próprias, usando o LLM local compartilhado."""

    nome = "agente"
    papel = ""
    instrucoes = ""

    def __init__(self, llm=None, modelo=None):
        if llm is None:
            from llm_clients import llm_clients as llm
        self.llm = llm
        self.modelo = modelo

    def _sistema(self) -> str:
        return (
            f"{self.instrucoes}\n\n"
            f"Hoje é {datetime.now():%d/%m/%Y}. Escreva sempre em português do Brasil."
        )

    def pedir_texto(self, prompt: str, max_tokens: int = 400) -> str:
        return self.llm.chat(
            prompt, system_prompt=self._sistema(), model=self.modelo,
            opcoes={**OPCOES_AGENTES, "num_predict": max_tokens},
        ).strip()

    def pedir_json(self, prompt: str, max_tokens: int = 2500) -> dict:
        """Pede uma resposta em JSON; se vier inválida, tenta mais uma vez com temperatura zero."""
        ultimo_erro = None
        for temperatura in (OPCOES_AGENTES["temperature"], 0.0):
            resposta = self.llm.chat(
                prompt, system_prompt=self._sistema(), model=self.modelo, formato="json",
                opcoes={**OPCOES_AGENTES, "temperature": temperatura, "num_predict": max_tokens},
            )
            try:
                dados = extrair_json(resposta)
                if isinstance(dados, dict):
                    return dados
            except (json.JSONDecodeError, ValueError) as exc:
                ultimo_erro = exc
                logger.warning("%s devolveu JSON inválido, tentando de novo: %s", self.nome, exc)
        raise ValueError(f"O {self.nome} não conseguiu produzir um JSON válido: {ultimo_erro}")

    def executar(self, tarefa: Tarefa) -> str:
        """Faz o trabalho do agente, guarda o resultado em `tarefa.contexto` e descreve o que fez."""
        raise NotImplementedError
