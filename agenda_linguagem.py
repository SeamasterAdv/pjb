"""Entende datas, horas, repetições, durações e lembretes ditos em português, para a agenda local.

Trabalha numa cópia do texto sem acentos e em minúsculas com o mesmo tamanho do original (cada
letra vira uma letra). Assim os trechos reconhecidos ("amanhã", "às 14h", "toda segunda") saem do
texto original, e o que sobra vira o título com os acentos ("Reunião com o cliente").
Sem LLM: regras simples e testáveis (test_agenda.py).
"""
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

# ------------------------------------------------------------------ texto
def sem_acentos(texto: str) -> str:
    """Minúsculas e sem acentos, com o mesmo número de caracteres do texto (já em NFC)."""
    return "".join(unicodedata.normalize("NFD", ch)[0].lower()[0] for ch in texto)


def preparar(texto: str) -> tuple[str, str]:
    original = unicodedata.normalize("NFC", texto or "")
    return original, sem_acentos(original)


# ------------------------------------------------------------------ vocabulário
NUMEROS = {
    "zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
    "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "quatorze": 14,
    "catorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19,
    "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60, "noventa": 90,
}
_PALAVRAS_NUMERO = "|".join(sorted(NUMEROS, key=len, reverse=True))
NUM = rf"(?:\d{{1,3}}|vinte e (?:um|uma|dois|duas|tres)|quarenta e cinco|meia|{_PALAVRAS_NUMERO})"
UNIDADE = r"(?:min(?:uto)?s?|h(?:ora)?s?|hs|dias?|semanas?|mes(?:es)?)\b"
DURACAO = rf"(?P<n>{NUM})\s*(?P<u>{UNIDADE})(?:\s+e\s+(?:(?P<meia>meia)\b|(?P<n2>{NUM})\s*min\w*))?|(?P<meiahora>meia hora)\b"

DIAS_SEMANA = {"segunda": 0, "terca": 1, "quarta": 2, "quinta": 3, "sexta": 4, "sabado": 5, "domingo": 6}
NOMES_DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
DIA_SEMANA = r"(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)s?(?:[- ]feiras?)?"
MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
NOMES_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
               "outubro", "novembro", "dezembro"]
MES = r"(?:" + "|".join(MESES) + r")"
HORA_DO_PERIODO = {"madrugada": 6, "manha": 9, "tarde": 14, "noite": 19}
FAIXA_DO_PERIODO = {"madrugada": (0, 6), "manha": (6, 12), "tarde": (12, 18), "noite": (18, 24)}


def numero(texto: str) -> int | None:
    texto = (texto or "").strip()
    if texto.isdigit():
        return int(texto)
    if texto == "meia":
        return 30  # "e meia"; "meia hora" é tratado à parte
    if texto.startswith("vinte e "):
        return 20 + NUMEROS.get(texto[8:], 0)
    if texto == "quarenta e cinco":
        return 45
    return NUMEROS.get(texto)


def minutos_de(m: re.Match) -> int | None:
    """Minutos de uma expressão de DURACAO: "2 horas", "uma hora e meia", "meia hora", "3 dias"."""
    if m.group("meiahora"):
        return 30
    n = numero(m.group("n"))
    if n is None:
        return None
    unidade = m.group("u")
    if m.group("n") == "meia" and unidade.startswith("h"):
        return 30
    if unidade.startswith("min"):
        total = n
    elif unidade.startswith("h"):
        total = n * 60
    elif unidade.startswith("dia"):
        total = n * 1440
    elif unidade.startswith("semana"):
        total = n * 10080
    else:
        total = n * 43200  # mês: 30 dias
    if m.group("meia"):
        total += 30
    elif m.group("n2"):
        total += numero(m.group("n2")) or 0
    return total


# ------------------------------------------------------------------ resultado
@dataclass
class Interpretacao:
    data: date | None = None
    hora: time | None = None
    hora_ambigua: bool = False  # "às 9" sem "h" nem período: pode ser 9h ou 21h
    hora_fim: time | None = None
    duracao: int | None = None  # minutos ("por 2 horas")
    periodo: str | None = None  # manha, tarde, noite, madrugada (sem hora exata)
    recorrencia: str = ""
    lembretes: list | None = None  # None: o padrão; []: sem lembrete
    intervalo: tuple | None = None  # (primeiro, último dia) de "essa semana", "fim de semana"...
    relativo: timedelta | None = None  # "daqui a 2 horas"
    ate_data: date | None = None  # "até sexta": prazo de tarefa ou fim de uma repetição
    prioridade: int | None = None
    trechos: list = field(default_factory=list)  # (início, fim) usados, para tirar do título

    @property
    def tem_quando(self) -> bool:
        return any([self.data, self.hora, self.relativo, self.periodo, self.recorrencia])

    @property
    def tem_dia(self) -> bool:
        return bool(self.data or self.intervalo or self.relativo)


class _Leitor:
    """Aplica os padrões no texto sem acentos, apagando o que já foi usado para não casar duas vezes."""

    def __init__(self, normalizado: str, resultado: Interpretacao):
        self.texto = normalizado
        self.resultado = resultado

    def procurar(self, padrao: str):
        return list(re.finditer(padrao, self.texto))

    def usar(self, inicio: int, fim: int):
        self.resultado.trechos.append((inicio, fim))
        self.texto = self.texto[:inicio] + " " * (fim - inicio) + self.texto[fim:]


# ------------------------------------------------------------------ datas
def _proximo_dia_da_semana(hoje: date, alvo: int, pular_hoje: bool = False) -> date:
    delta = (alvo - hoje.weekday()) % 7
    if delta == 0 and pular_hoje:
        delta = 7
    return hoje + timedelta(days=delta)


def _data_completa(hoje: date, dia: int, mes: int | None, ano: int | None) -> date | None:
    """Data de "dia 20", "20 de outubro", "20/10": sem ano, a próxima vez que ela acontece."""
    try:
        if mes is None:
            candidata = date(hoje.year, hoje.month, dia)
            if candidata < hoje:
                proximo = hoje.month % 12 + 1
                candidata = date(hoje.year + (hoje.month == 12), proximo, dia)
            return candidata
        if ano is not None:
            return date(ano + 2000 if ano < 100 else ano, mes, dia)
        candidata = date(hoje.year, mes, dia)
        return candidata if candidata >= hoje else date(hoje.year + 1, mes, dia)
    except ValueError:
        return None


def _ler_datas(leitor: _Leitor, hoje: date, resultado: Interpretacao):
    """Datas soltas. "até <data>" vira `ate_data` (prazo); a primeira das outras vira `data`."""
    padroes = [
        (r"\bdepois de amanha\b", lambda m: hoje + timedelta(days=2)),
        (r"\bamanha\b", lambda m: hoje + timedelta(days=1)),
        (r"\b(?:hoje|hj)\b", lambda m: hoje),
        (r"\banteontem\b", lambda m: hoje - timedelta(days=2)),
        (r"\bontem\b", lambda m: hoje - timedelta(days=1)),
        (rf"\bdia (\d{{1,2}})(?:\s+de\s+({MES})|/(\d{{1,2}}))?(?:(?:\s+de\s+|/)(\d{{4}}))?\b",
         lambda m: _data_completa(hoje, int(m.group(1)), MESES.get(m.group(2)) or (int(m.group(3)) if m.group(3) else None),
                                  int(m.group(4)) if m.group(4) else None)),
        (rf"\b(\d{{1,2}})\s+de\s+({MES})(?:\s+de\s+(\d{{4}}))?\b",
         lambda m: _data_completa(hoje, int(m.group(1)), MESES[m.group(2)], int(m.group(3)) if m.group(3) else None)),
        (r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b",
         lambda m: _data_completa(hoje, int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None)),
        (r"\b(?:(?:n[oa]|nest[ea]|ness[ea]|est[ea]|ess[ea])\s+)?(?P<prox>proxim[oa]\s+)?"
         r"(?P<dia>segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:[- ]feira)?(?P<quevem>\s+que vem)?\b",
         lambda m: _proximo_dia_da_semana(hoje, DIAS_SEMANA[m.group("dia")],
                                          pular_hoje=bool(m.group("prox") or m.group("quevem")))),
    ]
    achadas = []
    for padrao, calcular in padroes:
        for m in leitor.procurar(padrao):
            valor = calcular(m)
            if valor is not None:
                achadas.append((m.start(), m.end(), valor))
            leitor.usar(m.start(), m.end())
    for inicio, fim, valor in sorted(achadas):
        antes = leitor.texto[:inicio]
        prazo = re.search(r"\b(?:ate|para ate|pra ate)\s+(?:o\s+|a\s+)?(?:dia\s+)?$", antes)
        if prazo:
            resultado.ate_data = valor
            leitor.usar(prazo.start(), prazo.end())
        elif resultado.data is None:
            resultado.data = valor


def _ler_intervalos(leitor: _Leitor, hoje: date, resultado: Interpretacao):
    domingo = hoje + timedelta(days=6 - hoje.weekday())
    fim_do_mes = (date(hoje.year + (hoje.month == 12), hoje.month % 12 + 1, 1) - timedelta(days=1))
    segunda_que_vem = domingo + timedelta(days=1)
    mes_que_vem = fim_do_mes + timedelta(days=1)
    fim_mes_que_vem = date(mes_que_vem.year + (mes_que_vem.month == 12), mes_que_vem.month % 12 + 1, 1) - timedelta(days=1)
    sabado = hoje if hoje.weekday() >= 5 else _proximo_dia_da_semana(hoje, 5)
    padroes = [
        (r"\b(?:semana que vem|proxima semana)\b", (segunda_que_vem, segunda_que_vem + timedelta(days=6))),
        (r"\b(?:n?es[st]a|da|na|minha) semana\b|\bsemana atual\b", (hoje, domingo)),
        (r"\b(?:n?es[st]e |n?o |pro |para o )?(?:fim|final) de semana\b", (sabado, sabado + timedelta(days=6 - sabado.weekday()))),
        (r"\b(?:mes que vem|proximo mes)\b", (mes_que_vem, fim_mes_que_vem)),
        (r"\b(?:n?es[st]e|do|no) mes\b", (hoje, fim_do_mes)),
    ]
    for padrao, intervalo in padroes:
        for m in leitor.procurar(padrao):
            resultado.intervalo = resultado.intervalo or intervalo
            leitor.usar(m.start(), m.end())
    for m in leitor.procurar(rf"\bproxim[oa]s\s+({NUM})\s+dias\b"):
        n = numero(m.group(1)) or 7
        resultado.intervalo = resultado.intervalo or (hoje, hoje + timedelta(days=n))
        leitor.usar(m.start(), m.end())


# ------------------------------------------------------------------ horas
def _hora(h: int, minuto: int, periodo: str | None, escrita: bool = False) -> tuple[time | None, bool]:
    """(hora, ambígua). Sem período: "às 3" vira 15h, e "6h30" fica 6h30 (com "h" ou ":", só 1h a 5h
    viram tarde: ninguém marca dentista às 3 da madrugada). De 6h/7h a 11h fica de manhã, mas ambígua."""
    if periodo in ("tarde", "noite") and h < 12:
        h += 12
    elif periodo == "noite" and h == 12:
        h = 0
    ambigua = False
    limite_tarde = 5 if escrita else 6
    if periodo is None and 1 <= h <= limite_tarde:
        h += 12
    elif periodo is None and limite_tarde < h <= 11:
        ambigua = True
    if not (0 <= h <= 23 and 0 <= minuto <= 59):
        return None, False
    return time(h, minuto), ambigua


_PERIODO = r"\s+(?:da|de|pela|a|na)\s+(?P<per>manha|tarde|noite|madrugada)\b"
_PERIODO_APOS = rf"(?:{_PERIODO})?"
_PALAVRA_NUM_HORA = r"(?:uma|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze)"
_FIM_DE_HORA = (r"(?=\s*(?:$|[,.!?;:]|\be\b|\bda\b|\bde\b|\bdo\b|\bno\b|\bna\b|\bem\b|\bpra\b|\bpara\b|\bcom\b"
                r"|\bhoje\b|\bamanha\b|\bate\b|\bpor\b|\bdurante\b|\btodo|\btoda|\bsem\b|\bme\b|\bte\b|\bmas\b))")
_MINUTOS_APOS = r"(?:\s+e\s+(?P<mm>meia|quinze|trinta|quarenta e cinco|\d{1,2})(?:\s*min\w*)?)?"


def _ler_horas(leitor: _Leitor, resultado: Interpretacao):
    # Intervalo de horas: "das 14 às 16", "de 9h até as 11h30", "entre 14h e 15h".
    um = r"(\d{1,2})(?:(?::|h)(\d{2}))?\s*(h|hs|horas)?"
    for m in leitor.procurar(rf"\b(?:d[ae]s?|entre)\s+{um}\s+(?:as|a|ate as|ate|e)\s+{um}{_PERIODO_APOS}"):
        periodo = m.group("per")
        escrita = bool(m.group(2) or m.group(3) or m.group(5) or m.group(6))
        inicio, amb = _hora(int(m.group(1)), int(m.group(2) or 0), periodo, escrita)
        fim, _ = _hora(int(m.group(4)), int(m.group(5) or 0), periodo, escrita)
        if inicio and fim:
            if fim <= inicio and fim.hour + 12 < 24 and (fim.hour + 12, fim.minute) > (inicio.hour, inicio.minute):
                fim = time(fim.hour + 12, fim.minute)
            resultado.hora, resultado.hora_fim, resultado.hora_ambigua = inicio, fim, amb and fim.hour < 12
            leitor.usar(m.start(), m.end())
            return

    prefixo = r"(?:as|a|pelas|pras|para as|pra|umas)"
    padroes = [
        (r"\b(?:(?:ao|as|a|pro|para o)\s+)?(?P<meio>meio[- ]dia|meia[- ]noite)(?:\s+e\s+(?P<mm>meia|\d{1,2}))?", True),
        (rf"\b(?:{prefixo}\s+)?(?P<h>\d{{1,2}})(?::|h)(?P<m>\d{{2}})\b{_PERIODO_APOS}", True),
        (rf"\b(?:{prefixo}\s+)?(?P<h>\d{{1,2}})\s*(?:h|hs|horas?)\b{_MINUTOS_APOS}{_PERIODO_APOS}", True),
        # "às 3", "às 3 e meia da tarde": depois de "às", número é hora (menos "3/10" e "3 de maio").
        (rf"\b(?:as|pelas|pras|para as|umas)\s+(?P<h>\d{{1,2}})\b(?!\s*(?:/|%|de\s+{MES}))"
         rf"{_MINUTOS_APOS}{_PERIODO_APOS}", False),
        # "às três", "às duas da tarde": por extenso, só com algo de hora depois ("as duas tarefas" não é hora).
        (rf"\b(?:as|pelas|pras|para as|umas)\s+(?P<h>{_PALAVRA_NUM_HORA}){_MINUTOS_APOS}(?:{_PERIODO}|{_FIM_DE_HORA})", False),
    ]
    for padrao, escrita in padroes:
        for m in leitor.procurar(padrao):
            grupos = m.groupdict()
            periodo = grupos.get("per")
            if grupos.get("meio"):
                h = 12 if grupos["meio"].startswith("meio") else 0
                hora, amb = time(h, numero(grupos.get("mm") or "0") or 0), False
            else:
                h = numero(grupos["h"])
                minuto = int(grupos["m"]) if grupos.get("m") else (numero(grupos.get("mm") or "0") or 0)
                if h is None:
                    continue
                hora, amb = _hora(h, minuto, periodo, escrita)
                if grupos.get("m") and grupos["h"].startswith("0"):
                    amb = False  # "08:00" é de manhã mesmo
            if hora is None:
                continue
            resultado.hora, resultado.hora_ambigua = hora, amb
            leitor.usar(m.start(), m.end())
            return


def _ler_periodo(leitor: _Leitor, resultado: Interpretacao):
    for m in leitor.procurar(r"\b(?:(?:de|da|pela|a|na|nesta|nessa|esta|essa|hoje a|hoje de|no periodo da)\s+)?"
                             r"(manha|tarde|noite|madrugada)\b"):
        resultado.periodo = resultado.periodo or m.group(1)
        leitor.usar(m.start(), m.end())


# ------------------------------------------------------------------ repetição, lembretes, duração
def _ler_repeticao(leitor: _Leitor, hoje: date, resultado: Interpretacao):
    m = next(iter(leitor.procurar(r"\btodo (?:santo )?dia (\d{1,2})\b(?!\s*(?:de\b|/))")), None)
    if m:
        resultado.recorrencia = "mensal"
        resultado.data = _data_completa(hoje, int(m.group(1)), None, None)
        leitor.usar(m.start(), m.end())
        return
    simples = [
        (r"\b(?:tod[oa]s? (?:os )?)?dias? uteis\b|\b(?:tod[ao]s? )?(?:de )?segunda a sexta(?:[- ]feira)?\b", "dias_uteis"),
        (r"\b(?:todos os dias|todo (?:santo )?dia|diariamente|cada dia)\b", "diaria"),
        (r"\b(?:toda semana|todas as semanas|semanalmente|uma vez por semana)\b", "semanal"),
        (r"\b(?:todo mes|todos os meses|mensalmente|uma vez por mes)\b", "mensal"),
        (r"\b(?:todo ano|todos os anos|anualmente|uma vez por ano)\b", "anual"),
    ]
    for padrao, regra in simples:
        for m in leitor.procurar(padrao):
            resultado.recorrencia = resultado.recorrencia or regra
            leitor.usar(m.start(), m.end())
    lista = rf"{DIA_SEMANA}(?:\s*(?:,|e)\s*(?:[ao]s?\s+)?{DIA_SEMANA})*"
    for m in leitor.procurar(rf"\b(?P<art>tod[oa]s?(?: [oa]s)?|cada|nas|aos|as|nos)\s+(?P<dias>{lista})\b"):
        dias = [DIAS_SEMANA[d] for d in re.findall(r"segunda|terca|quarta|quinta|sexta|sabado|domingo", m.group("dias"))]
        plural = re.match(r"(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)s\b|\w+[- ]feiras\b", m.group("dias"))
        if m.group("art") in ("nas", "aos", "as", "nos") and not plural:
            continue  # "na segunda" é um dia só; "nas segundas" é toda semana
        resultado.recorrencia = "semanal:" + ",".join(str(d) for d in sorted(set(dias)))
        leitor.usar(m.start(), m.end())
        return


def _ler_lembretes(leitor: _Leitor, resultado: Interpretacao):
    for m in leitor.procurar(r"\bsem (?:lembretes?|avisos?|alertas?|notificac\w*)\b"):
        resultado.lembretes = []
        leitor.usar(m.start(), m.end())
    verbo = r"(?:(?:me |te )?(?:avis\w*|lembr\w*|alert\w*|notifi\w*)|(?:com )?(?:um )?(?:lembretes?|avisos?|alertas?)(?: de)?)"
    for m in leitor.procurar(rf"\b(?:{verbo}\s+)?(?:com\s+)?(?:{DURACAO})\s+(?:de\s+)?antes\b"):
        minutos = minutos_de(m)
        if minutos is not None:
            resultado.lembretes = sorted(set((resultado.lembretes or []) + [minutos]), reverse=True)
            leitor.usar(m.start(), m.end())
    for m in leitor.procurar(rf"\b(?:{verbo}\s+)?(?:um dia antes|no dia anterior|na vespera)\b"):
        resultado.lembretes = sorted(set((resultado.lembretes or []) + [1440]), reverse=True)
        leitor.usar(m.start(), m.end())
    for m in leitor.procurar(r"\b(?:me |te )?(?:avis\w*|lembr\w*) na hora\b"):
        resultado.lembretes = sorted(set((resultado.lembretes or []) + [0]), reverse=True)
        leitor.usar(m.start(), m.end())


def _ler_relativo(leitor: _Leitor, hoje: date, resultado: Interpretacao):
    for m in leitor.procurar(r"\bdaqui a (?:pouco|pouquinho)\b"):
        resultado.relativo = timedelta(minutes=10)
        leitor.usar(m.start(), m.end())
        return
    for m in leitor.procurar(rf"\b(?:daqui a|daqui|dentro de|em)\s+(?:{DURACAO})"):
        minutos = minutos_de(m)
        if minutos is None:
            continue
        if minutos >= 1440:  # "daqui a 3 dias": um dia, e a hora pode vir à parte
            resultado.data = resultado.data or hoje + timedelta(minutes=minutos)
        else:
            resultado.relativo = timedelta(minutes=minutos)
        leitor.usar(m.start(), m.end())
        return


def _ler_duracao(leitor: _Leitor, resultado: Interpretacao):
    for m in leitor.procurar(rf"\b(?:por|durante|com duracao de|vai durar|dura|leva|levo|demora)\s+"
                             rf"(?:uns\s+|umas\s+|cerca de\s+|mais ou menos\s+)?(?:{DURACAO})"):
        minutos = minutos_de(m)
        if minutos is not None and minutos < 1440:
            resultado.duracao = minutos
            leitor.usar(m.start(), m.end())
            return


def _ler_prioridade(leitor: _Leitor, resultado: Interpretacao):
    for padrao, valor in ((r"\b(?:urgente|muito importante|importante|prioridade alta|alta prioridade|prioritari\w*)\b", 1),
                          (r"\b(?:sem pressa|baixa prioridade|prioridade baixa|quando der|quando puder)\b", 3)):
        for m in leitor.procurar(padrao):
            resultado.prioridade = resultado.prioridade or valor
            leitor.usar(m.start(), m.end())


# ------------------------------------------------------------------ entrada principal
def interpretar(texto: str, agora: datetime | None = None) -> Interpretacao:
    """Lê tudo o que for de tempo na frase. A ordem importa: "30 minutos antes" é lembrete,
    "por 2 horas" é duração, "toda segunda" é repetição, e só o que sobrar vira data ou hora."""
    agora = agora or datetime.now()
    hoje = agora.date()
    _, normalizado = preparar(texto)
    resultado = Interpretacao()
    leitor = _Leitor(normalizado, resultado)
    _ler_lembretes(leitor, resultado)
    _ler_relativo(leitor, hoje, resultado)
    _ler_duracao(leitor, resultado)
    _ler_prioridade(leitor, resultado)
    _ler_repeticao(leitor, hoje, resultado)
    _ler_intervalos(leitor, hoje, resultado)
    _ler_datas(leitor, hoje, resultado)
    _ler_horas(leitor, resultado)
    _ler_periodo(leitor, resultado)
    return resultado


def resolver_momento(i: Interpretacao, agora: datetime) -> datetime | None:
    """Data e hora de início. Só o dia: meia-noite (o chamador decide se é "dia inteiro").
    Só a hora: hoje, ou amanhã se já passou ("às 9" já passado vira 21h, se ainda der)."""
    if i.relativo:
        return (agora + i.relativo).replace(second=0, microsecond=0)
    hora = i.hora or (time(HORA_DO_PERIODO[i.periodo]) if i.periodo else None)
    if i.hora and i.periodo in ("tarde", "noite") and i.hora.hour < 12:  # "às 8 ... à noite"
        hora = time(i.hora.hour + 12, i.hora.minute)
    if i.data is None and hora is None:
        return None
    if hora is None:
        return datetime.combine(i.data, time())
    if i.data is not None and i.data != agora.date():
        return datetime.combine(i.data, hora)
    momento = datetime.combine(agora.date(), hora)
    if i.data is not None:  # "hoje às 9", dito às 15h: 21h
        if momento <= agora and i.hora_ambigua and hora.hour < 12 and momento + timedelta(hours=12) > agora:
            return momento + timedelta(hours=12)
        return momento
    if momento <= agora and i.hora_ambigua and hora.hour < 12:
        mais_tarde = momento + timedelta(hours=12)
        if mais_tarde > agora:
            return mais_tarde
    if momento <= agora:
        momento += timedelta(days=1)
    return momento


# ------------------------------------------------------------------ título
CONECTIVOS = {"de", "do", "da", "dos", "das", "no", "na", "nos", "nas", "em", "para", "pra", "pro", "as", "a",
              "o", "os", "e", "com", "que", "um", "uma", "pelas", "pelos", "ao", "aos", "me", "te", "pela", "pelo",
              "ate", "por", "favor", "beatriz", "eu", "la", "ai", "aqui", "tambem", "isso", "sobre", "agora", "so"}


def tirar_trechos(original: str, trechos) -> str:
    """O texto sem os trechos (início, fim) indicados."""
    manter = [True] * len(original)
    for inicio, fim in trechos:
        for k in range(max(0, inicio), min(len(original), fim)):
            manter[k] = False
    return "".join(ch if manter[k] else " " for k, ch in enumerate(original))


def tirar_padroes(original: str, padroes) -> str:
    """Tira do texto original os trechos que casam (no texto sem acentos) com cada padrão."""
    texto = original
    for padrao in padroes:
        normalizado = sem_acentos(texto)
        trechos = [(m.start(), m.end()) for m in re.finditer(padrao, normalizado)]
        texto = tirar_trechos(texto, trechos)
    return texto


def limpar_titulo(texto: str) -> str:
    """Espaços, pontuação e palavras de ligação soltas nas pontas; primeira letra maiúscula."""
    texto = re.sub(r"\s+", " ", texto).strip(" ,.;:!?-–—\"'")
    palavras = texto.split(" ") if texto else []
    while palavras and sem_acentos(palavras[0]).strip(",.;:!?") in CONECTIVOS:
        palavras.pop(0)
    while palavras and sem_acentos(palavras[-1]).strip(",.;:!?") in CONECTIVOS:
        palavras.pop()
    texto = " ".join(palavras).strip(" ,.;:!?-–—\"'")
    texto = re.sub(r"\s+([,.;:!?])", r"\1", texto)
    return texto[:1].upper() + texto[1:] if texto else ""


def titulo_da_frase(texto: str, interpretacao: Interpretacao, padroes_comando=()) -> str:
    original, _ = preparar(texto)
    sobra = tirar_trechos(original, interpretacao.trechos)
    return limpar_titulo(tirar_padroes(sobra, padroes_comando))


# ------------------------------------------------------------------ falar datas e horas
def falar_hora(momento) -> str:
    """14h, 14h30, meio-dia, meia-noite."""
    h, m = momento.hour, momento.minute
    if (h, m) == (12, 0):
        return "meio-dia"
    if (h, m) == (0, 0):
        return "meia-noite"
    return f"{h}h{m:02d}" if m else f"{h}h"


def falar_dia(dia: date, hoje: date) -> str:
    """hoje, amanhã, sexta-feira (dia 24), dia 3 de novembro."""
    diferenca = (dia - hoje).days
    if diferenca == 0:
        return "hoje"
    if diferenca == 1:
        return "amanhã"
    if diferenca == 2:
        return f"depois de amanhã ({NOMES_DIAS[dia.weekday()]})"
    if diferenca == -1:
        return "ontem"
    if 2 < diferenca < 7:
        return f"{NOMES_DIAS[dia.weekday()]} (dia {dia.day})"
    texto = f"{dia.day} de {NOMES_MESES[dia.month - 1]}" + (f" de {dia.year}" if dia.year != hoje.year else "")
    return f"{NOMES_DIAS[dia.weekday()]} ({texto})"


def falar_dia_curto(dia: date, hoje: date) -> str:
    """hoje, amanhã, sexta-feira (até 6 dias), 3 de novembro: para prazos e listas."""
    diferenca = (dia - hoje).days
    if diferenca in (-1, 0, 1):
        return falar_dia(dia, hoje)
    if 1 < diferenca < 7:
        return NOMES_DIAS[dia.weekday()]
    return f"{dia.day} de {NOMES_MESES[dia.month - 1]}" + (f" de {dia.year}" if dia.year != hoje.year else "")


def falar_duracao(minutos: int) -> str:
    if minutos >= 1440 and minutos % 1440 == 0:
        dias = minutos // 1440
        return "1 dia" if dias == 1 else f"{dias} dias"
    if minutos >= 60:
        horas, resto = divmod(minutos, 60)
        base = "1 hora" if horas == 1 else f"{horas} horas"
        return base + (" e meia" if resto == 30 else f" e {resto} minutos" if resto else "")
    return "1 minuto" if minutos == 1 else f"{minutos} minutos"


def falar_recorrencia(regra: str, inicio: datetime | None = None) -> str:
    if regra == "diaria":
        return "todo dia"
    if regra == "dias_uteis":
        return "de segunda a sexta"
    if regra == "mensal":
        return f"todo dia {inicio.day} do mês" if inicio else "todo mês"
    if regra == "anual":
        return f"todo ano, em {inicio.day} de {NOMES_MESES[inicio.month - 1]}" if inicio else "todo ano"
    if regra.startswith("semanal"):
        dias = regra.split(":", 1)[1] if ":" in regra else (str(inicio.weekday()) if inicio else "")
        nomes = [NOMES_DIAS[int(d)].split("-")[0] for d in dias.split(",") if d.strip().isdigit()]
        if not nomes:
            return "toda semana"
        artigo = "todo" if nomes[0] in ("sábado", "domingo") else "toda"
        return f"{artigo} {juntar(nomes)}"
    return ""


def juntar(itens) -> str:
    itens = [str(i) for i in itens if i]
    if len(itens) <= 1:
        return "".join(itens)
    return ", ".join(itens[:-1]) + " e " + itens[-1]
