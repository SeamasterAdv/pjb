import ctypes
import os
import queue
import threading
import time
import tkinter as tk
import urllib.request
import json
from datetime import datetime
from tkinter import filedialog, ttk

import camadas_personalidade
import memory
from agente_agenda import criar_agente_agenda
from agente_controle import criar_agente_controle
from agente_craft import AgenteCraft
from agente_impressora import criar_agente_impressora, texto_da_agenda
from agente_leis import AgenteLeis
from agente_prazos import criar_agente_prazos
from agente_pastas import AgentePastas
from fontes import criar_agente_fontes
from leitura_tela import AgenteTela
from llm_clients import llm_clients
from agentes import PainelCompartilhado, criar_orquestrador
from cinema import responder_pedido_de_cinema
from jeito_de_falar import frase
from resumo_explicativo import gerar_resumo, preparar_pedido_de_resumo
from llm_nuvem import claude, gemini
from config import (
    ARQUIVO_MEMORIA_USUARIO,
    AUTO_CARREGAR_FONTES,
    BEATRIZ_API_KEY,
    GMAIL_TOKEN_FILE,
    MODOS_PASTAS,
    OLLAMA_HOST,
    OLLAMA_MODEL,
    OLLAMA_VISION_MODEL,
)
from memory import importar_fonte_local, notelooklm_responder
from painel_hud import (
    HUD_AMBAR,
    HUD_BORDA,
    HUD_CARTAO,
    HUD_DESTAQUE,
    HUD_DESTAQUE_CLARO,
    HUD_FUNDO,
    HUD_LILAS,
    HUD_PAINEL,
    HUD_SUAVE,
    HUD_TEXTO,
    HUD_VERDE,
    HUD_VERMELHO,
    CabecalhoHUD,
    GraficoBarras,
    MedidorCircular,
    MonitorRecursos,
    NucleoHUD,
    PainelHUD,
    RedeBiblioteca,
    contar_linhas,
    foto_sol,
    grupos_da_biblioteca,
    ler_fatos_memoria,
    ler_metricas_analise,
)
from professora_idiomas import processar_comando_idiomas
from voice import (
    definir_microfone_ativo,
    descricao_da_voz,
    falar_async,
    listar_microfones,
    listar_saidas_audio,
    microfone_ativo,
    microfone_disponivel,
    microfone_selecionado,
    ouvir,
    responder_troca_de_voz,
    saida_audio_selecionada,
    selecionar_microfone,
    selecionar_saida_audio,
)

_JANELA_CARREGAMENTO = None

# Ícone das janelas e da barra de tarefas (gerado por assets/gerar_icone.py).
PASTA_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
ICONE_ICO = os.path.join(PASTA_ASSETS, "beatriz_icone.ico")
ICONE_PNG = os.path.join(PASTA_ASSETS, "beatriz_icone_256.png")
ID_APP_WINDOWS = "Beatriz.Assistente"


def definir_id_app_windows():
    """Separa a Beatriz do python.exe na barra de tarefas, para que ela mostre o próprio ícone.

    Precisa rodar antes de a janela aparecer.
    """
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ID_APP_WINDOWS)
    except (AttributeError, OSError):
        pass  # fora do Windows


def aplicar_icone(janela):
    """Usa o sol da Beatriz como ícone desta janela e das que ela abrir."""
    try:
        if os.name == "nt":
            janela.iconbitmap(default=ICONE_ICO)  # o .ico traz versões próprias de 16 a 256 px
        else:
            janela.iconphoto(True, tk.PhotoImage(master=janela, file=ICONE_PNG))
    except tk.TclError:
        pass  # sem o arquivo, a janela abre com o ícone padrão


def pintar_barra_de_titulo(janela):
    """No Windows 11, pinta a barra de título e a borda com as cores do painel (no 10 nada muda)."""
    def colorref(cor):  # o Windows guarda as cores como 0x00BBGGRR
        r, g, b = (int(cor[i:i + 2], 16) for i in (1, 3, 5))
        return ctypes.c_int(r | g << 8 | b << 16)

    try:
        hwnd = int(janela.wm_frame(), 16)
        # DWMWA_BORDER_COLOR (34), DWMWA_CAPTION_COLOR (35) e DWMWA_TEXT_COLOR (36)
        for atributo, cor in ((34, HUD_BORDA), (35, HUD_FUNDO), (36, HUD_TEXTO)):
            valor = colorref(cor)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, atributo, ctypes.byref(valor), ctypes.sizeof(valor))
    except (AttributeError, OSError, ValueError, tk.TclError):
        pass  # fora do Windows ou sem o DWM

# Paleta do painel (roxo e dourado, de identidade_visual.py)
COR_FUNDO = HUD_FUNDO
COR_PAINEL = HUD_PAINEL
COR_CARTAO = HUD_CARTAO
COR_BORDA = HUD_BORDA
COR_TEXTO = HUD_TEXTO
COR_SUAVE = HUD_SUAVE
COR_DESTAQUE = HUD_DESTAQUE
COR_LILAS = HUD_LILAS
COR_VERDE = HUD_VERDE
COR_AMBAR = HUD_AMBAR
COR_VERMELHO = HUD_VERMELHO
COR_REALCE = "#3D2861"  # botão sob o mouse e aba ativa
COR_SELECAO = "#4A3275"
COR_CODIGO = "#150C25"

FONTE_TEXTO = ("Segoe UI", 10)
FONTE_MONO = ("Consolas", 10)  # só nos blocos de código
FONTE_PEQUENA = ("Segoe UI", 9)

PLACEHOLDER_ENTRADA = "Digite um comando ou pergunta...  (Enter envia · Shift+Enter quebra linha)"

ATALHOS_RAPIDOS = (
    ("Apresentação", "Crie uma apresentação com 8 slides sobre "),
    ("Word", "Crie um documento em Word sobre "),
    ("PDF + pesquisa", "Pesquise e faça um PDF sobre "),
    ("Planilha Excel", "Crie uma planilha em Excel com gráfico de "),
    ("Estudos", "Crie 15 flashcards e 8 questões sobre "),
    ("Minha agenda", "O que tenho hoje?"),
    ("Marcar compromisso", "Marca "),
    ("Organizar o dia", "Organiza meu dia"),
    ("Minhas tarefas", "Quais minhas tarefas?"),
    ("Resumo do dia", "Qual o resumo do dia?"),
    ("Meus e-mails", "Resuma meus e-mails e rascunhe as respostas."),
    ("Minha memória", "Resuma o que você sabe sobre mim."),
    ("Documentos", "O que meus documentos dizem sobre "),
    ("Aula de inglês", "Quero uma aula de inglês."),
)

CORES_STATUS_TAREFA = {
    "na fila": COR_SUAVE,
    "executando": COR_AMBAR,
    "revisando": COR_AMBAR,
    "relatando": COR_AMBAR,
    "concluída": COR_VERDE,
    "falhou": COR_VERMELHO,
    "cancelada": COR_SUAVE,
    "não realizada": COR_AMBAR,  # falta configurar algo (ex.: vincular o Gmail)
}


class JanelaCarregamento(tk.Tk):
    def __init__(self, evento_fechamento=None):
        super().__init__()
        global _JANELA_CARREGAMENTO
        _JANELA_CARREGAMENTO = self

        self.title("Beatriz - iniciando")
        self.configure(bg=COR_BORDA)
        self.resizable(False, False)
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        estilo = ttk.Style(self)
        try:
            estilo.theme_use("clam")
        except Exception:
            pass
        estilo.configure(
            "Carregando.Horizontal.TProgressbar",
            troughcolor=COR_CARTAO,
            background=COR_DESTAQUE,
            bordercolor=COR_CARTAO,
            lightcolor=COR_DESTAQUE,
            darkcolor=COR_DESTAQUE,
        )

        frame = tk.Frame(self, bg=COR_FUNDO, padx=40, pady=24)
        frame.pack(fill="both", expand=True, padx=1, pady=1)

        self._sol = foto_sol(96)
        tk.Label(frame, image=self._sol, bg=COR_FUNDO).pack(anchor="center")
        tk.Label(frame, text="Beatriz", font=("Segoe UI Semibold", 22), fg=HUD_DESTAQUE_CLARO, bg=COR_FUNDO).pack(anchor="center")
        tk.Label(frame, text="sua assistente pessoal está acordando", font=FONTE_PEQUENA, fg=COR_SUAVE, bg=COR_FUNDO).pack(anchor="center", pady=(0, 14))

        self.status = tk.Label(frame, text="Inicializando módulos...", font=FONTE_TEXTO, fg=COR_TEXTO, bg=COR_FUNDO)
        self.status.pack(anchor="center", pady=(0, 12))

        self.progress = ttk.Progressbar(
            frame, orient="horizontal", length=320, mode="indeterminate", style="Carregando.Horizontal.TProgressbar"
        )
        self.progress.pack(anchor="center")
        self.progress.start(18)
        self.evento_fechamento = evento_fechamento
        self.after(100, self._verificar_fechamento)

        self.update_idletasks()
        largura = self.winfo_reqwidth()
        altura = self.winfo_reqheight()
        x = (self.winfo_screenwidth() // 2) - (largura // 2)
        y = (self.winfo_screenheight() // 2) - (altura // 2)
        self.geometry(f"{largura}x{altura}+{x}+{y}")

    def _verificar_fechamento(self):
        if self.evento_fechamento is not None and self.evento_fechamento.is_set():
            self.fechar()
            return
        if self.winfo_exists():
            self.after(100, self._verificar_fechamento)

    def atualizar_status(self, texto):
        if self.winfo_exists():
            self.status.config(text=texto)
            self.update_idletasks()

    def fechar(self):
        try:
            self.progress.stop()
        except Exception:
            pass
        if self.winfo_exists():
            self.destroy()


def iniciar_tela_carregamento_em_background(evento_fechamento=None):
    global _JANELA_CARREGAMENTO
    if _JANELA_CARREGAMENTO is not None and _JANELA_CARREGAMENTO.winfo_exists():
        return _JANELA_CARREGAMENTO

    janela = JanelaCarregamento(evento_fechamento=evento_fechamento)
    janela.mainloop()
    return janela


def fechar_tela_carregamento():
    global _JANELA_CARREGAMENTO
    if _JANELA_CARREGAMENTO is not None and _JANELA_CARREGAMENTO.winfo_exists():
        _JANELA_CARREGAMENTO.fechar()
    _JANELA_CARREGAMENTO = None


def verificar_ollama(timeout=2):
    """Consulta o Ollama e devolve (online, modelo_carregado)."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST.rstrip('/')}/api/tags", timeout=timeout) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))
        nomes = {modelo.get("name", "") for modelo in dados.get("models", [])}
        modelo_ok = OLLAMA_MODEL in nomes or f"{OLLAMA_MODEL}:latest" in nomes
        return True, modelo_ok
    except Exception:
        return False, False


class JanelaComandos(tk.Tk):
    def __init__(self, microfone_evento=None, audio_config=None, fila_voz=None):
        definir_id_app_windows()
        super().__init__()
        aplicar_icone(self)
        self.microfone_evento = microfone_evento
        self.audio_config = audio_config
        self.fila_voz = fila_voz  # o que a voz ouviu e respondeu, vindo do processo principal
        self._voz_pensando_desde = 0.0
        self.title("Beatriz")
        self.geometry("1440x900")
        self.minsize(1180, 720)
        self.configure(bg=COR_FUNDO)
        try:
            self.state("zoomed")  # o painel HUD aproveita a tela inteira
        except tk.TclError:
            pass
        self._monitor = MonitorRecursos()
        self._ollama_online = None
        self._camera_status = ("não verificada", COR_SUAVE)

        self._historico = []  # (autor, hora, texto)
        self._historico_comandos = []
        self._indice_historico = None
        self._ultima_resposta = ""
        self._processando = False
        self._quadro_animacao = 0
        self._inicio_processamento = 0.0
        self._total_respostas = 0
        self._soma_tempos = 0.0
        # Tarefas dos agentes: as do painel e as pedidas por voz aparecem juntas no quadro "Agentes",
        # porque os dois processos publicam no mesmo dicionário compartilhado.
        self._dados_tarefas = audio_config if audio_config is not None else {}
        self.orquestrador = criar_orquestrador(publicar=PainelCompartilhado(self._dados_tarefas))
        self.agenda = criar_agente_agenda(llm=None)  # a mesma agenda local da voz (mesmo arquivo)
        self.prazos = criar_agente_prazos(agenda=lambda: self.agenda.agenda)  # "quando vence a contestação?"
        # artigos de lei e súmulas (vade-mécum do PC): texto oficial e explicação pelo Gemini
        self.leis = AgenteLeis(
            avisar=lambda texto: self.after(0, lambda: self.adicionar_mensagem("Beatriz", texto, tag="beatriz")))
        # "me chama de…", "fala mais curto": camadas de personalidade (o mesmo arquivo da voz e do painel web)
        self.camadas = camadas_personalidade.AgenteCamadas(llm=llm_clients)
        self.pastas = AgentePastas()  # "abre a pasta da OAB"
        threading.Thread(target=self.pastas.aquecer, daemon=True).start()
        self.tela = AgenteTela(llm=llm_clients, avisar=lambda texto: falar_async(texto))  # lê a janela ativa, não esta
        self.impressora = criar_agente_impressora(
            respostas_recentes=lambda: [self._ultima_resposta],
            texto_agenda=lambda t: texto_da_agenda(self.agenda, t),
            avisar=lambda texto: self.after(0, lambda: self.adicionar_mensagem("Beatriz", texto, tag="status")),
        )
        # TV e ar pelo Ekaza (o mesmo arquivo de códigos e estado da voz); o fim do aprender aparece no chat.
        self.controle = criar_agente_controle(
            avisar=lambda texto: self.after(0, lambda: self.adicionar_mensagem("Beatriz", texto, tag="beatriz")))
        # Arquivos recebidos pelo WhatsApp (no processo principal; o registro é o mesmo arquivo): vira fonte, arquiva, imprime, escaneia.
        self.fontes = criar_agente_fontes(avisar=lambda texto, canal: self.after(0, lambda: self.adicionar_mensagem("Beatriz", texto, tag="beatriz")),
                                          enviar_zap=None, impressora=self.impressora, pastas=self.pastas, llm=llm_clients,
                                          estudar=self._estudar_arquivos, peca=self._peca_de_arquivos)
        # Apps da ArtCraft: "abre o PhotoCraft", "converte esse Word em PDF", "junta esses PDFs" (o resultado abre no PC).
        self.craft = AgenteCraft(recebidos=self.fontes.recebidos_do_pedido, arquivos=self.pastas.arquivos,
                                 avisar=lambda resposta, canal: self.after(0, lambda: self.adicionar_mensagem(
                                     "Beatriz", resposta.texto, tag="beatriz")))  # o vídeo editado chega depois

        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        self.configure_padrao()
        self.criar_widgets()
        self.atualizar_relogio()
        self.monitorar_ollama()
        self.atualizar_recursos()
        self.atualizar_painel_dados()
        self.atualizar_agenda_e_clima()
        self.atualizar_tarefas()
        self.acompanhar_voz()
        self.after(100, lambda: pintar_barra_de_titulo(self))  # a moldura da janela só existe depois de aparecer

    # ------------------------------------------------------------------ agentes
    def atualizar_tarefas(self):
        """Redesenha o quadro "Agentes" quando alguma tarefa muda de etapa."""
        try:
            tarefas = PainelCompartilhado.listar(self._dados_tarefas)[:4]
            assinatura = [(t["id"], t["status"], t["etapa"], t.get("nota")) for t in tarefas]
            if assinatura != self._assinatura_tarefas:
                self._assinatura_tarefas = assinatura
                self._desenhar_tarefas(tarefas)
            self.after(1500, self.atualizar_tarefas)
        except tk.TclError:
            pass  # janela fechada

    def _desenhar_tarefas(self, tarefas):
        for filho in self.lista_tarefas.winfo_children():
            filho.destroy()
        ativas = sum(1 for t in tarefas if t["status"] in ("na fila", "executando", "revisando", "relatando"))
        self.painel_agentes.definir_destaque(f"· {ativas} em andamento" if ativas else "· livres",
                                             COR_AMBAR if ativas else COR_VERDE)
        if not tarefas:
            tk.Label(self.lista_tarefas, text="Peça um documento, apresentação, planilha,\nmaterial de estudo ou o resumo do dia.",
                     font=("Segoe UI", 8), fg=COR_SUAVE, bg=COR_PAINEL, justify="left").pack(anchor="w")
            return
        for tarefa in tarefas:
            cor = CORES_STATUS_TAREFA.get(tarefa["status"], COR_SUAVE)
            linha = tk.Frame(self.lista_tarefas, bg=COR_PAINEL, cursor="hand2")
            linha.pack(fill="x", pady=(0, 4))
            topo = tk.Frame(linha, bg=COR_PAINEL)
            topo.pack(fill="x")
            tk.Label(topo, text="●", font=("Segoe UI", 8), fg=cor, bg=COR_PAINEL).pack(side="left")
            pedido = tarefa["pedido"] if len(tarefa["pedido"]) <= 40 else tarefa["pedido"][:39] + "…"
            tk.Label(topo, text=pedido, font=("Segoe UI", 8), fg=COR_TEXTO, bg=COR_PAINEL, anchor="w").pack(side="left", padx=(4, 0))
            if tarefa["etapa"]:
                detalhe = f"{tarefa['status']} · {tarefa['etapa']}"
            else:
                partes = [tarefa["status"]]
                if tarefa.get("nota") is not None:
                    partes.append(f"nota {tarefa['nota']}/10")
                if tarefa.get("arquivos"):
                    partes.append(", ".join(os.path.splitext(a)[1].lstrip(".").upper() for a in tarefa["arquivos"]))
                detalhe = " · ".join(partes)
            tk.Label(linha, text=f"   {detalhe}", font=("Segoe UI", 8), fg=cor, bg=COR_PAINEL, anchor="w").pack(fill="x")
            if tarefa.get("pasta"):
                for widget in (linha, *linha.winfo_children(), *topo.winfo_children()):
                    widget.bind("<Button-1>", lambda e, pasta=tarefa["pasta"]: self._abrir_pasta_tarefa(pasta))

    def _abrir_pasta_tarefa(self, pasta):
        if os.path.isdir(pasta):
            os.startfile(pasta)
        else:
            self.definir_status("A pasta desta tarefa ainda não foi criada", erro=True)

    def _estudar_arquivos(self, caminhos, pedido, canal="painel", ia=None):
        """"Faz uma ficha desse PDF" sobre um arquivo recebido: a ficha tática dos agentes, escrita pelo Claude."""
        tarefa = self.orquestrador.enviar_estudo_de_arquivos(
            pedido, caminhos, ia=ia, ao_concluir=lambda t: self.after(0, lambda: self._tarefa_concluida(t)))
        return self.orquestrador.mensagem_inicio(tarefa) if tarefa else None

    def _peca_de_arquivos(self, caminhos, pedido, canal="painel", ia=None):
        """"Faz a peça desse caso" sobre um arquivo recebido: o agente de peças, com o arquivo como base."""
        from agentes.pecas import pedido_de_peca

        tarefa = self.orquestrador.enviar_peca(
            pedido_de_peca(pedido) or "", pedido, caminhos, ia=ia, pedido=pedido,
            ao_concluir=lambda t: self.after(0, lambda: self._tarefa_concluida(t)))
        return self.orquestrador.mensagem_inicio(tarefa) if tarefa else None

    def _tarefa_concluida(self, tarefa):
        self.adicionar_mensagem("Beatriz", tarefa.mensagem_final, tag="status" if tarefa.status == "falhou" else "beatriz")
        if tarefa.relatorio:
            self.adicionar_mensagem("Beatriz", f"Pasta da tarefa: {tarefa.pasta}", tag="status")
        self._ultima_resposta = tarefa.mensagem_final
        falar_async(tarefa.mensagem_final)

    # ------------------------------------------------------------------ estilo
    def configure_padrao(self):
        s = self.style
        s.configure(".", background=COR_PAINEL, foreground=COR_TEXTO, font=FONTE_TEXTO)
        s.configure("TFrame", background=COR_PAINEL)
        s.configure("Fundo.TFrame", background=COR_FUNDO)
        s.configure("TLabel", background=COR_PAINEL, foreground=COR_TEXTO, font=FONTE_TEXTO)
        s.configure("Secao.TLabel", background=COR_PAINEL, foreground=COR_DESTAQUE, font=("Segoe UI Semibold", 10))
        s.configure(
            "Botao.TButton",
            background=COR_CARTAO,
            foreground=COR_TEXTO,
            bordercolor=COR_BORDA,
            lightcolor=COR_CARTAO,
            darkcolor=COR_CARTAO,
            focuscolor=COR_CARTAO,
            font=FONTE_TEXTO,
            padding=(10, 6),
            anchor="w",
        )
        s.map(
            "Botao.TButton",
            background=[("disabled", COR_PAINEL), ("pressed", COR_BORDA), ("active", COR_REALCE)],
            foreground=[("disabled", COR_SUAVE)],
            bordercolor=[("active", COR_DESTAQUE)],
        )
        s.configure(
            "Enviar.TButton",
            background=COR_DESTAQUE,
            foreground=COR_FUNDO,
            bordercolor=COR_DESTAQUE,
            lightcolor=COR_DESTAQUE,
            darkcolor=COR_DESTAQUE,
            focuscolor=COR_DESTAQUE,
            font=("Segoe UI", 10, "bold"),
            padding=(18, 10),
        )
        s.map(
            "Enviar.TButton",
            background=[("disabled", COR_BORDA), ("active", HUD_DESTAQUE_CLARO)],
            foreground=[("disabled", COR_SUAVE)],
        )
        s.configure(
            "Atalho.TButton",
            background=COR_PAINEL,
            foreground=COR_DESTAQUE,
            bordercolor=COR_PAINEL,
            lightcolor=COR_PAINEL,
            darkcolor=COR_PAINEL,
            focuscolor=COR_PAINEL,
            font=("Segoe UI", 9),
            padding=(6, 3),
            anchor="w",
        )
        s.map("Atalho.TButton", background=[("active", COR_CARTAO)])
        s.configure(
            "TCombobox",
            fieldbackground=COR_CARTAO,
            background=COR_CARTAO,
            foreground=COR_TEXTO,
            arrowcolor=COR_DESTAQUE,
            bordercolor=COR_BORDA,
            lightcolor=COR_CARTAO,
            darkcolor=COR_CARTAO,
        )
        s.map(
            "TCombobox",
            fieldbackground=[("readonly", COR_CARTAO)],
            foreground=[("readonly", COR_TEXTO)],
            selectbackground=[("readonly", COR_CARTAO)],
            selectforeground=[("readonly", COR_TEXTO)],
        )
        self.option_add("*TCombobox*Listbox.background", COR_CARTAO)
        self.option_add("*TCombobox*Listbox.foreground", COR_TEXTO)
        self.option_add("*TCombobox*Listbox.selectBackground", COR_DESTAQUE)
        self.option_add("*TCombobox*Listbox.selectForeground", COR_FUNDO)
        s.map("Vertical.TScrollbar", background=[("active", COR_BORDA), ("!active", COR_CARTAO)])
        s.configure(
            "Vertical.TScrollbar",
            background=COR_CARTAO,
            troughcolor=COR_FUNDO,
            bordercolor=COR_FUNDO,
            arrowcolor=COR_SUAVE,
            lightcolor=COR_CARTAO,
            darkcolor=COR_CARTAO,
            gripcount=0,
        )

    # ----------------------------------------------------------------- layout
    def criar_widgets(self):
        self.cabecalho_hud = CabecalhoHUD(self)
        self.cabecalho_hud.pack(fill="x")
        self._criar_abas_modos()

        self._criar_barra_status()

        corpo = tk.Frame(self, bg=COR_FUNDO, padx=12, pady=6)
        corpo.pack(fill="both", expand=True)
        corpo.grid_columnconfigure(1, weight=1)
        corpo.grid_rowconfigure(0, weight=1)

        esquerda = tk.Frame(corpo, bg=COR_FUNDO, width=290)
        esquerda.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        esquerda.pack_propagate(False)
        centro = tk.Frame(corpo, bg=COR_FUNDO)
        centro.grid(row=0, column=1, sticky="nsew")
        direita = tk.Frame(corpo, bg=COR_FUNDO, width=320)
        direita.grid(row=0, column=2, sticky="ns", padx=(10, 0))
        direita.pack_propagate(False)

        self._criar_coluna_esquerda(esquerda)
        self._criar_coluna_direita(direita)

        painel_nucleo = PainelHUD(centro, "Núcleo", "· online", HUD_VERDE)
        painel_nucleo.pack(fill="x")
        self.painel_nucleo = painel_nucleo
        painel_nucleo.corpo.configure(padx=0, pady=0)
        self.nucleo = NucleoHUD(painel_nucleo.corpo, height=250)
        self.nucleo.pack(fill="x")
        for chave, rotulo, valor in (
            ("modelo", "Modelo", OLLAMA_MODEL),
            ("interacoes", "Interações", "--"),
            ("tempo", "Tempo médio", "--"),
            ("modo", "Modo", memory.MODO_CONVERSA_ATUAL),
            ("trechos", "Biblioteca", "--"),
            ("sucesso", "Taxa de sucesso", "--"),
        ):
            self.nucleo.definir_legenda(chave, rotulo, valor)

        painel_chat = PainelHUD(centro, "Conversa")
        painel_chat.pack(fill="both", expand=True, pady=(8, 0))
        painel_chat.corpo.configure(bg=COR_FUNDO, padx=0, pady=0)
        self._criar_entrada(painel_chat.corpo)
        self._criar_area_chat(painel_chat.corpo)

        self._criar_janela_audio()
        self.adicionar_mensagem(
            "Beatriz",
            "Painel pronto. Escreva comandos, use os atalhos ao lado ou anexe arquivos para a biblioteca.",
            tag="beatriz",
        )

    def _criar_abas_modos(self):
        """Abas com os modos de conversa; a aba acesa é o modo em uso."""
        barra = tk.Frame(self, bg=COR_FUNDO, padx=14)
        barra.pack(fill="x")
        self._abas_modos = {}
        for modo in MODOS_PASTAS:
            aba = tk.Label(
                barra, text=modo.capitalize(), font=("Segoe UI", 9), padx=14, pady=3, cursor="hand2",
                bg=COR_CARTAO, fg=COR_SUAVE, highlightthickness=1, highlightbackground=COR_BORDA,
            )
            aba.pack(side="left", padx=(0, 4))
            aba.bind("<Button-1>", lambda e, m=modo: self.escolher_modo(m))
            self._abas_modos[modo] = aba
        tk.Label(barra, text="modo de conversa", font=("Segoe UI", 8), fg=COR_SUAVE, bg=COR_FUNDO).pack(side="left", padx=8)
        self._marcar_aba_modo(memory.MODO_CONVERSA_ATUAL)

    def _marcar_aba_modo(self, modo_ativo):
        for modo, aba in self._abas_modos.items():
            ativo = modo == modo_ativo
            aba.configure(
                bg=COR_REALCE if ativo else COR_CARTAO,
                fg=COR_DESTAQUE if ativo else COR_SUAVE,
                highlightbackground=COR_DESTAQUE if ativo else COR_BORDA,
            )

    def escolher_modo(self, modo):
        resultado = memory.mudar_modo_conversa(modo)
        if self.audio_config is not None:
            self.audio_config["modo_pedido"] = modo  # o processo principal aplica o mesmo modo na voz
        self._marcar_aba_modo(modo)
        self.nucleo.definir_legenda("modo", "Modo", modo)
        self.definir_status(resultado)

    def _criar_linha_sistema(self, pai, rotulo, valor, cor):
        linha = tk.Frame(pai, bg=COR_PAINEL)
        linha.pack(fill="x", pady=1)
        ponto = tk.Label(linha, text="●", font=("Segoe UI", 8), fg=cor, bg=COR_PAINEL)
        ponto.pack(side="left")
        tk.Label(linha, text=rotulo, font=("Segoe UI", 9), fg=COR_SUAVE, bg=COR_PAINEL, width=10, anchor="w").pack(side="left", padx=(4, 0))
        texto = tk.Label(linha, text=valor, font=("Segoe UI", 9), fg=COR_TEXTO, bg=COR_PAINEL, anchor="e")
        texto.pack(side="right")
        return ponto, texto

    def _definir_indicador(self, indicador, valor, cor):
        ponto, texto = indicador
        ponto.configure(fg=cor)
        texto.configure(text=valor)

    def _criar_coluna_esquerda(self, coluna):
        sistemas = PainelHUD(coluna, "Sistemas")
        sistemas.pack(fill="x")
        c = sistemas.corpo
        self.indicador_llm = self._criar_linha_sistema(c, "LLM", "verificando...", COR_AMBAR)
        self._criar_linha_sistema(c, "Visão", OLLAMA_VISION_MODEL, COR_DESTAQUE)
        self.indicador_claude = self._criar_linha_sistema(c, "Claude", "verificando...", COR_AMBAR)
        self.indicador_gemini = self._criar_linha_sistema(c, "Gemini", "verificando...", COR_AMBAR)
        self.indicador_whatsapp = self._criar_linha_sistema(c, "WhatsApp", "desligado", COR_SUAVE)
        self.indicador_mic = self._criar_linha_sistema(c, "Microfone", "ativo", COR_VERDE)
        self.indicador_saida = self._criar_linha_sistema(c, "Saída", "padrão", COR_DESTAQUE)
        self.indicador_voz = self._criar_linha_sistema(c, "Voz", descricao_da_voz(), COR_DESTAQUE)
        self.indicador_camera = self._criar_linha_sistema(c, "Câmera", *self._camera_status)
        self.indicador_agenda = self._criar_linha_sistema(c, "Agenda", "verificando...", COR_AMBAR)
        gmail = os.path.exists(GMAIL_TOKEN_FILE)
        self._criar_linha_sistema(c, "Gmail", "vinculado" if gmail else "não vinculado", COR_VERDE if gmail else COR_SUAVE)
        self._criar_linha_sistema(c, "API local", "ativa" if BEATRIZ_API_KEY else "sem chave", COR_VERDE if BEATRIZ_API_KEY else COR_SUAVE)
        self._criar_linha_sistema(c, "Indexação", "automática" if AUTO_CARREGAR_FONTES else "manual", COR_VERDE if AUTO_CARREGAR_FONTES else COR_AMBAR)

        recursos = PainelHUD(coluna, "Recursos")
        recursos.pack(fill="x", pady=(8, 0))
        medidores = tk.Frame(recursos.corpo, bg=COR_PAINEL)
        medidores.pack(fill="x")
        self.medidor_cpu = MedidorCircular(medidores, "CPU")
        self.medidor_cpu.pack(side="left", expand=True)
        self.medidor_ram = MedidorCircular(medidores, "RAM")
        self.medidor_ram.pack(side="left", expand=True)
        self.ram_var = tk.StringVar(value="")
        tk.Label(recursos.corpo, textvariable=self.ram_var, font=("Segoe UI", 8), fg=COR_SUAVE, bg=COR_PAINEL).pack(anchor="center")
        self.grafico_cpu = GraficoBarras(recursos.corpo, quantidade=30, altura=44)
        self.grafico_cpu.pack(fill="x", pady=(4, 0))

        controles = PainelHUD(coluna, "Controles")
        controles.pack(fill="both", expand=True, pady=(8, 0))
        grade = tk.Frame(controles.corpo, bg=COR_PAINEL)
        grade.pack(fill="x")
        grade.grid_columnconfigure((0, 1), weight=1, uniform="botoes")
        self.botao_microfone = ttk.Button(grade, text="🎙 Mic: ativo", command=self.alternar_microfone, style="Botao.TButton")
        self.botao_camera = ttk.Button(grade, text="📹 Câmera", command=self.abrir_camera, style="Botao.TButton")
        self.botao_anexar = ttk.Button(grade, text="📎 Anexar", command=self.anexar_arquivos, style="Botao.TButton")
        self.botao_audio = ttk.Button(grade, text="⚙ Áudio", command=self.alternar_painel_audio, style="Botao.TButton")
        botao_copiar = ttk.Button(grade, text="📋 Copiar", command=self.copiar_ultima_resposta, style="Botao.TButton")
        botao_exportar = ttk.Button(grade, text="💾 Exportar", command=self.exportar_conversa, style="Botao.TButton")
        self.botao_limpar = ttk.Button(grade, text="🧹 Limpar", command=self.limpar_chat, style="Botao.TButton")
        botoes = (self.botao_microfone, self.botao_camera, self.botao_anexar, self.botao_audio, botao_copiar, botao_exportar, self.botao_limpar)
        for indice, botao in enumerate(botoes):
            botao.grid(row=indice // 2, column=indice % 2, sticky="ew", padx=2, pady=2)

        tk.Label(controles.corpo, text="Categoria dos anexos", font=("Segoe UI", 8), fg=COR_SUAVE, bg=COR_PAINEL).pack(anchor="w", pady=(6, 2))
        self.categoria_var = tk.StringVar(value="auto")
        self.categoria = ttk.Combobox(
            controles.corpo,
            textvariable=self.categoria_var,
            values=("auto", "geral", "comunicacao", "filosofia", "psicologia", "direito", "projetos", "procedimentos", "referencias", "idiomas"),
            state="readonly",
        )
        self.categoria.pack(fill="x")

    def _criar_coluna_direita(self, coluna):
        self.painel_agenda = PainelHUD(coluna, "Agenda")
        self.painel_agenda.pack(fill="x")
        self.agenda_var = tk.StringVar(value="Carregando compromissos...")
        tk.Label(
            self.painel_agenda.corpo, textvariable=self.agenda_var, font=("Segoe UI", 9), fg=COR_TEXTO, bg=COR_PAINEL,
            justify="left", anchor="w", wraplength=286,
        ).pack(fill="x")
        self.clima_var = tk.StringVar(value="")
        self._rotulo_clima = tk.Label(
            self.painel_agenda.corpo, textvariable=self.clima_var, font=("Segoe UI", 9), fg=COR_AMBAR, bg=COR_PAINEL,
            justify="left", anchor="w", wraplength=286,
        )
        self._rotulo_clima.pack(fill="x", pady=(4, 0))

        self.painel_biblioteca = PainelHUD(coluna, "Biblioteca")
        self.painel_biblioteca.pack(fill="x", pady=(8, 0))
        self.biblioteca_var = tk.StringVar(value="--")
        tk.Label(self.painel_biblioteca.corpo, textvariable=self.biblioteca_var, font=("Segoe UI", 9), fg=COR_TEXTO,
                 bg=COR_PAINEL, justify="left", anchor="w").pack(fill="x")
        self.rede_biblioteca = RedeBiblioteca(self.painel_biblioteca.corpo, altura=104)
        self.rede_biblioteca.pack(fill="x", pady=(4, 0))

        self.painel_memoria = PainelHUD(coluna, "Memória do usuário")
        self.painel_memoria.pack(fill="x", pady=(8, 0))
        self.memoria_var = tk.StringVar(value="--")
        tk.Label(self.painel_memoria.corpo, textvariable=self.memoria_var, font=("Segoe UI", 9), fg=COR_TEXTO,
                 bg=COR_PAINEL, justify="left", anchor="w", wraplength=286).pack(fill="x")

        self.painel_agentes = PainelHUD(coluna, "Agentes")
        self.painel_agentes.pack(fill="x", pady=(8, 0))
        self.lista_tarefas = tk.Frame(self.painel_agentes.corpo, bg=COR_PAINEL)
        self.lista_tarefas.pack(fill="x")
        self._assinatura_tarefas = None

        atalhos = PainelHUD(coluna, "Atalhos")
        atalhos.pack(fill="both", expand=True, pady=(8, 0))
        grade = tk.Frame(atalhos.corpo, bg=COR_PAINEL)
        grade.pack(fill="x")
        grade.grid_columnconfigure((0, 1), weight=1, uniform="atalhos")
        for indice, (rotulo, texto) in enumerate(ATALHOS_RAPIDOS):
            ttk.Button(
                grade, text=f"› {rotulo}", style="Atalho.TButton", command=lambda t=texto: self.usar_atalho(t)
            ).grid(row=indice // 2, column=indice % 2, sticky="ew")

    def _criar_janela_audio(self):
        # Janela própria para os dispositivos, para não espremer as colunas.
        self.janela_audio = tk.Toplevel(self, bg=COR_PAINEL)
        self.janela_audio.title("Dispositivos de áudio")
        self.janela_audio.resizable(False, False)
        self.janela_audio.transient(self)
        self.janela_audio.protocol("WM_DELETE_WINDOW", self.janela_audio.withdraw)
        self.janela_audio.withdraw()
        self.painel_audio = ttk.Frame(self.janela_audio, padding=16, width=340)
        self.painel_audio.pack(fill="both", expand=True)

        ttk.Label(self.painel_audio, text="Dispositivos de áudio", style="Secao.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(self.painel_audio, text="Microfone", foreground=COR_SUAVE, font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))
        self.microfone_var = tk.StringVar()
        self.microfone_combo = ttk.Combobox(self.painel_audio, textvariable=self.microfone_var, state="readonly", width=42)
        self.microfone_combo.pack(fill="x", pady=(2, 6))
        ttk.Label(self.painel_audio, text="Fone/saída", foreground=COR_SUAVE, font=("Segoe UI", 9)).pack(anchor="w")
        self.saida_audio_var = tk.StringVar()
        self.saida_audio_combo = ttk.Combobox(self.painel_audio, textvariable=self.saida_audio_var, state="readonly", width=42)
        self.saida_audio_combo.pack(fill="x", pady=(2, 6))

        botoes_audio = ttk.Frame(self.painel_audio)
        botoes_audio.pack(fill="x")
        ttk.Button(botoes_audio, text="Atualizar", command=self.atualizar_dispositivos_audio, style="Botao.TButton").pack(side="left", expand=True, fill="x", padx=(0, 3))
        ttk.Button(botoes_audio, text="Aplicar", command=self.aplicar_dispositivos_audio, style="Botao.TButton").pack(side="left", expand=True, fill="x", padx=(3, 0))

        self._microfones_por_nome = {}
        self.atualizar_dispositivos_audio()

    def alternar_painel_audio(self):
        if self.janela_audio.winfo_viewable():
            self.janela_audio.withdraw()
            return
        self.atualizar_dispositivos_audio()
        self.update_idletasks()
        x = self.winfo_rootx() + 260
        y = self.winfo_rooty() + max(80, self.winfo_height() - 300)
        self.janela_audio.geometry(f"+{x}+{y}")
        self.janela_audio.deiconify()
        pintar_barra_de_titulo(self.janela_audio)
        self.janela_audio.lift()

    def _criar_area_chat(self, principal):
        self.digitando_var = tk.StringVar(value="")
        tk.Label(principal, textvariable=self.digitando_var, font=FONTE_PEQUENA, fg=COR_DESTAQUE, bg=COR_FUNDO).pack(side="bottom", anchor="w", pady=(6, 0))

        moldura = tk.Frame(principal, bg=COR_BORDA, padx=1, pady=1)
        moldura.pack(fill="both", expand=True)
        interno = tk.Frame(moldura, bg=COR_PAINEL)
        interno.pack(fill="both", expand=True)

        barra = ttk.Scrollbar(interno, orient="vertical", style="Vertical.TScrollbar")
        barra.pack(side="right", fill="y")
        self.chat = tk.Text(
            interno,
            wrap="word",
            bg=COR_PAINEL,
            fg=COR_TEXTO,
            insertbackground=COR_TEXTO,
            selectbackground=COR_SELECAO,
            font=FONTE_TEXTO,
            padx=16,
            pady=12,
            relief="flat",
            borderwidth=0,
            spacing1=2,
            spacing3=2,
            yscrollcommand=barra.set,
        )
        self.chat.pack(side="left", fill="both", expand=True)
        barra.configure(command=self.chat.yview)
        self.chat.configure(state="disabled")

        self.chat.tag_configure("cab_usuario", foreground=COR_LILAS, font=("Segoe UI", 9, "bold"), spacing1=10)
        self.chat.tag_configure("cab_beatriz", foreground=COR_DESTAQUE, font=("Segoe UI", 9, "bold"), spacing1=10)
        self.chat.tag_configure("cab_status", foreground=COR_AMBAR, font=("Segoe UI", 9, "bold"), spacing1=10)
        self.chat.tag_configure("hora", foreground=COR_SUAVE, font=FONTE_PEQUENA)
        self.chat.tag_configure("usuario", foreground=COR_TEXTO, lmargin1=14, lmargin2=14)
        self.chat.tag_configure("beatriz", foreground=COR_TEXTO, lmargin1=14, lmargin2=14)
        self.chat.tag_configure("status", foreground=COR_AMBAR, lmargin1=14, lmargin2=14, font=("Segoe UI", 9, "italic"))
        # Frases que o microfone entendeu, mas a Beatriz deixou passar (sem o nome, por exemplo).
        self.chat.tag_configure("cab_ignorado", foreground=COR_SUAVE, font=("Segoe UI", 8), spacing1=6)
        self.chat.tag_configure("ignorado", foreground=COR_SUAVE, lmargin1=14, lmargin2=14, font=("Segoe UI", 9, "italic"))
        self.chat.tag_configure(
            "codigo", font=FONTE_MONO, background=COR_CODIGO, foreground="#F3E3B5", lmargin1=24, lmargin2=24, rmargin=16
        )


    def _criar_entrada(self, principal):
        self.contador_var = tk.StringVar(value="0 caracteres · ↑/↓ histórico")
        tk.Label(principal, textvariable=self.contador_var, font=FONTE_PEQUENA, fg=COR_SUAVE, bg=COR_FUNDO).pack(side="bottom", anchor="e", pady=(4, 0))

        caixa = tk.Frame(principal, bg=COR_FUNDO)
        caixa.pack(side="bottom", fill="x", pady=(4, 0))

        moldura = tk.Frame(caixa, bg=COR_BORDA, padx=1, pady=1)
        moldura.pack(side="left", fill="both", expand=True, padx=(0, 10))
        self._moldura_entrada = moldura

        self.entrada = tk.Text(
            moldura,
            height=3,
            wrap="word",
            bg=COR_CARTAO,
            fg=COR_SUAVE,
            insertbackground=COR_DESTAQUE,
            font=FONTE_TEXTO,
            padx=10,
            pady=8,
            relief="flat",
            borderwidth=0,
        )
        self.entrada.insert("1.0", PLACEHOLDER_ENTRADA)
        self.entrada.pack(fill="both", expand=True)
        self.entrada.bind("<FocusIn>", self._limpar_placeholder)
        self.entrada.bind("<FocusOut>", self._restaurar_placeholder)
        self.entrada.bind("<Return>", self._ao_pressionar_enter)
        self.entrada.bind("<Control-Return>", lambda event: (self.enviar(), "break")[1])
        self.entrada.bind("<Up>", self._historico_anterior)
        self.entrada.bind("<Down>", self._historico_proximo)
        self.entrada.bind("<KeyRelease>", self._atualizar_contador)

        self.botao_enviar = ttk.Button(caixa, text="Enviar ➤", command=self.enviar, style="Enviar.TButton")
        self.botao_enviar.pack(side="right", fill="y")

    def _criar_barra_status(self):
        barra = tk.Frame(self, bg=COR_PAINEL, padx=14, pady=5)
        barra.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar(value="Pronta")
        tk.Label(barra, text="Status", font=("Segoe UI Semibold", 9), fg=COR_SUAVE, bg=COR_PAINEL).pack(side="left")
        self._rotulo_status = tk.Label(barra, textvariable=self.status_var, font=FONTE_PEQUENA, fg=COR_VERDE, bg=COR_PAINEL)
        self._rotulo_status.pack(side="left", padx=(8, 0))

        self.metricas_var = tk.StringVar(value=f"modelo {OLLAMA_MODEL} · 0 respostas")
        tk.Label(barra, textvariable=self.metricas_var, font=FONTE_PEQUENA, fg=COR_SUAVE, bg=COR_PAINEL).pack(side="right")

    # ------------------------------------------------------------- indicadores
    def atualizar_relogio(self):
        agora = datetime.now()
        dias = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
        self.cabecalho_hud.definir_relogio(agora.strftime("%H:%M:%S"), f"{dias[agora.weekday()]} · {agora:%d/%m/%Y}")
        self.after(1000, self.atualizar_relogio)

    def atualizar_recursos(self):
        cpu = self._monitor.cpu()
        ram, usada, total = self._monitor.ram()
        self.medidor_cpu.definir(cpu)
        self.medidor_ram.definir(ram)
        if total:
            self.ram_var.set(f"{usada:.1f} de {total:.1f} GB em uso")
        if cpu is not None:
            self.grafico_cpu.adicionar(cpu)
        self.after(1000, self.atualizar_recursos)

    def atualizar_painel_dados(self):
        """Lê, em segundo plano, biblioteca, memória e métricas gravadas pelo processo principal."""
        def coletar():
            dados = {"metricas": ler_metricas_analise()}
            dados["trechos"] = memory.total_de_trechos()  # pelo processo principal: o painel não abre o banco
            dados["grupos"] = grupos_da_biblioteca(
                memory.CAMINHO_MANIFESTO_MEMORIA,
                {"documento_local": memory.PASTA_DOCUMENTOS, "obsidian": memory.VAULT_OBSIDIAN},
            )
            dados["fatos"] = ler_fatos_memoria(ARQUIVO_MEMORIA_USUARIO)
            dados["conversas"] = contar_linhas(memory.ARQUIVO_HISTORICO_CONVERSAS)
            painel = dict(self.audio_config.get("painel") or {}) if self.audio_config is not None else {}
            self.after(0, lambda: self._aplicar_painel_dados(dados, painel))

        threading.Thread(target=coletar, daemon=True).start()
        self.after(5000, self.atualizar_painel_dados)

    def _aplicar_status_whatsapp(self):
        """Estado do canal de comandos pelo WhatsApp, publicado pelo processo do WhatsApp Web."""
        try:
            situacao = dict(self.audio_config.get("whatsapp") or {}) if self.audio_config is not None else {}
        except Exception:
            situacao = {}
        status = situacao.get("status", "desligado")
        cores = {"conectado": COR_VERDE, "aguardando QR": COR_AMBAR, "abrindo": COR_AMBAR, "conectando": COR_AMBAR,
                 "reconectando": COR_AMBAR, "erro": COR_VERMELHO}
        texto = {"aguardando QR": "leia o QR code"}.get(status, status)
        self._definir_indicador(self.indicador_whatsapp, texto[:24], cores.get(status, COR_SUAVE))

    def _aplicar_painel_dados(self, dados, painel):
        cores_nuvem = {"ativo": COR_VERDE, "pausado": COR_AMBAR, "sem_chave": COR_SUAVE, "desligado": COR_SUAVE}
        for indicador, provedor in ((self.indicador_claude, claude), (self.indicador_gemini, gemini)):
            texto, estado = provedor.status()
            self._definir_indicador(indicador, texto[:24], cores_nuvem.get(estado, COR_SUAVE))
        self._aplicar_status_whatsapp()
        metricas = dados["metricas"]
        if metricas:
            self.nucleo.definir_legenda("interacoes", "Interações", metricas["interacoes"])
            self.nucleo.definir_legenda("tempo", "Tempo médio", f"{metricas['tempo_medio']:.1f}s")
            self.nucleo.definir_legenda("sucesso", "Taxa de sucesso", f"{metricas['sucesso']:.0f}%")
        if dados["trechos"] is not None:
            self.nucleo.definir_legenda("trechos", "Biblioteca", f"{dados['trechos']} trechos")

        # Status da indexação vem do processo principal, que é quem indexa.
        indexacao = painel.get("indexacao") or {}
        estado = indexacao.get("status", "inativa")
        if estado == "inativa" and dados["trechos"]:
            estado = "pronta"  # nada indexando agora, mas a biblioteca já tem conteúdo
        cores = {"pronta": HUD_VERDE, "carregando": HUD_AMBAR, "falhou": HUD_VERMELHO}
        progresso = ""
        if estado == "carregando" and indexacao.get("total"):
            progresso = f" {indexacao.get('progresso', 0)}/{indexacao['total']}"
        self.painel_biblioteca.definir_destaque(f"· {estado}{progresso}", cores.get(estado, HUD_SUAVE))
        fontes = sum(dados["grupos"].values())
        self.biblioteca_var.set(
            f"{fontes} fontes · {dados['trechos'] or 0} trechos\n{dados['conversas']} conversas registradas"
        )
        self.rede_biblioteca.definir_grupos(dados["grupos"])

        total_fatos, recentes = dados["fatos"]
        texto = f"{total_fatos} fatos guardados"
        if recentes:
            texto += "\n" + "\n".join(f"› {fato[:70]}" for fato in recentes)
        self.memoria_var.set(texto)

        pedido_pendente = self.audio_config is not None and "modo_pedido" in self.audio_config.keys()
        modo = None if pedido_pendente else painel.get("modo")
        if modo and modo != memory.MODO_CONVERSA_ATUAL and modo in MODOS_PASTAS:
            memory.mudar_modo_conversa(modo)  # o modo foi trocado por voz
        if modo:
            self._marcar_aba_modo(modo)
            self.nucleo.definir_legenda("modo", "Modo", modo)
        # Pela voz ela só reage ao nome; depois de chamada, escuta a próxima frase sem ele.
        escuta = painel.get("escuta")
        if escuta:
            self.nucleo.definir_legenda("escuta", "Voz", escuta)

    def atualizar_agenda_e_clima(self):
        def coletar():
            try:  # agenda local do PC: o que ainda tem hoje e o próximo compromisso
                from datetime import date
                agenda = f"{self.agenda.descrever_dia(date.today())}\n{self.agenda.proximo()}"
                conectada = True
            except Exception as exc:
                conectada, agenda = False, f"Agenda indisponível: {exc}"
            try:
                from tools import obter_previsao_tempo
                clima = obter_previsao_tempo()
            except Exception as exc:
                clima = f"Clima indisponível: {exc}"
            self.after(0, lambda: self._aplicar_agenda_e_clima(conectada, agenda, clima))

        threading.Thread(target=coletar, daemon=True).start()
        self.after(5 * 60 * 1000, self.atualizar_agenda_e_clima)

    def _aplicar_agenda_e_clima(self, conectada, agenda, clima):
        self.agenda_var.set(agenda.strip()[:400])
        self._definir_indicador(self.indicador_agenda, "local" if conectada else "indisponível", COR_VERDE if conectada else COR_SUAVE)
        self.painel_agenda.definir_destaque("· hoje" if conectada else "", HUD_VERDE)
        self.clima_var.set(clima)
        if not clima:
            self._rotulo_clima.pack_forget()

    def definir_atividade(self, atividade):
        """Muda a cor e a velocidade do núcleo: 'repouso', 'processando' ou 'ouvindo'."""
        self.nucleo.atividade = atividade

    def monitorar_ollama(self):
        def verificar():
            online, modelo_ok = verificar_ollama()
            self.after(0, lambda: self._aplicar_status_ollama(online, modelo_ok))

        threading.Thread(target=verificar, daemon=True).start()
        self.after(15000, self.monitorar_ollama)

    def _aplicar_status_ollama(self, online, modelo_ok):
        if not online:
            self._definir_indicador(self.indicador_llm, "offline", COR_VERMELHO)
            self.cabecalho_hud.definir_status("offline", HUD_VERMELHO)
            self.painel_nucleo.definir_destaque("· sem LLM", HUD_VERMELHO)
        elif not modelo_ok:
            self._definir_indicador(self.indicador_llm, f"{OLLAMA_MODEL} ausente", COR_AMBAR)
            self.cabecalho_hud.definir_status("parcial", HUD_AMBAR)
            self.painel_nucleo.definir_destaque("· modelo ausente", HUD_AMBAR)
        else:
            self._definir_indicador(self.indicador_llm, OLLAMA_MODEL, COR_VERDE)
            self.cabecalho_hud.definir_status("online", HUD_VERDE)
            self.painel_nucleo.definir_destaque("· online", HUD_VERDE)

    def _animar_digitando(self):
        if not self._processando:
            self.digitando_var.set("")
            return
        pontos = "." * (self._quadro_animacao % 4)
        decorrido = time.perf_counter() - self._inicio_processamento
        self.digitando_var.set(f"☀ Beatriz está pensando{pontos:<3}  {decorrido:4.1f}s")
        self._quadro_animacao += 1
        self.after(300, self._animar_digitando)

    def _atualizar_metricas(self, duracao):
        self._total_respostas += 1
        self._soma_tempos += duracao
        media = self._soma_tempos / self._total_respostas
        self.metricas_var.set(
            f"modelo {OLLAMA_MODEL} · {self._total_respostas} respostas · última {duracao:.1f}s · média {media:.1f}s"
        )

    # ------------------------------------------------------------------ áudio
    def atualizar_dispositivos_audio(self):
        self._microfones_por_nome = {"Padrão automático": None}
        for dispositivo in listar_microfones():
            rotulo = f"{dispositivo['indice']}: {dispositivo['nome']}"
            self._microfones_por_nome[rotulo] = dispositivo["indice"]
        self.microfone_combo.configure(values=tuple(self._microfones_por_nome))
        indice_atual = microfone_selecionado()
        rotulo_atual = next(
            (nome for nome, indice in self._microfones_por_nome.items() if indice == indice_atual),
            "Padrão automático",
        )
        self.microfone_var.set(rotulo_atual)

        saidas = ["Padrão do sistema", *listar_saidas_audio()]
        self.saida_audio_combo.configure(values=saidas)
        saida_atual = saida_audio_selecionada() or "Padrão do sistema"
        self.saida_audio_var.set(saida_atual if saida_atual in saidas else "Padrão do sistema")
        self._atualizar_indicador_saida()

    def _atualizar_indicador_saida(self):
        if not hasattr(self, "indicador_saida"):
            return
        saida = saida_audio_selecionada() or "padrão"
        self._definir_indicador(self.indicador_saida, saida[:22], COR_DESTAQUE)

    def aplicar_dispositivos_audio(self):
        indice = self._microfones_por_nome.get(self.microfone_var.get())
        saida = self.saida_audio_var.get()
        nome_saida = "" if saida == "Padrão do sistema" else saida

        if not selecionar_microfone(indice):
            self.definir_status("Não foi possível selecionar esse microfone", erro=True)
            return
        if not selecionar_saida_audio(nome_saida):
            self.definir_status("Não foi possível selecionar essa saída de áudio", erro=True)
            return
        if self.audio_config is not None:
            self.audio_config["microfone"] = indice
            self.audio_config["saida"] = nome_saida
        microfone = self.microfone_var.get()
        saida_exibida = saida if saida else "padrão do sistema"
        self._atualizar_indicador_saida()
        self.definir_status(f"Áudio aplicado: {microfone} | {saida_exibida}")

    # ------------------------------------------------------------------ entrada
    def _entrada_tem_placeholder(self):
        return self.entrada.get("1.0", "end-1c") == PLACEHOLDER_ENTRADA

    def _limpar_placeholder(self, event=None):
        if self._entrada_tem_placeholder():
            self.entrada.delete("1.0", "end")
            self.entrada.configure(fg=COR_TEXTO)
        self._moldura_entrada.configure(bg=COR_DESTAQUE)

    def _restaurar_placeholder(self, event=None):
        self._moldura_entrada.configure(bg=COR_BORDA)
        if not self.entrada.get("1.0", "end-1c").strip():
            self.entrada.delete("1.0", "end")
            self.entrada.insert("1.0", PLACEHOLDER_ENTRADA)
            self.entrada.configure(fg=COR_SUAVE)

    def _definir_texto_entrada(self, texto):
        self.entrada.delete("1.0", "end")
        self.entrada.insert("1.0", texto)
        self.entrada.configure(fg=COR_TEXTO)
        self.entrada.mark_set("insert", "end-1c")
        self._atualizar_contador()

    def _ao_pressionar_enter(self, event):
        if event.state & 0x0001:  # Shift: quebra de linha
            return None
        self.enviar()
        return "break"

    def _historico_anterior(self, event):
        if not self._historico_comandos or self.entrada.index("insert").split(".")[0] != "1":
            return None
        if self._indice_historico is None:
            self._indice_historico = len(self._historico_comandos)
        self._indice_historico = max(0, self._indice_historico - 1)
        self._definir_texto_entrada(self._historico_comandos[self._indice_historico])
        return "break"

    def _historico_proximo(self, event):
        if self._indice_historico is None:
            return None
        self._indice_historico += 1
        if self._indice_historico >= len(self._historico_comandos):
            self._indice_historico = None
            self._definir_texto_entrada("")
        else:
            self._definir_texto_entrada(self._historico_comandos[self._indice_historico])
        return "break"

    def _atualizar_contador(self, event=None):
        texto = "" if self._entrada_tem_placeholder() else self.entrada.get("1.0", "end-1c")
        self.contador_var.set(f"{len(texto)} caracteres · ↑/↓ histórico")

    def usar_atalho(self, texto):
        self.entrada.focus_set()
        self._limpar_placeholder()
        self._definir_texto_entrada(texto)

    # ---------------------------------------------------------------------- voz
    def acompanhar_voz(self):
        """Mostra no chat o que o microfone entendeu (e se a Beatriz aceitou) e o que ela respondeu."""
        if self.fila_voz is None:
            return
        try:
            for _ in range(20):  # poucos por vez, para não travar a janela
                self._mostrar_evento_voz(self.fila_voz.get_nowait())
        except queue.Empty:
            pass
        except (EOFError, OSError, ValueError):
            return  # o processo principal fechou a fila
        if self._voz_pensando_desde and time.time() - self._voz_pensando_desde > 40:
            self._voz_pensando_desde = 0.0  # pedido que não gerou resposta falada
            self.definir_atividade("repouso")
        try:
            self.after(250, self.acompanhar_voz)
        except tk.TclError:
            pass  # janela fechada

    def _mostrar_evento_voz(self, evento):
        tipo, texto, motivo = evento.get("tipo"), evento.get("texto", ""), evento.get("motivo", "")
        if tipo == "ouvido":
            self.adicionar_mensagem("Você (voz)" + (f" · {motivo}" if motivo else ""), texto, tag="usuario")
            if evento.get("pensando", not motivo):
                self._voz_pensando_desde = time.time()
                self.definir_atividade("processando")
            self.definir_status(f"{motivo[0].upper()}{motivo[1:]}" if motivo else "Entendi sua fala")
        elif tipo == "ignorado":
            self.adicionar_mensagem("Ouvi, mas não respondi", f"“{texto}” · {motivo}", tag="ignorado")
            self.definir_status(f"Ouvi “{texto[:40]}”, mas ignorei: {motivo}")
        elif tipo == "resposta":
            self._ultima_resposta = texto
            self._voz_pensando_desde = 0.0
            self.adicionar_mensagem("Beatriz", texto, tag="beatriz")
            self.definir_atividade("repouso")
            self.definir_status("Pronta")

    # --------------------------------------------------------------------- chat
    def definir_status(self, texto, erro=False):
        self.status_var.set(texto)
        self._rotulo_status.configure(fg=COR_VERMELHO if erro else COR_VERDE)

    def adicionar_mensagem(self, autor, texto, tag="beatriz"):
        hora = datetime.now().strftime("%H:%M:%S")
        self._historico.append((autor, hora, texto))

        cabecalho = {"usuario": "cab_usuario", "status": "cab_status", "ignorado": "cab_ignorado"}.get(tag, "cab_beatriz")
        simbolo = {"usuario": "▸", "status": "•", "ignorado": "○"}.get(tag, "☀")

        self.chat.configure(state="normal")
        self.chat.insert("end", f"{simbolo} {autor}", cabecalho)
        self.chat.insert("end", f"   {hora}\n", ("hora", cabecalho))
        self._inserir_corpo(texto, tag)
        self.chat.insert("end", "\n")
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _inserir_corpo(self, texto, tag):
        """Insere o texto destacando blocos de código delimitados por ```."""
        partes = str(texto).split("```")
        for indice, parte in enumerate(partes):
            if indice % 2 == 1:
                linhas = parte.split("\n", 1)
                codigo = linhas[1] if len(linhas) > 1 and linhas[0].strip().isidentifier() else parte
                self.chat.insert("end", codigo.strip("\n") + "\n", "codigo")
            elif parte.strip():
                self.chat.insert("end", parte.strip("\n") + "\n", tag)

    def limpar_chat(self):
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        self._historico.clear()
        self.adicionar_mensagem("Beatriz", "Conversa limpa. Posso ajudar em outra coisa.", tag="status")

    def copiar_ultima_resposta(self):
        if not self._ultima_resposta:
            self.definir_status("Nenhuma resposta para copiar ainda")
            return
        self.clipboard_clear()
        self.clipboard_append(self._ultima_resposta)
        self.definir_status("Última resposta copiada para a área de transferência")

    def exportar_conversa(self):
        if not self._historico:
            self.definir_status("Nada para exportar")
            return
        caminho = filedialog.asksaveasfilename(
            title="Exportar conversa",
            defaultextension=".md",
            initialfile=f"conversa_beatriz_{datetime.now():%Y%m%d_%H%M}.md",
            filetypes=[("Markdown", "*.md"), ("Texto", "*.txt")],
        )
        if not caminho:
            return
        try:
            with open(caminho, "w", encoding="utf-8") as arquivo:
                arquivo.write(f"# Conversa com a Beatriz — {datetime.now():%d/%m/%Y %H:%M}\n\n")
                for autor, hora, texto in self._historico:
                    arquivo.write(f"**{autor}** ({hora})\n\n{texto}\n\n---\n\n")
            self.definir_status(f"Conversa exportada: {os.path.basename(caminho)}")
        except OSError as exc:
            self.definir_status(f"Erro ao exportar: {exc}", erro=True)

    # ------------------------------------------------------------ dispositivos
    def alternar_microfone(self):
        ativo = not microfone_ativo()
        definir_microfone_ativo(ativo)
        if self.microfone_evento is not None:
            if ativo:
                self.microfone_evento.set()
            else:
                self.microfone_evento.clear()
        self.botao_microfone.configure(text="🎙 Mic: ativo" if ativo else "🎙 Mic: pausado")
        self._definir_indicador(self.indicador_mic, "ativo" if ativo else "pausado", COR_VERDE if ativo else COR_SUAVE)
        self.definir_status("Microfone ativo" if ativo else "Microfone pausado")
        self.adicionar_mensagem("Beatriz", "Microfone ativado." if ativo else "Microfone pausado.", tag="status")

    def usar_microfone(self):
        if not microfone_ativo():
            self.definir_status("Microfone pausado")
            return
        if not microfone_disponivel():
            self.definir_status("Microfone indisponível", erro=True)
            self.adicionar_mensagem("Beatriz", "Microfone não detectado no sistema.", tag="status")
            return

        self.definir_status("Ouvindo...")
        self.adicionar_mensagem("Beatriz", "Ouvindo sua voz...", tag="status")
        self.definir_atividade("ouvindo")
        self.update_idletasks()
        texto = ouvir()
        self.definir_atividade("repouso")
        if texto:
            self.adicionar_mensagem("Usuário", texto, tag="usuario")
            self.processar_texto(texto)
        else:
            self.adicionar_mensagem("Beatriz", "Não consegui entender o que foi dito.", tag="status")
            self.definir_status("Pronta")

    def abrir_camera(self):
        self.definir_status("Câmera em preparação")
        self.adicionar_mensagem("Beatriz", "A câmera será ativada se estiver disponível.", tag="status")
        try:
            from visao import detectar_camera_disponivel
            indice = detectar_camera_disponivel()
            if indice is not None:
                self.adicionar_mensagem("Beatriz", f"Câmera encontrada no índice {indice}.", tag="beatriz")
                self.definir_status("Câmera pronta")
                self._definir_indicador(self.indicador_camera, f"índice {indice}", COR_VERDE)
            else:
                self.adicionar_mensagem("Beatriz", "Nenhuma câmera foi detectada no momento.", tag="status")
                self.definir_status("Câmera indisponível", erro=True)
                self._definir_indicador(self.indicador_camera, "não detectada", COR_VERMELHO)
        except Exception as exc:
            self.adicionar_mensagem("Beatriz", f"Erro ao verificar câmera: {exc}", tag="status")
            self.definir_status("Erro na câmera", erro=True)

    # ------------------------------------------------------------------ anexos
    def _inferir_categoria(self, caminho):
        if self.categoria_var.get() != "auto":
            return self.categoria_var.get()
        nome = os.path.basename(caminho).lower()
        categorias = {
            "comunicacao": ["comunic", "fala", "dialog", "relacion"],
            "filosofia": ["filosof", "ética", "etica", "existencial"],
            "psicologia": ["psicolog", "ansiedade", "emoc", "terapia"],
            "direito": ["lei", "direito", "jurid", "contrato"],
            "projetos": ["projeto", "roadmap", "planejamento"],
            "procedimentos": ["procedimento", "manual", "tutorial", "guia"],
        }
        for categoria, palavras in categorias.items():
            if any(palavra in nome for palavra in palavras):
                return categoria
        return "referencias"

    def anexar_arquivos(self):
        caminhos = filedialog.askopenfilenames(
            title="Selecione arquivos para a biblioteca",
            filetypes=[
                ("Documentos suportados", "*.pdf *.docx *.xlsx *.ppt *.pptx *.txt *.md *.json *.jsonl *.html *.png *.jpg *.jpeg"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if not caminhos:
            return

        self.botao_anexar.configure(state="disabled")
        self.definir_status(f"Importando {len(caminhos)} arquivo(s)...")
        self.adicionar_mensagem("Beatriz", f"Recebi {len(caminhos)} arquivo(s). Vou organizá-los por assunto e indexá-los.", tag="status")

        def importar():
            resultados = []
            for caminho in caminhos:
                categoria = self._inferir_categoria(caminho)
                resultados.append((os.path.basename(caminho), categoria, importar_fonte_local(caminho, categoria)))
            self.after(0, lambda: self._finalizar_anexos(resultados))

        threading.Thread(target=importar, daemon=True).start()

    def _finalizar_anexos(self, resultados):
        self.botao_anexar.configure(state="normal")
        for nome, categoria, resultado in resultados:
            self.adicionar_mensagem("Beatriz", f"{nome} -> pasta '{categoria}': {resultado}", tag="beatriz")
        self.definir_status("Arquivos organizados e indexados")

    # ---------------------------------------------------------------- respostas
    def processar_texto(self, comando):
        comando = comando.strip()
        if not comando:
            return

        resposta_troca_voz = responder_troca_de_voz(comando)
        if resposta_troca_voz:  # "usa a voz da Siri": vale aqui e na voz (a escolha fica num arquivo)
            self._inicio_processamento = time.perf_counter()
            if hasattr(self, "indicador_voz"):
                self._definir_indicador(self.indicador_voz, descricao_da_voz(), COR_DESTAQUE)
            self._finalizar_resposta(resposta_troca_voz)
            return

        resposta_camadas = self.camadas.responder(comando)
        if resposta_camadas:  # vira camada de personalidade
            self._inicio_processamento = time.perf_counter()
            self._finalizar_resposta(resposta_camadas)
            return

        resposta_impressora = self.impressora.responder(comando)
        if resposta_impressora:  # imprimir, fila e estado da impressora: na hora, sem LLM
            self._inicio_processamento = time.perf_counter()
            self._finalizar_resposta(resposta_impressora)
            return

        resposta_prazo = self.prazos.responder(comando)
        if resposta_prazo:  # prazo processual: a conta inteira no chat, a curta na voz
            self._inicio_processamento = time.perf_counter()
            self._finalizar_resposta(resposta_prazo.painel or resposta_prazo.fala, fala=resposta_prazo.fala)
            return

        if self.controle.reconhece(comando):  # TV e ar: a rede pode demorar, então o envio vai numa thread
            self._inicio_processamento = time.perf_counter()

            def controlar():
                resposta = self.controle.responder(comando) or "Não entendi o pedido do controle remoto."
                self.after(0, lambda: self._finalizar_resposta(resposta))

            threading.Thread(target=controlar, daemon=True).start()
            return

        resposta_agenda = self.agenda.responder(comando)
        if resposta_agenda:  # compromissos, rotina e tarefas: resposta na hora, sem LLM
            self._inicio_processamento = time.perf_counter()
            self._finalizar_resposta(resposta_agenda)
            return

        # Pedidos de entrega (documento, PDF, Word, apresentação) vão para os agentes em segundo plano;
        # o chat fica livre na hora.
        gerenciamento = self.orquestrador.responder_gerenciamento(comando)
        if gerenciamento:
            self.adicionar_mensagem("Beatriz", gerenciamento, tag="beatriz")
            return
        tarefa = self.orquestrador.enviar(comando, ao_concluir=lambda t: self.after(0, lambda: self._tarefa_concluida(t)))
        if tarefa:
            mensagem = self.orquestrador.mensagem_inicio(tarefa)
            self.adicionar_mensagem("Beatriz", mensagem, tag="beatriz")
            falar_async(mensagem)
            self.definir_status("Tarefa enviada aos agentes")
            return

        self._processando = True
        self.definir_atividade("processando")
        self._inicio_processamento = time.perf_counter()
        self.botao_enviar.configure(state="disabled")
        self.definir_status("Processando...")
        self._animar_digitando()

        def responder():
            try:
                resposta_craft = self.craft.responder(comando, "painel")
                if resposta_craft:  # apps da ArtCraft, antes das fontes ("converte esse Word" não é pergunta sobre ele)
                    self.after(0, lambda: self._finalizar_resposta(resposta_craft.texto))
                    return
                resposta_fontes = self.fontes.responder(comando, "painel")
                if resposta_fontes:  # arquivos recebidos e escaneamento
                    self.after(0, lambda: self._finalizar_resposta(resposta_fontes))
                    return
                resposta_tela = self.tela.responder(comando)
                if resposta_tela:  # leitura de tela: fala a resposta curta e mostra a completa
                    texto_tela = f"{resposta_tela.fala}\n\n{resposta_tela.painel}" if resposta_tela.painel else resposta_tela.fala
                    self.after(0, lambda: self._finalizar_resposta(texto_tela, fala=resposta_tela.fala))
                    return
                resposta_pasta = self.pastas.responder(comando)
                if resposta_pasta:  # abre a pasta no Explorador e diz qual foi
                    self.after(0, lambda: self._finalizar_resposta(resposta_pasta))
                    return
                resposta_lei = self.leis.responder(comando)
                if resposta_lei:  # o texto oficial do artigo e, embaixo, a explicação da IA
                    partes, fala = [resposta_lei.painel or resposta_lei.leitura], resposta_lei.leitura
                    if resposta_lei.explicar:
                        explicacao = resposta_lei.explicar()
                        partes.append(explicacao)
                        fala = f"{fala} {explicacao}".strip()
                    texto_final = "\n\n".join(parte for parte in partes if parte)
                    self.after(0, lambda: self._finalizar_resposta(texto_final, fala=fala))
                    return
                idiomas_executado, resposta_idiomas = processar_comando_idiomas(comando)
                pedido_resumo = None if idiomas_executado else preparar_pedido_de_resumo(comando)
                if pedido_resumo:
                    # O resumo inteiro aparece no chat; em voz, só a versão curta.
                    self.after(0, lambda: self.definir_status(f"Pesquisando e resumindo: {pedido_resumo[0]}..."))
                    resumo = gerar_resumo(*pedido_resumo)
                    self.after(0, lambda: self._finalizar_resposta(resumo.texto, fala=resumo.fala))
                    return
                if idiomas_executado:
                    resposta = resposta_idiomas
                else:
                    resposta = responder_pedido_de_cinema(comando) or notelooklm_responder(comando)
                if not resposta:
                    resposta = frase("sem_resposta")
                self.after(0, lambda: self._finalizar_resposta(resposta))
            except Exception as exc:
                mensagem_erro = f"Erro ao processar: {exc}"
                self.after(0, lambda: self._finalizar_resposta(mensagem_erro, erro=True))

        threading.Thread(target=responder, daemon=True).start()

    def _finalizar_resposta(self, resposta, erro=False, fala=None):
        duracao = time.perf_counter() - self._inicio_processamento
        self._processando = False
        self.definir_atividade("repouso")
        self.digitando_var.set("")
        self.botao_enviar.configure(state="normal")
        self.adicionar_mensagem("Beatriz", resposta, tag="status" if erro else "beatriz")
        if not erro:
            self._ultima_resposta = resposta
            self._atualizar_metricas(duracao)
            falar_async(fala or resposta)
        self.definir_status("Erro" if erro else f"Pronta · respondeu em {duracao:.1f}s", erro=erro)

    def enviar(self):
        if self._processando:
            self.definir_status("Aguarde a resposta atual terminar")
            return
        comando = "" if self._entrada_tem_placeholder() else self.entrada.get("1.0", "end-1c").strip()
        if not comando:
            self.definir_status("Digite algo primeiro")
            return

        self._historico_comandos.append(comando)
        self._indice_historico = None
        self.adicionar_mensagem("Usuário", comando, tag="usuario")
        self.entrada.delete("1.0", "end")
        self._atualizar_contador()
        self.processar_texto(comando)


def iniciar(evento_pronto=None, microfone_evento=None, audio_config=None, fila_voz=None, niveis=None):
    """`niveis` (a memória dos níveis da voz) é só do painel web; o clássico não desenha a voz."""
    app = JanelaComandos(microfone_evento=microfone_evento, audio_config=audio_config, fila_voz=fila_voz)
    if evento_pronto is not None:
        evento_pronto.set()
    app.mainloop()


if __name__ == "__main__":
    iniciar()
