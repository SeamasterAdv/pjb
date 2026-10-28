"""O estilo dos vídeos: o que a pessoa monta à mão para os vídeos animados pelo Claude e para a edição dos vídeos dela.

Pedido do usuário (out/2026): "a mesma função e opções e edição no meu estilo pra vídeos, deixando eu criar um estilo meu",
no painel do PC (janela [ vídeos ]) e no app do celular (tela Estúdio), como a janela de estudos.

- **Opções** (`OPCOES`): formato (deitado, em pé, quadrado), duração, ritmo, tom do texto, letra, como o texto entra, a
  transição entre as cenas, o movimento das ilustrações, a narração (voz da Thalita, a parecida com a Siri ou nenhuma), as
  legendas e o tamanho delas, as cores (temas, as suas ou as do guia), a tela de abertura, a assinatura do fim e instruções
  livres. O que o pedido disser ("em pé", "sem narração", "de 30 segundos") vale mais.
- **Os seus vídeos** (`edicao`): o filtro de cor (do FilmCraft), o enquadramento no formato do estilo e as legendas
  automáticas (Whisper, no PC), para "edita esse vídeo no meu estilo".
- **Exemplos** (`PASTA_EXEMPLOS_VIDEO`, fora do git): prints e vídeos de que a pessoa gosta (de um vídeo ficam 3 quadros).
  `extrair_guia` manda as imagens **só ao Claude** (como nos estudos) e, sem ele, ao modelo de visão do PC; sai um guia
  editável que entra no prompt dos vídeos e, com o tema "do guia", nas cores.

Arquivo: `CAMINHO_ESTILO_VIDEO` (relido quando muda: o painel grava e o processo principal lê), gravado de uma vez.
"""
import json
import logging
import os
import re
import secrets
import threading
import time

from preferencias_estudo import _marca, _parece_imagem, miniatura  # noqa: F401 (miniatura: a janela e o app usam por aqui)

logger = logging.getLogger(__name__)

OPCOES = {
    "formato": [
        ("16x9", "Deitado 16:9", "YouTube, TV e computador."),
        ("9x16", "Em pé 9:16", "Reels, TikTok, Shorts e status do WhatsApp."),
        ("1x1", "Quadrado", "Feed do Instagram."),
    ],
    "duracao": [
        ("curto", "Curto", "Uns 20 segundos, de 3 a 5 cenas."),
        ("medio", "Médio", "Uns 45 segundos, de 6 a 8 cenas."),
        ("longo", "Longo", "Um minuto e meio, de 10 a 14 cenas."),
    ],
    "ritmo": [
        ("calmo", "Calmo", "Cenas longas e transições lentas."),
        ("normal", "Normal", "O de sempre."),
        ("rapido", "Rápido", "Cenas curtas e cortes ligeiros, estilo Reels."),
    ],
    "tom": [
        ("didatico", "Didático", "Explica passo a passo, com exemplos."),
        ("divertido", "Divertido", "Leve, com humor e comparações engraçadas."),
        ("inspirador", "Inspirador", "Frases de impacto, emoção, chamada no fim."),
        ("serio", "Sério", "Direto, sóbrio, como um telejornal."),
    ],
    "letra": [
        ("moderna", "Moderna", "Segoe UI: limpa e atual."),
        ("elegante", "Elegante", "Georgia, com serifa."),
        ("tecnica", "Técnica", "Consolas, de terminal."),
        ("condensada", "Condensada", "Bahnschrift: estreita e firme."),
        ("manuscrita", "Manuscrita", "Segoe Print, como escrita à mão."),
        ("impacto", "Impacto", "Títulos em Impact, bem grossos."),
    ],
    "entrada": [
        ("surgir", "Surgir", "O texto aparece subindo de leve."),
        ("deslizar", "Deslizar", "Entra pela esquerda."),
        ("digitar", "Digitar", "Letra por letra, como digitado."),
        ("palavra", "Palavra por palavra", "Uma palavra de cada vez."),
        ("pop", "Pop", "Cresce com um pulinho."),
    ],
    "transicao": [
        ("corte", "Corte seco", "Uma cena atrás da outra."),
        ("dissolver", "Dissolver", "Uma cena se mistura na outra."),
        ("deslizar", "Deslizar", "A cena nova empurra a velha para o lado."),
        ("zoom", "Zoom", "A cena nova chega de perto."),
        ("cortina", "Cortina", "A cena nova passa por cima, da esquerda para a direita."),
    ],
    "movimento": [
        ("parado", "Parado", "As ilustrações ficam paradas."),
        ("aproximar", "Aproximar", "Um zoom lento em cada cena."),
        ("passear", "Passear", "A ilustração anda devagar de um lado para o outro."),
    ],
    "narracao": [
        ("thalita", "Voz da Thalita", "A voz de sempre da Beatriz."),
        ("siri", "Parecida com a Siri", "A voz calma e neutra."),
        ("nenhuma", "Sem narração", "Só texto na tela (sem som)."),
    ],
    "legendas": [
        ("embaixo", "Embaixo", "Legenda da narração no pé do vídeo."),
        ("centro", "No meio", "Legenda grande no meio, estilo Reels."),
        ("nenhuma", "Sem legendas", ""),
    ],
    "tamanho_legenda": [("pequena", "Pequena", ""), ("media", "Média", ""), ("grande", "Grande", "")],
    "cores": [
        ("preto_dourado", "Preto e dourado", "Como o painel da Beatriz."),
        ("noite", "Noite", "Azul-escuro com destaque azul-claro."),
        ("branco", "Branco limpo", "Fundo claro, texto escuro, destaque vermelho."),
        ("neon", "Neon", "Roxo-escuro com rosa neon."),
        ("pastel", "Pastel", "Tons suaves de creme e rosa."),
        ("natureza", "Natureza", "Verde-escuro com amarelo."),
        ("livre", "Minhas cores", "Você escolhe o fundo, o texto e o destaque."),
        ("guia", "Do guia", "As cores que a IA tirou dos seus exemplos."),
    ],
}
# Os seus vídeos ("edita esse vídeo no meu estilo"): o filtro é um dos do FilmCraft (apps_craft.FILTROS com preset de vídeo).
OPCOES_DA_EDICAO = {
    "filtro": [("", "Nenhum", "As cores do vídeo como estão."), ("pb", "Preto e branco", ""), ("sepia", "Sépia", ""),
               ("vintage", "Antigo", ""), ("cinema", "Cinema", ""), ("quente", "Quente", ""), ("frio", "Frio", ""),
               ("dramatico", "Dramático", "")],
    "enquadrar": [
        ("original", "Como está", "Não muda o formato do vídeo."),
        ("preencher", "Preencher", "Corta as bordas para caber no formato do estilo."),
        ("encaixar", "Encaixar", "O vídeo inteiro no formato do estilo, com o fundo desfocado."),
    ],
}

# As cores de cada tema: fundo (em cima), fundo2 (embaixo, em degradê), texto e destaque.
TEMAS = {
    "preto_dourado": {"fundo": "#050505", "fundo2": "#1c1c1c", "texto": "#f2f2f2", "destaque": "#f5c04a"},
    "noite": {"fundo": "#0b1030", "fundo2": "#2a3a6a", "texto": "#ffffff", "destaque": "#7fd3ff"},
    "branco": {"fundo": "#fafafa", "fundo2": "#e6e6ec", "texto": "#141414", "destaque": "#e0452b"},
    "neon": {"fundo": "#0a0014", "fundo2": "#26004d", "texto": "#ffffff", "destaque": "#ff2bd6"},
    "pastel": {"fundo": "#fdf3e7", "fundo2": "#f6d6e3", "texto": "#3a2e3f", "destaque": "#7a5af5"},
    "natureza": {"fundo": "#0f2a1d", "fundo2": "#2f5d3a", "texto": "#f4f1e8", "destaque": "#e8c547"},
}
# A letra: (a do CSS, para a prévia; o arquivo do título; o do texto), em C:\Windows\Fonts.
LETRAS = {
    "moderna": ("'Segoe UI', sans-serif", "segoeuib.ttf", "segoeui.ttf"),
    "elegante": ("Georgia, serif", "georgiab.ttf", "georgia.ttf"),
    "tecnica": ("Consolas, monospace", "consolab.ttf", "consola.ttf"),
    "condensada": ("Bahnschrift, 'Arial Narrow', sans-serif", "bahnschrift.ttf", "bahnschrift.ttf"),
    "manuscrita": ("'Segoe Print', cursive", "segoeprb.ttf", "segoepr.ttf"),
    "impacto": ("Impact, sans-serif", "impact.ttf", "segoeui.ttf"),
}
PADRAO = {
    "formato": "16x9", "duracao": "medio", "ritmo": "normal", "tom": "didatico", "letra": "moderna", "entrada": "surgir",
    "transicao": "dissolver", "movimento": "aproximar", "narracao": "thalita", "legendas": "embaixo",
    "tamanho_legenda": "media", "cores": "preto_dourado",
    "cores_livres": {"fundo": "#101010", "fundo2": "#2b2b2b", "texto": "#ffffff", "destaque": "#f5c04a"},
    "abertura": True, "assinatura": "", "instrucoes": "", "usar_guia": True,
    "edicao": {"filtro": "", "enquadrar": "original", "legendas": True},
    "guia": {"texto": "", "cores": {}, "quem": "", "exemplos": 0, "atualizado": None},
}
CAMPOS_DAS_OPCOES = tuple(OPCOES)
LIMITE_DAS_INSTRUCOES = 2000
LIMITE_DO_GUIA = 4000
LIMITE_DA_ASSINATURA = 60
MAXIMO_DE_EXEMPLOS = 12  # imagens (de um vídeo ficam 3 quadros)
LIMITE_DA_IMAGEM_MB = 12
LIMITE_DO_VIDEO_MB = 300
EXTENSOES_DE_IMAGEM = (".png", ".jpg", ".jpeg", ".webp")
EXTENSOES_DE_VIDEO = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
QUADROS_POR_VIDEO = 3
RE_COR = re.compile(r"^#[0-9a-fA-F]{6}$")

_TRAVA = threading.Lock()
_GUARDADO = {"caminho": None, "marca": None, "dados": None}


class ErroDoEstilo(ValueError):
    """Mensagem para a janela: o que está errado no que foi escolhido."""


def caminho() -> str:
    import config

    return config.CAMINHO_ESTILO_VIDEO


def pasta_dos_exemplos() -> str:
    import config

    return config.PASTA_EXEMPLOS_VIDEO


def _copia(dados):
    return json.loads(json.dumps(dados))


def _juntar(lido: dict) -> dict:
    """O arquivo por cima do padrão, só com valores válidos (um campo estranho fica como o padrão)."""
    dados = _copia(PADRAO)
    lido = lido if isinstance(lido, dict) else {}
    try:
        dados.update(validar({**dados, **{k: v for k, v in lido.items() if k in PADRAO and k != "guia"}}))
    except ErroDoEstilo as exc:
        logger.warning("Estilo de vídeo com valor estranho (%s); fica o padrão.", exc)
    if isinstance(lido.get("guia"), dict):
        dados["guia"] = _guia_valido(lido["guia"])
    dados["atualizado"] = lido.get("atualizado")
    return dados


def atual() -> dict:
    """O estilo de agora (o arquivo, relido só quando muda; sem ele, o padrão)."""
    arquivo = caminho()
    marca = _marca(arquivo)
    with _TRAVA:
        if _GUARDADO["caminho"] == arquivo and _GUARDADO["marca"] == marca and _GUARDADO["dados"] is not None:
            return _copia(_GUARDADO["dados"])
    dados = {**_copia(PADRAO), "atualizado": None}
    if marca is not None:
        try:
            with open(arquivo, encoding="utf-8") as entrada:
                dados = _juntar(json.load(entrada))
        except (OSError, ValueError) as exc:
            logger.warning("Estilo de vídeo ilegível em %s (%s); fica o padrão.", arquivo, exc)
    with _TRAVA:
        _GUARDADO.update(caminho=arquivo, marca=marca, dados=dados)
    return _copia(dados)


def _texto(valor, limite: int, nome: str) -> str:
    texto = str(valor or "").strip()
    if len(texto) > limite:
        raise ErroDoEstilo(f"{nome} passou de {limite} caracteres.")
    return texto


def validar(dados: dict) -> dict:
    """O estilo conferido (sem o guia, que tem as próprias regras); levanta ErroDoEstilo."""
    if not isinstance(dados, dict):
        raise ErroDoEstilo("Estilo em formato estranho.")
    limpo = {}
    for chave in CAMPOS_DAS_OPCOES:
        valor = dados.get(chave, PADRAO[chave])
        if valor not in [v for v, _, _ in OPCOES[chave]]:
            raise ErroDoEstilo(f"Opção desconhecida em {chave}: {valor}.")
        limpo[chave] = valor
    livres = dados.get("cores_livres") or {}
    if not isinstance(livres, dict):
        raise ErroDoEstilo("Cores em formato estranho.")
    limpo["cores_livres"] = {chave: livres[chave] if isinstance(livres.get(chave), str) and RE_COR.match(livres[chave])
                             else padrao for chave, padrao in PADRAO["cores_livres"].items()}
    limpo["abertura"] = bool(dados.get("abertura", True))
    limpo["usar_guia"] = bool(dados.get("usar_guia", True))
    limpo["assinatura"] = _texto(dados.get("assinatura"), LIMITE_DA_ASSINATURA, "A assinatura")
    limpo["instrucoes"] = _texto(dados.get("instrucoes"), LIMITE_DAS_INSTRUCOES, "As instruções")
    edicao = dados.get("edicao") or {}
    if not isinstance(edicao, dict):
        raise ErroDoEstilo("A edição dos seus vídeos está em formato estranho.")
    limpo["edicao"] = {"legendas": bool(edicao.get("legendas", True))}
    for chave, lista in OPCOES_DA_EDICAO.items():
        valor = edicao.get(chave, PADRAO["edicao"][chave])
        if valor not in [v for v, _, _ in lista]:
            raise ErroDoEstilo(f"Opção desconhecida em {chave}: {valor}.")
        limpo["edicao"][chave] = valor
    return limpo


def _guia_valido(guia: dict) -> dict:
    cores = {k: v for k, v in (guia.get("cores") or {}).items()
             if k in ("fundo", "texto", "destaque") and isinstance(v, str) and RE_COR.match(v)}
    return {"texto": str(guia.get("texto") or "")[:LIMITE_DO_GUIA], "cores": cores, "quem": str(guia.get("quem") or ""),
            "exemplos": int(guia.get("exemplos") or 0), "atualizado": guia.get("atualizado")}


def _gravar(dados: dict) -> dict:
    arquivo = caminho()
    os.makedirs(os.path.dirname(arquivo) or ".", exist_ok=True)
    temporario = arquivo + ".tmp"
    with open(temporario, "w", encoding="utf-8") as saida:
        json.dump({**dados, "atualizado": time.time()}, saida, ensure_ascii=False, indent=1)
    os.replace(temporario, arquivo)
    return atual()


def salvar(dados: dict) -> dict:
    """Confere e grava as opções e, com "guia_texto", o guia editado à mão (as cores tiradas dos exemplos continuam)."""
    dados = dict(dados or {})
    texto_do_guia = dados.pop("guia_texto", None)
    agora = atual()
    limpo = validar({**agora, **dados})
    guia = agora["guia"]
    if texto_do_guia is not None:
        guia["texto"] = _texto(texto_do_guia, LIMITE_DO_GUIA, "O guia")
    return _gravar({**limpo, "guia": guia})


def restaurar_padrao() -> dict:
    """As opções voltam ao padrão; o guia e os exemplos ficam (têm os botões deles)."""
    return _gravar({**_copia(PADRAO), "guia": atual()["guia"]})


# ------------------------------------------------------------------ o que vai para o vídeo
def paleta(estilo: dict = None) -> dict:
    """fundo, fundo2, texto e destaque do tema escolhido (as suas cores ou as do guia, com o padrão no que faltar)."""
    estilo = estilo or atual()
    base = dict(TEMAS["preto_dourado"])
    if estilo.get("cores") == "livre":
        base.update(estilo.get("cores_livres") or {})
    elif estilo.get("cores") == "guia":
        cores = (estilo.get("guia") or {}).get("cores") or {}
        base.update({k: v for k, v in cores.items() if k in base})
        if "fundo" in cores:
            base["fundo2"] = misturar(cores["fundo"], cores.get("destaque", "#808080"), 0.18)
    else:
        base.update(TEMAS.get(estilo.get("cores"), {}))
    return base


def misturar(cor_a: str, cor_b: str, quanto: float) -> str:
    a = [int(cor_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(cor_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * quanto):02x}" for x, y in zip(a, b))


DURACOES = {"curto": (20, 3, 5), "medio": (45, 6, 8), "longo": (90, 10, 14)}  # segundos, cenas (mínimo, máximo)
TONS = {"didatico": "didático: explica passo a passo, com um exemplo concreto",
        "divertido": "divertido: leve, com humor e comparações engraçadas, sem perder a informação",
        "inspirador": "inspirador: frases de impacto e emoção, e uma chamada para ação no fim",
        "serio": "sério e direto, como um telejornal"}


def guia_em_uso(estilo: dict = None) -> str:
    estilo = estilo or atual()
    return (estilo.get("guia") or {}).get("texto", "").strip() if estilo.get("usar_guia", True) else ""


def bloco_do_prompt(estilo: dict = None, segundos: int = None) -> str:
    """O estilo para o Claude: duração, número de cenas, tom, cores, narração e o guia dos exemplos."""
    estilo = estilo or atual()
    alvo, minimo, maximo = DURACOES[estilo["duracao"]]
    if segundos:
        alvo = segundos
        minimo, maximo = max(2, round(segundos / 9)), max(3, round(segundos / 5))
    cores = paleta(estilo)
    formato = {"16x9": "deitado (16:9)", "9x16": "em pé (9:16, celular)", "1x1": "quadrado (1:1)"}[estilo["formato"]]
    linhas = [
        "ESTILO DO OPERADOR (siga sempre):",
        f"- Vídeo {formato}, com uns {alvo} segundos: de {minimo} a {maximo} cenas.",
        f"- Tom {TONS[estilo['tom']]}.",
        f"- Ritmo {dict((v, r) for v, r, _ in OPCOES['ritmo'])[estilo['ritmo']].lower()}.",
        f"- Cores: fundo {cores['fundo']} a {cores['fundo2']}, texto {cores['texto']}, destaque {cores['destaque']}. "
        "As ilustrações combinam com essa paleta.",
        ("- Sem narração: o texto na tela conta a história sozinho (frases curtas e completas)." if estilo["narracao"] == "nenhuma"
         else "- Cada cena tem uma narração falada (1 a 3 frases, até 35 palavras), que soa natural em voz alta."),
    ]
    if estilo.get("instrucoes"):
        linhas.append(f"- Instruções do operador: {estilo['instrucoes']}")
    guia = guia_em_uso(estilo)
    if guia:
        linhas.append(f"- Guia de estilo tirado dos exemplos que o operador gosta:\n{guia}")
    return "\n".join(linhas)


def descricao_curta(estilo: dict = None) -> str:
    estilo = estilo or atual()
    nome = lambda chave: dict((v, r) for v, r, _ in OPCOES[chave])[estilo[chave]].lower()
    return f"{nome('formato')}, {nome('ritmo')}, cores {nome('cores')}, {nome('narracao')}"


# ------------------------------------------------------------------ exemplos (prints e vídeos)
def listar_exemplos() -> list:
    pasta = pasta_dos_exemplos()
    try:
        nomes = sorted(n for n in os.listdir(pasta) if n.lower().endswith(EXTENSOES_DE_IMAGEM))
    except OSError:
        return []
    exemplos = []
    for nome in nomes:
        try:
            estado = os.stat(os.path.join(pasta, nome))
        except OSError:
            continue
        exemplos.append({"id": nome, "nome": nome.split("_", 2)[-1], "tamanho": estado.st_size, "quando": estado.st_mtime})
    return exemplos


def caminho_do_exemplo(exemplo_id: str) -> str:
    """O caminho de um exemplo pelo id (o nome do arquivo); nunca sai da pasta dos exemplos."""
    nome = os.path.basename(str(exemplo_id or ""))
    if not nome or nome != exemplo_id or not nome.lower().endswith(EXTENSOES_DE_IMAGEM):
        raise ErroDoEstilo("Exemplo não encontrado.")
    caminho_exemplo = os.path.join(pasta_dos_exemplos(), nome)
    if not os.path.isfile(caminho_exemplo):
        raise ErroDoEstilo("Exemplo não encontrado.")
    return caminho_exemplo


def _nome_base(nome: str) -> str:
    base = os.path.splitext(os.path.basename(nome or "exemplo"))[0]
    return re.sub(r"[^\w\- ]+", "", base, flags=re.UNICODE).strip()[:40] or "exemplo"


def _guardar(base: str, extensao: str, dados: bytes) -> str:
    os.makedirs(pasta_dos_exemplos(), exist_ok=True)
    exemplo_id = f"{time.strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(2)}_{base}{extensao}"
    with open(os.path.join(pasta_dos_exemplos(), exemplo_id), "wb") as saida:
        saida.write(dados)
    return exemplo_id


def quadros_do_video(caminho_video: str, quantos: int = QUADROS_POR_VIDEO, lado: int = 960) -> list:
    """`quantos` quadros espalhados pelo vídeo (a 20%, 50% e 80%), em JPEG, sem gravar o vídeo."""
    import io

    import av

    quadros = []
    with av.open(caminho_video) as entrada:
        fluxo = entrada.streams.video[0]
        duracao = float(entrada.duration / 1_000_000) if entrada.duration else 0.0
        momentos = [duracao * (i + 1) / (quantos + 1) for i in range(quantos)] if duracao else [0.0]
        for momento in momentos:
            entrada.seek(int(momento * 1_000_000), any_frame=False, backward=True)
            for quadro in entrada.decode(fluxo):
                if quadro.time is None or quadro.time >= momento - 0.05:
                    imagem = quadro.to_image().convert("RGB")
                    imagem.thumbnail((lado, lado))
                    saida = io.BytesIO()
                    imagem.save(saida, "JPEG", quality=85)
                    quadros.append(saida.getvalue())
                    break
    return quadros


def adicionar_exemplo(nome: str, dados: bytes = None, origem: str = None) -> dict:
    """Guarda um print (PNG, JPG, WebP) ou 3 quadros de um vídeo (MP4, MOV, WebM...), de bytes (do app) ou de um arquivo
    (da janela)."""
    extensao = os.path.splitext(nome or origem or "")[1].lower()
    livres = MAXIMO_DE_EXEMPLOS - len(listar_exemplos())
    if extensao in EXTENSOES_DE_VIDEO:
        if livres < 1:
            raise ErroDoEstilo(f"Já há {MAXIMO_DE_EXEMPLOS} exemplos: apague algum antes.")
        temporario = None
        if origem is None:
            if len(dados or b"") > LIMITE_DO_VIDEO_MB * 1024 * 1024:
                raise ErroDoEstilo(f"O vídeo passa de {LIMITE_DO_VIDEO_MB} MB.")
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=extensao, delete=False) as arquivo:
                arquivo.write(dados or b"")
                temporario = origem = arquivo.name
        try:
            quadros = quadros_do_video(origem, min(QUADROS_POR_VIDEO, livres))
        except Exception as exc:
            raise ErroDoEstilo("Não consegui abrir esse vídeo.") from exc
        finally:
            if temporario:
                os.remove(temporario)
        if not quadros:
            raise ErroDoEstilo("Não consegui tirar nenhum quadro desse vídeo.")
        base = _nome_base(nome or origem)
        return {"ids": [_guardar(f"{base} {i}", ".jpg", q) for i, q in enumerate(quadros, start=1)]}
    if livres < 1:
        raise ErroDoEstilo(f"Já há {MAXIMO_DE_EXEMPLOS} exemplos: apague algum antes.")
    if dados is None:
        with open(origem, "rb") as entrada:
            dados = entrada.read(LIMITE_DA_IMAGEM_MB * 1024 * 1024 + 1)
    if len(dados) > LIMITE_DA_IMAGEM_MB * 1024 * 1024:
        raise ErroDoEstilo(f"A imagem passa de {LIMITE_DA_IMAGEM_MB} MB.")
    if not _parece_imagem(dados):
        raise ErroDoEstilo("Só imagens (PNG, JPG, WebP) ou vídeos (MP4, MOV, WebM).")
    extensao = extensao if extensao in EXTENSOES_DE_IMAGEM else ".jpg"
    return {"ids": [_guardar(_nome_base(nome or origem), extensao, dados)]}


def apagar_exemplo(exemplo_id: str) -> dict:
    os.remove(caminho_do_exemplo(exemplo_id))
    return {"id": exemplo_id}


INSTRUCOES_DO_GUIA = (
    "Você é a diretora de arte dos vídeos da Beatriz. O usuário mandou prints e quadros de vídeos de que ele gosta. Olhe "
    "todos e descreva o ESTILO VISUAL em comum, para outra IA fazer um vídeo animado novo, de outro assunto, nesse estilo "
    "(não copie o conteúdo). Responda SOMENTE com um objeto JSON neste formato:\n"
    '{"resumo": str, "visual": [str], "texto": [str], "ritmo": [str], "evitar": [str], '
    '"cores": {"fundo": "#RRGGBB", "texto": "#RRGGBB", "destaque": "#RRGGBB"}}\n'
    "'resumo': o estilo numa frase; 'visual': ilustração (flat, 3D, foto, traço), formas, composição, texturas, luz; "
    "'texto': tamanho e posição dos títulos, quantas palavras por tela, legendas, caixa alta; 'ritmo': o que se percebe da "
    "energia (calmo, ágil, muitos cortes); 'evitar': o que não aparece e não deve aparecer; 'cores': as três que mais marcam "
    "(o fundo, o texto e o destaque), em hexadecimal. De 3 a 6 itens por lista, frases curtas e concretas."
)


def texto_do_guia(dados: dict) -> str:
    linhas = [str(dados["resumo"]).strip()] if dados.get("resumo") else []
    for chave, rotulo in (("visual", "Visual"), ("texto", "Texto"), ("ritmo", "Ritmo"), ("evitar", "Evitar")):
        itens = [str(i).strip() for i in dados.get(chave) or [] if str(i).strip()]
        if itens:
            linhas.append(f"{rotulo}:\n" + "\n".join(f"- {i}" for i in itens))
    return "\n".join(linhas)[:LIMITE_DO_GUIA]


def _ver_no_pc(caminhos: list, prompt: str) -> str:
    """O modelo de visão do Ollama, quando o Claude não pode; sai da placa logo depois (como nos estudos)."""
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
        threading.Thread(target=getattr(llm_clients, "aquecer", lambda: None), daemon=True).start()


def extrair_guia(ver_com_claude=None, ver_no_pc=None) -> dict:
    """Manda os exemplos ao Claude (só a ele, nunca ao Gemini) e, sem ele, ao modelo de visão do PC; grava o guia.
    Levanta ErroDoEstilo sem exemplos ou se nenhum dos dois conseguir."""
    from agentes.base import extrair_json

    exemplos = [caminho_do_exemplo(e["id"]) for e in listar_exemplos()]
    if not exemplos:
        raise ErroDoEstilo("Adicione pelo menos um print ou vídeo de exemplo.")
    if ver_com_claude is None:
        from llm_nuvem import claude

        ver_com_claude = claude.ver_imagens if claude.disponivel() else None
    prompt = f"São {len(exemplos)} imagem(ns) de exemplo (prints e quadros de vídeos). Descreva o estilo em comum."
    quem, resposta = "", ""
    if ver_com_claude is not None:
        try:
            resposta, quem = ver_com_claude(exemplos, prompt, INSTRUCOES_DO_GUIA), "Claude"
        except Exception as exc:
            logger.warning("O Claude não olhou os exemplos de vídeo: %s", exc)
    if not quem:
        try:
            resposta, quem = (ver_no_pc or _ver_no_pc)(exemplos, prompt), "modelo de visão do PC"
        except Exception as exc:
            logger.warning("O modelo de visão do PC não olhou os exemplos de vídeo: %s", exc)
            raise ErroDoEstilo("Nem o Claude nem o modelo de visão do PC conseguiram olhar os exemplos agora.") from exc
    try:
        dados = extrair_json(resposta)
    except ValueError as exc:
        raise ErroDoEstilo(f"O {quem} respondeu num formato que não entendi; tente de novo.") from exc
    texto = texto_do_guia(dados)
    if not texto:
        raise ErroDoEstilo(f"O {quem} não descreveu o estilo dos exemplos; tente outros.")
    cores = {k: v.strip() for k, v in (dados.get("cores") or {}).items() if isinstance(v, str) and RE_COR.match(v.strip())}
    guia = _guia_valido({"texto": texto, "cores": cores, "quem": quem, "exemplos": len(exemplos), "atualizado": time.time()})
    _gravar({**atual(), "guia": guia})
    return guia


# ------------------------------------------------------------------ janela
def para_a_janela() -> dict:
    """Tudo o que a janela e o app desenham: o estilo, as opções com os rótulos, os exemplos e as cores de cada tema."""
    estilo = atual()
    lista = lambda opcoes: [{"valor": v, "rotulo": r, "explica": e} for v, r, e in opcoes]
    return {
        "estilo": estilo,
        "opcoes": {chave: lista(opcoes) for chave, opcoes in OPCOES.items()},
        "opcoes_da_edicao": {chave: lista(opcoes) for chave, opcoes in OPCOES_DA_EDICAO.items()},
        "paletas": {valor: paleta({**estilo, "cores": valor}) for valor, _, _ in OPCOES["cores"]},
        "letras": {chave: css for chave, (css, _, _) in LETRAS.items()},
        "exemplos": listar_exemplos(), "maximo_de_exemplos": MAXIMO_DE_EXEMPLOS,
    }


def resultado(funcao, *argumentos, **nomeados) -> dict:
    """{"ok", "mensagem", "dados"} para a janela e o app, sem deixar escapar exceção."""
    try:
        funcao(*argumentos, **nomeados)
        return {"ok": True, "mensagem": "", "dados": para_a_janela()}
    except ErroDoEstilo as exc:
        return {"ok": False, "mensagem": str(exc), "dados": None}
    except (OSError, ValueError) as exc:
        logger.warning("Estilo de vídeo: %s", exc)
        return {"ok": False, "mensagem": f"Não consegui gravar: {exc}", "dados": None}
