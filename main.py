# main.py
import json
import os
import sys
import logging
import queue
import re
import time
import multiprocessing
import threading
import difflib
import unicodedata
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from config import (
    MODO_CONVERSA_PADRAO,
    AUTO_CARREGAR_FONTES,
    EXIGIR_PALAVRA_ATIVACAO,
    PALAVRAS_ATIVACAO,
    JANELA_ATIVACAO_SEGUNDOS,
    JANELA_CONTINUACAO_SEGUNDOS,
    JANELA_CONVERSA_SEGUNDOS,
    ENTENDER_SEM_NOME,
    CONFIANCA_MINIMA_VOZ,
    WHATSAPP_COMANDOS,
    WHATSAPP_MEU_NUMERO,
    APP_CELULAR,
    AGENDA_LEMBRETES_WHATSAPP,
    TELA_CONTEXTO_AUTOMATICO,
    MODO_ESCUTA,
    JANELA_CONVERSA_SEMPRE,
    OFERTAS_DE_AJUDA,
    INTERVALO_ENTRE_OFERTAS,
    AMBIENTE_SEGUNDOS,
    TELA_OBSERVAR,
)
import tools
from jeito_de_falar import frase, tipo_de_encerramento, todas as todas_as_frases
from agente_agenda import criar_agente_agenda, iniciar_lembretes
from agente_controle import criar_agente_controle
from agente_impressora import criar_agente_impressora, pede_impressao, texto_da_agenda
from agente_leis import AgenteLeis
from biometria_voz import BIOMETRIA, NEGADO, AgenteBiometria, Falante
import leitura_tela
from agente_pastas import AgentePastas
from fontes import criar_agente_fontes
from processos import iniciar_leve
import niveis_voz
from leitura_tela import AgenteTela, RespostaTela
from canal_whatsapp import CanalWhatsApp
import app_celular
import busca_arquivos
from biblioteca_fontes import BibliotecaDeFontes
from llm_clients import llm_clients
from agentes import (PainelCompartilhado, criar_orquestrador, descricao_do_arquivo_pedido, pede_envio, pedido_de_arquivo,
                     texto_da_entrega)
from cinema import cartao_de_filmes, responder_pedido_de_cinema
from resumo_explicativo import gerar_resumo, preparar_pedido_de_resumo
from llm_nuvem import NOMES_IA, extrair_ia_pedida, responder_na_nuvem
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
    ao_medir_nivel,
    falar,
    falar_async,
    falar_resposta_em_stream,
    falou_durante,
    fim_da_ultima_fala,
    iniciar_escuta_continua,
    microfone_disponivel,
    parar_fala,
    parece_eco,
    preparar_frases,
    descricao_da_voz,
    gerar_arquivo_de_fala,
    responder_troca_de_voz,
    selecionar_microfone,
    selecionar_saida_audio,
    transcrever_amostras,
)
from analytics import AnalisadorSistema
from agente_aprendizagem import AgenteAprendizagem
from google_drive_sync import sync_notelooklm_from_google_drive
from visao import GestosMaoDetector, GestorVisao
from interface_texto import (
    iniciar as iniciar_interface_texto,
    iniciar_tela_carregamento_em_background,
    fechar_tela_carregamento,
)
from config import INTERFACE


def escolher_painel():
    """A função que abre o painel: o web (padrão) ou, sem o pywebview ou com INTERFACE=tk, o clássico."""
    if INTERFACE != "tk":
        try:
            import webview  # noqa: F401  (só confere se está instalado)
            from painel_web import iniciar as iniciar_painel_web
            return iniciar_painel_web
        except ImportError as exc:
            logger.warning("Painel web indisponível (%s); abrindo o painel clássico.", exc)
    return iniciar_interface_texto
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
EXECUTOR = ThreadPoolExecutor(max_workers=4)
GESTO_ATUAL = {"valor": None}
MICROFONE_EVENTO = None


def pedir_confirmacao(acao: str) -> bool:
    """Exige confirmação explícita antes de executar uma ação externa."""
    if CANAL["atual"] != "voz":
        # Pelo WhatsApp ou pelo app não há ninguém no teclado: cancela em vez de travar esperando o input().
        return False
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
                "Classifique o comando do usuário. Responda com UMA palavra: simples, pesquisa, ferramentas ou sistema.\n"
                "simples = conversa, cumprimento, piada, opinião, sentimentos, explicação ou conhecimento geral que "
                "não muda.\n"
                "pesquisa = fatos atuais: notícias, preços, cotações, resultados de jogos, clima, pessoas ou dados "
                "que mudam.\n"
                "ferramentas = abrir, enviar, criar, agendar ou controlar algo.\n"
                "sistema = configuração ou estado da própria assistente.\n"
                "Exemplos: 'me conta uma piada' -> simples; 'tô cansado' -> simples; 'qual a capital da França' -> "
                "simples; 'quanto tá o dólar' -> pesquisa; 'abre o youtube' -> ferramentas."
            ),
            opcoes={"temperature": 0, "num_predict": 5},
        )
    except Exception as exc:
        logger.warning("Falha ao classificar com Ollama: %s", exc)
        return None
    return categoria_da_resposta(resposta)


# Os modelos pequenos às vezes respondem com a descrição ("conversa", "conhecimento geral") em vez do
# rótulo; sem isso, papo e piada caíam na rota da biblioteca e saíam com cara de relatório.
SINONIMOS_CATEGORIA = (
    ("simples", ("simples", "conversa", "cumpriment", "piada", "opini", "sentiment", "explica", "conhecimento")),
    ("pesquisa", ("pesquisa", "fatos atuais", "noticia", "preco", "cotac", "resultado", "clima")),
    ("ferramentas", ("ferramenta", "abrir", "enviar", "criar", "agendar", "controlar")),
    ("sistema", ("sistema", "configurac")),
)


def categoria_da_resposta(resposta: str) -> str | None:
    texto = _normalizar_frase(resposta or "")
    for categoria, _ in SINONIMOS_CATEGORIA:  # o rótulo certo primeiro
        if categoria in texto:
            return categoria
    for categoria, pistas in SINONIMOS_CATEGORIA:
        if any(pista in texto for pista in pistas):
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
    remoto = canal_remoto()
    if remoto is not None:
        # Pelo WhatsApp (ou pelo app) a pergunta vai para a conversa e a resposta é a próxima mensagem.
        remoto.enviar(pergunta)
        limite = time.time() + SEGUNDOS_RESPOSTA_WHATSAPP
        while time.time() < limite:
            resposta = remoto.receber()
            if isinstance(resposta, str) and resposta:
                avisar_painel("ouvido", f"{ROTULO_DO_CANAL[CANAL['atual']]}: {resposta}", pensando=False)
                return resposta.strip()
            time.sleep(0.5)
        return ""
    falar(pergunta)
    # A resposta vem sem o nome ("amanhã às três"), então abre a janela de seguimento.
    abrir_janela_continuacao(ESTADO_ATIVACAO, time.time())
    limite = time.time() + JANELA_CONTINUACAO_SEGUNDOS
    while time.time() < limite:
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


# Pedido de resumo na frase de pesquisa: "resuma", "me dê um resumo", "explique".
RE_PEDIDO_RESUMO = re.compile(r"\b(?:resum[aeio]\w*|expliqu\w*|explica\w*)\b", re.IGNORECASE)
# O trecho que pede o resumo sai da consulta, para a busca receber só o assunto.
RE_FRASE_RESUMO = re.compile(
    r"\b(?:e\s+)?(?:me\s+|pra mim\s+)?"
    r"(?:(?:(?:d[aáeê]|fa[çc]a|faz|traga|traz|mande|manda|quero|preciso de)\s+)?um\s+(?:breve\s+|pequeno\s+|bom\s+)?resumo"
    r"|resum[ae]|resumir|explique|explica|explicar)\b"
    r"(?:\s+(?:pra|para)\s+mim)?(?:\s+(?:sobre|a respeito d[eoa]s?|de|do|da|dos|das)\b)?",
    re.IGNORECASE,
)


def tirar_pedido_de_resumo(consulta: str) -> str:
    """'e me dê um resumo sobre a reforma tributária' -> 'a reforma tributária'."""
    limpa = re.sub(r"\s+", " ", RE_FRASE_RESUMO.sub(" ", consulta)).strip(" ?!.,;:")
    limpa = re.sub(r"^e\s+|\s+e$", "", limpa).strip(" ?!.,;:")
    return limpa or consulta


def responder_com_pesquisa_web(pergunta: str, salvar: bool = False, preferir: str = None, resumo: bool = False):
    """Pesquisa várias fontes por HTTP e resume; o Playwright fica como reserva.

    O navegador é aberto só para o usuário acompanhar; a resposta vem das páginas
    lidas diretamente, que trazem conteúdo real em vez do texto da página do Google.
    `preferir` ("claude" ou "gemini") escolhe a IA da nuvem que redige primeiro; `resumo`
    troca a resposta direta por um resumo falado de uns dois parágrafos.
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

    if resumo:
        prompt = (
            "Faça em português um resumo falado sobre o tema abaixo, usando apenas as fontes. "
            "Escreva dois parágrafos curtos: o primeiro com o essencial do assunto, "
            "o segundo com os pontos e as novidades mais relevantes. "
            "Escreva como numa conversa, em frases completas, sem emojis, markdown, títulos ou listas com marcadores. "
            "Cite de passagem o nome dos sites, por exemplo segundo o G1, sem ler URLs. "
            "Quando as fontes divergirem, diga isso e prefira a mais recente. "
            "Se as fontes não cobrirem algum ponto importante, diga isso e não invente. "
            "Não diga que abriu navegador.\n\n"
            f"Tema: {pergunta}\n\nFontes da internet:\n{conteudo}\n\nResumo:"
        )
    else:
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
    # Gemini, depois Claude (ORDEM_IA_PESQUISA; ou a IA pedida na frase, primeiro): só a pergunta e o conteúdo público
    # das fontes vão para a nuvem (sem memória nem histórico). Se nenhuma puder responder, o Ollama responde.
    quem_respondeu = []
    resposta = responder_na_nuvem(prompt, contexto, opcoes={"temperature": 0.3}, preferir=preferir, registro=quem_respondeu,
                                  funcao="pesquisa")
    if not resposta:
        try:
            resposta = llm_clients.chat_beatriz(prompt, contexto=contexto)
        except Exception:
            resposta = conteudo[:5000]
    if preferir in NOMES_IA and NOMES_IA[preferir] not in quem_respondeu:
        # A IA pedida não pôde responder (limite, sem chave, sem internet): diz quem respondeu no lugar.
        substituta = f"o {quem_respondeu[0]}" if quem_respondeu else "a IA local"
        resposta = f"O {NOMES_IA[preferir]} não pôde responder agora; usei {substituta}. {resposta}"
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
    # Só o alfabeto do português: alguns modelos (como o qwen) às vezes soltam uma palavra em chinês.
    texto = re.sub(r"[^A-Za-z0-9_À-ÿºª\s.,!?;:%$-]", " ", texto)
    # Risada escrita soa estranha na voz ("Ahah", "hahaha", "kkkk", "rsrs").
    texto = re.sub(r"\b(?:a?h[ae]h[aeh]*|k{3,}|(?:rs){2,})\b[\s,!.]*", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\s+([.,!?;:])", r"\1", texto)
    texto = re.sub(r"([.!?])(?:\s*[.!?])+", r"\1", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" ,;:")
    texto = re.sub(r"^[.!?]+\s*", "", texto)
    if texto and texto[-1] not in ".!?":
        texto += "."
    return texto[:1].upper() + texto[1:]


def registrar_resposta(pergunta: str, resposta_limpa: str, abrir_janela: bool = True):
    """Guarda a troca no histórico e abre a janela para perguntas de seguimento sem o nome."""
    registrar_conversa(pergunta, resposta_limpa)
    llm_clients.registrar_turno(pergunta, resposta_limpa)
    if abrir_janela:
        abrir_janela_continuacao(ESTADO_ATIVACAO, time.time(), resposta_limpa)


# ------------------------------------------------------------------ WhatsApp e app do celular
# De onde veio o comando que está sendo atendido: "voz" (inclui o teclado), "whatsapp" ou "app" (o app do celular).
# Só o laço principal muda este valor; as tarefas em segundo plano guardam o canal de origem. Os dois canais por
# escrito têm a mesma cara (receber, enviar, status): as respostas vão por escrito para eles, sem falar em casa.
CANAL = {"atual": "voz"}
CANAL_WHATSAPP = None  # CanalWhatsApp criado em main() quando WHATSAPP_COMANDOS está ligado
CANAL_APP = None  # app_celular.CanalApp criado em main() quando APP_CELULAR está ligado
APARELHOS = app_celular.Aparelhos()  # os celulares pareados com o app ("Beatriz, conecta meu celular")
# Biblioteca de fontes organizada à mão pelo app (a janela do painel tem a sua, no processo dela): cada mudança pede uma nova
# leitura do índice, que tira o que saiu e lê o que entrou.
BIBLIOTECA = BibliotecaDeFontes(ao_mudar=memory.agendar_indexacao_em_background)
ROTULO_DO_CANAL = {"whatsapp": "WhatsApp", "app": "Celular", "voz": "Voz"}
SEGUNDOS_RESPOSTA_WHATSAPP = 180.0
CONTEXTO_CONVERSA_ESCRITA = (
    "Esta conversa é por escrito (pelo WhatsApp ou pelo app do celular), no tom leve e informal de uma amiga. Responda de "
    "forma direta e útil; pode usar listas curtas quando ajudarem (sem títulos longos) e, no máximo, um emoji "
    "quando combinar."
)


def canal_remoto(canal: str | None = None):
    """O canal por escrito de onde veio o pedido (o WhatsApp ou o app do celular); None se veio pela voz."""
    return {"whatsapp": CANAL_WHATSAPP, "app": CANAL_APP}.get(canal or CANAL["atual"])


def responder_no_whatsapp(pergunta: str, resposta: str, arquivos=(), registrar: bool = True, canal: str | None = None):
    """Envia a resposta pelo canal por escrito do pedido (WhatsApp ou app; e mostra no painel), sem falar em casa."""
    (canal_remoto(canal) or CANAL_WHATSAPP).enviar(resposta, arquivos)
    avisar_painel("resposta", resposta)
    if registrar:
        # A janela de seguimento por voz não abre: quem perguntou não está falando em casa.
        registrar_resposta(pergunta, limpar_texto_para_fala(resposta), abrir_janela=False)


RE_PARA_O_WHATSAPP = re.compile(r"\b(pro|para o|pelo|no) (meu )?(whats\w*|zap)\b", re.IGNORECASE)


VAZIAS_DO_ENVIO = {"zap", "zapzap", "whatsapp", "whats", "wpp", "celular", "app", "aplicativo", "telefone", "iphone", "novo"}


def arquivo_descrito(comando: str, extensoes=(), so_os_que_saem: bool = False) -> tuple[str | None, str]:
    """O arquivo que "manda o PDF da aula de embargos" descreve, pelo nome aproximado: (caminho ou None, palavras da busca).
    Sem descrição ("manda o PDF"), (None, "") e quem chama usa a última tarefa."""
    termos = busca_arquivos.palavras_do_pedido(descricao_do_arquivo_pedido(comando), vazias=VAZIAS_DO_ENVIO)
    if not termos:
        return None, ""
    achados = PASTAS.arquivos.buscar(termos, set(extensoes) or None, maximo=10)
    if so_os_que_saem:  # pelo app, só o que pode ir para o celular (sem Downloads, Área de Trabalho...)
        achados = [(nota, a) for nota, a in achados if app_celular.pode_sair(a.caminho)]
    return (achados[0][1].caminho if achados else None), " ".join(termos)


def mandar_arquivo_pedido(comando: str, orquestrador) -> bool:
    """Atende "manda o arquivo/o PDF/a planilha": o arquivo descrito ("o PDF da aula de embargos", pelo nome aproximado) ou,
    sem descrição, os da última tarefa. False se não for esse pedido.

    Pelo WhatsApp ou pelo app vale sempre (vai para lá); pela voz, só quando o pedido diz para onde ("... pro meu WhatsApp").
    """
    pedido = pedido_de_arquivo(comando)
    if pedido is None:
        return False
    remoto = canal_remoto()
    if remoto is None and (CANAL_WHATSAPP is None or not RE_PARA_O_WHATSAPP.search(comando)):
        return False
    caminho, descrito = (None, "") if pedido == "relatorio" else arquivo_descrito(comando, pedido, CANAL.get("atual") == "app")
    tarefa = None if caminho else orquestrador.ultima_com_arquivos()
    if caminho:
        arquivos = [caminho]
        resposta = f"Aqui está: {os.path.basename(caminho)}."
    elif descrito and tarefa is None:
        resposta, arquivos = f"Não achei nenhum arquivo com “{descrito}”. Diga uma parte do nome do arquivo.", []
    elif tarefa is None:
        resposta, arquivos = "Ainda não tenho nenhum arquivo pronto para mandar. Peça um documento, planilha ou apresentação.", []
    elif pedido == "relatorio":
        arquivos = [tarefa.relatorio] if tarefa.relatorio else []
        resposta = "Aqui está o relatório da última tarefa." if arquivos else "A última tarefa não tem relatório."
    else:
        arquivos = [a for a in tarefa.arquivos if not pedido or a.lower().endswith(pedido)]
        nomes = ", ".join(os.path.basename(a) for a in arquivos)
        tipos = ", ".join(sorted({os.path.splitext(a)[1].lstrip(".").upper() for a in tarefa.arquivos}))
        resposta = f"Aqui está: {nomes}." if arquivos else f"A última tarefa não gerou esse tipo de arquivo; tenho {tipos}."
        if descrito:
            resposta = f"Não achei um arquivo com “{descrito}”; mandei o da última tarefa. " + resposta
    if remoto is not None:
        responder_no_whatsapp(comando, resposta, arquivos=arquivos, registrar=False)
    else:
        CANAL_WHATSAPP.enviar(resposta, arquivos)
        responder_rapido("Mandei no seu WhatsApp." if arquivos else resposta)
    return True


def proximo_comando_remoto() -> str:
    """Comando que chegou pelo WhatsApp ou pelo app do celular; troca o canal atual para as respostas irem para lá."""
    for nome in ("whatsapp", "app"):
        remoto = canal_remoto(nome)
        if remoto is None:
            continue
        comando = remoto.receber()
        if not comando:
            continue
        CANAL["atual"] = nome
        tools.CONFIRMAR_NO_TECLADO = False
        if isinstance(comando, dict):  # um documento ou foto que você mandou
            avisar_painel("ouvido", f"{ROTULO_DO_CANAL[nome]}: arquivo {comando.get('nome', '')}")
            try:
                resposta = FONTES.receber(comando["caminho"], comando.get("nome", ""), comando.get("legenda", ""),
                                          comando.get("origem", nome), nome)
            except Exception as exc:
                logger.exception("Não consegui tratar o arquivo recebido")
                resposta = f"Recebi o arquivo, mas tive um problema ao guardá-lo: {exc}"
            responder_fontes(resposta, nome)
            return ""
        avisar_painel("ouvido", f"{ROTULO_DO_CANAL[nome]}: {comando}")
        return comando
    return ""


def falar_e_registrar(pergunta: str, resposta: str, texto_painel: str = "", canal: str = None):
    """Mostra a resposta em texto primeiro e fala em paralelo para manter a conversa mais fluida.

    `texto_painel` mostra no painel uma versão mais completa do que foi falado (ex.: o resumo inteiro).
    `canal` vem das tarefas em segundo plano; sem ele vale o canal do comando atual.
    """
    if canal_remoto(canal) is not None:
        responder_no_whatsapp(pergunta, texto_painel or resposta, canal=canal)
        return
    resposta_limpa = limpar_texto_para_fala(resposta)
    falar_async(resposta_limpa)  # já exibe o texto
    avisar_painel("resposta", texto_painel or resposta_limpa)
    registrar_resposta(pergunta, resposta_limpa)


# A conversa por voz pede respostas curtas: cada frase a mais é tempo de fala a mais.
CONTEXTO_CONVERSA_FALADA = (
    "Isto é uma conversa por voz, solta e informal, como entre amigos. Responda em até três frases curtas "
    "(menos, se der). Reaja ao que a pessoa disse, de um jeito diferente da última vez, e responda de "
    "verdade: se pedir uma piada, conte uma piada; se pedir para escolher, escolha. Se for papo, mostre "
    "interesse e, quando fizer sentido, termine com uma pergunta curta. Se o assunto pedir mais, dê o "
    "essencial e ofereça os detalhes. Nada de listas, títulos ou markdown.\n"
    "Exemplos do tom (não copie as frases, só o jeito):\n"
    "Pessoa: tô entediado. Beatriz: Ah, vamos resolver isso! Sabia que o polvo tem três corações? "
    "Quer mais uma curiosidade ou uma ideia do que fazer?\n"
    "Pessoa: me conta uma piada. Beatriz: Lá vai: por que o livro de matemática ficou triste? Porque tinha "
    "problemas demais! Essa foi fraquinha, mas eu gosto. Quer outra?\n"
    "Pessoa: pizza ou hambúrguer? Beatriz: Pizza, sem dúvida! Dá pra dividir e ainda sobra pro café da "
    "manhã. Vai de qual sabor?\n"
    "Pessoa: perdi meu emprego hoje. Beatriz: Poxa, sinto muito. Isso pesa mesmo, e é normal ficar "
    "abalado. Quer desabafar um pouco ou prefere que eu ajude a pensar nos próximos passos?\n"
    "Pessoa: tudo bem com você? Beatriz: Tudo ótimo, ainda mais agora com você aqui! E com você, como "
    "tá o dia?"
)


def responder_conversa_em_stream(pergunta: str) -> str:
    """Resposta simples falada enquanto o LLM ainda escreve: a primeira frase sai em segundos."""
    if canal_remoto() is not None:
        resposta = llm_clients.chat_beatriz(pergunta, contexto=CONTEXTO_CONVERSA_ESCRITA)
        responder_no_whatsapp(pergunta, resposta)
        return resposta
    atividade = leitura_tela.contexto_da_atividade() if TELA_CONTEXTO_AUTOMATICO else ""
    resposta = falar_resposta_em_stream(
        llm_clients.chat_beatriz_stream(pergunta, contexto=f"{CONTEXTO_CONVERSA_FALADA}\n{atividade}" if atividade else CONTEXTO_CONVERSA_FALADA),
        limpar=limpar_texto_para_fala,
    )
    resposta_limpa = limpar_texto_para_fala(resposta)
    print(f"\nBeatriz: {resposta_limpa}")
    avisar_painel("resposta", resposta_limpa)
    registrar_resposta(pergunta, resposta_limpa)
    return resposta_limpa


CONTEXTO_CONVIDADO = (
    "Quem está falando agora NÃO é o dono da casa{quem}: é uma visita. Seja simpática e breve (até três frases), "
    "sem listas nem markdown. Você não sabe nada pessoal sobre o dono, a agenda, os arquivos ou a casa; se "
    "perguntarem disso, diga com naturalidade que esses assuntos são só com o dono. Não finja ter feito nada "
    "no computador."
)
# Pedidos que mexem no PC, nos dados ou nas contas do dono: ficam só com ele. A conversa livre do convidado
# não tem acesso a nada disso, esta lista só dá uma resposta mais clara.
RE_SO_DO_DONO = re.compile(
    r"\b(tela|janela|escane\w*|fontes?|agenda|compromiss\w*|lembr\w*|tarefa\w*|rotina|imprim\w*|impressora|e-?mails?|gmail|whatsapp|zap|mensagem|"
    r"document\w*|arquiv\w*|pdf|word|planilha|apresenta\w*|slides?|biblioteca|mem[óo]ria|obsidian|drive|resum\w*|"
    r"aula|estud\w*|ficha|abr[ae]\w*|fech\w*|toc[ae]\w*|liga\w*|deslig\w*|desativ\w*|ativ[ae]\w*|encerr\w*|"
    r"volume|mudo|ar.condicionado|temperatura do ar|aprend\w*|"
    r"sair|pesquis\w*|manda\w*|envi\w*|cri[ae]\w*|faz\b|fa[çc]a\b)",
    re.IGNORECASE,
)
_AVISO_CONVIDADO = {"em": 0.0}


def responder_ao_convidado(comando: str, falante: Falante | None) -> None:
    """O que a Beatriz faz por quem não é o dono: conversar, hora, tempo e filmes. O resto é só do dono.

    Nada daqui lê a memória, a agenda ou os documentos, não grava no histórico (que alimenta a biblioteca)
    e não mexe no PC. A conversa livre usa só o LLM local, sem histórico.
    """
    texto = comando.strip()
    c = texto.lower()
    conhecido = falante.nome if falante is not None and falante.tipo == "conhecido" else ""

    def dizer(resposta: str):
        resposta = limpar_texto_para_fala(resposta)
        falar_async(resposta)
        avisar_painel("resposta", resposta)

    if falante is not None and falante.tipo == "incerto":
        dizer("Não tive certeza de quem falou. Pode repetir, um pouquinho mais devagar?")
        return
    saudacao = responder_saudacao(comando)
    if saudacao:
        if conhecido:
            saudacao = f"{conhecido}! {saudacao}"
        elif time.time() - _AVISO_CONVIDADO["em"] > 600:
            _AVISO_CONVIDADO["em"] = time.time()
            saudacao += " Não conheço a sua voz, mas posso conversar, ver a hora, o tempo e os filmes em cartaz."
        dizer(saudacao)
        return
    encerramento = responder_encerramento(comando)
    if encerramento:
        dizer(encerramento)  # a conversa do dono continua: o convidado não a encerra
        return
    if RE_SO_DO_DONO.search(c):
        dizer(NEGADO if not conhecido else f"{NEGADO} Mas posso conversar com você, {conhecido}.")
        return
    pedido_clima = pedido_de_clima(c)
    if pedido_clima:
        dizer(obter_previsao_tempo(*pedido_clima))
        return
    if any(termo in c for termo in ["qual a hora", "que horas", "data de hoje", "qual a data"]):
        dizer(obter_data_hora_atual())
        return
    cinema = responder_pedido_de_cinema(texto)
    if cinema:
        dizer(cinema)
        return
    contexto = CONTEXTO_CONVIDADO.format(quem=f" (você está falando com {conhecido})" if conhecido else "")
    try:
        resposta = falar_resposta_em_stream(
            llm_clients.chat_beatriz_stream(texto, contexto=contexto, usar_historico=False),
            limpar=limpar_texto_para_fala,
        )
        avisar_painel("resposta", limpar_texto_para_fala(resposta))
    except Exception as exc:
        logger.warning("Conversa com convidado falhou: %s", exc)
        dizer(frase("erro"))


def cumprimentar():
    """Saudação inicial. Ela termina perguntando, então a resposta vale sem dizer o nome."""
    try:
        agenda_de_hoje = AGENDA.resumo_de_abertura()  # "Hoje você ainda tem 2 compromissos; o próximo é..."
    except Exception as exc:
        logger.warning("Agenda indisponível na saudação: %s", exc)
        agenda_de_hoje = ""
    saudacao = saudacao_inicial(agenda_de_hoje)
    falar_async(saudacao)
    avisar_painel("resposta", saudacao)
    abrir_janela_continuacao(ESTADO_ATIVACAO, time.time(), saudacao)


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


# "Como está o tempo hoje?" é sobre o clima (antes caía na regra da data); "quanto tempo falta?" não é.
RE_TEMPO_DO_DIA = re.compile(
    r"\b(como|qual) (esta|está|ta|tá|vai estar|vai ficar|fica|é|e) o tempo\b|\bo tempo (hoje|amanh[ãa]|agora|l[áa] fora|em)\b"
    r"|\bvai chover\b|\bchance de chuva\b|\bvai fazer (frio|calor|sol)\b|\b(est[áa]|t[áa]) (frio|calor|chovendo) l[áa] fora\b"
)


def pedido_de_clima(c: str) -> tuple[str, str] | None:
    """(cidade, dia) se o pedido (em minúsculas) é sobre o tempo; a cidade vazia é a padrão."""
    if not (any(termo in c for termo in ["previsao do tempo", "previsão do tempo", "previsao", "previsão", "clima", "temperatura"])
            or RE_TEMPO_DO_DIA.search(c)):
        return None
    sem_acento = c.replace("ã", "a")
    dia = "depois" if "depois de amanha" in sem_acento else "amanha" if "amanha" in sem_acento else "hoje"
    cidade = ""
    if " em " in c:
        cidade = c.split(" em ", 1)[1]
        cidade = re.sub(r"\b(hoje|amanh[ãa]|depois de amanh[ãa]|agora)\b", "", cidade)
        cidade = cidade.replace("?", "").replace("!", "").strip(" ,.")
    return cidade, dia


def tentar_executar_ferramenta(comando: str):
    """Executa ferramentas nativas antes de consultar documentos ou usar o LLM geral."""
    texto = comando.strip()
    if not texto:
        return False, None

    c = texto.lower()

    # Antes da data e hora: "quais filmes estão em cartaz hoje" é sobre cinema, não sobre o dia.
    resposta_cinema = responder_pedido_de_cinema(texto)
    if resposta_cinema:
        cartao = cartao_de_filmes(texto)
        if cartao:  # pôsteres no painel web; o texto é o mesmo que ela vai falar
            avisar_painel("filmes", limpar_texto_para_fala(resposta_cinema), **cartao)
        return True, resposta_cinema

    pedido_clima = pedido_de_clima(c)
    if pedido_clima:
        resposta_clima = obter_previsao_tempo(*pedido_clima)
        if CANAL["atual"] == "app":  # no app, o tempo também vem num cartão com os próximos dias
            cartao_para_o_app("clima", tools.clima_em_numeros(pedido_clima[0]))
        return True, resposta_clima

    if any(termo in c for termo in ["qual a hora", "que horas", "data de hoje", "qual a data", "hoje"]):
        return True, obter_data_hora_atual()

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

    # Horários das sessões ainda vêm da página do Ingresso.com aberta no navegador (filmes e cinemas: cinema.py).
    if any(termo in c for termo in ["filmes no ingresso", "horários do cinema", "horarios do cinema"]):
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

    if any(termo in c for termo in ["conectar gmail", "vincular gmail", "conectar meu gmail", "conectar o gmail"]):
        from google_gmail import conectar_gmail
        falar("Vou abrir o navegador para você liberar a leitura do Gmail.")
        return True, conectar_gmail()

    # Compromissos, rotina e tarefas ficam com o agente de agenda (AGENDA.responder), antes daqui.
    if any(termo in c for termo in ["calendário do windows", "calendario do windows", "app de calendário", "app de calendario"]):
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
        termos = ["pesquise", "procure", "buscar"]
        # "pelo Gemini" / "no Claude" escolhe a IA que redige; sai da consulta, como o pedido de resumo.
        sem_ia, ia = extrair_ia_pedida(texto)
        consulta = extrair_texto_apos_comando(sem_ia, termos)
        if ia and not consulta:
            # "pesquise pelo Gemini": aqui o Gemini é o assunto da pesquisa, não a IA escolhida.
            ia, consulta = None, extrair_texto_apos_comando(texto, termos)
        resumo = bool(RE_PEDIDO_RESUMO.search(c))
        if resumo and consulta:
            consulta = tirar_pedido_de_resumo(consulta)
        alvo = consulta or "assistente pessoal"
        salvar = any(termo in c for termo in ["salve", "salvar", "guarde", "guardar"])
        return True, responder_com_pesquisa_web(alvo, salvar=salvar, preferir=ia, resumo=resumo)

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


# Frases entendidas pela escuta contínua (voice.py), na ordem: (texto, início, fim, confiança, falante).
FILA_FALAS = queue.Queue()
# Quem falou o comando atual (biometria_voz.Falante); None no teclado, no WhatsApp ou sem biometria em uso.
FALANTE = {"atual": None}
# O que a voz ouviu (e se aceitou) e o que a Beatriz respondeu, para o painel mostrar no chat.
PAINEL_VOZ = None  # multiprocessing.Queue criada em main()
NIVEIS_VOZ = None  # níveis da voz dela e do microfone para o painel (niveis_voz), criados em main()
# Tudo o que o microfone captou e o que ela respondeu, para resumir quando pedirem. Fica só
# em memória: são conversas da casa, que não vão para o disco nem para a nuvem.
REGISTRO_CONVERSA = deque(maxlen=600)  # (quando, quem, texto)


def cartao_para_o_app(tipo: str, dados: dict | None):
    """Pedido feito pelo app do celular: o cartão visual (filmes com pôster, clima, resumo) vai junto da resposta."""
    if dados and CANAL["atual"] == "app" and CANAL_APP is not None:
        try:
            CANAL_APP.cartao(tipo, dados)
        except Exception as exc:
            logger.debug("Cartão não foi para o app: %s", exc)


def avisar_painel(tipo: str, texto: str, motivo: str = "", pensando: bool | None = None, **dados):
    """tipo: "ouvido" (frase aceita), "ignorado" (com o motivo) ou "resposta" (o que ela falou).

    `pensando` diz ao painel se ela vai trabalhar no pedido (padrão: frase aceita sem motivo).
    Cartões do painel web ("resumo", "filmes") levam os dados deles em `dados`.
    """
    if not texto:
        return
    if tipo in ("filmes", "resumo") and dados:
        cartao_para_o_app(tipo, dados)
    quem = {"ouvido": "Usuário", "ignorado": "Ouvido sem resposta", "resposta": "Beatriz"}.get(tipo)
    if quem:
        REGISTRO_CONVERSA.append((time.time(), quem, texto))
    if PAINEL_VOZ is None:
        return
    if pensando is None:
        pensando = tipo == "ouvido" and not motivo
    try:
        PAINEL_VOZ.put_nowait({"tipo": tipo, "texto": texto, "motivo": motivo, "pensando": pensando, **dados})
    except Exception as exc:
        logger.debug("Painel não recebeu o aviso da voz: %s", exc)


# Agenda local (compromissos, rotina, lembretes e tarefas) num SQLite do PC; a conversa livre sobre
# ela usa só o Ollama local. "Abre a agenda" pede ao painel para abrir a janela da agenda.
AGENDA = criar_agente_agenda(llm=llm_clients, ao_abrir=lambda: avisar_painel("abrir_agenda", "abrir a agenda"))

# Impressora (agente_impressora.py): arquivos, textos ditados, a última resposta e a agenda, com um
# "sim" antes de gastar papel. A impressão roda em segundo plano; só um erro volta (avisar).
IMPRESSORA = criar_agente_impressora(
    respostas_recentes=lambda: [resposta for _, resposta in reversed(llm_clients.historico_recente())],
    texto_agenda=lambda t: texto_da_agenda(AGENDA, t),
    avisar=lambda texto: responder_rapido(texto),
)

# Controle remoto (agente_controle.py): a TV e o ar-condicionado pelo Ekaza Mini 3 (infravermelho Tuya), pela rede de casa.
# O aprender um botão roda em segundo plano; o fim volta por avisar.
CONTROLE = criar_agente_controle(avisar=lambda texto: responder_rapido(texto))

# Artigos de lei (agente_leis.py): lê o texto oficial do Planalto e explica com o Gemini; "não entendi"
# traz outra explicação, com outra abordagem.
LEIS = AgenteLeis()

# Biometria vocal (biometria_voz.py): cadastrar e esquecer vozes, "quem está falando?". O que um convidado
# (voz que não é a do dono) pode pedir está em responder_ao_convidado.
AGENTE_VOZ = AgenteBiometria(BIOMETRIA, avisar=lambda texto: responder_rapido(texto))

# Leitura de tela (leitura_tela.py): "resume esse PDF", "por que deu esse erro?", "o que estou fazendo?". Captura a janela
# ativa (ou a tela inteira), lê com o OCR do Windows e responde com o Ollama local; nada vai para a nuvem nem para o histórico.
TELA = AgenteTela(llm=llm_clients, avisar=lambda texto: responder_rapido(texto))


# Pastas por voz (agente_pastas.py): "abre a pasta da OAB", "abre meus downloads". Entende siglas e lembra a escolha.
PASTAS = AgentePastas()


def responder_fontes(texto: str, canal: str = "voz") -> None:
    """Resposta do agente de fontes (arquivos recebidos, escaneamento...). No WhatsApp vai por escrito; na voz fala e mostra no
    painel. Não entra no histórico (que alimenta a biblioteca): o que estava nos seus arquivos não deve ficar guardado."""
    if canal_remoto(canal) is not None:
        responder_no_whatsapp("", texto, registrar=False, canal=canal)
        return
    falar_async(limpar_texto_para_fala(texto))
    avisar_painel("resposta", texto)
    abrir_janela_continuacao(ESTADO_ATIVACAO, time.time(), texto)


def estudar_arquivos(orquestrador, caminhos, pedido: str, canal: str = "voz", ia: str = None):
    """"Faz uma ficha desse PDF" sobre um arquivo recebido: a ficha tática dos agentes, escrita pelo Claude com o arquivo como
    fonte. Avisa no fim pelo canal do pedido (e manda os arquivos se o pedido disse "me manda"), como as outras tarefas."""
    tarefa = orquestrador.enviar_estudo_de_arquivos(
        pedido, caminhos, ia=ia, abrir=canal == "voz",
        ao_concluir=lambda t: anunciar_tarefa_concluida(pedido, t, canal, pede_envio(pedido)),
    )
    return orquestrador.mensagem_inicio(tarefa) if tarefa else None


def mandar_pelo_whatsapp(caminhos, texto: str = "") -> None:
    """Devolve arquivos pela conversa "Mensagem para você" (usado por "me manda no zap")."""
    if CANAL_WHATSAPP is None or CANAL_WHATSAPP.status().get("status") != "conectado":
        raise RuntimeError("o WhatsApp não está conectado")
    CANAL_WHATSAPP.enviar(texto, caminhos)


# Fontes e arquivos recebidos (fontes.py): o que chega pelo WhatsApp ou é escaneado vira fonte, é arquivado, impresso ou lido.
FONTES = criar_agente_fontes(avisar=lambda texto, canal: responder_fontes(texto, canal), enviar_zap=mandar_pelo_whatsapp,
                             impressora=IMPRESSORA, pastas=PASTAS, llm=llm_clients)


def responder_tela(resposta: RespostaTela) -> None:
    """Fala a resposta curta e mostra a completa no painel. Não entra no histórico (que alimenta a biblioteca):
    o que estava na tela não deve ficar guardado."""
    fala = limpar_texto_para_fala(resposta.fala)
    falar_async(fala)
    avisar_painel("resposta", f"{fala}\n\n{resposta.painel}" if resposta.painel else fala)
    abrir_janela_continuacao(ESTADO_ATIVACAO, time.time(), fala)  # a conversa segue aberta depois de ler a tela


def responder_lei(pergunta: str, resposta) -> None:
    """Lê o artigo (ou fala uma frase curta) e, enquanto isso toca, a IA escreve a explicação."""
    if resposta.leitura:
        falar_e_registrar(pergunta, resposta.leitura, texto_painel=resposta.painel)
    if resposta.explicar:
        try:
            explicacao = resposta.explicar()
        except Exception as exc:
            logger.warning("A explicação da lei falhou: %s", exc)
            explicacao = frase("erro")
        falar_e_registrar(pergunta, explicacao)


def avisar_lembrete(texto: str):
    """Lembrete da agenda: fala em casa, aparece no painel e no app do celular e, se ligado, chega no WhatsApp."""
    falar_async(texto)
    avisar_painel("lembrete", texto)
    if CANAL_APP is not None:  # aparece quando o app abrir, se ele estiver fechado agora
        CANAL_APP.enviar(f"⏰ {texto}", tipo="lembrete")
    if AGENDA_LEMBRETES_WHATSAPP and CANAL_WHATSAPP is not None and CANAL_WHATSAPP.status().get("status") == "conectado":
        CANAL_WHATSAPP.enviar(f"⏰ {texto}")


def responder_rapido(texto: str):
    """Fala curta que não entra no histórico ("Oi! Fala!", "Beleza, deixa pra lá!")."""
    if canal_remoto() is not None:
        responder_no_whatsapp("", texto, registrar=False)
        return
    falar_async(texto)
    avisar_painel("resposta", texto)


def receber_fala(texto: str, inicio: float, fim: float, confianca: float | None = None, falante: Falante | None = None):
    FILA_FALAS.put((texto, inicio, fim, confianca, falante))


def pode_escutar() -> bool:
    """O painel pausa o microfone por este evento compartilhado."""
    return MICROFONE_EVENTO is None or MICROFONE_EVENTO.is_set()


def obter_comando_usuario(espera: float = 0.5):
    """Próxima frase ouvida pela escuta contínua (ou comando do WhatsApp ou do app); sem microfone, lê do teclado."""
    comando_remoto = proximo_comando_remoto()
    FALANTE["atual"] = None  # WhatsApp (só a conversa do dono), app (aparelho pareado) e teclado: é o dono
    if comando_remoto:
        return comando_remoto
    CANAL["atual"] = "voz"
    tools.CONFIRMAR_NO_TECLADO = True
    if microfone_disponivel():
        try:
            texto, inicio, fim, confianca, falante = FILA_FALAS.get(timeout=espera)
        except queue.Empty:
            return ""
        return filtrar_fala_por_ativacao(texto, inicio, fim, confianca, falante)

    try:
        retorno = input("Digite seu comando (ou 'sair'): ").strip()
        if retorno:
            print(f"Você: {retorno}")
        return retorno
    except EOFError:
        return ""


# ------------------------------------------------------------------ confirmação
@dataclass
class Confirmacao:
    """Pedido que espera um "sim" ou um "não" antes de ser feito."""
    comando: str = ""
    ate: float = 0.0

    def ativa(self, agora: float) -> bool:
        return bool(self.comando) and agora < self.ate

    def limpar(self):
        self.comando, self.ate = "", 0.0


CONFIRMACAO = Confirmacao()
SEGUNDOS_PARA_CONFIRMAR = 15.0  # contados do fim estimado da pergunta
INTERVALO_ENTRE_PERGUNTAS = 30.0  # conversa do ambiente não vira uma pergunta atrás da outra
_ULTIMA_PERGUNTA = {"em": 0.0}

# ------------------------------------------------------------------ modo de escuta
ESCUTA = {"modo": MODO_ESCUTA}  # "sempre", "conversa" ou "nome" (dá para trocar falando)
AMBIENTE = {"outra_voz_em": 0.0, "ultima_oferta": 0.0}


def janela_da_conversa() -> float:
    """Quanto tempo a conversa fica aberta (sem precisar do nome) depois da última fala dela."""
    if ESCUTA["modo"] == "sempre":
        return JANELA_CONVERSA_SEMPRE
    return JANELA_CONVERSA_SEGUNDOS if ESCUTA["modo"] == "conversa" else 0.0


def entender_sem_nome() -> bool:
    return ENTENDER_SEM_NOME and ESCUTA["modo"] != "nome"


def motivo_para_exigir_o_nome(falante, agora: float) -> str:
    """No modo "sempre": quando uma frase sem o nome não deve ser atendida mesmo parecendo um pedido. "" se pode seguir."""
    if BIOMETRIA.ativa and falante is not None:
        if not falante.e_dono:
            return ("não tive certeza de que era você; diga “Beatriz” para eu atender" if falante.tipo == "incerto"
                    else "não parece a sua voz; diga “Beatriz” para eu atender")
        if agora - AMBIENTE["outra_voz_em"] < AMBIENTE_SEGUNDOS:
            return "tem mais gente falando por perto; diga “Beatriz” para eu atender"
    atividade = leitura_tela.atividade_atual()
    if atividade is not None and atividade.fazendo == "numa reunião":
        return "você está numa reunião; diga “Beatriz” para eu atender"
    return ""


RE_SO_QUANDO_CHAMAR = re.compile(
    r"\b(?:so|apenas) (?:quando|se) eu (?:te |a )?(?:chamar|chamo)\b"
    r"|\b(?:so|apenas) (?:me )?(?:atend\w+|respond\w+|escut\w+|ouv\w+).{0,25}\b(?:chamar|chamo|chamada|nome)\b"
    r"|\b(?:para|pare|chega|deixa) de (?:ficar )?(?:me )?(?:escutar|ouvir) (?:o tempo todo|sempre|toda hora)\b"
    r"|\bnao precisa (?:ficar )?(?:me )?(?:escutando|ouvindo)\b|\bmodo (?:so )?nome\b|\bvolta (?:a|para) (?:esperar|so atender)"
)
RE_SEMPRE_ESCUTANDO = re.compile(
    r"\b(?:pode )?(?:ficar|continuar|fique|fica) (?:sempre )?(?:escutando|ouvindo|de olho)\b|\b(?:escuta|ouve) (?:sempre|o tempo todo)\b"
    r"|\bmodo (?:sempre|conversa)\b|\bnao precisa (?:mais )?(?:me )?(?:chamar|dizer beatriz|falar beatriz)\b"
    r"|\bpode (?:me )?(?:escutar|ouvir) (?:sempre|o tempo todo)\b"
)


def controlar_modo_de_escuta(comando: str) -> str | None:
    """"Só quando eu chamar" / "pode ficar escutando": troca o modo (vale até fechar a Beatriz; o padrão é MODO_ESCUTA)."""
    c = _normalizar_frase(comando)
    if RE_SO_QUANDO_CHAMAR.search(c):
        ESCUTA["modo"] = "nome"
        ESTADO_ATIVACAO.encerrar()
        return "Beleza! Daqui em diante só respondo quando você me chamar pelo nome."
    if RE_SEMPRE_ESCUTANDO.search(c):
        ESCUTA["modo"] = "sempre"
        abrir_janela_continuacao(ESTADO_ATIVACAO, time.time(), "")
        return "Combinado! Vou ficar ouvindo e conversando sem você precisar me chamar. Se quiser silêncio, é só dizer “só quando eu chamar”."
    return None


def oferecer_ajuda(texto: str) -> bool:
    """A pessoa falou sozinha de algo que ela poderia resolver ("preciso marcar o dentista"): oferece, no máximo uma vez a
    cada INTERVALO_ENTRE_OFERTAS, e o "sim" faz o pedido. A IA local escreve a oferta e o pedido correspondente."""
    agora = time.time()
    if agora - AMBIENTE["ultima_oferta"] < INTERVALO_ENTRE_OFERTAS or CONFIRMACAO.ativa(agora):
        return False
    try:
        resposta = llm_clients.chat(
            f'A pessoa disse em voz alta, sem falar com você: "{texto}"\n'
            "Se uma assistente de computador puder ajudar com isso agora (marcar na agenda, criar um lembrete, pesquisar, "
            "dizer a hora, o clima ou os compromissos, abrir uma pasta, ler a tela, resumir, imprimir), responda só com JSON: "
            '{"oferta": "Quer que eu ...?", "comando": "o pedido curto, no imperativo, como a pessoa diria à assistente"}. '
            "A oferta tem uma frase curta. Se não houver nada útil a fazer, responda {}.\n"
            'Exemplos: "preciso marcar o dentista" -> {"oferta": "Quer que eu marque o dentista na agenda?", "comando": "marca dentista"}; '
            '"esqueci que dia é a prova" -> {"oferta": "Quer que eu veja na agenda quando é a prova?", "comando": "quando é a prova"}; '
            '"que droga, deu esse erro de novo" -> {"oferta": "Quer que eu dê uma olhada no erro na tela?", "comando": "olha esse erro na tela"}; '
            '"nossa que calor" -> {}.',
            opcoes={"temperature": 0.2, "num_predict": 120},
        )
        achado = re.search(r"\{.*\}", resposta, re.DOTALL)
        dados = json.loads(achado.group(0)) if achado else {}
    except Exception as exc:
        logger.debug("Não foi possível pensar numa oferta de ajuda: %s", exc)
        return False
    oferta, comando = str(dados.get("oferta", "")).strip(), str(dados.get("comando", "")).strip()
    if not (oferta.lower().startswith("quer que eu") and 3 <= len(comando) <= 90 and len(oferta) <= 160):
        return False
    AMBIENTE["ultima_oferta"] = agora
    avisar_painel("ouvido", texto, "percebi que você podia precisar de algo; ofereci ajuda", pensando=False)
    perguntar_antes(comando, oferta)
    return True


# Janelas de erro: só o título da janela em uso (nada de foto) e só se ela ficar muito tempo sem ninguém dizer nada.
RE_ERRO_FORTE = re.compile(r"traceback|exception|has stopped|stopped working|not responding|nao esta respondendo|nao responde|"
                           r"parou de funcionar|fatal error|crash")
RE_ERRO_FRACO = re.compile(r"\b(?:erro|error|falha|failed)\b")


def olhar_a_tela_uma_vez(avisados: dict) -> bool:
    """Uma checagem: se a janela em uso é de erro (e ainda não foi avisada), oferece olhar. True se ofereceu."""
    agora = time.time()
    if (ESCUTA["modo"] != "sempre" or not TELA_OBSERVAR or not pode_escutar() or CONFIRMACAO.ativa(agora)
            or agora - AMBIENTE["ultima_oferta"] < INTERVALO_ENTRE_OFERTAS or falou_durante(agora - 2, agora)):
        return False
    janela = leitura_tela.janela_em_foco()
    atividade = leitura_tela.atividade_de(janela)
    if atividade is None:
        return False
    titulo = leitura_tela.sem_acento(janela.titulo)
    em_editor = janela.programa in leitura_tela.EDITORES or janela.programa == "explorer"  # "erros.py" não é um erro
    if not (RE_ERRO_FORTE.search(titulo) or (not em_editor and RE_ERRO_FRACO.search(titulo))):
        return False
    chave = (janela.programa, titulo)
    if agora - avisados.get(chave, 0.0) < 900:
        return False
    avisados[chave] = AMBIENTE["ultima_oferta"] = agora
    perguntar_antes("olha esse erro na tela", f"Apareceu um erro na janela do {atividade.programa}. Quer que eu dê uma olhada?")
    return True


def observar_a_tela():
    """Thread: de tempos em tempos, vê se apareceu uma janela de erro (só o título da janela, sem foto)."""
    avisados: dict = {}
    while True:
        time.sleep(8)
        try:
            olhar_a_tela_uma_vez(avisados)
        except Exception as exc:
            logger.debug("Observação da tela falhou: %s", exc)


RESPOSTAS_SIM = {"sim", "isso", "isso mesmo", "e isso", "pode", "pode sim", "pode ser", "confirma", "confirmo",
                 "claro", "ok", "okay", "beleza", "manda", "vai", "faz", "faca", "exato", "correto", "certo",
                 "quero", "aham", "uhum", "positivo", "por favor", "com certeza", "falei"}
RESPOSTAS_NAO = {"nao", "nada", "deixa", "deixa pra la", "cancela", "esquece", "errado", "negativo",
                 "nao precisa", "nao e isso", "nao era isso", "nao quero"}
# Estas valem como resposta em qualquer frase; as outras ("faz", "vai", "deixa"...) só em frase
# curta, para "faz tempo que..." ou "deixa eu ver isso aí" não virarem sim ou não.
RESPOSTAS_FORTES = {"sim", "isso", "isso mesmo", "confirma", "confirmo", "exato", "correto", "nao", "cancela", "negativo"}
# Ditos logo depois de uma resposta: ela pede desculpas e pergunta o que era.
CORRECOES = {"nao era isso", "nao e isso", "nao foi isso", "nao era isso que eu queria", "entendeu errado",
             "voce entendeu errado", "entendeu tudo errado", "errado", "errou"}


def perguntar_antes(comando: str, pergunta: str):
    """Faz a pergunta e guarda o pedido até a pessoa confirmar ("sim") ou negar ("não")."""
    agora = time.time()
    CONFIRMACAO.comando = comando
    CONFIRMACAO.ate = agora + len(pergunta) / CARACTERES_FALA_POR_SEGUNDO + SEGUNDOS_PARA_CONFIRMAR
    _ULTIMA_PERGUNTA["em"] = agora
    responder_rapido(pergunta)


def _sim_ou_nao(texto: str) -> tuple[str | None, str]:
    """("sim" ou "nao", o resto da frase), ou (None, texto): "não, quero o clima" -> ("nao", "quero o clima")."""
    tokens = (texto or "").split()
    posicao = _localizar_nome(tokens)
    if posicao and posicao[0] == 0:  # "Beatriz, sim"
        tokens = tokens[posicao[1]:]
    normalizados = [_normalizar_token(token) for token in tokens]
    for tamanho in (4, 3, 2, 1):
        inicio = " ".join(normalizados[:tamanho])
        for decisao, respostas in (("sim", RESPOSTAS_SIM), ("nao", RESPOSTAS_NAO)):
            if inicio in respostas and (inicio in RESPOSTAS_FORTES or len(tokens) <= 3):
                return decisao, _limpar_bordas(" ".join(tokens[tamanho:]))
    return None, texto


def avaliar_fala_sem_nome(texto: str, em_conversa: bool = False, procurar_necessidade: bool = False) -> str:
    """"pedido", "talvez", "conversa" (ou "necessidade", se pedido): se a frase dita sem o nome parecia ser para a Beatriz.

    Usa o Ollama local, porque a frase pode ser conversa da casa; se ele falhar, "conversa". `em_conversa`: ela acabou de
    falar com a pessoa, então reações e comentários sobre o assunto contam. `procurar_necessidade`: também reconhece quando
    a pessoa fala sozinha de algo que ela poderia resolver (a Beatriz então oferece ajuda).
    """
    if len((texto or "").split()) < 2:
        return "conversa"
    recentes = [f"{quem}: {fala[:160]}" for _, quem, fala in list(REGISTRO_CONVERSA)[-4:] if quem in ("Usuário", "Beatriz")]
    ultimas = "Últimas falas:\n" + "\n".join(recentes) + "\n" if recentes else ""
    atividade = leitura_tela.atividade_atual() if TELA_CONTEXTO_AUTOMATICO else None
    onde = f"A pessoa está usando: {atividade.texto()}.\n" if atividade is not None else ""
    andamento = (
        "A assistente e a pessoa estão no meio de uma conversa: comentar ou reagir ao que a assistente "
        "disse, perguntar mais sobre o assunto ou puxar papo com ela também é pedido. Só é conversa o que "
        "claramente fala com outra pessoa (chama alguém pelo nome, mãe, filho...).\n"
        "Exemplos no meio da conversa: \"sério? e amanhã também?\" -> pedido; \"nossa, que legal\" -> pedido; "
        "\"e você, gosta?\" -> pedido; \"mãe, cadê minha mochila?\" -> conversa.\n" if em_conversa else ""
    )
    necessidade = (
        "necessidade: a pessoa falou sozinha de algo que a assistente poderia resolver (esqueceu algo, precisa marcar ou "
        "lembrar de algo, está em dúvida sobre um fato, tem um erro na tela), sem pedir nada.\n"
        "Exemplos: \"preciso marcar o dentista\" -> necessidade; \"esqueci que dia é a prova\" -> necessidade; "
        "\"tô sem ideia do que jantar\" -> necessidade; \"que droga, esse erro de novo\" -> necessidade.\n"
    ) if procurar_necessidade else ""
    categorias = ("pedido", "necessidade", "talvez", "conversa") if procurar_necessidade else ("pedido", "talvez", "conversa")
    try:
        resposta = llm_clients.chat(
            f'Frase captada pelo microfone, dita sem chamar a assistente pelo nome: "{texto}"\n'
            f"{ultimas}{onde}{andamento}"
            "Classifique a frase com uma palavra:\n"
            "pedido: pede algo que uma assistente de computador faz (horas, clima, música, pesquisa, "
            "lembrete, abrir programas ou pastas, filmes, notícias, ler a tela) ou responde à última fala da assistente.\n"
            f"{necessidade}"
            "talvez: pergunta, sugestão ou ordem que também poderia ser para outra pessoa da casa.\n"
            "conversa: pessoas falando entre si, chamando alguém, agradecendo, se despedindo, TV ou música.\n"
            "Exemplos: \"qual a previsão do tempo\" -> pedido; \"toca uma música calma\" -> pedido; "
            "\"me lembra de ligar pro João amanhã\" -> pedido; \"abre a pasta de estudos\" -> pedido; "
            "\"cadê o controle da TV\" -> talvez; \"bora jantar fora hoje\" -> talvez; "
            "\"filho, vem almoçar\" -> conversa; \"valeu, até amanhã\" -> conversa.\n"
            "Responda só com a palavra.",
            opcoes={"temperature": 0, "num_predict": 4},
        ).lower()
    except Exception as exc:
        logger.debug("Não foi possível avaliar a frase sem o nome: %s", exc)
        return "conversa"
    return next((categoria for categoria in categorias if categoria in resposta), "conversa")


def filtrar_fala_por_ativacao(texto: str, inicio: float | None = None, fim: float | None = None,
                              confianca: float | None = None, falante: Falante | None = None) -> str:
    """Decide o que fazer com a frase ouvida e devolve o pedido a executar ("" para nenhum).

    - Captada enquanto ela falava: o eco da própria voz é descartado; com o nome, ou como
      resposta a uma chamada ou a uma confirmação, a frase interrompe a fala.
    - Com o nome, ou na janela de conversa, é pedido.
    - Sem o nome, a IA local avalia: pedido claro é atendido, duvidoso vira pergunta
      ("Você falou comigo?").
    - Com a transcrição incerta, ela confirma antes de agir; "sim" faz, "não" cancela.
    """
    agora = time.time()
    falante = BIOMETRIA.resolver(falante, agora)  # frase curta ou duvidosa vale quem falava há pouco
    FALANTE["atual"] = falante
    if BIOMETRIA.ativa and falante is not None and falante.tipo in ("convidado", "conhecido") and not falante.herdado:
        AMBIENTE["outra_voz_em"] = agora  # tem outra pessoa falando por perto
    if inicio is not None and falou_durante(inicio, fim if fim is not None else agora):
        if parece_eco(texto):
            logger.debug("Eco da própria voz ignorado: %s", texto)  # não é fala do usuário: nem vai ao painel
            return ""
        chamou = _localizar_nome((texto or "").split()) is not None
        respondeu = CONFIRMACAO.ativa(agora) and _sim_ou_nao(texto)[0] is not None
        # No modo "sempre", a sua voz (a dela, tocando, é de "convidado" para a biometria) interrompe sem o nome.
        interrompe = (ESCUTA["modo"] == "sempre" and BIOMETRIA.ativa and falante is not None and falante.e_dono
                      and not falante.herdado)
        if not (chamou or respondeu or interrompe or ESTADO_ATIVACAO.chamada_ate > agora):
            logger.debug("Fala ignorada (captada enquanto a Beatriz falava): %s", texto)
            avisar_painel("ignorado", texto, "eu estava falando; diga “Beatriz” para me interromper")
            return ""
        parar_fala()

    if CONFIRMACAO.ativa(agora) and BIOMETRIA.pode_tudo(falante):  # o "sim" de um convidado não confirma nada
        decisao, resto = _sim_ou_nao(texto)
        if decisao:
            pedido = CONFIRMACAO.comando
            CONFIRMACAO.limpar()
            ESTADO_ATIVACAO.fechar()
            if decisao == "sim":
                avisar_painel("ouvido", texto, pensando=True)
                return pedido
            # "não, eu quero saber o clima" troca o pedido; "não sei, liga pra ele" (conversa) só cancela.
            trocou = len(resto.split()) >= 2 and avaliar_fala_sem_nome(resto) == "pedido"
            avisar_painel("ouvido", texto, pensando=trocou)
            if trocou:
                return resto
            ESTADO_ATIVACAO.encerrar()
            responder_rapido(frase("deixa_pra_la"))
            return ""

    fim_fala = fim_da_ultima_fala()
    # As janelas valem para quando a frase COMEÇOU: uma frase longa termina e é transcrita depois que a
    # janela já fechou, e nem por isso deixou de ser dita dentro dela.
    referencia = inicio if inicio and 0 <= agora - inicio <= 60 else agora
    comando = extrair_comando_ativado(texto, referencia, ESTADO_ATIVACAO, fim_fala=fim_fala)
    sem_nome = comando is None
    em_conversa = sem_nome and ESTADO_ATIVACAO.em_conversa(referencia, fim_fala)
    if sem_nome:
        motivo = motivo_para_exigir_o_nome(falante, agora) if ESCUTA["modo"] == "sempre" else ""
        if motivo:
            avisar_painel("ignorado", texto, motivo)
            return ""
        if em_conversa and (tipo_de_encerramento(texto) or LEIS.e_seguimento(texto) or TELA.e_seguimento(texto)
                            or PASTAS.esperando_resposta() or CONTROLE.esperando_resposta()):
            avaliacao = "pedido"  # "valeu!", "tchau", "não entendi", "explica melhor", "a segunda": no meio da conversa, é com ela
        elif FONTES.esperando_resposta() and FONTES.reconhece(texto):
            avaliacao = "pedido"  # acabou de chegar um arquivo (ou escaneou uma folha): "vira fonte", "arquiva", "mais uma"
        elif not entender_sem_nome():
            avaliacao = "conversa"
        else:
            procurar = ESCUTA["modo"] == "sempre" and OFERTAS_DE_AJUDA and not em_conversa
            avaliacao = (avaliar_fala_sem_nome(texto, em_conversa=em_conversa, procurar_necessidade=True) if procurar
                         else avaliar_fala_sem_nome(texto, em_conversa=em_conversa))
            if em_conversa and avaliacao == "talvez":
                avaliacao = "pedido"  # no meio do papo, o duvidoso é com ela: nada de "Foi comigo?"
        if avaliacao == "necessidade":
            oferecer_ajuda(texto)  # fala sozinho de algo que ela resolve: oferece e espera o "sim"
            return ""
        if avaliacao == "talvez" and agora - _ULTIMA_PERGUNTA["em"] > INTERVALO_ENTRE_PERGUNTAS:
            avisar_painel("ouvido", texto, "não sei se foi comigo; perguntei", pensando=False)
            perguntar_antes(texto, frase("foi_comigo", texto=texto))
            return ""
        if avaliacao != "pedido":
            avisar_painel("ignorado", texto, "diga “Beatriz” antes do pedido" if entender_sem_nome() else
                          "estou no modo “só quando você me chamar”; diga “Beatriz”")
            return ""
        comando = texto

    normalizado = _normalizar_frase(comando)
    if normalizado in COMANDOS_PARAR_FALA:
        parar_fala()  # "Beatriz, para": só interrompe, sem responder
        avisar_painel("ouvido", texto, "parei de falar")
        return ""
    if normalizado in CORRECOES:
        parar_fala()
        avisar_painel("ouvido", texto, pensando=False)
        pergunta = frase("entendi_errado")
        responder_rapido(pergunta)
        abrir_janela_continuacao(ESTADO_ATIVACAO, time.time(), pergunta)
        return ""
    if comando == "":
        avisar_painel("ouvido", texto, pensando=False)
        responder_rapido(frase("so_nome"))
        return ""
    if confianca is not None and confianca < CONFIANCA_MINIMA_VOZ:
        avisar_painel("ouvido", texto, "não tenho certeza do que ouvi; perguntei", pensando=False)
        perguntar_antes(comando, frase("confirmar_ouvido", comando=comando))
        return ""
    motivo = ("no meio da conversa, sem precisar do nome" if em_conversa
              else "sem o nome, mas entendi como pedido" if sem_nome else "")
    avisar_painel("ouvido", texto, motivo, pensando=True)
    return comando


# ------------------------------------------------------------------ resumo do que ouviu
RE_RESUMO_CONVERSA = re.compile(
    r"\b(resum\w*|recapitul\w*)\b.*\b(conversa\w*|ouviu|ouvido|falad[oa]s?|falamos|conversamos|disse|dissemos|"
    r"reuniao|aula|ultim[oa]s? \d+ minutos?|ultima hora)\b"
    r"|\bo que (voce ouviu|(foi|a gente|nos|eu) (falad[oa]|falou|falamos|conversamos|disse))\b"
)


def pede_resumo_da_conversa(comando: str) -> bool:
    return bool(RE_RESUMO_CONVERSA.search(_normalizar_frase(comando)))


def minutos_pedidos(comando: str, padrao: int = 30) -> int:
    texto = _normalizar_frase(comando)
    if re.search(r"\b(ultima|uma) hora\b", texto):
        return 60
    numero = re.search(r"\b(\d+) minutos?\b", texto)
    return int(numero.group(1)) if numero else padrao


def resumir_o_que_ouvi(minutos: int = 30) -> str:
    """Resumo falado do que o microfone captou e do que ela respondeu, só com o Ollama local."""
    desde = time.time() - minutos * 60
    falas = [(quando, quem, texto) for quando, quem, texto in list(REGISTRO_CONVERSA) if quando >= desde]
    if len(falas) < 3:
        return f"Ouvi muito pouco nos últimos {minutos} minutos, não dá para fazer um resumo."
    transcricao = "\n".join(f"[{time.strftime('%H:%M', time.localtime(q))}] {quem}: {t}" for q, quem, t in falas)
    prompt = (
        f"Abaixo está o que o microfone da Beatriz captou nos últimos {minutos} minutos: falas do usuário, "
        "conversas do ambiente que ela ouviu sem responder e as respostas dela. A transcrição automática "
        "tem erros. Faça um resumo falado, em português, em até cinco frases: os assuntos, os pedidos, o que "
        "foi decidido e o que ficou para fazer. Comece direto pelo conteúdo, sem frase de abertura. Não invente "
        "nada que não esteja escrito. Sem listas nem markdown."
        f"\n\n{transcricao[-12000:]}\n\nResumo:"
    )
    try:
        # Só no computador: conversa da casa não vai para a nuvem.
        return llm_clients.chat_beatriz(prompt, contexto="Resumo de conversa captada pelo microfone.",
                                        usar_historico=False)
    except Exception as exc:
        logger.warning("Não foi possível resumir a conversa: %s", exc)
        return "Não consegui fazer o resumo agora: o Ollama não respondeu."


# Só estas desligam o sistema. "Tchau", "até logo" e "era só isso" encerram a conversa (responder_encerramento).
PALAVRAS_DESLIGAR = ["desligar", "desliga", "pode desligar", "desliga o sistema", "encerrar", "encerrar o sistema",
                     "sair", "fechar", "finalizar", "terminar"]


def comando_solicita_encerramento(comando: str, palavras_desligar: list[str]) -> bool:
    """Reconhece desligamento sem confundir trechos de palavras maiores."""
    texto = re.sub(r"[^\wÀ-ÿ\s]", " ", (comando or "").lower(), flags=re.UNICODE)
    texto = re.sub(r"\s+", " ", texto).strip()
    comandos_exatos = {re.sub(r"\s+", " ", palavra.lower()).strip() for palavra in palavras_desligar}
    return texto in comandos_exatos


# ------------------------------------------------------------------ palavra de ativação
INTERJEICOES_CHAMADA = {"ei", "ô", "o", "ok", "okay", "hey", "alô", "e aí", "ah"}
CARACTERES_FALA_POR_SEGUNDO = 14.0  # estimativa da voz a +15%, para a janela começar depois da fala
# Pedidos para ela só parar de falar, sem responder nada.
COMANDOS_PARAR_FALA = {"para", "pare", "chega", "silencio", "espera", "pera", "psiu", "cala a boca", "fica quieta"}


@dataclass
class EstadoAtivacao:
    """Até quando a próxima frase falada é aceita sem o nome (relógio de time.time())."""
    janela_ate: float = 0.0
    seguimento: bool = False  # depois de uma resposta, a janela também conta do fim real da fala
    chamada_ate: float = 0.0  # chamaram só o nome: a próxima frase pode interromper o "Oi! Fala!"
    conversa_ate: float = 0.0  # conversa em andamento (JANELA_CONVERSA_SEGUNDOS depois da última fala)
    conversando: bool = False

    def janela_aberta(self, agora: float, fim_fala: float = 0.0) -> bool:
        if agora < self.janela_ate:
            return True
        return self.seguimento and fim_fala > 0 and agora < fim_fala + JANELA_CONTINUACAO_SEGUNDOS

    def em_conversa(self, agora: float, fim_fala: float = 0.0) -> bool:
        if not self.conversando:
            return False
        return agora < self.conversa_ate or (fim_fala > 0 and agora < fim_fala + janela_da_conversa())

    def fechar(self):
        """Fecha a janela de uma frase; a conversa em andamento continua."""
        self.janela_ate = 0.0
        self.seguimento = False
        self.chamada_ate = 0.0

    def encerrar(self):
        """Fim da conversa ("tchau", "deixa pra lá"): daqui em diante, só com o nome."""
        self.fechar()
        self.conversa_ate = 0.0
        self.conversando = False


ESTADO_ATIVACAO = EstadoAtivacao()


def _normalizar_frase(texto: str) -> str:
    return " ".join(_normalizar_token(token) for token in (texto or "").split() if _normalizar_token(token))


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
                            exigir: bool | None = None, fim_fala: float = 0.0) -> str | None:
    """Filtra a fala captada pelo microfone pela palavra de ativação.

    Retorna None para ignorar a frase (conversa de fundo), "" quando ela só chamou
    o nome (abre uma janela curta para o comando) ou o comando sem o nome.
    `fim_fala` é quando a última fala da Beatriz terminou de tocar.
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
        if estado.janela_aberta(agora, fim_fala):
            estado.fechar()  # a janela vale para uma frase; a resposta abre outra
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
        estado.fechar()
        estado.janela_ate = estado.chamada_ate = agora + JANELA_ATIVACAO_SEGUNDOS
        return ""
    estado.fechar()
    return comando


def abrir_janela_continuacao(estado: EstadoAtivacao, agora: float, resposta: str = "") -> None:
    """Depois de responder, aceita perguntas de seguimento sem o nome por um tempo curto.

    A fala é assíncrona: a janela vale do fim estimado da resposta falada e também do fim
    real (o `fim_fala` que `extrair_comando_ativado` recebe), o que vier depois.
    """
    duracao_fala = len(resposta or "") / CARACTERES_FALA_POR_SEGUNDO
    estado.janela_ate = max(estado.janela_ate, agora + duracao_fala + JANELA_CONTINUACAO_SEGUNDOS)
    estado.seguimento = True
    if janela_da_conversa() > 0:
        estado.conversa_ate = max(estado.conversa_ate, agora + duracao_fala + janela_da_conversa())
        estado.conversando = True


def estado_escuta(agora: float | None = None) -> str:
    """Texto curto para o painel: se a voz precisa do nome ou já está escutando."""
    if not EXIGIR_PALAVRA_ATIVACAO:
        return "escutando"
    agora = time.time() if agora is None else agora
    if ESCUTA["modo"] == "sempre":
        return "escutando sempre"
    return "escutando" if ESTADO_ATIVACAO.janela_aberta(agora, fim_da_ultima_fala()) else "aguardando o nome"


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


def saudacao_inicial(agenda_de_hoje: str = "") -> str:
    """Saudação de abertura, animada e diferente a cada vez: período do dia, hora, data e a agenda."""
    agora = time.localtime()
    periodo = periodo_do_dia(agora.tm_hour)
    data = f"{DIAS_SEMANA[agora.tm_wday]}, {agora.tm_mday} de {MESES[agora.tm_mon - 1]}"
    partes = [f"{periodo}! {frase('abertura')}", f"São {agora.tm_hour:02d}:{agora.tm_min:02d} desta {data}."]
    if agenda_de_hoje:
        partes.append(agenda_de_hoje)
    partes.append(frase("convite"))
    return " ".join(partes)


def responder_saudacao(comando: str) -> str | None:
    """Responde a saudações simples do usuário sem passar pelo LLM."""
    texto = re.sub(r"[^\wÀ-ÿ\s]", " ", (comando or "").lower(), flags=re.UNICODE)
    texto = re.sub(r"\s+", " ", texto).strip()
    if texto not in SAUDACOES_USUARIO:
        return None
    return frase("saudacao", periodo=periodo_do_dia(time.localtime().tm_hour))


def responder_encerramento(comando: str) -> str | None:
    """'Valeu!' ganha um 'de nada' animado; 'tchau'/'era só isso' encerra a conversa (sem desligar)."""
    tipo = tipo_de_encerramento(comando)
    if tipo == "tchau":
        return frase("tchau")
    if tipo == "obrigado":
        return frase("obrigado")
    return None


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


def anunciar_tarefa_concluida(pedido: str, tarefa, canal: str = "voz", mandar_arquivos: bool = False):
    """Chamado pelo orquestrador (em segundo plano) quando os agentes terminam uma tarefa.

    `canal` é de onde o pedido veio. Pelo WhatsApp ou pelo app a resposta é uma mensagem com o principal do
    resultado. No WhatsApp os arquivos só vão se o pedido disse para mandar (`mandar_arquivos`) ou se pedirem
    depois; no app eles vão sempre, como botões para baixar (não pesam na conversa).
    """
    if canal_remoto(canal) is not None:
        texto = tarefa.mensagem_final
        detalhes = texto_da_entrega(tarefa)
        if detalhes:
            texto += f"\n\n{detalhes}"
        mandar = mandar_arquivos or canal == "app"
        if tarefa.arquivos and not mandar:
            texto += "\n\nSe quiser o arquivo, é só pedir: *manda o arquivo*."
        responder_no_whatsapp(pedido, texto, arquivos=tarefa.arquivos if mandar else (), canal=canal)
    else:
        falar_e_registrar(pedido, tarefa.mensagem_final, canal="voz")
    # "Faz um PDF sobre X e imprime": com o arquivo pronto, ela pergunta se imprime (a resposta vem
    # pelo mesmo canal e cai em IMPRESSORA.responder).
    if tarefa.arquivos and pede_impressao(pedido):
        oferta = IMPRESSORA.oferecer_arquivos(tarefa.arquivos)
        if oferta:
            falar_e_registrar(pedido, oferta, canal=canal if canal_remoto(canal) is not None else "voz")
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
            if dados_compartilhados.pop("reindexar_biblioteca", None):
                # A janela da biblioteca (outro processo) mexeu nas fontes: a leitura do índice é feita só aqui.
                memory.agendar_indexacao_em_background()
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


def acompanhar_whatsapp(canal: CanalWhatsApp):
    """Avisa no painel (e pela voz, quando precisa de você) as mudanças de estado do WhatsApp."""
    anterior = None
    while canal.processo is not None and canal.processo.is_alive():
        situacao = canal.status().get("status")
        if situacao and situacao != anterior:
            if situacao == "aguardando QR":
                responder_rapido_por_voz("Para receber comandos pelo WhatsApp, leia o QR code na janela que abri: "
                                         "no celular, WhatsApp, Aparelhos conectados, Conectar um aparelho.")
            elif situacao == "conectado":
                avisar_painel("resposta", "WhatsApp conectado: mande comandos na conversa Mensagem para você.")
            logger.info("WhatsApp: %s", situacao)
            anterior = situacao
        time.sleep(2)


def responder_rapido_por_voz(texto: str):
    falar_async(texto)
    avisar_painel("resposta", texto)


def indice_da_etapa(plano: list, etapa: str) -> int:
    """Em que etapa do plano a tarefa está, pelo texto que ela publica ("revisando o resultado"). O andamento de dentro
    de um especialista ("escrevendo as fichas (3 de 8 prontas)") é da etapa que produz o material."""
    from agentes.orquestrador import ROTULOS_ETAPA

    for indice, nome in enumerate(plano):
        if ROTULOS_ETAPA.get(nome) == etapa or nome == etapa:
            return indice
    if etapa == ROTULOS_ETAPA["corrigindo"] and "critico" in plano:
        return plano.index("critico")
    return next((i for i, nome in enumerate(plano) if nome not in ("biblioteca", "pesquisa")), 0)


def dados_para_o_app(configuracao) -> dict:
    """A tela inicial do app do celular: o dia na agenda, as missões dos agentes e o estado do PC."""
    from llm_nuvem import claude, gemini

    tarefas = [{**{chave: t.get(chave) for chave in ("id", "pedido", "status", "etapa", "plano", "arquivos", "criada_em")},
                "etapa_indice": indice_da_etapa(t.get("plano") or [], t.get("etapa") or "")}
               for t in PainelCompartilhado.listar(configuracao)[:8]]
    sistemas = {
        "ia_local": "online" if llm_clients.is_ollama_available() else "fora do ar",
        "nuvem": [{"nome": p.nome, "texto": p.status()[0], "estado": p.status()[1]} for p in (claude, gemini)],
        "whatsapp": (CANAL_WHATSAPP.status().get("status") or "abrindo") if CANAL_WHATSAPP is not None else "desligado",
        "biblioteca": status_indexacao().get("status", ""),
        "voz": descricao_da_voz(),
        "escuta": ESCUTA["modo"],
    }
    sistemas.update(app_celular.recursos_do_pc())
    biblioteca = BIBLIOTECA.listar()
    sistemas["fontes"] = {"lidas": biblioteca["lidas"], "total": biblioteca["total"]}
    return {"hoje": AGENDA.dados_dia(), "proximo": AGENDA.proximo(), "tarefas": tarefas, "sistemas": sistemas,
            "clima": clima_do_app(), "nome": BIOMETRIA.nome_do_dono()}


CLIMA_DO_APP = {"valor": None, "em": 0.0, "buscando": False}


def clima_do_app() -> dict | None:
    """O clima da tela inicial do app. Devolve o que já tem na hora e, se tiver mais de 10 min, busca de novo em segundo
    plano: sem internet, a consulta levaria até 20 s e travaria a tela."""
    if time.time() - CLIMA_DO_APP["em"] > 600 and not CLIMA_DO_APP["buscando"]:
        CLIMA_DO_APP["buscando"] = True

        def buscar():
            try:
                valor = tools.clima_em_numeros()
                if valor:
                    CLIMA_DO_APP["valor"] = valor
                CLIMA_DO_APP["em"] = time.time()  # sem internet, tenta de novo daqui a 10 min
            finally:
                CLIMA_DO_APP["buscando"] = False

        threading.Thread(target=buscar, name="clima-do-app", daemon=True).start()
    return CLIMA_DO_APP["valor"]


def iniciar_app_celular(configuracao):
    """Sobe o servidor do app do celular (só no próprio PC; o iPhone chega pelo Tailscale) e liga o canal dele."""
    global CANAL_APP
    try:
        canal = app_celular.CanalApp()
        servidor = app_celular.criar_servidor(
            canal, APARELHOS, dados=lambda: dados_para_o_app(configuracao), transcrever=transcrever_amostras,
            gerar_fala=gerar_arquivo_de_fala, limpar=limpar_texto_para_fala, agenda_semana=AGENDA.dados_semana,
            biblioteca=BIBLIOTECA,
        )
        app_celular.iniciar_servidor(servidor)
        CANAL_APP = canal
    except Exception as exc:
        logger.warning("Não foi possível iniciar o app do celular: %s", exc)


def main(evento_carregamento=None):
    global MICROFONE_EVENTO, PAINEL_VOZ, CANAL_WHATSAPP, NIVEIS_VOZ
    memory.servir_biblioteca()  # já sobe no __main__; aqui só garante, antes do Manager e do painel
    MICROFONE_EVENTO = multiprocessing.Event()
    MICROFONE_EVENTO.set()
    PAINEL_VOZ = multiprocessing.Queue()
    # A esfera do painel vibra com a voz dela e a montanha com o microfone: o voice.py mede, o painel lê.
    niveis_compartilhados = niveis_voz.criar_compartilhado()
    NIVEIS_VOZ = niveis_voz.NiveisDeVoz(niveis_compartilhados)
    ao_medir_nivel(NIVEIS_VOZ.publicar)
    # Adianta o que deixaria a primeira conversa lenta: carregar o modelo (~3 s) e gerar
    # o áudio da resposta ao nome, que assim toca na hora.
    threading.Thread(target=llm_clients.aquecer, daemon=True).start()
    preparar_frases(todas_as_frases("so_nome"))
    iniciar_escuta_continua(receber_fala, pode_escutar=pode_escutar)
    threading.Thread(target=PASTAS.aquecer, name="indice-de-pastas", daemon=True).start()
    threading.Thread(target=observar_a_tela, name="observar-a-tela", daemon=True).start()
    inicializar_biblioteca_automatica()
    gerenciador_audio = multiprocessing.Manager()
    configuracao_audio = gerenciador_audio.dict({"microfone": None, "saida": ""})
    threading.Thread(target=publicar_status_painel, args=(configuracao_audio,), daemon=True).start()
    # As ferramentas (API, mapa de ferramentas) usam o mesmo agente; os lembretes só rodam neste processo.
    tools._AGENTE_AGENDA = AGENDA
    iniciar_lembretes(AGENDA, avisar_lembrete)
    # O andamento das tarefas dos agentes vai para o mesmo dicionário que o painel lê.
    orquestrador = criar_orquestrador(publicar=PainelCompartilhado(configuracao_audio))
    # "Faz uma ficha desse PDF" (arquivo recebido) vira tarefa deste orquestrador.
    FONTES.estudar = lambda caminhos, pedido, canal, ia=None: estudar_arquivos(orquestrador, caminhos, pedido, canal, ia)
    if WHATSAPP_COMANDOS:
        # O estado do WhatsApp (QR, conectado...) também vai para o dicionário do painel.
        CANAL_WHATSAPP = CanalWhatsApp(configuracao_audio, numero=WHATSAPP_MEU_NUMERO)
        try:
            CANAL_WHATSAPP.iniciar()
            threading.Thread(target=acompanhar_whatsapp, args=(CANAL_WHATSAPP,), daemon=True).start()
        except Exception as exc:
            logger.warning("Não foi possível iniciar o canal do WhatsApp: %s", exc)
            CANAL_WHATSAPP = None
    if APP_CELULAR:
        iniciar_app_celular(configuracao_audio)

    try:
        # Leve: o painel não reimporta o main.py inteiro (127 MB e 2,6 s, contra 184 MB e 4,9 s).
        processo_interface = iniciar_leve(
            escolher_painel(), args=(evento_carregamento, MICROFONE_EVENTO, configuracao_audio, PAINEL_VOZ,
                                     niveis_compartilhados), daemon=True)
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

    # A fala é em paralelo, então a saudação não atrasa o primeiro comando.
    cumprimentar()
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

    palavras_desligar = PALAVRAS_DESLIGAR
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

        comando = obter_comando_usuario()
        if not comando:
            continue

        # Quem está falando: os comandos de voz ("cadastra minha voz", "quem está falando?") vêm primeiro; quem
        # não é o dono (voz de visita) fica na conversa simples e nunca chega à agenda, aos arquivos e às contas.
        falante = FALANTE["atual"]
        resposta_voz = AGENTE_VOZ.responder(comando, falante, pelo_microfone=CANAL["atual"] == "voz")
        if resposta_voz:
            responder_rapido(resposta_voz)
            continue
        if not BIOMETRIA.pode_tudo(falante):
            responder_ao_convidado(comando, falante)
            continue

        # "Só quando eu chamar" / "pode ficar escutando": troca o modo de escuta.
        resposta_modo = controlar_modo_de_escuta(comando)
        if resposta_modo:
            falar_e_registrar(comando, resposta_modo)
            continue

        # "Usa a voz da Siri" / "volta pra voz da Thalita": troca a voz dela (vale também no painel).
        resposta_troca_voz = responder_troca_de_voz(comando)
        if resposta_troca_voz:
            responder_rapido(resposta_troca_voz)
            continue

        # "Conecta meu celular" (código de pareamento do app), "desconecta o celular", "o celular está conectado?".
        resposta_app = app_celular.responder_pedido_do_app(comando, APARELHOS, CANAL_APP)
        if resposta_app:
            responder_rapido(resposta_app)
            continue

        # "Beatriz, resume o que você ouviu (nos últimos 10 minutos)": antes dos agentes e da aula de idiomas.
        if pede_resumo_da_conversa(comando) and CANAL["atual"] != "voz":
            # São conversas da casa: ficam no computador, não vão para o WhatsApp nem para o celular.
            responder_rapido("Por privacidade, o resumo do que ouvi em casa só sai pela voz ou no painel.")
            continue
        if pede_resumo_da_conversa(comando):
            inicio_resumo = time.time()
            falar_e_registrar(comando, resumir_o_que_ouvi(minutos_pedidos(comando)))
            analisador.registrar_interacao(comando, ["resumo_da_conversa"], time.time() - inicio_resumo, True)
            continue

        idiomas_executado, resposta_idiomas = processar_comando_idiomas(comando)
        if idiomas_executado:
            falar_e_registrar(comando, resposta_idiomas)
            analisador.registrar_interacao(comando, ["professora_idiomas"], 0.0, True)
            continue

        if comando_solicita_encerramento(comando, palavras_desligar):
            if CANAL["atual"] != "voz":
                # Desligada pelo WhatsApp ou pelo app, ela não teria como ser religada à distância.
                responder_rapido(f"Pelo {ROTULO_DO_CANAL[CANAL['atual']].replace('Celular', 'app')} eu não desligo, para não "
                                 "ficar fora do ar sem ninguém para me religar. Para me desligar, fale comigo ou feche o painel.")
                continue
            falar(frase("encerrando"))
            break

        resposta_saudacao = responder_saudacao(comando)
        if resposta_saudacao:
            falar_e_registrar(comando, resposta_saudacao)
            analisador.registrar_interacao(comando, [], 0.0, True)
            continue

        # "Valeu!" ganha um "de nada"; "tchau" ou "era só isso" encerra a conversa, sem desligar o sistema.
        resposta_encerramento = responder_encerramento(comando)
        if resposta_encerramento:
            falar_e_registrar(comando, resposta_encerramento)
            if tipo_de_encerramento(comando) == "tchau":
                ESTADO_ATIVACAO.encerrar()  # daqui em diante, só chamando "Beatriz"
            analisador.registrar_interacao(comando, [], 0.0, True)
            continue

        comando_normalizado = agente_aprendizagem.normalizar(comando)

        # Fontes e arquivos recebidos antes da impressora, da leitura de tela e dos agentes: "vira fonte", "arquiva", "imprime isso",
        # "escaneia" e "resume esse PDF" logo depois de um arquivo chegar pelo WhatsApp (ou de escanear uma folha) são desse arquivo.
        resposta_fontes = FONTES.responder(comando, canal=CANAL["atual"])
        if resposta_fontes:
            responder_fontes(resposta_fontes, CANAL["atual"])
            analisador.registrar_interacao(comando, ["fontes"], 0.0, True)
            continue

        # Impressora antes da agenda e dos agentes: "imprime minha agenda" é impressão, e o "sim" que
        # confirma uma impressão é dela. "Faz um PDF sobre X e imprime" passa (os agentes criam antes).
        resposta_impressora = IMPRESSORA.responder(comando)
        if resposta_impressora:
            falar_e_registrar(comando, resposta_impressora)
            analisador.registrar_interacao(comando, ["impressora"], 0.0, True)
            continue

        # Controle remoto (TV e ar pelo Ekaza) antes da tela, das pastas, da agenda e dos agentes: "liga a TV",
        # "aumenta o volume", "liga o ar em 22 graus". Vale também pelo WhatsApp e pelo app (a resposta vai por escrito).
        inicio_controle = time.time()
        resposta_controle = CONTROLE.responder(comando)
        if resposta_controle:
            falar_e_registrar(comando, resposta_controle)
            analisador.registrar_interacao(comando, ["controle_remoto"], time.time() - inicio_controle, True)
            continue

        # Leitura de tela antes das leis, dos agentes e das ferramentas: "resume o que está na tela" não é um pedido de
        # PDF, e "explica melhor" logo depois de ler a tela é sobre a tela. Só pela voz ou pelo painel: o conteúdo
        # da tela não sai do computador (nada de WhatsApp).
        if CANAL["atual"] != "voz" and TELA.reconhece(comando):
            responder_rapido("Eu só leio a tela quando você pede pela voz ou pelo painel, para o conteúdo dela não sair do computador.")
            continue
        inicio_tela = time.time()
        resposta_tela = TELA.responder(comando)
        if resposta_tela:
            responder_tela(resposta_tela)
            analisador.registrar_interacao(comando, ["leitura_tela"], time.time() - inicio_tela, True)
            continue

        # Abrir pastas ("abre a pasta da OAB", "abre meus downloads") antes das leis, dos agentes e das ferramentas.
        resposta_pasta = PASTAS.responder(comando)
        if resposta_pasta:
            falar_e_registrar(comando, resposta_pasta)
            analisador.registrar_interacao(comando, ["pastas"], 0.0, True)
            continue

        # Artigos de lei antes dos agentes, do resumo e das ferramentas: "me explica o artigo 5 da
        # Constituição" não é pesquisa na web. "Faz um PDF sobre o artigo 5" passa (os agentes fazem).
        inicio_lei = time.time()
        resposta_lei = LEIS.responder(comando)
        if resposta_lei:
            responder_lei(comando, resposta_lei)
            analisador.registrar_interacao(comando, ["leis"], time.time() - inicio_lei, True)
            continue

        # "Manda o arquivo" (pelo WhatsApp) ou "manda o PDF pro meu WhatsApp" (pela voz).
        if mandar_arquivo_pedido(comando, orquestrador):
            continue

        # Agenda antes dos agentes e das ferramentas: "o que tenho hoje" não é a data de hoje, e
        # "amanhã às 3" pode ser a resposta a uma pergunta da agenda ("Para quando eu marco?").
        # Pedidos de documento ("faz um PDF da minha agenda") e o resumo do dia passam direto.
        resposta_agenda = AGENDA.responder(comando)
        if resposta_agenda:
            falar_e_registrar(comando, resposta_agenda)
            analisador.registrar_interacao(comando, ["agenda"], 0.0, True)
            continue

        # Agentes antes das ferramentas: as ferramentas reagem a palavras soltas ("hoje", "agenda"),
        # e "faça um PDF com as notícias de hoje" viraria a resposta da data.
        resposta_tarefas = orquestrador.responder_gerenciamento(comando)
        if resposta_tarefas:
            falar_e_registrar(comando, resposta_tarefas)
            analisador.registrar_interacao(comando, ["orquestrador"], 0.0, True)
            continue
        tarefa = orquestrador.enviar(
            comando,
            ao_concluir=lambda t, pedido=comando, canal=CANAL["atual"], mandar=pede_envio(comando):
                anunciar_tarefa_concluida(pedido, t, canal, mandar),
            # Pedido pelo WhatsApp ou pelo app: ninguém está na frente do PC para ver o arquivo aberto.
            abrir=CANAL["atual"] == "voz",
        )
        if tarefa:
            falar_e_registrar(comando, orquestrador.mensagem_inicio(tarefa))
            continue

        # "Faz um resumo sobre X": pesquisa e escreve um resumo explicativo. Depois dos agentes,
        # que cuidam dos resumos em PDF, de e-mails, do dia e para prova.
        pedido_resumo = preparar_pedido_de_resumo(comando, llm_clients.historico_recente())
        if pedido_resumo:
            tema, ia = pedido_resumo
            responder_rapido(f"Vou pesquisar e preparar um resumo sobre {tema}. Leva mais ou menos meio minuto.")
            inicio_resumo = time.time()
            resumo = gerar_resumo(tema, preferir=ia)
            avisar_painel("resumo", resumo.texto, fala=resumo.fala, tema=resumo.tema, secoes=resumo.secoes,
                          fontes=resumo.fontes, arquivo=resumo.arquivo)
            falar_e_registrar(comando, resumo.fala, texto_painel=resumo.texto)
            analisador.registrar_interacao(comando, ["resumo_explicativo"], time.time() - inicio_resumo, True)
            continue

        ferramenta_executada, resposta_ferramenta = tentar_executar_ferramenta(comando)
        if ferramenta_executada:
            if resposta_ferramenta:
                    falar_e_registrar(comando, resposta_ferramenta)
            analisador.registrar_interacao(comando, ["ferramenta_nativa"], 0.0, True)
            continue

        if any(expr in comando_normalizado for expr in ["que horas", "qual a hora", "data de hoje"]):
            resposta = obter_data_hora_atual()
            falar_e_registrar(comando, resposta)
            analisador.registrar_interacao(comando, ["obter_data_hora_atual"], 0.0, True)
            continue

        if comando_normalizado.strip(" .!?,") in COMANDOS_NOVA_CONVERSA:
            llm_clients.limpar_historico()
            falar_e_registrar(comando, frase("assunto_novo"))
            analisador.registrar_interacao(comando, [], 0.0, True)
            continue

        # "e quanto custa?" vira "quanto custa o produto X?" antes de buscar na memória ou na web.
        consulta = llm_clients.reescrever_consulta(comando)
        categoria = classificar_com_ollama(consulta)

        if categoria == "simples":
            try:
                inicio_resposta = time.time()
                responder_conversa_em_stream(comando)
                analisador.registrar_interacao(comando, [], time.time() - inicio_resposta, True)
                continue
            except Exception as exc:
                logger.warning("Ollama falhou para resposta simples: %s", exc)

        if not llm_clients.is_ollama_available():
            responder_rapido("Ollama não está disponível. Verifique se o serviço local está ativo.")
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
            responder_rapido(frase("erro"))
            analisador.registrar_interacao(comando, [], 0.0, False)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if memory.BANCO_OCUPADO:
        # Outra Beatriz já tem o banco da biblioteca (a trava de memory.py): duas no mesmo banco o corrompem.
        mensagem = "A Beatriz já está aberta (ou outro programa está usando a biblioteca dela). Feche-a antes de abrir de novo."
        print(mensagem)
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, mensagem, "Beatriz", 0x40)
        except Exception:
            pass
        sys.exit(1)
    # Login (rosto + senha) antes de qualquer coisa: abre a tela em outro processo e só segue se a pessoa entrou.
    # Sem login criado, a tela abre o cadastro. LOGIN_FACIAL=false no .env pula esta etapa.
    from autenticacao import exigir_login

    if not exigir_login():
        print("Login não concluído. Encerrando.")
        sys.exit(1)
    # Antes de qualquer processo filho: eles herdam o endereço e pedem a este as buscas e leituras da biblioteca,
    # sem abrir o banco (dois processos no ChromaDB corrompem o índice).
    memory.servir_biblioteca()
    print("1: Iniciando script...")
    print("2: Abrindo tela de carregamento separada...")
    evento_carregamento = multiprocessing.Event()
    processo_carregamento = iniciar_leve(iniciar_tela_carregamento_em_background, args=(evento_carregamento,), daemon=True)
    print("3: Inicialização em andamento. Entrando no main()...")
    main(evento_carregamento=evento_carregamento)