"""Identidade visual da Beatriz: a paleta roxo e dourado e o desenho do sol.

O sol segue o modelo heráldico: raios ondulados e retos alternados em volta de um
disco, com um "b" no centro. É o mesmo desenho no ícone (assets/gerar_icone.py) e
no núcleo animado do painel (painel_hud.NucleoHUD).
"""
import math

from PIL import Image, ImageDraw

# Paleta
ROXO = "#4A2574"  # fundo do ícone
ROXO_NOITE = "#1C1030"  # fundo do painel
ROXO_PAINEL = "#271843"
ROXO_CARTAO = "#33204F"
ROXO_BORDA = "#45306B"
ROXO_TRILHA = "#5A4285"  # trilhas de medidores e linhas inativas
LILAS = "#C7AEF0"
LILAS_SUAVE = "#A391C4"  # rótulos e textos secundários
DOURADO = "#F2B732"
DOURADO_CLARO = "#FFD25E"
CREME = "#FFF6E6"  # texto principal
VERDE = "#7BE0A6"
LARANJA = "#FF9F5A"
VERMELHO = "#FF7A8A"

# Medidas do sol numa grade em que o raio mais longo vai até 45.
RAIO_EXTERNO_SOL = 45


def _ponto(raio, angulo, desvio=0.0):
    """Ponto a `raio` do centro na direção `angulo`, deslocado `desvio` na perpendicular."""
    cos, sen = math.cos(angulo), math.sin(angulo)
    return raio * cos - desvio * sen, raio * sen + desvio * cos


def _raio_ondulado(angulo, inicio, fim, base, amplitude, passos):
    """Contorno de um raio em forma de chama: segue uma senoide e afina até a ponta."""
    esquerda, direita = [], []
    for i in range(passos + 1):
        t = i / passos
        raio = inicio + t * (fim - inicio)
        desvio = amplitude * math.sin(2 * math.pi * t)
        meia_largura = base / 2 * (1 - t)
        esquerda.append(_ponto(raio, angulo, desvio + meia_largura))
        direita.append(_ponto(raio, angulo, desvio - meia_largura))
    return esquerda + direita[::-1]


def _raio_reto(angulo, inicio, fim, base):
    return [_ponto(inicio, angulo, base / 2), _ponto(fim, angulo), _ponto(inicio, angulo, -base / 2)]


def formas_sol(cx=50.0, cy=50.0, escala=1.0, giro=0.0, simplificado=False, passos=40):
    """Raios, disco e "b" do sol centrados em (cx, cy).

    Com `escala` 1 o raio mais longo mede 45 (a grade do ícone é 100 x 100); `giro`
    gira só os raios, em graus. `simplificado` deixa só os raios ondulados, mais
    grossos, e tira o "b", para tamanhos pequenos.
    """
    angulos = [math.radians(giro - 90 + 45 * i) for i in range(8)]
    if simplificado:
        raios = [_raio_ondulado(a, 12, 46, 14, 3.5, passos) for a in angulos]
        disco, b = 18, None
    else:
        raios = [_raio_reto(a + math.radians(22.5), 13, 35, 8) for a in angulos]
        raios += [_raio_ondulado(a, 13, 45, 10, 3.5, passos) for a in angulos]
        disco = 16
        b = {
            "traco": 3.8 * escala,
            "haste": (cx - 5.5 * escala, cy - 10.5 * escala, cy + 8.5 * escala),  # x, topo, base
            "barriga": ((cx + 0.5 * escala, cy + 4.0 * escala), 5.3 * escala),  # centro, raio
        }
    return {
        "raios": [[(cx + x * escala, cy + y * escala) for x, y in raio] for raio in raios],
        "disco": disco * escala,
        "b": b,
    }


def imagem_sol(tamanho, giro=0.0, com_fundo=False, simplificado=None, superamostragem=8):
    """Sol suavizado num quadrado de `tamanho` px, desenhado grande e depois reduzido.

    `com_fundo` põe o quadrado roxo de cantos arredondados do ícone. Se `simplificado`
    não for informado, a versão simplificada é usada até 32 px.
    """
    if simplificado is None:
        simplificado = tamanho <= 32
    lado = tamanho * superamostragem
    k = lado / 100
    imagem = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    d = ImageDraw.Draw(imagem)
    sol = formas_sol(giro=giro, simplificado=simplificado)

    def circulo(centro, raio, cor):
        x, y = centro
        d.ellipse([(x - raio) * k, (y - raio) * k, (x + raio) * k, (y + raio) * k], fill=cor)

    if com_fundo:
        d.rounded_rectangle([0, 0, lado - 1, lado - 1], radius=24 * k, fill=ROXO)
    for raio in sol["raios"]:
        d.polygon([(x * k, y * k) for x, y in raio], fill=DOURADO)
    circulo((50, 50), sol["disco"], DOURADO_CLARO)

    b = sol["b"]
    if b:
        # Barriga do "b": disco roxo com o miolo da cor do sol por cima.
        (cx, cy), raio_barriga = b["barriga"]
        meio = b["traco"] / 2
        circulo((cx, cy), raio_barriga + meio, ROXO)
        circulo((cx, cy), raio_barriga - meio, DOURADO_CLARO)
        x, y1, y2 = b["haste"]
        d.rectangle([(x - meio) * k, y1 * k, (x + meio) * k, y2 * k], fill=ROXO)
        circulo((x, y1), meio, ROXO)
        circulo((x, y2), meio, ROXO)

    return imagem.resize((tamanho, tamanho), Image.LANCZOS)
