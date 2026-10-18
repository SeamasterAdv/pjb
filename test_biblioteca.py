"""Biblioteca de conhecimento: trechos, embedding do Ollama e busca híbrida (vetores + BM25).

Usa o ChromaDB em RAM com uma função de embedding falsa, então não precisa do Ollama.
"""
import hashlib
import json
import uuid

import chromadb
import numpy as np
import pytest
from chromadb.api.types import EmbeddingFunction

import memory


class EmbeddingFalso(EmbeddingFunction):
    """Bolsa de raízes das palavras; sinônimos caem na mesma posição (o "significado")."""

    SINONIMOS = {"automovel": "carro", "veiculo": "carro"}

    def __init__(self):
        self.quebrado = False

    def __call__(self, input):
        if self.quebrado:
            raise ConnectionError("Failed to connect to Ollama")
        vetores = []
        for texto in input:
            vetor = np.zeros(256, dtype=np.float32)
            for token in memory._tokens(texto):
                token = self.SINONIMOS.get(token, token)
                vetor[int(hashlib.md5(token.encode()).hexdigest(), 16) % 256] += 1
            vetor[0] += 0.01  # nunca o vetor zero
            vetores.append(vetor / np.linalg.norm(vetor))
        return vetores

    @staticmethod
    def name():
        return "falso_beatriz"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return EmbeddingFalso()

    def default_space(self):
        return "cosine"


@pytest.fixture
def biblioteca(monkeypatch, tmp_path):
    """Pasta de fontes, manifesto e coleção em RAM só deste teste, ligados ao memory."""
    fontes = tmp_path / "fontes"
    fontes.mkdir()
    funcao = EmbeddingFalso()
    cliente = chromadb.EphemeralClient()
    nome = f"teste_{uuid.uuid4().hex}"
    colecao = cliente.get_or_create_collection(name=nome, embedding_function=funcao)
    monkeypatch.setattr(memory, "colecao_docs", colecao)
    monkeypatch.setattr(memory, "_fontes_raiz", lambda: [(str(fontes), "documento_local")])
    monkeypatch.setattr(memory, "CAMINHO_MANIFESTO_MEMORIA", str(tmp_path / "manifesto.json"))
    monkeypatch.setattr(memory, "BANCO_EM_RAM", False)
    monkeypatch.setattr(memory, "_registrar_auditoria", lambda *args, **kwargs: None)
    memory._INDEXACAO_EM_ANDAMENTO = False
    memory._INDEXACAO_PENDENTE = False
    yield fontes, funcao, colecao
    cliente.delete_collection(nome)


def _escrever_fontes(pasta):
    (pasta / "aluguel.md").write_text(
        "# Contrato de aluguel\n\nA multa por rescisão do contrato de aluguel é proporcional ao tempo que falta.",
        encoding="utf-8",
    )
    (pasta / "carros.md").write_text(
        "Meu carro precisa de revisão a cada dez mil quilômetros. Troque o óleo e os filtros.", encoding="utf-8"
    )
    (pasta / "receitas.md").write_text("Bolo de chocolate leva cacau, ovos e farinha de trigo.", encoding="utf-8")


def _primeira_fonte(resposta):
    return resposta.splitlines()[0]


# ------------------------------------------------------------------ trechos
def test_trechos_cortam_em_frase_e_repetem_o_final_do_anterior():
    frases = [f"A frase número {i} fala sobre o assunto {i} com algumas palavras a mais." for i in range(40)]
    texto = "\n\n".join(" ".join(frases[i:i + 4]) for i in range(0, 40, 4))

    trechos = memory.dividir_em_trechos(texto, tamanho=300, sobreposicao=60)

    assert len(trechos) > 5
    assert all(len(trecho) <= 302 for trecho in trechos)
    assert all(trecho.endswith(".") for trecho in trechos)  # nenhum corte no meio da frase
    for anterior, seguinte in zip(trechos, trechos[1:]):
        assert seguinte.split(".")[0] in anterior  # a primeira frase repete o fim do anterior
    juntos = " ".join(trechos)
    assert all(frase in juntos for frase in frases)


def test_texto_sem_pontuacao_e_palavra_gigante_tambem_sao_cortados():
    texto = " ".join(f"palavra{i}" for i in range(400)) + " " + "x" * 2500

    trechos = memory.dividir_em_trechos(texto, tamanho=500, sobreposicao=80)

    assert all(len(trecho) <= 502 for trecho in trechos)
    juntos = " ".join(trechos)
    assert all(f"palavra{i}" in juntos for i in range(400))
    assert sum(trecho.count("x") for trecho in trechos) >= 2500  # a "palavra" gigante foi cortada seca, sem perdas


def test_unidades_pequenas_se_juntam_e_o_localizador_diz_o_intervalo():
    unidades = [(f"Conteúdo curto da página {i}.", f"página {i}") for i in range(1, 4)]
    unidades.append(("Texto longo da última página. " * 60, "página 4"))

    trechos = memory._trechos_das_unidades(unidades, tamanho=400, sobreposicao=50)

    assert trechos[0][1] == "páginas 1 a 4"
    assert "página 1." in trechos[0][0] and "página 3." in trechos[0][0]
    assert trechos[-1][1] == "página 4"
    assert memory._localizador_intervalo("planilha Vendas", "planilha Custos") == "planilha Vendas a planilha Custos"


# ------------------------------------------------------------------ BM25 e fusão
def test_bm25_poe_primeiro_quem_tem_todas_as_palavras_e_descarta_cobertura_baixa():
    palavras = memory.extrair_palavras_chave("multa na rescisão do contrato de aluguel")
    textos = [
        "Texto sobre contrato de aluguel",
        "Contrato de aluguel e multa por rescisão",
        "Aluguel",  # menos da metade das palavras-chave: não conta como achado por palavras
        "Receita de bolo",
    ]
    assert memory.ordenar_por_bm25(palavras, textos) == [1, 0]
    assert memory.ordenar_por_bm25([], textos) == []


def test_fusao_favorece_quem_aparece_nas_duas_listas():
    # Quem está nas duas listas passa à frente de quem lidera uma só; empate fica na ordem de chegada.
    assert memory.fundir_rankings(["x", "y"], ["z", "y"]) == ["y", "x", "z"]
    assert memory.fundir_rankings(["a", "b", "c"], ["c", "b", "d"]) == ["c", "b", "a", "d"]


# ------------------------------------------------------------------ indexação e busca
def test_busca_hibrida_acha_por_palavras_e_por_significado(biblioteca):
    fontes, _, colecao = biblioteca
    _escrever_fontes(fontes)

    assert memory.indexar_pasta_documentos() == "3 fontes indexadas ou atualizadas."
    assert colecao.count() == 3

    resposta = memory.buscar_nos_documentos_modo("qual a multa na rescisão do aluguel?", modo="padrão")
    assert "aluguel.md" in _primeira_fonte(resposta)
    assert "| documento |" in _primeira_fonte(resposta)

    # Nenhum documento tem a palavra "automóvel": só o significado (o sinônimo) leva ao carro.
    resposta = memory.buscar_nos_documentos_modo("me fale do automóvel", modo="padrão")
    assert "carros.md" in _primeira_fonte(resposta)


def test_manifesto_guarda_o_corte_e_mudar_o_corte_reindexa(biblioteca, monkeypatch):
    fontes, _, _ = biblioteca
    _escrever_fontes(fontes)
    memory.indexar_pasta_documentos()

    with open(memory.CAMINHO_MANIFESTO_MEMORIA, encoding="utf-8") as arquivo:
        manifesto = json.load(arquivo)
    assert {registro["trechos"] for registro in manifesto.values()} == {memory.VERSAO_TRECHOS}
    assert memory.indexar_pasta_documentos() == "0 fontes indexadas ou atualizadas."

    monkeypatch.setattr(memory, "VERSAO_TRECHOS", "800/100")
    assert memory.indexar_pasta_documentos() == "3 fontes indexadas ou atualizadas."


def test_sem_embedding_a_busca_continua_pelas_palavras(biblioteca):
    fontes, funcao, _ = biblioteca
    _escrever_fontes(fontes)
    memory.indexar_pasta_documentos()

    funcao.quebrado = True  # o Ollama caiu depois da indexação
    resposta = memory.buscar_nos_documentos_modo("qual a multa na rescisão do aluguel?", modo="padrão")

    assert "aluguel.md" in _primeira_fonte(resposta)
    assert "receitas.md" not in resposta


def test_indexacao_para_cedo_quando_o_ollama_esta_fora_do_ar(monkeypatch, tmp_path):
    fontes = tmp_path / "fontes"
    fontes.mkdir()
    for nome in ("a.txt", "b.txt", "c.txt"):
        (fontes / nome).write_text("Um texto qualquer para indexar.", encoding="utf-8")

    class ColecaoSemOllama:
        upserts = 0

        def delete(self, **kwargs):
            pass

        def upsert(self, **kwargs):
            self.upserts += 1
            raise ConnectionError("Failed to connect to Ollama")

    colecao = ColecaoSemOllama()
    monkeypatch.setattr(memory, "colecao_docs", colecao)
    monkeypatch.setattr(memory, "_fontes_raiz", lambda: [(str(fontes), "documento_local")])
    monkeypatch.setattr(memory, "CAMINHO_MANIFESTO_MEMORIA", str(tmp_path / "manifesto.json"))
    monkeypatch.setattr(memory, "BANCO_EM_RAM", False)
    monkeypatch.setattr(memory, "_registrar_auditoria", lambda *args, **kwargs: None)
    memory._INDEXACAO_EM_ANDAMENTO = False
    memory._INDEXACAO_PENDENTE = False

    memory.indexar_pasta_documentos()

    assert colecao.upserts == 1
    status = memory.status_indexacao()
    assert status["status"] == "falhou" and "Ollama" in status["mensagem"]


# ------------------------------------------------------------------ embedding e coleções
def test_sem_o_modelo_no_ollama_usa_o_padrao_do_chroma_em_colecao_propria(monkeypatch):
    monkeypatch.setattr(memory, "_verificar_modelo_embedding", lambda modelo: "ausente")
    funcao, sufixo, aviso = memory._escolher_embedding("bge-m3")
    assert funcao is None and sufixo == "padrao"
    assert "ollama pull bge-m3" in aviso

    monkeypatch.setattr(memory, "_verificar_modelo_embedding", lambda modelo: "ok")
    funcao, sufixo, aviso = memory._escolher_embedding("bge-m3:latest")
    assert isinstance(funcao, memory.EmbeddingOllama) and aviso is None
    assert sufixo == "bge_m3" == memory._sufixo_modelo("bge-m3")

    # Ollama ainda subindo: fica com a coleção do modelo e avisa que, por ora, só há palavras.
    monkeypatch.setattr(memory, "_verificar_modelo_embedding", lambda modelo: "sem_ollama")
    funcao, sufixo, aviso = memory._escolher_embedding("bge-m3")
    assert isinstance(funcao, memory.EmbeddingOllama) and sufixo == "bge_m3"
    assert "busca por palavras" in aviso

    assert memory._escolher_embedding("")[0] is None
    assert memory.NOME_COLECAO_DOCS == f"documentos_usuario_v3_{memory.SUFIXO_EMBEDDING}"


def test_embedding_do_ollama_vai_em_lotes_e_guarda_a_pergunta():
    funcao = memory.EmbeddingOllama("http://127.0.0.1:1", "bge-m3")
    chamadas = []

    class ClienteFalso:
        def embed(self, model, input, keep_alive=None):
            chamadas.append((model, len(input), keep_alive))
            return {"embeddings": [[float(len(texto)), 1.0] for texto in input]}

    funcao._client = ClienteFalso()

    assert len(funcao([f"trecho {i}" for i in range(70)])) == 70
    assert [quantidade for _, quantidade, _ in chamadas] == [32, 32, 6]
    assert all(modelo == "bge-m3" and manter == memory.OLLAMA_KEEP_ALIVE for modelo, _, manter in chamadas)

    chamadas.clear()
    primeiro = funcao.embed_query(["qual a multa?"])
    segundo = funcao.embed_query(["qual a multa?"])
    assert len(chamadas) == 1
    assert np.array_equal(primeiro[0], segundo[0])
    assert funcao.name() == "ollama" and funcao.get_config()["model_name"] == "bge-m3"


def test_banco_em_ram_nao_confia_no_manifesto_do_disco(monkeypatch, tmp_path):
    caminho = tmp_path / "manifesto_ram.json"
    caminho.write_text(json.dumps({"antigo": {"caminho": "x", "assinatura": "1:1"}}), encoding="utf-8")
    monkeypatch.setattr(memory, "BANCO_EM_RAM", True)
    monkeypatch.setattr(memory, "_MANIFESTO_EM_RAM", {})
    monkeypatch.setattr(memory, "CAMINHO_MANIFESTO_MEMORIA", str(caminho))

    assert memory._carregar_manifesto() == {}  # o banco em RAM começou vazio

    memory._salvar_manifesto({"novo": {"caminho": "y"}})
    assert memory._carregar_manifesto() == {"novo": {"caminho": "y"}}
    assert json.loads(caminho.read_text(encoding="utf-8")) == {"novo": {"caminho": "y"}}  # o painel lê o arquivo
