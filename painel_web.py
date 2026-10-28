"""Painel da Beatriz em interface web local: pywebview (WebView2 do Windows) com HTML, CSS e JS.

A janela carrega interface_web/index.html. A página chama os métodos de `ApiPainel`
(`pywebview.api.<método>`) e busca os eventos novos a cada 250 ms (`eventos`): o Python não
empurra nada para a página, o que evita mexer na janela a partir de outras threads.

Roda num processo próprio, como o painel clássico em Tkinter (interface_texto.py), que
continua disponível com INTERFACE=tk no .env. Os nomes que começam com "_" não ficam
visíveis para o JavaScript.
"""
import base64
import ctypes
import logging
import os
import queue
import threading
import time

import camadas_personalidade
import conversa_nuvem
import estilo_video
import memory
import niveis_voz
import personalidade
import preferencias_estudo
from agente_agenda import criar_agente_agenda
from agente_controle import criar_agente_controle
from agente_craft import AgenteCraft
from agente_impressora import criar_agente_impressora, pede_impressao, texto_da_agenda
from agente_leis import AgenteLeis
from agentes import PainelCompartilhado, criar_orquestrador
from biblioteca_fontes import BibliotecaDeFontes, ErroDaBiblioteca, resultado as resultado_da_biblioteca
from cinema import cartao_de_filmes, responder_pedido_de_cinema
from criacao_visual import AgenteCriacao
from config import MASCOTE_NA_TELA, MODOS_PASTAS, OLLAMA_MODEL, PASTA_DOCUMENTOS_GERADOS
from interface_texto import ATALHOS_RAPIDOS, definir_id_app_windows, verificar_ollama
from jeito_de_falar import frase
from agente_pastas import AgentePastas
from fontes import criar_agente_fontes
from leitura_tela import AgenteTela
from llm_nuvem import claude, extrair_ia_pedida, gemini
from memory import importar_fonte_local, notelooklm_responder
from painel_hud import MonitorRecursos, contar_linhas, grupos_da_biblioteca, ler_metricas_analise
from professora_idiomas import processar_comando_idiomas
from resumo_explicativo import gerar_resumo, preparar_pedido_de_resumo
from voice import (
    ao_medir_nivel,
    definir_microfone_ativo,
    falar_async,
    responder_troca_de_voz,
    listar_microfones,
    listar_saidas_audio,
    microfone_ativo,
    microfone_selecionado,
    saida_audio_selecionada,
    selecionar_microfone,
    selecionar_saida_audio,
)

logger = logging.getLogger(__name__)

PASTA_PROJETO = os.path.dirname(os.path.abspath(__file__))
PAGINA = os.path.join(PASTA_PROJETO, "interface_web", "index.html")
ICONE_ICO = os.path.join(PASTA_PROJETO, "assets", "beatriz_icone.ico")
PASTA_ARMAZENAMENTO = os.path.join(PASTA_PROJETO, "banco_memoria", "painel_web")  # preferências da página
FUNDO = "#000000"
EVENTOS_POR_BUSCA = 50
VALIDADE_OLLAMA = 15.0  # segundos entre consultas ao Ollama
PEDACO_DO_VIDEO = 2 * 1024 * 1024  # o MP4 das criações vai à página em pedaços
LIMITE_GLB = 60 * 1024 * 1024  # o modelo 3D das criações, exportado pelo painel
VALIDADE_BIBLIOTECA = 10.0  # contar trechos e conversas a cada busca de estado pesaria


def _inferir_categoria(caminho: str) -> str:
    nome = os.path.basename(caminho).lower()
    categorias = {
        "comunicacao": ["comunic", "fala", "dialog", "relacion"],
        "filosofia": ["filosof", "ética", "etica", "existencial"],
        "psicologia": ["psicolog", "ansiedade", "emoc", "terapia"],
        "direito": ["lei", "direito", "jurid", "contrato"],
        "projetos": ["projeto", "roadmap", "planejamento"],
        "procedimentos": ["procedimento", "manual", "tutorial", "guia"],
    }
    return next((categoria for categoria, palavras in categorias.items() if any(p in nome for p in palavras)), "referencias")


class ApiPainel:
    """Métodos que a página chama. Cada um devolve dados simples (viram JSON na ponte)."""

    def __init__(self, microfone_evento=None, audio_config=None, fila_voz=None, niveis=None):
        self._microfone_evento = microfone_evento
        # Níveis da voz (dela e do microfone) que o processo principal mede; a fala deste processo
        # (respostas do chat digitado) também entra, para a esfera vibrar com ela.
        self._niveis = niveis_voz.NiveisDeVoz(niveis) if niveis is not None else None
        if self._niveis is not None:
            ao_medir_nivel(self._niveis.publicar)
        self._audio_config = audio_config
        self._fila_voz = fila_voz  # o que a voz ouviu e respondeu, vindo do processo principal
        self._eventos = queue.Queue()  # eventos gerados aqui mesmo (pedidos digitados no painel)
        self._dados_tarefas = audio_config if audio_config is not None else {}
        self._orquestrador = criar_orquestrador(publicar=PainelCompartilhado(self._dados_tarefas))
        # A mesma agenda do processo principal (mesmo arquivo SQLite); os lembretes saem só de lá.
        from llm_clients import llm_clients
        self._agenda = criar_agente_agenda(llm=llm_clients, ao_abrir=lambda: self._emitir({"tipo": "abrir_agenda"}))
        self._ultimas_respostas = []  # mais nova primeiro, para "imprime isso"
        # artigos de lei e súmulas (vade-mécum do PC): texto oficial e explicação pelo Gemini
        self._leis = AgenteLeis(avisar=self._responder)
        # Camadas de personalidade: os pedidos diretos do chat ("me chama de…") e a aba "camadas" da janela; o
        # aprendizado de tempos em tempos roda no processo principal (aqui, só o "aprender agora").
        self._aprendiz = camadas_personalidade.Aprendiz(llm=llm_clients)
        self._camadas = camadas_personalidade.AgenteCamadas(
            llm=llm_clients, ultima_resposta=lambda: (self._ultimas_respostas or [""])[0], aprendiz=self._aprendiz)
        self._impressora = criar_agente_impressora(
            respostas_recentes=lambda: list(self._ultimas_respostas),
            texto_agenda=lambda t: texto_da_agenda(self._agenda, t),
            avisar=self._responder,
        )
        # TV e ar pelo Ekaza (o mesmo arquivo de códigos e estado do processo principal); o fim do aprender volta aqui.
        self._controle = criar_agente_controle(avisar=self._responder)
        self._pastas = AgentePastas()  # "abre a pasta da OAB" (a busca tem o próprio índice, neste processo)
        threading.Thread(target=self._pastas.aquecer, daemon=True).start()
        # Arquivos recebidos (o WhatsApp roda no processo principal; o registro é o mesmo arquivo): "vira fonte", "arquiva", "imprime",
        # "escaneia". "Me manda no zap" só funciona pela voz ou pelo próprio WhatsApp.
        self._fontes = criar_agente_fontes(avisar=lambda texto, canal: self._responder(texto), enviar_zap=None,
                                           impressora=self._impressora, pastas=self._pastas, llm=llm_clients,
                                           estudar=self._estudar_arquivos, peca=self._peca_de_arquivos)
        # Apps da ArtCraft: "abre o PhotoCraft", "converte esse Word em PDF", "junta esses PDFs" (o resultado abre no PC).
        self._craft = AgenteCraft(recebidos=self._fontes.recebidos_do_pedido, arquivos=self._pastas.arquivos,
                                  avisar=lambda resposta, canal: self._responder(resposta.texto),  # o vídeo editado chega depois
                                  video_animado_recente=lambda: self._criacao.tem_video_recente())
        # "Cria uma imagem de…" / "cria um modelo 3D de…": o Claude desenha e o resultado vai para o centro, no lugar da esfera.
        self._criacao = AgenteCriacao(mostrar=self._emitir, avisar=lambda texto, canal, arquivos: self._responder(texto))
        self._tela = AgenteTela(llm=llm_clients, avisar=lambda texto: falar_async(texto))  # lê a janela ativa (não a do painel)
        # Biblioteca de fontes organizada à mão: a nova leitura do índice é pedida ao processo principal (só ele grava no banco).
        self._biblioteca = BibliotecaDeFontes(ao_mudar=self._pedir_leitura_da_biblioteca)
        self._monitor = MonitorRecursos()
        self._janela = None
        self._mascote = None  # a mascote que anda pela tela (mascote_tela.MascoteDaTela)
        self._cache = {}  # nome -> (validade, valor)
        if fila_voz is not None:
            threading.Thread(target=self._distribuir_voz, name="distribuir-voz", daemon=True).start()

    # ------------------------------------------------------------------ chamadas da página
    def inicio(self) -> dict:
        return {"nome": "Beatriz", "modelo": OLLAMA_MODEL, "atalhos": [list(a) for a in ATALHOS_RAPIDOS]}

    def eventos(self) -> list:
        """Eventos novos: os da voz (processo principal) e os dos pedidos feitos no painel."""
        novos = []
        try:
            while len(novos) < EVENTOS_POR_BUSCA:
                novos.append(self._eventos.get_nowait())
        except queue.Empty:
            pass
        return novos

    def niveis(self) -> dict:
        """A voz dela e a do microfone agora (nível, dB e bandas); a página pergunta ~20 vezes por segundo."""
        return self._niveis.ler() if self._niveis is not None else {}

    def estado(self) -> dict:
        metricas = ler_metricas_analise() or {}
        cpu = self._monitor.cpu()
        ram, _, _ = self._monitor.ram()
        return {
            "modelo": OLLAMA_MODEL,
            "ollama": self._em_cache("ollama", VALIDADE_OLLAMA, self._consultar_ollama),
            # O estado que o processo principal publica (lá estão as pausas de verdade); sem ele, o deste processo.
            "nuvem": self._nuvem_do_principal() or [
                {"nome": provedor.nome, "texto": provedor.status()[0], "estado": provedor.status()[1]}
                for provedor in (claude, gemini)],
            "whatsapp": self._compartilhado("whatsapp"),
            "painel": self._compartilhado("painel"),
            "microfone_ativo": microfone_ativo(),
            "modo": memory.MODO_CONVERSA_ATUAL,
            "modos": list(MODOS_PASTAS),
            "metricas": {chave: metricas.get(chave) for chave in ("interacoes", "tempo_medio", "sucesso")},
            "biblioteca": self._em_cache("biblioteca", VALIDADE_BIBLIOTECA, self._contar_biblioteca),
            "recursos": {"cpu": cpu, "ram": ram},
            "tarefas": PainelCompartilhado.listar(self._dados_tarefas)[:6],
        }

    def enviar(self, texto: str) -> bool:
        texto = (texto or "").strip()
        if not texto:
            return False
        self._emitir({"tipo": "voce", "texto": texto})
        threading.Thread(target=self._processar, args=(texto,), name="pedido-painel", daemon=True).start()
        return True

    def alternar_microfone(self) -> bool:
        ativo = not microfone_ativo()
        definir_microfone_ativo(ativo)
        if self._microfone_evento is not None:
            self._microfone_evento.set() if ativo else self._microfone_evento.clear()
        return ativo

    def escolher_modo(self, modo: str) -> str:
        resultado = memory.mudar_modo_conversa(modo)
        if self._audio_config is not None:
            self._audio_config["modo_pedido"] = modo  # o processo principal aplica o mesmo modo na voz
        return resultado

    def dispositivos_audio(self) -> dict:
        indice = microfone_selecionado()
        return {
            "microfones": [{"nome": "Padrão automático", "valor": ""}] +
                          [{"nome": f"{d['indice']}: {d['nome']}", "valor": str(d["indice"])} for d in listar_microfones()],
            "saidas": [{"nome": "Padrão do sistema", "valor": ""}] + [{"nome": s, "valor": s} for s in listar_saidas_audio()],
            "microfone_atual": "" if indice is None else str(indice),
            "saida_atual": saida_audio_selecionada() or "",
        }

    def aplicar_audio(self, microfone: str, saida: str) -> str:
        indice = int(microfone) if str(microfone or "").strip() else None
        if not selecionar_microfone(indice):
            return "Não foi possível selecionar esse microfone."
        if not selecionar_saida_audio(saida or ""):
            return "Não foi possível selecionar essa saída de áudio."
        if self._audio_config is not None:
            self._audio_config["microfone"] = indice
            self._audio_config["saida"] = saida or ""
        return "Áudio aplicado."

    def anexar(self) -> bool:
        import webview

        if self._janela is None:
            return False
        caminhos = self._janela.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=("Documentos suportados (*.pdf;*.docx;*.xlsx;*.pptx;*.txt;*.md;*.html;*.png;*.jpg;*.jpeg)",
                        "Todos os arquivos (*.*)"),
        )
        if not caminhos:
            return False
        self._emitir({"tipo": "missao", "titulo": "Nova missão", "texto": f"Organizar {len(caminhos)} arquivo(s) na biblioteca"})
        threading.Thread(target=self._importar, args=(list(caminhos),), daemon=True).start()
        return True

    # ------------------------------------------------------------------ janela da agenda
    def agenda(self, deslocamento: int = 0) -> dict:
        """A semana (com `deslocamento` semanas a partir desta), as tarefas e o próximo compromisso."""
        return self._agenda.dados_semana(int(deslocamento or 0))

    def agenda_mes(self, ano: int = 0, mes: int = 0) -> dict:
        """O calendário do mês (0 = o mês atual), para o quadro da tela e a aba "mês" da janela."""
        return self._agenda.dados_mes(ano or None, mes or None)

    def agenda_dia(self, data: str = "") -> dict:
        return self._agenda.dados_dia(data or None)

    def agenda_pedir(self, texto: str) -> str:
        """Campo da janela da agenda: "dentista amanhã às 14h" vira compromisso; perguntas também valem."""
        texto = (texto or "").strip()
        if not texto:
            return ""
        resposta = self._agenda.responder(texto)
        if resposta is None:  # "dentista amanhã às 14h", sem o "marca": aqui o pedido é sempre para a agenda
            resposta = self._agenda.responder(f"marca {texto}")
        return resposta or "Não entendi. Tente algo como: dentista amanhã às 14h, ou academia toda segunda às 7h."

    def agenda_cancelar(self, evento_id: int, dia: str, tudo: bool = False) -> str:
        return self._agenda.cancelar_por_id(evento_id, dia, bool(tudo))

    def tarefa_nova(self, texto: str) -> str:
        texto = (texto or "").strip()
        return self._agenda.adicionar_tarefa(f"tarefa {texto}") if texto else ""

    def tarefa_alternar(self, tarefa_id: int) -> str:
        return self._agenda.alternar_tarefa(tarefa_id)

    def tarefa_remover(self, tarefa_id: int) -> str:
        return self._agenda.remover_tarefa_por_id(tarefa_id)

    # ------------------------------------------------------------------ janela da biblioteca de fontes
    def biblioteca(self) -> dict:
        """As áreas (fontes, só arquivos, recebidos) e as pastas com os arquivos e o estado de cada um."""
        dados = self._biblioteca.listar()
        dados["leitura"] = (self._compartilhado("painel").get("indexacao") or {}).get("status", "")
        return dados

    def biblioteca_mover(self, arquivo: str, pasta: str) -> dict:
        return resultado_da_biblioteca(self._biblioteca.mover, arquivo, pasta)

    def biblioteca_renomear(self, arquivo: str, nome: str) -> dict:
        return resultado_da_biblioteca(self._biblioteca.renomear, arquivo, nome)

    def biblioteca_criar_pasta(self, pasta: str, nome: str) -> dict:
        return resultado_da_biblioteca(self._biblioteca.criar_pasta, pasta, nome)

    def biblioteca_apagar_pasta(self, pasta: str) -> dict:
        return resultado_da_biblioteca(self._biblioteca.apagar_pasta, pasta)

    def biblioteca_so_arquivo(self, arquivo: str) -> dict:
        return resultado_da_biblioteca(self._biblioteca.so_arquivo, arquivo)

    def biblioteca_virar_fonte(self, arquivo: str) -> dict:
        return resultado_da_biblioteca(self._biblioteca.virar_fonte, arquivo)

    def biblioteca_lote(self, acao: str, arquivos: list, pasta: str = "") -> dict:
        """A mesma ação (mover, so_arquivo, virar_fonte, apagar) nos arquivos marcados, com uma nova leitura só."""
        return resultado_da_biblioteca(self._biblioteca.em_lote, acao, [str(a) for a in arquivos or []], pasta or "")

    def biblioteca_apagar(self, arquivo: str) -> dict:
        return resultado_da_biblioteca(self._biblioteca.apagar, arquivo)

    def biblioteca_adicionar(self, pasta: str) -> dict:
        """Escolher arquivos do PC e copiar para a pasta da biblioteca."""
        import webview

        if self._janela is None:
            return {"ok": False, "mensagem": "A janela não está pronta."}
        caminhos = self._janela.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True)
        if not caminhos:
            return {"ok": True, "mensagem": ""}
        respostas = [resultado_da_biblioteca(self._biblioteca.adicionar, os.path.basename(c), pasta, origem=c) for c in caminhos]
        return {"ok": all(r["ok"] for r in respostas), "mensagem": " ".join(r["mensagem"] for r in respostas)}

    def biblioteca_abrir(self, arquivo: str, na_pasta: bool = False) -> dict:
        """Abre o arquivo no programa dele, ou mostra onde ele está no Explorador (`na_pasta`)."""
        try:
            caminho = self._biblioteca.caminho_para_abrir(arquivo)
        except ErroDaBiblioteca as exc:
            return {"ok": False, "mensagem": str(exc)}
        if na_pasta:
            import subprocess

            subprocess.Popen(["explorer", "/select,", os.path.normpath(caminho)])
        else:
            os.startfile(caminho)
        return {"ok": True, "mensagem": ""}

    # ------------------------------------------------------------------ personalidade (janela)
    def personalidade(self) -> dict:
        """Quem ela é, os exemplos de conversa e as frases prontas de agora, com o padrão para comparar."""
        return personalidade.para_a_janela()

    def personalidade_salvar(self, dados: dict) -> dict:
        """Grava o que está na janela; a próxima resposta (aqui e no processo principal) já sai com ela."""
        return personalidade.resultado(personalidade.salvar, dados)

    def personalidade_padrao(self) -> dict:
        return personalidade.resultado(personalidade.restaurar_padrao)

    def personalidade_anterior(self) -> dict:
        return personalidade.resultado(personalidade.recuperar_anterior)

    def personalidade_testar(self, pergunta: str, texto: str = "", exemplos: str = "") -> dict:
        """A resposta dela à pergunta com o rascunho da janela, sem salvar: pela IA local, sem histórico,
        como na conversa por voz."""
        pergunta = (pergunta or "").strip()
        if not pergunta:
            return {"ok": False, "resposta": "", "mensagem": "Escreva uma pergunta para testar."}
        from llm_clients import llm_clients

        inicio = time.time()
        try:
            sistema = llm_clients._sistema_beatriz(personalidade.contexto_da_conversa_falada(exemplos),
                                                   texto_da_personalidade=(texto or "").strip() or None)
            resposta = llm_clients.chat(pergunta, system_prompt=sistema, usar_historico=False)
        except Exception as exc:
            logger.warning("Teste da personalidade: %s", exc)
            return {"ok": False, "resposta": "", "mensagem": f"A IA local não respondeu: {exc}"}
        return {"ok": True, "resposta": resposta, "segundos": round(time.time() - inicio, 1), "mensagem": ""}

    # ------------------------------------------------------------------ camadas de personalidade (aba da janela)
    def camadas(self) -> dict:
        return camadas_personalidade.para_a_janela()

    def camadas_nova(self, tipo: str, texto: str) -> dict:
        return camadas_personalidade.resultado(camadas_personalidade.adicionar, tipo, texto, "manual")

    def camadas_editar(self, camada_id: str, campos: dict) -> dict:
        campos = {k: v for k, v in (campos or {}).items() if k in ("texto", "tipo", "ativa", "fixada")}
        return camadas_personalidade.resultado(camadas_personalidade.editar, camada_id, **campos)

    def camadas_apagar(self, camada_id: str) -> dict:
        return camadas_personalidade.resultado(camadas_personalidade.apagar, camada_id)

    def camadas_aprendizado(self, ligado: bool) -> dict:
        return camadas_personalidade.resultado(camadas_personalidade.definir_aprender, bool(ligado))

    def camadas_aprender(self) -> dict:
        """Uma rodada agora, com as últimas trocas da conversa (pela IA local; leva alguns segundos)."""
        novas = self._aprendiz.aprender_agora(forcar=True)
        mensagem = (f"{len(novas)} {'camada nova' if len(novas) == 1 else 'camadas novas'}." if novas
                    else "Nada novo nas últimas conversas (ou a IA local não respondeu).")
        return {"ok": True, "mensagem": mensagem, "dados": camadas_personalidade.para_a_janela()}

    # ------------------------------------------------------------------ estudos (janela)
    def estudos(self) -> dict:
        """As escolhas dos resumos, fichas e peças, as opções da janela, as fotos de exemplo e o guia."""
        return preferencias_estudo.para_a_janela()

    def estudos_salvar(self, dados: dict) -> dict:
        return preferencias_estudo.resultado(preferencias_estudo.salvar, dados)

    def estudos_padrao(self) -> dict:
        return preferencias_estudo.resultado(preferencias_estudo.restaurar_padrao)

    def estudos_fotos_adicionar(self) -> dict:
        """Escolher fotos do PC (PNG, JPG ou WebP) para os exemplos de resumo."""
        import webview

        if self._janela is None:
            return {"ok": False, "mensagem": "A janela não está pronta.", "dados": None}
        caminhos = self._janela.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True, file_types=("Fotos (*.png;*.jpg;*.jpeg;*.webp)",))
        if not caminhos:
            return {"ok": True, "mensagem": "", "dados": preferencias_estudo.para_a_janela()}
        respostas = [preferencias_estudo.resultado(preferencias_estudo.adicionar_foto, os.path.basename(c), origem=c)
                     for c in caminhos]
        erros = [r["mensagem"] for r in respostas if not r["ok"]]
        mensagem = " ".join(dict.fromkeys(erros)) or f"{len(respostas)} foto(s) adicionada(s)."
        return {"ok": not erros or len(erros) < len(respostas), "mensagem": mensagem, "dados": preferencias_estudo.para_a_janela()}

    def estudos_foto(self, foto_id: str) -> str:
        """A miniatura de uma foto de exemplo (data URL), para a janela mostrar."""
        try:
            return preferencias_estudo.miniatura(preferencias_estudo.caminho_da_foto(foto_id))
        except (preferencias_estudo.ErroDasPreferencias, OSError) as exc:
            logger.debug("Miniatura da foto %s: %s", foto_id, exc)
            return ""

    def estudos_foto_apagar(self, foto_id: str) -> dict:
        return preferencias_estudo.resultado(preferencias_estudo.apagar_foto, foto_id)

    def estudos_guia(self) -> dict:
        """O Claude olha as fotos e monta o guia de estilo (só ele; sem ele, o modelo de visão do PC). ~20 s a 1 min."""
        return preferencias_estudo.resultado(preferencias_estudo.extrair_guia)

    def estudos_peca_arquivos(self) -> list:
        """Os arquivos do caso (a foto ou o PDF do enunciado, documentos) para a peça nova."""
        import webview

        if self._janela is None:
            return []
        return list(self._janela.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True) or [])

    def estudos_peca(self, tipo: str, fatos: str, arquivos: list = None) -> dict:
        """A peça nova da janela: vira uma tarefa dos agentes (os fatos vão para o Claude, com o Gemini de reserva)."""
        tarefa = self._orquestrador.enviar_peca(tipo or "", fatos or "", list(arquivos or []), ao_concluir=self._missao_concluida)
        if tarefa is None:
            return {"ok": False, "mensagem": "Escreva os fatos do caso ou adicione os arquivos dele."}
        return {"ok": True, "mensagem": self._orquestrador.mensagem_inicio(tarefa)}

    def _pedir_leitura_da_biblioteca(self):
        if self._audio_config is not None:
            try:
                self._audio_config["reindexar_biblioteca"] = time.time()
            except Exception as exc:
                logger.debug("Não consegui pedir a nova leitura da biblioteca: %s", exc)

    def abrir(self, caminho: str) -> bool:
        """Abre um arquivo ou pasta gerado pela Beatriz (só dentro da pasta do projeto ou dos documentos gerados)."""
        caminho = os.path.realpath(caminho or "")
        permitidas = [os.path.realpath(PASTA_PROJETO), os.path.realpath(PASTA_DOCUMENTOS_GERADOS)]
        if not os.path.exists(caminho) or not any(caminho.startswith(p) for p in permitidas):
            self._emitir({"tipo": "sistema", "titulo": "Alerta", "texto": "Não encontrei esse arquivo.", "erro": True})
            return False
        os.startfile(caminho)
        return True

    def criacao_video_parte(self, arquivo: str, parte: int = 0) -> dict:
        """Um pedaço (base64) do MP4 de uma criação, para a página montar o vídeo: a ponte não leva arquivos grandes de uma
        vez. Só .mp4 de uma pasta *_criacoes dos documentos gerados."""
        caminho = os.path.realpath(arquivo or "")
        pasta = os.path.dirname(caminho)
        if (not caminho.endswith(".mp4") or not os.path.isfile(caminho) or not pasta.endswith("_criacoes")
                or not pasta.startswith(os.path.realpath(PASTA_DOCUMENTOS_GERADOS))):
            return {"dados": "", "partes": 0}
        tamanho = os.path.getsize(caminho)
        with open(caminho, "rb") as entrada:
            entrada.seek(int(parte) * PEDACO_DO_VIDEO)
            pedaco = entrada.read(PEDACO_DO_VIDEO)
        return {"dados": base64.b64encode(pedaco).decode("ascii"), "partes": max(1, -(-tamanho // PEDACO_DO_VIDEO))}

    # -- a janela [ vídeos ]: o seu estilo (estilo_video.py), os exemplos e um vídeo novo
    def estilo_video(self) -> dict:
        return estilo_video.para_a_janela()

    def estilo_video_salvar(self, dados: dict) -> dict:
        return estilo_video.resultado(estilo_video.salvar, dados)

    def estilo_video_padrao(self) -> dict:
        return estilo_video.resultado(estilo_video.restaurar_padrao)

    def estilo_video_exemplos_adicionar(self) -> dict:
        """Escolher prints e vídeos do PC para os exemplos (de cada vídeo ficam 3 quadros)."""
        import webview

        if self._janela is None:
            return {"ok": False, "mensagem": "A janela não está pronta.", "dados": None}
        caminhos = self._janela.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True, file_types=(
            "Imagens e vídeos (*.png;*.jpg;*.jpeg;*.webp;*.mp4;*.mov;*.m4v;*.webm;*.mkv)",))
        if not caminhos:
            return {"ok": True, "mensagem": "", "dados": estilo_video.para_a_janela()}
        respostas = [estilo_video.resultado(estilo_video.adicionar_exemplo, os.path.basename(c), origem=c) for c in caminhos]
        erros = [r["mensagem"] for r in respostas if not r["ok"]]
        mensagem = " ".join(dict.fromkeys(erros)) or f"{len(respostas)} exemplo(s) adicionado(s)."
        return {"ok": not erros or len(erros) < len(respostas), "mensagem": mensagem, "dados": estilo_video.para_a_janela()}

    def estilo_video_exemplo(self, exemplo_id: str) -> str:
        try:
            return estilo_video.miniatura(estilo_video.caminho_do_exemplo(exemplo_id))
        except (estilo_video.ErroDoEstilo, OSError) as exc:
            logger.debug("Miniatura do exemplo %s: %s", exemplo_id, exc)
            return ""

    def estilo_video_exemplo_apagar(self, exemplo_id: str) -> dict:
        return estilo_video.resultado(estilo_video.apagar_exemplo, exemplo_id)

    def estilo_video_guia(self) -> dict:
        """O Claude olha os exemplos e monta o guia (só ele; sem ele, o modelo de visão do PC). ~20 s a 1 min."""
        return estilo_video.resultado(estilo_video.extrair_guia)

    def estilo_video_novo(self, assunto: str, opcoes: dict = None) -> dict:
        """Um vídeo novo da janela: o Claude faz o roteiro no estilo, e ele aparece no centro, no lugar da esfera."""
        texto = self._criacao.criar_video(assunto, "painel", opcoes)
        falar_async(texto)
        return {"ok": not texto.startswith("Escreva"), "mensagem": texto}

    def estilo_video_refazer(self) -> dict:
        """Monta o último vídeo de novo com o estilo de agora (sem o Claude)."""
        texto = self._criacao.refazer_video("painel")
        return {"ok": texto.startswith("Montando"), "mensagem": texto}

    def criacao_glb(self, arquivo_json: str, dados: str) -> str:
        """Grava ao lado do .json da criação o modelo 3D em .glb, que o painel exportou (base64). Só na pasta das criações."""
        caminho = os.path.realpath(arquivo_json or "")
        pasta = os.path.dirname(caminho)
        if (not caminho.endswith(".json") or not os.path.isfile(caminho) or not pasta.endswith("_criacoes")
                or not pasta.startswith(os.path.realpath(PASTA_DOCUMENTOS_GERADOS))):
            return ""
        try:
            conteudo = base64.b64decode(dados or "", validate=True)
        except ValueError:
            return ""
        if not conteudo.startswith(b"glTF") or len(conteudo) > LIMITE_GLB:
            return ""
        destino = caminho[:-5] + ".glb"
        with open(destino, "wb") as arquivo:
            arquivo.write(conteudo)
        return destino

    # ------------------------------------------------------------------ por dentro
    def _emitir(self, evento: dict):
        """Manda o evento para a página do painel e para a mascote da tela."""
        self._eventos.put(evento)
        if self._mascote is not None:
            self._mascote.ao_evento(evento)

    def _distribuir_voz(self):
        """Lê o que a voz manda (processo principal) sem depender da página do painel, que o
        navegador desacelera quando a janela está minimizada; assim a mascote reage na hora."""
        while True:
            try:
                self._emitir(self._fila_voz.get(timeout=1))
            except queue.Empty:
                continue
            except (EOFError, OSError, ValueError):
                return  # o processo principal fechou a fila

    def _responder(self, texto: str, mostrar: bool = True):
        """Fala a resposta (neste processo) e a manda para a página."""
        falar_async(texto)
        self._emitir({"tipo": "resposta", "texto": texto, "mostrar": mostrar})
        self._ultimas_respostas = [texto] + self._ultimas_respostas[:9]

    def _processar(self, comando: str):
        """O mesmo caminho do chat do painel clássico: impressora, agenda, agentes, idiomas, resumo, cinema e biblioteca."""
        self._emitir({"tipo": "pensando"})
        try:
            resposta_troca_voz = responder_troca_de_voz(comando)
            if resposta_troca_voz:  # "usa a voz da Siri": vale aqui e na voz (a escolha fica num arquivo)
                return self._responder(resposta_troca_voz)
            resposta_camadas = self._camadas.responder(comando)
            if resposta_camadas:  # "me chama de…", "fala mais curto": vira camada de personalidade
                return self._responder(resposta_camadas)
            resposta_impressora = self._impressora.responder(comando)
            if resposta_impressora:
                return self._responder(resposta_impressora)
            resposta_controle = self._controle.responder(comando)
            if resposta_controle:  # "liga a TV", "liga o ar em 22 graus"
                return self._responder(resposta_controle)
            resposta_craft = self._craft.responder(comando, "painel")
            if resposta_craft:  # apps da ArtCraft, antes das fontes ("converte esse Word" não é pergunta sobre ele)
                return self._responder(resposta_craft.texto)
            resposta_criacao = self._criacao.responder(comando, "painel")
            if resposta_criacao:  # imagem ou modelo 3D pelo Claude, depois dos apps ("faz uma imagem desse PDF" é deles)
                return self._responder(resposta_criacao)
            resposta_fontes = self._fontes.responder(comando, "painel")
            if resposta_fontes:
                return self._responder(resposta_fontes)
            resposta_tela = self._tela.responder(comando)
            if resposta_tela:  # leitura de tela: resposta curta falada, completa no painel; não vira "imprime isso"
                falar_async(resposta_tela.fala)
                texto = f"{resposta_tela.fala}\n\n{resposta_tela.painel}" if resposta_tela.painel else resposta_tela.fala
                return self._emitir({"tipo": "resposta", "texto": texto, "mostrar": True})
            resposta_pasta = self._pastas.responder(comando)
            if resposta_pasta:
                return self._responder(resposta_pasta)
            resposta_lei = self._leis.responder(comando)
            if resposta_lei:
                return self._responder_lei(resposta_lei)
            resposta_agenda = self._agenda.responder(comando)
            if resposta_agenda:
                self._emitir({"tipo": "agenda_mudou"})
                return self._responder(resposta_agenda)
            gerenciamento = self._orquestrador.responder_gerenciamento(comando)
            if gerenciamento:
                return self._responder(gerenciamento)
            # As missões dos agentes aparecem como lanternas, pelo estado compartilhado.
            tarefa = self._orquestrador.enviar(comando, ao_concluir=self._missao_concluida)
            if tarefa:
                return self._responder(self._orquestrador.mensagem_inicio(tarefa))

            idiomas_executado, resposta_idiomas = processar_comando_idiomas(comando)
            if idiomas_executado:
                return self._responder(resposta_idiomas)

            pedido_resumo = preparar_pedido_de_resumo(comando)
            if pedido_resumo:
                self._emitir({"tipo": "missao", "titulo": "Nova missão", "texto": f"Resumo sobre {pedido_resumo[0]}"})
                resumo = gerar_resumo(*pedido_resumo)
                self._emitir({"tipo": "resumo", "texto": resumo.texto, "fala": resumo.fala, "tema": resumo.tema,
                              "secoes": resumo.secoes, "fontes": resumo.fontes, "arquivo": resumo.arquivo})
                return self._responder(resumo.fala, mostrar=False)

            resposta_cinema = responder_pedido_de_cinema(comando)
            if resposta_cinema:
                cartao = cartao_de_filmes(comando)
                if cartao:
                    self._emitir({"tipo": "filmes", "texto": resposta_cinema, **cartao})
                return self._responder(resposta_cinema)

            # "usa o Claude pra…" e as perguntas de conhecimento geral: a nuvem responde só com a pergunta, como na voz.
            sem_ia, ia = extrair_ia_pedida(comando)
            pergunta = sem_ia if ia and sem_ia else comando
            from llm_clients import llm_clients
            categoria = None if ia else conversa_nuvem.classificar(pergunta, llm_clients)
            if ia or (categoria == "conhecimento" and conversa_nuvem.pode_ir_para_a_nuvem(pergunta)):
                resposta_nuvem = conversa_nuvem.responder(pergunta, preferir=ia, falada=False)
                if resposta_nuvem:
                    return self._responder(resposta_nuvem)
            resposta = notelooklm_responder(comando) or frase("sem_resposta")
            self._responder(resposta)
        except Exception as exc:
            logger.exception("Erro ao processar pedido do painel")
            self._emitir({"tipo": "erro", "texto": f"Erro ao processar: {exc}"})

    def _responder_lei(self, resposta):
        """Lê o artigo (ou uma frase curta), mostra o texto completo e depois fala a explicação da IA."""
        if resposta.leitura:
            falar_async(resposta.leitura)
        if resposta.painel or resposta.leitura:
            self._emitir({"tipo": "resposta", "texto": resposta.painel or resposta.leitura, "mostrar": True})
        if resposta.explicar:
            self._responder(resposta.explicar())

    def _estudar_arquivos(self, caminhos, pedido, canal="painel", ia=None):
        """"Faz uma ficha desse PDF" sobre um arquivo recebido: a ficha tática dos agentes, escrita pelo Claude."""
        tarefa = self._orquestrador.enviar_estudo_de_arquivos(pedido, caminhos, ao_concluir=self._missao_concluida, ia=ia)
        return self._orquestrador.mensagem_inicio(tarefa) if tarefa else None

    def _peca_de_arquivos(self, caminhos, pedido, canal="painel", ia=None):
        """"Faz a peça desse caso" sobre um arquivo anexado ou recebido: o agente de peças, com o arquivo como base."""
        from agentes.pecas import pedido_de_peca

        tarefa = self._orquestrador.enviar_peca(pedido_de_peca(pedido) or "", pedido, caminhos, ia=ia, pedido=pedido,
                                                ao_concluir=self._missao_concluida)
        return self._orquestrador.mensagem_inicio(tarefa) if tarefa else None

    def _missao_concluida(self, tarefa):
        # A janela de "missão concluída" sai da lanterna (estado compartilhado); aqui só a fala.
        falar_async(tarefa.mensagem_final)
        self._emitir({"tipo": "resposta", "texto": tarefa.mensagem_final})
        if tarefa.arquivos and pede_impressao(tarefa.pedido):  # "faz um PDF sobre X e imprime"
            oferta = self._impressora.oferecer_arquivos(tarefa.arquivos)
            if oferta:
                self._responder(oferta)

    def _importar(self, caminhos):
        linhas = []
        for caminho in caminhos:
            categoria = _inferir_categoria(caminho)
            linhas.append(f"{os.path.basename(caminho)} → {categoria}: {importar_fonte_local(caminho, categoria)}")
        self._emitir({"tipo": "resposta", "texto": "\n".join(linhas)})

    def _nuvem_do_principal(self) -> list:
        try:
            return [dict(item) for item in self._audio_config.get("nuvem") or []] if self._audio_config is not None else []
        except Exception:
            return []

    def _compartilhado(self, chave: str) -> dict:
        try:
            return dict(self._audio_config.get(chave) or {}) if self._audio_config is not None else {}
        except Exception:
            return {}

    def _em_cache(self, nome, validade, funcao):
        agora = time.monotonic()
        guardado = self._cache.get(nome)
        if guardado and guardado[0] > agora:
            return guardado[1]
        valor = funcao()
        self._cache[nome] = (agora + validade, valor)
        return valor

    @staticmethod
    def _consultar_ollama() -> dict:
        online, modelo_ok = verificar_ollama()
        return {"online": online, "modelo_ok": modelo_ok}

    @staticmethod
    def _contar_biblioteca() -> dict:
        trechos = memory.total_de_trechos()  # pelo processo principal: o painel não abre o banco
        grupos = grupos_da_biblioteca(memory.CAMINHO_MANIFESTO_MEMORIA,
                                      {"documento_local": memory.PASTA_DOCUMENTOS, "obsidian": memory.VAULT_OBSIDIAN})
        return {"trechos": trechos, "fontes": sum(grupos.values()), "conversas": contar_linhas(memory.ARQUIVO_HISTORICO_CONVERSAS)}


# ------------------------------------------------------------------ janela
def _cor_windows(cor: str) -> ctypes.c_int:
    r, g, b = (int(cor[i:i + 2], 16) for i in (1, 3, 5))
    return ctypes.c_int(r | g << 8 | b << 16)  # o Windows guarda as cores como 0x00BBGGRR


def _ajustar_janela_windows(janela):
    """Ícone da Beatriz e barra de título escura (Windows 11). Falhas aqui não impedem a janela de abrir."""
    try:
        hwnd = int(janela.native.Handle.ToInt64())
    except Exception as exc:
        logger.debug("Sem o identificador da janela: %s", exc)
        return
    try:
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        for tipo, tamanho in ((1, 48), (0, 16)):  # ICON_BIG, ICON_SMALL
            icone = user32.LoadImageW(None, ICONE_ICO, 1, tamanho, tamanho, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if icone:
                user32.SendMessageW(ctypes.c_void_p(hwnd), 0x80, ctypes.c_void_p(tipo), ctypes.c_void_p(icone))  # WM_SETICON
        for atributo, cor in ((34, "#3A3A3A"), (35, FUNDO), (36, "#E4E4E4")):  # borda, barra de título e texto
            valor = _cor_windows(cor)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, atributo, ctypes.byref(valor), ctypes.sizeof(valor))
    except (AttributeError, OSError) as exc:
        logger.debug("Não foi possível ajustar a janela: %s", exc)


def iniciar(evento_pronto=None, microfone_evento=None, audio_config=None, fila_voz=None, niveis=None):
    """Abre o painel (mesma assinatura de interface_texto.iniciar) e só volta quando a janela fecha."""
    import webview

    definir_id_app_windows()
    api = ApiPainel(microfone_evento, audio_config, fila_voz, niveis)
    janela = webview.create_window(
        "Beatriz", url=PAGINA, js_api=api, width=1440, height=900, min_size=(1100, 700),
        background_color=FUNDO, text_select=True, maximized=True,
    )
    api._janela = janela
    if MASCOTE_NA_TELA:
        from mascote_tela import MascoteDaTela

        api._mascote = MascoteDaTela(enviar_pedido=api.enviar, janela_painel=janela)
        api._mascote.criar_janela()

    def ao_mostrar():
        _ajustar_janela_windows(janela)
        if evento_pronto is not None:
            evento_pronto.set()

    janela.events.shown += ao_mostrar
    os.makedirs(PASTA_ARMAZENAMENTO, exist_ok=True)
    webview.start(gui="edgechromium", private_mode=False, storage_path=PASTA_ARMAZENAMENTO)


if __name__ == "__main__":
    iniciar()
