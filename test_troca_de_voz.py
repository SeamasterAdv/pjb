"""Troca da voz falando ("usa a voz da Siri"): perfis, arquivo da escolha e frases prontas, sem gerar áudio de verdade."""
import asyncio
import json
import os
import time

import pytest

import biometria_voz
import voice


class _ComunicacaoFalsa:
    """Imita o edge_tts.Communicate: guarda a voz, a velocidade e o tom pedidos."""

    pedidas = []

    def __init__(self, texto, voz, rate, pitch):
        _ComunicacaoFalsa.pedidas.append((voz, rate, pitch))

    async def save(self, arquivo):
        with open(arquivo, "wb") as saida:
            saida.write(b"audio")


@pytest.fixture
def voz_gerada(monkeypatch):
    _ComunicacaoFalsa.pedidas = []
    monkeypatch.setattr(voice.edge_tts, "Communicate", _ComunicacaoFalsa)
    return _ComunicacaoFalsa.pedidas


def _gerar(tmp_path):
    asyncio.run(voice.gerar_audio("Oi!", str(tmp_path / "fala.mp3")))


@pytest.mark.parametrize("frase, perfil", [
    ("Beatriz, usa a voz da Siri", "siri"),
    ("muda sua voz para a da Siri", "siri"),
    ("você consegue falar com a voz da Siri?", "siri"),
    ("troca de voz", "siri"),  # com duas vozes, a outra
    ("quero outra voz", "siri"),
])
def test_pedidos_que_trocam_para_a_siri(frase, perfil):
    assert voice.responder_troca_de_voz(frase)
    assert voice.perfil_de_voz() == perfil


@pytest.mark.parametrize("frase", [
    "volta pra voz da Thalita",
    "volta para a voz normal",
    "volta a sua voz",
    "para de usar a voz da Siri",
    "não quero mais a voz da Siri",
    "tira a voz da Siri e volta pra de antes",
])
def test_pedidos_que_voltam_para_a_thalita(frase):
    assert voice.trocar_voz("siri")
    assert voice.responder_troca_de_voz(frase)
    assert voice.perfil_de_voz() == "thalita"


@pytest.mark.parametrize("frase", [
    "Beatriz, cadastra minha voz",  # biometria
    "quem está falando?",
    "manda uma mensagem de voz pra Maria",
    "pode abaixar a voz",
    "fala mais alto, não escuto sua voz",
    "a voz da Siri é bonita",
    "o que a Siri faz?",
    "liga o reconhecimento de voz",
])
def test_outros_pedidos_nao_mexem_na_voz(frase):
    assert voice.responder_troca_de_voz(frase) is None
    assert voice.perfil_de_voz() == "thalita"


def test_a_biometria_nao_pega_os_pedidos_de_troca():
    agente = biometria_voz.AgenteBiometria(biometria_voz.BIOMETRIA, avisar=lambda texto: None)
    for frase in ["usa a voz da Siri", "volta pra voz da Thalita", "quais vozes você tem?", "troca de voz"]:
        assert agente.responder(frase) is None, frase


def test_respostas_dizem_a_voz_e_como_voltar():
    assert "parecida com a da Siri" in voice.responder_troca_de_voz("usa a voz da Siri")
    assert voice.responder_troca_de_voz("usa a voz da Siri") == "Já tô com a voz parecida com a da Siri!"
    lista = voice.responder_troca_de_voz("quais vozes você tem?")
    assert "Thalita" in lista and "Siri" in lista and "Agora tô com a voz parecida com a da Siri" in lista
    assert "da Thalita" in voice.responder_troca_de_voz("volta pra voz da Thalita")


def test_a_voz_gerada_segue_o_perfil(tmp_path, voz_gerada):
    _gerar(tmp_path)
    voice.trocar_voz("siri")
    _gerar(tmp_path)
    assert voz_gerada == [
        (voice.VOZ_BEATRIZ, voice.VELOCIDADE_VOZ, voice.TOM_VOZ),
        ("pt-BR-FranciscaNeural", "+5%", "+0Hz"),  # a amostra 5_siri_francisca_neutra que o usuário escolheu
    ]
    assert voice.descricao_da_voz() == "Siri: Francisca +5%"


def test_escolha_fica_no_arquivo_e_vale_para_o_outro_processo(tmp_path, voz_gerada):
    voice.trocar_voz("siri")
    with open(voice.CAMINHO_VOZ_ESCOLHIDA, encoding="utf-8") as arquivo:
        assert json.load(arquivo) == {"perfil": "siri"}

    # O painel (outro processo) volta para a Thalita: aqui a próxima fala já sai nela.
    with open(voice.CAMINHO_VOZ_ESCOLHIDA, "w", encoding="utf-8") as arquivo:
        json.dump({"perfil": "thalita"}, arquivo)
    depois = time.time() + 5
    os.utime(voice.CAMINHO_VOZ_ESCOLHIDA, (depois, depois))
    _gerar(tmp_path)
    assert voz_gerada[-1][0] == voice.VOZ_BEATRIZ

    # Voz desconhecida no arquivo: a voz de sempre.
    voice.trocar_voz("siri")
    with open(voice.CAMINHO_VOZ_ESCOLHIDA, "w", encoding="utf-8") as arquivo:
        arquivo.write('{"perfil": "alexa"}')
    os.utime(voice.CAMINHO_VOZ_ESCOLHIDA, (depois + 5, depois + 5))
    assert voice.perfil_de_voz() == "thalita"


def test_frases_prontas_sao_refeitas_na_voz_nova(monkeypatch):
    monkeypatch.setattr(voice, "_FRASES_PRONTAS", {})
    geradas = []

    def gerar(texto):
        geradas.append((voice.perfil_de_voz(), texto))
        return f"falso-{voice.perfil_de_voz()}-{texto}.mp3"

    monkeypatch.setattr(voice, "_gerar_arquivo_fala", gerar)

    def esperar(chave):
        limite = time.time() + 5
        while chave not in voice._FRASES_PRONTAS and time.time() < limite:
            time.sleep(0.01)
        return voice._FRASES_PRONTAS.get(chave)

    voice.preparar_frases(["Fala!"])
    assert esperar(("thalita", "Fala!")) == "falso-thalita-Fala!.mp3"
    voice.trocar_voz("siri")
    assert esperar(("siri", "Fala!")) == "falso-siri-Fala!.mp3"
    voice.trocar_voz("thalita")  # a da Thalita continua guardada: não gera de novo
    time.sleep(0.1)
    assert geradas == [("thalita", "Fala!"), ("siri", "Fala!")]
