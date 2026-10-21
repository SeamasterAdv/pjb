"""Tela de login e de cadastro da Beatriz. A lógica está em `autenticacao.py`; aqui só se mostra e se chama.

A tela padrão é a web (login_web.py: a esfera com a câmera dentro e a montanha da sua voz); a de Tkinter, aqui
embaixo, fica para LOGIN_TELA=tk e para quando o pywebview não abre.

    python login_tela.py --entrar      login (se ainda não existe, abre o cadastro) — é o que o main.py chama
    python login_tela.py --cadastrar   refaz tudo: usuário, senha, rosto e código (pede o login antes, se já existe)
    python login_tela.py --rosto       refaz só o rosto e gera um código novo (pede o login antes)

Código de saída 0 = entrou, 1 = não entrou.
"""
import argparse
import os
import sys
import threading
import time
import tkinter as tk

import autenticacao as aut
from config import LOGIN_TELA
from identidade_visual import (CREME, DOURADO, DOURADO_CLARO, LARANJA, LILAS, LILAS_SUAVE, ROXO_BORDA, ROXO_CARTAO,
                               ROXO_NOITE, ROXO_PAINEL, ROXO_TRILHA, VERDE, VERMELHO)

LARGURA_VIDEO, ALTURA_VIDEO = 480, 360
FONTE = "Segoe UI"
ICONE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "beatriz_icone.ico")


class _Janela(tk.Tk):
    """Cabeçalho, imagem da câmera, barra de progresso e mensagem; cada tela põe o seu conteúdo em `self.corpo`."""

    def __init__(self, titulo: str, subtitulo: str, conta: aut.ContaLocal, reconhecedor=None, camera=None):
        super().__init__()
        self.conta = conta
        self.reconhecedor = reconhecedor or aut.ReconhecedorFacial()
        self.camera = camera or aut.Camera()
        self.sucesso = False
        self._processador = None  # função quadro -> EstadoDoRosto, quando a câmera deve trabalhar
        self._estado = None
        self._preparo = None  # None enquanto prepara; ("ok", "") ou ("erro", mensagem)
        self._foto = None
        self.escala = max(1.0, self.winfo_fpixels("1i") / 96.0)

        self.title(f"Beatriz — {titulo}")
        self.configure(bg=ROXO_NOITE)
        self.resizable(False, False)
        try:
            self.iconbitmap(ICONE)
        except Exception:
            pass
        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        self.bind("<Escape>", lambda _e: self._cancelar())

        tk.Label(self, text="Beatriz", font=(FONTE, 26, "bold"), fg=DOURADO, bg=ROXO_NOITE).pack(pady=(18, 0))
        self.lbl_subtitulo = tk.Label(self, text=subtitulo, font=(FONTE, 11), fg=LILAS, bg=ROXO_NOITE)
        self.lbl_subtitulo.pack(pady=(0, 8))

        self.area_video = tk.Frame(self, bg=ROXO_NOITE)
        self.area_video.pack()
        self.video = tk.Canvas(self.area_video, width=int(LARGURA_VIDEO * self.escala * 0.9), height=int(ALTURA_VIDEO * self.escala * 0.9),
                               bg=ROXO_PAINEL, highlightthickness=2, highlightbackground=ROXO_BORDA)
        self.video.pack()
        self.barra = tk.Canvas(self.area_video, width=int(LARGURA_VIDEO * self.escala * 0.9), height=8, bg=ROXO_TRILHA, highlightthickness=0)
        self.barra.pack(pady=(6, 0))
        self.lbl_mensagem = tk.Label(self, text="", font=(FONTE, 12), fg=CREME, bg=ROXO_NOITE, wraplength=int(390 * self.escala),
                                     justify="center")
        self.lbl_mensagem.pack(pady=(8, 4))
        self.corpo = tk.Frame(self, bg=ROXO_NOITE)
        self.corpo.pack(fill="x", padx=36, pady=(0, 18))

        self.attributes("-topmost", True)  # abre na frente (o launcher roda sem janela própria)
        self.after(600, lambda: self.attributes("-topmost", False))
        self._centralizar()
        self.after(10, self._tick)

    # -- componentes de formulário
    def _campo(self, pai, rotulo: str, escondido: bool = False) -> tk.Entry:
        tk.Label(pai, text=rotulo, font=(FONTE, 10), fg=LILAS_SUAVE, bg=ROXO_NOITE, anchor="w").pack(fill="x", pady=(8, 2))
        entrada = tk.Entry(pai, font=(FONTE, 12), bg=ROXO_CARTAO, fg=CREME, insertbackground=CREME, relief="flat",
                           highlightthickness=1, highlightbackground=ROXO_BORDA, highlightcolor=DOURADO, show="•" if escondido else "")
        entrada.pack(fill="x", ipady=6)
        return entrada

    def _botao(self, pai, texto: str, comando, primario: bool = True) -> tk.Button:
        botao = tk.Button(pai, text=texto, command=comando, font=(FONTE, 11, "bold" if primario else "normal"), relief="flat",
                          cursor="hand2", bd=0, padx=16, pady=8,
                          bg=DOURADO if primario else ROXO_CARTAO, fg=ROXO_NOITE if primario else LILAS,
                          activebackground=DOURADO_CLARO if primario else ROXO_BORDA, activeforeground=ROXO_NOITE if primario else CREME,
                          disabledforeground=ROXO_TRILHA)
        botao.pack(fill="x", pady=(10, 0))
        return botao

    def _link(self, pai, texto: str, comando) -> tk.Label:
        link = tk.Label(pai, text=texto, font=(FONTE, 9, "underline"), fg=LILAS_SUAVE, bg=ROXO_NOITE, cursor="hand2")
        link.pack(pady=(10, 0))
        link.bind("<Button-1>", lambda _e: comando())
        return link

    def mensagem(self, texto: str, cor: str = CREME):
        self.lbl_mensagem.configure(text=texto, fg=cor)

    def _centralizar(self):
        self.update_idletasks()
        largura, altura = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f"+{(self.winfo_screenwidth() - largura) // 2}+{max(0, (self.winfo_screenheight() - altura) // 3)}")

    # -- câmera e modelos
    def preparar_rosto(self):
        """Carrega os modelos (baixa na primeira vez) e abre a câmera, sem travar a janela."""
        self._preparo = None
        self.mensagem("Preparando o reconhecimento facial... (na primeira vez baixa ~39 MB)", LILAS)

        def trabalhar():
            try:
                self.reconhecedor.preparar()
            except Exception as exc:
                self._preparo = ("erro", f"Não consegui preparar o reconhecimento facial: {exc}")
                return
            if not self.camera.abrir():
                self._preparo = ("erro", "Não consegui abrir a câmera. Veja se outro programa a está usando.")
                return
            self._preparo = ("ok", "")

        threading.Thread(target=trabalhar, daemon=True).start()

    def _tick(self):
        try:
            if self._preparo and self._preparo[0] == "ok" and self._processador is not None:
                quadro = self.camera.ler()
                if quadro is not None:
                    self._estado = self._processador(quadro)
                    self._desenhar(quadro, self._estado)
                    self._barra(self._estado.progresso, VERDE if self._estado.fase in ("ok", "pronto") else DOURADO)
                    self.ao_estado(self._estado)
            elif self._preparo and self._preparo[0] == "erro":
                erro, self._preparo = self._preparo[1], ("tratado", "")
                self.ao_erro_de_preparo(erro)
        except Exception as exc:
            self.mensagem(f"Erro na câmera: {exc}", VERMELHO)
        if self.winfo_exists():
            self.after(15, self._tick)

    def _desenhar(self, quadro, estado):
        import cv2
        from PIL import Image, ImageDraw, ImageTk

        largura, altura = int(self.video["width"]), int(self.video["height"])
        imagem = Image.fromarray(cv2.cvtColor(cv2.flip(quadro, 1), cv2.COLOR_BGR2RGB)).resize((largura, altura))
        pincel = ImageDraw.Draw(imagem)
        cor_guia = "#7BE0A6" if estado.fase in ("ok", "pronto") else DOURADO
        pincel.ellipse((largura * 0.28, altura * 0.08, largura * 0.72, altura * 0.92), outline=cor_guia, width=3)
        if estado.rosto is not None:
            altura_q, largura_q = quadro.shape[:2]
            x, y, w, h = estado.rosto.caixa
            esquerda = (1 - (x + w) / largura_q) * largura
            pincel.rectangle((esquerda, y / altura_q * altura, esquerda + w / largura_q * largura, (y + h) / altura_q * altura),
                             outline=cor_guia, width=2)
        self._foto = ImageTk.PhotoImage(imagem)
        self.video.delete("all")
        self.video.create_image(0, 0, image=self._foto, anchor="nw")

    def _barra(self, valor: float, cor: str):
        self.barra.delete("all")
        self.barra.create_rectangle(0, 0, int(int(self.barra["width"]) * max(0.0, min(1.0, valor))), 8, fill=cor, width=0)

    def esconder_video(self):
        self._processador = None
        self.area_video.pack_forget()
        self._centralizar()

    def mostrar_video(self):
        self.area_video.pack(before=self.lbl_mensagem)

    # -- ganchos das telas
    def ao_estado(self, estado):
        pass

    def ao_erro_de_preparo(self, mensagem: str):
        self.mensagem(mensagem, VERMELHO)

    def _cancelar(self):
        self.sucesso = False
        self._fechar()

    def _fechar(self):
        self._processador = None
        try:
            self.camera.fechar()
        finally:
            self.destroy()

    def executar(self) -> bool:
        self.mainloop()
        return self.sucesso


# ------------------------------------------------------------------ login
class JanelaDeLogin(_Janela):
    def __init__(self, conta: aut.ContaLocal, fluxo: aut.FluxoDeEntrada | None = None, **kw):
        super().__init__("Entrar", "Entre para acordar a sua assistente", conta, **kw)
        self.fluxo = fluxo or aut.FluxoDeEntrada(conta)
        self.resultado: aut.Resultado | None = None
        self._verificacao = None
        self._tentar = None

        self.zona_senha = tk.Frame(self.corpo, bg=ROXO_NOITE)
        self.zona_codigo = tk.Frame(self.corpo, bg=ROXO_NOITE)
        self.zona_links = tk.Frame(self.corpo, bg=ROXO_NOITE)
        for zona in (self.zona_senha, self.zona_codigo, self.zona_links):
            zona.pack(fill="x")
        self.frame_senha = tk.Frame(self.zona_senha, bg=ROXO_NOITE)  # só aparece quando a senha é pedida
        self.entrada_usuario = self._campo(self.frame_senha, "Usuário")
        self.entrada_senha = self._campo(self.frame_senha, "Senha", escondido=True)
        self.botao_entrar = self._botao(self.frame_senha, "Entrar", self._enviar_senha)
        self.entrada_usuario.insert(0, conta.usuario)
        self.bind("<Return>", self._ao_dar_enter)

        self.frame_codigo = tk.Frame(self.zona_codigo, bg=ROXO_NOITE)
        self.entrada_codigo = self._campo(self.frame_codigo, "Código de recuperação (XXXXX-XXXXX-XXXXX-XXXXX)")
        self._botao(self.frame_codigo, "Validar código", self._enviar_codigo, primario=False)

        # "Entrar com a senha": só no modo rosto_ou_senha, enquanto a câmera procura (a senha também aparece sozinha se o rosto falhar)
        self.link_senha = self._link(self.zona_links, "Entrar com a senha", self._mostrar_senha)
        self.link_senha.pack_forget()
        self.link_codigo = self._link(self.zona_links, "Sem câmera ou o rosto mudou? Use o código de recuperação", self._abrir_codigo)
        self.link_de_novo = self._link(self.zona_links, "Tentar o rosto de novo", self._reiniciar_rosto)
        self.link_de_novo.pack_forget()
        self.after(500, self._atualizar_bloqueio)

        if self.fluxo.usa_rosto:
            self.preparar_rosto()
            self._iniciar_verificacao()
            if self.fluxo.entra_so_com_o_rosto:
                self.link_senha.pack(before=self.link_codigo)  # a senha só aparece quando o rosto falha (ou se pedirem)
        else:
            self.esconder_video()
            self.link_codigo.pack_forget()
            self.mensagem("Digite o usuário e a senha.")
            self._mostrar_senha()

    # -- rosto
    def _iniciar_verificacao(self):
        self._verificacao = self.fluxo.verificacao(self.reconhecedor)
        self._processador = self._verificacao.passo
        self.link_de_novo.pack_forget()
        if not self.area_video.winfo_ismapped():
            self.mostrar_video()

    def _ao_dar_enter(self, _evento):
        if self.focus_get() is self.entrada_codigo:
            self._enviar_codigo()
        elif self.frame_senha.winfo_ismapped():
            self._enviar_senha()

    def _reiniciar_rosto(self):
        if self._preparo and self._preparo[0] == "ok":
            self._iniciar_verificacao()
        else:
            self.preparar_rosto()
            self._iniciar_verificacao()

    def ao_estado(self, estado):
        if self.fluxo.bloqueio():
            return
        if estado.fase == "ok":
            self._processador = None
            resultado = self.fluxo.rosto_confirmado()
            if resultado.ok:
                return self._entrou(resultado)
            self.mensagem("Rosto confirmado. Agora digite a senha.", VERDE)
            self._mostrar_senha()
        elif estado.fase == "falhou":
            self._processador = None
            resultado = self.fluxo.rosto_falhou(self._verificacao.viu_rosto if self._verificacao else True)
            if resultado.bloqueio:
                self.mensagem(resultado.mensagem, VERMELHO)
            else:
                pede_senha = " Digite o usuário e a senha." if self.fluxo.entra_so_com_o_rosto else ""
                self.mensagem((estado.mensagem or resultado.mensagem) + pede_senha, VERMELHO)
                self.link_de_novo.pack()
                if self.fluxo.entra_so_com_o_rosto:
                    self._mostrar_senha()  # não era você (ou não deu tempo): cai na senha
        else:
            self.mensagem(estado.mensagem, CREME)

    def ao_erro_de_preparo(self, mensagem: str):
        self._processador = None
        self.link_de_novo.pack()
        if self.fluxo.entra_so_com_o_rosto:  # câmera ou modelos não carregaram: vai direto para a senha
            self.mensagem(mensagem + "\nDigite o usuário e a senha para entrar.", VERMELHO)
            self._mostrar_senha()
        else:
            self.mensagem(mensagem + "\nUse o código de recuperação para entrar sem o rosto.", VERMELHO)

    # -- senha e código
    def _mostrar_senha(self):
        self.link_senha.pack_forget()
        if not self.frame_senha.winfo_ismapped():
            self.frame_senha.pack(fill="x")
        (self.entrada_senha if self.entrada_usuario.get() else self.entrada_usuario).focus_set()
        self._centralizar()

    def _abrir_codigo(self):
        self.frame_codigo.pack(fill="x")
        self.link_codigo.pack_forget()
        self.entrada_codigo.focus_set()
        self._centralizar()

    def _enviar_codigo(self):
        resultado = self.fluxo.usar_codigo(self.entrada_codigo.get())
        self.entrada_codigo.delete(0, "end")
        self.mensagem(resultado.mensagem, VERDE if self.fluxo.codigo_ok else VERMELHO)
        if self.fluxo.codigo_ok:
            self._processador = None
            self.frame_codigo.pack_forget()
            self._mostrar_senha()

    def _enviar_senha(self):
        resultado = self.fluxo.entrar(self.entrada_usuario.get(), self.entrada_senha.get())
        self.entrada_senha.delete(0, "end")
        if resultado.ok:
            return self._entrou(resultado)
        self.mensagem(resultado.mensagem, VERMELHO)
        self._atualizar_bloqueio()

    def _atualizar_bloqueio(self):
        """Enquanto a conta está travada por tentativas erradas, desliga os campos e mostra a contagem."""
        if not self.winfo_exists():
            return
        segundos = self.fluxo.bloqueio()
        estado = "disabled" if segundos else "normal"
        for campo in (self.entrada_usuario, self.entrada_senha, self.entrada_codigo, self.botao_entrar):
            campo.configure(state=estado)
        if segundos:
            self.mensagem(f"Muitas tentativas erradas. Tente de novo em {int(segundos) + 1} s.", VERMELHO)
        self.after(500, self._atualizar_bloqueio)

    def _entrou(self, resultado: aut.Resultado):
        self.resultado = resultado
        self.sucesso = True
        self._processador = None
        self.mensagem(f"Bem-vindo, {self.conta.usuario.title()}.", VERDE)
        self._barra(1.0, VERDE)
        self.after(700, self._fechar)


# ------------------------------------------------------------------ cadastro
class JanelaDeCadastro(_Janela):
    """Três páginas: usuário e senha, rosto (amostras, virar a cabeça e teste) e o código de recuperação."""

    def __init__(self, conta: aut.ContaLocal, so_rosto: bool = False, **kw):
        super().__init__("Cadastro", "Vamos criar o seu login" if not so_rosto else "Vamos cadastrar o seu rosto de novo", conta, **kw)
        self.so_rosto = so_rosto
        self.usuario = conta.usuario if so_rosto else ""
        self.senha = ""
        self.cadastro: aut.CadastroDeRosto | None = None
        self.vetores, self.sinal = [], 1
        self.codigo = aut.gerar_codigo_recuperacao()
        self._pagina = None
        self.esconder_video()
        self.bind("<Return>", lambda _e: self._avancar())
        self._ir_para("rosto" if so_rosto else "credenciais")

    def _limpar_corpo(self):
        for filho in self.corpo.winfo_children():
            filho.destroy()

    def _ir_para(self, pagina: str):
        self._pagina = pagina
        self._limpar_corpo()
        getattr(self, f"_pagina_{pagina}")()
        self._centralizar()

    def _avancar(self):
        {"credenciais": self._confirmar_credenciais, "codigo": self._concluir}.get(self._pagina, lambda: None)()

    # -- 1: usuário e senha
    def _pagina_credenciais(self):
        self.mensagem("Escolha o usuário e uma senha (mínimo 8 caracteres). Você a digitará toda vez que abrir a Beatriz.")
        self.e_usuario = self._campo(self.corpo, "Usuário")
        self.e_senha = self._campo(self.corpo, "Senha", escondido=True)
        self.e_senha2 = self._campo(self.corpo, "Repita a senha", escondido=True)
        self.e_usuario.insert(0, self.usuario)
        self._botao(self.corpo, "Continuar", self._confirmar_credenciais)
        self.e_usuario.focus_set()

    def _confirmar_credenciais(self):
        usuario, senha, repetida = self.e_usuario.get().strip(), self.e_senha.get(), self.e_senha2.get()
        if len(aut.normalizar_usuario(usuario)) < 3:
            return self.mensagem("O usuário precisa ter pelo menos 3 letras.", VERMELHO)
        problema = aut.validar_senha_nova(senha, usuario)
        if problema:
            return self.mensagem(problema, VERMELHO)
        if senha != repetida:
            return self.mensagem("As duas senhas não são iguais.", VERMELHO)
        self.usuario, self.senha = usuario, senha
        self._ir_para("rosto")

    # -- 2: rosto
    def _pagina_rosto(self):
        self.mostrar_video()
        self.cadastro = aut.CadastroDeRosto(self.reconhecedor)
        self.preparar_rosto()
        self._processador = self.cadastro.passo
        self._botao(self.corpo, "Cadastrar sem o rosto (só com a senha)", self._sem_rosto, primario=False)

    def ao_estado(self, estado):
        if self._pagina != "rosto":
            return
        self.mensagem(estado.mensagem, VERDE if estado.fase == "pronto" else CREME)
        if estado.fase == "pronto":
            self._processador = None
            self.vetores, self.sinal = self.cadastro.vetores, self.cadastro.sinal_esquerda
            self.after(900, lambda: self._ir_para("codigo"))

    def ao_erro_de_preparo(self, mensagem: str):
        self.mensagem(mensagem + "\nVocê pode cadastrar só com a senha e refazer o rosto depois.", VERMELHO)

    def _sem_rosto(self):
        self.vetores, self.sinal = [], 1
        self._processador = None
        self._ir_para("codigo")

    # -- 3: código de recuperação
    def _pagina_codigo(self):
        self.esconder_video()
        self.camera.fechar()
        self.mensagem("Anote este código de recuperação em um lugar seguro. Ele entra no lugar do rosto se a câmera falhar "
                      "e só aparece agora.", LARANJA)
        quadro = tk.Frame(self.corpo, bg=ROXO_CARTAO, highlightthickness=1, highlightbackground=DOURADO)
        quadro.pack(fill="x", pady=(6, 0))
        tk.Label(quadro, text=self.codigo, font=("Consolas", 18, "bold"), fg=DOURADO_CLARO, bg=ROXO_CARTAO).pack(pady=12)
        self._botao(self.corpo, "Copiar o código", self._copiar, primario=False)
        self.confirmou = tk.BooleanVar(value=False)
        tk.Checkbutton(self.corpo, text="Guardei o código em um lugar seguro", variable=self.confirmou, command=self._atualizar_concluir,
                       font=(FONTE, 10), fg=CREME, bg=ROXO_NOITE, selectcolor=ROXO_CARTAO, activebackground=ROXO_NOITE,
                       activeforeground=CREME).pack(pady=(12, 0))
        self.botao_concluir = self._botao(self.corpo, "Concluir", self._concluir)
        self.botao_concluir.configure(state="disabled")

    def _copiar(self):
        self.clipboard_clear()
        self.clipboard_append(self.codigo)
        self.mensagem("Código copiado. Cole-o em um gerenciador de senhas ou anote no papel.", VERDE)

    def _atualizar_concluir(self):
        self.botao_concluir.configure(state="normal" if self.confirmou.get() else "disabled")

    def _concluir(self):
        if self._pagina != "codigo" or not self.confirmou.get():
            return
        try:
            if self.so_rosto:
                self.conta.refazer_rosto(self.vetores, self.sinal)
                self.conta.novo_codigo(self.codigo)
            else:
                self.conta.criar(self.usuario, self.senha, self.vetores, self.sinal, self.codigo)
        except ValueError as exc:
            return self.mensagem(str(exc), VERMELHO)
        self.sucesso = True
        self.mensagem("Pronto! O seu login foi criado.", VERDE)
        self.after(700, self._fechar)


# ------------------------------------------------------------------ pontos de entrada
def _dpi_nitido():
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def executar(modo: str = "entrar", conta: aut.ContaLocal | None = None) -> bool:
    """Roda a tela pedida (a web; a de Tkinter se LOGIN_TELA=tk ou se a web não abrir). True se a pessoa entrou."""
    if LOGIN_TELA != "tk":
        try:
            import login_web

            return login_web.executar(modo, conta)
        except Exception as exc:  # sem pywebview ou sem o WebView2: a tela antiga faz o mesmo
            aut.logger.warning("A tela de login web não abriu (%s); usando a de Tkinter.", exc)
    return executar_tk(modo, conta)


def executar_tk(modo: str = "entrar", conta: aut.ContaLocal | None = None) -> bool:
    """A tela de Tkinter. True se a pessoa entrou (ou terminou o cadastro)."""
    _dpi_nitido()
    conta = conta or aut.ContaLocal()
    if not conta.existe():
        return JanelaDeCadastro(conta).executar()
    login = JanelaDeLogin(conta)
    if not login.executar():
        return False
    if modo == "cadastrar":
        return JanelaDeCadastro(conta).executar()
    if modo == "rosto" or login.resultado.refazer_rosto:
        # Entrou pelo código de recuperação (ou pediu): cadastra o rosto outra vez e troca o código, que aparece lá.
        # Se cancelar, continua logado (já provou quem é) e o código antigo segue valendo.
        JanelaDeCadastro(conta, so_rosto=True).executar()
    return True


if __name__ == "__main__":
    analisador = argparse.ArgumentParser(description="Login da Beatriz")
    grupo = analisador.add_mutually_exclusive_group()
    grupo.add_argument("--entrar", action="store_true", help="login (padrão)")
    grupo.add_argument("--cadastrar", action="store_true", help="refaz usuário, senha, rosto e código")
    grupo.add_argument("--rosto", action="store_true", help="refaz só o rosto")
    argumentos = analisador.parse_args()
    modo = "cadastrar" if argumentos.cadastrar else "rosto" if argumentos.rosto else "entrar"
    sys.exit(0 if executar(modo) else 1)
