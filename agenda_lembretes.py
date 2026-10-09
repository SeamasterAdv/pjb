# agenda_lembretes.py
class GerenciadorAgenda:
    def __init__(self):
        self.compromissos = []

    def criar_compromisso(self, titulo, data, hora, descricao=""):
        # Implementação simplificada
        return {"titulo": titulo, "inicio": f"{data}T{hora}"}

    def url_google_agenda(self, compromisso):
        return "https://calendar.google.com"

    def listar_compromissos(self):
        return "Nenhum compromisso."

    def verificar_lembretes(self):
        return []