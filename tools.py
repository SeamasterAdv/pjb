# tools.py
import base64
import os
import webbrowser
import urllib.parse
import logging
import re
import tempfile
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import pywhatkit

logger = logging.getLogger(__name__)
# Os feeds RSS são lidos com o parser HTML (não há lxml); funciona, só gera um aviso.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

_PLAYWRIGHT = None
_BROWSER = None
_BROWSER_PAGE = None


# Falso enquanto a Beatriz atende um comando do WhatsApp: ninguém está no teclado para confirmar,
# então a ação externa é cancelada em vez de travar esperando o input().
CONFIRMAR_NO_TECLADO = True


def _confirmar_acao(descricao: str) -> bool:
    if not CONFIRMAR_NO_TECLADO:
        logger.info("Ação cancelada (pedido sem ninguém no teclado para confirmar): %s", descricao)
        return False
    try:
        resposta = input(f"Confirmar {descricao}? [s/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return resposta in {"s", "sim", "y", "yes"}

def tentativa_segura(funcao, *args, **kwargs):
    try:
        return funcao(*args, **kwargs)
    except Exception as e:
        logger.error(f"Erro na ferramenta {funcao.__name__}: {e}")
        return f"Erro: {e}"

def abrir_app(nome_app: str) -> str:
    # Implementação real deve usar gerenciador_apps
    # Aqui é um placeholder
    return f"Aplicativo '{nome_app}' aberto."

def criar_ou_salvar_anotacao(conteudo: str, nome_arquivo: str = "notas.txt") -> str:
    try:
        if not nome_arquivo.endswith(".txt"):
            nome_arquivo += ".txt"
        modo = "a" if os.path.exists(nome_arquivo) else "w"
        with open(nome_arquivo, modo, encoding="utf-8") as f:
            f.write(f"\n--- Anotação ---\n{conteudo}\n")
        return f"Anotação salva em '{nome_arquivo}'."
    except Exception as e:
        return str(e)

def listar_arquivos_da_pasta(caminho: str) -> str:
    try:
        if not os.path.exists(caminho):
            return f"Pasta '{caminho}' não existe."
        return f"Conteúdo de {caminho}: " + ", ".join(os.listdir(caminho))
    except Exception as e:
        return str(e)

def criar_pasta(caminho: str) -> str:
    """Cria uma pasta, incluindo os diretórios-pai que ainda não existirem."""
    try:
        caminho = caminho.strip().strip('"')
        if not caminho:
            return "Preciso do nome ou caminho da pasta para criá-la."
        caminho_absoluto = os.path.abspath(os.path.expanduser(caminho))
        ja_existia = os.path.isdir(caminho_absoluto)
        os.makedirs(caminho_absoluto, exist_ok=True)
        if ja_existia:
            return f"A pasta '{caminho_absoluto}' já existe."
        return f"Pasta criada em '{caminho_absoluto}'."
    except Exception as e:
        return f"Não consegui criar a pasta: {e}"

def encontrar_e_abrir(candidato: str, limite: int = 10) -> str:
    """Localiza um arquivo ou pasta pelo nome e abre o primeiro resultado."""
    try:
        termo = candidato.strip().strip('"')
        if not termo:
            return "Diga o nome ou caminho do arquivo ou da pasta que devo encontrar."

        caminho_informado = os.path.abspath(os.path.expanduser(termo))
        if os.path.exists(caminho_informado):
            caminho_encontrado = caminho_informado
        else:
            nome_procurado = os.path.basename(termo).casefold()
            nome_sem_extensao = os.path.splitext(nome_procurado)[0]
            diretorios_ignorados = {
                "$recycle.bin",
                "appdata",
                "node_modules",
                ".git",
                "program files",
                "program files (x86)",
                "windows",
            }
            raizes = [os.path.expanduser("~"), os.path.dirname(__file__)]
            if os.name == "nt":
                raizes.extend(
                    f"{letra}:\\"
                    for letra in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    if os.path.exists(f"{letra}:\\")
                )

            resultados = []
            visitados = set()
            for raiz in raizes:
                raiz = os.path.abspath(raiz)
                if raiz in visitados or not os.path.isdir(raiz):
                    continue
                visitados.add(raiz)
                for diretorio, pastas, arquivos in os.walk(raiz, topdown=True, onerror=lambda _: None):
                    pastas[:] = [
                        pasta for pasta in pastas
                        if pasta.casefold() not in diretorios_ignorados
                    ]
                    nomes = pastas + arquivos
                    for nome in nomes:
                        nome_normalizado = nome.casefold()
                        corresponde = nome_normalizado == nome_procurado
                        if not os.path.splitext(nome_procurado)[1]:
                            corresponde = (
                                corresponde
                                or os.path.splitext(nome_normalizado)[0] == nome_sem_extensao
                            )
                        if corresponde:
                            resultados.append(os.path.join(diretorio, nome))
                            if len(resultados) >= limite:
                                break
                    if len(resultados) >= limite:
                        break
                if len(resultados) >= limite:
                    break

            if not resultados:
                return f"Não encontrei arquivo ou pasta com o nome '{termo}'."
            caminho_encontrado = resultados[0]

        if hasattr(os, "startfile"):
            os.startfile(caminho_encontrado)
        else:
            webbrowser.open(caminho_encontrado)
        return f"Abri '{caminho_encontrado}'."
    except Exception as e:
        logger.warning("Falha ao encontrar ou abrir '%s': %s", candidato, e)
        return f"Não consegui encontrar ou abrir '{candidato}': {e}"


def abrir_arquivo(nome_ou_caminho: str) -> str:
    """Localiza e abre arquivos pelo aplicativo padrão do sistema operacional."""
    return encontrar_e_abrir(nome_ou_caminho)

def abrir_explorador() -> str:
    """Abre o Explorador de Arquivos na visão de Este PC."""
    try:
        if os.name == "nt":
            os.startfile("shell:MyComputerFolder")
        else:
            webbrowser.open("file:///")
        return "Abri o Explorador de Arquivos em Este PC."
    except Exception as e:
        return f"Não consegui abrir o Explorador de Arquivos: {e}"

def abrir_navegador(termo_ou_url: str) -> str:
    try:
        if not termo_ou_url or not termo_ou_url.strip():
            webbrowser.open("https://www.google.com")
            return "Navegador aberto em branco com a página inicial do Google."
        if termo_ou_url.startswith(("http://", "https://")):
            url = termo_ou_url
        else:
            url = f"https://www.google.com/search?q={urllib.parse.quote(termo_ou_url)}"
        webbrowser.open(url)
        return f"Navegador aberto com '{termo_ou_url}'."
    except Exception as e:
        return f"Erro ao abrir navegador: {e}"

def ler_conteudo_de_site(url: str) -> str:
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form', 'noscript', 'svg', 'iframe']):
            tag.decompose()
        # Prioriza o conteúdo principal da página em vez de menus e barras laterais.
        principal = soup.find('article') or soup.find('main') or soup.body or soup
        texto = principal.get_text(separator=' ', strip=True)
        return texto[:4000] if len(texto) > 4000 else texto
    except Exception as e:
        return f"Erro ao ler site: {e}"


def navegar_e_consultar_site(url_ou_termo: str, pergunta: str = "") -> str:
    """Abre uma página dinâmica e devolve o texto visível para responder perguntas."""
    global _PLAYWRIGHT, _BROWSER, _BROWSER_PAGE
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return (
            "Para ler sites interativos preciso do Playwright. "
            "Instale com 'pip install playwright' e depois 'playwright install chromium'."
        )

    try:
        if _BROWSER_PAGE is None or _BROWSER_PAGE.is_closed():
            if _BROWSER is None:
                _PLAYWRIGHT = sync_playwright().start()
                _BROWSER = _PLAYWRIGHT.chromium.launch(headless=False)
            _BROWSER_PAGE = _BROWSER.new_page(viewport={"width": 1440, "height": 900})

        alvo = url_ou_termo.strip()
        if not alvo.startswith(("http://", "https://")):
            if "ingresso.com" in alvo.lower():
                alvo = "https://www.ingresso.com/"
            else:
                alvo = "https://www.google.com/search?q=" + urllib.parse.quote(alvo)
        _BROWSER_PAGE.goto(alvo, wait_until="domcontentloaded", timeout=30000)
        _BROWSER_PAGE.wait_for_timeout(1500)
        texto = _BROWSER_PAGE.locator("body").inner_text(timeout=10000)
        titulo = _BROWSER_PAGE.title()
        trecho = texto[:12000]
        if pergunta:
            from llm_clients import llm_clients

            prompt = (
                "Responda à pergunta usando somente o conteúdo visível da página. "
                "Se um filme, horário ou informação não estiver visível, diga isso. "
                "Organize filmes e horários de forma legível e não invente dados. "
                "Responda em frases curtas, sem markdown, emojis ou listas com marcadores.\n\n"
                f"Página: {titulo}\nURL: {_BROWSER_PAGE.url}\n"
                f"Pergunta: {pergunta}\nConteúdo visível:\n{trecho}"
            )
            try:
                resposta = llm_clients.chat_beatriz(
                    prompt,
                    contexto="Você está lendo uma página aberta no navegador em tempo real.",
                )
                return resposta or trecho
            except Exception as exc:
                logger.warning("Não foi possível resumir a página com Ollama: %s", exc)
                return f"Página: {titulo}\nConteúdo visível:\n{trecho}"
        return f"Página aberta: {titulo}\nURL: {_BROWSER_PAGE.url}\nConteúdo visível:\n{trecho}"
    except Exception as exc:
        logger.warning("Falha ao navegar e consultar site: %s", exc)
        return f"Não consegui abrir ou ler o site: {exc}"


def analisar_tela(pergunta: str = "Descreva o que está na tela e identifique textos, imagens e botões relevantes.") -> str:
    """Captura o monitor da janela ativa, lê o texto (OCR do Windows) e responde com o modelo local (leitura_tela.py)."""
    try:
        from llm_clients import llm_clients
        from leitura_tela import AgenteTela, Pedido

        resposta = AgenteTela(llm=llm_clients)._olhar(Pedido(pergunta, escopo="tela"))
        return f"{resposta.fala}\n\n{resposta.painel}" if resposta.painel else resposta.fala
    except Exception as exc:
        logger.warning("Falha ao analisar a tela: %s", exc)
        return "Não consegui analisar a tela. Verifique se o Ollama está ativo e se a captura de tela está disponível: " + str(exc)


def _url_real_duckduckgo(href: str) -> str:
    """Os links do DuckDuckGo HTML passam por um redirecionamento (/l/?uddg=...)."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        destino = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
        return destino or href
    return href


def _url_real_bing(href: str) -> str:
    """Os links do Bing podem vir como bing.com/ck/a?...&u=a1<base64 da URL>."""
    parsed = urllib.parse.urlparse(href)
    if "bing.com" in parsed.netloc and parsed.path.startswith("/ck/"):
        codificado = urllib.parse.parse_qs(parsed.query).get("u", [""])[0]
        if codificado.startswith("a1"):
            try:
                codificado = codificado[2:]
                return base64.urlsafe_b64decode(codificado + "=" * (-len(codificado) % 4)).decode("utf-8")
            except Exception:
                return href
    return href


_HEADERS_BUSCA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}


def _resultados_bing(consulta: str) -> list[tuple[str, str, str]]:
    url = "https://www.bing.com/search?setlang=pt-BR&cc=BR&q=" + urllib.parse.quote(consulta)
    resposta = requests.get(url, headers=_HEADERS_BUSCA, timeout=10)
    resposta.raise_for_status()
    soup = BeautifulSoup(resposta.text, "html.parser")
    resultados = []
    for item in soup.select("li.b_algo"):
        link = item.select_one("h2 a")
        if not link or not link.get("href"):
            continue
        trecho = item.select_one(".b_caption p, .b_lineclamp2, .b_lineclamp3, .b_lineclamp4")
        resultados.append((
            link.get_text(" ", strip=True),
            _url_real_bing(link["href"]),
            trecho.get_text(" ", strip=True) if trecho else "",
        ))
    return resultados


def _resultados_duckduckgo(consulta: str) -> list[tuple[str, str, str]]:
    url = "https://html.duckduckgo.com/html/?kl=br-pt&q=" + urllib.parse.quote(consulta)
    resposta = requests.get(url, headers=_HEADERS_BUSCA, timeout=10)
    resposta.raise_for_status()
    soup = BeautifulSoup(resposta.text, "html.parser")
    resultados = []
    for item in soup.select(".result"):
        if "result--ad" in (item.get("class") or []):
            continue
        link = item.select_one(".result__a")
        if not link or not link.get("href"):
            continue
        trecho = item.select_one(".result__snippet")
        resultados.append((
            link.get_text(" ", strip=True),
            _url_real_duckduckgo(link["href"]),
            trecho.get_text(" ", strip=True) if trecho else "",
        ))
    return resultados


def buscar_manchetes(consulta: str) -> list[tuple[str, str, str]]:
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(consulta) + "&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    resposta = requests.get(url, headers=_HEADERS_BUSCA, timeout=10)
    resposta.raise_for_status()
    soup = BeautifulSoup(resposta.content, "html.parser")
    resultados = []
    for item in soup.find_all("item")[:8]:
        titulo = item.title.get_text(" ", strip=True) if item.title else ""
        data = item.find("pubdate").get_text(" ", strip=True) if item.find("pubdate") else ""  # o parser HTML deixa as tags em minúsculas
        if titulo:
            resultados.append((titulo, "", f"Notícia publicada em {data}"))
    return resultados


_PALAVRAS_VAZIAS = {
    "qual", "quais", "quem", "quando", "onde", "como", "quanto", "quanta", "quantos", "quantas",
    "para", "pelo", "pela", "com", "sem", "sobre", "entre", "uma", "umas", "uns", "dos", "das",
    "que", "hoje", "agora", "atual", "último", "última", "ultimo", "ultima", "ganhou", "está", "esta",
    "foi", "são", "ser", "tem", "isso", "esse", "essa", "mais", "melhor", "pesquise", "pesquisa",
    # Palavras de atualidade casam com a página inicial de qualquer portal; não servem de filtro.
    "notícias", "noticias", "notícia", "noticia", "últimas", "ultimas", "recentes", "principais",
}


def _palavras_chave(texto: str) -> set[str]:
    return {
        palavra for palavra in re.findall(r"[\wÀ-ÿ]+", (texto or "").lower())
        if len(palavra) > 2 and palavra not in _PALAVRAS_VAZIAS
    }


def _filtrar_relevantes(consulta: str, resultados: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Descarta resultados que não têm nenhuma palavra-chave da pergunta.

    O Bing às vezes devolve páginas sem relação nenhuma para buscas automáticas.
    """
    chaves = _palavras_chave(consulta)
    if not chaves:
        return resultados
    return [r for r in resultados if chaves & _palavras_chave(f"{r[0]} {r[2]}")]


def pesquisar_web_autonoma(consulta: str, limite: int = 4) -> str:
    """Pesquisa e lê fontes da web sem abrir o navegador, baixando as páginas em paralelo.

    Tenta o Bing, depois o DuckDuckGo (que costuma bloquear buscas automáticas)
    e por fim as manchetes do Google Notícias, ficando com o primeiro que trouxer
    resultados relacionados à pergunta.
    """
    try:
        brutos = []
        for buscador in (_resultados_bing, _resultados_duckduckgo, buscar_manchetes):
            try:
                brutos = _filtrar_relevantes(consulta, buscador(consulta))
            except Exception as exc:
                logger.warning("Busca em %s falhou: %s", buscador.__name__, exc)
            if brutos:
                break

        candidatos = []
        dominios_vistos = set()
        for titulo, url, trecho in brutos:
            dominio = urllib.parse.urlparse(url).netloc
            if not dominio or dominio in dominios_vistos:
                continue
            dominios_vistos.add(dominio)
            candidatos.append((titulo, url, trecho))
            if len(candidatos) >= limite:
                break

        with ThreadPoolExecutor(max_workers=limite) as executor:
            paginas = list(executor.map(lambda candidato: ler_conteudo_de_site(candidato[1]), candidatos))

        # Os resumos da busca vêm primeiro: costumam trazer o dado direto (valores, datas, nomes).
        resumos = [f"{titulo}: {trecho}" for titulo, _, trecho in brutos[:8] if trecho]
        resultados = []
        if resumos:
            resultados.append("[Fonte: resumos do buscador]\n" + "\n".join(resumos))
        for (titulo, url, trecho), pagina in zip(candidatos, paginas):
            if pagina.startswith("Erro ao ler site") or len(pagina) < 200:
                continue
            resultados.append(f"[Fonte: {titulo}]\nURL: {url}\n{pagina[:3000]}")
        if not resultados:
            return "Nenhuma fonte relevante encontrada na internet."
        return "\n\n".join(resultados)
    except Exception as exc:
        logger.warning("Falha na pesquisa web autônoma: %s", exc)
        return f"Não foi possível pesquisar na internet agora: {exc}"


def salvar_pesquisa_web(consulta: str, conteudo: str) -> str:
    """Salva uma pesquisa web em formato Markdown para indexação futura."""
    try:
        pasta = os.path.join(os.path.dirname(__file__), "documentos_assistente", "pesquisas")
        os.makedirs(pasta, exist_ok=True)
        nome = re.sub(r"[^a-zA-Z0-9_-]+", "_", consulta.strip()).strip("_")[:80] or "pesquisa"
        caminho = os.path.join(pasta, f"{datetime.now():%Y%m%d_%H%M%S}_{nome}.md")
        with open(caminho, "w", encoding="utf-8") as arquivo:
            arquivo.write(f"# Pesquisa: {consulta}\n\n{conteudo}\n")
        return f"Conteúdo salvo em '{caminho}'."
    except Exception as exc:
        return f"Não consegui salvar a pesquisa: {exc}"


def obter_principais_noticias(temas=None, limite_por_tema=3) -> str:
    """Lê manchetes recentes via RSS, priorizando os temas do usuário."""
    temas = temas or ["direito", "jogos", "entretenimento", "filmes"]
    noticias = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for tema in temas:
        try:
            rss_url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(tema) + "&hl=pt-BR&gl=BR&ceid=BR:pt-419"
            resposta = requests.get(rss_url, headers=headers, timeout=10)
            resposta.raise_for_status()
            soup = BeautifulSoup(resposta.content, "html.parser")
            for item in soup.find_all("item")[:limite_por_tema]:
                titulo = item.title.get_text(" ", strip=True) if item.title else ""
                link = item.link.get_text(" ", strip=True) if item.link else ""
                data = item.find("pubdate").get_text(" ", strip=True) if item.find("pubdate") else ""  # o parser HTML deixa as tags em minúsculas
                if titulo:
                    noticias.append(f"[{tema}] {titulo} ({data})\n{link}")
        except Exception as exc:
            logger.warning("Falha ao carregar notícias de %s: %s", tema, exc)
    return "\n\n".join(noticias) if noticias else "Não consegui obter notícias agora."


def verificar_novos_emails(limite=5) -> str:
    """Consulta e-mails não lidos somente quando Gmail OAuth já está configurado."""
    try:
        from config import GMAIL_CREDENTIALS_FILE, GMAIL_TOKEN_FILE
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        if not os.path.exists(GMAIL_TOKEN_FILE) or not os.path.exists(GMAIL_CREDENTIALS_FILE):
            return ""
        escopos = ["https://www.googleapis.com/auth/gmail.readonly"]
        credenciais = Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE, escopos)
        if not credenciais or not credenciais.valid:
            if not credenciais or not credenciais.expired or not credenciais.refresh_token:
                return ""
            credenciais.refresh(Request())
            with open(GMAIL_TOKEN_FILE, "w", encoding="utf-8") as arquivo:
                arquivo.write(credenciais.to_json())
        servico = build("gmail", "v1", credentials=credenciais, cache_discovery=False)
        mensagens = servico.users().messages().list(userId="me", labelIds=["UNREAD"], maxResults=limite).execute().get("messages", [])
        resultados = []
        for item in mensagens:
            dados = servico.users().messages().get(userId="me", id=item["id"], format="metadata", metadataHeaders=["From", "Subject"]).execute()
            cabecalhos = {h["name"]: h["value"] for h in dados.get("payload", {}).get("headers", [])}
            resultados.append(f"De: {cabecalhos.get('From', 'desconhecido')} | Assunto: {cabecalhos.get('Subject', '(sem assunto)')}")
        return "\n".join(resultados)
    except Exception as exc:
        logger.warning("Não foi possível verificar e-mails: %s", exc)
        return ""

def pesquisar_youtube(termo: str) -> str:
    try:
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(termo)}"
        webbrowser.open(url)
        return f"Pesquisa '{termo}' aberta no YouTube."
    except Exception as e:
        return f"Erro: {e}"

def pesquisar_spotify(termo: str, tipo: str = "musica") -> str:
    try:
        tipo = tipo.lower()
        if tipo in ("playlist", "playlists"):
            filtro = "playlists"
        else:
            filtro = "tracks"
        url = f"https://open.spotify.com/search/{urllib.parse.quote(termo)}/{filtro}"
        webbrowser.open(url)
        return f"Busca '{termo}' aberta no Spotify."
    except Exception as e:
        return f"Erro: {e}"

def pesquisar_planalto_leis(termo_ou_lei: str) -> str:
    try:
        url = f"https://www.google.com/search?q=site:planalto.gov.br {urllib.parse.quote(termo_ou_lei)}"
        webbrowser.open(url)
        return f"Legislação '{termo_ou_lei}' aberta."
    except Exception as e:
        return f"Erro: {e}"

def obter_data_hora_atual() -> str:
    agora = datetime.now()
    return agora.strftime("Agora são %H:%M, de %d/%m/%Y.")

def abrir_calendario() -> str:
    try:
        if hasattr(os, "startfile"):
            os.startfile("ms-calendar:")
            return "Agenda nativa do computador aberta."
        webbrowser.open("calendar:")
        return "Agenda do computador aberta."
    except Exception as e:
        return f"Erro: {e}"

def enviar_email(destinatario: str, assunto: str, mensagem: str, confirmado: bool = False) -> str:
    try:
        url = f"mailto:{destinatario}?subject={urllib.parse.quote(assunto)}&body={urllib.parse.quote(mensagem)}"
        webbrowser.open(url)
        return "Cliente de e-mail aberto."
    except Exception as e:
        return f"Erro: {e}"

_AGENTE_AGENDA = None


def _agente_agenda():
    """Agente da agenda local (o mesmo banco do painel e da voz), criado na primeira vez que for usado."""
    global _AGENTE_AGENDA
    if _AGENTE_AGENDA is None:
        from agente_agenda import criar_agente_agenda
        _AGENTE_AGENDA = criar_agente_agenda()
    return _AGENTE_AGENDA


def criar_evento_agenda(titulo: str, data_hora: str, descricao: str = "", quando_lembrar: str = "", confirmado: bool = False) -> str:
    """Marca o compromisso na agenda local do PC (banco_memoria/agenda.sqlite3), sem Google.

    `data_hora` aceita o que se fala: "amanhã às 14h", "20/10 às 9", "toda segunda às 7h".
    A agenda é local (sem efeito fora do computador), então não há o que confirmar; `confirmado`
    fica pela compatibilidade com a API e o mapa de ferramentas.
    """
    try:
        lembretes = None
        if quando_lembrar:
            minutos = _interpretar_lembrete(quando_lembrar)
            if minutos is not None:
                lembretes = [minutos]
            elif re.search(r"sem lembrete|n[ãa]o lembrar", quando_lembrar.lower()):
                lembretes = []
        return _agente_agenda().marcar(titulo, data_hora, descricao.strip(), lembretes)
    except Exception as exc:
        logger.exception("Falha ao criar evento na agenda local")
        return f"Não consegui marcar na agenda: {exc}"


def _interpretar_lembrete(valor: str):
    texto = (valor or "").lower()
    if not texto:
        return None
    if "sem lembrete" in texto or "não lembrar" in texto or "nao lembrar" in texto:
        return None
    match = re.search(r"(\d+)\s*(min|mins|minuto|minutos)", texto)
    if match:
        return int(match.group(1))
    if "hora" in texto:
        match = re.search(r"(\d+)\s*hora", texto)
        return int(match.group(1)) * 60 if match else 60
    if "dia anterior" in texto or "um dia antes" in texto:
        return 1440
    if "dia" in texto:
        return 1440
    return None

def listar_compromissos() -> str:
    """Os compromissos dos próximos 7 dias na agenda local."""
    hoje = datetime.now().date()
    return _agente_agenda().descrever_periodo(hoje, hoje + timedelta(days=6))


def abrir_whatsapp() -> str:
    try:
        if not _confirmar_acao("abrir o WhatsApp"):
            return "Ação cancelada por você."
        webbrowser.open("https://web.whatsapp.com")
        return "WhatsApp aberto no navegador."
    except Exception as e:
        return f"Erro ao abrir WhatsApp: {e}"


CODIGOS_DO_TEMPO = {
    0: "céu limpo", 1: "predominantemente limpo", 2: "parcialmente nublado", 3: "nublado",
    45: "neblina", 48: "neblina", 51: "garoa fraca", 53: "garoa", 55: "garoa forte",
    56: "garoa congelante", 57: "garoa congelante", 61: "chuva fraca", 63: "chuva", 65: "chuva forte",
    66: "chuva congelante", 67: "chuva congelante", 71: "neve fraca", 73: "neve", 75: "neve forte",
    77: "grãos de neve", 80: "pancadas de chuva fracas", 81: "pancadas de chuva", 82: "pancadas de chuva fortes",
    85: "pancadas de neve", 86: "pancadas de neve fortes", 95: "tempestade", 96: "tempestade com granizo",
    99: "tempestade com granizo forte",
}
_CACHE_CLIMA: dict = {}


def _achar_cidade(nome: str) -> dict:
    """Coordenadas da cidade pelo geocodificador do Open-Meteo (gratuito, sem chave); prefere o Brasil."""
    resposta = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": nome, "count": 5, "language": "pt", "format": "json"}, timeout=10,
    )
    resposta.raise_for_status()
    achados = resposta.json().get("results") or []
    if not achados:
        raise LookupError(nome)
    return next((c for c in achados if c.get("country_code") == "BR"), achados[0])


def _consultar_clima(nome: str):
    """(lugar, dados) do Open-Meteo para a cidade, guardados por 10 min."""
    guardado = _CACHE_CLIMA.get(nome.lower())
    if guardado and (datetime.now() - guardado[0]).total_seconds() < 600:
        return guardado[1], guardado[2]
    local = _achar_cidade(nome)
    resposta = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": local["latitude"], "longitude": local["longitude"], "timezone": "auto", "forecast_days": 3,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        },
        timeout=10,
    )
    resposta.raise_for_status()
    dados = resposta.json()
    _CACHE_CLIMA[nome.lower()] = (datetime.now(), local, dados)
    return local, dados


def clima_em_numeros(cidade: str = "") -> dict | None:
    """O tempo agora e nos próximos 3 dias em números (o mesmo cache da previsão falada), para o app do celular.
    None se não deu para consultar."""
    from config import LOCAL_DO_CLIMA

    nome = (cidade or LOCAL_DO_CLIMA).strip()
    try:
        local, dados = _consultar_clima(nome)
        agora, diario = dados["current"], dados["daily"]
        return {
            "lugar": local.get("name", nome),
            "temperatura": round(agora["temperature_2m"]),
            "sensacao": round(agora["apparent_temperature"]),
            "codigo": agora["weather_code"],
            "descricao": CODIGOS_DO_TEMPO.get(agora["weather_code"], "tempo variável"),
            "umidade": round(agora["relative_humidity_2m"]),
            "vento": round(agora["wind_speed_10m"]),
            "dias": [{"data": diario["time"][i], "codigo": diario["weather_code"][i],
                      "minima": round(diario["temperature_2m_min"][i]), "maxima": round(diario["temperature_2m_max"][i]),
                      "chuva": diario["precipitation_probability_max"][i]} for i in range(len(diario["time"]))],
        }
    except Exception as exc:
        logger.debug("Clima em números indisponível: %s", exc)
        return None


def obter_previsao_tempo(cidade: str = "", dia: str = "hoje") -> str:
    """Tempo agora e previsão de hoje, amanhã ou depois de amanhã (Open-Meteo: sem chave, sem conta)."""
    from config import LOCAL_DO_CLIMA

    nome = (cidade or LOCAL_DO_CLIMA).strip()
    deslocamento = {"hoje": 0, "amanha": 1, "depois": 2}.get(dia, 0)
    try:
        local, dados = _consultar_clima(nome)
        lugar = local.get("name", nome)
        if local.get("admin2") and local["admin2"] != lugar:
            lugar += f", {local['admin2']}"
        diario = dados["daily"]
        descricao = CODIGOS_DO_TEMPO.get(diario["weather_code"][deslocamento], "tempo variável")
        minima, maxima = diario["temperature_2m_min"][deslocamento], diario["temperature_2m_max"][deslocamento]
        chuva = diario["precipitation_probability_max"][deslocamento]
        previsao = f"mínima de {minima:.0f}°C, máxima de {maxima:.0f}°C"
        if chuva is not None:
            previsao += f" e {chuva:.0f}% de chance de chuva"
        if deslocamento:
            quando = "amanhã" if deslocamento == 1 else "depois de amanhã"
            return f"Previsão para {quando} em {lugar}: {descricao}, {previsao}."

        agora = dados["current"]
        return (
            f"Agora em {lugar}: {agora['temperature_2m']:.0f}°C, sensação de {agora['apparent_temperature']:.0f}°C, "
            f"{CODIGOS_DO_TEMPO.get(agora['weather_code'], 'tempo variável')}, umidade de {agora['relative_humidity_2m']:.0f}% "
            f"e vento de {agora['wind_speed_10m']:.0f} km/h. Hoje: {descricao}, {previsao}."
        )
    except LookupError:
        return f"Não encontrei a cidade {nome} para ver a previsão."
    except Exception as e:
        logger.warning("Erro ao consultar previsão do tempo: %s", e, exc_info=True)
        return f"Não consegui consultar a previsão do tempo agora: {e}"


def buscar_rota(origem: str, destino: str) -> str:
    try:
        url = f"https://www.google.com/maps/dir/?api=1&origin={urllib.parse.quote(origem)}&destination={urllib.parse.quote(destino)}"
        webbrowser.open(url)
        return f"Rota de '{origem}' para '{destino}' aberta."
    except Exception as e:
        return f"Erro: {e}"

def enviar_mensagem_whatsapp(numero: str, mensagem: str, confirmado: bool = False) -> str:
    try:
        if not confirmado and not _confirmar_acao(f"enviar esta mensagem para {numero}: {mensagem}"):
            return "Ação cancelada por você."
        num_limpo = "".join(filter(str.isdigit, numero))
        if not num_limpo.startswith("55") and len(num_limpo) <= 11:
            num_limpo = "55" + num_limpo
        pywhatkit.sendwhatmsg_instantly(f"+{num_limpo}", mensagem, wait_time=10, tab_close=True)
        return "Mensagem enviada no WhatsApp."
    except Exception as e:
        # Fallback para WhatsApp Web
        try:
            url = f"https://web.whatsapp.com/send?phone={num_limpo}&text={urllib.parse.quote(mensagem)}"
            webbrowser.open(url)
            return "WhatsApp Web aberto."
        except:
            return f"Erro: {e}"