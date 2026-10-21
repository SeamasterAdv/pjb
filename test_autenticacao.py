"""Testes do login (autenticacao.py): senha, bloqueio, código de recuperação, rosto, prova de vida e os modos.

Nenhum teste abre a câmera, baixa modelos ou usa um rosto de verdade: detector e codificador são falsos. Os "quadros" são
dicionários com o rosto (quem é e para onde olha) e o relógio é manual.
"""
import json

import numpy as np
import pytest

import autenticacao as aut
from autenticacao import (CadastroDeRosto, ContaLocal, FluxoDeEntrada, ModelosIndisponiveis, ReconhecedorFacial, Rosto,
                          VerificacaoDeRosto)


# ------------------------------------------------------------------ senha
def test_hash_nao_guarda_a_senha_e_confere():
    registro = aut.hash_segredo("uma senha boa 123")
    assert "uma senha boa" not in json.dumps(registro) and registro["alg"] == "scrypt"
    assert aut.conferir_segredo("uma senha boa 123", registro)
    assert not aut.conferir_segredo("uma senha boa 124", registro)
    assert aut.hash_segredo("uma senha boa 123")["sal"] != registro["sal"]  # sal diferente a cada vez
    assert not aut.conferir_segredo("x", {"lixo": 1})


def test_regras_da_senha_nova():
    assert aut.validar_senha_nova("curta") and aut.validar_senha_nova("12345678") and aut.validar_senha_nova("aaaaaaaaaa")
    assert aut.validar_senha_nova("felipe2026!", usuario="Felipe")
    assert aut.validar_senha_nova("cafe-com-leite-9", usuario="felipe") == ""


def test_codigo_de_recuperacao_tem_formato_e_ignora_tracos_e_caixa():
    codigo = aut.gerar_codigo_recuperacao()
    grupos = codigo.split("-")
    assert len(grupos) == 4 and all(len(g) == 5 for g in grupos) and not set(codigo) & set("01OI")
    assert aut._limpar_codigo(codigo.lower()) == aut._limpar_codigo(codigo)
    assert aut.gerar_codigo_recuperacao() != codigo


# ------------------------------------------------------------------ conta
@pytest.fixture
def relogio():
    return [1000.0]


@pytest.fixture
def conta(tmp_path, relogio):
    return ContaLocal(str(tmp_path / "login.json"), relogio=lambda: relogio[0], tentativas=3)


def vetores(n=3, voz=0):
    base = np.zeros(128, dtype=np.float32)
    base[voz] = 1.0
    return [base + 0.01 * i for i in range(n)]


def criar(conta, usuario="Felipe", senha="cafe-com-leite-9"):
    return conta.criar(usuario, senha, vetores(), 1)


def test_cria_o_login_e_confere_usuario_e_senha(conta):
    assert not conta.existe()
    codigo = criar(conta)
    assert conta.existe() and conta.usuario == "felipe" and conta.tem_rosto() and codigo
    assert conta.verificar_senha("FELIPE", "cafe-com-leite-9") and conta.verificar_senha(" felipe ", "cafe-com-leite-9")
    assert not conta.verificar_senha("felipe", "errada") and not conta.verificar_senha("outro", "cafe-com-leite-9")
    arquivo = open(conta.caminho, encoding="utf-8").read()
    assert "cafe-com-leite" not in arquivo and codigo.replace("-", "") not in arquivo  # nada em texto puro
    assert len(json.loads(arquivo)["rosto"]["vetores"][0]) == 128  # só números do rosto, nenhuma foto


def test_conta_recusa_senha_fraca_e_usuario_vazio(conta):
    with pytest.raises(ValueError):
        conta.criar("felipe", "123", vetores(), 1)
    with pytest.raises(ValueError):
        conta.criar("  ", "cafe-com-leite-9", vetores(), 1)
    assert not conta.existe()


def test_sem_conta_nada_confere(conta):
    assert not conta.verificar_senha("felipe", "qualquer coisa") and not conta.verificar_codigo("AAAAA-AAAAA-AAAAA-AAAAA")


def test_bloqueio_depois_de_varios_erros_e_cresce(conta, relogio):
    criar(conta)
    for _ in range(2):
        conta.registrar_falha()
    assert conta.segundos_de_bloqueio() == 0
    conta.registrar_falha()  # terceira: trava 30 s
    assert 29 < conta.segundos_de_bloqueio() <= 30
    relogio[0] += 31
    assert conta.segundos_de_bloqueio() == 0
    conta.registrar_falha()  # quarta: 60 s
    assert 59 < conta.segundos_de_bloqueio() <= 60
    relogio[0] += 61
    conta.registrar_falha()
    assert conta.segundos_de_bloqueio() > 100
    conta.registrar_sucesso()
    assert conta.segundos_de_bloqueio() == 0 and conta.falhas() == 0


def test_bloqueio_sobrevive_a_reabrir_o_programa(conta, relogio):
    criar(conta)
    for _ in range(3):
        conta.registrar_falha()
    outra = ContaLocal(conta.caminho, relogio=lambda: relogio[0], tentativas=3)
    assert outra.segundos_de_bloqueio() > 0


def test_codigo_de_recuperacao_confere_e_o_novo_invalida_o_antigo(conta):
    codigo = criar(conta)
    assert conta.verificar_codigo(codigo) and conta.verificar_codigo(codigo.lower().replace("-", " "))
    assert not conta.verificar_codigo("AAAAA-AAAAA-AAAAA-AAAAA")
    novo = conta.novo_codigo()
    assert conta.verificar_codigo(novo) and not conta.verificar_codigo(codigo)


def test_trocar_senha_e_refazer_rosto(conta):
    criar(conta)
    assert conta.trocar_senha("123") != ""
    assert conta.trocar_senha("outra-senha-forte-7") == ""
    assert conta.verificar_senha("felipe", "outra-senha-forte-7") and not conta.verificar_senha("felipe", "cafe-com-leite-9")
    conta.refazer_rosto(vetores(2, voz=5), -1)
    modelos, sinal = conta.rosto()
    assert len(modelos) == 2 and sinal == -1 and conta.verificar_senha("felipe", "outra-senha-forte-7")


def test_arquivo_corrompido_nao_derruba_o_login(conta):
    open(conta.caminho, "w").write("{isto não é json")
    assert not conta.existe() and not conta.verificar_senha("a", "b")


# ------------------------------------------------------------------ rosto (detector e codificador falsos)
class Quadro:
    """Um 'quadro' de câmera falso: de quem é o rosto, para onde ele olha (giro) e o tamanho."""

    def __init__(self, pessoa=0, giro=0.0, largura=160.0, com_rosto=True):
        self.pessoa, self.giro, self.largura, self.com_rosto = pessoa, giro, largura, com_rosto


def detectar(quadro):
    if not quadro.com_rosto:
        return None
    # olhos a 50 px de distância; o nariz fica deslocado `giro` x 50 px do ponto médio
    pontos = np.array([[100, 100], [150, 100], [125 + quadro.giro * 50, 125], [105, 150], [145, 150]], dtype=np.float32)
    return Rosto((50, 50, quadro.largura, quadro.largura), pontos, 0.99)


def codificar(quadro, rosto):
    v = np.zeros(128, dtype=np.float32)
    v[quadro.pessoa] = 1.0
    v[(quadro.pessoa + 1) % 128] = 0.1  # mesma pessoa = quase o mesmo vetor
    return v


@pytest.fixture
def rec():
    return ReconhecedorFacial(detectar_fn=detectar, vetor_fn=codificar)


def modelos_do_dono(rec, pessoa=0):
    return [rec.vetor(Quadro(pessoa), None) for _ in range(5)]


def test_giro_do_rosto_pelos_pontos():
    assert detectar(Quadro(giro=0)).giro == 0
    assert detectar(Quadro(giro=0.4)).giro == pytest.approx(0.4)
    assert detectar(Quadro(giro=-0.4)).giro == pytest.approx(-0.4)


def test_semelhanca_usa_as_melhores_amostras(rec):
    dono = modelos_do_dono(rec)
    assert aut.semelhanca(rec.vetor(Quadro(0), None), dono) > 0.99
    assert aut.semelhanca(rec.vetor(Quadro(7), None), dono) < 0.2
    assert aut.semelhanca(rec.vetor(Quadro(0), None), []) == -1.0


def rodar(verif, quadros):
    estado = None
    for quadro in quadros:
        estado = verif.passo(quadro)
        if estado.fase in ("ok", "falhou"):
            break
    return estado


def test_dono_olha_de_frente_e_vira_para_o_lado_pedido(rec):
    verif = VerificacaoDeRosto(rec, modelos_do_dono(rec), sinal_esquerda=1, direcao="esquerda")
    estados = [verif.passo(Quadro(0)) for _ in range(5)]
    assert estados[-1].fase == "virar" and "lado esquerdo" in estados[-1].mensagem
    assert verif.passo(Quadro(0)).fase == "virar"  # sem virar ainda
    # esquerda com sinal +1: o nariz anda para o lado positivo
    estado = rodar(verif, [Quadro(0, giro=0.4)] * 3)
    assert estado.fase == "ok"


def test_virar_para_o_lado_errado_nao_vale(rec):
    verif = VerificacaoDeRosto(rec, modelos_do_dono(rec), sinal_esquerda=1, direcao="esquerda")
    rodar(verif, [Quadro(0)] * 5)
    assert rodar(verif, [Quadro(0, giro=-0.5)] * 6).fase == "virar"


def test_sentido_da_camera_espelhada_e_respeitado(rec):
    # câmera que espelha: a esquerda da pessoa aparece com sinal -1
    verif = VerificacaoDeRosto(rec, modelos_do_dono(rec), sinal_esquerda=-1, direcao="direita")
    rodar(verif, [Quadro(0)] * 5)
    assert rodar(verif, [Quadro(0, giro=0.4)] * 3).fase == "ok"


def test_foto_parada_nao_passa(rec, relogio):
    verif = VerificacaoDeRosto(rec, modelos_do_dono(rec), 1, relogio=lambda: relogio[0], direcao="esquerda")
    rodar(verif, [Quadro(0)] * 5)
    for _ in range(40):
        relogio[0] += 0.4
        estado = verif.passo(Quadro(0))
        if estado.fase == "falhou":
            break
    assert estado.fase == "falhou" and "virar" in estado.mensagem


def test_outra_pessoa_nao_passa_nem_trocando_na_virada(rec, relogio):
    verif = VerificacaoDeRosto(rec, modelos_do_dono(rec), 1, relogio=lambda: relogio[0], direcao="esquerda")
    for _ in range(10):
        estado = verif.passo(Quadro(7))
    assert estado.fase == "procurar" and "Não reconheço" in estado.mensagem
    relogio[0] += aut.TEMPO_DA_VERIFICACAO + 1
    assert verif.passo(Quadro(7)).fase == "falhou"

    # o dono confirma de frente e, na virada, entra outro rosto
    verif = VerificacaoDeRosto(rec, modelos_do_dono(rec), 1, direcao="esquerda")
    rodar(verif, [Quadro(0)] * 5)
    assert rodar(verif, [Quadro(7, giro=0.5)] * 5).fase == "virar"


def test_orientacoes_sem_rosto_longe_ou_de_lado(rec):
    verif = VerificacaoDeRosto(rec, modelos_do_dono(rec), 1)
    assert "Procurando" in verif.passo(Quadro(com_rosto=False)).mensagem
    assert "mais perto" in verif.passo(Quadro(0, largura=50)).mensagem
    assert "de frente" in verif.passo(Quadro(0, giro=0.6)).mensagem


def test_a_direcao_e_sorteada(rec):
    direcoes = {VerificacaoDeRosto(rec, modelos_do_dono(rec), 1).direcao for _ in range(40)}
    assert direcoes == {"esquerda", "direita"}


# ------------------------------------------------------------------ cadastrar o rosto
def test_cadastro_guarda_amostras_descobre_o_sentido_e_testa(rec, relogio):
    cad = CadastroDeRosto(rec, relogio=lambda: relogio[0])
    estado = None
    for _ in range(aut.AMOSTRAS_DO_ROSTO + 2):
        relogio[0] += 0.4
        estado = cad.passo(Quadro(3))
        if cad.fase != "frente":
            break
    assert cad.fase == "esquerda" and len(cad.vetores) == aut.AMOSTRAS_DO_ROSTO and "esquerdo" in estado.mensagem
    # câmera espelhada: o lado esquerdo da pessoa vira o nariz para o lado negativo
    for _ in range(3):
        estado = cad.passo(Quadro(3, giro=-0.45))
    assert cad.fase == "teste" and cad.sinal_esquerda == -1
    # o teste final usa o sentido que acabou de aprender
    for _ in range(5):
        relogio[0] += 0.1
        estado = cad.passo(Quadro(3))
    direcao = cad._teste.direcao
    giro = -0.45 if direcao == "esquerda" else 0.45
    for _ in range(3):
        estado = cad.passo(Quadro(3, giro=giro))
    assert estado.fase == "pronto" and cad.fase == "pronto"


def test_cadastro_recomeca_se_o_teste_falha(rec, relogio):
    cad = CadastroDeRosto(rec, relogio=lambda: relogio[0])
    for _ in range(12):
        relogio[0] += 0.4
        cad.passo(Quadro(3))
    for _ in range(3):
        cad.passo(Quadro(3, giro=0.45))
    assert cad.fase == "teste"
    relogio[0] += aut.TEMPO_DA_VERIFICACAO + 1
    estado = cad.passo(Quadro(3))
    assert estado.fase == "frente" and cad.fase == "frente" and cad.vetores == []


def test_cadastro_ignora_rosto_de_lado_ou_longe(rec, relogio):
    cad = CadastroDeRosto(rec, relogio=lambda: relogio[0])
    relogio[0] += 1
    cad.passo(Quadro(3, giro=0.7))
    cad.passo(Quadro(3, largura=40))
    cad.passo(Quadro(com_rosto=False))
    assert cad.vetores == []


# ------------------------------------------------------------------ juntar os fatores
@pytest.fixture
def pronta(conta, rec):
    codigo = conta.criar("felipe", "cafe-com-leite-9", modelos_do_dono(rec), 1)
    return conta, codigo


def test_rosto_mais_senha_exige_os_dois(pronta):
    conta, _ = pronta
    fluxo = FluxoDeEntrada(conta, "rosto+senha")
    assert fluxo.usa_rosto and not fluxo.senha_liberada
    assert fluxo.entrar("felipe", "cafe-com-leite-9").mensagem == "Confirme o rosto primeiro."
    assert not fluxo.rosto_confirmado().ok and fluxo.senha_liberada
    assert not fluxo.entrar("felipe", "errada").ok
    assert fluxo.entrar("felipe", "cafe-com-leite-9").ok and conta.falhas() == 0


def test_rosto_ou_senha_qualquer_um_basta(pronta):
    conta, _ = pronta
    assert FluxoDeEntrada(conta, "rosto_ou_senha").rosto_confirmado().ok
    assert FluxoDeEntrada(conta, "rosto_ou_senha").entrar("felipe", "cafe-com-leite-9").ok


def test_so_senha_nao_pede_rosto(pronta):
    conta, _ = pronta
    fluxo = FluxoDeEntrada(conta, "senha")
    assert not fluxo.usa_rosto and fluxo.senha_liberada and fluxo.entrar("felipe", "cafe-com-leite-9").ok


def test_modo_desconhecido_cai_no_mais_seguro(pronta):
    assert FluxoDeEntrada(pronta[0], "qualquer").modo == "rosto+senha"


def test_erros_travam_a_conta_e_o_certo_nao_entra_travado(pronta, relogio):
    conta, _ = pronta
    fluxo = FluxoDeEntrada(conta, "senha")
    for _ in range(3):
        resultado = fluxo.entrar("felipe", "errada")
    assert not resultado.ok and resultado.bloqueio > 0 and "Tente de novo" in resultado.mensagem
    resposta = fluxo.entrar("felipe", "cafe-com-leite-9")  # a senha certa durante o bloqueio também espera
    assert not resposta.ok and resposta.bloqueio > 0
    relogio[0] += 31
    assert fluxo.entrar("felipe", "cafe-com-leite-9").ok


def test_falha_do_rosto_conta_como_tentativa(pronta):
    conta, _ = pronta
    fluxo = FluxoDeEntrada(conta, "rosto+senha")
    fluxo.rosto_falhou()
    assert conta.falhas() == 1


def test_codigo_de_recuperacao_libera_a_senha_e_pede_novo_rosto(pronta):
    conta, codigo = pronta
    fluxo = FluxoDeEntrada(conta, "rosto+senha")
    assert not fluxo.usar_codigo("AAAAA-AAAAA-AAAAA-AAAAA").ok and not fluxo.senha_liberada
    assert "aceito" in fluxo.usar_codigo(codigo).mensagem and fluxo.senha_liberada
    resultado = fluxo.entrar("felipe", "cafe-com-leite-9")
    assert resultado.ok and resultado.refazer_rosto
    assert conta.verificar_codigo(codigo)  # só deixa de valer quando o cadastro do rosto termina e mostra o novo
    novo = aut.gerar_codigo_recuperacao()
    conta.novo_codigo(novo)
    assert not conta.verificar_codigo(codigo) and conta.verificar_codigo(novo)


def test_sem_rosto_cadastrado_pede_so_a_senha(conta):
    conta.criar("felipe", "cafe-com-leite-9", [], 1)
    fluxo = FluxoDeEntrada(conta, "rosto+senha")
    assert not fluxo.usa_rosto and fluxo.senha_liberada


# ------------------------------------------------------------------ modelos de rosto
def test_modelos_sao_baixados_conferidos_e_reaproveitados(tmp_path, monkeypatch):
    conteudos = {nome: nome.encode() * 10 for nome in aut.MODELOS_ROSTO}
    esperado = {nome: aut.hashlib.sha256(c).hexdigest() for nome, c in conteudos.items()}
    monkeypatch.setattr(aut, "MODELOS_ROSTO", {n: (f"http://x/{n}", esperado[n]) for n in conteudos})
    baixados = []

    def baixar(url, destino):
        baixados.append(url)
        open(destino, "wb").write(conteudos[url.rsplit("/", 1)[1]])

    detector, reconhecedor = aut.garantir_modelos(str(tmp_path), baixar=baixar)
    assert len(baixados) == 2 and detector.endswith("yunet_2023mar.onnx") and reconhecedor.endswith("sface_2021dec.onnx")
    aut.garantir_modelos(str(tmp_path), baixar=baixar)
    assert len(baixados) == 2  # já estavam lá e conferem


def test_modelo_adulterado_ou_sem_internet_nao_e_aceito(tmp_path, monkeypatch):
    monkeypatch.setattr(aut, "MODELOS_ROSTO", {"a.onnx": ("http://x/a.onnx", aut.hashlib.sha256(b"certo").hexdigest())})

    def baixar_errado(url, destino):
        open(destino, "wb").write(b"adulterado")

    with pytest.raises(ModelosIndisponiveis, match="não confere"):
        aut.garantir_modelos(str(tmp_path), baixar=baixar_errado)
    assert not list(tmp_path.glob("*"))  # nem o arquivo temporário fica

    def sem_internet(url, destino):
        raise OSError("sem rede")

    with pytest.raises(ModelosIndisponiveis, match="Não consegui baixar"):
        aut.garantir_modelos(str(tmp_path), baixar=sem_internet)


# ------------------------------------------------------------------ chamada do main
def test_exigir_login_so_segue_se_a_tela_terminou_bem(monkeypatch):
    import subprocess
    import types

    chamadas = []

    def falso(retorno):
        return lambda comando, cwd=None: chamadas.append(comando) or types.SimpleNamespace(returncode=retorno)

    monkeypatch.setattr("config.LOGIN_FACIAL", False)
    monkeypatch.setattr(subprocess, "run", falso(0))
    assert aut.exigir_login() is True and chamadas == []  # desligado: nem abre a tela

    monkeypatch.setattr("config.LOGIN_FACIAL", True)
    assert aut.exigir_login() is True and chamadas[0][-1] == "--entrar" and chamadas[0][1].endswith("login_tela.py")
    monkeypatch.setattr(subprocess, "run", falso(1))
    assert aut.exigir_login() is False  # fechou a janela ou errou: o main não abre

    def quebra(comando, cwd=None):
        raise OSError("sem python")

    monkeypatch.setattr(subprocess, "run", quebra)
    assert aut.exigir_login() is False  # na dúvida, não abre


# ------------------------------------------------------------------ rosto sozinho entra; senão, a senha
def test_o_padrao_e_entrar_so_com_o_rosto_e_cair_na_senha():
    import config

    assert config.LOGIN_MODO == "rosto_ou_senha"


def test_entra_so_com_o_rosto_so_se_o_modo_e_o_rosto_esta_cadastrado(conta, rec):
    conta.criar("felipe", "cafe-com-leite-9", modelos_do_dono(rec), 1)
    assert FluxoDeEntrada(conta, "rosto_ou_senha").entra_so_com_o_rosto
    assert not FluxoDeEntrada(conta, "rosto+senha").entra_so_com_o_rosto
    assert not FluxoDeEntrada(conta, "senha").entra_so_com_o_rosto
    sem_rosto = ContaLocal(conta.caminho + ".2")
    sem_rosto.criar("ana", "cafe-com-leite-9", [], 1)
    assert not FluxoDeEntrada(sem_rosto, "rosto_ou_senha").entra_so_com_o_rosto  # sem rosto cadastrado, só a senha


def test_outra_pessoa_desiste_depressa_sem_esperar_o_tempo_todo(rec, relogio):
    verif = VerificacaoDeRosto(rec, modelos_do_dono(rec), 1, relogio=lambda: relogio[0], direcao="esquerda")
    estados = []
    for _ in range(aut.ERROS_PARA_DESISTIR):
        relogio[0] += 0.05  # ~1,5 s no total, muito antes dos 15 s
        estados.append(verif.passo(Quadro(7)))
    assert estados[-1].fase == "falhou" and "Não reconheci" in estados[-1].mensagem
    assert estados[8].mensagem == "Não reconheço este rosto." and verif.viu_rosto
    assert relogio[0] - 1000.0 < aut.TEMPO_DA_VERIFICACAO


def test_um_quadro_ruim_do_dono_nao_derruba_a_verificacao(rec):
    verif = VerificacaoDeRosto(rec, modelos_do_dono(rec), 1, direcao="esquerda")
    for _ in range(aut.ERROS_PARA_DESISTIR - 1):  # quase desistiu...
        assert verif.passo(Quadro(7)).fase == "procurar"
    verif.passo(Quadro(0))  # ...mas um quadro seu zera a contagem de erros seguidos
    for _ in range(aut.ERROS_PARA_DESISTIR - 1):
        assert verif.passo(Quadro(7)).fase == "procurar"
    assert rodar(verif, [Quadro(0)] * 5).fase == "virar"


def test_camera_vazia_nao_conta_como_tentativa_errada(conta, rec, relogio):
    conta.criar("felipe", "cafe-com-leite-9", modelos_do_dono(rec), 1)
    verif = VerificacaoDeRosto(rec, modelos_do_dono(rec), 1, relogio=lambda: relogio[0])
    verif.passo(Quadro(com_rosto=False))
    relogio[0] += aut.TEMPO_DA_VERIFICACAO + 1
    assert verif.passo(Quadro(com_rosto=False)).fase == "falhou" and not verif.viu_rosto
    fluxo = FluxoDeEntrada(conta, "rosto_ou_senha")
    assert fluxo.rosto_falhou(viu_rosto=False).mensagem == "Não reconheci o seu rosto." and conta.falhas() == 0
    fluxo.rosto_falhou(viu_rosto=True)  # já um rosto que não bateu conta
    assert conta.falhas() == 1


def test_falhou_o_rosto_a_senha_sozinha_basta_no_modo_rosto_ou_senha(conta, rec):
    conta.criar("felipe", "cafe-com-leite-9", modelos_do_dono(rec), 1)
    fluxo = FluxoDeEntrada(conta, "rosto_ou_senha")
    fluxo.rosto_falhou()
    assert fluxo.senha_liberada and fluxo.entrar("felipe", "cafe-com-leite-9").ok
    assert conta.falhas() == 0  # entrar zera as tentativas
