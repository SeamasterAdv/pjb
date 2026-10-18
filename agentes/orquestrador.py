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

from agentes.base import Etapa, Tarefa, TarefaImpossivel, normalizar
from agentes.critico import AgenteCritico
from agentes.especialistas import (
    AgenteApresentacao,
    AgenteAulas,
    AgenteBiblioteca,
    AgenteDocumento,
    AgenteEmail,
    AgenteEstudos,
    AgentePesquisa,
    AgentePlanilha,
    AgenteResumoDia,
)
from agentes.geradores import _numero, nome_arquivo_seguro
from agentes.relator import AgenteRelator

logger = logging.getLogger(__name__)

# ------------------------------------------------------------ reconhecimento
_VERBOS_CRIAR = (
    "crie", "cria", "criar", "faca", "faz", "fazer", "gere", "gera", "gerar", "monte", "monta", "montar",
    "prepare", "prepara", "preparar", "elabore", "elabora", "elaborar", "escreva", "escreve", "escrever",
    "produza", "produzir", "redija", "redigir", "desenvolva", "desenvolver", "quero", "preciso",
)
RE_CRIAR = re.compile(
    r"\b(" + "|".join(_VERBOS_CRIAR) + r")\b"
    # "manda um PDF sobre X" pede algo novo; "manda o PDF" pede o arquivo que já existe.
    r"|\b(mand(a|e|ar)|envi(a|e|ar)) (me )?(um|uma|uns|umas)\b"
)
RE_ABRIR = re.compile(r"^(abr[ae]|abrir|leia|ler|mostre|mostra|feche|fechar|apague|apagar|delete)\b")
RE_PPTX = re.compile(r"\b(apresentac\w*|slides?|power ?point|pptx?)\b")
RE_PDF = re.compile(r"\bpdf\b")
RE_DOCX = re.compile(r"\b(word|docx)\b")
RE_DOCUMENTO = re.compile(r"\b(documento|relatorio|apostila|artigo|ebook|e-book|manual|guia|dossie|trabalho escolar)\b")
RE_WEB = re.compile(r"\b(pesquis\w*|atual|atuais|atualizad\w*|hoje|noticia\w*|ultim\w*|recente\w*|internet|web|cotac\w*|preco\w*|20[2-9]\d)\b")
RE_BIBLIOTECA = re.compile(r"\b(meus documentos|minhas notas|minha biblioteca|biblioteca|obsidian|meus arquivos|minhas anotacoes|minha memoria|com base no que eu)\b")
RE_SO_PESQUISA = re.compile(r"\b(pesquisa (completa|detalhada|aprofundada|profunda)|pesquis\w* a fundo|investigue|fa(ca|z|zer) uma pesquisa)\b")
RE_XLSX = re.compile(r"\b(planilhas?|excel|xlsx)\b")
# Pedidos de estudo inequívocos; "questões" e "exercícios" sozinhos só contam com um verbo de criar.
RE_ESTUDOS = re.compile(
    r"\b(flashcards?|cartoes de (estudo|memoria)|simulados?|material de estudo|guia de estudos?|"
    r"resumo para (a |minha )?prova|revis\w* para (a |minha )?prova|ajud\w* (a|para) estudar|"
    r"(quero|preciso|vou) estudar|estudar (para|sobre))\b"
)
RE_ESTUDOS_FRACO = re.compile(r"\b(questoes|questao|exercicios|perguntas de prova)\b")
# Resumo de matéria para estudar, a partir das fontes do usuário: "resumo da matéria de X",
# "quero estudar toda a matéria", "resume as fontes que eu coloquei (sobre X)".
RE_MATERIA = re.compile(r"\b(resum\w*|estud\w*|revis\w*)\b.{0,40}\bmaterias?\b|\bmaterias?\b.{0,40}\b(resum\w*|estudar|revisar)\b")
# "Matéria" também é reportagem: "resume essa matéria do G1" é sobre a notícia, não sobre estudo.
RE_MATERIA_NOTICIA = re.compile(
    r"\b(ess[ae]|est[ae]|aquel[ae]) materia\b|\bmateria (aberta|que (eu )?(estou lendo|abri|li|vi))\b"
    r"|\b(noticias?|reportage\w*|jornal|site|link|navegador|g1|uol|folha|estadao)\b"
)
RE_FONTES_DO_USUARIO = re.compile(
    r"\b(minhas fontes|meus materiais|(fontes|materiais|arquivos|pdfs|textos) que (eu )?(coloquei|mandei|adicionei|anexei|"
    r"enviei|botei|subi|salvei|passei)|fontes (anexadas|adicionadas|enviadas|salvas))\b"
)
RE_PEDE_RESUMO = re.compile(r"\b(resum\w*|estud\w*|revis\w*|analis\w*|explic\w*)\b")
# Aulas para aprender a matéria inteira: "crie aulas da matéria de X", "quero um curso de X",
# "me ensina toda a matéria de X". Inglês e francês ficam com a professora de idiomas.
RE_AULAS = re.compile(r"\b(aulas?|modulos) (de|da|do|das|dos|sobre|com|pra|para|em)\b")
# "me dá aulas de X" chega aqui como "da aulas de X" (o "me" do começo sai antes).
RE_PEDE_AULAS = re.compile(r"^(me )?(de|da|dar|passa|passe|monta|monte|prepara|prepare)\b|\bensin\w*\b|\baprender\b")
# "Curso" só com verbo de montar: "quero fazer um curso de culinária" é outra coisa.
RE_CURSO = re.compile(
    r"\b(crie|cria|criar|monte|monta|montar|prepare|prepara|preparar|gere|gera|gerar|elabore|elabora|"
    r"me (de|da|passa|monta|prepara))\b.{0,25}\b(curso|cursinho)\b"
)
RE_ENSINAR_TUDO = re.compile(
    r"\bensin\w*\b.{0,40}\b(tudo|toda|inteir\w*|materia)\b|\b(estud\w*|aprend\w*)\b.{0,15}\b(toda|inteira)\b.{0,10}\bmateria\b"
)
RE_NAO_E_CRIAR_AULA = re.compile(
    r"\b(cancel\w*|marc(a|ar|ou|ei|e)|desmarc\w*|remarc\w*|agend\w*|assist\w*|horario\w*|resum\w*|ingles|frances)\b"
)
# Os mesmos trechos no pedido original (com acentos), para tirar do tema.
RE_FONTES_NO_PEDIDO = re.compile(
    r"\b(?:com base n[oa]s? |a partir d[oa]s? |us(?:ando|e) |com )?(?:as |das |nas |os |dos |nos )?(?:minhas fontes|meus materiais|"
    r"(?:fontes|materiais|arquivos|pdfs|textos) que (?:eu )?\w+|fontes (?:anexadas|adicionadas|enviadas|salvas))\b",
    re.IGNORECASE,
)
RE_ANTES_DA_MATERIA = re.compile(
    r"^.*?\b(?:mat[ée]rias?|resum\w*|estud\w*|revis\w*|analis\w*|expli\w*|aulas?|curso|cursinho|m[óo]dulos|ensin\w*|aprender)\b"
    r"(?:\s+(?:toda|tudo|inteira|completa|completo|detalhad[oa]|geral))?\s*(?:(?:de|da|do|das|dos|sobre|com)\s+)?",
    re.IGNORECASE,
)
RE_DEPOIS_DO_TEMA = re.compile(
    r"\s*[,;]|\s+(?:e\s+)?(?:me\s+)?(?:fa[zç]a?|resum\w*|mand\w*|envi\w*|gere|crie|prepar\w*)\b|\s+(?:pra|para)\s+(?:eu\s+|mim\s+)?(?:estudar|revisar)\b|\s+(?:pra|para)\s+mim\b",
    re.IGNORECASE,
)
RE_RESUMO_DIA = re.compile(
    r"\b((resumo|briefing|panorama) (do|de|pro|para o) (meu )?dia|resumo de hoje|"
    r"como (esta|vai ser|sera) (o )?meu dia|o que tenho (pra|para) hoje)\b"
)
# Ler/resumir/responder e-mails (enviar continua com a ferramenta de e-mail, que pede confirmação).
RE_EMAIL = re.compile(
    r"\b(resum\w*|leia|ler|veja|verifiqu\w*|analis\w*|organiz\w*|rascunh\w*|respond\w*|triag\w*)\b.{0,30}\be-?mails?\b"
    r"|\be-?mails? importantes\b|\bcaixa de entrada\b"
)
RE_TEMA = re.compile(r"\b(?:sobre|a respeito d[eoa]s?|acerca d[eoa]s?|com o tema|explicando|falando d[eoa]s?)\s+(.+)", re.IGNORECASE)
RE_CORTE_TEMA = re.compile(
    r"[\s,.;]*\b(?:em (?:pdf|word|docx|power ?point|pptx|slides?)|com \d+ (?:slides?|p[áa]ginas?)|"
    r"com (?:tr[êe]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|quinze|vinte) slides?|"
    r"e (?:salve|salva|abra|imprim[ae]|(?:me )?(?:mand[ae]|envi[ae]|pass[ae]))|pelo (?:zap|whats\w*)|no formato|para (?:apresentar|a reuni[ãa]o|amanh[ãa]|a prova|minha prova)|"
    r"com \d+ (?:flashcards?|quest[õo]es|perguntas)|com (?:um |uns )?gr[áa]ficos?)\b",
    re.IGNORECASE,
)
NUMEROS = {"tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
           "onze": 11, "doze": 12, "quinze": 15, "vinte": 20}

ROTULOS_ETAPA = {
    "biblioteca": "consultando sua biblioteca",
    "pesquisa": "pesquisando na internet",
    "documento": "escrevendo o documento",
    "apresentacao": "montando os slides",
    "planilha": "montando a planilha",
    "estudos": "preparando o material de estudo",
    "aulas": "planejando as aulas",
    "resumo_dia": "reunindo agenda e notícias",
    "email": "lendo seus e-mails",
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
            r"^.*?\b(?:documento|relat[óo]rio|apostila|artigo|pdf|word|docx|apresenta[çc][ãa]o|slides?|power ?point|pptx?|"
            r"planilhas?|excel|xlsx|flashcards?|simulados?|material de estudo|estudar|quest[õo]es)\b"
            r"(?:\s+(?:em|no|de|do|da|dos|das|com|para))?\s*",
            "", tema, count=1, flags=re.IGNORECASE,
        ) or tema
        # "planilha em Excel comparando..." deixa o formato no começo.
        tema = re.sub(r"^(?:em |no )?(?:excel|xlsx|word|pdf|power ?point)\b\s*", "", tema, flags=re.IGNORECASE) or tema
    # A menção à biblioteca indica de onde tirar o conteúdo, não o assunto da busca.
    tema = re.sub(
        r"\b(?:com base n[oa]s? |a partir d[oa]s? )?(?:os |as |d?os |d?as )?(?:meus documentos|minhas notas|minha biblioteca|"
        r"meus arquivos|minhas anota[çc][õo]es)\s*(?:de|sobre|do|da|dos|das)?\s*",
        "", tema, flags=re.IGNORECASE,
    ) or tema
    return tema.strip(" .,!?:;\"'") or pedido


def tema_da_materia(pedido: str) -> str:
    """'faz um resumo da matéria de direito constitucional' -> 'direito constitucional'.

    Vazio quando o pedido não diz o assunto ("resume as fontes que eu coloquei").
    """
    texto = RE_FONTES_NO_PEDIDO.sub(" ", pedido)
    achado = RE_TEMA.search(texto)
    if achado:
        tema = achado.group(1)
    else:
        texto = re.sub(r"^\s*(?:beatriz|beatris|biatriz)[\s,]+", "", texto, flags=re.IGNORECASE)
        # O assunto vem depois de "matéria" ("quero estudar toda a matéria de X") ou do verbo ("resumo de X").
        depois_da_materia = re.search(
            r"\bmat[ée]rias?\b(?:\s+(?:toda|inteira|completa))?\s*(?:(?:de|da|do|das|dos)\s+)?", texto, re.IGNORECASE)
        tema = texto[depois_da_materia.end():] if depois_da_materia else RE_ANTES_DA_MATERIA.sub("", texto, count=1)
    tema = RE_CORTE_TEMA.split(tema)[0]
    tema = RE_DEPOIS_DO_TEMA.split(" " + tema)[0]
    return tema.strip(" .,!?:;\"'")


def _quantidade_de_aulas(texto_normalizado: str) -> int:
    """'em 5 aulas' -> 5, 'uma aula' -> 1; 0 quando o pedido não diz (o plano decide)."""
    achado = re.search(r"\b(uma|duas) (?:aulas?|modulos?)\b", texto_normalizado)
    if achado:
        return 1 if achado.group(1) == "uma" else 2
    return extrair_quantidade(texto_normalizado, r"aulas?|modulos?", 1, 15)


def _quantidades_estudo(texto_normalizado: str) -> dict:
    quantidades = {
        "flashcards": extrair_quantidade(texto_normalizado, r"flashcards?|cartoes|cards?", 3, 60),
        "questoes": extrair_quantidade(texto_normalizado, r"questoes|questao|perguntas|exercicios", 2, 40),
    }
    return {k: v for k, v in quantidades.items() if v}


def extrair_quantidade(texto_normalizado: str, palavras: str, minimo: int, maximo: int) -> int:
    """'com 20 flashcards' / 'dez slides' -> número (limitado); 0 se não houver quantidade."""
    achado = re.search(r"\b(\d{1,3}|" + "|".join(NUMEROS) + r")\s+(?:" + palavras + r")\b", texto_normalizado)
    if not achado:
        return 0
    valor = achado.group(1)
    quantidade = int(valor) if valor.isdigit() else NUMEROS[valor]
    return max(minimo, min(maximo, quantidade))


def extrair_quantidade_slides(texto_normalizado: str) -> int:
    return extrair_quantidade(texto_normalizado, r"slides?|laminas?|telas?", 2, 20)


def _etapas_de_contexto(texto: str, pesquisa_obrigatoria: bool = False) -> list:
    plano = []
    if RE_BIBLIOTECA.search(texto):
        plano.append("biblioteca")
    if pesquisa_obrigatoria or RE_WEB.search(texto):
        plano.append("pesquisa")
    return plano


def planejar(comando: str):
    """Transforma um pedido em Tarefa com o plano de agentes, ou None se não for um pedido de entrega.

    "pelo Gemini" / "no Claude" no pedido escolhe a IA da nuvem que vai primeiro; sai do pedido e do tema.
    """
    from llm_nuvem import extrair_ia_pedida

    sem_ia, ia = extrair_ia_pedida(comando.strip())
    tarefa = _planejar(sem_ia)
    if tarefa is not None and ia:
        tarefa.ia_preferida = ia
    return tarefa


def _planejar(comando: str):
    texto = normalizar(comando)
    texto = re.sub(r"^(beatriz|beatris|biatriz)[\s,]+", "", texto)
    texto = re.sub(r"^(por favor|me|pra mim)\s+", "", texto)
    if not texto or RE_ABRIR.match(texto):
        return None
    if pedido_de_arquivo(comando) is not None:
        return None  # "quero o PDF", "manda a planilha": pedem um arquivo que já existe, não uma tarefa nova
    pedido = comando.strip()

    # Tarefas com dados pessoais (agenda, e-mails) rodam inteiras no computador.
    if RE_RESUMO_DIA.search(texto):
        return Tarefa(pedido=pedido, plano=["resumo_dia"], formatos=["pdf"], tema="o resumo do dia")
    if RE_EMAIL.search(texto):
        return Tarefa(pedido=pedido, plano=["email"], formatos=["docx"], tema="seus e-mails")

    # Aulas da matéria: um plano do curso e uma aula completa por arquivo, também com as fontes do usuário.
    quer_aulas = (RE_AULAS.search(texto) and (RE_CRIAR.search(texto) or RE_PEDE_AULAS.search(texto))) or RE_CURSO.search(texto)
    if (quer_aulas or RE_ENSINAR_TUDO.search(texto)) and not RE_NAO_E_CRIAR_AULA.search(texto):
        tema = tema_da_materia(pedido)
        pesquisa = ["pesquisa"] if tema and re.search(r"\b(pesquis\w*|internet|web)\b", texto) else []
        aulas = _quantidade_de_aulas(texto)
        return Tarefa(
            pedido=pedido, plano=["biblioteca"] + pesquisa + ["aulas"],
            formatos=["docx"] if RE_DOCX.search(texto) else ["pdf"], tema=tema or "suas fontes recentes",
            quantidades={"aulas": aulas} if aulas else {}, materia=True, fontes_recentes=not tema,
        )

    # Resumo da matéria: sempre com as fontes do usuário sobre o assunto (ou as últimas que ele colocou).
    pede_fontes = bool(RE_FONTES_DO_USUARIO.search(texto)) and bool(RE_PEDE_RESUMO.search(texto))
    if pede_fontes or (RE_MATERIA.search(texto) and not RE_MATERIA_NOTICIA.search(texto)):
        tema = tema_da_materia(pedido)
        pesquisa = ["pesquisa"] if tema and re.search(r"\b(pesquis\w*|internet|web)\b", texto) else []
        return Tarefa(
            pedido=pedido, plano=["biblioteca"] + pesquisa + ["estudos"],
            formatos=["docx"] if RE_DOCX.search(texto) else ["pdf"], tema=tema or "suas fontes recentes",
            quantidades=_quantidades_estudo(texto), materia=True, fontes_recentes=not tema,
        )

    if RE_ESTUDOS.search(texto) or (RE_ESTUDOS_FRACO.search(texto) and RE_CRIAR.search(texto)):
        return Tarefa(
            pedido=pedido, plano=_etapas_de_contexto(texto) + ["estudos"],
            formatos=["docx"] if RE_DOCX.search(texto) else ["pdf"], tema=extrair_tema(pedido),
            quantidades=_quantidades_estudo(texto),
        )
    if RE_XLSX.search(texto) and RE_CRIAR.search(texto):
        return Tarefa(pedido=pedido, plano=_etapas_de_contexto(texto) + ["planilha"], formatos=["xlsx"],
                      tema=extrair_tema(pedido))

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

    plano = _etapas_de_contexto(texto, pesquisa_obrigatoria=so_pesquisa)
    if quer_documento:
        plano.append("apresentacao" if "pptx" in formatos else "documento")

    return Tarefa(
        pedido=pedido,
        plano=plano,
        formatos=[] if so_pesquisa else formatos,
        tema=extrair_tema(pedido),
        quantidade_slides=extrair_quantidade_slides(texto),
    )


# ------------------------------------------------------- arquivos por mensagem
# "faz um PDF sobre X e me manda": o pedido já inclui mandar o arquivo.
RE_PEDE_ENVIO = re.compile(r"\b(mand(a|e|ar)|envi(a|e|ar)|pass(a|e|ar)|compartilh(a|e|ar))\b|\bpelo (zap|whats\w*)\b")
# "manda o arquivo", "me envia o pdf", "cadê a planilha?": pedido de um arquivo já pronto.
RE_PEDIDO_DE_ARQUIVO = re.compile(
    r"^(?:(?:pode|poderia|consegue) )?(?:me )?(?:mand(?:a|e|ar)|envi(?:a|e|ar)|pass(?:a|e|ar)|compartilh(?:a|e|ar)|"
    r"quero|cade)(?: pra mim| aqui| de novo)? (?:o |a |os |as |esse |essa |esses |essas |aquele |aquela )?"
    r"(arquivos?|pdfs?|words?|documentos?|planilhas?|excel|apresentac\w*|slides|power ?point|flashcards|anki|relatorio)\b"
)
EXTENSOES_PEDIDAS = {
    "pdf": (".pdf",), "word": (".docx",), "planilha": (".xlsx",), "excel": (".xlsx",),
    "apresentac": (".pptx",), "slides": (".pptx",), "power": (".pptx",), "flashcards": (".txt",), "anki": (".txt",),
}


def pede_envio(comando: str) -> bool:
    """O pedido de tarefa já diz para mandar o arquivo ("... e me manda", "... pelo zap")?"""
    return bool(RE_PEDE_ENVIO.search(normalizar(comando)))


def pedido_de_arquivo(comando: str):
    """Para "manda o PDF": as extensões pedidas (tupla vazia = todos os arquivos), "relatorio" ou None se não for isso."""
    texto = re.sub(r"^(beatriz|beatris|biatriz)[\s,]+", "", normalizar(comando))
    achado = RE_PEDIDO_DE_ARQUIVO.search(texto)
    if not achado:
        return None
    palavra = achado.group(1)
    if palavra == "relatorio":
        return "relatorio"
    return next((ext for chave, ext in EXTENSOES_PEDIDAS.items() if palavra.startswith(chave)), ())


def _numero_br(valor: float) -> str:
    """1234.5 -> '1.234,50'."""
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def texto_da_entrega(tarefa: Tarefa, limite: int = 1800) -> str:
    """O principal do que foi produzido, para mandar por mensagem sem precisar abrir o arquivo."""
    conteudo = tarefa.contexto.get("conteudo") or {}
    linhas = []
    if tarefa.tipo == "documento":
        if conteudo.get("resumo"):
            linhas.append(str(conteudo["resumo"]))
        secoes = [s.get("titulo") for s in conteudo.get("secoes") or [] if isinstance(s, dict) and s.get("titulo")]
        if secoes:
            linhas.append("*Seções:* " + "; ".join(secoes))
        if conteudo.get("conclusao"):
            linhas.append("*Conclusão:* " + str(conteudo["conclusao"]))
    elif tarefa.tipo == "apresentacao":
        slides = [s.get("titulo") for s in conteudo.get("slides") or [] if isinstance(s, dict) and s.get("titulo")]
        if slides:
            linhas.append("*Slides:*\n" + "\n".join(f"{n}. {titulo}" for n, titulo in enumerate(slides, start=1)))
    elif tarefa.tipo == "planilha":
        for aba in conteudo.get("abas") or []:
            if not isinstance(aba, dict):
                continue
            colunas, tipos = aba.get("colunas") or [], aba.get("tipos") or []
            dados = [l for l in aba.get("linhas") or [] if isinstance(l, list)]
            linha = f"*{aba.get('nome', 'Planilha')}*: {len(dados)} linhas ({', '.join(map(str, colunas))})"
            if aba.get("total"):
                for indice, tipo in enumerate(tipos):
                    if tipo in ("moeda", "numero", "inteiro") and indice < len(colunas):
                        valores = [_numero(l[indice]) for l in dados if len(l) > indice]
                        soma = sum(v for v in valores if isinstance(v, (int, float)))
                        valor = f"R$ {_numero_br(soma)}" if tipo == "moeda" else _numero_br(soma)
                        linha += f"\nTotal de {colunas[indice]}: {valor}"
            linhas.append(linha)
        if conteudo.get("observacoes"):
            linhas.append(str(conteudo["observacoes"]))
    elif tarefa.tipo == "aulas":
        if conteudo.get("apresentacao"):
            linhas.append(str(conteudo["apresentacao"]))
        aulas = [a for a in conteudo.get("aulas") or [] if isinstance(a, dict)]
        if aulas:
            linhas.append("*Aulas:*\n" + "\n".join(f"{a.get('numero')}. {a.get('titulo')}" for a in aulas))
        importantes = conteudo.get("topicos_importantes") or []
        if importantes:
            linhas.append("*Tópicos mais importantes:*\n" + "\n".join(f"• {t}" for t in importantes[:8]))
    elif tarefa.tipo == "estudos":
        if conteudo.get("resumo"):
            linhas.append(str(conteudo["resumo"]))
        topicos = [t.get("titulo") for t in conteudo.get("topicos") or [] if isinstance(t, dict) and t.get("titulo")]
        if tarefa.materia and topicos:
            linhas.append("*Tópicos da matéria:* " + "; ".join(map(str, topicos)))
        cartoes = conteudo.get("flashcards") or []
        if cartoes:
            linhas.append("*Alguns flashcards:*\n" + "\n".join(f"• {c['frente']} → {c['verso']}" for c in cartoes[:5]))
        if conteudo.get("questoes"):
            linhas.append(f"E mais {len(conteudo['questoes'])} questões com gabarito comentado no material.")
    texto = "\n\n".join(linhas)
    return texto if len(texto) <= limite else texto[:limite].rsplit(" ", 1)[0] + "…"


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
                 max_revisoes: int = 1, abrir_ao_concluir: bool = True, agentes: dict = None, llm_nuvem=None,
                 llm_fontes=None):
        self.pasta_saida = pasta_saida
        opcoes = {"llm": llm, "modelo": modelo}
        self.agentes = agentes or {
            "biblioteca": AgenteBiblioteca(**opcoes),
            "pesquisa": AgentePesquisa(**opcoes),
            "documento": AgenteDocumento(**opcoes),
            "apresentacao": AgenteApresentacao(**opcoes),
            "planilha": AgentePlanilha(**opcoes),
            "estudos": AgenteEstudos(**opcoes),
            "aulas": AgenteAulas(**opcoes),
            # As manchetes do resumo do dia são públicas e podem ser resumidas na nuvem; o resto, não.
            "resumo_dia": AgenteResumoDia(llm_publico=llm_nuvem, **opcoes),
            "email": AgenteEmail(**opcoes),
            "critico": AgenteCritico(**opcoes),
            "relator": AgenteRelator(**opcoes),
        }
        # Versões dos agentes que escrevem com o modelo da nuvem (com o local de reserva).
        # Biblioteca, e-mail e resumo do dia não entram aqui: tarefas com dados pessoais ficam no Ollama.
        self.llm_nuvem = llm_nuvem
        self.agentes_nuvem = {}
        if llm_nuvem is not None:
            nuvem = {"llm": llm_nuvem, "modelo": modelo}
            self.agentes_nuvem = {
                "pesquisa": AgentePesquisa(**nuvem),
                "documento": AgenteDocumento(**nuvem),
                "apresentacao": AgenteApresentacao(**nuvem),
                "planilha": AgentePlanilha(**nuvem),
                "estudos": AgenteEstudos(**nuvem),
                "critico": AgenteCritico(**nuvem),
                "relator": AgenteRelator(**nuvem),
            }
        # Material de estudo com as fontes do usuário (ESTUDOS_COM_CLAUDE): escreve só com o Claude,
        # com o Ollama de reserva. O Gemini nunca recebe os documentos.
        self.llm_fontes = llm_fontes
        self.agentes_fontes = {}
        if llm_fontes is not None:
            com_fontes = {"llm": llm_fontes, "modelo": modelo}
            self.agentes_fontes = {
                "estudos": AgenteEstudos(**com_fontes),
                "aulas": AgenteAulas(**com_fontes),
                "critico": AgenteCritico(**com_fontes),
                "relator": AgenteRelator(**com_fontes),
            }
        self.publicar = publicar or (lambda resumo: None)
        # Aulas e fichas de estudo levam vários minutos: o agente publica no painel cada etapa que fica pronta.
        for grupo in (self.agentes, self.agentes_fontes, getattr(self, "agentes_nuvem", {})):
            for nome in ("aulas", "estudos"):
                if hasattr(grupo.get(nome), "ao_progredir"):
                    grupo[nome].ao_progredir = self._publicar
        self.abrir = abrir or getattr(os, "startfile", lambda caminho: None)
        self.max_revisoes = max_revisoes
        self.abrir_ao_concluir = abrir_ao_concluir
        self.tarefas = []
        self._lock = threading.Lock()
        # Um trabalhador só: as tarefas usam o mesmo modelo na placa de vídeo, então em fila é mais rápido.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agentes")

    # --------------------------------------------------------- envio
    def enviar(self, comando: str, ao_concluir=None, abrir: bool = True):
        """Se o comando for um pedido de entrega, coloca a tarefa na fila e devolve a Tarefa; senão None.

        `abrir=False` não abre o arquivo no PC ao terminar (ex.: pedido feito pelo WhatsApp, longe do PC).
        """
        tarefa = planejar(comando)
        if tarefa is None:
            return None
        tarefa.contexto["abrir_ao_concluir"] = abrir
        if (tarefa.tipo in ("estudos", "aulas") and "biblioteca" in tarefa.plano and self.llm_fontes is not None
                and any(p.disponivel() for p in self.llm_fontes.principais)):
            tarefa.fontes_no_claude = True
        if tarefa.fontes_recentes:
            # Escolhidas já, para a mensagem de início dizer quais arquivos serão resumidos.
            escolher = getattr(self.agentes["biblioteca"], "escolher_fontes_recentes", None)
            tarefa.contexto["fontes_escolhidas"] = escolher() if escolher else []
        carimbo = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        tarefa.pasta = os.path.join(self.pasta_saida, f"{carimbo}_{nome_arquivo_seguro(tarefa.tema, 40)}")
        with self._lock:
            self.tarefas.append(tarefa)
            self.tarefas = self.tarefas[-20:]
        self._publicar(tarefa)
        self._executor.submit(self._executar, tarefa, ao_concluir)
        return tarefa

    def mensagem_inicio(self, tarefa: Tarefa) -> str:
        na_fila = sum(1 for t in self.tarefas if t.status in ("na fila", "executando", "revisando", "relatando")) - 1
        fila = f" Há {na_fila} tarefa(s) na frente desta." if na_fila > 0 else ""
        if tarefa.tipo == "resumo_dia":
            # As manchetes públicas podem ir para a IA pedida; o resto já fica no computador.
            return f"Certo, vou reunir sua agenda, e-mails e as notícias dos seus temas para o resumo do dia.{fila}"
        fim = fila
        if tarefa.fontes_no_claude:
            if tarefa.ia_preferida == "gemini":
                fim = f" O Gemini fica de fora: suas fontes vão só para o Claude.{fila}"
        elif tarefa.ia_preferida and tarefa.local_apenas:
            # Pediram "pelo Gemini", mas a tarefa usa dados pessoais: roda inteira no Ollama, e a pessoa precisa saber.
            fim = f" Como envolve seus dados, o {tarefa.ia_preferida.capitalize()} fica de fora: faço tudo aqui no computador.{fila}"
        if tarefa.tipo == "email":
            return f"Certo, vou ler seus e-mails recentes, resumir e rascunhar respostas. Nada sai do seu computador e nada é enviado.{fim}"
        if tarefa.tipo == "aulas":
            return self._inicio_aulas(tarefa) + fim
        if tarefa.tipo == "estudos":
            cartoes, questoes = tarefa.quantidade_estudo("flashcards"), tarefa.quantidade_estudo("questoes")
            if tarefa.materia:
                return self._inicio_materia(tarefa, cartoes, questoes) + fim
            return (f"Certo, vou preparar a ficha de estudo sobre {tarefa.tema}: como cai na prova, cada tópico em "
                    f"regra, exceções e lei seca, {cartoes} flashcards e um teste de estresse com {questoes} questões. "
                    f"Te aviso quando terminar.{fim}")
        if tarefa.tipo == "planilha":
            entrega = "a planilha"
        elif "pptx" in tarefa.formatos:
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
        return f"Certo, vou preparar {entrega} sobre {tarefa.tema}.{passos} Te aviso quando terminar.{fim}"

    @staticmethod
    def _fontes_escolhidas(tarefa: Tarefa) -> str:
        """'a.pdf, b.docx e c.pdf' (as fontes recentes que serão usadas); vazio se não houver."""
        nomes = [os.path.basename(c) for c in tarefa.contexto.get("fontes_escolhidas") or []]
        if len(nomes) > 4:
            nomes = nomes[:3] + [f"mais {len(nomes) - 3}"]
        return (nomes[0] if len(nomes) == 1 else ", ".join(nomes[:-1]) + " e " + nomes[-1]) if nomes else ""

    def _inicio_aulas(self, tarefa: Tarefa) -> str:
        if tarefa.fontes_recentes:
            lista = self._fontes_escolhidas(tarefa)
            if not lista:
                return "Certo, vou procurar as fontes que você colocou na biblioteca para montar as aulas."
            o_que = f"montar as aulas a partir das suas fontes mais recentes ({lista})"
        else:
            o_que = f"montar as aulas da matéria {tarefa.tema} a partir das suas fontes sobre o assunto"
        if "pesquisa" in tarefa.plano:
            o_que += " e da internet"
        aulas = tarefa.quantidades.get("aulas")
        quantas = f"{aulas} aula{'s' if aulas > 1 else ''}" if aulas else "as aulas que a matéria pedir"
        quem = ", com o Claude" if tarefa.fontes_no_claude else ", aqui no computador"
        return (f"Certo, vou {o_que}{quem}: primeiro o plano do curso com os tópicos mais importantes, depois "
                f"{quantas}, cada uma num PDF com explicação completa, exemplos, exercícios resolvidos e questões. "
                "Leva uns 10 a 20 minutos; te aviso quando terminar.")

    @classmethod
    def _inicio_materia(cls, tarefa: Tarefa, cartoes: int, questoes: int) -> str:
        if tarefa.fontes_recentes:
            lista = cls._fontes_escolhidas(tarefa)
            if not lista:
                return "Certo, vou procurar as fontes que você colocou na biblioteca para resumir."
            o_que = f"resumir suas fontes mais recentes ({lista})"
        else:
            o_que = f"preparar o resumo da matéria {tarefa.tema} a partir das suas fontes sobre o assunto"
        if "pesquisa" in tarefa.plano:
            o_que += " e da internet"
        if tarefa.fontes_no_claude:
            return (f"Certo, vou {o_que}, com o Claude, numa ficha tática: primeiro a análise de como a matéria cai na "
                    f"prova, depois cada tópico explicado e desmontado em regra, exceções, prazos, lei seca conferida no "
                    f"Planalto e STF/STJ, glossário, {cartoes} flashcards e um teste de estresse com {questoes} questões "
                    "difíceis. Leva alguns minutos; te aviso quando terminar.")
        return (f"Certo, vou {o_que}, aqui no computador, numa ficha tática: tópicos com regra e exceções, {cartoes} "
                f"flashcards e um teste de estresse com {questoes} questões. Te aviso quando terminar.")

    # --------------------------------------------------------- execução
    def _publicar(self, tarefa: Tarefa):
        self.publicar(tarefa.resumo())

    def _agente(self, tarefa: Tarefa, nome: str):
        """Agente da nuvem quando a tarefa só usa conteúdo público; senão, o local.

        Material de estudo com as fontes do usuário (`fontes_no_claude`): o agente que escreve só com o Claude.
        """
        if tarefa.fontes_no_claude and nome in self.agentes_fontes:
            return self.agentes_fontes[nome]
        if nome in self.agentes_nuvem and not tarefa.local_apenas:
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
        if tarefa.fontes_no_claude:
            registro = list(dict.fromkeys(self.llm_fontes.registro))
            return [nome if nome.startswith("Ollama") else f"{nome}, que leu as suas fontes" for nome in registro] or ["Ollama (local)"]
        registro = list(dict.fromkeys(getattr(self.llm_nuvem, "registro", None) or []))
        if tarefa.local_apenas:
            # Os dados pessoais ficaram no Ollama; a nuvem só aparece se resumiu algo público (manchetes).
            return [f"{nome} (só notícias públicas)" for nome in registro if not nome.startswith("Ollama")] + ["Ollama (local)"]
        return registro or ["Ollama (local)"]

    def _executar(self, tarefa: Tarefa, ao_concluir):
        if getattr(self.llm_nuvem, "registro", None) is not None:
            self.llm_nuvem.registro.clear()  # uma tarefa por vez: o registro passa a ser desta
        if self.llm_nuvem is not None:
            self.llm_nuvem.preferir = tarefa.ia_preferida or None  # "pelo Gemini" no pedido: o Gemini vai primeiro
        if self.llm_fontes is not None:
            self.llm_fontes.registro.clear()
        if tarefa.fontes_no_claude and not any(p.disponivel() for p in self.llm_fontes.principais):
            # Claude em pausa (limite do plano, sem login): o material sai no Ollama, na versão normal,
            # com os trechos das fontes cabendo no contexto do modelo local.
            logger.warning("Claude indisponível; o material de estudo da tarefa %s será feito no Ollama.", tarefa.id)
            tarefa.fontes_no_claude = False
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
            if self.abrir_ao_concluir and tarefa.contexto.get("abrir_ao_concluir", True) and tarefa.arquivos:
                self._abrir(tarefa.arquivos[0])
        except TarefaCancelada:
            tarefa.status = "cancelada"
            tarefa.mensagem_final = f"Tarefa sobre {tarefa.tema} cancelada."
        except TarefaImpossivel as exc:
            # Falta algo do lado do usuário (ex.: vincular o Gmail): explica o que fazer, sem relatório.
            tarefa.status = "não realizada"
            tarefa.erro = str(exc)
            tarefa.mensagem_final = str(exc)
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
        problema = avaliacao.problemas[0] if avaliacao.problemas else "alguns pontos não ficaram como pedido"
        problema = problema.replace("Verificação automática falhou: ", "").rstrip(".")
        ressalva = "" if avaliacao.cumprido else f" Atenção: {problema}."
        resposta = tarefa.contexto.get("resposta", "")

        # Resumo do dia e e-mail: a própria resposta é o que se fala; os detalhes ficam no arquivo.
        if tarefa.tipo == "resumo_dia":
            return f"{resposta}\n\nO resumo completo, com as manchetes, está em PDF.{ressalva}"
        if tarefa.tipo == "email":
            if not tarefa.arquivos:
                return resposta
            rascunhos = tarefa.contexto.get("rascunhos", 0)
            extra = f" e {rascunhos} rascunho(s) de resposta" if rascunhos else ""
            return f"{resposta}\n\nOs resumos{extra} estão no documento do Word. Nada foi enviado.{ressalva}"
        if not tarefa.arquivos:
            return f"{resposta}\n\n{nota} O relatório da pesquisa ficou salvo."

        nomes_formato = {"pptx": "PowerPoint", "docx": "Word", "pdf": "PDF", "xlsx": "Excel", "txt": "Anki"}
        formatos = " e ".join(dict.fromkeys(nomes_formato.get(os.path.splitext(a)[1].lstrip("."), a) for a in tarefa.arquivos))
        conteudo = tarefa.contexto.get("conteudo") or {}
        titulo = conteudo.get("titulo") or tarefa.tema
        if tarefa.tipo == "planilha":
            entrega = f"A planilha '{titulo}' está pronta"
        elif tarefa.tipo == "aulas":
            prontas = sum(1 for aula in conteudo.get("aulas") or [] if not aula.get("erro"))
            entrega = (f"O curso '{titulo}' está pronto, com {prontas} aula{'s' if prontas != 1 else ''} (uma por arquivo) "
                       "e o plano do curso com os tópicos mais importantes")
        elif tarefa.tipo == "estudos":
            topicos = len(conteudo.get("topicos") or [])
            entrega = (f"O material de estudo '{titulo}', com {f'{topicos} tópicos, ' if tarefa.materia else ''}"
                       f"{len(conteudo.get('flashcards') or [])} flashcards e {len(conteudo.get('questoes') or [])} questões, está pronto")
        elif "pptx" in tarefa.formatos:
            entrega = f"A apresentação '{titulo}' está pronta"
        else:
            entrega = f"O documento '{titulo}' está pronto"
        sem_claude = ""
        if tarefa.materia and self.llm_fontes is not None and all(
                m.startswith("Ollama") for m in tarefa.contexto.get("modelos") or ["Ollama"]):
            sem_claude = (" O Claude não pôde escrever agora (limite do plano ou sem login), então o material saiu do "
                          "Ollama, mais simples; peça de novo mais tarde para a versão completa.")
        if avaliacao.cumprido:
            return f"Pronto! {entrega} em {formatos}. {nota} O relatório explicando o que foi feito está na mesma pasta.{sem_claude}"
        return f"{entrega} em {formatos}, mas com ressalvas: {problema}. {nota} Veja os detalhes no relatório.{sem_claude}"

    def _abrir(self, caminho: str):
        try:
            self.abrir(caminho)
        except Exception as exc:
            logger.warning("Não foi possível abrir %s: %s", caminho, exc)

    # --------------------------------------------------------- gerenciamento
    def ultima_com_arquivos(self):
        """Última tarefa concluída que gerou arquivos (para "manda o arquivo")."""
        with self._lock:
            return next((t for t in reversed(self.tarefas) if t.status == "concluída" and t.arquivos), None)

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
