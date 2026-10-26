"""Agente do controle remoto (TV e ar-condicionado pelo Ekaza Mini 3, veja controle_remoto.py).

Exemplos: "liga a TV", "desliga a TV", "aumenta o volume", "abaixa o volume em 5", "põe no mudo", "põe no canal 13",
"próximo canal", "aperta o OK", "liga o ar em 22 graus", "põe o ar no modo frio", "ventilação alta", "abaixa a temperatura",
"desliga o ar-condicionado", "desliga a TV e o ar", "o ar está ligado?", "aprende o botão ligar da TV", "aprende o ar em
22 graus", "esquece o botão netflix da TV", "quais botões você sabe da TV?", "o controle remoto está funcionando?",
"atualiza os controles da nuvem".

Por regras, sem LLM, como a impressora. `AgenteControle.responder(texto)` devolve a resposta ou None (o roteamento segue).
Ligar e desligar é doméstico e reversível: ela faz direto, sem perguntar. A exceção é a TV com um botão só para ligar e
desligar: se ela acha que a TV já está como pedido, pergunta antes (apertar desligaria). O aprender roda em segundo plano e
avisa no fim (`avisar`).
"""
import logging
import re
import threading
import time
from dataclasses import dataclass

from agenda_linguagem import preparar
from controle_remoto import MODOS_DO_AR, VENTILACOES, ErroControle, RegistroDoControle, codigo_sony, tecla_da_nuvem

logger = logging.getLogger(__name__)

SEGUNDOS_PARA_APRENDER = 15
SEGUNDOS_PARA_RESPONDER = 90.0  # validade das perguntas ("aperto mesmo assim?", "diga pronto")
VALIDADE_ESTADO_TV = 3 * 3600.0  # depois disso, o "a TV está ligada" lembrado não vale para perguntar
VALIDADE_CONTROLES_NUVEM = 7 * 24 * 3600.0
MAXIMO_TOQUES = 20

ESTADO_PADRAO_AR = {"ligado": False, "temperatura": 23, "modo": "frio", "ventilacao": "auto"}
NOMES_MODOS = {"frio": "no frio", "quente": "no quente", "auto": "no automático", "ventilar": "só ventilando",
               "seco": "desumidificando"}
NOMES_VENTILACAO = {"auto": "automática", "baixa": "baixa", "media": "média", "alta": "alta"}
MODOS_SEM_TEMPERATURA = {"ventilar", "seco"}
NOMES_APARELHOS = {"tv": "a TV", "ar": "o ar"}
NOMES_BOTOES = {
    "energia": "de ligar e desligar", "volume_mais": "de aumentar o volume", "volume_menos": "de abaixar o volume",
    "mudo": "do mudo", "canal_mais": "de passar o canal", "canal_menos": "de voltar o canal", "ok": "OK",
    "voltar": "de voltar", "inicio": "da tela inicial", "menu": "do menu", "entrada": "da entrada (fonte)",
    "cima": "da seta para cima", "baixo": "da seta para baixo", "esquerda": "da seta para a esquerda",
    "direita": "da seta para a direita", "play": "do play e pausa",
    **{f"digito_{n}": f"número {n}" for n in range(10)},
}

MENSAGEM_SEM_CONFIGURACAO = (
    "Ainda não estou conectada ao controle remoto (o Ekaza). Falta pôr o id e a chave local dele no .env "
    "(CONTROLE_ID e CONTROLE_CHAVE_LOCAL) ou rodar o wizard do tinytuya em banco_memoria\\tuya. Depois é só pedir de novo."
)

# ---------------------------------------------------------------- vocabulário
RE_CHAMADA = re.compile(r"^(?:beatriz|beatris|biatriz)\s*[,!.]?\s*")
RE_POLIDEZ = re.compile(
    r"^(?:por favor|pfv|me|pra mim|voce pode|voce poderia|vc pode|pode|poderia|consegue|quero que voce|"
    r"preciso que voce|eu quero|quero|da pra|tem como)\s+"
)
_UNIDADES = {"zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
             "sete": 7, "oito": 8, "nove": 9}
_DEZENAS = {"dez": 10, "onze": 11, "doze": 12, "treze": 13, "quatorze": 14, "catorze": 14, "quinze": 15,
            "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30,
            "quarenta": 40, "cinquenta": 50}
NUM = (r"(?:\d{1,3}|(?:vinte|trinta|quarenta|cinquenta)(?: e (?:um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove))?|"
       r"dez|onze|doze|treze|quatorze|catorze|quinze|dezesseis|dezessete|dezoito|dezenove|"
       r"zero|um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove)")


def numero(palavra: str) -> int | None:
    """"22", "vinte e dois", "dezoito" -> número; None se não for um número."""
    palavra = (palavra or "").strip()
    if palavra.isdigit():
        return int(palavra)
    partes = palavra.split(" e ")
    total = 0
    for parte in partes:
        valor = _DEZENAS.get(parte, _UNIDADES.get(parte))
        if valor is None:
            return None
        total += valor
    return total if partes else None


RE_TV = re.compile(r"\b(?:tv|tvs|televis(?:ao|or|oes)|(?:a|na|da) teve)\b")
RE_AR = re.compile(r"\bar[- ]?condicionados?\b|\bclimatizador\b|"
                   r"(?<!qualidade )\b(?:o|do|no|ao|pro|pelo) ar\b(?! livre| puro| fresco| la fora| da rua| seco)")
RE_LIGAR = re.compile(r"\b(?:lig(?:a|ue|ar|uem)|acend(?:e|a|er))\b")
RE_DESLIGAR = re.compile(r"\b(?:deslig(?:a|ue|ar|uem)|apag(?:a|ue|ar))\b")
RE_TUDO = re.compile(r"\bdeslig(?:a|ue|ar) tudo\b")
RE_DEPOIS = re.compile(r"\b(?:daqui a|daqui|dentro de|mais tarde|amanha|de noite|a noite|de madrugada|quando eu)\b|"
                       rf"\b(?:em|por) {NUM} (?:min\w*|horas?|segundos?)\b|\bas \d{{1,2}}(?::\d\d| ?h)\b|\bmeia hora\b")
RE_BOTAO = re.compile(r"\b(?:botao|botoes|tecla|teclas|codigo|codigos)\b")

# No começo da frase, para "quero aprender a mexer na TV" (a pessoa é que quer aprender) não virar o modo de aprender.
RE_APRENDER = re.compile(r"^(?:agora |entao |tambem |e )?(?:aprend[ae]|aprender(?! a )|grav[ae]|gravar|memoriz[ae]|memorizar)\b")
RE_ESQUECER = re.compile(r"^(?:agora |entao |e )?(?:esquec[ae]|esquecer|apag[ae]|apagar|remov[ae]|remover|delet[ae]|deletar)\b")
RE_LISTAR = re.compile(r"\b(?:quais|que)\s+(?:sao os\s+)?(?:botoes|codigos|comandos)\b|"
                       r"\bbotoes (?:que )?(?:voce )?(?:ja )?(?:sabe|aprendeu|conhece|tem)\b|"
                       r"\bo que (?:voce )?(?:ja )?(?:sabe|consegue) fazer (?:na|com a|no|com o) (?:tv|televisao|ar)\b")
RE_CONFERIR = re.compile(
    r"\b(?:testa|teste|testar|confer\w*|verific\w*)\s+(?:o |a )?(?:controle|conexao do controle|ekaza)\b|"
    r"\bcontrole(?: remoto)?(?: universal)? (?:esta|ta) (?:conectado|funcionando|online|respondendo|ligado)\b|"
    r"\bekaza\b.{0,25}\b(?:conectad\w*|funcion\w*|respond\w*)\b"
)
RE_ATUALIZAR_NUVEM = re.compile(
    r"\b(?:atualiz\w*|sincroniz\w*|busc\w*|carreg\w*|import\w*|procur\w*)\b.{0,20}\bcontroles?\b.{0,25}"
    r"\b(?:nuvem|app|aplicativo|smart ?life|tuya)\b|\bcontroles (?:virtuais|do app|da nuvem|do smart ?life)\b"
)
RE_PERGUNTA_ESTADO = re.compile(
    r"\b(?:esta|ta|ficou|continua|deixou|deixei)\s+(?:ligad[oa]|desligad[oa])\b|\b(?:esta|ta) (?:em|no|na) que\b|"
    r"\bcomo (?:esta|ta|ficou) (?:o ar|a tv|a televisao)\b|\b(?:qual|que|quantos?) (?:e a |a )?(?:temperatura|graus?)\b|"
    r"\bem (?:que|qual) (?:temperatura|modo)\b|\bqual (?:e o |o )?modo\b|\bem quantos? graus\b|\b(?:ligad[oa]|desligad[oa])\?"
)
RE_ACAO_NO_COMECO = re.compile(r"(?:lig|deslig|apag|poe|ponha|coloca|bota|deixa|muda|aument|abaix|baix|sob|diminu)")
RE_SIM = re.compile(r"^(?:sim|pode|pode sim|aperta|aperte|manda|confirma|ok|okay|isso|claro|vai|beleza|com certeza|"
                    r"faz|faca|positivo|aham|uhum|bora|pronto|ja|foi|feito|prontinho|to pronto|estou pronto)\b")
RE_NAO = re.compile(r"^(?:nao|cancela|deixa|esquece|para|pare|negativo|agora nao|melhor nao)\b")

# Ar-condicionado
RE_VENTILACAO = re.compile(
    r"\b(?:ventilacao|ventilador|ventoinha|vento|velocidade(?: do vento| da ventilacao)?)(?: do ar(?:[- ]?condicionado)?)?"
    r"\s+(?:no |na |em |pra |para |a |o )?"
    r"(?:modo )?(alta|alto|maxima|maximo|forte|media|medio|baixa|baixo|minima|minimo|fraca|fraco|automatica|automatico|auto)\b"
)
VENTILACAO_DITA = {"alta": "alta", "alto": "alta", "maxima": "alta", "maximo": "alta", "forte": "alta", "media": "media",
                   "medio": "media", "baixa": "baixa", "baixo": "baixa", "minima": "baixa", "minimo": "baixa",
                   "fraca": "baixa", "fraco": "baixa", "automatica": "auto", "automatico": "auto", "auto": "auto"}
_VERBOS_SUBIR = r"(?:aument\w*|sob[ea]\w*|sub[ai]\w*|levant\w*|esquent\w*)"
_VERBOS_DESCER = r"(?:abaix\w*|baix[ae]\w*|diminu\w*|reduz\w*|desc[ea]\w*|esfri\w*)"
RE_GRAUS_RELATIVOS = re.compile(
    rf"\b({_VERBOS_SUBIR}|{_VERBOS_DESCER})\s+(?:a temperatura (?:do ar )?(?:em )?|mais |uns |o ar em )?({NUM})\s*graus?\b"
)
RE_TEMP_SUBIR = re.compile(rf"\b{_VERBOS_SUBIR}\s+(?:um pouco |mais )?(?:a )?temperatura\b|\bmais quente\b|"
                           r"\btemperatura (?:pra|para) cima\b")
RE_TEMP_DESCER = re.compile(rf"\b{_VERBOS_DESCER}\s+(?:um pouco |mais )?(?:a )?temperatura\b|\bmais (?:frio|gelado|fresco)\b|"
                            r"\btemperatura (?:pra|para) baixo\b")
_NAO_E_TEMPO = r"(?!\s*(?:min\w*|horas?|h\b|segundos?|vezes))"
RE_TEMP_ABSOLUTA = re.compile(
    rf"\b({NUM})\s*(?:graus?|°c?|ºc?)\b|\btemperatura\s+(?:do ar\s+)?(?:em|pra|para|no|na|a|de)\s+({NUM})\b{_NAO_E_TEMPO}|"
    rf"\bar(?:[- ]?condicionado)?\s+(?:em|pra|para|no|na|a)\s+({NUM})\b{_NAO_E_TEMPO}"
)
RE_VERBO_AJUSTE = re.compile(r"\b(?:poe|ponha|por|coloca|coloque|bota|bote|deixa|deixe|muda|mude|ajusta|ajuste|regula|"
                             r"regule|troca|troque|lig\w*|configura\w*)\b")
# O modo precisa de "modo", "no", "pra"... antes: "põe o ar no frio" muda o modo, "o ar está frio demais" não.
_ANTES_DO_MODO = r"\b(?:modo|no|pro|pra|para|para o|pra o|em|de)\s+(?:modo\s+)?"
MODOS_DITOS = (
    (re.compile(_ANTES_DO_MODO + r"(?:frio|gelar|resfri\w*|refriger\w*|cool)\b"), "frio"),
    (re.compile(_ANTES_DO_MODO + r"(?:quente|aquec\w*|calor|heat)\b"), "quente"),
    (re.compile(_ANTES_DO_MODO + r"(?:automatico|auto)\b"), "auto"),
    (re.compile(r"\bmodo (?:ventila\w*|vento|ventilador|fan)\b|\bso (?:ventila\w*|vento)\b|\b(?:so )?pra ventilar\b"), "ventilar"),
    (re.compile(_ANTES_DO_MODO + r"(?:seco|desumidific\w*|dry)\b|\btirar a umidade\b"), "seco"),
)

# TV
RE_VOLUME_MAIS = re.compile(rf"\b(?:{_VERBOS_SUBIR}|mais)\b[^.]{{0,20}}\b(?:volume|som)\b|"
                            r"\bvolume\s+(?:mais alto|pra cima|para cima|\+|mais)\b")
RE_VOLUME_MENOS = re.compile(rf"\b(?:{_VERBOS_DESCER}|menos)\b[^.]{{0,20}}\b(?:volume|som)\b|"
                             r"\bvolume\s+(?:mais baixo|pra baixo|para baixo|-|menos)\b")
RE_VOLUME_DE_OUTRO = re.compile(r"\b(?:volume|som) d[aoe]s? (?:sua |minha |tua )?(?:voz|fala|computador|pc|notebook|spotify|"
                                r"musica|celular|beatriz|youtube|video|fone|caixa)\b")
RE_VOLUME_ABSOLUTO = re.compile(rf"\b(?:volume|som)\s+(?:no|na|pra|para|ate o|ate)\s+({NUM})\b|"
                                rf"\b(?:poe|coloca|bota|deixa)\s+(?:o )?volume\s+(?:no|em|pra|para)\s+({NUM})\b")
RE_QUANTIDADE = re.compile(
    rf"\b({NUM})\s*(?:vez|vezes|pontos?|niveis?|numeros?|tracinhos?|unidades?|barrinhas?|toques?)\b|"
    rf"\b(?:volume|som)\s+(?:em|mais)\s+({NUM})\b|"
    rf"\b(?:{_VERBOS_SUBIR}|{_VERBOS_DESCER})\s+(?:mais |uns |o volume em |o som em )?({NUM})\b"
)
RE_POUCO = re.compile(r"\bum (?:pouco|pouquinho|tiquinho|tico)\b")
RE_MUITO = re.compile(r"\b(?:bastante|muito|bem mais|bem alto|bem baixo|um monte)\b")
RE_MUDO = re.compile(r"\b(?:mut[ae]r?|mutad[ao]|no mudo|do mudo|desmut\w*)\b")
RE_MUDO_TV = re.compile(r"\b(?:tir\w* o som|volt\w* o som|silenci\w*|sem som)\b")
RE_CANAL_NUMERO = re.compile(rf"\b(?:no|pro|para o|pra o|o|de) canal\s+(\d{{1,4}}|{NUM})\b|"
                             rf"\b(?:poe|coloca|bota|muda|vai|troca|sintoniza|passa|liga)\w*\s+(?:\w+\s+)?canal\s+(\d{{1,4}}|{NUM})\b")
RE_CANAL_MAIS = re.compile(r"\b(?:proximo|sobe o|muda (?:de|o)|troca (?:de|o)|passa (?:o|de)|avanca o)\s+canal\b|"
                           r"\bcanal\s+(?:\+|mais|pra cima|para cima|seguinte|da frente)\b")
RE_CANAL_MENOS = re.compile(r"\bcanal\s+(?:anterior|-|menos|pra baixo|para baixo|de antes)\b|"
                            r"\b(?:volta|desce|voltar)\s+(?:o |um |pro |para o )?canal\b")
RE_CANAL_DE_OUTRO = re.compile(r"\b(?:youtube|whatsapp|telegram|twitch|discord|instagram)\b")
RE_APERTAR = re.compile(r"\b(?:aperta|aperte|apertar|pressiona|pressione|clica|clique|da um clique)\s+(?:o |no |em |a |na )?(.+)$")
RE_ABRIR_NA_TV = re.compile(r"\b(?:abre|abra|poe|coloca|bota|entra)\s+(?:a |o |no |na )?(.+?)\s+(?:na|da) (?:tv|televisao)\b")

PALAVRAS_SOLTAS = re.compile(
    r"\b(?:o|a|os|as|de|do|da|dos|das|no|na|pro|pra|para|em|botao|botoes|tecla|teclas|codigo|sinal|comando|"
    r"controle|remoto|tv|televisao|teve|ar|condicionado|mim|agora|favor|por|ai|la|aqui|e|me|voce|vc|pfv)\b|"
    r"\b(?:um|uma)\b(?=\s+(?:botao|tecla|codigo|sinal|comando)\b)"  # "um botão" é artigo; "o botão um" é o número
)
SINONIMOS_BOTAO = (
    ("volume_mais", r"^(?:(?:aument\w*|sob[ei]\w*|sub[ai]\w*|levant\w*|mais) volume|volume (?:\+|mais|cima|alto|mais alto))$"),
    ("volume_menos", r"^(?:(?:abaix\w*|baix\w*|diminu\w*|desc[ei]\w*|menos) volume|volume (?:-|menos|baixo|mais baixo))$"),
    ("canal_mais", r"^(?:canal (?:\+|mais|cima|seguinte)|proximo canal|(?:sob[ei]\w*|pass\w*) canal)$"),
    ("canal_menos", r"^(?:canal (?:-|menos|baixo|anterior)|(?:desc[ei]\w*|volt\w*) canal)$"),
    ("energia", r"^(?:ligar|desligar|liga|desliga|power|energia|liga e desliga|ligar e desligar|liga desliga|"
                r"ligar desligar|on off|ligar ou desligar)$"),
    ("mudo", r"^(?:mudo|mute|mutar|silencio|tirar som)$"),
    ("ok", r"^(?:ok|okay|confirmar|enter|selecionar)$"),
    ("voltar", r"^(?:voltar|volta|back|retornar)$"),
    ("inicio", r"^(?:inicio|home|casa|tela inicial|casinha)$"),
    ("menu", r"^(?:menu|configurac\w*)$"),
    ("entrada", r"^(?:entrada|fonte|source|input)$"),
    ("cima", r"^(?:cima|seta cima|seta pra cima)$"),
    ("baixo", r"^(?:baixo|seta baixo|seta pra baixo)$"),
    ("esquerda", r"^(?:esquerda|seta esquerda|seta pra esquerda)$"),
    ("direita", r"^(?:direita|seta direita|seta pra direita)$"),
    ("play", r"^(?:play|pausa|pause|play pausa|tocar|pausar|play e pausa)$"),
)


@dataclass
class Pedido:
    acao: str  # ligar | desligar | botao | canal | ar | estado | aprender | esquecer | listar | conferir | nuvem
    aparelhos: tuple = ("tv",)
    botao: str = ""  # botão da TV ("volume_mais", "netflix") ou código do ar ("frio_22", "desligar")
    vezes: int = 1
    canal: str = ""
    temperatura: int | None = None
    delta: int = 0
    modo: str | None = None
    ventilacao: str | None = None
    problema: str = ""  # explicação pronta (o volume não tem número, temperatura fora da faixa...)


@dataclass
class Pendencia:
    tipo: str  # energia_tv | aprender
    ate: float
    ligar: bool = False
    pedido: Pedido | None = None


class FaltaCodigo(Exception):
    """O botão pedido não foi aprendido nem existe na nuvem: a mensagem diz como ensinar."""


class Pergunta(Exception):
    """A resposta é uma pergunta (já guardada na pendência)."""


def _texto_preparado(texto: str) -> str:
    """Sem acentos, em minúsculas, sem o nome e sem "por favor", "pode"... no começo."""
    _, t = preparar(texto.strip())
    t = re.sub(r"[!?.,;]+", lambda m: "?" if "?" in m.group(0) else " ", t)
    for padrao in (RE_CHAMADA, RE_POLIDEZ, RE_POLIDEZ):
        achado = padrao.match(t.strip())
        if achado:
            t = t.strip()[achado.end():]
    return re.sub(r"\s+", " ", t).strip()


def _apagar(texto: str, achado) -> str:
    """O texto com o trecho achado trocado por espaços (para os outros padrões não pegarem o mesmo número)."""
    return texto[:achado.start()] + " " * (achado.end() - achado.start()) + texto[achado.end():]


def _limpo(texto: str) -> str:
    return re.sub(r"\s+", " ", PALAVRAS_SOLTAS.sub(" ", texto.replace("?", " "))).strip()


def nome_do_botao(texto: str) -> str:
    """O botão da TV que o texto diz: um dos nomes de NOMES_BOTOES, um nome livre ("netflix", "hdmi 1") ou ""."""
    limpo = _limpo(texto)
    if not limpo:
        return ""
    for chave, padrao in SINONIMOS_BOTAO:
        if re.search(padrao, limpo):
            return chave
    algarismo = re.fullmatch(rf"(?:numero |n )?({NUM})", limpo)
    if algarismo and (valor := numero(algarismo.group(1))) is not None and 0 <= valor <= 9:
        return f"digito_{valor}"
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 +-]", "", limpo))[:30].strip()


def descrever_botao(botao: str) -> str:
    """"o botão de aumentar o volume", "o botão netflix"."""
    nome = NOMES_BOTOES.get(botao)
    if botao.startswith("digito_"):
        return f"o {nome}"
    return f"o botão {nome}" if nome else f"o botão {botao}"


def descrever_codigo_do_ar(codigo: str) -> str:
    """"frio_22_alta" -> "22 graus no frio, ventilação alta"."""
    if codigo in {"ligar", "desligar"}:
        return f"o botão de {codigo}"
    partes = codigo.split("_")
    texto = f"{partes[1]} graus {NOMES_MODOS.get(partes[0], partes[0])}"
    if len(partes) > 2:
        texto += f", ventilação {NOMES_VENTILACAO.get(partes[2], partes[2])}"
    return texto


def descrever_ar(estado: dict, com_ventilacao: bool = False) -> str:
    """"em 22 graus, no frio, ventilação alta"; "só ventilando" (sem temperatura nos modos que não têm)."""
    if estado["modo"] in MODOS_SEM_TEMPERATURA:
        texto = NOMES_MODOS[estado["modo"]]
    else:
        texto = f"em {estado['temperatura']} graus, {NOMES_MODOS[estado['modo']]}"
    if com_ventilacao:
        texto += f", ventilação {NOMES_VENTILACAO[estado['ventilacao']]}"
    return texto


def _juntar(nomes: list[str]) -> str:
    return nomes[0] if len(nomes) == 1 else ", ".join(nomes[:-1]) + " e " + nomes[-1]


# ---------------------------------------------------------------- interpretação
def interpretar(t: str, conhecidos=frozenset(), minima: int = 16, maxima: int = 30, passos: int = 3) -> Pedido | None:
    """O pedido de controle remoto num texto já preparado (sem acentos), ou None se não for com ele."""
    if not t:
        return None
    tv, ar = bool(RE_TV.search(t)), bool(RE_AR.search(t))
    if RE_APRENDER.search(t) and (tv or ar or RE_BOTAO.search(t)):
        return _pedido_de_aprender(t, tv, ar, minima, maxima)
    if RE_ESQUECER.search(t) and RE_BOTAO.search(t) and (tv or ar):
        aprender = _pedido_de_aprender(RE_ESQUECER.sub("aprende", t, count=1), tv, ar, minima, maxima)
        aprender.acao = "esquecer"
        return aprender
    if RE_LISTAR.search(t) and (tv or ar or "controle" in t):
        return Pedido("listar", tuple(a for a, sim in (("tv", tv), ("ar", ar)) if sim) or ("tv", "ar"))
    if RE_ATUALIZAR_NUVEM.search(t):
        return Pedido("nuvem", ())
    if RE_CONFERIR.search(t):
        return Pedido("conferir", ())
    if (tv or ar) and RE_PERGUNTA_ESTADO.search(t) and not RE_ACAO_NO_COMECO.match(t):
        return Pedido("estado", ("ar",) if ar and not tv else ("tv",) if tv and not ar else ("tv", "ar"))
    pedido = _pedido_do_ar(t, tv, ar, minima, maxima)
    if pedido is None:
        pedido = _pedido_da_tv(t, tv, conhecidos, passos)
    if pedido is not None and RE_DEPOIS.search(t) and pedido.acao in {"ligar", "desligar", "ar"}:
        pedido.problema = ("Ainda não sei deixar o controle remoto para depois: por enquanto eu só ligo e desligo na hora. "
                           "Peça de novo quando for a hora.")
    return pedido


def _pedido_de_aprender(t: str, tv: bool, ar: bool, minima: int, maxima: int) -> Pedido:
    verbo = RE_APRENDER.search(t)
    resto = t[verbo.end():] if verbo else t
    if ar and not tv:
        if RE_DESLIGAR.search(resto):
            return Pedido("aprender", ("ar",), botao="desligar")
        graus = RE_TEMP_ABSOLUTA.search(resto)
        temperatura = numero(next((g for g in graus.groups() if g), "")) if graus else None
        if temperatura is not None:
            if not minima <= temperatura <= maxima:
                return Pedido("aprender", ("ar",), problema=f"O ar vai de {minima} a {maxima} graus.")
            ventilacao = RE_VENTILACAO.search(resto)
            modo = next((m for padrao, m in MODOS_DITOS if padrao.search(_apagar(resto, graus))), None)
            return Pedido("aprender", ("ar",), temperatura=temperatura, modo=modo,
                          ventilacao=VENTILACAO_DITA[ventilacao.group(1)] if ventilacao else None)
        if RE_LIGAR.search(resto):
            return Pedido("aprender", ("ar",), botao="ligar")
        return Pedido("aprender", ("ar",))
    return Pedido("aprender", ("tv",), botao=nome_do_botao(resto))


def _pedido_do_ar(t: str, tv: bool, ar: bool, minima: int, maxima: int) -> Pedido | None:
    texto, problema = t, ""
    ventilacao = RE_VENTILACAO.search(texto)
    if ventilacao:
        texto = _apagar(texto, ventilacao)
    delta = 0
    if (relativo := RE_GRAUS_RELATIVOS.search(texto)):
        quanto = numero(relativo.group(2)) or 1
        delta = quanto if re.match(_VERBOS_SUBIR, relativo.group(1)) else -quanto
        texto = _apagar(texto, relativo)
    elif (subir := RE_TEMP_SUBIR.search(texto)):
        delta, texto = 1, _apagar(texto, subir)
    elif (descer := RE_TEMP_DESCER.search(texto)):
        delta, texto = -1, _apagar(texto, descer)
    temperatura = None
    if (graus := RE_TEMP_ABSOLUTA.search(texto)):
        temperatura = numero(next((g for g in graus.groups() if g), ""))
        texto = _apagar(texto, graus)
        if temperatura is not None and not minima <= temperatura <= maxima:
            problema = f"O ar vai de {minima} a {maxima} graus."
    modo = None
    for padrao, nome in MODOS_DITOS:
        achado = padrao.search(texto)
        if achado and (ar or achado.group(0).startswith("modo") or nome in {"ventilar", "seco"}):
            modo = nome
            break
    mudanca = bool(ventilacao or delta or temperatura is not None or modo)
    if not ar:
        # Sem "o ar" na frase, só o que não pode ser de outra coisa: ventilação, "abaixa a temperatura", "põe em 22 graus".
        implicito = ventilacao or delta or (temperatura is not None and RE_VERBO_AJUSTE.search(t)) or \
            (modo and re.search(r"\bmodo (?:frio|quente|ventila\w*|seco|desumidific\w*)\b", t))
        if tv or not implicito:
            return None
    desligar, ligar = RE_DESLIGAR.search(t), RE_LIGAR.search(t)
    if desligar and not mudanca:
        return Pedido("desligar", ("tv", "ar") if tv else ("ar",))
    if not (ligar or mudanca):
        return None
    if ligar and not mudanca:
        return Pedido("ligar", ("tv", "ar") if tv else ("ar",))
    return Pedido("ar", ("ar",), temperatura=temperatura, delta=delta, modo=modo,
                  ventilacao=VENTILACAO_DITA[ventilacao.group(1)] if ventilacao else None, problema=problema)


def _pedido_da_tv(t: str, tv: bool, conhecidos, passos: int) -> Pedido | None:
    if RE_TUDO.search(t):
        return Pedido("desligar", ("tv", "ar"))
    canal = None
    if not RE_CANAL_DE_OUTRO.search(t) and (achado := RE_CANAL_NUMERO.search(t)):
        dito = next(g for g in achado.groups() if g)
        valor = numero(dito)
        canal = str(valor) if valor is not None else None
    if tv and RE_DESLIGAR.search(t):
        return Pedido("desligar", ("tv",))
    if tv and RE_LIGAR.search(t):
        return Pedido("ligar", ("tv",), canal=canal or "")
    if canal:
        return Pedido("canal", ("tv",), canal=canal)
    if not RE_CANAL_DE_OUTRO.search(t):
        if RE_CANAL_MENOS.search(t):
            return Pedido("botao", ("tv",), botao="canal_menos")
        if RE_CANAL_MAIS.search(t):
            return Pedido("botao", ("tv",), botao="canal_mais")
    if not RE_VOLUME_DE_OUTRO.search(t):
        if (absoluto := RE_VOLUME_ABSOLUTO.search(t)):
            quanto = numero(next(g for g in absoluto.groups() if g))
            return Pedido("botao", ("tv",), problema=(
                f"O controle não sabe em quanto o volume está agora, então não consigo pôr no {quanto}. "
                f"Diga, por exemplo, \"aumenta o volume em 5\", que eu aperto 5 vezes."))
        mais, menos = RE_VOLUME_MAIS.search(t), RE_VOLUME_MENOS.search(t)
        if mais or menos:
            return Pedido("botao", ("tv",), botao="volume_mais" if mais and not menos else "volume_menos",
                          vezes=_quantos_toques(t, passos))
        if RE_MUDO.search(t) or (tv and RE_MUDO_TV.search(t)):
            return Pedido("botao", ("tv",), botao="mudo")
    apertar = RE_APERTAR.search(t)
    if apertar:
        # Sem "TV" na frase, só um botão já aprendido: "clica em OK" pode ser na tela do PC.
        botao = nome_do_botao(apertar.group(1))
        if botao and (tv or botao in conhecidos):
            return Pedido("botao", ("tv",), botao=botao)
    if tv and (abrir := RE_ABRIR_NA_TV.search(t)):
        botao = nome_do_botao(abrir.group(1))
        if botao in conhecidos:
            return Pedido("botao", ("tv",), botao=botao)
    return None


def _quantos_toques(t: str, passos: int) -> int:
    if RE_POUCO.search(t):
        return 2
    achado = RE_QUANTIDADE.search(t)
    if achado:
        valor = numero(next(g for g in achado.groups() if g))
        if valor:
            return max(1, min(MAXIMO_TOQUES, valor))
    if RE_MUITO.search(t):
        return min(MAXIMO_TOQUES, passos * 2)
    return passos


# ---------------------------------------------------------------- agente
class AgenteControle:
    def __init__(self, registro: RegistroDoControle, local=None, nuvem=None, modo_nuvem: str = "auto", avisar=None,
                 executar=None, relogio=time.time, ligado: bool = True, minima: int = 16, maxima: int = 30,
                 passos_volume: int = 3, marca_tv: str = ""):
        self.registro = registro
        self.local = local  # controle_remoto.ControleLocalTuya (ou um falso nos testes)
        self.nuvem = nuvem  # controle_remoto.ControleNuvemTuya
        self.modo_nuvem = modo_nuvem if modo_nuvem in {"auto", "local", "nuvem"} else "auto"
        if self.modo_nuvem == "local":
            self.nuvem = None
        self.avisar = avisar or (lambda texto: None)  # fim do aprender (em segundo plano)
        self.executar = executar or (lambda funcao: threading.Thread(target=funcao, daemon=True,
                                                                      name="aprender-botao").start())
        self.relogio = relogio
        self.ligado = ligado
        self.minima, self.maxima, self.passos_volume = minima, maxima, passos_volume
        self.marca_tv = (marca_tv or "").strip().lower()  # "sony": os códigos da TV são montados aqui (codigo_sony)
        self.pendencia: Pendencia | None = None
        self.aprendendo = ""  # o que está esperando do controle original ("o botão de ligar e desligar da TV")
        self._trava = threading.RLock()

    @property
    def configurado(self) -> bool:
        return self.local is not None or self.nuvem is not None

    def esperando_resposta(self) -> bool:
        """Se ela acabou de perguntar ("aperto mesmo assim?", "quando estiver pronto, diga pronto") e a resposta ainda vale."""
        return self.pendencia is not None and self.pendencia.ate > self.relogio()

    def reconhece(self, texto: str) -> bool:
        """Se o texto é com o controle remoto, sem mandar nada (só as regras): o painel clássico manda numa thread."""
        if not self.ligado or not (texto or "").strip():
            return False
        t = _texto_preparado(texto)
        if self.esperando_resposta() and (RE_SIM.match(t) or RE_NAO.match(t)) and len(t.split()) <= 6:
            return True
        return interpretar(t, frozenset(self.registro.botoes("tv")), self.minima, self.maxima,
                           self.passos_volume) is not None

    # ================================================================== entrada
    def responder(self, texto: str) -> str | None:
        if not self.ligado or not (texto or "").strip():
            return None
        with self._trava:
            try:
                return self._responder(texto)
            except (FaltaCodigo, Pergunta) as exc:
                return str(exc)
            except ErroControle as exc:
                self.pendencia = None
                return f"Não consegui: {exc}."
            except ModuleNotFoundError as exc:
                logger.warning("Controle remoto sem o módulo %s", exc.name)
                return ("Para usar o controle remoto falta instalar o tinytuya: "
                        ".venv312\\Scripts\\python.exe -m pip install tinytuya")
            except Exception as exc:
                self.pendencia = None
                logger.exception("Falha no agente do controle remoto")
                return f"Tive um problema com o controle remoto: {exc}"

    def _responder(self, texto: str) -> str | None:
        t = _texto_preparado(texto)
        if not t:
            return None
        if self.pendencia and self.pendencia.ate > self.relogio():
            resposta = self._continuar(t)
            if resposta is not None:
                return resposta
        self.pendencia = None
        pedido = interpretar(t, frozenset(self.registro.botoes("tv")), self.minima, self.maxima, self.passos_volume)
        if pedido is None:
            return None
        if pedido.problema:
            return pedido.problema
        if not self.configurado:
            return MENSAGEM_SEM_CONFIGURACAO
        return getattr(self, f"_{pedido.acao}")(pedido)

    def _continuar(self, t: str) -> str | None:
        pendencia = self.pendencia
        sim, nao = RE_SIM.match(t) and not RE_NAO.match(t), RE_NAO.match(t)
        if not (sim or nao) or len(t.split()) > 6:
            return None  # outro pedido no lugar da resposta: segue o caminho normal
        self.pendencia = None
        if pendencia.tipo == "energia_tv":
            if nao:
                return "Beleza, deixei a TV como está."
            return self._tv_energia(pendencia.ligar, perguntar=False, forcar=True)
        if nao:
            return "Beleza, fica para outra hora."
        return self._comecar_a_aprender(pendencia.pedido)

    # ================================================================== caminhos
    def _ordem(self) -> tuple:
        if self.modo_nuvem == "nuvem":
            return ("nuvem", "local")
        return ("local", "nuvem")

    def _controles_da_nuvem(self, atualizar: bool = False) -> dict:
        """Os controles virtuais da TV e do ar no app (guardados por uma semana)."""
        guardado = self.registro.ler()["nuvem"]
        if (not atualizar and "controles" in guardado
                and self.relogio() - guardado.get("atualizado", 0) < VALIDADE_CONTROLES_NUVEM):
            return guardado["controles"]
        controles = {}
        for controle in self.nuvem.controles():
            categoria = int(controle.get("category_id") or 0)
            nome = str(controle.get("remote_name") or "")
            if categoria == 5 or re.search(r"\bar\b|condicionado|\bair\b|\ba/?c\b", nome, re.I):
                tipo = "ar"
            elif categoria == 2 or re.search(r"\btv\b|televis", nome, re.I):
                tipo = "tv"
            else:
                continue
            if tipo in controles or not controle.get("remote_id"):
                continue  # o primeiro de cada tipo
            controles[tipo] = {"remote_id": controle["remote_id"], "category_id": categoria, "nome": nome,
                               "marca": controle.get("brand_name", "")}
            if tipo == "tv":
                controles["tv"]["teclas"] = self.nuvem.teclas(controle["remote_id"])
            else:  # os modos, as temperaturas e as ventilações que esse ar aceita
                controles["ar"]["faixas"] = self.nuvem.faixas_do_ar(controle["remote_id"])
        agora = self.relogio()
        self.registro.mudar(lambda d: d["nuvem"].update(controles=controles, atualizado=agora))
        return controles

    def _marca_da_tv(self) -> str:
        """A marca da TV: a do .env (CONTROLE_TV_MARCA) ou a do controle virtual cadastrado no app."""
        if self.marca_tv:
            return self.marca_tv
        controle = self.registro.ler()["nuvem"].get("controles", {}).get("tv") or {}
        return str(controle.get("marca") or "").strip().lower()

    def _achar_botao(self, aparelho: str, botoes: list[str]):
        """O primeiro jeito de apertar algum dos botões: ("local", botão, código) ou ("nuvem", botão, (controle, tecla)).
        Pela rede de casa: o botão aprendido e, numa TV Sony, o código montado aqui (codigo_sony)."""
        sony = aparelho == "tv" and self._marca_da_tv() == "sony"
        for meio in self._ordem():
            if meio == "local" and self.local is not None:
                aprendidos = self.registro.botoes(aparelho)
                for botao in botoes:
                    if botao in aprendidos:
                        return "local", botao, aprendidos[botao]
                    if sony and (codigo := codigo_sony(botao)):
                        return "local", botao, codigo
            if meio == "nuvem" and self.nuvem is not None:
                controle = self._controles_da_nuvem().get(aparelho)
                if controle:
                    for botao in botoes:
                        tecla = tecla_da_nuvem(botao, controle.get("teclas", []))
                        if tecla:
                            return "nuvem", botao, (controle, tecla)
        return None

    def _apertar(self, achados: list, vezes: int = 1) -> None:
        """Manda os botões achados (todos locais vão numa conexão só)."""
        if all(meio == "local" for meio, _, _ in achados):
            self.local.enviar([codigo for _, _, codigo in achados for _ in range(vezes)])
            return
        for meio, _, dado in achados:
            if meio == "local":
                self.local.enviar([dado] * vezes)
            else:
                controle, tecla = dado
                self.nuvem.apertar(controle["remote_id"], controle["category_id"], tecla, vezes)

    def _falta(self, aparelho: str, botao: str) -> str:
        descricao = descrever_botao(botao) if aparelho == "tv" else descrever_codigo_do_ar(botao)
        partes = [f"Ainda não sei {descricao} {'da TV' if aparelho == 'tv' else 'do ar'}."]
        if self.local is not None:
            exemplo = (f"aprende {descricao} da TV" if aparelho == "tv"
                       else "aprende o botão desligar do ar" if botao == "desligar" else
                       f"aprende o ar em {botao.split('_')[1]} graus" if "_" in botao else "aprende o botão ligar do ar")
            partes.append(f"Para eu aprender, diga \"{exemplo}\" e aperte o botão no controle original, apontado para o Ekaza.")
        if self.nuvem is not None and not self._controles_da_nuvem().get(aparelho):
            partes.append(f"Ou cadastre {'a TV' if aparelho == 'tv' else 'o ar-condicionado'} no app Smart Life, dentro do "
                          "Ekaza, e diga \"atualiza os controles da nuvem\".")
        return " ".join(partes)

    # ================================================================== TV
    def _tv_energia(self, ligar: bool, perguntar: bool = True, forcar: bool = False) -> str:
        especifico = "ligar" if ligar else "desligar"
        achado = self._achar_botao("tv", [especifico, "energia"])
        if achado is None:
            raise FaltaCodigo(self._falta("tv", "energia"))
        estado = self.registro.estado("tv")
        recente = self.relogio() - estado.get("quando", 0) < VALIDADE_ESTADO_TV
        if achado[1] == "energia" and not forcar and recente and estado.get("ligada") is ligar:
            # Um botão só para ligar e desligar: apertar agora faria o contrário do pedido.
            if not perguntar:
                raise Pergunta(f"A TV eu deixei como estava: pelo que eu sei, ela já está {'ligada' if ligar else 'desligada'}.")
            self.pendencia = Pendencia("energia_tv", self.relogio() + SEGUNDOS_PARA_RESPONDER, ligar=ligar)
            raise Pergunta(f"Pelo que eu sei, a TV já está {'ligada' if ligar else 'desligada'}, e o botão de ligar e "
                           f"desligar dela é o mesmo: se eu apertar, ela {'desliga' if ligar else 'liga'}. Aperto mesmo assim?")
        self._apertar([achado])
        self.registro.guardar_estado("tv", ligada=ligar, quando=self.relogio(), ultimo=especifico)
        return f"Pronto, {'liguei' if ligar else 'desliguei'} a TV."

    def _botao(self, pedido: Pedido) -> str:
        achado = self._achar_botao("tv", [pedido.botao])
        if achado is None:
            raise FaltaCodigo(self._falta("tv", pedido.botao))
        self._apertar([achado], pedido.vezes)
        if pedido.botao == "energia":
            ligada = self.registro.estado("tv").get("ligada")
            self.registro.guardar_estado("tv", ligada=None if ligada is None else not ligada, quando=self.relogio(),
                                         ultimo="energia")
        vezes = pedido.vezes
        frases = {
            "volume_mais": f"Pronto, aumentei o volume da TV em {vezes}.",
            "volume_menos": f"Pronto, abaixei o volume da TV em {vezes}.",
            "mudo": "Pronto, apertei o mudo da TV.",
            "canal_mais": "Pronto, passei para o próximo canal.",
            "canal_menos": "Pronto, voltei um canal.",
        }
        return frases.get(pedido.botao, f"Pronto, apertei {descrever_botao(pedido.botao)} da TV.")

    def _canal(self, pedido: Pedido) -> str:
        achados, faltando = [], []
        for algarismo in pedido.canal:
            achado = self._achar_botao("tv", [f"digito_{algarismo}"])
            if achado is None:
                faltando.append(algarismo)
            achados.append(achado)
        if faltando:
            numeros = _juntar(sorted(set(faltando)))
            raise FaltaCodigo(f"Ainda não sei {'o número' if len(set(faltando)) == 1 else 'os números'} {numeros} do controle "
                              f"da TV. Diga \"aprende o botão {faltando[0]} da TV\" e aperte ele no controle original, "
                              "apontado para o Ekaza.")
        self._apertar(achados)
        return f"Pronto, pus no canal {pedido.canal}."

    # ================================================================== ar
    def _estado_ar(self) -> dict:
        return {**ESTADO_PADRAO_AR, **self.registro.estado("ar")}

    def _meio_do_ar(self, novo: dict, so_ligar: bool, ventilacao_pedida: bool):
        """("local", código guardado, Base64), ("nuvem", None, controle) ou None."""
        if not novo["ligado"]:
            nomes = ["desligar"]
        else:
            base = f"{novo['modo']}_{novo['temperatura']}"
            nomes = [f"{base}_{novo['ventilacao']}"] + ([] if ventilacao_pedida else [base]) + (["ligar"] if so_ligar else [])
        for meio in self._ordem():
            if meio == "local" and self.local is not None:
                aprendidos = self.registro.botoes("ar")
                for nome in nomes:
                    if nome in aprendidos:
                        return "local", nome, aprendidos[nome]
            if meio == "nuvem" and self.nuvem is not None:
                controle = self._controles_da_nuvem().get("ar")
                if controle:
                    return "nuvem", None, controle
        return None

    def _falta_ar(self, novo: dict) -> str:
        if not novo["ligado"]:
            texto = self._falta("ar", "desligar")
        else:
            texto = self._falta("ar", f"{novo['modo']}_{novo['temperatura']}")
        aprendidos = [c for c in self.registro.botoes("ar") if c not in {"ligar", "desligar"}]
        if aprendidos:
            texto += f" Do ar eu já sei: {_juntar([descrever_codigo_do_ar(c) for c in sorted(aprendidos)[:6]])}."
        return texto

    def _faixa_do_ar(self, modo: str) -> dict | None:
        """O que o ar cadastrado no app aceita nesse modo ({"temps": [18, 30] ou None, "ventos": [0, 1, 2, 3]}), lido da
        nuvem; {"inexistente": True} se ele não tem o modo; None se não se sabe (sem nuvem ou sem o ar no app)."""
        if self.nuvem is None:
            return None
        try:
            faixas = (self._controles_da_nuvem().get("ar") or {}).get("faixas")
        except ErroControle:
            return None
        if not faixas:
            return None
        return faixas.get(str(MODOS_DO_AR[modo]), {"inexistente": True})

    def _ar(self, pedido: Pedido, ligar: bool = True) -> str:
        antes = self._estado_ar()
        novo = dict(antes, ligado=ligar)
        sem_temperatura = False
        if ligar:
            if pedido.modo:
                novo["modo"] = pedido.modo
            # O ar do usuário (Daikin) vai de 18 a 30 no frio, no quente e no automático; só ventilar e desumidificar
            # não têm temperatura, e desumidificar só tem a ventilação automática.
            faixa = self._faixa_do_ar(novo["modo"]) or {}
            if faixa.get("inexistente"):
                return f"Esse ar não tem o modo {NOMES_MODOS[novo['modo']].removeprefix('no ')}."
            minima, maxima = faixa.get("temps") or (self.minima, self.maxima)
            sem_temperatura = bool(faixa) and not faixa.get("temps")
            if pedido.temperatura is not None and not sem_temperatura:
                if not minima <= pedido.temperatura <= maxima:
                    return f"Esse ar vai de {minima} a {maxima} graus."
                novo["temperatura"] = pedido.temperatura
            if pedido.delta:
                if sem_temperatura:
                    return f"{NOMES_MODOS[novo['modo']].capitalize()}, o ar não tem temperatura para mudar."
                novo["temperatura"] = max(minima, min(maxima, antes["temperatura"] + pedido.delta))
                if novo["temperatura"] == antes["temperatura"] and antes["ligado"]:
                    limite = "no mínimo" if pedido.delta < 0 else "no máximo"
                    return f"O ar já está {limite}, em {antes['temperatura']} graus."
            novo["temperatura"] = max(minima, min(maxima, novo["temperatura"]))
            if pedido.ventilacao:
                novo["ventilacao"] = pedido.ventilacao
            ventos = faixa.get("ventos")
            if ventos and VENTILACOES[novo["ventilacao"]] not in ventos:
                novo["ventilacao"] = next(nome for nome, valor in VENTILACOES.items() if valor == ventos[0])
        so_ligar = pedido.acao == "ligar"
        meio = self._meio_do_ar(novo, so_ligar, bool(pedido.ventilacao))
        if meio is None:
            raise FaltaCodigo(self._falta_ar(novo))
        tipo, nome, dado = meio
        if tipo == "local":
            self.local.enviar(dado)
        else:
            self.nuvem.ar(dado["remote_id"], novo["ligado"], novo["modo"], novo["temperatura"], novo["ventilacao"],
                          com_temperatura=not sem_temperatura)
        if nome == "ligar":  # o código do botão ligar: a temperatura é a do controle original, que eu não sei
            self.registro.guardar_estado("ar", ligado=True, quando=self.relogio(), temperatura_conhecida=False)
            return "Pronto, liguei o ar."
        guardar = {campo: novo[campo] for campo in ESTADO_PADRAO_AR}
        if tipo == "local" and nome and nome.count("_") == 1:
            guardar["ventilacao"] = antes["ventilacao"]  # o código aprendido não diz a ventilação
        self.registro.guardar_estado("ar", **guardar, quando=self.relogio(), temperatura_conhecida=True)
        if not novo["ligado"]:
            return "Pronto, desliguei o ar."
        com_ventilacao = bool(pedido.ventilacao) or (tipo == "local" and nome.count("_") == 2)
        return f"Pronto, {'deixei' if antes['ligado'] else 'liguei'} o ar {descrever_ar(novo, com_ventilacao)}."

    # ================================================================== ações
    def _ligar(self, pedido: Pedido) -> str:
        return self._energia(pedido, True)

    def _desligar(self, pedido: Pedido) -> str:
        return self._energia(pedido, False)

    def _energia(self, pedido: Pedido, ligar: bool) -> str:
        if len(pedido.aparelhos) == 1:
            if pedido.aparelhos[0] == "ar":
                return self._ar(pedido, ligar)
            resposta = self._tv_energia(ligar)
            if ligar and pedido.canal:
                resposta += f" Quando ela acabar de ligar, peça o canal {pedido.canal}."
            return resposta
        feitos, avisos = [], []
        for aparelho in pedido.aparelhos:
            try:
                if aparelho == "ar":
                    self._ar(Pedido("ligar" if ligar else "desligar", ("ar",)), ligar)
                else:
                    self._tv_energia(ligar, perguntar=False)
                feitos.append(NOMES_APARELHOS[aparelho])
            except (FaltaCodigo, Pergunta, ErroControle) as exc:
                avisos.append(str(exc) if not isinstance(exc, ErroControle) else
                              f"{NOMES_APARELHOS[aparelho].capitalize()} não deu: {exc}.")
        inicio = [f"Pronto, {'liguei' if ligar else 'desliguei'} {_juntar(feitos)}."] if feitos else []
        return " ".join(inicio + avisos)

    def _estado(self, pedido: Pedido) -> str:
        partes = []
        if "ar" in pedido.aparelhos:
            estado = self.registro.estado("ar")
            if not estado:
                partes.append("Ainda não mandei nada para o ar, então não sei como ele está.")
            elif not estado.get("ligado"):
                partes.append("Pelo último comando que mandei, o ar está desligado.")
            elif not estado.get("temperatura_conhecida", True):
                partes.append("Pelo último comando que mandei, o ar está ligado, na temperatura do controle original.")
            else:
                estado = {**ESTADO_PADRAO_AR, **estado}
                partes.append(f"Pelo último comando que mandei, o ar está ligado {descrever_ar(estado, True)}.")
        if "tv" in pedido.aparelhos:
            estado = self.registro.estado("tv")
            if estado.get("ligada") is None:
                partes.append("Da TV eu não sei: ainda não mandei ligar nem desligar.")
            else:
                quando = time.strftime("%H:%M", time.localtime(estado.get("quando", 0)))
                partes.append(f"O último que mandei foi {'ligar' if estado['ligada'] else 'desligar'} a TV, às {quando}.")
        partes.append("O infravermelho não tem volta: se alguém usou o controle original, posso estar desatualizada.")
        return " ".join(partes)

    def _listar(self, pedido: Pedido) -> str:
        partes = []
        if "tv" in pedido.aparelhos:
            botoes = sorted(self.registro.botoes("tv"))
            nomes = [NOMES_BOTOES.get(b, b).removeprefix("de ").removeprefix("do ").removeprefix("da ") for b in botoes]
            partes.append(f"Da TV eu sei {len(botoes)} {'botão' if len(botoes) == 1 else 'botões'}: {_juntar(nomes)}."
                          if botoes else "Da TV eu ainda não aprendi nenhum botão.")
        if "ar" in pedido.aparelhos:
            codigos = sorted(self.registro.botoes("ar"))
            partes.append(f"Do ar eu sei: {_juntar([descrever_codigo_do_ar(c) for c in codigos])}."
                          if codigos else "Do ar eu ainda não aprendi nenhum código.")
        if self.nuvem is not None:
            try:
                controles = self._controles_da_nuvem()
            except ErroControle:
                controles = {}
            if controles:
                nomes = [f"{NOMES_APARELHOS[a][2:]} ({c.get('marca') or c.get('nome')})" for a, c in controles.items()]
                partes.append(f"Pela nuvem da Tuya também uso {_juntar(nomes)}, com todos os botões do app.")
        return " ".join(partes)

    def _conferir(self, pedido: Pedido) -> str:
        partes = []
        if self.local is not None:
            try:
                ip = self.local.conferir()
                partes.append(f"O Ekaza respondeu na rede de casa (IP {ip}).")
            except ErroControle as exc:
                partes.append(f"O Ekaza não respondeu: {exc}.")
        else:
            partes.append("O controle pela rede de casa não está configurado (CONTROLE_ID e CONTROLE_CHAVE_LOCAL no .env).")
        if self.nuvem is not None:
            try:
                controles = self._controles_da_nuvem(atualizar=True)
                partes.append("A nuvem da Tuya respondeu" + (f": tem {_juntar([NOMES_APARELHOS[a] for a in controles])}."
                                                             if controles else ", mas sem controles da TV nem do ar."))
            except ErroControle as exc:
                partes.append(f"A nuvem da Tuya não deu: {exc}.")
        tv, ar = self.registro.botoes("tv"), self.registro.botoes("ar")
        partes.append(f"Aprendidos: {len(tv)} {'botão' if len(tv) == 1 else 'botões'} da TV e "
                      f"{len(ar)} {'código' if len(ar) == 1 else 'códigos'} do ar.")
        return " ".join(partes)

    def _nuvem(self, pedido: Pedido) -> str:
        if self.nuvem is None:
            return ("A nuvem da Tuya não está ligada: ponha TUYA_API_CHAVE e TUYA_API_SEGREDO no .env "
                    "(ou rode o wizard do tinytuya em banco_memoria\\tuya) e não deixe CONTROLE_NUVEM=local.")
        controles = self._controles_da_nuvem(atualizar=True)
        if not controles:
            return ("Na nuvem da Tuya não achei controles da TV nem do ar. Cadastre no app Smart Life, dentro do Ekaza "
                    "(Adicionar controle remoto > TV ou Ar-condicionado > a marca), e peça de novo.")
        nomes = [f"{NOMES_APARELHOS[a]} ({c.get('marca') or c.get('nome')})" for a, c in controles.items()]
        return f"Na nuvem da Tuya achei {_juntar(nomes)}. Pode pedir."

    def _esquecer(self, pedido: Pedido) -> str:
        aparelho = pedido.aparelhos[0]
        codigo = pedido.botao or (self._codigo_do_ar(pedido) if aparelho == "ar" else "")
        if not codigo:
            return "Qual botão eu esqueço? Diga, por exemplo, \"esquece o botão netflix da TV\"."
        descricao = descrever_botao(codigo) if aparelho == "tv" else descrever_codigo_do_ar(codigo)
        if self.registro.esquecer_botao(aparelho, codigo):
            return f"Pronto, esqueci {descricao} {'da TV' if aparelho == 'tv' else 'do ar'}."
        return f"Eu não tinha aprendido {descricao} {'da TV' if aparelho == 'tv' else 'do ar'}."

    # ================================================================== aprender
    def _codigo_do_ar(self, pedido: Pedido) -> str:
        if pedido.botao:
            return pedido.botao
        if pedido.temperatura is None:
            return ""
        modo = pedido.modo or self._estado_ar()["modo"]
        codigo = f"{modo}_{pedido.temperatura}"
        return f"{codigo}_{pedido.ventilacao}" if pedido.ventilacao else codigo

    def _descobrir_tipo_pela_nuvem(self) -> None:
        """Sem o tipo do controle e com a nuvem ligada, pergunta a ela (o primeiro aprender escuta então do jeito certo)."""
        if self.nuvem is None or getattr(self.local, "tipo_conhecido", lambda: True)():
            return
        try:
            tipo = self.nuvem.tipo_do_controle()
        except ErroControle as exc:
            logger.info("A nuvem não disse o tipo do controle: %s", exc)
            return
        if tipo:
            self.local.definir_tipo(tipo)

    def _aprender(self, pedido: Pedido) -> str:
        if self.local is None:
            return ("Para aprender os botões eu preciso falar com o Ekaza pela rede de casa: falta pôr CONTROLE_ID e "
                    "CONTROLE_CHAVE_LOCAL no .env (ou rodar o wizard do tinytuya em banco_memoria\\tuya).")
        if self.aprendendo:
            return f"Ainda estou esperando {self.aprendendo}."
        aparelho = pedido.aparelhos[0]
        self._descobrir_tipo_pela_nuvem()
        if aparelho == "tv" and not pedido.botao:
            return ("Qual botão da TV? Diga, por exemplo, \"aprende o botão ligar da TV\", \"aprende o volume mais da TV\" "
                    "ou \"aprende o botão netflix da TV\".")
        if aparelho == "tv":
            return self._comecar_a_aprender(pedido)
        if not getattr(self.local, "tipo_conhecido", lambda: True)():
            # No primeiro botão ela escuta dos dois jeitos (metade do tempo cada); o ar não pode levar um segundo toque.
            return ("Antes do ar, me ensine um botão da TV, por exemplo \"aprende o botão mudo da TV\": no primeiro "
                    "botão eu descubro o tipo do Ekaza. Depois a gente faz o ar.")
        codigo = self._codigo_do_ar(pedido)
        if not codigo:
            return ("O que do ar eu aprendo? Diga \"aprende o ar em 22 graus\" (no frio, ou \"no quente\"), "
                    "\"aprende o botão desligar do ar\" ou \"aprende o botão ligar do ar\".")
        # O controle do ar manda o estado inteiro a cada toque: primeiro ele é preparado sem apontar para o Ekaza.
        pedido.botao = codigo
        self.pendencia = Pendencia("aprender", self.relogio() + SEGUNDOS_PARA_RESPONDER * 2, pedido=pedido)
        if codigo == "desligar":
            preparo = "deixe o controle do ar mostrando ligado"
        elif codigo == "ligar":
            preparo = "deixe o controle do ar mostrando desligado, já na temperatura que você mais usa"
        else:
            temperatura = int(codigo.split("_")[1])
            vizinha = temperatura - 1 if temperatura > self.minima else temperatura + 1
            modo = NOMES_MODOS[codigo.split("_")[0]]
            ventilacao = f" e ventilação {NOMES_VENTILACAO[codigo.split('_')[2]]}" if codigo.count("_") == 2 else ""
            preparo = f"deixe o controle do ar ligado, {modo}{ventilacao}, em {vizinha} graus"
        return (f"Vamos lá. Primeiro, sem apontar para o Ekaza (cubra o controle com a mão), {preparo}. "
                "Quando estiver pronto, diga \"pronto\".")

    def _comecar_a_aprender(self, pedido: Pedido) -> str:
        aparelho, botao = pedido.aparelhos[0], pedido.botao
        if aparelho == "tv":
            descricao = f"{descrever_botao(botao)} da TV"
            instrucao = (f"Aponte o controle da TV para o Ekaza, bem pertinho, e aperte {descrever_botao(botao)} agora; "
                         f"se eu não pegar de primeira, aperte de novo. Tenho {SEGUNDOS_PARA_APRENDER} segundos.")
            exemplo = {"energia": "liga a TV", "volume_mais": "aumenta o volume", "volume_menos": "abaixa o volume",
                       "mudo": "põe no mudo", "canal_mais": "próximo canal", "canal_menos": "volta o canal"}.get(
                botao, f"põe no canal {botao[-1]}" if botao.startswith("digito_") else f"aperta o {botao} da TV")
        else:
            descricao = f"{descrever_codigo_do_ar(botao)} do ar"
            if botao in {"ligar", "desligar"}:
                toque = f"aperte o botão de {botao}"
                exemplo = "liga o ar" if botao == "ligar" else "desliga o ar"
            else:
                temperatura = int(botao.split("_")[1])
                para = "para cima" if temperatura > self.minima else "para baixo"
                toque = f"aperte uma vez a temperatura {para}, para ficar em {temperatura} graus"
                exemplo = f"liga o ar em {temperatura} graus"
            instrucao = (f"Agora aponte o controle do ar para o Ekaza, bem pertinho, e {toque}. "
                         f"Tenho {SEGUNDOS_PARA_APRENDER} segundos.")
        self.aprendendo = descricao

        def trabalho():
            try:
                codigo = self.local.aprender(SEGUNDOS_PARA_APRENDER)
            except ErroControle as exc:
                self.avisar(f"Não consegui aprender {descricao}: {exc}.")
                return
            except Exception as exc:
                logger.exception("Falha ao aprender %s", descricao)
                self.avisar(f"Tive um problema para aprender {descricao}: {exc}")
                return
            finally:
                self.aprendendo = ""
            if not codigo:
                self.avisar(f"Não peguei nenhum sinal, então não aprendi {descricao}. Tente de novo com o controle a um "
                            "palmo do Ekaza, apontado para ele.")
                return
            self.registro.guardar_botao(aparelho, botao, codigo)
            self.avisar(f"Aprendi {descricao}. Agora é só pedir: \"{exemplo}\".")

        self.executar(trabalho)
        return instrucao

    # ================================================================== app do celular
    def painel_do_app(self) -> dict:
        """O que a tela de controle do app mostra: o estado lembrado (o do último comando), os botões que dá para apertar
        e o que o ar aceita."""
        with self._trava:
            if not self.ligado:
                return {"configurado": False, "motivo": "O controle remoto está desligado no .env (CONTROLE_REMOTO=false)."}
            if not self.configurado:
                return {"configurado": False, "motivo": MENSAGEM_SEM_CONFIGURACAO}
            try:
                controles = self._controles_da_nuvem() if self.nuvem is not None else {}
            except ErroControle as exc:
                logger.info("Tela de controle sem a nuvem: %s", exc)
                controles = {}
            tv, ar = self.registro.estado("tv"), {**ESTADO_PADRAO_AR, **self.registro.estado("ar")}

            def tem(botoes):
                try:
                    return self._achar_botao("tv", botoes) is not None
                except ErroControle:
                    return False

            botoes = [b for b in BOTOES_DO_APP if tem([b])]
            separados = tem(["ligar"]) and tem(["desligar"])  # códigos só de ligar e só de desligar (a Sony tem)
            def marca(texto):  # a Tuya grava "DaiKin"; siglas como "LG" ficam como estão
                texto = str(texto or "").strip()
                return texto if texto.isupper() else texto[:1].upper() + texto[1:].lower()

            marca_tv = marca((controles.get("tv") or {}).get("marca") or ("Sony" if self._marca_da_tv() == "sony" else ""))
            faixas = (controles.get("ar") or {}).get("faixas") or {}
            if faixas:
                modos = {nome: {"temps": faixas[str(valor)]["temps"],
                                "ventos": [v for v, n in VENTILACOES.items() if n in faixas[str(valor)]["ventos"]]}
                         for nome, valor in MODOS_DO_AR.items() if str(valor) in faixas}
            else:
                modos = {nome: {"temps": None if nome in MODOS_SEM_TEMPERATURA else [self.minima, self.maxima],
                                "ventos": list(VENTILACOES)} for nome in MODOS_DO_AR}
            ar_disponivel = bool(controles.get("ar")) or bool(self.registro.botoes("ar"))
            return {
                "configurado": True,
                "tv": {"disponivel": tem(["ligar", "energia"]) or bool(botoes),
                       "nome": f"TV {marca_tv}".strip(), "ligada": tv.get("ligada"), "quando": tv.get("quando"),
                       "energia_separada": separados, "botoes": botoes},
                "ar": {"disponivel": ar_disponivel,
                       "nome": f"Ar {marca((controles.get('ar') or {}).get('marca'))}".strip(),
                       "ligado": bool(ar.get("ligado")), "temperatura": ar["temperatura"], "modo": ar["modo"],
                       "ventilacao": ar["ventilacao"], "temperatura_conhecida": ar.get("temperatura_conhecida", True),
                       "quando": ar.get("quando"), "modos": modos},
                "aviso": "O infravermelho não tem volta: o estado é o do último comando que eu mandei.",
            }

    def _pedido_do_app(self, dados: dict) -> Pedido:
        """O pedido de um toque no app, validado (o app só manda o que a tela oferece; o resto é recusado)."""
        aparelho, acao = str(dados.get("aparelho", "")), str(dados.get("acao", ""))
        if aparelho == "tv":
            if acao in {"ligar", "desligar"}:
                return Pedido(acao, ("tv",))
            if acao == "botao":
                botao = str(dados.get("botao", ""))
                if botao not in NOMES_BOTOES and botao not in self.registro.botoes("tv"):
                    raise ValueError("Esse botão eu não conheço.")
                vezes = max(1, min(MAXIMO_TOQUES, int(dados.get("vezes", 1) or 1)))
                return Pedido("botao", ("tv",), botao=botao, vezes=vezes)
            if acao == "canal":
                canal = re.sub(r"\D", "", str(dados.get("canal", "")))[:4]
                if not canal:
                    raise ValueError("Diga o número do canal.")
                return Pedido("canal", ("tv",), canal=canal)
        if aparelho == "ar":
            if acao in {"ligar", "desligar"}:
                return Pedido(acao, ("ar",))
            if acao == "ajustar":
                temperatura = dados.get("temperatura")
                modo, ventilacao = dados.get("modo") or None, dados.get("ventilacao") or None
                if temperatura is not None:
                    temperatura = int(temperatura)
                if modo is not None and modo not in MODOS_DO_AR or ventilacao is not None and ventilacao not in VENTILACOES:
                    raise ValueError("Esse modo ou essa ventilação eu não conheço.")
                if temperatura is None and modo is None and ventilacao is None:
                    raise ValueError("Nada para mudar.")
                return Pedido("ar", ("ar",), temperatura=temperatura, modo=modo, ventilacao=ventilacao)
        raise ValueError("Pedido desconhecido.")

    def comando_do_app(self, dados: dict) -> dict:
        """Um toque na tela de controle do app ({"aparelho": "tv", "acao": "botao", "botao": "volume_mais"}). Faz o mesmo
        que o pedido falado, sem passar pelo texto. Devolve {"ok", "texto", "painel"} (ok False = não mandou nada)."""
        try:
            pedido = self._pedido_do_app(dados if isinstance(dados, dict) else {})
        except (ValueError, TypeError) as exc:
            return {"ok": False, "texto": str(exc) if isinstance(exc, ValueError) else "Pedido inválido.",
                    "painel": self.painel_do_app()}
        with self._trava:
            ok = True
            try:
                if not self.configurado:
                    texto, ok = MENSAGEM_SEM_CONFIGURACAO, False
                elif pedido.aparelhos == ("tv",) and pedido.acao in {"ligar", "desligar"}:
                    # Um botão apertado na tela é a ordem: sem o "aperto mesmo assim?" do botão único de energia.
                    texto = self._tv_energia(pedido.acao == "ligar", perguntar=False, forcar=True)
                else:
                    texto = getattr(self, f"_{pedido.acao}")(pedido)
                # As respostas de quem mandou começam com "Pronto"; "Esse ar vai de 18 a 30 graus." não mandou nada.
                ok = ok and texto.startswith("Pronto")
            except (FaltaCodigo, Pergunta) as exc:
                texto, ok = str(exc), False
            except ErroControle as exc:
                texto, ok = f"Não consegui: {exc}.", False
            except Exception as exc:
                logger.exception("Falha no controle pelo app")
                texto, ok = f"Tive um problema com o controle remoto: {exc}", False
            return {"ok": ok, "texto": texto, "painel": self.painel_do_app()}


# Os botões da TV que a tela do app pode mostrar (aparecem só os que dá para apertar).
BOTOES_DO_APP = ("ligar", "desligar", "energia", "volume_mais", "volume_menos", "mudo", "canal_mais", "canal_menos",
                 *(f"digito_{n}" for n in range(10)), "ok", "voltar", "inicio", "menu", "entrada", "cima", "baixo",
                 "esquerda", "direita", "play")


def criar_agente_controle(avisar=None) -> AgenteControle:
    """Agente com o controle do .env (config.py) ou do que o wizard do tinytuya gravou em banco_memoria/tuya."""
    import config
    from controle_remoto import ControleLocalTuya, ControleNuvemTuya, dados_do_wizard

    registro = RegistroDoControle(config.CAMINHO_CONTROLE)
    wizard = dados_do_wizard(config.PASTA_TUYA)
    id_aparelho = config.CONTROLE_ID or wizard.get("id", "")
    chave_local = config.CONTROLE_CHAVE_LOCAL or wizard.get("chave_local", "")
    local = None
    if id_aparelho and chave_local:
        local = ControleLocalTuya(id_aparelho, chave_local, ip=config.CONTROLE_IP or wizard.get("ip", ""),
                                  versao=config.CONTROLE_VERSAO or wizard.get("versao", ""), tipo=config.CONTROLE_TIPO,
                                  registro=registro)
    nuvem = None
    chave_api = config.TUYA_API_CHAVE or wizard.get("chave_api", "")
    segredo = config.TUYA_API_SEGREDO or wizard.get("segredo_api", "")
    if id_aparelho and chave_api and segredo and config.CONTROLE_NUVEM != "local":
        nuvem = ControleNuvemTuya(config.TUYA_API_REGIAO or wizard.get("regiao", "") or "us", chave_api, segredo, id_aparelho)
    return AgenteControle(registro, local=local, nuvem=nuvem, modo_nuvem=config.CONTROLE_NUVEM, avisar=avisar,
                          ligado=config.CONTROLE_REMOTO, minima=config.CONTROLE_AR_MINIMA,
                          maxima=config.CONTROLE_AR_MAXIMA, passos_volume=config.CONTROLE_VOLUME_PASSOS,
                          marca_tv=config.CONTROLE_TV_MARCA)
