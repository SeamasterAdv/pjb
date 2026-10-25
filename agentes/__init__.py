"""Agentes da Beatriz: especialistas por tarefa, crítico, relator e o orquestrador que os coordena."""
from agentes.base import Avaliacao, Etapa, Tarefa
from agentes.orquestrador import (
    TIPOS_DE_DOCUMENTO,
    TIPOS_DE_ESTUDO,
    Orquestrador,
    PainelCompartilhado,
    descricao_do_arquivo_pedido,
    pede_envio,
    pedido_de_arquivo,
    planejar,
    texto_da_entrega,
)


TEMPO_LIMITE_CLAUDE_AGENTES = 900  # segundos: fichas, aulas e documentos longos levam minutos por chamada


def criar_orquestrador(publicar=None) -> Orquestrador:
    """Orquestrador com as configurações do .env (pasta de saída, modelo, revisões)."""
    from config import (
        ABRIR_DOCUMENTOS_GERADOS,
        DOCUMENTOS_COM_CLAUDE,
        ESTUDOS_COM_CLAUDE,
        MAX_REVISOES_AGENTES,
        MODELO_AGENTES,
        PASTA_DOCUMENTOS_GERADOS,
    )
    from llm_clients import llm_clients
    from llm_nuvem import ClienteClaudeCode, LLMComReserva, claude, provedores_nuvem

    # Um Claude só para as tarefas, que espera mais que o da conversa (5 min) e divide a pausa entre a cadeia comum e a
    # das fontes: se o limite do plano acabar numa tarefa, a próxima já sabe.
    claude_agentes = ClienteClaudeCode(executavel=claude.executavel, tempo_limite=TEMPO_LIMITE_CLAUDE_AGENTES)
    # Os agentes escrevem com o Claude (assinatura) ou o Gemini (gratuito), na ordem de ORDEM_IA_AGENTES,
    # e caem no Ollama se nenhum puder responder (limite, sem login, sem internet).
    provedores = [claude_agentes if p is claude else p for p in provedores_nuvem("agentes")]
    llm_nuvem = LLMComReserva(provedores, llm_clients) if provedores else None
    # As manchetes do resumo do dia são curtas e públicas: vão na ordem da pesquisa (Gemini primeiro).
    publicos = provedores_nuvem("pesquisa")
    llm_publico = LLMComReserva(publicos, llm_clients) if publicos else None
    # Material de estudo e documentos com as fontes do usuário: só o Claude lê os documentos.
    tipos = (TIPOS_DE_ESTUDO if ESTUDOS_COM_CLAUDE else ()) + (TIPOS_DE_DOCUMENTO if DOCUMENTOS_COM_CLAUDE else ())
    llm_fontes = None
    if tipos and claude.ativo and claude.configurado():
        llm_fontes = LLMComReserva([claude_agentes], llm_clients)
    return Orquestrador(
        pasta_saida=PASTA_DOCUMENTOS_GERADOS,
        modelo=MODELO_AGENTES,
        publicar=publicar,
        max_revisoes=MAX_REVISOES_AGENTES,
        abrir_ao_concluir=ABRIR_DOCUMENTOS_GERADOS,
        llm_nuvem=llm_nuvem,
        llm_fontes=llm_fontes,
        llm_publico=llm_publico,
        tipos_fontes_no_claude=tipos,
    )


__all__ = ["Avaliacao", "Etapa", "Orquestrador", "PainelCompartilhado", "Tarefa", "criar_orquestrador", "planejar"]
