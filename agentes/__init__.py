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

    return Orquestrador(
        pasta_saida=PASTA_DOCUMENTOS_GERADOS,
        modelo=MODELO_AGENTES,
        publicar=publicar,
        max_revisoes=MAX_REVISOES_AGENTES,
        abrir_ao_concluir=ABRIR_DOCUMENTOS_GERADOS,
    )


__all__ = ["Avaliacao", "Etapa", "Orquestrador", "PainelCompartilhado", "Tarefa", "criar_orquestrador", "planejar"]
