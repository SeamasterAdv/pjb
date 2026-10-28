---
name: isolar-nos-testes
description: Como garantir que os testes da Beatriz nunca toquem dados nem aparelhos de verdade (banco_memoria, ChromaDB, agenda, impressora, controle remoto, IAs na nuvem, Planalto, câmera, tela, microfone) e como acrescentar ao conftest.py a fixture de um módulo novo. Use ao escrever ou mexer em testes, ao criar um módulo que grava em disco ou fala com algo fora do PC, ou quando um teste deixar arquivo, mandar comando ou abrir algo de verdade.
---

# Isolar nos testes

**Regra:** nenhum teste lê ou grava `banco_memoria/` de verdade, nem imprime, manda infravermelho, chama o Claude ou
o Gemini, baixa do Planalto, fotografa a tela, abre a câmera ou roda os apps da ArtCraft.

Por quê (histórico real, out/2026): os testes abriram o ChromaDB de verdade com a Beatriz aberta e o índice
corrompeu (ela caía logo depois do login); um teste que importava o `main` dentro dele gravou na agenda de verdade.

## Como está montado

- O `conftest.py` aponta `BEATRIZ_BANCO_MEMORIA` para uma pasta temporária **antes** de qualquer import do `memory`,
  e apaga `BEATRIZ_BIBLIOTECA_REMOTA`.
- Uma fixture `autouse=True` por assunto (`agenda_temporaria`, `prazos_isolados`, `controle_remoto_intocado`,
  `impressora_intocada`…). Leia as que já existem antes de escrever outra: o módulo pode já estar coberto.
- Dois tipos de fixture:
  - **isolar** um arquivo: troca o caminho ou o objeto de registro por um em `tmp_path`;
  - **proibir** um efeito: troca a função que fala com o mundo por uma que levanta
    `AssertionError("um teste tentou … de verdade")`. Proibir é melhor que fingir: o teste que esquecer o falso falha
    alto, em vez de mandar o comando.

## Módulo novo: o que acrescentar

1. Liste o que o módulo toca fora do processo: arquivo em `banco_memoria/`, rede, aparelho, subprocesso, janela.
2. Dê a cada um **um ponto só** para trocar: o caminho numa constante ou num argumento (`CAMINHO_X`), e a ida ao
   mundo num método pequeno (`_baixar_da_internet`, `_aparelho`, `_nuvem`). Sem isso não dá para isolar.
3. No `conftest.py`, no estilo das vizinhas, com a docstring dizendo o que nunca acontece:

```python
@pytest.fixture(autouse=True)
def assunto_isolado(tmp_path, monkeypatch):
    """Nenhum teste … de verdade, e banco_memoria/assunto.json nunca é lido nem gravado."""
    import modulo_assunto

    def proibido(*args, **kwargs):
        raise AssertionError("um teste tentou usar … de verdade")

    monkeypatch.setattr(modulo_assunto, "CAMINHO_ASSUNTO", str(tmp_path / "assunto_teste.json"))
    monkeypatch.setattr(modulo_assunto.Cliente, "_enviar", proibido)
    if "main" in sys.modules:  # o objeto do main já foi criado com o caminho de verdade
        monkeypatch.setattr(sys.modules["main"].ASSUNTO, "registro", modulo_assunto.Registro(str(tmp_path / "assunto_teste.json")))
        monkeypatch.setattr(sys.modules["main"].ASSUNTO, "pendencia", None)
    yield
```

4. Os objetos já criados no import (`main.ASSUNTO`, o agente das ferramentas em `tools`) guardam o caminho de
   verdade: troque **o atributo do objeto**, não só a constante. Por isso o `if "main" in sys.modules`.
5. Zere as pendências (`pendencia = None`) para um teste não herdar a pergunta de outro.

## Nos próprios testes

- Use `tmp_path` e falsos (LLM, impressora, controle, executor) passados por argumento.
- Um teste que **importa o `main` dentro dele** roda depois do `conftest` ter trocado as coisas: troque você mesmo a
  agenda, o calendário e o que mais ele usar.
- Relógio fixo quando a data importa (veja `test_agenda.py`, `test_prazos.py`).

## Conferir

- `.venv312\Scripts\python.exe -m pytest` (a suíte inteira).
- Depois, nada novo em `banco_memoria/` (`git status` não mostra, porque ele está no `.gitignore`: olhe a data dos
  arquivos de lá) e nada impresso, aberto ou mandado.
- Documente a fixture no `CLAUDE.md`, na seção do módulo ("o `conftest.py` proíbe …").
