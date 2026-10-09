"""Treinos locais de ingles e frances para a Beatriz."""
import json
import os
import unicodedata
from datetime import datetime

from config import PERSONALIDADE_PROFESSORA_IDIOMAS, VAULT_OBSIDIAN

PASTA_IDIOMAS = os.path.join(VAULT_OBSIDIAN, "idiomas")
ARQUIVO_PROGRESSO = os.path.join(PASTA_IDIOMAS, "progresso.json")

BANCO_IDIOMAS = {
    "ingles": [
        {"palavra": "hello", "traducao": "ola", "frase": "Hello, how are you?", "significado": "Olá, como você está?", "pronuncia": "rélou, rau ar iú", "uso": "Neutro e comum. Entre amigos, 'Hi, how are you?' soa mais casual."},
        {"palavra": "thank you", "traducao": "obrigado", "frase": "Thank you for your help.", "significado": "Obrigado pela sua ajuda.", "pronuncia": "thénk iú for iór rélp", "uso": "'Thanks' é a forma mais casual; 'I appreciate it' demonstra mais ênfase."},
        {"palavra": "book", "traducao": "livro", "frase": "I am reading a book.", "significado": "Eu estou lendo um livro.", "pronuncia": "ai ém rídin a buk", "uso": "A contração 'I'm reading a book' é a forma mais natural na fala."},
        {"palavra": "good morning", "traducao": "bom dia", "frase": "Good morning, everyone.", "significado": "Bom dia, pessoal.", "pronuncia": "gud mórnin, évriuan", "uso": "'Morning!' também é usado informalmente entre pessoas que já se conhecem."},
        {"palavra": "water", "traducao": "agua", "frase": "Can I have some water?", "significado": "Posso tomar um pouco de água?", "pronuncia": "kén ai rév sam uórer", "uso": "Em um café, 'Could I have some water, please?' soa mais educado."},
    ],
    "frances": [
        {"palavra": "bonjour", "traducao": "ola", "frase": "Bonjour, comment allez-vous?", "significado": "Olá, como você está?", "pronuncia": "bonjur, comã tale vu", "uso": "Na França, dizer 'bonjour' ao entrar em uma loja é uma regra de cortesia importante."},
        {"palavra": "merci", "traducao": "obrigado", "frase": "Merci pour votre aide.", "significado": "Obrigado pela sua ajuda.", "pronuncia": "mérci pur votr éd", "uso": "'Merci beaucoup' acrescenta intensidade; 'merci' sozinho já é completo."},
        {"palavra": "livre", "traducao": "livro", "frase": "Je lis un livre.", "significado": "Eu leio um livro.", "pronuncia": "jê li an livr", "uso": "'Je suis en train de lire' enfatiza que a leitura está acontecendo agora."},
        {"palavra": "bonsoir", "traducao": "boa noite", "frase": "Bonsoir, tout le monde.", "significado": "Boa noite, pessoal.", "pronuncia": "bonsuar, tu le mond", "uso": "'Bonsoir' é uma saudação ao chegar; para desejar boa noite ao sair, use 'bonne nuit'."},
        {"palavra": "eau", "traducao": "agua", "frase": "Puis-je avoir de l'eau?", "significado": "Posso tomar água?", "pronuncia": "pui-j avuar de lô", "uso": "Em conversa informal, 'Je peux avoir de l'eau?' é mais espontâneo."},
    ],
}

DICIONARIO_EXTRA = {
    "ingles": {
        "casa": ("house", "raus", "Para o lar como conceito, também se usa 'home'."),
        "amigo": ("friend", "frénd", "'Buddy' e 'mate' são opções mais informais, dependendo da região."),
        "amor": ("love", "lâv", "'Love' serve para afeto e também aparece em contextos românticos."),
        "comida": ("food", "fud", "'Meal' significa uma refeição específica, não comida em geral."),
        "trabalho": ("work", "uârk", "'Job' é um emprego ou uma tarefa; 'work' é o trabalho em geral."),
        "escola": ("school", "skul", "A pronúncia começa com o som 'sk', sem uma vogal antes."),
        "obrigado": ("thank you", "thénk iú", "'Thanks' é mais casual e muito comum na fala."),
        "por favor": ("please", "pliz", "Pode aparecer no início ou no fim de um pedido educado."),
        "água": ("water", "uórer", "A pronúncia varia entre Estados Unidos e Reino Unido."),
        "feliz": ("happy", "répi", "'Glad' costuma expressar satisfação com uma situação específica."),
    },
    "frances": {
        "casa": ("maison", "mézõ", "'Chez moi' significa 'na minha casa' e é muito usado por nativos."),
        "amigo": ("ami", "ami", "Para uma amiga, use 'amie'; a pronúncia é praticamente igual."),
        "amor": ("amour", "amur", "'Mon amour' é uma forma carinhosa e comum em contexto romântico."),
        "comida": ("nourriture", "nuritúr", "Em situações cotidianas, 'repas' é usado para falar de uma refeição."),
        "trabalho": ("travail", "travai", "'Boulot' é a forma informal de dizer trabalho ou emprego."),
        "escola": ("école", "ecól", "O acento marca a pronúncia fechada do primeiro 'e'."),
        "obrigado": ("merci", "mérci", "'Merci beaucoup' acrescenta intensidade."),
        "por favor": ("s'il vous plaît", "sil vu plé", "A forma informal é 's'il te plaît'."),
        "água": ("eau", "ô", "Apesar de ter três letras, 'eau' tem um único som de vogal."),
        "feliz": ("heureux", "erê", "Para o feminino, use 'heureuse', com mudança na escrita."),
    },
}

def _normalizar(texto):
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(caractere for caractere in texto if unicodedata.category(caractere) != "Mn")


def _estado_padrao():
    return {
        "idiomas": {
            idioma: {"aprendidas": [], "exercicios": 0, "erros": 0, "acertos": 0}
            for idioma in BANCO_IDIOMAS
        },
        "idioma_atual": None,
        "exercicio_atual": None,
    }


def _carregar_estado():
    os.makedirs(PASTA_IDIOMAS, exist_ok=True)
    estado = _estado_padrao()
    try:
        with open(ARQUIVO_PROGRESSO, "r", encoding="utf-8") as arquivo:
            salvo = json.load(arquivo)
        estado.update({chave: valor for chave, valor in salvo.items() if chave in estado})
        for idioma in BANCO_IDIOMAS:
            estado["idiomas"].setdefault(idioma, _estado_padrao()["idiomas"][idioma])
    except (OSError, json.JSONDecodeError):
        pass
    return estado


def _salvar_estado(estado):
    os.makedirs(PASTA_IDIOMAS, exist_ok=True)
    temporario = ARQUIVO_PROGRESSO + ".tmp"
    with open(temporario, "w", encoding="utf-8") as arquivo:
        json.dump(estado, arquivo, ensure_ascii=False, indent=2)
    os.replace(temporario, ARQUIVO_PROGRESSO)


def _nome_idioma(texto):
    nome = _normalizar(texto)
    if "ingles" in nome or "english" in nome:
        return "ingles"
    if "frances" in nome or "francais" in nome or "francês" in texto.lower():
        return "frances"
    return None


def _consultar_dicionario(comando):
    """Consulta tradução, pronúncia e uso sem exigir que uma aula esteja ativa."""
    normalizado = _normalizar(comando)
    idioma = _nome_idioma(comando)
    gatilhos = ("como se fala", "como digo", "traduza", "traducao de", "traducao para")
    if not idioma or not any(gatilho in normalizado for gatilho in gatilhos):
        return False, None

    termo = normalizado
    for gatilho in gatilhos:
        termo = termo.replace(gatilho, " ")
    termo = termo.replace("ingles", " ").replace("frances", " ")
    termo = termo.replace(" em ", " ").replace(" para ", " ")
    termo = " ".join(termo.split()).strip(" ?!.,;:")
    if not termo:
        return True, "Qual palavra ou expressão você quer consultar?"

    for item in BANCO_IDIOMAS[idioma]:
        if termo in {_normalizar(item["palavra"]), _normalizar(item["significado"]), _normalizar(item["traducao"])}:
            return True, (
                f"Em {idioma}, '{termo}' pode ser '{item['palavra']}'.\n"
                f"Pronúncia aproximada: {item['pronuncia']}\n"
                f"Exemplo: {item['frase']}\n"
                f"Uso natural: {item['uso']}"
            )

    dados = DICIONARIO_EXTRA[idioma].get(termo)
    if dados:
        traducao, pronuncia, uso = dados
        return True, (
            f"Em {idioma}, '{termo}' é '{traducao}'.\n"
            f"Pronúncia aproximada: {pronuncia}\n"
            f"Uso natural: {uso}"
        )

    nomes = {"ingles": "inglês", "frances": "francês"}
    return True, f"Ainda não tenho '{termo}' no dicionário local de {nomes[idioma]}. Posso ensinar essa expressão em uma conversa contextualizada."


def _proximo_exercicio(estado, idioma):
    banco = BANCO_IDIOMAS[idioma]
    atual = estado["exercicio_atual"] or {}
    indice = (atual.get("indice", -1) + 1) % len(banco) if atual.get("idioma") == idioma else 0
    tipos = ["traducao", "escrita", "leitura"]
    if atual.get("idioma") == idioma and atual.get("tipo") in tipos:
        tipo = atual["tipo"]
    else:
        tipo = tipos[(estado["idiomas"][idioma]["exercicios"]) % len(tipos)]
    estado["idioma_atual"] = idioma
    estado["exercicio_atual"] = {"idioma": idioma, "indice": indice, "tipo": tipo}
    item = banco[indice]
    if tipo == "traducao":
        return f"Traduza para o português, preservando o sentido natural: {item['frase']}"
    if tipo == "escrita":
        return f"Escreva em {idioma} como uma pessoa local diria: {item['significado']}"
    return f"Leia em voz alta: {item['frase']}\nPronúncia aproximada: {item['pronuncia']}\nQuando terminar, diga 'próximo exercício'."


def _progresso(estado, idioma=None):
    idiomas = [idioma] if idioma else list(BANCO_IDIOMAS)
    linhas = ["Progresso da professora de idiomas:"]
    for nome in idiomas:
        dados = estado["idiomas"][nome]
        linhas.append(
            f"- {nome.title()}: {len(dados['aprendidas'])}/{len(BANCO_IDIOMAS[nome])} palavras/frases aprendidas; "
            f"{dados['exercicios']} exercícios; {dados['acertos']} acertos; {dados['erros']} erros."
        )
    return "\n".join(linhas)


def _corrigir(estado, resposta):
    atual = estado.get("exercicio_atual")
    if not atual:
        return False, None
    idioma = atual["idioma"]
    item = BANCO_IDIOMAS[idioma][atual["indice"]]
    dados = estado["idiomas"][idioma]
    dados["exercicios"] += 1
    resposta_limpa = _normalizar(resposta).strip(" .!?\n")
    if atual["tipo"] == "traducao":
        esperado = _normalizar(item["significado"]).strip(" .!?")
        correto = resposta_limpa == esperado or resposta_limpa == _normalizar(item["traducao"])
    elif atual["tipo"] == "escrita":
        esperado = _normalizar(item["palavra"]).strip(" .!?")
        correto = resposta_limpa == esperado
    else:
        return True, "Para avaliar pronúncia com precisão, preciso de uma captura de áudio; por enquanto, compare sua fala com a pronúncia aproximada e avance para o próximo exercício."

    if correto:
        dados["acertos"] += 1
        if atual["indice"] not in dados["aprendidas"]:
            dados["aprendidas"].append(atual["indice"])
        mensagem = f"Muito bem. A forma natural é: {item['frase']} ({item['significado']}).\nNota de uso: {item['uso']}"
    else:
        dados["erros"] += 1
        mensagem = f"Ainda não. A forma natural é: {item['frase']} ({item['significado']}).\nNota de uso: {item['uso']}\nTente novamente ou diga 'próximo exercício'."
    _salvar_estado(estado)
    return True, mensagem + "\nDeseja continuar ou sair?"


def _conversar_com_nativa(estado, idioma, pedido):
    """Cria uma fala contextual de nativa e mantém um fallback offline."""
    estado["idioma_atual"] = idioma
    _salvar_estado(estado)
    nomes = {"ingles": "inglês", "frances": "francês"}
    prompt = (
        f"Conduza uma conversa curta em {nomes[idioma]} sobre: {pedido or 'uma situação cotidiana'}. "
        "Escreva primeiro uma fala natural na língua-alvo e depois explique em português "
        "o significado, o nível de formalidade e uma expressão local importante. "
        "Termine com uma pergunta simples para o aluno responder na língua-alvo."
    )
    try:
        from llm_clients import llm_clients
        if llm_clients.is_ollama_available():
            return llm_clients.chat_beatriz(
                prompt,
                contexto=(
                    PERSONALIDADE_PROFESSORA_IDIOMAS
                    + f"\nIdioma da aula: {nomes[idioma]}."
                ),
            )
    except Exception:
        pass
    item = BANCO_IDIOMAS[idioma][0]
    return f"{item['frase']}\nSignificado: {item['significado']}\nNota de uma falante local: {item['uso']}"


def processar_comando_idiomas(comando):
    """Processa comandos de treino e retorna (foi_processado, resposta)."""
    texto = (comando or "").strip()
    normalizado = _normalizar(texto)
    idioma = _nome_idioma(texto)
    ativa = any(expressao in normalizado for expressao in ("professora de idiomas", "modo professora", "modo idiomas", "aula de idiomas"))
    estado = _carregar_estado()
    aula_ativa = bool(estado.get("idioma_atual") or estado.get("exercicio_atual"))
    sair = any(expressao in normalizado for expressao in ("sair da professora", "encerrar aula", "parar aula"))
    sair = sair or (aula_ativa and any(expressao in normalizado for expressao in ("quero sair", "sair")))

    dicionario_executado, resposta_dicionario = _consultar_dicionario(texto)
    if dicionario_executado:
        return True, resposta_dicionario

    if sair:
        estado["idioma_atual"] = None
        estado["exercicio_atual"] = None
        _salvar_estado(estado)
        return True, "Aula encerrada. Quando quiser continuar, diga 'modo professora de idiomas'."
    if ativa:
        return True, "Modo professora ativado. Posso ensinar inglês ou francês. Diga 'treinar inglês', 'treinar francês' ou 'progresso dos idiomas'."
    if "progresso" in normalizado and ("idioma" in normalizado or "ingles" in normalizado or "frances" in normalizado):
        return True, _progresso(estado, idioma)
    if idioma and any(expressao in normalizado for expressao in ("treinar", "praticar", "estudar", "aula")):
        estado["idioma_atual"] = idioma
        resposta = f"Vamos estudar {idioma}.\n" + _proximo_exercicio(estado, idioma)
        _salvar_estado(estado)
        return True, resposta
    if any(expressao in normalizado for expressao in ("conversar", "conversacao", "falar em", "dialogo")):
        idioma = idioma or estado.get("idioma_atual")
        if not idioma:
            return True, "Escolha o idioma da conversa: diga 'conversar em inglês' ou 'conversar em francês'."
        pedido = texto
        return True, _conversar_com_nativa(estado, idioma, pedido)
    if any(expressao in normalizado for expressao in ("continuar", "proximo exercicio", "novo exercicio", "outra questao")):
        idioma = estado.get("idioma_atual") or idioma
        if not idioma:
            return True, "Escolha um idioma primeiro: diga 'treinar inglês' ou 'treinar francês'."
        resposta = _proximo_exercicio(estado, idioma)
        _salvar_estado(estado)
        return True, resposta
    if estado.get("exercicio_atual") and estado.get("idioma_atual"):
        return _corrigir(estado, texto)
    return False, None


def progresso_idiomas():
    return _progresso(_carregar_estado())
