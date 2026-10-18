"""Agentes da Beatriz: especialistas por tarefa, crítico, relator e o orquestrador que os coordena."""
from agentes.base import Avaliacao, Etapa, Tarefa
from agentes.orquestrador import (
    Orquestrador,
    PainelCompartilhado,
    pede_envio,
    pedido_de_arquivo,
    planejar,
    texto_da_entrega,
)


TEMPO_LIMITE_CLAUDE_ESTUDOS = 900  # segundos


def criar_orquestrador(publicar=None) -> Orquestrador:
    """Orquestrador com as configurações do .env (pasta de saída, modelo, revisões)."""
    from config import (
        ABRIR_DOCUMENTOS_GERADOS,
        ESTUDOS_COM_CLAUDE,
        MAX_REVISOES_AGENTES,
        MODELO_AGENTES,
        PASTA_DOCUMENTOS_GERADOS,
    )
    from llm_clients import llm_clients
    from llm_nuvem import ClienteClaudeCode, LLMComReserva, claude, provedores_nuvem

    # Os agentes escrevem com o Claude (assinatura) ou o Gemini (gratuito), nessa ordem,
    # e caem no Ollama se nenhum puder responder (limite, sem login, sem internet).
    provedores = provedores_nuvem()
    llm_nuvem = LLMComReserva(provedores, llm_clients) if provedores else None
    # Material de estudo com as fontes do usuário: só o Claude lê os documentos (ESTUDOS_COM_CLAUDE).
    # A matéria inteira leva minutos para escrever, então esta cópia do cliente espera mais.
    llm_fontes = None
    if ESTUDOS_COM_CLAUDE and claude.ativo and claude.configurado():
        llm_fontes = LLMComReserva(
            [ClienteClaudeCode(executavel=claude.executavel, tempo_limite=TEMPO_LIMITE_CLAUDE_ESTUDOS)], llm_clients)
    return Orquestrador(
        pasta_saida=PASTA_DOCUMENTOS_GERADOS,
        modelo=MODELO_AGENTES,
        publicar=publicar,
        max_revisoes=MAX_REVISOES_AGENTES,
        abrir_ao_concluir=ABRIR_DOCUMENTOS_GERADOS,
        llm_nuvem=llm_nuvem,
        llm_fontes=llm_fontes,
    )


__all__ = ["Avaliacao", "Etapa", "Orquestrador", "PainelCompartilhado", "Tarefa", "criar_orquestrador", "planejar"]
