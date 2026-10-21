"""Testes das fontes e dos arquivos recebidos: chegada, "vira fonte", "arquiva", "imprime", leitura, escaneamento da folha e
envio pelo WhatsApp. Impressora, biblioteca, scanner e modelo são falsos; o agente de pastas é o de verdade, numa árvore temporária."""
import os
import shutil

import pytest
from PIL import Image

import fontes
from agente_pastas import AgentePastas, IndiceDePastas
from fontes import AgenteFontes, Registro
from scanner import ErroDoScanner


class Relogio:
    def __init__(self):
        self.agora = 1_760_000_000.0

    def __call__(self):
        return self.agora


class ImpressoraFalsa:
    def __init__(self, achados=None):
        self.pedidos, self.achados = [], achados or {}

    def imprimir_arquivos(self, caminhos, texto=""):
        self.pedidos.append((list(caminhos), texto))
        return f"Vou imprimir {len(caminhos)} arquivo(s). Posso?"

    def achar_arquivo(self, resto, original=""):
        return next((caminho for chave, caminho in self.achados.items() if chave in resto), None)


class LLMFalso:
    def __init__(self, resposta="O contrato fala de pagamento mensal e prazo de 12 meses."):
        self.resposta, self.prompts = resposta, []

    def chat_beatriz(self, prompt, contexto=None, usar_historico=True, **_):
        self.prompts.append(prompt)
        return self.resposta


class NuvemFalsa:
    """O responder_na_nuvem: guarda o que recebeu e responde como se fosse o Gemini ou o Claude."""

    def __init__(self, resposta="Resumo da nuvem: aluguel mensal de R$ 1.500 por 12 meses."):
        self.resposta, self.chamadas, self.indisponivel, self.falha = resposta, [], False, None

    def __call__(self, prompt, contexto="", preferir=None, exclusivo=False, registro=None, **_):
        self.chamadas.append({"prompt": prompt, "preferir": preferir, "exclusivo": exclusivo})
        if self.falha:
            raise self.falha
        if self.indisponivel:
            return None
        if registro is not None:
            registro.append((preferir or "claude").capitalize())
        return self.resposta


class ScannerFalso:
    def __init__(self, erro=None):
        self.erro, self.chamadas = erro, []

    def escanear(self, destino, dpi=200, cor="cor"):
        self.chamadas.append((dpi, cor))
        if self.erro:
            raise self.erro
        Image.new("RGB", (400, 560), "white").save(destino)
        return destino


class Mundo:
    """Tudo ligado: pastas temporárias, relógio manual e as peças falsas, com o agente de pastas de verdade."""

    def __init__(self, tmp_path, scanner=None, textos=None, ia_padrao="local", pessoal=None):
        self.tmp = tmp_path
        self.nuvem = NuvemFalsa()
        self.relogio = Relogio()
        self.recebidos, self.arquivados = str(tmp_path / "recebidos"), str(tmp_path / "arquivados")
        self.biblioteca = tmp_path / "biblioteca"
        self.biblioteca.mkdir()
        self.textos = textos if textos is not None else {}
        self.avisos, self.enviados, self.fontes_importadas = [], [], []
        self.impressora, self.llm = ImpressoraFalsa(), LLMFalso()
        self.pastas_raiz = tmp_path / "minhas_pastas"
        for pasta in ("OAB", "Estudos", "Contratos/2025"):
            (self.pastas_raiz / pasta).mkdir(parents=True)
        indice = IndiceDePastas(raizes=lambda: [(str(self.pastas_raiz), 3)], relogio=self.relogio)
        self.pastas = AgentePastas(indice=indice, abrir=lambda c: None, lugares={}, relogio=self.relogio,
                                   preferencias=str(tmp_path / "preferencias.json"))
        self.scanner = scanner if scanner is not None else ScannerFalso()
        self.agente = AgenteFontes(
            Registro(str(tmp_path / "registro.json"), relogio=self.relogio), self.recebidos, self.arquivados,
            importar_fonte=self._importar, impressora=self.impressora, pastas=self.pastas, scanner=self.scanner,
            extrair_texto=lambda caminho: self.textos.get(os.path.basename(caminho), ""), llm=self.llm,
            enviar_zap=lambda caminhos, texto: self.enviados.append(list(caminhos)),
            avisar=lambda texto, canal: self.avisos.append((texto, canal)), relogio=self.relogio,
            em_segundo_plano=lambda funcao: funcao(), limite_mb=1.0, nuvem=self.nuvem, ia_padrao=ia_padrao, pessoal=pessoal,
        )

    def _importar(self, caminho, categoria):
        destino = self.biblioteca / categoria
        destino.mkdir(exist_ok=True)
        shutil.copy2(caminho, destino / os.path.basename(caminho))
        self.fontes_importadas.append(os.path.basename(caminho))
        return f"Fonte importada e indexada: {destino}"

    def chega(self, nome="contrato.pdf", conteudo=b"%PDF-1.4 fake", legenda="", canal="whatsapp"):
        """Um arquivo chega pelo WhatsApp (o canal já o grava dentro de documentos_recebidos)."""
        pasta = os.path.join(self.recebidos, "2025-10-19")
        os.makedirs(pasta, exist_ok=True)
        caminho = fontes.caminho_livre(pasta, nome)
        with open(caminho, "wb") as arquivo:
            arquivo.write(conteudo)
        return self.agente.receber(caminho, nome, legenda, "whatsapp", canal)

    def diz(self, texto, canal="whatsapp"):
        return self.agente.responder(texto, canal)

    def passa(self, segundos):
        self.relogio.agora += segundos


@pytest.fixture
def mundo(tmp_path):
    return Mundo(tmp_path, textos={"contrato.pdf": "Contrato de prestação. Pagamento mensal de R$ 1.500. Prazo de 12 meses. " * 5})


# ------------------------------------------------------------------ chegada
def test_arquivo_recebido_e_registrado_e_ela_pergunta_o_que_fazer(mundo):
    resposta = mundo.chega("contrato.pdf")
    assert resposta.startswith("Recebi “contrato.pdf” (PDF") and "O que faço com ele?" in resposta
    recentes = mundo.agente.registro.recentes()
    assert [r.nome for r in recentes] == ["contrato.pdf"] and mundo.agente.esperando_resposta()


def test_arquivos_perigosos_grandes_ou_incompletos_nao_sao_guardados(mundo):
    for nome in ("instalador.exe", "script.ps1", "atalho.lnk", "x.py"):
        assert "não guardei" in mundo.chega(nome)
    assert "passa de 1 MB" in mundo.chega("enorme.pdf", conteudo=b"x" * (2 * 1024 * 1024))
    assert "não chegou inteiro" in mundo.agente.receber(str(mundo.tmp / "nao_existe.pdf"), "nao_existe.pdf")
    assert mundo.agente.registro.recentes() == []


def test_nome_vindo_do_celular_e_limpo(mundo):
    assert fontes.nome_seguro("..\\..\\Windows\\a:b?c.PDF") == "a_b_c.pdf"
    assert fontes.nome_seguro("") == "arquivo"
    assert len(fontes.nome_seguro("x" * 400 + ".pdf")) <= 110


def test_arquivo_de_fora_da_pasta_e_copiado_para_os_recebidos(mundo, tmp_path):
    fora = tmp_path / "Downloads" / "nota.pdf"
    fora.parent.mkdir()
    fora.write_bytes(b"%PDF fake")
    mundo.agente.receber(str(fora), "nota.pdf", origem="pc")
    guardado = mundo.agente.registro.recentes()[0].caminho
    assert guardado.startswith(mundo.recebidos) and os.path.isfile(guardado) and fora.exists()


def test_varios_arquivos_seguidos_nao_repetem_a_pergunta(mundo):
    mundo.chega("a.pdf")
    resposta = mundo.chega("b.pdf")
    assert "Já são 2 arquivos" in resposta and "O que faço" not in resposta


def test_a_legenda_ja_e_o_pedido(mundo):
    resposta = mundo.chega("contrato.pdf", legenda="vira fonte")
    assert resposta.startswith("Recebi “contrato.pdf”") and "virou fonte" in resposta
    assert mundo.fontes_importadas == ["contrato.pdf"] and "O que faço" not in resposta


def test_legenda_que_nao_e_pedido_vira_a_pergunta(mundo):
    assert "O que faço com ele?" in mundo.chega("contrato.pdf", legenda="olha o contrato da casa")


# ------------------------------------------------------------------ virar fonte
def test_vira_fonte_copia_para_a_biblioteca_e_conta_as_palavras(mundo):
    mundo.chega("contrato.pdf")
    resposta = mundo.diz("vira fonte")
    assert resposta.startswith("“contrato.pdf” virou fonte (") and "palavras)" in resposta and "resume as fontes que eu coloquei" in resposta
    assert (mundo.biblioteca / "fontes_recebidas" / "contrato.pdf").exists()
    assert "fonte" in mundo.agente.registro.recentes()[0].acoes


@pytest.mark.parametrize("frase", ["adiciona isso como fonte", "adiciona como fonte", "coloca na biblioteca", "salva como fonte",
                                   "transforma isso em fonte", "usa esse arquivo como fonte", "fonte", "como fonte"])
def test_jeitos_de_pedir_fonte(mundo, frase):
    mundo.chega("contrato.pdf")
    assert "virou fonte" in mundo.diz(frase), frase


def test_fonte_sem_texto_legivel_avisa(mundo):
    mundo.chega("foto.jpg", conteudo=b"fake")
    assert "não achei texto nele" in mundo.diz("vira fonte")


def test_registro_e_compartilhado_entre_processos(mundo, tmp_path):
    mundo.chega("contrato.pdf")
    outro_processo = Registro(str(tmp_path / "registro.json"), relogio=mundo.relogio)  # o painel lê o mesmo arquivo
    assert [r.nome for r in outro_processo.recentes()] == ["contrato.pdf"]


# ------------------------------------------------------------------ arquivar
def test_arquiva_na_pasta_dos_arquivados_do_mes(mundo):
    mundo.chega("contrato.pdf")
    resposta = mundo.diz("arquiva")
    assert resposta == "Arquivei “contrato.pdf” em arquivados do mês."
    mes = mundo.agente.pasta_arquivados
    assert [os.path.basename(p) for _, _, f in os.walk(mes) for p in f] == ["contrato.pdf"]
    assert mundo.agente.registro.recentes()[0].caminho.startswith(mundo.recebidos)  # a cópia nos recebidos continua


def test_arquiva_numa_pasta_citada_usando_o_agente_de_pastas(mundo):
    mundo.chega("contrato.pdf")
    assert mundo.diz("arquiva na pasta da OAB") == "Arquivei “contrato.pdf” em OAB."
    assert (mundo.pastas_raiz / "OAB" / "contrato.pdf").exists()


def test_arquivar_com_duas_pastas_parecidas_pergunta_e_a_resposta_arquiva(mundo):
    (mundo.pastas_raiz / "Estudos" / "OAB").mkdir()
    mundo.chega("contrato.pdf")
    pergunta = mundo.diz("arquiva na pasta da OAB")
    assert pergunta.startswith("Achei 2 pastas:") and pergunta.endswith("Em qual delas?")
    assert not (mundo.pastas_raiz / "OAB" / "contrato.pdf").exists()
    resposta = mundo.pastas.responder("a primeira")  # no main, quem responde é o agente de pastas
    assert resposta.startswith("Arquivei “contrato.pdf” em ")
    assert sum(1 for _ in mundo.pastas_raiz.rglob("contrato.pdf")) == 1


def test_arquivo_com_o_mesmo_nome_nao_sobrescreve(mundo):
    mundo.chega("contrato.pdf")
    mundo.diz("arquiva na pasta da OAB")
    mundo.chega("contrato.pdf", conteudo=b"%PDF outro")
    mundo.diz("arquiva na pasta da OAB")
    assert sorted(p.name for p in (mundo.pastas_raiz / "OAB").iterdir()) == ["contrato (1).pdf", "contrato.pdf"]


def test_pasta_inexistente_nao_arquiva_nem_inventa(mundo):
    mundo.chega("contrato.pdf")
    assert mundo.diz("arquiva na pasta xyzabc") == "Não achei nenhuma pasta com “xyzabc”."


def test_fonte_e_arquivo_no_mesmo_pedido(mundo):
    mundo.chega("contrato.pdf")
    resposta = mundo.diz("vira fonte e arquiva na pasta da OAB")
    assert "virou fonte" in resposta and "Arquivei “contrato.pdf” em OAB." in resposta


# ------------------------------------------------------------------ imprimir
def test_imprime_o_que_acabou_de_chegar_com_as_opcoes_ditas(mundo):
    mundo.chega("contrato.pdf")
    resposta = mundo.diz("imprime 2 cópias em preto e branco")
    caminhos, texto = mundo.impressora.pedidos[0]
    assert resposta.startswith("Vou imprimir") and os.path.basename(caminhos[0]) == "contrato.pdf"
    assert "2 cópias" in texto and "preto e branco" in texto


def test_imprime_outra_coisa_continua_sendo_da_impressora(mundo):
    assert mundo.diz("imprime minha agenda") is None  # sem arquivo recente
    mundo.chega("contrato.pdf")
    assert mundo.diz("imprime minha agenda de hoje") is None  # com arquivo recente, mas o pedido é outro
    assert mundo.diz("imprime o contrato da casa.pdf dos downloads") is None


def test_imprimir_varios_do_lote(mundo):
    mundo.chega("a.pdf")
    mundo.chega("b.pdf")
    mundo.diz("imprime todos")
    assert len(mundo.impressora.pedidos[0][0]) == 2


# ------------------------------------------------------------------ ler e ajudar
def test_resume_o_arquivo_que_acabou_de_chegar(mundo):
    mundo.chega("contrato.pdf")
    resposta = mundo.diz("resume esse PDF")
    assert resposta == "O contrato fala de pagamento mensal e prazo de 12 meses."
    prompt = mundo.llm.prompts[0]
    assert "contrato.pdf" in prompt and "Pagamento mensal" in prompt and "resume esse PDF" in prompt


def test_leitura_de_arquivo_velho_e_da_tela_nao_do_arquivo(mundo):
    mundo.chega("contrato.pdf")
    mundo.passa(fontes.VALIDADE_DA_LEITURA + 60)
    assert mundo.diz("resume esse PDF") is None  # deixa para a leitura de tela
    assert "virou fonte" in mundo.diz("vira fonte")  # mas as outras ações ainda valem por 20 minutos


def test_o_que_diz_e_escaneia_com_arquivo_recente_leem_o_arquivo(mundo):
    mundo.chega("contrato.pdf")
    assert mundo.diz("o que diz esse arquivo") and mundo.diz("escaneia isso")
    assert len(mundo.llm.prompts) == 2 and mundo.scanner.chamadas == []  # nada de scanner: é o arquivo


def test_arquivo_grande_e_lido_pelo_comeco_e_pelo_fim(mundo):
    mundo.textos["longo.pdf"] = "A" * 5000 + "B" * 5000
    mundo.chega("longo.pdf")
    resposta = mundo.diz("resume isso")
    assert "Li só o começo e o fim" in resposta and "meio do arquivo cortado" in mundo.llm.prompts[0]


def test_arquivo_sem_texto_nao_vai_para_o_modelo(mundo):
    mundo.chega("foto.jpg", conteudo=b"fake")
    assert "Não consegui ler texto" in mundo.diz("lê isso") and mundo.llm.prompts == []


def test_no_celular_a_resposta_pode_ser_mais_longa_e_na_voz_e_curta(mundo):
    mundo.chega("contrato.pdf")
    mundo.diz("resume isso", canal="whatsapp")
    mundo.diz("resume isso", canal="voz")
    assert "12 linhas" in mundo.llm.prompts[0] and "4 frases curtas" in mundo.llm.prompts[1]


# ------------------------------------------------------------------ o que não é com ela
@pytest.mark.parametrize("frase", [
    "que horas são", "abre a pasta da OAB", "qual a previsão do tempo", "resume as fontes que eu coloquei", "me manda o PDF no zap",
    "muda a fonte do documento", "cria um resumo da matéria de direito civil", "o que tenho amanhã", "arquivo", "imprime",
])
def test_pedidos_de_outras_funcoes_passam(mundo, frase):
    assert mundo.diz(frase) is None


def test_sem_arquivo_recente_os_pedidos_claros_recebem_orientacao(mundo):
    for frase in ("vira fonte", "adiciona como fonte", "arquiva"):
        assert "Não tenho nenhum arquivo recente" in mundo.diz(frase)


def test_arquivo_expira_depois_de_vinte_minutos(mundo):
    mundo.chega("contrato.pdf")
    mundo.passa(fontes.VALIDADE_DOS_RECENTES + 1)
    assert "Não tenho nenhum arquivo recente" in mundo.diz("vira fonte")


def test_fonte_de_um_arquivo_do_pc_descrito_na_voz(mundo, tmp_path):
    achado = tmp_path / "Downloads" / "oab_2025.pdf"
    achado.parent.mkdir()
    achado.write_bytes(b"%PDF fake")
    mundo.impressora.achados = {"oab": str(achado)}
    resposta = mundo.diz("adiciona o pdf da oab dos downloads como fonte", canal="voz")
    assert "virou fonte" in resposta or "não achei texto" in resposta
    assert mundo.fontes_importadas == ["oab_2025.pdf"]
    assert mundo.diz("adiciona o pdf da xyz dos downloads como fonte").startswith("Não achei nenhum arquivo")


def test_cancelar_a_pergunta(mundo):
    mundo.chega("contrato.pdf")
    assert mundo.diz("deixa").startswith("Beleza, deixei guardado")
    assert mundo.diz("deixa") is None  # sem pergunta pendente, "deixa" não é com ela


def test_listar_o_que_foi_recebido(mundo):
    assert mundo.diz("quais arquivos eu mandei") == "Não recebi nenhum arquivo nas últimas 24 horas."
    mundo.chega("contrato.pdf")
    mundo.diz("vira fonte")
    resposta = mundo.diz("quais arquivos eu mandei")
    assert resposta.startswith("Arquivos recentes: 1. contrato.pdf") and "fonte" in resposta


# ------------------------------------------------------------------ escanear a folha
def test_escaneia_sem_arquivo_usa_o_scanner_e_monta_o_pdf(mundo):
    resposta = mundo.diz("Beatriz, escaneia a folha")
    assert resposta.startswith("Escaneando a página 1")
    texto, canal = mundo.avisos[-1]
    assert texto.startswith("Escaneei a página: o PDF tem 1 página.") and "virar fonte" in texto and canal == "whatsapp"
    recebido = mundo.agente.registro.recentes()[0]
    assert recebido.origem == "scanner" and recebido.caminho.endswith(".pdf") and os.path.getsize(recebido.caminho) > 500
    assert mundo.scanner.chamadas == [(200, "cor")]


def test_escaneia_e_ja_faz_o_que_pediu(mundo):
    mundo.diz("escaneia a folha, vira fonte e arquiva na pasta da OAB")
    texto, _ = mundo.avisos[-1]
    assert "virou fonte" in texto or "não achei texto" in texto and "Arquivei" in texto
    assert mundo.fontes_importadas[0].startswith("escaneado_") and list((mundo.pastas_raiz / "OAB").glob("escaneado_*.pdf"))


def test_escaneia_e_manda_no_zap(mundo):
    mundo.diz("escaneia o papel e me manda no zap")
    assert len(mundo.enviados) == 1 and mundo.enviados[0][0].endswith(".pdf") and "Mandei o arquivo" in mundo.avisos[-1][0]


def test_opcoes_do_escaneamento_ditas_na_frase(mundo):
    mundo.diz("escaneia a folha em preto e branco em alta qualidade")
    mundo.diz("escaneia a folha em cinza")
    assert mundo.scanner.chamadas == [(300, "pb"), (200, "cinza")]


def test_varias_folhas_viram_um_pdf_so(mundo):
    mundo.diz("escaneia a folha")
    primeiro = mundo.agente.registro.recentes()[0].caminho
    assert mundo.agente.esperando_resposta()
    assert mundo.diz("mais uma").startswith("Escaneando a página 2")
    assert "o PDF tem 2 páginas" in mundo.avisos[-1][0]
    import fitz

    with fitz.open(primeiro) as pdf:
        assert len(pdf) == 2
    assert len(mundo.agente.registro.recentes()) == 1  # o mesmo arquivo, não dois
    pngs = list(mundo.agente._sessao.paginas)
    assert all(os.path.exists(p) for p in pngs)
    assert mundo.diz("pronto") == "Fechado: o PDF ficou com 2 páginas."
    assert not any(os.path.exists(p) for p in pngs) and mundo.diz("pronto") is None  # PNGs temporários apagados


def test_mais_uma_fora_do_escaneamento_nao_e_com_ela(mundo):
    assert mundo.diz("mais uma") is None and mundo.diz("outra pasta") is None


def test_scanner_desligado_explica_e_nao_quebra(tmp_path):
    mundo = Mundo(tmp_path, scanner=ScannerFalso(ErroDoScanner("Não achei nenhum scanner. A impressora está ligada?")))
    mundo.diz("escaneia a folha")
    assert mundo.avisos[-1][0].startswith("Não achei nenhum scanner") and mundo.agente.registro.recentes() == []


def test_erro_inesperado_do_scanner_vira_mensagem(tmp_path):
    mundo = Mundo(tmp_path, scanner=ScannerFalso(OSError("disco cheio")))
    mundo.diz("escaneia a folha")
    assert "Não consegui escanear: disco cheio" in mundo.avisos[-1][0]


def test_sessao_de_escaneamento_vence_e_limpa(mundo):
    mundo.diz("escaneia a folha")
    pngs = list(mundo.agente._sessao.paginas)
    mundo.passa(6 * 60)
    assert mundo.diz("mais uma") is None and mundo.agente._sessao is None
    assert not any(os.path.exists(p) for p in pngs)


def test_sem_scanner_instalado_avisa(tmp_path):
    mundo = Mundo(tmp_path)
    mundo.agente.scanner = None
    assert mundo.diz("escaneia a folha") == "O scanner não está disponível neste computador."


# ------------------------------------------------------------------ enviar de volta
def test_devolve_o_arquivo_pelo_zap(mundo):
    mundo.chega("contrato.pdf")
    assert mundo.diz("me manda isso de volta no zap").startswith("Mandei o arquivo")
    assert len(mundo.enviados) == 1


def test_painel_sem_whatsapp_explica(tmp_path):
    mundo = Mundo(tmp_path)
    mundo.agente.enviar_zap = None
    mundo.chega("contrato.pdf")
    assert "Daqui eu não consigo mandar para o WhatsApp" in mundo.diz("me manda isso no zap")


# ------------------------------------------------------------------ ligação com o main e os painéis
class CanalFalso:
    """Imita o CanalWhatsApp: o que chega (texto ou arquivo) e o que ela envia."""

    def __init__(self, chegadas=()):
        self.chegadas, self.enviados = list(chegadas), []

    def receber(self):
        return self.chegadas.pop(0) if self.chegadas else ""

    def enviar(self, texto="", arquivos=()):
        self.enviados.append((texto, list(arquivos)))

    def status(self):
        return {"status": "conectado"}


@pytest.fixture
def principal(monkeypatch, tmp_path):
    """O main com um agente de fontes de teste (peças falsas) e o canal do WhatsApp falso."""
    import main

    mundo = Mundo(tmp_path, textos={"contrato.pdf": "Contrato com pagamento mensal de R$ 1.500. " * 4})
    canal = CanalFalso()
    monkeypatch.setattr(main, "FONTES", mundo.agente)
    monkeypatch.setattr(main, "CANAL_WHATSAPP", canal)
    monkeypatch.setitem(main.CANAL, "atual", "voz")
    monkeypatch.setattr(main, "avisar_painel", lambda *a, **k: None)
    monkeypatch.setattr(main, "registrar_conversa", lambda *a, **k: pytest.fail("o conteúdo dos arquivos não vai para o histórico"))
    monkeypatch.setattr(main, "falar_async", lambda *a, **k: pytest.fail("não deveria falar em casa: veio pelo WhatsApp"))
    mundo.canal = canal
    return mundo


def test_arquivo_que_chega_pelo_whatsapp_e_recebido_e_respondido_por_escrito(principal):
    import main

    pasta = os.path.join(principal.recebidos, "2025-10-19")
    os.makedirs(pasta)
    caminho = os.path.join(pasta, "contrato.pdf")
    open(caminho, "wb").write(b"%PDF-1.4 fake")
    principal.canal.chegadas.append({"tipo": "arquivo", "caminho": caminho, "nome": "contrato.pdf", "legenda": "vira fonte", "origem": "whatsapp"})
    assert main.proximo_comando_remoto() == ""  # o arquivo não vira "comando" de texto
    texto, arquivos = principal.canal.enviados[-1]
    assert "Recebi “contrato.pdf”" in texto and "virou fonte" in texto and arquivos == []
    assert main.CANAL["atual"] == "whatsapp" and principal.fontes_importadas == ["contrato.pdf"]


def test_arquivo_sem_legenda_pergunta_o_que_fazer_e_o_proximo_comando_segue(principal):
    import main

    pasta = os.path.join(principal.recebidos, "2025-10-19")
    os.makedirs(pasta)
    caminho = os.path.join(pasta, "contrato.pdf")
    open(caminho, "wb").write(b"%PDF-1.4 fake")
    principal.canal.chegadas += [{"tipo": "arquivo", "caminho": caminho, "nome": "contrato.pdf", "legenda": "", "origem": "whatsapp"},
                                 "arquiva na pasta da OAB"]
    assert main.proximo_comando_remoto() == "" and "O que faço com ele?" in principal.canal.enviados[-1][0]
    assert main.proximo_comando_remoto() == "arquiva na pasta da OAB"  # o texto segue o caminho normal até o agente
    assert principal.agente.responder("arquiva na pasta da OAB", "whatsapp") == "Arquivei “contrato.pdf” em OAB."


def test_responder_fontes_no_zap_vai_por_escrito_sem_historico(principal):
    import main

    main.responder_fontes("Arquivei tudo.", "whatsapp")
    assert principal.canal.enviados[-1][0] == "Arquivei tudo."


def test_mandar_pelo_whatsapp_precisa_do_canal_conectado(principal, monkeypatch):
    import main

    main.mandar_pelo_whatsapp(["a.pdf"], "")
    assert principal.canal.enviados[-1] == ("", ["a.pdf"])
    monkeypatch.setattr(main, "CANAL_WHATSAPP", None)
    with pytest.raises(RuntimeError, match="não está conectado"):
        main.mandar_pelo_whatsapp(["a.pdf"])


def test_depois_de_chegar_um_arquivo_vira_fonte_passa_na_escuta_sem_o_nome(principal, monkeypatch):
    import main

    principal.chega("contrato.pdf")
    monkeypatch.setitem(main.ESCUTA, "modo", "sempre")
    monkeypatch.setattr(main, "avaliar_fala_sem_nome", lambda *a, **k: "conversa")  # a IA acharia que é papo
    monkeypatch.setattr(main, "falou_durante", lambda inicio, fim: False)
    monkeypatch.setattr(main, "fim_da_ultima_fala", lambda: 0.0)
    monkeypatch.setattr(main, "ESTADO_ATIVACAO", main.EstadoAtivacao())
    assert main.filtrar_fala_por_ativacao("vira fonte") == "vira fonte"
    assert main.filtrar_fala_por_ativacao("que dia é hoje") == ""  # outra frase sem o nome continua passando pela IA


def test_convidado_nao_chega_aos_arquivos():
    import main

    for pedido in ("escaneia a folha", "adiciona como fonte", "vira fonte"):
        assert main.RE_SO_DO_DONO.search(pedido), pedido


def test_paineis_criam_a_impressora_e_as_pastas_antes_do_agente_de_fontes():
    # No painel clássico o __init__ só roda com a janela aberta, então este erro de ordem não apareceria em nenhum outro teste.
    for arquivo, impressora, pastas, fontes in (("interface_texto.py", "self.impressora = ", "self.pastas = ", "self.fontes = "),
                                                 ("painel_web.py", "self._impressora = ", "self._pastas = ", "self._fontes = ")):
        codigo = open(arquivo, encoding="utf-8").read()
        assert codigo.index(impressora) < codigo.index(fontes) and codigo.index(pastas) < codigo.index(fontes), arquivo


# ------------------------------------------------------------------ perguntas sobre o arquivo
def test_pergunta_citando_o_arquivo_e_respondida_com_ele(mundo):
    mundo.chega("contrato.pdf")
    resposta = mundo.diz("qual o valor desse contrato?")
    assert resposta == "O contrato fala de pagamento mensal e prazo de 12 meses."
    assert "qual o valor desse contrato?" in mundo.llm.prompts[0] and "Pagamento mensal" in mundo.llm.prompts[0]
    assert "FORA_DO_ARQUIVO" not in mundo.llm.prompts[0]  # citou o arquivo: não precisa conferir o assunto


def test_pergunta_seguinte_logo_depois_de_ler_continua_sobre_o_arquivo(mundo):
    mundo.chega("contrato.pdf")
    mundo.diz("resume esse PDF")
    mundo.passa(40)
    assert mundo.diz("qual o valor do aluguel e a multa?") is not None
    assert "FORA_DO_ARQUIVO" in mundo.llm.prompts[1]  # sem citar o arquivo, o modelo confere se a pergunta é sobre ele
    assert "Pagamento mensal" in mundo.llm.prompts[1]


def test_pergunta_de_outro_assunto_segue_o_caminho_normal(mundo):
    mundo.chega("contrato.pdf")
    mundo.diz("resume esse PDF")
    mundo.llm.resposta = "FORA_DO_ARQUIVO"
    assert mundo.diz("qual a previsão do tempo amanhã?") is None
    assert mundo.agente.registro.recentes()[0].acoes.count("lido") == 1  # a recusa não conta como leitura


def test_pergunta_sem_arquivo_recente_ou_sem_leitura_nao_e_com_ela(mundo):
    assert mundo.diz("qual a previsão do tempo?") is None
    mundo.chega("contrato.pdf")
    assert mundo.diz("qual a previsão do tempo?") is None and mundo.llm.prompts == []  # chegou, mas ninguém o leu nem o citou
    mundo.diz("resume isso")
    mundo.passa(fontes.VALIDADE_DO_SEGUIMENTO + 5)
    assert mundo.diz("qual a previsão do tempo?") is None and len(mundo.llm.prompts) == 1  # passou o tempo do seguimento


def test_afirmacoes_depois_da_leitura_nao_viram_pergunta(mundo):
    mundo.chega("contrato.pdf")
    mundo.diz("resume isso")
    assert mundo.diz("obrigado") is None and mundo.diz("que bom") is None and len(mundo.llm.prompts) == 1


# ------------------------------------------------------------------ quem lê o arquivo: o PC, o Gemini ou o Claude
def separar(texto):
    return fontes.separar_ia(texto)


def test_separar_ia_acha_o_gemini_o_claude_a_nuvem_e_o_pc():
    assert separar("resume isso pelo Gemini") == ("resume isso", "gemini")
    assert separar("resume esse PDF no Claude, por favor") == ("resume esse PDF, por favor", "claude")
    assert separar("explica com o gêmini") == ("explica", "gemini")
    assert separar("resume isso na nuvem") == ("resume isso", "nuvem")
    assert separar("resume isso só no PC") == ("resume isso", "local")
    assert separar("resume isso localmente") == ("resume isso", "local")
    assert separar("resume isso no ollama") == ("resume isso", "local")
    assert separar("resume isso") == ("resume isso", None)
    assert separar("pesquise pelo Cláudio Castro")[1] is None  # nome de pessoa não é o Claude


def test_sem_pedir_o_arquivo_fica_no_pc(mundo):
    mundo.chega("contrato.pdf")
    assert mundo.diz("resume esse PDF") == "O contrato fala de pagamento mensal e prazo de 12 meses."
    assert mundo.nuvem.chamadas == [] and len(mundo.llm.prompts) == 1


def test_pelo_gemini_so_o_gemini_le_e_formula_a_resposta(mundo):
    mundo.chega("contrato.pdf")
    resposta = mundo.diz("resume esse PDF pelo Gemini")
    assert resposta == "Resumo da nuvem: aluguel mensal de R$ 1.500 por 12 meses. (Resposta do Gemini.)"
    chamada = mundo.nuvem.chamadas[0]
    assert chamada["preferir"] == "gemini" and chamada["exclusivo"] is True  # não passa para o Claude se o Gemini falhar
    assert "Pagamento mensal de R$ 1.500" in chamada["prompt"] and "pelo Gemini" not in chamada["prompt"]
    assert mundo.llm.prompts == []  # o modelo do PC nem foi usado
    assert mundo.agente.registro.recentes()[0].acoes.count("lido") == 1


def test_ela_avisa_antes_de_pedir_a_nuvem_mas_nao_no_pc(mundo):
    mundo.chega("contrato.pdf")
    mundo.diz("resume esse PDF")
    assert mundo.avisos == []
    mundo.diz("resume esse PDF pelo Gemini")
    assert mundo.avisos == [("Pedindo ao Gemini, leva uns segundos.", "whatsapp")]
    mundo.diz("e pelo Claude?")
    assert mundo.avisos[-1] == ("Pedindo ao Claude, leva uns segundos.", "whatsapp")


def test_pelo_claude(mundo):
    mundo.chega("contrato.pdf")
    resposta = mundo.diz("resume esse PDF com o Claude")
    assert resposta.endswith("(Resposta do Claude.)")
    assert mundo.nuvem.chamadas[0]["preferir"] == "claude" and mundo.nuvem.chamadas[0]["exclusivo"] is True


def test_na_nuvem_usa_a_ordem_configurada_sem_exclusividade(mundo):
    mundo.chega("contrato.pdf")
    mundo.diz("resume esse PDF na nuvem")
    assert mundo.nuvem.chamadas[0]["preferir"] is None and mundo.nuvem.chamadas[0]["exclusivo"] is False


def test_padrao_do_env_na_nuvem_e_so_no_pc_na_frase(tmp_path):
    mundo = Mundo(tmp_path, textos={"contrato.pdf": "Pagamento mensal de R$ 1.500. " * 5}, ia_padrao="claude")
    mundo.chega("contrato.pdf")
    assert mundo.diz("resume esse PDF").endswith("(Resposta do Claude.)")
    mundo.passa(fontes.VALIDADE_DO_SEGUIMENTO + 10)
    assert mundo.diz("resume esse PDF só no PC") == "O contrato fala de pagamento mensal e prazo de 12 meses."
    assert len(mundo.nuvem.chamadas) == 1 and len(mundo.llm.prompts) == 1


def test_a_legenda_do_arquivo_tambem_escolhe_a_ia(mundo):
    resposta = mundo.chega("contrato.pdf", legenda="resume pelo Claude")
    assert resposta.startswith("Recebi “contrato.pdf”") and "(Resposta do Claude.)" in resposta
    assert mundo.nuvem.chamadas[0]["preferir"] == "claude"


def test_pergunta_seguinte_continua_com_a_mesma_ia(mundo):
    mundo.chega("contrato.pdf")
    mundo.diz("resume esse PDF pelo Gemini")
    mundo.passa(30)
    mundo.llm.resposta = "SIM"  # o PC confere que a pergunta é sobre o arquivo antes de mandá-lo para a nuvem
    resposta = mundo.diz("qual o valor do aluguel e a multa?")
    assert resposta.endswith("(Resposta do Gemini.)") and len(mundo.nuvem.chamadas) == 2
    assert "qual o valor do aluguel e a multa?" in mundo.nuvem.chamadas[1]["prompt"]
    assert "Responda só SIM ou NAO" in mundo.llm.prompts[0] and "Pagamento mensal" in mundo.llm.prompts[0]


def test_pergunta_de_outro_assunto_nao_manda_o_arquivo_para_a_nuvem(mundo):
    mundo.chega("contrato.pdf")
    mundo.diz("resume esse PDF pelo Gemini")
    mundo.llm.resposta = "NAO"
    assert mundo.diz("qual a previsão do tempo amanhã?") is None
    assert len(mundo.nuvem.chamadas) == 1  # só a primeira; a conferência foi no PC


def test_na_duvida_ou_com_o_modelo_do_pc_fora_nao_manda_para_a_nuvem(mundo):
    mundo.chega("contrato.pdf")
    mundo.diz("resume esse PDF pelo Claude")

    def quebrado(*_a, **_k):
        raise RuntimeError("Ollama fora do ar")

    mundo.llm.chat_beatriz = quebrado
    assert mundo.diz("qual o valor do aluguel?") is None and len(mundo.nuvem.chamadas) == 1


def test_e_pelo_claude_refaz_o_mesmo_pedido_com_outra_ia(mundo):
    mundo.chega("contrato.pdf")
    mundo.diz("resume esse PDF pelo Gemini")
    mundo.passa(20)
    resposta = mundo.diz("e pelo Claude?")
    assert resposta.endswith("(Resposta do Claude.)") and mundo.nuvem.chamadas[1]["preferir"] == "claude"
    assert "PEDIDO DO USUÁRIO: resume esse PDF" in mundo.nuvem.chamadas[1]["prompt"]
    mundo.passa(20)
    assert mundo.diz("agora só no PC") == "O contrato fala de pagamento mensal e prazo de 12 meses."  # o mesmo pedido, sem sair do PC
    assert "PEDIDO DO USUÁRIO: resume esse PDF" in mundo.llm.prompts[-1]


def test_e_pelo_claude_sem_leitura_recente_segue_o_caminho_normal(mundo):
    assert mundo.diz("e pelo Claude?") is None
    mundo.chega("contrato.pdf")
    mundo.diz("resume isso")
    mundo.passa(fontes.VALIDADE_DA_LEITURA + 10)
    assert mundo.diz("agora pelo Gemini") is None and mundo.nuvem.chamadas == []


def test_pesquisa_pelo_gemini_nao_e_com_o_arquivo(mundo):
    mundo.chega("contrato.pdf")
    assert mundo.diz("pesquise pelo Gemini a reforma tributária") is None and mundo.nuvem.chamadas == []


def test_nuvem_que_nao_responde_cai_no_modelo_do_pc_e_avisa(mundo):
    mundo.nuvem.indisponivel = True
    mundo.chega("contrato.pdf")
    resposta = mundo.diz("resume esse PDF pelo Gemini")
    assert resposta.startswith("O contrato fala de pagamento mensal") and "Não consegui falar com Gemini agora" in resposta
    assert "Resposta do" not in resposta and len(mundo.llm.prompts) == 1


def test_nuvem_que_da_erro_tambem_cai_no_pc(mundo):
    mundo.nuvem.falha = RuntimeError("sem internet")
    mundo.chega("contrato.pdf")
    resposta = mundo.diz("resume esse PDF pelo Claude")
    assert "Não consegui falar com Claude agora" in resposta and len(mundo.llm.prompts) == 1


def test_arquivo_pessoal_nunca_vai_para_a_nuvem(tmp_path):
    mundo = Mundo(tmp_path, textos={"historico.pdf": "Conversa privada. " * 20}, pessoal=lambda caminho: True)
    mundo.chega("historico.pdf")
    resposta = mundo.diz("resume esse PDF pelo Gemini")
    assert "pessoal demais para sair do PC" in resposta and mundo.nuvem.chamadas == [] and len(mundo.llm.prompts) == 1


def test_json_e_jsonl_sao_pessoais_pela_regra_do_material_de_estudo(tmp_path):
    mundo = Mundo(tmp_path, textos={"dados.json": '{"a": 1}' * 40})  # sem `pessoal`: vale a regra de agentes.especialistas.fonte_pessoal
    mundo.chega("dados.json", conteudo=b'{"a": 1}')
    resposta = mundo.diz("resume esse arquivo pelo Claude")
    assert "pessoal demais" in resposta and mundo.nuvem.chamadas == []


def test_a_nuvem_le_muito_mais_que_o_modelo_do_pc(tmp_path):
    grande = "Cláusula importante sobre o aluguel e o reajuste anual. " * 900  # ~51 mil caracteres: cabe na nuvem, não no PC
    mundo = Mundo(tmp_path, textos={"contrato.pdf": grande})
    mundo.chega("contrato.pdf")
    mundo.diz("resume esse PDF pelo Gemini")
    assert "meio do arquivo cortado" not in mundo.nuvem.chamadas[0]["prompt"] and len(mundo.nuvem.chamadas[0]["prompt"]) > 45000
    mundo.nuvem.indisponivel = True
    mundo.passa(10)
    resposta = mundo.diz("resume esse PDF pelo Gemini")  # a reserva no PC volta a cortar no limite do modelo local
    assert "meio do arquivo cortado" in mundo.llm.prompts[-1] and len(mundo.llm.prompts[-1]) < 9000
    assert "Li só o começo e o fim" in resposta


def test_escaneia_e_resume_pelo_gemini(mundo):
    mundo.agente.extrair_texto = lambda caminho: "Nota fiscal 123. Total de R$ 980,00 com vencimento em 10/11. " * 3
    mundo.diz("escaneia a folha e resume pelo Gemini")
    texto, _ = mundo.avisos[-1]
    assert texto.startswith("Escaneei a página") and "(Resposta do Gemini.)" in texto
    assert mundo.nuvem.chamadas[0]["preferir"] == "gemini" and mundo.llm.prompts == []


# ------------------------------------------------------------------ auto: o Claude entra quando o modelo do PC não dá conta
GRANDE = "Cláusula sobre o aluguel de R$ 2.300,00 e o reajuste anual pelo IPCA. " * 200  # ~14 mil caracteres: passa do PC


def mundo_auto(tmp_path, **textos):
    return Mundo(tmp_path, textos={"contrato.pdf": "Pagamento mensal de R$ 1.500. " * 5, "grande.pdf": GRANDE, **textos},
                 ia_padrao="auto")


def quebrar_o_pc(mundo):
    def quebrado(*_a, **_k):
        raise RuntimeError("Ollama fora do ar")

    mundo.llm.chat_beatriz = quebrado


def test_auto_arquivo_que_cabe_fica_no_pc(tmp_path):
    mundo = mundo_auto(tmp_path)
    mundo.chega("contrato.pdf")
    assert mundo.diz("resume esse PDF") == "O contrato fala de pagamento mensal e prazo de 12 meses."
    assert mundo.nuvem.chamadas == [] and mundo.avisos == [] and len(mundo.llm.prompts) == 1


def test_auto_arquivo_grande_demais_para_o_pc_vai_so_para_o_claude(tmp_path):
    mundo = mundo_auto(tmp_path)
    mundo.chega("grande.pdf")
    resposta = mundo.diz("resume esse PDF")
    assert resposta.endswith("(Resposta do Claude.)") and "Li só o começo e o fim" not in resposta
    chamada = mundo.nuvem.chamadas[0]
    assert chamada["preferir"] == "claude" and chamada["exclusivo"] is True  # o Gemini nunca, sem pedir
    assert "meio do arquivo cortado" not in chamada["prompt"] and GRANDE.strip() in chamada["prompt"]
    assert mundo.llm.prompts == []
    assert mundo.avisos == [("O arquivo é grande para o modelo do PC: pedindo ao Claude, leva uns segundos.", "whatsapp")]


def test_auto_com_o_pc_fora_do_ar_o_claude_responde(tmp_path):
    mundo = mundo_auto(tmp_path)
    quebrar_o_pc(mundo)
    mundo.chega("contrato.pdf")
    assert mundo.diz("resume esse PDF").endswith("(Resposta do Claude.)")
    assert mundo.nuvem.chamadas[0]["preferir"] == "claude"
    assert mundo.avisos[-1][0].startswith("O modelo do PC não respondeu: pedindo ao Claude")


def test_auto_sem_o_pc_e_sem_o_claude_explica_sem_tentar_de_novo(tmp_path):
    mundo = mundo_auto(tmp_path)
    tentativas = []

    def quebrado(*_a, **_k):
        tentativas.append(1)
        raise RuntimeError("Ollama fora do ar")

    mundo.llm.chat_beatriz = quebrado
    mundo.nuvem.indisponivel = True
    mundo.chega("contrato.pdf")
    assert "nem o modelo do PC nem o Claude responderam" in mundo.diz("resume esse PDF")
    assert len(tentativas) == 1 and len(mundo.nuvem.chamadas) == 1


def test_auto_claude_em_pausa_le_o_comeco_e_o_fim_no_pc(tmp_path):
    mundo = mundo_auto(tmp_path)
    mundo.nuvem.indisponivel = True
    mundo.chega("grande.pdf")
    resposta = mundo.diz("resume esse PDF")
    assert "Não consegui falar com Claude agora" in resposta and "Li só o começo e o fim" in resposta
    assert "meio do arquivo cortado" in mundo.llm.prompts[-1]


def test_auto_arquivo_pessoal_ou_so_no_pc_nunca_sai(tmp_path):
    mundo = mundo_auto(tmp_path, **{"historico.json": GRANDE})
    mundo.chega("historico.json", conteudo=b"{}")
    mundo.diz("resume esse arquivo")
    mundo.passa(fontes.VALIDADE_DA_LEITURA + 10)
    mundo.chega("grande.pdf")
    mundo.diz("resume esse PDF só no PC")
    assert mundo.nuvem.chamadas == [] and len(mundo.llm.prompts) == 2
    assert all("meio do arquivo cortado" in p for p in mundo.llm.prompts)


def test_auto_pergunta_seguinte_num_arquivo_grande_confere_no_pc_e_vai_ao_claude(tmp_path):
    mundo = mundo_auto(tmp_path)
    mundo.chega("grande.pdf")
    mundo.diz("resume esse PDF")
    mundo.passa(30)
    mundo.llm.resposta = "SIM"
    assert mundo.diz("qual o índice do reajuste?").endswith("(Resposta do Claude.)")
    assert "Responda só SIM ou NAO" in mundo.llm.prompts[0] and len(mundo.nuvem.chamadas) == 2
    mundo.llm.resposta = "NAO"
    assert mundo.diz("qual a previsão do tempo amanhã?") is None and len(mundo.nuvem.chamadas) == 2


def test_o_padrao_do_agente_e_auto(tmp_path):
    assert AgenteFontes(Registro(str(tmp_path / "r.json")), str(tmp_path), str(tmp_path)).ia_padrao == "auto"


# ------------------------------------------------------------------ ficha de estudo do arquivo recebido (agentes, pelo Claude)
class EstudoFalso:
    """O que o processo com o orquestrador liga em `estudar`: guarda o pedido e devolve a mensagem de início."""

    def __init__(self):
        self.pedidos = []

    def __call__(self, caminhos, pedido, canal, ia=None):
        self.pedidos.append({"arquivos": [os.path.basename(c) for c in caminhos], "pedido": pedido, "canal": canal, "ia": ia})
        return "Certo, vou preparar a ficha de estudo, com o Claude."


@pytest.fixture
def estudo(mundo):
    mundo.agente.ia_padrao = "auto"  # o padrão do .env
    mundo.agente.estudar = EstudoFalso()
    return mundo.agente.estudar


@pytest.mark.parametrize("frase", [
    "faz uma ficha desse PDF", "resume esse PDF para a prova", "faz um resumo disso pra prova da OAB",
    "quero estudar isso", "monta flashcards desse arquivo", "transforma isso numa ficha de estudo",
    "faz um material de estudo desse PDF",
])
def test_ficha_de_estudo_do_arquivo_vai_para_os_agentes(mundo, estudo, frase):
    mundo.chega("contrato.pdf")
    mundo.passa(fontes.VALIDADE_DA_PERGUNTA + 10)  # já não é a resposta a "o que faço com ele?": vale por citar o arquivo
    assert mundo.diz(frase) == "Certo, vou preparar a ficha de estudo, com o Claude."
    assert estudo.pedidos == [{"arquivos": ["contrato.pdf"], "pedido": frase, "canal": "whatsapp", "ia": None}]
    assert mundo.llm.prompts == [] and mundo.nuvem.chamadas == []  # sem a leitura rápida
    assert "ficha de estudo" in mundo.agente.registro.recentes()[0].acoes


def test_resumo_sem_prova_continua_a_leitura_rapida(mundo, estudo):
    mundo.chega("contrato.pdf")
    assert mundo.diz("resume esse PDF") == "O contrato fala de pagamento mensal e prazo de 12 meses."
    assert estudo.pedidos == []


def test_legenda_ou_resposta_a_pergunta_tambem_pedem_a_ficha(mundo, estudo):
    resposta = mundo.chega("contrato.pdf", legenda="faz um resumo para a prova")
    assert resposta.startswith("Recebi “contrato.pdf”") and "ficha de estudo" in resposta
    mundo.passa(fontes.VALIDADE_DOS_RECENTES + 10)
    mundo.chega("aula.pdf")
    mundo.diz("faz uma ficha para a prova")  # responde a "o que faço com ele?", sem citar o arquivo
    assert [p["arquivos"] for p in estudo.pedidos] == [["contrato.pdf"], ["aula.pdf"]]


def test_resumo_para_a_prova_sem_arquivo_e_do_agente_de_estudos_de_sempre(mundo, estudo):
    assert mundo.diz("faz um resumo para a prova de história") is None
    mundo.chega("contrato.pdf")
    mundo.passa(fontes.VALIDADE_DA_PERGUNTA + 10)
    assert mundo.diz("faz um resumo para a prova de história") is None  # não cita o arquivo nem responde à pergunta
    assert estudo.pedidos == []


def test_ficha_so_no_pc_pelo_gemini_ou_com_fontes_ia_local(tmp_path):
    mundo = Mundo(tmp_path, textos={"contrato.pdf": "Pagamento mensal. " * 5})
    mundo.agente.estudar = estudo = EstudoFalso()
    mundo.chega("contrato.pdf")
    mundo.diz("faz uma ficha desse PDF só no PC")
    mundo.diz("faz uma ficha desse PDF pelo Gemini")
    mundo.agente.ia_padrao = "local"  # FONTES_IA=local: o conteúdo nunca sai do PC
    mundo.diz("faz uma ficha desse PDF")
    assert [p["ia"] for p in estudo.pedidos] == ["local", "gemini", "local"]


def test_ficha_de_arquivo_pessoal_ou_sem_orquestrador_explica(tmp_path):
    mundo = Mundo(tmp_path, textos={"diario.pdf": "Hoje eu... " * 5}, pessoal=lambda caminho: True)
    mundo.agente.estudar = estudo = EstudoFalso()
    mundo.chega("diario.pdf")
    assert "pessoal" in mundo.diz("faz uma ficha desse PDF") and estudo.pedidos == []
    mundo.agente.estudar = None
    assert "não consigo montar a ficha" in mundo.diz("faz uma ficha desse PDF")


def test_imprimir_a_ficha_continua_sendo_impressao(mundo, estudo):
    mundo.chega("ficha.pdf")
    mundo.diz("imprime essa ficha")
    assert estudo.pedidos == [] and mundo.impressora.pedidos


def test_ficha_e_me_manda_fica_para_quando_ela_ficar_pronta(mundo, estudo):
    mundo.chega("contrato.pdf")
    mundo.diz("faz uma ficha disso e me manda no zap")
    assert len(estudo.pedidos) == 1 and mundo.enviados == []  # quem manda é o aviso de tarefa concluída


def test_escaneia_e_faz_a_ficha(mundo, estudo):
    mundo.agente.extrair_texto = lambda caminho: "Apostila de direito civil, capítulo 3. " * 5
    mundo.diz("escaneia a folha e faz uma ficha para a prova")
    assert estudo.pedidos[0]["arquivos"][0].startswith("escaneado_") and mundo.llm.prompts == []


def test_estudar_arquivos_do_main_avisa_pelo_canal_e_manda_se_pediram(monkeypatch):
    import main

    avisos = []

    class OrquestradorFalso:
        def enviar_estudo_de_arquivos(self, pedido, caminhos, ao_concluir=None, abrir=True, ia=None):
            self.argumentos = {"pedido": pedido, "caminhos": caminhos, "abrir": abrir, "ia": ia}
            self.ao_concluir = ao_concluir
            return "tarefa"

        def mensagem_inicio(self, tarefa):
            return "Certo, vou preparar a ficha."

    monkeypatch.setattr(main, "anunciar_tarefa_concluida", lambda *args: avisos.append(args))
    orquestrador = OrquestradorFalso()
    assert main.estudar_arquivos(orquestrador, ["c:/x/a.pdf"], "faz uma ficha disso e me manda", "whatsapp", "gemini") == (
        "Certo, vou preparar a ficha.")
    assert orquestrador.argumentos == {"pedido": "faz uma ficha disso e me manda", "caminhos": ["c:/x/a.pdf"],
                                       "abrir": False, "ia": "gemini"}  # longe do PC: não abre o arquivo
    orquestrador.ao_concluir("pronta")
    assert avisos == [("faz uma ficha disso e me manda", "pronta", "whatsapp", True)]
