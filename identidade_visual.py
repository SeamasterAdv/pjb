"""Identidade visual da Beatriz.

O ícone (assets/gerar_icone.py) é a esfera em rede do painel, em branco sobre preto: uma casca de poeira e uma rede
de nós ligados por linhas, a frente mais clara que o fundo (`formas_esfera`, `imagem_esfera`). Até 48 px fica uma
versão de traço grosso (um anel e uma malha geodésica), para continuar legível na barra de tarefas.

A paleta roxo e dourado e o desenho do sol (raios ondulados e retos em volta de um disco, com um "b" no centro)
continuam no painel clássico em Tkinter (painel_hud.NucleoHUD) e na tela de login antiga.
"""
import math
import random

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


# ------------------------------------------------------------------ a esfera em rede (o ícone)
PRETO = "#000000"
BRANCO = "#FFFFFF"
INCLINACAO_ESFERA = (0.32, -0.55)  # graus em x e em y (radianos), como a esfera do painel parada


def _girar(ponto, inclinacao=INCLINACAO_ESFERA):
    x, y, z = ponto
    ax, ay = inclinacao
    x, z = x * math.cos(ay) + z * math.sin(ay), -x * math.sin(ay) + z * math.cos(ay)
    y, z = y * math.cos(ax) - z * math.sin(ax), y * math.sin(ax) + z * math.cos(ax)
    return x, y, z


def _direcao(sorteio):
    u, t = sorteio.uniform(-1, 1), sorteio.uniform(0, 2 * math.pi)
    s = math.sqrt(1 - u * u)
    return s * math.cos(t), u, s * math.sin(t)


def formas_esfera(poeira=1400, nos=170, distancia=0.4, semente=7):
    """A esfera numa grade de -1 a 1 (já girada; z > 0 é a frente).

    Devolve {"poeira": [(x, y, z)], "nos": [(x, y, z)], "linhas": [(a, b, perto)]}: `perto` vai de 0 (no limite da
    distância) a 1 (encostados). A mesma semente dá sempre o mesmo desenho.
    """
    sorteio = random.Random(semente)
    poeira_pts = [_girar(_direcao(sorteio)) for _ in range(poeira)]
    centros = [_direcao(sorteio) for _ in range(5)]
    nos_pts = []
    for i in range(nos):
        d = _direcao(sorteio)
        if sorteio.random() < 0.28:  # parte dos nós se junta em "novelos", como no painel
            c, espalhar = centros[i % len(centros)], 0.35 + sorteio.random() * 0.25
            d = tuple(c[k] + d[k] * espalhar * sorteio.random() for k in range(3))
            tamanho = math.sqrt(sum(v * v for v in d))
            d = tuple(v / tamanho for v in d)
        nos_pts.append(_girar(d))
    linhas = []
    for a in range(nos):
        for b in range(a + 1, nos):
            dist = math.dist(nos_pts[a], nos_pts[b])
            if dist < distancia:
                linhas.append((a, b, 1 - dist / distancia))
    return {"poeira": poeira_pts, "nos": nos_pts, "linhas": linhas}


def _malha_geodesica():
    """Os 12 vértices e as 30 arestas de um icosaedro (a rede de traço grosso dos ícones pequenos), já girados."""
    f = (1 + math.sqrt(5)) / 2
    vertices = [(-1, f, 0), (1, f, 0), (-1, -f, 0), (1, -f, 0), (0, -1, f), (0, 1, f), (0, -1, -f), (0, 1, -f),
                (f, 0, -1), (f, 0, 1), (-f, 0, -1), (-f, 0, 1)]
    norma = math.sqrt(1 + f * f)
    vertices = [_girar(tuple(v / norma for v in p), (0.45, 0.35)) for p in vertices]
    arestas = [(a, b) for a in range(12) for b in range(a + 1, 12) if abs(math.dist(vertices[a], vertices[b]) - 2 / norma) < 1e-6]
    return vertices, arestas


def _brilho_pela_frente(z, fundo=0.22):
    return fundo + (1 - fundo) * max(0.0, min(1.0, (z + 0.8) / 1.6))


def imagem_esfera(tamanho, com_fundo=True, simplificado=None, superamostragem=6, raio=0.38, cantos=0.22):
    """A esfera em rede num quadrado de `tamanho` px, desenhada grande e depois reduzida.

    `com_fundo` põe o quadrado preto de cantos arredondados (`cantos` = fração do lado; 0 = quadrado cheio, para o
    iPhone, que arredonda sozinho); sem ele, o fundo fica transparente. Até 48 px usa a versão de traço grosso.
    """
    if simplificado is None:
        simplificado = tamanho <= 48
    lado = tamanho * superamostragem
    luz = Image.new("L", (lado, lado), 0)  # o desenho em tons de cinza: depois vira branco sobre preto
    d = ImageDraw.Draw(luz)
    centro, r = lado / 2, raio * lado

    def tela(p):
        return centro + p[0] * r, centro - p[1] * r

    if simplificado:
        traco = max(1.0, lado * 0.045)
        vertices, arestas = _malha_geodesica()
        so_a_frente = tamanho <= 24  # muito pequeno: as arestas de trás embolam o desenho
        for a, b in sorted(arestas, key=lambda ar: vertices[ar[0]][2] + vertices[ar[1]][2]):  # o fundo antes da frente
            z = (vertices[a][2] + vertices[b][2]) / 2
            if so_a_frente and z < 0.05:
                continue
            d.line([tela(vertices[a]), tela(vertices[b])], fill=round(255 * _brilho_pela_frente(z, 0.3)), width=round(traco * 0.8))
        d.ellipse([centro - r, centro - r, centro + r, centro + r], outline=255, width=round(traco))
        for p in vertices:
            if p[2] > 0:
                x, y = tela(p)
                d.ellipse([x - traco, y - traco, x + traco, y + traco], fill=255)
    else:
        grande = tamanho >= 128
        formas = formas_esfera(poeira=1600 if grande else 450)
        # Medidas no tamanho final (em px), para a rede não sumir nos ícones médios; depois, na escala do desenho.
        ponto = max(0.35, tamanho * 0.0032) * superamostragem
        for p in sorted(formas["poeira"], key=lambda q: q[2]):
            x, y = tela(p)
            d.ellipse([x - ponto, y - ponto, x + ponto, y + ponto], fill=round((120 if grande else 90) * _brilho_pela_frente(p[2], 0.15)))
        nos = formas["nos"]
        largura = max(1, round(max(0.55, tamanho * 0.0042) * superamostragem))
        for a, b, perto in sorted(formas["linhas"], key=lambda l: nos[l[0]][2] + nos[l[1]][2]):
            z = (nos[a][2] + nos[b][2]) / 2
            d.line([tela(nos[a]), tela(nos[b])], fill=round(255 * _brilho_pela_frente(z) * (0.35 + 0.65 * perto)), width=largura)
        no = max(0.8, tamanho * 0.007) * superamostragem
        for p in sorted(nos, key=lambda q: q[2]):
            x, y = tela(p)
            d.ellipse([x - no, y - no, x + no, y + no], fill=round(255 * _brilho_pela_frente(p[2])))
        d.ellipse([centro - r, centro - r, centro + r, centro + r], outline=70, width=max(1, round(lado * 0.004)))

    imagem = Image.merge("RGBA", (luz, luz, luz, Image.new("L", (lado, lado), 255)))
    if com_fundo:
        mascara = Image.new("L", (lado, lado), 0)
        ImageDraw.Draw(mascara).rounded_rectangle([0, 0, lado - 1, lado - 1], radius=cantos * lado, fill=255)
        imagem.putalpha(mascara)
    else:
        imagem.putalpha(luz)  # sem fundo: só o desenho (o preto vira transparente)
    return imagem.resize((tamanho, tamanho), Image.LANCZOS)
