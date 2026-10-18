
from datetime import datetime

import pytest

import main
import tools
from canal_whatsapp import (
    AQUECIMENTO_MAXIMO,
    AQUECIMENTO_SEM_MENSAGENS,
    PREFIXO_RESPOSTA,
    LeitorDeMensagens,
    horario_da_mensagem,
    mensagens_de_resposta,
    numero_do_wid,
    para_formato_whatsapp,
)

ABERTURA = datetime(2026, 10, 14, 18, 47, 6).timestamp()  # quando a Beatriz abriu a conversa


def msg(identificador, texto, hora=None):
    """Mensagem como o JS_LER_MENSAGENS devolve; `hora` ("18:23") vira o data-pre-plain-text."""
    return {"id": identificador, "texto": texto, "quando": f"[{hora}, 14/10/2026] Felipe: " if hora else None}


def ler_por_segundo(leitor, leituras, inicio=ABERTURA):
    """Uma leitura da conversa por segundo, como no navegador; devolve tudo o que virou comando."""
    comandos = []
    for segundo, mensagens in enumerate(leituras):
        comandos += leitor.novas(mensagens, agora=inicio + segundo)
    return comandos


def leitor_pronto(historico=()):
    leitor = LeitorDeMensagens()
    assert ler_por_segundo(leitor, [list(historico)] * (AQUECIMENTO_MAXIMO + 1)) == []
    assert leitor.pronto
    return leitor


class CanalFalso:
    """Imita o CanalWhatsApp: comandos numa lista, envios registrados."""

    def __init__(self, comandos=()):
        self.comandos = list(comandos)
        self.enviados = []

    def receber(self):
        return self.comandos.pop(0) if self.comandos else ""

    def enviar(self, texto="", arquivos=()):
        self.enviados.append((texto, list(arquivos)))


@pytest.fixture
def canal(monkeypatch):
    falso = CanalFalso()
    monkeypatch.setattr(main, "CANAL_WHATSAPP", falso)
    monkeypatch.setitem(main.CANAL, "atual", "voz")
    monkeypatch.setattr(main, "avisar_painel", lambda *a, **k: None)
    monkeypatch.setattr(main, "registrar_conversa", lambda *a, **k: None)
    monkeypatch.setattr(main, "falar_async", lambda *a, **k: pytest.fail("não deveria falar em casa"))
    yield falso
    tools.CONFIRMAR_NO_TECLADO = True


# ------------------------------------------------------------ leitura da conversa
def test_historico_da_conversa_nao_vira_comando():
    leitor = leitor_pronto([msg("a", "lista de compras")])  # já estava na tela
    assert leitor.novas([msg("a", "lista de compras"), msg("b", "que horas são?")]) == ["que horas são?"]
    assert leitor.novas([msg("b", "que horas são?")]) == []  # não repete


def test_historico_que_chega_em_partes_na_abertura_nao_vira_comando():
    # Como na abertura real: a caixa de texto aparece antes das mensagens, que chegam em partes.
    antigas = [msg("h1", "Oi Beatriz", "18:20"),
               msg("h2", "Quero que crie um resumo da matéria de direito tributário", "18:21"),
               msg("h3", f"{PREFIXO_RESPOSTA}: Certo, vou preparar o PDF.", "18:21"),
               msg("h4", "Quero baixar", "18:23")]
    leitor = LeitorDeMensagens()
    assert ler_por_segundo(leitor, [[], [], antigas[2:], antigas[1:], antigas]) == []
    assert not leitor.pronto  # a conversa ainda está mudando
    assert ler_por_segundo(leitor, [antigas] * 3, inicio=ABERTURA + 5) == []
    assert leitor.pronto and leitor.historico == {"h1", "h2", "h3", "h4"}

    agora = [msg("c1", "que horas são?", "18:47")]
    assert leitor.novas(antigas + agora) == ["que horas são?"]


def test_mensagem_antiga_que_aparece_depois_da_abertura_nao_vira_comando():
    recentes = [msg("h3", "faz um pdf sobre energia solar", "18:40"), msg("h4", f"{PREFIXO_RESPOSTA}: Pronto!", "18:41")]
    leitor = leitor_pronto(recentes)  # pronta às 18:47:11
    # Mais antigas carregadas acima das que já estavam na tela (sem horário, para valer só a posição).
    acima = [msg("h1", "Oi Beatriz"), msg("h2", "Quero baixar")]
    assert leitor.novas(acima + recentes) == []
    # Mandadas com a Beatriz desligada e sincronizadas depois: entram embaixo, mas com horário antigo.
    atrasadas = [msg("o1", "manda o arquivo", "18:30"), msg("o2", "resume as notícias", "18:44")]
    assert leitor.novas(acima + recentes + atrasadas) == []
    # O que chega depois de pronta vale, mesmo com o relógio do celular um pouco atrasado.
    novas = [msg("c1", "e o clima?", "18:46"), msg("c2", "que horas são?", "18:47")]
    assert leitor.novas(acima + recentes + atrasadas + novas) == ["e o clima?", "que horas são?"]


def test_mesma_mensagem_com_data_id_em_outro_formato_nao_repete():
    leitor = leitor_pronto([msg("true_5511987654321@c.us_3EB0AA11", "lista de compras")])
    assert leitor.novas([msg("true_98765432101234@lid_3EB0AA11", "lista de compras")]) == []
    assert leitor.novas([msg("true_98765432101234@lid_3EB0BB22", "que horas são?")]) == ["que horas são?"]


def test_conversa_vazia_espera_mais_e_o_aquecimento_tem_limite():
    vazia = LeitorDeMensagens()
    ler_por_segundo(vazia, [[]] * AQUECIMENTO_SEM_MENSAGENS)
    assert not vazia.pronto  # vazia pode ser a conversa ainda carregando
    vazia.novas([], agora=ABERTURA + AQUECIMENTO_SEM_MENSAGENS)
    assert vazia.pronto
    assert vazia.novas([msg("h1", "Quero baixar", "18:30")]) == []  # histórico que demorou mais ainda

    mudando = LeitorDeMensagens()  # sincronização que não para: fica pronta no tempo máximo
    leituras = [[msg(f"h{n}", f"antiga {n}")] for n in range(AQUECIMENTO_MAXIMO + 1)]
    assert ler_por_segundo(mudando, leituras) == []
    assert mudando.pronto_em == ABERTURA + AQUECIMENTO_MAXIMO


def test_horario_da_mensagem():
    assert horario_da_mensagem("[18:23, 14/10/2026] Felipe: ") == datetime(2026, 10, 14, 18, 23).timestamp()
    assert horario_da_mensagem("[6:23 PM, 10/14/2026] Felipe: ") == datetime(2026, 10, 14, 18, 23).timestamp()
    assert horario_da_mensagem("[12:05 a.m., 10/14/2026] Felipe: ") == datetime(2026, 10, 14, 0, 5).timestamp()
    assert horario_da_mensagem("") is None and horario_da_mensagem(None) is None


def test_respostas_da_beatriz_nao_voltam_como_comando():
    leitor = leitor_pronto()
    resposta = mensagens_de_resposta("A capital é **Brasília**.")[0]
    leitor.registrar_envio(resposta)
    lido_da_tela = resposta.replace("*", "")  # o negrito chega da tela sem os asteriscos
    novas = leitor.novas([
        {"id": "r1", "texto": lido_da_tela},
        {"id": "r2", "texto": f"{PREFIXO_RESPOSTA}: qualquer outra resposta"},
        {"id": "f1", "texto": None},  # arquivo enviado, sem texto
        {"id": "c1", "texto": "faz um pdf sobre energia solar"},
    ])
    assert novas == ["faz um pdf sobre energia solar"]


def test_resposta_longa_tem_prefixo_em_todas_as_partes():
    partes = mensagens_de_resposta("\n\n".join(["parágrafo " * 120] * 5))
    assert len(partes) > 1
    assert all(p.startswith(f"{PREFIXO_RESPOSTA} (") for p in partes)
    assert para_formato_whatsapp("## Título\n**forte**") == "*Título*\n*forte*"


def test_numero_da_conta_conectada():
    assert numero_do_wid('"5511987654321:23@c.us"') == "5511987654321"
    assert numero_do_wid("") == ""


# ------------------------------------------------------------ roteamento na Beatriz
def test_comando_do_whatsapp_e_respondido_no_whatsapp(canal):
    canal.comandos.append("qual a capital do Brasil?")

    comando = main.obter_comando_usuario(espera=0.01)
    main.falar_e_registrar(comando, "A capital é Brasília.")

    assert comando == "qual a capital do Brasil?" and main.CANAL["atual"] == "whatsapp"
    assert canal.enviados == [("A capital é Brasília.", [])]
    assert tools.CONFIRMAR_NO_TECLADO is False


def test_depois_do_whatsapp_a_voz_volta_a_falar(canal, monkeypatch):
    monkeypatch.setattr(main, "microfone_disponivel", lambda: True)
    main.CANAL["atual"] = "whatsapp"
    assert main.obter_comando_usuario(espera=0.01) == ""
    assert main.CANAL["atual"] == "voz" and tools.CONFIRMAR_NO_TECLADO is True


def tarefa_de_planilha():
    from agentes import Tarefa

    tarefa = Tarefa(pedido="crie uma planilha de gastos", plano=["planilha"], formatos=["xlsx"],
                    status="concluída", arquivos=["C:/x/gastos.xlsx"], mensagem_final="Pronto! A planilha está pronta em Excel.")
    tarefa.contexto["conteudo"] = {"abas": [{"nome": "Gastos", "colunas": ["Categoria", "Valor"], "tipos": ["texto", "moeda"],
                                             "linhas": [["Moradia", 1500], ["Mercado", "R$ 642,35"]], "total": True}]}
    return tarefa


def test_tarefa_pelo_whatsapp_responde_em_texto_sem_mandar_o_arquivo(canal):
    main.anunciar_tarefa_concluida("crie uma planilha", tarefa_de_planilha(), canal="whatsapp")
    texto, arquivos = canal.enviados[0]
    assert arquivos == []
    assert "Total de Valor: R$ 2.142,35" in texto          # o principal do resultado vai na mensagem
    assert "manda o arquivo" in texto                       # e a dica de como pedir o arquivo


def test_tarefa_pedida_com_envio_manda_o_arquivo(canal):
    main.anunciar_tarefa_concluida("crie uma planilha e me manda", tarefa_de_planilha(), canal="whatsapp", mandar_arquivos=True)
    assert canal.enviados[0][1] == ["C:/x/gastos.xlsx"]


class OrquestradorFalso:
    def __init__(self, tarefa=None):
        self.tarefa = tarefa

    def ultima_com_arquivos(self):
        return self.tarefa


def test_manda_o_arquivo_pelo_whatsapp_envia_os_arquivos_da_ultima_tarefa(canal):
    main.CANAL["atual"] = "whatsapp"
    tarefa = tarefa_de_planilha()
    tarefa.arquivos = ["C:/x/gastos.xlsx", "C:/x/gastos.pdf"]
    assert main.mandar_arquivo_pedido("manda o pdf", OrquestradorFalso(tarefa))
    assert canal.enviados == [("Aqui está: gastos.pdf.", ["C:/x/gastos.pdf"])]


def test_manda_o_arquivo_sem_tarefa_avisa(canal):
    main.CANAL["atual"] = "whatsapp"
    assert main.mandar_arquivo_pedido("cadê o arquivo?", OrquestradorFalso())
    assert canal.enviados[0][1] == [] and "Ainda não tenho nenhum arquivo" in canal.enviados[0][0]


def test_pela_voz_so_manda_quando_diz_pro_whatsapp(canal, monkeypatch):
    falado = []
    monkeypatch.setattr(main, "responder_rapido", falado.append)
    tarefa = tarefa_de_planilha()
    assert not main.mandar_arquivo_pedido("manda a planilha", OrquestradorFalso(tarefa))
    assert main.mandar_arquivo_pedido("Beatriz, manda a planilha pro meu WhatsApp", OrquestradorFalso(tarefa))
    assert canal.enviados == [("Aqui está: gastos.xlsx.", ["C:/x/gastos.xlsx"])]
    assert falado == ["Mandei no seu WhatsApp."]


def test_pedido_de_arquivo_nao_vira_tarefa_nova():
    from agentes import pede_envio, planejar

    assert planejar("quero o pdf") is None and planejar("manda a planilha") is None
    assert planejar("me manda um pdf sobre energia solar").tema == "energia solar"
    assert pede_envio("faz um pdf sobre energia solar e me manda")
    assert not pede_envio("crie uma planilha de gastos")


def test_conversa_simples_pelo_whatsapp_nao_usa_a_fala(canal, monkeypatch):
    main.CANAL["atual"] = "whatsapp"
    monkeypatch.setattr(main.llm_clients, "chat_beatriz", lambda pergunta, contexto=None: "Tudo ótimo por aqui!")
    assert main.responder_conversa_em_stream("tudo bem?") == "Tudo ótimo por aqui!"
    assert canal.enviados == [("Tudo ótimo por aqui!", [])]


def test_confirmacao_no_teclado_e_cancelada_pelo_whatsapp(canal, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("não pode esperar o teclado"))
    main.CANAL["atual"] = "whatsapp"
    tools.CONFIRMAR_NO_TECLADO = False
    assert tools._confirmar_acao("enviar um e-mail") is False
    assert main.pedir_confirmacao("enviar um e-mail") is False


def test_agendamento_pergunta_e_espera_a_resposta_no_whatsapp(canal, monkeypatch):
    main.CANAL["atual"] = "whatsapp"
    canal.comandos.append("reunião com o João")
    assert main.perguntar_durante_agendamento("Qual é o título do compromisso?") == "reunião com o João"
    assert canal.enviados == [("Qual é o título do compromisso?", [])]
