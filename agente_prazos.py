"""Agente de prazos processuais: "fui intimado hoje, quando vence a contestação?", em português, sem LLM.

`AgentePrazos.responder(texto)` devolve uma `RespostaPrazo` (a fala curta e o texto completo para o painel e o
WhatsApp) ou None quando a frase não é sobre prazo, e o roteamento segue. Reconhece:
- o prazo de uma peça ou recurso (`prazos.TIPOS`: contestação, apelação, embargos de declaração, recurso ordinário,
  RESE...) ou "N dias úteis/corridos", com a data da intimação, da publicação ou da disponibilização no DJe;
- o rito pela frase ("trabalhista", "penal", "juizado", "réu preso") e o prazo em dobro (Fazenda, MP, Defensoria,
  litisconsortes com advogados diferentes em autos físicos), com o artigo de cada regra;
- "dia 31 de outubro não tem expediente" (guardado no calendário), "esquece o dia 31 sem expediente" e
  "quais feriados você considera?".
Sem a data, pergunta "De quando é a intimação?"; sem a peça, "Qual é o prazo?". Depois da conta, pergunta se marca
na agenda local (a tarefa vence no dia, com um lembrete 2 dias úteis antes); "... e marca na agenda" marca direto.
As perguntas valem por `SEGUNDOS_PENDENCIA`. Testes em test_prazos.py.
"""
import logging
import re
import time as relogio_do_sistema
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from agenda_linguagem import NOMES_DIAS, NOMES_MESES, NUM, interpretar, juntar, numero, preparar
from prazos import (CORRIDOS, EQUIVALENTES, RITOS, Calculo, Calendario, TipoDePrazo, calcular, dias_uteis_entre, suspenso,
                    tipo_pela_chave, tipos_citados)

logger = logging.getLogger(__name__)

SEGUNDOS_PENDENCIA = 180  # dá tempo de responder pelo WhatsApp
DIAS_UTEIS_DE_AVISO = 2  # o lembrete na agenda, antes do vencimento
DIAS_NO_PASSADO = 90  # uma intimação dita no passado que cairia no futuro volta até isso

# ------------------------------------------------------------------ reconhecimento (texto sem acentos)
RE_PRAZO = re.compile(r"\bprazos?\b")
RE_VENCE = re.compile(r"\b(?:vence|vencem|vencimento|venceu|termina|acaba)\b")
RE_ATE_QUANDO = re.compile(r"\bate quando\b|\bate que dia\b|\bultimo dia\b|\bdata limite\b")
RE_CONTAR = re.compile(r"\b(?:calcul\w*|cont[ae]\w*|conta)\b")
RE_INTIMACAO = re.compile(r"\bintima\w*|\bcitad[oa]s?\b|\bcitacao\b|\bpublica(?:d[oa]s?|cao)\b|\bdisponibiliza\w*"
                          r"|\bciencia\b|\bjuntad[oa]\b|\bdje\b|\bdiario (?:da justica|oficial)\b|\bnotificad[oa]s?\b")
RE_N_DIAS = re.compile(rf"\b(?P<n>{NUM})\s+dias?(?:\s+(?P<tipo>uteis|corridos))?\b")
RE_FORA = re.compile(r"\bprescri\w*|\bdecaden\w*|\bvalidade\b|\bentreg\w*|\bboleto\b|\bfatura\b|\bmandado de seguranca\b"
                     r"|\bgarantia\b|\bdevolu\w*|\barrependimento\b")

RE_PENAL = re.compile(r"\bpena(?:l|is)\b|\bcriminal\b|\bcrimes?\b|\bacusad[oa]s?\b|\bdenuncia\b|\bcpp\b|\bjuri\b"
                      r"|\bexecucao penal\b|\bmaria da penha\b|\bpres[oa]s?\b|\bprisao\b")
RE_TRABALHO = re.compile(r"\btrabalhist\w*|\bclt\b|\breclamante\b|\breclamad[ao]\b|\bvara do trabalho\b|\btrt\b"
                         r"|\btst\b|\bjustica do trabalho\b")
RE_JUIZADO = re.compile(r"\bjuizados?\b|\bjecc?\b|\bpequenas causas\b")
RE_JECRIM = re.compile(r"\bjecrim\b|\bjuizado (?:especial )?criminal\b")

RE_DOBRO = re.compile(r"\bem dobro\b|\bdobrad[oa]\b|\bprazo duplo\b")
RE_FAZENDA = re.compile(r"\bfazenda\b|\bente publico\b|\badvocacia publica\b|\bprocurador(?:ia)? d[oa] (?:estado|municipio|uniao)\b"
                        r"|\bagu\b|\bpgfn\b|\binss\b|\bautarquia\b|\bmunicipio\b|\bpela uniao\b|\ba uniao\b")
RE_MP = re.compile(r"\bministerio publico\b|\bpromotor\w*|\bprocurador da republica\b|\bmp\b")
RE_DEFENSORIA = re.compile(r"\bdefensori\w*|\bdefensor public\w*|\bnucleo de pratica\b|\bescritorio modelo\b")
RE_LITISCONSORTES = re.compile(r"\blitisconsort\w*")
RE_AUTOS_FISICOS = re.compile(r"\b(?:autos|processo) fisicos?\b|\bde papel\b")
RE_SEM_RECESSO = re.compile(r"\bpres[oa]s?\b|\bprisao\b|\bmaria da penha\b|\burgente\b")
RE_DISPONIBILIZACAO = re.compile(r"\bdisponibiliza\w*")
RE_PUBLICADO_NO_DJE = re.compile(r"\bpublica(?:d[oa]|cao)\b.*\b(?:dje|diario)\b|\b(?:dje|diario)\b.*\bpublica(?:d[oa]|cao)\b")
RE_MARCAR = re.compile(r"\b(?:marc\w*|anot\w*|coloc\w*|poe|bota|salv\w*|agend\w*)\b.*\bagenda\b|\bagenda\w*\b.*\bvencimento\b")

RE_PASSADO = re.compile(r"\b(?:fui|foi|fomos|foram|recebi|recebemos|saiu|tomei|tomou|passad[oa]|ultim[oa])\b"
                        r"|\b(?:intimad|publicad|disponibilizad|citad|juntad|notificad)[oa]s?\b")
RE_FUTURO = re.compile(r"\b(?:vai|vou|vamos|vao|sera|serei|for|formos|forem)\b")
RE_DIA_DA_SEMANA = re.compile(r"\b(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)\b")
RE_COM_MES = re.compile(r"\b\d{1,2}\s+de\s+(?:janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro"
                        r"|novembro|dezembro)\b|\b\d{1,2}/\d{1,2}\b")
RE_ANO = re.compile(r"\b\d{4}\b|\b\d{1,2}/\d{1,2}/\d{2}\b")

RE_SEM_EXPEDIENTE = re.compile(r"\b(?:nao (?:tem|tera|teve|houve|vai ter|ha|havera)|sem) expediente\b"
                               r"|\bferiado (?:local|forense|no tribunal|do tribunal)\b|\bponto facultativo\b"
                               r"|\bprazos? (?:suspens\w*|nao corr\w*)\b|\bsuspens\w* (?:d[oa]s? )?prazos?\b")
RE_DESMARCAR = re.compile(r"\b(?:esquece|esqueca|tira|tire|apaga|apague|remov\w*|desmarc\w*)\b")
RE_LISTAR_FERIADOS = re.compile(r"\b(?:quais|que|lista\w*|mostr\w*|considera\w*|proximos?)\b.*\bferiados?\b"
                                r"|\bferiados?\b.*\b(?:forenses?|tribunal|prazos?|considera\w*)\b")

RE_SIM = re.compile(r"^(?:sim|pode|pode sim|marca|marque|isso|claro|quero|ok|beleza|por favor|pode marcar)\b")
RE_NAO = re.compile(r"^(?:nao|nem precisa|deixa|deixa pra la|agora nao|nao precisa)\b")
RE_CNJ = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
RE_CASO = re.compile(r"\b(?:processo|caso|cliente)\s+(?:do |da |de )?(?P<nome>[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ú.-]+(?: [A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ú.-]+)?)")


@dataclass
class RespostaPrazo:
    fala: str  # curta, para a voz
    painel: str = ""  # a fala e os detalhes, para o painel e o WhatsApp
    agenda_mudou: bool = False

    def __str__(self):
        return self.painel or self.fala


@dataclass
class Pedido:
    """O que foi entendido da frase (pode faltar a data ou o prazo, perguntados depois)."""
    tipo: TipoDePrazo | None = None
    dias: int | None = None
    rito: str = "civel"
    data: date | None = None
    disponibilizacao: bool = False
    publicado_no_dje: bool = False
    dobro: bool = False
    fundamento_dobro: str = ""
    avisos: list = field(default_factory=list)
    suspender: bool = True
    motivo_sem_suspensao: str = ""
    caso: str = ""
    marcar: bool = False
    texto: str = ""  # a frase (original e sem acentos), para juntar com a resposta de uma pergunta
    t: str = ""


@dataclass
class Pendencia:
    tipo: str  # data | prazo | agenda
    dados: dict
    ate: float


# ------------------------------------------------------------------ texto falado
def _dia_e_mes(dia: date, hoje: date) -> str:
    return f"{dia.day} de {NOMES_MESES[dia.month - 1]}" + (f" de {dia.year}" if dia.year != hoje.year else "")


def _quando(dia: date, hoje: date) -> str:
    """hoje, ontem, amanhã ou "29 de outubro"."""
    return {0: "hoje", -1: "ontem", 1: "amanhã"}.get((dia - hoje).days) or _dia_e_mes(dia, hoje)


def _quando_forte(dia: date, hoje: date) -> str:
    """O vencimento: "quinta-feira, 19 de novembro" (e "hoje" ou "amanhã" na frente, se for)."""
    base = f"{NOMES_DIAS[dia.weekday()]}, {_dia_e_mes(dia, hoje)}"
    perto = {0: "hoje", 1: "amanhã"}.get((dia - hoje).days)
    return f"{perto}, {base}" if perto else base


def _curta(dia: date) -> str:
    return f"{dia.day:02d}/{dia.month:02d}/{dia.year} ({NOMES_DIAS[dia.weekday()]})"


def _maiuscula(texto: str) -> str:
    return texto[:1].upper() + texto[1:]


def _de(nome: str) -> str:
    """"a contestação" -> "da contestação", "os embargos" -> "dos embargos"."""
    artigo, resto = nome.split(" ", 1)
    return f"d{artigo} {resto}"


ROTULOS_DOS_RITOS = {"civel": "cível (CPC)", "trabalhista": "trabalhista (CLT)",
                     "juizado": "juizado especial cível (Lei 9.099)", "penal": "penal (CPP)",
                     "jecrim": "juizado especial criminal (Lei 9.099 e CPP)", "corridos": "dias corridos (Código Civil)"}


class AgentePrazos:
    def __init__(self, calendario: Calendario | None = None, agenda=None, relogio=datetime.now):
        self.calendario = calendario or Calendario()
        self._agenda = agenda  # função que devolve o agenda_local.Agenda (os testes trocam o banco do main)
        self.agora = relogio
        self.pendencia: Pendencia | None = None
        self.ultimo: tuple[Pedido, Calculo] | None = None

    def esperando_resposta(self) -> bool:
        return bool(self.pendencia and self.pendencia.ate > relogio_do_sistema.time())

    # ================================================================== entrada
    def responder(self, texto: str) -> RespostaPrazo | None:
        texto = (texto or "").strip()
        if not texto:
            return None
        try:
            return self._responder(texto)
        except Exception as exc:
            logger.exception("Falha no agente de prazos")
            return RespostaPrazo(f"Tive um problema ao calcular o prazo: {exc}")

    def _responder(self, texto: str) -> RespostaPrazo | None:
        original, t = preparar(texto)
        chamada = re.match(r"^(?:beatriz|beatris|biatriz)\s*[,!.]?\s*", t)
        if chamada:
            original, t = original[chamada.end():], t[chamada.end():]
        pergunta = original.rstrip().endswith("?")
        original, t = original.strip(" .!?"), t.strip(" .!?")
        if not t:
            return None

        if self.esperando_resposta():
            resposta = self._continuar(original, t)
            if resposta is not None:
                return resposta
        self.pendencia = None

        if not pergunta and (RE_SEM_EXPEDIENTE.search(t)
                             or (RE_DESMARCAR.search(t) and re.search(r"\bexpediente\b|\bferiado\b", t))):
            resposta = self._dia_sem_expediente(original, t)
            if resposta:
                return resposta
        achados = tipos_citados(t)
        if RE_LISTAR_FERIADOS.search(t) and not achados and not RE_INTIMACAO.search(t):
            return self._listar_feriados()
        if not self._e_comigo(t, achados):
            return None
        pedido = self._entender(original, t, achados)
        if isinstance(pedido, RespostaPrazo):
            return pedido
        return self._seguir(pedido)

    def _e_comigo(self, t: str, achados) -> bool:
        if RE_FORA.search(t):
            return False
        pergunta = RE_PRAZO.search(t) or RE_VENCE.search(t) or RE_ATE_QUANDO.search(t)
        if achados:
            return bool(pergunta or (RE_INTIMACAO.search(t) and RE_CONTAR.search(t)))
        dias = RE_N_DIAS.search(t)
        if dias and dias.group("tipo"):  # "conta 15 dias úteis a partir de hoje"
            return bool(pergunta or RE_CONTAR.search(t) or RE_INTIMACAO.search(t) or re.search(r"\ba partir\b", t))
        # "prazo de 10 dias, intimado ontem" ou "fui intimado hoje, qual o prazo?"
        return bool(RE_PRAZO.search(t) and RE_INTIMACAO.search(t))

    # ================================================================== entender
    def _rito_da_frase(self, t: str) -> str | None:
        if RE_JECRIM.search(t) or (RE_JUIZADO.search(t) and RE_PENAL.search(t)):
            return "jecrim"
        if RE_JUIZADO.search(t):
            return "juizado"
        if RE_TRABALHO.search(t):
            return "trabalhista"
        if RE_PENAL.search(t):
            return "penal"
        return None

    def _entender(self, original: str, t: str, achados) -> Pedido | RespostaPrazo:
        pedido = Pedido(texto=original, t=t)
        rito = self._rito_da_frase(t)
        if achados:
            candidatos = [a for a in achados if a[0].rito == rito] if rito else (
                [a for a in achados if a[0].rito == "civel"] or achados)
            citado = max(achados, key=lambda a: a[1])[0]
            equivalente = EQUIVALENTES.get((rito, citado.chave))
            if not candidatos and equivalente:
                candidatos = [(tipo_pela_chave(equivalente[0], rito), 0)]
                pedido.avisos.append(equivalente[1])
            if not candidatos:
                nome = citado.nome
                return RespostaPrazo(f"Não tenho o prazo {_de(nome)} {RITOS[rito].nome}. "
                                     "Diga quantos dias são que eu conto.")
            pedido.tipo = max(candidatos, key=lambda a: a[1])[0]
            pedido.rito = pedido.tipo.rito
        dias = RE_N_DIAS.search(t)
        if dias and numero(dias.group("n")):
            pedido.dias = numero(dias.group("n"))
            if not pedido.tipo:
                if dias.group("tipo") == "corridos" and rito not in ("penal", "jecrim"):
                    rito = "corridos"
                pedido.rito = rito or "civel"
        elif not pedido.tipo:
            pedido.rito = rito or "civel"
        self._ler_dobro(t, pedido)
        if pedido.rito in ("penal", "jecrim") and RE_SEM_RECESSO.search(t):
            pedido.suspender = False
            pedido.motivo_sem_suspensao = ("réu preso" if re.search(r"\bpres[oa]s?\b|\bprisao\b", t) else
                                           "Lei Maria da Penha" if "maria da penha" in t else "medida urgente")
        pedido.disponibilizacao = bool(RE_DISPONIBILIZACAO.search(t))
        pedido.publicado_no_dje = not pedido.disponibilizacao and bool(RE_PUBLICADO_NO_DJE.search(t))
        pedido.marcar = bool(RE_MARCAR.search(t))
        pedido.data = self._data_da_intimacao(original, t)
        cnj, caso = RE_CNJ.search(original), RE_CASO.search(original)
        pedido.caso = cnj.group(0) if cnj else (caso.group("nome") if caso else "")
        return pedido

    def _ler_dobro(self, t: str, pedido: Pedido):
        rito, pedido.dobro = pedido.rito, False
        if rito in ("penal", "jecrim"):
            if RE_DEFENSORIA.search(t):
                pedido.dobro, pedido.fundamento_dobro = True, "pela Defensoria Pública (LC 80/94, arts. 44, I, 89, I, e 128, I)"
            elif RE_MP.search(t):
                pedido.avisos.append("No processo penal o Ministério Público não tem prazo em dobro.")
        elif rito == "juizado" and (RE_FAZENDA.search(t) or RE_MP.search(t)):
            pedido.avisos.append("Nos juizados não há prazo diferenciado para o poder público (Lei 12.153, art. 7º; "
                                 "Lei 10.259, art. 9º): contei o prazo simples.")
        elif rito == "trabalhista" and RE_FAZENDA.search(t):
            if not pedido.tipo or pedido.tipo.recurso:
                pedido.dobro, pedido.fundamento_dobro = True, "pela Fazenda Pública (art. 1º, III, do Decreto-Lei 779/69)"
        elif RE_FAZENDA.search(t):
            pedido.dobro, pedido.fundamento_dobro = True, "pela Fazenda Pública (art. 183 do CPC)"
        elif RE_MP.search(t):
            pedido.dobro, pedido.fundamento_dobro = True, "pelo Ministério Público (art. 180 do CPC)"
        elif RE_DEFENSORIA.search(t):
            pedido.dobro, pedido.fundamento_dobro = True, "pela Defensoria Pública (art. 186 do CPC)"
            if rito == "juizado":
                pedido.avisos.append("Nos juizados, confira se a turma recursal aplica o prazo em dobro.")
        elif RE_LITISCONSORTES.search(t):
            if RE_AUTOS_FISICOS.search(t):
                pedido.dobro, pedido.fundamento_dobro = True, "pelos litisconsortes com advogados diferentes (art. 229 do CPC)"
            else:
                pedido.avisos.append("Litisconsortes com advogados diferentes só têm prazo em dobro em autos físicos "
                                     "(art. 229, § 2º, do CPC): contei o prazo simples.")
        if not pedido.dobro and RE_DOBRO.search(t):
            pedido.dobro, pedido.fundamento_dobro = True, ""

    def _data_da_intimacao(self, original: str, t: str) -> date | None:
        """A data dita. Com o verbo no passado ("fui intimado sexta", "publicado dia 20"), uma data que cairia no
        futuro volta para a última vez que ela aconteceu (a agenda entende "sexta" como a próxima), se foi há até
        `DIAS_NO_PASSADO` dias: "intimado dia 17 de dezembro", dito em outubro, é o que vem."""
        limpo = RE_N_DIAS.sub(lambda m: " " * len(m.group(0)), t)  # "em 15 dias" não é a data
        limpo_original = "".join(o if l != " " or c == " " else " " for o, c, l in zip(original, t, limpo))
        hoje = self.agora().date()
        dia = interpretar(limpo_original, self.agora()).data
        if dia is None or dia <= hoje or not RE_PASSADO.search(t) or RE_FUTURO.search(t):
            return dia
        if RE_ANO.search(t):
            return dia
        passado = dia
        if RE_DIA_DA_SEMANA.search(t):
            while passado > hoje:
                passado -= timedelta(days=7)
        elif RE_COM_MES.search(t):
            try:
                passado = dia.replace(year=dia.year - 1)
            except ValueError:
                passado = dia.replace(year=dia.year - 1, day=28)
        else:  # "dia 20": o dia 20 deste mês
            mes, ano = (dia.month - 1, dia.year) if dia.month > 1 else (12, dia.year - 1)
            try:
                passado = dia.replace(year=ano, month=mes)
            except ValueError:
                return dia
        return passado if (hoje - passado).days <= DIAS_NO_PASSADO else dia

    # ================================================================== conta e resposta
    def _seguir(self, pedido: Pedido) -> RespostaPrazo:
        if not pedido.tipo and not pedido.dias:
            pergunta = "Qual é o prazo: contestação, apelação, embargos de declaração...? Ou me diga quantos dias."
            return RespostaPrazo(self._perguntar("prazo", pergunta, pedido=pedido))
        if pedido.data is None:
            inicio = self._cabecalho(pedido)
            return RespostaPrazo(self._perguntar("data", f"{inicio} De quando é a intimação?", pedido=pedido))
        return self._calcular_e_responder(pedido)

    def _perguntar(self, tipo: str, pergunta: str, **dados) -> str:
        self.pendencia = Pendencia(tipo, dados, relogio_do_sistema.time() + SEGUNDOS_PENDENCIA)
        return pergunta

    def _dias(self, pedido: Pedido) -> int:
        base = pedido.dias or pedido.tipo.dias
        return base * 2 if pedido.dobro else base

    def _cabecalho(self, pedido: Pedido) -> str:
        """"A contestação: 15 dias úteis (art. 335 do CPC)." ou "Prazo de 10 dias úteis.\""""
        rito = RITOS[pedido.rito]
        unidade = "dias úteis" if rito.contagem != CORRIDOS else "dias corridos"
        dias = self._dias(pedido)
        dobro = (", em dobro" + (f" {pedido.fundamento_dobro}" if pedido.fundamento_dobro else "")) if pedido.dobro else ""
        if pedido.tipo:
            fundamento = f" ({pedido.tipo.fundamento})" if pedido.dias in (None, pedido.tipo.dias) else ""
            return f"{_maiuscula(pedido.tipo.nome)}: {dias} {unidade}{fundamento}{dobro}."
        return f"Prazo de {dias} {unidade}{dobro}."

    def _calcular_e_responder(self, pedido: Pedido) -> RespostaPrazo:
        hoje = self.agora().date()
        intimacao = pedido.data
        anterior = None
        if pedido.tipo and pedido.tipo.depois_de:  # a impugnação começa quando acaba o prazo do pagamento
            antes = tipo_pela_chave(pedido.tipo.depois_de, pedido.rito)
            anterior = (antes, calcular(intimacao, antes.dias * (2 if pedido.dobro else 1), pedido.rito,
                                        calendario=self.calendario, disponibilizacao=pedido.disponibilizacao,
                                        suspender=pedido.suspender))
            intimacao = anterior[1].vencimento
        calculo = calcular(intimacao, self._dias(pedido), pedido.rito, calendario=self.calendario,
                           disponibilizacao=pedido.disponibilizacao and not anterior, suspender=pedido.suspender)
        self.ultimo = (pedido, calculo)

        partes = [self._cabecalho(pedido)]
        if anterior:
            partes.append(f"Ela começa quando acaba o prazo {_de(anterior[0].nome)}, "
                          f"que vence {_quando(anterior[1].vencimento, hoje)}.")
        elif calculo.publicacao:
            partes.append(f"Disponibilizado {_quando(pedido.data, hoje)}, a publicação é {_quando(calculo.publicacao, hoje)}.")
        elif calculo.considerada != pedido.data:
            partes.append(f"A intimação {_quando(pedido.data, hoje)} caiu sem expediente e vale "
                          f"{_quando(calculo.considerada, hoje)}.")
        partes.append(f"A contagem começa {_quando(calculo.inicio, hoje)} e vence {_quando_forte(calculo.vencimento, hoje)}.")
        feriados = [f"{d.day}/{d.month} ({motivo})" for d, motivo in calculo.pulados]
        if feriados:
            partes.append(f"Não contei {juntar(feriados)}.")
        if calculo.suspensao:
            partes.append("A suspensão de 20 de dezembro a 20 de janeiro ficou fora da conta.")
        if calculo.prorrogado_de:
            partes.append(f"O último dia caía em {_quando(calculo.prorrogado_de, hoje)}, sem expediente, e passou "
                          "para o dia útil seguinte.")
        if calculo.vencimento < hoje:
            partes.append("Esse prazo já venceu.")
        elif calculo.rito.contagem != CORRIDOS:
            faltam = dias_uteis_entre(hoje, calculo.vencimento, self.calendario)
            if faltam == 1:
                partes.append("Falta 1 dia útil.")
            elif faltam > 1:
                partes.append(f"Faltam {faltam} dias úteis.")
        fala = " ".join(partes) + " Confira os feriados locais do tribunal."

        detalhes = self._detalhes(pedido, calculo, anterior)
        agenda_mudou = False
        if calculo.vencimento >= hoje and self._agenda:
            if pedido.marcar:
                marcado = self._marcar(pedido, calculo)
                fala += f" {marcado}"
                agenda_mudou = True
            else:
                fala += " " + self._perguntar("agenda", "Quer que eu marque na agenda?", pedido=pedido, calculo=calculo)
        return RespostaPrazo(fala, f"{fala}\n\n{detalhes}", agenda_mudou)

    def _detalhes(self, pedido: Pedido, calculo: Calculo, anterior) -> str:
        rito = calculo.rito
        linhas = [f"Rito: {ROTULOS_DOS_RITOS[rito.chave]}"]
        if rito.contagem == CORRIDOS:
            regra = f"dias corridos ({rito.fundamento_contagem}), sem o dia do começo"
            if rito.chave != "corridos":
                regra += "; começa no primeiro dia útil (Súmula 310 do STF) e conta da intimação, não da juntada (Súmula 710 do STF)"
        elif rito.chave == "trabalhista":
            regra = f"só dias úteis, sem o dia do começo ({rito.fundamento_contagem})"
        else:
            regra = f"só dias úteis ({rito.fundamento_contagem}), sem o dia do começo (art. 224 do CPC)"
        linhas.append(f"Contagem: {regra}")
        if anterior:
            linhas.append(f"{_maiuscula(anterior[0].nome)} ({anterior[0].fundamento}): vence {_curta(anterior[1].vencimento)}")
        else:
            linhas.append(f"{'Disponibilização no DJe' if calculo.publicacao else 'Intimação'}: {_curta(pedido.data)}")
            if calculo.publicacao:
                linhas.append(f"Publicação: {_curta(calculo.publicacao)} (art. 224, § 2º, do CPC)")
        linhas.append(f"Primeiro dia contado: {_curta(calculo.inicio)}")
        linhas.append(f"Vencimento: {_curta(calculo.vencimento)}")
        if calculo.pulados:
            linhas.append("Fora da conta: " + "; ".join(f"{d:%d/%m} ({m})" for d, m in calculo.pulados))
        if pedido.caso:
            linhas.append(f"Processo: {pedido.caso}")

        avisos = list(pedido.avisos)
        if rito.fundamento_suspensao:
            if not pedido.suspender:
                avisos.append(f"Sem a suspensão de 20/12 a 20/1: {pedido.motivo_sem_suspensao} (art. 798-A do CPP).")
            elif calculo.suspensao:
                avisos.append(f"De 20/12 a 20/1 o prazo fica suspenso ({rito.fundamento_suspensao}).")
            if calculo.intimacao_na_suspensao:
                avisos.append("A intimação caiu na suspensão: contei a partir do primeiro dia útil depois de 20/1. "
                              "Há tribunal que começa a contar um dia depois; confira.")
        if pedido.publicado_no_dje and not calculo.publicacao and not anterior:
            alternativa = calcular(pedido.data, self._dias(pedido), pedido.rito, calendario=self.calendario,
                                   disponibilizacao=True, suspender=pedido.suspender)
            avisos.append(f"Contei {pedido.data:%d/%m} como a publicação. Se foi a disponibilização no DJe, a publicação "
                          f"é o dia útil seguinte e o prazo vence em {_curta(alternativa.vencimento)}.")
        if pedido.tipo and pedido.tipo.chave == "contestacao":
            avisos.append("O começo da contestação depende da audiência de conciliação ou de como foi a citação "
                          "(arts. 231 e 335 do CPC): usei a data que você disse.")
        if pedido.tipo and pedido.tipo.recurso and rito.chave in ("civel", "juizado"):
            avisos.append("Feriado local precisa ser comprovado no ato da interposição do recurso (art. 1.003, § 6º, do CPC).")
        avisos.append("Feriados locais, indisponibilidade do sistema e portarias do tribunal mudam a conta: confira no "
                      "tribunal. Para eu contar um dia sem expediente, diga \"dia X não tem expediente\".")
        return "\n".join(linhas) + "\n\nAvisos:\n" + "\n".join(f"- {a}" for a in avisos)

    # ================================================================== agenda
    def _marcar(self, pedido: Pedido, calculo: Calculo) -> str:
        agenda = self._agenda()
        nome = _maiuscula(pedido.tipo.nome.split(" ", 1)[1]) if pedido.tipo else f"Prazo de {self._dias(pedido)} dias"
        caso = f" ({pedido.caso})" if pedido.caso else ""
        titulo = f"Prazo: {nome}{caso}"
        if any(t.titulo == titulo and t.prazo == calculo.vencimento for t in agenda.tarefas()):
            return "Já estava na agenda."
        agenda.criar_tarefa(titulo, prazo=calculo.vencimento, prioridade=1, duracao=60)
        hoje = self.agora().date()
        aviso = calculo.vencimento
        for _ in range(DIAS_UTEIS_DE_AVISO):
            aviso -= timedelta(days=1)
            while not self.calendario.dia_util(aviso) or suspenso(aviso):
                aviso -= timedelta(days=1)
        fundamento = pedido.tipo.fundamento if pedido.tipo else ""
        texto = f"Marquei na agenda: a tarefa vence {_quando(calculo.vencimento, hoje)}"
        if aviso > hoje:
            agenda.criar_evento(f"Faltam {DIAS_UTEIS_DE_AVISO} dias úteis: {nome}{caso} (vence {calculo.vencimento:%d/%m})",
                                datetime.combine(aviso, time()), dia_inteiro=True, tipo="lembrete", categoria="trabalho",
                                lembretes=[0], notas=fundamento)
            texto += f", e eu aviso {_quando(aviso, hoje)}, {DIAS_UTEIS_DE_AVISO} dias úteis antes"
        return texto + "."

    # ================================================================== continuação
    def _continuar(self, original: str, t: str) -> RespostaPrazo | None:
        pendencia = self.pendencia
        if pendencia.tipo == "agenda":
            if RE_SIM.search(t):
                self.pendencia = None
                return RespostaPrazo(self._marcar(pendencia.dados["pedido"], pendencia.dados["calculo"]), agenda_mudou=True)
            if RE_NAO.search(t):
                self.pendencia = None
                return RespostaPrazo("Certo, não marquei.")
            return None
        pedido: Pedido = pendencia.dados["pedido"]
        if pendencia.tipo == "data":  # a resposta a "De quando é a intimação?"
            if tipos_citados(t) and RE_PRAZO.search(t):
                return None  # um pedido novo de prazo
            if interpretar(original, self.agora()).data is None:
                return None
            juntos, juntos_t = f"{pedido.texto} foi {original}", f"{pedido.t} foi {t}"
        else:  # a resposta a "Qual é o prazo?": "contestação", "dez dias", "15"
            if re.fullmatch(NUM, t):
                original, t = f"{original} dias", f"{t} dias"
            if not tipos_citados(t) and not RE_N_DIAS.search(t):
                return None
            juntos, juntos_t = f"{pedido.texto} {original}", f"{pedido.t} {t}"
        novo = self._entender(juntos, juntos_t, tipos_citados(juntos_t))
        self.pendencia = None
        return novo if isinstance(novo, RespostaPrazo) else self._seguir(novo)

    # ================================================================== calendário
    def _dia_sem_expediente(self, original: str, t: str) -> RespostaPrazo | None:
        dia = interpretar(original, self.agora()).data
        if dia is None:
            return None
        hoje = self.agora().date()
        if RE_DESMARCAR.search(t):
            if self.calendario.desmarcar(dia):
                return RespostaPrazo(f"Pronto: {_quando(dia, hoje)} volta a contar como dia útil.")
            return RespostaPrazo(f"Eu não tinha {_quando(dia, hoje)} marcado como dia sem expediente.")
        ja = self.calendario.motivo(dia)
        if ja and ja != "fim de semana":
            return RespostaPrazo(f"{_maiuscula(_quando(dia, hoje))} eu já não conto: {ja}.")
        motivo = "ponto facultativo" if "ponto facultativo" in t else (
            "prazos suspensos" if re.search(r"\bsuspens\w*\b", t) else "sem expediente")
        self.calendario.marcar_sem_expediente(dia, motivo)
        return RespostaPrazo(f"Anotado: {_quando(dia, hoje)} sem expediente. Vale para as próximas contas de prazo.")

    def _listar_feriados(self) -> RespostaPrazo:
        hoje = self.agora().date()
        fim = hoje + timedelta(days=90)
        dias = {}
        for ano in range(hoje.year, fim.year + 1):
            dias.update(self.calendario.feriados(ano))
        proximos = sorted((d, m) for d, m in dias.items() if hoje <= d <= fim and d.weekday() < 5)
        if proximos:
            fala = "Nos próximos três meses eu não conto " + juntar(
                f"{d.day}/{d.month} ({m})" for d, m in proximos[:6]) + (" e mais alguns" if len(proximos) > 6 else "") + "."
        else:
            fala = "Nos próximos três meses não há feriado em dia de semana."
        if any(suspenso(hoje + timedelta(days=n)) for n in range(91)):
            fala += " E os prazos ficam suspensos de 20 de dezembro a 20 de janeiro."
        do_ano = sorted(self.calendario.feriados(hoje.year).items())
        painel = "\n".join(f"{_curta(d)}: {m}" for d, m in do_ano)
        marcados = self.calendario.dias_marcados()
        extras = ("\n\nDias que você marcou: " + "; ".join(f"{d:%d/%m/%Y} ({m})" for d, m in sorted(marcados.items()))
                  if marcados else "")
        return RespostaPrazo(fala, f"{fala}\n\nDias sem expediente de {hoje.year}:\n{painel}{extras}")


def criar_agente_prazos(agenda=None) -> AgentePrazos:
    """O agente com o calendário do .env; `agenda` é uma função que devolve o agenda_local.Agenda."""
    from prazos import criar_calendario
    return AgentePrazos(criar_calendario(), agenda=agenda)
