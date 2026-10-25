"""Testes do controle remoto (TV e ar pelo Ekaza): um controle e uma nuvem falsos, nada de infravermelho de verdade."""
import json

import pytest

import controle_remoto
import main
from agente_controle import (MENSAGEM_SEM_CONFIGURACAO, AgenteControle, _texto_preparado, interpretar, nome_do_botao,
                             numero)
from controle_remoto import ControleLocalTuya, ErroControle, RegistroDoControle, dados_do_wizard, tecla_da_nuvem


class ControleFalso:
    """Faz o papel do ControleLocalTuya: guarda o que mandaria e aprende o código que o teste mandar."""

    def __init__(self, aprendido="QUJDREVGR0g=", erro=None):
        self.enviados = []  # uma lista por conexão
        self.aprendido = aprendido
        self.erro = erro
        self.aprendeu = 0

    def enviar(self, codigos, intervalo=0):
        if self.erro:
            raise ErroControle(self.erro)
        self.enviados.append([codigos] if isinstance(codigos, str) else list(codigos))

    def aprender(self, segundos=15):
        self.aprendeu += 1
        return self.aprendido

    def conferir(self):
        if self.erro:
            raise ErroControle(self.erro)
        return "192.168.0.50"


class NuvemFalsa:
    TECLAS_TV = [{"key": "Power", "key_id": 1, "key_name": "Power"}, {"key": "Volume+", "key_id": 3, "key_name": "Vol+"},
                 {"key": "Volume-", "key_id": 4, "key_name": "Vol-"}, {"key": "Mute", "key_id": 5, "key_name": "Mute"},
                 {"key": "1", "key_id": 11, "key_name": "1"}, {"key": "3", "key_id": 13, "key_name": "3"}]

    def __init__(self, controles=None):
        self.lista = controles if controles is not None else [
            {"remote_id": "tv1", "remote_name": "TV Sala", "category_id": 2, "brand_name": "Samsung"},
            {"remote_id": "ar1", "remote_name": "Ar quarto", "category_id": 5, "brand_name": "LG"},
        ]
        self.apertos, self.comandos_ar, self.listagens = [], [], 0
        self.faixas = {}  # o que o ar aceita (vazio = não se sabe)

    def controles(self):
        self.listagens += 1
        return self.lista

    def teclas(self, remote_id):
        return self.TECLAS_TV

    def apertar(self, remote_id, category_id, tecla, vezes=1):
        self.apertos.append((remote_id, category_id, tecla["key"], vezes))

    def ar(self, remote_id, ligado, modo="frio", temperatura=23, ventilacao="auto", com_temperatura=True):
        self.comandos_ar.append((remote_id, ligado, modo, temperatura if com_temperatura else None, ventilacao))

    def faixas_do_ar(self, remote_id):
        return self.faixas


class Relogio:
    def __init__(self):
        self.agora = 1_000_000.0

    def __call__(self):
        return self.agora


@pytest.fixture
def casa(tmp_path):
    """Monta um agente com o controle falso (e, se pedido, a nuvem falsa)."""
    class Casa:
        def __init__(self):
            self.registro = RegistroDoControle(str(tmp_path / "controle.json"))
            self.local = ControleFalso()
            self.nuvem = None
            self.avisos = []
            self.relogio = Relogio()
            self.agente = None

        def montar(self, local=True, nuvem=False, modo="auto", **kw):
            self.nuvem = NuvemFalsa() if nuvem is True else nuvem or None
            self.agente = AgenteControle(self.registro, local=self.local if local else None, nuvem=self.nuvem,
                                         modo_nuvem=modo, avisar=self.avisos.append, executar=lambda funcao: funcao(),
                                         relogio=self.relogio, **kw)
            return self.agente

        def diz(self, texto):
            if self.agente is None:
                self.montar()
            return self.agente.responder(texto)

        def ensina(self, aparelho, botao, codigo):
            self.registro.guardar_botao(aparelho, botao, codigo)

    return Casa()


# ------------------------------------------------------------------ entender os pedidos
@pytest.mark.parametrize("frase, acao, aparelhos, campos", [
    ("liga a TV", "ligar", ("tv",), {}),
    ("Beatriz, desliga a televisão", "desligar", ("tv",), {}),
    ("apaga a tv", "desligar", ("tv",), {}),
    ("aumenta o volume", "botao", ("tv",), {"botao": "volume_mais", "vezes": 3}),
    ("abaixa o volume em 5", "botao", ("tv",), {"botao": "volume_menos", "vezes": 5}),
    ("aumenta um pouco o volume", "botao", ("tv",), {"botao": "volume_mais", "vezes": 2}),
    ("diminui o volume da TV três vezes", "botao", ("tv",), {"botao": "volume_menos", "vezes": 3}),
    ("põe no mudo", "botao", ("tv",), {"botao": "mudo"}),
    ("tira o som da TV", "botao", ("tv",), {"botao": "mudo"}),
    ("põe no canal 13", "canal", ("tv",), {"canal": "13"}),
    ("muda pro canal sete", "canal", ("tv",), {"canal": "7"}),
    ("próximo canal", "botao", ("tv",), {"botao": "canal_mais"}),
    ("volta o canal", "botao", ("tv",), {"botao": "canal_menos"}),
    ("aperta o OK da TV", "botao", ("tv",), {"botao": "ok"}),
    ("liga a TV no canal 5", "ligar", ("tv",), {"canal": "5"}),
    ("liga o ar", "ligar", ("ar",), {}),
    ("liga o ar em 22 graus", "ar", ("ar",), {"temperatura": 22}),
    ("liga o ar no 18", "ar", ("ar",), {"temperatura": 18}),
    ("põe o ar pra vinte e dois", "ar", ("ar",), {"temperatura": 22}),
    ("põe o ar no modo frio", "ar", ("ar",), {"modo": "frio"}),
    ("liga o ar no quente em 25 graus", "ar", ("ar",), {"modo": "quente", "temperatura": 25}),
    ("põe o ar pra desumidificar", "ar", ("ar",), {"modo": "seco"}),
    ("ventilação alta", "ar", ("ar",), {"ventilacao": "alta"}),
    ("põe o vento do ar no máximo", "ar", ("ar",), {"ventilacao": "alta"}),
    ("abaixa a temperatura", "ar", ("ar",), {"delta": -1}),
    ("aumenta a temperatura do ar em 2 graus", "ar", ("ar",), {"delta": 2}),
    ("deixa o ar mais frio", "ar", ("ar",), {"delta": -1}),
    ("põe em 20 graus", "ar", ("ar",), {"temperatura": 20}),
    ("desliga o ar-condicionado", "desligar", ("ar",), {}),
    ("desliga o ar condicionado", "desligar", ("ar",), {}),
    ("desliga a TV e o ar", "desligar", ("tv", "ar"), {}),
    ("desliga tudo", "desligar", ("tv", "ar"), {}),
    ("o ar está ligado?", "estado", ("ar",), {}),
    ("a TV está ligada?", "estado", ("tv",), {}),
    ("em quantos graus está o ar?", "estado", ("ar",), {}),
    ("aprende o botão ligar da TV", "aprender", ("tv",), {"botao": "energia"}),
    ("aprende o volume mais da TV", "aprender", ("tv",), {"botao": "volume_mais"}),
    ("aprende o botão de abaixar o volume da TV", "aprender", ("tv",), {"botao": "volume_menos"}),
    ("aprende o botão 5 da TV", "aprender", ("tv",), {"botao": "digito_5"}),
    ("aprende o botão um da TV", "aprender", ("tv",), {"botao": "digito_1"}),
    ("aprende o botão netflix da TV", "aprender", ("tv",), {"botao": "netflix"}),
    ("pode aprender o botão HDMI 1 da TV", "aprender", ("tv",), {"botao": "hdmi 1"}),
    ("aprende o ar em 22 graus", "aprender", ("ar",), {"temperatura": 22, "modo": None}),
    ("aprende o ar em 18 graus no quente", "aprender", ("ar",), {"temperatura": 18, "modo": "quente"}),
    ("aprende o botão desligar do ar", "aprender", ("ar",), {"botao": "desligar"}),
    ("esquece o botão netflix da TV", "esquecer", ("tv",), {"botao": "netflix"}),
    ("quais botões você sabe da TV?", "listar", ("tv",), {}),
    ("o controle remoto está funcionando?", "conferir", (), {}),
    ("atualiza os controles da nuvem", "nuvem", (), {}),
])
def test_entende_os_pedidos(frase, acao, aparelhos, campos):
    pedido = interpretar(_texto_preparado(frase))
    assert pedido is not None, frase
    assert (pedido.acao, pedido.aparelhos) == (acao, aparelhos), frase
    for campo, valor in campos.items():
        assert getattr(pedido, campo) == valor, (frase, campo)


@pytest.mark.parametrize("frase", [
    "o ar está frio demais",  # reclamação, não pedido
    "quero aprender a mexer na TV",  # quem quer aprender é a pessoa
    "qual a temperatura hoje?",  # previsão do tempo
    "como está o ar lá fora?",
    "qual a qualidade do ar hoje?",
    "aumenta o volume da sua voz",
    "liga pra minha mãe",
    "abre o canal do youtube",
    "desliga",  # sozinho, é desligar a Beatriz (main.PALAVRAS_DESLIGAR)
    "clica em ok",  # sem "TV" e sem botão aprendido: pode ser na tela do PC
    "vai fazer 22 graus amanhã?",
    "imprime a lista de compras",
])
def test_ignora_o_que_nao_e_com_o_controle(frase):
    assert interpretar(_texto_preparado(frase)) is None, frase


def test_numeros_falados():
    assert numero("22") == 22 and numero("vinte e dois") == 22 and numero("dezoito") == 18 and numero("trinta") == 30
    assert numero("banana") is None
    assert nome_do_botao("o botao de ligar e desligar") == "energia"  # recebe o texto já sem acentos
    assert nome_do_botao("seta pra cima") == "cima"


def test_aperta_botao_aprendido_sem_dizer_tv(casa):
    casa.ensina("tv", "netflix", "TkVURkxJWA==")
    assert casa.diz("aperta o netflix") == "Pronto, apertei o botão netflix da TV."
    assert casa.diz("abre a netflix na TV") == "Pronto, apertei o botão netflix da TV."
    assert casa.local.enviados == [["TkVURkxJWA=="], ["TkVURkxJWA=="]]


# ------------------------------------------------------------------ configuração
def test_sem_configuracao_explica_o_que_falta(casa):
    casa.montar(local=False)
    assert casa.diz("liga a TV") == MENSAGEM_SEM_CONFIGURACAO
    assert casa.diz("que horas são?") is None


def test_desligado_no_env_nao_responde_nada(casa):
    casa.montar(ligado=False)
    assert casa.diz("liga a TV") is None
    assert not casa.agente.reconhece("liga a TV")


def test_problemas_respondem_sem_mandar_nada(casa):
    assert casa.diz("põe o ar em 35 graus") == "O ar vai de 16 a 30 graus."
    assert "não sei deixar o controle remoto para depois" in casa.diz("liga o ar daqui a 10 minutos")
    assert "não sabe em quanto o volume está" in casa.diz("põe o volume no 20")
    assert casa.local.enviados == []


# ------------------------------------------------------------------ TV
def test_liga_a_tv_com_o_botao_aprendido(casa):
    casa.ensina("tv", "energia", "UE9XRVI=")
    assert casa.diz("liga a TV") == "Pronto, liguei a TV."
    assert casa.local.enviados == [["UE9XRVI="]]
    estado = casa.registro.estado("tv")
    assert estado["ligada"] is True and estado["ultimo"] == "ligar"


def test_botao_unico_de_energia_pergunta_antes_de_apertar_de_novo(casa):
    casa.ensina("tv", "energia", "UE9XRVI=")
    casa.diz("liga a TV")
    pergunta = casa.diz("liga a TV")
    assert pergunta.startswith("Pelo que eu sei, a TV já está ligada") and pergunta.endswith("Aperto mesmo assim?")
    assert len(casa.local.enviados) == 1
    assert casa.agente.esperando_resposta() and casa.agente.reconhece("sim")
    assert casa.diz("sim") == "Pronto, liguei a TV."
    assert len(casa.local.enviados) == 2

    casa.diz("liga a TV")  # ligada de novo, pelo que ela sabe
    assert casa.diz("não") == "Beleza, deixei a TV como está."
    assert len(casa.local.enviados) == 2


def test_lembranca_velha_da_tv_nao_pergunta(casa):
    casa.ensina("tv", "energia", "UE9XRVI=")
    casa.diz("liga a TV")
    casa.relogio.agora += 4 * 3600  # depois de horas, alguém pode ter desligado pelo controle original
    assert casa.diz("liga a TV") == "Pronto, liguei a TV."


def test_botoes_separados_de_ligar_e_desligar_nao_perguntam(casa):
    casa.ensina("tv", "ligar", "TElHQVI=")
    casa.ensina("tv", "desligar", "REVTTElHQVI=")
    casa.diz("liga a TV")
    assert casa.diz("liga a TV") == "Pronto, liguei a TV."
    assert casa.diz("desliga a TV") == "Pronto, desliguei a TV."
    assert casa.local.enviados == [["TElHQVI="], ["TElHQVI="], ["REVTTElHQVI="]]


def test_botao_que_falta_explica_como_ensinar(casa):
    resposta = casa.diz("aumenta o volume")
    assert resposta.startswith("Ainda não sei o botão de aumentar o volume da TV.")
    assert "aprende o botão de aumentar o volume da TV" in resposta
    assert casa.local.enviados == []


def test_volume_aperta_varias_vezes_numa_conexao_so(casa):
    casa.ensina("tv", "volume_mais", "Vk9MKw==")
    casa.ensina("tv", "volume_menos", "Vk9MLQ==")
    assert casa.diz("aumenta o volume") == "Pronto, aumentei o volume da TV em 3."
    assert casa.diz("abaixa o volume em 5") == "Pronto, abaixei o volume da TV em 5."
    assert casa.local.enviados == [["Vk9MKw=="] * 3, ["Vk9MLQ=="] * 5]


def test_canal_pelos_numeros(casa):
    casa.ensina("tv", "digito_1", "MQ==")
    resposta = casa.diz("põe no canal 13")
    assert resposta.startswith("Ainda não sei o número 3 do controle da TV.")
    casa.ensina("tv", "digito_3", "Mw==")
    assert casa.diz("põe no canal 13") == "Pronto, pus no canal 13."
    assert casa.local.enviados == [["MQ==", "Mw=="]]


def test_controle_fora_da_rede_avisa(casa):
    casa.local.erro = "o controle não respondeu na rede (ele está fora de alcance)"
    casa.ensina("tv", "energia", "UE9XRVI=")
    resposta = casa.diz("liga a TV")
    assert resposta == "Não consegui: o controle não respondeu na rede (ele está fora de alcance)."
    assert casa.registro.estado("tv") == {}  # não mandou: o estado lembrado não muda


# ------------------------------------------------------------------ aprender
def test_aprende_um_botao_da_tv_e_usa_depois(casa):
    instrucao = casa.diz("aprende o botão ligar da TV")
    assert instrucao.startswith("Aponte o controle da TV para o Ekaza")
    assert "o botão de ligar e desligar" in instrucao
    assert casa.registro.botoes("tv") == {"energia": "QUJDREVGR0g="}
    assert casa.avisos == ['Aprendi o botão de ligar e desligar da TV. Agora é só pedir: "liga a TV".']
    assert casa.diz("desliga a TV") == "Pronto, desliguei a TV."
    assert casa.local.enviados == [["QUJDREVGR0g="]]


def test_sem_sinal_nao_guarda_nada(casa):
    casa.local.aprendido = None
    casa.diz("aprende o botão netflix da TV")
    assert casa.registro.botoes("tv") == {}
    assert casa.avisos[-1].startswith("Não peguei nenhum sinal")
    assert casa.agente.aprendendo == ""


def test_aprender_sem_dizer_o_botao_pergunta(casa):
    assert casa.diz("aprende um botão da TV").startswith("Qual botão da TV?")
    assert casa.local.aprendeu == 0


def test_aprende_o_ar_em_duas_etapas(casa):
    preparo = casa.diz("aprende o ar em 22 graus")
    assert "sem apontar para o Ekaza" in preparo and "no frio, em 21 graus" in preparo and preparo.endswith('diga "pronto".')
    assert casa.local.aprendeu == 0  # ainda não: mexer no controle agora mandaria o 21
    instrucao = casa.diz("pronto")
    assert "aperte uma vez a temperatura para cima, para ficar em 22 graus" in instrucao
    assert casa.registro.botoes("ar") == {"frio_22": "QUJDREVGR0g="}
    assert casa.avisos[-1] == 'Aprendi 22 graus no frio do ar. Agora é só pedir: "liga o ar em 22 graus".'

    assert casa.diz("liga o ar em 22 graus") == "Pronto, liguei o ar em 22 graus, no frio."
    estado = casa.registro.estado("ar")
    assert (estado["ligado"], estado["temperatura"], estado["modo"]) == (True, 22, "frio")


def test_sem_saber_o_tipo_pergunta_a_nuvem_antes_de_aprender(casa):
    tipos = []
    casa.local.tipo_conhecido = lambda: bool(tipos)
    casa.local.definir_tipo = tipos.append
    nuvem = NuvemFalsa()
    nuvem.tipo_do_controle = lambda: 1
    casa.montar(nuvem=nuvem)
    preparo = casa.diz("aprende o ar em 22 graus")  # a nuvem disse o tipo: o ar já pode ser o primeiro
    assert tipos == [1] and "sem apontar para o Ekaza" in preparo


def test_controles_do_app_vem_dos_subaparelhos(monkeypatch):
    # O Ekaza do usuário: a lista de "remotes" vem vazia, e a TV criada no Smart Life é um sub-aparelho (out/2026).
    nuvem = controle_remoto.ControleNuvemTuya("us", "a", "b", "ir1")
    respostas = {"/v2.0/infrareds/ir1/remotes": [],
                 "/v1.0/devices/ir1/sub-devices": [{"id": "tv9", "name": "TV", "category": "infrared_tv", "online": True},
                                                   {"id": "luz1", "name": "Luz", "category": "infrared_light"}]}
    monkeypatch.setattr(nuvem, "_pedir", lambda caminho, corpo=None: respostas[caminho])
    assert nuvem.controles() == [{"remote_id": "tv9", "remote_name": "TV", "brand_name": "", "category_id": 2}]


def test_tipo_pelas_funcoes_da_nuvem(monkeypatch):
    nuvem = controle_remoto.ControleNuvemTuya("us", "a", "b", "id")
    sombra_ekaza = {"properties": [{"code": "ir_send", "dp_id": 201, "type": "string"},
                                   {"code": "ir_study_code", "dp_id": 202, "type": "raw"}]}  # o do usuário, out/2026
    monkeypatch.setattr(nuvem, "_pedir", lambda caminho, corpo=None: sombra_ekaza)
    assert nuvem.tipo_do_controle() == 1
    monkeypatch.setattr(nuvem, "_pedir", lambda caminho, corpo=None: {"properties": [{"code": "control", "dp_id": 1}]})
    assert nuvem.tipo_do_controle() == 2
    monkeypatch.setattr(nuvem, "_pedir", lambda caminho, corpo=None: {})
    assert nuvem.tipo_do_controle() == 0


def test_sem_saber_o_tipo_o_primeiro_botao_e_da_tv(casa):
    casa.local.tipo_conhecido = lambda: False
    assert casa.diz("aprende o ar em 22 graus").startswith("Antes do ar, me ensine um botão da TV")
    assert casa.agente.pendencia is None and casa.local.aprendeu == 0
    assert casa.diz("aprende o botão mudo da TV").startswith("Aponte o controle da TV para o Ekaza")
    assert casa.registro.botoes("tv") == {"mudo": "QUJDREVGR0g="}


def test_aprender_no_minimo_usa_a_temperatura_de_cima(casa):
    preparo = casa.diz("aprende o ar em 16 graus")
    assert "em 17 graus" in preparo
    assert "a temperatura para baixo" in casa.diz("pronto")


def test_outro_pedido_no_lugar_do_pronto_desfaz_a_espera(casa):
    casa.diz("aprende o botão desligar do ar")
    assert casa.diz("que horas são?") is None
    assert not casa.agente.esperando_resposta()
    assert casa.local.aprendeu == 0


def test_aprender_precisa_do_controle_local(casa):
    casa.montar(local=False, nuvem=True)
    assert casa.diz("aprende o botão ligar da TV").startswith("Para aprender os botões eu preciso falar com o Ekaza")


def test_esquece_e_lista(casa):
    casa.ensina("tv", "netflix", "TkVURkxJWA==")
    casa.ensina("tv", "energia", "UE9XRVI=")
    casa.ensina("ar", "frio_22", "MjI=")
    assert casa.diz("quais botões você sabe da TV?") == "Da TV eu sei 2 botões: ligar e desligar e netflix."
    assert casa.diz("o que você sabe fazer no ar?") == "Do ar eu sei: 22 graus no frio."
    assert casa.diz("esquece o botão netflix da TV") == "Pronto, esqueci o botão netflix da TV."
    assert casa.diz("esquece o botão netflix da TV") == "Eu não tinha aprendido o botão netflix da TV."
    assert casa.registro.botoes("tv") == {"energia": "UE9XRVI="}


# ------------------------------------------------------------------ ar
def test_ar_liga_desliga_e_ajusta_com_os_codigos_aprendidos(casa):
    for codigo in ("frio_23", "frio_22", "quente_22"):
        casa.ensina("ar", codigo, codigo.upper())
    casa.ensina("ar", "desligar", "DESLIGAR")
    assert casa.diz("liga o ar") == "Pronto, liguei o ar em 23 graus, no frio."  # o estado padrão: 23 no frio
    assert casa.diz("abaixa a temperatura") == "Pronto, deixei o ar em 22 graus, no frio."
    assert casa.diz("põe o ar no modo quente") == "Pronto, deixei o ar em 22 graus, no quente."
    assert casa.diz("desliga o ar") == "Pronto, desliguei o ar."
    assert casa.local.enviados == [["FRIO_23"], ["FRIO_22"], ["QUENTE_22"], ["DESLIGAR"]]
    assert casa.diz("como está o ar?").startswith("Pelo último comando que mandei, o ar está desligado.")


def test_temperatura_que_falta_diz_o_que_ja_sabe(casa):
    casa.ensina("ar", "frio_22", "MjI=")
    resposta = casa.diz("liga o ar em 18 graus")
    assert resposta.startswith("Ainda não sei 18 graus no frio do ar.")
    assert 'aprende o ar em 18 graus' in resposta and "Do ar eu já sei: 22 graus no frio." in resposta
    assert casa.local.enviados == []


def test_ventilacao_pedida_precisa_do_codigo_com_ventilacao(casa):
    casa.ensina("ar", "frio_23", "MjM=")
    assert casa.diz("ventilação alta").startswith("Ainda não sei 23 graus no frio do ar.")
    casa.ensina("ar", "frio_23_alta", "MjNB")
    assert casa.diz("ventilação alta") == "Pronto, liguei o ar em 23 graus, no frio, ventilação alta."


def test_codigo_do_botao_ligar_do_ar(casa):
    casa.ensina("ar", "ligar", "TElHQVI=")
    assert casa.diz("liga o ar") == "Pronto, liguei o ar."
    assert "na temperatura do controle original" in casa.diz("o ar está ligado?")


def test_temperatura_no_limite(casa):
    casa.ensina("ar", "frio_16", "MTY=")
    casa.registro.guardar_estado("ar", ligado=True, temperatura=16, modo="frio", ventilacao="auto")
    assert casa.diz("abaixa a temperatura") == "O ar já está no mínimo, em 16 graus."
    assert casa.local.enviados == []


def test_desliga_a_tv_e_o_ar_juntos(casa):
    casa.ensina("tv", "energia", "UE9XRVI=")
    casa.ensina("ar", "desligar", "REVTTElHQVI=")
    assert casa.diz("desliga a TV e o ar") == "Pronto, desliguei a TV e o ar."
    assert casa.local.enviados == [["UE9XRVI="], ["REVTTElHQVI="]]


def test_desliga_tudo_nao_religa_a_tv_que_ja_estava_desligada(casa):
    casa.ensina("tv", "energia", "UE9XRVI=")
    casa.ensina("ar", "desligar", "REVTTElHQVI=")
    casa.diz("desliga a TV")
    resposta = casa.diz("desliga tudo")
    assert resposta == ("Pronto, desliguei o ar. A TV eu deixei como estava: pelo que eu sei, ela já está desligada.")
    assert casa.local.enviados == [["UE9XRVI="], ["REVTTElHQVI="]]


# ------------------------------------------------------------------ Sony (códigos montados aqui)
def test_codigos_sony_sirc():
    from tinytuya.Contrib.IRRemoteControlDevice import IRRemoteControlDevice

    pulsos = controle_remoto.pulsos_sony(20)  # mudo
    assert pulsos[:2] == [2400, 600] and sum(pulsos[:26]) == 45000  # cabeçalho e um quadro a cada 45 ms
    # mudo = 20 = 0010100 (7 bits, o menos significativo primeiro) e TV = 1 (5 bits)
    bits = [1 if aceso == 1200 else 0 for aceso in pulsos[2:26:2]]
    assert bits == [0, 0, 1, 0, 1, 0, 0] + [1, 0, 0, 0, 0]
    assert len(pulsos) == 26 * controle_remoto.SONY_REPETICOES - 1
    cabeca, chave = controle_remoto.codigo_sony("mudo")
    assert cabeca.startswith("010FA0")  # 40 kHz na cabeça (0x0FA0 = 4000 x 10 Hz)
    assert IRRemoteControlDevice.head_key_to_pulses(cabeca, chave)[:len(pulsos)] == pulsos
    assert controle_remoto.codigo_sony("netflix") is None
    assert controle_remoto.SONY_TV["digito_1"] == 0 and controle_remoto.SONY_TV["digito_0"] == 9


def test_tv_sony_vai_pela_rede_de_casa_sem_a_nuvem(casa):
    casa.montar(nuvem=True, marca_tv="sony")
    assert casa.diz("desliga a TV") == "Pronto, desliguei a TV."
    assert casa.diz("desliga a TV") == "Pronto, desliguei a TV."  # código só de desligar: não pergunta
    assert casa.diz("liga a TV") == "Pronto, liguei a TV."
    assert casa.diz("aumenta o volume") == "Pronto, aumentei o volume da TV em 3."
    assert casa.nuvem.apertos == []
    desligar, ligar, volume = (controle_remoto.codigo_sony(b) for b in ("desligar", "ligar", "volume_mais"))
    assert casa.local.enviados == [[desligar], [desligar], [ligar], [volume] * 3]
    # Sem código Sony, a nuvem; a TV falsa da nuvem não tem play, então vem a explicação do que falta.
    assert casa.diz("aperta o play da TV").startswith("Ainda não sei o botão do play e pausa da TV.")
    assert casa.nuvem.apertos == []


def test_marca_da_tv_vem_do_controle_do_app(casa):
    casa.montar(nuvem=True)
    casa.registro.mudar(lambda d: d["nuvem"].update(controles={"tv": {"remote_id": "tv1", "category_id": 2, "marca": "Sony",
                                                                      "teclas": NuvemFalsa.TECLAS_TV}},
                                                     atualizado=casa.relogio()))
    casa.ensina("tv", "mudo", "QVBSRU5ESURP")  # o aprendido de um controle de verdade vem antes do montado
    casa.diz("põe no mudo")
    casa.diz("abaixa o volume")
    assert casa.local.enviados == [["QVBSRU5ESURP"], [controle_remoto.codigo_sony("volume_menos")] * 3]


# ------------------------------------------------------------------ nuvem
def test_ar_pela_nuvem_sem_aprender_nada(casa):
    casa.montar(local=False, nuvem=True)
    assert casa.diz("liga o ar no quente em 25 graus com ventilação alta") == \
        "Pronto, liguei o ar em 25 graus, no quente, ventilação alta."
    assert casa.diz("desliga o ar") == "Pronto, desliguei o ar."
    assert casa.nuvem.comandos_ar == [("ar1", True, "quente", 25, "alta"), ("ar1", False, "quente", 25, "alta")]
    assert casa.nuvem.listagens == 1  # a lista de controles fica guardada


# O key_range do ar Daikin do usuário (out/2026): 18 a 30 no frio, no quente e no automático; ventilar e desumidificar sem
# temperatura, e desumidificar só com a ventilação automática.
KEY_RANGE_DAIKIN = (
    [{"mode": m, "mode_name": n, "temp_list": [{"temp": t, "fan_list": [{"fan": f} for f in (0, 1, 2, 3)]} for t in range(18, 31)]}
     for m, n in ((0, "mode cool"), (1, "mode heat"), (2, "mode auto"))]
    + [{"mode": 3, "mode_name": "mode fan", "temp_list": [{"fan_list": [{"fan": 2}, {"fan": 0}, {"fan": 1}, {"fan": 3}],
                                                          "temp_name": "null"}]},
       {"mode": 4, "mode_name": "mode dry", "temp_list": [{"fan_list": [{"fan": 0}], "temp_name": "null"}]}]
)


def test_faixas_do_ar_pelo_key_range(monkeypatch):
    nuvem = controle_remoto.ControleNuvemTuya("us", "a", "b", "ir1")
    monkeypatch.setattr(nuvem, "_pedir", lambda caminho, corpo=None: {"category_id": 5, "key_range": KEY_RANGE_DAIKIN})
    faixas = nuvem.faixas_do_ar("ar1")
    assert faixas["0"] == {"temps": [18, 30], "ventos": [0, 1, 2, 3]}
    assert faixas["3"] == {"temps": None, "ventos": [0, 1, 2, 3]} and faixas["4"] == {"temps": None, "ventos": [0]}


def test_ar_daikin_respeita_o_que_ele_aceita(casa, monkeypatch):
    nuvem = NuvemFalsa()
    real = controle_remoto.ControleNuvemTuya("us", "a", "b", "ir1")
    monkeypatch.setattr(real, "_pedir", lambda caminho, corpo=None: {"key_range": KEY_RANGE_DAIKIN})
    nuvem.faixas = real.faixas_do_ar("ar1")
    casa.montar(local=False, nuvem=nuvem)
    assert casa.diz("liga o ar em 17 graus") == "Esse ar vai de 18 a 30 graus."
    assert casa.diz("liga o ar em 22 graus") == "Pronto, liguei o ar em 22 graus, no frio."
    assert casa.diz("põe o ar pra ventilar") == "Pronto, deixei o ar só ventilando."
    assert casa.diz("abaixa a temperatura") == "Só ventilando, o ar não tem temperatura para mudar."
    assert casa.diz("põe o ar pra desumidificar com ventilação alta") == \
        "Pronto, deixei o ar desumidificando, ventilação automática."  # ele só desumidifica na automática
    assert casa.diz("o ar está ligado?").startswith("Pelo último comando que mandei, o ar está ligado desumidificando")
    assert nuvem.comandos_ar == [("ar1", True, "frio", 22, "auto"), ("ar1", True, "ventilar", None, "auto"),
                                 ("ar1", True, "seco", None, "auto")]


def test_tv_pela_nuvem_acha_as_teclas(casa):
    casa.montar(local=False, nuvem=True)
    assert casa.diz("aumenta o volume") == "Pronto, aumentei o volume da TV em 3."
    assert casa.diz("põe no canal 13") == "Pronto, pus no canal 13."
    assert casa.nuvem.apertos == [("tv1", 2, "Volume+", 3), ("tv1", 2, "1", 1), ("tv1", 2, "3", 1)]


def test_o_aprendido_vai_pela_rede_de_casa_antes_da_nuvem(casa):
    casa.montar(nuvem=True)
    casa.ensina("tv", "energia", "UE9XRVI=")
    casa.diz("liga a TV")
    assert casa.local.enviados == [["UE9XRVI="]] and casa.nuvem.apertos == []
    casa.diz("põe no mudo")  # não aprendido: vai pela nuvem
    assert casa.nuvem.apertos == [("tv1", 2, "Mute", 1)]


def test_modo_local_nunca_usa_a_nuvem(casa):
    casa.montar(nuvem=True, modo="local")
    assert casa.agente.nuvem is None
    assert casa.diz("põe no mudo").startswith("Ainda não sei o botão do mudo da TV.")


def test_sem_controles_na_nuvem_explica_como_cadastrar(casa):
    casa.montar(local=False, nuvem=NuvemFalsa(controles=[]))
    assert casa.diz("atualiza os controles da nuvem").startswith("Na nuvem da Tuya não achei controles")
    assert "cadastre a TV no app Smart Life" in casa.diz("aumenta o volume")


def test_conferir_mostra_rede_nuvem_e_o_que_sabe(casa):
    casa.montar(nuvem=True)
    casa.ensina("tv", "energia", "UE9XRVI=")
    resposta = casa.diz("o controle remoto está funcionando?")
    assert resposta == ("O Ekaza respondeu na rede de casa (IP 192.168.0.50). A nuvem da Tuya respondeu: tem a TV e o ar. "
                        "Aprendidos: 1 botão da TV e 0 códigos do ar.")


def test_teclas_da_nuvem():
    teclas = [{"key": "PowerOn", "key_id": 0}, {"key": "PowerOff", "key_id": 0}, {"key": "Ch+", "key_id": 7},
              {"key": "vol_down", "key_id": 4}, {"key": "Num_5", "key_id": 15}]
    assert tecla_da_nuvem("ligar", teclas)["key"] == "PowerOn"
    assert tecla_da_nuvem("desligar", teclas)["key"] == "PowerOff"
    assert tecla_da_nuvem("energia", teclas) is None
    assert tecla_da_nuvem("canal_mais", teclas)["key"] == "Ch+"
    assert tecla_da_nuvem("volume_menos", teclas)["key"] == "vol_down"
    assert tecla_da_nuvem("digito_5", teclas)["key"] == "Num_5"


# ------------------------------------------------------------------ o aparelho (tinytuya falso)
REDE_FORA = {"Error": "Network Error: Device Unreachable", "Err": "905", "Payload": None}
# O que o Ekaza Mini 3 do usuário responde ao pedido de estado, conforme a versão (medido em out/2026).
RESPOSTAS_DO_EKAZA = {
    "3.3": {"Error": "Unexpected Payload from Device", "Err": "904", "Payload": None},
    "3.4": {"Error": "Check device key or version", "Err": "914", "Payload": None},
    "3.5": {"Error": "Invalid JSON Response from Device", "Err": "900", "Payload": "json obj data unvalid"},
}


class AparelhoIR:
    """Faz o papel do IRRemoteControlDevice do tinytuya (o set_value devolve o erro de rede como dicionário)."""

    def __init__(self, ip, tipo, retorno=None, aprendido="Q09ESUdP", tipo_certo=1):
        self.address, self.control_type, self.retorno = ip, tipo, retorno
        self.aprendido, self.tipo_certo = aprendido, tipo_certo
        self.mandados, self.fechado, self.esperou = [], False, None

    def set_sendWait(self, segundos):
        self.espera_envio = segundos

    def send_key(self, cabeca, chave):
        self.mandados.append((cabeca, chave))
        self.ultimo_retorno = self.retorno

    def send_button(self, codigo):
        assert getattr(self, "espera_envio", 0) >= 0.3, "a conexão tem de ficar aberta depois do envio"
        self.mandados.append(codigo)
        self.ultimo_retorno = self.retorno

    def receive_button(self, timeout=30):
        self.esperou = timeout
        if self.retorno:
            return self.retorno
        return self.aprendido if self.control_type == self.tipo_certo else None  # o tipo errado não ouve nada

    def close(self):
        self.fechado = True


class AparelhoSimples:
    """Faz o papel do tinytuya.Device usado para conferir a conexão e a versão."""
    testados = []

    def __init__(self, versao, respostas):
        self.versao, self.respostas = versao, respostas
        AparelhoSimples.testados.append(versao)

    def status(self):
        return self.respostas.get(self.versao, REDE_FORA)

    def close(self):
        pass


@pytest.fixture
def rede_falsa(monkeypatch):
    """Liga o ControleLocalTuya a aparelhos falsos; devolve as listas do que foi criado."""
    criados = {"ir": [], "simples": AparelhoSimples.testados}
    AparelhoSimples.testados.clear()
    estado = {"retorno": None, "tipo_certo": 1, "respostas": RESPOSTAS_DO_EKAZA}

    def aparelho(self, ip, versao, tipo):
        criado = AparelhoIR(ip, tipo, retorno=estado["retorno"], tipo_certo=estado["tipo_certo"])
        criado.versao = versao
        criados["ir"].append(criado)
        return criado

    monkeypatch.setattr(ControleLocalTuya, "_aparelho", aparelho)
    monkeypatch.setattr(ControleLocalTuya, "_aparelho_simples",
                        lambda self, ip, versao: AparelhoSimples(versao, estado["respostas"]))
    criados["estado"] = estado
    return criados


def test_controle_local_manda_aprende_e_fecha_a_conexao(tmp_path, rede_falsa):
    controle = ControleLocalTuya("id123", "chave", ip="192.168.0.9", versao="3.5", tipo=1,
                                 registro=RegistroDoControle(str(tmp_path / "c.json")))
    controle.enviar(["A", ("010FA0", "01$abc"), "B"], intervalo=0)
    assert rede_falsa["ir"][0].mandados == ["A", ("010FA0", "01$abc"), "B"] and rede_falsa["ir"][0].fechado
    assert controle.aprender(1) == "Q09ESUdP"
    assert controle.conferir() == "192.168.0.9"


def test_descobre_a_versao_do_ekaza_e_guarda(tmp_path, rede_falsa):
    registro = RegistroDoControle(str(tmp_path / "c.json"))
    controle = ControleLocalTuya("id123", "chave", ip="192.168.0.50", registro=registro)
    assert controle.conferir() == "192.168.0.50"  # o "json obj data unvalid" da 3.5 vale como resposta
    assert registro.ler()["rede"]["versao"] == "3.5"
    assert rede_falsa["simples"] == ["3.5", "3.5"]  # descobriu na primeira e conferiu com ela
    controle.conferir()
    assert rede_falsa["simples"] == ["3.5", "3.5", "3.5"]  # depois, só a versão guardada


def test_versao_que_nao_bate_vira_erro(tmp_path, rede_falsa):
    rede_falsa["estado"]["respostas"] = {"3.3": RESPOSTAS_DO_EKAZA["3.4"], "3.4": RESPOSTAS_DO_EKAZA["3.4"],
                                         "3.5": RESPOSTAS_DO_EKAZA["3.4"]}
    controle = ControleLocalTuya("id123", "chave", ip="192.168.0.50", registro=RegistroDoControle(str(tmp_path / "c.json")))
    with pytest.raises(ErroControle, match="a chave local ou a versão não batem"):
        controle.conferir()


def test_sem_saber_o_tipo_aprende_pelos_dois_e_guarda(tmp_path, rede_falsa):
    registro = RegistroDoControle(str(tmp_path / "c.json"))
    controle = ControleLocalTuya("id123", "chave", ip="192.168.0.50", versao="3.5", registro=registro)
    with pytest.raises(controle_remoto.TipoDesconhecido, match="ensine um botão primeiro"):
        controle.enviar("A")
    rede_falsa["estado"]["tipo_certo"] = 2
    assert controle.aprender(15) == "Q09ESUdP"
    assert [(a.control_type, a.esperou) for a in rede_falsa["ir"]] == [(1, 8), (2, 8)]
    assert registro.ler()["rede"]["tipo"] == 2
    controle.enviar("A")  # agora sabe o tipo
    assert rede_falsa["ir"][-1].control_type == 2 and rede_falsa["ir"][-1].mandados == ["A"]


def test_controle_fora_da_rede_vira_erro(tmp_path, rede_falsa):
    rede_falsa["estado"]["retorno"] = REDE_FORA
    rede_falsa["estado"]["respostas"] = {}
    controle = ControleLocalTuya("id123", "chave", ip="192.168.0.9", versao="3.5", tipo=1,
                                 registro=RegistroDoControle(str(tmp_path / "c.json")))
    with pytest.raises(ErroControle, match="fora de alcance"):
        controle.enviar("A")
    with pytest.raises(ErroControle, match="fora de alcance"):
        controle.conferir()
    with pytest.raises(ErroControle, match="fora de alcance"):
        controle.aprender(1)


def test_ip_guardado_que_nao_responde_procura_de_novo(tmp_path, rede_falsa, monkeypatch):
    registro = RegistroDoControle(str(tmp_path / "c.json"))
    registro.mudar(lambda d: d["rede"].update(ip="192.168.0.9", versao="3.4", tipo=1))
    usados = []

    def aparelho(self, ip, versao, tipo):
        usados.append((ip, versao))
        return AparelhoIR(ip, tipo, retorno=REDE_FORA if ip == "192.168.0.9" else None)

    def descobrir(self):
        self._guardar_rede(ip="192.168.0.33", versao="3.4")
        return "192.168.0.33", "3.4"

    monkeypatch.setattr(ControleLocalTuya, "_aparelho", aparelho)
    monkeypatch.setattr(ControleLocalTuya, "_descobrir_ip", descobrir)
    ControleLocalTuya("id123", "chave", registro=registro).enviar("A")
    assert usados == [("192.168.0.9", "3.4"), ("192.168.0.33", "3.4")]
    assert registro.ler()["rede"]["ip"] == "192.168.0.33"


def test_ip_que_mudou_e_achado_pela_porta_na_faixa_antiga(tmp_path, monkeypatch):
    # Na casa do usuário o PC (cabo, 192.168.1.x) e o Ekaza (Wi-Fi de outro roteador, 192.168.0.x) ficam em redes
    # diferentes: o broadcast não chega, mas a conexão direta sim.
    registro = RegistroDoControle(str(tmp_path / "c.json"))
    registro.mudar(lambda d: d["rede"].update(ip="192.168.0.50", versao="3.5"))
    faixas = []
    monkeypatch.setattr(controle_remoto, "hosts_com_porta_aberta",
                        lambda faixa, porta: faixas.append((faixa, porta)) or ["192.168.0.20", "192.168.0.61"])
    respostas = {"192.168.0.20": RESPOSTAS_DO_EKAZA["3.4"], "192.168.0.61": RESPOSTAS_DO_EKAZA["3.5"]}
    monkeypatch.setattr(ControleLocalTuya, "_aparelho_simples",
                        lambda self, ip, versao: type("D", (), {"status": lambda s: respostas[ip], "close": lambda s: None})())
    controle = ControleLocalTuya("id123", "chave", registro=registro)
    assert controle._procurar_na_faixa_antiga() == "192.168.0.61"  # o .20 é outro aparelho Tuya (a chave não bate)
    assert faixas == [("192.168.0", 6668)]
    assert registro.ler()["rede"]["ip"] == "192.168.0.61"


def test_ip_fixo_nao_procura_na_rede(tmp_path, rede_falsa):
    rede_falsa["estado"]["retorno"] = REDE_FORA
    controle = ControleLocalTuya("id123", "chave", ip="10.0.0.2", versao="3.5", tipo=1,
                                 registro=RegistroDoControle(str(tmp_path / "c.json")))
    with pytest.raises(ErroControle):
        controle.enviar("A")  # o conftest proíbe a busca na rede: se ela acontecesse, o erro seria outro


def test_le_o_que_o_wizard_gravou(tmp_path):
    (tmp_path / "devices.json").write_text(json.dumps([
        {"name": "Lâmpada", "id": "lamp1", "key": "k1", "category": "dj"},
        {"name": "Controle Universal", "id": "ir123", "key": "chave16caracter", "category": "wnykq",
         "ip": "192.168.0.40", "version": "3.4"},
    ]), encoding="utf-8")
    (tmp_path / "tinytuya.json").write_text(json.dumps({"apiKey": "ak", "apiSecret": "as", "apiRegion": "us"}),
                                            encoding="utf-8")
    dados = dados_do_wizard(str(tmp_path))
    assert (dados["id"], dados["chave_local"], dados["ip"], dados["versao"]) == ("ir123", "chave16caracter", "192.168.0.40", "3.4")
    assert (dados["chave_api"], dados["segredo_api"], dados["regiao"]) == ("ak", "as", "us")
    assert dados_do_wizard(str(tmp_path / "nada")) == {}


def test_registro_sobrevive_a_arquivo_estragado(tmp_path):
    caminho = tmp_path / "c.json"
    caminho.write_text("{quebrado", encoding="utf-8")
    registro = RegistroDoControle(str(caminho))
    assert registro.botoes("tv") == {}
    registro.guardar_botao("tv", "mudo", "TVVETw==")
    assert json.loads(caminho.read_text(encoding="utf-8"))["aparelhos"]["tv"]["botoes"] == {"mudo": "TVVETw=="}


# ------------------------------------------------------------------ ligação com o resto
def test_conftest_proibe_o_controle_de_verdade(tmp_path):
    controle = ControleLocalTuya("id", "chave", ip="10.0.0.2", versao="3.5", tipo=1,
                                 registro=RegistroDoControle(str(tmp_path / "c.json")))
    with pytest.raises(AssertionError, match="controle remoto de verdade"):
        controle.enviar("A")
    with pytest.raises(AssertionError, match="controle remoto de verdade"):
        controle.conferir()
    with pytest.raises(AssertionError, match="controle remoto de verdade"):
        ControleLocalTuya("id", "chave", registro=RegistroDoControle(str(tmp_path / "d.json"))).conferir()  # sem IP: busca
    with pytest.raises(AssertionError, match="controle remoto de verdade"):
        controle_remoto.ControleNuvemTuya("us", "a", "b", "id").controles()


def test_main_roteia_o_controle_antes_da_tela_da_agenda_e_dos_agentes():
    codigo = open("main.py", encoding="utf-8").read()
    laco = codigo[codigo.index("comando = obter_comando_usuario()"):]
    posicoes = [laco.index(trecho) for trecho in ("IMPRESSORA.responder(comando)", "CONTROLE.responder(comando)",
                                                   "TELA.responder(comando)", "AGENDA.responder(comando)",
                                                   "orquestrador.enviar(", "tentar_executar_ferramenta(comando)")]
    assert posicoes == sorted(posicoes)
    assert laco.index("BIOMETRIA.pode_tudo(falante)") < laco.index("CONTROLE.responder(comando)")  # convidado não chega


def test_paineis_tem_o_controle_antes_da_agenda():
    for arquivo, controle, agenda in (("painel_web.py", "self._controle.responder(comando)", "self._agenda.responder(comando)"),
                                      ("interface_texto.py", "self.controle.reconhece(comando)", "self.agenda.responder(comando)")):
        codigo = open(arquivo, encoding="utf-8").read()
        assert codigo.index(controle) < codigo.index(agenda), arquivo


def test_convidado_nao_mexe_na_tv_nem_no_ar():
    for pedido in ("liga a TV", "desliga o ar-condicionado", "aumenta o volume", "põe no mudo", "aprende o botão da TV"):
        assert main.RE_SO_DO_DONO.search(pedido), pedido


def test_main_usa_o_registro_temporario_nos_testes(tmp_path):
    assert main.CONTROLE.registro.caminho.startswith(str(tmp_path))
    assert main.CONTROLE.reconhece("liga a TV")
