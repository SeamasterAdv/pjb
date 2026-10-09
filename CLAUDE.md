# CLAUDE.md

Orientações para o Claude Code ao trabalhar neste repositório.

## Visão geral

**Beatriz** é uma assistente pessoal em português do Brasil, para Windows, que roda localmente. Ela combina:
- LLM local via **Ollama** (`llm_clients.py`), sem dependência obrigatória de APIs na nuvem;
- voz (entrada com `speech_recognition`, saída com `edge-tts` + `pygame`);
- interface gráfica em Tkinter (`interface_texto.py`);
- uma biblioteca de conhecimento estilo RAG em **ChromaDB** (`memory.py`), alimentada por documentos locais, uma vault do Obsidian e o histórico de conversas;
- ferramentas de automação (navegador, e-mail, Google Agenda, WhatsApp, clima, notícias etc. em `tools.py`).

Todo o código, os identificadores, os prompts e as mensagens ao usuário estão em português. Mantenha essa convenção.

## Comandos

```powershell
# Executar (o launcher exige o ambiente Python 3.12 em .venv312)
.\Iniciar_Beatriz.bat
# ou diretamente
.venv312\Scripts\python.exe main.py

# Instalar dependências
.venv312\Scripts\python.exe -m pip install -r requirements.txt

# Testes (pytest)
.venv312\Scripts\python.exe -m pytest
.venv312\Scripts\python.exe -m pytest test_comunicacao.py::test_comando_de_pesquisa_nao_desliga_o_sistema

# API HTTP local (FastAPI; exige BEATRIZ_API_KEY no .env)
.venv312\Scripts\python.exe api.py   # 127.0.0.1:8000, GET /saude, POST /agenda com o header X-API-Key
```

Existem três ambientes virtuais (`venv/`, `.venv/`, `.venv312/`). O ambiente oficial é o **`.venv312`** (é o que o `Iniciar_Beatriz.bat` usa). O `.vscode/settings.json` ainda aponta para `.venv`.

O Ollama precisa estar rodando (`OLLAMA_HOST`, padrão `http://localhost:11434`), com o modelo `OLLAMA_MODEL` (padrão `llama3.1:8b`) e, para visão, `OLLAMA_VISION_MODEL` (padrão `llava`).

## Arquitetura

### Inicialização (`main.py`)
1. `__main__` abre a tela de carregamento (Tkinter) em um **processo separado** (`multiprocessing`) e depois chama `main()`.
2. `main()` inicia a indexação da biblioteca em uma thread de fundo e o observador de arquivos (`inicializar_biblioteca_automatica`), abre a janela de chat (`interface_texto.iniciar`) em outro processo, sincroniza o Google Drive se estiver configurado, faz o diagnóstico de hardware e liga a webcam/gestos (`visao.py`) se houver câmera.
3. Entra no laço principal. O processo da interface e o laço principal se comunicam por `multiprocessing.Event` (liga/desliga o microfone) e por um `Manager().dict` (dispositivos de áudio escolhidos).

### Roteamento de cada comando (laço do `main()`), nesta ordem
1. `professora_idiomas.processar_comando_idiomas`: modo de aula de inglês/francês, com estado persistido em JSON na vault.
2. `comando_solicita_encerramento`: palavras de desligamento.
3. `tentar_executar_ferramenta`: casamento por **palavras-chave** (sem LLM) que dispara as funções de `tools.py`/`memory.py`. Para adicionar um comando nativo, acrescente um bloco `if any(termo in c ...)` aqui.
4. `classificar_com_ollama`: se o LLM classificar o comando como "simples", responde direto com `llm_clients.chat_beatriz`.
5. `memory.notelooklm_responder`: RAG (documentos + memória do usuário + histórico) → Ollama.
6. Se a resposta indicar falta de contexto (`deve_pesquisar_web`/`resposta_sem_contexto`), recorre à pesquisa na web (`responder_com_pesquisa_web`).

Todas as respostas passam por `falar_e_registrar` (TTS + `memory.registrar_conversa` → `historico_conversas.jsonl`), e as métricas vão para `analytics.AnalisadorSistema` (`beatriz_analise.json`).

`MAPA_DE_FERRAMENTAS` em `main.py` registra as funções de ferramenta pelo nome.

### Memória / biblioteca (`memory.py`)
- Coleções ChromaDB persistentes em `banco_memoria/` (`memoria_usuario`, `documentos_usuario_v2`); se o banco persistente falhar, usa um cliente em RAM.
- Fontes indexadas (`_fontes_raiz`): `documentos_assistente/` (`CAMINHO_DOCUMENTOS`), `obsidian/` (`VAULT_OBSIDIAN`) e `historico_conversas.jsonl`.
- Indexação incremental com IDs estáveis (sha256 do caminho), controlada por `banco_memoria/manifesto_fontes_v2.json`, com auditoria em `auditoria_memoria.jsonl`. Extrai texto de PDF, DOCX, PPTX, XLSX, HTML e imagens.
- A indexação usa uma trava (`_INDEXACAO_LOCK`) para impedir varreduras concorrentes; o progresso é consultado com `status_indexacao()`.
- `deve_consultar_documentos` decide se a pergunta deve usar o RAG (comandos de sistema/agenda/navegador não usam).
- Os fatos de longo prazo sobre o usuário ficam em `obsidian/geral/memoria_usuario.md` (`atualizar_memoria_usuario`, `buscar_memoria`).
- Os "modos de conversa" (`padrão`, `comunicacao`, `filosofia`, `psicologia`, `direito`) são definidos em `config.MODOS_PASTAS` e mapeados para subpastas.

### Configuração (`config.py`)
Lê o `.env` com `python-dotenv`. Define as chaves de API e os caminhos de tokens do Google (Drive, Calendar, Gmail), a personalidade da Beatriz (`PERSONALIDADE_BEATRIZ`, usada como prompt de sistema em `llm_clients.chat_beatriz`), os modos e as pastas. **Ao ser importado, cria as pastas da vault e de documentos.** `AUTO_CARREGAR_FONTES` (padrão `true`) liga ou desliga a indexação automática.

### Outros módulos
- `voice.py`: `falar`, `falar_async`, `ouvir`, seleção de microfone e saída de áudio. Se o áudio não estiver disponível, degrada sem quebrar.
- `visao.py`: detecção de gestos da mão com MediaPipe/OpenCV (`GestosMaoDetector`, `GestorVisao`).
- `google_drive_sync.py`: OAuth e download de uma pasta do Drive.
- `agenda_lembretes.py`, `agente_aprendizagem.py`, `apps.py`: stubs mínimos, mantidos para compatibilidade.

## Convenções e cuidados

- **Ações externas exigem confirmação**: `enviar_email`, `enviar_mensagem_whatsapp`, `criar_evento_agenda` e `sync_notelooklm_from_google_drive` recebem `confirmado=False` e pedem confirmação via `input()` (`_confirmar_acao`/`pedir_confirmacao`). Preserve esse padrão em qualquer ferramenta nova que tenha efeito externo.
- As ferramentas devolvem **strings** com mensagens para o usuário. Os erros são tratados e retornados como texto (`tentativa_segura`), sem propagar exceções para o laço principal.
- Muitos módulos têm efeitos colaterais ao serem importados (inicialização do Ollama, do ChromaDB, do `pygame.mixer` e criação de pastas). Os testes importam `main` e `memory` diretamente e usam `monkeypatch` nesses globais.
- Os segredos ficam no `.env` e nos arquivos `*token.json`/`google_credentials.json` (todos no `.gitignore`). Nunca faça commit deles.
- `beatriz.log` cresce bastante (dezenas de MB) e está no `.gitignore`.
- O repositório ainda não tem commits. Vários arquivos de `projetoBeatriz/` e alguns JSONs aparecem como "adicionados ao índice e depois apagados" (`AD`); confirme com o usuário antes de mexer no índice do git.
