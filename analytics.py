# analytics.py
import json
import os
import time

class AnalisadorSistema:
    def __init__(self, arquivo_log="beatriz_analise.json"):
        self.arquivo_log = arquivo_log
        self.dados = self._carregar()
        self.inicio_sessao = time.time()
        self._buffer_saves = []
        self._ultimo_save = time.time()

    def _carregar(self):
        if os.path.exists(self.arquivo_log):
            try:
                with open(self.arquivo_log, "r") as f:
                    return json.load(f)
            except:
                pass
        return {
            "total_interacoes": 0,
            "total_erros": 0,
            "ferramentas_uso": {},
            "tempo_resposta": [],
            "taxa_sucesso": 0.0
        }

    def _salvar(self):
        try:
            with open(self.arquivo_log, "w") as f:
                json.dump(self.dados, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Erro ao salvar log: {e}")

    def registrar_interacao(self, comando, ferramentas, tempo, sucesso):
        self.dados["total_interacoes"] += 1
        self.dados["tempo_resposta"].append(tempo)
        if not sucesso:
            self.dados["total_erros"] += 1
        for ferramenta in ferramentas:
            self.dados["ferramentas_uso"][ferramenta] = self.dados["ferramentas_uso"].get(ferramenta, 0) + 1
        total = self.dados["total_interacoes"]
        erros = self.dados["total_erros"]
        self.dados["taxa_sucesso"] = ((total - erros) / total * 100) if total else 0.0
        # Salvar periodicamente
        self._buffer_saves.append(time.time())
        if time.time() - self._ultimo_save > 60:
            self._salvar()
            self._ultimo_save = time.time()

    def gerar_relatorio(self):
        tempo_sessao = time.time() - self.inicio_sessao
        rel = f"""Relatório do Sistema:
Total de interações: {self.dados['total_interacoes']}
Erros: {self.dados['total_erros']}
Taxa de sucesso: {self.dados['taxa_sucesso']:.1f}%
Tempo de sessão: {tempo_sessao/60:.1f} minutos
"""
        if self.dados['ferramentas_uso']:
            rel += "\nFerramentas mais usadas:\n"
            for ferramenta, uso in sorted(self.dados['ferramentas_uso'].items(), key=lambda x: x[1], reverse=True)[:10]:
                rel += f"  - {ferramenta}: {uso} usos\n"
        if self.dados['tempo_resposta']:
            tempo_medio = sum(self.dados['tempo_resposta']) / len(self.dados['tempo_resposta'])
            rel += f"\nTempo médio de resposta: {tempo_medio:.2f}s\n"
        return rel

    def diagnostico(self):
        if self.dados['taxa_sucesso'] >= 95:
            return "Sistema saudável."
        elif self.dados['taxa_sucesso'] >= 80:
            return "Sistema com pequenos problemas."
        else:
            return "Sistema crítico."