"""Pinta o modelo 3D da mascote a partir da imagem de referência.

O modelo vem do Meshy (Imagem → 3D, etapa "preview"): só a forma, sem cor, sem textura e sem
esqueleto. Este script:
1. separa a imagem de referência em pelo (escuro), chamas (roxo) e olhos (amarelo);
2. acha a câmera em que a silhueta do modelo coincide com a da imagem (escala, posição, giro e perspectiva);
3. projeta a imagem no lado visível do modelo: olhos (íris, pupila e brilho) e gema vêm da imagem;
4. pela forma da malha: chifres (cone a partir da ponta), nariz (ponta do focinho), lado de dentro das
   orelhas (roxo) e a cauda em chamas (degradê da raiz até a ponta). O resto é pelo preto; as chamas do
   corpo não são pintadas, porque serão animadas no laboratório;
5. vira o modelo para +Z, em pé em y = 0 e centrado no plano de simetria, e grava um .glb com um material
   com nome para cada parte. Olhos e gema levam em `extras` o centro, os eixos e os raios, que o
   laboratório usa para desenhá-los por shader (interface_web/mascote3d.js, `vestirModelo`). A cauda leva
   o atributo _CHAMA (0 onde o fogo começa, 1 na ponta);
6. monta o esqueleto e a pele (assets/esqueleto_mascote.py): os mesmos ossos do modelo de código, para as
   animações dele moverem este modelo, mais os marcadores de chão e os pontos das chamas do corpo.

Os pontos marcados na imagem (pontas e bases dos chifres, gema, raiz da cauda) valem para este modelo e
esta imagem. Se trocar um deles, rode com --conferir e veja as imagens geradas.

Uso:
    .venv312\\Scripts\\python.exe assets\\colorir_mascote.py [--conferir PASTA]
"""
import argparse
import json
import os
import struct
import sys

import cv2
import numpy as np
from scipy import sparse
from scipy.optimize import minimize
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from esqueleto_mascote import montar_esqueleto  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRADA = os.path.join(RAIZ, "interface_web", "modelos", "mascote_sem_cor.glb")
REFERENCIA = os.path.join(RAIZ, "assets", "mascote_referencia.jpg")
SAIDA = os.path.join(RAIZ, "interface_web", "modelos", "mascote.glb")

ALTURA_RENDER = 1244  # a imagem tem o render em cima e a tela do Blender embaixo
# Pixels marcados na imagem para este modelo: (ponta, base) dos chifres de trás e da frente (no modelo,
# o de trás fica mais baixo que na imagem), os cantos da gema e onde a cauda sai do quadril.
CHIFRES = (((358, 227), (382, 312)), ((496, 217), (490, 322)))
GEMA = [(421, 305), (434, 338), (408, 379), (392, 338)]
RAIZ_DA_CAUDA = (945, 700)

CLASSES = ["pelo", "olho", "pupila", "brilho", "gema", "chifre", "nariz", "roxo", "chama"]
C = {nome: i for i, nome in enumerate(CLASSES)}
OLHO = [C["olho"], C["pupila"], C["brilho"]]
DESCONHECIDO = -1

# Cores (sRGB) de cada parte; no .glb vão em linear, como pede o formato.
MATERIAIS = {
    "pelo": {"cor": "#1c1622", "aspereza": 0.85},
    "olhos": {"cor": "#ffffff", "aspereza": 0.2, "por_vertice": True},
    "gema": {"cor": "#a35cff", "aspereza": 0.15, "brilho": ("#a35cff", 0.8)},
    "chifres": {"cor": "#2a1b3a", "aspereza": 0.35},
    "nariz": {"cor": "#0b0710", "aspereza": 0.25},
    "roxo": {"cor": "#3a1f5e", "aspereza": 0.85},
    "chama": {"cor": "#ffffff", "aspereza": 0.6, "por_vertice": True, "brilho": ("#b04dff", 0.35)},
}
CORES_OLHO = {C["olho"]: "#ffc81a", C["pupila"]: "#0b0610", C["brilho"]: "#fff6e0", C["pelo"]: "#1c1622"}
DEGRADE_FOGO = [(0.0, "#1c1622"), (0.2, "#3a1670"), (0.45, "#6a2cff"), (0.75, "#b04dff"), (1.0, "#ff9cf2")]


# ================================================================== .glb
def ler_glb(caminho):
    with open(caminho, "rb") as f:
        dados = f.read()
    magia, _, total = struct.unpack_from("<III", dados, 0)
    if magia != 0x46546C67:
        raise ValueError(f"{caminho} não é um .glb")
    pos, doc, binario = 12, None, b""
    while pos < total:
        tamanho, tipo = struct.unpack_from("<II", dados, pos)
        pedaco = dados[pos + 8: pos + 8 + tamanho]
        if tipo == 0x4E4F534A:
            doc = json.loads(pedaco)
        elif tipo == 0x004E4942:
            binario = pedaco
        pos += 8 + tamanho
    return doc, binario


def carregar_malha(caminho):
    """Posições e triângulos da primeira primitiva (o Meshy grava uma malha só)."""
    doc, binario = ler_glb(caminho)
    tipos = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
    componentes = {"SCALAR": 1, "VEC3": 3}

    def acessor(i):
        a = doc["accessors"][i]
        v = doc["bufferViews"][a["bufferView"]]
        n = componentes[a["type"]]
        inicio = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        arr = np.frombuffer(binario, dtype=tipos[a["componentType"]], count=a["count"] * n, offset=inicio)
        return arr.reshape(a["count"], n) if n > 1 else arr

    prim = doc["meshes"][0]["primitives"][0]
    return acessor(prim["attributes"]["POSITION"]).astype(np.float64), acessor(prim["indices"]).astype(np.int64).reshape(-1, 3)


def gravar_glb(caminho, pos, normais, tris, parte, cor_v, calor, extras_materiais, extras_malha, esqueleto=None):
    binario = bytearray()
    vistas, acessores, materiais, primitivas = [], [], [], []

    def anexar(arr, tipo_comp, tipo, alvo, minmax=False):
        while len(binario) % 4:
            binario.append(0)
        dados = np.ascontiguousarray(arr).tobytes()
        vistas.append({"buffer": 0, "byteOffset": len(binario), "byteLength": len(dados)})
        if alvo:
            vistas[-1]["target"] = alvo
        binario.extend(dados)
        acc = {"bufferView": len(vistas) - 1, "componentType": tipo_comp, "count": int(arr.shape[0]), "type": tipo}
        if minmax:
            acc["min"], acc["max"] = arr.min(0).astype(float).tolist(), arr.max(0).astype(float).tolist()
        acessores.append(acc)
        return len(acessores) - 1

    for nome, cfg in MATERIAIS.items():
        sel = np.nonzero(parte == nome)[0]
        if not len(sel):
            continue
        usados, novo = np.unique(tris[sel], return_inverse=True)
        atributos = {
            "POSITION": anexar(pos[usados].astype(np.float32), 5126, "VEC3", 34962, minmax=True),
            "NORMAL": anexar(normais[usados].astype(np.float32), 5126, "VEC3", 34962),
        }
        if cfg.get("por_vertice"):
            atributos["COLOR_0"] = anexar(linear(cor_v[usados]).astype(np.float32), 5126, "VEC3", 34962)
        if nome == "chama":
            atributos["_CHAMA"] = anexar(calor[usados].astype(np.float32), 5126, "SCALAR", 34962)
        if esqueleto:
            atributos["JOINTS_0"] = anexar(esqueleto["indices"][usados], 5121, "VEC4", 34962)
            atributos["WEIGHTS_0"] = anexar(esqueleto["pesos"][usados], 5126, "VEC4", 34962)
        indices = anexar(novo.reshape(-1).astype(np.uint32), 5125, "SCALAR", 34963)
        mat = {"name": nome, "pbrMetallicRoughness": {"baseColorFactor": linear(hex_rgb(cfg["cor"])).tolist() + [1.0],
                                                     "metallicFactor": 0.0, "roughnessFactor": cfg["aspereza"]}}
        if "brilho" in cfg:
            mat["emissiveFactor"] = (linear(hex_rgb(cfg["brilho"][0])) * cfg["brilho"][1]).tolist()
        if nome in extras_materiais:
            mat["extras"] = extras_materiais[nome]
        materiais.append(mat)
        primitivas.append({"attributes": atributos, "indices": indices, "material": len(materiais) - 1})
        print(f"  {nome}: {len(sel)} triângulos")

    nos = [{"mesh": 0, "name": "mascote"}]
    doc = {
        "asset": {"version": "2.0", "generator": "Beatriz - assets/colorir_mascote.py"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nos,
        "meshes": [{"name": "mascote", "primitives": primitivas, "extras": extras_malha}],
        "materials": materiais, "accessors": acessores, "bufferViews": vistas,
    }
    if esqueleto:
        # Juntas sem rotação no descanso (translação = posição em relação ao pai); marcadores e pontos
        # das chamas são filhos das juntas. A matriz inversa de cada junta é só a translação de volta.
        nos[0]["skin"] = 0
        indice = {}
        posicao = {o["nome"]: np.asarray(o["pos"], float) for o in esqueleto["ossos"]}
        for o in esqueleto["ossos"]:
            local = posicao[o["nome"]] - (posicao[o["pai"]] if o["pai"] else 0)
            indice[o["nome"]] = len(nos)
            nos.append({"name": o["nome"], "translation": local.round(6).tolist(), "children": []})
            if o["pai"]:
                nos[indice[o["pai"]]]["children"].append(indice[o["nome"]])
        for e in esqueleto["extras"]:
            no = {"name": e["nome"], "translation": (np.asarray(e["pos"], float) - posicao[e["pai"]]).round(6).tolist()}
            if e.get("extras"):
                no["extras"] = e["extras"]
            nos[indice[e["pai"]]]["children"].append(len(nos))
            nos.append(no)
        for no in nos:
            if "children" in no and not no["children"]:
                del no["children"]
        inversas = np.zeros((len(esqueleto["ossos"]), 16), np.float32)
        for k, o in enumerate(esqueleto["ossos"]):
            m = np.eye(4)
            m[:3, 3] = -posicao[o["nome"]]
            inversas[k] = m.T.ravel()  # o glTF guarda as matrizes por coluna
        raiz = indice[esqueleto["ossos"][0]["nome"]]
        doc["scenes"][0]["nodes"].append(raiz)
        doc["skins"] = [{"name": "esqueleto", "joints": [indice[o["nome"]] for o in esqueleto["ossos"]],
                         "inverseBindMatrices": anexar(inversas, 5126, "MAT4", None), "skeleton": raiz}]
    doc["buffers"] = [{"byteLength": len(binario)}]
    texto = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    texto += b" " * ((4 - len(texto) % 4) % 4)
    while len(binario) % 4:
        binario.append(0)
    with open(caminho, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(texto) + 8 + len(binario)))
        f.write(struct.pack("<II", len(texto), 0x4E4F534A) + texto)
        f.write(struct.pack("<II", len(binario), 0x004E4942) + bytes(binario))


# ================================================================== geometria e desenho
def hex_rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i: i + 2], 16) for i in (0, 2, 4)], float) / 255.0


def linear(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def normais_dos_vertices(pos, tris):
    a, b, c = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    nf = np.cross(b - a, c - a)  # ponderada pela área
    nv = np.zeros_like(pos)
    for k in range(3):
        np.add.at(nv, tris[:, k], nf)
    return nv / np.maximum(np.linalg.norm(nv, axis=1, keepdims=True), 1e-12)


def normais_das_faces(pos, tris):
    a, b, c = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    nf = np.cross(b - a, c - a)
    return nf / np.maximum(np.linalg.norm(nf, axis=1, keepdims=True), 1e-12)


def rot_x(graus):
    t = np.radians(graus)
    return np.array([[1, 0, 0], [0, np.cos(t), -np.sin(t)], [0, np.sin(t), np.cos(t)]])


def rot_y(graus):
    t = np.radians(graus)
    return np.array([[np.cos(t), 0, np.sin(t)], [0, 1, 0], [-np.sin(t), 0, np.cos(t)]])


def rot_z(graus):
    t = np.radians(graus)
    return np.array([[np.cos(t), -np.sin(t), 0], [np.sin(t), np.cos(t), 0], [0, 0, 1]])


def camera_olhando(para_camera, cima=(0, 1, 0)):
    """Linhas: direita, cima e frente da câmera, que olha na direção oposta a `para_camera`."""
    f = np.asarray(para_camera, float)
    f /= np.linalg.norm(f)
    d = np.cross(cima, f)
    d /= np.linalg.norm(d)
    return np.stack([d, np.cross(f, d), f])


def rasterizar(proj, tris, largura, altura, sub=4):
    """Índice do triângulo visível em cada pixel (-1 = fundo): pinta do fundo para a frente."""
    alvo = np.zeros((altura, largura), dtype=np.int32)
    pts = np.round(proj[:, :2] * sub).astype(np.int32)
    for t in np.argsort(proj[tris, 2].mean(axis=1)):
        cv2.fillConvexPoly(alvo, pts[tris[t]], int(t) + 1, lineType=cv2.LINE_8, shift=int(np.log2(sub)))
    return alvo - 1


def sombrear(ids, normais_face, R, cores_face=None):
    img = np.empty(ids.shape + (3,))
    img[:] = np.array([120, 118, 128]) / 255.0
    m = ids >= 0
    n = normais_face[ids[m]] @ R.T
    luz = np.array([0.4, 0.6, 0.7]) / np.linalg.norm([0.4, 0.6, 0.7])
    difusa = np.clip(n @ luz, 0, 1)
    base = np.full((m.sum(), 3), 0.62) if cores_face is None else cores_face[ids[m]]
    img[m] = np.clip(base * (0.28 + 0.72 * difusa[:, None]) + 0.25 * np.clip(1 - np.abs(n[:, 2]), 0, 1)[:, None] ** 3 * base, 0, 1)
    return (img * 255).astype(np.uint8)


def renderizar(pos, tris, R, tamanho, cores_face=None, escala=None, centro=None):
    centro = (pos.min(0) + pos.max(0)) / 2 if centro is None else centro
    p = (pos - centro) @ R.T
    escala = 0.9 * min(tamanho) / (2 * np.abs(p[:, :2]).max()) if escala is None else escala
    proj = np.stack([tamanho[0] / 2 + p[:, 0] * escala, tamanho[1] / 2 - p[:, 1] * escala, p[:, 2]], axis=1)
    ids = rasterizar(proj, tris, *tamanho)
    return sombrear(ids, normais_das_faces(pos, tris), R, cores_face)


def salvar_rgb(img, caminho):
    cv2.imwrite(caminho, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))


# ================================================================== 1. imagem de referência
def separar_referencia(ref):
    hsv = cv2.cvtColor(ref, cv2.COLOR_BGR2HSV)
    h, s, v = (hsv[..., k].astype(int) for k in range(3))
    abrir = lambda m: cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0  # noqa: E731
    olhos = abrir((h >= 15) & (h <= 35) & (s > 120) & (v > 150))
    chamas = abrir((h >= 125) & (h <= 165) & (s > 90) & (v > 90))
    pelo = abrir((v < 85) & ~olhos)
    corpo = cv2.morphologyEx((pelo | olhos).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8)) > 0
    return {"olhos": olhos, "corpo": corpo, "tudo": corpo | chamas}


# ================================================================== 2. câmera
class Camera:
    """giro (y), inclinação (x), rolagem (z) em graus, escala, deslocamento em pixels e distância
    (perspectiva; 0 = ortográfica), com o modelo centrado em `centro`."""

    def __init__(self, params, centro, largura, altura):
        self.giro, self.inclinacao, self.rolagem, log_escala, self.tx, self.ty, self.dist = params
        self.escala = float(np.exp(log_escala))
        self.centro, self.largura, self.altura = centro, largura, altura
        self.R = rot_x(self.inclinacao) @ rot_z(self.rolagem) @ camera_olhando((0, 0, 1)) @ rot_y(self.giro)

    def projetar(self, pontos, fator=1):
        p = (pontos - self.centro) @ self.R.T
        if self.dist > 0.5:
            d = max(self.dist, 2.5)
            f = d / np.maximum(d - p[:, 2], 1e-3)
            p[:, 0] *= f
            p[:, 1] *= f
        x = (self.largura / 2 + self.tx + p[:, 0] * self.escala) / fator
        y = (self.altura / 2 + self.ty - p[:, 1] * self.escala) / fator
        return np.stack([x, y, p[:, 2]], axis=1)


def alinhar(pos, tris, mascaras):
    altura, largura = mascaras["corpo"].shape
    fator = 4
    reduz = lambda m: cv2.resize(m.astype(np.uint8), (largura // fator, altura // fator), interpolation=cv2.INTER_AREA) > 0  # noqa: E731
    corpo, tudo = reduz(mascaras["corpo"]), reduz(mascaras["tudo"])
    centro = (pos.min(0) + pos.max(0)) / 2
    amostra = np.vstack([pos, pos[tris].mean(axis=1)])

    def pontuacao(params):
        p = Camera(params, centro, largura, altura).projetar(amostra, fator)
        h, w = corpo.shape
        xi, yi = np.round(p[:, 0]).astype(int), np.round(p[:, 1]).astype(int)
        ok = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
        img = np.zeros((h, w), np.uint8)
        img[yi[ok], xi[ok]] = 1
        s = cv2.morphologyEx(img, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)) > 0
        # Cobrir o pelo, sem vazar para fora do bicho (com as chamas) nem sair da imagem.
        cobre = (s & corpo).sum() / corpo.sum()
        vaza = (s & ~tudo).sum() / (s.sum() + 1e-6)
        iou = (s & tudo).sum() / ((s | tudo).sum() + 1e-6)
        return -(cobre + 0.5 * iou - 1.5 * vaza - 3 * (~ok).sum() / len(xi))

    ys, xs = np.nonzero(mascaras["tudo"])
    p0 = (pos - centro) @ camera_olhando((0, 0, 1)).T
    escala0 = (ys.max() - ys.min()) / (p0[:, 1].max() - p0[:, 1].min())
    tx0 = (xs.min() + xs.max()) / 2 - largura / 2 - (p0[:, 0].max() + p0[:, 0].min()) / 2 * escala0
    ty0 = (ys.min() + ys.max()) / 2 - altura / 2 + (p0[:, 1].max() + p0[:, 1].min()) / 2 * escala0
    chutes = [np.array([g, i, 0, np.log(escala0), tx0, ty0, d], float) for g in (-12, -6, 0, 6, 12) for i in (-8, 0, 8) for d in (0, 4, 6)]
    melhor = min(chutes, key=pontuacao)
    res = minimize(pontuacao, melhor, method="Powell",
                   options={"xtol": 0.05, "ftol": 1e-4, "maxiter": 4000, "direc": np.diag([3, 3, 3, 0.03, 20, 20, 1.0])})
    params = res.x.copy()
    params[6] = max(params[6], 0)
    print(f"câmera: giro {params[0]:.1f}°, inclinação {params[1]:.1f}°, escala {np.exp(params[3]):.0f}, distância {params[6]:.2f} (nota {-res.fun:.3f})")
    return Camera(params, centro, largura, altura)


# ================================================================== 3 e 4. pintura
def amostrar(img, x, y):
    x = np.clip(x, 0, img.shape[1] - 1.001)
    y = np.clip(y, 0, img.shape[0] - 1.001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    f = img.astype(np.float64)
    topo = f[y0, x0] * (1 - fx) + f[y0, x0 + 1] * fx
    baixo = f[y0 + 1, x0] * (1 - fx) + f[y0 + 1, x0 + 1] * fx
    return np.round(topo * (1 - fy) + baixo * fy).astype(np.uint8)


def pintar(pos, tris, nv, cam, ref, mascaras):
    N = len(pos)
    H, W = mascaras["corpo"].shape
    proj = cam.projetar(pos)
    ids = rasterizar(proj, tris, W, H)
    zbuf = np.where(ids >= 0, proj[tris, 2].mean(axis=1)[np.clip(ids, 0, None)], -1e9).astype(np.float32)
    xi = np.clip(np.round(proj[:, 0]).astype(int), 0, W - 1)
    yi = np.clip(np.round(proj[:, 1]).astype(int), 0, H - 1)
    zmax = cv2.dilate(zbuf, np.ones((3, 3), np.uint8))
    visivel = (proj[:, 2] >= zmax[yi, xi] - 0.015) & ((nv @ cam.R.T)[:, 2] > 0.2)

    cor_bgr = amostrar(ref, proj[:, 0], proj[:, 1])
    hsv = cv2.cvtColor(cor_bgr.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(int)
    h, s, v = hsv[:, 0], hsv[:, 1], hsv[:, 2]
    # Olho inteiro = casco convexo de cada mancha amarela (a pupila escura fica dentro).
    olho_cheio = np.zeros((H, W), np.uint8)
    n_comp, rotulos, stats, _ = cv2.connectedComponentsWithStats(mascaras["olhos"].astype(np.uint8))
    for i in range(1, n_comp):
        if stats[i, cv2.CC_STAT_AREA] >= 300:
            pts = np.column_stack(np.nonzero(rotulos == i))[:, ::-1].astype(np.int32)
            cv2.fillConvexPoly(olho_cheio, cv2.convexHull(pts), 1)
    dentro_olho = cv2.dilate(olho_cheio, np.ones((5, 5), np.uint8))[yi, xi] > 0
    gema_img = np.zeros((H, W), np.uint8)
    cv2.fillPoly(gema_img, [np.array(GEMA, np.int32)], 1)
    na_gema = cv2.dilate(gema_img, np.ones((5, 5), np.uint8))[yi, xi] > 0

    roxo = (h >= 118) & (h <= 165) & (s > 90) & (v > 30)
    amarelo = (h >= 12) & (h <= 38) & (s > 110) & (v > 140)
    escuro = v < 85
    claro_neutro = (v > 200) & (s < 90)
    classe = np.full(N, DESCONHECIDO)
    classe[visivel & dentro_olho & escuro] = C["pupila"]
    classe[visivel & dentro_olho & amarelo] = C["olho"]
    classe[visivel & dentro_olho & claro_neutro] = C["brilho"]
    classe[visivel & dentro_olho & (classe == DESCONHECIDO)] = C["olho"]
    classe[visivel & na_gema & (roxo | (v > 90))] = C["gema"]

    arestas = np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    comprimento = np.linalg.norm(pos[arestas[:, 0]] - pos[arestas[:, 1]], axis=1)
    grafo = sparse.coo_matrix((np.r_[comprimento, comprimento], (np.r_[arestas[:, 0], arestas[:, 1]], np.r_[arestas[:, 1], arestas[:, 0]])),
                              shape=(N, N)).tocsr()

    def mais_perto_na_imagem(x, y):
        candidatos = np.nonzero(visivel)[0]
        return candidatos[np.argmin((proj[candidatos, 0] - x) ** 2 + (proj[candidatos, 1] - y) ** 2)]

    # Chifres: cone a partir da ponta, ao longo do eixo do chifre (os anéis enganam a distância pela
    # superfície). O raio fica limitado ao maior raio da parte de cima, e a cabeça em volta da base,
    # que fica de frente para a ponta, sai fora.
    for ponta, base in CHIFRES:
        i_ponta = mais_perto_na_imagem(*ponta)
        comp = np.hypot(ponta[0] - base[0], ponta[1] - base[1]) / cam.escala
        dist = dijkstra(grafo, indices=i_ponta, limit=comp * 2.2)
        eixo = pos[dist < 0.55 * comp].mean(0) - pos[i_ponta]
        eixo /= np.linalg.norm(eixo)
        rel = pos - pos[i_ponta]
        ao_longo = rel @ eixo
        radial = np.linalg.norm(rel - np.outer(ao_longo, eixo), axis=1)
        meio = (dist < 1.5 * comp) & (ao_longo > 0.35 * comp) & (ao_longo < 0.6 * comp)
        abertura = np.percentile(radial[meio], 95) / np.median(ao_longo[meio]) if meio.sum() > 10 else 0.6
        cima = (dist < 1.5 * comp) & (ao_longo > 0.05 * comp) & (ao_longo < 0.8 * comp)
        raio_max = 1.15 * np.percentile(radial[cima], 97)
        chifre = ((dist < comp * 2.2) & (ao_longo > -0.01) & (ao_longo < 1.15 * comp)
                  & (radial <= np.minimum(abertura * ao_longo * 1.3 + 0.012, raio_max))
                  & ~(((nv @ -eixo) > 0.55) & (ao_longo > 0.35 * comp)))
        classe[chifre] = C["chifre"]

    # Frente da cabeça: para onde os olhos olham.
    olho_v = np.isin(classe, OLHO)
    centro_olhos = pos[olho_v].mean(0)
    frente = nv[olho_v].mean(0)
    frente[1] = 0
    frente /= np.linalg.norm(frente)
    lado = np.cross((0, 1, 0), frente)
    rel = pos - centro_olhos
    # Nariz: disco em volta da ponta do focinho (o ponto da cabeça mais à frente).
    candidatos = np.nonzero((np.linalg.norm(rel, axis=1) < 0.4) & (rel[:, 1] < 0.0))[0]
    i_nariz = candidatos[np.argmax(rel[candidatos] @ frente)]
    classe[dijkstra(grafo, indices=i_nariz, limit=0.1) < 0.065] = C["nariz"]
    # Orelhas: a ponta é o ponto mais alto de cada lado da cabeça; dentro = a face virada para frente.
    cabeca = (np.linalg.norm(rel, axis=1) < 0.55) & (rel[:, 1] > 0.05) & (classe != C["chifre"])
    pontas_orelhas = []
    for sinal in (1, -1):
        lado_cab = np.nonzero(cabeca & ((rel @ lado) * sinal > 0.06))[0]
        i_topo = lado_cab[np.argmax(pos[lado_cab, 1])]
        pontas_orelhas.append(int(i_topo))
        orelha = (dijkstra(grafo, indices=i_topo, limit=0.5) < 0.45) & (pos[:, 1] > pos[i_topo, 1] - 0.38)
        classe[orelha & ((nv @ frente) > 0.35) & (classe == DESCONHECIDO)] = C["roxo"]
    classe[classe == DESCONHECIDO] = C["pelo"]

    # Manchas soltas de roxo/pelo somem pela maioria dos vizinhos (olho, gema, nariz e chifres ficam).
    vizinhos = (grafo > 0).astype(np.float32)
    fixos = np.isin(classe, OLHO + [C["gema"], C["nariz"], C["chifre"]])
    for _ in range(4):
        uma = np.zeros((N, len(CLASSES)), np.float32)
        uma[np.arange(N), classe] = 1
        contagem = vizinhos @ uma
        maioria = np.argmax(contagem, axis=1)
        forte = contagem[np.arange(N), maioria] >= 0.6 * contagem.sum(1)
        trocar = ~fixos & forte & (maioria != classe) & np.isin(maioria, [C["pelo"], C["roxo"]])
        classe[trocar] = maioria[trocar]

    # Cauda: a ponta é o ponto mais alto do modelo; pela distância da ponta (pela superfície), o fogo
    # começa a 30% do caminho e esquenta até a ponta, com um pouco de ruído e mais fogo por cima.
    ponta = int(np.argmax(pos[:, 1]))
    d_ponta = dijkstra(grafo, indices=ponta)
    raiz_cauda = int(mais_perto_na_imagem(*RAIZ_DA_CAUDA))
    comprimento_cauda = d_ponta[raiz_cauda]
    t_cauda = np.clip(1 - d_ponta / comprimento_cauda, 0, 1)
    ruido = (np.sin(pos @ np.array([7.1, 3.3, 5.7]) + 1.3) + np.sin(pos @ np.array([-4.2, 9.1, 2.6]) + 0.7)
             + np.sin(pos @ np.array([5.5, -6.3, 8.8]))) / 3
    t_fogo = t_cauda + 0.07 * ruido + 0.1 * nv[:, 1]
    limiar = 0.3
    fogo = (d_ponta < comprimento_cauda) & (t_fogo > limiar) & ~fixos
    classe[fogo] = C["chama"]
    calor = np.zeros(N)
    calor[fogo] = np.clip((t_fogo[fogo] - limiar) / (1 - limiar), 0, 1)
    print("partes:", {n: int((classe == i).sum()) for n, i in C.items()})
    # Pontos de referência (índices de vértices) para o esqueleto.
    marcos = {"nariz": int(i_nariz), "orelhas": pontas_orelhas, "ponta_cauda": ponta, "raiz_cauda": raiz_cauda}
    return classe, calor, marcos


# ================================================================== 5. orientação e exportação
def plano_de_simetria(pos):
    """Normal horizontal e deslocamento do plano em que o modelo espelhado cai em cima de si mesmo."""
    arvore = cKDTree(pos)
    amostra = pos[np.random.default_rng(1).choice(len(pos), 6000, replace=False)]
    _, _, vt = np.linalg.svd(pos[:, [0, 2]] - pos[:, [0, 2]].mean(0), full_matrices=False)
    t0 = np.arctan2(-vt[0][0], vt[0][1])

    def erro(t, d):
        n = np.array([np.cos(t), 0, np.sin(t)])
        return np.median(arvore.query(amostra - 2 * ((amostra @ n) - d)[:, None] * n)[0])

    melhor = None
    for t in t0 + np.radians(np.arange(-20, 21, 2)):
        d0 = np.median(pos @ np.array([np.cos(t), 0, np.sin(t)]))
        for d in d0 + np.arange(-0.1, 0.101, 0.01):
            e = erro(t, d)
            if melhor is None or e < melhor[0]:
                melhor = (e, t, d)
    for t in melhor[1] + np.radians(np.arange(-2, 2.01, 0.25)):
        for d in melhor[2] + np.arange(-0.01, 0.0101, 0.0025):
            e = erro(t, d)
            if e < melhor[0]:
                melhor = (e, t, d)
    return np.array([np.cos(melhor[1]), 0, np.sin(melhor[1])]), melhor[2]


def quadro(pontos, normais, cima, eixo_y=None):
    """Centro, normal, eixos e raios (percentil 97) de uma região, para os shaders do laboratório."""
    centro = pontos.mean(0)
    normal = normais.mean(0)
    normal /= np.linalg.norm(normal)
    rel = pontos - centro
    if eixo_y is None:
        eixo_x = np.cross(cima, normal)
        eixo_x /= np.linalg.norm(eixo_x)
        eixo_y = np.cross(normal, eixo_x)
    else:
        eixo_x = np.cross(eixo_y, normal)
    return {"centro": centro.round(5).tolist(), "normal": normal.round(5).tolist(), "eixoX": eixo_x.round(5).tolist(),
            "eixoY": eixo_y.round(5).tolist(), "raioX": round(float(np.percentile(np.abs(rel @ eixo_x), 97)), 5),
            "raioY": round(float(np.percentile(np.abs(rel @ eixo_y), 97)), 5)}


def orientar(pos, nv, classe):
    """Vira para +Z (a cabeça), com +X do lado esquerdo do bicho, em pé em y = 0 e centrado no plano de simetria."""
    normal_sim, d_sim = plano_de_simetria(pos)
    cima = np.array([0.0, 1.0, 0.0])
    frente = np.cross(cima, normal_sim)
    frente /= np.linalg.norm(frente)
    olhos_v = np.isin(classe, OLHO)
    if (pos[olhos_v].mean(0) - pos.mean(0)) @ frente < 0:
        frente = -frente
    esquerda = np.cross(cima, frente)
    M = np.stack([esquerda, cima, frente])
    pos2 = pos @ M.T
    pos2[:, 0] -= d_sim * float(esquerda @ normal_sim)
    pos2[:, 1] -= pos2[:, 1].min()
    pos2[:, 2] -= (pos2[:, 2].min() + pos2[:, 2].max()) / 2
    return pos2, nv @ M.T


def exportar(pos2, nv2, tris, classe, calor, caminho, esqueleto=None):
    N = len(pos2)
    cima = np.array([0.0, 1.0, 0.0])
    olhos_v = np.isin(classe, OLHO)

    # Parte de cada triângulo: a classe da maioria dos vértices (empate: a de maior prioridade). Olhos e
    # gema ganham duas voltas de margem, onde o shader desenha a borda certinha.
    ct = classe[tris]
    prioridade = np.array([0, 9, 9, 9, 8, 6, 7, 2, 3])
    v0, v1, v2 = ct[:, 0], ct[:, 1], ct[:, 2]
    tri_classe = np.where(v0 == v1, v0, np.where(v0 == v2, v0, np.where(v1 == v2, v1, -1)))
    empate = tri_classe < 0
    tri_classe[empate] = ct[empate][np.arange(empate.sum()), np.argmax(prioridade[ct[empate]], axis=1)]
    incidencia = sparse.csr_matrix((np.ones(tris.size), (np.repeat(np.arange(len(tris)), 3), tris.ravel())), shape=(len(tris), N))

    def com_margem(sel, voltas=2):
        for _ in range(voltas):
            sel = np.asarray(incidencia @ (np.asarray(incidencia[sel].sum(0)).ravel() > 0).astype(float)).ravel() > 0
        return sel

    nomes = {C["pelo"]: "pelo", C["gema"]: "gema", C["chifre"]: "chifres", C["nariz"]: "nariz", C["roxo"]: "roxo",
             C["chama"]: "chama", C["olho"]: "olhos", C["pupila"]: "olhos", C["brilho"]: "olhos"}
    parte = np.array([nomes[k] for k in tri_classe], dtype=object)
    tri_olho = com_margem(np.isin(ct, OLHO).sum(1) >= 2)
    parte[tri_olho] = "olhos"
    parte[com_margem((ct == C["gema"]).sum(1) >= 2) & ~tri_olho] = "gema"

    cor_v = np.tile(hex_rgb("#1c1622"), (N, 1))
    for k, h in CORES_OLHO.items():
        cor_v[classe == k] = hex_rgb(h)
    fogo = classe == C["chama"]
    pontos, tons = np.array([d[0] for d in DEGRADE_FOGO]), np.array([hex_rgb(d[1]) for d in DEGRADE_FOGO])
    for canal in range(3):
        cor_v[fogo, canal] = np.interp(calor[fogo], pontos, tons[:, canal])

    # Olhos (esquerdo, +X, primeiro) e gema, para os shaders.
    vizinhos = sparse.csr_matrix((np.ones(len(tris) * 3), (np.r_[tris[:, 0], tris[:, 1], tris[:, 2]], np.r_[tris[:, 1], tris[:, 2], tris[:, 0]])), shape=(N, N))
    idx = np.nonzero(olhos_v)[0]
    _, rotulos = connected_components(vizinhos[idx][:, idx], directed=False)
    olhos = [quadro(pos2[idx[rotulos == k]], nv2[idx[rotulos == k]], cima) for k in np.unique(rotulos) if (rotulos == k).sum() >= 40]
    olhos.sort(key=lambda o: -o["centro"][0])
    vg = np.nonzero(classe == C["gema"])[0]
    rel = pos2[vg] - pos2[vg].mean(0)
    normal_g = nv2[vg].mean(0) / np.linalg.norm(nv2[vg].mean(0))
    _, _, vt = np.linalg.svd(rel - np.outer(rel @ normal_g, normal_g), full_matrices=False)
    gema = quadro(pos2[vg], nv2[vg], cima, eixo_y=vt[0] if vt[0] @ cima > 0 else -vt[0])
    print("olhos:", len(olhos), "| tamanho (x, y, z):", np.round(pos2.max(0) - pos2.min(0), 3))

    gravar_glb(caminho, pos2, nv2, tris, parte, cor_v, calor, {"olhos": {"olhos": olhos}, "gema": {"gema": gema}},
               {"olhos": olhos, "gema": gema}, esqueleto)
    return parte, cor_v


# ================================================================== conferência
def conferir(pasta, pos, tris, cam, ref, pos2, parte, cor_v):
    os.makedirs(pasta, exist_ok=True)
    H, W = ref.shape[:2]
    proj = cam.projetar(pos)
    ids = rasterizar(proj, tris, W, H)
    render = sombrear(ids, normais_das_faces(pos, tris), cam.R)
    mistura = cv2.cvtColor(ref, cv2.COLOR_BGR2RGB)
    dentro = ids >= 0
    mistura[dentro] = (0.5 * mistura[dentro] + 0.5 * render[dentro]).astype(np.uint8)
    mistura[cv2.Canny((dentro * 255).astype(np.uint8), 50, 150) > 0] = (0, 255, 120)
    salvar_rgb(cv2.resize(mistura, None, fx=0.5, fy=0.5), os.path.join(pasta, "alinhamento.png"))

    cores = {n: hex_rgb(c["cor"]) for n, c in MATERIAIS.items()}
    cor_face = np.array([cores[n] for n in parte])
    por_vertice = np.isin(parte, ["olhos", "chama"])
    cor_face[por_vertice] = cor_v[tris[por_vertice]].mean(1)
    vistas = []
    for nome, d in (("3/4", (1, 0.35, 1.2)), ("esquerda", (1, 0.15, 0)), ("direita", (-1, 0.15, 0)),
                    ("frente", (0, 0.2, 1)), ("trás", (-0.4, 0.4, -1)), ("cima", (0.001, 1, 0.3))):
        img = renderizar(pos2, tris, camera_olhando(d), (420, 420), cor_face)
        cv2.putText(img, nome, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        vistas.append(img)
    salvar_rgb(np.vstack([np.hstack(vistas[:3]), np.hstack(vistas[3:])]), os.path.join(pasta, "vistas.png"))
    print("conferência em", pasta)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--entrada", default=ENTRADA)
    parser.add_argument("--referencia", default=REFERENCIA)
    parser.add_argument("--saida", default=SAIDA)
    parser.add_argument("--conferir", metavar="PASTA", help="grava imagens do alinhamento e do resultado")
    args = parser.parse_args()

    pos, tris = carregar_malha(args.entrada)
    nv = normais_dos_vertices(pos, tris)
    ref = cv2.imread(args.referencia)[:ALTURA_RENDER]
    mascaras = separar_referencia(ref)
    cam = alinhar(pos, tris, mascaras)
    classe, calor, marcos = pintar(pos, tris, nv, cam, ref, mascaras)
    pos2, nv2 = orientar(pos, nv, classe)
    esqueleto = montar_esqueleto(pos2, nv2, tris, classe, marcos)
    print("gravando", args.saida)
    parte, cor_v = exportar(pos2, nv2, tris, classe, calor, args.saida, esqueleto)
    if args.conferir:
        conferir(args.conferir, pos, tris, cam, ref, pos2, parte, cor_v)


if __name__ == "__main__":
    main()
