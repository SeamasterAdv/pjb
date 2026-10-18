"""Agente relator: explica o que foi feito e produzido, num relatório salvo junto dos arquivos."""
import logging
import os
from datetime import datetime

from agentes.base import Agente, Tarefa

logger = logging.getLogger(__name__)

NOMES_AGENTES = {
    "biblioteca": "Agente de Biblioteca",
    "pesquisa": "Agente de Pesquisa",
    "documento": "Agente de Documentos",
    "apresentacao": "Agente de Apresentações",
    "planilha": "Agente de Planilhas",
    "estudos": "Agente de Estudos",
    "aulas": "Agente Professor",
    "resumo_dia": "Agente de Resumo do Dia",
    "email": "Agente de E-mail",
    "critico": "Agente Crítico",
    "relator": "Agente Relator",
}


def _tamanho(caminho):
    try:
        bytes_ = os.path.getsize(caminho)
    except OSError:
        return "?"
    return f"{bytes_ / 1024:.0f} KB" if bytes_ < 1024 ** 2 else f"{bytes_ / 1024 ** 2:.1f} MB"


class AgenteRelator(Agente):
    nome = "relator"
    papel = "Escreve o relatório explicando o que foi feito e produzido."
    instrucoes = (
        "Você é o Agente Relator da Beatriz. Explica para o usuário, em linguagem simples e direta, "
        "o que a equipe de agentes fez, o que foi produzido e o que o crítico concluiu. "
        "A tarefa já terminou: descreva o estado final, não prometa melhorias futuras. "
        "Se o resultado foi aprovado, apresente os problemas apontados como sugestões; se foi "
        "reprovado, diga com clareza o que ficou faltando. Escreva de 3 a 5 frases, em terceira "
        "pessoa ('os agentes...', 'o crítico...'), sem markdown e sem listas."
    )

    def explicacao(self, tarefa: Tarefa) -> str:
        avaliacao = tarefa.avaliacao
        etapas = "; ".join(f"{NOMES_AGENTES.get(e.agente, e.agente)}: {e.descricao}" for e in tarefa.etapas)
        prompt = (
            f"Pedido: {tarefa.pedido}\nEtapas: {etapas}\n"
            f"Arquivos: {', '.join(os.path.basename(a) for a in tarefa.arquivos) or 'nenhum (resposta em texto)'}\n"
            f"Revisões feitas após crítica: {max(0, len(tarefa.avaliacoes) - 1)}\n"
            f"Avaliação final do crítico: nota {avaliacao.nota if avaliacao else '?'}/10, "
            f"{'APROVADO' if avaliacao and avaliacao.cumprido else 'REPROVADO'}; "
            f"problemas: {'; '.join(avaliacao.problemas) if avaliacao and avaliacao.problemas else 'nenhum'}."
        )
        try:
            return self.pedir_texto(prompt, max_tokens=300)
        except Exception as exc:
            logger.warning("Relator sem LLM, usando explicação padrão: %s", exc)
            return f"A tarefa passou por {len(tarefa.etapas)} etapas e gerou {len(tarefa.arquivos)} arquivo(s)."

    def montar_relatorio(self, tarefa: Tarefa, explicacao: str) -> str:
        avaliacao = tarefa.avaliacao
        duracao = (tarefa.concluida_em or datetime.now().timestamp()) - tarefa.criada_em
        linhas = [
            f"# Relatório da tarefa",
            "",
            f"**Pedido:** {tarefa.pedido}",
            f"**Data:** {datetime.fromtimestamp(tarefa.criada_em):%d/%m/%Y %H:%M} · **Duração:** {duracao:.0f} s",
            f"**Resultado:** {'cumprido' if avaliacao and avaliacao.cumprido else 'entregue com ressalvas'}"
            + (f" · nota do crítico {avaliacao.nota}/10" if avaliacao else ""),
            f"**IA usada:** {', '.join(tarefa.contexto.get('modelos') or ['Ollama (local)'])}",
            "",
            "## O que foi feito",
            "",
            explicacao,
            "",
            "## Etapas e agentes",
            "",
            "| Agente | O que fez | Tempo |",
            "|---|---|---|",
        ]
        linhas += [
            f"| {NOMES_AGENTES.get(e.agente, e.agente)} | {e.descricao.replace('|', '/')} | {e.duracao:.1f} s |"
            for e in tarefa.etapas
        ]
        linhas.append(f"| {NOMES_AGENTES['relator']} | Escreveu este relatório | — |")
        linhas += ["", "## Arquivos produzidos", ""]
        if tarefa.arquivos:
            linhas += [f"- `{os.path.basename(a)}` ({_tamanho(a)})" for a in tarefa.arquivos]
        else:
            linhas += ["- Nenhum arquivo: a resposta foi em texto.", "", tarefa.contexto.get("resposta", "")]
        conteudo = tarefa.contexto.get("conteudo") or {}
        estrutura = [s.get("titulo") for s in (conteudo.get("secoes") or conteudo.get("slides") or []) if s.get("titulo")]
        if estrutura:
            linhas += ["", "**Estrutura:** " + " → ".join(estrutura)]
        fontes = list(dict.fromkeys(tarefa.contexto.get("fontes", [])))
        linhas += ["", "## Fontes consultadas", ""]
        linhas += [f"- {f}" for f in fontes] or ["- Nenhuma fonte externa; conteúdo baseado no conhecimento do modelo."]
        if tarefa.contexto.get("web"):
            linhas += ["", "O texto completo lido na internet está em `fontes.md`, nesta mesma pasta."]
        if avaliacao:
            linhas += ["", "## Avaliação do crítico", ""]
            linhas += [f"- {'✔' if passou else '✘'} {descricao}" for descricao, passou in avaliacao.verificacoes]
            if avaliacao.pontos_fortes:
                linhas += ["", "**Pontos fortes:**"] + [f"- {p}" for p in avaliacao.pontos_fortes]
            if avaliacao.problemas:
                linhas += ["", "**Problemas apontados:**"] + [f"- {p}" for p in avaliacao.problemas]
            if len(tarefa.avaliacoes) > 1:
                linhas += ["", f"O resultado foi revisado {len(tarefa.avaliacoes) - 1} vez(es) após críticas."]
        return "\n".join(linhas) + "\n"

    def executar(self, tarefa: Tarefa) -> str:
        texto = self.montar_relatorio(tarefa, self.explicacao(tarefa))
        os.makedirs(tarefa.pasta, exist_ok=True)
        tarefa.relatorio = os.path.join(tarefa.pasta, "relatorio.md")
        with open(tarefa.relatorio, "w", encoding="utf-8") as arquivo:
            arquivo.write(texto)
        return "Escreveu o relatório da tarefa."
