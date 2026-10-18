"""Agenda local: entendimento das frases, conversa com o agente e lembretes (sem Ollama e sem Google)."""
from datetime import date, datetime, time, timedelta

import pytest

import agenda_linguagem as L
from agenda_local import Agenda
from agente_agenda import AgenteAgenda

SEXTA_15H = datetime(2026, 10, 16, 15, 0)  # sexta-feira


@pytest.fixture
def relogio():
    return {"agora": SEXTA_15H}


@pytest.fixture
def agente(tmp_path, relogio):
    return AgenteAgenda(Agenda(str(tmp_path / "agenda.sqlite3")), relogio=lambda: relogio["agora"])


def _interpretar(frase):
    i = L.interpretar(frase, SEXTA_15H)
    return i, L.resolver_momento(i, SEXTA_15H)


# ------------------------------------------------------------------ linguagem
@pytest.mark.parametrize("frase, esperado", [
    ("dentista amanhã às 14h", datetime(2026, 10, 17, 14, 0)),
    ("reunião segunda das 10 às 12", datetime(2026, 10, 19, 10, 0)),
    ("às 3 e meia da tarde", datetime(2026, 10, 16, 15, 30)),
    ("às duas da tarde", datetime(2026, 10, 17, 14, 0)),  # hoje às 14h já passou
    ("às 9", datetime(2026, 10, 16, 21, 0)),  # 9h já passou; 21h ainda dá
    ("hoje às 8 da noite", datetime(2026, 10, 16, 20, 0)),
    ("amanhã às 3", datetime(2026, 10, 17, 15, 0)),
    ("dia 3/11 às 10h30", datetime(2026, 11, 3, 10, 30)),
    ("20 de outubro às 8", datetime(2026, 10, 20, 8, 0)),
    ("daqui a 30 minutos", datetime(2026, 10, 16, 15, 30)),
    ("daqui a uma hora e meia", datetime(2026, 10, 16, 16, 30)),
    ("ao meio-dia", datetime(2026, 10, 17, 12, 0)),
    ("amanhã de manhã", datetime(2026, 10, 17, 9, 0)),
    ("próxima sexta às 10h", datetime(2026, 10, 23, 10, 0)),
])
def test_datas_e_horas(frase, esperado):
    assert _interpretar(frase)[1] == esperado


def test_repeticoes():
    assert L.interpretar("toda segunda, quarta e sexta às 7h").recorrencia == "semanal:0,2,4"
    assert L.interpretar("às terças e quintas").recorrencia == "semanal:1,3"
    assert L.interpretar("na terça").recorrencia == ""  # um dia só
    assert L.interpretar("todo dia às 20h").recorrencia == "diaria"
    assert L.interpretar("dias úteis 6:30").recorrencia == "dias_uteis"
    mensal = L.interpretar("todo dia 5 às 10h", SEXTA_15H)
    assert (mensal.recorrencia, mensal.data) == ("mensal", date(2026, 11, 5))


def test_lembretes_duracao_e_prazo():
    i = L.interpretar("reunião por uma hora e meia com lembrete 1 hora antes")
    assert (i.duracao, i.lembretes) == (90, [60])
    assert L.interpretar("dentista sem lembrete").lembretes == []
    assert L.interpretar("me avisa um dia antes").lembretes == [1440]
    tarefa = L.interpretar("pagar boleto até segunda urgente", SEXTA_15H)
    assert (tarefa.ate_data, tarefa.data, tarefa.prioridade) == (date(2026, 10, 19), None, 1)


def test_titulo_sem_as_partes_de_tempo():
    frase = "reunião com o cliente sexta das 10 às 12"
    assert L.titulo_da_frase(frase, L.interpretar(frase)) == "Reunião com o cliente"
    assert L.titulo_da_frase("as duas tarefas", L.interpretar("as duas tarefas")) == "Duas tarefas"  # não é hora


# ------------------------------------------------------------------ marcar, consultar, mudar
def test_marca_e_consulta(agente):
    assert agente.responder("Beatriz, marca dentista amanhã às 14h") == \
        "Marquei dentista amanhã às 14h, com aviso 15 minutos antes."
    resposta = agente.responder("me lembra de tomar remédio todo dia às 20h")
    assert "todo dia às 20h" in resposta
    assert agente.responder("o que tenho amanhã?") == "Amanhã: dentista às 14h e lembrete de tomar remédio às 20h."
    assert agente.responder("que horas é o dentista?") == "Dentista é amanhã às 14h."
    assert "dentista" in agente.responder("qual meu próximo compromisso?") or "remédio" in agente.responder("qual meu próximo compromisso?")


def test_pergunta_o_que_falta_e_completa_com_a_resposta(agente):
    assert agente.responder("agende pra mim") == "O que eu marco?"
    assert agente.responder("corte de cabelo") == "Para quando eu marco corte de cabelo?"
    assert agente.responder("sábado às 10") == "Marquei corte de cabelo amanhã às 10h, com aviso 15 minutos antes."


def test_pedido_novo_no_lugar_da_resposta_nao_vira_horario(agente):
    agente.responder("me lembra de pagar a luz")
    assert agente.responder("o que tenho amanhã?") == "Nada marcado para amanhã."
    assert agente.agenda.eventos_ativos() == []


def test_conflito_pergunta_e_aceita_outro_horario(agente):
    agente.responder("marca dentista amanhã às 14h")
    pergunta = agente.responder("marca almoço com a Bia amanhã às 14h")
    assert "você já tem dentista" in pergunta and pergunta.endswith("mesmo assim?")
    assert agente.responder("não, às 13h") == "Marquei almoço com a Bia amanhã às 13h, com aviso 15 minutos antes."


def test_remarca_desfaz_e_adia(agente):
    agente.responder("marca dentista amanhã às 14h")
    assert agente.responder("remarca o dentista para terça às 9") == "Pronto: dentista passou para terça-feira (dia 20) às 9h."
    assert agente.responder("desfaz").startswith("Desfeito")
    assert agente.responder("adia o dentista em 1 hora") == "Pronto: dentista passou para amanhã às 15h."


def test_cancela_so_uma_vez_da_rotina(agente):
    agente.responder("marca academia toda segunda, quarta e sexta às 7 da manhã")
    assert agente.responder("cancela a academia").endswith("ou todas?")
    assert agente.responder("só a de segunda").startswith("Cancelei só academia")
    segunda, quarta = date(2026, 10, 19), date(2026, 10, 21)
    assert agente.agenda.ocorrencias_do_dia(segunda) == []
    assert [o.titulo for o in agente.agenda.ocorrencias_do_dia(quarta)] == ["Academia"]
    assert agente.responder("minha rotina") == "Sua rotina: academia toda segunda, quarta e sexta às 7h."


def test_livre(agente):
    agente.responder("marca dentista amanhã às 14h")
    assert agente.responder("estou livre amanhã às 14h?") == "Não: amanhã às 14h você tem dentista (14h às 15h)."
    assert agente.responder("quando estou livre amanhã?") == "Amanhã você está livre das 8h às 14h e das 15h às 22h."
    assert agente.responder("estou livre segunda à tarde?") == "Sim, segunda-feira (dia 19) à tarde você está livre."


def test_dia_inteiro_e_lembrete_sem_hora(agente):
    assert "o dia todo" in agente.responder("marca aniversário da Ana dia 25")
    assert agente.responder("lembra de comprar pão amanhã") == "Combinado: te lembro de comprar pão amanhã às 9h."


def test_frases_sem_verbo(agente):
    assert agente.responder("academia toda segunda às 7h").startswith("Coloquei academia na sua rotina: toda segunda às 7h")
    assert agente.responder("tenho dentista amanhã às 14h") == "Marquei dentista amanhã às 14h, com aviso 15 minutos antes."
    assert agente.responder("tomar remédio todo dia às 22h").startswith("Combinado: vou te lembrar de tomar remédio")
    # Só com a data, ou com "tem ...", ela confirma antes.
    assert agente.responder("aniversário da Ana dia 25") == "Quer que eu marque aniversário da Ana no domingo (25 de outubro)?"
    assert "o dia todo" in agente.responder("sim")
    assert agente.responder("tem jogo do Brasil hoje às 20h") == "Quer que eu marque jogo do Brasil hoje às 20h?"
    assert agente.responder("não") == "Tudo bem, não marquei."
    assert sorted(e.titulo for e in agente.agenda.eventos_ativos()) == ["Academia", "Aniversário da Ana", "Dentista", "Tomar remédio"]


def test_frases_sem_verbo_que_nao_sao_da_agenda(agente):
    for frase in ["previsão do tempo amanhã às 15h", "filmes hoje às 20h", "vou dormir às 23h", "notícias de hoje",
                  "academia essa semana", "tenho alguma coisa amanhã às 10?"]:
        resposta = agente.responder(frase)
        assert resposta is None or not resposta.startswith(("Marquei", "Coloquei", "Quer que eu")), frase
    assert agente.agenda.eventos_ativos() == []


def test_nao_pega_o_que_nao_e_da_agenda(agente):
    for frase in ["faz um PDF da minha agenda", "qual a capital da França?", "lembra que eu te falei do filme?",
                  "o gol foi marcado por quem?", "resumo do dia", "crie uma apresentação sobre agenda 2030",
                  "me lembra o nome daquele filme", "lembra de quando fomos à praia?"]:
        assert agente.responder(frase) is None, frase


# ------------------------------------------------------------------ tarefas e organizar o dia
def test_tarefas_e_organizar_o_dia(agente):
    assert agente.responder("nova tarefa pagar boleto até segunda urgente") == \
        "Anotei a tarefa pagar boleto, para segunda-feira, com prioridade alta."
    agente.responder("adiciona estudar cálculo na minha lista de tarefas, leva 1 hora")
    assert agente.responder("quais minhas tarefas?").startswith("Você tem 2 tarefas pendentes: pagar boleto")
    plano = agente.responder("organiza meu dia")
    assert plano.startswith("Sugestão para hoje: 15h, pagar boleto; 15h40, estudar cálculo")
    assert agente.responder("sim").startswith("Pronto, coloquei 2 blocos")
    assert agente.responder("terminei de pagar o boleto") == "Boa! Marquei pagar boleto como feita. Falta 1."
    # O bloco da tarefa feita sai da agenda; o desfazer devolve os dois.
    assert [o.titulo for o in agente.agenda.ocorrencias_do_dia(SEXTA_15H.date())] == ["Estudar cálculo"]
    agente.responder("desfaz")
    assert len(agente.agenda.ocorrencias_do_dia(SEXTA_15H.date())) == 2


# ------------------------------------------------------------------ lembretes
def test_lembretes_saem_uma_vez_na_hora_certa(agente, relogio):
    agente.responder("marca dentista amanhã às 14h")
    agente.responder("me lembra de tomar remédio todo dia às 20h")
    agente.responder("marca aniversário da Ana amanhã")
    amanha = SEXTA_15H.date() + timedelta(days=1)
    assert agente.verificar_lembretes(datetime.combine(amanha, time(13, 40))) == []
    assert agente.verificar_lembretes(datetime.combine(amanha, time(13, 45))) == ["Daqui a 15 minutos, às 14h: dentista."]
    assert agente.verificar_lembretes(datetime.combine(amanha, time(13, 46))) == []  # já avisado
    assert agente.verificar_lembretes(datetime(2026, 10, 16, 20, 1)) == ["Lembrete: tomar remédio."]
    assert agente.verificar_lembretes(datetime.combine(amanha, time(9, 0))) == ["Lembrete: hoje é dia de aniversário da Ana."]
    # PC desligado na hora: aviso atrasado demais não sai mais.
    assert agente.verificar_lembretes(datetime.combine(amanha, time(20, 30))) == []


def test_dois_processos_nao_avisam_duas_vezes(tmp_path, relogio):
    caminho = str(tmp_path / "agenda.sqlite3")
    um = AgenteAgenda(Agenda(caminho), relogio=lambda: relogio["agora"])
    outro = AgenteAgenda(Agenda(caminho), relogio=lambda: relogio["agora"])
    um.responder("me avisa daqui a 30 minutos de tirar o bolo do forno")
    momento = SEXTA_15H + timedelta(minutes=30)
    assert um.verificar_lembretes(momento) + outro.verificar_lembretes(momento) == ["Lembrete: tirar o bolo do forno."]


def test_main_poe_a_agenda_na_saudacao_e_manda_os_lembretes(monkeypatch, tmp_path):
    import main

    falas, painel, whatsapp = [], [], []
    monkeypatch.setattr(main.AGENDA, "agenda", Agenda(str(tmp_path / "agenda.sqlite3")))
    monkeypatch.setattr(main.AGENDA, "agora", lambda: SEXTA_15H)
    monkeypatch.setattr(main, "falar_async", falas.append)
    monkeypatch.setattr(main, "avisar_painel", lambda tipo, texto, **dados: painel.append((tipo, texto)))
    main.AGENDA.responder("marca dentista hoje às 18h")
    main.cumprimentar()
    assert "Hoje você ainda tem 1 compromisso; o próximo é dentista às 18h." in falas[-1]
    assert any(falas[-1].endswith(convite) for convite in main.todas_as_frases("convite"))

    class CanalFalso:
        def status(self):
            return {"status": "conectado"}

        def enviar(self, texto, arquivos=()):
            whatsapp.append(texto)

    monkeypatch.setattr(main, "CANAL_WHATSAPP", CanalFalso())
    main.avisar_lembrete("Daqui a 15 minutos, às 18h: dentista.")
    assert falas[-1] == "Daqui a 15 minutos, às 18h: dentista."
    assert ("lembrete", "Daqui a 15 minutos, às 18h: dentista.") in painel
    assert whatsapp == ["⏰ Daqui a 15 minutos, às 18h: dentista."]


def test_ferramenta_e_api_marcam_na_agenda_local(monkeypatch, tmp_path):
    import tools

    agente = AgenteAgenda(Agenda(str(tmp_path / "agenda.sqlite3")))
    monkeypatch.setattr(tools, "_AGENTE_AGENDA", agente)
    monkeypatch.setattr(tools.os, "startfile", lambda caminho: (_ for _ in ()).throw(AssertionError("nada de .ics")),
                        raising=False)
    assert tools.criar_evento_agenda("Dentista", "10/12 às 14", quando_lembrar="30 minutos").startswith("Marquei dentista")
    evento = agente.agenda.eventos_ativos()[0]
    assert (evento.titulo, evento.inicio.hour, evento.lembretes) == ("Dentista", 14, [30])
    # Data que não dá para entender: nada é marcado, e a resposta é a pergunta (a API devolve 422).
    assert tools.criar_evento_agenda("Reunião", "quando der") == "Para quando eu marco reunião?"
    assert len(agente.agenda.eventos_ativos()) == 1
    assert "dentista às 14h" in tools.listar_compromissos() or tools.listar_compromissos() == "Nada marcado nesse período."


def test_calendario_do_mes_e_dia_escolhido(agente):
    agente.responder("academia toda segunda às 7h")
    agente.responder("tenho dentista amanhã às 14h")
    agente.responder("nova tarefa pagar boleto até dia 20")
    mes = agente.dados_mes()
    assert mes["titulo"] == "outubro de 2026" and mes["anterior"] == [2026, 9] and mes["seguinte"] == [2026, 11]
    assert (mes["dias"][0]["data"], mes["dias"][-1]["data"]) == ("2026-09-28", "2026-11-01")  # semanas inteiras
    marcados = {d["numero"]: (d["quantidade"], d["tarefas"]) for d in mes["dias"] if d["do_mes"] and (d["quantidade"] or d["tarefas"])}
    assert marcados == {17: (1, 0), 19: (1, 0), 20: (0, 1), 26: (1, 0)}
    dia = agente.dados_dia("2026-10-17")
    assert dia["titulo"] == "Amanhã" and [i["titulo"] for i in dia["itens"]] == ["Dentista"]
    assert agente.dados_dia("2026-10-20")["tarefas"] == ["Pagar boleto"]
    assert agente.dados_mes(2026, 12)["seguinte"] == [2027, 1]


def test_dados_da_semana_para_o_painel(agente):
    agente.responder("marca dentista amanhã às 14h")
    agente.responder("nova tarefa pagar boleto")
    dados = agente.dados_semana()
    assert [d["nome"] for d in dados["dias"]][:2] == ["segunda", "terça"]
    sabado = next(d for d in dados["dias"] if d["data"] == "2026-10-17")
    assert sabado["itens"][0]["titulo"] == "Dentista" and sabado["itens"][0]["categoria"] == "saude"
    assert dados["tarefas"][0]["titulo"] == "Pagar boleto"
