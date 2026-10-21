"""Processos filhos leves.

No Windows o `multiprocessing` usa "spawn": cada filho começa reimportando o `main.py` inteiro (chromadb, Whisper's
dependências, pygame, as 12 integrações...), mesmo quando só precisa do próprio alvo. Medido com o painel: 184 MB e 4,9 s
assim, contra 127 MB e 2,6 s só com o necessário. `iniciar_leve` esconde, só durante o `start()`, o arquivo e o `spec` do
módulo principal para o filho não os reimportar.

Regra: o alvo e os argumentos têm de ser importáveis sem o `main.py` (funções de módulos próprios, `Event`, `Queue` e
proxies do Manager servem; funções definidas dentro do `main.py` não).
"""
import multiprocessing
import sys


def iniciar_leve(alvo, args=(), daemon: bool = True):
    """Cria e inicia um processo `spawn` que não reimporta o módulo principal. Devolve o processo."""
    processo = multiprocessing.get_context("spawn").Process(target=alvo, args=args, daemon=daemon)
    principal = sys.modules.get("__main__")
    guardados = {}
    if principal is not None:
        for atributo in ("__file__", "__spec__"):
            if hasattr(principal, atributo):
                guardados[atributo] = getattr(principal, atributo)
        principal.__spec__ = None  # sem nome de módulo...
        if "__file__" in guardados:
            del principal.__file__  # ...e sem caminho: o filho não tem o que reimportar
    try:
        processo.start()
    finally:
        if principal is not None:
            for atributo, valor in guardados.items():
                setattr(principal, atributo, valor)
            if "__spec__" not in guardados:
                try:
                    del principal.__spec__
                except AttributeError:
                    pass
    return processo
