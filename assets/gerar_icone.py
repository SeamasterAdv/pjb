"""Gera o ícone da Beatriz (a esfera em rede, branca sobre preto) em SVG, PNG e ICO, e os ícones do app do celular.

Uso: .venv312\\Scripts\\python.exe assets\\gerar_icone.py

O desenho vem de identidade_visual.py (`formas_esfera`, `imagem_esfera`), o mesmo visual do painel. Até 48 px fica a
versão de traço grosso (um anel e uma malha geodésica), para continuar legível na barra de tarefas.
"""
import os
import sys

PASTA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(PASTA))

from identidade_visual import PRETO, _brilho_pela_frente, formas_esfera, imagem_esfera  # noqa: E402

CANTOS = 0.22  # fração do lado (o raio dos cantos do quadrado preto)
RAIO = 0.38  # o raio da esfera, em fração do lado
TAMANHOS_ICO = [16, 20, 24, 32, 40, 48, 64, 256]
# Ícones do app do celular (interface_app/icones): quadrados cheios, porque o iPhone arredonda os cantos sozinho; a
# esfera um pouco menor, para caber na área segura dos ícones "maskable" do Android.
PASTA_ICONES_APP = os.path.join(os.path.dirname(PASTA), "interface_app", "icones")
TAMANHOS_APP = [180, 192, 512]
RAIO_APP = 0.34


def svg() -> str:
    formas = formas_esfera(poeira=700)
    escala, centro = RAIO * 100, 50

    def tela(p):
        return centro + p[0] * escala, centro - p[1] * escala

    partes = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">',
        f'<rect width="100" height="100" rx="{CANTOS * 100:g}" fill="{PRETO}"/>',
        '<g fill="#fff">',
    ]
    for p in sorted(formas["poeira"], key=lambda q: q[2]):
        x, y = tela(p)
        partes.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r=".28" opacity="{0.47 * _brilho_pela_frente(p[2], 0.15):.2f}"/>')
    partes.append('</g><g stroke="#fff" stroke-width=".35" stroke-linecap="round">')
    nos = formas["nos"]
    for a, b, perto in sorted(formas["linhas"], key=lambda linha: nos[linha[0]][2] + nos[linha[1]][2]):
        (x1, y1), (x2, y2) = tela(nos[a]), tela(nos[b])
        opacidade = _brilho_pela_frente((nos[a][2] + nos[b][2]) / 2) * (0.35 + 0.65 * perto)
        partes.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" opacity="{opacidade:.2f}"/>')
    partes.append('</g><g fill="#fff">')
    for p in sorted(nos, key=lambda q: q[2]):
        x, y = tela(p)
        partes.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r=".85" opacity="{_brilho_pela_frente(p[2]):.2f}"/>')
    partes.append(f'</g><circle cx="50" cy="50" r="{escala:g}" fill="none" stroke="#fff" stroke-width=".4" opacity=".27"/>')
    partes.append("</svg>\n")
    return "".join(partes)


def main():
    imagens = {tamanho: imagem_esfera(tamanho, raio=RAIO, cantos=CANTOS) for tamanho in TAMANHOS_ICO}
    imagens[256].save(
        os.path.join(PASTA, "beatriz_icone.ico"),
        format="ICO",
        sizes=[(t, t) for t in TAMANHOS_ICO],
        append_images=[imagens[t] for t in TAMANHOS_ICO if t != 256],
    )
    imagens[256].save(os.path.join(PASTA, "beatriz_icone_256.png"))
    with open(os.path.join(PASTA, "beatriz_icone.svg"), "w", encoding="utf-8") as arquivo:
        arquivo.write(svg())
    icones_do_app()
    print(f"Ícones gerados em {PASTA} e {PASTA_ICONES_APP}")


def icones_do_app():
    os.makedirs(PASTA_ICONES_APP, exist_ok=True)
    for tamanho in TAMANHOS_APP:
        imagem = imagem_esfera(tamanho, raio=RAIO_APP, cantos=0)
        imagem.convert("RGB").save(os.path.join(PASTA_ICONES_APP, f"icone-{tamanho}.png"))


if __name__ == "__main__":
    main()
