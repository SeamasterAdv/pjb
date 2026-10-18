"""Filmes em cartaz, sinopses e melhores cinemas, com dados falsos no formato do Ingresso.com."""
from datetime import date

import requests

import cinema
import main

HOJE = date(2026, 10, 14)

ESTADOS = [
    {"name": "São Paulo", "cities": [{"id": "1", "name": "São Paulo"}, {"id": "15", "name": "Campinas"}]},
    {"name": "Rio de Janeiro", "cities": [{"id": "2", "name": "Rio de Janeiro"}]},
]


def _filme(titulo, cinemas, estreia, generos, **extras):
    return {"title": titulo, "countIsPlaying": cinemas, "premiereDate": {"localDate": f"{estreia}T00:00:00+00:00"},
            "genres": generos, "isComingSoon": False, **extras}


FILMES = [
    _filme("Verity", 389, "2026-09-30", ["Drama"], synopsis="Uma escritora aceita terminar a série de outra autora.",
           duration=118, contentRating="16 anos"),
    _filme("Se Eu Fosse Você 3", 390, "2026-10-08", ["Comédia"]),
    _filme("Street Fighter", 365, "2026-10-14", ["Ação"]),
    _filme("Outra Mamãe", 360, "2026-10-08", ["Drama", "Terror"]),
    _filme("Vingadores: Doutor Destino", 377, "2026-12-16", ["Ação", "Aventura"], isComingSoon=True,
           synopsis="Os Vingadores enfrentam o Doutor Destino."),
]

CINEMAS = [
    {"id": "a", "name": "Cinépolis JK Iguatemi", "neighborhood": "Vila Nova Conceição", "totalRooms": 9, "rooms": []},
    {"id": "b", "name": "Cinemark Eldorado", "neighborhood": "Pinheiros", "totalRooms": 10,
     "rooms": [{"fullName": "Sala 3 - LASER"}]},
    {"id": "c", "name": "Cinema Belas Artes", "neighborhood": "Consolação", "totalRooms": 6, "rooms": []},
    {"id": "d", "name": "Cine Araújo Campo Limpo", "neighborhood": "Santo Amaro", "totalRooms": 6, "rooms": []},
]
# Formatos das sessões de cada cinema, como vêm em sessions/city/<cidade>/theater/<cinema>.
FORMATOS = {"a": ["IMAX", "Vip", "Legendado", "Dublado"], "b": ["XD", "D-Box", "Dublado"],
            "c": ["Legendado", "Nacional"], "d": ["Dublado", "Normal"]}


def _sessoes(tipos):
    return [{"movies": [{"rooms": [{"sessions": [{"types": [{"name": t} for t in tipos]}]}]}]}]


def _ingresso_falso(monkeypatch):
    def buscar(caminho, validade=None, **parametros):
        if caminho == "states":
            return ESTADOS
        if caminho.startswith("events/city/"):
            return FILMES
        if caminho.startswith("theaters/city/"):
            return CINEMAS
        if caminho.startswith("sessions/city/"):
            return _sessoes(FORMATOS[caminho.rsplit("/", 1)[1]])
        raise AssertionError(caminho)

    monkeypatch.setattr(cinema, "_buscar", buscar)


def test_reconhece_pedidos_de_cinema_e_ignora_conversa():
    for frase in ["quais filmes estão em cartaz?", "Beatriz, filmes em cartaz", "o que está passando no cinema",
                  "quais filmes de terror estão passando", "quais as estreias do cinema essa semana"]:
        assert cinema.identificar_pedido(frase) == "filmes", frase
    for frase in ["quais os melhores cinemas de São Paulo", "qual o melhor cinema para ver filmes",
                  "me indica um cinema bom"]:
        assert cinema.identificar_pedido(frase) == "cinemas", frase
    assert cinema.identificar_pedido("me fala a sinopse de Verity") == "sinopse"
    for frase in ["assisti um filme no cinema ontem", "que horas são", "qual a previsão do tempo"]:
        assert cinema.identificar_pedido(frase) is None, frase


def test_cidade_dita_no_pedido(monkeypatch):
    _ingresso_falso(monkeypatch)
    assert cinema._cidade_do_pedido("filmes em cartaz em Campinas hoje")["id"] == "15"
    assert cinema._cidade_do_pedido("o que está passando no cinema no rio")["id"] == "2"
    assert cinema._cidade_do_pedido("quais filmes estão em cartaz") is None


def test_resumo_em_ordem_de_cinemas_com_pre_venda_separada():
    texto = cinema.resumo_filmes(FILMES, "São Paulo", "quais filmes estão em cartaz", hoje=HOJE)
    assert texto.startswith("Em São Paulo há 4 filmes em cartaz.")
    assert texto.index("Se Eu Fosse Você 3") < texto.index("Verity") < texto.index("Street Fighter")
    assert "Street Fighter, ação, que estreia hoje" in texto
    assert "Em pré-venda: Vingadores: Doutor Destino, que estreia em 16 de dezembro." in texto


def test_filtra_por_genero_e_estreias():
    terror = cinema.resumo_filmes(FILMES, "São Paulo", "filmes de terror em cartaz", hoje=HOJE)
    assert "1 filme de terror em cartaz" in terror and "Verity" not in terror
    assert "É Outra Mamãe." in terror  # sem repetir o gênero que foi pedido

    estreias = cinema.resumo_filmes(FILMES, "São Paulo", "quais as estreias do cinema", hoje=HOJE)
    assert estreias.startswith("Estreias da semana em São Paulo:")
    assert "Street Fighter" in estreias and "Verity" not in estreias


def test_sinopse_pelo_nome_inteiro_ou_parcial():
    assert cinema.sinopse(FILMES, "me fala a sinopse de Verity") == (
        "Verity, drama, 118 minutos, classificação 16 anos. Uma escritora aceita terminar a série de outra autora."
    )
    assert cinema.sinopse(FILMES, "qual a sinopse do vingadores").startswith("Vingadores: Doutor Destino, ação, aventura.")
    assert "Não achei esse filme" in cinema.sinopse(FILMES, "sinopse de um filme que não existe nada")


def test_sinopse_longa_termina_numa_frase_inteira_e_sem_classificacao_vazia():
    longa = ("Primeira frase da história. " * 12 + "\nSegunda parte que fica cortada no meio de uma palavra " * 5)
    filme = _filme("Longo", 10, "2026-10-01", ["Drama"], synopsis=longa, contentRating="Verifique a Classificação")
    texto = cinema.sinopse([filme], "sinopse de longo")
    assert texto.startswith("Longo, drama.") and "classificação" not in texto
    assert texto.endswith("história.") and "\n" not in texto


def test_melhores_cinemas_pelos_formatos_das_salas(monkeypatch):
    _ingresso_falso(monkeypatch)
    assert cinema.melhores_cinemas("1", CINEMAS, "São Paulo") == (
        "Pelos recursos das salas, os melhores cinemas em São Paulo são: "
        "Cinépolis JK Iguatemi, em Vila Nova Conceição, com IMAX e sala VIP; "
        "Cinemark Eldorado, em Pinheiros, com XD, D-Box e projeção a laser. "
        "Para filmes de arte e legendados: Cinema Belas Artes, em Consolação."
    )


def test_cidade_sem_sala_especial_cita_os_maiores_e_ignora_falhas(monkeypatch):
    _ingresso_falso(monkeypatch)
    simples = [CINEMAS[3], {"id": "x", "name": "Cine Centro", "neighborhood": "Centro", "totalRooms": 2, "rooms": []}]
    original = cinema._buscar

    def buscar(caminho, validade=None, **parametros):
        if caminho.endswith("/theater/x"):
            raise requests.ConnectionError("falhou")  # um cinema que falha não derruba a resposta
        return original(caminho, **parametros)

    monkeypatch.setattr(cinema, "_buscar", buscar)
    texto = cinema.melhores_cinemas("1", simples, "Itu")
    assert texto == ("Itu tem 2 cinemas no Ingresso.com, mas nenhum com sala especial como IMAX ou VIP. "
                     "Os maiores são: Cine Araújo Campo Limpo, em Santo Amaro e Cine Centro, em Centro.")


def test_sem_conexao_avisa_e_pedido_comum_nao_e_de_cinema(monkeypatch):
    def falhar(caminho, **parametros):
        raise requests.ConnectionError("sem internet")

    monkeypatch.setattr(cinema, "_buscar", falhar)
    assert "Não consegui consultar a programação" in cinema.responder_pedido_de_cinema("filmes em cartaz")
    assert cinema.responder_pedido_de_cinema("me conta uma piada") is None


def test_filmes_em_cartaz_hoje_nao_vira_a_data(monkeypatch):
    # "hoje" respondia a data e a hora antes de chegar no cinema.
    monkeypatch.setattr(main, "responder_pedido_de_cinema", lambda texto: "programação" if "cartaz" in texto else None)
    assert main.tentar_executar_ferramenta("quais filmes estão em cartaz hoje") == (True, "programação")
