"""Agentes especialistas: cada um faz uma parte específica da tarefa."""
import json
import logging
import os
import re

from agentes import geradores
from agentes.base import Agente, Tarefa

logger = logging.getLogger(__name__)

LIMITE_CONTEXTO_WEB = 7000
LIMITE_CONTEXTO_BIBLIOTECA = 4000


def _bloco_contexto(tarefa: Tarefa) -> str:
    """Junta o que os agentes anteriores levantaram, para o agente redator usar."""
    partes = []
    if tarefa.contexto.get("biblioteca"):
        partes.append("TRECHOS DOS DOCUMENTOS DO USUÁRIO:\n" + tarefa.contexto["biblioteca"][:LIMITE_CONTEXTO_BIBLIOTECA])
    if tarefa.contexto.get("web"):
        partes.append("FONTES DA INTERNET:\n" + tarefa.contexto["web"][:LIMITE_CONTEXTO_WEB])
    return "\n\n".join(partes)


def _bloco_correcao(tarefa: Tarefa) -> str:
    """Quando o crítico reprova, o agente refaz o trabalho com as correções pedidas."""
    avaliacao = tarefa.avaliacao
    if not avaliacao or avaliacao.cumprido:
        return ""
    problemas = "\n".join(f"- {p}" for p in avaliacao.problemas)
    anterior = json.dumps(tarefa.contexto.get("conteudo", {}), ensure_ascii=False)[:3000]
    return (
        "\n\nA VERSÃO ANTERIOR FOI REPROVADA PELO CRÍTICO. Problemas apontados:\n"
        f"{problemas}\nCorreção pedida: {avaliacao.correcao}\n"
        f"Versão anterior (para corrigir, não copiar os erros):\n{anterior}"
    )


# ------------------------------------------------------------- biblioteca
class AgenteBiblioteca(Agente):
    nome = "biblioteca"
    papel = "Consulta os documentos, notas do Obsidian e memórias do usuário."

    def __init__(self, buscar=None, **kwargs):
        super().__init__(**kwargs)
        if buscar is None:
            from memory import buscar_nos_documentos_modo as buscar
        self.buscar = buscar

    def executar(self, tarefa: Tarefa) -> str:
        resultado = self.buscar(tarefa.tema or tarefa.pedido) or ""
        if not resultado.strip() or resultado.startswith(("Nenhuma informação", "Erro", "Consulta de documentos desativada")):
            return "Não encontrou nada relevante nos seus documentos."
        tarefa.contexto["biblioteca"] = resultado
        arquivos = sorted(set(re.findall(r"\[Fonte: ([^|\]]+)", resultado)))
        tarefa.contexto.setdefault("fontes", []).extend(f"Biblioteca: {a.strip()}" for a in arquivos)
        return f"Encontrou trechos em {len(arquivos)} documento(s) seu(s): {', '.join(a.strip() for a in arquivos[:4])}."


# ---------------------------------------------------------------- pesquisa
class AgentePesquisa(Agente):
    nome = "pesquisa"
    papel = "Pesquisa na internet, lê as páginas e reúne as fontes."
    instrucoes = (
        "Você é o Agente de Pesquisa da Beatriz. Responde à pergunta usando apenas as fontes fornecidas, "
        "de forma direta, organizada e sem inventar dados. Cite o nome dos sites ao longo do texto."
    )

    def __init__(self, pesquisar=None, buscar_manchetes=None, **kwargs):
        super().__init__(**kwargs)
        if pesquisar is None:
            from tools import pesquisar_web_autonoma as pesquisar
        if buscar_manchetes is None:
            from tools import buscar_manchetes
        self.pesquisar = pesquisar
        self.buscar_manchetes = buscar_manchetes

    @staticmethod
    def pede_noticias(tarefa: Tarefa) -> bool:
        return bool(re.search(r"\b(not[íi]cias?|manchetes?|hoje|[úu]ltim[oa]s|recentes?)\b", tarefa.pedido, re.IGNORECASE))

    @staticmethod
    def consulta(tarefa: Tarefa) -> str:
        """Só o assunto, sem as palavras de atualidade: 'as notícias de hoje sobre carros elétricos' -> 'carros elétricos'.

        Com "notícias" na busca, o buscador devolve a página inicial dos portais em vez de matérias do assunto.
        """
        tema = tarefa.tema or tarefa.pedido
        tema = re.sub(
            r"\b(?:not[íi]cias?|manchetes?|novidades|hoje|[úu]ltim[oa]s?|recentes?|atuais|atualizad[oa]s?|principais)\b",
            " ", tema, flags=re.IGNORECASE,
        )
        # Conectivos que sobram no começo: "as  de  sobre carros elétricos" -> "carros elétricos".
        tema = re.sub(r"^(?:\s*\b(?:as|os|a|o|de|do|da|dos|das|sobre|com|em)\b)+", " ", tema, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", tema).strip(" ,.") or tarefa.pedido

    def executar(self, tarefa: Tarefa) -> str:
        assunto = self.consulta(tarefa)
        blocos = []
        tem_manchetes = False
        if self.pede_noticias(tarefa):
            try:
                manchetes = self.buscar_manchetes(assunto)
            except Exception as exc:
                logger.warning("Manchetes indisponíveis: %s", exc)
                manchetes = []
            if manchetes:
                linhas = "\n".join(f"- {titulo} ({data.replace('Notícia publicada em ', '')})" for titulo, _, data in manchetes)
                blocos.append(f"[Fonte: Google Notícias, manchetes recentes sobre {assunto}]\n{linhas}")
                tem_manchetes = True
                tarefa.contexto.setdefault("fontes", []).append(f"Google Notícias: manchetes sobre {assunto}")
        paginas = self.pesquisar(assunto, limite=5)
        if paginas and not paginas.startswith(("Nenhuma fonte", "Não foi possível")):
            blocos.append(paginas)
        if not blocos:
            return "Não conseguiu fontes na internet; o texto será feito com o conhecimento do modelo."
        conteudo = "\n\n".join(blocos)
        tarefa.contexto["web"] = conteudo
        urls = re.findall(r"^URL: (\S+)", conteudo, flags=re.MULTILINE)
        tarefa.contexto.setdefault("fontes", []).extend(urls)
        # Guarda o texto lido para o usuário poder conferir de onde veio cada informação.
        os.makedirs(tarefa.pasta, exist_ok=True)
        with open(os.path.join(tarefa.pasta, "fontes.md"), "w", encoding="utf-8") as arquivo:
            arquivo.write(f"# Fontes lidas pelo Agente de Pesquisa\n\nConsulta: {self.consulta(tarefa)}\n\n{conteudo}\n")

        # Pesquisa sem documento: este agente também escreve a resposta final.
        if tarefa.plano and tarefa.plano[-1] == self.nome:
            tarefa.contexto["resposta"] = self.pedir_texto(
                f"Pergunta: {tarefa.pedido}\n\n{_bloco_contexto(tarefa)}{_bloco_correcao(tarefa)}\n\n"
                "Escreva uma resposta completa em 2 a 4 parágrafos curtos, sem markdown.",
                max_tokens=700,
            )
        manchetes = "as manchetes recentes e " if tem_manchetes else ""
        return f"Leu {manchetes}{len(urls)} página(s) da internet sobre '{assunto}'."


# --------------------------------------------------------------- documento
class AgenteDocumento(Agente):
    nome = "documento"
    papel = "Escreve o conteúdo e gera os arquivos Word e PDF."
    instrucoes = (
        "Você é o Agente de Documentos da Beatriz, um redator técnico cuidadoso. "
        "Produz documentos bem estruturados, claros e úteis, em português do Brasil. "
        "Responda SOMENTE com um objeto JSON neste formato:\n"
        '{"titulo": str, "subtitulo": str, "resumo": str, '
        '"secoes": [{"titulo": str, "paragrafos": [str], "topicos": [str], '
        '"tabela": {"colunas": [str], "linhas": [[str]]} ou null}], "conclusao": str}\n'
        "Regras: de 3 a 6 seções (2 a 3 se pedirem algo curto, até 7 se pedirem algo detalhado); "
        "parágrafos de 3 a 5 frases com informação concreta (explique o porquê e dê exemplos); "
        "tópicos são frases completas, não palavras soltas; use tabela só quando ajudar a comparar; "
        "não invente números, datas ou estatísticas que não estejam nas fontes; "
        "quando houver fontes, baseie o texto nelas."
    )

    def escrever_conteudo(self, tarefa: Tarefa) -> dict:
        prompt = (
            f"Pedido do usuário: {tarefa.pedido}\nTema: {tarefa.tema}\n\n"
            f"{_bloco_contexto(tarefa) or 'Sem fontes externas: use conhecimento geral consolidado.'}"
            f"{_bloco_correcao(tarefa)}"
        )
        return self.pedir_json(prompt, max_tokens=5000)

    def executar(self, tarefa: Tarefa) -> str:
        conteudo = self.escrever_conteudo(tarefa)
        conteudo["fontes"] = list(dict.fromkeys(tarefa.contexto.get("fontes", [])))
        tarefa.contexto["conteudo"] = conteudo
        formatos = [f for f in tarefa.formatos if f in ("docx", "pdf")] or ["docx"]
        tarefa.arquivos = [geradores.gerar_arquivo(f, conteudo, tarefa.pasta) for f in formatos]
        secoes = len(conteudo.get("secoes") or [])
        return f"Escreveu '{conteudo.get('titulo')}' com {secoes} seções e gerou {', '.join(f.upper() for f in formatos)}."


# ------------------------------------------------------------ apresentação
class AgenteApresentacao(Agente):
    nome = "apresentacao"
    papel = "Monta o roteiro dos slides e gera o PowerPoint."
    instrucoes = (
        "Você é o Agente de Apresentações da Beatriz, especialista em slides objetivos e visuais. "
        "Responda SOMENTE com um objeto JSON neste formato:\n"
        '{"titulo": str, "subtitulo": str, '
        '"slides": [{"titulo": str, "topicos": [str], "destaque": str, "notas": str}]}\n'
        "Regras: cada slide com 3 a 5 tópicos; cada tópico é uma frase informativa completa de 6 a 14 "
        "palavras que ensina algo concreto (ruim: 'Baterias'; bom: 'Baterias guardam a energia do dia "
        "para usar à noite'); "
        "'destaque' é a mensagem-chave do slide em até 15 palavras (pode ser vazio); "
        "'notas' são 2 ou 3 frases para quem vai apresentar; o primeiro slide apresenta o tema "
        "e o roteiro, o último traz conclusão ou próximos passos; não invente dados sem fonte."
    )

    def executar(self, tarefa: Tarefa) -> str:
        quantidade = tarefa.quantidade_slides or 7
        prompt = (
            f"Pedido do usuário: {tarefa.pedido}\nTema: {tarefa.tema}\n"
            f"Quantidade de slides de conteúdo (sem contar a capa): exatamente {quantidade}.\n\n"
            f"{_bloco_contexto(tarefa) or 'Sem fontes externas: use conhecimento geral consolidado.'}"
            f"{_bloco_correcao(tarefa)}"
        )
        conteudo = self.pedir_json(prompt, max_tokens=3000)
        slides = [s for s in (conteudo.get("slides") or []) if isinstance(s, dict) and s.get("titulo")]
        if len(slides) > quantidade:
            # O modelo passou da quantidade pedida: mantém os primeiros e o slide de conclusão.
            slides = slides[: quantidade - 1] + [slides[-1]]
        conteudo["slides"] = slides
        conteudo["fontes"] = list(dict.fromkeys(tarefa.contexto.get("fontes", [])))
        tarefa.contexto["conteudo"] = conteudo
        arquivos = [geradores.gerar_arquivo("pptx", conteudo, tarefa.pasta)]
        # Pediram também um PDF ou Word junto da apresentação: gera um documento com os mesmos tópicos.
        extras = [f for f in tarefa.formatos if f in ("pdf", "docx")]
        if extras:
            documento = {
                "titulo": conteudo.get("titulo"), "subtitulo": conteudo.get("subtitulo"),
                "secoes": [{"titulo": s.get("titulo"), "topicos": s.get("topicos"),
                            "paragrafos": [s["notas"]] if s.get("notas") else []} for s in conteudo.get("slides") or []],
                "fontes": conteudo["fontes"],
            }
            arquivos += [geradores.gerar_arquivo(f, documento, tarefa.pasta) for f in extras]
        tarefa.arquivos = arquivos
        return f"Montou '{conteudo.get('titulo')}' com {len(conteudo.get('slides') or [])} slides de conteúdo."
