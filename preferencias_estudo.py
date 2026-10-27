"""Como saem os resumos, as fichas de estudo, as aulas e as peças: o que a pessoa escolhe à mão.

Pedido do usuário (out/2026): "uma janela pra poder escolher formas de resumo manualmente pra decidir cores ou formato",
no painel do PC (janela [ estudos ]) e no app do celular (tela Estudos), e "adicionar fotos de como quero o resumo".

- **Opções** (`OPCOES`): a IA que escreve, o tamanho, o estilo, o foco (OAB, concurso...), quantos flashcards e questões,
  as partes que entram na ficha, o formato (PDF, Word ou os dois), o tema de cores, a letra, o marca-texto e instruções
  livres. Valem para os próximos pedidos; o que o próprio pedido disser ("em Word", "com 20 flashcards", "pelo Gemini")
  vale mais. As peças processuais têm as suas (`pecas`: modo prova da OAB ou caso real, o advogado e a cidade).
- **Fotos de exemplo** (`PASTA_EXEMPLOS_ESTUDO`, fora do git): até `MAXIMO_DE_FOTOS` imagens de resumos de que a pessoa
  gosta. `extrair_guia` manda as fotos **só ao Claude** (escolha do usuário; nunca ao Gemini) e, sem ele, ao modelo de
  visão do PC; sai um guia de estilo (estrutura, visual, texto, cores), editável na janela, que entra no prompt das fichas
  e das aulas e, com o tema "do guia", nas cores do PDF e do Word.

Arquivo: `CAMINHO_PREFERENCIAS_ESTUDO` (relido quando muda: o painel grava e o processo principal lê), gravado de uma
vez (temporário + os.replace). Arquivo quebrado = o padrão.
"""
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import threading
import time

logger = logging.getLogger(__name__)

# Cada opção: (valor, rótulo, explicação). A janela e o app montam os botões daqui.
OPCOES = {
    "ia": [
        ("auto", "Automático", "O Claude escreve; sem ele, o Gemini (só quando não há arquivos seus) e por último o PC."),
        ("claude", "Claude", "O Claude primeiro, sempre que puder."),
        ("gemini", "Gemini", "O Gemini primeiro. Com arquivos seus no material, eles continuam indo só para o Claude."),
        ("local", "Só no PC", "Nada sai do computador; o material fica mais simples."),
    ],
    "tamanho": [
        ("enxuto", "Enxuto", "Menos tópicos e explicação curta: para revisar."),
        ("normal", "Normal", "O de sempre."),
        ("completo", "Completo", "Mais tópicos e explicações longas: para aprender do zero."),
    ],
    "estilo": [
        ("tatico", "Ficha de prova", "Como cai, regra numa linha, exceções, lei seca, pegadinhas."),
        ("didatico", "Didático", "Explica do zero, com mais exemplos e comparações do dia a dia."),
        ("esquematico", "Esquemático", "Frases curtas, tópicos e tabelas; pouco texto corrido."),
        ("revisao", "Revisão rápida", "Só o essencial: regra, exceções, prazos e palavras-chave."),
    ],
    "foco": [
        ("oab", "OAB", "Questões no estilo da FGV, 1ª e 2ª fase."),
        ("concurso", "Concurso", "Estilo das bancas de concurso (diga qual em 'banca')."),
        ("faculdade", "Faculdade", "Prova da faculdade: conceitos, doutrina e casos."),
        ("geral", "Geral", "Sem uma prova em vista: entender o assunto."),
    ],
    "formato": [
        ("pdf", "PDF", "Pronto para ler e imprimir."),
        ("docx", "Word", "Para editar depois."),
        ("ambos", "PDF e Word", "Os dois arquivos."),
    ],
    "cores": [
        ("padrao", "Roxo e dourado", "O padrão da Beatriz."),
        ("preto_dourado", "Preto e dourado", "Como o painel."),
        ("azul", "Azul-marinho", "Sóbrio."),
        ("verde", "Verde", "Tons de verde."),
        ("vinho", "Vinho", "Vinho e âmbar."),
        ("cinza", "Econômico", "Cinza, para imprimir gastando pouca tinta."),
        ("livre", "Minhas cores", "Você escolhe a cor do título, a dos destaques e a de cada caixa."),
        ("guia", "Do guia das fotos", "As cores que a IA tirou das suas fotos de exemplo."),
    ],
    "letra": [("normal", "Normal", ""), ("grande", "Grande", "Mais fácil de ler e imprimir.")],
}
PARTES = [
    ("banca", "Como a banca cobra", "A análise de como o assunto cai e o \"como cai\" de cada tópico."),
    ("mapa", "Mapa de incidência", "A tabela dos tópicos com o peso de cada um."),
    ("lei_seca", "Lei seca", "Os artigos com a palavra-chave e o texto oficial conferido."),
    ("jurisprudencia", "STF e STJ", "Súmulas e teses, conferidas no vade-mécum."),
    ("pegadinhas", "Pegadinhas", "As confusões que a banca explora."),
    ("exemplo", "Exemplo", "Um caso concreto por tópico."),
    ("prazos", "Quadro de prazos", "Prazos e efeitos de cada tópico e o quadro geral."),
    ("glossario", "Glossário", "Os termos técnicos."),
    ("como_estudar", "Como estudar", "O passo a passo de estudo no fim."),
]
# As caixas da ficha que ganham cor própria em "Minhas cores" (a chave é a de ficha_estudo.CORES).
CAIXAS = [
    ("banca", "Como cai"), ("natureza", "Requisitos"), ("regra", "Regra"), ("excecoes", "Exceções"), ("prazos", "Prazos"),
    ("lei_seca", "Lei seca"), ("jurisprudencia", "STF/STJ"), ("pegadinha", "Pegadinhas"), ("exemplo", "Exemplo"),
    ("estresse", "Teste de estresse"),
]
FLASHCARDS = [None, 0, 5, 10, 12, 15, 20, 30, 40, 60]  # None = automático (12; 20 no resumo da matéria)
QUESTOES = [None, 0, 3, 5, 6, 10, 15, 20, 30]  # None = automático (6; 10 no resumo da matéria)
MODOS_DE_PECA = [
    ("oab", "Prova da OAB", "Sem identificar ninguém: \"Nome…\", \"Advogado…, OAB…\", como pede o exame."),
    ("real", "Caso real", "Com os dados que você der; o que faltar fica entre colchetes para completar."),
]

PADRAO = {
    "ia": "auto", "tamanho": "normal", "estilo": "tatico", "foco": "oab", "banca": "", "flashcards": None,
    "questoes": None, "partes": {chave: True for chave, _, _ in PARTES}, "formato": "pdf", "cores": "padrao",
    "letra": "normal", "marca_texto": True, "instrucoes": "", "usar_guia": True,
    "cores_livres": {"marca": "#2E1A47", "ouro": "#C99A1E", "caixas": {}},
    "guia": {"texto": "", "cores": {}, "quem": "", "fotos": 0, "atualizado": None},
    "pecas": {"modo": "oab", "advogado": "", "oab": "", "cidade": "", "formato": "ambos", "instrucoes": ""},
}
LIMITE_DAS_INSTRUCOES = 2000
LIMITE_DO_GUIA = 4000
MAXIMO_DE_FOTOS = 8
LIMITE_DA_FOTO_MB = 12
EXTENSOES_DE_FOTO = (".png", ".jpg", ".jpeg", ".webp")
RE_COR = re.compile(r"^#[0-9a-fA-F]{6}$")

# Os temas mudam o título, as linhas e (no econômico) as caixas; cada parte da ficha continua com o rótulo escrito.
TEMAS = {
    "padrao": {"marca": "#2E1A47", "ouro": "#C99A1E"},
    "preto_dourado": {"marca": "#111111", "ouro": "#C99A1E"},
    "azul": {"marca": "#14305A", "ouro": "#2563EB"},
    "verde": {"marca": "#14532D", "ouro": "#65A30D"},
    "vinho": {"marca": "#5B1A2E", "ouro": "#D97706"},
    "cinza": {"marca": "#222222", "ouro": "#666666", "caixas": ("#444444", "#F3F3F3"), "destaque": "#D9D9D9"},
}

_TRAVA = threading.Lock()
_GUARDADO = {"caminho": None, "marca": None, "dados": None}


class ErroDasPreferencias(ValueError):
    """Mensagem para a janela: o que está errado no que foi escolhido."""


def caminho() -> str:
    import config

    return config.CAMINHO_PREFERENCIAS_ESTUDO


def pasta_das_fotos() -> str:
    import config

    return config.PASTA_EXEMPLOS_ESTUDO


def _marca(arquivo: str):
    try:
        estado = os.stat(arquivo)
        return estado.st_mtime_ns, estado.st_size
    except OSError:
        return None


def _juntar(lido: dict) -> dict:
    """O arquivo por cima do padrão, só com valores válidos (um campo estranho fica como o padrão)."""
    dados = json.loads(json.dumps(PADRAO))
    try:
        dados.update(validar({**dados, **{k: v for k, v in (lido or {}).items() if k in PADRAO and k != "guia"}}))
    except ErroDasPreferencias as exc:
        logger.warning("Preferências de estudo com valor estranho (%s); ficam as do padrão.", exc)
    guia = lido.get("guia") if isinstance(lido, dict) else None
    if isinstance(guia, dict):
        dados["guia"] = _guia_valido(guia)
    dados["atualizado"] = lido.get("atualizado") if isinstance(lido, dict) else None
    return dados


def atual() -> dict:
    """As preferências de agora (o arquivo, relido só quando muda; sem ele, o padrão)."""
    arquivo = caminho()
    marca = _marca(arquivo)
    with _TRAVA:
        if _GUARDADO["caminho"] == arquivo and _GUARDADO["marca"] == marca and _GUARDADO["dados"] is not None:
            return json.loads(json.dumps(_GUARDADO["dados"]))
    dados = {**json.loads(json.dumps(PADRAO)), "atualizado": None}
    if marca is not None:
        try:
            with open(arquivo, encoding="utf-8") as entrada:
                dados = _juntar(json.load(entrada))
        except (OSError, ValueError) as exc:
            logger.warning("Preferências de estudo ilegíveis em %s (%s); fica o padrão.", arquivo, exc)
    with _TRAVA:
        _GUARDADO.update(caminho=arquivo, marca=marca, dados=dados)
    return json.loads(json.dumps(dados))


def _valores(chave: str) -> list:
    return [valor for valor, _, _ in OPCOES[chave]]


def _quantidade(valor, permitidos: list, nome: str):
    if valor in (None, "", "auto"):
        return None
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        raise ErroDasPreferencias(f"{nome}: escolha um número da lista.") from None
    if numero not in permitidos:
        raise ErroDasPreferencias(f"{nome}: escolha um número da lista.")
    return numero


def _texto(valor, limite: int, nome: str) -> str:
    texto = str(valor or "").strip()
    if len(texto) > limite:
        raise ErroDasPreferencias(f"{nome} passou de {limite} caracteres.")
    return texto


def validar(dados: dict) -> dict:
    """As preferências conferidas (sem o guia, que tem as próprias regras); levanta ErroDasPreferencias."""
    if not isinstance(dados, dict):
        raise ErroDasPreferencias("Preferências em formato estranho.")
    limpo = {}
    for chave in ("ia", "tamanho", "estilo", "foco", "formato", "cores", "letra"):
        valor = dados.get(chave, PADRAO[chave])
        if valor not in _valores(chave):
            raise ErroDasPreferencias(f"Opção desconhecida em {chave}: {valor}.")
        limpo[chave] = valor
    limpo["banca"] = _texto(dados.get("banca"), 60, "A banca")
    limpo["flashcards"] = _quantidade(dados.get("flashcards"), [n for n in FLASHCARDS if n is not None], "Flashcards")
    limpo["questoes"] = _quantidade(dados.get("questoes"), [n for n in QUESTOES if n is not None], "Questões")
    partes = dados.get("partes") or {}
    if not isinstance(partes, dict):
        raise ErroDasPreferencias("Partes em formato estranho.")
    limpo["partes"] = {chave: bool(partes.get(chave, True)) for chave, _, _ in PARTES}
    livres = dados.get("cores_livres") or {}
    if not isinstance(livres, dict) or not isinstance(livres.get("caixas") or {}, dict):
        raise ErroDasPreferencias("Cores em formato estranho.")
    cor = lambda valor, padrao: valor if isinstance(valor, str) and RE_COR.match(valor) else padrao
    limpo["cores_livres"] = {
        "marca": cor(livres.get("marca"), PADRAO["cores_livres"]["marca"]),
        "ouro": cor(livres.get("ouro"), PADRAO["cores_livres"]["ouro"]),
        "caixas": {chave: valor for chave, valor in (livres.get("caixas") or {}).items()
                   if chave in dict(CAIXAS) and isinstance(valor, str) and RE_COR.match(valor)},
    }
    limpo["marca_texto"] = bool(dados.get("marca_texto", True))
    limpo["usar_guia"] = bool(dados.get("usar_guia", True))
    limpo["instrucoes"] = _texto(dados.get("instrucoes"), LIMITE_DAS_INSTRUCOES, "As instruções")
    pecas = dados.get("pecas") or {}
    if not isinstance(pecas, dict):
        raise ErroDasPreferencias("Peças em formato estranho.")
    modo = pecas.get("modo", "oab")
    if modo not in [m for m, _, _ in MODOS_DE_PECA]:
        raise ErroDasPreferencias(f"Modo de peça desconhecido: {modo}.")
    formato_da_peca = pecas.get("formato", "ambos")
    if formato_da_peca not in _valores("formato"):
        raise ErroDasPreferencias(f"Formato de peça desconhecido: {formato_da_peca}.")
    limpo["pecas"] = {
        "modo": modo, "formato": formato_da_peca,
        "advogado": _texto(pecas.get("advogado"), 120, "O nome do advogado"),
        "oab": _texto(pecas.get("oab"), 40, "O número da OAB"),
        "cidade": _texto(pecas.get("cidade"), 80, "A cidade"),
        "instrucoes": _texto(pecas.get("instrucoes"), LIMITE_DAS_INSTRUCOES, "As instruções das peças"),
    }
    return limpo


def _guia_valido(guia: dict) -> dict:
    cores = {k: v for k, v in (guia.get("cores") or {}).items() if k in ("principal", "destaque", "caixas") and
             isinstance(v, str) and RE_COR.match(v)}
    return {"texto": str(guia.get("texto") or "")[:LIMITE_DO_GUIA], "cores": cores, "quem": str(guia.get("quem") or ""),
            "fotos": int(guia.get("fotos") or 0), "atualizado": guia.get("atualizado")}


def _gravar(dados: dict) -> dict:
    arquivo = caminho()
    os.makedirs(os.path.dirname(arquivo) or ".", exist_ok=True)
    temporario = arquivo + ".tmp"
    with open(temporario, "w", encoding="utf-8") as saida:
        json.dump({**dados, "atualizado": time.time()}, saida, ensure_ascii=False, indent=1)
    os.replace(temporario, arquivo)
    return atual()


def salvar(dados: dict) -> dict:
    """Confere e grava as opções e, com "guia_texto", o guia editado à mão (as cores tiradas das fotos continuam)."""
    dados = dict(dados or {})
    texto_do_guia = dados.pop("guia_texto", None)
    limpo = validar({**atual(), **dados})
    guia = atual()["guia"]
    if texto_do_guia is not None:
        guia["texto"] = _texto(texto_do_guia, LIMITE_DO_GUIA, "O guia")
    return _gravar({**limpo, "guia": guia})


def salvar_guia(texto: str) -> dict:
    """O guia editado à mão na janela (as cores tiradas das fotos continuam)."""
    guia = atual()["guia"]
    guia["texto"] = _texto(texto, LIMITE_DO_GUIA, "O guia")
    return _gravar({**atual(), "guia": guia})


def restaurar_padrao() -> dict:
    """As opções voltam ao padrão; o guia e as fotos ficam (têm os botões deles)."""
    return _gravar({**json.loads(json.dumps(PADRAO)), "guia": atual()["guia"]})


# ------------------------------------------------------------------ o que vai para os agentes
def visual(prefs: dict = None) -> dict:
    """Para o PDF e o Word da ficha: as cores do tema, a letra, as partes e o marca-texto."""
    prefs = prefs or atual()
    return {"cores": paleta(prefs), "letra": prefs.get("letra", "normal"), "partes": dict(prefs.get("partes") or {}),
            "marca_texto": prefs.get("marca_texto", True)}


def paleta(prefs: dict = None) -> dict:
    """As cores da ficha (no formato de `ficha_estudo.CORES`) para o tema escolhido."""
    from agentes.ficha_estudo import CORES

    prefs = prefs or atual()
    cores = dict(CORES)
    tema = prefs.get("cores", "padrao")
    if tema == "livre":
        livres = prefs.get("cores_livres") or PADRAO["cores_livres"]
        cores["marca"], cores["ouro"] = livres.get("marca", CORES["marca"]), livres.get("ouro", CORES["ouro"])
        for chave, borda in (livres.get("caixas") or {}).items():
            if chave in cores:
                cores[chave] = (borda, clarear(borda))
        return cores
    if tema == "guia":
        do_guia = (prefs.get("guia") or {}).get("cores") or {}
        escolha = {"marca": do_guia.get("principal") or TEMAS["padrao"]["marca"],
                   "ouro": do_guia.get("destaque") or TEMAS["padrao"]["ouro"]}
    else:
        escolha = TEMAS.get(tema, TEMAS["padrao"])
    cores["marca"], cores["ouro"] = escolha["marca"], escolha["ouro"]
    if escolha.get("caixas"):
        cores.update({k: escolha["caixas"] for k, v in CORES.items() if isinstance(v, tuple)})
    if escolha.get("destaque"):
        cores["destaque"] = escolha["destaque"]
    return cores


def clarear(cor: str, quanto: float = 0.92) -> str:
    """O fundo de uma caixa a partir da cor da borda: a mesma cor quase branca."""
    r, g, b = (int(cor[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(c + (255 - c) * quanto):02X}" for c in (r, g, b))


TAMANHOS = {
    "enxuto": "ENXUTO: menos tópicos (só os que mais caem); 'explicacao' com 1 parágrafo curto; listas com até 4 itens.",
    "normal": "",
    "completo": ("COMPLETO: cubra o assunto inteiro, sem pular institutos menores; 'explicacao' com 4 a 6 parágrafos, "
                 "com o porquê, exemplos e as divergências; listas completas."),
}
ESTILOS = {
    "tatico": "",
    "didatico": ("DIDÁTICO: ensine como a um aluno que nunca viu o assunto: comece pelo problema que o instituto resolve, "
                 "use comparações do dia a dia e um exemplo resolvido em cada tópico; termos técnicos explicados na "
                 "primeira vez."),
    "esquematico": ("ESQUEMÁTICO: frases curtas e diretas, em tópicos; 'explicacao' em parágrafos de no máximo 3 frases; "
                    "prefira listas e quadros a texto corrido."),
    "revisao": ("REVISÃO RÁPIDA: só o essencial para a véspera: a regra, as exceções, os prazos e as palavras-chave da lei "
                "seca; 'explicacao' com 1 parágrafo curto; nada de histórico."),
}
FOCOS = {
    "oab": "a prova da OAB (FGV)",
    "concurso": "provas de concurso",
    "faculdade": "a prova da faculdade (conceitos, doutrina e casos)",
    "geral": "entender o assunto, sem uma prova específica",
}
CAMPOS_DAS_PARTES = {
    "banca": ["como_cai"], "lei_seca": ["lei_seca"], "jurisprudencia": ["jurisprudencia"], "pegadinhas": ["pegadinhas"],
    "exemplo": ["exemplo"], "prazos": ["prazos_e_efeitos"],
}


def guia_em_uso(prefs: dict = None) -> str:
    prefs = prefs or atual()
    return (prefs.get("guia") or {}).get("texto", "").strip() if prefs.get("usar_guia", True) else ""


def bloco_do_prompt(prefs: dict = None, para: str = "ficha") -> str:
    """As escolhas da pessoa, para o fim do prompt de sistema das fichas ("ficha") e das aulas ("aulas"); vazio no padrão."""
    prefs = prefs or atual()
    linhas = []
    for texto in (TAMANHOS.get(prefs.get("tamanho"), ""), ESTILOS.get(prefs.get("estilo"), "")):
        if texto:
            linhas.append(f"- {texto}")
    foco = FOCOS.get(prefs.get("foco"), "")
    if prefs.get("foco") != "oab" or prefs.get("banca"):
        banca = f" (banca: {prefs['banca']})" if prefs.get("banca") else ""
        linhas.append(f"- FOCO: {foco}{banca}; as questões seguem esse estilo.")
    if para == "ficha":
        desligadas = [campo for parte, campos in CAMPOS_DAS_PARTES.items() if not (prefs.get("partes") or {}).get(parte, True)
                      for campo in campos]
        if desligadas:
            linhas.append(f"- Deixe vazios os campos {', '.join(desligadas)}: o usuário tirou essas partes da ficha.")
    if prefs.get("instrucoes"):
        linhas.append(f"- Instruções do usuário: {prefs['instrucoes']}")
    guia = guia_em_uso(prefs)
    if not linhas and not guia:
        return ""
    bloco = ""
    if linhas:
        bloco += ("\n\nPREFERÊNCIAS DO USUÁRIO PARA ESTE MATERIAL (escolhidas por ele na janela de estudos; quando "
                  "conflitarem com as regras acima, valem estas):\n" + "\n".join(linhas))
    if guia:
        bloco += ("\n\nGUIA DE ESTILO DO USUÁRIO (tirado das fotos de resumos de que ele gosta; siga na organização, no "
                  f"tamanho das frases e no tom, dentro do formato JSON pedido):\n{guia}")
    return bloco


def descricao_curta(prefs: dict = None) -> str:
    """'enxuto, esquemático, em Word, cores azul-marinho' (só o que mudou do padrão), para a mensagem de início."""
    prefs = prefs or atual()
    rotulos = {chave: {valor: rotulo for valor, rotulo, _ in OPCOES[chave]} for chave in OPCOES}
    partes = []
    for chave in ("tamanho", "estilo"):
        if prefs.get(chave) != PADRAO[chave]:
            partes.append(rotulos[chave][prefs[chave]].lower())
    if prefs.get("cores") != PADRAO["cores"]:
        partes.append(f"cores {rotulos['cores'][prefs['cores']].lower()}")
    if guia_em_uso(prefs):
        partes.append("o guia das suas fotos")
    return ", ".join(partes)


def resumo_para_o_critico(prefs: dict = None) -> str:
    """O que o crítico precisa saber para não reprovar o que foi pedido de propósito (ficha enxuta, partes tiradas)."""
    prefs = prefs or atual()
    bloco = bloco_do_prompt(prefs)
    return f"O usuário escolheu este formato na janela de estudos; não é problema:{bloco}\n\n" if bloco else ""


def faixa_de_topicos(prefs: dict, materia: bool) -> tuple:
    """(mínimo, máximo) de tópicos da ficha pelo tamanho escolhido."""
    tamanho = (prefs or {}).get("tamanho", "normal")
    if materia:
        return {"enxuto": (4, 8), "normal": (6, 16), "completo": (10, 20)}.get(tamanho, (6, 16))
    return {"enxuto": (2, 4), "normal": (2, 8), "completo": (4, 12)}.get(tamanho, (2, 8))


# ------------------------------------------------------------------ fotos de exemplo
def listar_fotos() -> list:
    pasta = pasta_das_fotos()
    try:
        nomes = sorted(n for n in os.listdir(pasta) if n.lower().endswith(EXTENSOES_DE_FOTO))
    except OSError:
        return []
    fotos = []
    for nome in nomes:
        caminho_foto = os.path.join(pasta, nome)
        try:
            estado = os.stat(caminho_foto)
        except OSError:
            continue
        fotos.append({"id": nome, "nome": nome.split("_", 2)[-1], "tamanho": estado.st_size, "quando": estado.st_mtime})
    return fotos


def caminho_da_foto(foto_id: str) -> str:
    """O caminho de uma foto pelo id (o nome do arquivo); nunca sai da pasta das fotos."""
    nome = os.path.basename(str(foto_id or ""))
    if not nome or nome != foto_id or not nome.lower().endswith(EXTENSOES_DE_FOTO):
        raise ErroDasPreferencias("Foto não encontrada.")
    caminho_foto = os.path.join(pasta_das_fotos(), nome)
    if not os.path.isfile(caminho_foto):
        raise ErroDasPreferencias("Foto não encontrada.")
    return caminho_foto


def _parece_imagem(dados: bytes) -> bool:
    return dados.startswith((b"\x89PNG", b"\xff\xd8\xff")) or (dados[:4] == b"RIFF" and dados[8:12] == b"WEBP")


def adicionar_foto(nome: str, dados: bytes = None, origem: str = None) -> dict:
    """Guarda uma foto de exemplo (bytes, do app, ou um arquivo do PC, da janela). Só PNG, JPG e WebP."""
    if len(listar_fotos()) >= MAXIMO_DE_FOTOS:
        raise ErroDasPreferencias(f"Já há {MAXIMO_DE_FOTOS} fotos: apague uma antes.")
    if dados is None:
        with open(origem, "rb") as entrada:
            dados = entrada.read(LIMITE_DA_FOTO_MB * 1024 * 1024 + 1)
    if len(dados) > LIMITE_DA_FOTO_MB * 1024 * 1024:
        raise ErroDasPreferencias(f"A foto passa de {LIMITE_DA_FOTO_MB} MB.")
    if not _parece_imagem(dados):
        raise ErroDasPreferencias("Só fotos em PNG, JPG ou WebP.")
    base, extensao = os.path.splitext(os.path.basename(nome or "foto.jpg"))
    extensao = extensao.lower() if extensao.lower() in EXTENSOES_DE_FOTO else ".jpg"
    base = re.sub(r"[^\w\- ]+", "", base, flags=re.UNICODE).strip()[:40] or "foto"
    os.makedirs(pasta_das_fotos(), exist_ok=True)
    foto_id = f"{time.strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(2)}_{base}{extensao}"
    with open(os.path.join(pasta_das_fotos(), foto_id), "wb") as saida:
        saida.write(dados)
    return {"id": foto_id}


def miniatura(caminho_foto: str, lado: int = 360) -> str:
    """A foto reduzida, em JPEG, como data URL: a janela e o app a mostram sem acesso ao disco."""
    import base64
    import io

    from PIL import Image, ImageOps

    with Image.open(caminho_foto) as imagem:
        reduzida = ImageOps.exif_transpose(imagem).convert("RGB")
        reduzida.thumbnail((lado, lado))
        saida = io.BytesIO()
        reduzida.save(saida, "JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(saida.getvalue()).decode("ascii")


def apagar_foto(foto_id: str) -> dict:
    os.remove(caminho_da_foto(foto_id))
    return {"id": foto_id}


INSTRUCOES_DO_GUIA = (
    "Você é a designer de material de estudo da Beatriz. O usuário mandou fotos de resumos e fichas de que ele gosta. "
    "Olhe todas e descreva o ESTILO em comum, para outra IA reproduzir num resumo novo de outro assunto (não copie o "
    "conteúdo das fotos). Responda SOMENTE com um objeto JSON neste formato:\n"
    '{"resumo": str, "estrutura": [str], "visual": [str], "texto": [str], "evitar": [str], '
    '"cores": {"principal": "#RRGGBB", "destaque": "#RRGGBB"}}\n'
    "'resumo': o estilo numa frase; 'estrutura': como o conteúdo se organiza (ordem das partes, títulos, numeração, "
    "esquemas, perguntas e respostas...); 'visual': caixas, tabelas, colunas, setas, cores por tipo de informação, "
    "destaques; 'texto': tamanho das frases, tom, abreviações, negrito; 'evitar': o que não aparece nas fotos e não deve "
    "aparecer; 'cores': as duas cores que mais marcam o material (a dos títulos e a dos destaques), em hexadecimal, ou "
    "{} se forem fotos em preto e branco. De 3 a 6 itens por lista, frases curtas e concretas."
)


def texto_do_guia(dados: dict) -> str:
    """O guia em linhas curtas (é o que a janela mostra para editar e o que vai para o prompt)."""
    linhas = []
    if dados.get("resumo"):
        linhas.append(str(dados["resumo"]).strip())
    for chave, rotulo in (("estrutura", "Estrutura"), ("visual", "Visual"), ("texto", "Texto"), ("evitar", "Evitar")):
        itens = [str(i).strip() for i in dados.get(chave) or [] if str(i).strip()]
        if itens:
            linhas.append(f"{rotulo}:\n" + "\n".join(f"- {i}" for i in itens))
    return "\n".join(linhas)[:LIMITE_DO_GUIA]


def _ver_no_pc(caminhos: list, prompt: str) -> str:
    """O modelo de visão do Ollama (qwen2.5vl), quando o Claude não pode; sai da placa logo depois."""
    from config import OLLAMA_VISION_MODEL
    from llm_clients import llm_clients

    try:
        resposta = llm_clients.ollama_client.chat(
            model=OLLAMA_VISION_MODEL, format="json", keep_alive=0, options={"num_ctx": 8192, "temperature": 0.2},
            messages=[{"role": "system", "content": INSTRUCOES_DO_GUIA},
                      {"role": "user", "content": prompt, "images": list(caminhos[:4])}],
        )
        return resposta["message"]["content"]
    finally:
        # Como na leitura de tela: o modelo de conversa volta para a placa em segundo plano.
        threading.Thread(target=getattr(llm_clients, "aquecer", lambda: None), daemon=True).start()


def extrair_guia(ver_com_claude=None, ver_no_pc=None) -> dict:
    """Manda as fotos ao Claude (só a ele, nunca ao Gemini) e, sem ele, ao modelo de visão do PC; grava o guia.

    `ver_com_claude(caminhos, prompt, sistema)` e `ver_no_pc(caminhos, prompt)` devolvem o texto em JSON (trocáveis nos
    testes). Levanta ErroDasPreferencias sem fotos ou se nenhuma das duas conseguir.
    """
    from agentes.base import extrair_json

    fotos = [caminho_da_foto(f["id"]) for f in listar_fotos()]
    if not fotos:
        raise ErroDasPreferencias("Adicione pelo menos uma foto de exemplo.")
    if ver_com_claude is None:
        from llm_nuvem import claude

        ver_com_claude = claude.ver_imagens if claude.disponivel() else None
    prompt = f"São {len(fotos)} foto(s) de exemplo. Descreva o estilo em comum delas."
    quem, resposta = "", ""
    if ver_com_claude is not None:
        try:
            resposta, quem = ver_com_claude(fotos, prompt, INSTRUCOES_DO_GUIA), "Claude"
        except Exception as exc:
            logger.warning("O Claude não olhou as fotos de exemplo: %s", exc)
    if not quem:
        try:
            resposta, quem = (ver_no_pc or _ver_no_pc)(fotos, prompt), "modelo de visão do PC"
        except Exception as exc:
            logger.warning("O modelo de visão do PC não olhou as fotos de exemplo: %s", exc)
            raise ErroDasPreferencias("Nem o Claude nem o modelo de visão do PC conseguiram olhar as fotos agora.") from exc
    try:
        dados = extrair_json(resposta)
    except ValueError as exc:
        raise ErroDasPreferencias(f"O {quem} respondeu num formato que não entendi; tente de novo.") from exc
    texto = texto_do_guia(dados)
    if not texto:
        raise ErroDasPreferencias(f"O {quem} não descreveu o estilo das fotos; tente outras fotos.")
    cores = {k: v for k, v in (dados.get("cores") or {}).items() if isinstance(v, str) and RE_COR.match(v.strip())}
    guia = _guia_valido({"texto": texto, "cores": cores, "quem": quem, "fotos": len(fotos), "atualizado": time.time()})
    _gravar({**atual(), "guia": guia})
    return guia


def copiar_fotos_para(pasta: str, caminhos: list) -> list:
    """Cópias com nomes simples (exemplo_1.jpg...) numa pasta só delas: é a única que o Claude pode ler."""
    copias = []
    for numero, origem in enumerate(caminhos, start=1):
        destino = os.path.join(pasta, f"exemplo_{numero}{os.path.splitext(origem)[1].lower()}")
        shutil.copyfile(origem, destino)
        copias.append(os.path.basename(destino))
    return copias


def pasta_temporaria() -> str:
    return tempfile.mkdtemp(prefix="beatriz_fotos_")


# ------------------------------------------------------------------ janela
def para_a_janela() -> dict:
    """Tudo o que a janela e o app desenham: as escolhas, as opções com os rótulos, as fotos e o guia."""
    prefs = atual()
    return {
        "preferencias": prefs,
        "opcoes": {chave: [{"valor": v, "rotulo": r, "explica": e} for v, r, e in lista] for chave, lista in OPCOES.items()},
        "partes": [{"chave": c, "rotulo": r, "explica": e} for c, r, e in PARTES],
        "caixas": [{"chave": c, "rotulo": r} for c, r in CAIXAS],
        "flashcards": FLASHCARDS, "questoes": QUESTOES,
        "modos_de_peca": [{"valor": v, "rotulo": r, "explica": e} for v, r, e in MODOS_DE_PECA],
        "fotos": listar_fotos(), "maximo_de_fotos": MAXIMO_DE_FOTOS,
        # As cores de cada tema (com as do guia de agora), para a prévia da folha mudar na hora.
        "paletas": {valor: paleta({**prefs, "cores": valor}) for valor, _, _ in OPCOES["cores"]},
        "tipos_de_peca": _tipos_de_peca(),
    }


def _tipos_de_peca() -> list:
    try:
        from agentes.pecas import TIPOS_DE_PECA
    except ImportError:
        return []
    return [{"valor": chave, "rotulo": tipo["nome"], "area": tipo["area"]} for chave, tipo in TIPOS_DE_PECA.items()]


def resultado(funcao, *argumentos, **nomeados) -> dict:
    """{"ok", "mensagem", "dados"} para a janela e o app, sem deixar escapar exceção."""
    try:
        funcao(*argumentos, **nomeados)
        return {"ok": True, "mensagem": "", "dados": para_a_janela()}
    except ErroDasPreferencias as exc:
        return {"ok": False, "mensagem": str(exc), "dados": None}
    except (OSError, ValueError) as exc:
        logger.warning("Preferências de estudo: %s", exc)
        return {"ok": False, "mensagem": f"Não consegui gravar: {exc}", "dados": None}
