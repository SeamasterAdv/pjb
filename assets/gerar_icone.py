"""Gera o ícone da Beatriz (sol dourado sobre roxo) em SVG, PNG e ICO.

Uso: .venv312\\Scripts\\python.exe assets\\gerar_icone.py

O desenho do sol e as cores vêm de identidade_visual.py, os mesmos do painel. Até
32 px fica a versão simplificada do sol, para continuar legível na barra de tarefas.
"""
import os
import sys

PASTA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(PASTA))

from identidade_visual import DOURADO, DOURADO_CLARO, ROXO, formas_sol, imagem_sol  # noqa: E402

RAIO_CANTO = 24
TAMANHOS_ICO = [16, 20, 24, 32, 40, 48, 64, 256]


def svg() -> str:
    sol = formas_sol()
    partes = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">',
        f'<rect width="100" height="100" rx="{RAIO_CANTO}" fill="{ROXO}"/>',
    ]
    for raio in sol["raios"]:
        pontos = " ".join(f"{x:.2f},{y:.2f}" for x, y in raio)
        partes.append(f'<polygon points="{pontos}" fill="{DOURADO}"/>')
    partes.append(f'<circle cx="50" cy="50" r="{sol["disco"]}" fill="{DOURADO_CLARO}"/>')
    b = sol["b"]
    (cx, cy), raio_barriga = b["barriga"]
    x, y1, y2 = b["haste"]
    partes.append(
        f'<circle cx="{cx}" cy="{cy}" r="{raio_barriga}" fill="none" stroke="{ROXO}" stroke-width="{b["traco"]}"/>'
        f'<line x1="{x}" y1="{y1}" x2="{x}" y2="{y2}" stroke="{ROXO}" stroke-width="{b["traco"]}" stroke-linecap="round"/>'
    )
    partes.append("</svg>\n")
    return "".join(partes)


def main():
    imagens = {tamanho: imagem_sol(tamanho, com_fundo=True) for tamanho in TAMANHOS_ICO}
    imagens[256].save(
        os.path.join(PASTA, "beatriz_icone.ico"),
        format="ICO",
        sizes=[(t, t) for t in TAMANHOS_ICO],
        append_images=[imagens[t] for t in TAMANHOS_ICO if t != 256],
    )
    imagens[256].save(os.path.join(PASTA, "beatriz_icone_256.png"))
    with open(os.path.join(PASTA, "beatriz_icone.svg"), "w", encoding="utf-8") as arquivo:
        arquivo.write(svg())
    print(f"Ícones gerados em {PASTA}")


if __name__ == "__main__":
    main()
