"""Os apps da ArtCraft por pedido, sem LLM: abrir, converter e juntar arquivos (os programas estão em `apps_craft.py`).

- "abre o PhotoCraft", "abre esse PDF no PdfCraft", "abre a foto da praia no PhotoCraft";
- "converte esse Word em PDF", "converte a prova 2 de penal em PDF", "passa esse PDF pra imagem", "salva o desenho como PDF";
- "junta esses PDFs", "junta esses arquivos num PDF só", "junta os PDFs das aulas de tributário";
- "quais apps da ArtCraft você tem?", "pra que serve o FigCraft?".

"Esse", "esses" e "todos" são os arquivos que acabaram de chegar (pelo WhatsApp, pelo app ou pelo scanner); com uma
descrição ("a prova 2 de penal"), o arquivo é achado pelo nome aproximado (`busca_arquivos`). O resultado fica em
`documentos_gerados/AAAA-MM-DD_conversoes`: pela voz e pelos painéis ela o abre no PC ("... e me manda" o manda pelo WhatsApp);
pelo WhatsApp e pelo app ele volta como arquivo, porque foi pedido de lá. Nada vai para a nuvem.
"""
import os
import re
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import busca_arquivos
from apps_craft import (APPS, CONVERSORES, FORMATO_FALADO, FOTOS, RE_DO_APP, RE_LAUNCHER, VIDEOS, App, AppsCraft, EdicaoDeFoto,
                        EdicaoDeVideo, ErroCraft, app_citado)

CANAIS_REMOTOS = ("whatsapp", "app")
MAXIMO_PARA_JUNTAR = 30
# "Junta os PDFs das aulas de tributário": as 12 aulas da pasta ficaram entre 0,70 e 0,86 (as palavras no nome ou só na pasta),
# e um "Tributário.pdf" de outra pasta, em 0,53. Com a folga de empate de uma escolha só (0,08), entravam 2 aulas.
FOLGA_PARA_JUNTAR = 0.2
MAXIMO_PARA_MANDAR = 10

RE_ABRIR = re.compile(r"\b(?:abr[ea]\w*|abrir|inici\w*|execut\w*|rod[ae]r?|lig[ae]r?|lanc[ae]r?|carreg\w*)\b")
RE_LISTAR = re.compile(r"\b(?:quais|que)\W+(?:sao\W+)?(?:os\W+)?(?:apps?|aplicativos?|programas?)\b(?:\W+\w+){0,5}?\W+"
                       r"(?:art\W?craft|craft)\b|\b(?:apps?|aplicativos?|programas?)\W+d[ao]\W+art\W?craft\b")
RE_PRA_QUE = re.compile(r"\b(?:pra|para)\W+que\W+serve\b|\bo\W+que\W+(?:e|faz)\b|\bserve\W+(?:pra|para)\W+que\b")
# "Converte" é sempre conversão; "transforma", "passa", "salva como"... só quando a conversão é possível e o arquivo é dito
# pelo tipo ("esse Word") ou é o que acabou de chegar ("isso"): "transforma esse PDF numa apresentação" é com os agentes, e
# "salva essa resposta em PDF" não é sobre um arquivo.
RE_CONVERTE = re.compile(r"\bconvert\w*\b")
RE_TRANSFORMA = re.compile(r"\b(?:transform\w*|pass[ae]\w*|vir[ae]\w*|salv[ae]\w*|export\w*|mud[ae]\w*|troc[ae]\w*|deix[ae]\w*)\b")
# Só os verbos: "combinado, me manda esses arquivos" não é juntar.
RE_JUNTAR = re.compile(r"\b(?:junt(?:a|ar|e|em|as)|une|unir|unifica|unificar|combin(?:a|ar|e|em)|mescl(?:a|ar|e|em))\b")
RE_OBJETO_DE_JUNTAR = re.compile(r"\b(?:pdfs?|arquivos?|documentos?|eles|elas|esses|essas|estes|estas|tudo|todos|todas|"
                                 r"fotos?|imagens?|paginas?|aulas?)\b")
RE_NUM_PDF = re.compile(r"\b(?:num|numa|em\W+um|em|para\W+um|pra\W+um|pra|para)\W+(?:so\W+|unico\W+)?pdf\b(?:\W+(?:so|unico))?")
RE_VARIOS = re.compile(r"\b(?:esses|essas|estes|estas|todos|todas|tudo|eles|elas)\b")
RE_MANDAR = re.compile(r"\bme\W+(?:manda|envia|mande|envie|passa)\w*\b|\b(?:manda|envia)\w*\W+(?:\w+\W+){0,3}?(?:zap|whatsapp|celular)\b")
FORMATOS = {
    "pdf": ".pdf", "word": ".docx", "docx": ".docx", "odt": ".odt", "rtf": ".rtf", "texto": ".txt", "txt": ".txt",
    "markdown": ".md", "html": ".html", "imagem": ".png", "imagens": ".png", "png": ".png", "foto": ".png", "fotos": ".png",
    "figura": ".png", "jpg": ".jpg", "jpeg": ".jpg", "webp": ".webp", "tiff": ".tif", "tif": ".tif", "psd": ".psd",
    "photoshop": ".psd", "powerpoint": ".pptx", "pptx": ".pptx", "excel": ".xlsx", "xlsx": ".xlsx", "csv": ".csv",
    "svg": ".svg", "gif": ".gif", "dwg": ".dwg", "dxf": ".dxf", "json": ".json",
}
RE_ALVO = re.compile(r"\b(?:em|para|pra|como|num|numa|vira|virar|vire)\W+(?:um\W+|uma\W+|o\W+|a\W+)?(?:formato\W+(?:de\W+)?)?"
                     rf"(?P<formato>{'|'.join(sorted(FORMATOS, key=len, reverse=True))})\b")
TODAS_AS_ENTRADAS = tuple(sorted({e for entradas, _ in CONVERSORES.values() for e in entradas} | {".pdf"}))
TIPOS_DE_ORIGEM = (
    (re.compile(r"\b(?:word|docx?|odt|rtf)\b"), (".docx", ".doc", ".odt", ".rtf")),
    (re.compile(r"\b(?:textos?|txt|markdown|html)\b"), (".txt", ".md", ".html", ".htm")),
    (re.compile(r"\bpdfs?\b"), (".pdf",)),
    (re.compile(r"\b(?:planilhas?|excel|xlsx|csv)\b"), CONVERSORES["gridcraft"][0]),
    (re.compile(r"\b(?:apresentac\w*|slides?|powerpoint|pptx?)\b"), CONVERSORES["deckcraft"][0]),
    (re.compile(r"\b(?:fotos?|imagens?|figuras?|png|jpe?g|psd|prints?)\b"), CONVERSORES["photocraft"][0]),
    (re.compile(r"\b(?:svg|vetor\w*|ilustrac\w*)\b"), CONVERSORES["vectorcraft"][0]),
    (re.compile(r"\b(?:desenhos?|dwg|dxf|plantas?)\b"), CONVERSORES["cadcraft"][0]),
)
# "Esse", "esses", "todos", "o último": o que acabou de chegar.
RE_REFERE = re.compile(r"\b(?:isso|isto|disso|esse|essa|esses|essas|este|esta|estes|estas|desse|dessa|desses|dessas|ele|ela|"
                       r"eles|elas|todos|todas|tudo|ultim[oa]s?|o\W+que\W+(?:eu\W+)?(?:mandei|enviei|te\W+mandei))\b")
RE_CAMINHO = re.compile(r"[A-Za-z]:[\\/][^\"<>|*?\r\n]*?\.[A-Za-z0-9]{2,5}\b")
VAZIAS = set("""
converte converter converta converti convertido transforma transformar transforme passa passar passe vira virar vire salva
salvar salve exporta exportar exporte muda mudar mude troca trocar troque deixa deixar deixe junta juntar junte une unir
unifica combina combinar mescla mesclar abre abrir abra num numa unico unica so todos todas tudo eles elas craft art arte
esse essa esses essas este esta estes estas desse dessa desses dessas isso isto disso ultimo ultima formato
""".split()) | set(FORMATOS)


# ------------------------------------------------------------------ edição de fotos e vídeos
RE_FOTO = re.compile(r"\b(?:fotos?|imagens?|selfies?|retratos?|prints?|figuras?|fotografias?)\b")
RE_VIDEO = re.compile(r"\b(?:videos?|clipes?|gravac\w+|filmagens?|reels?|videozinho)\b")
RE_FILTROS = {
    "pb": re.compile(r"\bpreto\W+e\W+branco\b|\bp\W?e?\W?b\b|\bmonocrom\w*|\bem\W+cinza\b|\bsem\W+cor\b"),
    "sepia": re.compile(r"\bsepia\b"),
    "vintage": re.compile(r"\b(?:vintage|retro|antig[ao]s?|envelhecid\w*|polaroid)\b|\banos\W+(?:70|setenta)\b"),
    "cinema": re.compile(r"\bcinema\w*|\bcinematograf\w*|\bcara\W+de\W+filme\b"),
    "quente": re.compile(r"\b(?:tom\W+)?quente\b|\bdourad\w*|\bmais\W+amarelad\w*"),
    "frio": re.compile(r"\b(?:tom\W+)?fri[ao]\b|\bazulad\w*"),
    "vivo": re.compile(r"\bcores?\W+viv\w+|\bvibrantes?\b"),
    "dramatico": re.compile(r"\bdramatic\w*|\bsombri\w*|\bmoody\b"),
    "suave": re.compile(r"\bsuaves?\b|\bpastel\b"),
}
RE_AUTOMATICO = re.compile(r"\bmelhor(?:a|ar|e)\b|\bautomatic\w*|\barrum(?:a|ar|e)\b|\bdeix\w*\W+(?:\w+\W+){0,3}?bonit\w*|\bretoc\w*")
RE_MAIS_CLARA = re.compile(r"\bclare(?:i|a|ar|ie)\w*|\bmais\W+(?:clar[ao]|luz|brilho|iluminad[ao])\b"
                           r"|\b(?:aument|sobe|subir|coloc|poe)\w*\W+(?:o\W+|a\W+|mais\W+)?(?:brilho|luz|exposicao)\b")
RE_MAIS_ESCURA = re.compile(r"\bescure\w*|\bmais\W+escur[ao]\b|\bmenos\W+(?:luz|brilho)\b"
                            r"|\b(?:diminu|abaix|reduz|tir)\w*\W+(?:o\W+|a\W+|um\W+pouco\W+(?:d[oa]\W+)?)?(?:brilho|luz|exposicao)\b")
RE_MAIS_CONTRASTE = re.compile(r"\bmais\W+contraste\b|\b(?:aument|sobe)\w*\W+(?:o\W+)?contraste\b|\bcontraste\W+(?:forte|alto)\b")
RE_MENOS_CONTRASTE = re.compile(r"\bmenos\W+contraste\b|\b(?:diminu|abaix|reduz)\w*\W+(?:o\W+)?contraste\b")
RE_MAIS_COR = re.compile(r"\bmais\W+(?:cor|cores|colorid[ao]|saturad\w*|viv[ao]s?)\b|\b(?:aument|sobe)\w*\W+(?:a\W+)?(?:saturac\w*|cor|cores)\b")
RE_MENOS_COR = re.compile(r"\bmenos\W+(?:cor|cores|saturad\w*)\b|\bdessatur\w*|\b(?:diminu|abaix|reduz)\w*\W+(?:a\W+)?(?:saturac\w*|cor|cores)\b")
RE_NITIDEZ = re.compile(r"\bnitid\w*|\bmais\W+definid\w*|\bdesembac\w*|\bmenos\W+borrad\w*|\brealc\w*\W+(?:os\W+)?detalhes\b")
RE_RUIDO = re.compile(r"\b(?:tir|remov|diminu|reduz|limp|sem|menos)\w*\W+(?:o\W+|a\W+)?(?:ruido|granulad\w*|graos?|granulac\w*)\b")
RE_BEM = re.compile(r"\b(?:bem|muito|bastante)\W+(?:mais|menos)\b")
RE_GIRAR = re.compile(r"\b(?:gir\w*|rotacion\w*|rod(?:a|ar|e)\b|vir(?:a|ar|e)\b(?=(?:\W+\w+){0,4}?\W+(?:direita|esquerda|horario|graus)))")
RE_ESQUERDA = re.compile(r"\besquerda\b|\banti\W?horario\b")
RE_180 = re.compile(r"\b180\b|\bcabeca\W+pra\W+baixo\b|\bde\W+ponta\W+cabeca\b|\bao\W+contrario\b")
RE_ESPELHAR = re.compile(r"\bespelh\w*|\binvert\w*\W+(?:\w+\W+){0,2}?(?:lado|horizontal\w*)\b")
RE_RECORTES = {
    "1x1": re.compile(r"\bquadrad[ao]s?\b|\b1\W?(?:x|por)\W?1\b|\bpro\W+feed\b|\bpro\W+instagram\b"),
    "9x16": re.compile(r"\bstor(?:y|ies|is)\b|\b9\W?(?:x|por)\W?16\b"),
    "16x9": re.compile(r"\b16\W?(?:x|por)\W?9\b|\bwidescreen\b"),
    "4x5": re.compile(r"\b4\W?(?:x|por)\W?5\b"),
}
# "No meu estilo" (estilo_video.py) e o título dito ("com o título Férias na praia", até o fim da frase), no texto original.
RE_MEU_ESTILO = re.compile(r"\b(?:no|com\s+o|do|pro)\s+meu\s+estilo\b|\bmeu\s+estilo\s+(?:nesse|neste|no)\b")
RE_TITULO_DO_VIDEO = re.compile(r"\s*,?\s*\b(?:e\s+)?com\s+(?:o\s+)?t[íi]tulo\s+(?:de\s+)?(?P<titulo>.{1,80}?)\s*$", re.IGNORECASE)
VAZIAS_DA_EDICAO = set("""
deixa deixar deixe coloca colocar coloque poe por aplica aplicar aplique edita editar edite faz faca fazer fica ficar mais menos
bem muito bastante pouco filtro efeito cara estilo tom versao video videos clipe clipes gravacao foto fotos imagem imagens
selfie selfies print prints retrato fotografia segundos segundo minutos minuto primeiros primeiras ultimos ultimas ate partir
comeca comecar trecho parte corta cortar corte recorta recortar tira tirar remove remover lado graus direita esquerda horario
""".split())
RE_DIMINUIR = re.compile(r"\b(?:diminu|reduz|comprim|encolh)\w*\b(?!\W+(?:o\W+|a\W+)?(?:brilho|luz|contraste|saturac\w*|cor|cores|exposic\w*|velocidade|ruido|som|volume))"
                         r"|\bmais\W+leve\b|\bmenor(?:zinh\w*)?\b|\bpesad[ao]\b|\bpra\W+mandar\b|\bmetade\W+do\W+tamanho\b")
RE_PIXELS = re.compile(r"\b(\d{3,4})\W*(?:px|pixels?)\b")
RE_PARA_FORMATO = re.compile(r"\b(?:em|como|pra|para)\W+(?:um\W+|uma\W+)?(png|jpe?g|webp|tiff?)\b")
# Vídeo: velocidade, som, saída e o momento de uma imagem
RE_RAPIDO = re.compile(r"\bacelera\w*|\bmais\W+rapid\w*|\b(\d)\W?x\b|\bem\W+dobro\b|\b(?:duas|tres|quatro)\W+vezes\W+mais\W+rapid\w*|\btimelapse\b")
RE_LENTO = re.compile(r"\bcamera\W+lenta\b|\bmais\W+devagar\b|\bdesacelera\w*|\bslow\W?motion\b|\bmetade\W+da\W+velocidade\b|\bcamera\W+lent\w*")
RE_SO_O_SOM = re.compile(r"\b(?:extrai|extrair|separa|separar|salva|salvar|pega|pegar)\w*\W+(?:\w+\W+){0,2}?(?:som|audio|musica)\b"
                         r"|\bso\W+(?:o\W+)?(?:som|audio)\b|\b(?:em|vira|virar|pra|para)\W+(?:um\W+)?(?:mp3|audio|aac)\b")
RE_SEM_SOM = re.compile(r"\bsem\W+(?:o\W+)?(?:som|audio)\b|\b(?:tira|tirar|remove|remover|corta|cortar)\w*\W+(?:o\W+)?(?:som|audio)\b"
                        r"|\bmut[ae]\w*|\bmudo\b|\bsilenci\w*")
RE_GIF = re.compile(r"\bgif\b")
RE_IMAGEM_DO_VIDEO = re.compile(r"\b(?:tira|tirar|faz|fazer|pega|pegar|salva|salvar|gera|gerar|quero)\w*\W+(?:um\W+|uma\W+|o\W+|a\W+)?"
                                r"(?:print|foto|imagem|captura|quadro|frame)\b")
# Os tempos: "do 10 ao 30", "de 0:10 a 0:45", "entre 1:00 e 1:30", "os primeiros 15 segundos", "a partir do minuto 2"...
T = r"(\d{1,2}:\d{2}(?::\d{2})?|\d+(?:[.,]\d+)?)"
NUMEROS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
           "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "quatorze": 14, "catorze": 14, "quinze": 15,
           "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30, "quarenta": 40,
           "cinquenta": 50, "sessenta": 60, "noventa": 90}
RE_TRECHO = re.compile(rf"\b(?:d[oea]s?|desde\W+o?)\W*{T}\W*(?:ate\W+(?:o\W+|a\W+)?|aos?\W+|as?\W+|-\W*){T}|\bentre\W+{T}\W+e\W+{T}")
RE_PRIMEIROS = re.compile(rf"\b(?:(?P<tira>tir\w*|cort\w*|remov\w*|sem)\W+(?:os\W+|as\W+)?)?primeir[oa]s?\W+{T}")
RE_ULTIMOS = re.compile(rf"\b(?:(?P<tira>tir\w*|cort\w*|remov\w*|sem)\W+(?:os\W+|as\W+)?)?ultim[oa]s?\W+{T}")
RE_A_PARTIR = re.compile(rf"\ba\W+partir\W+d[oea]\W*{T}|\bcomec\w*\W+(?:n[oa]|em|aos?)\W*{T}")
RE_ATE = re.compile(rf"\bate\W+(?:o\W+|a\W+)?{T}")
RE_MOMENTO = re.compile(rf"\b(?:n[oa]|em|aos?|d[oa])\W*{T}")


def texto_de_tempo(texto: str) -> str:
    """O pedido em minúsculas e sem acento, mas com ":" e com os tempos em segundos ou "M:SS": "do minuto 1 e meio ao
    dois minutos" -> "do 1:30 ao 2:00". O resto (palavras) fica como estava."""
    decomposto = unicodedata.normalize("NFKD", (texto or "").lower())
    t = re.sub(r"[^a-z0-9:.,\s-]+", " ", "".join(c for c in decomposto if not unicodedata.combining(c)))
    t = re.sub(r"\b(vinte|trinta|quarenta|cinquenta)\s+e\s+(um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove)\b",
               lambda m: str(NUMEROS[m.group(1)] + NUMEROS[m.group(2)]), t)
    t = re.sub(rf"\b({'|'.join(NUMEROS)})\b(?=\s+(?:e\s+meio\s+)?(?:segundos?|minutos?|s\b|min\b))",
               lambda m: str(NUMEROS[m.group(1)]), t)
    t = re.sub(r"\bmeio\s+minuto\b", "30 segundos", t)
    t = re.sub(r"\b(\d+)\s+(?:minutos?|min)\s+e\s+meio\b", lambda m: f"{m.group(1)}:30", t)
    t = re.sub(r"\bminutos?\s+(\d+)\s+e\s+meio\b", lambda m: f"{m.group(1)}:30", t)
    t = re.sub(r"\b(\d+)\s*(?:minutos?|min)\s+e\s+(\d+)\s*(?:segundos?|seg|s)?\b", lambda m: f"{m.group(1)}:{int(m.group(2)):02d}", t)
    t = re.sub(r"\b(\d+)\s*(?:minutos?|min)\b", lambda m: f"{m.group(1)}:00", t)
    t = re.sub(r"\bminutos?\s+(\d+)\b(?!:)", lambda m: f"{m.group(1)}:00", t)
    t = re.sub(r"\bsegundos?\s+(\d+(?:[.,]\d+)?)\b", r"\1", t)
    t = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*(?:segundos?|seg|s)\b", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def segundos(valor: str) -> float:
    """ "1:30" -> 90, "12,5" -> 12.5."""
    if ":" in valor:
        partes = [int(p) for p in valor.split(":")]
        total = 0
        for parte in partes:
            total = total * 60 + parte
        return float(total)
    return float(valor.replace(",", "."))


def ler_edicao_de_foto(t: str):
    """(EdicaoDeFoto, padrões usados) do pedido sem acento ("deixa essa foto preto e branco e mais clara")."""
    edicao, usados = EdicaoDeFoto(), []

    def achou(padrao):
        if padrao.search(t):
            usados.append(padrao)
            return True
        return False

    for chave, padrao in RE_FILTROS.items():
        if achou(padrao):
            edicao.filtro = chave
            break
    edicao.automatico = achou(RE_AUTOMATICO)
    forte = bool(RE_BEM.search(t))
    if achou(RE_MAIS_CLARA):
        edicao.luz = 1.2 if forte else 0.6
    elif achou(RE_MAIS_ESCURA):
        edicao.luz = -1.2 if forte else -0.6
    if achou(RE_MAIS_CONTRASTE):
        edicao.contraste = 50 if forte else 30
    elif achou(RE_MENOS_CONTRASTE):
        edicao.contraste = -50 if forte else -30
    if achou(RE_MAIS_COR):
        edicao.cor = 60 if forte else 35
    elif achou(RE_MENOS_COR):
        edicao.cor = -60 if forte else -35
    if achou(RE_NITIDEZ):
        edicao.nitidez = 70
    if achou(RE_RUIDO):
        edicao.ruido = 50
    if achou(RE_GIRAR):
        edicao.girar = 2 if achou(RE_180) else 3 if achou(RE_ESQUERDA) else 1
        achou(re.compile(r"\bdireita\b|\bhorario\b|\b90\W+graus\b"))
    edicao.espelhar = achou(RE_ESPELHAR)
    for chave, padrao in RE_RECORTES.items():
        if achou(padrao):
            edicao.recorte = chave
            break
    pixels = RE_PIXELS.search(t)
    if pixels:
        usados.append(RE_PIXELS)
        edicao.tamanho = max(200, min(8000, int(pixels.group(1))))
    elif achou(RE_DIMINUIR):
        edicao.tamanho = 1080 if re.search(r"\bbem\W+(?:menor|mais\W+leve)\b", t) else 1600
    formato = RE_PARA_FORMATO.search(t)
    if formato:
        usados.append(RE_PARA_FORMATO)
        edicao.formato = {"png": ".png", "webp": ".webp", "tif": ".tif", "tiff": ".tif"}.get(formato.group(1), ".jpg")
    return edicao, usados


def ler_edicao_de_video(texto: str, t: str):
    """(EdicaoDeVideo, padrões usados) do pedido: `texto` é o original (os tempos), `t` sem acento."""
    edicao, usados = EdicaoDeVideo(), []
    p = texto_de_tempo(texto)

    def achou(padrao, alvo=t):
        if padrao.search(alvo):
            usados.append(padrao)
            return True
        return False

    trecho = RE_TRECHO.search(p)
    primeiros, ultimos = RE_PRIMEIROS.search(p), RE_ULTIMOS.search(p)
    if trecho:
        a, b = (trecho.group(1), trecho.group(2)) if trecho.group(1) else (trecho.group(3), trecho.group(4))
        edicao.inicio, edicao.fim = segundos(a), segundos(b)
    else:
        if primeiros:
            if primeiros.group("tira"):
                edicao.inicio = segundos(primeiros.group(2))
            else:
                edicao.inicio, edicao.fim = 0.0, segundos(primeiros.group(2))
        if ultimos:
            if ultimos.group("tira"):
                edicao.sem_os_ultimos = segundos(ultimos.group(2))
            else:
                edicao.ultimos = segundos(ultimos.group(2))
        a_partir = RE_A_PARTIR.search(p)
        if a_partir and edicao.inicio is None:
            edicao.inicio = segundos(a_partir.group(1) or a_partir.group(2))
        ate = RE_ATE.search(p)
        if ate and edicao.fim is None and not edicao.sem_os_ultimos:
            edicao.fim = segundos(ate.group(1))
    if achou(RE_IMAGEM_DO_VIDEO):
        momento = RE_MOMENTO.search(p)
        edicao.saida, edicao.inicio, edicao.fim = ".jpg", (segundos(momento.group(1)) if momento else 0.0), None
    elif achou(RE_GIF):
        edicao.saida = ".gif"
    elif achou(RE_SO_O_SOM):
        edicao.saida = ".aac"
    if edicao.saida == ".mp4" and achou(RE_SEM_SOM):
        edicao.sem_som = True
    rapido = RE_RAPIDO.search(t)
    if rapido:
        usados.append(RE_RAPIDO)
        # "acelera esse vídeo 4x": o número vale mesmo vindo depois do verbo
        numero = re.search(r"\b(\d)\W?x\b", t) or re.search(r"\b(tres|quatro)\W+vezes\b", t)
        vezes = {"tres": 3, "quatro": 4}.get(numero.group(1), None) if numero else 2
        vezes = vezes or int(numero.group(1))
        edicao.velocidade = max(100, min(400, 100 * vezes))
    elif achou(RE_LENTO):
        edicao.velocidade = 50
    for chave, padrao in RE_FILTROS.items():
        if achou(padrao):
            edicao.filtro = chave
            break
    edicao.metade = achou(RE_DIMINUIR)
    if any(v is not None for v in (edicao.inicio, edicao.fim)) or edicao.ultimos or edicao.sem_os_ultimos:
        usados.append(re.compile(r"\b\d+\b"))  # os números dos tempos não são o nome do arquivo
    return edicao, usados


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento e só letras e números separados por espaço."""
    decomposto = unicodedata.normalize("NFKD", (texto or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in decomposto if not unicodedata.combining(c))).strip()


def abrir_no_pc(caminho: str):
    """Abre o arquivo no programa padrão do Windows (ou a pasta, no Explorador)."""
    os.startfile(caminho)


def app_sem_nome(t: str, app: App) -> str:
    """A frase sem o nome do app ("abre esse pdf no pdf craft" -> "abre esse pdf no")."""
    padrao = RE_LAUNCHER if app.chave == "launcher" else RE_DO_APP[app.chave]
    return re.sub(r"\s+", " ", padrao.sub(" ", t)).strip()


@dataclass
class RespostaCraft:
    texto: str
    arquivos: list = field(default_factory=list)  # o que vai pelo WhatsApp ou pelo app (o pedido veio de lá ou disse "me manda")


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _lista_falada(partes: list[str]) -> str:
    """["a", "b", "c"] -> "a, b e c"."""
    return partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + f" e {partes[-1]}" if partes else ""


def _frase_do_video(nome: str, edicao: EdicaoDeVideo, destino: str) -> str:
    feito = os.path.basename(destino)
    if edicao.saida == ".jpg":
        return f"Pronto: tirei {edicao.descricao()[0]} do vídeo “{nome}”, “{feito}”, em documentos gerados."
    if edicao.saida == ".aac":
        return f"Pronto: separei o som do vídeo “{nome}”, “{feito}”, em documentos gerados."
    como = _lista_falada(edicao.descricao())
    return f"Pronto: o vídeo ficou {como or 'em MP4'}, “{feito}”, em documentos gerados."


def _ordem_natural(caminho: str):
    """"aula 2" antes de "aula 10"."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", os.path.basename(caminho).lower())]


class AgenteCraft:
    """`responder(texto, canal)` devolve `RespostaCraft` ou None (o roteamento segue).

    `recebidos(texto)` dá os arquivos que "esse"/"esses" querem dizer, na ordem em que chegaram
    (`AgenteFontes.recebidos_do_pedido`); `arquivos` é o
    índice de arquivos pelo nome aproximado (`busca_arquivos.IndiceDeArquivos`, o do `AgentePastas`)."""

    def __init__(self, apps: AppsCraft | None = None, recebidos=None, arquivos=None, pasta_saida: str | None = None,
                 abrir_arquivo=None, relogio=time.time, avisar=None, em_segundo_plano=None, estilizar=None, transcrever=None,
                 video_animado_recente=None):
        if pasta_saida is None:
            from config import PASTA_DOCUMENTOS_GERADOS

            pasta_saida = PASTA_DOCUMENTOS_GERADOS
        self.apps = apps or AppsCraft()
        self.recebidos = recebidos or (lambda texto: [])
        self.arquivos = arquivos
        self.pasta_saida = pasta_saida
        self.abrir_arquivo = abrir_arquivo  # None = abrir_no_pc (o conftest.py o proíbe nos testes)
        self.relogio = relogio
        # Vídeos levam de segundos a minutos: são feitos em segundo plano e o fim chega por `avisar(RespostaCraft, canal)`.
        self.avisar = avisar or (lambda resposta, canal: None)
        self.em_segundo_plano = em_segundo_plano or (lambda funcao: threading.Thread(target=funcao, daemon=True,
                                                                                      name="apps-craft").start())
        self._tarefas = {}  # as edições pedidas pela tela do celular: id -> situação
        self._trava = threading.Lock()
        # "Edita esse vídeo no meu estilo": video_animado.estilizar_video e o Whisper do PC (voice.transcrever_palavras).
        self.estilizar = estilizar
        self.transcrever = transcrever
        self.video_animado_recente = video_animado_recente or (lambda: False)

    # ------------------------------------------------------------------ pedidos
    def responder(self, texto: str, canal: str = "voz") -> RespostaCraft | None:
        t = normalizar(texto)
        if not t:
            return None
        if RE_LISTAR.search(t):
            return RespostaCraft(self._listar(canal))
        app = app_citado(t)
        if app is not None and RE_PRA_QUE.search(t) and not RE_ABRIR.search(t):
            return RespostaCraft(self._descrever(app))
        resposta = self._no_meu_estilo(texto, t, canal) if RE_MEU_ESTILO.search(t) else None
        if resposta:
            return resposta
        resposta = self._editar(texto, t, canal)
        if resposta:
            return resposta
        if RE_JUNTAR.search(t) and RE_OBJETO_DE_JUNTAR.search(t):
            resposta = self._juntar(texto, t, canal)
            if resposta:
                return resposta
        alvo = RE_ALVO.search(t)
        if alvo and (RE_CONVERTE.search(t) or RE_TRANSFORMA.search(t)):
            resposta = self._converter(texto, t, alvo, canal)
            if resposta:
                return resposta
        if app is not None and RE_ABRIR.search(t):
            return RespostaCraft(self._abrir(texto, t, app, canal))
        return None

    # ------------------------------------------------------------------ abrir e listar
    def _listar(self, canal: str) -> str:
        instalados = [a for a in self.apps.instalados() if a in APPS]
        if not instalados:
            return "Nenhum app da ArtCraft está instalado. Eles vêm pelo ArtCraft Launcher."
        if canal == "voz":
            nomes = instalados[0].nome if len(instalados) == 1 else (
                ", ".join(a.nome for a in instalados[:-1]) + f" e {instalados[-1].nome}")
            return f"Tenho {_plural(len(instalados), 'app', 'apps')} da ArtCraft: {nomes}. Pergunte pra que serve qualquer um."
        linhas = "\n".join(f"• {a.nome}: {a.para_que}" for a in instalados)
        return f"Apps da ArtCraft instalados:\n{linhas}"

    def _descrever(self, app: App) -> str:
        instalado = self.apps.executavel(app) is not None
        return f"O {app.nome} é para {app.para_que}." + ("" if instalado else " Ele não está instalado; vem pelo ArtCraft Launcher.")

    def _abrir(self, texto: str, t: str, app: App, canal: str) -> str:
        if canal in CANAIS_REMOTOS:
            return "Pelo celular eu não abro programas no PC. Quando estiver no computador, é só pedir."
        arquivo = None
        resto = RE_ABRIR.sub(" ", app_sem_nome(t, app))
        if RE_REFERE.search(resto) or self._termos(resto) or RE_CAMINHO.search(texto):
            arquivos, problema = self._origem(texto, resto, self._tipo_de_origem(resto))
            if problema:
                return problema
            arquivo = arquivos[0] if arquivos else None
        try:
            self.apps.abrir(app, arquivo)
        except ErroCraft as exc:
            return str(exc)
        except OSError as exc:
            return f"Não consegui abrir o {app.nome}: {exc}"
        return f"Abri “{os.path.basename(arquivo)}” no {app.nome}." if arquivo else f"Abri o {app.nome}."

    # ------------------------------------------------------------------ converter
    def _converter(self, texto: str, t: str, alvo: re.Match, canal: str) -> RespostaCraft | None:
        extensao = FORMATOS[alvo.group("formato")]
        explicito = bool(RE_CONVERTE.search(t))
        resto = (t[:alvo.start()] + " " + t[alvo.end():]).strip()
        tipo = self._tipo_de_origem(resto)
        if not explicito and tipo is None and self._termos(resto):
            return None  # "salva essa resposta em PDF": não fala de um arquivo
        arquivos, problema = self._origem(texto, resto, tipo, excluir=extensao)
        if problema:
            return RespostaCraft(problema) if explicito or tipo else None
        if not arquivos:
            if explicito and tipo:
                return RespostaCraft("Qual arquivo? Diga uma parte do nome ou me mande o arquivo antes.")
            return None
        if not explicito and not all(self._converte(a, extensao) for a in arquivos):
            return None  # "transforma esse PDF numa apresentação" é com os agentes
        pasta = self._pasta_do_dia()
        feitos, falhas = [], []
        for arquivo in arquivos[:MAXIMO_PARA_JUNTAR]:
            try:
                feitos.append((arquivo, self.apps.converter(arquivo, extensao, pasta)))
            except ErroCraft as exc:
                falhas.append(str(exc))
            except OSError as exc:  # disco cheio, arquivo em uso...: o laço principal não pode cair por isso
                falhas.append(f"Não consegui gravar o resultado: {exc}")
        if not feitos:
            return RespostaCraft(falhas[0])
        formato = FORMATO_FALADO.get(extensao, extensao)
        if len(feitos) == 1:
            origem, saidas = feitos[0]
            if len(saidas) == 1:
                frase = f"Pronto: “{os.path.basename(origem)}” virou {formato}, “{os.path.basename(saidas[0])}”, em documentos gerados."
            else:
                frase = f"Pronto: “{os.path.basename(origem)}” virou {_plural(len(saidas), 'imagem', 'imagens')}, uma por página."
        else:
            frase = f"Pronto: converti {_plural(len(feitos), 'arquivo', 'arquivos')} em {formato}, em documentos gerados."
        if falhas:
            frase += f" {_plural(len(falhas), 'não deu', 'não deram')}: {falhas[0]}"
        return self._entregar(frase, [s for _, lista in feitos for s in lista], t, canal)

    @staticmethod
    def _converte(arquivo: str, extensao: str) -> bool:
        origem = os.path.splitext(arquivo)[1].lower()
        if origem == ".pdf":
            return extensao in (".png", ".jpg", ".txt")
        for entradas, saidas in CONVERSORES.values():
            if origem in entradas:
                return extensao in saidas or (extensao in (".png", ".jpg") and ".pdf" in saidas)
        return False

    # ------------------------------------------------------------------ juntar
    def _juntar(self, texto: str, t: str, canal: str) -> RespostaCraft | None:
        sem_destino = RE_NUM_PDF.sub(" ", t)  # "junta esses arquivos num PDF só": o PDF é o resultado, não o tipo
        arquivos, problema = self._origem(texto, sem_destino, self._tipo_de_origem(sem_destino), varios=True)
        if problema:
            return RespostaCraft(problema)
        if not arquivos:
            if RE_REFERE.search(t) or re.search(r"\bpdfs?\b", t):
                return RespostaCraft("Não achei os arquivos para juntar. Me mande os arquivos antes, ou diga uma parte do nome.")
            return None
        if len(arquivos) < 2:
            return RespostaCraft(f"Só achei um arquivo, “{os.path.basename(arquivos[0])}”. Para juntar, preciso de pelo menos dois.")
        arquivos = arquivos[:MAXIMO_PARA_JUNTAR]
        primeiro = os.path.splitext(os.path.basename(arquivos[0]))[0][:60].rstrip()
        try:
            destino = self.apps.juntar(arquivos, self._pasta_do_dia(), f"Juntos - {primeiro} e mais {len(arquivos) - 1}.pdf")
        except ErroCraft as exc:
            return RespostaCraft(str(exc))
        except OSError as exc:
            return RespostaCraft(f"Não consegui gravar o PDF: {exc}")
        paginas = self.apps.paginas(destino)
        detalhe = f", com {_plural(paginas, 'página', 'páginas')}" if paginas else ""
        frase = f"Pronto: juntei {len(arquivos)} arquivos num PDF só, “{os.path.basename(destino)}”{detalhe}, em documentos gerados."
        return self._entregar(frase, [destino], t, canal)

    # ------------------------------------------------------------------ editar fotos e vídeos
    def _editar(self, texto: str, t: str, canal: str) -> RespostaCraft | None:
        """"Deixa essa foto preto e branco", "corta esse vídeo do 10 ao 30 e tira o som". None se não for edição."""
        midia = "video" if RE_VIDEO.search(t) else "foto" if RE_FOTO.search(t) else ""
        so_pelo_isso = not midia
        if so_pelo_isso:  # "deixa isso preto e branco": o tipo é o do arquivo que acabou de chegar
            if not RE_REFERE.search(t):
                return None
            recebidos = self.recebidos(texto)
            extensao = os.path.splitext(recebidos[-1])[1].lower() if recebidos else ""
            midia = "video" if extensao in VIDEOS else "foto" if extensao in FOTOS else ""
            if not midia:
                return None
        if midia == "foto":
            edicao, usados = ler_edicao_de_foto(t)
            # Sem dizer "foto", só o que não tem outro sentido: "deixa isso mais claro" é pedir uma explicação melhor.
            fortes = edicao.filtro or edicao.girar or edicao.espelhar or edicao.recorte
        else:
            edicao, usados = ler_edicao_de_video(texto, t)
            fortes = (edicao.saida != ".mp4" or edicao.velocidade != 100 or edicao.filtro or edicao.sem_som
                      or edicao.inicio is not None or edicao.fim is not None or edicao.ultimos or edicao.sem_os_ultimos)
        if edicao.vazia() or (so_pelo_isso and not fortes):
            return None
        resto = t
        for padrao in usados:
            resto = padrao.sub(" ", resto)
        resto = " ".join(p for p in resto.split() if p not in VAZIAS_DA_EDICAO)
        arquivos, problema = self._origem(texto, resto, FOTOS if midia == "foto" else VIDEOS)
        if problema:
            return RespostaCraft(problema)
        nome = "foto" if midia == "foto" else "vídeo"
        if not arquivos:
            return RespostaCraft(f"Qual {nome}? Me mande o {nome} antes, ou diga uma parte do nome do arquivo."
                                 .replace("o foto", "a foto"))
        if midia == "foto":
            return self._editar_fotos(arquivos, edicao, t, canal)
        return self._editar_video_depois(arquivos[-1], edicao, t, canal)

    def _editar_fotos(self, arquivos: list[str], edicao: EdicaoDeFoto, t: str, canal: str) -> RespostaCraft:
        pasta, feitos, falhas = self._pasta_do_dia(), [], []
        for arquivo in arquivos[:MAXIMO_PARA_MANDAR]:
            try:
                feitos.append(self.apps.editar_foto(arquivo, edicao, pasta))
            except ErroCraft as exc:
                falhas.append(str(exc))
            except OSError as exc:
                falhas.append(f"Não consegui gravar a foto: {exc}")
        if not feitos:
            return RespostaCraft(falhas[0])
        como = _lista_falada(edicao.descricao())
        if len(feitos) == 1:
            frase = f"Pronto: a foto ficou {como}, “{os.path.basename(feitos[0])}”, em documentos gerados."
        else:
            frase = f"Pronto: {len(feitos)} fotos ficaram {como}, em documentos gerados."
        if falhas:
            frase += f" {_plural(len(falhas), 'não deu', 'não deram')}: {falhas[0]}"
        return self._entregar(frase, feitos, t, canal)

    def _editar_video_depois(self, video: str, edicao: EdicaoDeVideo, t: str, canal: str, tarefa: str = "") -> RespostaCraft:
        """Começa a edição em segundo plano e responde na hora; o resultado chega por `avisar` (e pela situação da tarefa)."""
        nome = os.path.basename(video)

        def editar():
            try:
                destino = self.apps.editar_video(video, edicao, self._pasta_do_dia())
                resposta = self._entregar(_frase_do_video(nome, edicao, destino), [destino], t, canal)
                arquivos = [destino]
            except (ErroCraft, OSError) as exc:
                resposta, arquivos = RespostaCraft(f"Não deu para editar “{nome}”: {exc}"), []
            if tarefa:
                self._marcar(tarefa, pronto=True, ok=bool(arquivos), texto=resposta.texto, arquivos=arquivos)
            self.avisar(resposta, canal)

        self.em_segundo_plano(editar)
        como = _lista_falada(edicao.descricao())
        return RespostaCraft(f"Editando “{nome}” no FilmCraft{': ' + como if como else ''}. Aviso quando ficar pronto.")

    # ------------------------------------------------------------------ o meu estilo (estilo_video.py)
    def _no_meu_estilo(self, texto: str, t: str, canal: str) -> RespostaCraft | None:
        """"Edita esse vídeo no meu estilo", "põe o vídeo da viagem no meu estilo com o título Férias": o filtro do estilo (e o
        que mais a frase pedir: cortar, acelerar) pelo FilmCraft, e o enquadramento, o título, as legendas e a assinatura em
        video_animado.estilizar_video. None se não for sobre um vídeo (o estilo é só de vídeos)."""
        if RE_FOTO.search(t) and not RE_VIDEO.search(t):
            return None
        titulo = RE_TITULO_DO_VIDEO.search(texto)
        sem_titulo = RE_TITULO_DO_VIDEO.sub(" ", texto)
        t = normalizar(sem_titulo)
        edicao, usados = ler_edicao_de_video(sem_titulo, t)
        resto = RE_MEU_ESTILO.sub(" ", t)
        for padrao in usados:
            resto = padrao.sub(" ", resto)
        resto = " ".join(p for p in resto.split() if p not in VAZIAS_DA_EDICAO)
        arquivos, problema = self._origem(sem_titulo, resto, VIDEOS)
        if problema:
            return RespostaCraft(problema)
        if not arquivos:
            # Sem vídeo seu, "aplica meu estilo no vídeo" logo depois de um vídeo animado é refazê-lo (criacao_visual.py).
            if not RE_VIDEO.search(t) or self.video_animado_recente():
                return None
            return RespostaCraft("Qual vídeo? Me mande o vídeo antes, ou diga uma parte do nome do arquivo.")
        if edicao.saida != ".mp4":
            return RespostaCraft("O meu estilo vale para o vídeo em MP4: peça o GIF ou o som numa frase à parte.")
        video, nome = arquivos[-1], os.path.basename(arquivos[-1])
        titulo = titulo.group("titulo").strip(" \"'“”.") if titulo else ""

        def trabalhar():
            import tempfile

            import estilo_video

            estilo = estilo_video.atual()
            if not edicao.filtro and estilo["edicao"].get("filtro"):
                edicao.filtro = estilo["edicao"]["filtro"]
            temporaria = tempfile.mkdtemp(prefix="beatriz_estilo_")
            try:
                entrada = video if edicao.vazia() else self.apps.editar_video(video, edicao, temporaria)
                destino = os.path.join(self._pasta_do_dia(), f"{os.path.splitext(nome)[0]} (meu estilo).mp4")
                os.makedirs(os.path.dirname(destino), exist_ok=True)
                estilizar = self.estilizar
                if estilizar is None:
                    from video_animado import estilizar_video as estilizar
                transcrever = self.transcrever
                if transcrever is None:
                    from voice import transcrever_palavras as transcrever
                info = estilizar(entrada, estilo, destino, titulo, transcrever)
                partes = [p for p in (_lista_falada(edicao.descricao()), f"com {info.get('legendas')} legendas"
                                      if info.get("legendas") else "", f"com o título “{titulo}”" if titulo else "") if p]
                frase = f"Pronto: “{nome}” no seu estilo{', ' + ', '.join(partes) if partes else ''}, em documentos gerados."
                resposta = self._entregar(frase, [destino], t, canal)
            except (ErroCraft, OSError, RuntimeError, ValueError) as exc:
                resposta = RespostaCraft(f"Não deu para pôr “{nome}” no seu estilo: {exc}")
            finally:
                import shutil

                shutil.rmtree(temporaria, ignore_errors=True)
            self.avisar(resposta, canal)

        self.em_segundo_plano(trabalhar)
        return RespostaCraft(f"Pondo “{nome}” no seu estilo{' com o título “' + titulo + '”' if titulo else ''}. "
                             "Leva de um a alguns minutos (as legendas são ouvidas no PC); aviso quando ficar pronto.")

    # ------------------------------------------------------------------ a tela "Editar" do celular
    def editar_do_app(self, caminho: str, dados: dict) -> dict:
        """A tela "Editar" do app mandou um arquivo e o que fazer com ele: {"tipo": "foto"|"video"|"converter", ...}.
        Foto e conversão voltam prontas; vídeo (e conversão de vídeo) volta como tarefa, e o celular pergunta pela situação."""
        tipo, extensao = str(dados.get("tipo") or ""), os.path.splitext(caminho)[1].lower()
        try:
            if tipo == "foto":
                edicao = EdicaoDeFoto.do_app(dados)
                if edicao.vazia():
                    return {"pronto": True, "ok": False, "texto": "Escolha pelo menos um ajuste.", "arquivos": []}
                destino = self.apps.editar_foto(caminho, edicao, self._pasta_do_dia())
                como = _lista_falada(edicao.descricao())
                return {"pronto": True, "ok": True, "texto": f"Pronto: a foto ficou {como}.", "arquivos": [destino]}
            if tipo == "video" or (tipo == "converter" and extensao in VIDEOS):
                edicao = EdicaoDeVideo.do_app(dados) if tipo == "video" else EdicaoDeVideo(saida=str(dados.get("formato") or ""))
                if tipo == "converter" and edicao.saida not in (".mp4", ".gif", ".aac"):
                    return {"pronto": True, "ok": False, "texto": "Vídeo eu passo para MP4, GIF ou só o som.", "arquivos": []}
                if tipo == "video" and edicao.vazia():
                    return {"pronto": True, "ok": False, "texto": "Escolha pelo menos um ajuste.", "arquivos": []}
                tarefa = uuid.uuid4().hex[:12]
                resposta = self._editar_video_depois(caminho, edicao, "", "app", tarefa=tarefa)
                self._marcar(tarefa, pronto=False, ok=None, texto=resposta.texto, arquivos=[])
                return {"pronto": False, "tarefa": tarefa, "texto": resposta.texto, "arquivos": []}
            if tipo == "converter":
                formato = str(dados.get("formato") or "")
                if formato not in FORMATO_FALADO:
                    return {"pronto": True, "ok": False, "texto": "Formato desconhecido.", "arquivos": []}
                feitos = self.apps.converter(caminho, formato, self._pasta_do_dia())
                return {"pronto": True, "ok": True, "arquivos": feitos,
                        "texto": f"Pronto: virou {FORMATO_FALADO[formato]}" + (f" ({len(feitos)} imagens)." if len(feitos) > 1 else ".")}
        except ErroCraft as exc:
            return {"pronto": True, "ok": False, "texto": str(exc), "arquivos": []}
        except OSError as exc:
            return {"pronto": True, "ok": False, "texto": f"Não consegui gravar o resultado: {exc}", "arquivos": []}
        return {"pronto": True, "ok": False, "texto": "Pedido de edição desconhecido.", "arquivos": []}

    def situacao(self, tarefa: str) -> dict | None:
        with self._trava:
            return dict(self._tarefas[tarefa]) if tarefa in self._tarefas else None

    def _marcar(self, tarefa: str, **situacao):
        with self._trava:
            if tarefa in self._tarefas and self._tarefas[tarefa].get("pronto"):
                return  # o vídeo acabou antes de a tarefa ser anotada: fica o resultado
            self._tarefas[tarefa] = situacao
            while len(self._tarefas) > 30:
                self._tarefas.pop(next(iter(self._tarefas)))

    # ------------------------------------------------------------------ de onde vêm os arquivos
    @staticmethod
    def _tipo_de_origem(t: str) -> tuple | None:
        for padrao, extensoes in TIPOS_DE_ORIGEM:
            if padrao.search(t):
                return extensoes
        return None

    @staticmethod
    def _termos(t: str) -> list[str]:
        """As palavras que descrevem o arquivo ("converte a prova 2 de penal" -> prova 2 penal), sem os nomes dos apps."""
        for app in APPS:
            t = app_sem_nome(t, app)
        return busca_arquivos.palavras_do_pedido(t, vazias=VAZIAS)

    def _origem(self, texto: str, t: str, tipo, excluir: str = "", varios: bool = False) -> tuple[list[str], str]:
        """(arquivos, problema): um caminho dito, os que acabaram de chegar ("esse", "esses") ou os achados pelo nome."""
        caminho = RE_CAMINHO.search(texto)
        if caminho:
            achado = caminho.group().strip()
            return ([achado], "") if os.path.isfile(achado) else ([], f"Não achei o arquivo “{achado}”.")
        extensoes = tuple(e for e in (tipo or TODAS_AS_ENTRADAS) if e != excluir)
        termos = self._termos(t)
        if RE_REFERE.search(t) or not termos:
            recebidos = [a for a in self.recebidos(texto) if os.path.splitext(a)[1].lower() in extensoes]
            if recebidos:  # na ordem em que chegaram; "esse" é o último
                return (recebidos if varios or RE_VARIOS.search(t) else recebidos[-1:]), ""
        if not termos or self.arquivos is None:
            return [], ""
        achados = self.arquivos.buscar(termos, set(extensoes), maximo=60 if varios else 10)
        if not achados:
            return [], f"Não achei nenhum arquivo com “{' '.join(termos)}”."
        if varios:
            juntos = busca_arquivos.empatados(achados, folga=FOLGA_PARA_JUNTAR, maximo=MAXIMO_PARA_JUNTAR)
            return sorted((a.caminho for a in juntos), key=_ordem_natural), ""
        candidatos = busca_arquivos.empatados(achados)
        if len(candidatos) > 1:
            nomes = " e ".join(f"“{a.nome}” ({a.lugar})" for a in candidatos[:3])
            return [], f"Achei mais de um: {nomes}. Diga mais uma parte do nome."
        return [achados[0][1].caminho], ""

    # ------------------------------------------------------------------ entrega
    def _pasta_do_dia(self) -> str:
        return os.path.join(self.pasta_saida, datetime.fromtimestamp(self.relogio()).strftime("%Y-%m-%d") + "_conversoes")

    def _entregar(self, frase: str, saidas: list[str], t: str, canal: str) -> RespostaCraft:
        if canal in CANAIS_REMOTOS or RE_MANDAR.search(t):  # pedido do celular, ou "... e me manda": o resultado vai para lá
            return RespostaCraft(frase, saidas[:MAXIMO_PARA_MANDAR])
        try:
            (self.abrir_arquivo or abrir_no_pc)(saidas[0] if len(saidas) == 1 else os.path.dirname(saidas[0]))
            frase += " Abri pra você."
        except OSError:
            pass
        return RespostaCraft(frase)
