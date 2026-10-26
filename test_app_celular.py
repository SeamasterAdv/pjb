"""App do celular: pareamento, segurança das rotas, pedidos, voz, arquivos e a ligação com o laço do main.py.

Sem rede, sem Whisper, sem voz de verdade: o servidor roda no TestClient do FastAPI e o "PC" é o CanalApp.
"""
import io
import json
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app_celular
from app_celular import Aparelhos, CanalApp, criar_servidor, responder_pedido_do_app

CHAVE = app_celular.CABECALHO_DA_CHAVE


class Relogio:
    def __init__(self, agora=1_000_000.0):
        self.agora = agora

    def __call__(self):
        return self.agora


@pytest.fixture
def pastas(tmp_path, monkeypatch):
    gerados, recebidos = tmp_path / "gerados", tmp_path / "recebidos"
    gerados.mkdir()
    recebidos.mkdir()
    monkeypatch.setattr(app_celular, "PASTAS_QUE_SAEM", (str(gerados), str(recebidos)))
    return gerados, recebidos


@pytest.fixture
def ambiente(tmp_path, pastas):
    """Servidor com canal, aparelhos e funções falsas; devolve (cliente, canal, aparelhos, chamadas)."""
    canal, aparelhos = CanalApp(), Aparelhos(str(tmp_path / "aparelhos.json"))
    chamadas = {"transcrever": [], "falas": []}

    def transcrever(amostras):
        chamadas["transcrever"].append(amostras)
        return "que horas são", 0.9

    def gerar_fala(texto):
        chamadas["falas"].append(texto)
        caminho = tmp_path / f"fala{len(chamadas['falas'])}.mp3"
        caminho.write_bytes(b"ID3audio")
        return str(caminho)

    app = criar_servidor(canal, aparelhos, dados=lambda: {"hoje": {"itens": []}, "tarefas": []}, transcrever=transcrever,
                         gerar_fala=gerar_fala, limpar=str.upper, pasta_recebidos=str(pastas[1]), limite_mb=1,
                         agenda_semana=lambda d: {"titulo": f"semana {d}", "dias": [], "tarefas": []})
    return TestClient(app), canal, aparelhos, chamadas


def parear(cliente, aparelhos) -> dict:
    codigo = aparelhos.novo_codigo()
    resposta = cliente.post("/api/parear", json={"codigo": f"{codigo[:3]} {codigo[3:]}", "nome": "iPhone"})
    assert resposta.status_code == 200
    return {CHAVE: resposta.json()["chave"]}


# ------------------------------------------------------------------ pareamento e chaves
def test_codigo_vale_uma_vez_e_so_o_hash_fica_no_pc(tmp_path):
    relogio = Relogio()
    aparelhos = Aparelhos(str(tmp_path / "a.json"), relogio=relogio)
    codigo = aparelhos.novo_codigo()
    assert len(codigo) == 6 and codigo.isdigit()
    chave = aparelhos.parear(codigo, "iPhone")
    assert chave and len(chave) >= 40
    assert aparelhos.parear(codigo, "outro") is None  # já foi usado
    salvo = open(tmp_path / "a.json", encoding="utf-8").read()
    assert chave not in salvo and "hash" in salvo
    assert aparelhos.autorizado(chave)["nome"] == "iPhone"
    assert aparelhos.autorizado("chave-qualquer") is None and aparelhos.autorizado("") is None


def test_codigo_vence_e_tentativas_erradas_o_derrubam(tmp_path):
    relogio = Relogio()
    aparelhos = Aparelhos(str(tmp_path / "a.json"), relogio=relogio)
    codigo = aparelhos.novo_codigo()
    relogio.agora += app_celular.VALIDADE_DO_CODIGO + 1
    assert aparelhos.parear(codigo) is None  # venceu

    codigo = aparelhos.novo_codigo()
    errado = "000000" if codigo != "000000" else "111111"
    for _ in range(app_celular.TENTATIVAS_DO_CODIGO):
        assert aparelhos.parear(errado) is None
    assert aparelhos.parear(codigo) is None  # depois de 5 erros nem o certo vale (adivinhar não compensa)


def test_rotas_exigem_a_chave_e_desconectar_tira_o_acesso(ambiente):
    cliente, canal, aparelhos, _ = ambiente
    for metodo, rota in [("get", "/api/painel"), ("get", "/api/mensagens"), ("post", "/api/pedido"), ("post", "/api/voz"),
                         ("post", "/api/arquivo"), ("get", "/api/agenda"), ("get", "/api/fala/1")]:
        assert getattr(cliente, metodo)(rota).status_code == 401, rota
    assert cliente.get("/api/painel", headers={CHAVE: "inventada"}).status_code == 401
    assert cliente.post("/api/parear", json={"codigo": "123456"}).status_code == 403

    cabecalho = parear(cliente, aparelhos)
    assert cliente.get("/api/painel", headers=cabecalho).json()["aparelho"] == "iPhone"
    assert cliente.get("/api/agenda?deslocamento=1", headers=cabecalho).json()["titulo"] == "semana 1"
    assert cliente.post("/api/esquecer", headers=cabecalho).status_code == 200
    assert cliente.get("/api/painel", headers=cabecalho).status_code == 401


def test_pagina_sai_com_cabecalhos_de_seguranca(ambiente):
    cliente, *_ = ambiente
    pagina = cliente.get("/")
    assert pagina.status_code == 200 and "Beatriz" in pagina.text
    assert "default-src 'self'" in pagina.headers["content-security-policy"]
    assert pagina.headers["x-frame-options"] == "DENY"
    assert cliente.get("/api/painel").headers["cache-control"] == "no-store"
    assert cliente.get("/manifest.webmanifest").status_code == 200


def test_visual_em_rede_sai_so_com_os_arquivos_dele(ambiente):
    # A esfera e a montanha vêm do interface_web: só o módulo, o Three.js e os ajustes, como JavaScript de verdade
    # (o módulo não carrega com outro tipo, por causa do nosniff).
    cliente, *_ = ambiente
    modulo = cliente.get("/visual/visual_rede.js")
    assert modulo.status_code == 200 and modulo.headers["content-type"].startswith("text/javascript")
    assert 'from "./vendor/three/build/three.module.min.js"' in modulo.text
    assert cliente.get("/visual/vendor/three/build/three.module.min.js").headers["content-type"].startswith("text/javascript")
    assert cliente.get("/visual/visual_ajustes.json").headers["content-type"].startswith("application/json")
    for fora in ("/visual/laboratorio_visual.js", "/visual/modelos/mascote.glb", "/visual/../app_celular.py", "/visual/%2e%2e/main.py"):
        assert cliente.get(fora).status_code == 404, fora
    pagina = cliente.get("/").text
    assert 'id="esfera-inicio"' in pagina and 'src="visual_app.js"' in pagina and 'id="montanha-gravando"' in pagina


# ------------------------------------------------------------------ pedidos e mensagens
def test_pedido_vai_para_o_laco_e_a_resposta_volta(ambiente):
    cliente, canal, aparelhos, _ = ambiente
    cabecalho = parear(cliente, aparelhos)
    assert cliente.post("/api/pedido", headers=cabecalho, json={"texto": "  que horas são?  "}).status_code == 200
    assert cliente.post("/api/pedido", headers=cabecalho, json={"texto": "   "}).status_code == 400
    assert canal.receber() == "que horas são?" and canal.receber() == ""
    canal.enviar("São 14h.")
    mensagens = cliente.get("/api/mensagens?depois=0", headers=cabecalho).json()["mensagens"]
    assert [(m["tipo"], m["texto"]) for m in mensagens] == [("voce", "que horas são?"), ("resposta", "São 14h.")]
    ultimo = mensagens[-1]["id"]
    assert cliente.get(f"/api/mensagens?depois={ultimo}&espera=0.1", headers=cabecalho).json()["mensagens"] == []
    assert canal.status()["status"] == "conectado"  # o app acabou de buscar mensagens


def test_arquivos_so_saem_pelo_id_e_das_pastas_permitidas(ambiente, pastas, tmp_path):
    cliente, canal, aparelhos, _ = ambiente
    cabecalho = parear(cliente, aparelhos)
    pdf = pastas[0] / "resumo.pdf"
    pdf.write_bytes(b"%PDF-1.4 teste")
    segredo = tmp_path / "segredo.txt"
    segredo.write_text("não sai")
    canal.enviar("Pronto!", [str(pdf), str(segredo)])
    anexos = cliente.get("/api/mensagens", headers=cabecalho).json()["mensagens"][-1]["arquivos"]
    assert [a["nome"] for a in anexos] == ["resumo.pdf"]  # o de fora das pastas nem é oferecido
    baixado = cliente.get(f"/api/arquivo/{anexos[0]['id']}", headers=cabecalho)
    assert baixado.status_code == 200 and baixado.content == b"%PDF-1.4 teste"
    assert cliente.get("/api/arquivo/..%2Fsegredo.txt", headers=cabecalho).status_code == 404
    assert cliente.get(f"/api/arquivo/{anexos[0]['id']}").status_code == 401


def test_arquivo_do_celular_chega_como_o_do_whatsapp(ambiente, pastas):
    cliente, canal, aparelhos, _ = ambiente
    cabecalho = parear(cliente, aparelhos)
    enviado = cliente.post("/api/arquivo", headers={**cabecalho, "X-Nome": "contrato%20aluguel.pdf", "X-Legenda": "vira%20fonte"},
                           content=b"%PDF conteudo")
    assert enviado.status_code == 200
    chegou = canal.receber()
    assert chegou["tipo"] == "arquivo" and chegou["nome"] == "contrato aluguel.pdf" and chegou["legenda"] == "vira fonte"
    assert chegou["origem"] == "celular" and os.path.dirname(os.path.dirname(chegou["caminho"])) == str(pastas[1])
    assert open(chegou["caminho"], "rb").read() == b"%PDF conteudo"

    programa = cliente.post("/api/arquivo", headers={**cabecalho, "X-Nome": "atualizacao.exe"}, content=b"MZ")
    assert programa.status_code == 415 and "programas" in programa.json()["erro"]
    grande = cliente.post("/api/arquivo", headers={**cabecalho, "X-Nome": "video.mov"}, content=b"0" * (1024 * 1024 + 1))
    assert grande.status_code == 413
    assert canal.receber() == ""  # nada disso chegou ao laço


def _audio_m4a(segundos=1.0, taxa=44100) -> bytes:
    """Um tom gravado como o iPhone grava (AAC em mp4), feito na hora com o PyAV."""
    import av

    saida = io.BytesIO()
    with av.open(saida, "w", format="mp4") as recipiente:
        trilha = recipiente.add_stream("aac", rate=taxa)
        trilha.layout = "mono"
        amostras = (np.sin(2 * np.pi * 440 * np.arange(int(segundos * taxa)) / taxa) * 0.3).astype(np.float32)
        for inicio in range(0, len(amostras), 1024):
            quadro = av.AudioFrame.from_ndarray(amostras[None, inicio:inicio + 1024], format="flt", layout="mono")
            quadro.sample_rate = taxa
            for pacote in trilha.encode(quadro):
                recipiente.mux(pacote)
        for pacote in trilha.encode(None):
            recipiente.mux(pacote)
    return saida.getvalue()


def test_voz_do_celular_e_transcrita_e_vira_pedido(ambiente):
    cliente, canal, aparelhos, chamadas = ambiente
    cabecalho = parear(cliente, aparelhos)
    resposta = cliente.post("/api/voz", headers={**cabecalho, "Content-Type": "audio/mp4"}, content=_audio_m4a())
    assert resposta.status_code == 200 and resposta.json()["texto"] == "que horas são"
    amostras = chamadas["transcrever"][0]
    assert amostras.dtype == np.float32 and 15000 < len(amostras) < 17500  # ~1 s em 16 kHz
    assert canal.receber() == "que horas são"
    eco = cliente.get("/api/mensagens", headers=cabecalho).json()["mensagens"][-1]
    assert eco["tipo"] == "voce" and eco["por_voz"] is True
    assert cliente.post("/api/voz", headers=cabecalho, content=b"isso nao e audio").status_code == 400


def test_resposta_falada_no_celular_usa_a_voz_dela(ambiente):
    cliente, canal, aparelhos, chamadas = ambiente
    cabecalho = parear(cliente, aparelhos)
    cliente.post("/api/pedido", headers=cabecalho, json={"texto": "oi"})
    canal.enviar("Oi! Tudo bem?")
    pedido, resposta = cliente.get("/api/mensagens", headers=cabecalho).json()["mensagens"]
    audio = cliente.get(f"/api/fala/{resposta['id']}", headers=cabecalho)
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg"
    cliente.get(f"/api/fala/{resposta['id']}", headers=cabecalho)
    assert chamadas["falas"] == ["OI! TUDO BEM?"]  # passou pela limpeza e foi gerada uma vez só
    assert cliente.get(f"/api/fala/{pedido['id']}", headers=cabecalho).status_code == 404  # a sua fala não é falada


# ------------------------------------------------------------------ pedidos falados
def test_conecta_meu_celular_da_o_codigo_e_explica(tmp_path, monkeypatch):
    aparelhos = Aparelhos(str(tmp_path / "a.json"))
    monkeypatch.setattr(app_celular, "situacao_tailscale",
                        lambda: {"instalado": True, "endereco": "https://pc.exemplo.ts.net", "servindo": False})
    resposta = responder_pedido_do_app("Beatriz, conecta meu iPhone", aparelhos)
    assert "https://pc.exemplo.ts.net" in resposta and "tailscale serve --bg" in resposta
    codigo = "".join(c for c in resposta.split("código")[1] if c.isdigit())[:6]
    assert aparelhos.parear(codigo)

    monkeypatch.setattr(app_celular, "situacao_tailscale", lambda: {"instalado": False, "endereco": "", "servindo": False})
    assert "instale o Tailscale" in responder_pedido_do_app("instala o app no celular", aparelhos)
    assert "Pareado: Celular" in responder_pedido_do_app("o celular está conectado?", aparelhos)
    assert "desconectei" in responder_pedido_do_app("desconecta o celular", aparelhos) and not aparelhos.lista()
    for outro in ["liga pro celular da Maria", "manda o PDF pro meu celular", "abre o app do banco", "para de usar o celular"]:
        assert responder_pedido_do_app(outro, aparelhos) is None, outro


# ------------------------------------------------------------------ ligação com o main.py
@pytest.fixture
def principal(monkeypatch, pastas):
    import main
    import tools

    canal = CanalApp()
    monkeypatch.setattr(main, "CANAL_APP", canal)
    monkeypatch.setattr(main, "CANAL_WHATSAPP", None)
    monkeypatch.setitem(main.CANAL, "atual", "voz")
    monkeypatch.setattr(main, "avisar_painel", lambda *a, **k: None)
    monkeypatch.setattr(main, "registrar_conversa", lambda *a, **k: None)
    monkeypatch.setattr(main, "falar_async", lambda *a, **k: pytest.fail("não deveria falar em casa"))
    yield main, canal
    tools.CONFIRMAR_NO_TECLADO = True


def test_pedido_do_app_responde_por_escrito_no_app(principal):
    main, canal = principal
    canal.pedido("que horas são?")
    assert main.obter_comando_usuario() == "que horas são?"
    assert main.CANAL["atual"] == "app" and main.FALANTE["atual"] is None  # o aparelho pareado é o dono
    main.responder_rapido("São 14h.")
    main.falar_e_registrar("que horas são?", "São 14h, de novo.")
    assert [m["texto"] for m in canal.mensagens()][-2:] == ["São 14h.", "São 14h, de novo."]
    assert main.pedir_confirmacao("mandar e-mail") is False  # ninguém no teclado


def test_tarefa_pedida_pelo_app_volta_com_os_arquivos(principal, pastas):
    main, canal = principal
    pdf = pastas[0] / "reforma.pdf"
    pdf.write_bytes(b"%PDF")

    class Tarefa:
        mensagem_final = "Pronto! O PDF sobre a reforma tributária está feito."
        arquivos = [str(pdf)]
        plano = ["pesquisa", "documento"]
        criada_em = concluida_em = 0.0
        status = "concluída"
        contexto = {}
        tipo = "documento"

    main.anunciar_tarefa_concluida("faz um PDF sobre a reforma", Tarefa(), canal="app")
    ultima = canal.mensagens()[-1]
    assert ultima["texto"].startswith("Pronto!") and [a["nome"] for a in ultima["arquivos"]] == ["reforma.pdf"]


def test_lembrete_aparece_no_app(principal, monkeypatch):
    main, canal = principal
    monkeypatch.setattr(main, "falar_async", lambda texto: None)  # o lembrete também é falado em casa
    main.avisar_lembrete("Daqui a 15 minutos: dentista.")
    assert canal.mensagens()[-1]["tipo"] == "lembrete"


def test_arquivo_do_app_passa_pelas_fontes(principal, monkeypatch, pastas):
    main, canal = principal
    recebidos = []
    monkeypatch.setattr(main.FONTES, "receber",
                        lambda caminho, nome, legenda, origem, canal_: recebidos.append((nome, origem, canal_)) or "Recebi.")
    arquivo = pastas[1] / "foto.jpg"
    arquivo.write_bytes(b"jpg")
    canal.arquivo_recebido(str(arquivo), "foto.jpg")
    assert main.proximo_comando_remoto() == ""
    assert recebidos == [("foto.jpg", "celular", "app")]
    assert canal.mensagens()[-1]["texto"] == "Recebi."


def test_indice_da_etapa_das_missoes():
    import main

    plano = ["biblioteca", "estudos", "critico", "relator"]
    assert main.indice_da_etapa(plano, "consultando sua biblioteca") == 0
    assert main.indice_da_etapa(plano, "escrevendo as fichas (3 de 8 prontas)") == 1
    assert main.indice_da_etapa(plano, "corrigindo após a crítica") == 2
    assert main.indice_da_etapa(plano, "escrevendo o relatório") == 3


# ------------------------------------------------------------------ cartões e pôsteres
def test_cartao_de_filmes_so_repassa_posteres_permitidos(tmp_path, pastas):
    canal, aparelhos = CanalApp(), Aparelhos(str(tmp_path / "a.json"))
    buscas = []

    def buscar(endereco):
        buscas.append(endereco)
        return "image/jpeg", b"JPG"

    cliente = TestClient(criar_servidor(canal, aparelhos, buscar_imagem=buscar))
    cabecalho = parear(cliente, aparelhos)
    canal.cartao("filmes", {"cidade": "Brasília", "filmes": [
        {"titulo": "Verity", "poster": "https://ingresso-a.akamaihd.net/img/cinema/cartaz/1.jpg"},
        {"titulo": "Golpe", "poster": "https://site-qualquer.com/x.jpg"},
        {"titulo": "Inseguro", "poster": "http://ingresso-a.akamaihd.net/2.jpg"},
    ]})
    cartao = cliente.get("/api/mensagens", headers=cabecalho).json()["mensagens"][-1]
    assert cartao["tipo"] == "cartao" and cartao["cartao"] == "filmes"
    posteres = [f["poster"] for f in cartao["dados"]["filmes"]]
    assert posteres[0] and posteres[1:] == ["", ""]  # outro site ou sem https: nem vira imagem
    assert "akamaihd" not in json.dumps(cartao)  # o celular nunca vê o endereço de fora
    for _ in range(2):
        imagem = cliente.get(f"/api/imagem/{posteres[0]}", headers=cabecalho)
        assert imagem.status_code == 200 and imagem.content == b"JPG" and imagem.headers["content-type"] == "image/jpeg"
    assert buscas == ["https://ingresso-a.akamaihd.net/img/cinema/cartaz/1.jpg"]  # buscou uma vez só
    assert cliente.get("/api/imagem/inventada", headers=cabecalho).status_code == 404
    assert cliente.get(f"/api/imagem/{posteres[0]}").status_code == 401


def test_cartao_de_resumo_leva_o_word_como_anexo(pastas):
    canal = CanalApp()
    word = pastas[0] / "resumo.docx"
    word.write_bytes(b"docx")
    canal.cartao("resumo", {"tema": "reforma", "fala": "Curto.", "secoes": {"o que e": "X"}, "arquivo": str(word)})
    cartao = canal.mensagens()[-1]
    assert "arquivo" not in cartao["dados"] and [a["nome"] for a in cartao["arquivos"]] == ["resumo.docx"]


def test_clima_em_numeros_usa_o_mesmo_cache_da_previsao(monkeypatch):
    import tools

    chamadas = []

    class Resposta:
        def __init__(self, dados):
            self.dados = dados

        def raise_for_status(self):
            pass

        def json(self):
            return self.dados

    def get(url, params=None, timeout=None):
        chamadas.append(url)
        if "geocoding" in url:
            return Resposta({"results": [{"name": "Plano Piloto", "latitude": -15.8, "longitude": -47.9, "country_code": "BR"}]})
        return Resposta({"current": {"temperature_2m": 27.6, "apparent_temperature": 29.2, "relative_humidity_2m": 40,
                                     "weather_code": 2, "wind_speed_10m": 8.7},
                         "daily": {"time": ["2026-10-20", "2026-10-21", "2026-10-22"], "weather_code": [2, 61, 0],
                                   "temperature_2m_max": [29.4, 25, 30], "temperature_2m_min": [18.2, 17, 16],
                                   "precipitation_probability_max": [20, 80, 0]}})

    monkeypatch.setattr(tools, "_CACHE_CLIMA", {})
    monkeypatch.setattr(tools.requests, "get", get)
    clima = tools.clima_em_numeros("Plano Piloto")
    assert (clima["temperatura"], clima["sensacao"], clima["descricao"]) == (28, 29, "parcialmente nublado")
    assert [(d["maxima"], d["minima"], d["chuva"]) for d in clima["dias"]] == [(29, 18, 20), (25, 17, 80), (30, 16, 0)]
    assert "28°C" in tools.obter_previsao_tempo("Plano Piloto")
    assert len(chamadas) == 2  # a frase falada reaproveitou a consulta

    monkeypatch.setattr(tools, "_CACHE_CLIMA", {})
    monkeypatch.setattr(tools.requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("sem internet")))
    assert tools.clima_em_numeros("Plano Piloto") is None


def test_pedido_do_app_ganha_cartao_de_clima_e_de_filmes(principal, monkeypatch):
    main, canal = principal
    monkeypatch.setitem(main.CANAL, "atual", "app")
    monkeypatch.setattr(main, "obter_previsao_tempo", lambda cidade="", dia="hoje": "Agora: 28°C.")
    monkeypatch.setattr(main.tools, "clima_em_numeros", lambda cidade="": {"lugar": "Plano Piloto", "temperatura": 28})
    executou, resposta = main.tentar_executar_ferramenta("como está o tempo hoje?")
    assert executou and resposta == "Agora: 28°C."
    assert canal.mensagens()[-1]["cartao"] == "clima" and canal.mensagens()[-1]["dados"]["temperatura"] == 28


def test_cartoes_do_painel_vao_para_o_app_so_quando_o_pedido_veio_de_la(monkeypatch, pastas):
    import main

    canal = CanalApp()
    monkeypatch.setattr(main, "CANAL_APP", canal)
    monkeypatch.setattr(main, "PAINEL_VOZ", None)
    monkeypatch.setitem(main.CANAL, "atual", "app")
    main.avisar_painel("filmes", "Há 3 filmes.", cidade="Brasília", filmes=[{"titulo": "Verity", "poster": ""}])
    assert canal.mensagens()[-1]["cartao"] == "filmes"
    monkeypatch.setitem(main.CANAL, "atual", "voz")  # pela voz o cartão fica só no painel do PC
    main.avisar_painel("filmes", "Há 3 filmes.", cidade="Brasília", filmes=[])
    assert len(canal.mensagens()) == 1


@pytest.mark.parametrize("frase, esperado", [
    ("como está o tempo hoje?", ("", "hoje")),  # o pedido do bloco Clima do app (antes respondia a data)
    ("como vai estar o tempo amanhã?", ("", "amanha")),
    ("vai chover amanhã em são paulo?", ("são paulo", "amanha")),
    ("qual a previsão do tempo?", ("", "hoje")),
    ("quanto tempo falta para o natal?", None),
    ("não tenho tempo hoje", None),
])
def test_pedidos_de_tempo_viram_clima(frase, esperado):
    import main

    assert main.pedido_de_clima(frase) == esperado


def test_clima_da_tela_inicial_nao_trava_a_resposta(monkeypatch):
    import threading

    import main

    liberar = threading.Event()
    monkeypatch.setattr(main, "CLIMA_DO_APP", {"valor": None, "em": 0.0, "buscando": False})
    monkeypatch.setattr(main.tools, "clima_em_numeros", lambda: liberar.wait(5) and {"temperatura": 25})
    assert main.clima_do_app() is None  # devolve na hora, a busca segue em segundo plano
    assert main.clima_do_app() is None and main.CLIMA_DO_APP["buscando"]  # e não abre outra busca
    liberar.set()
    for _ in range(100):
        if main.CLIMA_DO_APP["valor"]:
            break
        threading.Event().wait(0.02)
    assert main.clima_do_app() == {"temperatura": 25}


# ------------------------------------------------------------------ biblioteca de fontes e diagnóstico
@pytest.fixture
def com_biblioteca(tmp_path, pastas):
    """Servidor com uma biblioteca de verdade em pastas temporárias e uma lixeira falsa."""
    from urllib.parse import quote

    from biblioteca_fontes import BibliotecaDeFontes

    documentos, obsidian = tmp_path / "documentos_assistente", tmp_path / "obsidian"
    for pasta in (documentos / "direito", obsidian / "estudos", obsidian / "geral"):
        pasta.mkdir(parents=True)
    (documentos / "direito" / "lei.pdf").write_bytes(b"%PDF lei")
    (obsidian / "geral" / "memoria_usuario.md").write_text("pessoal")
    lixo = []
    biblioteca = BibliotecaDeFontes(
        raizes={"documentos": ("Documentos", str(documentos)), "obsidian": ("Obsidian", str(obsidian))},
        pessoais=[str(obsidian / "geral")], ao_mudar=lambda: None, estado_do_indice=lambda: {},
        lixeira=lambda caminho: (lixo.append(os.path.basename(caminho)), os.remove(caminho)),
        arquivos=str(tmp_path / "arquivados"), recebidos=None, limite_mb=1)
    canal, aparelhos = CanalApp(), Aparelhos(str(tmp_path / "aparelhos.json"))
    app = criar_servidor(canal, aparelhos, dados=lambda: {}, biblioteca=biblioteca,
                         recursos=lambda: {"cpu": 30, "ram": 60, "disco": 70})
    cliente = TestClient(app)
    return cliente, parear(cliente, aparelhos), lixo, quote


def test_biblioteca_e_recursos_exigem_a_chave(com_biblioteca):
    cliente, *_ = com_biblioteca
    for metodo, rota in [("get", "/api/biblioteca"), ("post", "/api/biblioteca/mover"), ("post", "/api/biblioteca/adicionar"),
                         ("get", "/api/biblioteca-arquivo?id=documentos/direito/lei.pdf"), ("get", "/api/recursos")]:
        assert getattr(cliente, metodo)(rota).status_code == 401, rota


def test_biblioteca_pelo_app_lista_move_baixa_e_apaga(com_biblioteca):
    cliente, cabecalho, lixo, _ = com_biblioteca
    lista = cliente.get("/api/biblioteca", headers=cabecalho).json()
    assert [p["id"] for p in lista["pastas"]] == ["documentos", "documentos/direito", "obsidian", "obsidian/estudos", "arquivos"]
    assert lista["total"] == 1  # a memória do usuário (geral) não aparece

    movido = cliente.post("/api/biblioteca/mover", headers=cabecalho,
                          json={"arquivo": "documentos/direito/lei.pdf", "pasta": "obsidian/estudos"}).json()
    assert movido["ok"] and "Obsidian › estudos" in movido["mensagem"]
    baixado = cliente.get("/api/biblioteca-arquivo?id=obsidian/estudos/lei.pdf", headers=cabecalho)
    assert baixado.status_code == 200 and baixado.content == b"%PDF lei"
    assert cliente.get("/api/biblioteca-arquivo?id=obsidian/geral/memoria_usuario.md", headers=cabecalho).status_code == 404

    errado = cliente.post("/api/biblioteca/renomear", headers=cabecalho, json={"arquivo": "obsidian/../x", "nome": "y"}).json()
    assert errado["ok"] is False and errado["mensagem"]
    assert cliente.post("/api/biblioteca/formatar", headers=cabecalho, json={}).status_code == 404

    guardado = cliente.post("/api/biblioteca/so_arquivo", headers=cabecalho, json={"arquivo": "obsidian/estudos/lei.pdf"}).json()
    assert guardado["ok"] and "Só arquivos › estudos" in guardado["mensagem"]
    lote = cliente.post("/api/biblioteca/lote", headers=cabecalho,
                        json={"acao": "virar_fonte", "arquivos": ["arquivos/estudos/lei.pdf"]}).json()
    assert lote == {"ok": True, "mensagem": "1 arquivo virou fonte: a Beatriz vai ler."}
    assert cliente.post("/api/biblioteca/lote", headers=cabecalho, json={"acao": "apagar", "arquivos": "x"}).status_code == 400
    apagado = cliente.post("/api/biblioteca/apagar", headers=cabecalho, json={"arquivo": "obsidian/estudos/lei.pdf"}).json()
    assert apagado["ok"] and lixo == ["lei.pdf"]


def test_fonte_mandada_pelo_celular_entra_na_pasta_escolhida(com_biblioteca, tmp_path):
    cliente, cabecalho, _, quote = com_biblioteca
    resposta = cliente.post("/api/biblioteca/adicionar", content=b"anotacoes",
                            headers={**cabecalho, "X-Nome": quote("Aula 2 - posse.txt"), "X-Pasta": quote("obsidian/estudos")}).json()
    assert resposta["ok"]
    assert (tmp_path / "obsidian" / "estudos" / "Aula 2 - posse.txt").read_bytes() == b"anotacoes"
    programa = cliente.post("/api/biblioteca/adicionar", content=b"MZ",
                            headers={**cabecalho, "X-Nome": "instalar.exe", "X-Pasta": "obsidian/estudos"}).json()
    assert programa["ok"] is False and not (tmp_path / "obsidian" / "estudos" / "instalar.exe").exists()
    pessoal = cliente.post("/api/biblioteca/adicionar", content=b"x",
                           headers={**cabecalho, "X-Nome": "nota.md", "X-Pasta": "obsidian/geral"}).json()
    assert pessoal["ok"] is False and not (tmp_path / "obsidian" / "geral" / "nota.md").exists()


def test_recursos_do_pc_para_o_grafico(com_biblioteca, ambiente):
    cliente, cabecalho, *_ = com_biblioteca
    assert cliente.get("/api/recursos", headers=cabecalho).json() == {"cpu": 30, "ram": 60, "disco": 70}
    sem, _, aparelhos, _ = ambiente  # servidor sem biblioteca: a rota avisa em vez de quebrar
    assert sem.get("/api/biblioteca", headers=parear(sem, aparelhos)).status_code == 503
    recursos = app_celular.recursos_do_pc()
    assert set(recursos) >= {"disco"} and all(0 <= v <= 100 for v in recursos.values() if isinstance(v, (int, float)))


# ------------------------------------------------------------------ controle remoto (TV e ar)
class ControleFalso:
    def __init__(self):
        self.comandos = []

    def painel_do_app(self):
        return {"configurado": True, "tv": {"disponivel": True, "botoes": ["ligar", "desligar"]}, "ar": {"disponivel": False}}

    def comando_do_app(self, dados):
        self.comandos.append(dados)
        return {"ok": True, "texto": "Pronto, liguei a TV.", "painel": self.painel_do_app()}


def test_controle_pelo_app_exige_a_chave_e_vai_direto_ao_agente(tmp_path):
    canal, aparelhos, controle = CanalApp(), Aparelhos(str(tmp_path / "a.json")), ControleFalso()
    cliente = TestClient(criar_servidor(canal, aparelhos, controle=controle))
    assert cliente.get("/api/controle").status_code == 401
    assert cliente.post("/api/controle", json={"aparelho": "tv", "acao": "ligar"}).status_code == 401
    chave = parear(cliente, aparelhos)
    assert cliente.get("/api/controle", headers=chave).json()["tv"]["botoes"] == ["ligar", "desligar"]
    resposta = cliente.post("/api/controle", headers=chave, json={"aparelho": "tv", "acao": "ligar"})
    assert resposta.json()["texto"] == "Pronto, liguei a TV."
    assert controle.comandos == [{"aparelho": "tv", "acao": "ligar"}]
    assert canal.receber() == ""  # não vira pedido da conversa
    assert cliente.post("/api/controle", headers=chave, content=b"[1, 2]").status_code == 400
    assert cliente.post("/api/controle", headers=chave, content=b"isso nao e json").status_code == 400


def test_controle_pelo_app_sem_o_agente(ambiente):
    cliente, _, aparelhos, _ = ambiente
    chave = parear(cliente, aparelhos)
    assert cliente.get("/api/controle", headers=chave).status_code == 503


def test_main_liga_o_controle_ao_app():
    codigo = open("main.py", encoding="utf-8").read()
    inicio = codigo.index("def iniciar_app_celular")
    assert "controle=CONTROLE" in codigo[inicio:codigo.index("\ndef ", inicio + 10)]


def test_tela_do_controle_na_pagina():
    pagina = open(os.path.join(app_celular.PASTA_APP, "index.html"), encoding="utf-8").read()
    for trecho in ('id="tela-controle"', 'data-acao="controle"', 'id="w-casa"', 'data-tv="volume_mais"', 'id="ar-ligar"'):
        assert trecho in pagina, trecho
    assert 'data-acao="arquivos" role="listitem"' not in pagina  # "Mandar ao PC" ficou só no clipe da barra de baixo
