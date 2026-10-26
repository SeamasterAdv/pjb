"""Testes do ajuste fino de madrugada (ajuste_fino.py): dados, conferência, ciclo e troca de modelo, sem treinar de
verdade (um treinador falso) e sem a nuvem (uma IA falsa)."""
import json
import os
from datetime import datetime

import pytest

import ajuste_fino as af

SISTEMA = "Você é a Beatriz."


def troca(pergunta, resposta, segundos):
    return {"pergunta": pergunta, "resposta": resposta, "timestamp": 1000.0 + segundos}


# ------------------------------------------------------------------ dados
def test_monta_os_dados_com_acertos_elogios_e_correcoes():
    trocas = [
        troca("me conta uma curiosidade", "O polvo tem três corações.", 0),
        troca("valeu", "Disponha.", 10),
        troca("qual a previsão pra amanhã?", "Amanhã é segunda-feira.", 100),
        troca("não era isso", "Leitura incorreta da minha parte. O que você queria?", 110),
        troca("a previsão do tempo de amanhã", "Amanhã: 28 graus e sol.", 130),
        troca("abre o navegador", "Erro ao abrir.", 200),
        troca("lê o artigo 5", "x" * 2000, 300),
    ]
    correcoes = [{"pergunta": "qual a previsão pra amanhã?", "resposta": "Amanhã é segunda-feira.", "quando": 1110.0}]
    treino, avaliacao, contas = af.montar_dados(SISTEMA, trocas, correcoes, parte_avaliacao=0.0)
    pares = [(e["messages"][1]["content"], e["messages"][2]["content"]) for e in treino + avaliacao]
    assert ("me conta uma curiosidade", "O polvo tem três corações.") in pares
    assert pares.count(("me conta uma curiosidade", "O polvo tem três corações.")) == 2  # elogiada vale em dobro
    assert ("qual a previsão pra amanhã?", "Amanhã: 28 graus e sol.") in pares  # o pedido original com a resposta certa
    assert ("qual a previsão pra amanhã?", "Amanhã é segunda-feira.") not in pares  # a errada fica de fora
    assert not any(p.startswith("não era isso") or r.startswith("Erro") or len(r) > 1200 for p, r in pares)
    assert all(e["messages"][0] == {"role": "system", "content": SISTEMA} for e in treino)
    assert contas["elogiadas"] == 1 and contas["corrigidas"] == 1 and contas["erradas"] == 1


def test_separa_parte_para_avaliar():
    trocas = [troca(f"pergunta número {i}", f"Resposta número {i}.", i * 10) for i in range(40)]
    treino, avaliacao, contas = af.montar_dados(SISTEMA, trocas, [])
    assert len(avaliacao) == 6 and len(treino) == 34 and contas["treino"] == 34
    assert not {json.dumps(e) for e in treino} & {json.dumps(e) for e in avaliacao}


def test_registra_e_le_as_correcoes():
    af.registrar_correcao("qual o clima?", "Hoje é segunda.", quando=50.0)
    assert af.ler_correcoes() == [{"pergunta": "qual o clima?", "resposta": "Hoje é segunda.", "quando": 50.0}]


def test_sinteticos_so_com_a_personalidade():
    pedidos = []

    class IAFalsa:
        def chat(self, prompt, formato=None, **_):
            pedidos.append(prompt)
            return json.dumps({"conversas": [{"pessoa": "tô entediado", "beatriz": "Diagnóstico: tédio."},
                                             {"pessoa": "", "beatriz": "sem pessoa"}]})

    exemplos = af.gerar_sinteticos("PERSONALIDADE DE TESTE", quantidade_por_tema=4, temas=["papo"], ia=IAFalsa(),
                                   sistema_do_treino="O PROMPT DO PC")
    assert len(exemplos) == 1 and exemplos[0]["messages"][2]["content"] == "Diagnóstico: tédio."
    assert exemplos[0]["messages"][0]["content"] == "O PROMPT DO PC"  # o que o modelo local recebe de verdade
    assert "PERSONALIDADE DE TESTE" in pedidos[0] and '"papo"' in pedidos[0]


# ------------------------------------------------------------------ conferência
@pytest.mark.parametrize("tipo, resposta, problema", [
    ("conversa", "", "resposta vazia"),
    ("conversa", "Diagnóstico concluído. Proposta: estude primeiro e arrume as pastas depois.", None),
    ("conversa", "Isso aqui se repete muito. Isso aqui se repete muito.", "repete frases"),
    ("conversa", "The quick brown fox jumps over the lazy dog again and again", "não parece português"),
    ("conversa", "Diagnóstico concluído. Como foi sua manhã?", None),
    ("conversa", "Olá 这是 um teste de você", "tem caracteres chineses"),
    ("json", '{"categoria": "pesquisa"}', None),
    ("json", '{"categoria": "outra"}', "JSON sem a categoria certa"),
    ("json", "não sei", "JSON inválido"),
])
def test_problemas_da_resposta(tipo, resposta, problema):
    problemas = af.problemas_da_resposta(tipo, resposta)
    assert (problema in problemas) if problema else problemas == []


# ------------------------------------------------------------------ o ciclo, com um treinador falso
class TreinadorFalso(af.Treinador):
    def __init__(self, melhora=0.08, conferencia_ok=True, falta=()):
        super().__init__(base_ollama="qwen2.5:14b")
        self.melhora, self.conferencia_ok, self.falta, self.passos = melhora, conferencia_ok, list(falta), []
        self.apagados = []

    def o_que_falta(self):
        return self.falta

    def liberar_placa(self):
        self.passos.append("placa")

    def treinar(self, dados, avaliacao, saida, max_passos=-1):
        self.passos.append(("treino", len(open(dados, encoding="utf-8").readlines()), max_passos))
        return {"exemplos": 40, "passos": 5, "melhora": self.melhora, "perda_antes": 2.0, "perda_depois": 1.84,
                "memoria_gb": 10.9}

    def instalar_no_ollama(self, nome, saida):
        self.passos.append(("ollama", nome))

    def conferir(self, nome, sistema):
        return {"ok": self.conferencia_ok, "respostas": [], "problemas": [] if self.conferencia_ok else ["tudo bem: repete frases"]}

    @property
    def ollama(self):
        apagados = self.apagados

        class Cliente:
            def delete(self, nome):
                apagados.append(nome)

        return Cliente()


@pytest.fixture
def historico(monkeypatch):
    trocas = [troca(f"pergunta {i}", f"Resposta boa número {i}.", i * 10) for i in range(40)]
    monkeypatch.setattr(af, "ler_historico", lambda arquivo=None: trocas)
    return trocas


def test_ciclo_promove_e_volta_ao_original(historico):
    treinador = TreinadorFalso()
    relatorio = af.ciclo_de_ajuste(treinador, sinteticos=False, relogio=lambda: datetime(2026, 10, 27, 3, 0).timestamp())
    assert relatorio["feito"] and relatorio["modelo"] == "beatriz-ajustada-20261027-0300"
    assert treinador.passos[0] == "placa" and treinador.passos[1][0] == "treino" and treinador.passos[1][1] == 34
    assert relatorio["mensagem"].startswith("Esta madrugada treinei o meu modelo com 40 exemplos: a perda caiu 8%")
    assert af.modelo_em_uso("qwen2.5:14b") == "beatriz-ajustada-20261027-0300"
    assert af.modelo_em_uso("qwen2.5vl:7b") == "qwen2.5vl:7b"  # só o modelo base troca
    assert af.relatorio_da_manha() == relatorio["mensagem"] and af.relatorio_da_manha() == ""  # uma vez só
    assert af.responder("Beatriz, volta pro modelo original").startswith("Pronto: voltei ao qwen2.5:14b.")
    assert af.modelo_em_uso("qwen2.5:14b") == "qwen2.5:14b"
    assert af.responder("usa o modelo ajustado") == "Pronto: voltei a usar o beatriz-ajustada-20261027-0300."
    assert af.responder("qual modelo você está usando?").startswith("Estou usando o beatriz-ajustada-20261027-0300.")


def test_ciclo_nao_troca_sem_melhora_ou_sem_passar_na_conferencia(historico):
    pouca = TreinadorFalso(melhora=0.005)
    assert "não troquei: a perda caiu só 0.5%" in af.ciclo_de_ajuste(pouca, sinteticos=False)["mensagem"]
    assert pouca.apagados and af.modelo_em_uso("qwen2.5:14b") == "qwen2.5:14b"
    reprovado = TreinadorFalso(conferencia_ok=False)
    assert "falhou na conferência (tudo bem: repete frases)" in \
        af.ciclo_de_ajuste(reprovado, sinteticos=False, forcar=True)["mensagem"]


def test_um_ajustado_por_vez_no_ollama(historico):
    def as_tres(hora):
        return lambda: datetime(2026, 10, 27, hora, 0).timestamp()

    primeiro = TreinadorFalso()
    af.ciclo_de_ajuste(primeiro, sinteticos=False, relogio=as_tres(3))
    reprovado = TreinadorFalso(melhora=0.001)
    af.ciclo_de_ajuste(reprovado, sinteticos=False, forcar=True, relogio=as_tres(4))
    # o reprovado sai do Ollama; o que está em uso, de outro horário, fica
    assert reprovado.apagados == ["beatriz-ajustada-20261027-0400"]
    assert af.modelo_em_uso("qwen2.5:14b") == "beatriz-ajustada-20261027-0300"
    segundo = TreinadorFalso()
    af.ciclo_de_ajuste(segundo, sinteticos=False, forcar=True, relogio=as_tres(5))
    assert segundo.apagados == ["beatriz-ajustada-20261027-0300"]  # o novo substitui o anterior
    assert af.modelo_em_uso("qwen2.5:14b") == "beatriz-ajustada-20261027-0500" and af.estado()["anterior"] is None


def test_guarda_so_as_ultimas_pastas_de_treino():
    raiz = os.path.join(af.pasta(), "treinos")
    for nome in ("20261001-0300", "20261008-0300", "20261015-0300", "20261022-0300", "20261029-0300"):
        os.makedirs(os.path.join(raiz, nome, "adaptador"))
    af._limpar_treinos({"promovido": {"pasta": os.path.join(raiz, "20261001-0300")}}, manter=2)
    assert sorted(os.listdir(raiz)) == ["20261001-0300", "20261022-0300", "20261029-0300"]  # a do modelo em uso fica


def test_ciclo_adiado(historico, monkeypatch):
    assert af.ciclo_de_ajuste(TreinadorFalso(falta=["o modelo base"]))["mensagem"] == \
        "Ajuste fino adiado: falta o modelo base."
    af.gravar_estado({"ultimo_ate": 1000.0 + 35 * 10, "treinos": []})
    assert af.ciclo_de_ajuste(TreinadorFalso())["mensagem"].startswith("Ajuste fino adiado: 4 trocas novas")


def test_teste_nao_troca_nem_conta_como_treino(historico):
    treinador = TreinadorFalso()
    relatorio = af.ciclo_de_ajuste(treinador, sinteticos=False, max_passos=3, teste=True, forcar=True)
    assert relatorio["mensagem"].startswith("Teste do ajuste fino: 5 passos com 40 exemplos, 10.9 GB na placa")
    assert treinador.passos[1][2] == 3 and treinador.apagados[0].startswith("beatriz-teste-")
    assert af.modelo_em_uso("qwen2.5:14b") == "qwen2.5:14b" and not af.estado().get("ultimo_treino")


def test_llm_clients_usa_o_modelo_promovido(monkeypatch):
    from llm_clients import llm_clients

    monkeypatch.setattr(llm_clients, "is_ollama_available", lambda: True)
    assert llm_clients._argumentos_chat("oi", None, None, False, None, None)["model"] == llm_clients.model
    af.gravar_estado({"promovido": {"modelo": "beatriz-ajustada-x", "base": llm_clients.model}})
    assert llm_clients._argumentos_chat("oi", None, None, False, None, None)["model"] == "beatriz-ajustada-x"


def test_sem_o_ajustado_no_ollama_ela_volta_ao_original(monkeypatch):
    import ollama

    from llm_clients import llm_clients

    pedidos = []

    class Ollama:
        def chat(self, model, stream=False, **_):
            pedidos.append(model)
            if model == "beatriz-ajustada-x":
                raise ollama.ResponseError("model 'beatriz-ajustada-x' not found", 404)
            resposta = {"message": {"content": "Diagnóstico concluído."}}
            return iter([resposta]) if stream else resposta

    monkeypatch.setattr(llm_clients, "is_ollama_available", lambda: True)
    monkeypatch.setattr(llm_clients, "ollama_client", Ollama())
    af.gravar_estado({"promovido": {"modelo": "beatriz-ajustada-x", "base": llm_clients.model}})
    assert llm_clients.chat("oi") == "Diagnóstico concluído."
    assert pedidos == ["beatriz-ajustada-x", llm_clients.model] and af.modelo_em_uso(llm_clients.model) == llm_clients.model
    af.gravar_estado({"promovido": {"modelo": "beatriz-ajustada-x", "base": llm_clients.model}})
    assert list(llm_clients.chat_stream("oi")) == ["Diagnóstico concluído."]
    assert pedidos[-2:] == ["beatriz-ajustada-x", llm_clients.model]


# ------------------------------------------------------------------ a madrugada
def test_madrugada_previne_e_treina_uma_vez_por_noite(tmp_path):
    log = tmp_path / "beatriz.log"
    projeto = os.path.dirname(af.__file__).replace("\\", "/")
    bloco = (f'Traceback (most recent call last):\n  File "{projeto}/agente_x.py", line 3, in f\n    g()\n'
             "ValueError: quebrou\n")
    log.write_text(bloco * 3, encoding="utf-8")
    pedidos, ciclos = [], []

    class MelhoriaFalsa:
        def pedir(self, pedido, origem="voz", **_):
            pedidos.append((pedido, origem))
            return "Diagnóstico aceito."

    tres_da_manha = datetime(2026, 10, 27, 3, 0).timestamp()
    rotina = af.RotinaDaMadrugada(melhoria=MelhoriaFalsa(), treinador=TreinadorFalso(), relogio=lambda: tres_da_manha,
                                  ociosa_desde=lambda: tres_da_manha - 3600, caminho_log=str(log),
                                  ciclo=lambda treinador, forcar=False: ciclos.append(forcar) or {"feito": True})
    assert rotina.hora_de_trabalhar()
    rotina.noite()
    assert pedidos[0][1] == "noite" and "agente_x.py:3 em f" in pedidos[0][0] and ciclos == [False]
    assert not rotina.hora_de_trabalhar()  # já trabalhou esta noite
    dados = af.estado()
    dados["noite"] = ""
    af.gravar_estado(dados)
    rotina.noite()
    assert len(pedidos) == 1  # o mesmo erro não é tentado de novo
    meio_dia = af.RotinaDaMadrugada(relogio=lambda: datetime(2026, 10, 27, 12, 0).timestamp())
    assert not meio_dia.hora_de_trabalhar()
    acordada = af.RotinaDaMadrugada(relogio=lambda: tres_da_manha + 86400, ociosa_desde=lambda: tres_da_manha + 86000)
    assert not acordada.hora_de_trabalhar()  # conversou há poucos minutos


@pytest.mark.parametrize("frase, esperado", [
    ("treina o seu modelo hoje", "Combinado: nesta madrugada eu treino"),
    ("pode treinar esta noite", "Combinado: nesta madrugada eu treino"),
    ("para de treinar", "Ajuste fino da madrugada desligado."),
    ("como está o seu treino?", "Estou usando o modelo original. Ainda não fiz nenhum ajuste fino."),
    ("me treina para a prova de direito", None),
    ("quero treinar inglês", None),
])
def test_pedidos_falados(frase, esperado, monkeypatch):
    monkeypatch.setattr(af.Treinador, "o_que_falta", lambda self: [])
    resposta = af.responder(frase)
    assert resposta.startswith(esperado) if esperado else resposta is None


def test_instala_o_gguf_no_formato_do_modelo_do_ollama(tmp_path):
    comandos = []
    blob = tmp_path / "sha256-base"
    blob.write_bytes(b"GGUF")

    class Ollama:
        sem_arquivo = False

        def show(self, nome):
            assert nome == "qwen2.5:14b"
            return {"template": "{{ .Prompt }}", "parameters": 'stop "<|im_start|>"\nstop "<|im_end|>"',
                    "modelfile": "# Modelfile\n" + ("" if self.sem_arquivo else f"FROM {blob}\n") + "TEMPLATE x\n"}

    class Treinador(af.Treinador):
        recusa = False

        def pasta_da_base(self):
            return str(tmp_path / "base")

        def executar(self, comando, **_):
            comandos.append(comando)
            if comando[0] != "ollama":  # a junção grava o GGUF
                with open(comando[comando.index("--destino") + 1], "wb") as arquivo:
                    arquivo.write(b"GGUF")
            return type("P", (), {"returncode": 1 if self.recusa and comando[0] == "ollama" else 0,
                                  "stderr": "\x1b[?25lError: invalid file magic", "stdout": ""})

    ollama = Ollama()
    treinador = Treinador(ollama=ollama, base_ollama="qwen2.5:14b")
    treinador.instalar_no_ollama("beatriz-ajustada-x", str(tmp_path))
    juncao, criacao = comandos
    assert juncao[1].endswith("treinar_lora.py") and "--juntar" in juncao
    assert juncao[juncao.index("--molde") + 1] == str(blob)  # o formato vem do modelo base do Ollama
    assert criacao == ["ollama", "create", "beatriz-ajustada-x", "-f", str(tmp_path / "Modelfile")]  # sem --quantize
    assert (tmp_path / "Modelfile").read_text(encoding="utf-8").splitlines() == [
        f"FROM {(tmp_path / 'modelo.gguf').as_posix()}", 'TEMPLATE """{{ .Prompt }}"""',
        'PARAMETER stop "<|im_start|>"', 'PARAMETER stop "<|im_end|>"']
    assert not (tmp_path / "modelo.gguf").exists()  # o Ollama guarda a cópia dele; os ~9 GB daqui saem
    treinador.recusa = True
    with pytest.raises(af.ErroDoAjuste, match="^o Ollama não criou o modelo: Error: invalid file magic$"):
        treinador.instalar_no_ollama("beatriz-ajustada-x", str(tmp_path))
    assert not (tmp_path / "modelo.gguf").exists()
    ollama.sem_arquivo = True
    with pytest.raises(af.ErroDoAjuste, match="não achei o arquivo do qwen2.5:14b"):
        treinador.instalar_no_ollama("beatriz-ajustada-x", str(tmp_path))


# ------------------------------------------------------------------ o treino em pacotes (treinar_lora.py)
class TokenizadorFalso:
    """Um token por caractere, com um modelo de conversa simples ("<s>…|<u>…|<a>…|")."""

    def apply_chat_template(self, mensagens, tokenize=False):
        return "".join(f"<{m['role'][0]}>{m['content']}|" for m in mensagens)

    def __call__(self, texto, add_special_tokens=False):
        return {"input_ids": [ord(c) % 64 for c in texto]}


def conversa(sistema, pessoa, resposta):
    return [{"role": "system", "content": sistema}, {"role": "user", "content": pessoa},
            {"role": "assistant", "content": resposta}]


def test_exemplo_marca_o_sistema_e_so_a_resposta_conta():
    import treinar_lora

    exemplo = treinar_lora.exemplo_tokenizado(TokenizadorFalso(), conversa("SIS", "oi", "olá"), 100)
    assert exemplo["prefixo"] == len("<s>SIS|")
    assert len(exemplo["input_ids"]) == len("<s>SIS|<u>oi|<a>olá|")
    assert [r != -100 for r in exemplo["labels"]] == [False] * len("<s>SIS|<u>oi|") + [True] * len("<a>olá|")


def test_empacota_com_o_sistema_uma_vez_so():
    import treinar_lora

    tokenizador = TokenizadorFalso()
    exemplos = [treinar_lora.exemplo_tokenizado(tokenizador, conversa("SIS", p, r), 100)
                for p, r in [("a", "um"), ("bb", "dois"), ("c", "três")]]
    exemplos.append(treinar_lora.exemplo_tokenizado(tokenizador, conversa("OUTRO", "d", "quatro"), 100))
    pacotes = treinar_lora.empacotar(exemplos, por_pacote=2, maximo=100)
    assert [len(p["segmentos"]) for p in pacotes] == [2, 1, 1]  # 2 + 1 com o mesmo sistema; o outro sistema à parte
    primeiro, prefixo = pacotes[0], exemplos[0]["prefixo"]
    assert primeiro["input_ids"][:prefixo] == exemplos[0]["input_ids"][:prefixo]
    assert primeiro["input_ids"][prefixo:] == exemplos[0]["input_ids"][prefixo:] + exemplos[1]["input_ids"][prefixo:]
    (a, b), (c, d) = primeiro["segmentos"]
    assert (a, b, c) == (prefixo, len(exemplos[0]["input_ids"]), b)
    assert primeiro["position_ids"][c:d] == list(range(prefixo, prefixo + d - c))  # as posições recomeçam no sistema
    assert len(treinar_lora.empacotar(exemplos[:3], por_pacote=8, maximo=len(primeiro["input_ids"]))) == 2  # o máximo vale


def test_mascara_cada_conversa_ve_o_sistema_e_ela_mesma():
    import treinar_lora

    pacote = {"input_ids": [0] * 6, "segmentos": [(2, 4), (4, 6)]}
    mascara = treinar_lora.mascara_do_pacote(pacote)[0, 0]
    assert mascara.shape == (6, 6)
    assert mascara[1].tolist() == [True, True, False, False, False, False]  # o sistema: causal
    assert mascara[3].tolist() == [True, True, True, True, False, False]
    assert mascara[5].tolist() == [True, True, False, False, True, True]  # não vê a conversa de antes


def test_pacote_da_a_mesma_perda_e_o_mesmo_gradiente():
    """Num Qwen2 minúsculo, em float32 na CPU: os exemplos empacotados dão a perda e o gradiente dos separados."""
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")

    import treinar_lora

    torch.manual_seed(0)
    configuracao = transformers.Qwen2Config(vocab_size=64, hidden_size=32, intermediate_size=64, num_hidden_layers=2,
                                            num_attention_heads=4, num_key_value_heads=2, max_position_embeddings=256,
                                            attn_implementation="sdpa")
    modelo = transformers.Qwen2ForCausalLM(configuracao).float()
    tokenizador = TokenizadorFalso()
    exemplos = [treinar_lora.exemplo_tokenizado(tokenizador, conversa("um sistema comprido", p, r), 200)
                for p, r in [("oi", "olá, tudo bem?"), ("e aí", "diagnóstico concluído"), ("piada", "três patos")]]

    def perda_e_gradiente(por_pacote):
        modelo.zero_grad(set_to_none=True)
        soma_total, tokens_total = 0.0, 0
        for pacote in treinar_lora.empacotar(exemplos, por_pacote, 400):
            soma, tokens = treinar_lora.soma_da_perda(modelo, treinar_lora.juntar([pacote]))
            soma.backward()
            soma_total, tokens_total = soma_total + float(soma), tokens_total + tokens
        gradiente = torch.cat([p.grad.flatten() for p in modelo.parameters() if p.grad is not None])
        return soma_total / tokens_total, gradiente / tokens_total

    separado, gradiente_separado = perda_e_gradiente(1)
    junto, gradiente_junto = perda_e_gradiente(3)
    assert abs(separado - junto) < 1e-5
    assert torch.allclose(gradiente_separado, gradiente_junto, atol=1e-6, rtol=1e-4)
    # e o exemplo sozinho pelo caminho padrão do transformers (máscara 2D) dá a mesma perda que o pacote de um
    lote = treinar_lora.juntar([treinar_lora.empacotar(exemplos[:1], 1, 400)[0]])
    padrao = dict(lote, attention_mask=torch.ones_like(lote["input_ids"]))
    del padrao["position_ids"]
    with torch.no_grad():
        assert abs(float(treinar_lora.soma_da_perda(modelo, padrao)[0]) - float(treinar_lora.soma_da_perda(modelo, lote)[0])) < 1e-4


# ------------------------------------------------------------------ o GGUF (treinar_lora.py)
def desfazer_q4_k(blocos):
    """O block_q4_K lido como o ggml lê: d e dmin (f16), 8 escalas e 8 mínimos de 6 bits em 12 bytes, níveis de 4 bits."""
    import numpy as np

    d = blocos[:, 0:2].copy().view(np.float16).astype(np.float32)
    dmin = blocos[:, 2:4].copy().view(np.float16).astype(np.float32)
    e, qs = blocos[:, 4:16], blocos[:, 16:]
    escalas = np.concatenate([e[:, 0:4] & 63, (e[:, 8:12] & 15) | ((e[:, 0:4] >> 6) << 4)], axis=1)
    minimos = np.concatenate([e[:, 4:8] & 63, (e[:, 8:12] >> 4) | ((e[:, 4:8] >> 6) << 4)], axis=1)
    pares = qs.reshape(-1, 4, 32)
    niveis = np.stack([pares & 15, pares >> 4], axis=2).reshape(-1, 8, 32).astype(np.float32)
    return ((d * escalas)[..., None] * niveis - (dmin * minimos)[..., None]).reshape(-1, 256)


def desfazer_q6_k(blocos):
    """O block_q6_K: 4 bits de baixo, 2 de cima, 16 escalas de 8 bits e o d (f16)."""
    import numpy as np

    ql, qh = blocos[:, :128].reshape(-1, 2, 64), blocos[:, 128:192].reshape(-1, 2, 32)
    escalas = blocos[:, 192:208].copy().view(np.int8).astype(np.float32)
    d = blocos[:, 208:210].copy().view(np.float16).astype(np.float32)
    baixo = np.concatenate([ql[:, :, :32] & 15, ql[:, :, 32:] & 15, ql[:, :, :32] >> 4, ql[:, :, 32:] >> 4], axis=2)
    alto = np.concatenate([(qh >> s) & 3 for s in (0, 2, 4, 6)], axis=2)
    niveis = (baixo | (alto << 4)).astype(np.float32).reshape(-1, 256) - 32
    return np.repeat(d * escalas, 16, axis=1) * niveis


def pesos_de_teste(linhas=64, colunas=1024):
    import torch

    gerador = torch.Generator().manual_seed(7)
    pesos = torch.randn(linhas, colunas, generator=gerador) * 0.02
    pesos[torch.rand(linhas, colunas, generator=gerador) < 0.002] *= 15  # alguns valores grandes, como nos de verdade
    return pesos.to(torch.bfloat16)


@pytest.mark.parametrize("tipo, desfazer, tamanho, erro_maximo", [
    ("Q4_K", desfazer_q4_k, 144, 0.095),  # 4,5 bits por peso: entre o Q4_1 (0,091) e o Q5_0 de um teste igual
    ("Q6_K", desfazer_q6_k, 210, 0.03),
])
def test_quantizacao_k_no_formato_do_ggml(tipo, desfazer, tamanho, erro_maximo):
    import numpy as np
    import torch

    import treinar_lora

    pesos = pesos_de_teste()
    dados = treinar_lora.quantizar(pesos, tipo)
    assert dados.dtype == np.uint8 and dados.shape == (64, 1024 // 256 * tamanho)
    original = pesos.float().numpy()
    desfeito = desfazer(dados.reshape(-1, tamanho)).reshape(original.shape)
    erro = np.sqrt(((desfeito - original) ** 2).mean() / (original ** 2).mean())
    assert erro < erro_maximo
    assert not desfazer(treinar_lora.quantizar(torch.zeros(2, 256), tipo)).any()  # zeros continuam zeros
    try:
        import gguf
    except ImportError:
        return
    # Onde o pacote `gguf` existe (no .venv_treino), o decodificador oficial tem de ler igual.
    oficial = gguf.quants.dequantize(dados, getattr(gguf.GGMLQuantizationType, tipo))
    assert np.allclose(oficial, desfeito, atol=1e-6)


def test_nomes_do_qwen2_no_gguf():
    import treinar_lora

    assert treinar_lora.nome_gguf("model.layers.12.self_attn.q_proj.bias") == "blk.12.attn_q.bias"
    assert treinar_lora.nome_gguf("model.layers.0.mlp.down_proj.weight") == "blk.0.ffn_down.weight"
    assert treinar_lora.nome_gguf("model.embed_tokens.weight") == "token_embd.weight"
    assert treinar_lora.nome_gguf("lm_head.weight") == "output.weight"
    with pytest.raises(SystemExit, match="não sei converter"):
        treinar_lora.nome_gguf("model.layers.0.mlp.experts.0.weight")


def test_grava_o_gguf_no_molde_do_ollama(tmp_path):
    """O caminho inteiro da junção: base em safetensors + adaptador + o GGUF do Ollama como molde. Só roda onde o
    pacote `gguf` existe (o .venv_treino)."""
    gguf = pytest.importorskip("gguf")
    import numpy as np
    import torch
    from safetensors.torch import save_file

    import treinar_lora

    base, adaptador = tmp_path / "base", tmp_path / "adaptador"
    base.mkdir()
    adaptador.mkdir()
    pesos = {"model.embed_tokens.weight": pesos_de_teste(8, 256),
             "model.layers.0.self_attn.q_proj.weight": pesos_de_teste(256, 256),
             "model.layers.0.input_layernorm.weight": torch.ones(256, dtype=torch.bfloat16)}
    save_file(pesos, str(base / "model.safetensors"), metadata={"format": "pt"})
    a, b = torch.randn(2, 256) * 0.01, torch.randn(256, 2) * 0.01
    save_file({"base_model.model.model.layers.0.self_attn.q_proj.lora_A.weight": a,
               "base_model.model.model.layers.0.self_attn.q_proj.lora_B.weight": b},
              str(adaptador / "adapter_model.safetensors"))
    (adaptador / "adapter_config.json").write_text('{"r": 2, "lora_alpha": 4}', encoding="utf-8")
    molde = tmp_path / "molde.gguf"
    escritor = gguf.GGUFWriter(str(molde), "qwen2")
    escritor.add_name("Qwen2.5 14B Instruct")
    escritor.add_block_count(1)
    escritor.add_token_list(["olá", "mundo"])
    tipos = {"token_embd.weight": "Q4_K", "blk.0.attn_q.weight": "Q6_K", "blk.0.attn_norm.weight": "F32"}
    for nome_hf, nome in [("model.embed_tokens.weight", "token_embd.weight"),
                          ("model.layers.0.self_attn.q_proj.weight", "blk.0.attn_q.weight"),
                          ("model.layers.0.input_layernorm.weight", "blk.0.attn_norm.weight")]:
        tipo = tipos[nome]
        dados = treinar_lora.quantizar(pesos[nome_hf], tipo)
        escritor.add_tensor(nome, dados, raw_dtype=None if tipo == "F32" else getattr(gguf.GGMLQuantizationType, tipo))
    escritor.write_header_to_file()
    escritor.write_kv_data_to_file()
    escritor.write_tensors_to_file()
    escritor.close()

    destino = tmp_path / "modelo.gguf"
    treinar_lora.escrever_gguf(str(base), str(adaptador), str(molde), str(destino))
    leitor = gguf.GGUFReader(str(destino))
    assert leitor.fields["general.name"].contents() == "Beatriz ajustada"
    assert leitor.fields["tokenizer.ggml.tokens"].contents() == ["olá", "mundo"]
    assert {t.name: t.tensor_type.name for t in leitor.tensors} == tipos
    lidos = {t.name: t for t in leitor.tensors}
    somado = (pesos["model.layers.0.self_attn.q_proj.weight"].float() + 2.0 * (b @ a)).numpy()
    desfeito = gguf.quants.dequantize(lidos["blk.0.attn_q.weight"].data, gguf.GGMLQuantizationType.Q6_K)
    assert np.sqrt(((desfeito - somado) ** 2).mean() / (somado ** 2).mean()) < 0.03
    assert np.array_equal(lidos["blk.0.attn_norm.weight"].data, np.ones(256, dtype=np.float32))


def test_pasta_da_base_no_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    treinador = af.Treinador(python_treino=str(tmp_path / "nao_existe.exe"))
    assert treinador.pasta_da_base() == ""
    foto = tmp_path / ("models--" + af.BASE_HF.replace("/", "--")) / "snapshots" / "abc"
    foto.mkdir(parents=True)
    indice = {"weight_map": {"a": "model-00001-of-00002.safetensors", "b": "model-00002-of-00002.safetensors"}}
    (foto / "model.safetensors.index.json").write_text(json.dumps(indice), encoding="utf-8")
    (foto / "config.json").write_text("{}", encoding="utf-8")
    (foto / "model-00001-of-00002.safetensors").write_bytes(b"1")
    assert treinador.pasta_da_base() == ""  # o índice chega antes das partes: ainda baixando
    (foto / "model-00002-of-00002.safetensors").write_bytes(b"2")
    assert treinador.pasta_da_base() == str(foto)
    assert treinador.o_que_falta() == ["o ambiente de treino (.venv_treino)"]
