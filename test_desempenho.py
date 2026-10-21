"""Testes das correções de memória e de tempo de resposta: um único num_ctx (sem recargas do modelo), processos filhos leves,
câmera testada uma vez, mediapipe só quando usado e o ECAPA em ONNX. Nada aqui abre a câmera, uma janela ou o Ollama."""
import multiprocessing
import sys

import numpy as np
import pytest

import biometria_voz as bv
import llm_clients as lc
import processos
import visao
from config import OLLAMA_NUM_CTX


# ------------------------------------------------------------------ um num_ctx só
class OllamaFalso:
    def __init__(self):
        self.chamadas = []

    def list(self):
        return []

    def chat(self, **argumentos):
        self.chamadas.append(argumentos)
        return {"message": {"content": "ok"}}

    def generate(self, **argumentos):
        self.chamadas.append(argumentos)


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(lc.ollama, "Client", lambda host=None: OllamaFalso())
    return lc.LLMClients()


def test_toda_chamada_leva_o_mesmo_num_ctx(cliente):
    cliente.chat("oi")
    cliente.chat("oi", opcoes={"temperature": 0, "num_predict": 4})  # o juiz da escuta
    cliente.chat_beatriz("oi", usar_historico=False)
    cliente.chat("oi", opcoes={"temperature": 0.3, "num_ctx": OLLAMA_NUM_CTX})  # resumo
    contextos = {chamada["options"]["num_ctx"] for chamada in cliente.ollama_client.chamadas}
    assert contextos == {OLLAMA_NUM_CTX}  # trocar o num_ctx faz o Ollama recarregar o modelo (8 a 10 s)


def test_a_pre_carga_usa_o_mesmo_num_ctx_das_conversas(cliente):
    cliente.aquecer()
    assert cliente.ollama_client.chamadas[-1]["options"] == {"num_ctx": OLLAMA_NUM_CTX}


def test_as_opcoes_do_chamador_continuam_valendo(cliente):
    cliente.chat("oi", opcoes={"temperature": 0.9, "num_predict": 7})
    opcoes = cliente.ollama_client.chamadas[-1]["options"]
    assert opcoes == {"num_ctx": OLLAMA_NUM_CTX, "temperature": 0.9, "num_predict": 7}


def test_agentes_e_resumo_usam_o_valor_central():
    from agentes.base import OPCOES_AGENTES

    assert OPCOES_AGENTES["num_ctx"] == OLLAMA_NUM_CTX
    assert "OLLAMA_NUM_CTX" in open("resumo_explicativo.py", encoding="utf-8").read()
    assert '"num_ctx": 8192' not in open("resumo_explicativo.py", encoding="utf-8").read()


# ------------------------------------------------------------------ processos filhos leves
def _alvo_do_filho(fila, evento):
    principal = sys.modules["__main__"]
    # No filho comum o __main__ é o script reimportado (tem __file__); no leve não há script nenhum para reimportar.
    fila.put({"arquivo_do_main": getattr(principal, "__file__", None), "evento": evento.is_set()})


def test_filho_leve_nao_reimporta_o_modulo_principal_e_nada_fica_alterado():
    principal = sys.modules["__main__"]
    antes = (getattr(principal, "__file__", "sem"), getattr(principal, "__spec__", "sem"))
    contexto = multiprocessing.get_context("spawn")
    fila, evento = contexto.Queue(), contexto.Event()
    evento.set()
    processo = processos.iniciar_leve(_alvo_do_filho, args=(fila, evento))
    resposta = fila.get(timeout=60)
    processo.join(timeout=30)
    assert resposta == {"arquivo_do_main": None, "evento": True}  # sem script reimportado, e o Event chegou ao filho
    assert (getattr(principal, "__file__", "sem"), getattr(principal, "__spec__", "sem")) == antes  # restaurado
    assert processo.daemon and processo.exitcode == 0


def test_filho_leve_restaura_o_principal_mesmo_se_o_start_falhar():
    principal = sys.modules["__main__"]
    antes = (getattr(principal, "__file__", "sem"), getattr(principal, "__spec__", "sem"))
    with pytest.raises(Exception):
        processos.iniciar_leve(lambda: None)  # um lambda não pode ser enviado a um filho spawn
    assert (getattr(principal, "__file__", "sem"), getattr(principal, "__spec__", "sem")) == antes


# ------------------------------------------------------------------ câmera e mediapipe
def test_importar_visao_nao_carrega_o_mediapipe():
    import subprocess

    saida = subprocess.run([sys.executable, "-c", "import visao, sys; print('mediapipe' in sys.modules)"],
                           capture_output=True, text=True, timeout=60).stdout.strip()
    assert saida == "False"


def test_camera_e_testada_uma_vez_por_minuto(monkeypatch):
    chamadas = []
    monkeypatch.setattr(visao, "_procurar_camera", lambda indices: chamadas.append(1) or 0)
    monkeypatch.setattr(visao, "_CAMERA_DETECTADA", {"em": 0.0, "valor": None})
    agora = [1000.0]
    monkeypatch.setattr(visao.time, "time", lambda: agora[0])
    assert [visao.detectar_camera_disponivel() for _ in range(5)] == [0] * 5 and len(chamadas) == 1
    agora[0] += visao.VALIDADE_DA_CAMERA + 1
    visao.detectar_camera_disponivel()
    assert len(chamadas) == 2


def test_abrir_a_camera_por_indice_tambem_e_guardado(monkeypatch):
    aberturas = []

    class Captura:
        def __init__(self, indice):
            aberturas.append(indice)

        def isOpened(self):
            return True

        def release(self):
            pass

    monkeypatch.setattr(visao.cv2, "VideoCapture", Captura)
    monkeypatch.setattr(visao, "_CAMERA_ABRE", {})
    assert all(visao.webcam_disponivel(0) for _ in range(4)) and aberturas == [0]


# ------------------------------------------------------------------ ECAPA em ONNX
def test_frontend_em_numpy_tem_o_formato_do_fbank():
    janela = np.hamming(400).astype(np.float32)
    mel = np.random.rand(201, 80).astype(np.float32)
    som = (np.random.randn(16000) * 0.1).astype(np.float32)
    caracteristicas = bv.caracteristicas(som, janela, mel)
    assert caracteristicas.shape == (1, 101, 80)  # 1 s = 101 quadros de 10 ms
    assert abs(float(caracteristicas.mean(axis=1).max())) < 1e-4  # a média por frase foi tirada
    assert float(caracteristicas.max() - caracteristicas.min()) <= 80.0 + 1e-3  # corte de 80 dB


def test_codificador_usa_o_onnx_quando_ele_existe_e_nao_importa_o_torch(tmp_path, monkeypatch):
    dado = tmp_path
    (dado / bv.ARQUIVO_ONNX).write_bytes(b"x")
    np.savez(dado / bv.ARQUIVO_FRONTEND, janela=np.hamming(400).astype(np.float32), mel=np.ones((201, 80), np.float32))

    class Sessao:
        def run(self, _saidas, entradas):
            assert entradas["caracteristicas"].shape[2] == 80
            return [np.arange(192, dtype=np.float32).reshape(1, 1, 192) + 1]

    monkeypatch.setattr("onnxruntime.InferenceSession", lambda *a, **k: Sessao())
    codificador = bv.CodificadorEcapa(str(dado))
    codificador._carregar()
    assert codificador.sessao is not None and codificador.modelo is None and codificador.pronto.is_set()
    vetor = codificador.vetor(np.zeros(16000, dtype=np.float32))
    assert vetor.shape == (192,) and abs(float(np.linalg.norm(vetor)) - 1.0) < 1e-5


def test_sem_onnx_tenta_gerar_e_sem_conseguir_cai_no_pytorch(tmp_path, monkeypatch):
    codificador = bv.CodificadorEcapa(str(tmp_path))
    gerou = []
    monkeypatch.setattr(codificador, "_gerar_onnx", lambda: gerou.append(1) or False)
    carregou_torch = []
    monkeypatch.setattr(codificador, "_carregar_torch", lambda: carregou_torch.append(1))
    codificador._carregar()
    assert gerou == [1] and carregou_torch == [1] and codificador.pronto.is_set()


def test_onnx_que_nao_bate_com_o_original_nao_fica_no_disco():
    codigo = open("biometria_voz.py", encoding="utf-8").read()
    assert "cosseno < 0.999" in codigo and "os.remove(os.path.join(pasta, arquivo))" in codigo
