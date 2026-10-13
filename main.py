# main.py
import os
import logging
import re
import time
import multiprocessing
import threading
import difflib
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from config import (
    MODO_CONVERSA_PADRAO,
    AUTO_CARREGAR_FONTES,
    EXIGIR_PALAVRA_ATIVACAO,
    PALAVRAS_ATIVACAO,
    JANELA_ATIVACAO_SEGUNDOS,
    JANELA_CONTINUACAO_SEGUNDOS,
)
from llm_clients import llm_clients
from agentes import PainelCompartilhado, criar_orquestrador
from llm_nuvem import responder_na_nuvem
import memory
from memory import (
    indexar_pasta_documentos,
    buscar_nos_documentos_modo,
    atualizar_memoria_usuario,
    buscar_memoria,
    criar_nota_obsidian,
    buscar_notas_obsidian,
    importar_fonte_local,
    importar_url,
    importar_video_transcricao,
    importar_emails_importantes,
    auditar_biblioteca_conhecimento,
    iniciar_observador_memoria,
    mudar_modo_conversa,
    notelooklm_responder,
    registrar_conversa,
    agendar_indexacao_em_background,
    status_indexacao,
)
from tools import (
    listar_arquivos_da_pasta,
    criar_pasta,
    encontrar_e_abrir,
    abrir_arquivo,
    abrir_explorador,
    abrir_app,
    criar_ou_salvar_anotacao,
    abrir_navegador,
    ler_conteudo_de_site,
    navegar_e_consultar_site,
    analisar_tela,
    pesquisar_web_autonoma,
    buscar_manchetes,
    salvar_pesquisa_web,
    obter_principais_noticias,
    verificar_novos_emails,
    pesquisar_youtube,
    pesquisar_spotify,
    pesquisar_planalto_leis,
    obter_data_hora_atual,
    abrir_calendario,
    enviar_email,
    criar_evento_agenda,
    listar_compromissos,
    buscar_rota,
    enviar_mensagem_whatsapp,
    abrir_whatsapp,
    obter_previsao_tempo,
    tentativa_segura,
)
from voice import (
    falar,
    falar_async,
    microfone_ativo,
    microfone_disponivel,
    ouvir,
    selecionar_microfone,
    selecionar_saida_audio,
)
from analytics import AnalisadorSistema
from agente_aprendizagem import AgenteAprendizagem
from agenda_lembretes import GerenciadorAgenda
from google_drive_sync import sync_notelooklm_from_google_drive
from visao import GestosMaoDetector, GestorVisao
from interface_texto import (
    iniciar as iniciar_interface_texto,
    iniciar_tela_carregamento_em_background,
    fechar_tela_carregamento,
)
from professora_idiomas import processar_comando_idiomas

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("beatriz.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

analisador = AnalisadorSistema()
agente_aprendizagem = AgenteAprendizagem()
gerenciador_agenda = GerenciadorAgenda()
EXECUTOR = ThreadPoolExecutor(max_workers=4)
GESTO_ATUAL = {"valor": None}
MICROFONE_EVENTO = None


def pedir_confirmacao(acao: str) -> bool:
    """Exige confirmação explícita antes de executar uma ação externa."""
    try:
        resposta = input(f"Confirmar {acao}? [s/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return resposta in {"s", "sim", "y", "yes"}

MAPA_DE_FERRAMENTAS = {
    "listar_arquivos_da_pasta": listar_arquivos_da_pasta,
    "criar_pasta": criar_pasta,
    "encontrar_e_abrir": encontrar_e_abrir,
    "abrir_arquivo": abrir_arquivo,
    "abrir_app": abrir_app,
    "criar_ou_salvar_anotacao": criar_ou_salvar_anotacao,
    "atualizar_memoria_usuario": atualizar_memoria_usuario,
    "buscar_memoria": buscar_memoria,
    "criar_nota_obsidian": criar_nota_obsidian,
    "buscar_notas_obsidian": buscar_notas_obsidian,
    "importar_fonte_local": importar_fonte_local,
    "importar_url": importar_url,
    "importar_video_transcricao": importar_video_transcricao,
    "importar_emails_importantes": importar_emails_importantes,
    "auditar_biblioteca_conhecimento": auditar_biblioteca_conhecimento,
    "buscar_nos_documentos_modo": buscar_nos_documentos_modo,
    "mudar_modo_conversa": mudar_modo_conversa,
    "abrir_navegador": abrir_navegador,
    "ler_conteudo_de_site": ler_conteudo_de_site,
    "navegar_e_consultar_site": navegar_e_consultar_site,
    "analisar_tela": analisar_tela,
    "pesquisar_youtube": pesquisar_youtube,
    "pesquisar_spotify": pesquisar_spotify,
    "pesquisar_planalto_leis": pesquisar_planalto_leis,
    "obter_data_hora_atual": obter_data_hora_atual,
    "abrir_calendario": abrir_calendario,
    "enviar_email": enviar_email,
    "criar_evento_agenda": criar_evento_agenda,
    "listar_compromissos": listar_compromissos,
    "buscar_rota": buscar_rota,
    "enviar_mensagem_whatsapp": enviar_mensagem_whatsapp,
}


def classificar_com_ollama(comando):
    """Classifica o comando usando Ollama para decidir rota rápida."""
    if not llm_clients.is_ollama_available():
        return None

    try:
        resposta = llm_clients.chat(
            f"Comando: {comando}\nCategoria:",
            system_prompt=(
                "Classifique o comando do usuário em exatamente uma categoria e responda só com ela.\n"
                "simples: conversa, cumprimento, opinião, explicação ou conhecimento geral estável.\n"
                "pesquisa: fatos atuais, notícias, preços, resultados, clima, pessoas ou dados que mudam.\n"
                "ferramentas: abrir, enviar, criar, agendar ou controlar algo.\n"
                "sistema: configuração ou estado da própria assistente."
            ),
            opcoes={"temperature": 0, "num_predict": 5},
        ).lower()
    except Exception as exc:
        logger.warning("Falha ao classificar com Ollama: %s", exc)
        return None
    for categoria in ("simples", "pesquisa", "ferramentas", "sistema"):
        if categoria in resposta:
            return categoria
    return None


def extrair_texto_apos_comando(comando: str, frases: list[str]) -> str:
    texto = comando.strip()
    for frase in frases:
        if frase in texto.lower():
            restante = texto.lower().split(frase, 1)[1].strip()
            if restante:
                return restante.strip(" ?!.,;:")
    return ""


def perguntar_durante_agendamento(pergunta: str) -> str:
    """Pergunta um dado por voz e permite digitação se a captura falhar."""
    falar(pergunta)
    resposta = obter_comando_usuario()
    if resposta:
        return resposta.strip()

    try:
        resposta = input(f"{pergunta} ").strip()
        if resposta:
            print(f"Você: {resposta}")
        return resposta
    except EOFError:
        return ""


def agendar_compromisso_interativo(comando: str):
    """Coleta os dados que faltam e cria o compromisso no calendário."""
    titulo = perguntar_durante_agendamento("Qual é o título do compromisso?")
    hora = perguntar_durante_agendamento("Qual é a hora do compromisso? Exemplo: 14 horas.")
    data = perguntar_durante_agendamento("Qual é o dia do compromisso? Exemplo: amanhã ou 10 de setembro.")
    quando_lembrar = perguntar_durante_agendamento(
        "Quando devo lembrar você? Exemplo: 10 minutos antes ou no dia anterior."
    )

    if not titulo or not data or not hora or not quando_lembrar:
        return "Não consegui reunir todos os dados. O agendamento foi cancelado."

    return criar_evento_agenda(titulo, f"{data} às {hora}", quando_lembrar=quando_lembrar)


def responder_com_pesquisa_web(pergunta: str, salvar: bool = False):
    """Pesquisa várias fontes por HTTP e resume; o Playwright fica como reserva.

    O navegador é aberto só para o usuário acompanhar; a resposta vem das páginas
    lidas diretamente, que trazem conteúdo real em vez do texto da página do Google.
    """
    consulta = pergunta.strip()
    if consulta:
        try:
            abrir_navegador(consulta)
        except Exception as exc:
            logger.warning("Falha ao abrir navegador antes da pesquisa: %s", exc)

    conteudo = pesquisar_web_autonoma(consulta)
    if consulta:
        # Esta rota recebe perguntas sobre fatos atuais: as manchetes do Google Notícias trazem
        # data e veículo, e muitas vezes o próprio dado ("X lidera as vendas em setembro").
        try:
            manchetes = buscar_manchetes(consulta)
        except Exception as exc:
            logger.warning("Manchetes indisponíveis: %s", exc)
            manchetes = []
        if manchetes:
            linhas = "\n".join(f"- {titulo} ({data.replace('Notícia publicada em ', '')})" for titulo, _, data in manchetes)
            bloco = f"[Fonte: Google Notícias, manchetes recentes]\n{linhas}"
            conteudo = bloco if conteudo.startswith(("Nenhuma fonte", "Não foi possível")) else f"{bloco}\n\n{conteudo}"
    if conteudo.startswith(("Nenhuma fonte", "Não foi possível")):
        leitura_navegador = navegar_e_consultar_site(consulta, consulta) if consulta else ""
        if leitura_navegador and not leitura_navegador.startswith(("Não consegui", "Para ler sites", "Erro:")):
            if salvar:
                salvar_pesquisa_web(consulta, leitura_navegador)
                indexar_pasta_documentos()
                leitura_navegador += "\nO conteúdo completo foi salvo para consultas futuras."
            return leitura_navegador
        return conteudo

    if salvar:
        salvar_pesquisa_web(consulta, conteudo)
        indexar_pasta_documentos()

    prompt = (
        "Responda em português usando apenas as fontes abaixo. "
        "Responda exatamente o que foi perguntado: se a pergunta pede um valor, nome ou data, "
        "a primeira frase deve trazer esse dado. Não explique o tema em geral. "
        "Use duas a quatro frases no total. Quando as fontes divergirem, diga isso e prefira a mais recente. "
        "Se o dado pedido não aparecer nas fontes, diga isso claramente e não invente. "
        "Cite de passagem o nome do site principal, por exemplo segundo o G1, sem ler URLs. "
        "Não diga que abriu navegador. Sem emojis, markdown ou listas com marcadores.\n\n"
        f"Pergunta: {pergunta}\n\nFontes da internet:\n{conteudo}\n\nResposta:"
    )
    contexto = "Use as fontes fornecidas e preserve o tom natural da Beatriz."
    # Claude, depois Gemini: só a pergunta e o conteúdo público das fontes vão para a nuvem
    # (sem memória nem histórico). Se nenhuma puder responder, o Ollama responde.
    resposta = responder_na_nuvem(prompt, contexto, opcoes={"temperature": 0.3})
    if not resposta:
        try:
            resposta = llm_clients.chat_beatriz(prompt, contexto=contexto)
        except Exception:
            resposta = conteudo[:5000]
    if salvar:
        resposta += "\nO conteúdo completo foi salvo para consultas futuras."
    return resposta


def resposta_sem_contexto(resposta: str) -> bool:
    """Identifica a resposta padrão usada quando a memória local não basta."""
    texto = (resposta or "").lower()
    return any(
        trecho in texto
        for trecho in [
            "não encontrei contexto suficiente",
            "ainda não encontrei contexto",
            "não há informação suficiente",
            "nenhuma informação encontrada",
            "não sei",
            "não tenho conhecimento",
            "não tenho informação",
            "não tenho base suficiente",
            "não consegui confirmar",
        ]
    )


def deve_pesquisar_web(resposta: str | None) -> bool:
    """Quando a resposta for insuficiente, busca na web para tentar responder melhor."""
    if resposta is None:
        return True
    texto = (resposta or "").strip()
    if not texto:
        return True
    return resposta_sem_contexto(texto) or texto.startswith("Posso ajudar") or texto.startswith("Não foi possível")


def limpar_texto_para_fala(texto: str) -> str:
    """Remove markdown, URLs e símbolos, mas mantém a pontuação que dá pausas à voz."""
    texto = re.sub(r"https?://\S+|www\.\S+", " ", texto or "")
    texto = re.sub(r"\[Fonte:[^\]]*\]", " ", texto)
    texto = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", texto, flags=re.MULTILINE)
    texto = re.sub(r"\s*\n+\s*", ". ", texto)
    texto = re.sub(r"[^\wÀ-ÿ\s.,!?;:%$-]", " ", texto, flags=re.UNICODE)
    texto = re.sub(r"\s+([.,!?;:])", r"\1", texto)
    texto = re.sub(r"([.!?])(?:\s*[.!?])+", r"\1", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" ,;:")
    texto = re.sub(r"^[.!?]+\s*", "", texto)
    if texto and texto[-1] not in ".!?":
        texto += "."
    return texto


def falar_e_registrar(pergunta: str, resposta: str):
    """Mostra a resposta em texto primeiro e fala em paralelo para manter a conversa mais fluida."""
    resposta_limpa = limpar_texto_para_fala(resposta)
    print(f"\nBeatriz: {resposta_limpa}")
    registrar_conversa(pergunta, resposta_limpa)
    llm_clients.registrar_turno(pergunta, resposta_limpa)
    falar_async(resposta_limpa)
    abrir_janela_continuacao(ESTADO_ATIVACAO, time.time(), resposta_limpa)


def resumir_noticias(noticias: str) -> str:
    if noticias.startswith("Não consegui"):
        return noticias
    try:
        return llm_clients.chat_beatriz(
            "Resuma em português as principais notícias abaixo, separando por tema. "
            "Diga apenas o que estiver nas fontes e seja concisa.\n\n" + noticias
        )
    except Exception:
        return noticias


def monitorar_notificacoes():
    """Verifica e-mails novos e notícias periodicamente em segundo plano."""
    emails_anunciados = set()
    ultima_noticia = 0
    while True:
        try:
            emails = verificar_novos_emails()
            if emails:
                novos = [linha for linha in emails.splitlines() if linha not in emails_anunciados]
                if novos:
                    falar_async("Você recebeu novos e-mails. " + " ".join(novos))
                    emails_anunciados.update(novos)

            agora = time.time()
            if agora - ultima_noticia >= 21600:
                noticias = resumir_noticias(obter_principais_noticias())
                if noticias and not noticias.startswith("Não consegui"):
                    falar_async("Aqui estão as principais notícias dos seus temas: " + noticias)
                ultima_noticia = agora
        except Exception as exc:
            logger.warning("Falha no monitor de notificações: %s", exc)
        time.sleep(300)


def tentar_executar_ferramenta(comando: str):
    """Executa ferramentas nativas antes de consultar documentos ou usar o LLM geral."""
    texto = comando.strip()
    if not texto:
        return False, None

    c = texto.lower()

    if any(termo in c for termo in ["qual a hora", "que horas", "data de hoje", "qual a data", "hoje"]):
        return True, obter_data_hora_atual()

    if any(termo in c for termo in ["previsao do tempo", "previsão do tempo", "previsao", "previsão", "clima", "temperatura"]):
        cidade = ""
        if " em " in c:
            cidade = c.split(" em ", 1)[1].strip()
            cidade = cidade.replace("?", "").replace("!", "").strip()
        if not cidade:
            cidade = "São Paulo"
        return True, obter_previsao_tempo(cidade)

    if any(termo in c for termo in ["principais notícias", "principais noticias", "notícias de hoje", "noticias de hoje"]):
        return True, resumir_noticias(obter_principais_noticias())

    if any(termo in c for termo in ["novos e-mails", "novos emails", "ver meus e-mails", "ver meus emails"]):
        emails = verificar_novos_emails()
        return True, emails or "Não há e-mails novos ou o Gmail ainda não foi configurado."

    if any(termo in c for termo in ["abrir navegador", "abra o navegador", "abre o navegador", "navegador"]):
        consulta = extrair_texto_apos_comando(texto, ["abrir navegador", "abra o navegador", "abre o navegador", "navegador"])
        if consulta:
            alvo = consulta
        else:
            alvo = ""
        return True, abrir_navegador(alvo)

    if any(termo in c for termo in ["olhe minha tela", "veja minha tela", "leia minha tela", "o que está na tela", "o que esta na tela", "o que tem na tela", "o que há na tela", "o que ha na tela", "identifique a imagem", "descreva a tela"]):
        pergunta = extrair_texto_apos_comando(texto, ["olhe minha tela", "veja minha tela", "leia minha tela", "o que está na tela", "o que esta na tela", "o que tem na tela", "o que há na tela", "o que ha na tela", "identifique a imagem", "descreva a tela"])
        return True, analisar_tela(pergunta or "Descreva o que está na tela, incluindo textos, imagens, filmes, horários e botões relevantes.")

    if any(termo in c for termo in ["acesse o site", "acessar o site", "abra o site", "leia o site", "consulte o site", "veja no site"]):
        alvo = extrair_texto_apos_comando(texto, ["acesse o site", "acessar o site", "abra o site", "leia o site", "consulte o site", "veja no site"])
        pergunta = ""
        for marcador in [" e me diga ", " e veja ", " e informe ", " e leia "]:
            if marcador in alvo:
                alvo, pergunta = alvo.split(marcador, 1)
                break
        return True, navegar_e_consultar_site(alvo.strip() or texto, pergunta.strip())

    if any(termo in c for termo in ["filmes em cartaz", "filmes no ingresso", "horários do cinema", "horarios do cinema"]):
        return True, navegar_e_consultar_site(
            "https://www.ingresso.com/",
            "Quais filmes estão em cartaz e quais são os horários das sessões? Liste cinema, filme, data e horário quando estiverem visíveis.",
        )

    if any(termo in c for termo in ["pesquise no youtube", "youtube", "pesquisar no youtube"]):
        consulta = extrair_texto_apos_comando(texto, ["pesquise no youtube", "youtube", "pesquisar no youtube"])
        alvo = consulta or "assistente pessoal"
        return True, pesquisar_youtube(alvo)

    if any(termo in c for termo in ["pesquise no spotify", "spotify", "pesquisar no spotify"]):
        consulta = extrair_texto_apos_comando(texto, ["pesquise no spotify", "spotify", "pesquisar no spotify"])
        alvo = consulta or "música brasileira"
        return True, pesquisar_spotify(alvo)

    if any(termo in c for termo in ["pesquise no planalto", "lei do planalto", "planalto", "pesquisar no planalto"]):
        consulta = extrair_texto_apos_comando(texto, ["pesquise no planalto", "lei do planalto", "planalto", "pesquisar no planalto"])
        alvo = consulta or "educação"
        return True, pesquisar_planalto_leis(alvo)

    if any(termo in c for termo in [
        "marcar na agenda",
        "agende pra mim",
        "agendar pra mim",
        "agende para mim",
        "agendar para mim",
        "agenda pra mim",
        "agenda para mim",
    ]):
        return True, agendar_compromisso_interativo(texto)

    if any(termo in c for termo in [
        "conectar google agenda",
        "vincular google agenda",
        "conectar agenda do google",
        "vincular agenda do google",
        "conectar meu google",
    ]):
        from google_agenda import conectar_google_agenda
        falar("Vou abrir o navegador para você entrar na sua conta do Google.")
        return True, conectar_google_agenda()

    if any(termo in c for termo in ["desconectar google agenda", "desvincular google agenda"]):
        from google_agenda import desconectar_google_agenda
        return True, desconectar_google_agenda()

    if any(termo in c for termo in [
        "listar compromissos",
        "meus compromissos",
        "próximos eventos",
        "quais os compromissos",
        "o que tenho na agenda",
        "próximos compromissos",
    ]):
        return True, listar_compromissos()

    if any(termo in c for termo in ["abrir calendário", "abrir agenda", "calendario", "agenda"]):
        return True, abrir_calendario()

    if "http://" in c or "https://" in c:
        url = next((parte for parte in texto.split() if parte.startswith(("http://", "https://"))), "")
        return True, responder_com_pesquisa_web(url)

    if any(termo in c for termo in ["buscar nas notas", "buscar no obsidian", "pesquisar nas notas", "consultar minhas notas"]):
        consulta = extrair_texto_apos_comando(texto, ["buscar nas notas", "buscar no obsidian", "pesquisar nas notas", "consultar minhas notas"])
        return True, buscar_notas_obsidian(consulta or texto)

    if any(termo in c for termo in ["auditar biblioteca", "verificar biblioteca", "qualidade da biblioteca"]):
        return True, auditar_biblioteca_conhecimento()

    if any(termo in c for termo in ["atualizar biblioteca", "reindexar documentos", "indexar documentos"]):
        return True, indexar_pasta_documentos()

    if any(termo in c for termo in ["salve o conteúdo", "salvar o conteúdo", "guarde o conteúdo", "guardar o conteúdo"]):
        consulta = extrair_texto_apos_comando(texto, ["sobre", "de"])
        return True, responder_com_pesquisa_web(consulta or texto, salvar=True)

    if any(termo in c for termo in ["marcar evento", "agendar", "criar evento", "me lembre", "marque evento"]):
        titulo = "Evento"
        if " para " in c:
            titulo = c.split(" para ", 1)[1].strip().split(" às ", 1)[0].strip() or titulo
        data_hora = "hoje às 20:00"
        if " amanhã " in c:
            data_hora = "amanhã às 18:00"
        if " às " in c:
            partes = c.split(" às ", 1)[1].strip()
            if partes:
                data_hora = "".join(partes.split(".", 1)[0]) if "amanhã" in c else f"{('amanhã' if 'amanhã' in c else 'hoje')} às {partes}"
        if "amanhã" in c and "às" in c:
            data_hora = "amanhã às " + c.split(" às ", 1)[1].strip().split()[0]
        return True, criar_evento_agenda(titulo, data_hora, confirmado=True)

    if any(termo in c for termo in ["abrir app", "abra o app", "abrir aplicativo", "abra o aplicativo"]):
        nome = extrair_texto_apos_comando(texto, ["abrir app", "abra o app", "abrir aplicativo", "abra o aplicativo"])
        return True, abrir_app(nome or "notepad")

    if any(termo in c for termo in [
        "abrir este pastara",
        "abrir este diretório",

        "abra este pc",
        "abrir meu pc",
        "abra meu pc",
        "abrir computador",
        "abra o computador",
        "abrir explorador de arquivos",
        "abra o explorador de arquivos",
    ]):
        return True, abrir_explorador()

    if any(termo in c for termo in ["crie uma pasta", "criar uma pasta", "crie pasta", "criar pasta", "nova pasta"]):
        caminho = extrair_texto_apos_comando(
            texto,
            ["crie uma pasta", "criar uma pasta", "crie pasta", "criar pasta", "nova pasta"],
        )
        return True, criar_pasta(caminho)

    if any(termo in c for termo in [
        "abrir pastas",
        "abrir pasta",
        "abra a pasta",
        "abra as pastas",
        "localizar pasta",
        "localize a pasta",
        "abrir arquivo",
        "abra o arquivo",
        "abre o arquivo",
        "abrir documento",
        "abra o documento",
        "abrir planilha",
        "abra a planilha",
    ]):
        nome = extrair_texto_apos_comando(
            texto,
            [
                "abrir pastas",
                "abrir pasta",
                "abra a pasta",
                "abra as pastas",
                "localizar pasta",
                "localize a pasta",
                "abrir arquivo",
                "abra o arquivo",
                "abre o arquivo",
                "abrir documento",
                "abra o documento",
                "abrir planilha",
                "abra a planilha",
            ],
        )
        for sufixo in [
            " em geral no pc",
            " e geral no pc",
            " em geral no computador",
            " e geral no computador",
            " no computador",
            " no pc",
        ]:
            nome = nome.removesuffix(sufixo).strip()
        if not nome:
            return True, "Diga o nome ou caminho da pasta ou arquivo que devo abrir no PC."
        return True, abrir_arquivo(nome)

    if any(termo in c for termo in [
        "encontre e abra",
        "encontrar e abrir",
        "localize e abra",
        "localizar e abrir",
        "procure e abra",
        "procurar e abrir",
        "abra a pasta",
        "abra o arquivo",
    ]):
        alvo = extrair_texto_apos_comando(
            texto,
            [
                "encontre e abra",
                "encontrar e abrir",
                "localize e abra",
                "localizar e abrir",
                "procure e abra",
                "procurar e abrir",
                "abra a pasta",
                "abra o arquivo",
            ],
        )
        return True, encontrar_e_abrir(alvo)

    if any(termo in c for termo in ["rota", "traçar rota", "mostrar rota", "como chegar"]):
        if " para " in texto.lower():
            origem, destino = texto.lower().split(" para ", 1)
            if " de " in origem:
                origem = origem.split(" de ", 1)[1].strip()
            return True, buscar_rota(origem.strip(), destino.strip())
        return True, "Posso traçar a rota, mas preciso saber a origem e o destino."

    if any(termo in c for termo in ["whatsapp", "mandar mensagem no whatsapp", "abrir whatsapp", "abra whatsapp"]):
        if "para" in c:
            numero = c.split("para", 1)[1].strip()
            mensagem = "Olá!"
            if " mensagem " in c:
                mensagem = c.split("mensagem", 1)[1].strip().strip(" ?!.,;:") or mensagem
            if not pedir_confirmacao(f"enviar esta mensagem para {numero}: {mensagem}"):
                return True, "Ação cancelada por você."
            return True, enviar_mensagem_whatsapp(numero, mensagem, confirmado=True)
        return True, abrir_whatsapp()

    if any(termo in c for termo in ["email", "e-mail", "enviar email", "envie email", "manda email"]):
        destinatario = ""
        if " para " in c:
            destinatario = c.split(" para ", 1)[1].strip()
            destinatario = destinatario.replace("?", "").replace("!", "").strip()
        if destinatario:
            assunto = "Contato"
            mensagem = "Olá!"
            return True, enviar_email(destinatario, assunto, mensagem, confirmado=True)
        return True, "Preciso do destinatário do e-mail para abrir o cliente de e-mail."

    if any(termo in c for termo in ["pesquise", "procure", "buscar"]):
        consulta = extrair_texto_apos_comando(texto, ["pesquise", "procure", "buscar"])
        alvo = consulta or "assistente pessoal"
        salvar = any(termo in c for termo in ["salve", "salvar", "guarde", "guardar"])
        return True, responder_com_pesquisa_web(alvo, salvar=salvar)

    return False, None


def iniciar_webcam_em_background(camera_index: int = None):
    """Inicia a detecção de gestos em segundo plano apenas quando houver webcam disponível."""
    try:
        from visao import detectar_camera_disponivel, webcam_disponivel
        indice = camera_index if camera_index is not None else detectar_camera_disponivel()
        if indice is None or not webcam_disponivel(indice):
            logger.info("Webcam não detectada; continuando em modo texto/voz sem visão.")
            return

        detector = GestosMaoDetector(camera_index=indice)
        gestor = GestorVisao(funcoes={
            "explorar": lambda ctx: listar_arquivos_da_pasta(ctx.get("caminho", os.getcwd())),
            "selecionar": lambda ctx: abrir_navegador(ctx.get("consulta", "beatriz assistente pessoal")),
            "pesquisar": lambda ctx: abrir_navegador(ctx.get("consulta", "assistente ia")),
            "cancelar": lambda ctx: "Ação cancelada pelo gesto.",
            "confirmar": lambda ctx: "Gesto confirmado.",
        })

        def callback(gesto, frame):
            if not gesto:
                return
            GESTO_ATUAL["valor"] = gesto
            contexto = {
                "caminho": os.getcwd(),
                "consulta": "assistente ia",
                "acao": gesto,
            }
            resultado = gestor.interpretar_gesto(gesto, contexto)
            if resultado:
                print(f"[Gesto] {gesto} -> {resultado}")

        detector.iniciar_camera(exibir=False, callback=callback)
    except Exception as exc:
        logger.warning("Webcam indisponível ou falha ao iniciar detecção de gestos: %s", exc)


def diagnosticar_hardware():
    """Analisa o estado de câmera, microfone, áudio, Ollama e integrações opcionais no início."""
    from visao import detectar_camera_disponivel
    from voice import AUDIO_DISPONIVEL, microfone_disponivel

    camera_index = detectar_camera_disponivel()
    status = {
        "camera": {
            "disponivel": camera_index is not None,
            "indice": camera_index,
        },
        "microfone": {
            "disponivel": microfone_disponivel(),
        },
        "audio": {
            "disponivel": AUDIO_DISPONIVEL,
        },
        "ollama": {
            "disponivel": llm_clients.is_ollama_available(),
        },
    }

    try:
        from config import GOOGLE_DRIVE_FOLDER_ID, GOOGLE_CREDENTIALS_FILE
        status["google_drive"] = {
            "configurado": bool(GOOGLE_DRIVE_FOLDER_ID),
            "credencial_local": os.path.exists(GOOGLE_CREDENTIALS_FILE),
        }
    except Exception:
        status["google_drive"] = {"configurado": False, "credencial_local": False}

    camera_texto = "ativa"
    if camera_index is not None:
        camera_texto = f"ativa no índice {camera_index}"

    linhas = [
        "[Diagnóstico de inicialização]",
        f"- Câmera: {camera_texto if status['camera']['disponivel'] else 'indisponível'}",
        f"- Microfone: {'ativo' if status['microfone']['disponivel'] else 'indisponível'}",
        f"- Áudio de saída: {'ativo' if status['audio']['disponivel'] else 'indisponível'}",
        f"- Ollama: {'ativo' if status['ollama']['disponivel'] else 'indisponível'}",
        f"- Google Drive: {'configurado' if status['google_drive']['configurado'] else 'não configurado'}",
    ]
    print("\n".join(linhas))
    logger.info("Diagnóstico de hardware concluído: %s", status)
    return status


def obter_comando_usuario():
    """Obtém entrada em voz, ou em texto como fallback se o microfone não estiver disponível."""
    if (MICROFONE_EVENTO is None or MICROFONE_EVENTO.is_set()) and microfone_ativo() and microfone_disponivel():
        return filtrar_fala_por_ativacao(ouvir())

    try:
        retorno = input("Digite seu comando (ou 'sair'): ").strip()
        if retorno:
            print(f"Você: {retorno}")
        return retorno
    except EOFError:
        return ""


def filtrar_fala_por_ativacao(texto: str) -> str:
    """Só repassa a fala que chamou a Beatriz (ou veio dentro da janela de conversa)."""
    comando = extrair_comando_ativado(texto, time.time(), ESTADO_ATIVACAO)
    if comando is None:
        return ""
    if comando == "":
        print(f"\nBeatriz: {RESPOSTA_SO_NOME}")
        falar_async(RESPOSTA_SO_NOME)
        return ""
    return comando


def comando_solicita_encerramento(comando: str, palavras_desligar: list[str]) -> bool:
    """Reconhece desligamento sem confundir trechos de palavras maiores."""
    texto = re.sub(r"[^\wÀ-ÿ\s]", " ", (comando or "").lower(), flags=re.UNICODE)
    texto = re.sub(r"\s+", " ", texto).strip()
    comandos_exatos = {re.sub(r"\s+", " ", palavra.lower()).strip() for palavra in palavras_desligar}
    return texto in comandos_exatos


# ------------------------------------------------------------------ palavra de ativação
RESPOSTA_SO_NOME = "Sim? Pode falar."
INTERJEICOES_CHAMADA = {"ei", "ô", "o", "ok", "okay", "hey", "alô", "e aí", "ah"}
CARACTERES_FALA_POR_SEGUNDO = 14.0  # estimativa da voz a +15%, para a janela começar depois da fala


@dataclass
class EstadoAtivacao:
    """Até quando a próxima frase falada é aceita sem o nome (relógio de time.time())."""
    janela_ate: float = 0.0

    def janela_aberta(self, agora: float) -> bool:
        return agora < self.janela_ate


ESTADO_ATIVACAO = EstadoAtivacao()


def _normalizar_token(token: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", token.lower())
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"[^\w]", "", sem_acento)


INTERJEICOES_CHAMADA_NORMALIZADAS = {_normalizar_token(i) for i in INTERJEICOES_CHAMADA}


def _eh_palavra_ativacao(token: str, palavras=None) -> bool:
    palavras = [_normalizar_token(p) for p in (palavras or PALAVRAS_ATIVACAO)]
    normal = _normalizar_token(token)
    if not normal:
        return False
    if normal in palavras:
        return True
    # Tolera pequenas variações da transcrição ("beatrys", "beatri").
    return len(normal) >= 6 and bool(difflib.get_close_matches(normal, palavras, n=1, cutoff=0.85))


def _localizar_nome(tokens: list[str]) -> tuple[int, int] | None:
    """Devolve (início, fim) do nome entre os tokens: nas 3 primeiras palavras ou na última.

    No meio da frase o nome costuma ser citação ("falei com a Beatriz ontem"), não chamado.
    """
    candidatos = list(range(min(3, len(tokens))))
    if tokens and len(tokens) - 1 not in candidatos:
        candidatos.append(len(tokens) - 1)
    for i in candidatos:
        if _eh_palavra_ativacao(tokens[i]):
            return i, i + 1
        # A transcrição às vezes separa o nome em dois ("bia triz").
        if i + 1 < len(tokens) and _eh_palavra_ativacao(tokens[i] + tokens[i + 1]):
            return i, i + 2
    return None


def _limpar_bordas(texto: str) -> str:
    return re.sub(r"^[\s,;:.!?-]+|[\s,;:-]+$", "", texto)


def extrair_comando_ativado(texto: str, agora: float, estado: EstadoAtivacao,
                            exigir: bool | None = None) -> str | None:
    """Filtra a fala captada pelo microfone pela palavra de ativação.

    Retorna None para ignorar a frase (conversa de fundo), "" quando ela só chamou
    o nome (abre uma janela curta para o comando) ou o comando sem o nome.
    """
    texto = (texto or "").strip()
    if not texto:
        return None
    if exigir is None:
        exigir = EXIGIR_PALAVRA_ATIVACAO
    if not exigir:
        return texto

    tokens = texto.split()
    posicao = _localizar_nome(tokens)
    if posicao is None:
        if estado.janela_aberta(agora):
            estado.janela_ate = 0.0  # a janela vale para uma frase; a resposta abre outra
            return texto
        logger.debug("Fala ignorada (sem a palavra de ativação): %s", texto)
        return None

    inicio, fim = posicao
    antes = _limpar_bordas(" ".join(tokens[:inicio]))
    depois = _limpar_bordas(" ".join(tokens[fim:]))
    if _normalizar_token(antes) in INTERJEICOES_CHAMADA_NORMALIZADAS:
        antes = ""  # "ei, Beatriz" é só a chamada
    # "Beatriz, que horas são?", "que horas são, Beatriz?", "bom dia Beatriz".
    comando = " ".join(parte for parte in (antes, depois) if parte)

    if not comando:
        estado.janela_ate = agora + JANELA_ATIVACAO_SEGUNDOS
        return ""
    estado.janela_ate = 0.0
    return comando


def abrir_janela_continuacao(estado: EstadoAtivacao, agora: float, resposta: str = "") -> None:
    """Depois de responder, aceita perguntas de seguimento sem o nome por um tempo curto.

    A fala é assíncrona, então a janela conta a partir do fim estimado da resposta falada.
    """
    duracao_fala = len(resposta or "") / CARACTERES_FALA_POR_SEGUNDO
    estado.janela_ate = max(estado.janela_ate, agora + duracao_fala + JANELA_CONTINUACAO_SEGUNDOS)


def estado_escuta(agora: float | None = None) -> str:
    """Texto curto para o painel: se a voz precisa do nome ou já está escutando."""
    if not EXIGIR_PALAVRA_ATIVACAO:
        return "escutando"
    agora = time.time() if agora is None else agora
    return "escutando" if ESTADO_ATIVACAO.janela_aberta(agora) else "aguardando o nome"


DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
SAUDACOES_USUARIO = {"bom dia", "boa tarde", "boa noite", "oi", "olá", "ola", "oi beatriz",
                     "olá beatriz", "ola beatriz", "bom dia beatriz", "boa tarde beatriz", "boa noite beatriz"}


COMANDOS_NOVA_CONVERSA = {"nova conversa", "esquece a conversa", "esqueça a conversa", "mudando de assunto",
                          "outro assunto", "novo assunto", "limpar conversa"}


def periodo_do_dia(hora: int) -> str:
    """Retorna 'Bom dia', 'Boa tarde' ou 'Boa noite' conforme a hora."""
    if 5 <= hora < 12:
        return "Bom dia"
    if 12 <= hora < 18:
        return "Boa tarde"
    return "Boa noite"


def saudacao_inicial():
    """Saudação de abertura no estilo Jarvis: período do dia, hora e data."""
    agora = time.localtime()
    periodo = periodo_do_dia(agora.tm_hour)
    data = f"{DIAS_SEMANA[agora.tm_wday]}, {agora.tm_mday} de {MESES[agora.tm_mon - 1]}"
    return (
        f"{periodo}! São {agora.tm_hour:02d}:{agora.tm_min:02d} desta {data}. "
        "Todos os sistemas estão online. Em que posso ajudar?"
    )


def responder_saudacao(comando: str) -> str | None:
    """Responde a saudações simples do usuário sem passar pelo LLM."""
    texto = re.sub(r"[^\wÀ-ÿ\s]", " ", (comando or "").lower(), flags=re.UNICODE)
    texto = re.sub(r"\s+", " ", texto).strip()
    if texto not in SAUDACOES_USUARIO:
        return None
    periodo = periodo_do_dia(time.localtime().tm_hour)
    return f"{periodo}! Estou aqui. O que vamos fazer?"


def iniciar_tela_carregamento():
    """Exibe uma tela de carregamento em terminal enquanto o sistema inicializa."""
    etapas = [
        "Inicializando módulos",
        "Verificando microfone",
        "Verificando câmera",
        "Conectando assistente local",
        "Preparando modo de escuta",
    ]

    print("\n=== Beatriz - iniciando ===")
    for indice, etapa in enumerate(etapas, start=1):
        print(f"[{indice}/{len(etapas)}] {etapa}...", flush=True)
        time.sleep(0.2)

    print("\nSistema pronto para ouvir e responder.\n")


def inicializar_biblioteca_automatica():
    """Carrega a biblioteca de fontes em thread separada para não bloquear a abertura."""
    if not AUTO_CARREGAR_FONTES:
        logger.info("Inicialização automática de fontes desativada pela configuração.")
        return False

    try:
        agendar_indexacao_em_background()
        logger.info("Indexação da biblioteca iniciada em segundo plano.")
        observer_status = iniciar_observador_memoria()
        logger.info("Observador: %s", observer_status)
        return True
    except Exception as exc:
        logger.warning("Não foi possível iniciar a biblioteca em segundo plano: %s", exc)
        return False


def anunciar_tarefa_concluida(pedido: str, tarefa):
    """Chamado pelo orquestrador (em segundo plano) quando os agentes terminam uma tarefa."""
    falar_e_registrar(pedido, tarefa.mensagem_final)
    duracao = (tarefa.concluida_em or time.time()) - tarefa.criada_em
    analisador.registrar_interacao(pedido, ["orquestrador", *tarefa.plano], duracao, tarefa.status == "concluída")


def publicar_status_painel(dados_compartilhados, intervalo=2.0):
    """Mantém o painel informado sobre a indexação e o modo, e aplica o modo escolhido nas abas.

    A indexação roda neste processo, então só daqui dá para saber o progresso real.
    """
    while True:
        try:
            pedido = dados_compartilhados.pop("modo_pedido", None)
            if pedido:
                mudar_modo_conversa(pedido)
            dados_compartilhados["painel"] = {
                "indexacao": status_indexacao(),
                "modo": memory.MODO_CONVERSA_ATUAL,
                "escuta": estado_escuta(),
            }
        except (EOFError, BrokenPipeError, ConnectionError):
            return  # o gerenciador foi encerrado junto com o programa
        except Exception as exc:
            logger.debug("Falha ao publicar status do painel: %s", exc)
        time.sleep(intervalo)


def main(evento_carregamento=None):
    global MICROFONE_EVENTO
    MICROFONE_EVENTO = multiprocessing.Event()
    MICROFONE_EVENTO.set()
    inicializar_biblioteca_automatica()
    gerenciador_audio = multiprocessing.Manager()
    configuracao_audio = gerenciador_audio.dict({"microfone": None, "saida": ""})
    threading.Thread(target=publicar_status_painel, args=(configuracao_audio,), daemon=True).start()
    # O andamento das tarefas dos agentes vai para o mesmo dicionário que o painel lê.
    orquestrador = criar_orquestrador(publicar=PainelCompartilhado(configuracao_audio))

    try:
        processo_interface = multiprocessing.Process(
            target=iniciar_interface_texto,
            args=(evento_carregamento, MICROFONE_EVENTO, configuracao_audio),
            daemon=True,
        )
        processo_interface.start()
        if evento_carregamento is not None:
            evento_carregamento.set()
        fechar_tela_carregamento()
        print("Aba de comunicação aberta automaticamente.")
        logger.info("Interface de texto aberta em janela separada.")
    except Exception as exc:
        if evento_carregamento is not None:
            evento_carregamento.set()
        logger.warning("Não foi possível abrir a interface de texto: %s", exc)

    try:
        from config import GOOGLE_DRIVE_FOLDER_ID, GOOGLE_CREDENTIALS_FILE
        if GOOGLE_DRIVE_FOLDER_ID and os.path.exists(GOOGLE_CREDENTIALS_FILE):
            sync_notelooklm_from_google_drive(confirmado=True)
            logger.info("Sincronização do Google Drive concluída.")
    except Exception as exc:
        logger.warning("Google Drive não sincronizado: %s", exc)

    diagnosticar_hardware()

    # Notícias e e-mails automáticos foram desativados para não interromper a conversa
    # e reduzir o tempo antes do usuário poder formular o primeiro pedido.
    logger.info("Monitor automático de notícias/e-mails desativado por padrão.")

    # falar_async fala em paralelo, então a saudação não atrasa o primeiro comando.
    falar_async(saudacao_inicial())
    print("Biblioteca de conhecimento sendo carregada em segundo plano. Quando terminar, eu aviso que todas as funções já estão prontas.")

    try:
        from visao import detectar_camera_disponivel, webcam_disponivel
        camera_index = detectar_camera_disponivel()
        if camera_index is not None and webcam_disponivel(camera_index):
            EXECUTOR.submit(iniciar_webcam_em_background, camera_index)
            logger.info("Webcam detectada no índice %s e iniciada em segundo plano.", camera_index)
        else:
            logger.info("Webcam não detectada. Sistema continuará em modo texto/voz.")
    except Exception as exc:
        logger.warning("Não foi possível iniciar a webcam: %s", exc)

    palavras_desligar = ["desligar", "encerrar", "sair", "tchau", "fechar", "até logo", "adeus", "finalizar", "parar", "terminar"]
    biblioteca_pronta = False
    configuracao_aplicada = (None, "")

    while True:
        nova_configuracao = (
            configuracao_audio.get("microfone"),
            configuracao_audio.get("saida", ""),
        )
        if nova_configuracao != configuracao_aplicada:
            if not selecionar_microfone(nova_configuracao[0]):
                logger.warning("Não foi possível aplicar o microfone escolhido na interface.")
            if not selecionar_saida_audio(nova_configuracao[1]):
                logger.warning("Não foi possível aplicar a saída de áudio escolhida na interface.")
            configuracao_aplicada = nova_configuracao

        status_biblioteca = status_indexacao()
        if not biblioteca_pronta and status_biblioteca.get("status") == "pronta":
            print("Biblioteca de conhecimento pronta. Todas as funções locais e de memória já estão disponíveis.")
            biblioteca_pronta = True

        for aviso in gerenciador_agenda.verificar_lembretes():
            falar_async(aviso)

        comando = obter_comando_usuario()
        if not comando:
            continue

        idiomas_executado, resposta_idiomas = processar_comando_idiomas(comando)
        if idiomas_executado:
            falar_e_registrar(comando, resposta_idiomas)
            analisador.registrar_interacao(comando, ["professora_idiomas"], 0.0, True)
            continue

        if comando_solicita_encerramento(comando, palavras_desligar):
            falar("Encerrando o sistema. Até logo!")
            break

        resposta_saudacao = responder_saudacao(comando)
        if resposta_saudacao:
            falar_e_registrar(comando, resposta_saudacao)
            analisador.registrar_interacao(comando, [], 0.0, True)
            continue

        comando_normalizado = agente_aprendizagem.normalizar(comando)

        # Agentes antes das ferramentas: as ferramentas reagem a palavras soltas ("hoje", "agenda"),
        # e "faça um PDF com as notícias de hoje" viraria a resposta da data.
        resposta_tarefas = orquestrador.responder_gerenciamento(comando)
        if resposta_tarefas:
            falar_e_registrar(comando, resposta_tarefas)
            analisador.registrar_interacao(comando, ["orquestrador"], 0.0, True)
            continue
        tarefa = orquestrador.enviar(comando, ao_concluir=lambda t, pedido=comando: anunciar_tarefa_concluida(pedido, t))
        if tarefa:
            falar_e_registrar(comando, orquestrador.mensagem_inicio(tarefa))
            continue

        ferramenta_executada, resposta_ferramenta = tentar_executar_ferramenta(comando)
        if ferramenta_executada:
            if resposta_ferramenta:
                    falar_e_registrar(comando, resposta_ferramenta)
            analisador.registrar_interacao(comando, ["ferramenta_nativa"], 0.0, True)
            continue

        if comando_normalizado in ["obrigado", "obrigada", "valeu", "agradecido", "agradecida" , "muito obrigado", "muito obrigada"]:
            falar_e_registrar(comando, "Por nada! Estou aqui para ajudar.")
            analisador.registrar_interacao(comando, [], 0.0, True)
            continue

        if any(expr in comando_normalizado for expr in ["que horas", "qual a hora", "data de hoje"]):
            resposta = obter_data_hora_atual()
            falar_e_registrar(comando, resposta)
            analisador.registrar_interacao(comando, ["obter_data_hora_atual"], 0.0, True)
            continue

        if comando_normalizado.strip(" .!?,") in COMANDOS_NOVA_CONVERSA:
            llm_clients.limpar_historico()
            falar_e_registrar(comando, "Certo, começando um assunto novo.")
            analisador.registrar_interacao(comando, [], 0.0, True)
            continue

        # "e quanto custa?" vira "quanto custa o produto X?" antes de buscar na memória ou na web.
        consulta = llm_clients.reescrever_consulta(comando)
        categoria = classificar_com_ollama(consulta)

        if categoria == "simples":
            try:
                texto = llm_clients.chat_beatriz(comando)
                falar_e_registrar(comando, texto)
                analisador.registrar_interacao(comando, [], 0.0, True)
                continue
            except Exception as exc:
                logger.warning("Ollama falhou para resposta simples: %s", exc)

        if not llm_clients.is_ollama_available():
            falar("Ollama não está disponível. Verifique se o serviço local está ativo.")
            analisador.registrar_interacao(comando, [], 0.0, False)
            continue

        tempo_inicio = time.time()
        try:
            if categoria == "pesquisa":
                # Fatos atuais: a memória local tende a estar desatualizada, então vai direto à web.
                resposta = responder_com_pesquisa_web(consulta)
            else:
                # Sem modo explícito, usa o modo atual (trocado por voz ou pelas abas do painel).
                resposta = notelooklm_responder(consulta)
                if deve_pesquisar_web(resposta):
                    resposta = responder_com_pesquisa_web(consulta)
            if not resposta or not resposta.strip():
                resposta = llm_clients.chat_beatriz(comando)
            falar_e_registrar(comando, resposta)
            analisador.registrar_interacao(comando, [], time.time() - tempo_inicio, True)
        except Exception as exc:
            logger.error("Erro no fluxo principal: %s", exc)
            falar("Desculpe, ocorreu um erro.")
            analisador.registrar_interacao(comando, [], 0.0, False)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    print("1: Iniciando script...")
    print("2: Abrindo tela de carregamento separada...")
    evento_carregamento = multiprocessing.Event()
    processo_carregamento = multiprocessing.Process(
        target=iniciar_tela_carregamento_em_background,
        args=(evento_carregamento,),
        daemon=True,
    )
    processo_carregamento.start()
    print("3: Inicialização em andamento. Entrando no main()...")
    main(evento_carregamento=evento_carregamento)