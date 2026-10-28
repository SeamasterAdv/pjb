# Skills do Claude Code para a Beatriz

Cada skill é uma **pasta** aqui dentro com um arquivo `SKILL.md`. O Claude Code
lê o nome e a descrição de todas na abertura da sessão e carrega o resto só
quando o pedido combina com a descrição (ou quando você digita `/nome-da-skill`).

```
.claude/skills/
├── README.md              ← este arquivo
├── _modelo/
│   └── SKILL.md.exemplo   ← copie para começar (não é carregado: falta o nome SKILL.md)
└── minha-skill/
    ├── SKILL.md           ← obrigatório
    └── (scripts, exemplos, textos de apoio: opcionais)
```

## Como criar uma skill

1. Copie a pasta `_modelo` com outro nome (só letras minúsculas, números e
   hífen, por exemplo `nova-ferramenta`).
2. Renomeie `SKILL.md.exemplo` para `SKILL.md`.
3. Preencha o `name` (igual ao nome da pasta) e a `description`: é por ela que
   o Claude decide quando usar a skill, então diga **o que faz e quando usar**.
4. Escreva as instruções embaixo, em português, como passos.
5. Abra uma sessão nova do Claude Code (as skills são lidas na abertura).

Dica: no Claude Code, peça "cria uma skill para …" e ele usa o `skill-creator`
para montar a pasta aqui.

## Ideias para este projeto

- `nova-ferramenta`: como acrescentar um comando em `tentar_executar_ferramenta`
  e no `MAPA_DE_FERRAMENTAS`, com o teste.
- `novo-agente`: o passo a passo de um agente novo (regras sem LLM, ordem no
  roteamento do `main`, os dois painéis, o `conftest.py`).
- `rodar-testes`: sempre `.venv312\Scripts\python.exe -m pytest`.
- `conferir-visual`: abrir o painel no navegador e mostrar cada etapa.

## Skills instaladas

Feitas para este projeto:

- `novo-agente`: o passo a passo de um agente novo.
- `atualizar-claude-md`: onde e como registrar uma mudança no `CLAUDE.md`, no tom dele.
- `isolar-nos-testes`: nenhum teste toca dados ou aparelhos de verdade; a fixture de um módulo novo no `conftest.py`.
- `conferir-visual`: o modo demonstração, as fotos com o Playwright e mostrar uma etapa por vez.

De fora:

- `ponytail`, `ponytail-review`, `ponytail-audit`: do [Ponytail](https://github.com/DietrichGebert/ponytail)
  (licença MIT, cópia em cada pasta), só as skills, sem os hooks nem o placar. Cada uma ganhou no fim
  uma seção "Neste projeto". Para atualizar, copie de novo o `SKILL.md` de lá e acrescente essa seção.
