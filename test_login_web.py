"""Tela de login web (login_web.ApiLogin): o que aparece em cada passo e o que cada botão faz.

Nenhum teste abre a câmera, o microfone ou a janela: câmera, detector e codificador são falsos (os mesmos "quadros" de
test_autenticacao.py) e a imagem para a página é trocada por bytes fixos.
"""
import time

import pytest

import autenticacao as aut
import login_web
from autenticacao import ContaLocal, FluxoDeEntrada, ReconhecedorFacial
from test_autenticacao import Quadro, codificar, detectar, modelos_do_dono

SENHA = "cafe-com-leite-9"


class CameraFalsa:
    def __init__(self, quadros=(), abre=True):
        self.quadros, self.abre, self.aberta, self.lidos = list(quadros), abre, False, 0

    def abrir(self):
        self.aberta = self.abre
        return self.abre

    def ler(self):
        if not self.aberta:
            return None
        self.lidos += 1
        return self.quadros.pop(0) if len(self.quadros) > 1 else (self.quadros[0] if self.quadros else None)

    def fechar(self):
        self.aberta = False


@pytest.fixture(autouse=True)
def sem_imagem_de_verdade(monkeypatch):
    monkeypatch.setattr(login_web, "_imagem_para_a_pagina", lambda quadro: b"jpeg")


@pytest.fixture
def relogio():
    return [1000.0]


@pytest.fixture
def conta(tmp_path, relogio):
    return ContaLocal(str(tmp_path / "login.json"), relogio=lambda: relogio[0], tentativas=3)


@pytest.fixture
def rec():
    return ReconhecedorFacial(detectar_fn=detectar, vetor_fn=codificar)


def api_de(conta, rec, modo="rosto_ou_senha", pedido="entrar", camera=None, preparar=False, monkeypatch=None):
    api = login_web.ApiLogin(conta, pedido, reconhecedor=rec, camera=camera or CameraFalsa(), fluxo=FluxoDeEntrada(conta, modo),
                             microfone=False)
    if not preparar:  # sem a thread da câmera: os testes entregam os estados à mão
        api._preparar_camera = lambda: None
    return api


def verificar_dono(api, quadros=None):
    """Passa os quadros do dono (de frente e virando para o lado sorteado) pela verificação da tela."""
    verificacao = api._verificacao
    sentido = 0.5 if verificacao.direcao == "esquerda" else -0.5
    for quadro in quadros or ([Quadro(0)] * 5 + [Quadro(0, giro=sentido)] * 3):
        estado = verificacao.passo(quadro)
        api._aplicar_estado(quadro, estado)
        if estado.fase in ("ok", "falhou"):
            break


def test_sem_login_abre_o_cadastro_e_cria_a_conta(conta, rec):
    api = api_de(conta, rec)
    tela = api.inicio()
    assert tela["pagina"] == "credenciais" and not tela["mostra_video"] and not tela["terminou"]
    assert "pelo menos 3" in api.confirmar_credenciais("fe", SENHA, SENHA)["mensagem"]
    assert api.confirmar_credenciais("felipe", "12345678", "12345678")["tom"] == "erro"
    assert "não são iguais" in api.confirmar_credenciais("felipe", SENHA, SENHA + "x")["mensagem"]
    tela = api.confirmar_credenciais("felipe", SENHA, SENHA)
    assert tela["pagina"] == "rosto" and tela["mostra_video"] and tela["usuario"] == "felipe"
    tela = api.sem_rosto()
    codigo = tela["codigo_novo"]
    assert tela["pagina"] == "codigo" and len(codigo) == 23 and tela["tom"] == "aviso"
    assert api.concluir(False)["tom"] == "erro" and not conta.existe()  # sem marcar que guardou, não conclui
    tela = api.concluir(True)
    assert tela["terminou"] and tela["sucesso"] and tela["codigo_novo"] == ""
    assert conta.verificar_senha("felipe", SENHA) and conta.verificar_codigo(codigo) and not conta.tem_rosto()


def test_cadastro_do_rosto_vai_para_o_codigo_e_guarda_os_vetores(conta, rec, relogio):
    api = api_de(conta, rec)
    api.inicio()
    api.confirmar_credenciais("felipe", SENHA, SENHA)
    cadastro = api._cadastro
    cadastro.relogio = lambda: relogio[0]
    quadros = [Quadro(0)] * 8 + [Quadro(0, giro=0.5)] * 3 + [Quadro(0)] * 5
    for quadro in quadros:
        relogio[0] += 0.4
        api._aplicar_estado(quadro, cadastro.passo(quadro))
        if api.estado()["pagina"] != "rosto":
            break
    else:  # o teste final do cadastro sorteia o lado: vira para onde ele pedir
        sentido = 0.5 if cadastro._teste.direcao == "esquerda" else -0.5
        for quadro in [Quadro(0, giro=sentido)] * 3:
            api._aplicar_estado(quadro, cadastro.passo(quadro))
    assert api.estado()["pagina"] == "codigo" and len(api._vetores) == aut.AMOSTRAS_DO_ROSTO
    assert api.concluir(True)["sucesso"] and conta.tem_rosto()


def test_o_dono_entra_so_com_o_rosto(conta, rec):
    conta.criar("felipe", SENHA, modelos_do_dono(rec), 1)
    api = api_de(conta, rec)
    tela = api.inicio()
    assert tela["pagina"] == "entrar" and tela["mostra_video"] and tela["link_senha"] and tela["link_codigo"]
    assert not tela["pede_senha"]
    verificar_dono(api)
    tela = api.estado()
    assert tela["terminou"] and tela["sucesso"] and tela["mensagem"] == "Bem-vindo, Felipe." and tela["progresso"] == 1.0


def test_outra_pessoa_cai_na_senha_e_conta_como_erro(conta, rec):
    conta.criar("felipe", SENHA, modelos_do_dono(rec), 1)
    api = api_de(conta, rec)
    api.inicio()
    verificar_dono(api, [Quadro(7)] * aut.ERROS_PARA_DESISTIR)
    tela = api.estado()
    assert tela["pede_senha"] and tela["link_de_novo"] and not tela["link_senha"] and tela["tom"] == "erro"
    assert tela["falhas"] == 1 and not tela["terminou"]
    assert api.enviar_senha("felipe", "errada")["mensagem"] == "Usuário ou senha incorretos."
    assert api.enviar_senha("Felipe", SENHA)["sucesso"]


def test_bloqueio_aparece_na_mensagem_e_trava_o_rosto(conta, rec, relogio):
    conta.criar("felipe", SENHA, modelos_do_dono(rec), 1)
    api = api_de(conta, rec, modo="senha")
    tela = api.inicio()
    assert tela["pede_senha"] and not tela["mostra_video"] and not tela["link_codigo"]
    for _ in range(3):
        tela = api.enviar_senha("felipe", "errada")
    assert tela["bloqueio"] > 0 and "Tente de novo" in tela["mensagem"] and tela["tom"] == "erro"
    assert not api.enviar_senha("felipe", SENHA)["sucesso"]  # a certa também espera
    relogio[0] += 31
    assert api.estado()["bloqueio"] == 0
    assert api.enviar_senha("felipe", SENHA)["sucesso"]


def test_codigo_de_recuperacao_libera_a_senha_e_refaz_o_rosto(conta, rec):
    codigo = conta.criar("felipe", SENHA, modelos_do_dono(rec), 1)
    api = api_de(conta, rec, modo="rosto+senha")
    api.inicio()
    assert api.abrir_codigo()["pede_codigo"]
    assert api.enviar_codigo("AAAAA-AAAAA-AAAAA-AAAAA")["tom"] == "erro"
    tela = api.enviar_codigo(codigo)
    assert tela["pede_senha"] and not tela["pede_codigo"] and tela["tom"] == "ok"
    tela = api.enviar_senha("felipe", SENHA)
    assert tela["pagina"] == "rosto" and not tela["terminou"]  # entrou pelo código: cadastra o rosto de novo
    api.fechar()  # desistiu do rosto novo: continua logado, e o código antigo segue valendo
    assert api.estado()["sucesso"] and conta.verificar_codigo(codigo)


def test_pedido_de_refazer_tudo_cancelado_nao_entra(conta, rec):
    conta.criar("felipe", SENHA, modelos_do_dono(rec), 1)
    api = api_de(conta, rec, modo="senha", pedido="cadastrar")
    api.inicio()
    assert api.enviar_senha("felipe", SENHA)["pagina"] == "credenciais"
    api.fechar()
    assert not api.estado()["sucesso"]


def test_camera_que_nao_abre_cai_na_senha(conta, rec):
    conta.criar("felipe", SENHA, modelos_do_dono(rec), 1)
    api = api_de(conta, rec)
    api.inicio()
    api._erro_de_preparo(api._geracao_da_camera, "Não consegui abrir a câmera.")
    tela = api.estado()
    assert tela["pede_senha"] and not tela["mostra_video"] and tela["link_de_novo"] and tela["camera"] == "erro"


def test_quadro_so_manda_a_imagem_quando_muda(conta, rec):
    api = api_de(conta, rec)
    assert api.quadro() == {"n": 0}
    api._jpeg, api._numero_do_quadro = b"abc", 1
    quadro = api.quadro(-1)
    assert quadro["n"] == 1 and quadro["imagem"] == "data:image/jpeg;base64,YWJj"
    assert api.quadro(1) == {"n": 1}


def test_niveis_do_microfone_valem_pouco_tempo(conta, rec):
    api = api_de(conta, rec)
    assert api.niveis() == {"voce": None}
    api._nivel = (time.time(), 0.5, -30.0, [0.2] * 16)
    assert api.niveis()["voce"]["nivel"] == 0.5
    api._nivel = (time.time() - 5, 0.5, -30.0, [0.2] * 16)
    assert api.niveis() == {"voce": None}


def test_a_pagina_nao_alcanca_a_conta_nem_a_camera(conta, rec):
    # O pywebview expõe os métodos públicos e os de objetos guardados em atributos públicos: só a ponte pode aparecer.
    api = api_de(conta, rec)
    publicos = [nome for nome in dir(api) if not nome.startswith("_")]
    assert all(callable(getattr(api, nome)) for nome in publicos)
    assert set(publicos) == {"inicio", "estado", "quadro", "niveis", "mostrar_senha", "abrir_codigo", "enviar_codigo",
                             "enviar_senha", "tentar_rosto_de_novo", "confirmar_credenciais", "sem_rosto", "concluir", "fechar"}


def test_a_camera_de_verdade_roda_numa_thread_e_entra(conta, rec, monkeypatch):
    conta.criar("felipe", SENHA, modelos_do_dono(rec), 1)
    monkeypatch.setattr(aut.random, "choice", lambda opcoes: "esquerda")
    camera = CameraFalsa([Quadro(0)] * 5 + [Quadro(0, giro=0.5)] * 3)
    api = api_de(conta, rec, camera=camera, preparar=True)
    api.inicio()
    limite = time.time() + 5
    while not api.estado()["terminou"] and time.time() < limite:
        time.sleep(0.02)
    tela = api.estado()
    assert tela["sucesso"] and api.quadro()["imagem"].startswith("data:image/jpeg")
    limite = time.time() + 2
    while camera.aberta and time.time() < limite:  # entrou: a própria thread fecha a câmera
        time.sleep(0.02)
    assert not camera.aberta
