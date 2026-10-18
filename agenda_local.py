"""Agenda local da Beatriz: compromissos, rotina (eventos que se repetem) e tarefas num SQLite no PC.

Sem Google e sem nuvem. O processo principal (voz, WhatsApp, lembretes) e o painel abrem o mesmo
arquivo; cada operação abre e fecha a conexão, e o SQLite cuida das travas entre os processos.
Os horários são datetimes sem fuso, no relógio do Windows.

Recorrência (`Evento.recorrencia`): "" (uma vez), "diaria", "dias_uteis", "semanal:0,2,4"
(dias da semana, segunda = 0), "mensal" (mesmo dia do mês) ou "anual". Uma ocorrência cancelada
ou remarcada de um evento repetido vira uma exceção (tabela `excecoes`); a remarcada ganha um
evento avulso no novo horário.
"""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

TIPOS = ("compromisso", "lembrete", "foco")  # foco: bloco de tarefa encaixado no "organiza meu dia"
CATEGORIAS = ("pessoal", "trabalho", "estudo", "saude", "foco")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    inicio TEXT NOT NULL,
    duracao INTEGER NOT NULL DEFAULT 60,
    dia_inteiro INTEGER NOT NULL DEFAULT 0,
    tipo TEXT NOT NULL DEFAULT 'compromisso',
    categoria TEXT NOT NULL DEFAULT 'pessoal',
    recorrencia TEXT NOT NULL DEFAULT '',
    repetir_ate TEXT,
    lembretes TEXT NOT NULL DEFAULT '[]',
    local TEXT NOT NULL DEFAULT '',
    notas TEXT NOT NULL DEFAULT '',
    tarefa_id INTEGER,
    criado_em TEXT NOT NULL,
    cancelado INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS excecoes (
    evento_id INTEGER NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (evento_id, data)
);
CREATE TABLE IF NOT EXISTS tarefas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    prazo TEXT,
    prioridade INTEGER NOT NULL DEFAULT 2,
    duracao INTEGER NOT NULL DEFAULT 30,
    concluida_em TEXT,
    criada_em TEXT NOT NULL,
    removida INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS avisos (
    chave TEXT PRIMARY KEY,
    em TEXT NOT NULL
);
"""

FORMATO = "%Y-%m-%dT%H:%M"


def _texto_data_hora(valor: datetime) -> str:
    return valor.strftime(FORMATO)


def _ler_data_hora(texto: str) -> datetime:
    return datetime.strptime(texto[:16], FORMATO)


def _ler_data(texto) -> date | None:
    return date.fromisoformat(texto) if texto else None


@dataclass
class Evento:
    id: int
    titulo: str
    inicio: datetime
    duracao: int = 60  # minutos; 0 = só um aviso
    dia_inteiro: bool = False
    tipo: str = "compromisso"
    categoria: str = "pessoal"
    recorrencia: str = ""
    repetir_ate: date | None = None
    lembretes: list = field(default_factory=list)  # minutos antes do início
    local: str = ""
    notas: str = ""
    tarefa_id: int | None = None
    cancelado: bool = False

    @property
    def repete(self) -> bool:
        return bool(self.recorrencia)

    def repete_no_dia(self, dia: date) -> bool:
        if dia < self.inicio.date() or (self.repetir_ate and dia > self.repetir_ate):
            return False
        regra = self.recorrencia
        if not regra:
            return dia == self.inicio.date()
        if regra == "diaria":
            return True
        if regra == "dias_uteis":
            return dia.weekday() < 5
        if regra.startswith("semanal"):
            dias = regra.split(":", 1)[1] if ":" in regra else str(self.inicio.weekday())
            return dia.weekday() in {int(d) for d in dias.split(",") if d.strip().isdigit()}
        if regra == "mensal":
            return dia.day == self.inicio.day
        if regra == "anual":
            return (dia.month, dia.day) == (self.inicio.month, self.inicio.day)
        return False


@dataclass
class Ocorrencia:
    """Um evento num dia: o próprio evento (avulso) ou uma das vezes de um evento repetido."""
    evento: Evento
    inicio: datetime

    @property
    def fim(self) -> datetime:
        if self.evento.dia_inteiro:
            return datetime.combine(self.inicio.date() + timedelta(days=1), time())
        return self.inicio + timedelta(minutes=self.evento.duracao)

    @property
    def ocupa(self) -> bool:
        """Conta como horário ocupado (lembretes e eventos de dia inteiro não ocupam)."""
        return self.evento.tipo != "lembrete" and not self.evento.dia_inteiro and self.evento.duracao > 0

    @property
    def titulo(self) -> str:
        return self.evento.titulo


@dataclass
class Tarefa:
    id: int
    titulo: str
    prazo: date | None = None
    prioridade: int = 2  # 1 alta, 2 normal, 3 baixa
    duracao: int = 30
    concluida_em: datetime | None = None
    criada_em: datetime | None = None

    @property
    def concluida(self) -> bool:
        return self.concluida_em is not None


class Agenda:
    """Acesso ao banco da agenda. Seguro para usar de várias threads e processos."""

    def __init__(self, caminho: str):
        self.caminho = caminho
        self._pronta = False
        self._trava = threading.Lock()

    # ------------------------------------------------------------------ conexão
    @contextmanager
    def _conexao(self):
        self._preparar()
        conexao = sqlite3.connect(self.caminho, timeout=10)
        conexao.row_factory = sqlite3.Row
        try:
            with conexao:  # commit no fim, rollback se der erro
                yield conexao
        finally:
            conexao.close()

    def _preparar(self):
        if self._pronta:
            return
        with self._trava:
            if self._pronta:
                return
            pasta = os.path.dirname(os.path.abspath(self.caminho))
            os.makedirs(pasta, exist_ok=True)
            conexao = sqlite3.connect(self.caminho, timeout=10)
            try:
                conexao.execute("PRAGMA journal_mode=WAL")  # leituras do painel não travam a gravação da voz
                conexao.executescript(ESQUEMA)
                conexao.commit()
            finally:
                conexao.close()
            self._pronta = True

    # ------------------------------------------------------------------ eventos
    @staticmethod
    def _evento(linha) -> Evento:
        return Evento(
            id=linha["id"], titulo=linha["titulo"], inicio=_ler_data_hora(linha["inicio"]),
            duracao=linha["duracao"], dia_inteiro=bool(linha["dia_inteiro"]), tipo=linha["tipo"],
            categoria=linha["categoria"], recorrencia=linha["recorrencia"],
            repetir_ate=_ler_data(linha["repetir_ate"]), lembretes=json.loads(linha["lembretes"] or "[]"),
            local=linha["local"], notas=linha["notas"], tarefa_id=linha["tarefa_id"],
            cancelado=bool(linha["cancelado"]),
        )

    def criar_evento(self, titulo: str, inicio: datetime, duracao: int = 60, *, dia_inteiro: bool = False,
                     tipo: str = "compromisso", categoria: str = "pessoal", recorrencia: str = "",
                     repetir_ate: date | None = None, lembretes=(), local: str = "", notas: str = "",
                     tarefa_id: int | None = None) -> Evento:
        if dia_inteiro:
            inicio = datetime.combine(inicio.date(), time())
        with self._conexao() as c:
            cursor = c.execute(
                "INSERT INTO eventos (titulo, inicio, duracao, dia_inteiro, tipo, categoria, recorrencia, repetir_ate,"
                " lembretes, local, notas, tarefa_id, criado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (titulo.strip(), _texto_data_hora(inicio), int(duracao), int(dia_inteiro), tipo, categoria,
                 recorrencia, repetir_ate.isoformat() if repetir_ate else None, json.dumps(list(lembretes)),
                 local, notas, tarefa_id, _texto_data_hora(datetime.now())),
            )
            return self._evento(c.execute("SELECT * FROM eventos WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def obter_evento(self, evento_id: int) -> Evento | None:
        with self._conexao() as c:
            linha = c.execute("SELECT * FROM eventos WHERE id = ?", (evento_id,)).fetchone()
        return self._evento(linha) if linha else None

    def atualizar_evento(self, evento_id: int, **campos) -> Evento | None:
        conversores = {
            "inicio": _texto_data_hora,
            "repetir_ate": lambda d: d.isoformat() if d else None,
            "lembretes": lambda v: json.dumps(list(v)),
            "dia_inteiro": int, "cancelado": int,
        }
        permitidos = {"titulo", "inicio", "duracao", "dia_inteiro", "tipo", "categoria", "recorrencia",
                      "repetir_ate", "lembretes", "local", "notas", "cancelado"}
        valores = {k: conversores.get(k, lambda v: v)(v) for k, v in campos.items() if k in permitidos}
        if valores:
            with self._conexao() as c:
                c.execute(f"UPDATE eventos SET {', '.join(f'{k} = ?' for k in valores)} WHERE id = ?",
                          (*valores.values(), evento_id))
        return self.obter_evento(evento_id)

    def cancelar_evento(self, evento_id: int):
        self.atualizar_evento(evento_id, cancelado=True)

    def restaurar_evento(self, evento_id: int):
        self.atualizar_evento(evento_id, cancelado=False)

    def cancelar_ocorrencia(self, evento_id: int, dia: date):
        with self._conexao() as c:
            c.execute("INSERT OR IGNORE INTO excecoes (evento_id, data) VALUES (?, ?)", (evento_id, dia.isoformat()))

    def restaurar_ocorrencia(self, evento_id: int, dia: date):
        with self._conexao() as c:
            c.execute("DELETE FROM excecoes WHERE evento_id = ? AND data = ?", (evento_id, dia.isoformat()))

    def eventos_ativos(self) -> list[Evento]:
        with self._conexao() as c:
            return [self._evento(l) for l in c.execute("SELECT * FROM eventos WHERE cancelado = 0 ORDER BY inicio")]

    def ocorrencias(self, de: datetime, ate: datetime) -> list[Ocorrencia]:
        """Ocorrências que acontecem (ao menos em parte) entre `de` e `ate`, em ordem de início."""
        with self._conexao() as c:
            linhas = c.execute("SELECT * FROM eventos WHERE cancelado = 0").fetchall()
            excecoes = {(l["evento_id"], l["data"]) for l in c.execute("SELECT evento_id, data FROM excecoes")}
        resultado = []
        for evento in map(self._evento, linhas):
            primeiro = max(evento.inicio.date(), de.date() - timedelta(days=1))  # eventos que passam da meia-noite
            dia = primeiro
            while dia <= ate.date():
                if evento.repete_no_dia(dia) and (evento.id, dia.isoformat()) not in excecoes:
                    ocorrencia = Ocorrencia(evento, datetime.combine(dia, evento.inicio.time()))
                    if ocorrencia.inicio < ate and (ocorrencia.fim > de or ocorrencia.inicio >= de):
                        resultado.append(ocorrencia)
                if not evento.repete:
                    break
                dia += timedelta(days=1)
        resultado.sort(key=lambda o: (o.inicio, not o.evento.dia_inteiro, o.evento.titulo.lower()))
        return resultado

    def ocorrencias_do_dia(self, dia: date) -> list[Ocorrencia]:
        inicio = datetime.combine(dia, time())
        return [o for o in self.ocorrencias(inicio, inicio + timedelta(days=1)) if o.inicio.date() == dia]

    def proximas(self, agora: datetime, quantidade: int = 5, dias: int = 60) -> list[Ocorrencia]:
        """As próximas ocorrências a começar (as de dia inteiro de hoje também contam)."""
        hoje = datetime.combine(agora.date(), time())
        futuras = [o for o in self.ocorrencias(hoje, agora + timedelta(days=dias))
                   if o.inicio >= agora or (o.evento.dia_inteiro and o.inicio.date() >= agora.date())]
        return futuras[:quantidade]

    def conflitos(self, inicio: datetime, duracao: int, ignorar_id: int | None = None) -> list[Ocorrencia]:
        fim = inicio + timedelta(minutes=max(duracao, 1))
        return [o for o in self.ocorrencias(inicio, fim)
                if o.ocupa and o.evento.id != ignorar_id and o.inicio < fim and o.fim > inicio]

    def horarios_livres(self, de: datetime, ate: datetime, minimo: int = 30) -> list[tuple[datetime, datetime]]:
        """Intervalos livres de pelo menos `minimo` minutos entre `de` e `ate`."""
        livres, cursor = [], de
        for ocorrencia in self.ocorrencias(de, ate):
            if not ocorrencia.ocupa:
                continue
            if ocorrencia.inicio > cursor:
                livres.append((cursor, min(ocorrencia.inicio, ate)))
            cursor = max(cursor, ocorrencia.fim)
        if cursor < ate:
            livres.append((cursor, ate))
        return [(a, b) for a, b in livres if (b - a) >= timedelta(minutes=minimo)]

    # ------------------------------------------------------------------ tarefas
    @staticmethod
    def _tarefa(linha) -> Tarefa:
        return Tarefa(
            id=linha["id"], titulo=linha["titulo"], prazo=_ler_data(linha["prazo"]), prioridade=linha["prioridade"],
            duracao=linha["duracao"],
            concluida_em=_ler_data_hora(linha["concluida_em"]) if linha["concluida_em"] else None,
            criada_em=_ler_data_hora(linha["criada_em"]),
        )

    def criar_tarefa(self, titulo: str, prazo: date | None = None, prioridade: int = 2, duracao: int = 30) -> Tarefa:
        with self._conexao() as c:
            cursor = c.execute(
                "INSERT INTO tarefas (titulo, prazo, prioridade, duracao, criada_em) VALUES (?,?,?,?,?)",
                (titulo.strip(), prazo.isoformat() if prazo else None, prioridade, duracao,
                 _texto_data_hora(datetime.now())),
            )
            return self._tarefa(c.execute("SELECT * FROM tarefas WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def obter_tarefa(self, tarefa_id: int) -> Tarefa | None:
        with self._conexao() as c:
            linha = c.execute("SELECT * FROM tarefas WHERE id = ? AND removida = 0", (tarefa_id,)).fetchone()
        return self._tarefa(linha) if linha else None

    def tarefas(self, pendentes: bool = True, concluidas_desde: datetime | None = None) -> list[Tarefa]:
        """Pendentes (por prazo e prioridade) e, se pedido, as concluídas a partir de uma data."""
        with self._conexao() as c:
            linhas = c.execute("SELECT * FROM tarefas WHERE removida = 0").fetchall()
        todas = [self._tarefa(l) for l in linhas]
        escolhidas = [t for t in todas if (pendentes and not t.concluida)
                      or (concluidas_desde and t.concluida and t.concluida_em >= concluidas_desde)]
        return sorted(escolhidas, key=lambda t: (t.concluida, t.prazo or date.max, t.prioridade, t.id))

    def concluir_tarefa(self, tarefa_id: int, concluida: bool = True):
        with self._conexao() as c:
            c.execute("UPDATE tarefas SET concluida_em = ? WHERE id = ?",
                      (_texto_data_hora(datetime.now()) if concluida else None, tarefa_id))

    def remover_tarefa(self, tarefa_id: int, removida: bool = True):
        with self._conexao() as c:
            c.execute("UPDATE tarefas SET removida = ? WHERE id = ?", (int(removida), tarefa_id))

    # ------------------------------------------------------------------ avisos já dados
    def ja_avisado(self, chave: str) -> bool:
        with self._conexao() as c:
            return c.execute("SELECT 1 FROM avisos WHERE chave = ?", (chave,)).fetchone() is not None

    def marcar_avisado(self, chave: str) -> bool:
        """Grava o aviso; False se outro processo já tinha gravado (então não avisa duas vezes)."""
        with self._conexao() as c:
            cursor = c.execute("INSERT OR IGNORE INTO avisos (chave, em) VALUES (?, ?)",
                               (chave, _texto_data_hora(datetime.now())))
            return cursor.rowcount == 1
