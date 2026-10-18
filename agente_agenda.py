"""Agente de agenda da Beatriz: compromissos, rotina, lembretes e tarefas, por conversa em português.

Tudo fica no PC (`agenda_local.Agenda`, SQLite em banco_memoria/agenda.sqlite3): nada vai para o
Google nem para a nuvem. A conversa livre sobre a agenda ("tenho tempo de ir ao mercado essa
semana?") usa só o Ollama local.

`AgenteAgenda.responder(texto)` devolve a resposta (para falar ou escrever) ou None quando a frase
não é com a agenda, e o roteamento segue. Perguntas de volta ("Para quando?", "Marco mesmo assim?",
"Só essa ou todas?", "Coloco na agenda?") ficam guardadas como pendência e valem para a próxima
frase por `SEGUNDOS_PENDENCIA`. "Desfaz" volta a última mudança.

`verificar_lembretes()` devolve os avisos que venceram (compromissos, lembretes e tarefas do dia);
`iniciar_lembretes` roda isso numa thread, só no processo principal.
"""
import difflib
import logging
import re
import threading
import time as relogio_do_sistema
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta

from agenda_linguagem import (
    CONECTIVOS,
    FAIXA_DO_PERIODO,
    HORA_DO_PERIODO,
    NOMES_DIAS,
    NOMES_MESES,
    Interpretacao,
    falar_dia,
    falar_dia_curto,
    falar_duracao,
    falar_hora,
    falar_recorrencia,
    interpretar,
    juntar,
    limpar_titulo,
    preparar,
    resolver_momento,
    sem_acentos,
    tirar_padroes,
    tirar_trechos,
)
from agenda_local import Agenda, Evento, Ocorrencia, Tarefa

logger = logging.getLogger(__name__)

SEGUNDOS_PENDENCIA = 180  # dá tempo de responder pelo WhatsApp
LIMITE_SEMELHANCA = 0.55
TOLERANCIA_AVISO = timedelta(minutes=15)  # aviso atrasado além disso (PC desligado) não sai mais
INTERVALO_BLOCOS = timedelta(minutes=10)  # folga entre as tarefas encaixadas no "organiza meu dia"

# ------------------------------------------------------------------ reconhecimento (texto sem acentos)
RE_DOCUMENTO = re.compile(r"\b(pdf|word|docx|planilhas?|excel|xlsx|apresentac\w*|slides?|power ?point|pptx|documento)\b")
RE_RESUMO_DO_DIA = re.compile(r"\b(resumo|briefing|panorama) (do|de|pro|para o) (meu )?dia\b|\bresumo de hoje\b")
RE_DESFAZER = re.compile(r"^(desfaz\w*|desfac\w*|volta atras|volte atras)\b")
RE_ABRIR = re.compile(r"^(abr[aei]\w*|mostr\w*|exib\w*|ver|veja)\s+(a |minha |o |meu )?(agenda|calendario)\b"
                      r"|^(a |minha |o |meu )?(agenda|calendario)[.!?]*$")
RE_TAREFA = re.compile(r"\b(tarefas?|afazeres|to-do|pendencias)\b")
RE_LISTAR_TAREFAS = re.compile(
    r"\b(quais|qual|lista\w*|mostr\w*|ver|veja|le|leia|minhas|tenho|pendentes?|falta\w*)\b.*\btarefas\b"
    r"|\btarefas\b.*\b(pendentes?|de hoje|da semana|pra hoje|para hoje|que faltam)\b"
    r"|\bo que (eu )?(tenho|preciso|falta|ainda tenho) (pra|para|que|de) fazer\b|^(minhas )?tarefas\??$"
)
RE_CONCLUIR = re.compile(r"\b(conclu\w*|termin\w*|finaliz\w*|fiz|feit[ao]s?|cumpri|ja paguei|pronta|prontas)\b")
RE_REMOVER = re.compile(r"\b(remov\w*|apag\w*|exclu\w*|tir[ae]|delet\w*|cancel\w*|esquec\w*|desmarc\w*)\b")
RE_ADICIONAR = re.compile(r"\b(adicion\w*|coloc\w*|anot\w*|inclu\w*|bot[ae]|poe|ponha|cri[ae]|nova|novo|registr\w*|acrescent\w*|lembr\w*)\b")

RE_CRIAR = re.compile(
    r"^(?:me\s+)?(?:marc(?:a|ar|ou|ando)\b|marqu(?:e|ei|em)\b|agend(?:e|ar|ei|ou|em|ando)\b|reserv\w*|bloqu\w*)"
    r"|^(?:me\s+)?agenda\s+(?!(?:de|da|do|dos|das|desta|dessa|deste|desse|pra hoje|para hoje|hoje|amanha|semanal|dela|dele)\b)"
    r"|\b(?:coloc\w*|poe|ponha|bot[ae]|adicion\w*|inclu\w*|anot\w*|registr\w*|salv\w*)\b.{0,40}\b(?:na|a|minha) agenda\b"
    r"|\b(?:cri[ae]\w*|nov[oa]|adicion\w*)\s+(?:um\s+|uma\s+)?(?:compromisso|evento|reuniao|lembrete)\b"
)
RE_LEMBRAR = re.compile(r"^(?:me|te)\s+(?:lembr[ae]\w*|avis[ae]\w*|alert[ae]\w*|notifi\w*)\b|^(?:lembr[ae]|avis[ae])[- ]me\b"
                        r"|\bme (?:lembr\w*|avis\w*) (?:de|que|para|pra)\b"
                        r"|^(?:cri[ae]|nov[oa]|coloc\w*|bot[ae]|poe)\s+(?:um\s+)?lembrete\b|^lembrete\b")
# "lembra de comprar pão amanhã" (sem o "me") só vale com quando; "lembra que eu te falei..." é conversa.
RE_LEMBRAR_FRACO = re.compile(r"^(?:lembr[ae]|avis[ae])\b")
# "me lembra de/que/pra ...": lembrete mesmo sem quando (ela pergunta). "me lembra o nome do filme" é outra coisa.
RE_LEMBRAR_ALVO = re.compile(r"^(?:me|te)\s+(?:lembr\w*|avis\w*|alert\w*|notifi\w*)\s+(?:de|que|para|pra|do|da|dos|das)\b"
                             r"|^(?:lembr[ae]|avis[ae])[- ]me\s+(?:de|que|para|pra)\b|\blembrete\b")
RE_CANCELAR = re.compile(r"^(?:me\s+)?(?:cancel\w*|desmarc\w*|apag\w*|remov\w*|exclu\w*|tir[ae]\w*|delet\w*)\b")
RE_REMARCAR = re.compile(r"^(?:me\s+)?(?:remarc\w*|reagend\w*|mud[ae]\w*|transfer\w*|pass[ae]\w*|adi[ae]\w*|antecip\w*|mov[ae]\w*|troc[ae]\w*|empurr\w*|jog[ae]\w*)\b")
RE_REMARCAR_FORTE = re.compile(r"^(?:me\s+)?(?:remarc\w*|reagend\w*|adi[ae]\w*|antecip\w*)\b")
RE_PLANEJAR = re.compile(
    r"\b(organiz\w*|planej\w*|mont\w*|distribu\w*|encaix\w*|arrum\w*)\b.{0,30}"
    r"\b(meu dia|minha rotina|minhas tarefas|o dia|meu amanha|dia de amanha|amanha|minha tarde|minha manha|hoje)\b"
)
RE_ROTINA = re.compile(r"\bminha rotina\b|\brotina (semanal|diaria|da semana)\b|\bo que (se repete|e fixo)\b")
RE_PROXIMO = re.compile(
    r"\bproxim[oa]s? (compromissos?|eventos?|reunio\w*|consultas?|aulas?|lembretes?|horario marcado|coisa)\b"
    r"|\bqual (e )?(o |a )?proxim[oa]\b|\bo que (eu )?tenho agora\b|\bo que vem (agora|depois|a seguir)\b"
)
RE_LIVRE = re.compile(r"\b(livres?|disponive(l|is)|desocupad\w*|vagos?|vaga|folga|tempo livre|horarios? (livres?|vagos?))\b")
RE_PESSOAL_LIVRE = re.compile(r"\b(estou|to|tou|esto|fico|ficarei|tenho|terei|estarei|eu|meu|minha|meus|minhas|quando|que horas|qual horario)\b")
RE_PERGUNTA_DIA = re.compile(
    r"\b(o que|que|quais?|qual)\b.{0,40}\b(marcad\w*|agendad\w*|compromissos?|eventos?|programad\w*)\b"
    r"|\b(minha |a |na )?agenda (de|da|do|pra|para|desta|dessa|deste|desse|hoje|amanha)\b"
    r"|\bmeus compromissos\b|\bminha agenda\b|\bo que tem na (minha )?agenda\b"
    r"|\bcomo (esta|vai ser|sera|ta|fica|ficou) (o |a )?(meu dia|minha semana|minha agenda|meu amanha|meu fim de semana)\b"
    r"|^(e )?(pra |para )?(hoje|amanha|depois de amanha)\??$"
)
# "o que eu tenho amanhã?", "tenho alguma coisa sexta?": só com um dia ou período na frase.
RE_O_QUE_TENHO = re.compile(r"\b(o que|que|quais?|qual)\b.{0,25}\btenho\b|\btenho (algum|alguma|algo|compromisso|evento|reuniao|consulta|aula|coisa)\b")
RE_QUANDO_E = re.compile(
    r"^(?:que horas|quando|qual dia|que dia|qual horario|qual a hora|a que horas)\b.{0,12}?"
    r"\b(?:e|eh|vai ser|sera|tenho|fica|marquei|esta marcad[oa]|ta marcad[oa]|comeca)\s+(?:o |a |meu |minha |os |as |aquel[ea] )?(?P<alvo>.+)$"
)
RE_ASSUNTO_AGENDA = re.compile(
    r"\b(agenda|compromissos?|rotina|tarefas?|calendario|lembretes?)\b"
    r"|\b(estou|to|fico|tenho|terei) (livre|ocupad\w*|tempo)\b"
)
RE_PESSOAL = re.compile(r"\b(minha|meu|meus|minhas|eu|tenho|estou|to|fico|terei)\b")
# Frases sem verbo de comando ("academia toda segunda às 7h", "tenho dentista amanhã às 14h"):
# viram compromisso se não forem pergunta nem assunto de outra função da Beatriz.
RE_PERGUNTA = re.compile(r"\?\s*$|^(?:e\s+)?(?:o que|que|qual|quais|quando|como|onde|quem|quanto\w*|por ?que|sera|ser[aá] que)\b")
RE_OUTRO_ASSUNTO = re.compile(
    r"\b(tempo|clima|previsao|temperatura|chuva|chover|noticias?|manchetes?|filmes?|cinema|sess(?:ao|oes)|cotac\w*|dolar"
    r"|euro|bitcoin|preco|musica|youtube|spotify|e-?mails?|whatsapp|pesquis\w*|procur\w*|resum\w*|traduz\w*|volume"
    r"|horas? (?:sao|e)|data de hoje|que dia e hoje|abr[aei]\w*|fech[ae]\w*|toc[ae]\w*|lig[ae]\w* (?:a|o) (?:luz|tv|som))\b"
)
RE_TENHO_NO_INICIO = re.compile(r"^(?:eu\s+)?(?:tenho|vou ter|terei)\s+(?:(?:um|uma)\s+)?", re.IGNORECASE)
RE_TEM_NO_INICIO = re.compile(r"^(?:vai ter|tem)\s+(?:(?:um|uma)\s+)?", re.IGNORECASE)
RE_COMECA_COM_VERBO = re.compile(
    r"^(?:vai|vou|vamos|esta|estou|estava|ta|to|e|eh|foi|era|sera|seria|fica|ficou|quero|queria|preciso|pode|posso"
    r"|consegue|da|faz|fiz|tava|acho|achei|gosto|gostei|odeio|amo|vi|ouvi|comi|sai|cheguei|chego|acordei|dormi)\b"
)
TITULOS_VAGOS = {"algo", "alguma coisa", "algum compromisso", "nada", "compromisso", "evento", "isso", "aquilo", "coisa"}
# Primeira palavra que indica lembrete (algo a fazer na hora) e não um horário ocupado.
VERBOS_DE_LEMBRETE = {"tomar", "ligar", "pagar", "comprar", "mandar", "enviar", "levar", "buscar", "regar", "tirar",
                      "lembrar", "beber", "renovar", "responder", "devolver", "trocar", "alimentar", "dar"}
# Frase que é um pedido novo, e não a resposta a "Para quando?" ou "O que eu marco?".
RE_PEDIDO_NOVO = re.compile(
    r"\?\s*$|^(?:o que|que|qual|quais|quando|como|onde|quem|por que|porque)\b"
    r"|^(?:me\s+)?(?:marc\w*|agend\w*|cancel\w*|desmarc\w*|remarc\w*|abr\w*|pesquis\w*|procur\w*|toca|coloca|crie|cria|faz|faca)\b"
)

# Palavras de comando que saem do título ("marca", "na agenda", "me lembra de"...).
PADROES_CRIAR = [
    r"^\s*(?:beatriz\s*,?\s*)?(?:por favor\s*,?\s*)?(?:me\s+)?(?:marc\w*|agend[ae]\w*|agenda|reserv\w*|bloqu\w*|coloc\w*|poe|ponha|bot[ae]|adicion\w*|inclu\w*|anot\w*|registr\w*|salv\w*|cri[ae]\w*)\b",
    r"\b(?:pra|para) mim\b",
    r"\b(?:na|a|minha|para a|pra) (?:minha )?agenda\b",
    r"^\s*(?:um|uma|o|a|novo|nova)?\s*(?:compromisso|evento|horario)\b(?:\s+(?:de|do|da|para|pra|com))?",
    r"\bpor favor\b",
]
PADROES_LEMBRAR = [
    r"^\s*(?:beatriz\s*,?\s*)?(?:por favor\s*,?\s*)?(?:cri[ae]\s+|nov[oa]\s+|coloc\w*\s+)?(?:um\s+)?lembrete\b(?:\s+(?:de|para|pra|que))?",
    r"^\s*(?:beatriz\s*,?\s*)?(?:por favor\s*,?\s*)?(?:me\s+|te\s+)?(?:lembr[ae]\w*|avis[ae]\w*|alert[ae]\w*|notifi\w*)\b(?:\s+(?:de|que|para|pra|do|da))?",
    r"\bme (?:lembr\w*|avis\w*)(?:\s+(?:de|que|para|pra))?\b",
    r"^\s*(?:que\s+)?(?:eu\s+)?(?:tenho que|tenho de|preciso|devo)\b",
    r"\bpor favor\b",
]
PADROES_TAREFA = [
    r"^\s*(?:beatriz\s*,?\s*)?(?:por favor\s*,?\s*)?(?:me\s+)?(?:adicion\w*|coloc\w*|anot\w*|inclu\w*|bot[ae]|poe|ponha|cri[ae]\w*|registr\w*|acrescent\w*|lembr\w*)\b",
    r"\b(?:uma\s+|a\s+|nova\s+)?tarefas?\b\s*:?(?:\s+(?:de|para|pra))?",
    r"\b(?:na|nas|a|para a|pra|em) (?:minha\s+)?lista(?: de (?:tarefas|afazeres|coisas))?\b",
    r"\b(?:nas|nas minhas|em) (?:minhas\s+)?(?:tarefas|pendencias)\b",
    r"\b(?:pra|para) mim\b|\bpor favor\b",
]
PADROES_ALVO = [  # para achar o compromisso ou a tarefa de que se fala
    r"^\s*(?:beatriz\s*,?\s*)?(?:por favor\s*,?\s*)?(?:me\s+)?(?:cancel\w*|desmarc\w*|apag\w*|remov\w*|exclu\w*|tir[ae]\w*|delet\w*"
    r"|remarc\w*|reagend\w*|mud[ae]\w*|transfer\w*|pass[ae]\w*|adi[ae]\w*|antecip\w*|mov[ae]\w*|troc[ae]\w*|empurr\w*|jog[ae]\w*"
    r"|conclu\w*|termin\w*|finaliz\w*|fiz|cumpri|marc\w*)\b",
    r"\b(?:o|a|os|as|meu|minha|meus|minhas)?\s*(?:compromissos?|eventos?|lembretes?|tarefas?|horarios?)\b(?:\s+(?:de|do|da|com))?",
    r"\b(?:da|na|de|para a|pra|minha)\s+(?:minha\s+)?(?:agenda|lista|rotina)\b",
    r"\bcomo (?:feit[ao]|conclu\w*|pront[ao])\b|\bja\b|\bpor favor\b|\bpra mim\b|\btodas?\b|\bde vez\b|\bso\b|\bapenas\b",
]
SIM = {"sim", "isso", "pode", "pode sim", "pode marcar", "marca", "marca sim", "confirma", "confirmo", "ok", "okay",
       "beleza", "claro", "quero", "manda", "faz", "faca", "exato", "certo", "positivo", "s", "pode ser", "bora",
       "sim por favor", "sim pode", "coloca", "pode colocar", "isso mesmo", "uhum", "aham", "com certeza", "fechado"}
NAO = {"nao", "n", "nao precisa", "deixa", "deixa pra la", "esquece", "cancela", "negativo", "nao quero", "melhor nao",
       "agora nao", "nada", "nenhum", "nenhuma"}
SO_ESSA = re.compile(r"\b(so|somente|apenas)\b|\b(essa|esta|a proxima|so a proxima|uma vez|desta vez|dessa vez)\b")
TODAS = re.compile(r"\b(todas?|tudo|a serie|de vez|sempre|todos|toda a rotina|para sempre|pra sempre)\b")

CATEGORIAS_POR_PALAVRA = [
    ("trabalho", r"\b(reunia\w*|reuniao|trabalho|cliente|projeto|entrega|call|meet|expediente|escritorio|chefe|empresa|apresentac\w*)\b"),
    ("estudo", r"\b(aula|prova|estud\w*|faculdade|curso|escola|trabalho da faculdade|revis\w*|tcc|ingles|frances|exame final|vestibular|enem|oab|concurso)\b"),
    ("saude", r"\b(medic\w*|dentista|consulta|exame|academia|treino|remedio|terapia|psicolog\w*|fisioterapia|vacina|corrida|caminhada|yoga|pilates|nutricionista)\b"),
]


def categoria_de(titulo: str) -> str:
    texto = sem_acentos(titulo)
    return next((nome for nome, padrao in CATEGORIAS_POR_PALAVRA if re.search(padrao, texto)), "pessoal")


def _palavras(texto: str) -> list[str]:
    return [p for p in re.findall(r"\w+", sem_acentos(texto)) if p not in CONECTIVOS and len(p) > 1]


def semelhanca(alvo: str, titulo: str) -> float:
    """Quanto o que foi dito ("o dentista") lembra um título ("Dentista com a Dra. Ana"), de 0 a 1."""
    a, b = _palavras(alvo), _palavras(titulo)
    if not a or not b:
        return 0.0
    acertos = sum(1 for x in a if any(y.startswith(x[:5]) or x.startswith(y[:5]) for y in b))
    cobertura = acertos / len(a)
    parecido = difflib.SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    return max(0.85 * cobertura + 0.15 * parecido, parecido)


def no_meio_da_frase(titulo: str) -> str:
    """"Dentista" vira "dentista" no meio da frase; siglas ("TCC", "ENEM") ficam como estão."""
    if not titulo:
        return titulo
    primeira = titulo.split()[0]
    if len(primeira) > 1 and primeira[1:].isupper():
        return titulo
    return titulo[0].lower() + titulo[1:]


def _sim_ou_nao(t: str) -> tuple[str | None, str]:
    """("sim"/"nao", o resto da frase) ou (None, frase): "não, às 16h" -> ("nao", "às 16h")."""
    limpo = t.strip(" .!?,;")
    if limpo in SIM:
        return "sim", ""
    if limpo in NAO:
        return "nao", ""
    m = re.match(r"^(sim|nao|pode|isso)\b[\s,.;!]*(.*)$", limpo)
    if m:
        return ("nao" if m.group(1) == "nao" else "sim"), m.group(2)
    return None, t


# ------------------------------------------------------------------ estado da conversa
@dataclass
class Rascunho:
    """Compromisso ou lembrete que está sendo marcado (pode faltar o título ou o quando)."""
    titulo: str
    tipo: str  # compromisso | lembrete
    interpretacao: Interpretacao = field(default_factory=Interpretacao)
    notas: str = ""


@dataclass
class Pendencia:
    tipo: str  # quando | titulo | conflito | serie | plano | remarcar
    dados: dict
    ate: float


class AgenteAgenda:
    def __init__(self, agenda: Agenda, llm=None, relogio=datetime.now, lembrete_padrao: int = 15,
                 inicio_dia: str = "08:00", fim_dia: str = "22:00", hora_tarefas: str = "09:00", ao_abrir=None):
        self.agenda = agenda
        self.llm = llm  # cliente com .chat(prompt, system_prompt=, opcoes=) (llm_clients, só o Ollama local)
        self.agora = relogio
        self.lembrete_padrao = lembrete_padrao
        self.inicio_dia = time.fromisoformat(inicio_dia)
        self.fim_dia = time.fromisoformat(fim_dia)
        self.hora_tarefas = time.fromisoformat(hora_tarefas)
        self.ao_abrir = ao_abrir  # o painel abre a janela da agenda
        self.pendencia: Pendencia | None = None
        self._desfazer = None  # (mensagem, função)
        self._trava = threading.RLock()

    # ================================================================== entrada
    def responder(self, texto: str) -> str | None:
        texto = (texto or "").strip()
        if not texto:
            return None
        with self._trava:
            try:
                return self._responder(texto)
            except Exception as exc:
                logger.exception("Falha no agente de agenda")
                return f"Tive um problema com a agenda: {exc}"

    def _responder(self, texto: str) -> str | None:
        original, t = preparar(texto)
        chamada = re.match(r"^(?:beatriz|beatris|biatriz)\s*[,!.]?\s*", t)
        if chamada:  # o mesmo corte no original, que tem o mesmo tamanho
            original, t = original[chamada.end():], t[chamada.end():]
        texto, t = original.strip(), t.strip()
        if not t:
            return None

        if self.pendencia and self.pendencia.ate > relogio_do_sistema.time():
            resposta = self._continuar(texto, t)
            if resposta is not None:
                return resposta
        self.pendencia = None

        if RE_DOCUMENTO.search(t) or RE_RESUMO_DO_DIA.search(t):
            return None  # "faz um PDF da minha agenda", "resumo do dia": ficam com os agentes
        if RE_DESFAZER.search(t):
            return self._desfazer_ultima()
        if RE_ABRIR.search(t):
            if self.ao_abrir:
                self.ao_abrir()
            return self.descrever_dia(self.agora().date())
        if RE_TAREFA.search(t) or RE_LISTAR_TAREFAS.search(t):
            resposta = self._tarefas(texto, t)
            if resposta:
                return resposta
        if re.search(r"\bcomo (feit[ao]|conclu\w*|pront[ao])\b", t):  # "marca pagar o boleto como feito"
            resposta = self._concluir_tarefa(texto, t)
            if resposta:
                return resposta
        if RE_PLANEJAR.search(t):
            dia = self.agora().date() + timedelta(days=1 if "amanha" in t else 0)
            return self.planejar_dia(dia)
        if RE_CANCELAR.search(t):
            resposta = self._cancelar(texto, t)
            if resposta:
                return resposta
        if RE_REMARCAR.search(t):
            resposta = self._remarcar(texto, t)
            if resposta:
                return resposta
        if RE_LEMBRAR.search(t) or RE_LEMBRAR_FRACO.search(t):
            if (RE_LEMBRAR.search(t) and RE_LEMBRAR_ALVO.search(t)) or interpretar(texto, self.agora()).tem_quando:
                return self._criar(texto, "lembrete")
        if RE_CRIAR.search(t):
            return self._criar(texto, "compromisso")
        if RE_CONCLUIR.search(t):
            resposta = self._concluir_tarefa(texto, t, exigir_palavra=False)
            if resposta:
                return resposta
        if RE_PROXIMO.search(t):
            return self.proximo()
        if RE_LIVRE.search(t) and RE_PESSOAL_LIVRE.search(t):
            return self._livre(texto)
        if RE_ROTINA.search(t):
            return self.descrever_rotina()
        quando = RE_QUANDO_E.search(t.rstrip("?! ."))
        if quando:
            resposta = self._quando_e(quando.group("alvo"))
            if resposta:
                return resposta
        if RE_PERGUNTA_DIA.search(t):
            return self._consultar(texto, t)
        resposta = self._criar_sem_verbo(texto, t)
        if resposta:
            return resposta
        if RE_O_QUE_TENHO.search(t):
            i = interpretar(texto, self.agora())
            if i.data or i.intervalo:
                return self._consultar(texto, t)
        if RE_ASSUNTO_AGENDA.search(t) and RE_PESSOAL.search(t):
            return self._conversar(texto)
        return None

    # ================================================================== pendências
    def _perguntar(self, tipo: str, pergunta: str, **dados) -> str:
        self.pendencia = Pendencia(tipo, dados, relogio_do_sistema.time() + SEGUNDOS_PENDENCIA)
        return pergunta

    def _continuar(self, texto: str, t: str) -> str | None:
        pendencia, self.pendencia = self.pendencia, None
        decisao, resto = _sim_ou_nao(t)
        dados = pendencia.dados

        if pendencia.tipo in ("quando", "titulo", "remarcar") and decisao is None and RE_PEDIDO_NOVO.search(t):
            return None  # "o que tenho amanhã?" no lugar da resposta: é outro pedido
        if pendencia.tipo == "quando":
            nova = interpretar(texto, self.agora())
            original, _ = preparar(texto)
            sobra = limpar_titulo(tirar_padroes(tirar_trechos(original, nova.trechos), [r"\bpode ser\b", r"\b(?:marca|coloca)\b"]))
            if nova.tem_quando and len(sobra.split()) <= 3:
                return self._salvar(Rascunho(dados["rascunho"].titulo, dados["rascunho"].tipo,
                                             _juntar(dados["rascunho"].interpretacao, nova)))
            if decisao == "nao":
                return "Tudo bem, não marquei."
            return None
        if pendencia.tipo == "titulo":
            if decisao == "nao":
                return "Tudo bem, não marquei."
            if t.endswith("?") or len(t.split()) > 12:
                return None
            nova = interpretar(texto, self.agora())
            original, _ = preparar(texto)
            titulo = limpar_titulo(tirar_trechos(original, nova.trechos))
            if not titulo:
                return None
            return self._salvar(Rascunho(titulo, dados["rascunho"].tipo, _juntar(dados["rascunho"].interpretacao, nova)))
        if pendencia.tipo == "conflito":
            rascunho = dados["rascunho"]
            nova = interpretar(resto or texto, self.agora())
            if nova.hora or nova.data or nova.relativo:  # "não, às 16h"
                return self._salvar(Rascunho(rascunho.titulo, rascunho.tipo, _juntar(rascunho.interpretacao, nova)))
            if decisao == "sim":
                return self._salvar(rascunho, checar_conflito=False)
            if decisao == "nao":
                return "Tudo bem, não marquei. Se quiser, me diga outro horário."
            return None
        if pendencia.tipo == "serie":
            ocorrencia = dados["ocorrencia"]
            if TODAS.search(t) and decisao != "nao":
                return self._cancelar_serie(ocorrencia.evento)
            if SO_ESSA.search(t) or decisao == "sim":
                return self._cancelar_ocorrencia(ocorrencia)
            if decisao == "nao":
                return "Tudo bem, não cancelei nada."
            return None
        if pendencia.tipo == "confirmar_criacao":
            rascunho = dados["rascunho"]
            if decisao == "sim" and not resto:
                return self._salvar(rascunho)
            nova = interpretar(resto or texto, self.agora())
            if nova.tem_quando and not RE_PEDIDO_NOVO.search(resto or t):  # "sim, às 15h", "não, sexta"
                return self._salvar(Rascunho(rascunho.titulo, rascunho.tipo, _juntar(rascunho.interpretacao, nova)))
            if decisao == "sim":
                return self._salvar(rascunho)
            if decisao == "nao":
                return "Tudo bem, não marquei."
            return None
        if pendencia.tipo == "plano":
            if decisao == "sim":
                return self._aplicar_plano(dados["blocos"])
            if decisao == "nao":
                return "Tudo bem, deixei a agenda como estava."
            return None
        if pendencia.tipo == "remarcar":
            nova = interpretar(texto, self.agora())
            if nova.tem_quando:
                return self._mover(dados["ocorrencia"], nova)
            if decisao == "nao":
                return "Tudo bem, deixei como estava."
            return None
        return None

    # ================================================================== criar
    def _criar(self, texto: str, tipo: str) -> str:
        agora = self.agora()
        interpretacao = interpretar(texto, agora)
        padroes = PADROES_LEMBRAR + PADROES_CRIAR if tipo == "lembrete" else PADROES_CRIAR
        original, _ = preparar(texto)
        titulo = limpar_titulo(tirar_padroes(tirar_trechos(original, interpretacao.trechos), padroes))
        # "marca um lembrete pra mim", "agenda pra mim": sobrou só o nome do tipo de coisa.
        if sem_acentos(titulo) in {"lembrete", "compromisso", "evento", "horario", "algo", "uma coisa"}:
            titulo = ""
        return self._salvar(Rascunho(titulo, tipo, interpretacao))

    def _salvar(self, rascunho: Rascunho, checar_conflito: bool = True) -> str:
        i = rascunho.interpretacao
        agora = self.agora()
        lembrete = rascunho.tipo == "lembrete"
        if not rascunho.titulo:
            quando = self._falar_quando_parcial(i)
            pergunta = (f"Do que eu te lembro{quando}?" if lembrete else f"O que eu marco{quando}?")
            return self._perguntar("titulo", pergunta, rascunho=rascunho)
        nome = no_meio_da_frase(rascunho.titulo)
        if not i.tem_quando:
            pergunta = f"Quando devo te lembrar de {nome}?" if lembrete else f"Para quando eu marco {nome}?"
            return self._perguntar("quando", pergunta, rascunho=rascunho)

        if i.recorrencia:
            hora = i.hora or (time(HORA_DO_PERIODO[i.periodo]) if i.periodo else None)
            if hora is None:
                return self._perguntar("quando", f"Que horas é {nome}?", rascunho=rascunho)
            inicio = self._primeira_vez(i.recorrencia, hora, i.data or agora.date())
            dia_inteiro = False
        else:
            inicio = resolver_momento(i, agora)
            if inicio is None:
                return self._perguntar("quando", f"Para quando eu marco {nome}?", rascunho=rascunho)
            so_o_dia = i.hora is None and i.periodo is None and not i.relativo
            dia_inteiro = so_o_dia and not lembrete
            if so_o_dia and lembrete:
                inicio = datetime.combine(inicio.date(), self.hora_tarefas)
            if not dia_inteiro and inicio < agora - timedelta(minutes=1):
                return self._perguntar("quando", f"{falar_dia(inicio.date(), agora.date()).capitalize()} às "
                                       f"{falar_hora(inicio)} já passou. Para quando eu marco {nome}?", rascunho=rascunho)

        duracao = 0 if lembrete else (i.duracao or _minutos_entre(i.hora, i.hora_fim) or 60)
        if i.lembretes is not None:
            lembretes = i.lembretes
        else:
            lembretes = [0] if (lembrete or dia_inteiro) else [self.lembrete_padrao]
        recorrencia = i.recorrencia
        if recorrencia == "semanal":
            recorrencia = f"semanal:{inicio.weekday()}"

        if checar_conflito and not lembrete and not dia_inteiro and not recorrencia:
            conflitos = self.agenda.conflitos(inicio, duracao)
            if conflitos:
                outro = conflitos[0]
                return self._perguntar(
                    "conflito",
                    f"{falar_dia(inicio.date(), agora.date()).capitalize()} às {falar_hora(outro.inicio)} você já tem "
                    f"{no_meio_da_frase(outro.titulo)} (até {falar_hora(outro.fim)}). Marco {nome} às {falar_hora(inicio)} mesmo assim?",
                    rascunho=rascunho,
                )

        evento = self.agenda.criar_evento(
            rascunho.titulo, inicio, duracao, dia_inteiro=dia_inteiro, tipo=rascunho.tipo,
            categoria=categoria_de(rascunho.titulo), recorrencia=recorrencia, repetir_ate=i.ate_data if recorrencia else None,
            lembretes=lembretes, notas=rascunho.notas,
        )
        self._desfazer = (f"Desfeito: tirei {nome} da agenda.", lambda: self.agenda.cancelar_evento(evento.id))
        return self._confirmacao_de_criacao(evento, i)

    def marcar(self, titulo: str, quando: str, notas: str = "", lembretes=None) -> str:
        """Marca direto, sem perguntas de volta (API HTTP e ferramentas): "amanhã às 14h", "toda segunda às 7h"."""
        with self._trava:
            i = interpretar(quando, self.agora())
            if lembretes is not None:
                i.lembretes = list(lembretes)
            resposta = self._salvar(Rascunho(limpar_titulo(titulo) or "Compromisso", "compromisso", i, notas=notas),
                                    checar_conflito=False)
            self.pendencia = None  # sem conversa: se faltou o quando, a resposta já diz
            return resposta

    def _criar_sem_verbo(self, texto: str, t: str) -> str | None:
        """"academia toda segunda às 7h", "tenho dentista amanhã às 14h", "aniversário da Ana dia 25":
        compromisso sem dizer "marca". Com repetição, hora ou "tenho", marca na hora; só com a data, ou
        com "tem" ("tem jogo hoje às 20h"), pergunta antes. Perguntas e assuntos de outras funções passam."""
        if RE_PERGUNTA.search(t) or RE_OUTRO_ASSUNTO.search(t) or RE_PEDIDO_NOVO.search(t):
            return None
        i = interpretar(texto, self.agora())
        if not (i.recorrencia or i.hora or i.relativo or i.data):
            return None
        original, _ = preparar(texto)
        titulo = limpar_titulo(tirar_trechos(original, i.trechos))
        confirmar = False
        for padrao, pede_confirmacao in ((RE_TENHO_NO_INICIO, False), (RE_TEM_NO_INICIO, True)):
            inicio = padrao.match(sem_acentos(titulo))
            if inicio:
                titulo, confirmar = limpar_titulo(titulo[inicio.end():]), pede_confirmacao
                break
        normalizado = sem_acentos(titulo)
        palavras = normalizado.split()
        if not palavras or len(palavras) > 8 or normalizado in TITULOS_VAGOS or RE_COMECA_COM_VERBO.match(normalizado):
            return None
        tipo = "lembrete" if palavras[0] in VERBOS_DE_LEMBRETE else "compromisso"
        rascunho = Rascunho(titulo, tipo, i)
        so_a_data = not (i.recorrencia or i.hora or i.relativo or i.periodo)
        if confirmar or so_a_data:
            verbo = "te lembre de" if tipo == "lembrete" else "marque"
            return self._perguntar("confirmar_criacao",
                                   f"Quer que eu {verbo} {no_meio_da_frase(titulo)}{self._falar_quando_parcial(i)}?",
                                   rascunho=rascunho)
        return self._salvar(rascunho)

    def _primeira_vez(self, regra: str, hora: time, a_partir: date) -> datetime:
        """Primeiro dia (a partir de hoje) em que a repetição acontece, num horário que ainda não passou."""
        agora = self.agora()
        if regra == "semanal":  # "toda semana": no dia da semana do início
            primeiro = datetime.combine(a_partir, hora)
            return primeiro if primeiro > agora else primeiro + timedelta(days=7)
        modelo = Evento(id=0, titulo="", inicio=datetime.combine(a_partir, hora), recorrencia=regra)
        dia = a_partir
        for _ in range(400):
            candidato = datetime.combine(dia, hora)
            if modelo.repete_no_dia(dia) and candidato > agora:
                return candidato
            dia += timedelta(days=1)
        return datetime.combine(a_partir, hora)

    def _confirmacao_de_criacao(self, evento: Evento, i: Interpretacao) -> str:
        agora = self.agora()
        nome = no_meio_da_frase(evento.titulo)
        dia = falar_dia(evento.inicio.date(), agora.date())
        hora = falar_hora(evento.inicio)
        if evento.recorrencia:
            regra = falar_recorrencia(evento.recorrencia, evento.inicio)
            inicio = f", a partir de {dia}" if evento.inicio.date() != agora.date() else ", começando hoje"
            if evento.tipo == "lembrete":
                return f"Combinado: vou te lembrar de {nome} {regra} às {hora}{inicio}."
            return f"Coloquei {nome} na sua rotina: {regra} às {hora}{inicio}{self._falar_avisos(evento)}."
        if evento.tipo == "lembrete":
            if i.relativo:
                return f"Combinado: daqui a {falar_duracao(int(i.relativo.total_seconds() // 60))}, às {hora}, te lembro de {nome}."
            return f"Combinado: te lembro de {nome} {dia} às {hora}."
        if evento.dia_inteiro:
            return f"Anotei {nome} {_no_dia(dia)}, o dia todo. Te aviso nesse dia às {falar_hora(self.hora_tarefas)}."
        duracao = f", por {falar_duracao(evento.duracao)}" if evento.duracao != 60 else ""
        return f"Marquei {nome} {_no_dia(dia)} às {hora}{duracao}{self._falar_avisos(evento)}."

    def _falar_avisos(self, evento: Evento) -> str:
        if not evento.lembretes:
            return ", sem aviso"
        antes = [m for m in evento.lembretes if m > 0]
        if not antes:
            return ", com aviso na hora"
        return ", com aviso " + juntar(falar_duracao(m) for m in antes) + " antes"

    def _falar_quando_parcial(self, i: Interpretacao) -> str:
        agora = self.agora()
        momento = resolver_momento(i, agora) if not i.recorrencia else None
        if i.recorrencia:
            return " " + falar_recorrencia(i.recorrencia, None) + (f" às {falar_hora(i.hora)}" if i.hora else "")
        if momento is None:
            return ""
        texto = " " + _no_dia(falar_dia(momento.date(), agora.date()))
        return texto + (f" às {falar_hora(momento)}" if (i.hora or i.periodo or i.relativo) else "")

    # ================================================================== cancelar e remarcar
    def _achar(self, alvo: str, dia: date | None = None) -> Ocorrencia | None:
        """A ocorrência cujo título mais lembra `alvo` (no dia dito, ou a próxima)."""
        agora = self.agora()
        if dia:
            candidatas = self.agenda.ocorrencias_do_dia(dia)
        else:
            candidatas = self.agenda.ocorrencias(datetime.combine(agora.date(), time()), agora + timedelta(days=120))
        melhor, nota_melhor = None, LIMITE_SEMELHANCA
        for ocorrencia in candidatas:
            nota = semelhanca(alvo, ocorrencia.titulo)
            ja_passou = ocorrencia.fim < agora and not dia
            if ja_passou:
                continue
            if nota > nota_melhor + 0.01:
                melhor, nota_melhor = ocorrencia, nota
        return melhor

    def _alvo(self, texto: str) -> tuple[str, Interpretacao]:
        interpretacao = interpretar(texto, self.agora())
        original, _ = preparar(texto)
        alvo = limpar_titulo(tirar_padroes(tirar_trechos(original, interpretacao.trechos), PADROES_ALVO))
        return alvo, interpretacao

    def _cancelar(self, texto: str, t: str) -> str | None:
        alvo, i = self._alvo(texto)
        fala_da_agenda = bool(re.search(r"\b(agenda|compromisso|evento|lembrete|reuniao|consulta|marcad\w*)\b", t))
        if not alvo:
            if i.data and fala_da_agenda:
                return f"Qual compromisso eu cancelo? {self.descrever_dia(i.data)}"
            return None
        ocorrencia = self._achar(alvo, i.data)
        if ocorrencia is None:
            return f"Não achei {alvo.lower()} na sua agenda." if fala_da_agenda else None
        evento = ocorrencia.evento
        if not evento.repete:
            return self._cancelar_evento(ocorrencia)
        if i.data or SO_ESSA.search(t):
            return self._cancelar_ocorrencia(ocorrencia)
        if TODAS.search(t):
            return self._cancelar_serie(evento)
        dia = falar_dia(ocorrencia.inicio.date(), self.agora().date())
        return self._perguntar(
            "serie",
            f"{evento.titulo} se repete {falar_recorrencia(evento.recorrencia, evento.inicio)}. "
            f"Cancelo só a de {dia} ou todas?",
            ocorrencia=ocorrencia,
        )

    def _cancelar_evento(self, ocorrencia: Ocorrencia) -> str:
        evento = ocorrencia.evento
        self.agenda.cancelar_evento(evento.id)
        nome = no_meio_da_frase(evento.titulo)
        self._desfazer = (f"Desfeito: {nome} voltou para a agenda.", lambda: self.agenda.restaurar_evento(evento.id))
        quando = _no_dia(falar_dia(ocorrencia.inicio.date(), self.agora().date()))
        hora = "" if evento.dia_inteiro else f" às {falar_hora(ocorrencia.inicio)}"
        return f"Cancelei {nome} {quando}{hora}."

    def _cancelar_ocorrencia(self, ocorrencia: Ocorrencia) -> str:
        evento, dia = ocorrencia.evento, ocorrencia.inicio.date()
        self.agenda.cancelar_ocorrencia(evento.id, dia)
        nome = no_meio_da_frase(evento.titulo)
        self._desfazer = (f"Desfeito: {nome} voltou para a agenda.", lambda: self.agenda.restaurar_ocorrencia(evento.id, dia))
        return f"Cancelei só {nome} {_no_dia(falar_dia(dia, self.agora().date()))}; o resto da rotina continua."

    def _cancelar_serie(self, evento: Evento) -> str:
        self.agenda.cancelar_evento(evento.id)
        nome = no_meio_da_frase(evento.titulo)
        self._desfazer = (f"Desfeito: {nome} voltou para a rotina.", lambda: self.agenda.restaurar_evento(evento.id))
        return f"Tirei {nome} da sua rotina ({falar_recorrencia(evento.recorrencia, evento.inicio)})."

    def _remarcar(self, texto: str, t: str) -> str | None:
        forte = bool(RE_REMARCAR_FORTE.search(t))
        # "adia o dentista em 1 hora", "antecipa a reunião 30 minutos"
        deslocar = re.search(r"\b(adi[ae]\w*|antecip\w*|empurr\w*)\b", t)
        corte = None
        for m in re.finditer(r"\s(?:para|pra|pro)\s", t):
            corte = m
        if corte is None and deslocar:
            i = interpretar(texto, self.agora())
            minutos = i.relativo or (timedelta(minutes=i.duracao) if i.duracao else None)
            if minutos is None:
                m = re.search(r"\b(\d+|uma|um|meia)\s*(min\w*|h(?:ora)?s?)\b", t)
                if m:
                    n = 30 if m.group(1) == "meia" else 1 if m.group(1) in ("um", "uma") else int(m.group(1))
                    minutos = timedelta(minutes=n if m.group(2).startswith("min") else n * 60)
            if minutos is None:
                return None
            original, _ = preparar(texto)
            alvo = limpar_titulo(tirar_padroes(tirar_trechos(original, i.trechos), PADROES_ALVO + [
                r"\b(?:em|por|uns|umas)?\s*(?:\d+|uma|um|meia)\s*(?:min\w*|h(?:ora)?s?)\b"]))
            ocorrencia = self._achar(alvo, i.data) if alvo else None
            if ocorrencia is None:
                return f"Não achei {alvo.lower() or 'esse compromisso'} na sua agenda." if forte else None
            sinal = -1 if re.search(r"\bantecip\w*", t) else 1
            nova = Interpretacao(relativo=None)
            novo_inicio = ocorrencia.inicio + sinal * minutos
            return self._mover(ocorrencia, nova, novo_inicio=novo_inicio)
        if corte is None:
            if not forte:
                return None
            alvo, i = self._alvo(texto)
            ocorrencia = self._achar(alvo, i.data) if alvo else None
            if ocorrencia is None:
                return f"Não achei {alvo.lower() or 'esse compromisso'} na sua agenda."
            return self._perguntar("remarcar", f"Para quando eu passo {no_meio_da_frase(ocorrencia.titulo)}?",
                                   ocorrencia=ocorrencia)
        original, _ = preparar(texto)
        antes, depois = original[:corte.start()], original[corte.end():]
        alvo, i_antes = self._alvo(antes)
        nova = interpretar(depois, self.agora())
        if not alvo or not nova.tem_quando:
            return None
        ocorrencia = self._achar(alvo, i_antes.data)
        if ocorrencia is None:
            return f"Não achei {alvo.lower()} na sua agenda." if forte else None
        return self._mover(ocorrencia, nova)

    def _mover(self, ocorrencia: Ocorrencia, nova: Interpretacao, novo_inicio: datetime | None = None) -> str:
        agora = self.agora()
        evento = ocorrencia.evento
        if novo_inicio is None:
            if nova.relativo:
                novo_inicio = (agora + nova.relativo).replace(second=0, microsecond=0)
            else:
                hora = nova.hora or (time(HORA_DO_PERIODO[nova.periodo]) if nova.periodo else ocorrencia.inicio.time())
                if nova.hora and nova.periodo in ("tarde", "noite") and hora.hour < 12:
                    hora = time(hora.hour + 12, hora.minute)
                dia = nova.data or ocorrencia.inicio.date()
                novo_inicio = datetime.combine(dia, hora)
                if novo_inicio <= agora and nova.hora_ambigua and hora.hour < 12:
                    novo_inicio += timedelta(hours=12)
                if novo_inicio <= agora and nova.data is None:
                    novo_inicio += timedelta(days=1)
        nome = no_meio_da_frase(evento.titulo)
        if evento.repete:
            dia_antigo = ocorrencia.inicio.date()
            self.agenda.cancelar_ocorrencia(evento.id, dia_antigo)
            copia = self.agenda.criar_evento(
                evento.titulo, novo_inicio, evento.duracao, tipo=evento.tipo, categoria=evento.categoria,
                lembretes=evento.lembretes, local=evento.local, notas=evento.notas,
            )

            def desfazer():
                self.agenda.restaurar_ocorrencia(evento.id, dia_antigo)
                self.agenda.cancelar_evento(copia.id)
            self._desfazer = (f"Desfeito: {nome} voltou para o horário de antes.", desfazer)
            id_ignorar = copia.id
        else:
            inicio_antigo, dia_inteiro_antigo = evento.inicio, evento.dia_inteiro
            self.agenda.atualizar_evento(evento.id, inicio=novo_inicio, dia_inteiro=False if nova.hora else evento.dia_inteiro)
            self._desfazer = (f"Desfeito: {nome} voltou para o horário de antes.",
                              lambda: self.agenda.atualizar_evento(evento.id, inicio=inicio_antigo, dia_inteiro=dia_inteiro_antigo))
            id_ignorar = evento.id
        resposta = f"Pronto: {nome} passou para {falar_dia(novo_inicio.date(), agora.date())} às {falar_hora(novo_inicio)}."
        if evento.tipo != "lembrete":
            conflitos = self.agenda.conflitos(novo_inicio, evento.duracao, ignorar_id=id_ignorar)
            if conflitos:
                resposta += f" Atenção: nesse horário também tem {no_meio_da_frase(conflitos[0].titulo)}."
        return resposta

    def _desfazer_ultima(self) -> str:
        if not self._desfazer:
            return "Não tenho nenhuma mudança recente da agenda para desfazer."
        mensagem, funcao = self._desfazer
        self._desfazer = None
        funcao()
        return mensagem

    # ================================================================== consultas
    def descrever_dia(self, dia: date, incluir_tarefas: bool = True) -> str:
        agora = self.agora()
        hoje = agora.date()
        ocorrencias = self.agenda.ocorrencias_do_dia(dia)
        nome_dia = falar_dia(dia, hoje)
        if dia == hoje:
            restantes = [o for o in ocorrencias if o.evento.dia_inteiro or o.fim > agora]
        else:
            restantes = ocorrencias
        partes = []
        if restantes:
            itens = juntar(self._falar_ocorrencia(o, agora) for o in restantes)
            if dia == hoje and len(restantes) < len(ocorrencias):
                partes.append(f"Ainda hoje: {itens}.")
            else:
                partes.append(f"{nome_dia.capitalize()}: {itens}.")
        elif dia == hoje and ocorrencias:
            partes.append("Hoje não tem mais nada na agenda.")
        else:
            partes.append(f"Nada marcado para {nome_dia}." if dia != hoje else "Hoje você não tem nada marcado.")
        if incluir_tarefas:
            tarefas = [t for t in self.agenda.tarefas() if t.prazo and (t.prazo == dia or (dia == hoje and t.prazo < hoje))]
            if tarefas:
                atrasadas = [t for t in tarefas if t.prazo < hoje]
                texto = "Tarefas para " + falar_dia_curto(dia, hoje) + ": "
                texto += juntar(no_meio_da_frase(t.titulo) + (" (atrasada)" if t in atrasadas else "") for t in tarefas)
                partes.append(texto + ".")
        return " ".join(partes)

    def _falar_ocorrencia(self, o: Ocorrencia, agora: datetime) -> str:
        nome = no_meio_da_frase(o.titulo)
        if o.evento.dia_inteiro:
            return f"{nome} (o dia todo)"
        if o.evento.tipo == "lembrete":
            return f"lembrete de {nome} às {falar_hora(o.inicio)}"
        if o.inicio <= agora < o.fim:
            return f"{nome} (agora, até {falar_hora(o.fim)})"
        return f"{nome} às {falar_hora(o.inicio)}"

    def descrever_periodo(self, primeiro: date, ultimo: date) -> str:
        agora = self.agora()
        linhas, total = [], 0
        dia = primeiro
        while dia <= ultimo:
            ocorrencias = [o for o in self.agenda.ocorrencias_do_dia(dia)
                           if dia != agora.date() or o.evento.dia_inteiro or o.fim > agora]
            if ocorrencias:
                total += len(ocorrencias)
                nome = "hoje" if dia == agora.date() else "amanhã" if dia == agora.date() + timedelta(days=1) else \
                    f"{NOMES_DIAS[dia.weekday()].split('-')[0]} {dia.day}"
                linhas.append((nome, ocorrencias))
            dia += timedelta(days=1)
        if not linhas:
            return "Nada marcado nesse período."
        if total > 12:
            resumo = "; ".join(f"{nome}, {len(oc)} {'item' if len(oc) == 1 else 'itens'}" for nome, oc in linhas)
            return f"São {total} itens no período: {resumo}. Os detalhes estão na janela da agenda do painel."
        return " ".join(f"{nome.capitalize()}: {juntar(self._falar_ocorrencia(o, agora) for o in oc)}." for nome, oc in linhas)

    def _consultar(self, texto: str, t: str) -> str:
        agora = self.agora()
        i = interpretar(texto, agora)
        if i.intervalo:
            return self.descrever_periodo(*i.intervalo)
        if re.search(r"\bminha semana\b", t):
            return self.descrever_periodo(agora.date(), agora.date() + timedelta(days=6 - agora.weekday()))
        return self.descrever_dia(i.data or agora.date())

    def proximo(self) -> str:
        agora = self.agora()
        atuais = [o for o in self.agenda.ocorrencias(agora - timedelta(hours=12), agora + timedelta(minutes=1))
                  if o.ocupa and o.inicio <= agora < o.fim]
        proximas = [o for o in self.agenda.proximas(agora, 3) if not o.evento.dia_inteiro]
        partes = []
        if atuais:
            partes.append(f"Agora você está em {no_meio_da_frase(atuais[0].titulo)}, até {falar_hora(atuais[0].fim)}.")
        if proximas:
            o = proximas[0]
            quando = falar_dia(o.inicio.date(), agora.date())
            falta = o.inicio - agora
            if falta < timedelta(hours=3):
                minutos = int(falta.total_seconds() // 60)
                quando = f"daqui a {falar_duracao(max(minutos, 1))}"
                partes.append(f"O próximo é {no_meio_da_frase(o.titulo)}, {quando}, às {falar_hora(o.inicio)}.")
            else:
                partes.append(f"O próximo é {no_meio_da_frase(o.titulo)}, {quando} às {falar_hora(o.inicio)}.")
        if not partes:
            return "Você não tem nenhum compromisso pela frente nos próximos dois meses."
        return " ".join(partes)

    def _quando_e(self, alvo: str) -> str | None:
        alvo = limpar_titulo(alvo)
        ocorrencia = self._achar(alvo) if alvo else None
        if ocorrencia is None:
            return None
        agora = self.agora()
        dia = falar_dia(ocorrencia.inicio.date(), agora.date())
        if ocorrencia.evento.dia_inteiro:
            return f"{ocorrencia.titulo} é {dia}, o dia todo."
        repete = f" (se repete {falar_recorrencia(ocorrencia.evento.recorrencia, ocorrencia.evento.inicio)})" \
            if ocorrencia.evento.repete else ""
        return f"{ocorrencia.titulo} é {dia} às {falar_hora(ocorrencia.inicio)}{repete}."

    def _livre(self, texto: str) -> str:
        agora = self.agora()
        i = interpretar(texto, agora)
        if i.intervalo:
            dias = [i.intervalo[0] + timedelta(days=k) for k in range((i.intervalo[1] - i.intervalo[0]).days + 1)]
        else:
            dias = [i.data or agora.date()]
        if i.periodo:
            faixa = FAIXA_DO_PERIODO[i.periodo]
            de_hora, ate_hora = time(faixa[0]), (time(23, 59) if faixa[1] == 24 else time(faixa[1]))
        else:
            de_hora, ate_hora = self.inicio_dia, self.fim_dia

        if i.hora and len(dias) == 1:  # "estou livre amanhã às 14h?"
            inicio = datetime.combine(dias[0], i.hora)
            conflitos = self.agenda.conflitos(inicio, i.duracao or 60)
            quando = f"{falar_dia(dias[0], agora.date())} às {falar_hora(inicio)}"
            if not conflitos:
                return f"Sim, {quando} você está livre."
            outro = conflitos[0]
            return f"Não: {quando} você tem {no_meio_da_frase(outro.titulo)} ({falar_hora(outro.inicio)} às {falar_hora(outro.fim)})."

        respostas = []
        for dia in dias:
            de = datetime.combine(dia, de_hora)
            ate = datetime.combine(dia, ate_hora)
            if dia == agora.date():
                de = max(de, _arredondar(agora))
            if de >= ate:
                continue
            livres = self.agenda.horarios_livres(de, ate, minimo=30)
            nome = falar_dia(dia, agora.date()) + (f" {_falar_periodo(i.periodo)}" if i.periodo else "")
            if not livres:
                respostas.append(f"{nome.capitalize()} está tudo ocupado.")
            elif len(livres) == 1 and livres[0] == (de, ate):
                faixa = "" if i.periodo else f" das {falar_hora(de)} às {falar_hora(ate)}"
                frase = f"{nome} você está livre{faixa}."
                respostas.append(f"Sim, {frase}" if len(dias) == 1 else frase[0].upper() + frase[1:])
            else:
                faixas = juntar(f"das {falar_hora(a)} às {falar_hora(b)}" for a, b in livres[:4])
                respostas.append(f"{nome.capitalize()} você está livre {faixas}.")
        if not respostas:
            return "Esse período já passou."
        if len(respostas) > 4:
            respostas = respostas[:4] + ["Os outros dias estão na janela da agenda do painel."]
        return " ".join(respostas)

    def descrever_rotina(self) -> str:
        repetidos = [e for e in self.agenda.eventos_ativos() if e.repete]
        if not repetidos:
            return ("Sua rotina ainda está vazia. Diga, por exemplo: academia toda segunda, quarta e sexta às 7h, "
                    "ou me lembra de tomar remédio todo dia às 20h.")
        repetidos.sort(key=lambda e: (e.inicio.time(), e.titulo))
        itens = [f"{no_meio_da_frase(e.titulo)} {falar_recorrencia(e.recorrencia, e.inicio)} às {falar_hora(e.inicio)}"
                 for e in repetidos]
        return "Sua rotina: " + "; ".join(itens) + "."

    def resumo_de_abertura(self) -> str:
        """Frase curta para a saudação: o que tem hoje e o que vence."""
        agora = self.agora()
        restantes = [o for o in self.agenda.ocorrencias_do_dia(agora.date())
                     if not o.evento.dia_inteiro and o.inicio > agora and o.evento.tipo != "lembrete"]
        tarefas = [t for t in self.agenda.tarefas() if t.prazo and t.prazo <= agora.date()]
        partes = []
        if restantes:
            primeiro = restantes[0]
            quantos = "1 compromisso" if len(restantes) == 1 else f"{len(restantes)} compromissos"
            partes.append(f"Hoje você ainda tem {quantos}; o próximo é {no_meio_da_frase(primeiro.titulo)} às {falar_hora(primeiro.inicio)}.")
        if tarefas:
            partes.append("1 tarefa vence hoje." if len(tarefas) == 1 else f"{len(tarefas)} tarefas vencem até hoje.")
        return " ".join(partes)

    # ================================================================== tarefas
    def _tarefas(self, texto: str, t: str) -> str | None:
        # O verbo vale antes da palavra "tarefa": "adiciona a tarefa cancelar a academia" é para adicionar.
        comeco = t.split("tarefa")[0] if "tarefa" in t else t
        if RE_CONCLUIR.search(comeco) and not RE_ADICIONAR.search(comeco):
            resposta = self._concluir_tarefa(texto, t)
            if resposta:
                return resposta
        if RE_REMOVER.search(comeco) and not RE_ADICIONAR.search(comeco):
            return self._remover_tarefa(texto)
        if RE_LISTAR_TAREFAS.search(t) and not re.search(r"\b(adicion\w*|coloc\w*|anot\w*|inclu\w*|nova)\b", t):
            return self.listar_tarefas()
        if RE_ADICIONAR.search(t) or re.match(r"^tarefas?\b", t):
            return self._adicionar_tarefa(texto)
        return None

    def _adicionar_tarefa(self, texto: str) -> str:
        i = interpretar(texto, self.agora())
        original, _ = preparar(texto)
        titulo = limpar_titulo(tirar_padroes(tirar_trechos(original, i.trechos), PADROES_TAREFA))
        if not titulo:
            return "Qual é a tarefa? Diga, por exemplo: nova tarefa pagar o boleto até sexta."
        prazo = i.ate_data or i.data
        tarefa = self.agenda.criar_tarefa(titulo, prazo=prazo, prioridade=i.prioridade or 2, duracao=i.duracao or 30)
        nome = no_meio_da_frase(tarefa.titulo)
        self._desfazer = (f"Desfeito: tirei {nome} das tarefas.", lambda: self.agenda.remover_tarefa(tarefa.id))
        detalhes = ""
        if prazo:
            detalhes += f", para {falar_dia_curto(prazo, self.agora().date())}"
        if tarefa.prioridade == 1:
            detalhes += ", com prioridade alta"
        return f"Anotei a tarefa {nome}{detalhes}."

    def listar_tarefas(self) -> str:
        tarefas = self.agenda.tarefas()
        if not tarefas:
            return "Você não tem tarefas pendentes."
        hoje = self.agora().date()

        def falar(tarefa: Tarefa) -> str:
            nome = no_meio_da_frase(tarefa.titulo)
            if tarefa.prazo and tarefa.prazo < hoje:
                nome += " (atrasada)"
            elif tarefa.prazo:
                nome += f" (para {falar_dia_curto(tarefa.prazo, hoje)})"
            if tarefa.prioridade == 1:
                nome += ", urgente"
            return nome

        quantas = "1 tarefa pendente" if len(tarefas) == 1 else f"{len(tarefas)} tarefas pendentes"
        mostrar = tarefas[:8]
        texto = f"Você tem {quantas}: " + "; ".join(falar(t) for t in mostrar) + "."
        if len(tarefas) > len(mostrar):
            texto += f" E mais {len(tarefas) - len(mostrar)} na janela da agenda do painel."
        return texto

    def _achar_tarefa(self, texto: str) -> Tarefa | None:
        i = interpretar(texto, self.agora())
        original, _ = preparar(texto)
        alvo = limpar_titulo(tirar_padroes(tirar_trechos(original, i.trechos), PADROES_ALVO))
        if not alvo:
            return None
        melhor, nota_melhor = None, LIMITE_SEMELHANCA
        for tarefa in self.agenda.tarefas():
            nota = semelhanca(alvo, tarefa.titulo)
            if nota > nota_melhor:
                melhor, nota_melhor = tarefa, nota
        return melhor

    def _concluir_tarefa(self, texto: str, t: str, exigir_palavra: bool = True) -> str | None:
        tarefa = self._achar_tarefa(texto)
        if tarefa is None:
            return "Não achei essa tarefa na sua lista." if exigir_palavra and RE_TAREFA.search(t) else None
        self.agenda.concluir_tarefa(tarefa.id)
        # Os horários reservados para ela no "organiza meu dia" (os que ainda não acabaram) saem da agenda.
        agora = self.agora()
        blocos = [o.evento.id for o in self.agenda.ocorrencias(agora, agora + timedelta(days=60))
                  if o.evento.tarefa_id == tarefa.id and o.fim > agora]
        for evento_id in blocos:
            self.agenda.cancelar_evento(evento_id)
        nome = no_meio_da_frase(tarefa.titulo)

        def desfazer():
            self.agenda.concluir_tarefa(tarefa.id, False)
            for evento_id in blocos:
                self.agenda.restaurar_evento(evento_id)
        self._desfazer = (f"Desfeito: {nome} voltou para as pendentes.", desfazer)
        restantes = len(self.agenda.tarefas())
        falta = "Não sobrou nenhuma tarefa pendente!" if restantes == 0 else \
            f"Falta {restantes}." if restantes == 1 else f"Faltam {restantes}."
        return f"Boa! Marquei {nome} como feita. {falta}"

    def _remover_tarefa(self, texto: str) -> str:
        tarefa = self._achar_tarefa(texto)
        if tarefa is None:
            return "Não achei essa tarefa na sua lista."
        self.agenda.remover_tarefa(tarefa.id)
        nome = no_meio_da_frase(tarefa.titulo)
        self._desfazer = (f"Desfeito: {nome} voltou para as tarefas.", lambda: self.agenda.remover_tarefa(tarefa.id, False))
        return f"Tirei {nome} das suas tarefas."

    # ================================================================== organizar o dia
    def planejar_dia(self, dia: date) -> str:
        """Encaixa as tarefas pendentes nos horários livres do dia e pergunta se pode marcar."""
        agora = self.agora()
        de = datetime.combine(dia, self.inicio_dia)
        if dia == agora.date():
            de = max(de, _arredondar(agora))
        ate = datetime.combine(dia, self.fim_dia)
        nome_dia = falar_dia(dia, agora.date())
        tarefas = [t for t in self.agenda.tarefas() if not t.prazo or t.prazo >= dia or t.prazo < agora.date()]
        ja_encaixadas = {o.evento.tarefa_id for o in self.agenda.ocorrencias(de, ate) if o.evento.tarefa_id}
        tarefas = [t for t in tarefas if t.id not in ja_encaixadas]
        if not tarefas:
            return ("Você não tem tarefas pendentes para encaixar. " + self.descrever_dia(dia, incluir_tarefas=False) +
                    " Para anotar uma, diga: nova tarefa, e o que é.")
        if de >= ate:
            return f"Não sobra horário {nome_dia} dentro da sua faixa do dia ({falar_hora(self.inicio_dia)} às {falar_hora(self.fim_dia)})."
        livres = self.agenda.horarios_livres(de, ate, minimo=15)
        blocos, sobraram = [], []
        for tarefa in tarefas:
            duracao = timedelta(minutes=tarefa.duracao or 30)
            for k, (a, b) in enumerate(livres):
                if b - a >= duracao:
                    blocos.append((tarefa, a, a + duracao))
                    livres[k] = (a + duracao + INTERVALO_BLOCOS, b)
                    break
            else:
                sobraram.append(tarefa)
        if not blocos:
            return f"{nome_dia.capitalize()} não sobra horário livre para as suas tarefas."
        sugestao = "; ".join(f"{falar_hora(a)}, {no_meio_da_frase(t.titulo)}" for t, a, _ in blocos)
        texto = f"Sugestão para {nome_dia}: {sugestao}."
        if sobraram:
            texto += f" Não couberam: {juntar(no_meio_da_frase(t.titulo) for t in sobraram)}."
        return self._perguntar("plano", texto + " Coloco esses horários na agenda?",
                               blocos=[(t.id, t.titulo, a, (b - a)) for t, a, b in blocos])

    def _aplicar_plano(self, blocos) -> str:
        criados = []
        for tarefa_id, titulo, inicio, duracao in blocos:
            evento = self.agenda.criar_evento(titulo, inicio, int(duracao.total_seconds() // 60), tipo="foco",
                                              categoria="foco", lembretes=[0], tarefa_id=tarefa_id)
            criados.append(evento.id)

        def desfazer():
            for evento_id in criados:
                self.agenda.cancelar_evento(evento_id)
        self._desfazer = ("Desfeito: tirei os horários das tarefas da agenda.", desfazer)
        return f"Pronto, coloquei {len(criados)} {'bloco' if len(criados) == 1 else 'blocos'} de tarefa na agenda. Te aviso na hora de cada um."

    # ================================================================== conversa livre
    def contexto_para_llm(self) -> str:
        agora = self.agora()
        linhas = [f"Agora: {NOMES_DIAS[agora.weekday()]}, {agora:%d/%m/%Y %H:%M}."]
        for k in range(8):
            dia = agora.date() + timedelta(days=k)
            ocorrencias = self.agenda.ocorrencias_do_dia(dia)
            itens = [("dia todo" if o.evento.dia_inteiro else f"{o.inicio:%H:%M}-{o.fim:%H:%M}") + f" {o.titulo}"
                     + (" (lembrete)" if o.evento.tipo == "lembrete" else "") for o in ocorrencias]
            linhas.append(f"{NOMES_DIAS[dia.weekday()]} {dia:%d/%m}: " + ("; ".join(itens) if itens else "nada marcado"))
        tarefas = self.agenda.tarefas()
        if tarefas:
            linhas.append("Tarefas pendentes: " + "; ".join(
                t.titulo + (f" (prazo {t.prazo:%d/%m})" if t.prazo else "") + (" [urgente]" if t.prioridade == 1 else "")
                for t in tarefas[:15]))
        linhas.append(f"Faixa do dia do usuário: {self.inicio_dia:%H:%M} às {self.fim_dia:%H:%M}.")
        return "\n".join(linhas)

    def _conversar(self, pergunta: str) -> str | None:
        if self.llm is None:
            return None
        sistema = (
            "Você é a Beatriz, assistente pessoal, cuidando da agenda e da rotina do usuário. Responda em português "
            "do Brasil, como numa conversa falada: no máximo três frases curtas, sem listas nem markdown. Use só a "
            "agenda abaixo; não invente compromissos. Você não consegue marcar nada por esta resposta: se o usuário "
            "quiser marcar, mudar ou cancelar, diga a frase que ele pode usar, como 'marca dentista amanhã às 14h', "
            "'remarca a reunião para sexta às 10h' ou 'cancela o dentista'.\n\nAGENDA:\n" + self.contexto_para_llm()
        )
        try:
            return self.llm.chat(pergunta, system_prompt=sistema, opcoes={"temperature": 0.2, "num_predict": 220})
        except Exception as exc:
            logger.warning("Ollama indisponível para a conversa da agenda: %s", exc)
            return None

    # ================================================================== lembretes
    def verificar_lembretes(self, agora: datetime | None = None) -> list[str]:
        """Avisos que venceram agora (cada um sai uma vez só, mesmo com dois processos olhando)."""
        agora = agora or self.agora()
        avisos = []
        ocorrencias = self.agenda.ocorrencias(agora - timedelta(days=1), agora + timedelta(days=2, hours=1))
        for o in ocorrencias:
            for minutos in (o.evento.lembretes or []):
                if o.evento.dia_inteiro:
                    momento = datetime.combine(o.inicio.date(), self.hora_tarefas) - timedelta(minutes=minutos)
                else:
                    momento = o.inicio - timedelta(minutes=minutos)
                if not (momento <= agora < momento + TOLERANCIA_AVISO):
                    continue
                chave = f"ev:{o.evento.id}:{o.inicio:%Y-%m-%dT%H:%M}:{minutos}"
                if self.agenda.marcar_avisado(chave):
                    avisos.append(self._texto_do_aviso(o, minutos, agora))
        momento_tarefas = datetime.combine(agora.date(), self.hora_tarefas)
        if momento_tarefas <= agora < momento_tarefas + TOLERANCIA_AVISO * 4:
            vencendo = [t for t in self.agenda.tarefas() if t.prazo == agora.date()]
            if vencendo and self.agenda.marcar_avisado(f"tarefas:{agora.date().isoformat()}"):
                nomes = juntar(no_meio_da_frase(t.titulo) for t in vencendo)
                avisos.append(f"Lembrete: hoje vence {'a tarefa' if len(vencendo) == 1 else 'as tarefas'} {nomes}.")
        return avisos

    def _texto_do_aviso(self, o: Ocorrencia, minutos: int, agora: datetime) -> str:
        nome = no_meio_da_frase(o.titulo)
        if o.evento.dia_inteiro:
            quando = "hoje" if o.inicio.date() == agora.date() else falar_dia(o.inicio.date(), agora.date())
            return f"Lembrete: {quando} é dia de {nome}."
        if o.evento.tipo == "foco":
            return f"Hora da tarefa: {nome}, por {falar_duracao(o.evento.duracao)}."
        if minutos == 0 or o.inicio <= agora:
            return f"Lembrete: {nome}." if o.evento.tipo == "lembrete" else f"Está na hora: {nome}, às {falar_hora(o.inicio)}."
        falta = o.inicio - agora
        if falta >= timedelta(hours=20):
            return f"Lembrete: {falar_dia(o.inicio.date(), agora.date())} às {falar_hora(o.inicio)} você tem {nome}."
        minutos_faltando = max(1, round(falta.total_seconds() / 60))
        return f"Daqui a {falar_duracao(minutos_faltando)}, às {falar_hora(o.inicio)}: {nome}."

    # ================================================================== botões do painel
    def cancelar_por_id(self, evento_id: int, dia: str, tudo: bool = False) -> str:
        """× num item da janela da agenda: o evento avulso, só aquele dia da rotina, ou a rotina toda."""
        with self._trava:
            evento = self.agenda.obter_evento(int(evento_id))
            if evento is None or evento.cancelado:
                return "Esse compromisso já não está na agenda."
            ocorrencia = Ocorrencia(evento, datetime.combine(date.fromisoformat(dia), evento.inicio.time()))
            if not evento.repete:
                return self._cancelar_evento(ocorrencia)
            return self._cancelar_serie(evento) if tudo else self._cancelar_ocorrencia(ocorrencia)

    def adicionar_tarefa(self, texto: str) -> str:
        with self._trava:
            return self._adicionar_tarefa(texto)

    def alternar_tarefa(self, tarefa_id: int) -> str:
        with self._trava:
            tarefa = self.agenda.obter_tarefa(int(tarefa_id))
            if tarefa is None:
                return "Essa tarefa não existe mais."
            if tarefa.concluida:
                self.agenda.concluir_tarefa(tarefa.id, False)
                return f"{tarefa.titulo} voltou para as pendentes."
            self.agenda.concluir_tarefa(tarefa.id)
            nome = no_meio_da_frase(tarefa.titulo)
            self._desfazer = (f"Desfeito: {nome} voltou para as pendentes.", lambda: self.agenda.concluir_tarefa(tarefa.id, False))
            return f"Boa! {tarefa.titulo} feita."

    def remover_tarefa_por_id(self, tarefa_id: int) -> str:
        with self._trava:
            tarefa = self.agenda.obter_tarefa(int(tarefa_id))
            if tarefa is None:
                return "Essa tarefa não existe mais."
            self.agenda.remover_tarefa(tarefa.id)
            nome = no_meio_da_frase(tarefa.titulo)
            self._desfazer = (f"Desfeito: {nome} voltou para as tarefas.", lambda: self.agenda.remover_tarefa(tarefa.id, False))
            return f"Tirei {nome} das tarefas."

    # ================================================================== dados para o painel
    def dados_mes(self, ano: int | None = None, mes: int | None = None) -> dict:
        """Calendário do mês (semanas de segunda a domingo) com os itens de cada dia, para a tela e a janela."""
        agora = self.agora()
        hoje = agora.date()
        ano, mes = int(ano or hoje.year), int(mes or hoje.month)
        primeiro = date(ano, mes, 1)
        ultimo = date(ano + (mes == 12), mes % 12 + 1, 1) - timedelta(days=1)
        inicio = primeiro - timedelta(days=primeiro.weekday())
        fim = ultimo + timedelta(days=6 - ultimo.weekday())
        por_dia = {}
        for o in self.agenda.ocorrencias(datetime.combine(inicio, time()), datetime.combine(fim + timedelta(days=1), time())):
            por_dia.setdefault(o.inicio.date(), []).append(o)
        tarefas = self.agenda.tarefas()
        prazos = {}
        for tarefa in tarefas:
            if tarefa.prazo:
                prazos[tarefa.prazo] = prazos.get(tarefa.prazo, 0) + 1
        dias = []
        dia = inicio
        while dia <= fim:
            ocorrencias = por_dia.get(dia, [])
            dias.append({
                "data": dia.isoformat(), "numero": dia.day, "do_mes": dia.month == mes,
                "hoje": dia == hoje, "passado": dia < hoje, "quantidade": len(ocorrencias),
                "tarefas": prazos.get(dia, 0),
                "itens": [_ocorrencia_para_painel(o, agora) for o in ocorrencias],
            })
            dia += timedelta(days=1)
        anterior = primeiro - timedelta(days=1)
        return {
            "titulo": f"{NOMES_MESES[mes - 1]} de {ano}", "ano": ano, "mes": mes, "dias": dias,
            "anterior": [anterior.year, anterior.month], "seguinte": [ultimo.year + (ultimo.month == 12), ultimo.month % 12 + 1],
            "pendentes": len(tarefas), "atrasadas": sum(1 for t in tarefas if t.prazo and t.prazo < hoje),
            "proximo": self.proximo() if self.agenda.proximas(agora, 1) else "Nada marcado pela frente.",
        }

    def dados_dia(self, dia: str | None = None) -> dict:
        """Os itens de um dia e as tarefas que vencem nele (o dia clicado no calendário da tela)."""
        agora = self.agora()
        escolhido = date.fromisoformat(dia) if dia else agora.date()
        nome = falar_dia(escolhido, agora.date())
        return {
            "data": escolhido.isoformat(),
            "titulo": nome[0].upper() + nome[1:],
            "itens": [_ocorrencia_para_painel(o, agora) for o in self.agenda.ocorrencias_do_dia(escolhido)],
            "tarefas": [t.titulo for t in self.agenda.tarefas() if t.prazo == escolhido],
        }

    def dados_semana(self, deslocamento: int = 0) -> dict:
        """Semana (segunda a domingo) para a janela da agenda do painel, mais as tarefas."""
        agora = self.agora()
        segunda = agora.date() - timedelta(days=agora.weekday()) + timedelta(weeks=deslocamento)
        dias = []
        for k in range(7):
            dia = segunda + timedelta(days=k)
            dias.append({
                "data": dia.isoformat(),
                "nome": NOMES_DIAS[dia.weekday()].split("-")[0],
                "numero": dia.day,
                "hoje": dia == agora.date(),
                "passado": dia < agora.date(),
                "itens": [_ocorrencia_para_painel(o, agora) for o in self.agenda.ocorrencias_do_dia(dia)],
            })
        tarefas = [{
            "id": t.id, "titulo": t.titulo, "prazo": t.prazo.isoformat() if t.prazo else "",
            "prazo_texto": falar_dia_curto(t.prazo, agora.date()) if t.prazo else "",
            "atrasada": bool(t.prazo and t.prazo < agora.date()), "urgente": t.prioridade == 1, "feita": t.concluida,
        } for t in self.agenda.tarefas(concluidas_desde=agora - timedelta(days=1))]
        proximas = self.agenda.proximas(agora, 1)
        return {
            "titulo": f"{segunda:%d/%m} a {segunda + timedelta(days=6):%d/%m}",
            "deslocamento": deslocamento,
            "dias": dias,
            "tarefas": tarefas,
            "proximo": self.proximo() if proximas else "",
            "rotina": self.descrever_rotina(),
        }


# ------------------------------------------------------------------ auxiliares
def _juntar(antes: Interpretacao, nova: Interpretacao) -> Interpretacao:
    """A interpretação do pedido completada com a da resposta ("Para quando?" -> "amanhã às 3")."""
    campos = {}
    for nome in ("data", "hora", "hora_fim", "duracao", "periodo", "recorrencia", "lembretes", "relativo", "ate_data"):
        valor = getattr(nova, nome)
        if valor not in (None, "", []) or (nome == "lembretes" and valor == []):
            campos[nome] = valor
    if "hora" in campos:
        campos["hora_ambigua"] = nova.hora_ambigua
    return replace(antes, **campos)


def _minutos_entre(inicio: time | None, fim: time | None) -> int | None:
    if not inicio or not fim:
        return None
    minutos = (fim.hour * 60 + fim.minute) - (inicio.hour * 60 + inicio.minute)
    return minutos if minutos > 0 else None


def _arredondar(momento: datetime, minutos: int = 15) -> datetime:
    """Próximo múltiplo de 15 minutos (14h07 -> 14h15)."""
    base = momento.replace(second=0, microsecond=0)
    resto = base.minute % minutos
    return base + timedelta(minutes=(minutos - resto) % minutos) if resto or momento.second else base


def _no_dia(dia_falado: str) -> str:
    """"hoje" fica "hoje"; "sexta-feira, dia 23" vira "na sexta-feira, dia 23"."""
    if dia_falado in ("hoje", "amanhã", "ontem") or dia_falado.startswith("depois de amanhã"):
        return dia_falado
    return ("no " if dia_falado.startswith(("sábado", "domingo")) else "na ") + dia_falado


def _falar_periodo(periodo: str) -> str:
    return {"manha": "de manhã", "tarde": "à tarde", "noite": "à noite", "madrugada": "de madrugada"}.get(periodo, "")


def _ocorrencia_para_painel(o: Ocorrencia, agora: datetime) -> dict:
    evento = o.evento
    return {
        "id": evento.id,
        "data": o.inicio.date().isoformat(),
        "titulo": evento.titulo,
        "hora": "" if evento.dia_inteiro else falar_hora(o.inicio),
        "fim": "" if (evento.dia_inteiro or evento.duracao == 0) else falar_hora(o.fim),
        "dia_inteiro": evento.dia_inteiro,
        "tipo": evento.tipo,
        "categoria": evento.categoria,
        "repete": evento.repete,
        "regra": falar_recorrencia(evento.recorrencia, evento.inicio) if evento.repete else "",
        "passou": o.fim < agora and not evento.dia_inteiro,
        "agora": o.inicio <= agora < o.fim and o.ocupa,
    }


def criar_agente_agenda(llm=None, ao_abrir=None) -> AgenteAgenda:
    """Agente com o banco e os horários do .env (config.py)."""
    from config import (AGENDA_FIM_DIA, AGENDA_HORA_TAREFAS, AGENDA_INICIO_DIA, AGENDA_LEMBRETE_PADRAO,
                        CAMINHO_AGENDA)
    return AgenteAgenda(Agenda(CAMINHO_AGENDA), llm=llm, lembrete_padrao=AGENDA_LEMBRETE_PADRAO,
                        inicio_dia=AGENDA_INICIO_DIA, fim_dia=AGENDA_FIM_DIA, hora_tarefas=AGENDA_HORA_TAREFAS,
                        ao_abrir=ao_abrir)


def iniciar_lembretes(agente: AgenteAgenda, avisar, intervalo: float = 20.0) -> threading.Thread:
    """Confere os lembretes a cada `intervalo` segundos e chama `avisar(texto)` para cada um que vencer."""
    def vigiar():
        while True:
            try:
                for texto in agente.verificar_lembretes():
                    avisar(texto)
            except Exception as exc:
                logger.warning("Falha ao verificar os lembretes da agenda: %s", exc)
            relogio_do_sistema.sleep(intervalo)

    linha = threading.Thread(target=vigiar, name="lembretes-agenda", daemon=True)
    linha.start()
    return linha
