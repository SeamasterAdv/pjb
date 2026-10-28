---
name: novo-agente
description: Passo a passo para criar um agente novo da Beatriz (um assunto que ela passa a entender pela voz, pelo WhatsApp, pelo app e pelos painéis) e ligá-lo ao roteamento do main.py, ao painel web e ao painel clássico, com os testes. Use quando o pedido for acrescentar um agente, uma rota nova no laço do main ou um comando falado de um assunto novo.
---

# Agente novo da Beatriz

Um agente é um módulo `agente_<assunto>.py` que reconhece os pedidos dele **por regras, sem LLM**, e devolve a
resposta ou `None` (aí o roteamento segue para o próximo). O molde mais limpo é o dos prazos
(`agente_prazos.py`, ligado em `main.py`, `painel_web.py` e `interface_texto.py`): copie a forma dele.

## 1. Antes de escrever

- Leia no `CLAUDE.md` a seção **"Roteamento de cada comando"**: a posição do agente na fila decide o que ele rouba
  dos outros. Escolha onde ele entra e diga por quê ("antes das leis: 'qual o prazo da apelação?' não é o artigo").
- Liste frases que **devem** cair nele e frases parecidas que **não devem** (são de outro agente). Elas viram os testes.

## 2. O módulo `agente_<assunto>.py`

- Docstring no topo em português explicando o que ele faz e o que fica de fora.
- Classe `Agente<Assunto>` com:
  - `responder(texto) -> Resposta<Assunto> | None` (ou `str | None`). Compare no texto **sem acentos e em
    minúsculas**, com expressões regulares nomeadas em maiúsculas (`RE_...`) e uma `RE_FORA` para o que não é dele.
  - Se faz perguntas ("Quer que eu marque?"), uma pendência com prazo (2 a 5 min) e
    `esperando_resposta() -> bool`, para o "sim" passar sem o nome.
  - A resposta como `dataclass` quando há fala curta e texto completo: `fala` (falada) e `painel` (inteiro).
- Uma função `criar_agente_<assunto>(...)` que monta o agente; dependências (agenda, IA, caminhos) entram por
  argumento ou função (`agenda=lambda: AGENDA.agenda`), para os testes trocarem.
- Dados guardados em `banco_memoria/` (fora do git), com o caminho em `config.py`/`.env`
  (`CAMINHO_<ASSUNTO>`), lidos a cada uso e gravados de uma vez (temporário + `os.replace`): o processo principal
  e os painéis têm cada um o seu agente e usam o mesmo arquivo.
- Ação externa (mandar mensagem, gastar papel, mexer fora do PC) **pergunta antes**.
- Tudo em português: nomes, mensagens, comentários.

## 3. Ligar nos três lugares

1. **`main.py`** (voz, WhatsApp e app):
   - `from agente_<assunto> import criar_agente_<assunto>` e um global `<ASSUNTO> = criar_agente_<assunto>(...)`
     com um comentário de uma linha sobre o que ele faz.
   - No laço do `main()`, na posição escolhida, no mesmo formato dos vizinhos:
     ```python
     resposta_x = ASSUNTO.responder(comando)
     if resposta_x:
         falar_e_registrar(comando, resposta_x.fala, texto_painel=resposta_x.painel)
         analisador.registrar_interacao(comando, ["assunto"], 0.0, True)
         continue
     ```
   - Se tem `esperando_resposta()`, acrescente-o à lista em `filtrar_fala_por_ativacao`
     (`... or PRAZOS.esperando_resposta()`).
   - Se é só do dono, garanta que `RE_SO_DO_DONO` pega os pedidos dele (convidados não chegam às rotas do dono).
2. **`painel_web.py`**: `self._<assunto> = criar_agente_<assunto>(...)` no `ApiPainel.__init__` e a chamada no
   chat digitado, na mesma ordem relativa do `main` (`falar_async` + `self._emitir({"tipo": "resposta", ...})`).
3. **`interface_texto.py`**: `self.<assunto> = criar_agente_<assunto>(...)` e a chamada no chat do painel clássico.

## 4. Testes

- `test_<assunto>.py` com as frases do passo 1: as que caem nele e as que **passam** (`responder` devolve `None`).
- Nada de verdade nos testes: banco, arquivos e rede trocados por temporários e falsos (`tmp_path`, `monkeypatch`).
- No `conftest.py`, uma fixture `autouse` que aponta o arquivo do `main.<ASSUNTO>` para um temporário e proíbe
  o que tem efeito fora (veja `prazos_isolados`). Um teste que importa o `main` dentro dele precisa trocar isso
  ele mesmo.
- Rode a suíte inteira: `.venv312\Scripts\python.exe -m pytest`. Os testes de roteamento de outros agentes
  costumam acusar quando o novo rouba frases deles.

## 5. Documentar

- No `CLAUDE.md`: a linha nova em **"Roteamento de cada comando"** (com a posição e o motivo), um parágrafo do
  módulo em "Outros módulos" ou uma seção própria, e as variáveis novas do `.env`.
- Commit só quando o usuário pedir.
