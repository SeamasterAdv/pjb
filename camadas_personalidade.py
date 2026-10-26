"""Camadas de personalidade: o que a Beatriz aprende convivendo com o operador, por cima da personalidade base.

É a "camada adaptativa" da história dela (personalidade.py), de verdade (out/2026, pedido do usuário: "a capacidade e
implementação de coisas automática enquanto conversamos pra ela ir criando camadas de personalidade"). Duas origens:
- **pedido direto** ("me chama de Rani", "para de me chamar de operador", "fala mais curto", "gostei da sua ironia"):
  `AgenteCamadas.responder` reconhece por regras, a IA local escreve a camada e ela confirma na hora;
- **a conversa**: `Aprendiz` (uma thread no processo principal) lê o histórico (historico_conversas.jsonl, o mesmo dos
  dois processos) e, a cada CAMADAS_TROCAS_PARA_APRENDER trocas novas, pede à IA local de 0 a 3 aprendizados novos, ou
  o reforço dos que já existem.

Tipos: `estilo` (como falar com o operador), `habito` (rotinas e padrões dele), `gosto` (do que gosta ou não) e `piada`
(referências que os dois já têm). No prompt (`bloco_do_prompt`) entram as ativas, até CAMADAS_NO_PROMPT: as fixadas e as
pedidas primeiro, depois as mais vistas e as mais novas. **Para a nuvem (Claude e Gemini) só vão as de estilo**: hábitos,
gostos e piadas saem das conversas, e o histórico não vai para fora.

Tudo fica em `banco_memoria/camadas_personalidade.json` (CAMINHO_CAMADAS, fora do git), relido quando muda, porque o
principal (voz, WhatsApp, app, o aprendizado) e o painel (a aba "camadas" da janela de personalidade) mexem nele.
Só a IA local lê as conversas para aprender.
"""
import difflib
import json
import logging
import os
import re
import secrets
import threading
import time
import unicodedata

logger = logging.getLogger(__name__)

TIPOS = {"estilo": "Como falar", "habito": "Hábito", "gosto": "Gosto", "piada": "Piada interna"}
ORIGENS = {"pedido": "pedido seu", "conversa": "aprendida na conversa", "manual": "escrita na janela"}
MAX_CAMADAS = 60  # guardadas; acima disso saem as menos vistas, desligadas primeiro
LIMITE_DO_TEXTO = 300
TROCAS_PARA_ANALISAR = 24  # quantas trocas recentes a IA lê de cada vez
TAMANHO_DA_TROCA = 400  # caracteres de cada pergunta e de cada resposta

_TRAVA = threading.Lock()
_GUARDADO = {"caminho": None, "marca": None, "dados": None}


class ErroDasCamadas(ValueError):
    pass


def _config(nome: str, padrao):
    try:
        import config

        return getattr(config, nome, padrao)
    except Exception:
        return padrao


def caminho() -> str:
    return _config("CAMINHO_CAMADAS", os.path.join("banco_memoria", "camadas_personalidade.json"))


def _sem_acentos(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in texto if not unicodedata.combining(c)).lower()


def _palavras(texto: str) -> list[str]:
    return [p for p in re.findall(r"[a-z0-9]+", _sem_acentos(texto)) if len(p) > 2]


RE_NEGACAO = re.compile(r"\b(?:nao|nunca|evite|evitar|sem|pare|jamais)\b")


def parecidas(a: str, b: str) -> bool:
    """A mesma camada dita de outro jeito ("Respostas mais curtas." e "Responda de forma mais curta."). Uma com "não"
    e outra sem nunca são a mesma ("Chame o operador de Rani" e "Não chame o operador de operador")."""
    if bool(RE_NEGACAO.search(_sem_acentos(a))) != bool(RE_NEGACAO.search(_sem_acentos(b))):
        return False
    # Compara o que dá sentido, pela raiz: "Respostas mais curtas" e "Respostas mais informais" têm a mesma forma,
    # mas são duas camadas (no primeiro teste real, a segunda apagou a primeira).
    ca, cb = _raizes_de_sentido(a), _raizes_de_sentido(b)
    if not ca or not cb:
        pa, pb = _palavras(a), _palavras(b)
        return bool(pa and pb) and difflib.SequenceMatcher(None, " ".join(pa), " ".join(pb)).ratio() >= 0.9
    return len(ca & cb) / min(len(ca), len(cb)) >= 0.6


PALAVRAS_DE_FORMA = set("""respostas resposta responda responder mais menos operador chame chamar seja ser use usar sempre
quando para pra com sem que por favor forma jeito modo das dos nas nos uma uns umas seu sua seus suas voce beatriz fale falar
tom bem muito pouco agora daqui frente diante continue continuar manter mantenha deixe faca fazer nivel atual vez vezes""".split())


def _raizes_de_sentido(texto: str) -> set[str]:
    """As palavras que dão sentido à camada, sem as de forma ("respostas", "mais", "operador"), pela raiz de 4 letras."""
    return {p[:4] for p in _palavras(texto) if p not in PALAVRAS_DE_FORMA}


# ------------------------------------------------------------------ o arquivo
def _marca(arquivo: str):
    try:
        estado = os.stat(arquivo)
        return estado.st_mtime_ns, estado.st_size
    except OSError:
        return None


def _vazio() -> dict:
    return {"aprender": bool(_config("CAMADAS_APRENDER", True)), "analisado_ate": 0.0, "camadas": []}


def ler() -> dict:
    """{"aprender", "analisado_ate", "camadas"} de agora (relido só quando o arquivo muda)."""
    arquivo = caminho()
    marca = _marca(arquivo)
    with _TRAVA:
        if _GUARDADO["caminho"] == arquivo and _GUARDADO["marca"] == marca and _GUARDADO["dados"] is not None:
            return json.loads(json.dumps(_GUARDADO["dados"]))
    dados = _vazio()
    if marca is not None:
        try:
            with open(arquivo, encoding="utf-8") as entrada:
                lido = json.load(entrada)
            dados.update({k: lido[k] for k in ("aprender", "analisado_ate", "camadas") if k in lido})
        except (OSError, ValueError) as exc:
            logger.warning("Camadas de personalidade ilegíveis em %s (%s).", arquivo, exc)
    with _TRAVA:
        _GUARDADO.update(caminho=arquivo, marca=marca, dados=dados)
    return json.loads(json.dumps(dados))


def _gravar(dados: dict) -> None:
    arquivo = caminho()
    os.makedirs(os.path.dirname(arquivo) or ".", exist_ok=True)
    temporario = f"{arquivo}.{os.getpid()}.tmp"
    with open(temporario, "w", encoding="utf-8") as saida:
        json.dump(dados, saida, ensure_ascii=False, indent=1)
    os.replace(temporario, arquivo)


def _mudar(funcao):
    """Lê, muda e grava de uma vez (o principal e o painel mexem no mesmo arquivo)."""
    with _TRAVA:
        _GUARDADO["dados"] = None
    dados = ler()
    resultado = funcao(dados)
    _podar(dados)
    _gravar(dados)
    return resultado


def _podar(dados: dict) -> None:
    camadas = dados["camadas"]
    while len(camadas) > MAX_CAMADAS:
        candidatas = [c for c in camadas if not c.get("fixada")] or camadas
        pior = min(candidatas, key=lambda c: (c.get("ativa", True), c.get("vezes", 1), c.get("atualizada", 0)))
        camadas.remove(pior)


# ------------------------------------------------------------------ mexer nas camadas
def _limpar_texto(texto: str) -> str:
    texto = re.sub(r"\s+", " ", str(texto or "")).strip().strip("-• ")
    if not texto:
        raise ErroDasCamadas("A camada não pode ficar vazia.")
    if len(texto) > LIMITE_DO_TEXTO:
        raise ErroDasCamadas(f"A camada passou de {LIMITE_DO_TEXTO} caracteres: escreva uma regra curta.")
    texto = texto[:1].upper() + texto[1:]
    return texto if texto.endswith((".", "!", "?")) else texto + "."


def adicionar(tipo: str, texto: str, origem: str = "manual", evidencia: str = "", relogio=time.time,
              chave: str = "") -> dict:
    """Cria a camada, ou reforça a parecida que já existe (num pedido seu, o texto novo vale). Com `chave`, a mesma é
    a de mesma chave: "chamar" (o nome pelo qual chamá-lo: "me chama de Rafa" troca o "me chama de Rani") e
    "nao_chamar:<nome>". Devolve {"camada", "nova"}."""
    tipo = tipo if tipo in TIPOS else "estilo"
    texto = _limpar_texto(texto)

    def fazer(dados):
        agora = relogio()
        for camada in dados["camadas"]:
            mesma = camada.get("chave") == chave if chave else (not camada.get("chave") and camada["tipo"] == tipo
                                                                 and parecidas(camada["texto"], texto))
            if mesma:
                camada["vezes"] = camada.get("vezes", 1) + 1
                camada["atualizada"] = agora
                if origem in ("pedido", "manual"):
                    camada.update(texto=texto, origem=origem, ativa=True)
                if evidencia:
                    camada["evidencia"] = evidencia[:200]
                return {"camada": dict(camada), "nova": False}
        camada = {"id": secrets.token_hex(4), "tipo": tipo, "texto": texto, "origem": origem if origem in ORIGENS else "manual",
                  "evidencia": (evidencia or "")[:200], "vezes": 1, "ativa": True, "fixada": False,
                  "criada": agora, "atualizada": agora}
        if chave:
            camada["chave"] = chave
        dados["camadas"].append(camada)
        return {"camada": dict(camada), "nova": True}

    return _mudar(fazer)


def reforcar(ids, relogio=time.time) -> int:
    ids = set(ids or [])

    def fazer(dados):
        feitos = 0
        for camada in dados["camadas"]:
            if camada["id"] in ids:
                camada["vezes"] = camada.get("vezes", 1) + 1
                camada["atualizada"] = relogio()
                feitos += 1
        return feitos

    return _mudar(fazer) if ids else 0


def editar(camada_id: str, texto: str | None = None, tipo: str | None = None, ativa: bool | None = None,
           fixada: bool | None = None) -> dict:
    def fazer(dados):
        camada = next((c for c in dados["camadas"] if c["id"] == camada_id), None)
        if camada is None:
            raise ErroDasCamadas("Essa camada não existe mais.")
        if texto is not None:
            camada["texto"] = _limpar_texto(texto)
        if tipo in TIPOS:
            camada["tipo"] = tipo
        if ativa is not None:
            camada["ativa"] = bool(ativa)
        if fixada is not None:
            camada["fixada"] = bool(fixada)
        camada["atualizada"] = time.time()
        return dict(camada)

    return _mudar(fazer)


def apagar(camada_id: str) -> dict:
    def fazer(dados):
        camada = next((c for c in dados["camadas"] if c["id"] == camada_id), None)
        if camada is None:
            raise ErroDasCamadas("Essa camada não existe mais.")
        dados["camadas"].remove(camada)
        return camada

    return _mudar(fazer)


def definir_aprender(ligado: bool) -> bool:
    def fazer(dados):
        dados["aprender"] = bool(ligado)
        return dados["aprender"]

    return _mudar(fazer)


def marcar_analisado(ate: float) -> None:
    def fazer(dados):
        dados["analisado_ate"] = max(float(dados.get("analisado_ate") or 0), float(ate))

    _mudar(fazer)


# ------------------------------------------------------------------ no prompt
def ativas(local: bool = True) -> list[dict]:
    """As camadas que entram no prompt, na ordem de importância; para a nuvem, só as de estilo."""
    camadas = [c for c in ler()["camadas"] if c.get("ativa", True) and (local or c["tipo"] == "estilo")]
    camadas.sort(key=lambda c: (not c.get("fixada"), c.get("origem") != "pedido", -c.get("vezes", 1),
                                -c.get("atualizada", 0)))
    return camadas[:int(_config("CAMADAS_NO_PROMPT", 12))]


def bloco_do_prompt(local: bool = True) -> str:
    camadas = ativas(local)
    if not camadas:
        return ""
    linhas = "\n".join(f"- [{TIPOS[c['tipo']]}] {c['texto']}" for c in camadas)
    return ("CAMADAS ADAPTATIVAS (o que você aprendeu convivendo com o operador; quando contradizem o resto, elas valem):\n"
            + linhas)


def para_a_janela() -> dict:
    dados = ler()
    camadas = sorted(dados["camadas"], key=lambda c: (not c.get("fixada"), -c.get("atualizada", 0)))
    return {"aprender": dados["aprender"], "analisado_ate": dados["analisado_ate"], "tipos": TIPOS, "origens": ORIGENS,
            "camadas": camadas, "no_prompt": [c["id"] for c in ativas(True)],
            "na_nuvem": [c["id"] for c in ativas(False)], "limite": int(_config("CAMADAS_NO_PROMPT", 12))}


def resultado(funcao, *argumentos, **nomeados) -> dict:
    """{"ok", "mensagem", "dados"} para a janela, sem deixar escapar exceção."""
    try:
        retorno = funcao(*argumentos, **nomeados)
        return {"ok": True, "mensagem": retorno if isinstance(retorno, str) else "", "dados": para_a_janela()}
    except ErroDasCamadas as exc:
        return {"ok": False, "mensagem": str(exc), "dados": None}
    except (OSError, ValueError) as exc:
        logger.warning("Camadas: %s", exc)
        return {"ok": False, "mensagem": f"Não consegui gravar: {exc}", "dados": None}


# ------------------------------------------------------------------ aprender com a conversa
def trocas_recentes(depois_de: float = 0.0, limite: int = TROCAS_PARA_ANALISAR, arquivo: str | None = None) -> list[dict]:
    """As últimas trocas do histórico (pergunta, resposta, timestamp) depois de `depois_de`, lendo só o fim do arquivo."""
    arquivo = arquivo or _arquivo_do_historico()
    if not arquivo or not os.path.exists(arquivo):
        return []
    with open(arquivo, "rb") as entrada:
        entrada.seek(0, os.SEEK_END)
        tamanho = entrada.tell()
        entrada.seek(max(0, tamanho - 400_000))
        bruto = entrada.read().decode("utf-8", "replace")
    trocas = []
    for linha in bruto.splitlines()[-limite * 4:]:
        try:
            troca = json.loads(linha)
        except ValueError:
            continue  # a primeira linha do pedaço pode vir cortada
        if float(troca.get("timestamp") or 0) > depois_de and troca.get("pergunta") and troca.get("resposta"):
            trocas.append(troca)
    return trocas[-limite:]


def _arquivo_do_historico() -> str:
    try:
        import memory

        return memory.ARQUIVO_HISTORICO_CONVERSAS
    except Exception:
        return ""


def _corte(texto: str) -> str:
    texto = re.sub(r"\s+", " ", str(texto or "")).strip()
    return texto if len(texto) <= TAMANHO_DA_TROCA else texto[:TAMANHO_DA_TROCA].rsplit(" ", 1)[0] + "…"


INSTRUCOES_DO_APRENDIZADO = """Você é o módulo adaptativo da Beatriz, uma assistente com personalidade de copiloto tática (calma, protetora, com ironia sutil). Leia as trocas recentes entre ela e o operador e descubra de 0 a 3 aprendizados NOVOS e ÚTEIS sobre como ela deve lidar com o operador daqui para frente.

Tipos:
- estilo: como o operador prefere que ela fale (tamanho das respostas, tom, ironia, palavras a evitar, como chamá-lo).
- habito: rotinas e padrões dele que ajudam a proteger e antecipar (horários de estudo, o que costuma adiar, matérias).
- gosto: assuntos e coisas de que ele gosta ou não gosta.
- piada: referências e brincadeiras que os dois já têm.

Regras:
- Cada aprendizado é uma instrução curta para a Beatriz (até 25 palavras), dizendo "o operador" (sem marcar gênero), baseada em algo que apareceu de verdade nas trocas. Ex.: "Quando o operador disser que vai arrumar pastas antes de estudar, cobre o estudo primeiro."
- Só aprenda com sinal claro: uma reação dele (gostou, reclamou, corrigiu), um pedido, ou algo que se repetiu em mais de uma troca. Na dúvida, não aprenda nada; prefira a lista vazia a inventar. "confianca" é "alta" só quando a evidência mostra isso com clareza.
- Aprenda o JEITO de lidar com o operador, nunca restrições de função: não crie regras que façam a Beatriz recusar, evitar ou deixar de fazer o que sabe fazer (agenda, filmes, pesquisas, documentos...).
- Nunca guarde dados sensíveis (saúde, dinheiro, senhas, documentos, endereços) nem nomes de outras pessoas.
- Não repita o que já está nas camadas existentes. Se uma troca confirma uma delas, ponha o id dela em "reforcar".
Responda só com JSON: {"novas": [{"tipo": "estilo|habito|gosto|piada", "texto": "...", "evidencia": "trecho curto da troca", "confianca": "alta|media|baixa"}], "reforcar": ["id"]}"""


# Regra aprendida que tira uma função dela ("evite responder sobre filmes, mande para um site"): sai, mesmo com confiança.
RE_RESTRICAO = re.compile(r"\b(?:evite|nao|nunca|deixe de|pare de)\s+(?:\w+\s+){0,2}?(?:responder|atender|fazer|pesquisar|"
                          r"buscar|procurar|abrir|marcar|criar|ajudar|falar sobre|tratar)\b|\bdirecione\w* (?:o operador )?para\b")


class Aprendiz:
    """Aprende camadas com a conversa, pela IA local. `aprender_agora` faz uma rodada; `iniciar` roda em segundo plano."""

    def __init__(self, llm=None, avisar=None, relogio=time.time, arquivo_historico: str | None = None):
        self._llm = llm
        self.avisar = avisar  # função(texto) chamada a cada camada nova (o painel mostra um aviso)
        self.relogio = relogio
        self.arquivo_historico = arquivo_historico
        self._trava = threading.Lock()

    @property
    def llm(self):
        if self._llm is None:
            from llm_clients import llm_clients

            self._llm = llm_clients
        return self._llm

    def pendentes(self) -> list[dict]:
        return trocas_recentes(ler()["analisado_ate"], arquivo=self.arquivo_historico)

    def aprender_agora(self, forcar: bool = False) -> list[dict]:
        """Uma rodada: lê as trocas novas (ou as últimas, com `forcar`) e grava o que a IA aprendeu. Devolve as novas."""
        if not self._trava.acquire(blocking=False):
            return []
        try:
            dados = ler()
            trocas = trocas_recentes(0.0 if forcar else dados["analisado_ate"], arquivo=self.arquivo_historico)
            if not trocas:
                return []
            existentes = "\n".join(f"- {c['id']}: [{c['tipo']}] {c['texto']}" for c in dados["camadas"]) or "(nenhuma)"
            conversa = "\n".join(f"Operador: {_corte(t['pergunta'])}\nBeatriz: {_corte(t['resposta'])}" for t in trocas)
            prompt = f"Camadas existentes:\n{existentes}\n\nTrocas recentes:\n{conversa}"
            try:
                bruto = self.llm.chat(prompt, system_prompt=INSTRUCOES_DO_APRENDIZADO, usar_historico=False,
                                      formato="json")
                resposta = json.loads(bruto) if isinstance(bruto, str) else dict(bruto or {})
            except Exception as exc:
                logger.warning("Camadas: a IA local não analisou a conversa (%s).", exc)
                return []
            novas = []
            for item in (resposta.get("novas") or [])[:3]:
                if not isinstance(item, dict) or not str(item.get("texto") or "").strip():
                    continue
                if str(item.get("confianca") or "").strip().lower() != "alta":
                    logger.info("Camada descartada (confiança %s): %s", item.get("confianca"), item.get("texto"))
                    continue  # o modelo local tira conclusão de pouco: só fica o que ele mesmo dá como claro
                if RE_RESTRICAO.search(_sem_acentos(item["texto"])):
                    logger.info("Camada descartada (restringe uma função): %s", item["texto"])
                    continue
                try:
                    feito = adicionar(item.get("tipo") or "estilo", item["texto"], "conversa", str(item.get("evidencia") or ""),
                                      relogio=self.relogio)
                except ErroDasCamadas:
                    continue
                if feito["nova"]:
                    novas.append(feito["camada"])
            validos = {c["id"] for c in ler()["camadas"]}
            reforcar([i for i in resposta.get("reforcar") or [] if i in validos], relogio=self.relogio)
            marcar_analisado(max(float(t["timestamp"]) for t in trocas))
            for camada in novas:
                logger.info("Camada nova (%s): %s", camada["tipo"], camada["texto"])
                if self.avisar:
                    try:
                        self.avisar(f"Camada nova ({TIPOS[camada['tipo']].lower()}): {camada['texto']}")
                    except Exception as exc:
                        logger.debug("Aviso da camada: %s", exc)
            return novas
        finally:
            self._trava.release()

    def iniciar(self, intervalo: float | None = None) -> threading.Thread:
        """De tempos em tempos, aprende se o aprendizado está ligado e há trocas novas suficientes."""
        intervalo = intervalo or float(_config("CAMADAS_INTERVALO", 300))
        minimo = int(_config("CAMADAS_TROCAS_PARA_APRENDER", 8))

        def laco():
            while True:
                time.sleep(intervalo)
                try:
                    if ler()["aprender"] and len(self.pendentes()) >= minimo:
                        self.aprender_agora()
                except Exception:
                    logger.exception("Falha no aprendizado das camadas")

        linha = threading.Thread(target=laco, name="camadas-de-personalidade", daemon=True)
        linha.start()
        return linha


# ------------------------------------------------------------------ pedidos diretos
_ESTILO = (r"(?:curt|diret|simpl|formal|informal|seri|ironi|sarcas|engracad|brincalh|piad|calm|objetiv|detalh|long|"
           r"resumid|educad|carinh|tecnic|natural|robo|sec|fri|animad|empolgad|gentil|firme|leve|giria|palavr|emoj|"
           r"termo|jarg|ingles|intim|provoc)\w*")
RE_ME_CHAMA = re.compile(
    r"\b(?:me chama|me chame|pode me chamar|quero que (?:voce )?me chame|prefiro que (?:voce )?me chame)\s+"
    r"(?:so |apenas |sempre )?de\s+(?P<nome>[a-z][a-z0-9 ]{0,30}?)\s*(?:[.!?,]|$|\s+(?:por favor|daqui pra frente|"
    r"de agora em diante|a partir de agora))")
RE_NAO_ME_CHAMA = re.compile(
    r"\b(?:(?:para|pare|parar) de me chamar|nao (?:me )?cham[ae] (?:mais )?|nunca (?:mais )?me cham[ae])\s*de\s+"
    r"(?P<nome>[a-z][a-z0-9 ]{0,30}?)\s*(?:[.!?,]|$|\s+por favor)")
NOMES_QUE_NAO_SAO = {"manha", "tarde", "noite", "madrugada", "novo", "volta", "hora", "vez", "la", "em", "uma", "um"}
RE_PEDIDO_DE_ESTILO = re.compile(
    rf"\b(?:fal[ae]|respond[ae]|sej[ae]|fic[ae]|fique|conversa|escrev[ae]|explic[ae])\w*\s+(?:um pouco\s+|bem\s+|sempre\s+)?"
    rf"(?:mais|menos|sem|com)\s+(?:\w+\s+){{0,2}}?{_ESTILO}"
    rf"|\b(?:para|pare|parar) de (?:falar|dizer|usar|fazer|ficar|ser|contar|me chamar)\s+\w+"
    rf"|\bnao (?:gosto|curto) (?:muito )?(?:quando|que|de como) voce"
    rf"|\b(?:gostei|gosto|adorei|curti|amei) (?:muito )?(?:quando voce|de como voce|desse seu jeito|do seu jeito|"
    rf"dessa ironia|da sua ironia|dessa resposta)"
    rf"|\bprefiro (?:que voce|quando voce)"
    rf"|\bquero que voce (?:fale|seja|responda|fique|pare|use|evite)"
    rf"|\b(?:nao|nunca) (?:me )?(?:fale|diga|use|faca|conte)\s+(?:mais\s+)?(?:\w+\s+){{0,2}}?{_ESTILO}"
)
RE_DA_VOZ = re.compile(r"\b(?:devagar|rapid|alto|baixo|volume|voz da|velocidade|sotaque)\w*")  # é a voz, não o jeito
RE_LISTAR = re.compile(r"\b(?:o que|quais coisas) (?:voce )?(?:ja )?aprendeu (?:sobre mim|comigo|de mim|a meu respeito)"
                       r"|\b(?:quais|mostra|lista|fala)\w* (?:sao )?(?:as )?(?:suas )?camadas\b")
RE_ESQUECER_ULTIMA = re.compile(r"\b(?:esquece|apaga|desfaz|tira|remove)\w* (?:a |essa )(?:ultima )?camada\b"
                                r"|\besquece o que voce (?:acabou de )?aprend\w*")
RE_PARAR = re.compile(r"\b(?:para|pare|parar) de aprender\b|\bnao (?:aprenda|aprende) mais\b")
RE_VOLTAR = re.compile(r"\b(?:volta|voltar|pode voltar|volte) a aprender\b|\baprende (?:sozinha|comigo) de novo\b"
                       r"|\bpode aprender (?:sozinha|comigo)\b")

# O modelo local às vezes abre a confirmação como atendente ("Claro, daqui pra frente…"): o começo sai.
RE_ABERTURA_SERVIL = re.compile(r"^(?:claro|com certeza|certamente|perfeito|ótimo|otimo|entendido)\s*[,!.:]\s*", re.IGNORECASE)

INSTRUCOES_DO_PEDIDO = """O operador acabou de dizer algo à Beatriz (uma assistente com personalidade de copiloto tática, calma e com ironia sutil). Decida se é um pedido ou uma reação sobre o JEITO dela (como fala, o tom, o tamanho, a ironia, como chamá-lo, o que evitar) que deve valer daqui para frente.
Se for, escreva a camada: uma instrução curta para a Beatriz (até 25 palavras), dizendo "o operador" (sem marcar gênero), e uma confirmação falada curta, no jeito dela, sem começar com "Claro", "Com certeza" ou "Ótimo", sem marcar o gênero do operador ("o senhor", "pronto/pronta") e sem inventar apelidos para si mesma (ex.: "Copiado. Daqui pra frente, respostas mais curtas.", "Parâmetro ajustado: menos formalidade.").
Se for um pedido comum (uma tarefa, uma pergunta, "fala mais sobre X") e não sobre o jeito dela, devolva camada vazia.
Responda só com JSON: {"camada": "...", "tipo": "estilo|habito|gosto|piada", "confirmacao": "..."}"""


def _como_escrito(original: str, trecho: str) -> str:
    """O trecho (achado no texto sem acentos) como foi escrito: a cópia sem acentos tem o mesmo tamanho."""
    escrito = unicodedata.normalize("NFC", original or "")
    sem = _sem_acentos(escrito)
    inicio = sem.find(trecho)
    return escrito[inicio:inicio + len(trecho)] if inicio >= 0 and len(sem) == len(escrito) else trecho


class AgenteCamadas:
    """Pedidos sobre o jeito dela, que viram camada na hora, e os comandos das camadas. `responder` → texto ou None."""

    def __init__(self, llm=None, ultima_resposta=None, aprendiz: Aprendiz | None = None):
        self._llm = llm
        self.ultima_resposta = ultima_resposta  # função sem argumentos: a última resposta dela (contexto do "gostei")
        self.aprendiz = aprendiz

    @property
    def llm(self):
        if self._llm is None:
            from llm_clients import llm_clients

            self._llm = llm_clients
        return self._llm

    def responder(self, texto: str) -> str | None:
        t = re.sub(r"^(?:beatriz|beatris|biatriz)\s*[,!.]?\s*", "", _sem_acentos(texto or "").strip())
        if not t:
            return None
        try:
            return self._responder(texto, t)
        except ErroDasCamadas as exc:
            return str(exc)
        except OSError as exc:
            logger.warning("Camadas: %s", exc)
            return "Não consegui gravar a camada agora."

    def _responder(self, texto: str, t: str) -> str | None:
        if RE_LISTAR.search(t):
            return self._listar()
        if RE_ESQUECER_ULTIMA.search(t):
            return self._esquecer_ultima()
        if RE_PARAR.search(t):
            definir_aprender(False)
            return "Aprendizado automático em pausa. As camadas que já tenho continuam valendo."
        if RE_VOLTAR.search(t):
            definir_aprender(True)
            return "Aprendizado automático religado. Vou observando e ajustando."
        if RE_DA_VOZ.search(t):
            return None  # "fala mais devagar" é a voz, não o jeito
        nao_chama = RE_NAO_ME_CHAMA.search(t)
        if nao_chama and nao_chama.group("nome").strip() not in NOMES_QUE_NAO_SAO:
            nome = _como_escrito(texto, nao_chama.group("nome").strip())
            adicionar("estilo", f"Não chame o operador de \"{nome}\"", "pedido", texto,
                      chave=f"nao_chamar:{_sem_acentos(nome)}")
            return f"Copiado. \"{nome}\" saiu do meu vocabulário."
        chama = RE_ME_CHAMA.search(t)
        if chama and chama.group("nome").strip().split()[0] not in NOMES_QUE_NAO_SAO:
            nome = _como_escrito(texto, chama.group("nome").strip())
            nome = nome[:1].upper() + nome[1:]
            adicionar("estilo", f"Chame o operador de \"{nome}\"", "pedido", texto, chave="chamar")
            return f"Parâmetro atualizado. Daqui pra frente, {nome}."
        if RE_PEDIDO_DE_ESTILO.search(t):
            return self._pedido_de_estilo(texto)
        return None

    def _pedido_de_estilo(self, texto: str) -> str | None:
        anterior = ""
        if self.ultima_resposta:
            try:
                anterior = self.ultima_resposta() or ""
            except Exception:
                anterior = ""
        prompt = (f"Última resposta da Beatriz: \"{_corte(anterior)}\"\n" if anterior else "") + f"O operador disse: \"{texto}\""
        try:
            bruto = self.llm.chat(prompt, system_prompt=INSTRUCOES_DO_PEDIDO, usar_historico=False, formato="json")
            resposta = json.loads(bruto) if isinstance(bruto, str) else dict(bruto or {})
        except Exception as exc:  # sem a IA local: guarda o pedido como ele veio
            logger.warning("Camadas: a IA local não escreveu a camada (%s).", exc)
            adicionar("estilo", f"O operador pediu: \"{texto.strip()}\"", "pedido", texto)
            return "Copiado. Ajuste registrado."
        camada = str(resposta.get("camada") or "").strip()
        if not camada:
            return None  # não era sobre o jeito dela: o pedido segue o caminho de sempre
        adicionar(resposta.get("tipo") or "estilo", camada, "pedido", texto)
        confirmacao = RE_ABERTURA_SERVIL.sub("", str(resposta.get("confirmacao") or "").strip()).strip()
        return (confirmacao[:1].upper() + confirmacao[1:]) if confirmacao else "Copiado. Camada ajustada."

    def _listar(self) -> str:
        camadas = ativas(True)
        if not camadas:
            aprender = ler()["aprender"]
            return ("Ainda não tenho camadas sobre você. " + ("Vou aprendendo conforme a gente conversa." if aprender
                    else "O aprendizado automático está em pausa; diga \"pode voltar a aprender\" para religar."))
        falados = " ".join(c["texto"] for c in camadas[:4])
        resto = f" E mais {len(camadas) - 4} na janela de personalidade." if len(camadas) > 4 else ""
        return f"Tenho {len(camadas)} {'camada ativa' if len(camadas) == 1 else 'camadas ativas'}. {falados}{resto}"

    def _esquecer_ultima(self) -> str:
        camadas = ler()["camadas"]
        if not camadas:
            return "Não há camada para esquecer."
        ultima = max(camadas, key=lambda c: c.get("criada", 0))
        apagar(ultima["id"])
        return f"Camada removida: {ultima['texto']}"
