"""Prazos processuais: o calendário forense e a conta do vencimento, sem LLM e sem internet.

Regras (os artigos e as súmulas foram conferidos no vade-mécum do PC em out/2026):
- cível, trabalhista e juizados cíveis: só os dias úteis (CPC, art. 219; CLT, art. 775; Lei 9.099, art. 12-A), sem o
  dia do começo e com o do vencimento (CPC, art. 224), e o curso suspenso de 20/12 a 20/1 (CPC, art. 220; CLT,
  art. 775-A). Intimação em dia sem expediente vale no primeiro dia útil seguinte (Lei 11.419, art. 5º, § 2º).
- penal e Jecrim: dias corridos (CPP, art. 798), contados da intimação, não da juntada (Súmula 710 do STF), começando
  no primeiro dia útil seguinte (art. 798, § 1º, e Súmula 310 do STF); terminou em dia sem expediente, passa para o
  primeiro dia útil (art. 798, § 3º). Suspensão de 20/12 a 20/1 (art. 798-A), menos com réu preso, Lei Maria da Penha
  ou medida urgente.
- Diário da Justiça eletrônico: a publicação é o primeiro dia útil depois da disponibilização (CPC, art. 224, § 2º).
- Intimação dentro da suspensão: a conta começa no primeiro dia útil depois de 20/1 (há tribunal que começa um dia
  depois; a resposta avisa).

Os feriados locais e as portarias de cada tribunal mudam a conta, por isso as respostas pedem para conferir.
Testes em test_prazos.py.
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

logger = logging.getLogger(__name__)

UTEIS = "uteis"
CORRIDOS = "corridos"
LIMITE_DE_DIAS = 400  # um prazo maior que isso é engano de transcrição


# ------------------------------------------------------------------ ritos e prazos
@dataclass(frozen=True)
class Rito:
    chave: str
    nome: str  # "no processo civil"
    contagem: str  # UTEIS | CORRIDOS
    fundamento_contagem: str
    fundamento_suspensao: str


RITOS = {
    "civel": Rito("civel", "no processo civil", UTEIS, "art. 219 do CPC", "art. 220 do CPC"),
    "trabalhista": Rito("trabalhista", "na Justiça do Trabalho", UTEIS, "art. 775 da CLT", "art. 775-A da CLT"),
    "juizado": Rito("juizado", "no juizado especial cível", UTEIS, "art. 12-A da Lei 9.099", "art. 220 do CPC"),
    "penal": Rito("penal", "no processo penal", CORRIDOS, "art. 798 do CPP", "art. 798-A do CPP"),
    "jecrim": Rito("jecrim", "no juizado especial criminal", CORRIDOS, "art. 798 do CPP", "art. 798-A do CPP"),
    # "15 dias corridos" fora do processo penal: a regra geral (sem a Súmula 310 nem a suspensão de fim de ano)
    "corridos": Rito("corridos", "em dias corridos", CORRIDOS, "art. 132 do Código Civil", ""),
}


@dataclass(frozen=True)
class TipoDePrazo:
    chave: str
    nome: str  # com o artigo: "a contestação"
    dias: int
    rito: str
    fundamento: str
    padrao: str  # no texto sem acentos
    recurso: bool = False
    depois_de: str = ""  # o prazo que precisa acabar antes (a impugnação começa quando acaba o do pagamento)


_CONTRARRAZOES = r"\bcontra-?\s?razo(?:es|ar)\b|\bcontrarrazo(?:es|ar)\b"
_EMBARGOS_DE_DECLARACAO = (r"\bembargos (?:de declaracao|declaratorios)\b|\bdeclaratorios\b|\baclaratorios\b"
                           r"|\bembargar\b|\bopor embargos\b")
_EMBARGOS_A_EXECUCAO = r"\bembargos (?:a|da|de|do) execu(?:cao|tado)\b|\bembargar a execucao\b"

TIPOS = [
    # cível (CPC)
    TipoDePrazo("contestacao", "a contestação", 15, "civel", "art. 335 do CPC", r"\bcontesta(?:cao|coes|r)\b"),
    TipoDePrazo("replica", "a réplica", 15, "civel", "art. 351 do CPC", r"\breplica\b|\bimpugnacao a contestacao\b"),
    TipoDePrazo("apelacao", "a apelação", 15, "civel", "art. 1.003, § 5º, do CPC", r"\bapela(?:cao|r)\b", True),
    TipoDePrazo("contrarrazoes", "as contrarrazões", 15, "civel", "art. 1.003, § 5º, do CPC", _CONTRARRAZOES),
    TipoDePrazo("agravo_instrumento", "o agravo de instrumento", 15, "civel", "art. 1.003, § 5º, do CPC",
                r"\bagravo de instrumento\b|\bagravar\b", True),
    TipoDePrazo("agravo_interno", "o agravo interno", 15, "civel", "art. 1.070 do CPC",
                r"\bagravo (?:interno|regimental)\b", True),
    TipoDePrazo("embargos_declaracao", "os embargos de declaração", 5, "civel", "art. 1.023 do CPC",
                _EMBARGOS_DE_DECLARACAO, True),
    TipoDePrazo("recurso_especial", "o recurso especial", 15, "civel", "art. 1.003, § 5º, do CPC",
                r"\brecurso especial\b|\bresp\b", True),
    TipoDePrazo("recurso_extraordinario", "o recurso extraordinário", 15, "civel", "art. 1.003, § 5º, do CPC",
                r"\brecurso extraordinario\b", True),
    TipoDePrazo("pagamento", "o pagamento no cumprimento de sentença", 15, "civel", "art. 523 do CPC",
                r"\bcumprimento de sentenca\b|\bpagamento voluntario\b|\bpagar a condenacao\b|\bart(?:igo)?\.? 523\b"),
    TipoDePrazo("impugnacao_cumprimento", "a impugnação ao cumprimento de sentença", 15, "civel", "art. 525 do CPC",
                r"\bimpugna\w* (?:ao|do|o) cumprimento(?: de sentenca)?\b", depois_de="pagamento"),
    TipoDePrazo("embargos_execucao", "os embargos à execução", 15, "civel", "art. 915 do CPC", _EMBARGOS_A_EXECUCAO),
    # juizado especial cível (Lei 9.099)
    TipoDePrazo("recurso_inominado", "o recurso inominado", 10, "juizado", "art. 42 da Lei 9.099",
                r"\brecurso inominado\b", True),
    TipoDePrazo("contrarrazoes", "as contrarrazões do recurso inominado", 10, "juizado", "art. 42, § 2º, da Lei 9.099",
                _CONTRARRAZOES),
    TipoDePrazo("embargos_declaracao", "os embargos de declaração", 5, "juizado", "art. 49 da Lei 9.099",
                _EMBARGOS_DE_DECLARACAO, True),
    # Justiça do Trabalho (CLT; 8 dias para os recursos, art. 6º da Lei 5.584/70)
    TipoDePrazo("recurso_ordinario", "o recurso ordinário", 8, "trabalhista", "art. 895 da CLT",
                r"\brecurso ordinario\b", True),
    TipoDePrazo("contrarrazoes", "as contrarrazões", 8, "trabalhista", "art. 900 da CLT", _CONTRARRAZOES),
    TipoDePrazo("agravo_peticao", "o agravo de petição", 8, "trabalhista", "art. 897, a, da CLT",
                r"\bagravo de peticao\b", True),
    TipoDePrazo("agravo_instrumento", "o agravo de instrumento", 8, "trabalhista", "art. 897, b, da CLT",
                r"\bagravo de instrumento\b|\bagravar\b", True),
    TipoDePrazo("recurso_revista", "o recurso de revista", 8, "trabalhista", "art. 6º da Lei 5.584/70",
                r"\brecurso de revista\b", True),
    TipoDePrazo("embargos_declaracao", "os embargos de declaração", 5, "trabalhista", "art. 897-A da CLT",
                _EMBARGOS_DE_DECLARACAO, True),
    TipoDePrazo("embargos_execucao", "os embargos à execução", 5, "trabalhista", "art. 884 da CLT", _EMBARGOS_A_EXECUCAO),
    # processo penal (CPP)
    TipoDePrazo("resposta_acusacao", "a resposta à acusação", 10, "penal", "art. 396 do CPP",
                r"\bresposta a acusacao\b|\bdefesa previa\b|\bresposta escrita\b"),
    TipoDePrazo("apelacao", "a apelação", 5, "penal", "art. 593 do CPP", r"\bapela(?:cao|r)\b", True),
    TipoDePrazo("razoes_apelacao", "as razões da apelação", 8, "penal", "art. 600 do CPP",
                r"\brazoes (?:de|da|do) apela\w*\b|\barrazoar\b"),
    TipoDePrazo("contrarrazoes", "as contrarrazões", 8, "penal", "art. 600 do CPP", _CONTRARRAZOES),
    TipoDePrazo("rese", "o recurso em sentido estrito", 5, "penal", "art. 586 do CPP",
                r"\brecurso em sentido estrito\b|\brese\b", True),
    TipoDePrazo("embargos_declaracao", "os embargos de declaração", 2, "penal", "arts. 382 e 619 do CPP",
                _EMBARGOS_DE_DECLARACAO, True),
    TipoDePrazo("embargos_infringentes", "os embargos infringentes", 10, "penal", "art. 609, parágrafo único, do CPP",
                r"\bembargos infringentes\b", True),
    TipoDePrazo("memoriais", "os memoriais", 5, "penal", "art. 403, § 3º, do CPP", r"\bmemoriais\b|\balegacoes finais\b"),
    TipoDePrazo("agravo_execucao", "o agravo em execução", 5, "penal", "Súmula 700 do STF",
                r"\bagravo (?:em|de|na) execucao(?: penal)?\b", True),
    # juizado especial criminal (Lei 9.099; contagem do CPP)
    TipoDePrazo("apelacao", "a apelação", 10, "jecrim", "art. 82, § 1º, da Lei 9.099", r"\bapela(?:cao|r)\b", True),
    TipoDePrazo("contrarrazoes", "as contrarrazões", 10, "jecrim", "art. 82, § 2º, da Lei 9.099", _CONTRARRAZOES),
    TipoDePrazo("embargos_declaracao", "os embargos de declaração", 5, "jecrim", "art. 83, § 1º, da Lei 9.099",
                _EMBARGOS_DE_DECLARACAO, True),
]


def tipos_citados(normalizado: str) -> list[tuple[TipoDePrazo, int]]:
    """Os prazos citados na frase (sem acentos), com o tamanho do trecho que casou."""
    achados = []
    for tipo in TIPOS:
        trechos = [m.end() - m.start() for m in re.finditer(tipo.padrao, normalizado)]
        if trechos:
            achados.append((tipo, max(trechos)))
    return achados


# O nome do recurso de outro rito: "apelação trabalhista" é o recurso ordinário, e a do juizado cível, o inominado.
EQUIVALENTES = {
    ("trabalhista", "apelacao"): ("recurso_ordinario", "Na Justiça do Trabalho, contra a sentença cabe recurso ordinário."),
    ("juizado", "apelacao"): ("recurso_inominado", "No juizado especial cível, contra a sentença cabe recurso inominado."),
}


def tipo_pela_chave(chave: str, rito: str) -> TipoDePrazo | None:
    return next((t for t in TIPOS if t.chave == chave and t.rito == rito), None)


# ------------------------------------------------------------------ calendário
def pascoa(ano: int) -> date:
    """Domingo de Páscoa (algoritmo de Meeus/Jones/Butcher)."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = (h + l - 7 * m + 114) % 31 + 1
    return date(ano, mes, dia)


FIXOS_NACIONAIS = {
    (1, 1): "Confraternização Universal", (21, 4): "Tiradentes", (1, 5): "Dia do Trabalho", (7, 9): "Independência",
    (12, 10): "Nossa Senhora Aparecida", (2, 11): "Finados", (15, 11): "Proclamação da República", (25, 12): "Natal",
}
FIXOS_FORENSES = {(8, 12): "Dia da Justiça"}
MOVEIS_FORENSES = {-48: "segunda-feira de Carnaval", -47: "terça-feira de Carnaval", -3: "quinta-feira santa",
                   60: "Corpus Christi"}


def suspenso(dia: date) -> bool:
    """De 20 de dezembro a 20 de janeiro, inclusive (CPC, art. 220; CLT, art. 775-A; CPP, art. 798-A)."""
    return (dia.month == 12 and dia.day >= 20) or (dia.month == 1 and dia.day <= 20)


def ler_datas_extras(texto: str) -> tuple[set, set]:
    """"30/11, 24/12/2026" -> ({(30, 11)}, {date(2026, 12, 24)}): sem o ano vale todo ano."""
    todo_ano, uma_vez = set(), set()
    for parte in re.split(r"[,;\s]+", texto or ""):
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", parte.strip())
        if not m:
            continue
        try:
            dia = date(int(m.group(3) or 2000), int(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
        if m.group(3):
            uma_vez.add(dia)
        else:
            todo_ano.add((dia.day, dia.month))
    return todo_ano, uma_vez


class Calendario:
    """Os dias sem expediente forense: fins de semana, feriados nacionais, os forenses comuns (opcionais), os do
    .env e os ditos ("dia 31 não tem expediente"), guardados num JSON lido de novo quando muda."""

    def __init__(self, forenses: bool = True, extras: str = "", caminho: str | None = None):
        self.forenses = forenses
        self.extras_todo_ano, self.extras_uma_vez = ler_datas_extras(extras)
        self.caminho = caminho
        self._cache: dict[int, dict] = {}
        self._marca_do_arquivo = None
        self._marcados: dict[date, str] = {}

    # ------------------------------------------------ os dias ditos, no arquivo
    def _ler_marcados(self) -> dict[date, str]:
        if not self.caminho:
            return self._marcados
        try:
            estado = os.stat(self.caminho)
            marca = (estado.st_mtime_ns, estado.st_size)
        except OSError:
            marca = None
        if marca != self._marca_do_arquivo:
            self._marca_do_arquivo = marca
            self._cache.clear()
            self._marcados = {}
            if marca:
                try:
                    with open(self.caminho, encoding="utf-8") as arquivo:
                        dados = json.load(arquivo)
                    self._marcados = {date.fromisoformat(dia): motivo for dia, motivo in dados.get("dias", {}).items()}
                except (OSError, ValueError, AttributeError) as exc:
                    logger.warning("Arquivo dos dias sem expediente com defeito (%s): %s", self.caminho, exc)
        return self._marcados

    def _gravar_marcados(self, marcados: dict[date, str]):
        self._marcados = dict(marcados)
        self._cache.clear()
        if not self.caminho:
            return
        os.makedirs(os.path.dirname(self.caminho) or ".", exist_ok=True)
        temporario = self.caminho + ".tmp"
        with open(temporario, "w", encoding="utf-8") as arquivo:
            json.dump({"dias": {d.isoformat(): m for d, m in sorted(marcados.items())}}, arquivo,
                      ensure_ascii=False, indent=2)
        os.replace(temporario, self.caminho)
        self._marca_do_arquivo = None  # relê na próxima consulta

    def marcar_sem_expediente(self, dia: date, motivo: str = "sem expediente"):
        marcados = dict(self._ler_marcados())
        marcados[dia] = motivo
        self._gravar_marcados(marcados)

    def desmarcar(self, dia: date) -> bool:
        marcados = dict(self._ler_marcados())
        if marcados.pop(dia, None) is None:
            return False
        self._gravar_marcados(marcados)
        return True

    def dias_marcados(self) -> dict[date, str]:
        return dict(self._ler_marcados())

    # ------------------------------------------------ consultas
    def feriados(self, ano: int) -> dict[date, str]:
        marcados = self._ler_marcados()
        if ano in self._cache:
            return self._cache[ano]
        dias = {date(ano, mes, dia): nome for (dia, mes), nome in FIXOS_NACIONAIS.items()}
        if ano >= 2024:  # Lei 14.759/2023
            dias[date(ano, 11, 20)] = "Dia da Consciência Negra"
        domingo = pascoa(ano)
        dias[domingo - timedelta(days=2)] = "sexta-feira santa"
        if self.forenses:
            dias.update({date(ano, mes, dia): nome for (dia, mes), nome in FIXOS_FORENSES.items()})
            dias.update({domingo + timedelta(days=d): nome for d, nome in MOVEIS_FORENSES.items()})
        for dia, mes in self.extras_todo_ano:
            try:
                dias.setdefault(date(ano, mes, dia), "feriado local")
            except ValueError:
                continue
        dias.update({d: "feriado local" for d in self.extras_uma_vez if d.year == ano and d not in dias})
        dias.update({d: m for d, m in marcados.items() if d.year == ano})
        self._cache[ano] = dias
        return dias

    def motivo(self, dia: date) -> str | None:
        """Por que não há expediente nesse dia (None: há)."""
        feriado = self.feriados(dia.year).get(dia)
        if feriado:
            return feriado
        return "fim de semana" if dia.weekday() >= 5 else None

    def dia_util(self, dia: date) -> bool:
        return self.motivo(dia) is None

    def proximo_dia_util(self, dia: date, suspender: bool = False) -> date:
        """O primeiro dia útil depois de `dia` (com `suspender`, também fora da suspensão de fim de ano)."""
        dia += timedelta(days=1)
        while not self.dia_util(dia) or (suspender and suspenso(dia)):
            dia += timedelta(days=1)
        return dia


# ------------------------------------------------------------------ a conta
@dataclass
class Calculo:
    dias: int
    rito: Rito
    intimacao: date  # a data dita
    considerada: date  # o dia que vale como intimação (ou publicação)
    inicio: date  # o primeiro dia contado
    vencimento: date
    publicacao: date | None = None  # quando a data dita foi a disponibilização no DJe
    pulados: list = field(default_factory=list)  # (dia, motivo): de segunda a sexta, sem expediente, fora da conta
    suspensao: bool = False  # o recesso de 20/12 a 20/1 entrou na conta
    intimacao_na_suspensao: bool = False
    prorrogado_de: date | None = None  # penal: o último dia caiu sem expediente


def calcular(intimacao: date, dias: int, rito: str = "civel", *, calendario: Calendario,
             disponibilizacao: bool = False, suspender: bool = True) -> Calculo:
    """O vencimento de um prazo de `dias` a partir da intimação (ou da disponibilização no DJe)."""
    if not 0 < dias <= LIMITE_DE_DIAS:
        raise ValueError(f"prazo fora do esperado: {dias} dias")
    regra = RITOS[rito]
    suspender = suspender and bool(regra.fundamento_suspensao)
    publicacao =calendario.proximo_dia_util(intimacao) if disponibilizacao else None
    base = publicacao or intimacao
    pulados, usou_suspensao = [], False

    def conta(dia: date) -> bool:
        return calendario.dia_util(dia) and not (suspender and suspenso(dia))

    def fora_da_conta(dia: date):
        nonlocal usou_suspensao
        if suspender and suspenso(dia):
            usou_suspensao = True
        elif dia.weekday() < 5:
            pulados.append((dia, calendario.motivo(dia)))

    prorrogado_de = None
    if regra.contagem == UTEIS:
        considerada = base if calendario.dia_util(base) else calendario.proximo_dia_util(base)
        dia, contados, inicio = considerada, 0, None
        while contados < dias:
            dia += timedelta(days=1)
            if conta(dia):
                contados += 1
                inicio = inicio or dia
            else:
                fora_da_conta(dia)
        vencimento = dia
    else:
        considerada = base
        dia = considerada + timedelta(days=1)
        while rito != "corridos" and not conta(dia):  # Súmula 310 do STF: começa no primeiro dia útil
            fora_da_conta(dia)
            dia += timedelta(days=1)
        inicio, contados = dia, 1
        while contados < dias:
            dia += timedelta(days=1)
            if suspender and suspenso(dia):
                usou_suspensao = True
                continue
            contados += 1
        vencimento = dia
        while not conta(vencimento):  # art. 798, § 3º, do CPP; art. 132, § 1º, do Código Civil
            fora_da_conta(vencimento)
            vencimento += timedelta(days=1)
        if vencimento != dia:
            prorrogado_de = dia
    return Calculo(dias=dias, rito=regra, intimacao=intimacao, considerada=considerada, inicio=inicio,
                   vencimento=vencimento, publicacao=publicacao, pulados=pulados, suspensao=usou_suspensao,
                   intimacao_na_suspensao=suspender and suspenso(considerada), prorrogado_de=prorrogado_de)


def dias_uteis_entre(de: date, ate: date, calendario: Calendario, suspender: bool = True) -> int:
    """Quantos dias úteis depois de `de` até `ate`, inclusive (0 se `ate` não for depois)."""
    total, dia = 0, de
    while dia < ate:
        dia += timedelta(days=1)
        if calendario.dia_util(dia) and not (suspender and suspenso(dia)):
            total += 1
    return total


def criar_calendario() -> Calendario:
    """O calendário com os feriados do .env (config.py)."""
    from config import CAMINHO_PRAZOS, PRAZOS_FERIADOS_EXTRAS, PRAZOS_FERIADOS_FORENSES
    return Calendario(forenses=PRAZOS_FERIADOS_FORENSES, extras=PRAZOS_FERIADOS_EXTRAS, caminho=CAMINHO_PRAZOS)
