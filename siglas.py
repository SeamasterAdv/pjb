"""Siglas: reconhecer "OAB", "STF", "CPC"... na fala e achar o que elas significam.

O Whisper escreve a sigla de vários jeitos ("O A B", "o.a.b.", "ó á bê"). `juntar_siglas` põe tudo em "OAB".
`vocabulario_de_voz` é a lista que o Whisper recebe como `hotwords` para já esperar as siglas (e os nomes) certos.
`interpretacoes` serve às buscas ("a pasta da ordem dos advogados" acha a pasta "OAB" e vice-versa).
Para acrescentar siglas suas, use `SIGLAS_EXTRAS` no .env: "TCC=Trabalho de Conclusão de Curso;XYZ=Meu projeto".
"""
import os
import re
import unicodedata

# sigla -> o que significa (as palavras do significado entram na busca por pastas)
SIGLAS = {
    "OAB": "Ordem dos Advogados do Brasil", "STF": "Supremo Tribunal Federal", "STJ": "Superior Tribunal de Justiça",
    "TST": "Tribunal Superior do Trabalho", "TSE": "Tribunal Superior Eleitoral", "CNJ": "Conselho Nacional de Justiça",
    "MP": "Ministério Público", "CF": "Constituição Federal", "CC": "Código Civil", "CP": "Código Penal",
    "CPC": "Código de Processo Civil", "CPP": "Código de Processo Penal", "CLT": "Consolidação das Leis do Trabalho",
    "CDC": "Código de Defesa do Consumidor", "ECA": "Estatuto da Criança e do Adolescente", "CTN": "Código Tributário Nacional",
    "CTB": "Código de Trânsito Brasileiro", "LGPD": "Lei Geral de Proteção de Dados", "LINDB": "Lei de Introdução às Normas do Direito Brasileiro",
    "ADI": "Ação Direta de Inconstitucionalidade", "ADPF": "Arguição de Descumprimento de Preceito Fundamental",
    "ENEM": "Exame Nacional do Ensino Médio", "INSS": "Instituto Nacional do Seguro Social", "FGTS": "Fundo de Garantia do Tempo de Serviço",
    "CPF": "Cadastro de Pessoas Físicas", "CNPJ": "Cadastro Nacional da Pessoa Jurídica", "RG": "Registro Geral",
    "CNH": "Carteira Nacional de Habilitação", "IPTU": "Imposto Predial e Territorial Urbano", "IPVA": "Imposto sobre a Propriedade de Veículos Automotores",
    "IRPF": "Imposto de Renda Pessoa Física", "SUS": "Sistema Único de Saúde", "TCC": "Trabalho de Conclusão de Curso",
    "PDF": "Portable Document Format", "TI": "Tecnologia da Informação", "RH": "Recursos Humanos", "IA": "Inteligência Artificial",
    "API": "Interface de Programação de Aplicações", "SQL": "Structured Query Language", "CSV": "Valores Separados por Vírgula",
}
STOP = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas", "para", "pra", "por", "com", "um", "uma"}


def _extras() -> dict:
    extras = {}
    for item in os.getenv("SIGLAS_EXTRAS", "").split(";"):
        if "=" in item:
            sigla, significado = item.split("=", 1)
            if sigla.strip():
                extras[sigla.strip().upper()] = significado.strip()
    return extras


def todas() -> dict:
    return {**SIGLAS, **_extras()}


def sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


# ------------------------------------------------------------------ letras soltas -> sigla
# Como o Whisper e as pessoas soletram, COM o acento: "pê" é a letra, "pé" é o pé; "dê" é a letra, "de" é a preposição.
NOMES_DE_LETRA = {
    "á": "A", "bê": "B", "be": "B", "cê": "C", "ce": "C", "dê": "D", "de": "D", "é": "E", "efe": "F", "gê": "G", "ge": "G",
    "agá": "H", "aga": "H", "í": "I", "jota": "J", "cá": "K", "ca": "K", "ele": "L", "eme": "M", "ene": "N", "ó": "O",
    "pê": "P", "pe": "P", "quê": "Q", "ke": "Q", "erre": "R", "esse": "S", "tê": "T", "te": "T", "ú": "U", "vê": "V",
    "ve": "V", "dáblio": "W", "dablio": "W", "xis": "X", "ípsilon": "Y", "ipsilon": "Y", "zê": "Z", "ze": "Z",
}
# Nomes de letra que também são palavras comuns: sozinhos não provam que é uma sigla.
AMBIGUAS = {"a", "e", "o", "de", "te", "ve", "ele", "á", "é", "ó"}
_RE_TOKEN = re.compile(r"^([^\wÀ-ÿ]*)([\wÀ-ÿ]+)([^\wÀ-ÿ]*)$")


def _letra(palavra: str) -> str | None:
    """A letra que esta palavra soletra ("b" ou "bê" -> "B"), ou None."""
    minuscula = palavra.lower()
    if len(minuscula) == 1:
        return sem_acento(minuscula).upper() if minuscula.isalpha() else None
    return NOMES_DE_LETRA.get(minuscula)


def _forte(palavra: str) -> bool:
    """Uma letra que dificilmente é uma palavra de verdade: "b", "bê", "cê"... (e não "a", "e", "o", "de", "te")."""
    minuscula = palavra.lower()
    if len(minuscula) == 1:
        return sem_acento(minuscula) not in "aeo"
    return minuscula not in AMBIGUAS


def _recorte_da_sigla(corrida: list, conhecidas: set) -> tuple[int, int] | None:
    """Que pedaço da sequência de letras é a sigla: (início, fim) ou None.
    1) a maior parte que seja uma sigla conhecida ("s t f e" -> "s t f", e o "e" fica como palavra);
    2) senão, a sequência toda se tiver 3 letras ou mais, ao menos uma "forte" e não for só vogais, sem um "e" solto nas pontas."""
    letras = "".join(letra for letra, _ in corrida)
    for tamanho in range(len(corrida), 1, -1):
        for inicio in range(0, len(corrida) - tamanho + 1):
            if letras[inicio:inicio + tamanho].lower() in conhecidas:
                return inicio, inicio + tamanho
    inicio, fim = 0, len(corrida)
    while fim - inicio > 3 and sem_acento(corrida[inicio][1].group(2)) == "e":
        inicio += 1
    while fim - inicio > 3 and sem_acento(corrida[fim - 1][1].group(2)) == "e":
        fim -= 1
    pedaco = corrida[inicio:fim]
    if pedaco and pedaco[0][1].group(2).lower() in ("e", "é"):  # "e a b ou c", "é a b": a conjunção ou o verbo, não uma letra
        return None
    if len(pedaco) >= 3 and any(_forte(p.group(2)) for _, p in pedaco) and not set(letras[inicio:fim]) <= set("AEIOU"):
        return inicio, fim
    return None


def juntar_siglas(texto: str) -> str:
    """"abre a pasta da o a b" -> "abre a pasta da OAB"; "O.A.B." e "ó a bê" também. Prefere siglas conhecidas; as
    desconhecidas só se tiverem 3 letras ou mais, ao menos uma que não seja palavra comum ("a", "e", "o", "de") e não
    forem só vogais."""
    if not texto:
        return texto
    texto = re.sub(r"(?<![\wÀ-ÿ])((?:[A-Za-zÀ-ÿ]\.){2,})", lambda m: m.group(1).replace(".", "").upper(), texto)  # O.A.B.
    palavras = texto.split(" ")
    conhecidas = {s.lower() for s in todas()}
    saida, i = [], 0
    while i < len(palavras):
        corrida = []  # (letra, partes da palavra)
        j = i
        while j < len(palavras):
            partes = _RE_TOKEN.match(palavras[j])
            letra = _letra(partes.group(2)) if partes else None
            if letra is None or (corrida and partes.group(1)):  # aspas ou parêntese antes cortam a sequência
                break
            corrida.append((letra, partes))
            j += 1
            if partes.group(3):  # pontuação no fim da palavra encerra a sequência
                break
        recorte = _recorte_da_sigla(corrida, conhecidas) if len(corrida) >= 2 else None
        if recorte is None:
            saida.append(palavras[i])
            i += 1
            continue
        inicio, fim = recorte
        saida.extend(palavras[i:i + inicio])
        sigla = "".join(letra for letra, _ in corrida[inicio:fim])
        saida.append(corrida[inicio][1].group(1) + sigla + corrida[fim - 1][1].group(3))
        i += fim
    return " ".join(saida)


# ------------------------------------------------------------------ vocabulário e buscas
# As siglas que o Whisper mais erra e que mais pesam aqui ("OAB" saía "OBE" e "OB"). A lista é curta de propósito: com
# todas as siglas ele passou a emendar palavras ("o que o STF" virou "O QSTF"). Acrescente as suas em VOCABULARIO_VOZ
# (separadas por vírgula) no .env.
VOCABULARIO_PADRAO = ("OAB", "STF", "STJ", "CPC", "CF", "CLT", "CDC", "LGPD")


def vocabulario_de_voz(nomes: tuple = ("Beatriz",)) -> str:
    """Palavras que o Whisper deve esperar (hotwords): o nome dela, as siglas padrão e as de VOCABULARIO_VOZ."""
    extras = [p.strip() for p in os.getenv("VOCABULARIO_VOZ", "").split(",") if p.strip()]
    vistas, lista = set(), []
    for palavra in (*nomes, *VOCABULARIO_PADRAO, *extras):
        if palavra.lower() not in vistas:
            vistas.add(palavra.lower())
            lista.append(palavra)
    return " ".join(lista)


# Erros de contexto que as hotwords não resolvem: "artigo 5 da CF" sai "artigo 5 da confira".
RE_CONFIRA = re.compile(r"(?i)\b(art(?:igo|\.)?\s+\S+\s+(?:d[aoe]|n[aoe]|pela|pelo)\s+)confira\b")


def corrigir_siglas(texto: str) -> str:
    """O texto transcrito com as siglas no lugar: letras soltas juntas ("o a b" -> "OAB") e "da confira" -> "da CF"."""
    return RE_CONFIRA.sub(lambda m: m.group(1) + "CF", juntar_siglas(texto))


def _tokens(texto: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", sem_acento(texto)) if t]


def palavras_do_significado(sigla: str) -> list[str]:
    return [t for t in _tokens(todas().get(sigla.upper(), "")) if t not in STOP]


def interpretacoes(palavras: list[str]) -> list[list[str]]:
    """Jeitos equivalentes de escrever a mesma busca (palavras sem acento e em minúsculas): a original; com cada sigla
    trocada pelo significado ("oab" -> "ordem advogados brasil"); e com o significado trocado pela sigla ("ordem advogados"
    -> "oab"). Quem busca pontua cada jeito e fica com o melhor."""
    base = list(palavras)
    significados = {sigla.lower(): palavras_do_significado(sigla) for sigla in todas()}
    resultado = [base]
    for curto in (False, True):  # o significado inteiro e, para os de 3 palavras ou mais, sem a última ("ordem advogados")
        trocada = []
        for p in base:
            significado = significados.get(p) or [p]
            trocada += significado[:-1] if curto and len(significado) >= 3 else significado
        if trocada != base:
            resultado.append(trocada)
    for sigla, significado in significados.items():
        if sigla in base or len(significado) < 2:
            continue
        # a frase inteira ("ordem advogados brasil") ou sem a última palavra ("ordem dos advogados"), mas não sem uma do meio
        for dito in ([significado, significado[:-1]] if len(significado) >= 3 else [significado]):
            if set(dito) <= set(base):
                resultado.append([p for p in base if p not in dito] + [sigla])
                break
    vistos, unicas = set(), []
    for interpretacao in resultado:
        chave = tuple(interpretacao)
        if interpretacao and chave not in vistos:
            vistos.add(chave)
            unicas.append(interpretacao)
    return unicas


def eh_sigla(palavra: str) -> bool:
    return palavra.upper() in todas()
