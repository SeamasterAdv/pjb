"""Tela de login e de cadastro da Beatriz em interface web (pywebview), no visual do painel: a esfera em rede no
centro com a câmera dentro dela, a montanha de pontos com a sua voz e o dourado quando você entra.

A lógica de verdade continua em `autenticacao.py`. `ApiLogin` decide o que a tela mostra (`estado()`) e o que cada
botão faz; a página (interface_web/login.html) só desenha e chama. A câmera roda numa thread daqui: cada quadro passa
pela verificação (ou pelo cadastro) e vira um JPEG pequeno, cinza e espelhado, que a página busca (`quadro()`). A
imagem e o som ficam só na memória. O microfone (LOGIN_MICROFONE) só mede o nível da voz, para a montanha.

O login_tela.py abre esta tela; sem o pywebview (ou com LOGIN_TELA=tk), abre a de Tkinter. Os nomes que começam com
"_" não ficam visíveis para o JavaScript: o pywebview expõe os métodos públicos e também os de objetos guardados em
atributos públicos, por isso tudo o que não é da página fica em atributos com "_".
"""
import base64
import logging
import os
import threading
import time

import autenticacao as aut
from config import LOGIN_MICROFONE, LOGIN_TENTATIVAS

logger = logging.getLogger(__name__)

PASTA_PROJETO = os.path.dirname(os.path.abspath(__file__))
PAGINA = os.path.join(PASTA_PROJETO, "interface_web", "login.html")
PASTA_ARMAZENAMENTO = os.path.join(PASTA_PROJETO, "banco_memoria", "painel_web")
LARGURA_DA_IMAGEM = 480  # a câmera vai à página reduzida (é só para você se ver dentro da esfera)
QUALIDADE_JPEG = 72
VALIDADE_DO_NIVEL = 0.35  # segundos: medida do microfone mais velha que isso vale silêncio

MENSAGEM_DO_CODIGO = ("Anote este código de recuperação em um lugar seguro. Ele entra no lugar do rosto se a câmera "
                      "falhar e só aparece agora.")


class ApiLogin:
    """O que a página do login chama. Cada método devolve dados simples (viram JSON na ponte)."""

    def __init__(self, conta: aut.ContaLocal, pedido: str = "entrar", reconhecedor=None, camera=None, fluxo=None,
                 microfone: bool = LOGIN_MICROFONE):
        self._conta = conta
        self._pedido = pedido  # "entrar", "cadastrar" ou "rosto" (como o login_tela.py foi chamado)
        self._reconhecedor = reconhecedor or aut.ReconhecedorFacial()
        self._camera = camera or aut.Camera()
        self._fluxo = fluxo or aut.FluxoDeEntrada(conta)
        self._usar_microfone = microfone
        self._trava = threading.RLock()
        self._janela = None
        self._resultado: aut.Resultado | None = None
        self._sucesso = False  # entrou (ou terminou o cadastro): é o código de saída do login_tela.py
        self._sucesso_se_cancelar = False  # já provou quem é (entrou para refazer o rosto): fechar não desfaz
        self._terminou = False
        self._tela = {}  # o que a página mostra (veja estado())
        # cadastro
        self._so_rosto = False
        self._usuario_novo, self._senha_nova = "", ""
        self._cadastro: aut.CadastroDeRosto | None = None
        self._vetores, self._sinal = [], 1
        self._codigo_novo = ""
        # câmera
        self._processador = None  # quadro -> EstadoDoRosto, quando a câmera deve trabalhar
        self._verificacao: aut.VerificacaoDeRosto | None = None
        self._preparo = None  # None (sem câmera), "preparando", "ok" ou "erro"
        self._fechar_camera = False
        self._geracao_da_camera = 0  # cada abertura da câmera tem a sua thread; as antigas só fecham e saem
        self._thread_da_camera = None
        self._jpeg, self._numero_do_quadro = b"", 0
        self._nivel = None  # (quando, nivel, db, bandas) do microfone

    # ------------------------------------------------------------------ chamadas da página
    def inicio(self) -> dict:
        """Começa: o cadastro se ainda não há login, senão a entrada."""
        with self._trava:
            if not self._tela:
                if self._conta.existe():
                    self._abrir_entrada()
                else:
                    self._abrir_cadastro(so_rosto=False)
                if self._usar_microfone:
                    threading.Thread(target=self._laco_do_microfone, name="login-microfone", daemon=True).start()
            return self.estado()

    def estado(self) -> dict:
        with self._trava:
            tela = dict(self._tela)
            tela.update(sucesso=self._sucesso, terminou=self._terminou, usuario=self._conta.usuario or self._usuario_novo,
                        modo=self._fluxo.modo, falhas=self._conta.falhas(), tentativas=LOGIN_TENTATIVAS,
                        camera=self._preparo or "", codigo_novo=self._codigo_novo if tela.get("pagina") == "codigo" else "")
            bloqueio = self._fluxo.bloqueio() if tela.get("pagina") == "entrar" else 0.0
            tela["bloqueio"] = round(bloqueio, 1)
            if bloqueio:
                tela.update(mensagem=f"Muitas tentativas erradas. Tente de novo em {int(bloqueio) + 1} s.", tom="erro")
            return tela

    def quadro(self, ultimo: int = -1) -> dict:
        """A imagem mais nova da câmera (data URL); só o número, se a página já tem esta."""
        with self._trava:
            numero, jpeg = self._numero_do_quadro, self._jpeg
        if numero == ultimo or not jpeg:
            return {"n": numero}
        return {"n": numero, "imagem": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")}

    def niveis(self) -> dict:
        """O nível da sua voz no microfone agora, para a montanha (o mesmo formato do painel)."""
        nivel = self._nivel
        if nivel is None or time.time() - nivel[0] > VALIDADE_DO_NIVEL:
            return {"voce": None}
        return {"voce": {"nivel": round(nivel[1], 3), "db": round(nivel[2], 1), "bandas": [round(b, 3) for b in nivel[3]]}}

    # -- entrada
    def mostrar_senha(self) -> dict:
        with self._trava:
            self._tela.update(pede_senha=True, link_senha=False)
            return self.estado()

    def abrir_codigo(self) -> dict:
        with self._trava:
            self._tela.update(pede_codigo=True, link_codigo=False)
            return self.estado()

    def enviar_codigo(self, codigo: str) -> dict:
        with self._trava:
            resultado = self._fluxo.usar_codigo(codigo or "")
            if self._fluxo.codigo_ok:
                self._processador = None
                self._tela.update(pede_codigo=False, pede_senha=True, link_senha=False, link_codigo=False)
                self._dizer(resultado.mensagem, "ok")
            else:
                self._dizer(resultado.mensagem, "erro")
            return self.estado()

    def enviar_senha(self, usuario: str, senha: str) -> dict:
        with self._trava:
            resultado = self._fluxo.entrar(usuario or "", senha or "")
            if resultado.ok:
                self._entrou(resultado)
            else:
                self._dizer(resultado.mensagem, "erro")
            return self.estado()

    def tentar_rosto_de_novo(self) -> dict:
        with self._trava:
            if self._fluxo.bloqueio() or self._tela.get("pagina") != "entrar":
                return self.estado()
            self._preparar_camera()
            self._iniciar_verificacao()
            return self.estado()

    # -- cadastro
    def confirmar_credenciais(self, usuario: str, senha: str, repetida: str) -> dict:
        with self._trava:
            usuario, senha, repetida = (usuario or "").strip(), senha or "", repetida or ""
            if len(aut.normalizar_usuario(usuario)) < 3:
                self._dizer("O usuário precisa ter pelo menos 3 letras.", "erro")
            elif problema := aut.validar_senha_nova(senha, usuario):
                self._dizer(problema, "erro")
            elif senha != repetida:
                self._dizer("As duas senhas não são iguais.", "erro")
            else:
                self._usuario_novo, self._senha_nova = usuario, senha
                self._abrir_rosto_do_cadastro()
            return self.estado()

    def sem_rosto(self) -> dict:
        """Cadastra só com a senha (o rosto pode ser refeito depois com login_tela.py --rosto)."""
        with self._trava:
            if self._tela.get("pagina") == "rosto":
                self._vetores, self._sinal = [], 1
                self._abrir_pagina_do_codigo()
            return self.estado()

    def concluir(self, guardou: bool) -> dict:
        with self._trava:
            if self._tela.get("pagina") != "codigo":
                return self.estado()
            if not guardou:
                self._dizer("Marque que guardou o código antes de concluir.", "erro")
                return self.estado()
            try:
                if self._so_rosto:
                    self._conta.refazer_rosto(self._vetores, self._sinal)
                    self._conta.novo_codigo(self._codigo_novo)
                else:
                    self._conta.criar(self._usuario_novo, self._senha_nova, self._vetores, self._sinal, self._codigo_novo)
            except ValueError as exc:
                self._dizer(str(exc), "erro")
                return self.estado()
            self._senha_nova = ""
            self._codigo_novo = ""
            self._terminar(True, "Pronto! O seu login foi criado." if not self._so_rosto else "Pronto! O seu rosto foi cadastrado.")
            return self.estado()

    # -- janela
    def fechar(self) -> None:
        """A página terminou a animação de saída (ou a pessoa desistiu): fecha a janela."""
        with self._trava:
            if not self._terminou:
                self._terminar(self._sucesso_se_cancelar, "")
        if self._janela is not None:
            self._janela.destroy()

    # ------------------------------------------------------------------ telas
    def _dizer(self, mensagem: str, tom: str = "normal"):
        self._tela.update(mensagem=mensagem, tom=tom)

    def _abrir_entrada(self):
        self._tela = {"pagina": "entrar", "mostra_video": False, "pede_senha": False, "pede_codigo": False,
                      "link_senha": False, "link_codigo": False, "link_de_novo": False, "progresso": 0.0,
                      "fase": "", "caixa": None, "mensagem": "", "tom": "normal"}
        if self._fluxo.usa_rosto:
            self._tela.update(link_codigo=True, link_senha=self._fluxo.entra_so_com_o_rosto)
            self._preparar_camera()
            self._iniciar_verificacao()
        else:
            self._tela.update(pede_senha=True)
            self._dizer("Digite o usuário e a senha.")

    def _iniciar_verificacao(self):
        self._verificacao = self._fluxo.verificacao(self._reconhecedor)
        self._processador = self._verificacao.passo
        self._tela.update(mostra_video=True, link_de_novo=False, progresso=0.0, fase="procurar")

    def _entrou(self, resultado: aut.Resultado):
        self._resultado = resultado
        self._processador = None
        if self._pedido == "cadastrar":  # refazer tudo: provou quem é, agora o cadastro completo
            self._abrir_cadastro(so_rosto=False)
        elif self._pedido == "rosto" or resultado.refazer_rosto:
            # Entrou pelo código de recuperação (ou pediu): cadastra o rosto outra vez e troca o código.
            # Se fechar, continua logado (já provou quem é) e o código antigo segue valendo.
            self._sucesso_se_cancelar = True
            self._abrir_cadastro(so_rosto=True)
            if resultado.refazer_rosto:
                self._dizer("Você entrou com o código. Agora vamos cadastrar o seu rosto de novo.", "ok")
        else:
            self._fechar_camera = True
            self._terminar(True, f"Bem-vindo, {self._conta.usuario.title()}.")

    def _terminar(self, sucesso: bool, mensagem: str):
        self._sucesso = sucesso
        self._terminou = True
        self._processador = None
        self._fechar_camera = True
        self._tela.update(progresso=1.0 if sucesso else self._tela.get("progresso", 0.0), pede_senha=False, pede_codigo=False,
                          link_senha=False, link_codigo=False, link_de_novo=False)
        if mensagem:
            self._dizer(mensagem, "ok" if sucesso else "normal")

    def _abrir_cadastro(self, so_rosto: bool):
        self._so_rosto = so_rosto
        self._usuario_novo = self._conta.usuario if so_rosto else ""
        self._codigo_novo = aut.gerar_codigo_recuperacao()
        if so_rosto:
            self._abrir_rosto_do_cadastro()
            return
        self._processador = None
        self._fechar_camera = True
        self._tela = {"pagina": "credenciais", "mostra_video": False, "progresso": 0.0, "fase": "", "caixa": None}
        self._dizer("Escolha o usuário e uma senha (mínimo 8 caracteres). Ela entra no lugar do rosto quando a câmera falhar.")

    def _abrir_rosto_do_cadastro(self):
        self._tela = {"pagina": "rosto", "mostra_video": True, "progresso": 0.0, "fase": "frente", "caixa": None}
        self._dizer("Vamos cadastrar o seu rosto." if self._so_rosto else "Agora o seu rosto.")
        self._cadastro = aut.CadastroDeRosto(self._reconhecedor)
        self._preparar_camera()
        self._processador = self._cadastro.passo

    def _abrir_pagina_do_codigo(self):
        self._processador = None
        self._fechar_camera = True
        self._tela = {"pagina": "codigo", "mostra_video": False, "progresso": 1.0, "fase": "", "caixa": None}
        self._dizer(MENSAGEM_DO_CODIGO, "aviso")

    # ------------------------------------------------------------------ câmera
    def _preparar_camera(self):
        """Carrega os modelos (baixa na primeira vez) e abre a câmera numa thread, sem travar a página."""
        if self._preparo in ("preparando", "ok") and not self._fechar_camera:
            return  # já aberta (ou abrindo): só troca o que se faz com os quadros
        self._fechar_camera = False
        self._geracao_da_camera += 1
        anterior = self._thread_da_camera
        self._preparo = "preparando"
        self._dizer("Preparando o reconhecimento facial... (na primeira vez baixa ~39 MB)")
        self._thread_da_camera = threading.Thread(target=self._trabalhar_camera, args=(self._geracao_da_camera, anterior),
                                                  name="login-camera", daemon=True)
        self._thread_da_camera.start()

    def _camera_vale(self, geracao: int) -> bool:
        return geracao == self._geracao_da_camera and not self._fechar_camera and not self._terminou

    def _trabalhar_camera(self, geracao: int, anterior=None):
        if anterior is not None:
            anterior.join(5)  # a abertura anterior fecha a câmera antes desta abrir
        try:
            self._reconhecedor.preparar()
        except Exception as exc:
            return self._erro_de_preparo(geracao, f"Não consegui preparar o reconhecimento facial: {exc}")
        if not self._camera.abrir():
            return self._erro_de_preparo(geracao, "Não consegui abrir a câmera. Veja se outro programa a está usando.")
        with self._trava:
            if not self._camera_vale(geracao):  # a tela mudou enquanto abria
                self._camera.fechar()
                if geracao == self._geracao_da_camera:
                    self._preparo = None
                return
            self._preparo = "ok"
        self._laco_da_camera(geracao)

    def _laco_da_camera(self, geracao: int):
        """Só esta thread mexe na câmera depois de aberta (fechar no meio de uma leitura derruba o OpenCV)."""
        try:
            while True:
                with self._trava:
                    if not self._camera_vale(geracao):
                        break
                    processador = self._processador
                quadro = self._camera.ler()
                if quadro is None:
                    time.sleep(0.03)
                    continue
                estado = processador(quadro) if processador is not None else None
                jpeg = _imagem_para_a_pagina(quadro)
                with self._trava:
                    self._jpeg, self._numero_do_quadro = jpeg, self._numero_do_quadro + 1
                    if estado is not None and processador is self._processador:
                        self._aplicar_estado(quadro, estado)
                time.sleep(0.005)
        except Exception as exc:
            logger.warning("Erro na câmera do login: %s", exc)
            with self._trava:
                self._dizer(f"Erro na câmera: {exc}", "erro")
        finally:
            self._camera.fechar()
            with self._trava:
                if geracao == self._geracao_da_camera:
                    self._preparo = None
                    self._jpeg = b""

    def _erro_de_preparo(self, geracao: int, mensagem: str):
        with self._trava:
            if geracao != self._geracao_da_camera:
                return
            self._preparo = "erro"
            self._processador = None
            pagina = self._tela.get("pagina")
            if pagina == "entrar":
                self._tela.update(link_de_novo=True, mostra_video=False)
                if self._fluxo.entra_so_com_o_rosto:  # câmera ou modelos não carregaram: vai direto para a senha
                    self._tela.update(pede_senha=True, link_senha=False)
                    self._dizer(mensagem + " Digite o usuário e a senha para entrar.", "erro")
                else:
                    self._dizer(mensagem + " Use o código de recuperação para entrar sem o rosto.", "erro")
            elif pagina == "rosto":
                self._dizer(mensagem + " Você pode cadastrar só com a senha e refazer o rosto depois.", "erro")

    def _aplicar_estado(self, quadro, estado: aut.EstadoDoRosto):
        self._tela.update(progresso=estado.progresso, fase=estado.fase, caixa=_caixa_espelhada(quadro, estado.rosto))
        if self._tela.get("pagina") == "rosto":
            return self._estado_do_cadastro(estado)
        if self._fluxo.bloqueio():
            return
        if estado.fase == "ok":
            self._processador = None
            resultado = self._fluxo.rosto_confirmado()
            if resultado.ok:
                return self._entrou(resultado)
            self._tela.update(pede_senha=True, link_senha=False)
            self._dizer("Rosto confirmado. Agora digite a senha.", "ok")
        elif estado.fase == "falhou":
            self._processador = None
            resultado = self._fluxo.rosto_falhou(self._verificacao.viu_rosto if self._verificacao else True)
            if resultado.bloqueio:
                self._dizer(resultado.mensagem, "erro")
                return
            pede_senha = " Digite o usuário e a senha." if self._fluxo.entra_so_com_o_rosto else ""
            self._dizer((estado.mensagem or resultado.mensagem) + pede_senha, "erro")
            self._tela.update(link_de_novo=True)
            if self._fluxo.entra_so_com_o_rosto:  # não era você (ou não deu tempo): cai na senha
                self._tela.update(pede_senha=True, link_senha=False)
        else:
            self._dizer(estado.mensagem)

    def _estado_do_cadastro(self, estado: aut.EstadoDoRosto):
        self._dizer(estado.mensagem, "ok" if estado.fase == "pronto" else "normal")
        if estado.fase == "pronto":
            self._vetores, self._sinal = self._cadastro.vetores, self._cadastro.sinal_esquerda
            self._abrir_pagina_do_codigo()

    # ------------------------------------------------------------------ microfone (só o nível, para a montanha)
    def _laco_do_microfone(self):
        try:
            import numpy as np
            import speech_recognition as sr

            import niveis_voz

            with sr.Microphone() as fonte:
                while not self._terminou:
                    dados = fonte.stream.read(fonte.CHUNK)
                    if fonte.SAMPLE_WIDTH != 2:
                        break
                    nivel, db, bandas = niveis_voz.medir(np.frombuffer(dados, dtype=np.int16) / 32768.0, fonte.SAMPLE_RATE)
                    self._nivel = (time.time(), nivel, db, bandas)
        except Exception as exc:  # sem microfone: a montanha fica parada, o login segue
            logger.info("Login sem o microfone: %s", exc)


def _imagem_para_a_pagina(quadro) -> bytes:
    """O quadro da câmera espelhado (como um espelho), em cinza e reduzido, em JPEG."""
    import cv2

    altura, largura = quadro.shape[:2]
    escala = LARGURA_DA_IMAGEM / max(1, largura)
    imagem = cv2.cvtColor(cv2.flip(quadro, 1), cv2.COLOR_BGR2GRAY)
    if escala < 1:
        imagem = cv2.resize(imagem, (LARGURA_DA_IMAGEM, int(altura * escala)), interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", imagem, [cv2.IMWRITE_JPEG_QUALITY, QUALIDADE_JPEG])
    return jpeg.tobytes() if ok else b""


def _caixa_espelhada(quadro, rosto) -> list | None:
    """Onde está o rosto na imagem espelhada, de 0 a 1: [x, y, largura, altura]."""
    if rosto is None or not hasattr(quadro, "shape"):
        return None
    altura, largura = quadro.shape[:2]
    x, y, w, h = rosto.caixa
    return [round(1 - (x + w) / largura, 4), round(y / altura, 4), round(w / largura, 4), round(h / altura, 4)]


def executar(pedido: str = "entrar", conta: aut.ContaLocal | None = None) -> bool:
    """Abre a janela e só volta quando ela fecha. True se a pessoa entrou (ou terminou o cadastro)."""
    import webview

    api = ApiLogin(conta or aut.ContaLocal(), pedido)
    titulo = "Beatriz — Cadastro" if not api._conta.existe() else "Beatriz — Entrar"
    janela = webview.create_window(titulo, url=PAGINA, js_api=api, width=1240, height=800, min_size=(1000, 680),
                                   background_color="#000000", on_top=True)
    api._janela = janela

    def ao_mostrar():
        time.sleep(0.6)  # abre na frente (o launcher roda sem janela própria) e depois deixa de ficar por cima
        janela.on_top = False

    janela.events.shown += lambda: threading.Thread(target=ao_mostrar, daemon=True).start()
    os.makedirs(PASTA_ARMAZENAMENTO, exist_ok=True)
    webview.start(gui="edgechromium", private_mode=False, storage_path=PASTA_ARMAZENAMENTO)
    with api._trava:  # fechou pelo X: vale o que já tinha sido conquistado
        if not api._terminou:
            api._terminar(api._sucesso_se_cancelar, "")
        api._fechar_camera = True
    return api._sucesso
