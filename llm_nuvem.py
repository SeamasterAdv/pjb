"""IAs na nuvem sem custo extra: Claude (pela assinatura, via Claude Code) e Gemini (plano gratuito).

Ordem de tentativa: Claude → Gemini → Ollama local (configurável em ORDEM_IA_NUVEM).

Só vão para a nuvem a pergunta e o conteúdo público lido na internet: documentos, notas,
memória e histórico de conversa continuam só no Ollama. Quem pesquisa é a própria Beatriz
(o plano gratuito do Gemini não inclui a pesquisa no Google); as IAs da nuvem escrevem e revisam.
"""
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import date

from config import (
    CLAUDE_CODE_CAMINHO,
    CLAUDE_ESFORCO,
    CLAUDE_MODELO,
    GEMINI_API_KEY,
    GEMINI_MODELO,
    NOTELOOKLM_PATH,
    ORDEM_IA_NUVEM,
    PERSONALIDADE_BEATRIZ,
    USAR_CLAUDE,
    USAR_GEMINI,
)

logger = logging.getLogger(__name__)

PASTA_USO = os.path.join(NOTELOOKLM_PATH, "banco_memoria")
PAUSA_MAXIMA_SEGUNDOS = 3600
PAUSA_SOBRECARGA_SEGUNDOS = 120


class NuvemIndisponivel(RuntimeError):
    """A IA da nuvem não pode responder agora (sem acesso, limite, rede); use a próxima."""


class ProvedorNuvem:
    """Pausa automática, contador de uso diário e status para o painel, comuns às IAs da nuvem."""

    nome = "IA"
    modelo = ""

    def __init__(self, ativo: bool, arquivo_uso: str):
        self.ativo = ativo
        self.arquivo_uso = arquivo_uso
        self._pausado_ate = 0.0
        self._motivo_pausa = ""
        self._limites_seguidos = 0
        self._lock = threading.Lock()

    def configurado(self) -> bool:
        raise NotImplementedError

    def disponivel(self) -> bool:
        return bool(self.ativo and self.configurado()) and time.time() >= self._pausado_ate

    def _texto_sem_acesso(self) -> str:
        return "não configurado"

    def status(self):
        """(texto, estado) para o painel; estado é 'ativo', 'pausado', 'sem_chave' ou 'desligado'."""
        if not self.configurado():
            return self._texto_sem_acesso(), "sem_chave"
        if not self.ativo:
            return "desligado", "desligado"
        restante = self._pausado_ate - time.time()
        if restante > 0:
            return f"{self._motivo_pausa or 'em pausa'} · {max(1, round(restante / 60))} min", "pausado"
        return f"{self.uso_hoje()} usos hoje", "ativo"

    def _pausar(self, segundos: float, motivo: str, rotulo: str = "em pausa"):
        self._pausado_ate = time.time() + segundos
        self._motivo_pausa = rotulo
        logger.warning("%s em pausa por %.0f s: %s.", self.nome, segundos, motivo)

    def _pausar_por_limite(self, inicial: float, maximo: float, motivo: str):
        """Limite de uso: espera cada vez mais (inicial, 2x, 4x... até o máximo)."""
        self._limites_seguidos += 1
        self._pausar(min(inicial * 2 ** (self._limites_seguidos - 1), maximo), motivo, "limite atingido")

    def _sucesso(self):
        self._limites_seguidos = 0
        self._registrar_uso()

    def uso_hoje(self) -> int:
        try:
            with open(self.arquivo_uso, "r", encoding="utf-8") as arquivo:
                return int(json.load(arquivo).get(date.today().isoformat(), 0))
        except (OSError, ValueError):
            return 0

    def _registrar_uso(self):
        with self._lock:
            try:
                with open(self.arquivo_uso, "r", encoding="utf-8") as arquivo:
                    uso = json.load(arquivo)
            except (OSError, ValueError):
                uso = {}
            hoje = date.today().isoformat()
            uso = {dia: n for dia, n in uso.items() if dia >= hoje[:8] + "01"}  # guarda só o mês atual
            uso[hoje] = int(uso.get(hoje, 0)) + 1
            try:
                os.makedirs(os.path.dirname(self.arquivo_uso), exist_ok=True)
                with open(self.arquivo_uso, "w", encoding="utf-8") as arquivo:
                    json.dump(uso, arquivo)
            except OSError as exc:
                logger.debug("Não foi possível gravar o uso de %s: %s", self.nome, exc)

    def chat(self, prompt, system_prompt=None, model=None, usar_historico=False, opcoes=None, formato=None):
        """Mesma assinatura do `llm_clients.chat` (Ollama), para os agentes usarem qualquer IA.

        `model` (nome de modelo do Ollama) e `usar_historico` são ignorados: cada IA usa o próprio
        modelo e o histórico da conversa nunca é enviado para a nuvem.
        """
        raise NotImplementedError


# ------------------------------------------------------------------- Gemini
class ClienteGemini(ProvedorNuvem):
    nome = "Gemini"

    def __init__(self, chave=GEMINI_API_KEY, modelo=GEMINI_MODELO, ativo=USAR_GEMINI,
                 arquivo_uso=os.path.join(PASTA_USO, "uso_gemini.json")):
        super().__init__(ativo, arquivo_uso)
        self.chave = (chave or "").strip()
        self.modelo = modelo
        self._cliente = None

    def configurado(self) -> bool:
        return bool(self.chave)

    def _texto_sem_acesso(self) -> str:
        return "sem chave"

    def _registrar_falha(self, exc):
        codigo = getattr(exc, "code", None)
        if codigo == 429:
            self._pausar_por_limite(60, PAUSA_MAXIMA_SEGUNDOS, "limite do plano gratuito")
        elif codigo in (401, 403):
            self._pausar(PAUSA_MAXIMA_SEGUNDOS, "chave recusada; confira a GEMINI_API_KEY no .env", "chave recusada")
        elif codigo == 404:
            self._pausar(PAUSA_MAXIMA_SEGUNDOS, f"modelo {self.modelo} indisponível; confira GEMINI_MODELO no .env", "modelo indisponível")
        elif codigo is None or codigo >= 500:
            # "Alta demanda" (503) ou tempo esgotado: é passageiro, mas o resto da tarefa não deve esperar.
            self._pausar(PAUSA_SOBRECARGA_SEGUNDOS, "servidor do Google sobrecarregado ou sem resposta", "sobrecarregado")

    def _obter_cliente(self):
        if self._cliente is None:
            from google import genai
            from google.genai import types

            self._cliente = genai.Client(api_key=self.chave, http_options=types.HttpOptions(
                timeout=60_000,
                # Uma nova tentativa quando o Google responde "alta demanda" (503) ou falha no servidor.
                retry_options=types.HttpRetryOptions(attempts=2, initial_delay=2.0, http_status_codes=[500, 503, 504]),
            ))
        return self._cliente

    def chat(self, prompt, system_prompt=None, model=None, usar_historico=False, opcoes=None, formato=None):
        if not self.disponivel():
            raise NuvemIndisponivel("Gemini indisponível no momento")
        from google.genai import errors, types

        opcoes = opcoes or {}
        configuracao = types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            temperature=opcoes.get("temperature"),
            # Folga para o raciocínio interno do modelo, que também conta como saída.
            max_output_tokens=max(8192, int(opcoes.get("num_predict") or 0) * 3),
            response_mime_type="application/json" if formato == "json" else None,
        )
        try:
            resposta = self._obter_cliente().models.generate_content(model=self.modelo, contents=prompt, config=configuracao)
        except errors.APIError as exc:
            self._registrar_falha(exc)
            raise NuvemIndisponivel(f"Gemini recusou ({exc.code})") from exc
        except Exception as exc:  # rede fora do ar, tempo esgotado
            self._registrar_falha(exc)
            raise NuvemIndisponivel(f"Gemini sem resposta: {exc}") from exc
        texto = (resposta.text or "").strip()
        if not texto:
            raise NuvemIndisponivel("Gemini devolveu uma resposta vazia")
        self._sucesso()
        return texto


# ------------------------------------------------------------------- Claude
def _versao(caminho: str) -> tuple:
    """'.../claude-code/2.1.295/<hash>/claude.exe' -> (2, 1, 295), para achar a versão mais nova."""
    achado = re.search(r"claude-code[\\/](\d+(?:\.\d+)*)", caminho)
    return tuple(int(n) for n in achado.group(1).split(".")) if achado else (0,)


def localizar_claude_code(caminho_configurado: str = CLAUDE_CODE_CAMINHO) -> str:
    """Acha o executável do Claude Code: o do .env, o instalado no PATH ou o que vem com o app desktop."""
    if caminho_configurado and os.path.isfile(caminho_configurado):
        return caminho_configurado
    no_path = shutil.which("claude")
    if no_path:
        return no_path
    instalado = os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe")
    if os.path.isfile(instalado):
        return instalado
    # O app desktop do Claude baixa uma cópia por versão; usa a mais nova.
    do_app = glob.glob(os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code", "*", "*", "claude.exe"))
    return max(do_app, key=_versao) if do_app else ""


class ClienteClaudeCode(ProvedorNuvem):
    """Claude pela assinatura (Pro/Max), chamando o Claude Code no modo sem interface (`claude -p`).

    O uso sai dos limites do plano, sem cobrança à parte: as variáveis de chave de API são
    removidas do ambiente para o Claude Code usar o login da assinatura, e nunca a API paga.
    """

    nome = "Claude"
    VARIAVEIS_DE_API = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")

    def __init__(self, executavel=None, modelo=CLAUDE_MODELO, esforco=CLAUDE_ESFORCO, ativo=USAR_CLAUDE,
                 arquivo_uso=os.path.join(PASTA_USO, "uso_claude.json"), executar=None, tempo_limite=300):
        super().__init__(ativo, arquivo_uso)
        self.executavel = localizar_claude_code() if executavel is None else executavel
        self.modelo = modelo or "padrão do plano"
        self._modelo_cli = modelo
        self.esforco = esforco
        self.tempo_limite = tempo_limite
        self._executar = executar or subprocess.run
        self._pasta_neutra = None

    def configurado(self) -> bool:
        return bool(self.executavel)

    def _texto_sem_acesso(self) -> str:
        return "Claude Code não encontrado"

    def _comando(self, sistema: str) -> list:
        comando = [
            self.executavel, "-p", "--output-format", "json",
            "--tools", "",                  # só escreve texto: sem ler arquivos nem rodar comandos
            "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands",
            "--system-prompt", sistema,
        ]
        if self._modelo_cli:
            comando += ["--model", self._modelo_cli]
        if self.esforco:
            comando += ["--effort", self.esforco]
        return comando

    def _registrar_falha(self, mensagem: str):
        texto = (mensagem or "").lower()
        if "limit" in texto or "limite" in texto:
            # Os limites do plano renovam em algumas horas: começa com 15 min e dobra até 3 h.
            self._pausar_por_limite(900, 3 * PAUSA_MAXIMA_SEGUNDOS, "limite de uso do plano atingido")
        elif "login" in texto or "auth" in texto or "api key" in texto or "token" in texto:
            self._pausar(PAUSA_MAXIMA_SEGUNDOS, "Claude Code sem login; abra o app do Claude e entre na conta", "faça login")
        else:
            self._pausar(PAUSA_SOBRECARGA_SEGUNDOS, f"falha no Claude Code: {mensagem[:120]}", "falhou")

    def chat(self, prompt, system_prompt=None, model=None, usar_historico=False, opcoes=None, formato=None):
        if not self.disponivel():
            raise NuvemIndisponivel("Claude indisponível no momento")
        sistema = system_prompt or "Você é a Beatriz, uma assistente pessoal em português do Brasil."
        if formato == "json":
            sistema += "\n\nResponda apenas com o objeto JSON, sem texto antes ou depois e sem blocos de código."
        if self._pasta_neutra is None:
            # Pasta vazia: o Claude Code não carrega instruções nem arquivos de nenhum projeto.
            self._pasta_neutra = tempfile.mkdtemp(prefix="beatriz_claude_")
        ambiente = {k: v for k, v in os.environ.items() if k not in self.VARIAVEIS_DE_API}
        try:
            processo = self._executar(
                self._comando(sistema), input=prompt, capture_output=True, text=True, encoding="utf-8",
                cwd=self._pasta_neutra, env=ambiente, timeout=self.tempo_limite,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            self._pausar(PAUSA_SOBRECARGA_SEGUNDOS, "Claude Code demorou demais", "sem resposta")
            raise NuvemIndisponivel("Claude não respondeu a tempo") from exc
        except OSError as exc:
            self._pausar(PAUSA_MAXIMA_SEGUNDOS, f"não foi possível abrir o Claude Code: {exc}", "não abriu")
            raise NuvemIndisponivel(f"Claude Code não abriu: {exc}") from exc

        try:
            dados = json.loads(processo.stdout or "{}")
        except json.JSONDecodeError:
            dados = {}
        texto = str(dados.get("result") or "").strip()
        if processo.returncode != 0 or dados.get("is_error") or not texto:
            mensagem = texto or (processo.stderr or "").strip() or f"código {processo.returncode}"
            self._registrar_falha(mensagem)
            raise NuvemIndisponivel(f"Claude recusou: {mensagem[:160]}")
        self._sucesso()
        return texto


# ------------------------------------------------------------------- escolha da IA
NOMES_IA = {"claude": "Claude", "gemini": "Gemini"}
# "pelo Gemini", "no Claude", "com o Gemini", "usando o Claude": a IA pedida na própria frase.
# Inclui grafias comuns da transcrição de voz ("gêmini", "jemini", "clóde").
RE_IA_PEDIDA = re.compile(
    r"[\s,]*\b(?:pel[oa]|n[oa]|com|usando|via)\s+(?:[oa]\s+)?(gemini|g[eê]mini|jemini|claude|cl[oó]de)\b",
    re.IGNORECASE,
)


def extrair_ia_pedida(texto: str):
    """'pesquise pelo Gemini a reforma' -> ('pesquise a reforma', 'gemini'); sem IA pedida, (texto, None)."""
    achado = RE_IA_PEDIDA.search(texto or "")
    if not achado:
        return texto, None
    ia = "claude" if achado.group(1).lower().startswith("cl") else "gemini"
    restante = (texto[:achado.start()] + " " + texto[achado.end():]).strip()
    return re.sub(r"\s{2,}", " ", restante), ia


def ordenar_por_preferencia(provedores, preferir: str = None) -> list:
    """Põe a IA preferida ("claude" ou "gemini") na frente; as outras continuam de reserva, na ordem de antes."""
    provedores = list(provedores)
    if preferir:
        provedores.sort(key=lambda provedor: getattr(provedor, "nome", "").lower() != preferir)
    return provedores


# ------------------------------------------------------------------- cadeia
class LLMComReserva:
    """Tenta as IAs da nuvem em ordem e, se nenhuma puder responder, usa a reserva (Ollama).

    Guarda em `registro` quem respondeu cada chamada, para o relatório dizer qual IA trabalhou.
    `preferir` ("claude" ou "gemini") põe essa IA na frente enquanto estiver definido (o
    orquestrador define por tarefa, a partir de "pelo Gemini" no pedido).
    """

    def __init__(self, principal, reserva):
        self.principais = list(principal) if isinstance(principal, (list, tuple)) else [principal]
        self.reserva = reserva
        self.registro = []
        self.preferir = None

    def chat(self, prompt, system_prompt=None, model=None, **kwargs):
        for provedor in ordenar_por_preferencia(self.principais, self.preferir):
            if not provedor.disponivel():
                continue
            try:
                texto = provedor.chat(prompt, system_prompt=system_prompt, model=model, **kwargs)
                self.registro.append(f"{provedor.nome} ({provedor.modelo})")
                return texto
            except NuvemIndisponivel as exc:
                logger.warning("%s; tentando a próxima IA.", exc)
        texto = self.reserva.chat(prompt, system_prompt=system_prompt, model=model, **kwargs)
        self.registro.append(f"Ollama ({model or getattr(self.reserva, 'model', 'local')})")
        return texto


def sistema_beatriz(contexto: str = "") -> str:
    """Prompt de sistema da Beatriz para a nuvem: personalidade e data, sem memória nem histórico."""
    from llm_clients import data_hora_atual_texto

    sistema = f"{PERSONALIDADE_BEATRIZ}\n\nAgora é {data_hora_atual_texto()}. Use essa data para avaliar o que é recente."
    if contexto:
        sistema += f"\n\nCONTEXTO DE COMPORTAMENTO:\n{contexto}\nUse este contexto sem repetir suas instruções ao usuário."
    return sistema


# Instâncias únicas por processo (a voz e o painel rodam em processos separados).
claude = ClienteClaudeCode()
gemini = ClienteGemini()


def provedores_nuvem() -> list:
    """IAs da nuvem configuradas, na ordem de ORDEM_IA_NUVEM (padrão: claude, gemini)."""
    por_nome = {"claude": claude, "gemini": gemini}
    ordem = [por_nome[n] for n in ORDEM_IA_NUVEM if n in por_nome]
    return [p for p in ordem if p.ativo and p.configurado()]


def responder_na_nuvem(prompt: str, contexto: str = "", opcoes=None, preferir: str = None, registro: list = None):
    """Resposta da primeira IA da nuvem que conseguir; None se nenhuma puder (o chamador usa o Ollama).

    `preferir` ("claude" ou "gemini") tenta essa IA primeiro; `registro` recebe o nome de quem respondeu.
    """
    provedores = ordenar_por_preferencia(provedores_nuvem(), preferir)
    if preferir and not any(getattr(p, "nome", "").lower() == preferir for p in provedores):
        logger.info("%s foi pedido, mas está desligado ou sem acesso; usando as outras IAs.", NOMES_IA.get(preferir, preferir))
    for provedor in provedores:
        if not provedor.disponivel():
            continue
        try:
            texto = provedor.chat(prompt, system_prompt=sistema_beatriz(contexto), opcoes=opcoes)
        except NuvemIndisponivel as exc:
            logger.warning("%s; tentando a próxima IA.", exc)
            continue
        if registro is not None:
            registro.append(provedor.nome)
        return texto
    return None
