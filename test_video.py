"""Vídeos no estilo do operador: o estilo (estilo_video.py), a montagem (video_animado.py), o vídeo animado pelo Claude
(criacao_visual.py), "edita esse vídeo no meu estilo" (agente_craft.py), a ponte do painel e as rotas do app.

Sem rede, sem Chromium, sem voz online e sem Whisper: narração, ilustrações, transcrição e IA são falsas. Os MP4 são de
verdade (PyAV), pequenos.
"""
import base64
import io
import json
import math
import os
import shutil
import uuid
import wave

import av
import numpy as np
import pytest
from PIL import Image

import criacao_visual
import estilo_video
import video_animado
from criacao_visual import AgenteCriacao, interpretar, normalizar_roteiro
from llm_nuvem import NuvemIndisponivel

SVG = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 600'><circle cx='400' cy='300' r='200' fill='#f5c04a'/></svg>"
ROTEIRO = {"titulo": "Fotossíntese", "descricao": "Como as plantas comem luz", "cenas": [
    {"layout": "lado", "titulo": "A luz", "texto": "As folhas captam a luz.", "narracao": "Tudo começa com a luz do sol.",
     "ilustracao": SVG},
    {"layout": "lista", "titulo": "Entra e sai", "itens": ["Entra: luz e água", "Sai: oxigênio"], "narracao": "Entra luz e sai oxigênio."},
]}


# ------------------------------------------------------------------ falsos
def wav_falso(pasta, segundos=1.0, taxa=22050) -> str:
    caminho = os.path.join(pasta, f"fala_{uuid.uuid4().hex}.wav")  # as narrações são feitas em paralelo
    amostras = (np.sin(np.arange(int(segundos * taxa)) / taxa * 2 * math.pi * 300) * 8000).astype(np.int16)
    with wave.open(caminho, "wb") as arquivo:
        arquivo.setnchannels(1)
        arquivo.setsampwidth(2)
        arquivo.setframerate(taxa)
        arquivo.writeframes(amostras.tobytes())
    return caminho


def narrador(pasta, segundos=1.0):
    falas = []

    def narrar(texto, voz):
        falas.append((texto, voz))
        return wav_falso(pasta, segundos)
    narrar.falas = falas
    return narrar


def rasterizador(itens):
    return [Image.new("RGBA", (int(largura), int(altura)), (220, 40, 40, 255)) for _, largura, altura, _ in itens]


def video_de_entrada(caminho, segundos=1.0, tamanho=(320, 240), fps=10):
    quadros = (Image.new("RGB", tamanho, (i * 20 % 255, 80, 120)) for i in range(int(segundos * fps)))
    som = np.zeros((2, int(segundos * video_animado.TAXA)), dtype=np.float32)
    som[:, ::50] = 0.3
    video_animado.gravar_mp4(str(caminho), tamanho, fps, quadros, som)
    return str(caminho)


def duracao_e_tamanho(caminho):
    with av.open(caminho) as entrada:
        fluxo = entrada.streams.video[0]
        return entrada.duration / 1_000_000, (fluxo.codec_context.width, fluxo.codec_context.height), len(entrada.streams.audio)


# ------------------------------------------------------------------ o estilo
def test_estilo_padrao_salvar_e_recusar():
    assert estilo_video.atual()["formato"] == "16x9" and estilo_video.atual()["atualizado"] is None
    salvo = estilo_video.salvar({"formato": "9x16", "cores": "livre", "cores_livres": {"fundo": "#112233", "texto": "#ffffff"},
                                 "assinatura": "@eu", "guia_texto": "Flat e colorido", "edicao": {"filtro": "pb"}})
    assert salvo["formato"] == "9x16" and salvo["assinatura"] == "@eu" and salvo["guia"]["texto"] == "Flat e colorido"
    assert salvo["edicao"] == {"legendas": True, "filtro": "pb", "enquadrar": "original"}
    assert estilo_video.paleta(salvo)["fundo"] == "#112233" and estilo_video.paleta(salvo)["destaque"] == "#f5c04a"
    errado = estilo_video.resultado(estilo_video.salvar, {"transicao": "explosao"})
    assert not errado["ok"] and "transicao" in errado["mensagem"]
    assert estilo_video.resultado(estilo_video.salvar, {"assinatura": "x" * 61})["ok"] is False
    assert estilo_video.restaurar_padrao()["formato"] == "16x9"
    assert estilo_video.atual()["guia"]["texto"] == "Flat e colorido"  # o padrão não apaga o guia


def test_arquivo_quebrado_vale_o_padrao():
    os.makedirs(os.path.dirname(estilo_video.caminho()), exist_ok=True)
    with open(estilo_video.caminho(), "w", encoding="utf-8") as arquivo:
        json.dump({"formato": "cinema", "ritmo": "rapido"}, arquivo)
    assert estilo_video.atual()["formato"] == "16x9"


def test_bloco_do_prompt_leva_o_estilo_a_duracao_e_o_guia():
    estilo = {**estilo_video.atual(), "narracao": "nenhuma", "instrucoes": "termine com uma pergunta", "tom": "divertido",
              "guia": {"texto": "Ilustrações isométricas", "cores": {}}}
    bloco = estilo_video.bloco_do_prompt(estilo, segundos=30)
    assert "uns 30 segundos" in bloco and "Sem narração" in bloco and "termine com uma pergunta" in bloco
    assert "isométricas" in bloco and "divertido" in bloco
    assert "isométricas" not in estilo_video.bloco_do_prompt({**estilo, "usar_guia": False})


def test_exemplos_imagem_e_video_viram_imagens(tmp_path):
    imagem = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(imagem, "PNG")
    assert len(estilo_video.adicionar_exemplo("print.png", imagem.getvalue())["ids"]) == 1
    video = video_de_entrada(tmp_path / "ref.mp4", segundos=2.0)
    ids = estilo_video.adicionar_exemplo("ref.mp4", origem=video)["ids"]
    assert len(ids) == 3 and all(i.endswith(".jpg") for i in ids)
    assert len(estilo_video.listar_exemplos()) == 4
    with pytest.raises(estilo_video.ErroDoEstilo):
        estilo_video.adicionar_exemplo("virus.png", b"MZ nao e imagem")
    with pytest.raises(estilo_video.ErroDoEstilo):
        estilo_video.caminho_do_exemplo("../estilo_video.json")
    estilo_video.apagar_exemplo(ids[0])
    assert len(estilo_video.listar_exemplos()) == 3


def test_guia_dos_exemplos_so_pelo_claude_e_com_as_cores():
    imagem = io.BytesIO()
    Image.new("RGB", (40, 30), "blue").save(imagem, "JPEG")
    estilo_video.adicionar_exemplo("a.jpg", imagem.getvalue())
    vistos = []

    def claude(caminhos, prompt, sistema):
        vistos.append(caminhos)
        return json.dumps({"resumo": "Flat", "visual": ["sombra longa"], "ritmo": ["cortes rápidos"],
                           "cores": {"fundo": "#14213D", "texto": "#ffffff", "destaque": "nada"}})
    guia = estilo_video.extrair_guia(ver_com_claude=claude, ver_no_pc=lambda *a: pytest.fail("não devia ir ao PC"))
    assert guia["quem"] == "Claude" and "sombra longa" in guia["texto"] and guia["cores"] == {"fundo": "#14213D", "texto": "#ffffff"}
    assert estilo_video.paleta({**estilo_video.atual(), "cores": "guia"})["fundo"] == "#14213D"
    assert len(vistos[0]) == 1


# ------------------------------------------------------------------ a montagem
def test_legendas_repartem_o_tempo_e_seguem_as_palavras():
    legendas = video_animado.legendas_no_tempo("Uma frase curta. E outra frase um pouco maior que a primeira, para quebrar.", 1.0, 4.0)
    assert legendas[0][0] == 1.0 and abs(legendas[-1][1] - 5.0) < 1e-6 and len(legendas) >= 2
    palavras = [(0.0, 0.3, "Oi"), (0.3, 0.6, "gente."), (2.0, 2.4, "Hoje"), (2.4, 2.8, "vamos")]
    assert [t for _, _, t in video_animado.legendas_das_palavras(palavras)] == ["Oi gente.", "Hoje vamos"]


def test_svg_no_tamanho_troca_so_a_cabeca():
    svg = video_animado.svg_no_tamanho("<svg width='10' height=\"20\" viewBox='0 0 8 6'><rect width='5'/></svg>", 400, 300, True)
    assert svg.startswith("<svg viewBox='0 0 8 6' width=\"400\" height=\"300\" preserveAspectRatio=\"xMidYMid slice\"")
    assert "<rect width='5'/>" in svg


def test_renderizar_grava_o_mp4_com_som_abertura_e_assinatura(tmp_path):
    estilo = {**estilo_video.atual(), "ritmo": "rapido", "assinatura": "@eu"}
    narrar = narrador(str(tmp_path))
    progresso = []
    destino = str(tmp_path / "video.mp4")
    info = video_animado.renderizar(ROTEIRO, estilo, destino, progresso.append, narrar_com=narrar, rasterizar=rasterizador)
    assert info["cenas"] == 4  # abertura, as duas cenas e a assinatura
    duracao, tamanho, audios = duracao_e_tamanho(destino)
    assert tamanho == (1280, 720) and audios == 1 and abs(duracao - info["duracao"]) < 0.3
    assert [voz for _, voz in narrar.falas] == ["thalita", "thalita"] and "montando 100%" in progresso
    assert not os.path.exists(destino + ".parcial.mp4")


def test_sem_ilustracao_e_sem_narracao_o_video_sai_com_os_textos(tmp_path):
    estilo = {**estilo_video.atual(), "formato": "9x16", "narracao": "nenhuma", "abertura": False, "ritmo": "rapido"}

    def quebrado(itens):
        raise RuntimeError("sem navegador")
    destino = str(tmp_path / "em_pe.mp4")
    info = video_animado.renderizar(ROTEIRO, estilo, destino, narrar_com=lambda *a: pytest.fail("sem narração"),
                                    rasterizar=quebrado)
    assert info["cenas"] == 2 and duracao_e_tamanho(destino)[1] == (720, 1280)


@pytest.mark.parametrize("entrada", ["surgir", "deslizar", "digitar", "palavra", "pop"])
@pytest.mark.parametrize("transicao", ["corte", "dissolver", "deslizar", "zoom", "cortina"])
def test_cada_entrada_e_transicao_desenha(entrada, transicao, tmp_path):
    estilo = {**estilo_video.atual(), "entrada": entrada, "transicao": transicao, "legendas": "centro", "movimento": "passear"}
    montador = video_animado.Montador(estilo, narrar_com=narrador(str(tmp_path)), rasterizar=rasterizador)
    cenas = montador.preparar({"cenas": ROTEIRO["cenas"]})
    meio = montador.quadro(cenas[0], 1.0)
    assert meio.size == (1280, 720)
    mistura = montador.transicao_entre(montador.quadro(cenas[0], cenas[0].duracao), montador.quadro(cenas[1], 0.2), 0.5)
    assert mistura.size == (1280, 720)


def test_estilizar_video_enquadra_poe_titulo_legendas_e_assinatura(tmp_path):
    entrada = video_de_entrada(tmp_path / "meu.mp4", segundos=1.0)
    estilo = {**estilo_video.atual(), "formato": "9x16", "assinatura": "@eu",
              "edicao": {"filtro": "", "enquadrar": "encaixar", "legendas": True}}
    ouvidos = []

    def transcrever(amostras):
        ouvidos.append(amostras.size)
        return [(0.1, 0.4, "Oi"), (0.4, 0.8, "gente.")]
    destino = str(tmp_path / "estilizado.mp4")
    info = video_animado.estilizar_video(entrada, estilo, destino, "Férias", transcrever)
    duracao, tamanho, audios = duracao_e_tamanho(destino)
    assert info["legendas"] == 1 and tamanho == (720, 1280) and audios == 1
    assert abs(duracao - (1.0 + video_animado.DURACAO_DA_ASSINATURA)) < 0.4
    assert ouvidos and ouvidos[0] > 10000  # o som do vídeo, em 16 kHz


def test_estilizar_como_esta_mantem_o_tamanho_e_sem_legendas_nao_ouve(tmp_path):
    entrada = video_de_entrada(tmp_path / "meu.mp4", segundos=1.0, tamanho=(320, 240))
    estilo = {**estilo_video.atual(), "assinatura": "", "edicao": {"filtro": "", "enquadrar": "original", "legendas": False}}
    destino = str(tmp_path / "igual.mp4")
    video_animado.estilizar_video(entrada, estilo, destino, "", lambda a: pytest.fail("não devia ouvir"))
    assert duracao_e_tamanho(destino)[1] == (320, 240)


# ------------------------------------------------------------------ o vídeo pelo Claude
class IAFalsa:
    def __init__(self, nome, respostas):
        self.nome, self.respostas, self.pedidos = nome, list(respostas), []

    def chat(self, prompt, system_prompt=None, formato=None, **kwargs):
        self.pedidos.append({"prompt": prompt, "sistema": system_prompt, "formato": formato})
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def agente_de_video(tmp_path, *provedores):
    eventos, avisos, montagens = [], [], []

    def montar(roteiro, estilo, destino, ao_progredir):
        montagens.append({"roteiro": roteiro, "estilo": estilo, "destino": destino})
        ao_progredir("montando 50%")
        with open(destino, "wb") as arquivo:
            arquivo.write(b"mp4 de mentira")
        return {"duracao": 21.4, "quadros": 642, "cenas": len(roteiro["cenas"])}
    agente = AgenteCriacao(mostrar=eventos.append, avisar=lambda texto, canal, arquivos: avisos.append((texto, canal, arquivos)),
                           provedores=list(provedores), pasta=str(tmp_path / "criacoes"), em_segundo_plano=False, montar=montar)
    return agente, eventos, avisos, montagens


@pytest.mark.parametrize("frase, assunto, opcoes", [
    ("faz um vídeo sobre a fotossíntese", "a fotossíntese", {}),
    ("cria um vídeo animado explicando a LGPD em pé de 30 segundos", "explicando a LGPD", {"formato": "9x16", "segundos": 30}),
    ("faz um reels sobre dicas de estudo sem narração", "dicas de estudo", {"formato": "9x16", "narracao": "nenhuma"}),
    ("faz um vídeo de 1 minuto e meio sobre a história do Brasil na horizontal", "a história do Brasil",
     {"formato": "16x9", "segundos": 90}),
    ("faz um vídeo quadrado sobre café sem legenda", "café", {"formato": "1x1", "legendas": "nenhuma"}),
    ("faz um vídeo curto sobre como funciona a fotossíntese", "como funciona a fotossíntese", {"segundos": 20}),
    ("faz um vídeo longo de 30 segundos sobre café", "café", {"segundos": 30}),
])
def test_reconhece_os_pedidos_de_video(frase, assunto, opcoes):
    pedido = interpretar(frase)
    assert (pedido.formato, pedido.assunto, pedido.opcoes) == ("video", assunto, opcoes)


@pytest.mark.parametrize("frase", ["faz um vídeo desse PDF", "cria um vídeo com essas fotos", "corta esse vídeo do 10 ao 30",
                                   "como fazer um vídeo no celular?"])
def test_nao_e_video_novo(frase):
    assert interpretar(frase) is None


def test_normalizar_roteiro_limpa_as_cenas():
    roteiro = normalizar_roteiro({"titulo": "x" * 200, "cenas": [
        {"layout": "explosao", "titulo": "Oi", "ilustracao": "<svg><rect></svg>", "fundo": "#abc", "duracao": 99},
        {"layout": "lista"},  # sem texto nenhum: sai
        "nada",
        {"titulo": "Com desenho", "ilustracao": "<svg xmlns='http://www.w3.org/2000/svg' onload='x()'><rect/></svg>",
         "itens": ["a", "", "b", "c", "d", "e", "f"]},
    ]})
    primeira, segunda = roteiro["cenas"]
    assert len(roteiro["titulo"]) == 80 and primeira["layout"] == "" and "ilustracao" not in primeira
    assert primeira["fundo"] == "#aabbcc" and primeira["duracao"] == 20
    assert "onload" not in segunda["ilustracao"] and segunda["itens"] == ["a", "b", "c", "d"]
    with pytest.raises(criacao_visual.ErroDeCriacao):
        normalizar_roteiro({"cenas": [{"layout": "lista"}]})


def test_video_pelo_claude_no_estilo_aparece_no_centro(tmp_path):
    estilo_video.salvar({"tom": "divertido", "instrucoes": "termine com uma pergunta"})
    claude = IAFalsa("Claude", [json.dumps(ROTEIRO)])
    agente, eventos, avisos, montagens = agente_de_video(tmp_path, claude)
    resposta = agente.responder("faz um vídeo em pé sobre a fotossíntese")
    assert resposta.startswith("Fazendo um vídeo sobre a fotossíntese com o Claude, no seu estilo")
    pedido = claude.pedidos[0]
    assert pedido["formato"] == "json" and "divertido" in pedido["sistema"] and "termine com uma pergunta" in pedido["sistema"]
    assert "em pé (9:16" in pedido["sistema"]  # a opção do pedido vale mais que o estilo
    assert montagens[0]["estilo"]["formato"] == "9x16" and montagens[0]["estilo"]["tom"] == "divertido"
    tipos = [e["tipo"] for e in eventos]
    assert tipos == ["criacao_iniciada", "criacao_andamento", "criacao"]
    final = eventos[-1]
    assert final["formato"] == "video" and final["arquivo"].endswith(".mp4") and final["duracao"] == 21.4 and final["cenas"] == 2
    assert os.path.isfile(final["arquivo"][:-4] + ".roteiro.json")
    assert avisos[-1][0] == "Pronto: o vídeo “Fotossíntese” tem 21 segundos e 2 cenas. Está no centro da tela."


def test_video_pedido_pelo_celular_volta_com_o_mp4(tmp_path):
    agente, eventos, avisos, _ = agente_de_video(tmp_path, IAFalsa("Claude", [json.dumps(ROTEIRO)]))
    agente.responder("faz um vídeo sobre a fotossíntese", canal="whatsapp")
    texto, canal, arquivos = avisos[-1]
    assert canal == "whatsapp" and arquivos == [eventos[-1]["arquivo"]] and "mandei o vídeo" in texto


def test_refaz_em_pe_sem_o_claude_e_ajuste_com_o_claude(tmp_path):
    claude = IAFalsa("Claude", [json.dumps(ROTEIRO), json.dumps({**ROTEIRO, "titulo": "Fotossíntese 2"})])
    agente, eventos, avisos, montagens = agente_de_video(tmp_path, claude)
    agente.responder("faz um vídeo sobre a fotossíntese")
    assert agente.tem_video_recente()
    assert agente.responder("refaz o vídeo em pé").startswith("Montando o vídeo de novo")
    assert len(claude.pedidos) == 1 and montagens[-1]["estilo"]["formato"] == "9x16"
    assert avisos[-1][0].startswith("Pronto, montei o vídeo de novo")
    assert agente.responder("muda a cena 2 do vídeo para falar do oxigênio").startswith("Mexendo no vídeo")
    assert len(claude.pedidos) == 2 and "Mudança pedida: muda a cena 2" in claude.pedidos[1]["prompt"]
    assert montagens[-1]["estilo"]["formato"] == "9x16"  # o "em pé" de antes continua


def test_refazer_pelo_roteiro_salvo_noutro_processo(tmp_path, monkeypatch):
    primeiro, _, _, _ = agente_de_video(tmp_path, IAFalsa("Claude", [json.dumps(ROTEIRO)]))
    primeiro.responder("faz um vídeo sobre a fotossíntese")
    roteiro = primeiro.ultima.arquivo[:-4] + ".roteiro.json"
    monkeypatch.setattr(criacao_visual, "ultimo_roteiro_salvo", lambda: roteiro)
    estilo_video.salvar({"cores": "neon"})
    outro, eventos, avisos, montagens = agente_de_video(tmp_path)  # o painel: nenhum vídeo feito por ele
    assert outro.refazer_video("painel").startswith("Montando")
    assert montagens[0]["estilo"]["cores"] == "neon" and montagens[0]["roteiro"]["titulo"] == "Fotossíntese"
    assert eventos[-1]["tipo"] == "criacao"


def test_sem_video_nenhum_refazer_explica(tmp_path):
    agente, _, _, _ = agente_de_video(tmp_path)
    assert agente.refazer_video().startswith("Ainda não fiz nenhum vídeo")


def test_criar_video_da_janela_so_aceita_as_opcoes_conhecidas(tmp_path):
    agente, _, _, montagens = agente_de_video(tmp_path, IAFalsa("Claude", [json.dumps(ROTEIRO)]))
    assert agente.criar_video("   ") == "Escreva sobre o que é o vídeo."
    agente.criar_video("café", opcoes={"formato": "1x1", "cores": "neon", "narracao": "nenhuma"})
    assert montagens[0]["estilo"]["formato"] == "1x1" and montagens[0]["estilo"]["narracao"] == "nenhuma"
    assert montagens[0]["estilo"]["cores"] == "preto_dourado"


def test_roteiro_sem_ia_avisa(tmp_path):
    agente, eventos, avisos, _ = agente_de_video(tmp_path, IAFalsa("Claude", [NuvemIndisponivel("limite")]))
    agente.responder("faz um vídeo sobre a fotossíntese")
    assert eventos[-1]["tipo"] == "criacao_falhou" and "não responderam" in avisos[-1][0]


# ------------------------------------------------------------------ "edita esse vídeo no meu estilo"
class AppsDeMentira:
    def __init__(self):
        self.edicoes = []

    def editar_video(self, entrada, edicao, pasta):
        self.edicoes.append(edicao)
        destino = os.path.join(pasta, "filtrado.mp4")
        shutil.copyfile(entrada, destino)
        return destino


def test_meu_estilo_no_video_recebido(tmp_path):
    from agente_craft import AgenteCraft

    estilo_video.salvar({"edicao": {"filtro": "pb", "enquadrar": "encaixar", "legendas": True}})
    video = video_de_entrada(tmp_path / "viagem.mp4")
    apps, chamadas, avisos = AppsDeMentira(), [], []

    def estilizar(entrada, estilo, destino, titulo, transcrever):
        chamadas.append({"entrada": entrada, "titulo": titulo, "enquadrar": estilo["edicao"]["enquadrar"]})
        shutil.copyfile(entrada, destino)
        return {"legendas": 4}
    craft = AgenteCraft(apps=apps, recebidos=lambda texto: [video], pasta_saida=str(tmp_path / "gerados"),
                        avisar=lambda resposta, canal: avisos.append(resposta), em_segundo_plano=lambda funcao: funcao(),
                        estilizar=estilizar, transcrever=lambda amostras: [], abrir_arquivo=lambda caminho: None)
    resposta = craft.responder("edita esse vídeo no meu estilo com o título Férias na praia", canal="whatsapp")
    assert resposta.texto.startswith("Pondo “viagem.mp4” no seu estilo com o título “Férias na praia”")
    assert apps.edicoes[0].filtro == "pb"  # o filtro do estilo, pelo FilmCraft
    assert chamadas[0]["titulo"] == "Férias na praia" and chamadas[0]["enquadrar"] == "encaixar"
    final = avisos[-1]
    assert "com 4 legendas" in final.texto and final.arquivos[0].endswith("viagem (meu estilo).mp4")


def test_meu_estilo_sem_video_deixa_para_o_video_animado(tmp_path):
    from agente_craft import AgenteCraft

    recente = {"sim": False}
    craft = AgenteCraft(apps=AppsDeMentira(), recebidos=lambda texto: [], pasta_saida=str(tmp_path),
                        video_animado_recente=lambda: recente["sim"])
    assert craft.responder("aplica meu estilo no vídeo").texto.startswith("Qual vídeo?")
    recente["sim"] = True
    assert craft.responder("aplica meu estilo no vídeo") is None


# ------------------------------------------------------------------ painel e app
def test_painel_entrega_o_mp4_em_pedacos_so_das_criacoes(tmp_path, monkeypatch):
    import painel_web

    monkeypatch.setattr(painel_web, "PASTA_DOCUMENTOS_GERADOS", str(tmp_path))
    monkeypatch.setattr(painel_web, "PEDACO_DO_VIDEO", 10)
    pasta = tmp_path / "2026-10-27_criacoes"
    pasta.mkdir()
    (pasta / "v.mp4").write_bytes(b"0123456789abcdefghijXYZ")
    partes = [painel_web.ApiPainel.criacao_video_parte(None, str(pasta / "v.mp4"), i) for i in range(3)]
    assert partes[0]["partes"] == 3 and b"".join(base64.b64decode(p["dados"]) for p in partes) == b"0123456789abcdefghijXYZ"
    (tmp_path / "fora.mp4").write_bytes(b"x")
    assert painel_web.ApiPainel.criacao_video_parte(None, str(tmp_path / "fora.mp4"))["partes"] == 0


def test_rotas_da_tela_estudio(tmp_path):
    from fastapi.testclient import TestClient

    import app_celular
    from app_celular import Aparelhos, CanalApp, criar_servidor

    pedidos = []
    aparelhos = Aparelhos(str(tmp_path / "aparelhos.json"))
    app = criar_servidor(CanalApp(), aparelhos, estilo_de_video=estilo_video,
                         video_novo=lambda assunto, opcoes: pedidos.append((assunto, opcoes)) or f"Fazendo um vídeo sobre {assunto}",
                         refazer_video=lambda: "Montando o vídeo de novo")
    cliente = TestClient(app)
    codigo = aparelhos.novo_codigo()
    chave = {app_celular.CABECALHO_DA_CHAVE: cliente.post("/api/parear", json={"codigo": codigo, "nome": "iPhone"}).json()["chave"]}
    assert cliente.get("/api/video-estilo").status_code == 401
    dados = cliente.get("/api/video-estilo", headers=chave).json()
    assert dados["estilo"]["formato"] == "16x9" and "letras" in dados
    assert cliente.post("/api/video-estilo", headers=chave, json={"ritmo": "rapido"}).json()["dados"]["estilo"]["ritmo"] == "rapido"
    imagem = io.BytesIO()
    Image.new("RGB", (20, 20), "green").save(imagem, "PNG")
    novo = cliente.post("/api/video-estilo/exemplo", headers={**chave, "X-Nome": "print.png"}, content=imagem.getvalue()).json()
    exemplo = novo["dados"]["exemplos"][0]["id"]
    assert cliente.get(f"/api/video-estilo/exemplo/{exemplo}", headers=chave).json()["src"].startswith("data:image/jpeg")
    resposta = cliente.post("/api/video-estilo/novo", headers=chave, json={"assunto": "café", "opcoes": {"formato": "9x16"}}).json()
    assert resposta["ok"] and pedidos == [("café", {"formato": "9x16"})]
    assert cliente.post("/api/video-estilo/refazer", headers=chave).json()["ok"]
