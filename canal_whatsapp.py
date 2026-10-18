"""Comandos para a Beatriz pelo WhatsApp, pela conversa "Mensagem para você" (a sua própria conversa).

Você escreve do celular nessa conversa; a Beatriz lê pelo WhatsApp Web, faz o pedido no PC e
responde na mesma conversa, com as respostas começando por "◆ Beatriz". Só essa conversa é lida,
então só você consegue mandar comandos.

O WhatsApp Web roda num Chromium próprio (Playwright), num processo separado, com o login salvo em
banco_memoria/whatsapp_web. Na primeira vez a janela mostra o QR code: no celular, abra
WhatsApp > Aparelhos conectados > Conectar um aparelho. Depois a janela fica minimizada.

Para testar sozinho (sem o resto da Beatriz):
    .venv312\\Scripts\\python.exe canal_whatsapp.py
"""
import logging
import multiprocessing
import os
import queue
import re
import time
from collections import deque
from datetime import datetime

logger = logging.getLogger(__name__)

PREFIXO_RESPOSTA = "◆ Beatriz"
URL_WHATSAPP = "https://web.whatsapp.com"
INTERVALO_LEITURA = 1.0  # segundos entre uma leitura da conversa e outra
LIMITE_TEXTO = 4000      # respostas maiores vão em mais de uma mensagem

# Na abertura a caixa de texto aparece antes das mensagens, e o histórico chega aos poucos. Até a
# conversa parar de mudar ("aquecimento"), tudo o que aparece nela é histórico, não comando.
LEITURAS_ESTAVEIS = 3            # leituras seguidas iguais para a conversa contar como carregada
AQUECIMENTO_MINIMO = 5           # segundos, com mensagens na tela
AQUECIMENTO_SEM_MENSAGENS = 15   # segundos, com a conversa vazia (ela pode estar só carregando)
AQUECIMENTO_MAXIMO = 30          # segundos; depois disso fica pronta mesmo com a conversa mudando
FOLGA_RELOGIO = 60               # segundos de diferença aceita entre o relógio do celular e o do PC

# Leitura das mensagens visíveis da conversa aberta (só as de fora, não as citadas dentro de outra).
# O atributo data-pre-plain-text ("[10:41, 14/10/2026] Felipe: ") existe há anos no WhatsApp Web e
# marca as mensagens de texto; dele vem o horário.
JS_LER_MENSAGENS = """
() => Array.from(document.querySelectorAll('#main [data-id]'))
    .filter(el => !el.parentElement.closest('[data-id]'))
    .map(el => {
        const texto = el.querySelector('.copyable-text[data-pre-plain-text]');
        const corpo = texto ? (texto.querySelector('span.selectable-text') || texto) : null;
        return {id: el.getAttribute('data-id'), texto: corpo ? corpo.innerText : null,
                quando: texto ? texto.getAttribute('data-pre-plain-text') : null};
    })
"""
# O número da conta conectada fica no armazenamento local do WhatsApp Web ("5511...:12@c.us").
JS_MEU_NUMERO = """
() => localStorage.getItem('last-wid-md') || localStorage.getItem('last-wid') || ''
"""

SELETOR_LOGADO = "#pane-side"
SELETOR_QR = "canvas[aria-label], div[data-ref]"
SELETOR_CAIXA = '#main footer div[contenteditable="true"]'
SELETORES_ANEXAR = (
    '#main footer [aria-label="Anexar"]', '#main footer [aria-label="Attach"]',
    '#main footer [title="Anexar"]', '#main footer [data-icon="plus-rounded"]', '#main footer [data-icon="clip"]',
)
# Na prévia do anexo o botão se chama "Enviar 1 item selecionado" (out/2026).
SELETOR_ENVIAR_ARQUIVO = '[aria-label^="Enviar"], [aria-label^="Send"]'


# ----------------------------------------------------------------- lógica pura
def numero_do_wid(valor: str) -> str:
    """'"5511987654321:23@c.us"' -> '5511987654321'."""
    achado = re.search(r"(\d{10,15})", valor or "")
    return achado.group(1) if achado else ""


def para_formato_whatsapp(texto: str) -> str:
    """Markdown do LLM para a formatação do WhatsApp (**negrito** -> *negrito*, títulos em negrito)."""
    texto = re.sub(r"\*\*(.+?)\*\*", r"*\1*", texto or "")
    texto = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"*\1*", texto)
    return texto.strip()


def dividir_mensagem(texto: str, limite: int = LIMITE_TEXTO) -> list:
    """Quebra respostas longas em partes, de preferência entre parágrafos."""
    partes, atual = [], ""
    for paragrafo in texto.split("\n\n"):
        candidato = f"{atual}\n\n{paragrafo}" if atual else paragrafo
        if len(candidato) <= limite:
            atual = candidato
            continue
        if atual:
            partes.append(atual)
        while len(paragrafo) > limite:
            partes.append(paragrafo[:limite])
            paragrafo = paragrafo[limite:]
        atual = paragrafo
    if atual:
        partes.append(atual)
    return partes


RE_HORARIO = re.compile(r"\[(\d{1,2}):(\d{2})(?:\s*([ap])\.?\s*m\.?)?,\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\]", re.I)


def horario_da_mensagem(pre_texto: str):
    """'[18:23, 14/10/2026] Felipe: ' -> início daquele minuto (como time.time()), ou None.

    Aceita também o formato em inglês ('[6:23 PM, 10/14/2026]'), se o WhatsApp abrir em outro idioma.
    """
    achado = RE_HORARIO.search(pre_texto or "")
    if not achado:
        return None
    hora, minuto, periodo, primeiro, segundo, ano = achado.groups()
    hora, minuto, ano = int(hora), int(minuto), int(ano) + (2000 if len(ano) == 2 else 0)
    if periodo:  # 12 h vem com a data no formato americano (mês/dia)
        hora = hora % 12 + (12 if periodo.lower() == "p" else 0)
        datas = ((int(segundo), int(primeiro)), (int(primeiro), int(segundo)))
    else:
        datas = ((int(primeiro), int(segundo)), (int(segundo), int(primeiro)))
    for dia, mes in datas:
        try:
            return datetime(ano, mes, dia, hora, minuto).timestamp()
        except ValueError:
            continue
    return None


def chave_da_mensagem(identificador: str) -> str:
    """Parte estável do data-id: 'true_5511987654321@c.us_3EB0AB12' -> '3EB0AB12'.

    O começo (de quem é a conversa) pode mudar de formato com a conversa aberta (@c.us -> @lid);
    o fim é o id da própria mensagem.
    """
    achado = re.match(r"^(?:true|false)_[^_]+_(.+)$", identificador or "")
    return achado.group(1) if achado else (identificador or "")


class LeitorDeMensagens:
    """Decide quais mensagens visíveis da conversa são comandos novos.

    Só vale o que chegou depois de a conversa ficar pronta. Na abertura a caixa de texto aparece antes
    das mensagens e o histórico chega em partes, então a primeira leitura não serve de linha de base:
    até a conversa parar de mudar (aquecimento), tudo o que aparece é histórico. Depois disso, também
    é histórico o que aparecer acima de uma mensagem do histórico (mensagens antigas carregadas depois)
    ou com horário de antes de a conversa ficar pronta (mandadas com a Beatriz desligada e
    sincronizadas depois). Ignora ainda o que ela já leu e as próprias respostas (que começam com o
    prefixo ou acabaram de ser enviadas por ela).
    """

    def __init__(self):
        self.vistos = set()
        self.historico = set()
        self.enviados = deque(maxlen=30)
        self.pronto_em = None
        self._inicio = None
        self._ultima_leitura = None
        self._leituras_iguais = 0

    @property
    def pronto(self) -> bool:
        return self.pronto_em is not None

    @staticmethod
    def _normalizar(texto: str) -> str:
        # Sem os marcadores de formatação: "*negrito*" chega lido da tela como "negrito".
        return re.sub(r"\s+", " ", re.sub(r"[*_~`]", "", texto or "")).strip()

    def registrar_envio(self, texto: str):
        self.enviados.append(self._normalizar(texto))

    def _aquecer(self, chaves: list, agora: float):
        if self._inicio is None:
            self._inicio = agora
        self.vistos.update(chaves)
        self.historico.update(chaves)
        self._leituras_iguais = self._leituras_iguais + 1 if chaves == self._ultima_leitura else 1
        self._ultima_leitura = chaves
        decorrido = agora - self._inicio
        minimo = AQUECIMENTO_MINIMO if chaves else AQUECIMENTO_SEM_MENSAGENS
        if (self._leituras_iguais >= LEITURAS_ESTAVEIS and decorrido >= minimo) or decorrido >= AQUECIMENTO_MAXIMO:
            self.pronto_em = agora

    def _antes_de_pronto(self, quando: str) -> bool:
        # O horário só tem os minutos: é antiga se o minuto inteiro, mais a folga entre os relógios,
        # terminou antes de a conversa ficar pronta.
        horario = horario_da_mensagem(quando)
        return horario is not None and horario + 60 + FOLGA_RELOGIO <= self.pronto_em

    def novas(self, mensagens: list, agora: float = None) -> list:
        """mensagens: [{"id", "texto", "quando"}] na ordem da tela. Devolve os textos que são comandos novos."""
        agora = time.time() if agora is None else agora
        chaves = [chave_da_mensagem(m.get("id")) for m in mensagens]
        if not self.pronto:
            self._aquecer([c for c in chaves if c], agora)
            return []
        # Posição da última mensagem do histórico na tela: o que aparece acima dela é mais antigo.
        limite = max((posicao for posicao, chave in enumerate(chaves) if chave in self.historico), default=-1)
        comandos, antigas = [], 0
        for posicao, (mensagem, chave) in enumerate(zip(mensagens, chaves)):
            if not chave or chave in self.vistos:
                continue
            self.vistos.add(chave)
            if posicao < limite or self._antes_de_pronto(mensagem.get("quando")):
                self.historico.add(chave)
                antigas += 1
                continue
            texto = self._normalizar(mensagem.get("texto"))
            if not texto or texto.startswith(PREFIXO_RESPOSTA) or texto in self.enviados:
                continue
            comandos.append(texto)
        if antigas:
            logger.info("WhatsApp: %d mensagem(ns) antiga(s) ignorada(s) (carregadas depois da abertura).", antigas)
        return comandos


# ----------------------------------------------------------- processo do navegador
def _definir_estado(estado, status: str, detalhe: str = ""):
    if estado is None:
        return
    try:
        estado["whatsapp"] = {"status": status, "detalhe": detalhe, "em": time.time()}
    except Exception:
        pass  # o gerenciador pode ter sido encerrado junto com o programa


def _minimizar(pagina):
    """Minimiza a janela do Chromium (os argumentos de abertura evitam que ele pare em segundo plano)."""
    try:
        cdp = pagina.context.new_cdp_session(pagina)
        janela = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": janela, "bounds": {"windowState": "minimized"}})
    except Exception as exc:
        logger.debug("Não foi possível minimizar a janela do WhatsApp: %s", exc)


def _aguardar_login(pagina, estado):
    avisado = False
    while not pagina.query_selector(SELETOR_LOGADO):
        if pagina.query_selector(SELETOR_QR) and not avisado:
            _definir_estado(estado, "aguardando QR", "Leia o QR code: WhatsApp > Aparelhos conectados > Conectar um aparelho.")
            avisado = True
        pagina.wait_for_timeout(1500)
    _definir_estado(estado, "conectando", "Abrindo a conversa Mensagem para você...")


def _abrir_minha_conversa(pagina, numero: str):
    pagina.goto(f"{URL_WHATSAPP}/send?phone={numero}", wait_until="domcontentloaded")
    pagina.wait_for_selector(SELETOR_CAIXA, timeout=90_000)


def _enviar_texto(pagina, texto: str):
    caixa = pagina.locator(SELETOR_CAIXA).last
    caixa.focus()
    for indice, linha in enumerate(texto.split("\n")):
        if indice:
            pagina.keyboard.press("Shift+Enter")
        if linha:
            pagina.keyboard.insert_text(linha)
    pagina.keyboard.press("Enter")
    pagina.wait_for_timeout(400)


def _ultima_mensagem(pagina) -> str:
    mensagens = pagina.evaluate(JS_LER_MENSAGENS)
    return mensagens[-1]["id"] if mensagens else ""


def _enviar_arquivo(pagina, caminho: str) -> bool:
    """Anexa um arquivo como documento e confere que ele apareceu na conversa.

    Menu Anexar > "Documento" abre o seletor de arquivos do sistema, que o Playwright responde.
    (A única entrada de arquivo que existe antes disso é a de figurinhas, que só aceita imagens.)
    """
    anexar = next((pagina.locator(s).first for s in SELETORES_ANEXAR if pagina.locator(s).count()), None)
    if anexar is None:
        return False
    antes = _ultima_mensagem(pagina)
    anexar.click()
    documento = pagina.get_by_role("button", name="Documento").or_(pagina.get_by_text("Documento", exact=True)).first
    with pagina.expect_file_chooser(timeout=10_000) as escolha:
        documento.click()
    escolha.value.set_files(caminho)
    pagina.locator(SELETOR_ENVIAR_ARQUIVO).first.click(timeout=20_000)
    for _ in range(30):  # até 15 s para o documento aparecer como mensagem nova
        pagina.wait_for_timeout(500)
        if _ultima_mensagem(pagina) != antes:
            return True
    return False


def mensagens_de_resposta(texto: str) -> list:
    """Resposta pronta para enviar: cada parte começa com o prefixo, para nunca ser lida como comando."""
    partes = dividir_mensagem(para_formato_whatsapp(texto), LIMITE_TEXTO - 30)
    total = len(partes)
    return [f"{PREFIXO_RESPOSTA}{f' ({n}/{total})' if total > 1 else ''}: {parte}" for n, parte in enumerate(partes, start=1)]


def _enviar(pagina, leitor: LeitorDeMensagens, pedido: dict):
    for parte in mensagens_de_resposta(pedido.get("texto") or ""):
        leitor.registrar_envio(parte)
        _enviar_texto(pagina, parte)
    for caminho in pedido.get("arquivos") or []:
        if not os.path.isfile(caminho):
            continue
        try:
            if _enviar_arquivo(pagina, caminho):
                continue
        except Exception as exc:
            logger.warning("Falha ao anexar %s no WhatsApp: %s", caminho, exc)
            pagina.keyboard.press("Escape")
        aviso = f"{PREFIXO_RESPOSTA}: não consegui anexar {os.path.basename(caminho)}; ele está no PC em {caminho}"
        leitor.registrar_envio(aviso)
        _enviar_texto(pagina, aviso)


def executar_navegador(fila_comandos, fila_envios, estado, pasta_perfil: str, numero_configurado: str = ""):
    """Processo do WhatsApp Web: lê a conversa consigo mesmo e envia as respostas da Beatriz."""
    from playwright.sync_api import sync_playwright

    logging.basicConfig(level=logging.INFO)
    os.makedirs(pasta_perfil, exist_ok=True)
    with sync_playwright() as playwright:
        contexto = playwright.chromium.launch_persistent_context(
            pasta_perfil,
            headless=False,
            viewport={"width": 1100, "height": 800},
            args=[
                "--window-size=1120,900",
                # Sem isso o Chromium desacelera a página minimizada e as mensagens demoram a chegar.
                "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows", "--disable-features=CalculateNativeWinOcclusion",
            ],
        )
        pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
        leitor = LeitorDeMensagens()
        while True:
            try:
                _definir_estado(estado, "abrindo", "Abrindo o WhatsApp Web...")
                pagina.goto(URL_WHATSAPP, wait_until="domcontentloaded")
                _aguardar_login(pagina, estado)
                numero = numero_configurado or numero_do_wid(pagina.evaluate(JS_MEU_NUMERO))
                if not numero:
                    _definir_estado(estado, "erro", "Não achei o seu número; coloque WHATSAPP_MEU_NUMERO no .env.")
                    pagina.wait_for_timeout(60_000)
                    continue
                _abrir_minha_conversa(pagina, numero)
                _minimizar(pagina)
                leitor = LeitorDeMensagens()
                _definir_estado(estado, "conectando", "Carregando a conversa; o que já está nela não vira comando...")
                while True:
                    while True:
                        try:
                            _enviar(pagina, leitor, fila_envios.get_nowait())
                        except queue.Empty:
                            break
                    if not pagina.query_selector(SELETOR_CAIXA):
                        raise RuntimeError("a conversa foi fechada ou o WhatsApp desconectou")
                    aquecendo = not leitor.pronto
                    for comando in leitor.novas(pagina.evaluate(JS_LER_MENSAGENS)):
                        fila_comandos.put(comando)
                    if aquecendo and leitor.pronto:
                        logger.info("WhatsApp: conversa carregada (%d mensagens do histórico ignoradas).", len(leitor.historico))
                        _definir_estado(estado, "conectado", "Mande comandos na conversa Mensagem para você.")
                    pagina.wait_for_timeout(int(INTERVALO_LEITURA * 1000))
            except Exception as exc:
                if "Target page, context or browser has been closed" in str(exc):
                    _definir_estado(estado, "desligado", "A janela do WhatsApp foi fechada.")
                    return
                logger.warning("WhatsApp Web: %s; reabrindo em 10 s.", exc)
                _definir_estado(estado, "reconectando", str(exc)[:120])
                time.sleep(10)


# ------------------------------------------------------------- lado da Beatriz
class CanalWhatsApp:
    """Liga o processo do WhatsApp Web ao laço principal: comandos chegam por `receber`, respostas saem por `enviar`."""

    def __init__(self, estado=None, pasta_perfil: str = None, numero: str = ""):
        from config import NOTELOOKLM_PATH

        self.estado = estado
        self.pasta_perfil = pasta_perfil or os.path.join(NOTELOOKLM_PATH, "banco_memoria", "whatsapp_web")
        self.numero = numero
        self.fila_comandos = multiprocessing.Queue()
        self.fila_envios = multiprocessing.Queue()
        self.processo = None

    def iniciar(self):
        self.processo = multiprocessing.Process(
            target=executar_navegador,
            args=(self.fila_comandos, self.fila_envios, self.estado, self.pasta_perfil, self.numero),
            daemon=True,
        )
        self.processo.start()
        logger.info("Canal do WhatsApp iniciado (processo %s).", self.processo.pid)

    def receber(self) -> str:
        """Próximo comando que chegou pelo WhatsApp, ou "" se não houver nenhum."""
        try:
            return self.fila_comandos.get_nowait()
        except queue.Empty:
            return ""

    def enviar(self, texto: str = "", arquivos=()):
        self.fila_envios.put({"texto": texto, "arquivos": [str(a) for a in arquivos]})

    def status(self) -> dict:
        try:
            return dict(self.estado.get("whatsapp") or {}) if self.estado is not None else {}
        except Exception:
            return {}


if __name__ == "__main__":
    # Teste isolado: ecoa cada comando recebido, sem o resto da Beatriz.
    multiprocessing.freeze_support()
    estado = multiprocessing.Manager().dict()
    canal = CanalWhatsApp(estado)
    canal.iniciar()
    ultimo = None
    while canal.processo.is_alive():
        situacao = canal.status()
        if situacao and situacao != ultimo:
            print(f"[WhatsApp] {situacao.get('status')}: {situacao.get('detalhe')}")
            ultimo = situacao
        comando = canal.receber()
        if comando:
            print(f"Comando recebido: {comando}")
            if comando.lower().startswith("teste arquivo"):
                exemplo = os.path.join(canal.pasta_perfil, "..", "teste_whatsapp.txt")
                with open(exemplo, "w", encoding="utf-8") as arquivo:
                    arquivo.write("Arquivo de teste enviado pela Beatriz.\n")
                canal.enviar("segue o arquivo de teste:", [os.path.abspath(exemplo)])
            else:
                canal.enviar(f"recebi o seu comando: {comando}")
        time.sleep(0.5)
