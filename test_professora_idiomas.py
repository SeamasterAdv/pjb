import json

import professora_idiomas as professora


def configurar_memoria_temporaria(tmp_path, monkeypatch):
    pasta = tmp_path / "idiomas"
    monkeypatch.setattr(professora, "PASTA_IDIOMAS", str(pasta))
    monkeypatch.setattr(professora, "ARQUIVO_PROGRESSO", str(pasta / "progresso.json"))


def test_treino_corrige_e_persiste_progresso(tmp_path, monkeypatch):
    configurar_memoria_temporaria(tmp_path, monkeypatch)

    ativou, mensagem = professora.processar_comando_idiomas("modo professora de idiomas")
    assert ativou
    assert "ativado" in mensagem

    iniciou, exercicio = professora.processar_comando_idiomas("treinar inglês")
    assert iniciou
    assert "Traduza" in exercicio

    corrigiu, feedback = professora.processar_comando_idiomas("Olá, como você está?")
    assert corrigiu
    assert "Muito bem" in feedback
    assert "continuar ou sair" in feedback

    continuou, proximo_exercicio = professora.processar_comando_idiomas("continuar")
    assert continuou
    assert "Traduza" in proximo_exercicio

    with open(professora.ARQUIVO_PROGRESSO, "r", encoding="utf-8") as arquivo:
        estado = json.load(arquivo)
    assert estado["idiomas"]["ingles"]["acertos"] == 1
    assert estado["idiomas"]["ingles"]["exercicios"] == 1


def test_frances_e_saida_da_aula(tmp_path, monkeypatch):
    configurar_memoria_temporaria(tmp_path, monkeypatch)

    iniciou, exercicio = professora.processar_comando_idiomas("treinar francês")
    assert iniciou
    assert "Traduza" in exercicio

    saiu, mensagem = professora.processar_comando_idiomas("sair")
    assert saiu
    assert "Aula encerrada" in mensagem


def test_consulta_dicionario_nativo_sem_aula(tmp_path, monkeypatch):
    configurar_memoria_temporaria(tmp_path, monkeypatch)

    consultou, resposta = professora.processar_comando_idiomas("como se fala casa em inglês?")
    assert consultou
    assert "house" in resposta
    assert "Pronúncia" in resposta

    consultou, resposta = professora.processar_comando_idiomas("como se fala água em francês?")
    assert consultou
    assert "eau" in resposta