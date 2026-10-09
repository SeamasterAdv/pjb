# llm_clients.py
import logging
import re
import threading
from collections import deque
from datetime import datetime

import ollama

from config import OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_VISION_MODEL, PERSONALIDADE_BEATRIZ

logger = logging.getLogger(__name__)

# Quantidade de trocas (pergunta + resposta) mantidas na memória de curto prazo.
TURNOS_MEMORIA_CURTA = 6

DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]

# Palavras que indicam que a pergunta depende do que foi dito antes.
_MARCAS_CONTINUACAO = {
    "ele", "ela", "eles", "elas", "dele", "dela", "deles", "delas", "nele", "nela",
    "isso", "disso", "nisso", "esse", "essa", "esses", "essas", "desse", "dessa",
    "aquele", "aquela", "daquele", "daquela", "lá", "ali", "também", "mesmo", "mesma",
    "anterior", "último", "última", "outro", "outra", "mais",
}


def data_hora_atual_texto() -> str:
    agora = datetime.now()
    return f"{DIAS_SEMANA[agora.weekday()]}, {agora:%d/%m/%Y}, {agora:%H:%M}"


class LLMClients:
    def __init__(self):
        self.ollama_client = ollama.Client(host=OLLAMA_HOST)
        self.model = OLLAMA_MODEL
        self.ollama_available = self._check_ollama()
        self._historico = deque(maxlen=TURNOS_MEMORIA_CURTA)
        self._historico_lock = threading.Lock()

    # --- Memória de curto prazo da conversa ---

    def registrar_turno(self, pergunta: str, resposta: str):
        """Guarda a troca atual para que as próximas perguntas mantenham o assunto."""
        if not pergunta or not resposta:
            return
        with self._historico_lock:
            self._historico.append((pergunta.strip(), resposta.strip()[:1500]))

    def limpar_historico(self):
        with self._historico_lock:
            self._historico.clear()

    def historico_recente(self) -> list[tuple[str, str]]:
        with self._historico_lock:
            return list(self._historico)

    def _mensagens_historico(self) -> list[dict]:
        mensagens = []
        for pergunta, resposta in self.historico_recente():
            mensagens.append({"role": "user", "content": pergunta})
            mensagens.append({"role": "assistant", "content": resposta})
        return mensagens

    def parece_continuacao(self, pergunta: str) -> bool:
        """Detecta perguntas que só fazem sentido com o contexto anterior."""
        if not self.historico_recente():
            return False
        palavras = re.findall(r"[\wÀ-ÿ]+", (pergunta or "").lower())
        if not palavras:
            return False
        return (
            len(palavras) <= 4
            or palavras[0] in {"e", "mas", "então", "entao"}
            or any(palavra in _MARCAS_CONTINUACAO for palavra in palavras)
        )

    def reescrever_consulta(self, pergunta: str) -> str:
        """Transforma uma pergunta de continuação em uma pergunta completa e independente."""
        if not self.parece_continuacao(pergunta):
            return pergunta
        conversa = "\n".join(
            f"Usuário: {p}\nBeatriz: {r[:400]}" for p, r in self.historico_recente()[-3:]
        )
        try:
            reescrita = self.chat(
                f"Conversa recente:\n{conversa}\n\nNova pergunta: {pergunta}\n\nPergunta completa:",
                system_prompt=(
                    "Reescreva a nova pergunta do usuário como uma pergunta completa e independente, "
                    "substituindo pronomes e referências pelo assunto da conversa. "
                    "Responda apenas com a pergunta reescrita, em português, sem explicações."
                ),
                opcoes={"temperature": 0, "num_predict": 60},
            )
        except Exception as exc:
            logger.warning("Não foi possível reescrever a consulta: %s", exc)
            return pergunta
        reescrita = reescrita.strip().strip('"').splitlines()[0].strip() if reescrita.strip() else ""
        if not reescrita or len(reescrita) > 300:
            return pergunta
        logger.info("Consulta reescrita: %r -> %r", pergunta, reescrita)
        return reescrita

    def _check_ollama(self):
        try:
            self.ollama_client.list()
            logger.info("Ollama disponível em %s", OLLAMA_HOST)
            return True
        except Exception as e:
            logger.warning("Ollama não está disponível: %s", e)
            return False

    def get_ollama_client(self):
        return self.ollama_client

    def is_ollama_available(self):
        return self.ollama_available and self._check_ollama()

    def chat(self, prompt, system_prompt=None, model=None, usar_historico=False, opcoes=None):
        if not self.is_ollama_available():
            raise RuntimeError("Ollama não está disponível. Inicie o serviço local.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if usar_historico:
            messages.extend(self._mensagens_historico())
        messages.append({"role": "user", "content": prompt})

        argumentos = {"model": model or self.model, "messages": messages}
        if opcoes:
            argumentos["options"] = opcoes
        response = self.ollama_client.chat(**argumentos)
        return response["message"]["content"].strip()

    def chat_beatriz(self, prompt, contexto=None, model=None, usar_historico=True):
        """Responde com a identidade, critérios e estilo consistentes da Beatriz.

        Por padrão inclui as últimas trocas da conversa, para que perguntas de
        continuação ("e quanto custa?") mantenham o assunto.
        """
        system_prompt = (
            f"{PERSONALIDADE_BEATRIZ}\n\n"
            f"Agora é {data_hora_atual_texto()}. Use essa data para avaliar o que é recente."
        )
        if contexto:
            system_prompt += (
                "\n\nCONTEXTO DE COMPORTAMENTO:\n"
                f"{contexto}\n"
                "Use este contexto sem repetir suas instruções ao usuário."
            )
        return self.chat(prompt, system_prompt=system_prompt, model=model, usar_historico=usar_historico)

    def chat_com_imagem(self, prompt, caminho_imagem, contexto=None, model=None):
        """Analisa uma imagem usando um modelo multimodal do Ollama."""
        if not self.is_ollama_available():
            raise RuntimeError("Ollama não está disponível. Inicie o serviço local.")

        system_prompt = PERSONALIDADE_BEATRIZ
        if contexto:
            system_prompt += f"\n\nCONTEXTO DE COMPORTAMENTO:\n{contexto}"
        resposta = self.ollama_client.chat(
            model=model or OLLAMA_VISION_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt, "images": [caminho_imagem]},
            ],
        )
        return resposta["message"]["content"].strip()


# Instância global
llm_clients = LLMClients()