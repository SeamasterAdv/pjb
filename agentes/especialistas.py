"""Agentes especialistas: cada um faz uma parte específica da tarefa."""
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from agentes import geradores
from agentes.base import Agente, Tarefa, TarefaImpossivel, normalizar

logger = logging.getLogger(__name__)

LIMITE_CONTEXTO_WEB = 7000
LIMITE_CONTEXTO_BIBLIOTECA = 4000
# Material de estudo com o Claude: as fontes da matéria vão inteiras (até LIMITE_FONTES_ESTUDO no total).
LIMITE_POR_FONTE = 40000
TRECHOS_MATERIA = 20  # trechos da busca usados para achar os arquivos da matéria
DIAS_FONTES_RECENTES = 7
MAXIMO_FONTES_RECENTES = 8
EXTENSOES_DE_ESTUDO = (".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".html", ".htm", ".png", ".jpg", ".jpeg")


def _bloco_contexto(tarefa: Tarefa) -> str:
    """Junta o que os agentes anteriores levantaram, para o agente redator usar."""
    partes = []
    if tarefa.contexto.get("biblioteca"):
        # Com o Claude, as fontes já vêm no tamanho certo (o agente da biblioteca limita o total).
        limite = None if tarefa.fontes_no_claude else LIMITE_CONTEXTO_BIBLIOTECA
        partes.append("TRECHOS DOS DOCUMENTOS DO USUÁRIO:\n" + tarefa.contexto["biblioteca"][:limite])
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
def fonte_pessoal(caminho: str) -> bool:
    """Diário, memória do usuário e histórico de conversa: nunca entram num material que vai para a nuvem."""
    from config import ARQUIVO_MEMORIA_USUARIO, PASTAS_OBSIDIAN

    real = os.path.normcase(os.path.realpath(caminho or ""))
    if real.endswith((".json", ".jsonl")):
        return True  # histórico de conversa, estado das aulas e afins: são dados, não fontes de estudo
    if real == os.path.normcase(os.path.realpath(ARQUIVO_MEMORIA_USUARIO)):
        return True
    diario = os.path.normcase(os.path.realpath(PASTAS_OBSIDIAN["diario"]))
    return real.startswith(diario + os.sep)


def fontes_recentes(raizes=None, dias: int = DIAS_FONTES_RECENTES, maximo: int = MAXIMO_FONTES_RECENTES, agora=None) -> list:
    """Arquivos de estudo colocados por último na biblioteca (documentos e Obsidian), do mais novo ao mais antigo.

    Conta quando o arquivo chegou na pasta (a criação, no Windows), porque a cópia feita ao anexar
    mantém a data de modificação do original. Sem nada nos últimos `dias`, devolve os três mais novos.
    """
    if raizes is None:
        from config import CAMINHO_DOCUMENTOS, VAULT_OBSIDIAN
        raizes = (CAMINHO_DOCUMENTOS, VAULT_OBSIDIAN)
    achados = {}
    for raiz in raizes:
        for pasta, subpastas, nomes in os.walk(raiz):
            subpastas[:] = [s for s in subpastas if not s.startswith(".")]  # .obsidian, .trash
            for nome in nomes:
                caminho = os.path.join(pasta, nome)
                if not nome.lower().endswith(EXTENSOES_DE_ESTUDO) or fonte_pessoal(caminho):
                    continue
                try:
                    achados[os.path.realpath(caminho)] = max(os.path.getctime(caminho), os.path.getmtime(caminho))
                except OSError:
                    continue
    ordem = sorted(achados, key=achados.get, reverse=True)
    limite = (agora or time.time()) - dias * 86400
    return [c for c in ordem if achados[c] >= limite][:maximo] or ordem[:3]


RE_BLOCO_FONTE = re.compile(r"^\[Fonte: ([^|\]]*)\|([^|\]]*)\|([^|\]]*)\|([^\]]*)\]\n", re.MULTILINE)


def trechos_por_arquivo(resultado: str) -> dict:
    """{caminho: [(localizador, trecho)]} a partir da busca da biblioteca, na ordem de relevância e sem fontes pessoais."""
    achados = list(RE_BLOCO_FONTE.finditer(resultado or ""))
    trechos = {}
    for indice, achado in enumerate(achados):
        fim = achados[indice + 1].start() if indice + 1 < len(achados) else len(resultado)
        tipo, localizador, caminho = (parte.strip() for parte in achado.groups()[1:])
        if tipo == "historico" or not caminho or fonte_pessoal(caminho):
            continue
        trechos.setdefault(caminho, []).append((localizador, resultado[achado.end():fim].strip()))
    return trechos


class AgenteBiblioteca(Agente):
    nome = "biblioteca"
    papel = "Consulta os documentos, notas do Obsidian e memórias do usuário."

    def __init__(self, buscar=None, ler_arquivo=None, listar_recentes=None, limite_fontes=None, **kwargs):
        super().__init__(**kwargs)
        if buscar is None:
            from memory import buscar_nos_documentos_modo as buscar
            self._buscar_padrao = buscar
        self.buscar = buscar
        self.ler_arquivo = ler_arquivo
        self.listar_recentes = listar_recentes or fontes_recentes
        self.limite_fontes = limite_fontes

    def escolher_fontes_recentes(self) -> list:
        return [c for c in self.listar_recentes() if not fonte_pessoal(c)]

    def _buscar_para_materia(self, consulta: str) -> str:
        """Mais trechos que o normal, para achar todos os arquivos da matéria."""
        if self.buscar is getattr(self, "_buscar_padrao", None):
            return self.buscar(consulta, quantidade_trechos=TRECHOS_MATERIA)
        return self.buscar(consulta)

    def _ler(self, caminho: str, limite: int) -> str:
        if self.ler_arquivo is None:
            from memory import extrair_texto_arquivo_otimizado
            self.ler_arquivo = extrair_texto_arquivo_otimizado
        try:
            return (self.ler_arquivo(caminho, limite) or "").strip()
        except Exception as exc:
            logger.warning("Não foi possível ler %s: %s", caminho, exc)
            return ""

    def executar(self, tarefa: Tarefa) -> str:
        if tarefa.fontes_recentes:
            caminhos = tarefa.contexto.get("fontes_escolhidas") or self.escolher_fontes_recentes()
            if not caminhos:
                raise TarefaImpossivel(
                    "Não achei fontes suas na biblioteca para resumir. Anexe os arquivos no painel (ou coloque na "
                    "pasta documentos_assistente) e peça de novo: resume as fontes que eu coloquei.")
            return self._guardar_fontes(tarefa, caminhos, {})
        consulta = tarefa.tema or tarefa.pedido
        resultado = (self._buscar_para_materia(consulta) if tarefa.fontes_no_claude else self.buscar(consulta)) or ""
        if not resultado.strip() or resultado.startswith(("Nenhuma informação", "Erro", "Consulta de documentos desativada")):
            return "Não encontrou nada relevante nos seus documentos."
        if tarefa.fontes_no_claude:
            # O Claude lê os arquivos da matéria inteiros, não só os trechos; diário, memória e histórico ficam de fora.
            trechos = trechos_por_arquivo(resultado)
            if not trechos:
                return "Não encontrou fontes de estudo sobre o assunto nos seus documentos."
            return self._guardar_fontes(tarefa, list(trechos), trechos)
        tarefa.contexto["biblioteca"] = resultado
        arquivos = sorted(set(re.findall(r"\[Fonte: ([^|\]]+)", resultado)))
        tarefa.contexto.setdefault("fontes", []).extend(f"Biblioteca: {a.strip()}" for a in arquivos)
        return f"Encontrou trechos em {len(arquivos)} documento(s) seu(s): {', '.join(a.strip() for a in arquivos[:4])}."

    def _guardar_fontes(self, tarefa: Tarefa, caminhos: list, trechos: dict) -> str:
        """Junta as fontes em `contexto["biblioteca"]`: arquivos inteiros quando cabem; dos grandes, os trechos achados."""
        if tarefa.fontes_no_claude:
            if self.limite_fontes is None:
                from config import LIMITE_FONTES_ESTUDO
                self.limite_fontes = LIMITE_FONTES_ESTUDO
            restante = self.limite_fontes
        else:
            restante = LIMITE_CONTEXTO_BIBLIOTECA  # o modelo local não comporta mais que isso
        blocos, usados = [], []
        for caminho in caminhos:
            if restante < 500:
                break
            nome = os.path.basename(caminho)
            cabe = min(LIMITE_POR_FONTE, restante)
            texto = self._ler(caminho, cabe + 1)
            if texto and len(texto) <= cabe:
                bloco = f"[Fonte: {nome} | arquivo inteiro]\n{texto}"
            elif trechos.get(caminho):
                bloco = "\n\n".join(f"[Fonte: {nome} | {localizador}]\n{trecho}" for localizador, trecho in trechos[caminho])
            elif texto:
                bloco = f"[Fonte: {nome} | começo do arquivo]\n{texto[:cabe]}"
            else:
                continue
            blocos.append(bloco[:restante])
            usados.append(nome)
            restante -= len(blocos[-1]) + 2
        if not blocos:
            if tarefa.fontes_recentes:
                raise TarefaImpossivel("Não consegui ler o texto das suas fontes recentes (arquivos vazios ou protegidos).")
            return "Não conseguiu ler as fontes encontradas."
        tarefa.contexto["biblioteca"] = "\n\n".join(blocos)
        tarefa.contexto.setdefault("fontes", []).extend(f"Biblioteca: {nome}" for nome in usados)
        para_quem = " para o Claude" if tarefa.fontes_no_claude else ""
        mais = f" e mais {len(usados) - 4}" if len(usados) > 4 else ""
        return f"Leu {len(usados)} fonte(s) sua(s){para_quem}: {', '.join(usados[:4])}{mais}."


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


# ---------------------------------------------------------------- planilha
class AgentePlanilha(Agente):
    nome = "planilha"
    papel = "Monta tabelas, fórmulas e gráficos e gera a planilha do Excel."
    instrucoes = (
        "Você é o Agente de Planilhas da Beatriz, especialista em Excel. "
        "Responda SOMENTE com um objeto JSON neste formato:\n"
        '{"titulo": str, "abas": [{"nome": str, "colunas": [str], '
        '"tipos": [um por coluna: "texto", "numero", "inteiro", "moeda", "percentual" ou "data"], '
        '"linhas": [[valores na ordem das colunas]], "total": true ou false, '
        '"grafico": {"tipo": "barras", "linhas" ou "pizza", "titulo": str, "categorias": nome de uma coluna, '
        '"valores": [nomes de colunas numéricas]} ou null}], "observacoes": str}\n'
        "Regras: números como números (sem 'R$' nem '%'); datas no formato AAAA-MM-DD; de 5 a 30 linhas "
        "por aba; \"total\": true quando somar as colunas numéricas fizer sentido; inclua um gráfico quando "
        "ele ajudar a enxergar os dados ou quando pedirem; para modelos e controles (gastos, metas, estudos) "
        "preencha linhas de exemplo realistas e diga em 'observacoes' que são exemplos; com fontes, use só os "
        "dados delas e diga de onde vieram em 'observacoes'; nunca invente estatísticas reais."
    )

    def executar(self, tarefa: Tarefa) -> str:
        prompt = (
            f"Pedido do usuário: {tarefa.pedido}\nTema: {tarefa.tema}\n\n"
            f"{_bloco_contexto(tarefa) or 'Sem fontes externas.'}{_bloco_correcao(tarefa)}"
        )
        conteudo = self.pedir_json(prompt, max_tokens=5000)
        conteudo["fontes"] = list(dict.fromkeys(tarefa.contexto.get("fontes", [])))
        tarefa.contexto["conteudo"] = conteudo
        tarefa.arquivos = [geradores.gerar_arquivo("xlsx", conteudo, tarefa.pasta)]
        abas = [a for a in conteudo.get("abas") or [] if isinstance(a, dict)]
        linhas = sum(len(a.get("linhas") or []) for a in abas)
        return f"Montou '{conteudo.get('titulo')}' com {len(abas)} aba(s) e {linhas} linhas."


# ----------------------------------------------------------------- estudos
LETRAS = "ABCD"


def _questao_valida(questao):
    """Normaliza uma questão de múltipla escolha; None se não tiver 4 alternativas e gabarito."""
    if not isinstance(questao, dict) or not questao.get("enunciado"):
        return None
    alternativas = [re.sub(r"^\s*[A-Da-d][).:-]\s*", "", str(a)).strip() for a in (questao.get("alternativas") or [])]
    correta = questao.get("correta")
    if isinstance(correta, int) and 0 <= correta < 4:
        correta = LETRAS[correta]
    correta = str(correta or "").strip()[:1].upper()
    if len(alternativas) != 4 or correta not in LETRAS:
        return None
    return {**questao, "alternativas": alternativas, "correta": correta}


def _secao_do_topico(topico: dict) -> dict:
    """Um tópico do material: explicação (um ou vários parágrafos), exemplo, pegadinhas, fonte e pontos-chave."""
    paragrafos = geradores._lista(topico.get("paragrafos")) or ([topico["explicacao"]] if topico.get("explicacao") else [])
    for chave, rotulo in (("exemplo", "Exemplo"), ("atencao", "Atenção"), ("fonte", "Fonte")):
        if topico.get(chave):
            paragrafos.append(f"{rotulo}: {topico[chave]}")
    return {"titulo": topico.get("titulo"), "paragrafos": paragrafos, "topicos": topico.get("pontos_chave")}


def documento_de_estudo(conteudo: dict, materia: bool = False) -> dict:
    """Material de estudo no formato de documento (PDF/Word): tópicos, flashcards, questões e gabarito.

    `materia`: o resumo completo de uma matéria, que também traz o glossário e o que veio de fora das fontes.
    """
    secoes = [_secao_do_topico(t) for t in conteudo.get("topicos") or [] if isinstance(t, dict) and t.get("titulo")]
    glossario = [g for g in conteudo.get("glossario") or [] if isinstance(g, dict) and g.get("termo") and g.get("definicao")]
    if glossario:
        secoes.append({"titulo": "Glossário",
                       "tabela": {"colunas": ["Termo", "Definição"], "linhas": [[g["termo"], g["definicao"]] for g in glossario]}})
    flashcards = conteudo.get("flashcards") or []
    if flashcards:
        secoes.append({
            "titulo": "Flashcards",
            "paragrafos": ["Os mesmos cartões estão no arquivo para o Anki, na mesma pasta (Arquivo > Importar)."],
            "tabela": {"colunas": ["Pergunta", "Resposta"], "linhas": [[c["frente"], c["verso"]] for c in flashcards]},
        })
    questoes = conteudo.get("questoes") or []
    for numero, questao in enumerate(questoes, start=1):
        secoes.append({
            "titulo": f"Questão {numero}",
            "paragrafos": [questao["enunciado"]],
            "topicos": [f"{letra}) {texto}" for letra, texto in zip(LETRAS, questao["alternativas"])],
        })
    if questoes:
        secoes.append({
            "titulo": "Gabarito comentado",
            "paragrafos": [f"{n}. {q['correta']}: {q.get('explicacao', '')}".strip() for n, q in enumerate(questoes, start=1)],
        })
    if conteudo.get("dicas"):
        secoes.append({"titulo": "Dicas de estudo", "topicos": conteudo["dicas"]})
    fora = geradores._lista(conteudo.get("fora_das_fontes"))
    if fora:
        secoes.append({"titulo": "Complementado além das suas fontes",
                       "paragrafos": ["Estes assuntos vieram do conhecimento geral da IA, não dos seus arquivos; confira no material da aula."],
                       "topicos": fora})
    return {
        "titulo": conteudo.get("titulo") or "Material de estudo",
        "subtitulo": ("Resumo da matéria: tópicos, glossário, flashcards e questões" if materia
                      else "Material de estudo: resumo, flashcards e questões"),
        "resumo": conteudo.get("resumo"),
        "secoes": secoes,
        "fontes": conteudo.get("fontes"),
    }


class AgenteEstudos(Agente):
    nome = "estudos"
    papel = "Prepara resumo, flashcards e questões de prova."
    instrucoes = (
        "Você é o Agente de Estudos da Beatriz, um professor didático e rigoroso. "
        "Responda SOMENTE com um objeto JSON neste formato:\n"
        '{"titulo": str, "resumo": str, "topicos": [{"titulo": str, "explicacao": str, "pontos_chave": [str]}], '
        '"flashcards": [{"frente": str, "verso": str}], '
        '"questoes": [{"enunciado": str, "alternativas": [4 textos, sem as letras], '
        '"correta": "A", "B", "C" ou "D", "explicacao": str}], "dicas": [str]}\n'
        "Regras: explique do básico ao avançado, com exemplos; de 4 a 7 tópicos; a frente do flashcard é uma "
        "pergunta curta e o verso, uma resposta objetiva; questões no estilo de prova, com uma única alternativa "
        "correta e distratores plausíveis, variando a letra correta; a explicação diz por que a correta está certa; "
        "quando houver trechos dos documentos do usuário, baseie-se neles."
    )
    # Resumo da matéria inteira, escrito pelo Claude a partir das fontes do usuário.
    instrucoes_materia = (
        "Você é o Agente de Estudos da Beatriz, um professor didático e rigoroso. Prepara o resumo completo de "
        "uma matéria para o aluno estudar sozinho, no computador ou no celular, a partir das fontes dele. "
        "Responda SOMENTE com um objeto JSON neste formato:\n"
        '{"titulo": str, "resumo": str, "topicos": [{"titulo": str, "paragrafos": [str], "pontos_chave": [str], '
        '"exemplo": str, "atencao": str, "fonte": str}], "glossario": [{"termo": str, "definicao": str}], '
        '"flashcards": [{"frente": str, "verso": str}], '
        '"questoes": [{"enunciado": str, "alternativas": [4 textos, sem as letras], '
        '"correta": "A", "B", "C" ou "D", "explicacao": str}], "dicas": [str], "fora_das_fontes": [str]}\n'
        "Regras: cubra a matéria inteira que está nas fontes, na ordem em que faz sentido estudar, do básico ao "
        "avançado, em 6 a 14 tópicos; 'resumo' é a visão geral da matéria em um parágrafo; cada tópico tem de 2 a 4 "
        "parágrafos de 3 a 6 frases que explicam o conceito e o porquê, com a terminologia das fontes; "
        "'pontos_chave' são de 3 a 6 frases completas para revisar; 'exemplo' é um caso concreto; 'atencao' traz "
        "confusões comuns e pegadinhas de prova (pode ser vazio); 'fonte' é o nome do arquivo de onde veio o "
        "conteúdo, como aparece em [Fonte: ...] (vazio se não veio das fontes); glossário com 8 a 20 termos; "
        "flashcards e questões cobrem todos os tópicos; questões no estilo de prova, com uma única alternativa "
        "correta, distratores plausíveis e a letra correta variando; a explicação da questão diz por que a correta "
        "está certa e as outras não. Baseie-se primeiro nas fontes do usuário; quando precisar completar com "
        "conhecimento consolidado, complete e liste em 'fora_das_fontes' os assuntos que não vieram delas. Nunca "
        "invente artigos de lei, datas, números, autores ou citações."
    )

    def executar(self, tarefa: Tarefa) -> str:
        n_cartoes, n_questoes = tarefa.quantidade_estudo("flashcards"), tarefa.quantidade_estudo("questoes")
        # O resumo completo da matéria só com o Claude: o modelo local não comporta as fontes inteiras.
        materia = tarefa.materia and tarefa.fontes_no_claude
        if materia:
            assunto = ("a das fontes acima, que o usuário acabou de colocar" if tarefa.fontes_recentes else tarefa.tema)
            # Fontes antes do pedido: se o texto passar do limite de algum modelo, o pedido não se perde no corte.
            prompt = (
                f"{_bloco_contexto(tarefa) or 'Sem fontes do usuário sobre o assunto: use conhecimento consolidado.'}\n\n---\n"
                f"Pedido do usuário: {tarefa.pedido}\nMatéria: {assunto}\n"
                f"Quantidade: exatamente {n_cartoes} flashcards e {n_questoes} questões.{_bloco_correcao(tarefa)}"
            )
            conteudo = self.pedir_json(prompt, max_tokens=16000, sistema=self._sistema(self.instrucoes_materia))
        else:
            prompt = (
                f"Pedido do usuário: {tarefa.pedido}\nTema: {tarefa.tema}\n"
                f"Quantidade: exatamente {n_cartoes} flashcards e {n_questoes} questões.\n\n"
                f"{_bloco_contexto(tarefa) or 'Sem fontes externas: use conhecimento consolidado.'}{_bloco_correcao(tarefa)}"
            )
            conteudo = self.pedir_json(prompt, max_tokens=7000)
        conteudo["flashcards"] = [
            c for c in conteudo.get("flashcards") or [] if isinstance(c, dict) and c.get("frente") and c.get("verso")
        ][:n_cartoes]
        conteudo["questoes"] = [q for q in map(_questao_valida, conteudo.get("questoes") or []) if q][:n_questoes]
        conteudo["fontes"] = list(dict.fromkeys(tarefa.contexto.get("fontes", [])))
        tarefa.contexto["conteudo"] = conteudo

        documento = documento_de_estudo(conteudo, materia=materia)
        formatos = [f for f in tarefa.formatos if f in ("pdf", "docx")] or ["pdf"]
        arquivos = [geradores.gerar_arquivo(f, documento, tarefa.pasta) for f in formatos]
        if conteudo["flashcards"]:
            nome = geradores.nome_arquivo_seguro(documento["titulo"])
            arquivos.append(geradores.gerar_flashcards_anki(
                conteudo["flashcards"], os.path.join(tarefa.pasta, f"{nome}_flashcards_anki.txt")))
        tarefa.arquivos = arquivos
        return (f"Preparou '{documento['titulo']}' com {len(conteudo.get('topicos') or [])} tópicos, "
                f"{len(conteudo['flashcards'])} flashcards e {len(conteudo['questoes'])} questões.")


# ------------------------------------------------------------------- aulas
PALAVRAS_MINIMAS_AULA = 600
AULAS_EM_PARALELO = 2  # duas aulas escritas ao mesmo tempo: o curso sai na metade do tempo


def problemas_da_aula(aula: dict) -> list:
    """O que falta para a aula ensinar de verdade; vazio se ela estiver completa."""
    if aula.get("erro"):
        return [f"não foi escrita ({aula['erro']})"]
    secoes = [s for s in aula.get("secoes") or [] if isinstance(s, dict) and s.get("titulo")]
    problemas = []
    if len(secoes) < 3:
        problemas.append(f"tem {len(secoes)} seção(ões) de explicação (mínimo 3)")
    palavras = sum(len(str(p).split()) for s in secoes for p in geradores._lista(s.get("paragrafos")))
    if palavras < PALAVRAS_MINIMAS_AULA:
        problemas.append(f"a explicação tem {palavras} palavras (mínimo {PALAVRAS_MINIMAS_AULA})")
    if not aula.get("exercicios"):
        problemas.append("não tem exercícios resolvidos")
    return problemas


def documento_da_aula(aula: dict, curso: str, fontes: list) -> dict:
    """Uma aula no formato de documento (PDF/Word): explicação, destaques, exercícios, questões e revisão."""
    secoes = []
    if aula.get("objetivos"):
        secoes.append({"titulo": "Nesta aula você vai aprender", "topicos": aula["objetivos"]})
    for secao in aula.get("secoes") or []:
        if not isinstance(secao, dict) or not secao.get("titulo"):
            continue
        paragrafos = geradores._lista(secao.get("paragrafos"))
        if secao.get("exemplo"):
            paragrafos.append(f"Exemplo: {secao['exemplo']}")
        if secao.get("importante"):
            paragrafos.append(f"Importante: {secao['importante']}")
        tabela = secao.get("tabela") if isinstance(secao.get("tabela"), dict) and secao["tabela"].get("linhas") else None
        secoes.append({"titulo": secao["titulo"], "paragrafos": paragrafos, "topicos": secao.get("topicos"), "tabela": tabela})
    if aula.get("pontos_importantes"):
        secoes.append({"titulo": "Pontos importantes (os que mais caem)", "topicos": aula["pontos_importantes"]})
    for numero, exercicio in enumerate(e for e in aula.get("exercicios") or [] if isinstance(e, dict) and e.get("enunciado")):
        secoes.append({"titulo": f"Exercício resolvido {numero + 1}",
                       "paragrafos": [exercicio["enunciado"], f"Resolução: {exercicio.get('resolucao', '')}"]})
    questoes = [q for q in map(_questao_valida, aula.get("questoes") or []) if q]
    for numero, questao in enumerate(questoes, start=1):
        secoes.append({"titulo": f"Questão {numero}", "paragrafos": [questao["enunciado"]],
                       "topicos": [f"{letra}) {texto}" for letra, texto in zip(LETRAS, questao["alternativas"])]})
    if questoes:
        secoes.append({"titulo": "Gabarito comentado",
                       "paragrafos": [f"{n}. {q['correta']}: {q.get('explicacao', '')}".strip() for n, q in enumerate(questoes, start=1)]})
    if aula.get("resumo"):
        secoes.append({"titulo": "Resumo da aula", "paragrafos": [aula["resumo"]]})
    if aula.get("para_revisar"):
        secoes.append({"titulo": "Para revisar", "topicos": aula["para_revisar"]})
    fora = geradores._lista(aula.get("fora_das_fontes"))
    if fora:
        secoes.append({"titulo": "Complementado além das suas fontes",
                       "paragrafos": ["Vieram do conhecimento geral da IA, não dos seus arquivos; confira no material da aula."],
                       "topicos": fora})
    return {"titulo": f"Aula {aula['numero']}: {aula.get('titulo') or ''}".rstrip(": "), "subtitulo": curso,
            "resumo": aula.get("introducao"), "secoes": secoes, "fontes": fontes}


def documento_do_plano(curso: dict) -> dict:
    """O plano do curso: apresentação, o que cada aula ensina e os tópicos mais importantes da matéria."""
    secoes = [{"titulo": "Os tópicos mais importantes da matéria", "topicos": curso.get("topicos_importantes")}]
    for aula in curso.get("aulas") or []:
        topicos = geradores._lista(aula.get("objetivos")) + [f"Importante: {i}" for i in geradores._lista(aula.get("importante"))]
        situacao = [f"Esta aula não ficou pronta: {aula['erro']}"] if aula.get("erro") else []
        secoes.append({"titulo": f"Aula {aula['numero']}: {aula.get('titulo', '')}", "paragrafos": situacao, "topicos": topicos})
    if curso.get("como_estudar"):
        secoes.append({"titulo": "Como estudar com estas aulas", "topicos": curso["como_estudar"]})
    return {"titulo": curso.get("titulo") or "Curso", "subtitulo": f"Plano do curso em {len(curso.get('aulas') or [])} aulas",
            "resumo": curso.get("apresentacao"), "secoes": secoes, "fontes": curso.get("fontes")}


class AgenteAulas(Agente):
    nome = "aulas"
    papel = "Planeja o curso da matéria e escreve cada aula em PDF, para ensinar tudo."
    # Primeiro o plano do curso, depois cada aula numa chamada (a matéria inteira não cabe numa resposta só).
    instrucoes = (
        "Você é o Agente Professor da Beatriz e vai montar o PLANO DO CURSO de uma matéria, para ensinar tudo a "
        "um aluno que vai estudar sozinho, a partir das fontes dele. Responda SOMENTE com um objeto JSON neste formato:\n"
        '{"titulo": str, "apresentacao": str, "aulas": [{"titulo": str, "objetivos": [str], "conteudo": [str], '
        '"importante": [str]}], "topicos_importantes": [str], "como_estudar": [str]}\n'
        "Regras: o curso cobre a matéria inteira que está nas fontes, do básico ao avançado, na ordem em que faz "
        "sentido aprender; cada aula tem um assunto bem definido, de 3 a 6 'objetivos' (o que o aluno vai saber "
        "fazer) e de 3 a 8 itens de 'conteudo' (os subtópicos que a aula explica); 'importante' são os pontos da "
        "aula que mais caem em prova; 'topicos_importantes' reúne de 8 a 15 tópicos centrais da matéria inteira; "
        "'apresentacao' explica em um parágrafo o que é a matéria e como o curso está organizado; 'como_estudar' traz "
        "de 3 a 5 dicas práticas. Baseie-se primeiro nas fontes do usuário e complete com conhecimento consolidado "
        "quando elas não cobrirem a matéria."
    )
    instrucoes_aula = (
        "Você é o Agente Professor da Beatriz e vai escrever UMA AULA COMPLETA de um curso, para um aluno que está "
        "vendo o assunto pela primeira vez e precisa dominar tudo para a prova. Responda SOMENTE com um objeto JSON "
        "neste formato:\n"
        '{"titulo": str, "introducao": str, "objetivos": [str], "secoes": [{"titulo": str, "paragrafos": [str], '
        '"topicos": [str], "exemplo": str, "importante": str, "tabela": {"colunas": [str], "linhas": [[str]]} ou null}], '
        '"pontos_importantes": [str], "exercicios": [{"enunciado": str, "resolucao": str}], '
        '"questoes": [{"enunciado": str, "alternativas": [4 textos, sem as letras], "correta": "A", "B", "C" ou "D", '
        '"explicacao": str}], "resumo": str, "para_revisar": [str], "fora_das_fontes": [str]}\n'
        "Regras: ensine como um bom professor numa aula, do básico ao avançado, explicando o conceito, o porquê e "
        "como aplicar, passo a passo; cubra todo o 'conteudo' previsto para esta aula no plano, sem repetir o que é "
        "de outras aulas; de 4 a 8 seções, cada uma com 3 a 6 parágrafos de 3 a 6 frases; um exemplo concreto por "
        "seção; tabela só quando ajudar a comparar; 'importante' destaca o que mais cai em prova e as confusões "
        "comuns; 'introducao' liga a aula ao resto do curso; de 3 a 5 exercícios com a resolução passo a passo; de 3 "
        "a 5 questões no estilo de prova, com uma única alternativa correta, distratores plausíveis e a letra correta "
        "variando; 'resumo' fecha a aula em um parágrafo; 'para_revisar' são de 3 a 6 perguntas para o aluno se "
        "testar. Use a terminologia das fontes do usuário e baseie-se nelas primeiro; o que completar com "
        "conhecimento consolidado, liste em 'fora_das_fontes'. Nunca invente artigos de lei, datas, números, autores "
        "ou citações."
    )

    def __init__(self, paralelo: int = AULAS_EM_PARALELO, **kwargs):
        super().__init__(**kwargs)
        self.paralelo = paralelo
        self.ao_progredir = None  # o orquestrador liga aqui a publicação do andamento no painel

    def _base(self, tarefa: Tarefa) -> str:
        """Começo igual em todas as chamadas do curso (fontes, pedido): o que muda fica no fim do prompt."""
        assunto = "a das fontes acima, que o usuário acabou de colocar" if tarefa.fontes_recentes else tarefa.tema
        fontes = _bloco_contexto(tarefa) or "Sem fontes do usuário sobre o assunto: ensine com conhecimento consolidado."
        return f"{fontes}\n\n---\nPedido do usuário: {tarefa.pedido}\nMatéria: {assunto}\n"

    def _avisar(self, tarefa: Tarefa, etapa: str):
        tarefa.etapa_atual = etapa
        if self.ao_progredir:
            try:
                self.ao_progredir(tarefa)
            except Exception as exc:
                logger.debug("Não foi possível publicar o andamento das aulas: %s", exc)

    def planejar_curso(self, tarefa: Tarefa) -> dict:
        self._avisar(tarefa, "planejando as aulas")
        pedidas = tarefa.quantidades.get("aulas")
        quantidade = f"exatamente {pedidas}" if pedidas else "as que a matéria pedir, de 4 a 10"
        plano = self.pedir_json(f"{self._base(tarefa)}Quantidade de aulas: {quantidade}.", max_tokens=4000)
        aulas = [a for a in plano.get("aulas") or [] if isinstance(a, dict) and a.get("titulo")][:pedidas or 12]
        if not aulas:
            raise ValueError("o plano do curso veio sem aulas")
        plano["aulas"] = [{**aula, "numero": numero} for numero, aula in enumerate(aulas, start=1)]
        return plano

    def escrever_aulas(self, tarefa: Tarefa, plano: dict, numeros: list, correcoes: dict = None) -> dict:
        """{número: aula} para as aulas pedidas, escritas `paralelo` por vez. Aula que falha volta com 'erro'."""
        resumo_plano = json.dumps(
            {"titulo": plano.get("titulo"), "aulas": [{k: a.get(k) for k in ("numero", "titulo", "conteudo")} for a in plano["aulas"]]},
            ensure_ascii=False)
        base = f"{self._base(tarefa)}PLANO DO CURSO:\n{resumo_plano}\n\n"
        prontas, trava = [0], threading.Lock()

        def escrever(numero):
            previsto = plano["aulas"][numero - 1]
            if tarefa.cancelada:
                return numero, {**previsto, "erro": "tarefa cancelada"}
            pedido = f"Escreva agora a aula {numero}: {previsto['titulo']}."
            if correcoes and correcoes.get(numero):
                pedido += f"\nA versão anterior desta aula foi reprovada: {'; '.join(correcoes[numero])}. Corrija isso."
            try:
                aula = self.pedir_json(base + pedido, max_tokens=12000, sistema=self._sistema(self.instrucoes_aula))
                # O título do plano, para o nome do arquivo não mudar quando a aula é reescrita.
                aula = {**aula, "numero": numero, "titulo": previsto["titulo"]}
            except Exception as exc:
                logger.warning("Aula %s não foi escrita: %s", numero, exc)
                aula = {**previsto, "erro": str(exc)[:160]}
            with trava:
                prontas[0] += 1
                self._avisar(tarefa, f"escrevendo as aulas ({prontas[0]} de {len(numeros)} prontas)")
            return numero, aula

        self._avisar(tarefa, f"escrevendo as aulas (0 de {len(numeros)} prontas)")
        with ThreadPoolExecutor(max_workers=max(1, self.paralelo), thread_name_prefix="aulas") as executor:
            return dict(executor.map(escrever, numeros))

    def _gerar_arquivos(self, tarefa: Tarefa, curso: dict, numeros=None) -> list:
        """Plano do curso e uma aula por arquivo; `numeros` regera só essas aulas (o resto continua igual)."""
        formato = "docx" if "docx" in tarefa.formatos else "pdf"
        os.makedirs(tarefa.pasta, exist_ok=True)

        def gerar(conteudo, nome):
            return geradores.GERADORES[formato](conteudo, os.path.join(tarefa.pasta, f"{nome}.{formato}"))

        fontes = curso.get("fontes")
        arquivos = [gerar(documento_do_plano(curso), "00_plano_do_curso")]
        for aula in curso["aulas"]:
            nome = f"{aula['numero']:02d}_aula_{geradores.nome_arquivo_seguro(aula.get('titulo') or '', 40)}"
            caminho = os.path.join(tarefa.pasta, f"{nome}.{formato}")
            if aula.get("erro"):
                continue
            if numeros is None or aula["numero"] in numeros or not os.path.exists(caminho):
                caminho = gerar(documento_da_aula(aula, curso.get("titulo") or tarefa.tema, fontes), nome)
            arquivos.append(caminho)
        return arquivos

    def executar(self, tarefa: Tarefa) -> str:
        anterior = tarefa.contexto.get("conteudo")
        if tarefa.avaliacao and not tarefa.avaliacao.cumprido and anterior:
            return self._refazer_aulas_fracas(tarefa, anterior)
        plano = self.planejar_curso(tarefa)
        aulas = self.escrever_aulas(tarefa, plano, [a["numero"] for a in plano["aulas"]])
        curso = {**plano, "aulas": [aulas[a["numero"]] for a in plano["aulas"]],
                 "fontes": list(dict.fromkeys(tarefa.contexto.get("fontes", [])))}
        tarefa.contexto["plano_curso"] = plano
        tarefa.contexto["conteudo"] = curso
        tarefa.arquivos = self._gerar_arquivos(tarefa, curso)
        falhas = [a["numero"] for a in curso["aulas"] if a.get("erro")]
        extra = f"; não conseguiu escrever a(s) aula(s) {', '.join(map(str, falhas))}" if falhas else ""
        return f"Planejou '{curso.get('titulo')}' em {len(curso['aulas'])} aulas e escreveu {len(curso['aulas']) - len(falhas)}{extra}."

    def _refazer_aulas_fracas(self, tarefa: Tarefa, curso: dict) -> str:
        """Correção pedida pelo crítico: reescreve só as aulas incompletas, não o curso inteiro."""
        fracas = {a["numero"]: problemas_da_aula(a) for a in curso["aulas"] if problemas_da_aula(a)}
        if not fracas:
            return "Nenhuma aula estava incompleta; o curso ficou como estava."
        refeitas = self.escrever_aulas(tarefa, tarefa.contexto["plano_curso"], sorted(fracas), correcoes=fracas)
        novo = {**curso, "aulas": [refeitas.get(a["numero"], a) for a in curso["aulas"]]}
        tarefa.contexto["conteudo"] = novo
        tarefa.arquivos = self._gerar_arquivos(tarefa, novo, numeros=set(refeitas))
        return f"Reescreveu a(s) aula(s) {', '.join(map(str, sorted(refeitas)))}."


# ------------------------------------------------------------ resumo do dia
DIAS_SEMANA =["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]


def coletar_dados_do_dia() -> dict:
    """Agenda, clima, e-mails não lidos e manchetes dos temas do usuário; o que não estiver configurado fica None."""
    from config import OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY, TEMAS_RESUMO_DIA
    from llm_clients import data_hora_atual_texto

    dados = {"data": data_hora_atual_texto(), "agenda": None, "clima": None, "emails": None, "noticias": {}}
    try:
        # Agenda local do PC: compromissos e tarefas de hoje.
        from datetime import date

        from agente_agenda import criar_agente_agenda
        dados["agenda"] = criar_agente_agenda().descrever_dia(date.today())
    except Exception as exc:
        logger.warning("Agenda indisponível para o resumo do dia: %s", exc)
    if OPENWEATHERMAP_API_KEY:
        try:
            from tools import obter_previsao_tempo
            dados["clima"] = obter_previsao_tempo(OPENWEATHERMAP_CITY)
        except Exception as exc:
            logger.warning("Clima indisponível para o resumo do dia: %s", exc)
    try:
        from google_gmail import gmail_conectado, listar_emails
        if gmail_conectado():
            dados["emails"] = [f"{e['de']}: {e['assunto']}" for e in listar_emails("is:unread newer_than:1d", 8)]
    except Exception as exc:
        logger.warning("E-mails indisponíveis para o resumo do dia: %s", exc)
    from tools import buscar_manchetes
    for tema in TEMAS_RESUMO_DIA:
        try:
            dados["noticias"][tema] = [
                (titulo, data.replace("Notícia publicada em ", "")) for titulo, _, data in buscar_manchetes(tema)[:4]
            ]
        except Exception as exc:
            logger.warning("Manchetes de %s indisponíveis: %s", tema, exc)
    return dados


class AgenteResumoDia(Agente):
    nome = "resumo_dia"
    papel = "Reúne agenda, clima, e-mails e notícias e prepara o resumo do dia."
    instrucoes = "Você é o Agente de Resumo do Dia da Beatriz."
    INSTRUCOES_NOTICIAS = (
        "Você resume manchetes de notícias em português do Brasil. Responda SOMENTE com JSON: "
        '{"temas": [{"tema": str, "resumo": str}]}, um resumo de uma ou duas frases por tema, '
        "usando apenas as manchetes dadas, sem inventar fatos."
    )

    def __init__(self, coletar=None, llm_publico=None, **kwargs):
        super().__init__(**kwargs)
        self.coletar = coletar or coletar_dados_do_dia
        # As manchetes são públicas: podem ser resumidas pela IA da nuvem. A agenda e os e-mails não.
        self.llm_publico = llm_publico

    def _resumir_noticias(self, noticias: dict) -> dict:
        com_manchetes = {tema: itens for tema, itens in noticias.items() if itens}
        if not com_manchetes:
            return {}
        texto = "\n\n".join(
            f"Tema: {tema}\n" + "\n".join(f"- {t} ({d})" for t, d in itens) for tema, itens in com_manchetes.items()
        )
        try:
            dados = self.pedir_json(texto, max_tokens=900, llm=self.llm_publico, sistema=self.INSTRUCOES_NOTICIAS)
        except Exception as exc:
            logger.warning("Não foi possível resumir as manchetes: %s", exc)
            return {}
        # O modelo pode devolver o tema com outra grafia ("Direito" para "direito").
        por_nome = {normalizar(str(t.get("tema"))): str(t.get("resumo", "")) for t in dados.get("temas") or [] if isinstance(t, dict)}
        return {tema: por_nome[normalizar(tema)] for tema in com_manchetes if por_nome.get(normalizar(tema))}

    @staticmethod
    def montar_falado(dados: dict, resumos: dict, agora: datetime = None) -> str:
        """Texto falado montado direto dos dados, sem LLM: agenda e e-mails nunca são inventados.

        Só a parte das notícias vem de IA (o resumo das manchetes públicas); o que não estiver
        configurado simplesmente não aparece.
        """
        agora = agora or datetime.now()
        saudacao = "Bom dia" if 5 <= agora.hour < 12 else "Boa tarde" if 12 <= agora.hour < 18 else "Boa noite"
        partes = [f"{saudacao}! Hoje é {DIAS_SEMANA[agora.weekday()]}, {agora.day} de {MESES[agora.month - 1]}."]
        if dados.get("agenda"):
            partes.append(dados["agenda"])
        if dados.get("clima"):
            partes.append(dados["clima"])
        if dados.get("emails") is not None:
            emails = dados["emails"]
            if not emails:
                partes.append("Nenhum e-mail novo.")
            else:
                remetentes = ", ".join(re.sub(r"\s*<.*?>", "", e.split(":")[0]).strip('" ') for e in emails[:3])
                partes.append(f"Você tem {len(emails)} e-mail(s) não lido(s), de {remetentes}.")
        noticias = []
        for tema, itens in dados.get("noticias", {}).items():
            if resumos.get(tema):
                noticias.append(f"Em {tema}: {resumos[tema]}")
            elif itens:
                noticias.append(f"Em {tema}, destaque para: {itens[0][0]}.")
        if noticias:
            partes.append("Nas notícias. " + " ".join(noticias))
        return " ".join(partes)

    def executar(self, tarefa: Tarefa) -> str:
        dados = self.coletar()
        resumos = self._resumir_noticias(dados["noticias"])
        secoes = [{"titulo": "Agenda", "paragrafos": [
            dados["agenda"] or "Não consegui ler a agenda agora."]}]
        if dados["clima"]:
            secoes.append({"titulo": "Clima", "paragrafos": [dados["clima"]]})
        if dados["emails"] is not None:
            secoes.append({"titulo": "E-mails não lidos", "topicos": dados["emails"],
                           "paragrafos": [] if dados["emails"] else ["Nenhum e-mail novo."]})
        for tema, itens in dados["noticias"].items():
            if itens:
                secoes.append({"titulo": f"Notícias: {tema}",
                               "paragrafos": [resumos[tema]] if resumos.get(tema) else [],
                               "topicos": [f"{titulo} ({data})" for titulo, data in itens]})

        falado = self.montar_falado(dados, resumos)
        tarefa.contexto["resposta"] = falado
        documento = {"titulo": f"Resumo do dia · {datetime.now():%d/%m/%Y}", "subtitulo": dados["data"],
                     "resumo": falado, "secoes": secoes}
        tarefa.contexto["conteudo"] = documento
        tarefa.arquivos = [geradores.gerar_arquivo("pdf", documento, tarefa.pasta)]
        temas = sum(1 for itens in dados["noticias"].values() if itens)
        return f"Reuniu agenda, {'clima, ' if dados['clima'] else ''}e-mails e notícias de {temas} tema(s)."


# ------------------------------------------------------------------ e-mail
class AgenteEmail(Agente):
    nome = "email"
    papel = "Lê os e-mails, resume o que importa e rascunha respostas (sem enviar nada)."
    instrucoes = (
        "Você é o Agente de E-mail da Beatriz. Para cada e-mail, diga em uma frase do que se trata, a prioridade "
        "e se precisa de resposta; quando precisar, escreva um rascunho curto, educado, em português, terminando "
        "com 'Atenciosamente,'. Não invente nada que não esteja no e-mail. Responda SOMENTE com JSON: "
        '{"visao_geral": str de 1 a 3 frases para ser falada, "emails": [{"numero": int, '
        '"prioridade": "alta", "media" ou "baixa", "resumo": str, "acao": "responder", "ler" ou "nenhuma", '
        '"rascunho": str ou ""}]}'
    )

    def __init__(self, listar=None, **kwargs):
        super().__init__(**kwargs)
        self.listar = listar

    def executar(self, tarefa: Tarefa) -> str:
        from google_gmail import GmailNaoConectado, listar_emails

        listar = self.listar or listar_emails
        importantes = re.search(r"importante", tarefa.pedido, re.IGNORECASE)
        consulta = "is:important newer_than:7d" if importantes else "is:unread newer_than:3d"
        try:
            emails = listar(consulta, limite=10)
        except GmailNaoConectado as exc:
            raise TarefaImpossivel(str(exc)) from exc
        tarefa.contexto["emails_lidos"] = len(emails)
        if not emails:
            tarefa.contexto["resposta"] = "Você não tem e-mails novos para resumir agora."
            tarefa.arquivos = []
            return "Não havia e-mails novos."

        texto = "\n\n".join(
            f"E-mail {n}\nDe: {e['de']}\nAssunto: {e['assunto']}\nData: {e['data']}\nTrecho: {e['trecho']}"
            for n, e in enumerate(emails, start=1)
        )
        dados = self.pedir_json(f"Pedido do usuário: {tarefa.pedido}\n\n{texto}{_bloco_correcao(tarefa)}", max_tokens=4000)
        resumos = {}
        for item in dados.get("emails") or []:
            if isinstance(item, dict) and str(item.get("numero", "")).isdigit():
                resumos[int(item["numero"])] = item
        linhas, rascunhos = [], []
        for n, email in enumerate(emails, start=1):
            item = resumos.get(n, {})
            linhas.append([str(n), email["de"], email["assunto"], item.get("prioridade", "?"), item.get("resumo", "(sem resumo)")])
            if item.get("rascunho"):
                rascunhos.append({"titulo": f"Rascunho {n}: Re: {email['assunto']}",
                                  "paragrafos": [f"Para: {email['de']}", *str(item["rascunho"]).split("\n\n")]})
        visao = str(dados.get("visao_geral") or f"Você tem {len(emails)} e-mails para ver.")
        documento = {
            "titulo": "Resumo dos e-mails",
            "subtitulo": f"{len(emails)} e-mail(s) · nada foi enviado: copie os rascunhos que quiser usar",
            "resumo": visao,
            "secoes": [{"titulo": "Visão geral",
                        "tabela": {"colunas": ["#", "De", "Assunto", "Prioridade", "Resumo"], "linhas": linhas}},
                       *rascunhos],
        }
        tarefa.contexto.update(conteudo={**dados, "emails_resumidos": len(resumos)}, resposta=visao,
                               rascunhos=len(rascunhos))
        tarefa.arquivos = [geradores.gerar_arquivo("docx", documento, tarefa.pasta)]
        return f"Leu {len(emails)} e-mail(s), resumiu {len(resumos)} e rascunhou {len(rascunhos)} resposta(s)."
