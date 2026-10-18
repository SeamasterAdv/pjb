"""O jeito de falar da Beatriz nas frases prontas: alegre, informal e sem repetir sempre a mesma.

As respostas do LLM seguem a personalidade (config.PERSONALIDADE_BEATRIZ); aqui ficam as falas
que não passam por ele (chamada pelo nome, saudação, agradecimento, despedida, confirmações).
`frase(chave, **campos)` sorteia uma variação e nunca repete a última da mesma chave.
"""
import random
import re
import threading
import unicodedata

FRASES = {
    # Disse só "Beatriz": curtas, porque o áudio delas é gerado de antemão (voice.preparar_frases).
    "so_nome": ["Oi! Fala!", "Tô aqui!", "Oi, oi!", "Diga!", "Opa, pode falar!", "Sou toda ouvidos!"],
    "abertura": ["Que bom ter você por aqui!", "Bora pra mais um dia?", "Cheguei! Tava com saudade.",
                 "Prontinha pra o que der e vier!", "Tudo pronto por aqui!"],
    "convite": ["O que vamos fazer hoje?", "Me conta, o que manda?", "Bora começar?", "Em que eu te ajudo?",
                "O que a gente apronta hoje?"],
    "saudacao": ["{periodo}! Que bom te ouvir. Tudo bem com você?", "{periodo}! Tô aqui. O que vamos aprontar?",
                 "Oi! {periodo}! Como tá o seu dia?", "{periodo}! Chegou quem faltava. Me conta, o que manda?"],
    "obrigado": ["Imagina! Foi um prazer.", "De nada! Precisando, é só chamar.", "Por nada! Tamo junto.",
                 "Que isso! Sempre que quiser.", "Disponha! Adoro ajudar."],
    "tchau": ["Tchau! Foi ótimo conversar com você. Quando quiser, é só chamar.", "Até mais! Tô por aqui se precisar.",
              "Falou! Qualquer coisa, chama a Beatriz aqui.", "Tchauzinho! Até a próxima."],
    "encerrando": ["Vou dar uma descansada. Até logo!", "Desligando! Foi ótimo hoje. Até mais!",
                   "Tchau! Quando precisar, é só me ligar de novo."],
    "deixa_pra_la": ["Beleza, deixa pra lá!", "Tranquilo, esquece então.", "Tá bom! Fica pra próxima.",
                     "Sem problema, deixei quieto."],
    "foi_comigo": ["Opa, foi comigo? Entendi: {texto}.", "Foi pra mim? Entendi: {texto}.",
                   "Ué, falou comigo? Entendi: {texto}."],
    "confirmar_ouvido": ["Só pra confirmar: {comando}, né?", "Entendi: {comando}. É isso mesmo?",
                         "Peraí, deixa eu ver se entendi: {comando}. Certo?"],
    "entendi_errado": ["Ops, foi mal! Entendi errado. O que você queria?",
                       "Eita, viajei! Me fala de novo o que você queria?",
                       "Desculpa, me confundi! O que era mesmo?"],
    "assunto_novo": ["Bora! Assunto novo, cabeça nova. Manda!", "Fechou, página virada! Sobre o que vamos falar?",
                     "Pronto, esqueci tudo! Do que você quer falar agora?"],
    "erro": ["Eita, deu um probleminha aqui. Tenta de novo?", "Ih, travei rapidinho. Pode repetir?",
             "Ops, algo deu errado aqui do meu lado. Bora tentar de novo?"],
    "sem_resposta": ["Hum, essa eu não sei com o que tenho aqui. Me pergunta de outro jeito?",
                     "Olha, não achei nada sobre isso aqui comigo. Tenta falar de outro jeito?"],
}

_ULTIMAS: dict[str, str] = {}
_TRAVA = threading.Lock()


def frase(chave: str, **campos) -> str:
    """Uma variação da frase `chave`, diferente da última dita, com os `campos` preenchidos."""
    opcoes = FRASES[chave]
    with _TRAVA:
        candidatas = [opcao for opcao in opcoes if opcao != _ULTIMAS.get(chave)] or opcoes
        escolhida = random.choice(candidatas)
        _ULTIMAS[chave] = escolhida
    return escolhida.format(**campos) if campos else escolhida


def todas(chave: str) -> list[str]:
    return list(FRASES[chave])


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", (texto or "").lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^\w\s]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


# Agradecimento ou despedida ditos para ela, sem mais nenhum pedido junto.
RE_AGRADECIMENTO = re.compile(r"^(?:(?:muito |mto )?(?:obrigad[oa]|brigad[oa]|valeu|vlw|agradec\w*|thanks?)"
                              r"(?: (?:mesmo|demais|viu|beatriz|querida|amiga|pela ajuda|por tudo))*)$")
RE_DESPEDIDA = re.compile(
    r"^(?:(?:ok |beleza |valeu |obrigad[oa] |entao )*(?:tchau(?: tchau)?|tchauzinho|ate logo|ate mais|ate depois|ate amanha|"
    r"adeus|falou|fui|era so isso|e so isso|so isso|so isso mesmo|por enquanto e so|pode descansar|"
    r"pode ir|chega por hoje)(?: (?:beatriz|querida|obrigad[oa]|valeu|por hoje|entao|mesmo|viu))*)$"
)


def tipo_de_encerramento(texto: str) -> str | None:
    """"tchau" (encerra a conversa), "obrigado" (agradece e segue) ou None."""
    normal = re.sub(r"^(?:beatriz|beatris|biatriz) ", "", _normalizar(texto))
    if RE_DESPEDIDA.match(normal):
        return "tchau"
    if RE_AGRADECIMENTO.match(normal):
        return "obrigado"
    return None
