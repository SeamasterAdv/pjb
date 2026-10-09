# agente_aprendizagem.py
class AgenteAprendizagem:
    def __init__(self):
        self.historico = []

    def normalizar(self, texto):
        return texto.lower().strip()

    def contexto_para_prompt(self, comando):
        return ""

    def orientacao_de_resposta(self):
        return ""

    def registrar_interacao(self, comando, ferramentas, tempo, sucesso):
        pass

    def gerar_relatorio(self):
        return "Nenhum aprendizado registrado."