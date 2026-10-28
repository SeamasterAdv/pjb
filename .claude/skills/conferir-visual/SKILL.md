---
name: conferir-visual
description: Como conferir e mostrar ao usuário uma mudança de visual da Beatriz (painel web, janelas do painel, login, app do celular, laboratórios da esfera e da mascote) no modo demonstração, com fotos do Chromium do Playwright em tamanho de PC ou de iPhone, uma etapa por vez. Use em qualquer mudança em interface_web/ ou interface_app/, ou quando o pedido for "mostra como ficou", "tira uma foto do painel".
---

# Conferir o visual

O usuário decide o visual **vendo**: monte uma etapa, mostre a foto e pergunte o que ajustar antes da próxima
(poucas perguntas objetivas). Não invente enfeite: o tema é preto e branco, com o branco virando **dourado** quando
uma missão é cumprida, e só dados de verdade ("menos enfeite").

## 1. Subir o modo demonstração

Pelo `.claude/launch.json` (com `preview_start` e o nome), nunca pelo Bash:

| Configuração | Porta | Endereço |
|---|---|---|
| `painel-web-demonstracao` | 8765 | `http://127.0.0.1:8765/interface_web/index.html` (login: `login.html`, `?tela=cadastro`, `?tela=senha`) |
| `app-celular-demonstracao` | 8772 | `http://127.0.0.1:8772` (o código de pareamento sai no log do servidor e vale uma vez: para outra foto, reinicie o servidor) |
| `laboratorio-visual` | 8773 | `/interface_web/laboratorio_visual.html` |
| `laboratorio-mascote` | 8771 | `/interface_web/laboratorio_mascote.html` |

Aberto num navegador comum, o painel entra sozinho no modo demonstração (`data-demonstracao`), com os dados de
mentira do fim de cada script (`ESTUDOS_DADOS_FIXOS`, `VD_DADOS_FIXOS`, `BIBLIOTECA_DEMONSTRACAO`…). Se a mudança
mexeu nas opções, refaça esses dados.

## 2. Fotografar no tamanho certo

O painel do navegador do app desktop é estreito (~377 px): bom para conferir o console, ruim para foto. Para foto
use um script com o Playwright do `.venv312` (o Chromium já está instalado por causa do WhatsApp), salvo no
scratchpad:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    navegador = p.chromium.launch()
    pagina = navegador.new_page(viewport={"width": 1366, "height": 820})  # PC; iPhone: 390x844
    pagina.goto("http://127.0.0.1:8765/interface_web/index.html")
    pagina.wait_for_timeout(1500)  # a esfera e a montanha levam um instante
    # pagina.click("text=[ estudos ]")  # abrir a janela que mudou
    pagina.screenshot(path=r"<scratchpad>\painel.png")
    navegador.close()
```

Rode com `.venv312\Scripts\python.exe`, abra a imagem com `Read` para conferir e mande ao usuário com
`SendUserFile`. Tamanhos: PC 1366x820 (as janelas largas, como biblioteca e agenda, somem ou estreitam abaixo de
1250 a 1320 px); iPhone 390x844.

## 3. O que conferir antes de mostrar

- Console sem erro (`read_console_messages` ou `page.on("console")`).
- Os estados: repouso, ouvindo, pensando, falando e o dourado (`data-vitoria`, 5 s).
- A tela estreita, se a mudança for no app.
- O app tem CSP restrita: nada de `style=""` nem script embutido no HTML.

## 4. Mostrar e perguntar

Uma foto (ou antes e depois) por etapa, com uma linha dizendo o que mudou, e as perguntas do que ajustar. Só passe
à próxima etapa com a resposta. No fim, registre no `CLAUDE.md` onde foi conferido ("conferida com o Chromium do
Playwright em 1366x820").
