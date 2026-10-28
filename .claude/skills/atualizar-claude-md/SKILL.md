---
name: atualizar-claude-md
description: Como registrar no CLAUDE.md da Beatriz uma mudança de comportamento (agente, rota, comando falado, variável do .env, arquivo em banco_memoria, medição, decisão do usuário), no tom e na estrutura que o arquivo já tem. Use ao terminar qualquer mudança que altere o que a Beatriz faz, ou quando o pedido for "documenta isso", "atualiza o CLAUDE.md".
---

# Atualizar o CLAUDE.md

O `CLAUDE.md` é o manual do projeto: o Claude o lê em toda sessão. Tudo o que muda o comportamento entra nele
**na mesma mudança**, não depois.

## Onde escrever

Procure a seção certa antes de escrever (`Grep` pelo nome do módulo ou do assunto); quase sempre já existe uma.

| Mudou | Onde |
|---|---|
| A ordem do laço do `main` (rota nova, agente novo) | "Roteamento de cada comando": um item na posição exata, com o motivo da posição |
| Um módulo com seção própria (agenda, prazos, impressora, controle, ArtCraft, IAs, memória…) | A seção dele |
| Um módulo pequeno | Um item em "Outros módulos" |
| Comando para rodar, ambiente virtual, modelo do Ollama | "Comandos" |
| Tempo, RAM, VRAM | "Desempenho" (com a máquina e o mês) |
| Regra que vale para tudo | "Convenções e cuidados" |
| Recurso novo que o usuário vê | Uma linha na lista da "Visão geral" |

Prefira **editar a frase que ficou errada** a acrescentar uma nova que a contradiz. O que deixou de existir sai.

## Como escrever (o tom do arquivo)

- Português, frases curtas e diretas, sem marketing nem emoji.
- **Nomes de verdade entre crases**: funções, classes, constantes, arquivos, variáveis do `.env`
  (`AgentePrazos.responder`, `RE_FORA`, `CAMINHO_PRAZOS`, `banco_memoria/prazos_sem_expediente.json`).
- **Exemplos de frases faladas entre aspas**, com o que cai e o que fica de fora:
  "liga o ar em 22 graus"; ficam de fora "qual a temperatura hoje?" e "desliga" sozinho.
- **Números com o padrão entre parênteses**: `JANELA_CONVERSA_SEGUNDOS` (40 s), `LIMITE_DO_TEXTO` (7000 caracteres).
- **O motivo**, quando não é óbvio: "antes das leis: 'qual o prazo da apelação?' não é o artigo";
  "por isso…", "antes, …; agora, …".
- **Quem pediu e quando**, em decisões do usuário: "(pedido do usuário, out/2026)", "(escolha do usuário: …)".
  Datas sempre em mês/ano ou dia/mês absolutos, nunca "ontem" ou "semana passada".
- **O que foi medido e o que não foi testado**: "Medido: …"; "Não foi testado com o aparelho"; "Conferido no
  Chromium em 1366x820". Sem inventar número.
- **Onde está**: em que objeto cada processo tem o seu (`main.X`, `painel_web.ApiPainel._x`, `interface_texto`
  `self.x`).
- **Os testes**: "Testes em `test_x.py` (…falsos); o `conftest.py` …".
- Arquivos novos em `banco_memoria/`: o caminho, a constante e "fora do git".

## Antes de terminar

- Releia o trecho que mudou junto com o que está em volta: nenhuma frase antiga pode contradizer a nova.
- Variável nova do `.env`: o nome, o padrão e o que acontece sem ela.
- Não mexa em seções que a mudança não tocou.
