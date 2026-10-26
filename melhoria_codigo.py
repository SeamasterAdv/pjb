"""A Beatriz melhora o próprio código com o Claude, num ambiente separado, e só aplica o que passou nos testes.

Out/2026, pedido do usuário: "quero poder falar algo e ela achar uma forma de se melhorar usando o Claude e dando
soluções e só modificando sozinha o código de forma preventiva e testando num ambiente separado pra evitar falhas".

Uma melhoria (`Laboratorio`, rodada por `AgenteMelhoria` numa thread):
1. **Ambiente separado**: um worktree do git numa pasta irmã (`C:\\B.E.A.T.R.I.Z-evolucao\\<id>`, ramo `evolucao/<id>`),
   a partir do commit atual. A pasta da Beatriz em uso não muda, e o worktree só tem o que está no git (sem o `.env`,
   o `banco_memoria` nem os seus documentos).
2. **O Claude Code** (pela assinatura, como o `llm_nuvem.ClienteClaudeCode`) roda lá dentro, sem interface, com o pedido,
   o CLAUDE.md do projeto e só estas ferramentas: ler, procurar, editar e rodar o pytest (`--permission-mode dontAsk`:
   o resto é negado; sem internet).
3. **A Beatriz confere ela mesma**: roda a suíte inteira no worktree; com uma falha, descarta.
4. Passou: um commit no ramo, e ela pergunta "Aplico?" (`EVOLUCAO_APLICAR_SOZINHA=true` aplica sem perguntar).
   **Aplicar** = juntar o ramo no ramo atual (fast-forward; se o principal andou, merge; com conflito, nada muda) e vale
   quando ela reiniciar. **"Desfaz a última melhoria"** = `git revert` do que foi aplicado.

**Prevenção** (`erros_recorrentes`, chamada de madrugada pelo `ajuste_fino.RotinaDaMadrugada`): os erros com traceback
que se repetiram no `beatriz.log` viram pedidos de correção, preparados e testados à noite; de manhã ela conta e pergunta.

Vai para o Claude: o pedido, o código do projeto (o que está no git) e, num pedido sobre algo que acabou de acontecer,
a última troca da conversa; dos erros do log, só o tipo, a mensagem curta e as linhas do código.
O registro de tudo fica em `banco_memoria/evolucao/melhorias.json` (fora do git).
"""
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import unicodedata

logger = logging.getLogger(__name__)

PASTA_PROJETO = os.path.dirname(os.path.abspath(__file__))
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VARIAVEIS_DE_API = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
SEGUNDOS_PARA_APROVAR = 30 * 60
ESTADOS = {"preparando": "preparando", "testando": "testando", "pronta": "pronta, esperando você",
           "falhou": "não deu certo", "aplicada": "aplicada", "descartada": "descartada", "desfeita": "desfeita"}

REGRAS_DO_CLAUDE = """Você está melhorando o código da Beatriz, uma assistente pessoal em português do Brasil, a pedido do dono dela. Leia o CLAUDE.md desta pasta antes de mexer: ele explica a arquitetura e as convenções.

Regras:
- Esta pasta é uma cópia separada (um worktree do git): a Beatriz em uso não é afetada. Trabalhe só aqui.
- Faça a menor mudança que resolve o pedido, no estilo do código em volta (português em tudo: nomes, comentários, mensagens).
- Acrescente ou ajuste testes que provem o comportamento novo.
- Rode a suíte inteira com: {python} -m pytest -q -p no:warnings
  e só termine com tudo passando. Não use outros comandos.
- Não mexa em .env, segredos, banco_memoria, .claude nem em arquivos fora desta pasta. Não faça commit.
- Se o pedido for perigoso, impossível, ou não for sobre o código da Beatriz, não mude nada e explique.
Ao terminar, responda só com um JSON: {{"mudou": true ou false, "resumo": "o que mudou e por quê, em 2 a 4 frases simples para serem faladas", "testes": "quantos passaram", "motivo": "se não mudou, por quê"}}"""


def _sem_acentos(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in texto if not unicodedata.combining(c)).lower()


def _config(nome: str, padrao):
    try:
        import config

        return getattr(config, nome, padrao)
    except Exception:
        return padrao


# ------------------------------------------------------------------ registro
class Registro:
    """As melhorias (pedido, situação, ramo, resumo...) num JSON, lido e gravado a cada uso."""

    def __init__(self, caminho: str | None = None):
        self._caminho = caminho
        self._trava = threading.RLock()

    @property
    def caminho(self) -> str:
        return self._caminho or _config("CAMINHO_MELHORIAS", os.path.join(PASTA_PROJETO, "banco_memoria", "evolucao",
                                                                          "melhorias.json"))

    def todas(self) -> list[dict]:
        with self._trava:
            try:
                with open(self.caminho, encoding="utf-8") as entrada:
                    return json.load(entrada).get("melhorias", [])
            except (OSError, ValueError):
                return []

    def _gravar(self, melhorias: list[dict]) -> None:
        os.makedirs(os.path.dirname(self.caminho), exist_ok=True)
        temporario = f"{self.caminho}.{os.getpid()}.tmp"
        with open(temporario, "w", encoding="utf-8") as saida:
            json.dump({"melhorias": melhorias[-200:]}, saida, ensure_ascii=False, indent=1)
        os.replace(temporario, self.caminho)

    def guardar(self, melhoria: dict) -> dict:
        with self._trava:
            melhorias = [m for m in self.todas() if m["id"] != melhoria["id"]] + [melhoria]
            melhoria["atualizada"] = time.time()
            self._gravar(melhorias)
        return melhoria

    def achar(self, melhoria_id: str) -> dict | None:
        return next((m for m in self.todas() if m["id"] == melhoria_id), None)

    def ultima(self, *estados) -> dict | None:
        candidatas = [m for m in self.todas() if not estados or m["status"] in estados]
        return max(candidatas, key=lambda m: m.get("atualizada", 0)) if candidatas else None


# ------------------------------------------------------------------ o laboratório (git, Claude e testes)
class ErroDoLaboratorio(RuntimeError):
    pass


def resumo_dos_testes(saida: str) -> tuple[bool, str]:
    """(passou, "1131 passaram") pela última linha do pytest ("1131 passed in 113.10s", "2 failed, 1129 passed")."""
    linhas = [l for l in (saida or "").strip().splitlines() if re.search(r"\b(?:passed|failed|error)", l)]
    final = linhas[-1] if linhas else ""
    passaram = re.search(r"(\d+) passed", final)
    falharam = re.search(r"(\d+) (?:failed|errors?)", final)
    if falharam:
        return False, f"{falharam.group(1)} falharam" + (f", {passaram.group(1)} passaram" if passaram else "")
    if passaram:
        return True, f"{passaram.group(1)} passaram"
    return False, "a suíte não rodou até o fim"


class Laboratorio:
    """O git, o Claude Code e o pytest de uma melhoria. `executar` é o subprocess.run (os testes trocam o do Claude)."""

    def __init__(self, repo: str = PASTA_PROJETO, pasta_evolucao: str | None = None, python: str | None = None,
                 claude: str | None = None, executar=None, tempo_claude: float | None = None,
                 tempo_testes: float | None = None):
        self.repo = repo
        self._pasta_evolucao = pasta_evolucao
        self.python = python or os.path.join(repo, ".venv312", "Scripts", "python.exe")
        self._claude = claude
        self.executar = executar or subprocess.run
        self.tempo_claude = tempo_claude or float(_config("EVOLUCAO_TEMPO_CLAUDE", 1800))
        self.tempo_testes = tempo_testes or float(_config("EVOLUCAO_TEMPO_TESTES", 900))

    @property
    def pasta_evolucao(self) -> str:
        return self._pasta_evolucao or _config("PASTA_EVOLUCAO", os.path.normpath(self.repo) + "-evolucao")

    @property
    def claude(self) -> str:
        if self._claude is None:
            from llm_nuvem import localizar_claude_code

            self._claude = localizar_claude_code()
        return self._claude

    def git(self, *argumentos, pasta: str | None = None, verificar: bool = True) -> str:
        processo = subprocess.run(["git", *argumentos], cwd=pasta or self.repo, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", creationflags=SEM_JANELA)
        if verificar and processo.returncode != 0:
            raise ErroDoLaboratorio(f"git {argumentos[0]}: {(processo.stderr or processo.stdout).strip()[:300]}")
        return processo.stdout.strip()

    def criar_ambiente(self, melhoria: dict) -> None:
        """O worktree separado, a partir do commit atual (o que não está commitado na pasta em uso fica de fora)."""
        pasta = os.path.join(self.pasta_evolucao, melhoria["id"])
        os.makedirs(self.pasta_evolucao, exist_ok=True)
        base = self.git("rev-parse", "HEAD")
        self.git("worktree", "add", "-b", melhoria["ramo"], pasta, base)
        melhoria.update(pasta=pasta, base=base)

    def rodar_claude(self, melhoria: dict) -> dict:
        """O Claude Code no worktree, só com ler, procurar, editar e o pytest. Devolve o JSON final dele."""
        if not self.claude:
            raise ErroDoLaboratorio("não achei o Claude Code neste PC")
        python = self.python.replace("\\", "/")
        comando = [
            self.claude, "-p", "--output-format", "json", "--no-session-persistence", "--strict-mcp-config",
            "--disable-slash-commands", "--permission-mode", "dontAsk",
            "--tools", "Read,Edit,Write,Glob,Grep,Bash",
            # Ler e escrever só dentro do worktree: sem o "./**", ele leria qualquer arquivo do PC (o .env da pasta em
            # uso, por exemplo). Conferido com o Claude Code 2.1.308: fora da pasta, ler e criar arquivo são negados.
            "--allowedTools", "Read(./**)", "Edit(./**)", "Write(./**)", "Glob", "Grep", f"Bash({python} -m pytest:*)",
            "--append-system-prompt", REGRAS_DO_CLAUDE.format(python=python),
        ]
        modelo = _config("EVOLUCAO_MODELO_CLAUDE", "")
        if modelo:
            comando += ["--model", modelo]
        pedido = melhoria["pedido"]
        if melhoria.get("contexto"):
            pedido += f"\n\nContexto (o que acabou de acontecer):\n{melhoria['contexto']}"
        ambiente = {k: v for k, v in os.environ.items() if k not in VARIAVEIS_DE_API}  # a assinatura, nunca a API paga
        try:
            processo = self.executar(comando, input=pedido, capture_output=True, text=True, encoding="utf-8",
                                     errors="replace", cwd=melhoria["pasta"], env=ambiente, timeout=self.tempo_claude,
                                     creationflags=SEM_JANELA)
        except subprocess.TimeoutExpired as exc:
            raise ErroDoLaboratorio(f"o Claude passou de {int(self.tempo_claude // 60)} minutos") from exc
        try:
            dados = json.loads(processo.stdout or "{}")
        except ValueError:
            dados = {}
        texto = str(dados.get("result") or "").strip()
        if processo.returncode != 0 or dados.get("is_error"):
            raise ErroDoLaboratorio(f"o Claude não terminou: {(texto or processo.stderr or '').strip()[:300]}")
        return _json_do_fim(texto) or {"mudou": True, "resumo": texto[:600]}

    def rodar_testes(self, pasta: str) -> tuple[bool, str]:
        try:
            processo = self.executar([self.python, "-m", "pytest", "-q", "-p", "no:warnings"], cwd=pasta,
                                     capture_output=True, text=True, encoding="utf-8", errors="replace",
                                     timeout=self.tempo_testes, creationflags=SEM_JANELA)
        except subprocess.TimeoutExpired:
            return False, f"os testes passaram de {int(self.tempo_testes // 60)} minutos"
        return resumo_dos_testes(processo.stdout)

    def guardar_mudancas(self, melhoria: dict) -> bool:
        """Commita no ramo o que o Claude mudou. False se ele não mudou nada (rastreado pelo git)."""
        pasta = melhoria["pasta"]
        self.git("add", "-A", pasta=pasta)
        arquivos = [l for l in self.git("diff", "--cached", "--name-only", pasta=pasta).splitlines() if l.strip()]
        if not arquivos:
            return False
        numeros = self.git("diff", "--cached", "--shortstat", pasta=pasta)
        self.git("-c", "user.name=Beatriz", "-c", "user.email=beatriz@localhost", "commit", "-q", "-m",
                 f"Evolução: {melhoria['pedido'][:60]}\n\n{melhoria.get('resumo', '')}\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
                 pasta=pasta)
        melhoria.update(arquivos=arquivos, numeros=numeros, commit=self.git("rev-parse", "HEAD", pasta=pasta))
        return True

    def remover_ambiente(self, melhoria: dict, apagar_ramo: bool = True) -> None:
        pasta = melhoria.get("pasta")
        if pasta and os.path.exists(pasta):
            self.git("worktree", "remove", "--force", pasta, verificar=False)
            shutil.rmtree(pasta, ignore_errors=True)  # o que o Windows segurou
        self.git("worktree", "prune", verificar=False)
        if apagar_ramo:
            self.git("branch", "-D", melhoria["ramo"], verificar=False)

    def aplicar(self, melhoria: dict) -> str:
        """Junta o ramo no ramo atual da pasta em uso. Devolve o commit a desfazer depois."""
        rapido = subprocess.run(["git", "merge", "--ff-only", melhoria["ramo"]], cwd=self.repo, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", creationflags=SEM_JANELA)
        if rapido.returncode == 0:
            melhoria["aplicacao"] = "rapida"
            return melhoria["commit"]
        junta = subprocess.run(["git", "-c", "user.name=Beatriz", "-c", "user.email=beatriz@localhost", "merge", "--no-edit",
                                melhoria["ramo"]], cwd=self.repo, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", creationflags=SEM_JANELA)
        if junta.returncode != 0:
            # Nunca um reset: a pasta em uso pode ter mudanças suas, não commitadas. O abort volta ao que era.
            self.git("merge", "--abort", verificar=False)
            if "local changes" in (junta.stderr or "") + (rapido.stderr or ""):
                raise ErroDoLaboratorio("os mesmos arquivos têm mudanças que ainda não foram para o git (um commit "
                                        "resolve); nada foi alterado")
            raise ErroDoLaboratorio("o código mudou desde que preparei a melhoria e as mudanças se chocam; nada foi alterado")
        melhoria["aplicacao"] = "merge"
        return self.git("rev-parse", "HEAD")

    def desfazer(self, melhoria: dict) -> str:
        argumentos = ["revert", "--no-edit"] + (["-m", "1"] if melhoria.get("aplicacao") == "merge" else [])
        processo = subprocess.run(["git", "-c", "user.name=Beatriz", "-c", "user.email=beatriz@localhost", *argumentos,
                                   melhoria["aplicada_em"]], cwd=self.repo, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", creationflags=SEM_JANELA)
        if processo.returncode != 0:
            self.git("revert", "--abort", verificar=False)
            raise ErroDoLaboratorio("o que veio depois mexeu nos mesmos trechos; desfaça à mão pelo git")
        return self.git("rev-parse", "HEAD")


def _json_do_fim(texto: str) -> dict | None:
    """O último objeto JSON do texto (o Claude às vezes escreve antes, ou põe num bloco de código)."""
    for achado in reversed(list(re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", texto or "", re.S))):
        try:
            dados = json.loads(achado.group(0))
            if isinstance(dados, dict):
                return dados
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------ prevenção: os erros do log
RE_TRACEBACK = re.compile(r"Traceback \(most recent call last\):\n(?:[ \t].*\n)+?(?P<erro>[A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit)\b[^\n]*)")
RE_QUADRO = re.compile(r'File "(?P<arquivo>[^"]+)", line (?P<linha>\d+), in (?P<funcao>[\w<>]+)')


def erros_recorrentes(caminho_log: str, desde_bytes: int = 0, minimo: int = 2, projeto: str = PASTA_PROJETO) -> tuple[list[dict], int]:
    """Os erros com traceback do log (a partir de `desde_bytes`) que se repetiram, agrupados pelo último quadro do
    código da Beatriz. Só o tipo, a mensagem curta e as linhas do código: nada do que foi falado.
    Devolve (erros, até onde o log foi lido)."""
    try:
        with open(caminho_log, "rb") as entrada:
            entrada.seek(0, os.SEEK_END)
            tamanho = entrada.tell()
            inicio = desde_bytes if 0 <= desde_bytes <= tamanho else 0
            entrada.seek(max(inicio, tamanho - 20_000_000))  # no máximo os últimos 20 MB
            texto = entrada.read().decode("utf-8", "replace").replace("\r\n", "\n")
    except OSError:
        return [], desde_bytes
    projeto_normal = os.path.normcase(os.path.normpath(projeto))
    grupos = {}
    for achado in RE_TRACEBACK.finditer(texto):
        bloco = achado.group(0)
        quadros = [q for q in RE_QUADRO.finditer(bloco)
                   if os.path.normcase(os.path.normpath(q.group("arquivo"))).startswith(projeto_normal)
                   and "site-packages" not in q.group("arquivo") and ".venv" not in q.group("arquivo")]
        if not quadros:
            continue
        ultimo = quadros[-1]
        relativo = os.path.relpath(ultimo.group("arquivo"), projeto).replace("\\", "/")
        tipo = achado.group("erro").split(":")[0].strip()
        chave = f"{relativo}:{ultimo.group('linha')}:{tipo}"
        mensagem = re.sub(r"(['\"]).*?\1", "'…'", achado.group("erro"))[:160]  # sem trechos entre aspas (falas)
        grupo = grupos.setdefault(chave, {"chave": chave, "vezes": 0, "erro": mensagem, "funcao": ultimo.group("funcao"),
                                          "quadros": [f"{os.path.relpath(q.group('arquivo'), projeto).replace(chr(92), '/')}:"
                                                      f"{q.group('linha')} em {q.group('funcao')}" for q in quadros[-6:]]})
        grupo["vezes"] += 1
    erros = sorted((g for g in grupos.values() if g["vezes"] >= minimo), key=lambda g: -g["vezes"])
    return erros, tamanho


def pedido_de_correcao(erro: dict) -> str:
    return (f"Erro recorrente no log da Beatriz ({erro['vezes']} vezes): {erro['erro']}\n"
            f"Onde (do mais antigo ao que falhou):\n" + "\n".join(f"- {q}" for q in erro["quadros"]) +
            "\nAche a causa e corrija de forma preventiva, sem mudar o comportamento que já funciona. Se não for um "
            "erro do código (rede fora do ar, arquivo do usuário), trate-o com uma mensagem clara em vez de "
            "deixar a exceção subir, e explique.")


# ------------------------------------------------------------------ o agente (pedidos por voz, aprovação)
RE_PEDIR = re.compile(
    r"\b(?:se melhor\w*|(?:melhor|aperfeico)\w* (?:o |a )?(?:seu|sua|teu|tua|voce mesma|o proprio codigo|seu proprio codigo|o seu codigo)\b"
    r"|(?:evolu)\w* (?:o |a )?(?:seu|sua|voce)\b|(?:corrig|consert|arrum)\w* (?:isso|esse erro|esse problema|esse bug|essa falha)"
    r" (?:em voce|no seu codigo|no codigo|de vez|no seu sistema)|(?:muda|altera|mexe)\w* (?:n)?o seu (?:proprio )?codigo"
    r"|ach[ae] (?:um|uma) (?:jeito|forma|maneira) de (?:voce|se) \w+|(?:implementa|programa|cria)\w* (?:em|para) voce mesma)")
RE_SOBRE_AGORA = re.compile(r"\b(?:isso|esse erro|esse problema|essa falha|o que (?:acabou de|aconteceu))\b")
RE_APLICAR = re.compile(r"^(?:sim|pode|pode aplicar|aplica\w*|manda ver|confirmo|fechado|isso|ok|beleza)\b|\baplica\w* (?:a )?melhoria\b")
RE_DESCARTAR = re.compile(r"^(?:nao|melhor nao|descarta\w*|esquece|deixa pra la|cancela\w*)\b|\bdescarta\w* (?:a )?melhoria\b")
RE_O_QUE_MUDOU = re.compile(r"\b(?:o que (?:mudou|muda|voce mudou)|me (?:mostra|conta|explica) (?:o que mudou|a melhoria)|"
                            r"quais arquivos)\b")
RE_SITUACAO = re.compile(r"\b(?:como (?:esta|vai|anda) (?:a )?melhoria|o que voce (?:esta|ta) melhorando|"
                         r"(?:ja )?(?:terminou|acabou) (?:a )?melhoria|melhorias? pendentes?|status da melhoria)\b")
RE_DESFAZER = re.compile(r"\b(?:desfaz|desfazer|reverte|volta atras)\w* (?:a )?(?:ultima )?melhoria\b")


class AgenteMelhoria:
    """"Beatriz, se melhora: …" → uma melhoria no laboratório; depois, "aplica"/"descarta", "o que mudou?",
    "desfaz a última melhoria". `responder(texto, canal)` devolve a fala ou None (o roteamento segue)."""

    def __init__(self, laboratorio: Laboratorio | None = None, registro: Registro | None = None, avisar=None,
                 relogio=time.time, contexto=None, aplicar_sozinha: bool | None = None, em_segundo_plano: bool = True):
        self.laboratorio = laboratorio or Laboratorio()
        self.registro = registro or Registro()
        self.avisar = avisar  # função(texto, canal): o fim da melhoria chega por aqui
        self.relogio = relogio
        self.contexto = contexto  # função sem argumentos: a última troca da conversa ("Operador: … Beatriz: …")
        self.aplicar_sozinha = (bool(_config("EVOLUCAO_APLICAR_SOZINHA", False)) if aplicar_sozinha is None
                                else aplicar_sozinha)
        self.em_segundo_plano = em_segundo_plano
        self._trava = threading.Lock()
        self._perguntou = {"id": None, "ate": 0.0}  # o "Aplico?" de agora: o "sim" vale por SEGUNDOS_PARA_APROVAR

    # ------------------------------------------------------------ pedidos
    def esperando_resposta(self) -> bool:
        return bool(self._perguntou["id"]) and self.relogio() < self._perguntou["ate"]

    def responder(self, texto: str, canal: str = "voz") -> str | None:
        t = re.sub(r"^(?:beatriz|beatris|biatriz)\s*[,!.]?\s*", "", _sem_acentos(texto or "").strip()).strip(" ?!.")
        if not t:
            return None
        if self.esperando_resposta():
            if RE_APLICAR.search(t):
                return self.aplicar(self._perguntou["id"])
            if RE_DESCARTAR.search(t):
                return self.descartar(self._perguntou["id"])
        if RE_DESFAZER.search(t):
            return self.desfazer_ultima()
        if RE_SITUACAO.search(t):
            return self.situacao()
        if RE_O_QUE_MUDOU.search(t) and self.registro.ultima("pronta", "aplicada"):
            return self.o_que_mudou()
        if re.search(r"\baplica\w* (?:a )?melhoria\b", t):
            pronta = self.registro.ultima("pronta")
            return self.aplicar(pronta["id"]) if pronta else "Não há melhoria pronta para aplicar."
        if re.search(r"\bdescarta\w* (?:a )?melhoria\b", t):
            pronta = self.registro.ultima("pronta")
            return self.descartar(pronta["id"]) if pronta else "Não há melhoria pronta para descartar."
        if RE_PEDIR.search(t):
            contexto = ""
            if RE_SOBRE_AGORA.search(t) and self.contexto:
                try:
                    contexto = self.contexto() or ""
                except Exception:
                    contexto = ""
            return self.pedir(texto.strip(), origem="voz", canal=canal, contexto=contexto)
        return None

    def pedir(self, pedido: str, origem: str = "voz", canal: str = "voz", contexto: str = "") -> str:
        andamento = self.registro.ultima("preparando", "testando")
        if andamento and self.relogio() - andamento.get("atualizada", 0) > 2 * 3600:
            andamento.update(status="falhou", erro="interrompida (a Beatriz fechou no meio)")  # não trava para sempre
            self.registro.guardar(andamento)
            andamento = None
        # Uma de cada vez, também entre a voz e o painel (processos diferentes, o mesmo registro).
        if andamento or not self._trava.acquire(blocking=False):
            return (f"Já estou trabalhando numa melhoria ({andamento['pedido'][:80]}). Uma de cada vez: eu aviso quando terminar."
                    if andamento else "Já estou trabalhando numa melhoria. Uma de cada vez.")
        identificador = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.relogio())) + "-" + secrets.token_hex(2)
        melhoria = self.registro.guardar({"id": identificador, "pedido": pedido, "origem": origem, "canal": canal,
                                          "contexto": contexto[:2000], "status": "preparando", "criada": self.relogio(),
                                          "ramo": f"evolucao/{identificador}"})
        if self.em_segundo_plano:
            threading.Thread(target=self._trabalhar, args=(melhoria,), name="melhoria-de-codigo", daemon=True).start()
        else:
            self._trabalhar(melhoria)
        return ("Diagnóstico aceito. Vou preparar essa melhoria com o Claude, numa cópia separada do meu código, e rodar "
                "todos os testes antes. Leva alguns minutos; eu aviso quando terminar.")

    # ------------------------------------------------------------ o trabalho
    def _trabalhar(self, melhoria: dict) -> None:
        laboratorio = self.laboratorio
        try:
            laboratorio.criar_ambiente(melhoria)
            self.registro.guardar(melhoria)
            resposta = laboratorio.rodar_claude(melhoria)
            melhoria["resumo"] = str(resposta.get("resumo") or "").strip()
            if not laboratorio.guardar_mudancas(melhoria):
                motivo = str(resposta.get("motivo") or "").strip() or melhoria["resumo"] or "o Claude não achou o que mudar"
                self._terminar(melhoria, "falhou", f"Não mudei nada: {motivo}", erro=motivo)
                return
            melhoria["status"] = "testando"
            self.registro.guardar(melhoria)
            passou, testes = laboratorio.rodar_testes(melhoria["pasta"])
            melhoria["testes"] = testes
            if not passou:
                self._terminar(melhoria, "falhou", f"Preparei a mudança, mas a conferência não passou ({testes}). "
                               "Descartei; nada mudou no código em uso.", erro=testes)
                return
            laboratorio.remover_ambiente(melhoria, apagar_ramo=False)  # o ramo fica, a pasta sai
            melhoria["status"] = "pronta"
            self.registro.guardar(melhoria)
            if self.aplicar_sozinha:
                self._avisar(melhoria, f"{self._anuncio(melhoria)} {self.aplicar(melhoria['id'])}")
            else:
                self._perguntou.update(id=melhoria["id"], ate=self.relogio() + SEGUNDOS_PARA_APROVAR)
                self._avisar(melhoria, f"{self._anuncio(melhoria)} Aplico?")
        except Exception as exc:
            logger.exception("Falha na melhoria %s", melhoria.get("id"))
            self._terminar(melhoria, "falhou", f"A melhoria não deu certo: {exc}. Nada mudou no código em uso.", erro=str(exc))
        finally:
            self._trava.release()

    def _anuncio(self, melhoria: dict) -> str:
        arquivos = melhoria.get("arquivos") or []
        quantos = f"{len(arquivos)} {'arquivo' if len(arquivos) == 1 else 'arquivos'}"
        return (f"Melhoria pronta e testada: {melhoria.get('resumo') or melhoria['pedido']} Mudei {quantos}, e na cópia "
                f"separada os testes passaram ({melhoria.get('testes', '')}).")

    def _terminar(self, melhoria: dict, status: str, fala: str, erro: str = "") -> None:
        melhoria.update(status=status, erro=erro)
        try:
            self.laboratorio.remover_ambiente(melhoria)
        except Exception as exc:
            logger.warning("Não apaguei o ambiente da melhoria: %s", exc)
        self.registro.guardar(melhoria)
        self._avisar(melhoria, fala)

    def _avisar(self, melhoria: dict, texto: str) -> None:
        logger.info("Melhoria %s: %s", melhoria["id"], texto)
        if self.avisar and melhoria.get("origem") != "noite":  # a da madrugada espera a manhã (saudacao_pendente)
            try:
                self.avisar(texto, melhoria.get("canal") or "voz")
            except Exception as exc:
                logger.warning("Aviso da melhoria: %s", exc)

    # ------------------------------------------------------------ aprovar, descartar, desfazer
    def aplicar(self, melhoria_id: str) -> str:
        melhoria = self.registro.achar(melhoria_id)
        self._perguntou.update(id=None, ate=0.0)
        if not melhoria or melhoria["status"] != "pronta":
            return "Essa melhoria não está mais pronta para aplicar."
        try:
            melhoria["aplicada_em"] = self.laboratorio.aplicar(melhoria)
        except ErroDoLaboratorio as exc:
            melhoria["erro"] = str(exc)
            self.registro.guardar(melhoria)
            return f"Não apliquei: {exc}."
        melhoria["status"] = "aplicada"
        self.registro.guardar(melhoria)
        self.laboratorio.git("branch", "-D", melhoria["ramo"], verificar=False)
        return ("Aplicado no meu código. Passa a valer quando eu reiniciar; se der qualquer problema, diga "
                "\"desfaz a última melhoria\".")

    def descartar(self, melhoria_id: str) -> str:
        melhoria = self.registro.achar(melhoria_id)
        self._perguntou.update(id=None, ate=0.0)
        if not melhoria or melhoria["status"] != "pronta":
            return "Não há essa melhoria para descartar."
        melhoria["status"] = "descartada"
        self.laboratorio.remover_ambiente(melhoria)
        self.registro.guardar(melhoria)
        return "Descartada. Nada mudou no meu código."

    def desfazer_ultima(self) -> str:
        melhoria = self.registro.ultima("aplicada")
        if not melhoria:
            return "Não há melhoria aplicada para desfazer."
        try:
            melhoria["desfeita_em"] = self.laboratorio.desfazer(melhoria)
        except ErroDoLaboratorio as exc:
            return f"Não desfiz: {exc}."
        melhoria["status"] = "desfeita"
        self.registro.guardar(melhoria)
        return f"Desfeito: {melhoria['pedido'][:100]}. Volta ao que era quando eu reiniciar."

    def situacao(self) -> str:
        andamento = self.registro.ultima("preparando", "testando")
        if andamento:
            etapa = "o Claude está mexendo no código" if andamento["status"] == "preparando" else "estou rodando os testes"
            return f"Trabalhando em \"{andamento['pedido'][:100]}\": {etapa}."
        pronta = self.registro.ultima("pronta")
        if pronta:
            return f"{self._anuncio(pronta)} Diga \"aplica a melhoria\" ou \"descarta a melhoria\"."
        ultima = self.registro.ultima()
        if not ultima:
            return "Nenhuma melhoria ainda. É só dizer, por exemplo: \"Beatriz, se melhora: quando eu disser X, faça Y\"."
        return f"A última melhoria ({ultima['pedido'][:80]}) está {ESTADOS.get(ultima['status'], ultima['status'])}."

    def o_que_mudou(self) -> str:
        melhoria = self.registro.ultima("pronta", "aplicada")
        arquivos = ", ".join(melhoria.get("arquivos") or []) or "nenhum arquivo"
        return f"{melhoria.get('resumo') or melhoria['pedido']} Arquivos: {arquivos}. {melhoria.get('numeros', '')}".strip()

    def saudacao_pendente(self) -> str:
        """Para a abertura: a melhoria preparada de madrugada que espera a sua resposta."""
        pronta = self.registro.ultima("pronta")
        if not pronta or pronta.get("origem") != "noite":
            return ""
        self._perguntou.update(id=pronta["id"], ate=self.relogio() + SEGUNDOS_PARA_APROVAR)
        return f"Esta madrugada preparei uma correção preventiva. {self._anuncio(pronta)} Aplico?"
