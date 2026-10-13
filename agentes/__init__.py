"""Agentes da Beatriz: especialistas por tarefa, crítico, relator e o orquestrador que os coordena."""
from agentes.base import Avaliacao, Etapa, Tarefa
from agentes.orquestrador import Orquestrador, PainelCompartilhado, planejar


def criar_orquestrador(publicar=None) -> Orquestrador:
    """Orquestrador com as configurações do .env (pasta de saída, modelo, revisões)."""
    from config import (
        ABRIR_DOCUMENTOS_GERADOS,
        MAX_REVISOES_AGENTES,
        MODELO_AGENTES,
        PASTA_DOCUMENTOS_GERADOS,
    )
    from llm_clients import llm_clients
    from llm_nuvem import LLMComReserva, provedores_nuvem

    # Os agentes escrevem com o Claude (assinatura) ou o Gemini (gratuito), nessa ordem,
    # e caem no Ollama se nenhum puder responder (limite, sem login, sem internet).
    provedores = provedores_nuvem()
    llm_nuvem = LLMComReserva(provedores, llm_clients) if provedores else None
    return Orquestrador(
        pasta_saida=PASTA_DOCUMENTOS_GERADOS,
        modelo=MODELO_AGENTES,
        publicar=publicar,
        max_revisoes=MAX_REVISOES_AGENTES,
        abrir_ao_concluir=ABRIR_DOCUMENTOS_GERADOS,
        llm_nuvem=llm_nuvem,
    )


__all__ = ["Avaliacao", "Etapa", "Orquestrador", "PainelCompartilhado", "Tarefa", "criar_orquestrador", "planejar"]
