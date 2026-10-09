import google_agenda
import tools


def test_evento_vai_para_google_agenda_quando_vinculado(monkeypatch):
    chamadas = []
    monkeypatch.setattr(google_agenda, "google_agenda_conectada", lambda: True)
    monkeypatch.setattr(
        google_agenda,
        "criar_evento_google",
        lambda titulo, inicio, fim, descricao, minutos: chamadas.append((titulo, inicio.hour, minutos)) or "criado no google",
    )
    monkeypatch.setattr(tools.os, "startfile", lambda caminho: (_ for _ in ()).throw(AssertionError("não deveria abrir ICS")), raising=False)

    resposta = tools.criar_evento_agenda("Dentista", "amanhã às 14", quando_lembrar="30 minutos")

    assert resposta == "criado no google"
    assert chamadas == [("Dentista", 14, 30)]


def test_falha_no_google_cai_para_agenda_do_computador(monkeypatch, tmp_path):
    abertos = []
    monkeypatch.setattr(google_agenda, "google_agenda_conectada", lambda: True)

    def falhar(*args):
        raise RuntimeError("API desativada")

    monkeypatch.setattr(google_agenda, "criar_evento_google", falhar)
    monkeypatch.setattr(tools.os, "startfile", abertos.append, raising=False)
    monkeypatch.setattr(tools.os.path, "dirname", lambda _: str(tmp_path))

    resposta = tools.criar_evento_agenda("Reunião", "10/12 às 9")

    assert "API desativada" in resposta
    assert len(abertos) == 1 and abertos[0].endswith(".ics")


def test_listar_compromissos_sem_vinculo_orienta_o_usuario(monkeypatch):
    monkeypatch.setattr(google_agenda, "google_agenda_conectada", lambda: False)
    assert "conectar google agenda" in tools.listar_compromissos()


def test_sem_credenciais_explica_o_que_falta(monkeypatch, tmp_path):
    monkeypatch.setattr(google_agenda, "GOOGLE_CREDENTIALS_FILE", str(tmp_path / "nao_existe.json"))
    monkeypatch.setattr(google_agenda, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(google_agenda, "GOOGLE_CLIENT_SECRET", "")
    assert "Credenciais do Google não encontradas" in google_agenda.conectar_google_agenda()
