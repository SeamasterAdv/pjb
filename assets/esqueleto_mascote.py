"""Esqueleto e pele (pesos) do modelo da mascote, montados pela geometria real.

Usado por colorir_mascote.py, com o modelo já virado para +Z, em pé em y = 0 e com +X do lado
esquerdo do bicho. Os ossos têm os mesmos nomes e a mesma hierarquia do modelo de código
(interface_web/mascote3d.js), todos sem rotação no descanso, para as mesmas poses animarem os dois.

- Patas: a linha central de cada pata (fatias horizontais) dá as juntas; quatro colunas abaixo da barriga.
- Corpo: linha central do tronco entre as patas; pescoço até a base da cabeça (atrás dos olhos).
- Cauda: oito juntas pelo caminho da raiz até a ponta (distância pela superfície).
- Pesos: cada vértice começa no osso mais perto entre os permitidos para a região dele (uma pata não
  pega osso de outra pata; a cauda só pega cauda e quadril); depois os pesos se espalham pelos vizinhos
  da malha, o que suaviza as dobras nas juntas.
- Marcadores: solas das patas, assento, barriga e peito (para encostar no chão) e os pontos das chamas
  do corpo (`chama_*`, com tamanho em `extras`).
"""
import numpy as np
from scipy import sparse
from scipy.cluster.vq import kmeans2
from scipy.sparse.csgraph import dijkstra

N_CAUDA = 8
LADOS = ("E", "D")  # E = esquerdo = +X


def _grafo(pos, tris):
    arestas = np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    comp = np.linalg.norm(pos[arestas[:, 0]] - pos[arestas[:, 1]], axis=1)
    n = len(pos)
    return sparse.coo_matrix((np.r_[comp, comp], (np.r_[arestas[:, 0], arestas[:, 1]], np.r_[arestas[:, 1], arestas[:, 0]])),
                             shape=(n, n)).tocsr()


def _linha_da_pata(pos, centro_xz, alturas):
    """(x, z) da linha central da pata em cada altura, subindo a partir da sola."""
    c = np.asarray(centro_xz, float)
    linha = []
    for y in alturas:
        fatia = pos[np.abs(pos[:, 1] - y) < 0.03]
        perto = fatia[np.linalg.norm(fatia[:, [0, 2]] - c, axis=1) < 0.13]
        if len(perto) >= 5:
            c = perto[:, [0, 2]].mean(0)
        linha.append(c.copy())
    return np.array(linha)


def _no_ponto(linha, alturas, y):
    return np.array([np.interp(y, alturas, linha[:, 0]), y, np.interp(y, alturas, linha[:, 1])])


def _distancia_ao_segmento(p, a, b):
    ab = b - a
    t = np.clip(((p - a) @ ab) / max(ab @ ab, 1e-12), 0, 1)
    return np.linalg.norm(p - (a + np.outer(t, ab)), axis=1)


def montar_esqueleto(pos, nv, tris, classe, marcos, classe_olho=(1, 2, 3)):
    N = len(pos)
    grafo = _grafo(pos, tris)
    juntas = {}  # nome -> posição no mundo (descanso)
    pais = {}

    def junta(nome, pai, p):
        juntas[nome] = np.asarray(p, float)
        pais[nome] = pai

    # ---------------------------------------------------------------- patas
    baixo = pos[:, 1] < 0.3
    centros, _ = kmeans2(pos[baixo][:, [0, 2]], 4, minit="++", seed=3)
    frente = centros[np.argsort(-centros[:, 1])[:2]]
    tras = centros[np.argsort(-centros[:, 1])[2:]]
    patas = {}
    for nome_par, par in (("frente", frente), ("tras", tras)):
        par = par[np.argsort(-par[:, 0])]  # +X primeiro (esquerda)
        for lado, c in zip(LADOS, par):
            patas[(nome_par, lado)] = c
    alturas = np.arange(0.02, 0.62, 0.03)
    linhas = {k: _linha_da_pata(pos, c, alturas) for k, c in patas.items()}

    # ---------------------------------------------------------------- tronco
    z_frente = np.mean([patas[("frente", l)][1] for l in LADOS])
    z_tras = np.mean([patas[("tras", l)][1] for l in LADOS])

    def centro_do_tronco(z):
        fatia = pos[(np.abs(pos[:, 2] - z) < 0.05) & (pos[:, 1] > 0.5) & (pos[:, 1] < 1.08)]
        return np.array([(fatia[:, 0].min() + fatia[:, 0].max()) / 2, (fatia[:, 1].min() + fatia[:, 1].max()) / 2, z])

    quadril = centro_do_tronco(z_tras + 0.04)
    meio = centro_do_tronco((z_tras + z_frente) / 2)
    peito = centro_do_tronco(z_frente - 0.12)
    junta("raiz", None, (0, 0, 0))
    junta("corpo", "raiz", quadril)
    junta("coluna", "corpo", meio)
    junta("peito", "coluna", peito)

    # ---------------------------------------------------------------- cabeça e pescoço
    olho = np.isin(classe, classe_olho)
    centro_olhos = pos[olho].mean(0)
    nariz = pos[marcos["nariz"]]
    frente_cabeca = nv[olho].mean(0)
    frente_cabeca[1] = 0
    frente_cabeca /= np.linalg.norm(frente_cabeca)
    centro_cabeca = centro_olhos - 0.16 * frente_cabeca + np.array([0, -0.02, 0])
    base_cabeca = centro_cabeca + np.array([0, -0.17, 0]) - 0.05 * frente_cabeca
    junta("pescoco", "peito", peito + 0.4 * (base_cabeca - peito) + np.array([0, 0.05, 0]))
    junta("pescoco2", "pescoco", peito + 0.72 * (base_cabeca - peito) + np.array([0, 0.03, 0]))
    junta("cabeca", "pescoco2", base_cabeca)
    junta("mandibula", "cabeca", centro_cabeca + np.array([0, -0.12, 0]) - 0.04 * frente_cabeca)
    pontas_orelhas = {}
    esquerda_cabeca = np.cross((0, 1, 0), frente_cabeca)  # lado esquerdo da cabeça (ela está virada)
    for i in marcos["orelhas"]:
        ponta = pos[i]
        lado = "E" if esquerda_cabeca @ (ponta - centro_cabeca) > 0 else "D"
        para_dentro = (centro_cabeca - ponta) / np.linalg.norm(centro_cabeca - ponta)
        pontas_orelhas[lado] = ponta
        junta("orelha" + lado, "cabeca", ponta + 0.28 * para_dentro)

    # ---------------------------------------------------------------- patas: juntas
    solas = {}
    for lado in LADOS:
        lf = linhas[("frente", lado)]
        junta("ombro" + lado, "peito", _no_ponto(lf, alturas, 0.5) + np.array([0, 0.18, 0]))
        junta("cotovelo" + lado, "ombro" + lado, _no_ponto(lf, alturas, 0.41))
        junta("pulso" + lado, "cotovelo" + lado, _no_ponto(lf, alturas, 0.12))
        solas["sola" + lado] = np.array([lf[0, 0], 0.0, lf[0, 1]])
        lt = linhas[("tras", lado)]
        junta("coxa" + lado, "corpo", _no_ponto(lt, alturas, 0.59) + np.array([0, 0.15, 0]))
        junta("joelho" + lado, "coxa" + lado, _no_ponto(lt, alturas, 0.44))
        junta("jarrete" + lado, "joelho" + lado, _no_ponto(lt, alturas, 0.23))
        junta("pataT" + lado, "jarrete" + lado, _no_ponto(lt, alturas, 0.08))
        solas["solaT" + lado] = np.array([lt[0, 0], 0.0, lt[0, 1]])

    # ---------------------------------------------------------------- cauda
    ponta_cauda, raiz_cauda = marcos["ponta_cauda"], marcos["raiz_cauda"]
    d_ponta = dijkstra(grafo, indices=ponta_cauda)
    comp_cauda = d_ponta[raiz_cauda]
    na_cauda = d_ponta < comp_cauda * 1.02
    t = 1 - d_ponta / comp_cauda
    # Caminho: da raiz (centro do anel de vértices perto dela) pelos centros das faixas de distância até a
    # ponta. As faixas do começo misturam os tufos de baixo da cauda, então o caminho só usa as de t >= 0,15.
    raiz_p = pos[na_cauda & (np.linalg.norm(pos - pos[raiz_cauda], axis=1) < 0.15)].mean(0)
    caminho = [raiz_p]
    for tk in np.linspace(0.15, 0.95, 17):
        faixa = na_cauda & (np.abs(t - tk) < 0.03)
        if faixa.sum() > 20:
            caminho.append(pos[faixa].mean(0))
    caminho.append(pos[ponta_cauda])
    caminho = np.array(caminho)
    s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(caminho, axis=0), axis=1))]
    pontos_cauda = [np.array([np.interp(a, s, caminho[:, k]) for k in range(3)]) for a in np.linspace(0, 0.9 * s[-1], N_CAUDA)]
    for k, p in enumerate(pontos_cauda):
        junta("cauda" + str(k), "corpo" if k == 0 else "cauda" + str(k - 1), p)
    ponta_cauda_p = pos[ponta_cauda]

    # ---------------------------------------------------------------- pesos
    nomes = list(juntas)
    indice = {n: i for i, n in enumerate(nomes)}
    # Fim de cada osso (para a distância ao segmento): o filho principal ou um ponto da anatomia.
    fim = {
        "corpo": juntas["coluna"], "coluna": juntas["peito"], "peito": juntas["pescoco"], "pescoco": juntas["pescoco2"],
        "pescoco2": juntas["cabeca"], "cabeca": nariz, "mandibula": nariz + np.array([0, -0.08, 0]),
        "cauda" + str(N_CAUDA - 1): ponta_cauda_p,
    }
    for lado in LADOS:
        fim.update({"ombro" + lado: juntas["cotovelo" + lado], "cotovelo" + lado: juntas["pulso" + lado],
                    "pulso" + lado: solas["sola" + lado] + np.array([0, 0.03, 0.06]),
                    "coxa" + lado: juntas["joelho" + lado], "joelho" + lado: juntas["jarrete" + lado],
                    "jarrete" + lado: juntas["pataT" + lado], "pataT" + lado: solas["solaT" + lado] + np.array([0, 0.03, 0.06]),
                    "orelha" + lado: pontas_orelhas[lado]})
    for k in range(N_CAUDA - 1):
        fim["cauda" + str(k)] = juntas["cauda" + str(k + 1)]
    # O quadril vai da traseira (atrás do assento) até a coluna; a cabeça cobre o crânio inteiro.
    inicio = {n: juntas[n] for n in nomes}
    inicio["corpo"] = juntas["corpo"] + np.array([0, 0, -0.18])
    inicio["cabeca"] = centro_cabeca - 0.12 * frente_cabeca

    # Regiões: cada vértice só pode pegar os ossos da região dele.
    regiao = np.full(N, "tronco", dtype=object)
    for lado in LADOS:
        for par, topo in (("frente", 0.56), ("tras", 0.62)):
            linha = linhas[(par, lado)]
            cx = np.interp(pos[:, 1], alturas, linha[:, 0])
            cz = np.interp(pos[:, 1], alturas, linha[:, 1])
            perto = np.hypot(pos[:, 0] - cx, pos[:, 2] - cz) < 0.16
            regiao[(pos[:, 1] < topo) & perto & (regiao == "tronco")] = par + lado
    regiao[na_cauda] = "cauda"
    cabeca_v = (np.linalg.norm(pos - centro_cabeca, axis=1) < 0.36) & (pos[:, 1] > juntas["cabeca"][1] - 0.05)
    regiao[cabeca_v & (regiao == "tronco")] = "cabeca"
    for lado in LADOS:
        orelha_v = (dijkstra(grafo, indices=int(np.argmin(np.linalg.norm(pos - pontas_orelhas[lado], axis=1))), limit=0.4) < 0.3)
        regiao[orelha_v & np.isin(regiao, ["cabeca", "tronco"])] = "orelha" + lado
    permitidos = {
        "tronco": ["corpo", "coluna", "peito", "pescoco", "pescoco2"],
        "cabeca": ["cabeca", "pescoco2"],
        "cauda": ["corpo"] + ["cauda" + str(k) for k in range(N_CAUDA)],
    }
    for lado in LADOS:
        permitidos["frente" + lado] = ["peito", "ombro" + lado, "cotovelo" + lado, "pulso" + lado]
        permitidos["tras" + lado] = ["corpo", "coxa" + lado, "joelho" + lado, "jarrete" + lado, "pataT" + lado]
        permitidos["orelha" + lado] = ["cabeca", "orelha" + lado]

    peso = np.zeros((N, len(nomes)), np.float32)
    for reg, ossos in permitidos.items():
        sel = np.nonzero(regiao == reg)[0]
        if not len(sel):
            continue
        dists = np.stack([_distancia_ao_segmento(pos[sel], inicio[o], fim[o]) for o in ossos], axis=1)
        melhor = np.argmin(dists, axis=1)
        peso[sel, [indice[ossos[k]] for k in melhor]] = 1.0

    # Espalha pelos vizinhos (suaviza as dobras nas juntas).
    vizinhos = (grafo > 0).astype(np.float32)
    grau = np.asarray(vizinhos.sum(1)).ravel()
    for _ in range(25):
        peso = 0.5 * peso + 0.5 * (vizinhos @ peso) / grau[:, None]
    ordem = np.argsort(-peso, axis=1)[:, :4]
    w4 = np.take_along_axis(peso, ordem, axis=1)
    w4[w4 < 0.02] = 0
    w4 /= np.maximum(w4.sum(1, keepdims=True), 1e-9)
    regioes = {r: int((regiao == r).sum()) for r in np.unique(regiao)}

    # ---------------------------------------------------------------- marcadores e chamas
    extras = []
    for lado in LADOS:
        extras.append({"nome": "sola" + lado, "pai": "pulso" + lado, "pos": solas["sola" + lado]})
        extras.append({"nome": "solaT" + lado, "pai": "pataT" + lado, "pos": solas["solaT" + lado]})
    y_barriga = lambda z: pos[(np.abs(pos[:, 2] - z) < 0.04) & (pos[:, 1] > 0.4) & (regiao == "tronco"), 1].min()  # noqa: E731
    extras.append({"nome": "assento", "pai": "corpo", "pos": np.array([quadril[0], y_barriga(z_tras - 0.05) + 0.02, z_tras - 0.12])})
    extras.append({"nome": "barriga", "pai": "coluna", "pos": np.array([meio[0], y_barriga(meio[2]), meio[2]])})
    extras.append({"nome": "peitoBaixo", "pai": "peito", "pos": np.array([peito[0], y_barriga(peito[2]) - 0.03, peito[2] + 0.06])})

    def na_superficie(perto_de, direcao, raio=0.3):
        """Vértice mais saliente na direção dada, perto de um ponto (um pouco para dentro do pelo)."""
        d = np.asarray(direcao, float) / np.linalg.norm(direcao)
        sel = np.nonzero((np.linalg.norm(pos - perto_de, axis=1) < raio) & (regiao == "tronco"))[0]
        i = sel[np.argmax((pos[sel] - perto_de) @ d)]
        return pos[i] - 0.03 * nv[i]

    lado_corpo = np.cross((0, 1, 0), (peito - quadril) / np.linalg.norm(peito - quadril))
    for lado, s in (("E", 1), ("D", -1)):
        extras.append({"nome": "chama_ombro" + lado, "pai": "peito", "pos": na_superficie(peito, 0.55 * s * lado_corpo + np.array([0, 0.85, 0])),
                       "extras": {"larg": 0.5, "alt": 0.85, "adiante": 0.12, "taxa": 28, "tam": 0.2}})
        extras.append({"nome": "chama_peito" + lado, "pai": "peito",
                       "pos": na_superficie(peito, 0.6 * s * lado_corpo + np.array([0, -0.1, 0.8])),
                       "extras": {"larg": 0.34, "alt": 0.6, "adiante": 0.1}})
        extras.append({"nome": "chama_pulso" + lado, "pai": "pulso" + lado, "pos": juntas["pulso" + lado] + np.array([0, 0.03, 0]),
                       "extras": {"larg": 0.32, "alt": 0.45, "adiante": 0.12, "taxa": 12, "tam": 0.13}})
        extras.append({"nome": "chama_cotovelo" + lado, "pai": "cotovelo" + lado, "pos": 0.5 * (juntas["cotovelo" + lado] + juntas["pulso" + lado]),
                       "extras": {"larg": 0.26, "alt": 0.36, "adiante": 0.1}})
        extras.append({"nome": "chama_pataT" + lado, "pai": "pataT" + lado, "pos": juntas["pataT" + lado] + np.array([0, 0.05, 0]),
                       "extras": {"larg": 0.3, "alt": 0.4, "adiante": 0.1, "taxa": 12, "tam": 0.13}})
    extras.append({"nome": "chama_costas", "pai": "peito", "pos": na_superficie(peito + np.array([0, 0, -0.1]), (0, 1, -0.2)),
                   "extras": {"larg": 0.55, "alt": 0.95, "adiante": 0.02, "taxa": 30, "tam": 0.22}})
    extras.append({"nome": "chama_dorso", "pai": "coluna", "pos": na_superficie(meio, (0, 1, 0)),
                   "extras": {"larg": 0.42, "alt": 0.55, "adiante": 0.02, "taxa": 18, "tam": 0.16}})

    ossos = [{"nome": n, "pai": pais[n], "pos": juntas[n]} for n in nomes]
    return {"ossos": ossos, "indices": ordem.astype(np.uint8), "pesos": w4.astype(np.float32), "extras": extras, "regioes": regioes}
