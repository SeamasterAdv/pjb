"""As escolhas da janela [ estudos ]: gravar e conferir, as cores, o que vai para o prompt, as fotos de exemplo e o guia, e
o efeito nas fichas e nas tarefas (arquivos temporários; nada da nuvem de verdade)."""
import json
import os
import threading
import unicodedata

import pymupdf
import pytest

import painel_web as pw
import preferencias_estudo as pe
from agentes import Orquestrador, ficha_estudo, planejar
from agentes.base import Tarefa
from agentes.especialistas import AgenteEstudos
from llm_nuvem import ClienteClaudeCode
from test_agentes import LLMFalso
from test_ficha_estudo import IAEtapas, topico

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00"
       b"\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82")


def test_sem_arquivo_vale_o_padrao_e_o_prompt_fica_igual():
    prefs = pe.atual()
    assert prefs["tamanho"] == "normal" and prefs["formato"] == "pdf" and prefs["flashcards"] is None
    assert pe.bloco_do_prompt(prefs) == "" and pe.descricao_curta(prefs) == ""


def test_salvar_confere_e_rele():
    pe.salvar({"tamanho": "enxuto", "estilo": "esquematico", "flashcards": 0, "questoes": 10, "banca": "FGV",
               "partes": {"jurisprudencia": False}, "instrucoes": "sempre um esquema no começo"})
    prefs = pe.atual()
    assert prefs["tamanho"] == "enxuto" and prefs["flashcards"] == 0 and prefs["questoes"] == 10
    assert prefs["partes"]["jurisprudencia"] is False and prefs["partes"]["lei_seca"] is True
    bloco = pe.bloco_do_prompt(prefs)
    assert "ENXUTO" in bloco and "ESQUEMÁTICO" in bloco and "banca: FGV" in bloco
    assert "Deixe vazios os campos jurisprudencia" in bloco and "sempre um esquema" in bloco
    assert pe.descricao_curta(prefs) == "enxuto, esquemático"
    with pytest.raises(pe.ErroDasPreferencias):
        pe.salvar({"tamanho": "gigante"})
    with pytest.raises(pe.ErroDasPreferencias):
        pe.salvar({"flashcards": 7})  # fora da lista
    assert pe.atual()["tamanho"] == "enxuto"  # nada mudou
    pe.restaurar_padrao()
    assert pe.atual()["tamanho"] == "normal"


def test_arquivo_quebrado_vale_o_padrao():
    os.makedirs(os.path.dirname(pe.caminho()), exist_ok=True)
    with open(pe.caminho(), "w", encoding="utf-8") as saida:
        saida.write("{quebrado")
    assert pe.atual()["estilo"] == "tatico"


def test_temas_de_cores_e_minhas_cores():
    assert pe.paleta({"cores": "azul"})["marca"] == "#14305A"
    economico = pe.paleta({"cores": "cinza"})
    assert economico["regra"] == ("#444444", "#F3F3F3") and economico["destaque"] == "#D9D9D9"
    livres = pe.paleta({"cores": "livre", "cores_livres": {"marca": "#112233", "ouro": "#AA0000", "caixas": {"regra": "#0055FF"}}})
    assert livres["marca"] == "#112233" and livres["regra"] == ("#0055FF", pe.clarear("#0055FF"))
    assert livres["excecoes"] == ficha_estudo.CORES["excecoes"]
    pe.salvar({"cores": "livre", "cores_livres": {"marca": "#123456", "ouro": "zzz", "caixas": {"regra": "#00FF00", "inventada": "#000000"}}})
    salvas = pe.atual()["cores_livres"]
    assert salvas == {"marca": "#123456", "ouro": "#C99A1E", "caixas": {"regra": "#00FF00"}}  # o que não é cor válida sai


def test_fotos_de_exemplo_e_miniatura(tmp_path):
    assert pe.listar_fotos() == []
    foto = pe.adicionar_foto("resumo do caderno.png", PNG)["id"]
    assert [f["id"] for f in pe.listar_fotos()] == [foto] and pe.listar_fotos()[0]["nome"] == "resumo do caderno.png"
    assert pe.miniatura(pe.caminho_da_foto(foto)).startswith("data:image/jpeg;base64,")
    with pytest.raises(pe.ErroDasPreferencias):
        pe.adicionar_foto("virus.exe", b"MZ\x90\x00")
    with pytest.raises(pe.ErroDasPreferencias):
        pe.caminho_da_foto("..\\..\\config.py")
    pe.apagar_foto(foto)
    assert pe.listar_fotos() == []


def test_guia_pelo_claude_e_o_pc_de_reserva():
    pe.adicionar_foto("a.png", PNG)
    resposta = json.dumps({"resumo": "Esquemas curtos.", "estrutura": ["Regra logo embaixo do título"], "visual": ["Caixas"],
                           "texto": ["Frases de uma linha"], "evitar": ["Parágrafos longos"],
                           "cores": {"principal": "#1D3A8A", "destaque": "rosa"}})
    recebido = []
    guia = pe.extrair_guia(ver_com_claude=lambda fotos, prompt, sistema: recebido.append(fotos) or resposta)
    assert guia["quem"] == "Claude" and guia["fotos"] == 1 and guia["cores"] == {"principal": "#1D3A8A"}
    assert "Estrutura:\n- Regra logo embaixo do título" in guia["texto"] and len(recebido[0]) == 1
    assert pe.atual()["guia"]["texto"] == guia["texto"]
    assert "GUIA DE ESTILO DO USUÁRIO" in pe.bloco_do_prompt() and "o guia das suas fotos" in pe.descricao_curta()
    assert pe.paleta({**pe.atual(), "cores": "guia"})["marca"] == "#1D3A8A"

    def claude_falhou(*a):
        raise RuntimeError("limite")

    guia = pe.extrair_guia(ver_com_claude=claude_falhou, ver_no_pc=lambda fotos, prompt: resposta)
    assert guia["quem"] == "modelo de visão do PC"
    pe.salvar({"usar_guia": False, "guia_texto": "Meu guia à mão."})
    assert pe.atual()["guia"]["texto"] == "Meu guia à mão." and pe.bloco_do_prompt() == ""


def test_guia_sem_fotos_explica():
    with pytest.raises(pe.ErroDasPreferencias, match="pelo menos uma foto"):
        pe.extrair_guia(ver_com_claude=lambda *a: "{}")


def test_claude_ve_as_fotos_so_numa_pasta_temporaria(tmp_path):
    foto = tmp_path / "minha foto.jpg"
    foto.write_bytes(b"\xff\xd8\xff" + b"0" * 50)
    chamadas = []

    class Processo:
        returncode, stderr = 0, ""
        stdout = json.dumps({"result": "{\"resumo\": \"ok\"}"})

    def executar(comando, cwd=None, input=None, **kwargs):
        chamadas.append((comando, cwd, input, sorted(os.listdir(cwd))))
        return Processo()

    claude = ClienteClaudeCode(executavel="claude.exe", executar=executar, arquivo_uso=str(tmp_path / "uso.json"))
    assert claude.ver_imagens([str(foto)], "Descreva o estilo.", "Você é a designer.") == "{\"resumo\": \"ok\"}"
    comando, pasta, pedido, arquivos = chamadas[0]
    assert comando[comando.index("--tools") + 1] == "Read" and "Read(./**)" in comando
    assert "dontAsk" in comando and arquivos == ["exemplo_1.jpg"] and "exemplo_1.jpg" in pedido
    assert not os.path.exists(pasta)  # as cópias saem depois
    assert claude.uso_hoje() == 1


# ------------------------------------------------------------------ nas fichas e nas tarefas
def test_ficha_sem_as_partes_desligadas_e_nas_cores_do_tema(tmp_path):
    ficha = ficha_estudo.normalizar_ficha({"titulo": "Responsabilidade", "area": "direito", "como_a_banca_cobra": ["Prazos."],
                                           "topicos": [topico()], "glossario": [{"termo": "Nexo", "definicao": "Ligação."}]})
    prefs = {**pe.atual(), "cores": "azul", "partes": {**pe.atual()["partes"], "jurisprudencia": False, "lei_seca": False,
                                                       "mapa": False, "como_estudar": False}, "letra": "grande"}
    visual = pe.visual(prefs)
    caminho = ficha_estudo.gerar_pdf_ficha(ficha, str(tmp_path / "f.pdf"), visual)
    with pymupdf.open(caminho) as documento:
        texto = unicodedata.normalize("NFKC", " ".join(p.get_text() for p in documento))
        cores = {s["color"] for p in documento for b in p.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]}
    assert "REGRA" in texto and "EXCEÇÕES" in texto
    for fora in ("LEI SECA", "STF E STJ", "Mapa de incidência", "Como estudar com esta ficha"):
        assert fora not in texto, fora
    assert 0x14305A in cores  # os títulos na cor do tema azul-marinho
    # Sem as escolhas (ficha antiga), tudo como antes.
    with pymupdf.open(ficha_estudo.gerar_pdf_ficha(ficha, str(tmp_path / "g.pdf"))) as documento:
        assert "LEI SECA" in " ".join(p.get_text() for p in documento)
    from docx import Document

    docx = Document(ficha_estudo.gerar_docx_ficha(ficha, str(tmp_path / "f.docx"), visual))
    assert "Mapa de incidência" not in [p.text for p in docx.paragraphs]


def test_agente_sem_questoes_nem_flashcards_pula_as_etapas(tmp_path):
    ia = IAEtapas(topicos=2)
    tarefa = Tarefa(pedido="resumo para a prova de responsabilidade civil", plano=["estudos"], formatos=["pdf"],
                    tema="responsabilidade civil", pasta=str(tmp_path / "saida"), quantidades={"flashcards": 0, "questoes": 0})
    tarefa.contexto["preferencias"] = {**pe.atual(), "tamanho": "enxuto", "partes": {**pe.atual()["partes"], "glossario": False}}
    AgenteEstudos(llm=ia, conferir_lei=None, conferir_sumulas=None).executar(tarefa)
    etapas = [nome for nome, _ in ia.etapas]
    assert "fechamento" not in etapas and "estresse" not in etapas
    ficha = tarefa.contexto["conteudo"]
    assert ficha["flashcards"] == [] and ficha["questoes"] == [] and ficha["visual"]["partes"]["glossario"] is False
    assert len(tarefa.arquivos) == 1  # sem o arquivo do Anki


def test_prompt_das_fichas_leva_as_escolhas(tmp_path):
    ia = IAEtapas(topicos=2)
    vistos = []
    original = ia.chat
    ia.chat = lambda prompt, system_prompt=None, **k: vistos.append(system_prompt) or original(prompt, system_prompt=system_prompt, **k)
    tarefa = Tarefa(pedido="resumo para a prova de responsabilidade civil", plano=["estudos"], formatos=["pdf"],
                    tema="responsabilidade civil", pasta=str(tmp_path / "saida"))
    tarefa.contexto["preferencias"] = {**pe.atual(), "estilo": "didatico", "instrucoes": "use exemplos de futebol"}
    AgenteEstudos(llm=ia, conferir_lei=None, conferir_sumulas=None).executar(tarefa)
    assert vistos and all("DIDÁTICO" in s and "exemplos de futebol" in s for s in vistos)


def _orquestrador(tmp_path, prefs):
    return Orquestrador(str(tmp_path), llm=LLMFalso(), abrir_ao_concluir=False, preferencias=lambda: prefs)


def test_tarefa_de_estudo_recebe_as_escolhas_e_o_pedido_vale_mais(tmp_path):
    prefs = {**pe.atual(), "flashcards": 30, "questoes": 0, "formato": "ambos", "ia": "gemini"}
    orq = _orquestrador(tmp_path, prefs)
    tarefa = planejar("faz um resumo para a prova sobre usucapião")
    orq._aplicar_preferencias(tarefa)
    assert tarefa.quantidade_estudo("flashcards") == 30 and tarefa.quantidade_estudo("questoes") == 0
    assert tarefa.formatos == ["pdf", "docx"] and tarefa.ia_preferida == "gemini"
    dito = planejar("faz um resumo para a prova sobre usucapião em Word com 10 flashcards pelo Claude")
    orq._aplicar_preferencias(dito)
    assert dito.formatos == ["docx"] and dito.quantidade_estudo("flashcards") == 10 and dito.ia_preferida == "claude"


def test_so_no_pc_na_janela_deixa_a_ficha_no_pc(tmp_path):
    orq = _orquestrador(tmp_path, {**pe.atual(), "ia": "local"})
    orq.agentes_nuvem = {"estudos": "agente da nuvem"}
    tarefa = planejar("faz um resumo para a prova sobre usucapião")
    orq._aplicar_preferencias(tarefa)
    assert tarefa.contexto["so_no_pc"] is True and orq._agente(tarefa, "estudos") is orq.agentes["estudos"]


def test_mensagem_de_inicio_diz_o_jeito_escolhido(tmp_path):
    orq = _orquestrador(tmp_path, {**pe.atual(), "tamanho": "completo", "cores": "verde"})
    pronto = threading.Event()
    orq._executor.submit = lambda *a, **k: pronto.set()  # só a mensagem: a tarefa não roda
    tarefa = orq.enviar("faz um resumo para a prova sobre usucapião")
    assert "Do jeito escolhido na janela de estudos: completo, cores verde." in orq.mensagem_inicio(tarefa)


# ------------------------------------------------------------------ a ponte do painel
def test_ponte_do_painel_salva_apaga_e_mostra(tmp_path):
    api = pw.ApiPainel(None, {}, None)
    dados = api.estudos()
    assert {o["valor"] for o in dados["opcoes"]["cores"]} >= {"padrao", "livre", "guia"}
    assert dados["paletas"]["azul"]["marca"] == "#14305A" and dados["tipos_de_peca"]
    assert {"valor": "agravo_de_instrumento", "rotulo": "Agravo de instrumento", "area": "cível"} in dados["tipos_de_peca"]
    resposta = api.estudos_salvar({**dados["preferencias"], "estilo": "revisao", "guia_texto": "curto e direto"})
    assert resposta["ok"] and resposta["dados"]["preferencias"]["estilo"] == "revisao"
    assert resposta["dados"]["preferencias"]["guia"]["texto"] == "curto e direto"
    assert not api.estudos_salvar({"estilo": "poema"})["ok"]
    foto = pe.adicionar_foto("b.png", PNG)["id"]
    assert api.estudos_foto(foto).startswith("data:image/jpeg") and api.estudos_foto("nao_existe.png") == ""
    assert api.estudos_foto_apagar(foto)["dados"]["fotos"] == []
    assert api.estudos_padrao()["dados"]["preferencias"]["estilo"] == "tatico"


def test_peca_nova_da_janela_vira_tarefa(monkeypatch):
    api = pw.ApiPainel(None, {}, None)
    pedidos = []

    class TarefaFalsa:
        pass

    monkeypatch.setattr(api._orquestrador, "enviar_peca",
                        lambda tipo, fatos, arquivos, ao_concluir=None: pedidos.append((tipo, fatos, arquivos)) or (TarefaFalsa() if fatos or arquivos else None))
    monkeypatch.setattr(api._orquestrador, "mensagem_inicio", lambda tarefa: "Certo, vou redigir a contestação.")
    assert api.estudos_peca("contestacao", "O autor cobra R$ 5 mil.", []) == {"ok": True, "mensagem": "Certo, vou redigir a contestação."}
    assert pedidos[-1] == ("contestacao", "O autor cobra R$ 5 mil.", [])
    assert not api.estudos_peca("", "", [])["ok"]
