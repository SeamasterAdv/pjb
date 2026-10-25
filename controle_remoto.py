"""Controle remoto universal infravermelho pela rede: o Ekaza "Controle Remoto Universal Mini 3" (EKNH-T304-M3), um
aparelho Tuya/Smart Life com Wi-Fi que manda os sinais de controle remoto para a TV e o ar-condicionado.

Dois caminhos:
- **local** (`ControleLocalTuya`, o padrão): pelo tinytuya, direto na rede de casa, sem passar pela nuvem. Manda os códigos
  aprendidos do controle original (em Base64, o formato do próprio aparelho) e aprende novos. Precisa do id do aparelho e
  da chave local, que o `python -m tinytuya wizard` tira uma vez da conta Tuya (rodado em `banco_memoria/tuya`, os arquivos
  dele são lidos sozinhos por `dados_do_wizard`). O IP é descoberto na rede e guardado.
- **nuvem da Tuya** (`ControleNuvemTuya`, opcional): os controles virtuais cadastrados no app (a TV e o ar pela marca), pela
  API de infravermelho da Tuya. Serve sobretudo para o ar, que manda o estado inteiro em cada código ("ligado, frio, 22
  graus"): pela nuvem não é preciso aprender cada temperatura. Só o comando sai do PC.

`RegistroDoControle` guarda os códigos aprendidos, o estado dos aparelhos (o infravermelho não tem volta: a Beatriz lembra
o que mandou) e o que veio da nuvem em `banco_memoria/controle_remoto.json`. O arquivo é lido a cada uso, porque o processo
principal e os painéis têm cada um o seu agente.
"""
import json
import logging
import os
import re
import threading
import time

logger = logging.getLogger(__name__)

CATEGORIA_TUYA_IR = "wnykq"  # categoria dos controles infravermelhos na Tuya (a que o wizard mostra)
CATEGORIA_NUVEM_TV = 2  # category_id dos controles virtuais na API de infravermelho
CATEGORIA_NUVEM_AR = 5
# Os controles virtuais do app como sub-aparelhos do Ekaza (categoria Tuya -> category_id da API de infravermelho).
CATEGORIAS_DOS_SUBAPARELHOS = {"infrared_tv": CATEGORIA_NUVEM_TV, "infrared_ac": CATEGORIA_NUVEM_AR}
MODOS_DO_AR = {"frio": 0, "quente": 1, "auto": 2, "ventilar": 3, "seco": 4}  # mode da API da Tuya
VENTILACOES = {"auto": 0, "baixa": 1, "media": 2, "alta": 3}  # wind da API da Tuya
INTERVALO_ENTRE_TOQUES = 0.35  # segundos entre dois toques seguidos (volume, os números do canal)
ESPERA_DEPOIS_DO_ENVIO = 0.3  # a conexão fica aberta esse tempo depois de cada comando


class ErroControle(Exception):
    """Mensagem para o usuário: o controle não respondeu, a nuvem recusou..."""


# ====================================================================== registro
class RegistroDoControle:
    """O JSON dos códigos e do estado. Cada operação lê o arquivo de novo e grava de uma vez (arquivo temporário +
    os.replace), porque o processo principal e os painéis mexem no mesmo arquivo."""

    def __init__(self, caminho: str):
        self.caminho = caminho
        self._trava = threading.RLock()

    def ler(self) -> dict:
        with self._trava:
            try:
                with open(self.caminho, "r", encoding="utf-8") as arquivo:
                    dados = json.load(arquivo)
            except FileNotFoundError:
                dados = {}
            except (OSError, ValueError) as exc:
                logger.warning("Não li %s: %s", self.caminho, exc)
                dados = {}
            dados.setdefault("aparelhos", {})
            for nome in ("tv", "ar"):
                dados["aparelhos"].setdefault(nome, {})
                dados["aparelhos"][nome].setdefault("botoes", {})
                dados["aparelhos"][nome].setdefault("estado", {})
            dados.setdefault("rede", {})
            dados.setdefault("nuvem", {})
            return dados

    def gravar(self, dados: dict) -> None:
        with self._trava:
            pasta = os.path.dirname(self.caminho)
            if pasta:
                os.makedirs(pasta, exist_ok=True)
            temporario = f"{self.caminho}.{os.getpid()}.tmp"
            with open(temporario, "w", encoding="utf-8") as arquivo:
                json.dump(dados, arquivo, ensure_ascii=False, indent=2)
            os.replace(temporario, self.caminho)

    def mudar(self, funcao):
        """Lê, aplica `funcao(dados)` e grava; devolve o que a função devolveu."""
        with self._trava:
            dados = self.ler()
            resultado = funcao(dados)
            self.gravar(dados)
            return resultado

    # ------------------------------------------------------------ atalhos
    def botoes(self, aparelho: str) -> dict:
        return dict(self.ler()["aparelhos"][aparelho]["botoes"])

    def guardar_botao(self, aparelho: str, botao: str, codigo: str) -> None:
        self.mudar(lambda d: d["aparelhos"][aparelho]["botoes"].__setitem__(botao, codigo))

    def esquecer_botao(self, aparelho: str, botao: str) -> bool:
        return self.mudar(lambda d: d["aparelhos"][aparelho]["botoes"].pop(botao, None) is not None)

    def estado(self, aparelho: str) -> dict:
        return dict(self.ler()["aparelhos"][aparelho]["estado"])

    def guardar_estado(self, aparelho: str, **campos) -> None:
        self.mudar(lambda d: d["aparelhos"][aparelho]["estado"].update(campos))


# ====================================================================== Sony
# TVs Sony usam o protocolo SIRC (público): os códigos podem ser montados aqui, sem o controle original e sem a nuvem.
# O usuário não tem o controle físico da TV (uma Sony) e, pela nuvem, o Power às vezes não pegava (out/2026). A Sony tem
# códigos separados para ligar (46) e desligar (47), então "desliga a TV" nunca liga. Testado na TV dele: mandado em
# 40 kHz (a portadora da Sony, no formato cabeça + chave do Ekaza) pegou sempre; cru, na portadora padrão do Ekaza
# (38 kHz), um mudo pegou e o outro não.
SONY_TV = {
    **{f"digito_{n}": (n - 1) % 10 for n in range(10)},  # o 1 é o comando 0... o 0 é o 9
    "canal_mais": 16, "canal_menos": 17, "volume_mais": 18, "volume_menos": 19, "mudo": 20, "energia": 21,
    "entrada": 37, "ligar": 46, "desligar": 47,
}
SONY_ENDERECO_TV = 1
# A TV só aceita o comando repetido (o controle original manda enquanto o botão está apertado); 5 quadros (225 ms) são
# um toque curto: o mudo e o Power não viram dois toques.
SONY_REPETICOES = 5
SONY_PORTADORA_KHZ = 40


def pulsos_sony(comando: int, endereco: int = SONY_ENDERECO_TV, repeticoes: int = SONY_REPETICOES) -> list[int]:
    """Os pulsos (microssegundos, aceso/apagado alternados) do SIRC de 12 bits: cabeçalho de 2400, cada bit "1" aceso
    1200 e "0" aceso 600, apagado 600 entre eles; 7 bits do comando e 5 do endereço, o menos significativo primeiro;
    um quadro a cada 45 ms."""
    quadro = [2400, 600]
    for valor, bits in ((comando, 7), (endereco, 5)):
        for indice in range(bits):
            quadro += [1200 if valor >> indice & 1 else 600, 600]
    pulsos = []
    for vez in range(repeticoes):
        atual = list(quadro)
        if vez < repeticoes - 1:
            atual[-1] = 45000 - sum(quadro[:-1])  # o último apagado completa os 45 ms do quadro
        else:
            atual.pop()  # termina aceso, como os códigos aprendidos
        pulsos += atual
    return pulsos


def codigo_sony(botao: str) -> tuple[str, str] | None:
    """O código de um botão da TV Sony no formato cabeça + chave do Ekaza (a cabeça leva a portadora de 40 kHz), ou None.
    `ControleLocalTuya.enviar` manda as tuplas com `send_key` e os textos (códigos aprendidos) com `send_button`."""
    from tinytuya.Contrib.IRRemoteControlDevice import IRRemoteControlDevice

    if botao not in SONY_TV:
        return None
    cabeca, chave = IRRemoteControlDevice.pulses_to_head_key(pulsos_sony(SONY_TV[botao]), freq=SONY_PORTADORA_KHZ)
    return cabeca, chave


# ====================================================================== wizard
def dados_do_wizard(pasta: str) -> dict:
    """O que o `python -m tinytuya wizard` gravou na pasta: o controle infravermelho de devices.json (id, chave local, IP,
    versão) e as chaves da API de tinytuya.json. Vazio se o wizard não rodou ali."""
    resultado = {}
    try:
        with open(os.path.join(pasta, "devices.json"), "r", encoding="utf-8") as arquivo:
            aparelhos = json.load(arquivo)
    except (OSError, ValueError):
        aparelhos = []
    controles = [a for a in aparelhos if isinstance(a, dict) and a.get("category") == CATEGORIA_TUYA_IR and a.get("key")]
    if not controles:  # sem a categoria (versões antigas do wizard), o primeiro que tem "controle"/"remote"/"ir" no nome
        controles = [a for a in aparelhos if isinstance(a, dict) and a.get("key")
                     and re.search(r"controle|remote|\bir\b|universal|ekaza", str(a.get("name", "")), re.I)]
    if controles:
        controle = controles[0]
        resultado.update(id=controle.get("id", ""), chave_local=controle.get("key", ""), ip=controle.get("ip", "") or "",
                         versao=str(controle.get("version", "") or ""), nome=controle.get("name", ""))
    try:
        with open(os.path.join(pasta, "tinytuya.json"), "r", encoding="utf-8") as arquivo:
            api = json.load(arquivo)
        resultado.update(regiao=api.get("apiRegion", ""), chave_api=api.get("apiKey", ""),
                         segredo_api=api.get("apiSecret", ""))
    except (OSError, ValueError):
        pass
    return resultado


# ====================================================================== local
# Os erros do tinytuya (error_helper.error_codes) que ela fala, em português.
ERROS_TUYA = {
    "901": "não consegui conectar",
    "902": "ele demorou demais para responder",
    "904": "a versão do protocolo não bate (confira CONTROLE_VERSAO)",
    "905": "ele está fora de alcance",
    "914": "a chave local ou a versão não batem (confira CONTROLE_CHAVE_LOCAL e CONTROLE_VERSAO)",
}
ERROS_DE_REDE = {"901", "902", "905"}  # nem chegou a conversar: trocar de versão ou de tipo não adianta
# Versões do protocolo Tuya, a mais nova primeiro. O Ekaza Mini 3 do usuário é 3.5 (out/2026).
VERSOES = ("3.5", "3.4", "3.3")
PORTA_TUYA = 6668


def hosts_com_porta_aberta(faixa: str, porta: int, espera: float = 0.6) -> list[str]:
    """Os IPs da faixa ("192.168.0") que aceitam conexão na porta, em ordem (~1 s para os 254)."""
    import socket
    from concurrent.futures import ThreadPoolExecutor

    def aberta(ip):
        try:
            with socket.create_connection((ip, porta), timeout=espera):
                return ip
        except OSError:
            return None

    with ThreadPoolExecutor(max_workers=64) as executor:
        return [ip for ip in executor.map(aberta, (f"{faixa}.{n}" for n in range(1, 255))) if ip]


def _erro_tuya(resposta) -> str:
    """A mensagem de erro que o tinytuya devolve como dicionário ({"Error": ..., "Err": "901"}), ou ""."""
    if isinstance(resposta, dict) and ("Err" in resposta or "Error" in resposta):
        return ERROS_TUYA.get(str(resposta.get("Err")), str(resposta.get("Error") or resposta.get("Err")))
    return ""


def _atendeu(resposta) -> bool:
    """Se o aparelho respondeu com a chave e a versão certas. O Ekaza não dá o estado: ao pedido ele responde
    "json obj data unvalid" (erro 900 do tinytuya), o que já mostra que a conversa cifrada funcionou."""
    if isinstance(resposta, dict) and "dps" in resposta:
        return True
    return isinstance(resposta, dict) and str(resposta.get("Err")) == "900"


class TipoDesconhecido(ErroControle):
    """O controle responde, mas ainda não se sabe se é dos antigos (DPs 201/202) ou dos novos (DPs 1 a 13)."""


class ControleLocalTuya:
    """O controle infravermelho na rede de casa, pelo tinytuya (`Contrib.IRRemoteControlDevice`).

    Cada operação abre a conexão, faz o que tem de fazer e fecha: os aparelhos Tuya aceitam poucas conexões ao mesmo tempo,
    e o processo principal e os painéis usam o mesmo controle. O que for descoberto (IP, versão do protocolo e tipo) fica
    guardado no registro."""

    def __init__(self, id_aparelho: str, chave_local: str, ip: str = "", versao: str = "", tipo: int = 0,
                 registro: RegistroDoControle | None = None, tempo_limite: float = 4.0):
        self.id = id_aparelho
        self.chave = chave_local
        self.ip_fixo = ip
        self.versao_fixa = versao
        self.tipo_fixo = int(tipo or 0)
        self.registro = registro
        self.tempo_limite = tempo_limite

    # ------------------------------------------------------------ rede
    def _rede(self) -> dict:
        return self.registro.ler()["rede"] if self.registro else {}

    def _guardar_rede(self, **campos) -> None:
        if self.registro:
            self.registro.mudar(lambda d: d["rede"].update(campos))

    def _descobrir_ip(self) -> tuple[str, str]:
        """Procura o controle na rede pelo id (o broadcast dos aparelhos Tuya; ~5 a 18 s) e guarda o IP e a versão.
        O broadcast não passa de um roteador para outro: na casa do usuário o PC está no cabo (192.168.1.x) e o Wi-Fi
        do Ekaza é de outro roteador (192.168.0.x), então lá o IP fica guardado no registro ou vai em CONTROLE_IP."""
        import tinytuya

        achado = tinytuya.find_device(self.id)
        if not achado or not achado.get("ip"):
            ip = self._procurar_na_faixa_antiga()
            if ip:
                return ip, self._rede().get("versao", "")
            raise ErroControle("não achei o controle na rede de casa: confira se ele está na tomada e no Wi-Fi "
                               "(a luz azul fica acesa), ou ponha o IP dele em CONTROLE_IP no .env")
        versao = str(achado.get("version") or "")
        self._guardar_rede(ip=achado["ip"], **({"versao": versao} if versao else {}))
        return achado["ip"], versao

    def _procurar_na_faixa_antiga(self) -> str:
        """Sem o broadcast (outro roteador no meio), procura na faixa do último IP conhecido (x.y.z.1 a 254) quem atende na
        porta 6668 dos aparelhos Tuya e fica com o que responde com esta chave. Guarda o IP achado."""
        antigo = self._rede().get("ip", "")
        if not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", antigo):
            return ""
        faixa = antigo.rsplit(".", 1)[0]
        versoes = [self._rede().get("versao")] if self._rede().get("versao") else list(VERSOES)
        for ip in hosts_com_porta_aberta(faixa, PORTA_TUYA):
            for versao in versoes:
                if _atendeu(self._testar(ip, versao)):
                    self._guardar_rede(ip=ip, versao=versao)
                    logger.info("Controle achado em %s (antes em %s)", ip, antigo)
                    return ip
        return ""

    def _aparelho_simples(self, ip: str, versao: str):
        """Um tinytuya.Device comum, só para conferir a conexão e descobrir a versão (não manda infravermelho)."""
        import tinytuya

        return tinytuya.Device(self.id, ip, self.chave, version=float(versao), connection_timeout=self.tempo_limite,
                               connection_retry_limit=1, connection_retry_delay=1)

    def _testar(self, ip: str, versao: str):
        aparelho = self._aparelho_simples(ip, versao)
        try:
            return aparelho.status()
        finally:
            try:
                aparelho.close()
            except Exception:
                pass

    def _descobrir_versao(self, ip: str) -> str:
        """Testa as versões do protocolo, a mais nova primeiro, e guarda a primeira que o aparelho entende."""
        resposta = None
        for versao in VERSOES:
            resposta = self._testar(ip, versao)
            if _atendeu(resposta):
                self._guardar_rede(versao=versao)
                return versao
            if isinstance(resposta, dict) and str(resposta.get("Err")) in ERROS_DE_REDE:
                break
        raise ErroControle(f"o controle não respondeu na rede ({_erro_tuya(resposta) or 'sem resposta'})")

    def _versao(self, ip: str) -> str:
        return self.versao_fixa or self._rede().get("versao", "") or self._descobrir_versao(ip)

    def _tipo(self) -> int:
        return self.tipo_fixo or int(self._rede().get("tipo", 0) or 0)

    def tipo_conhecido(self) -> bool:
        """Se já se sabe o tipo do controle (pela nuvem ou pelo primeiro botão aprendido)."""
        return bool(self._tipo())

    def definir_tipo(self, tipo: int) -> None:
        if tipo in (1, 2):
            self._guardar_rede(tipo=tipo)

    def _aparelho(self, ip: str, versao: str, tipo: int):
        from tinytuya.Contrib.IRRemoteControlDevice import IRRemoteControlDevice

        class ComRetorno(IRRemoteControlDevice):
            """Guarda o que o envio devolveu: o send_command do tinytuya descarta, e é ali que vem o "não respondeu".
            Os controles antigos mandam por set_value (DP 201); os novos, por set_multiple_values (DPs 1 a 13)."""
            ultimo_retorno = None

            def set_value(self, index, value, nowait=False):
                self.ultimo_retorno = super().set_value(index, value, nowait)
                return self.ultimo_retorno

            def set_multiple_values(self, data, nowait=False):
                self.ultimo_retorno = super().set_multiple_values(data, nowait)
                return self.ultimo_retorno

        # O tipo vai sempre dito: a detecção do tinytuya pede o estado, e o Ekaza não responde a esse pedido.
        return ComRetorno(self.id, ip, self.chave, version=float(versao), persist=True, control_type=tipo,
                          connection_timeout=self.tempo_limite, connection_retry_limit=1, connection_retry_delay=1)

    def _na_rede(self, funcao):
        """Roda `funcao(ip, versão)`; se o IP guardado não responder (o roteador pode ter trocado), procura de novo uma vez."""
        ip = self.ip_fixo or self._rede().get("ip", "")
        guardado = bool(ip) and not self.ip_fixo
        if not ip:
            ip, _ = self._descobrir_ip()
        try:
            return funcao(ip, self._versao(ip))
        except TipoDesconhecido:
            raise
        except ErroControle:
            if not guardado:
                raise
        logger.info("O controle não respondeu em %s; procurando na rede de novo", ip)
        ip, _ = self._descobrir_ip()
        return funcao(ip, self._versao(ip))

    @staticmethod
    def _fechar(aparelho) -> None:
        try:
            aparelho.close()
        except Exception:
            pass

    # ------------------------------------------------------------ operações
    def conferir(self) -> str:
        """O IP do controle, se ele responder; senão ErroControle."""
        def funcao(ip, versao):
            resposta = self._testar(ip, versao)
            if _atendeu(resposta):
                return ip
            raise ErroControle(f"o controle não respondeu na rede ({_erro_tuya(resposta) or 'sem resposta'})")

        return self._na_rede(funcao)

    def enviar(self, codigos, intervalo: float = INTERVALO_ENTRE_TOQUES) -> None:
        """Manda um ou mais códigos na mesma conexão: aprendidos (Base64) ou montados aqui ((cabeça, chave), os da Sony)."""
        codigos = [codigos] if isinstance(codigos, (str, tuple)) else list(codigos)
        tipo = self._tipo()
        if not tipo:
            raise TipoDesconhecido("ainda não sei o tipo deste controle: ensine um botão primeiro "
                                   "(\"aprende o botão ligar da TV\"), que eu descubro")

        def funcao(ip, versao):
            aparelho = self._aparelho(ip, versao, tipo)
            # O tinytuya manda sem esperar resposta (o Ekaza não confirma o envio: esperar custaria os 4 s do limite) e
            # fechava a conexão 0,01 s depois; com a pausa, o aparelho tem tempo de pegar o comando antes de fechar.
            aparelho.set_sendWait(ESPERA_DEPOIS_DO_ENVIO)
            try:
                for indice, codigo in enumerate(codigos):
                    if indice:
                        time.sleep(intervalo)
                    aparelho.ultimo_retorno = None
                    if isinstance(codigo, tuple):
                        aparelho.send_key(*codigo)
                    else:
                        aparelho.send_button(codigo)
                    erro = _erro_tuya(aparelho.ultimo_retorno)
                    if erro:
                        raise ErroControle(f"o controle não respondeu na rede ({erro})")
            finally:
                self._fechar(aparelho)

        self._na_rede(funcao)

    def aprender(self, segundos: int = 15) -> str | None:
        """Põe o controle no modo de aprender e espera um botão do controle original; devolve o código ou None.
        Sem saber o tipo, escuta metade do tempo como os controles antigos e metade como os novos, e guarda o que pegou."""
        conhecido = self._tipo()
        tipos = [conhecido] if conhecido else [1, 2]
        tempo = segundos if conhecido else max(6, segundos // 2 + 1)

        def funcao(ip, versao):
            for tipo in tipos:
                aparelho = self._aparelho(ip, versao, tipo)
                try:
                    codigo = aparelho.receive_button(timeout=tempo)
                finally:
                    self._fechar(aparelho)
                if isinstance(codigo, str) and len(codigo) >= 8:
                    if not conhecido:
                        self._guardar_rede(tipo=tipo)
                    return codigo
                erro = str(codigo.get("Err")) if isinstance(codigo, dict) and "Err" in codigo else ""
                if erro and (conhecido or erro in ERROS_DE_REDE or erro == "914"):
                    raise ErroControle(f"o controle não respondeu na rede ({_erro_tuya(codigo)})")
                if codigo is not None:
                    logger.info("Resposta do controle ao aprender (tipo %s): %r", tipo, codigo)
            return None

        return self._na_rede(funcao)


# ====================================================================== nuvem
# Teclas da TV pelo nome dos controles virtuais da Tuya ("Power", "Volume+", "Ch-", "Mute", "1"...).
TECLAS_DA_NUVEM = {
    "ligar": r"^power ?on$|^ligar$",
    "desligar": r"^power ?off$|^desligar$",
    "energia": r"^power$|^power ?on ?off$|^energia$|^on ?off$",
    "volume_mais": r"^vol(?:ume)?[ _]?(?:\+|up|plus|mais)$",
    "volume_menos": r"^vol(?:ume)?[ _]?(?:-|down|minus|menos)$",
    "mudo": r"^mute$|^mudo$",
    "canal_mais": r"^(?:ch|channel|canal)[ _]?(?:\+|up|plus|mais)$",
    "canal_menos": r"^(?:ch|channel|canal)[ _]?(?:-|down|minus|menos)$",
    "ok": r"^ok$|^enter$|^confirm$",
    "voltar": r"^back$|^return$|^voltar$",
    "inicio": r"^home$|^inicio$",
    "menu": r"^menu$",
    "entrada": r"^input$|^source$|^av ?/ ?tv$|^tv ?/ ?av$|^signal$",
    "cima": r"^up$", "baixo": r"^down$", "esquerda": r"^left$", "direita": r"^right$",
}


def tecla_da_nuvem(botao: str, teclas: list) -> dict | None:
    """A tecla do controle virtual (da lista que a API devolve) para um botão da Beatriz."""
    def normal(texto):
        return re.sub(r"\s+", " ", str(texto or "").strip().lower())

    if botao.startswith("digito_"):
        padrao = rf"^(?:num(?:ber)?[ _]?|key[ _]?)?{botao[-1]}$"
    else:
        padrao = TECLAS_DA_NUVEM.get(botao) or rf"^{re.escape(botao.replace('_', ' '))}$"
    for tecla in teclas or ():
        if re.search(padrao, normal(tecla.get("key"))) or re.search(padrao, normal(tecla.get("key_name"))):
            return tecla
    return None


class ControleNuvemTuya:
    """Os controles virtuais do app pela API de infravermelho da Tuya (o tinytuya assina os pedidos)."""

    def __init__(self, regiao: str, chave: str, segredo: str, id_aparelho: str):
        self.regiao = regiao or "us"
        self.chave = chave
        self.segredo = segredo
        self.id = id_aparelho
        self._cliente = None
        self._trava = threading.Lock()

    def _nuvem(self):
        with self._trava:
            if self._cliente is None:
                import tinytuya

                cliente = tinytuya.Cloud(apiRegion=self.regiao, apiKey=self.chave, apiSecret=self.segredo,
                                         apiDeviceID=self.id, configFile=os.devnull)
                if not cliente.token:
                    raise ErroControle(f"a nuvem da Tuya não aceitou as chaves ({_erro_tuya(cliente.error) or 'sem resposta'})")
                self._cliente = cliente
            return self._cliente

    def _pedir(self, caminho: str, corpo: dict | None = None):
        resposta = self._nuvem().cloudrequest(caminho, action="POST" if corpo is not None else "GET", post=corpo)
        if isinstance(resposta, dict) and resposta.get("success"):
            return resposta.get("result")
        if isinstance(resposta, dict) and str(resposta.get("code")) in {"1010", "1011"}:  # token vencido: pega outro
            self._cliente = None
            resposta = self._nuvem().cloudrequest(caminho, action="POST" if corpo is not None else "GET", post=corpo)
            if isinstance(resposta, dict) and resposta.get("success"):
                return resposta.get("result")
        motivo = (resposta.get("msg") or _erro_tuya(resposta)) if isinstance(resposta, dict) else "sem resposta"
        raise ErroControle(f"a nuvem da Tuya recusou o pedido ({motivo})")

    def tipo_do_controle(self) -> int:
        """O tipo do controle pelas funções que a nuvem lista: DP 201 "ir_send" = tipo 1 (o Ekaza Mini 3 do usuário);
        DP 1 "control" = tipo 2; 0 se não der para saber."""
        resultado = self._pedir(f"/v2.0/cloud/thing/{self.id}/shadow/properties") or {}
        dps = {int(p.get("dp_id", 0)): p.get("code", "") for p in resultado.get("properties", []) if isinstance(p, dict)}
        if dps.get(201) == "ir_send":
            return 1
        if dps.get(1) in {"control", "mode"} or 13 in dps:
            return 2
        return 0

    def controles(self) -> list:
        """Os controles virtuais cadastrados no app: [{remote_id, remote_name, category_id, brand_name...}]. A lista da API de
        infravermelho veio vazia para o Ekaza do usuário (out/2026), embora a TV criada no Smart Life exista: ela é um
        sub-aparelho dele (categoria infrared_tv), e o id do sub-aparelho serve de remote_id nas outras rotas."""
        lista = self._pedir(f"/v2.0/infrareds/{self.id}/remotes") or []
        if lista:
            return lista
        subaparelhos = self._pedir(f"/v1.0/devices/{self.id}/sub-devices") or []
        return [{"remote_id": s["id"], "remote_name": s.get("name", ""), "brand_name": "",
                 "category_id": CATEGORIAS_DOS_SUBAPARELHOS[s.get("category")]}
                for s in subaparelhos if isinstance(s, dict) and s.get("id") and s.get("category") in CATEGORIAS_DOS_SUBAPARELHOS]

    def teclas(self, remote_id: str) -> list:
        resultado = self._pedir(f"/v2.0/infrareds/{self.id}/remotes/{remote_id}/keys") or {}
        return resultado.get("key_list", []) if isinstance(resultado, dict) else []

    def apertar(self, remote_id: str, category_id: int, tecla: dict, vezes: int = 1) -> None:
        corpo = {"category_id": int(category_id), "key": tecla.get("key")}
        if tecla.get("key_id") is not None:
            corpo["key_id"] = tecla["key_id"]
        for indice in range(max(1, vezes)):
            if indice:
                time.sleep(INTERVALO_ENTRE_TOQUES)
            self._pedir(f"/v2.0/infrareds/{self.id}/remotes/{remote_id}/raw/command", corpo)

    def faixas_do_ar(self, remote_id: str) -> dict:
        """O que o ar aceita, pelo key_range da nuvem: {"0": {"temps": [18, 30], "ventos": [0, 1, 2, 3]}, "3": {"temps": None,
        ...}} com o mode da API (0 frio, 1 quente, 2 automático, 3 ventilar, 4 desumidificar)."""
        resultado = self._pedir(f"/v2.0/infrareds/{self.id}/remotes/{remote_id}/keys") or {}
        faixas = {}
        for faixa in resultado.get("key_range", []) if isinstance(resultado, dict) else []:
            lista = faixa.get("temp_list") or []
            temps = [int(t["temp"]) for t in lista if re.fullmatch(r"-?\d+", str(t.get("temp", "")))]
            ventos = sorted({int(v["fan"]) for t in lista for v in t.get("fan_list") or [] if "fan" in v})
            faixas[str(faixa.get("mode"))] = {"temps": [min(temps), max(temps)] if temps else None, "ventos": ventos}
        return faixas

    def ar(self, remote_id: str, ligado: bool, modo: str = "frio", temperatura: int = 23, ventilacao: str = "auto",
           com_temperatura: bool = True) -> None:
        corpo = {"power": 1 if ligado else 0}
        if ligado:
            corpo.update(mode=MODOS_DO_AR.get(modo, 0), wind=VENTILACOES.get(ventilacao, 0))
            if com_temperatura:  # só ventilar e desumidificar não têm temperatura
                corpo["temp"] = int(temperatura)
        self._pedir(f"/v2.0/infrareds/{self.id}/air-conditioners/{remote_id}/scenes/command", corpo)


# ====================================================================== linha de comando
def _principal(argumentos) -> int:
    """`python controle_remoto.py --conferir`: o controle responde na rede? `--controles`: os controles virtuais da nuvem."""
    from agente_controle import criar_agente_controle

    agente = criar_agente_controle()
    if "--controles" in argumentos:
        print(agente.responder("atualiza os controles da nuvem") or "O controle remoto está desligado no .env.")
    else:
        print(agente.responder("o controle remoto está funcionando?") or "O controle remoto está desligado no .env.")
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_principal(sys.argv[1:]))
