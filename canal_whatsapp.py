"""Comandos para a Beatriz pelo WhatsApp, pela conversa "Mensagem para você" (a sua própria conversa).

Você escreve do celular nessa conversa; a Beatriz lê pelo WhatsApp Web, faz o pedido no PC e
responde na mesma conversa, com as respostas começando por "◆ Beatriz". Só essa conversa é lida,
então só você consegue mandar comandos.

Documentos e fotos que você manda nela são baixados para a pasta de recebidos (`PASTA_RECEBIDOS`) e viram um aviso para o
agente de fontes (`fontes.py`): "vira fonte", "arquiva", "imprime", "escaneia"... Os arquivos que a própria Beatriz manda de
volta nunca são tratados como seus. Os seletores do WhatsApp Web mudam com o tempo; se o download falhar, o log guarda o
esqueleto da mensagem (sem o texto) para ajustar `SELETOR_DOCUMENTO`.

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
# marca as mensagens de texto; dele vem o horário. Documentos e fotos aparecem com um `anexo`
# ({tipo: documento|imagem|audio|video, nome}); a legenda, se houver, vem em `texto`.
JS_LER_MENSAGENS = r"""
() => Array.from(document.querySelectorAll('#main [data-id]'))
    .filter(el => !el.parentElement.closest('[data-id]'))
    .map(el => {
        const texto = el.querySelector('.copyable-text[data-pre-plain-text]');
        const corpo = texto ? (texto.querySelector('span.selectable-text') || texto) : null;
        // Só o que é da própria mensagem: o documento ou a foto citados numa resposta não contam.
        const citada = 'div[data-testid="quoted-message"], [aria-label^="Mensagem citada"], [aria-label^="Quoted"]';
        const buscar = (seletor) => Array.from(el.querySelectorAll(seletor))
            .find(x => x.closest('[data-id]') === el && !x.closest(citada)) || null;
        let anexo = null;
        if (buscar('[data-icon^="document-"], [data-icon="document"], [title^="Baixar"], [title^="Download"], [aria-label^="Baixar"], [aria-label^="Download"]')) {
            const baixar = buscar('[title^="Baixar"], [title^="Download"]');
            let nome = baixar ? (baixar.getAttribute('title') || '').replace(/^(Baixar|Download)\s*"?/i, '').replace(/"$/, '') : '';
            if (!nome) {
                const candidato = Array.from(el.querySelectorAll('span[title], span[dir="auto"]'))
                    .filter(x => x.closest('[data-id]') === el && !x.closest(citada))
                    .map(x => (x.getAttribute('title') || x.innerText || '').trim()).find(x => /\.[A-Za-z0-9]{2,5}$/.test(x));
                nome = candidato || '';
            }
            anexo = {tipo: 'documento', nome: nome};
        } else if (buscar('img[src^="blob:"]')) {
            anexo = {tipo: 'imagem', nome: ''};
        } else if (buscar('audio, [data-icon="audio-play"], [data-icon="ptt-status"], [data-icon="audio-download"]')) {
            anexo = {tipo: 'audio', nome: ''};
        } else if (buscar('video, [data-icon="media-play"], [data-icon="video-pip"]')) {
            anexo = {tipo: 'video', nome: ''};
        }
        return {id: el.getAttribute('data-id'), texto: corpo ? corpo.innerText : null,
                quando: texto ? texto.getAttribute('data-pre-plain-text') : null, anexo: anexo};
    })
"""
# A foto da conversa é um blob do próprio WhatsApp Web: o navegador a lê e devolve em base64.
JS_BAIXAR_IMAGEM = r"""
async (id) => {
    const img = document.querySelector('[data-id="' + id + '"] img[src^="blob:"]');
    if (!img) return null;
    const resposta = await fetch(img.src);
    const blob = await resposta.blob();
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let texto = '';
    for (let i = 0; i < bytes.length; i += 0x8000) texto += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return {tipo: blob.type, base64: btoa(texto)};
}
"""
# Só a forma da mensagem (tags, ícones, rótulos), para ajustar os seletores quando o WhatsApp Web mudar; sem o texto das conversas.
JS_ESQUELETO = r"""
(id) => {
    const raiz = document.querySelector('[data-id="' + id + '"]');
    const forma = (e, d) => {
        if (!e || d > 14) return '';
        const marcas = ['role', 'data-icon', 'data-testid', 'aria-label', 'title'].map(a => e.getAttribute && e.getAttribute(a) ? a + '=' + e.getAttribute(a).slice(0, 40) : '').filter(Boolean);
        return '  '.repeat(d) + e.tagName.toLowerCase() + (marcas.length ? ' [' + marcas.join(' ') + ']' : '') + '\n'
            + Array.from(e.children).map(f => forma(f, d + 1)).join('');
    };
    return raiz ? forma(raiz, 0) : '';
}
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
# Documento recebido: clicar nele (ou na seta de baixar) faz o navegador baixar o arquivo.
SELETOR_DOCUMENTO = '[title^="Baixar"], [title^="Download"], [aria-label^="Baixar"], [aria-label^="Download"], [data-icon^="document-"], [data-icon="document"]'
SELETOR_SETA_DE_BAIXAR = '[data-icon="audio-download"], [data-icon="download"], [data-icon="media-download"]'
TEMPO_DO_PRIMEIRO_CLIQUE = 6_000  # ms que cada tentativa espera o download começar
TEMPO_DO_DOWNLOAD = 60_000  # ms da última tentativa (o arquivo pode ser grande)
# Botões da mensagem que nunca são o de baixar.
RE_BOTAO_QUE_NAO_BAIXA = re.compile(r"encaminhar|forward|responder|reply|reagir|react|fixar|pin|estrela|star|menu|mais op|more|emoji", re.I)
SELETOR_MENU_DA_MENSAGEM = ('[data-icon="down-context"], [data-icon="ic-chevron-down-menu"], [aria-label="Menu de contexto"], '
                            '[aria-label="Context menu"], [aria-label="Mais opções"], [aria-label="More options"]')
SELETOR_BAIXAR_NA_PAGINA = ('[aria-label="Baixar"], [aria-label="Download"], [title="Baixar"], [title="Download"], '
                            '[data-icon="download"], [data-icon="ic-download"]')
RE_ITEM_BAIXAR = re.compile(r"^\s*(Baixar|Download)\s*$", re.I)
EXTENSAO_DA_IMAGEM = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


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

    def ignorar(self, identificador: str):
        """Marca uma mensagem como já vista (os arquivos que a própria Beatriz mandou: não são seus)."""
        chave = chave_da_mensagem(identificador)
        if chave:
            self.vistos.add(chave)
            self.historico.add(chave)

    def novas_entradas(self, mensagens: list, agora: float = None) -> list:
        """mensagens: [{"id", "texto", "quando", "anexo"}] na ordem da tela. Devolve o que é novo, na ordem:
        [{"id", "texto", "anexo"}], com `anexo` None nas mensagens de texto (comandos) e {"tipo", "nome"} nos documentos e fotos
        (o `texto` delas é a legenda, ou None)."""
        agora = time.time() if agora is None else agora
        chaves = [chave_da_mensagem(m.get("id")) for m in mensagens]
        if not self.pronto:
            self._aquecer([c for c in chaves if c], agora)
            return []
        # Posição da última mensagem do histórico na tela: o que aparece acima dela é mais antigo.
        limite = max((posicao for posicao, chave in enumerate(chaves) if chave in self.historico), default=-1)
        entradas, antigas = [], 0
        for posicao, (mensagem, chave) in enumerate(zip(mensagens, chaves)):
            if not chave or chave in self.vistos:
                continue
            self.vistos.add(chave)
            if posicao < limite or self._antes_de_pronto(mensagem.get("quando")):
                self.historico.add(chave)
                antigas += 1
                continue
            texto = self._normalizar(mensagem.get("texto"))
            anexo = mensagem.get("anexo") or None
            if anexo:
                if texto and texto == self._normalizar(anexo.get("nome")):
                    texto = ""  # o nome do arquivo não é uma legenda
                if texto.startswith(PREFIXO_RESPOSTA) or texto in self.enviados:
                    continue
                entradas.append({"id": mensagem.get("id"), "texto": texto or None, "anexo": anexo})
                continue
            if not texto or texto.startswith(PREFIXO_RESPOSTA) or texto in self.enviados:
                continue
            entradas.append({"id": mensagem.get("id"), "texto": texto, "anexo": None})
        if antigas:
            logger.info("WhatsApp: %d mensagem(ns) antiga(s) ignorada(s) (carregadas depois da abertura).", antigas)
        return entradas

    def novas(self, mensagens: list, agora: float = None) -> list:
        """Só os textos dos comandos novos (sem os anexos): `novas_entradas` sem o que vem com arquivo."""
        return [e["texto"] for e in self.novas_entradas(mensagens, agora) if e["anexo"] is None]


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


def _enviar_arquivo(pagina, caminho: str) -> str:
    """Anexa um arquivo como documento e confere que ele apareceu na conversa. Devolve o id da mensagem ("" se falhou).

    Menu Anexar > "Documento" abre o seletor de arquivos do sistema, que o Playwright responde.
    (A única entrada de arquivo que existe antes disso é a de figurinhas, que só aceita imagens.)
    """
    anexar = next((pagina.locator(s).first for s in SELETORES_ANEXAR if pagina.locator(s).count()), None)
    if anexar is None:
        return ""
    antes = _ultima_mensagem(pagina)
    anexar.click()
    documento = pagina.get_by_role("button", name="Documento").or_(pagina.get_by_text("Documento", exact=True)).first
    with pagina.expect_file_chooser(timeout=10_000) as escolha:
        documento.click()
    escolha.value.set_files(caminho)
    pagina.locator(SELETOR_ENVIAR_ARQUIVO).first.click(timeout=20_000)
    for _ in range(30):  # até 15 s para o documento aparecer como mensagem nova
        pagina.wait_for_timeout(500)
        novo = _ultima_mensagem(pagina)
        if novo != antes:
            return novo
    return ""


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
            identificador = _enviar_arquivo(pagina, caminho)
            if identificador:
                leitor.ignorar(identificador)  # o arquivo que ela mesma mandou não volta como se fosse seu
                continue
        except Exception as exc:
            logger.warning("Falha ao anexar %s no WhatsApp: %s", caminho, exc)
            pagina.keyboard.press("Escape")
        aviso = f"{PREFIXO_RESPOSTA}: não consegui anexar {os.path.basename(caminho)}; ele está no PC em {caminho}"
        leitor.registrar_envio(aviso)
        _enviar_texto(pagina, aviso)


class AnexoRecusado(Exception):
    """O arquivo chegou mas não é guardado (programa, grande demais...); a mensagem vai para a pessoa."""


def _tentar_baixar(pagina, acao, tempo: int):
    """Faz `acao` (um clique) e espera o navegador começar um download; None se nada baixou."""
    try:
        with pagina.expect_download(timeout=tempo) as espera:
            acao()
        return espera.value
    except Exception as exc:
        logger.info("WhatsApp: tentativa de baixar sem download (%s).", str(exc).splitlines()[0][:80] if str(exc) else "")
        return None


def _fechar_visualizador(pagina):
    try:
        pagina.keyboard.press("Escape")
    except Exception:
        pass


def _baixar_documento(pagina, identificador: str):
    """O WhatsApp Web muda de cara: tenta, em ordem, o clique no documento, a seta de baixar, os outros botões da mensagem, o botão
    "Baixar" do visualizador que um clique pode abrir e o menu da mensagem ("Baixar"). Devolve o download ou None."""
    mensagem = pagina.locator(f'[data-id="{identificador}"]')
    if not mensagem.count():
        return None
    mensagem.first.scroll_into_view_if_needed()

    def clicar(alvo):
        # clicar no ícone dentro de um botão tem de clicar no botão
        botao = alvo.locator("xpath=ancestor-or-self::*[@role='button'][1]")
        (botao.first if botao.count() else alvo).click()

    candidatos = []
    for seletor in (SELETOR_DOCUMENTO, SELETOR_SETA_DE_BAIXAR):
        candidatos.extend(mensagem.locator(seletor).all()[:3])
    for botao in mensagem.locator('[role="button"]').all()[:8]:
        rotulo = (botao.get_attribute("aria-label") or "") + " " + (botao.get_attribute("title") or "")
        if not RE_BOTAO_QUE_NAO_BAIXA.search(rotulo):
            candidatos.append(botao)
    for indice, alvo in enumerate(candidatos):
        ultimo = indice == len(candidatos) - 1
        baixou = _tentar_baixar(pagina, lambda alvo=alvo: clicar(alvo), TEMPO_DO_DOWNLOAD if ultimo else TEMPO_DO_PRIMEIRO_CLIQUE)
        if baixou is not None:
            return baixou
        # o clique pode ter aberto o visualizador, que tem o botão de baixar
        visualizador = pagina.locator(SELETOR_BAIXAR_NA_PAGINA).last
        if visualizador.count():
            baixou = _tentar_baixar(pagina, lambda: visualizador.click(), TEMPO_DO_DOWNLOAD)
            _fechar_visualizador(pagina)
            if baixou is not None:
                return baixou
    # menu da mensagem: passa o mouse, abre o menu e escolhe "Baixar" (ou clique direito)
    for abrir_menu in (lambda: (mensagem.first.hover(), mensagem.locator(SELETOR_MENU_DA_MENSAGEM).first.click()),
                       lambda: mensagem.first.click(button="right")):
        try:
            abrir_menu()
            item = pagina.get_by_text(RE_ITEM_BAIXAR).first
            item.wait_for(state="visible", timeout=2000)
        except Exception:
            _fechar_visualizador(pagina)
            continue
        baixou = _tentar_baixar(pagina, lambda: item.click(), TEMPO_DO_DOWNLOAD)
        if baixou is not None:
            return baixou
        _fechar_visualizador(pagina)
    return None


def _baixar_anexo(pagina, identificador: str, anexo: dict, pasta_recebidos: str, limite_mb: float) -> str:
    """Baixa o documento ou a foto da mensagem para `pasta_recebidos/AAAA-MM-DD/` e devolve o caminho."""
    from fontes import caminho_livre, nome_seguro, validar_arquivo

    pasta = os.path.join(pasta_recebidos, datetime.now().strftime("%Y-%m-%d"))
    if anexo.get("tipo") == "imagem":
        dados = pagina.evaluate(JS_BAIXAR_IMAGEM, identificador)
        if not dados:
            raise RuntimeError("não achei a foto na conversa")
        import base64

        destino = caminho_livre(pasta, f"foto_{datetime.now():%H%M%S}{EXTENSAO_DA_IMAGEM.get(dados.get('tipo'), '.jpg')}")
        with open(destino, "wb") as arquivo:
            arquivo.write(base64.b64decode(dados["base64"]))
    else:
        baixou = _baixar_documento(pagina, identificador)
        if baixou is None:
            raise RuntimeError("o WhatsApp Web não começou o download")
        destino = caminho_livre(pasta, nome_seguro(baixou.suggested_filename or anexo.get("nome") or "documento"))
        baixou.save_as(destino)
    problema = validar_arquivo(destino, limite_mb)
    if problema:
        try:
            os.remove(destino)
        except OSError:
            pass
        raise AnexoRecusado(f"“{os.path.basename(destino)}”: {problema}")
    return destino


def _avisar(pagina, leitor: LeitorDeMensagens, texto: str):
    for parte in mensagens_de_resposta(texto):
        leitor.registrar_envio(parte)
        _enviar_texto(pagina, parte)


def _processar_anexo(pagina, leitor: LeitorDeMensagens, entrada: dict, fila_comandos, pasta_recebidos: str, limite_mb: float):
    """Um documento ou foto chegou: baixa e entrega ao agente de fontes (a legenda, se houver, é o pedido)."""
    anexo = entrada["anexo"]
    if anexo.get("tipo") not in ("documento", "imagem"):
        _avisar(pagina, leitor, "Só recebo documentos e fotos por aqui (áudio e vídeo, ainda não).")
        return
    try:
        caminho = _baixar_anexo(pagina, entrada["id"], anexo, pasta_recebidos, limite_mb)
    except AnexoRecusado as exc:
        _avisar(pagina, leitor, f"Não guardei {exc}")
        return
    except Exception as exc:
        logger.warning("WhatsApp: não consegui baixar o anexo: %s", exc)
        try:
            logger.info("WhatsApp: forma da mensagem que não baixou:\n%s", pagina.evaluate(JS_ESQUELETO, entrada["id"]))
        except Exception:
            pass
        _avisar(pagina, leitor, "Recebi um arquivo, mas não consegui baixar. Tente mandar de novo como documento "
                                "(no clipe, escolha Documento).")
        return
    fila_comandos.put({"tipo": "arquivo", "caminho": caminho, "nome": os.path.basename(caminho),
                       "legenda": entrada.get("texto") or "", "origem": "whatsapp"})


def executar_navegador(fila_comandos, fila_envios, estado, pasta_perfil: str, numero_configurado: str = "",
                       pasta_recebidos: str = "", limite_mb: float = 50.0):
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
                    for entrada in leitor.novas_entradas(pagina.evaluate(JS_LER_MENSAGENS)):
                        if entrada["anexo"] is None:
                            fila_comandos.put(entrada["texto"])
                        elif pasta_recebidos:
                            _processar_anexo(pagina, leitor, entrada, fila_comandos, pasta_recebidos, limite_mb)
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

    def __init__(self, estado=None, pasta_perfil: str = None, numero: str = "", pasta_recebidos: str = None, limite_mb: float = None):
        from config import LIMITE_DO_ARQUIVO_MB, NOTELOOKLM_PATH, PASTA_RECEBIDOS

        self.pasta_recebidos = PASTA_RECEBIDOS if pasta_recebidos is None else pasta_recebidos
        self.limite_mb = LIMITE_DO_ARQUIVO_MB if limite_mb is None else limite_mb
        self.estado = estado
        self.pasta_perfil = pasta_perfil or os.path.join(NOTELOOKLM_PATH, "banco_memoria", "whatsapp_web")
        self.numero = numero
        self.fila_comandos = multiprocessing.Queue()
        self.fila_envios = multiprocessing.Queue()
        self.processo = None

    def iniciar(self):
        self.processo = multiprocessing.Process(
            target=executar_navegador,
            args=(self.fila_comandos, self.fila_envios, self.estado, self.pasta_perfil, self.numero,
                  self.pasta_recebidos, self.limite_mb),
            daemon=True,
        )
        self.processo.start()
        logger.info("Canal do WhatsApp iniciado (processo %s).", self.processo.pid)

    def receber(self):
        """Próxima coisa que chegou pelo WhatsApp: o texto de um comando, ou um dict {"tipo": "arquivo", "caminho", "nome",
        "legenda", "origem"} quando você mandou um documento ou foto. "" se não houver nada."""
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
