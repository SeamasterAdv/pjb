"""O jeito de falar da Beatriz nas frases prontas, sem repetir sempre a mesma.

As respostas da IA seguem a personalidade (personalidade.texto()); aqui ficam as falas que não passam por ela
(chamada pelo nome, saudação, agradecimento, despedida, confirmações). As frases são as da janela
[ personalidade ] do painel (personalidade.frases); o padrão está em personalidade.FRASES_PADRAO.
`frase(chave, **campos)` sorteia uma variação e nunca repete a última da mesma chave.
"""
import random
import re
import threading
import unicodedata

import personalidade

FRASES = personalidade.FRASES_PADRAO  # o padrão; as de agora vêm de personalidade.frases(chave)

_ULTIMAS: dict[str, str] = {}
_TRAVA = threading.Lock()


def frase(chave: str, **campos) -> str:
    """Uma variação da frase `chave`, diferente da última dita, com os `campos` preenchidos."""
    opcoes = personalidade.frases(chave)
    with _TRAVA:
        candidatas = [opcao for opcao in opcoes if opcao != _ULTIMAS.get(chave)] or opcoes
        escolhida = random.choice(candidatas)
        _ULTIMAS[chave] = escolhida
    return escolhida.format(**campos) if campos else escolhida


def todas(chave: str) -> list[str]:
    return personalidade.frases(chave)


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
