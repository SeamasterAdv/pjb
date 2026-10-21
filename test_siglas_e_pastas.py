"""Testes das siglas (OAB, STF...) na transcrição e na busca, e do agente que abre pastas por voz. Pastas de mentira em
uma pasta temporária; nenhuma janela do Explorador é aberta de verdade."""
import os
import time

import pytest

import agente_pastas as ap
import siglas
import voice
from agente_pastas import AgentePastas, IndiceDePastas


# ------------------------------------------------------------------ siglas
@pytest.mark.parametrize("entrada, esperado", [
    ("abre a pasta da o a b", "abre a pasta da OAB"),
    ("abre a pasta da O A B", "abre a pasta da OAB"),
    ("abre a pasta da o.a.b.", "abre a pasta da OAB"),
    ("abre a pasta da O.A.B.", "abre a pasta da OAB"),
    ("abre a pasta da ó a bê", "abre a pasta da OAB"),
    ("me explica o artigo cinco da cê efe", "me explica o artigo cinco da CF"),
    ("preciso do s t f e do s t j", "preciso do STF e do STJ"),
    ("pasta do t i", "pasta do TI"),
    ("o a b", "OAB"),
    ("quero a b c d", "quero ABCD"),
])
def test_letras_soltas_viram_sigla(entrada, esperado):
    assert siglas.juntar_siglas(entrada) == esperado


@pytest.mark.parametrize("frase", [
    "abre a pasta da oab", "eu e a o u", "ele disse que de repente se foi", "vou de a pé", "eu te ve", "a e o", "plano b",
    "o que você acha", "isso é a b", "e a b ou c", "quero um café",
])
def test_frases_normais_ficam_como_estao(frase):
    assert siglas.juntar_siglas(frase) == frase


def test_corrige_confira_no_lugar_de_cf_so_no_contexto_do_artigo():
    assert siglas.corrigir_siglas("Me explica o artigo 5 da Confira.") == "Me explica o artigo 5 da CF."
    assert siglas.corrigir_siglas("art. 37 da confira") == "art. 37 da CF"
    assert siglas.corrigir_siglas("confira se está certo") == "confira se está certo"
    assert siglas.corrigir_siglas("abre a pasta da o a b") == "abre a pasta da OAB"


def test_vocabulario_do_whisper_e_curto_e_aceita_extras(monkeypatch):
    base = siglas.vocabulario_de_voz().split()
    assert base[0] == "Beatriz" and "OAB" in base and "STF" in base and len(base) <= 10  # lista longa deformava as frases
    monkeypatch.setenv("VOCABULARIO_VOZ", "TCC, Plano Piloto,oab")
    assert siglas.vocabulario_de_voz().split().count("OAB") == 1 and "TCC" in siglas.vocabulario_de_voz()


def test_siglas_extras_do_usuario(monkeypatch):
    monkeypatch.setenv("SIGLAS_EXTRAS", "XYZ=Meu Projeto Secreto;ABC = Outra Coisa")
    assert siglas.todas()["XYZ"] == "Meu Projeto Secreto" and siglas.todas()["ABC"] == "Outra Coisa"
    assert siglas.interpretacoes(["xyz"]) == [["xyz"], ["meu", "projeto", "secreto"], ["meu", "projeto"]]


def test_interpretacoes_vao_da_sigla_ao_significado_e_de_volta():
    assert siglas.interpretacoes(["oab"]) == [["oab"], ["ordem", "advogados", "brasil"], ["ordem", "advogados"]]
    assert ["oab"] in siglas.interpretacoes(["ordem", "advogados"])
    assert ["estudos", "oab"] in siglas.interpretacoes(["ordem", "advogados", "estudos"])
    assert ["cc"] in siglas.interpretacoes(["codigo", "civil"]) and ["cpc"] not in siglas.interpretacoes(["codigo", "civil"])


def test_transcricao_leva_o_vocabulario_e_junta_as_siglas(monkeypatch):
    class Segmento:
        text, no_speech_prob, avg_logprob = "Abre a pasta da o a b", 0.0, -0.1

    class Modelo:
        opcoes = {}

        def transcribe(self, audio, **opcoes):
            Modelo.opcoes = opcoes
            return iter([Segmento()]), None

    monkeypatch.setattr(voice, "_audio_para_whisper", lambda audio: b"")
    texto, _ = voice._transcrever_whisper(Modelo(), None)
    assert "OAB" in Modelo.opcoes["hotwords"]
    monkeypatch.setattr(voice, "_transcrever_bruto", lambda audio: (texto, 0.9))
    assert voice._transcrever(None) == ("Abre a pasta da OAB", 0.9)


# ------------------------------------------------------------------ pastas
@pytest.fixture
def arvore(tmp_path):
    for caminho in ("Documentos/OAB", "Documentos/Estudos/Direito Civil", "Documentos/LGPD 2025", "Downloads/OAB",
                    "Documentos/Ordem dos Advogados Antigos", "Projeto/Chamada OAB até passar", "Projeto/node_modules/oab",
                    "Projeto/.git/oab", "Documentos/a/b/c/d/e/f/Muito Fundo", "Fotos/Viagem 2025"):
        (tmp_path / caminho).mkdir(parents=True)
    return tmp_path


class Cenario:
    def __init__(self, raiz, preferencias=None, relogio=None):
        self.abertas = []
        self.relogio = relogio or [1000.0]
        self.indice = IndiceDePastas(raizes=lambda: [(str(raiz), 4)], relogio=lambda: self.relogio[0])
        lugares = {"downloads": ("Downloads", str(raiz / "Downloads")), "documentos": ("Documentos", str(raiz / "Documentos")),
                   "lixeira": ("a Lixeira", "shell:RecycleBinFolder")}
        self.agente = AgentePastas(indice=self.indice, abrir=self.abertas.append, lugares=lugares, relogio=lambda: self.relogio[0],
                                   preferencias=preferencias or str(raiz / "prefs.json"))
        self.raiz = raiz

    def dizer(self, frase):
        return self.agente.responder(frase)

    @property
    def ultima(self):
        return os.path.relpath(self.abertas[-1], self.raiz).replace("\\", "/") if self.abertas else None


def test_abre_a_pasta_pelo_nome_e_pela_sigla(arvore):
    c = Cenario(arvore)
    assert c.dizer("abre a pasta de estudos") == "Abri a pasta Estudos." and c.ultima == "Documentos/Estudos"
    assert c.dizer("Beatriz, mostra a pasta direito civil") and c.ultima == "Documentos/Estudos/Direito Civil"
    assert c.dizer("abre a pasta da LGPD") == "Abri a pasta LGPD 2025." and c.ultima == "Documentos/LGPD 2025"
    assert c.dizer("abre a pasta de viagem") and c.ultima == "Fotos/Viagem 2025"


def test_pasta_dita_letra_por_letra_tambem_funciona(arvore):
    c = Cenario(arvore)
    c.dizer("abre a pasta da LGPD")
    assert c.ultima == "Documentos/LGPD 2025"
    c.dizer("abre a pasta do l g p d")
    assert c.ultima == "Documentos/LGPD 2025"


def test_oab_acha_o_significado_e_o_significado_acha_a_sigla(arvore):
    outra = arvore / "so_a_ordem"
    (outra / "Ordem dos Advogados do Brasil").mkdir(parents=True)
    c = Cenario(outra)
    assert c.dizer("abre a pasta da oab") == "Abri a pasta Ordem dos Advogados do Brasil."
    unica = arvore / "so_sigla"
    (unica / "OAB").mkdir(parents=True)
    c = Cenario(unica)
    assert c.dizer("abre a pasta da ordem dos advogados") == "Abri a pasta OAB."


def test_empate_pergunta_qual_delas_e_entende_a_resposta(arvore):
    c = Cenario(arvore)
    resposta = c.dizer("abre a pasta da OAB")
    assert resposta.startswith("Achei ") and "OAB em Documentos" in resposta and "OAB em Downloads" in resposta
    assert resposta.endswith("Qual delas?") and c.abertas == [] and c.agente.esperando_resposta()
    assert c.dizer("que horas são") is None and c.agente.esperando_resposta()  # outro assunto: a pergunta espera
    assert c.dizer("a de downloads") == "Abri a pasta OAB." and c.ultima == "Downloads/OAB"
    assert not c.agente.esperando_resposta()


def test_so_as_candidatas_parecidas_entram_na_pergunta(arvore):
    c = Cenario(arvore)
    c.dizer("abre a pasta da OAB")
    nomes = [(p.nome, os.path.basename(os.path.dirname(p.caminho))) for p in c.agente.pergunta.candidatas]
    assert nomes == [("OAB", "Documentos"), ("OAB", "Downloads")]  # "Chamada OAB até passar" fica de fora: parece menos


def test_respostas_por_ordem_e_cancelamento(arvore, tmp_path):
    for fala, posicao in (("a primeira", 0), ("a segunda", 1), ("a última", -1), ("segunda", 1)):
        c = Cenario(arvore, preferencias=str(tmp_path / f"p_{fala}.json"))
        c.dizer("abre a pasta da OAB")
        candidatas = c.agente.pergunta.candidatas
        assert c.dizer(fala) == "Abri a pasta OAB."
        assert c.abertas[-1] == candidatas[posicao].caminho
    c = Cenario(arvore, preferencias=str(tmp_path / "p_cancela.json"))
    c.dizer("abre a pasta da OAB")
    assert c.dizer("nenhuma") == "Tudo bem, não abri nenhuma." and not c.agente.esperando_resposta() and c.abertas == []


def test_lembra_a_escolha_e_deixa_corrigir(arvore, tmp_path):
    prefs = str(tmp_path / "preferencias.json")
    c = Cenario(arvore, preferencias=prefs)
    c.dizer("abre a pasta da OAB")
    c.dizer("a de downloads")
    assert c.ultima == "Downloads/OAB"
    resposta = c.dizer("abre a pasta da OAB")  # da próxima vez abre direto, sem perguntar
    assert c.ultima == "Downloads/OAB" and "a que você escolheu da outra vez" in resposta and "a outra" in resposta
    assert c.dizer("a primeira") is None  # só "não" ou "a outra" corrigem
    assert c.dizer("não, a outra") == "Abri a pasta OAB." and c.ultima == "Documentos/OAB"  # a única que sobra, e vira a nova escolha
    novo = Cenario(arvore, preferencias=prefs)  # o programa reiniciou: a nova escolha continua valendo
    assert "escolheu da outra vez" in novo.dizer("abre a pasta da OAB") and novo.ultima == "Documentos/OAB"


def test_lugares_conhecidos_abrem_direto(arvore):
    c = Cenario(arvore)
    assert c.dizer("abre meus downloads") == "Abri Downloads." and c.ultima == "Downloads"
    assert c.dizer("abre a pasta de documentos") == "Abri Documentos." and c.ultima == "Documentos"
    c.dizer("mostra os downloads por favor")
    assert c.ultima == "Downloads"
    c.dizer("abre a lixeira")
    assert c.abertas[-1] == "shell:RecycleBinFolder"


def test_sem_nome_pergunta_qual_e_a_resposta_vira_a_busca(arvore):
    c = Cenario(arvore)
    assert c.dizer("abre a pasta") == "Qual pasta você quer abrir?"
    assert c.dizer("estudos") == "Abri a pasta Estudos."
    c.dizer("abre a pasta")
    assert c.dizer("deixa pra lá") == "Tudo bem, não abri nenhuma."


def test_onde_fica_diz_o_lugar_e_abre(arvore):
    c = Cenario(arvore)
    assert c.dizer("onde fica a pasta de estudos") == "A pasta Estudos fica em Documentos. Já abri para você."


def test_pasta_inexistente_e_pedidos_que_nao_sao_de_pasta(arvore):
    c = Cenario(arvore)
    assert c.dizer("abre a pasta xyzabc") == "Não achei nenhuma pasta com “xyzabc”."
    for frase in ("que horas são", "abre o youtube", "abre o aplicativo bloco de notas", "cria uma pasta nova", "abre a agenda",
                  "me conta uma piada", "o que tem na pasta", "resume o pdf"):
        assert c.dizer(frase) is None, frase
    assert c.abertas == []


def test_nao_entra_em_pastas_do_sistema_nem_fundo_demais(arvore):
    c = Cenario(arvore)
    c.indice.atualizar()
    nomes = {p.caminho.replace("\\", "/") for p in c.indice.pastas}
    assert not any("node_modules" in n or "/.git" in n for n in nomes)
    assert not any(n.endswith("Muito Fundo") for n in nomes)  # passa do limite de profundidade
    assert any(n.endswith("Documentos/Estudos/Direito Civil") for n in nomes)


def test_pasta_criada_agora_e_achada_e_a_pergunta_vence(arvore):
    relogio = [1000.0]
    c = Cenario(arvore, relogio=relogio)
    c.dizer("abre a pasta de estudos")
    (arvore / "Documentos" / "Pasta Nova").mkdir()
    relogio[0] += 30  # o índice é velho de 30 s: refaz ao não achar
    assert c.dizer("abre a pasta nova") == "Abri a pasta Pasta Nova." and c.ultima == "Documentos/Pasta Nova"
    c.dizer("abre a pasta da OAB")
    relogio[0] += ap.VALIDADE_DA_PERGUNTA + 1
    assert not c.agente.esperando_resposta() and c.dizer("a primeira") is None
