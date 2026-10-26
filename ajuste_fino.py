"""Evolução contínua do modelo local: ajuste fino LoRA/QLoRA de madrugada, com o que deu certo e o que você corrigiu.

Out/2026, pedido do usuário: "um pipeline que analisa comandos bem-sucedidos ou correções feitas por você, gera
conjuntos de dados sintéticos (JSONL) e agenda fine-tunings automáticos do seu modelo local (LoRA/QLoRA) durante a
madrugada".

1. **Dados** (`montar_dados`, só no PC): do histórico de conversas, as trocas que deram certo (sem "não era isso" logo
   depois; com "valeu"/"isso mesmo" valem em dobro) e as correções (`registrar_correcao`, chamada quando você diz "não
   era isso": o pedido original passa a ter a resposta que você aceitou em seguida). 15% ficam de fora, para avaliar.
2. **Sintéticos** (`gerar_sinteticos`): o Claude (o Gemini de reserva) escreve conversas de exemplo no jeito dela, a
   partir só da personalidade e de temas genéricos: nenhuma conversa sua vai para a nuvem.
3. **Treino** (`treinar_lora.py`, no `.venv_treino`, com a placa liberada: os modelos do Ollama saem dela antes): QLoRA
   em 4 bits no Qwen2.5-14B-Instruct (os pesos originais, ~30 GB, baixados uma vez com `--baixar`), a mesma base do
   `qwen2.5:14b`; a perda no conjunto separado é medida antes e
   depois.
4. **Conferência e troca**: o adaptador é somado aos pesos originais (o Ollama 0.35 não aceita mais ADAPTER) e o
   modelo inteiro vira um GGUF no formato do `qwen2.5:14b` (q4_K_M, com o vocabulário e o modelo de conversa dele) e
   entra no Ollama como `beatriz-ajustada-AAAAMMDD` (`instalar_no_ollama`), e só passa a ser usado se a perda caiu pelo menos
   AJUSTE_MELHORA_MINIMA e ele passou nas perguntas de conferência (`PERGUNTAS_DE_CONFERENCIA`: português, sem
   repetição, sem resposta vazia, e o JSON das tarefas continua válido). Promovido, ele responde por todo pedido que iria
   ao modelo base (`modelo_em_uso`, no `llm_clients`): um modelo só na placa. "Volta pro modelo original" desfaz.

`RotinaDaMadrugada` (thread no processo principal): entre AJUSTE_HORA_INICIO e AJUSTE_HORA_FIM, com a Beatriz parada há
30 min, uma vez por noite: a prevenção do código (`melhoria_codigo`: os erros que se repetiram no log viram uma correção
testada, que espera o seu "aplica") e, com exemplos novos suficientes, o ajuste fino. De manhã ela conta o que fez.
Tudo em `banco_memoria/evolucao/` (fora do git). À mão: `python ajuste_fino.py --dados`, `--treinar` ou `--teste`.
"""
import json
import logging
import os
import random
import re
import shutil
import subprocess
import threading
import time
import unicodedata
from datetime import datetime

logger = logging.getLogger(__name__)

PASTA_PROJETO = os.path.dirname(os.path.abspath(__file__))
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Os pesos originais do qwen2.5:14b (bf16, ~30 GB): o treino os carrega em 4 bits e a junção usa os de 16 bits.
BASE_HF = "Qwen/Qwen2.5-14B-Instruct"
SEGUNDOS_CORRECAO = 180  # uma correção vale para a troca anterior se veio até 3 min depois dela
SEGUNDOS_REFEITO = 300  # e o pedido refeito é o que veio até 5 min depois da correção
TREINOS_GUARDADOS = 3  # pastas de treino que ficam no disco (fora a do modelo em uso)
RE_POSITIVO = re.compile(r"^(?:valeu|obrigad[oa]|perfeito|isso mesmo|exato|boa|show|otimo|adorei|massa|top)\b")
RE_CORRECAO = re.compile(r"^(?:nao era isso|nao e isso|nao foi isso|entendeu errado|voce entendeu errado|errado|errou)\b")
TEMAS_SINTETICOS = [
    "estudar para uma prova de direito e procrastinar", "organizar a agenda da semana", "papo leve e curiosidades",
    "pedir uma piada", "um dia ruim e frustração", "escolher entre duas opções", "dúvida rápida de conhecimento geral",
    "cansaço depois de muito trabalho", "pedir para ser mais direta", "comemorar uma conquista",
]
PERGUNTAS_DE_CONFERENCIA = [
    ("conversa", "tudo bem com você?"),
    ("conversa", "vou só arrumar minhas pastas antes de estudar"),
    ("conversa", "me conta uma curiosidade rápida"),
    ("suporte", "deu tudo errado no meu trabalho hoje"),
    ("operacional", "tenho três provas essa semana, por onde começo?"),
    ("json", 'Classifique o pedido "qual a previsão do tempo?" em uma destas categorias: simples, pesquisa, ferramentas. '
             'Responda só com JSON: {"categoria": "..."}'),
]


def _sem_acentos(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in texto if not unicodedata.combining(c)).lower().strip()


def _config(nome: str, padrao):
    try:
        import config

        return getattr(config, nome, padrao)
    except Exception:
        return padrao


def pasta() -> str:
    return _config("PASTA_AJUSTE_FINO", os.path.join(PASTA_PROJETO, "banco_memoria", "evolucao", "ajuste"))


def _ler_json(caminho: str, padrao):
    try:
        with open(caminho, encoding="utf-8") as entrada:
            return json.load(entrada)
    except (OSError, ValueError):
        return padrao


def _gravar_json(caminho: str, dados) -> None:
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    temporario = f"{caminho}.{os.getpid()}.tmp"
    with open(temporario, "w", encoding="utf-8") as saida:
        json.dump(dados, saida, ensure_ascii=False, indent=1)
    os.replace(temporario, caminho)


def estado() -> dict:
    return _ler_json(os.path.join(pasta(), "estado.json"), {"treinos": [], "treinar": True})


def gravar_estado(dados: dict) -> None:
    _gravar_json(os.path.join(pasta(), "estado.json"), dados)


# ------------------------------------------------------------------ o modelo em uso
_PROMOVIDO = {"marca": None, "modelo": None}


def modelo_em_uso(pedido: str) -> str:
    """O modelo a usar no lugar de `pedido`: o ajustado, se um foi promovido e o pedido é o modelo base dele (um só
    modelo na placa: trocar de modelo a cada chamada custaria 8 a 10 s de recarga)."""
    arquivo = os.path.join(pasta(), "estado.json")
    try:
        marca = (arquivo, os.stat(arquivo).st_mtime_ns)
    except OSError:
        return pedido
    if marca != _PROMOVIDO["marca"]:
        promovido = (_ler_json(arquivo, {}) or {}).get("promovido") or {}
        _PROMOVIDO.update(marca=marca, modelo=promovido)
    promovido = _PROMOVIDO["modelo"] or {}
    return promovido.get("modelo") if promovido.get("modelo") and pedido == promovido.get("base") else pedido


# ------------------------------------------------------------------ dados
def registrar_correcao(pergunta: str, resposta: str, quando: float | None = None) -> None:
    """"Não era isso" logo depois de uma resposta: a troca errada fica marcada (e o pedido refeito em seguida vira
    o exemplo certo para a pergunta original)."""
    if not pergunta:
        return
    os.makedirs(pasta(), exist_ok=True)
    with open(os.path.join(pasta(), "correcoes.jsonl"), "a", encoding="utf-8") as saida:
        saida.write(json.dumps({"pergunta": pergunta, "resposta": resposta, "quando": quando or time.time()},
                               ensure_ascii=False) + "\n")


def ler_correcoes() -> list[dict]:
    try:
        with open(os.path.join(pasta(), "correcoes.jsonl"), encoding="utf-8") as entrada:
            return [json.loads(linha) for linha in entrada if linha.strip()]
    except (OSError, ValueError):
        return []


def ler_historico(arquivo: str | None = None) -> list[dict]:
    if arquivo is None:
        try:
            import memory

            arquivo = memory.ARQUIVO_HISTORICO_CONVERSAS
        except Exception:
            return []
    trocas = []
    try:
        with open(arquivo, encoding="utf-8") as entrada:
            for linha in entrada:
                try:
                    troca = json.loads(linha)
                except ValueError:
                    continue
                if troca.get("pergunta") and troca.get("resposta"):
                    trocas.append(troca)
    except OSError:
        return []
    return sorted(trocas, key=lambda t: float(t.get("timestamp") or 0))


def _boa_para_treino(troca: dict) -> bool:
    pergunta, resposta = str(troca["pergunta"]).strip(), str(troca["resposta"]).strip()
    return (3 <= len(pergunta) <= 600 and 8 <= len(resposta) <= 1200 and "[Fonte:" not in resposta
            and not resposta.startswith(("Não consegui", "Erro", "Desculpe, ocorreu")))


def _mensagens(sistema: str, pergunta: str, resposta: str) -> dict:
    return {"messages": [{"role": "system", "content": sistema}, {"role": "user", "content": pergunta.strip()},
                         {"role": "assistant", "content": resposta.strip()}]}


def montar_dados(sistema: str, trocas: list[dict], correcoes: list[dict], parte_avaliacao: float = 0.15,
                 semente: int = 7) -> tuple[list[dict], list[dict], dict]:
    """(treino, avaliação, contas) a partir do histórico e das correções. Só fica o que deu certo."""
    erradas, refeitos = set(), []
    for correcao in correcoes:
        quando = float(correcao.get("quando") or 0)
        anteriores = [i for i, t in enumerate(trocas) if 0 <= quando - float(t.get("timestamp") or 0) <= SEGUNDOS_CORRECAO
                      and t["pergunta"] == correcao["pergunta"]]
        if anteriores:
            erradas.add(anteriores[-1])
        depois = next((t for t in trocas if 0 < float(t.get("timestamp") or 0) - quando <= SEGUNDOS_REFEITO
                       and not RE_CORRECAO.match(_sem_acentos(t["pergunta"]))), None)
        if depois and _boa_para_treino(depois):
            refeitos.append(_mensagens(sistema, correcao["pergunta"], depois["resposta"]))
    # Uma troca seguida de "não era isso" também é errada, mesmo sem o registro (versões antigas não registravam).
    for i, troca in enumerate(trocas[1:], start=1):
        if RE_CORRECAO.match(_sem_acentos(troca["pergunta"])):
            erradas.add(i - 1)
    exemplos, elogiadas = [], 0
    for i, troca in enumerate(trocas):
        if i in erradas or RE_CORRECAO.match(_sem_acentos(troca["pergunta"])) or not _boa_para_treino(troca):
            continue
        exemplo = _mensagens(sistema, troca["pergunta"], troca["resposta"])
        exemplos.append(exemplo)
        seguinte = trocas[i + 1] if i + 1 < len(trocas) else None
        if seguinte and RE_POSITIVO.match(_sem_acentos(seguinte["pergunta"])):
            exemplos.append(exemplo)  # elogiada: vale em dobro
            elogiadas += 1
    exemplos += refeitos
    aleatorio = random.Random(semente)
    aleatorio.shuffle(exemplos)
    corte = max(1, int(len(exemplos) * parte_avaliacao)) if len(exemplos) > 4 else 0
    contas = {"trocas": len(trocas), "erradas": len(erradas), "elogiadas": elogiadas, "corrigidas": len(refeitos),
              "treino": len(exemplos) - corte, "avaliacao": corte}
    return exemplos[corte:], exemplos[:corte], contas


INSTRUCOES_SINTETICAS = """Você escreve dados de treino para a Beatriz, uma assistente pessoal em português do Brasil. A personalidade dela está abaixo. Escreva {quantidade} conversas curtas e variadas (uma fala da pessoa e a resposta dela, falada em voz alta: até 3 frases, sem markdown, sem emojis), sobre o tema "{tema}". A resposta tem de mostrar o jeito dela com fidelidade. Não invente fatos verificáveis (datas, preços, notícias) nem dados pessoais.
Responda só com JSON: {{"conversas": [{{"pessoa": "...", "beatriz": "..."}}]}}

PERSONALIDADE:
{personalidade}"""


def gerar_sinteticos(sistema_publico: str, quantidade_por_tema: int = 6, temas=TEMAS_SINTETICOS, ia=None,
                     sistema_do_treino: str | None = None) -> list[dict]:
    """Conversas de exemplo escritas por uma IA da nuvem só a partir da personalidade (nada das suas conversas). A nuvem
    vê só `sistema_publico` (a personalidade e as camadas de estilo); no treino, os exemplos levam `sistema_do_treino`,
    o prompt que o modelo local recebe de verdade."""
    if ia is None:
        from llm_nuvem import claude, gemini

        ia = next((cliente for cliente in (claude, gemini) if cliente.disponivel()), None)
        if ia is None:
            return []
    exemplos = []
    for tema in temas:
        try:
            bruto = ia.chat(INSTRUCOES_SINTETICAS.format(quantidade=quantidade_por_tema, tema=tema,
                                                         personalidade=sistema_publico), formato="json")
            conversas = (json.loads(bruto) if isinstance(bruto, str) else bruto).get("conversas") or []
        except Exception as exc:
            logger.warning("Sintéticos sobre %s: %s", tema, exc)
            continue
        for conversa in conversas[:quantidade_por_tema]:
            pessoa, beatriz = str(conversa.get("pessoa") or "").strip(), str(conversa.get("beatriz") or "").strip()
            if pessoa and beatriz:
                exemplos.append(_mensagens(sistema_do_treino or sistema_publico, pessoa, beatriz))
    return exemplos


def gravar_jsonl(exemplos: list[dict], caminho: str) -> str:
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as saida:
        for exemplo in exemplos:
            saida.write(json.dumps(exemplo, ensure_ascii=False) + "\n")
    return caminho


# ------------------------------------------------------------------ conferência do modelo ajustado
def problemas_da_resposta(tipo: str, resposta: str) -> list[str]:
    """O que reprova uma resposta da conferência: vazia, longa demais, em loop, fora do português, JSON quebrado."""
    texto = (resposta or "").strip()
    problemas = []
    if not texto:
        return ["resposta vazia"]
    if tipo == "json":
        try:
            dados = json.loads(re.sub(r"^```(?:json)?|```$", "", texto).strip())
            if not isinstance(dados, dict) or dados.get("categoria") not in ("simples", "pesquisa", "ferramentas"):
                problemas.append("JSON sem a categoria certa")
        except ValueError:
            problemas.append("JSON inválido")
        return problemas
    if len(texto) > 1500:
        problemas.append("longa demais")
    frases = [f.strip().lower() for f in re.split(r"[.!?]+", texto) if len(f.strip()) > 12]
    if len(frases) != len(set(frases)):
        problemas.append("repete frases")
    palavras = re.findall(r"[a-zà-ú]+", texto.lower())
    comuns = {"de", "que", "e", "o", "a", "os", "as", "um", "uma", "para", "pra", "com", "não", "você", "é", "do", "da",
              "em", "no", "na", "se", "por", "mais", "como", "foi", "sua", "seu", "eu", "isso", "está", "tem", "me", "ao"}
    # Só com 12 palavras ou mais: "Diagnóstico concluído. Como foi sua manhã?" é português e quase não tem palavra comum.
    if len(palavras) >= 12 and sum(p in comuns for p in palavras) / len(palavras) < 0.08:
        problemas.append("não parece português")
    if re.search(r"[一-鿿]", texto):
        problemas.append("tem caracteres chineses")
    return problemas


# ------------------------------------------------------------------ o treinador
class ErroDoAjuste(RuntimeError):
    pass


def _executar(*args, **kwargs):
    """Os subprocessos do treinador (o conftest troca esta função: nenhum teste treina nem cria modelo de verdade)."""
    return subprocess.run(*args, **kwargs)


def _cliente_ollama():
    import ollama

    return ollama.Client(host=_config("OLLAMA_HOST", "http://localhost:11434"))


class Treinador:
    """Os passos pesados (treino, junção, Ollama), cada um num subprocesso. `executar` = subprocess.run."""

    def __init__(self, executar=None, ollama=None, base_ollama: str | None = None, python_treino: str | None = None,
                 base_hf: str | None = None):
        self._executar = executar
        self._ollama = ollama
        self.base_ollama = base_ollama or _config("OLLAMA_MODEL", "qwen2.5:14b")
        self.base_hf = base_hf or _config("AJUSTE_BASE_HF", "") or BASE_HF
        self.python_treino = python_treino or os.path.join(PASTA_PROJETO, ".venv_treino", "Scripts", "python.exe")

    def executar(self, *args, **kwargs):
        return (self._executar or _executar)(*args, **kwargs)

    @property
    def ollama(self):
        if self._ollama is None:
            self._ollama = _cliente_ollama()
        return self._ollama

    def pasta_da_base(self) -> str:
        """A pasta do modelo base no cache do Hugging Face (baixado uma vez, ~30 GB), ou "" se ainda não baixou."""
        cache = os.environ.get("HF_HUB_CACHE") or os.path.join(os.environ.get("HF_HOME") or os.path.join(
            os.path.expanduser("~"), ".cache", "huggingface"), "hub")
        fotos = os.path.join(cache, "models--" + self.base_hf.replace("/", "--"), "snapshots")
        candidatas = [os.path.join(fotos, d) for d in (os.listdir(fotos) if os.path.isdir(fotos) else [])]
        prontas = [c for c in candidatas if self._base_completa(c)]
        return max(prontas, key=os.path.getmtime) if prontas else ""

    @staticmethod
    def _base_completa(foto: str) -> bool:
        """O índice chega primeiro: a base só está pronta com todas as partes que ele lista já baixadas (um modelo
        pequeno tem um `model.safetensors` só, sem índice)."""
        if not os.path.exists(os.path.join(foto, "config.json")):
            return False
        try:
            with open(os.path.join(foto, "model.safetensors.index.json"), encoding="utf-8") as arquivo:
                partes = set(json.load(arquivo).get("weight_map", {}).values())
        except FileNotFoundError:
            partes = {"model.safetensors"}
        except (OSError, ValueError):
            return False
        return bool(partes) and all(os.path.exists(os.path.join(foto, parte)) for parte in partes)

    def baixar_base(self) -> str:
        """Baixa os pesos originais do Hugging Face (só os arquivos do modelo e do tokenizador) para o cache."""
        processo = self.executar(
            [self.python_treino, "-c", "import sys; from huggingface_hub import snapshot_download; "
             "print(snapshot_download(sys.argv[1], allow_patterns=['*.json', '*.safetensors', '*.txt']))", self.base_hf],
            capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=SEM_JANELA)
        if processo.returncode != 0 or not self.pasta_da_base():
            raise ErroDoAjuste(f"não baixei o modelo base: {(processo.stderr or '').strip()[-200:]}")
        return self.pasta_da_base()

    def o_que_falta(self) -> list[str]:
        falta = []
        if not os.path.exists(self.python_treino):
            falta.append("o ambiente de treino (.venv_treino)")
        if not self.pasta_da_base():
            falta.append(f"o modelo base ({self.base_hf}, ~30 GB: python ajuste_fino.py --baixar)")
        return falta

    def liberar_placa(self) -> list[str]:
        """Tira os modelos do Ollama da placa (eles voltam sozinhos no próximo pedido, em ~8 s)."""
        tirados = []
        try:
            carregados = self.ollama.ps().get("models", [])
        except Exception as exc:
            logger.warning("Ollama: não vi os modelos carregados (%s)", exc)
            return tirados
        for modelo in carregados:
            nome = modelo.get("model") or modelo.get("name")
            try:
                self.ollama.generate(model=nome, prompt="", keep_alive=0)
                tirados.append(nome)
            except Exception as exc:
                logger.warning("Ollama: não tirei %s da placa (%s)", nome, exc)
        return tirados

    def _rodar_script(self, argumentos: list[str], log: str, tempo: float, o_que: str):
        try:
            processo = self.executar([self.python_treino, os.path.join(PASTA_PROJETO, "treinar_lora.py"), *argumentos],
                                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=tempo,
                                     creationflags=SEM_JANELA, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        except subprocess.TimeoutExpired as exc:
            raise ErroDoAjuste(f"{o_que} passou do tempo máximo") from exc
        with open(log, "w", encoding="utf-8") as arquivo:
            arquivo.write((processo.stdout or "") + "\n" + (processo.stderr or ""))
        if processo.returncode != 0:
            ultima = [l for l in (processo.stderr or processo.stdout or "").strip().splitlines() if l.strip()][-1:] or ["?"]
            raise ErroDoAjuste(f"{o_que} falhou: {ultima[0][:200]}")
        return processo

    def treinar(self, dados: str, avaliacao: str, saida: str, max_passos: int = -1) -> dict:
        self._rodar_script(["--dados", dados, "--avaliacao", avaliacao, "--saida", saida,
                            "--base", self.pasta_da_base() or self.base_hf, "--max-passos", str(max_passos)],
                           os.path.join(saida, "treino.log"), float(_config("AJUSTE_TEMPO_MAXIMO", 4 * 3600)), "o treino")
        resultado = _ler_json(os.path.join(saida, "resultado.json"), None)
        if not resultado:
            raise ErroDoAjuste("o treino falhou: não gravou o resultado")
        return resultado

    def molde(self) -> str:
        """O arquivo GGUF do modelo base no Ollama (o blob do qwen2.5:14b): a junção copia o formato dele."""
        try:
            base = self.ollama.show(self.base_ollama)
            texto = getattr(base, "modelfile", None) or base.get("modelfile") or ""
        except Exception as exc:
            raise ErroDoAjuste(f"não li o modelo base {self.base_ollama} no Ollama ({exc})") from exc
        achado = re.search(r"^FROM (.+)$", texto, re.M)
        if not achado or not os.path.isfile(achado.group(1).strip()):
            raise ErroDoAjuste(f"não achei o arquivo do {self.base_ollama} no Ollama")
        return achado.group(1).strip()

    def juntar(self, saida: str) -> str:
        """O adaptador somado aos pesos originais de 16 bits, gravado num GGUF no formato do modelo base do Ollama
        (q4_K_M, o vocabulário e o modelo de conversa dele): `saida/modelo.gguf` (~9 GB)."""
        gguf = os.path.join(saida, "modelo.gguf")
        self._rodar_script(["--juntar", "--base", self.pasta_da_base(), "--adaptador", os.path.join(saida, "adaptador"),
                            "--molde", self.molde(), "--destino", gguf], os.path.join(saida, "juncao.log"), 2 * 3600,
                           "a junção do adaptador")
        return gguf

    def instalar_no_ollama(self, nome: str, saida: str) -> None:
        """O Ollama 0.35 não aceita mais adaptador LoRA por cima de um modelo ("LoRA adapters are no longer supported")
        nem quantiza um GGUF na importação: o adaptador é somado aos pesos originais, quantizado como o modelo base
        (`treinar_lora.escrever_gguf`) e o GGUF inteiro entra no Ollama com o modelo de conversa e as paradas do base.
        O GGUF da pasta do treino é apagado no fim, dando certo ou não (o Ollama guarda a cópia dele)."""
        gguf = self.juntar(saida)
        try:
            self.criar_no_ollama(nome, gguf)
        finally:
            try:
                os.remove(gguf)
            except OSError:
                pass

    def criar_no_ollama(self, nome: str, pesos: str) -> None:
        linhas = [f"FROM {pesos.replace(os.sep, '/')}"]
        try:
            base = self.ollama.show(self.base_ollama)
            modelo_de_conversa = getattr(base, "template", None) or base.get("template")
            parametros = getattr(base, "parameters", None) or base.get("parameters") or ""
        except Exception as exc:
            raise ErroDoAjuste(f"não li o modelo base {self.base_ollama} no Ollama ({exc})") from exc
        if modelo_de_conversa:
            linhas.append(f'TEMPLATE """{modelo_de_conversa}"""')
        linhas += [f"PARAMETER {linha.strip()}" for linha in parametros.splitlines() if linha.strip()]
        modelfile = os.path.join(os.path.dirname(pesos), "Modelfile")  # na pasta do treino
        with open(modelfile, "w", encoding="utf-8") as saida:
            saida.write("\n".join(linhas) + "\n")
        processo = self.executar(["ollama", "create", nome, "-f", modelfile], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=3 * 3600, creationflags=SEM_JANELA)
        if processo.returncode != 0:
            saida_ollama = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", processo.stderr or processo.stdout or "").strip()
            raise ErroDoAjuste(f"o Ollama não criou o modelo: {saida_ollama[-200:]}")

    def conferir(self, nome: str, sistema: str) -> dict:
        """As perguntas de conferência no modelo ajustado: {"ok", "respostas", "problemas"}."""
        respostas, problemas = [], []
        num_ctx = int(_config("OLLAMA_NUM_CTX", 8192))
        for tipo, pergunta in PERGUNTAS_DE_CONFERENCIA:
            try:
                resposta = self.ollama.chat(model=nome, messages=[{"role": "system", "content": sistema},
                                                                  {"role": "user", "content": pergunta}],
                                            options={"num_ctx": num_ctx}, format="json" if tipo == "json" else None)
                texto = resposta["message"]["content"]
            except Exception as exc:
                texto = ""
                problemas.append(f"{pergunta[:40]}: {exc}")
            respostas.append({"tipo": tipo, "pergunta": pergunta, "resposta": texto})
            problemas += [f"{pergunta[:40]}: {p}" for p in problemas_da_resposta(tipo, texto)]
        return {"ok": not problemas, "respostas": respostas, "problemas": problemas}


# ------------------------------------------------------------------ o ciclo inteiro
def novos_exemplos_desde(ultimo: float, trocas: list[dict]) -> int:
    return sum(1 for t in trocas if float(t.get("timestamp") or 0) > ultimo and _boa_para_treino(t))


def ciclo_de_ajuste(treinador: Treinador | None = None, forcar: bool = False, max_passos: int = -1,
                    sinteticos: bool = True, relogio=time.time, teste: bool = False) -> dict:
    """Uma rodada completa: dados → (sintéticos) → treino → GGUF → Ollama → conferência → promoção.
    Devolve o relatório ({"feito", "mensagem", ...}) e o guarda no estado. Com `teste`, faz tudo mas não troca o
    modelo, não conta como treino (a madrugada continua igual) e apaga do Ollama o modelo de teste."""
    import personalidade

    treinador = treinador or Treinador()
    dados_estado = estado()
    falta = treinador.o_que_falta()
    if falta:
        return _relatar(dados_estado, {"feito": False, "mensagem": f"Ajuste fino adiado: falta {', '.join(falta)}."},
                        guardar_relatorio=not teste)
    trocas = ler_historico()
    novos = novos_exemplos_desde(float(dados_estado.get("ultimo_ate") or 0), trocas)
    minimo = int(_config("AJUSTE_MINIMO_NOVOS", 30))
    if not forcar and novos < minimo:
        return _relatar(dados_estado, {"feito": False, "mensagem": f"Ajuste fino adiado: {novos} trocas novas (mínimo {minimo})."},
                        guardar_relatorio=False)
    dia = datetime.fromtimestamp(relogio()).strftime("%Y%m%d-%H%M")
    saida = os.path.join(pasta(), "treinos", dia)
    sistema_local = personalidade.sistema(local=True)
    treino, avaliacao, contas = montar_dados(sistema_local, trocas, ler_correcoes())
    if sinteticos and _config("AJUSTE_SINTETICOS", True):
        extras = gerar_sinteticos(personalidade.sistema(local=False), sistema_do_treino=sistema_local)
        treino += extras
        contas["sinteticos"] = len(extras)
    if len(treino) < 10 or not avaliacao:
        return _relatar(dados_estado, {"feito": False, "mensagem": "Ajuste fino adiado: poucos exemplos bons ainda."},
                        guardar_relatorio=not teste)
    caminho_treino = gravar_jsonl(treino, os.path.join(saida, "treino.jsonl"))
    caminho_avaliacao = gravar_jsonl(avaliacao, os.path.join(saida, "avaliacao.jsonl"))
    # Um nome por treino (com a hora): com um nome só por dia, um segundo treino reprovado no mesmo dia apagaria do
    # Ollama o modelo que está em uso.
    nome = f"beatriz-{'teste' if teste else 'ajustada'}-{dia}"
    try:
        treinador.liberar_placa()
        resultado = treinador.treinar(caminho_treino, caminho_avaliacao, saida, max_passos=max_passos)
        treinador.instalar_no_ollama(nome, saida)
        conferencia = treinador.conferir(nome, sistema_local)
    except ErroDoAjuste as exc:
        if teste:
            _apagar_do_ollama(treinador, nome)
            return _relatar(dados_estado, {"feito": False, "mensagem": f"Teste do ajuste fino falhou: {exc}.",
                                           "contas": contas, "pasta": saida}, guardar_relatorio=False)
        return _relatar(dados_estado, {"feito": False, "mensagem": f"O ajuste fino desta noite não deu certo: {exc}.",
                                       "contas": contas})
    _gravar_json(os.path.join(saida, "conferencia.json"), conferencia)
    minima = float(_config("AJUSTE_MELHORA_MINIMA", 0.02))
    melhora = float(resultado.get("melhora") or 0)
    relatorio = {"feito": True, "modelo": nome, "contas": contas, "resultado": resultado, "conferencia": conferencia["ok"],
                 "problemas": conferencia["problemas"], "pasta": saida}
    if teste:
        _apagar_do_ollama(treinador, nome)
        relatorio["mensagem"] = (f"Teste do ajuste fino: {resultado['passos']} passos com {resultado['exemplos']} exemplos, "
                                 f"{resultado['memoria_gb']} GB na placa, perda {resultado['perda_antes']} → "
                                 f"{resultado['perda_depois']}; conferência {'ok' if conferencia['ok'] else 'com problemas'}.")
        dados_estado["ultimo_teste"] = relatorio
        gravar_estado(dados_estado)
        _limpar_treinos(dados_estado)
        return relatorio
    dados_estado["ultimo_ate"] = max((float(t.get("timestamp") or 0) for t in trocas), default=0)
    dados_estado["ultimo_treino"] = relogio()
    velhos = []
    if melhora >= minima and conferencia["ok"]:
        # Um ajustado por vez no Ollama (~9 GB cada): o novo substitui o anterior; o original fica sempre.
        velhos = [m for m in ((dados_estado.get(c) or {}).get("modelo") for c in ("promovido", "anterior")) if m and m != nome]
        dados_estado["anterior"] = None
        dados_estado["promovido"] = {"modelo": nome, "base": treinador.base_ollama, "quando": relogio(), "melhora": melhora,
                                     "pasta": saida}
        relatorio["mensagem"] = (f"Esta madrugada treinei o meu modelo com {resultado['exemplos']} exemplos: a perda caiu "
                                 f"{melhora:.0%} e ele passou na conferência, então já estou usando. Se estranhar algo, "
                                 "diga \"volta pro modelo original\".")
    else:
        motivo = (f"a perda caiu só {melhora:.1%}" if melhora < minima
                  else "ele falhou na conferência (" + "; ".join(conferencia["problemas"][:2]) + ")")
        relatorio["mensagem"] = f"Esta madrugada treinei um modelo novo, mas não troquei: {motivo}."
        velhos = [nome]
    relatorio = _relatar(dados_estado, relatorio)  # o estado novo primeiro: só então os modelos velhos saem do Ollama
    for velho in velhos:
        _apagar_do_ollama(treinador, velho)
    _limpar_treinos(dados_estado)
    return relatorio


def _limpar_treinos(dados_estado: dict, manter: int = TREINOS_GUARDADOS) -> None:
    """Só as últimas pastas de treino ficam (cada uma tem o adaptador, ~300 MB, e os dados do treino), além da do
    modelo em uso."""
    raiz = os.path.join(pasta(), "treinos")
    em_uso = {os.path.normcase(os.path.abspath((dados_estado.get(c) or {}).get("pasta") or ""))
              for c in ("promovido", "anterior")}
    try:
        pastas = sorted(n for n in os.listdir(raiz) if os.path.isdir(os.path.join(raiz, n)))
    except OSError:
        return
    for nome in pastas[:-manter] if manter else pastas:
        caminho = os.path.join(raiz, nome)
        if os.path.normcase(os.path.abspath(caminho)) not in em_uso:
            shutil.rmtree(caminho, ignore_errors=True)


def _apagar_do_ollama(treinador: Treinador, nome: str) -> None:
    """Tira da placa (senão ele fica lá até o keep_alive vencer, mesmo apagado) e apaga do Ollama."""
    try:
        treinador.ollama.generate(model=nome, prompt="", keep_alive=0)
    except Exception as exc:
        logger.debug("Ollama: não tirei %s da placa (%s)", nome, exc)
    try:
        treinador.ollama.delete(nome)
    except Exception as exc:
        logger.debug("Ollama: não apaguei %s (%s)", nome, exc)


def _relatar(dados_estado: dict, relatorio: dict, guardar_relatorio: bool = True) -> dict:
    relatorio["quando"] = time.time()
    if guardar_relatorio:
        dados_estado["relatorio"] = relatorio
        dados_estado["relatorio_contado"] = False
        dados_estado.setdefault("treinos", []).append({k: relatorio.get(k) for k in ("quando", "feito", "modelo", "mensagem")})
        dados_estado["treinos"] = dados_estado["treinos"][-30:]
    gravar_estado(dados_estado)
    logger.info("Ajuste fino: %s", relatorio["mensagem"])
    return relatorio


def voltar_ao_original() -> str:
    dados = estado()
    if not dados.get("promovido"):
        return "Já estou no modelo original."
    dados["anterior"], dados["promovido"] = dados["promovido"], None
    gravar_estado(dados)
    return f"Pronto: voltei ao {dados['anterior']['base']}. O ajustado continua guardado; diga \"usa o modelo ajustado\" para voltar."


def modelo_sumiu(nome: str) -> None:
    """O Ollama não tem mais o modelo ajustado em uso (apagado à mão, por exemplo): volta ao original, para ela não
    ficar sem responder. Chamada pelo `llm_clients` quando o Ollama responde 404 para ele."""
    dados = estado()
    if (dados.get("promovido") or {}).get("modelo") != nome:
        return
    dados["promovido"] = None
    gravar_estado(dados)
    logger.warning("Ajuste fino: o Ollama não tem mais o %s; voltei ao modelo original.", nome)


def usar_o_ajustado() -> str:
    dados = estado()
    if dados.get("promovido"):
        return f"Já estou usando o {dados['promovido']['modelo']}."
    if not dados.get("anterior"):
        return "Ainda não tenho um modelo ajustado aprovado."
    dados["promovido"], dados["anterior"] = dados["anterior"], None
    gravar_estado(dados)
    return f"Pronto: voltei a usar o {dados['promovido']['modelo']}."


# ------------------------------------------------------------------ a madrugada
class RotinaDaMadrugada:
    """Uma vez por noite, com a Beatriz parada: a prevenção do código e, se for a hora, o ajuste fino."""

    def __init__(self, melhoria=None, treinador: Treinador | None = None, ociosa_desde=None, relogio=time.time,
                 caminho_log: str | None = None, ciclo=ciclo_de_ajuste):
        self.melhoria = melhoria  # o AgenteMelhoria do processo principal
        self.treinador = treinador or Treinador()
        self.ociosa_desde = ociosa_desde or (lambda: 0.0)  # quando foi a última conversa (time.time())
        self.relogio = relogio
        self.caminho_log = caminho_log or os.path.join(PASTA_PROJETO, "beatriz.log")
        self.ciclo = ciclo

    def hora_de_trabalhar(self) -> bool:
        agora = datetime.fromtimestamp(self.relogio())
        inicio, fim = int(_config("AJUSTE_HORA_INICIO", 2)), int(_config("AJUSTE_HORA_FIM", 5))
        if not inicio <= agora.hour < fim:
            return False
        if estado().get("noite") == agora.strftime("%Y-%m-%d"):
            return False
        return self.relogio() - self.ociosa_desde() >= 30 * 60

    def noite(self) -> dict:
        dados = estado()
        dados["noite"] = datetime.fromtimestamp(self.relogio()).strftime("%Y-%m-%d")
        gravar_estado(dados)
        feito = {"prevencao": self.prevencao()}
        if _config("AJUSTE_FINO", True) and estado().get("treinar", True):
            intervalo = float(_config("AJUSTE_DIAS_ENTRE_TREINOS", 7)) * 86400
            forcar = bool(estado().get("forcar"))
            if forcar or self.relogio() - float(estado().get("ultimo_treino") or 0) >= intervalo:
                feito["ajuste"] = self.ciclo(self.treinador, forcar=forcar)
                if forcar:
                    dados = estado()
                    dados["forcar"] = False
                    gravar_estado(dados)
        return feito

    def prevencao(self) -> str:
        """O erro mais repetido do log (desde a última noite) vira uma correção preparada e testada."""
        if self.melhoria is None:
            return ""
        from melhoria_codigo import erros_recorrentes, pedido_de_correcao

        dados = estado()
        erros, lido = erros_recorrentes(self.caminho_log, int(dados.get("log_lido") or 0))
        tentados = set(dados.get("erros_tentados") or [])
        novo = next((erro for erro in erros if erro["chave"] not in tentados), None)
        dados["log_lido"] = lido
        if novo:
            dados["erros_tentados"] = sorted(tentados | {novo["chave"]})[-200:]
        gravar_estado(dados)
        if not novo:
            return ""
        return self.melhoria.pedir(pedido_de_correcao(novo), origem="noite")

    def iniciar(self) -> threading.Thread:
        def laco():
            while True:
                time.sleep(600)
                try:
                    if self.hora_de_trabalhar():
                        self.noite()
                except Exception:
                    logger.exception("Falha na rotina da madrugada")

        linha = threading.Thread(target=laco, name="rotina-da-madrugada", daemon=True)
        linha.start()
        return linha


def relatorio_da_manha() -> str:
    """O que o ajuste fino fez esta noite, uma vez só (para a saudação de abertura)."""
    dados = estado()
    relatorio = dados.get("relatorio") or {}
    if not relatorio or dados.get("relatorio_contado") or time.time() - float(relatorio.get("quando") or 0) > 20 * 3600:
        return ""
    dados["relatorio_contado"] = True
    gravar_estado(dados)
    return relatorio.get("mensagem", "")


# ------------------------------------------------------------------ pedidos falados
RE_SITUACAO = re.compile(r"\b(?:como (?:esta|vai|anda) (?:o )?(?:seu )?(?:treino|ajuste fino|aprendizado do modelo)|"
                         r"(?:qual|que) modelo (?:voce )?(?:esta|ta) usando|o que (?:voce )?treinou)\b")
RE_ORIGINAL = re.compile(r"\b(?:volta|voltar|usa|use)\w* (?:pro|para o|o) modelo original\b")
RE_AJUSTADO = re.compile(r"\b(?:usa|use|volta a usar|voltar a usar)\w* (?:o )?modelo ajustado\b")
# "Me treina para a prova" é estudo: aqui só o modelo dela ou a madrugada.
RE_TREINA = re.compile(r"\b(?:(?:pode )?trein\w* (?:o )?(?:seu )?modelo|fa(?:z|ca|zer) o ajuste fino|"
                       r"(?:treina|treine|pode treinar) (?:hoje a noite|esta noite|de madrugada|hoje de madrugada))\b")
RE_NAO_TREINA = re.compile(r"\b(?:para|pare|parar) de treinar\b|\bnao (?:treina|treine) mais\b")


def responder(texto: str) -> str | None:
    """"Como está o seu treino?", "volta pro modelo original", "usa o modelo ajustado", "treina hoje à noite",
    "para de treinar". None se não for sobre isso."""
    t = re.sub(r"^(?:beatriz|beatris|biatriz)\s*[,!.]?\s*", "", _sem_acentos(texto)).strip(" ?!.")
    if RE_ORIGINAL.search(t):
        return voltar_ao_original()
    if RE_AJUSTADO.search(t):
        return usar_o_ajustado()
    if RE_NAO_TREINA.search(t):
        dados = estado()
        dados["treinar"] = False
        gravar_estado(dados)
        return "Ajuste fino da madrugada desligado. A prevenção do código continua."
    if RE_TREINA.search(t):
        dados = estado()
        dados.update(treinar=True, forcar=True)
        gravar_estado(dados)
        falta = Treinador().o_que_falta()
        aviso = f" Antes, ainda falta {', '.join(falta)}." if falta else ""
        return f"Combinado: nesta madrugada eu treino, mesmo com poucos exemplos novos.{aviso}"
    if RE_SITUACAO.search(t):
        dados = estado()
        promovido = dados.get("promovido")
        uso = f"Estou usando o {promovido['modelo']}." if promovido else "Estou usando o modelo original."
        ultimo = (dados.get("relatorio") or {}).get("mensagem")
        return f"{uso} {('O último ajuste: ' + ultimo) if ultimo else 'Ainda não fiz nenhum ajuste fino.'}"
    return None


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    treinador = Treinador()
    if "--baixar" in sys.argv:
        print("base:", treinador.baixar_base())
    if "--dados" in sys.argv:
        import personalidade

        treino, avaliacao, contas = montar_dados(personalidade.sistema(local=True), ler_historico(), ler_correcoes())
        print(json.dumps(contas, ensure_ascii=False))
    if "--treinar" in sys.argv or "--teste" in sys.argv:
        teste = "--teste" in sys.argv  # poucos passos e sem sintéticos: para conferir que cabe na placa
        print(json.dumps(ciclo_de_ajuste(treinador, forcar=True, max_passos=3 if teste else -1, sinteticos=not teste,
                                         teste=teste), ensure_ascii=False, indent=1, default=str))
    print("falta:", treinador.o_que_falta() or "nada")
