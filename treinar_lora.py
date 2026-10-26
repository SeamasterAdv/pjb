"""Treino LoRA/QLoRA do modelo de conversa da Beatriz. Roda no `.venv_treino` (PyTorch com CUDA, transformers, peft e
bitsandbytes), separado do programa, chamado pelo `ajuste_fino.py` de madrugada:

    .venv_treino\\Scripts\\python.exe treinar_lora.py --dados treino.jsonl --avaliacao avaliacao.jsonl --saida PASTA
    .venv_treino\\Scripts\\python.exe treinar_lora.py --juntar --base BASE --adaptador PASTA\\adaptador --molde BLOB_DO_OLLAMA
        --destino X.gguf

- A base são os pesos originais do Qwen2.5-14B-Instruct (bf16), os mesmos do `qwen2.5:14b` do Ollama, carregados em 4
  bits (NF4) na hora do treino.
- QLoRA: base em 4 bits congelada, LoRA r=16 nas projeções de atenção e do MLP, checkpoint de gradiente, AdamW paginado
  de 8 bits e bf16: cabe nos 12 GB da RTX 4070 com textos de até `--maximo` tokens (o prompt de sistema dela, a
  personalidade inteira, tem ~1430; a perda e os logits ficam só nos tokens da resposta).
- Só a resposta da Beatriz conta na perda (o sistema e a fala da pessoa ficam com -100).
- Em pacotes (`empacotar`): até `--por-pacote` exemplos com o mesmo prompt de sistema numa sequência só, o sistema uma
  vez, com uma máscara 4D que separa as conversas: o mesmo resultado, ~3 vezes mais rápido no 14B. Antes de treinar,
  `sondar_memoria` confere que o pacote mais longo cabe na memória livre da placa (senão, pacotes menores).
- Mede a perda no conjunto de avaliação antes (o LoRA começa zerado: é a base) e depois, e grava
  `resultado.json` com as duas, o número de exemplos, os passos e o tempo. O adaptador fica em `PASTA/adaptador`.
- `--juntar`: soma o adaptador aos pesos de 16 bits (W + escala·B·A), um peso por vez (poucos GB de memória), e
  grava o modelo inteiro num GGUF no molde do modelo base do Ollama (`escrever_gguf`): os metadados dele e o mesmo tipo
  em cada peso (o q4_K_M: Q4_K, Q6_K e F32), com os k-quants feitos aqui (`quantizar_q4_k`, `quantizar_q6_k`, a conta
  de referência do ggml). Por quê: o Ollama 0.35 não aceita mais adaptador LoRA por cima de um modelo ("LoRA adapters
  are no longer supported"), só importa safetensors pelo motor novo, que não aceita o Qwen2 ("unsupported MLX
  architecture"), e só quantiza na importação os safetensors; um GGUF já quantizado ele aceita.
Cada linha dos JSONL: {"messages": [{"role": "system"|"user"|"assistant", "content": "..."}]}.
Não importa nada da Beatriz: é para rodar sozinho.
"""
import argparse
import json
import math
import os
import re
import time

BASE_PADRAO = "Qwen/Qwen2.5-14B-Instruct"
RE_CHAVE_LORA = re.compile(r"^base_model\.model\.(.+)\.lora_([AB])(?:\.default)?\.weight$")


def ler_jsonl(caminho):
    with open(caminho, encoding="utf-8") as entrada:
        return [json.loads(linha) for linha in entrada if linha.strip()]


def exemplo_tokenizado(tokenizador, mensagens, maximo):
    """input_ids e labels (só os tokens das respostas da Beatriz entram na perda) e `prefixo`: quantos tokens do começo
    são a mensagem de sistema."""
    ids, rotulos, prefixo = [], [], 0
    for indice in range(len(mensagens)):
        anterior = tokenizador.apply_chat_template(mensagens[:indice], tokenize=False) if indice else ""
        ate_aqui = tokenizador.apply_chat_template(mensagens[:indice + 1], tokenize=False)
        pedaco = tokenizador(ate_aqui[len(anterior):], add_special_tokens=False)["input_ids"]
        ids += pedaco
        rotulos += pedaco if mensagens[indice]["role"] == "assistant" else [-100] * len(pedaco)
        if indice == 0 and mensagens[0]["role"] == "system":
            prefixo = len(pedaco)
    return {"input_ids": ids[:maximo], "labels": rotulos[:maximo], "prefixo": min(prefixo, maximo)}


def empacotar(exemplos, por_pacote, maximo):
    """Os exemplos com o mesmo prompt de sistema juntos numa sequência só: o sistema uma vez e, depois dele, cada
    conversa (`segmentos`), com as posições recomeçando logo depois do sistema. Com a máscara de `mascara_do_pacote`,
    cada conversa vê só o sistema e ela mesma: a perda e o gradiente são os de cada exemplo sozinho, mas o sistema
    (~1430 dos ~1500 tokens de cada exemplo dela) é processado uma vez por pacote, e não uma vez por exemplo."""
    grupos = {}
    for exemplo in exemplos:
        grupos.setdefault(tuple(exemplo["input_ids"][:exemplo["prefixo"]]), []).append(exemplo)
    pacotes = []
    for prefixo, membros in grupos.items():
        atual = None
        for exemplo in membros:
            ids, rotulos = exemplo["input_ids"][len(prefixo):], exemplo["labels"][len(prefixo):]
            if atual is None or len(atual["segmentos"]) >= por_pacote or len(atual["input_ids"]) + len(ids) > maximo:
                atual = {"input_ids": list(prefixo), "labels": [-100] * len(prefixo),
                         "position_ids": list(range(len(prefixo))), "prefixo": len(prefixo), "segmentos": []}
                pacotes.append(atual)
            inicio = len(atual["input_ids"])
            atual["input_ids"] += ids
            atual["labels"] += rotulos
            atual["position_ids"] += list(range(len(prefixo), len(prefixo) + len(ids)))
            atual["segmentos"].append((inicio, inicio + len(ids)))
    return pacotes


def mascara_do_pacote(pacote):
    """[1, 1, L, L] booleana (verdadeiro = pode olhar, o formato que o transformers 5 passa direto ao SDPA): causal,
    e cada conversa só enxerga o sistema e ela mesma."""
    import torch

    tamanho = len(pacote["input_ids"])
    posicao = torch.arange(tamanho)
    conversa = torch.zeros(tamanho, dtype=torch.long)  # 0 = o sistema
    for numero, (inicio, fim) in enumerate(pacote["segmentos"], 1):
        conversa[inicio:fim] = numero
    causal = posicao[None, :] <= posicao[:, None]
    return (causal & ((conversa[:, None] == conversa[None, :]) | (conversa[None, :] == 0)))[None, None]


def juntar(lote):
    """O lote do Trainer: um pacote por vez (os pacotes já juntam vários exemplos)."""
    import torch

    pacote, = lote
    return {"input_ids": torch.tensor([pacote["input_ids"]]), "labels": torch.tensor([pacote["labels"]]),
            "position_ids": torch.tensor([pacote["position_ids"]]), "attention_mask": mascara_do_pacote(pacote)}


def _logits_por_mascara():
    """O `logits_to_keep` aceita uma máscara (as posições que interessam) desde o transformers 5.21; antes, só um
    número. O `.venv_treino` tem a 5.21; os testes no `.venv312` (5.18) calculam todos os logits e escolhem depois."""
    try:
        from transformers.modeling_utils import slice_logits_to_keep  # noqa: F401
    except ImportError:
        return False
    return True


def soma_da_perda(modelo, lote):
    """(soma da perda, nº de tokens) só nos tokens da resposta. O lm_head (vocabulário de 152 mil) roda só nessas
    posições (`logits_to_keep` com máscara, do transformers 5), não nas do sistema e da pergunta: os logits de todas,
    em float32 e com o gradiente, passariam de 1 GB na placa."""
    import torch
    import torch.nn.functional as F

    alvos = lote["labels"][:, 1:]
    mascara = torch.zeros_like(lote["labels"], dtype=torch.bool)
    mascara[:, :-1] = alvos != -100  # a posição t prevê o token t+1 (a primeira de cada conversa é da pessoa: -100)
    tokens = int(mascara.sum())
    if not tokens:
        return None, 0
    so_as_respostas = _logits_por_mascara()
    saida = modelo(input_ids=lote["input_ids"], attention_mask=lote["attention_mask"],
                   position_ids=lote.get("position_ids"), logits_to_keep=mascara if so_as_respostas else 0)
    logits = saida.logits if so_as_respostas else saida.logits[mascara.to(saida.logits.device)]
    logits = logits.reshape(-1, logits.shape[-1]).float()
    # Com os embeddings no PC (`carregar_base`), os rótulos podem ficar lá e os logits na placa.
    return F.cross_entropy(logits, alvos[alvos != -100].to(logits.device), reduction="sum"), tokens


def placa_do_modelo(modelo):
    """Onde as entradas vão: a placa, onde está a saída (com `embeddings_no_pc`, o primeiro peso do modelo fica no PC)."""
    return modelo.get_output_embeddings().weight.device


def perda_media(modelo, pacotes):
    import torch

    modelo.eval()
    total, tokens = 0.0, 0
    dispositivo = placa_do_modelo(modelo)
    with torch.no_grad():
        for pacote in pacotes:
            lote = {k: v.to(dispositivo) for k, v in juntar([pacote]).items()}
            soma, validos = soma_da_perda(modelo, lote)
            if validos:
                total += float(soma)
                tokens += validos
    modelo.train()
    return total / tokens if tokens else float("nan")


def pares_do_adaptador(adaptador):
    """{nome do peso na base: (A, B)} e a escala do LoRA (alfa/r, ou alfa/√r com rsLoRA)."""
    from safetensors.torch import load_file

    with open(os.path.join(adaptador, "adapter_config.json"), encoding="utf-8") as arquivo:
        configuracao = json.load(arquivo)
    r, alfa = configuracao["r"], configuracao["lora_alpha"]
    escala = alfa / (math.sqrt(r) if configuracao.get("use_rslora") else r)
    partes = {}
    for chave, tensor in load_file(os.path.join(adaptador, "adapter_model.safetensors")).items():
        achado = RE_CHAVE_LORA.match(chave)
        if not achado:
            raise SystemExit(f"Parte do adaptador que eu não sei somar: {chave}")
        partes.setdefault(achado.group(1) + ".weight", {})[achado.group(2)] = tensor
    incompletos = [nome for nome, par in partes.items() if set(par) != {"A", "B"}]
    if incompletos:
        raise SystemExit(f"Adaptador sem o par A/B em: {incompletos[:3]}")
    return {nome: (par["A"], par["B"]) for nome, par in partes.items()}, escala


# Os nomes do Qwen2 no GGUF, os mesmos do conversor do llama.cpp (o `qwen2` que o Ollama e o llama.cpp leem).
NOMES_GGUF = [
    (r"model\.embed_tokens\.weight", "token_embd.weight"),
    (r"model\.norm\.weight", "output_norm.weight"),
    (r"lm_head\.weight", "output.weight"),
    (r"model\.layers\.(\d+)\.input_layernorm\.weight", r"blk.\1.attn_norm.weight"),
    (r"model\.layers\.(\d+)\.post_attention_layernorm\.weight", r"blk.\1.ffn_norm.weight"),
    (r"model\.layers\.(\d+)\.self_attn\.q_proj\.(weight|bias)", r"blk.\1.attn_q.\2"),
    (r"model\.layers\.(\d+)\.self_attn\.k_proj\.(weight|bias)", r"blk.\1.attn_k.\2"),
    (r"model\.layers\.(\d+)\.self_attn\.v_proj\.(weight|bias)", r"blk.\1.attn_v.\2"),
    (r"model\.layers\.(\d+)\.self_attn\.o_proj\.weight", r"blk.\1.attn_output.weight"),
    (r"model\.layers\.(\d+)\.mlp\.gate_proj\.weight", r"blk.\1.ffn_gate.weight"),
    (r"model\.layers\.(\d+)\.mlp\.up_proj\.weight", r"blk.\1.ffn_up.weight"),
    (r"model\.layers\.(\d+)\.mlp\.down_proj\.weight", r"blk.\1.ffn_down.weight"),
]
QK_K = 256  # os "k-quants" vão em superblocos de 256 pesos
GRUPO_VAZIO = 1e-15  # GROUP_MAX_EPS do ggml
PEDACO = 1 << 22  # pesos por vez na quantização (16 MB em float32)


def nome_gguf(nome):
    for padrao, novo in NOMES_GGUF:
        if re.fullmatch(padrao, nome):
            return re.sub(padrao, novo, nome)
    raise SystemExit(f"Peso do modelo que eu não sei converter: {nome}")


def partes_da_base(base):
    """{nome do peso: arquivo .safetensors} da base."""
    try:
        with open(os.path.join(base, "model.safetensors.index.json"), encoding="utf-8") as arquivo:
            return dict(json.load(arquivo)["weight_map"])
    except FileNotFoundError:
        from safetensors import safe_open

        with safe_open(os.path.join(base, "model.safetensors"), framework="pt") as arquivo:
            return {nome: "model.safetensors" for nome in arquivo.keys()}


# ------------------------------------------------------------------ quantização no formato do llama.cpp
# A mesma conta das funções de referência do ggml (quantize_row_q4_K_ref, quantize_row_q6_K_ref, make_qkx2_quants e
# make_qx_quants), feita em lote no PyTorch (na placa, quando há), sem o importance matrix, como o `llama-quantize`
# sem `--imatrix`. O pacote `gguf` só sabe desfazer esses formatos (o teste confere com ele), não montá-los.
def _faixa_qkx2(x, pesos, nmax=15, rmin=-1.0, rdelta=0.1, passos=20):
    """make_qkx2_quants: para cada grupo (a última dimensão), a escala e o mínimo (>= 0) que dão o menor erro
    ponderado, testando `passos` + 1 escalas perto da que cobre a faixa inteira."""
    import torch

    minimo = torch.clamp(x.amin(-1), max=0.0)
    maximo = x.amax(-1)
    soma_p = pesos.sum(-1)
    soma_x = (pesos * x).sum(-1)
    iguais = maximo == minimo
    faixa = torch.where(iguais, torch.ones_like(maximo), maximo - minimo)
    deslocado = x - minimo[..., None]
    inverso = nmax / faixa
    escala = 1 / inverso
    niveis = torch.clamp(torch.round(inverso[..., None] * deslocado), 0, nmax)
    melhor = (pesos * (escala[..., None] * niveis + minimo[..., None] - x) ** 2).sum(-1)
    escolhido = minimo.clone()
    for passo in range(passos + 1):
        inverso = (rmin + rdelta * passo + nmax) / faixa
        niveis = torch.clamp(torch.round(inverso[..., None] * deslocado), 0, nmax)
        soma_l = (pesos * niveis).sum(-1)
        soma_l2 = (pesos * niveis * niveis).sum(-1)
        soma_xl = (pesos * niveis * x).sum(-1)
        det = soma_p * soma_l2 - soma_l * soma_l
        valido = det > 0
        det = torch.where(valido, det, torch.ones_like(det))
        esta_escala = (soma_p * soma_xl - soma_x * soma_l) / det
        este_minimo = (soma_l2 * soma_x - soma_l * soma_xl) / det
        positivo = este_minimo > 0
        esta_escala = torch.where(positivo, soma_xl / torch.where(soma_l2 > 0, soma_l2, torch.ones_like(soma_l2)),
                                  esta_escala)
        este_minimo = torch.where(positivo, torch.zeros_like(este_minimo), este_minimo)
        erro = (pesos * (esta_escala[..., None] * niveis + este_minimo[..., None] - x) ** 2).sum(-1)
        trocar = valido & (erro < melhor)
        melhor = torch.where(trocar, erro, melhor)
        escala = torch.where(trocar, esta_escala, escala)
        escolhido = torch.where(trocar, este_minimo, escolhido)
    escala = torch.where(iguais, torch.zeros_like(escala), escala)
    return escala, -torch.where(iguais, minimo, escolhido)


def _escala_qx(x, nmax=32):
    """make_qx_quants (rmse_type 1): a escala com sinal de cada grupo, testando 19 escalas perto de -nmax/máximo."""
    import torch

    absoluto = x.abs()
    maior = absoluto.argmax(-1, keepdim=True)
    maximo = torch.gather(x, -1, maior)[..., 0]
    vazio = absoluto.amax(-1) < GRUPO_VAZIO
    maximo = torch.where(vazio, torch.ones_like(maximo), maximo)
    pesos = x * x

    def somas(inverso):
        niveis = torch.clamp(torch.round(inverso[..., None] * x), -nmax, nmax - 1)
        return (pesos * x * niveis).sum(-1), (pesos * niveis * niveis).sum(-1)

    def dividir(a, b):
        return torch.where(b > 0, a / torch.where(b > 0, b, torch.ones_like(b)), torch.zeros_like(a))

    soma_lx, soma_l2 = somas(-nmax / maximo)
    escala = dividir(soma_lx, soma_l2)
    melhor = escala * soma_lx
    for passo in range(-9, 10):
        if passo:
            lx, l2 = somas(-(nmax + 0.1 * passo) / maximo)
            trocar = (l2 > 0) & (lx * lx > melhor * l2)
            nova = dividir(lx, l2)
            escala = torch.where(trocar, nova, escala)
            melhor = torch.where(trocar, nova * lx, melhor)
    return torch.where(vazio, torch.zeros_like(escala), escala)


def _bytes_f16(valores):
    import torch

    return valores.to(torch.float16).contiguous().view(torch.uint8).reshape(-1, 2)


def quantizar_q4_k(x):
    """(blocos, 256) float32 → (blocos, 144) uint8 no formato block_q4_K: d, dmin (f16), 12 bytes com as 8 escalas e os
    8 mínimos de 6 bits e os 128 bytes dos níveis de 4 bits."""
    import torch

    blocos = x.shape[0]
    grupos = x.reshape(blocos, 8, 32)
    pesos = torch.sqrt((grupos * grupos).mean(-1, keepdim=True)) + grupos.abs()
    escalas, minimos = _faixa_qkx2(grupos, pesos)
    maior_escala, maior_minimo = escalas.amax(1), minimos.amax(1)
    inv_escala = torch.where(maior_escala > 0, 63 / torch.where(maior_escala > 0, maior_escala, 1.0), 0.0)
    inv_minimo = torch.where(maior_minimo > 0, 63 / torch.where(maior_minimo > 0, maior_minimo, 1.0), 0.0)
    ls = torch.clamp(torch.round(inv_escala[:, None] * escalas), 0, 63).to(torch.uint8)
    lm = torch.clamp(torch.round(inv_minimo[:, None] * minimos), 0, 63).to(torch.uint8)
    d = (maior_escala / 63).to(torch.float16)
    dmin = (maior_minimo / 63).to(torch.float16)
    passo = d.float()[:, None] * ls.float()
    base = dmin.float()[:, None] * lm.float()
    divisor = torch.where(passo > 0, passo, torch.ones_like(passo))
    niveis = torch.clamp(torch.round((grupos + base[..., None]) / divisor[..., None]), 0, 15)
    niveis = torch.where(passo[..., None] > 0, niveis, torch.zeros_like(niveis)).to(torch.uint8)
    escalas_bytes = torch.cat([ls[:, :4] | ((ls[:, 4:] >> 4) << 6), lm[:, :4] | ((lm[:, 4:] >> 4) << 6),
                               (ls[:, 4:] & 0xF) | ((lm[:, 4:] & 0xF) << 4)], dim=1)
    pares = niveis.reshape(blocos, 4, 2, 32)
    qs = (pares[:, :, 0] | (pares[:, :, 1] << 4)).reshape(blocos, 128)
    return torch.cat([_bytes_f16(d), _bytes_f16(dmin), escalas_bytes, qs], dim=1)


def quantizar_q6_k(x):
    """(blocos, 256) float32 → (blocos, 210) uint8 no formato block_q6_K: 128 bytes com os 4 bits de baixo, 64 com
    os 2 de cima, 16 escalas de 8 bits e o d (f16)."""
    import torch

    blocos = x.shape[0]
    grupos = x.reshape(blocos, 16, 16)
    escalas = _escala_qx(grupos)
    maior = escalas.abs().argmax(1, keepdim=True)
    maior_escala = torch.gather(escalas, 1, maior)[:, 0]
    vazio = escalas.abs().amax(1) < GRUPO_VAZIO
    inverso = torch.where(vazio, 0.0, -128 / torch.where(vazio, 1.0, maior_escala))
    d = torch.where(vazio, 0.0, 1 / torch.where(vazio, 1.0, inverso)).to(torch.float16)
    sc = torch.clamp(torch.round(inverso[:, None] * escalas), max=127).to(torch.int8)
    passo = d.float()[:, None] * sc.float()
    divisor = torch.where(passo != 0, passo, torch.ones_like(passo))
    niveis = torch.clamp(torch.round(grupos / divisor[..., None]), -32, 31) + 32
    niveis = torch.where(passo[..., None] != 0, niveis, torch.full_like(niveis, 32))
    niveis = torch.where(vazio[:, None, None], torch.zeros_like(niveis), niveis).to(torch.uint8)
    quartos = niveis.reshape(blocos, 2, 4, 32)  # cada metade de 128: os pedaços 0, 32, 64 e 96
    q1, q2, q3, q4 = quartos[:, :, 0], quartos[:, :, 1], quartos[:, :, 2], quartos[:, :, 3]
    ql = torch.cat([(q1 & 0xF) | ((q3 & 0xF) << 4), (q2 & 0xF) | ((q4 & 0xF) << 4)], dim=2).reshape(blocos, 128)
    qh = ((q1 >> 4) | ((q2 >> 4) << 2) | ((q3 >> 4) << 4) | ((q4 >> 4) << 6)).reshape(blocos, 64)
    return torch.cat([ql, qh, sc.view(torch.uint8), _bytes_f16(d)], dim=1)


QUANTIZADORES_K = {"Q4_K": (quantizar_q4_k, 144), "Q6_K": (quantizar_q6_k, 210)}


def quantizar(peso, tipo, dispositivo="cpu"):
    """Um peso (tensor do PyTorch, [saídas, entradas]) no `tipo` do GGUF (o nome: "Q4_K", "F32"...), como numpy: o
    float para F32/F16 e os bytes para os quantizados (os k-quants aqui; os outros pelo pacote `gguf`)."""
    import numpy as np
    import torch

    if tipo == "F32":
        return peso.float().numpy()
    if tipo == "F16":
        return peso.to(torch.float16).numpy()
    if tipo in QUANTIZADORES_K:
        funcao, tamanho = QUANTIZADORES_K[tipo]
        if peso.shape[-1] % QK_K:
            raise SystemExit(f"Um peso de {tuple(peso.shape)} não cabe em superblocos de {QK_K} ({tipo}).")
        planos = peso.reshape(-1, QK_K)
        saida = np.empty((planos.shape[0], tamanho), dtype=np.uint8)
        passo = max(1, PEDACO // QK_K)
        for inicio in range(0, planos.shape[0], passo):
            pedaco = planos[inicio:inicio + passo].to(dispositivo, torch.float32)
            saida[inicio:inicio + passo] = funcao(pedaco).cpu().numpy()
        return saida.reshape(*peso.shape[:-1], peso.shape[-1] // QK_K * tamanho)
    import gguf

    return gguf.quants.quantize(peso.float().numpy(), getattr(gguf.GGMLQuantizationType, tipo))


def escrever_gguf(base, adaptador, molde, destino):
    """O GGUF do modelo ajustado no molde do modelo base do Ollama (`molde`, o arquivo do qwen2.5:14b): os mesmos
    metadados (vocabulário, modelo de conversa, arquitetura), os mesmos pesos na mesma ordem e com o mesmo tipo de cada
    um (Q4_K/Q6_K/F32, o q4_K_M), só que com os números da base original + o adaptador. O Ollama 0.35 só aceita
    GGUF já quantizado ("create-time quantization is only supported for safetensors imports"). Grava em fluxo, um peso
    por vez: o modelo de 14B (~30 GB em 16 bits) não precisa caber na memória."""
    import contextlib

    import gguf
    import numpy as np
    import torch
    from safetensors import safe_open

    dispositivo = "cuda" if torch.cuda.is_available() else "cpu"
    leitor = gguf.GGUFReader(molde)
    partes = partes_da_base(base)
    do_gguf = {nome_gguf(nome): nome for nome in partes}
    faltam = [t.name for t in leitor.tensors if t.name not in do_gguf]
    if faltam:
        raise SystemExit(f"O modelo do Ollama tem pesos que a base não tem: {faltam[:3]}")
    pares, escala = pares_do_adaptador(adaptador) if adaptador else ({}, 1.0)
    sem_par = set(pares) - set(partes)
    if sem_par:
        raise SystemExit(f"O adaptador tem pesos que a base não tem: {sorted(sem_par)[:3]}")

    arquitetura = leitor.fields[gguf.Keys.General.ARCHITECTURE].contents()
    escritor = gguf.GGUFWriter(destino, arquitetura)
    for campo in leitor.fields.values():
        if campo.name == gguf.Keys.General.ARCHITECTURE or campo.name.startswith("GGUF."):
            continue  # o escritor põe esses sozinho
        tipo_do_valor = campo.types[0]
        subtipo = campo.types[-1] if tipo_do_valor == gguf.GGUFValueType.ARRAY else None
        valor = "Beatriz ajustada" if campo.name == gguf.Keys.General.NAME else campo.contents()
        escritor.add_key_value(campo.name, valor, tipo_do_valor, sub_type=subtipo)
    for tensor in leitor.tensors:
        escritor.add_tensor_info(tensor.name, tensor.data.shape, tensor.data.dtype, tensor.data.nbytes, tensor.tensor_type)
    escritor.write_header_to_file()
    escritor.write_kv_data_to_file()
    escritor.write_ti_data_to_file()

    conferencia = None
    with contextlib.ExitStack() as pilha:
        abertos = {}
        for posicao, tensor in enumerate(leitor.tensors):
            nome = do_gguf[tensor.name]
            if partes[nome] not in abertos:
                abertos[partes[nome]] = pilha.enter_context(safe_open(os.path.join(base, partes[nome]), framework="pt"))
            original = abertos[partes[nome]].get_tensor(nome)
            peso = original
            if nome in pares:
                a, b = pares[nome]
                soma = original.to(dispositivo, torch.float32) + escala * (b.to(dispositivo).float() @ a.to(dispositivo).float())
                peso = soma.to(original.dtype).cpu()
            tipo = tensor.tensor_type.name
            dados = quantizar(peso, tipo, dispositivo)
            if tuple(dados.shape) != tuple(tensor.data.shape):
                raise SystemExit(f"{tensor.name}: formato {tuple(dados.shape)}, o do Ollama é {tuple(tensor.data.shape)}.")
            if conferencia is None and tipo in QUANTIZADORES_K:
                # O primeiro peso quantizado confere tudo: o do Ollama (desfeito pelo pacote `gguf`) tem de bater com o
                # original (nomes e orientação certos), e o meu erro de quantização tem de ser parecido com o dele.
                qtipo = getattr(gguf.GGMLQuantizationType, tipo)
                linhas = min(original.shape[0], 1024)  # um pedaço basta (a quantização é por bloco), sem 10 GB de RAM
                referencia = original[:linhas].float().numpy()
                norma = float(np.sqrt((referencia ** 2).mean()))
                erro_ollama = float(np.sqrt(((gguf.quants.dequantize(tensor.data[:linhas], qtipo) - referencia) ** 2).mean())) / norma
                erro_meu = float(np.sqrt(((gguf.quants.dequantize(quantizar(original[:linhas], tipo, dispositivo), qtipo)
                                           - referencia) ** 2).mean())) / norma
                conferencia = {"peso": tensor.name, "tipo": tipo, "erro_ollama": round(erro_ollama, 4),
                               "erro_meu": round(erro_meu, 4)}
                print(json.dumps({"conferencia": conferencia}), flush=True)
                if erro_ollama > 0.25:
                    raise SystemExit(f"Os pesos da base não batem com os do modelo do Ollama ({tensor.name}).")
                if erro_meu > erro_ollama * 1.3 + 1e-4:
                    raise SystemExit(f"A minha quantização ficou pior que a do Ollama ({erro_meu:.4f} × {erro_ollama:.4f}).")
            escritor.write_tensor_data(np.ascontiguousarray(dados))
            if (posicao + 1) % 50 == 0:
                print(f"pesos gravados: {posicao + 1} de {len(leitor.tensors)}", flush=True)
    escritor.close()
    print(json.dumps({"pesos": len(leitor.tensors), "somados": len(pares), "conferencia": conferencia,
                      "gb": round(os.path.getsize(destino) / 1e9, 2)}), flush=True)


def embeddings_no_pc(modelo):
    """A tabela de embeddings de entrada (152 mil × 5120 no de 14B: 1,6 GB em 16 bits) só é consultada, um índice por
    token: ela vai para a memória do PC e a consulta acontece lá; só o resultado (poucos MB) vai para a placa. Com o
    `device_map` "cpu" do accelerate era pior: ele trazia a tabela inteira de volta para a placa a cada passada, e em
    float32 (3,1 GB), e o treino estourava. Com os embeddings presos à saída (os modelos pequenos), nada muda."""
    import torch

    if getattr(modelo.config, "tie_word_embeddings", False):
        return
    tabela = modelo.get_input_embeddings()
    placa = tabela.weight.device
    tabela.to("cpu")
    # A consulta vai para onde a tabela estiver, e o resultado para a placa.
    tabela.register_forward_pre_hook(
        lambda m, entradas: tuple(e.to(m.weight.device) if torch.is_tensor(e) else e for e in entradas))
    tabela.register_forward_hook(lambda _m, _entradas, saida: saida.to(placa))
    # Com um mapa de dispositivos de mais de um lugar, o Trainer e o accelerate não movem o modelo (sem isso, o Trainer
    # punha a tabela de volta na placa ao começar).
    modelo.hf_device_map = {"model.embed_tokens": "cpu", "model.layers": placa.index or 0, "model.norm": placa.index or 0,
                            "lm_head": placa.index or 0}
    torch.cuda.empty_cache()


def tabela_no_pc(modelo):
    """Se alguém pôs a tabela de embeddings de volta na placa (+1,6 GB, que a sonda não contou), ela volta para o PC."""
    import torch

    tabela = modelo.get_input_embeddings()
    if getattr(modelo.config, "tie_word_embeddings", False) or tabela.weight.device.type == "cpu":
        return
    print("a tabela de embeddings tinha voltado para a placa; de volta ao PC", flush=True)
    tabela.to("cpu")
    torch.cuda.empty_cache()


def carregar_base(caminho):
    """A base em 4 bits (NF4), quantizada na hora a partir dos pesos de 16 bits (ou como veio, num repositório já em 4
    bits), e congelada para o QLoRA. Sem o `prepare_model_for_kbit_training` do peft: ele passa para float32 tudo o que
    não é de 4 bits, e no de 14B os embeddings e a saída iriam de 3,1 para 6,2 GB, mais do que sobra nos 12 GB."""
    import torch
    from transformers import AutoConfig, AutoModelForCausalLM, BitsAndBytesConfig

    configuracao = AutoConfig.from_pretrained(caminho)
    extras = {}
    if not getattr(configuracao, "quantization_config", None):
        extras["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True)
    modelo = AutoModelForCausalLM.from_pretrained(caminho, device_map={"": 0}, dtype=torch.bfloat16, **extras)
    embeddings_no_pc(modelo)
    for parametro in modelo.parameters():
        parametro.requires_grad = False
    modelo.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    modelo.enable_input_require_grads()  # sem isso, com tudo congelado, o checkpoint não deixa o gradiente passar
    # O from_pretrained devolve o modelo em modo de avaliação, e nele o checkpoint fica desligado: cada camada guardaria
    # ~0,7 GB para a volta (o peso desfeito em 16 bits e as ativações), e a sonda de memória, que roda antes do
    # Trainer, estourava com qualquer tamanho.
    modelo.train()
    return modelo


def sondar_memoria(modelo, pacotes, margem=0.35e9):
    """Um passo de ida e volta, sem otimizador, no pacote mais longo: se ele não couber na memória livre da placa (com
    `margem` para os estados do AdamW de 8 bits e a fragmentação), os pacotes precisam ser menores. No Windows, passar
    da memória da placa não dá erro: o driver transborda para a RAM do PC e tudo fica 10 a 15 vezes mais lento (medido
    no 14B: 4 exemplos por pacote, pico de 11,2 GB, 1,8 s por exemplo; 8 por pacote, 12,3 GB, 11,9 s). Devolve o pico
    em GB, ou None se não coube. Os gradientes da sonda são jogados fora."""
    import torch

    dispositivo = placa_do_modelo(modelo)
    maior = max(pacotes, key=lambda pacote: len(pacote["input_ids"]))
    modelo.train()  # o checkpoint de gradiente só vale no modo de treino
    torch.cuda.empty_cache()
    # O que este processo já segura (o reservado pelo PyTorch, não só o alocado) + o livre.
    limite = torch.cuda.memory_reserved() + torch.cuda.mem_get_info()[0] - margem
    torch.cuda.reset_peak_memory_stats()
    try:
        soma, tokens = soma_da_perda(modelo, {k: v.to(dispositivo) for k, v in juntar([maior]).items()})
        if tokens:
            (soma / tokens).backward()
        pico = torch.cuda.max_memory_allocated()
    except torch.cuda.OutOfMemoryError:
        pico = None
    modelo.zero_grad(set_to_none=True)
    torch.cuda.empty_cache()
    if pico is None or pico > limite:
        print(f"a sonda passou do livre: {pico and round(pico / 1e9, 2)} GB, limite {limite / 1e9:.2f} GB", flush=True)
        return None
    return round(pico / 1e9, 2)


def treinar(opcoes):
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoTokenizer, Trainer, TrainingArguments

    if not torch.cuda.is_available():
        raise SystemExit("Sem CUDA: o treino precisa da placa de vídeo.")
    inicio = time.time()
    tempos = {}
    os.makedirs(opcoes.saida, exist_ok=True)
    tokenizador = AutoTokenizer.from_pretrained(opcoes.base)
    treino = [exemplo_tokenizado(tokenizador, e["messages"], opcoes.maximo) for e in ler_jsonl(opcoes.dados)]
    avaliacao = [exemplo_tokenizado(tokenizador, e["messages"], opcoes.maximo) for e in ler_jsonl(opcoes.avaliacao)]
    treino = [e for e in treino if any(r != -100 for r in e["labels"])]
    print(f"exemplos: {len(treino)} de treino, {len(avaliacao)} de avaliação", flush=True)

    modelo = get_peft_model(carregar_base(opcoes.base), LoraConfig(
        r=opcoes.r, lora_alpha=2 * opcoes.r, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
    modelo.print_trainable_parameters()
    tempos["carga"] = round(time.time() - inicio)

    # O maior pacote que cabe: começa no pedido e vai pela metade até a sonda passar (de madrugada, faltar memória
    # não pode custar a noite).
    por_pacote = max(1, opcoes.por_pacote)
    while True:
        pacotes = empacotar(treino, por_pacote, opcoes.maximo_pacote)
        pico = sondar_memoria(modelo, pacotes)
        print(f"pacotes de até {por_pacote}: {len(pacotes)}, pico da sonda {pico} GB", flush=True)
        if pico is not None or por_pacote == 1:
            break
        por_pacote //= 2
    if pico is None:
        raise SystemExit("Nem um exemplo por vez coube na placa.")
    pacotes_avaliacao = empacotar(avaliacao, por_pacote, opcoes.maximo_pacote)

    marca = time.time()
    perda_antes = perda_media(modelo, pacotes_avaliacao)
    tempos["perda_antes"] = round(time.time() - marca)
    print(f"perda antes: {perda_antes:.4f}", flush=True)
    # `--acumular` é em exemplos (8 por passo do otimizador, como antes); cada pacote já traz vários.
    acumular = max(1, round(opcoes.acumular * len(pacotes) / len(treino)))
    passos = opcoes.max_passos if opcoes.max_passos > 0 else math.ceil(len(pacotes) / acumular * opcoes.epocas)
    configuracao = TrainingArguments(  # o transformers 5 não tem mais o warmup_ratio: 5% dos passos, contados aqui
        output_dir=os.path.join(opcoes.saida, "passos"), per_device_train_batch_size=1,
        gradient_accumulation_steps=acumular, num_train_epochs=opcoes.epocas, max_steps=opcoes.max_passos,
        learning_rate=opcoes.taxa, lr_scheduler_type="cosine", warmup_steps=max(1, round(0.05 * passos)), logging_steps=5,
        optim="paged_adamw_8bit", bf16=True, gradient_checkpointing=True, save_strategy="no", report_to=[],
        remove_unused_columns=False)

    class TreinoSoDaResposta(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            if not getattr(self, "_tabela_conferida", False):
                tabela_no_pc(model)
                self._tabela_conferida = True
            soma, tokens = soma_da_perda(model, inputs)
            # Com num_items_in_batch (os tokens de todos os lotes acumulados), o Trainer não divide de novo.
            return soma / (num_items_in_batch if num_items_in_batch is not None else tokens)

    marca = time.time()
    estado = TreinoSoDaResposta(model=modelo, args=configuracao, train_dataset=pacotes, data_collator=juntar).train()
    tempos["treino"] = round(time.time() - marca)
    marca = time.time()
    perda_depois = perda_media(modelo, pacotes_avaliacao)
    tempos["perda_depois"] = round(time.time() - marca)
    print(f"perda depois: {perda_depois:.4f}", flush=True)
    modelo.save_pretrained(os.path.join(opcoes.saida, "adaptador"))
    resultado = {
        "base": opcoes.base, "exemplos": len(treino), "avaliacao": len(avaliacao), "passos": estado.global_step,
        "por_pacote": por_pacote, "pacotes": len(pacotes), "acumular": acumular,
        "perda_treino": round(float(estado.training_loss), 4),
        "perda_antes": round(perda_antes, 4), "perda_depois": round(perda_depois, 4),
        "melhora": round((perda_antes - perda_depois) / perda_antes, 4) if perda_antes and not math.isnan(perda_antes) else 0,
        "segundos": round(time.time() - inicio), "tempos": tempos,
        "memoria_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2), "sonda_gb": pico,
    }
    with open(os.path.join(opcoes.saida, "resultado.json"), "w", encoding="utf-8") as saida:
        json.dump(resultado, saida, ensure_ascii=False, indent=1)
    print(json.dumps(resultado, ensure_ascii=False), flush=True)


def main():
    argumentos = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    argumentos.add_argument("--juntar", action="store_true", help="somar o adaptador à base em vez de treinar")
    argumentos.add_argument("--dados")
    argumentos.add_argument("--avaliacao")
    argumentos.add_argument("--saida")
    argumentos.add_argument("--adaptador")
    argumentos.add_argument("--destino")
    argumentos.add_argument("--molde", help="o GGUF do modelo base no Ollama (a junção copia o formato dele)")
    argumentos.add_argument("--base", default=BASE_PADRAO)
    argumentos.add_argument("--epocas", type=float, default=1.0)
    argumentos.add_argument("--max-passos", type=int, default=-1, help="para um teste rápido (-1 = as épocas inteiras)")
    argumentos.add_argument("--maximo", type=int, default=2048, help="tokens por exemplo (o sistema dela tem ~1430)")
    argumentos.add_argument("--r", type=int, default=16)
    argumentos.add_argument("--taxa", type=float, default=1e-4)
    argumentos.add_argument("--acumular", type=int, default=8, help="exemplos por passo do otimizador")
    argumentos.add_argument("--por-pacote", type=int, default=4, help="exemplos por sequência (o sistema vai uma vez)")
    argumentos.add_argument("--maximo-pacote", type=int, default=4096, help="tokens por sequência")
    opcoes = argumentos.parse_args()
    if opcoes.juntar:
        if not (opcoes.adaptador and opcoes.destino and opcoes.molde):
            argumentos.error("--juntar precisa de --adaptador, --molde e --destino")
        escrever_gguf(opcoes.base, opcoes.adaptador, opcoes.molde, opcoes.destino)
    else:
        if not (opcoes.dados and opcoes.avaliacao and opcoes.saida):
            argumentos.error("o treino precisa de --dados, --avaliacao e --saida")
        treinar(opcoes)


if __name__ == "__main__":
    main()
