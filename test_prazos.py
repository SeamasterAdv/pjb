"""Prazos processuais: o calendário forense, a conta do vencimento e a conversa com o agente (sem LLM nem internet).

As datas esperadas foram contadas à mão no calendário de 2026/2027 (Páscoa em 5/4/2026 e 28/3/2027)."""
from datetime import date, datetime

import pytest

from agenda_local import Agenda
from agente_prazos import AgentePrazos
from prazos import Calendario, calcular, dias_uteis_entre, ler_datas_extras, pascoa, suspenso, tipos_citados

QUARTA_10H = datetime(2026, 10, 28, 10, 0)  # quarta-feira


@pytest.fixture
def calendario(tmp_path):
    return Calendario(caminho=str(tmp_path / "sem_expediente.json"))


@pytest.fixture
def agenda(tmp_path):
    return Agenda(str(tmp_path / "agenda.sqlite3"))


@pytest.fixture
def agente(calendario, agenda):
    return AgentePrazos(calendario, agenda=lambda: agenda, relogio=lambda: QUARTA_10H)


# ------------------------------------------------------------------ calendário
def test_pascoa_e_feriados_moveis(calendario):
    assert pascoa(2026) == date(2026, 4, 5)
    assert pascoa(2027) == date(2027, 3, 28)
    feriados = calendario.feriados(2026)
    assert feriados[date(2026, 4, 3)] == "sexta-feira santa"
    assert feriados[date(2026, 2, 16)] == "segunda-feira de Carnaval"
    assert feriados[date(2026, 6, 4)] == "Corpus Christi"
    assert feriados[date(2026, 11, 20)] == "Dia da Consciência Negra"
    assert date(2023, 11, 20) not in calendario.feriados(2023)  # nacional só a partir de 2024 (Lei 14.759/2023)


def test_sem_feriados_forenses_ficam_so_os_nacionais():
    calendario = Calendario(forenses=False)
    feriados = calendario.feriados(2026)
    assert date(2026, 2, 16) not in feriados and date(2026, 12, 8) not in feriados
    assert date(2026, 4, 3) in feriados  # a sexta-feira santa é nacional


def test_feriados_extras_do_env():
    assert ler_datas_extras("30/11, 24/12/2026; lixo 31/02") == ({(30, 11)}, {date(2026, 12, 24)})
    calendario = Calendario(extras="30/11, 24/12/2026")
    assert calendario.motivo(date(2027, 11, 30)) == "feriado local"
    assert calendario.motivo(date(2026, 12, 24)) == "feriado local"
    assert calendario.motivo(date(2027, 12, 24)) is None


def test_dias_marcados_ficam_no_arquivo(tmp_path):
    caminho = str(tmp_path / "dias.json")
    Calendario(caminho=caminho).marcar_sem_expediente(date(2026, 10, 30))
    outro = Calendario(caminho=caminho)  # outro processo lê o mesmo arquivo
    assert outro.motivo(date(2026, 10, 30)) == "sem expediente"
    assert outro.desmarcar(date(2026, 10, 30))
    assert Calendario(caminho=caminho).motivo(date(2026, 10, 30)) is None


def test_suspensao_de_fim_de_ano():
    assert suspenso(date(2026, 12, 20)) and suspenso(date(2027, 1, 20))
    assert not suspenso(date(2026, 12, 19)) and not suspenso(date(2027, 1, 21))


# ------------------------------------------------------------------ a conta
def test_contestacao_em_dias_uteis_pula_finados(calendario):
    c = calcular(date(2026, 10, 28), 15, calendario=calendario)
    assert (c.inicio, c.vencimento) == (date(2026, 10, 29), date(2026, 11, 19))
    assert c.pulados == [(date(2026, 11, 2), "Finados")]


def test_recesso_suspende_o_prazo_civel(calendario):
    c = calcular(date(2026, 12, 15), 15, calendario=calendario)
    assert c.vencimento == date(2027, 2, 5) and c.suspensao


def test_intimacao_na_suspensao_comeca_depois_de_20_de_janeiro(calendario):
    c = calcular(date(2027, 1, 5), 10, "juizado", calendario=calendario)
    assert c.inicio == date(2027, 1, 21) and c.intimacao_na_suspensao
    assert c.vencimento == date(2027, 2, 3)


def test_intimacao_no_sabado_vale_na_segunda(calendario):
    c = calcular(date(2026, 10, 24), 5, calendario=calendario)
    assert c.considerada == date(2026, 10, 26)
    assert (c.inicio, c.vencimento) == (date(2026, 10, 27), date(2026, 11, 3))


def test_disponibilizacao_no_dje(calendario):
    # disponibilizado na sexta 30/10; publicação no primeiro dia útil (3/11, porque 2/11 é Finados); conta de 4/11
    c = calcular(date(2026, 10, 30), 5, calendario=calendario, disponibilizacao=True)
    assert c.publicacao == date(2026, 11, 3)
    assert (c.inicio, c.vencimento) == (date(2026, 11, 4), date(2026, 11, 10))


def test_penal_corrido_comeca_no_dia_util_e_prorroga(calendario):
    # intimação na sexta: começa no primeiro dia útil (Súmula 310; 2/11 é feriado) e o 5º dia cai no sábado
    c = calcular(date(2026, 10, 30), 5, "penal", calendario=calendario)
    assert c.inicio == date(2026, 11, 3)
    assert c.prorrogado_de == date(2026, 11, 7) and c.vencimento == date(2026, 11, 9)


def test_penal_com_e_sem_a_suspensao(calendario):
    solto = calcular(date(2026, 12, 17), 5, "penal", calendario=calendario)
    assert solto.vencimento == date(2027, 1, 25) and solto.suspensao
    preso = calcular(date(2026, 12, 17), 5, "penal", calendario=calendario, suspender=False)
    assert preso.vencimento == date(2026, 12, 22) and not preso.suspensao


def test_dias_corridos_fora_do_penal_nao_suspendem(calendario):
    c = calcular(date(2026, 12, 17), 5, "corridos", calendario=calendario)
    assert c.inicio == date(2026, 12, 18)
    assert c.vencimento == date(2026, 12, 22)  # o 5º dia, terça, sem suspensão


def test_dia_marcado_sem_expediente_entra_na_conta(calendario):
    calendario.marcar_sem_expediente(date(2026, 10, 29))
    c = calcular(date(2026, 10, 28), 15, calendario=calendario)
    assert c.vencimento == date(2026, 11, 23)  # 20/11 é feriado, então pula para segunda


def test_prazo_absurdo_e_recusado(calendario):
    with pytest.raises(ValueError):
        calcular(date(2026, 10, 28), 0, calendario=calendario)


def test_dias_uteis_entre(calendario):
    assert dias_uteis_entre(date(2026, 10, 28), date(2026, 11, 19), calendario) == 15
    assert dias_uteis_entre(date(2026, 11, 19), date(2026, 10, 28), calendario) == 0


@pytest.mark.parametrize("frase, chave", [
    ("contrarrazoes da apelacao", "contrarrazoes"),
    ("embargos a execucao", "embargos_execucao"),
    ("embargos de declaracao", "embargos_declaracao"),
    ("razoes de apelacao", "razoes_apelacao"),
    ("impugnacao ao cumprimento de sentenca", "impugnacao_cumprimento"),
])
def test_o_nome_mais_longo_ganha(frase, chave):
    achados = tipos_citados(frase)
    assert max(achados, key=lambda a: a[1])[0].chave == chave


# ------------------------------------------------------------------ o agente
def test_contestacao_intimado_hoje(agente):
    r = agente.responder("Beatriz, fui intimado hoje, quando vence a contestação?")
    assert "15 dias úteis (art. 335 do CPC)" in r.fala
    assert "quinta-feira, 19 de novembro" in r.fala
    assert "2/11 (Finados)" in r.fala
    assert r.fala.endswith("Quer que eu marque na agenda?")
    assert "Vencimento: 19/11/2026" in r.painel and "arts. 231 e 335 do CPC" in r.painel


def test_sim_marca_a_tarefa_e_o_lembrete(agente, agenda):
    agente.responder("fui intimado hoje, quando vence a contestação?")
    assert agente.esperando_resposta()
    r = agente.responder("sim")
    assert r.agenda_mudou and "Marquei na agenda" in r.fala
    tarefa, = agenda.tarefas()
    assert tarefa.titulo == "Prazo: Contestação" and tarefa.prazo == date(2026, 11, 19) and tarefa.prioridade == 1
    lembrete, = agenda.eventos_ativos()
    assert lembrete.tipo == "lembrete" and lembrete.inicio.date() == date(2026, 11, 17)


def test_nao_deixa_sem_marcar(agente, agenda):
    agente.responder("fui intimado hoje, quando vence a contestação?")
    assert agente.responder("não").fala == "Certo, não marquei."
    assert agenda.tarefas() == []


def test_marca_direto_quando_pedido_e_nao_repete(agente, agenda):
    frase = "intimado hoje, prazo da apelação, e marca na agenda"
    assert "Marquei na agenda" in agente.responder(frase).fala
    assert "Já estava na agenda" in agente.responder(frase).fala
    assert len(agenda.tarefas()) == 1


def test_sem_data_pergunta_e_entende_a_resposta(agente):
    r = agente.responder("qual o prazo dos embargos de declaração?")
    assert r.fala == "Os embargos de declaração: 5 dias úteis (art. 1.023 do CPC). De quando é a intimação?"
    r = agente.responder("sexta")  # a última sexta, 23/10
    assert "Intimação: 23/10/2026" in r.painel and "Vencimento: 30/10/2026" in r.painel


def test_sem_prazo_pergunta_qual(agente):
    r = agente.responder("fui intimado ontem, qual o prazo?")
    assert r.fala.startswith("Qual é o prazo")
    r = agente.responder("dez dias")
    assert r.fala.startswith("Prazo de 10 dias úteis.") and "Intimação: 27/10/2026" in r.painel


@pytest.mark.parametrize("frase, esperado", [
    ("fui intimado dia 20, prazo da contestação", "Intimação: 20/10/2026"),
    ("publicado na sexta, prazo da apelação", "Intimação: 23/10/2026"),
    ("intimado em 17 de dezembro, prazo da contestação", "Intimação: 17/12/2026"),  # longe demais para ser passado
    ("vou ser intimado dia 3 de novembro, prazo da contestação", "Intimação: 03/11/2026"),
])
def test_data_dita_no_passado(agente, frase, esperado):
    assert esperado in agente.responder(frase).painel


def test_rito_trabalhista_e_apelacao_vira_recurso_ordinario(agente):
    r = agente.responder("fui intimado hoje, prazo da apelação trabalhista")
    assert r.fala.startswith("O recurso ordinário: 8 dias úteis (art. 895 da CLT).")
    assert "contra a sentença cabe recurso ordinário" in r.painel


def test_rito_penal_em_dias_corridos_e_reu_preso(agente):
    r = agente.responder("réu preso, intimado dia 17 de dezembro, prazo da apelação no processo penal")
    assert "5 dias corridos (art. 593 do CPP)" in r.fala
    assert "Vencimento: 22/12/2026" in r.painel and "réu preso (art. 798-A do CPP)" in r.painel


def test_prazo_em_dobro_da_fazenda(agente):
    r = agente.responder("sou procurador do município, intimado hoje, prazo da contestação")
    assert "30 dias úteis (art. 335 do CPC), em dobro pela Fazenda Pública (art. 183 do CPC)" in r.fala


def test_litisconsortes_em_autos_eletronicos_nao_dobram(agente):
    r = agente.responder("litisconsortes com advogados diferentes, intimado hoje, prazo da contestação")
    assert "15 dias úteis" in r.fala and "art. 229, § 2º" in r.painel


def test_ministerio_publico_no_penal_nao_dobra(agente):
    r = agente.responder("sou promotor, intimado hoje, prazo do recurso em sentido estrito")
    assert "5 dias corridos" in r.fala and "não tem prazo em dobro" in r.painel


def test_publicado_no_dje_mostra_a_conta_da_disponibilizacao(agente):
    r = agente.responder("prazo da apelação, publicado no DJe ontem")
    assert "Se foi a disponibilização no DJe" in r.painel


def test_impugnacao_comeca_depois_do_pagamento(agente):
    r = agente.responder("fui intimado hoje para pagar, prazo da impugnação ao cumprimento de sentença")
    assert "começa quando acaba o prazo do pagamento no cumprimento de sentença" in r.fala
    assert "Vencimento: 14/12/2026" in r.painel


def test_rito_sem_o_prazo_pede_os_dias(agente):
    r = agente.responder("prazo da contestação no processo penal, intimado hoje")
    assert r.fala.startswith("Não tenho o prazo da contestação no processo penal.")


def test_prazo_vencido(agente):
    r = agente.responder("intimado em 1 de setembro de 2026, prazo dos embargos de declaração")
    assert "Esse prazo já venceu." in r.fala and "Quer que eu marque" not in r.fala


def test_conta_dias_uteis_sem_peca(agente):
    r = agente.responder("conta 10 dias úteis a partir de hoje")
    assert r.fala.startswith("Prazo de 10 dias úteis.") and "Vencimento: 12/11/2026" in r.painel


def test_dia_sem_expediente_dito_e_esquecido(agente, calendario):
    assert agente.responder("dia 30 de outubro não tem expediente").fala.startswith("Anotado")
    assert calendario.motivo(date(2026, 10, 30)) == "sem expediente"
    assert "volta a contar" in agente.responder("esquece o dia 30 de outubro sem expediente").fala
    assert calendario.motivo(date(2026, 10, 30)) is None
    assert agente.responder("dia 30 de outubro os prazos ficam suspensos?") is None  # pergunta não marca


def test_lista_os_feriados(agente):
    r = agente.responder("quais feriados você considera?")
    assert "2/11 (Finados)" in r.fala and "20 de dezembro a 20 de janeiro" in r.fala
    assert "08/12/2026 (terça-feira): Dia da Justiça" in r.painel


@pytest.mark.parametrize("frase", [
    "qual o prazo de entrega do trabalho?",
    "o que tenho hoje?",
    "qual o prazo de prescrição do furto?",
    "prazo de validade do leite",
    "me explica o artigo 335 do CPC",
    "vou apelar pro seu bom senso",
    "tenho um prazo de 15 dias pra entregar o relatório",
])
def test_frases_que_nao_sao_de_prazo(agente, frase):
    assert agente.responder(frase) is None


def test_main_marca_na_agenda_do_agente_de_agenda(agenda, calendario, monkeypatch):
    import main  # importado aqui, depois do conftest: a agenda e o calendário são trocados neste teste

    monkeypatch.setattr(main.AGENDA, "agenda", agenda)
    monkeypatch.setattr(main.PRAZOS, "calendario", calendario)
    r = main.PRAZOS.responder("intimado hoje, prazo da contestação, e marca na agenda")
    assert r.agenda_mudou
    assert [t.titulo for t in agenda.tarefas()] == ["Prazo: Contestação"]
