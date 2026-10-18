"""Painel web e mascote da tela, sem abrir janelas: ponte com a página e decisões da mascote."""
import queue
import time

import mascote_tela as mt
import painel_web as pw

AREA = (0, 0, 1920, 1040)


# ------------------------------------------------------------------ painel
def test_eventos_da_voz_chegam_ao_painel_e_a_mascote():
    fila = queue.Queue()
    api = pw.ApiPainel(None, {}, fila)

    class MascoteFalsa:
        recebidos = []

        def ao_evento(self, evento):
            self.recebidos.append(evento["tipo"])

    api._mascote = MascoteFalsa()
    fila.put({"tipo": "ouvido", "texto": "Beatriz, que horas são?"})
    fila.put({"tipo": "resposta", "texto": "São dez horas."})
    limite = time.time() + 3
    recebidos = []
    while len(recebidos) < 2 and time.time() < limite:
        recebidos += api.eventos()
        time.sleep(0.05)
    assert [e["tipo"] for e in recebidos] == ["ouvido", "resposta"]
    assert MascoteFalsa.recebidos == ["ouvido", "resposta"]


def test_estado_traz_tarefas_e_dados_compartilhados(monkeypatch):
    monkeypatch.setattr(pw.ApiPainel, "_consultar_ollama", staticmethod(lambda: {"online": True, "modelo_ok": True}))
    monkeypatch.setattr(pw.ApiPainel, "_contar_biblioteca", staticmethod(lambda: {"trechos": 5, "fontes": 2, "conversas": 9}))
    monkeypatch.setattr(pw, "ler_metricas_analise", lambda: {"interacoes": 50, "tempo_medio": 1.5, "sucesso": 98})
    dados = {"whatsapp": {"status": "conectado"}, "painel": {"escuta": "escutando"},
             "tarefa:1": {"id": "1", "pedido": "Planilha", "status": "executando", "criada_em": 1.0}}
    estado = pw.ApiPainel(None, dados, None).estado()
    assert estado["whatsapp"] == {"status": "conectado"} and estado["painel"]["escuta"] == "escutando"
    assert estado["tarefas"][0]["pedido"] == "Planilha"
    assert estado["metricas"]["interacoes"] == 50 and estado["biblioteca"]["fontes"] == 2
    assert "nivel" not in estado


def test_abrir_so_dentro_das_pastas_da_beatriz(monkeypatch, tmp_path):
    abertos = []
    monkeypatch.setattr(pw.os, "startfile", abertos.append, raising=False)
    api = pw.ApiPainel(None, {}, None)
    arquivo_fora = tmp_path / "x.txt"
    arquivo_fora.write_text("x")
    assert not api.abrir(str(arquivo_fora))
    assert api.abrir(pw.PAGINA) and abertos == [pw.os.path.realpath(pw.PAGINA)]


def test_janela_da_agenda_marca_cancela_e_cuida_das_tarefas(tmp_path):
    from agenda_local import Agenda

    api = pw.ApiPainel(None, {}, None)
    api._agenda.agenda = Agenda(str(tmp_path / "agenda.sqlite3"))
    # No campo da janela da agenda não precisa dizer "marca".
    assert api.agenda_pedir("dentista amanhã às 14h").startswith("Marquei dentista amanhã às 14h")
    itens = [i for semana in (0, 1) for dia in api.agenda(semana)["dias"] for i in dia["itens"]]
    assert [i["titulo"] for i in itens] == ["Dentista"]
    assert api.agenda_cancelar(itens[0]["id"], itens[0]["data"]).startswith("Cancelei dentista")
    assert api.tarefa_nova("pagar boleto") == "Anotei a tarefa pagar boleto."
    tarefa = api.agenda(0)["tarefas"][0]
    assert api.tarefa_alternar(tarefa["id"]) == "Boa! Pagar boleto feita."
    assert api.agenda(0)["tarefas"][0]["feita"] is True
    assert api.tarefa_remover(tarefa["id"]) == "Tirei pagar boleto das tarefas."
    # Quadro da tela: o mês atual e o dia de hoje quando não se diz qual.
    assert api.agenda_mes()["dias"] and api.agenda_dia()["titulo"] == "Hoje"


def test_pedido_de_agenda_no_chat_do_painel_responde_sem_llm(tmp_path, monkeypatch):
    from agenda_local import Agenda

    api = pw.ApiPainel(None, {}, None)
    api._agenda.agenda = Agenda(str(tmp_path / "agenda.sqlite3"))
    monkeypatch.setattr(pw, "falar_async", lambda texto: None)
    monkeypatch.setattr(pw, "notelooklm_responder", lambda comando: (_ for _ in ()).throw(AssertionError("não é RAG")))
    api._processar("o que tenho amanhã?")
    eventos = api.eventos()
    assert {"tipo": "agenda_mudou"} in eventos
    assert eventos[-1]["texto"] == "Nada marcado para amanhã."


# ------------------------------------------------------------------ mascote
def _mascote(x=900.0):
    mascote = mt.MascoteDaTela(enviar_pedido=lambda comando: None, sugestoes=True)
    mascote.x = x
    mascote._enviar_pose = lambda: None
    return mascote


def test_anda_e_vira_na_borda_da_janela_em_uso():
    mascote = _mascote(x=500.0)
    contexto = {"area": AREA, "ocioso": 0, "janela": (400, 100, 1000, 800)}
    assert mascote.faixa(contexto) == (400, 1000 - mt.LARGURA)
    assert mascote.passo(0.0, 0.0, contexto) == "andando"
    mascote.direcao = -1
    for i in range(1, 200):
        mascote.passo(i * 0.05, 0.05, contexto)
        mascote._ate = 1e9  # continua andando
    assert 400 <= mascote.x <= 1000 - mt.LARGURA


def test_fora_da_faixa_volta_andando_sem_teletransporte():
    mascote = _mascote(x=1500.0)
    contexto = {"area": AREA, "ocioso": 0, "janela": (100, 100, 700, 800)}
    mascote.passo(0.0, 0.0, contexto)
    assert mascote.direcao == -1  # vai em direção à janela em uso
    mascote.direcao = 1  # andando para longe
    mascote.passo(0.1, 0.1, contexto)
    assert mascote.direcao == -1 and mascote.x > 1400  # virou, mas não pulou


def test_cochila_some_em_tela_cheia_e_para_quando_conversa():
    mascote = _mascote()
    assert mascote.passo(0.0, 0.05, {"area": AREA, "ocioso": 10, "tela_cheia": True}) == "escondida"
    assert mascote.passo(0.0, 0.05, {"area": AREA, "ocioso": mt.COCHILO_SEGUNDOS + 1}) == "dormindo"
    mascote.ao_evento({"tipo": "resposta", "texto": "Claro, já faço."})
    assert mascote.passo(1.0, 0.05, {"area": AREA, "ocioso": 0}) == "parada"
    assert mascote.api.eventos()[0]["tipo"] == "resposta"


def test_assunto_da_noticia_sai_limpo_do_titulo():
    assert mt.assunto_do_titulo("(3) Reforma tributária: o que muda em 2027 - g1 - Google Chrome") == \
        "Reforma tributária: o que muda em 2027"
    assert mt.assunto_do_titulo("Fotossíntese – Wikipédia, a enciclopédia livre - Microsoft​ Edge") == "Fotossíntese"


def test_sugere_resumo_da_noticia_uma_vez_so():
    mascote = _mascote()
    contexto = {"area": AREA, "ocioso": 0, "programa": "chrome.exe",
                "titulo": "Reforma tributária: o que muda em 2027 - g1 - Google Chrome"}
    mascote.pensar_em_sugestao(contexto)
    sugestao = mascote.api.eventos()
    assert sugestao == [{"tipo": "sugestao", "texto": "Quer um resumo sobre “Reforma tributária: o que muda em 2027”?",
                         "comando": "faz um resumo sobre Reforma tributária: o que muda em 2027"}]
    mascote._ultima_sugestao = 0  # mesmo depois do intervalo, a mesma notícia não volta
    mascote.pensar_em_sugestao(contexto)
    assert mascote.api.eventos() == []


def test_sem_sugestoes_quando_desligadas():
    mascote = mt.MascoteDaTela(enviar_pedido=lambda comando: None, sugestoes=False)
    mascote.pensar_em_sugestao({"area": AREA, "programa": "code.exe", "titulo": "main.py - Visual Studio Code"})
    assert mascote.api.eventos() == []


def test_aceitar_sugestao_vira_pedido_e_abre_o_painel():
    pedidos, aberturas = [], []
    mascote = mt.MascoteDaTela(enviar_pedido=pedidos.append)
    mascote._mostrar_painel = lambda: aberturas.append(1)
    mascote.api._abrir_painel = mascote._mostrar_painel
    assert mascote.api.aceitar_sugestao("faz um resumo sobre a reforma tributária")
    assert pedidos == ["faz um resumo sobre a reforma tributária"] and aberturas == [1]
