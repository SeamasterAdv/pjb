/* Mascote 3D: um lobinho de pelo preto, chamas roxas e olhos amarelos.
 *
 * Tudo é montado em código, sem arquivo de modelo:
 * - esqueleto de THREE.Bone: quadril, costas, peito, dois ossos de pescoço, cabeça, boca, orelhas,
 *   três ossos em cada pata da frente, quatro em cada pata de trás e oito na cauda;
 * - corpo, pernas e cauda em tubos com pele (SkinnedMesh), que dobram junto com os ossos;
 * - cabeça, patas, chifres e tufos de pelo presos aos ossos;
 * - chamas: labaredas (planos virados para a câmera, desenhados com ruído) e partículas que sobem.
 * As animações são calculadas quadro a quadro (`atualizar`): uma pose por modo, com transição suave,
 * e as patas apoiadas sempre encostam no chão.
 *
 * Usado pelo laboratório (laboratorio_mascote.html), onde os ajustes são escolhidos.
 */
import * as THREE from "three";

export const AJUSTES_PADRAO = Object.freeze({
  corPelo: "#17121e",
  corBrilho: "#7a45ff",
  forcaBrilho: 0.35,
  corOlhos: "#ffc81a",
  brilhoOlhos: 1.5,
  corGema: "#a35cff",
  corChifres: "#2a1b3a",
  corOrelhaDentro: "#3a1f5e",
  cabeca: 1,
  olhos: 1,
  pupila: 0.6,
  palpebra: 0,
  orelhas: 1,
  focinho: 1,
  chifres: 1,
  gema: true,
  corpo: 1,
  pernas: 1,
  cauda: 1,
  caudaVolume: 1,
  pelagem: 1,
  chamas: 1,
  alturaChamas: 1,
  corChamaNucleo: "#ff9cf2",
  corChamaMeio: "#a94dff",
  corChamaBorda: "#4b1cff",
  chamasOmbros: true,
  chamasCostas: true,
  chamasPeito: true,
  chamasPatas: true,
  chamasCauda: true,
});

// Ajustes que mudam a forma (refazem a malha); os outros só trocam cores e brilhos.
export const CHAVES_DE_FORMA = new Set([
  "cabeca", "olhos", "orelhas", "focinho", "chifres", "gema", "corpo", "pernas", "cauda", "caudaVolume", "pelagem",
  "chamasOmbros", "chamasCostas", "chamasPeito", "chamasPatas", "chamasCauda",
]);

export const PRESETS = [
  { id: "referencia", nome: "Referência", descricao: "Como na imagem: pelo preto, chamas roxas e olhos amarelos.", ajustes: {} },
  {
    id: "fofinho", nome: "Fofinho", descricao: "Cabeça e olhos maiores, pupila redonda e chamas calmas.",
    ajustes: { cabeca: 1.15, olhos: 1.22, pupila: 0.7, focinho: 0.8, chifres: 0.6, pernas: 0.85, corpo: 0.92, orelhas: 1.08, chamas: 0.75, alturaChamas: 0.8 },
  },
  {
    id: "feroz", nome: "Feroz", descricao: "Olhar fechado, pupila fina, chifres maiores e mais fogo.",
    ajustes: { cabeca: 0.94, olhos: 0.92, pupila: 0.16, palpebra: 0.42, focinho: 1.2, chifres: 1.45, orelhas: 1.12, pelagem: 1.3, forcaBrilho: 0.9, chamas: 1.45, alturaChamas: 1.3 },
  },
  {
    id: "sistema", nome: "Sistema azul", descricao: "Chamas e brilho azuis, nas cores do painel.",
    ajustes: { corChamaNucleo: "#d6fbff", corChamaMeio: "#3fa9ff", corChamaBorda: "#2346ff", corBrilho: "#3f7dff", corGema: "#5fd0ff", corOrelhaDentro: "#1d2a5c" },
  },
];

export const MODOS = [
  { id: "parado", nome: "Parado" },
  { id: "andar", nome: "Andando" },
  { id: "correr", nome: "Correndo" },
  { id: "sentar", nome: "Sentado" },
  { id: "deitar", nome: "Dormindo" },
  { id: "brincar", nome: "Brincando" },
  { id: "falar", nome: "Falando" },
  { id: "pensar", nome: "Pensando" },
  { id: "uivar", nome: "Uivando" },
];

const N_CAUDA = 8;

export const ARTICULACOES = [
  { grupo: "Cabeça", ossos: [["cabeca", "Cabeça"], ["mandibula", "Boca"], ["orelhaE", "Orelha esq."], ["orelhaD", "Orelha dir."]] },
  { grupo: "Pescoço e corpo", ossos: [["pescoco", "Pescoço (base)"], ["pescoco2", "Pescoço (alto)"], ["peito", "Peito"], ["coluna", "Costas"], ["corpo", "Quadril"]] },
  { grupo: "Pata da frente esquerda", ossos: [["ombroE", "Ombro"], ["cotoveloE", "Cotovelo"], ["pulsoE", "Pulso"]] },
  { grupo: "Pata da frente direita", ossos: [["ombroD", "Ombro"], ["cotoveloD", "Cotovelo"], ["pulsoD", "Pulso"]] },
  { grupo: "Pata de trás esquerda", ossos: [["coxaE", "Coxa"], ["joelhoE", "Joelho"], ["jarreteE", "Jarrete"], ["pataTE", "Pata"]] },
  { grupo: "Pata de trás direita", ossos: [["coxaD", "Coxa"], ["joelhoD", "Joelho"], ["jarreteD", "Jarrete"], ["pataTD", "Pata"]] },
  { grupo: "Cauda", ossos: Array.from({ length: N_CAUDA }, (_, k) => ["cauda" + k, String(k + 1)]) },
];

// Ossos que as poses giram por Euler (a cauda e a boca têm tratamento próprio).
const ARTICULADOS = [
  "corpo", "coluna", "peito", "pescoco", "pescoco2", "cabeca", "orelhaE", "orelhaD",
  "ombroE", "cotoveloE", "pulsoE", "ombroD", "cotoveloD", "pulsoD",
  "coxaE", "joelhoE", "jarreteE", "pataTE", "coxaD", "joelhoD", "jarreteD", "pataTD",
];
const SOLAS = ["solaE", "solaD", "solaTE", "solaTD"];
const EIXO_X = new THREE.Vector3(1, 0, 0);
const ZERO3 = [0, 0, 0];

const v3 = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const limitar = (x, a, b) => Math.min(b, Math.max(a, x));
const misturar = (a, b, t) => a + (b - a) * t;
const suave01 = (t) => { t = limitar(t, 0, 1); return t * t * (3 - 2 * t); };

function aleatorio(semente) {
  let a = semente >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function catmull(p0, p1, p2, p3, t) {
  const t2 = t * t, t3 = t2 * t;
  return 0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3);
}

// Pulso curto e periódico (orelha que mexe de vez em quando).
function contracao(t, periodo, fase) {
  const x = (((t + fase) % periodo) + periodo) % periodo;
  return x < 0.3 ? Math.sin((x / 0.3) * Math.PI) : 0;
}

// Uma pata no ciclo do passo: ângulo (negativo = para a frente) e quanto ela está levantada.
function passo(fase, apoio, amplitude) {
  const p = ((fase % 1) + 1) % 1;
  if (p < apoio) return [-amplitude + 2 * amplitude * (p / apoio), 0];
  const u = (p - apoio) / (1 - apoio);
  return [amplitude - 2 * amplitude * suave01(u), Math.sin(u * Math.PI)];
}

// ------------------------------------------------------------------ geometria
// Pesos de pele de um anel: perto de cada junta, metade de cada osso.
function pesosDoAnel(nos, j, t) {
  const atual = nos[j].osso, antes = j > 0 ? nos[j - 1].osso : atual, depois = nos[j + 1].osso;
  const FAIXA = 0.35;
  if (t < FAIXA && antes !== atual) {
    const w = 0.5 * (1 - suave01(t / FAIXA));
    return [[atual, antes], [1 - w, w]];
  }
  if (t > 1 - FAIXA && depois !== atual) {
    const w = 0.5 * suave01((t - (1 - FAIXA)) / FAIXA);
    return [[atual, depois], [1 - w, w]];
  }
  return [[atual, atual], [1, 0]];
}

/* Tubo de seção elíptica passando pelos nós ({p, rx, ry, osso?}). Os tubos ficam no plano YZ
 * (corpo, pernas, cauda), então o eixo X serve de referência e o tubo nunca torce.
 * Com `osso` em todos os nós, ganha pele (skinIndex/skinWeight) para dobrar com o esqueleto. */
function construirTubo(nos, { aneis = 32, lados = 18, tampaInicio = 0, tampaFim = 0, escalaUV = 0.2 } = {}) {
  const n = nos.length;
  const curva = new THREE.CatmullRomCurve3(nos.map((no) => no.p), false, "catmullrom", 0.5);
  const comPele = nos.every((no) => no.osso !== undefined);
  const no = (k) => nos[limitar(k, 0, n - 1)];
  const lista = [];
  let comprimento = 0, anterior = null;
  for (let i = 0; i <= aneis; i++) {
    const s = i / aneis;
    const c = curva.getPoint(s);
    const T = curva.getTangent(s).normalize();
    const R = EIXO_X.clone().addScaledVector(T, -EIXO_X.dot(T));
    if (R.lengthSq() < 1e-6) R.set(0, 0, 1);
    R.normalize();
    const U = new THREE.Vector3().crossVectors(T, R).normalize();
    const f = s * (n - 1), j = Math.min(n - 2, Math.floor(f)), t = f - j;
    const rx = Math.max(0.002, catmull(no(j - 1).rx, no(j).rx, no(j + 1).rx, no(j + 2).rx, t));
    const ry = Math.max(0.002, catmull(no(j - 1).ry, no(j).ry, no(j + 1).ry, no(j + 2).ry, t));
    if (anterior) comprimento += c.distanceTo(anterior);
    anterior = c;
    lista.push({ c, T, R, U, rx, ry, v: comprimento, inclina: 0, pele: comPele ? pesosDoAnel(nos, j, t) : null });
  }
  // Onde o raio muda, a normal inclina para frente ou para trás (sombra certa no afunilamento).
  for (let i = 0; i < lista.length; i++) {
    const a = lista[Math.max(0, i - 1)], b = lista[Math.min(lista.length - 1, i + 1)];
    const ds = b.v - a.v;
    lista[i].inclina = ds > 1e-6 ? Math.atan2(-((b.rx + b.ry) - (a.rx + a.ry)) / 2, ds) : 0;
  }
  const PASSOS = 7;
  const anelDeTampa = (anel, sentido, frac, fator) => {
    const fi = (frac * Math.PI) / 2, raio = ((anel.rx + anel.ry) / 2) * fator;
    return {
      ...anel,
      c: anel.c.clone().addScaledVector(anel.T, sentido * Math.sin(fi) * raio),
      rx: anel.rx * Math.cos(fi) + 1e-4, ry: anel.ry * Math.cos(fi) + 1e-4,
      v: anel.v + sentido * Math.sin(fi) * raio, inclina: sentido * fi,
    };
  };
  const inicio = [], fim = [];
  if (tampaInicio) for (let k = PASSOS; k >= 1; k--) inicio.push(anelDeTampa(lista[0], -1, k / PASSOS, tampaInicio));
  if (tampaFim) for (let k = 1; k <= PASSOS; k++) fim.push(anelDeTampa(lista[lista.length - 1], 1, k / PASSOS, tampaFim));
  const todos = [...inicio, ...lista, ...fim];

  const raioMedio = lista.reduce((soma, a) => soma + (a.rx + a.ry) / 2, 0) / lista.length;
  const voltasU = Math.max(1, Math.round((2 * Math.PI * raioMedio) / escalaUV));
  const posicoes = [], normais = [], uvs = [], indices = [], indOssos = [], pesos = [];
  const p = v3(), radial = v3();
  for (const anel of todos) {
    for (let k = 0; k <= lados; k++) {
      const a = (k / lados) * Math.PI * 2, ca = Math.cos(a), sa = Math.sin(a);
      p.copy(anel.c).addScaledVector(anel.R, ca * anel.rx).addScaledVector(anel.U, sa * anel.ry);
      radial.copy(anel.R).multiplyScalar(ca * anel.ry).addScaledVector(anel.U, sa * anel.rx).normalize();
      radial.multiplyScalar(Math.cos(anel.inclina)).addScaledVector(anel.T, Math.sin(anel.inclina)).normalize();
      posicoes.push(p.x, p.y, p.z);
      normais.push(radial.x, radial.y, radial.z);
      uvs.push((k / lados) * voltasU, anel.v / escalaUV);
      if (anel.pele) {
        indOssos.push(anel.pele[0][0], anel.pele[0][1], 0, 0);
        pesos.push(anel.pele[1][0], anel.pele[1][1], 0, 0);
      }
    }
  }
  for (let i = 0; i < todos.length - 1; i++) {
    for (let k = 0; k < lados; k++) {
      const a = i * (lados + 1) + k, b = a + lados + 1;
      indices.push(a, a + 1, b, b, a + 1, b + 1);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(posicoes, 3));
  geo.setAttribute("normal", new THREE.Float32BufferAttribute(normais, 3));
  geo.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  if (comPele) {
    geo.setAttribute("skinIndex", new THREE.Uint16BufferAttribute(indOssos, 4));
    geo.setAttribute("skinWeight", new THREE.Float32BufferAttribute(pesos, 4));
  }
  geo.setIndex(indices);
  return geo;
}

// Tufo de pelo: base em (0,0,0), ponta em (0,1,curva), achatado na direção em que dobra.
function geometriaMecha(curva) {
  const nos = [];
  for (let i = 0; i <= 6; i++) {
    const s = i / 6;
    const r = 0.5 * Math.pow(1 - s, 0.85) + 0.004;
    nos.push({ p: v3(0, 2 * (1 - s) * s * 0.55 + s * s, s * s * curva), rx: r, ry: r * 0.5 });
  }
  return construirTubo(nos, { aneis: 9, lados: 7, tampaInicio: 1 });
}

function matrizMecha(base, dir, dobra, comp, larg) {
  const y = dir.clone().normalize();
  const z = dobra.clone().addScaledVector(y, -dobra.dot(y));
  if (z.lengthSq() < 1e-6) z.set(0, 0, 1).addScaledVector(y, -y.z);
  z.normalize();
  const x = new THREE.Vector3().crossVectors(y, z);
  const m = new THREE.Matrix4().makeBasis(x.multiplyScalar(larg), y.multiplyScalar(comp), z.multiplyScalar(larg));
  return m.setPosition(base);
}

// Junta várias cópias transformadas de geometrias numa só (os tufos de cada osso viram uma malha).
class Acumulador {
  constructor() { this.pos = []; this.nor = []; this.uv = []; this.ind = []; }

  adicionar(geo, m) {
    const base = this.pos.length / 3;
    const pos = geo.attributes.position, nor = geo.attributes.normal, uv = geo.attributes.uv;
    const nm = new THREE.Matrix3().getNormalMatrix(m), t = v3();
    for (let i = 0; i < pos.count; i++) {
      t.fromBufferAttribute(pos, i).applyMatrix4(m);
      this.pos.push(t.x, t.y, t.z);
      t.fromBufferAttribute(nor, i).applyMatrix3(nm).normalize();
      this.nor.push(t.x, t.y, t.z);
      this.uv.push(uv.getX(i), uv.getY(i));
    }
    for (let i = 0; i < geo.index.count; i++) this.ind.push(base + geo.index.getX(i));
  }

  geometria() {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(this.pos, 3));
    g.setAttribute("normal", new THREE.Float32BufferAttribute(this.nor, 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute(this.uv, 2));
    g.setIndex(this.ind);
    return g;
  }
}

// Fios de pelo em tons de cinza, usados como relevo (bumpMap). Repete sem emenda.
function texturaPelo() {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const g = c.getContext("2d");
  g.fillStyle = "#808080";
  g.fillRect(0, 0, 256, 256);
  g.lineCap = "round";
  const rnd = aleatorio(7);
  for (let i = 0; i < 2400; i++) {
    const x = rnd() * 256, y = rnd() * 256, comp = 6 + rnd() * 14, ang = (rnd() - 0.5) * 0.6;
    const tom = Math.floor(40 + rnd() * 175);
    g.strokeStyle = `rgb(${tom},${tom},${tom})`;
    g.lineWidth = 0.8 + rnd() * 1.6;
    const dx = Math.sin(ang) * comp, dy = Math.cos(ang) * comp;
    for (const ox of [-256, 0, 256]) {
      for (const oy of [-256, 0, 256]) {
        g.beginPath();
        g.moveTo(x + ox, y + oy);
        g.lineTo(x + ox + dx, y + oy + dy);
        g.stroke();
      }
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

// Material padrão com um contorno colorido (luz de borda), que dá o brilho roxo no pelo escuro.
function materialComAro(parametros, aro) {
  return adicionarAro(new THREE.MeshStandardMaterial(parametros), aro);
}

// `extra` mexe no shader antes (olho e gema desenhados no próprio pelo); `chave` separa o programa
// compilado de cada variação (o three.js reaproveita programas com o mesmo onBeforeCompile).
export function adicionarAro(m, aro, extra = null, chave = "") {
  m.needsUpdate = true;
  m.customProgramCacheKey = () => "aro:" + chave;
  m.onBeforeCompile = (sh) => {
    if (extra) extra(sh);
    sh.uniforms.uCorAro = aro.cor;
    sh.uniforms.uForcaAro = aro.forca;
    sh.fragmentShader = sh.fragmentShader
      .replace("#include <common>", "#include <common>\nuniform vec3 uCorAro;\nuniform float uForcaAro;")
      .replace(
        "#include <opaque_fragment>",
        "float aro = 1.0 - clamp(dot(normalize(vViewPosition), normal), 0.0, 1.0);\n" +
          "outgoingLight += uCorAro * pow(aro, 3.5) * uForcaAro;\n#include <opaque_fragment>",
      );
  };
  return m;
}

// ------------------------------------------------------------------ shaders
const GLSL_RUIDO = `
float hash12(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float ruido(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash12(i), hash12(i + vec2(1.0, 0.0)), f.x), mix(hash12(i + vec2(0.0, 1.0)), hash12(i + vec2(1.0, 1.0)), f.x), f.y);
}
float fbm(vec2 p) { float v = 0.0, a = 0.5; for (int i = 0; i < 4; i++) { v += a * ruido(p); p = p * 2.03 + 17.1; a *= 0.5; } return v; }
`;

const OLHO_VERT = `
varying vec3 vP; varying vec3 vN; varying vec3 vV;
void main() {
  vP = position;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vN = normalize(normalMatrix * normal);
  vV = normalize(-mv.xyz);
  gl_Position = projectionMatrix * mv;
}`;

// Íris amarela com raios, borda escura, pupila de gato (fina ou redonda) e dois brilhos.
const OLHO_FRAG = `
uniform vec3 uCor; uniform float uBrilho; uniform float uPupila;
varying vec3 vP; varying vec3 vN; varying vec3 vV;
void main() {
  vec3 p = normalize(vP);
  vec2 d = p.xy;
  float r = length(d);
  vec3 claro = mix(uCor, vec3(1.0, 0.97, 0.75), 0.5);
  vec3 escuro = uCor * vec3(0.9, 0.45, 0.18);
  vec3 cor = mix(claro, uCor, smoothstep(0.0, 0.5, r));
  cor = mix(cor, escuro, smoothstep(0.5, 0.86, r));
  cor *= 0.88 + 0.12 * sin(atan(d.y, d.x) * 26.0 + r * 9.0);
  cor = mix(cor, vec3(0.05, 0.02, 0.01), smoothstep(0.84, 0.95, r));
  float meia = uPupila * 0.42 * sqrt(max(0.0, 1.0 - pow(d.y / 0.8, 2.0)));
  float pupila = (1.0 - smoothstep(meia - 0.012, meia + 0.025, abs(d.x))) * (1.0 - smoothstep(0.76, 0.8, abs(d.y)));
  cor = mix(cor, vec3(0.02, 0.005, 0.03), pupila);
  vec3 luz = cor * uBrilho;
  float b1 = 1.0 - smoothstep(0.12, 0.17, length(d - vec2(-0.3, 0.36)));
  float b2 = 1.0 - smoothstep(0.05, 0.085, length(d - vec2(0.26, -0.3)));
  luz = mix(luz, vec3(3.0), max(b1, b2 * 0.85));
  luz *= smoothstep(-0.3, 0.25, p.z);
  luz *= 1.0 - 0.55 * pow(1.0 - max(dot(vN, vV), 0.0), 2.5);
  gl_FragColor = vec4(luz, 1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;

// Labareda: plano em pé, sempre de frente para a câmera, que balança e se curva com o vento.
const LABAREDA_VERT = `
uniform vec2 uTamanho; uniform vec3 uVento; uniform float uTempo; uniform float uSemente; uniform float uAdiante;
varying vec2 vUv;
void main() {
  vUv = uv;
  vec3 centro = (modelMatrix * vec4(0.0, 0.0, 0.0, 1.0)).xyz;
  vec3 paraCamera = cameraPosition - centro;
  vec3 plano = vec3(paraCamera.x, 0.0, paraCamera.z);
  plano = dot(plano, plano) < 1e-6 ? vec3(0.0, 0.0, 1.0) : normalize(plano);
  vec3 direita = normalize(cross(vec3(0.0, 1.0, 0.0), plano));
  float h = uv.y;
  vec3 p = centro + direita * position.x * uTamanho.x + vec3(0.0, position.y * uTamanho.y, 0.0);
  p += uVento * pow(h, 1.5) * uTamanho.y;
  p += direita * sin(uTempo * 3.3 + uSemente * 6.28 + h * 2.5) * 0.07 * h * uTamanho.x;
  p += normalize(paraCamera) * uAdiante;
  gl_Position = projectionMatrix * viewMatrix * vec4(p, 1.0);
}`;

const LABAREDA_FRAG = `
uniform float uTempo; uniform float uSemente; uniform float uIntensidade; uniform float uCorte;
uniform vec3 uCor1; uniform vec3 uCor2; uniform vec3 uCor3;
varying vec2 vUv;
${GLSL_RUIDO}
void main() {
  float t = uTempo * 1.5 + uSemente * 13.0;
  vec2 q = vec2((vUv.x - 0.5) * 2.0, vUv.y);
  // Ruído subindo: perto da base a chama é cheia; no alto, se parte em línguas e fiapos que se soltam.
  float n = fbm(vec2(q.x * 2.2 + uSemente * 7.0, q.y * 2.0 - t * 2.0));
  float n2 = fbm(vec2(q.x * 4.5 - uSemente * 3.0, q.y * 3.6 - t * 3.2));
  float chama = (1.0 - q.y) - abs(q.x) * (1.15 + q.y * 1.6) + (n - 0.5) * 1.1 * (0.3 + q.y) + (n2 - 0.5) * 0.35 * q.y;
  float a = smoothstep(0.0, 0.3, chama) * smoothstep(0.0, 0.15, q.y);
  if (a < max(0.003, uCorte)) discard;
  float calor = clamp(chama * 1.4, 0.0, 1.0);
  vec3 cor = mix(uCor3, uCor2, smoothstep(0.0, 0.45, calor));
  cor = mix(cor, uCor1, smoothstep(0.6, 1.0, calor));
  gl_FragColor = vec4(cor * uIntensidade * (0.55 + 0.9 * calor), a * 0.9);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;

const PARTICULA_VERT = `
attribute float aVida; attribute float aTam; attribute float aSemente;
uniform float uEscala;
varying float vVida; varying float vSemente;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = aTam * uEscala / max(-mv.z, 0.01);
  gl_Position = projectionMatrix * mv;
  vVida = aVida;
  vSemente = aSemente;
}`;

// Cada partícula é uma gota de fogo (base redonda, ponta fina) que tremula, esfria e some.
const PARTICULA_FRAG = `
uniform float uTempo; uniform float uIntensidade; uniform float uCorte;
uniform vec3 uCor1; uniform vec3 uCor2; uniform vec3 uCor3;
varying float vVida; varying float vSemente;
${GLSL_RUIDO}
void main() {
  vec2 p = gl_PointCoord * 2.0 - 1.0;
  p.y = -p.y;
  float n = ruido(vec2(p.x * 2.5 + vSemente * 17.0, p.y * 2.0 - uTempo * 4.0 + vSemente * 5.0));
  p.x += (n - 0.5) * 0.5 * (p.y + 1.0) * 0.5;
  float meia = p.y < -0.3
    ? 0.55 * sqrt(max(0.0, 1.0 - pow((p.y + 0.3) / 0.6, 2.0)))
    : 0.55 * pow(max(0.0, 1.0 - (p.y + 0.3) / 1.25), 1.3);
  float f = 1.0 - smoothstep(meia * 0.35, meia + 0.001, abs(p.x));
  float a = f * (1.0 - smoothstep(0.6, 1.0, vVida)) * smoothstep(0.0, 0.08, vVida);
  if (a < max(0.01, uCorte)) discard;
  float calor = f * (1.0 - vVida) * (1.0 - 0.6 * smoothstep(-0.3, 0.9, p.y));
  vec3 cor = mix(uCor3, uCor2, smoothstep(0.05, 0.4, calor));
  cor = mix(cor, uCor1, smoothstep(0.45, 0.9, calor));
  gl_FragColor = vec4(cor * uIntensidade, a);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;

// ------------------------------------------------------------------ modelo de arquivo
// O .glb pintado por assets/colorir_mascote.py traz um material com nome para cada parte (pelo, olhos,
// gema, chifres, nariz, roxo, garras, chama). `vestirModelo` dá a eles o contorno roxo, desenha olhos
// e gema dentro do próprio material do pelo (a partir do centro, eixos e raios guardados no arquivo;
// fora do desenho fica o pelo com a mesma luz do resto) e faz a cauda brilhar e tremular como fogo.
// As cores seguem os mesmos ajustes do modelo de código.

// Posição no espaço do modelo, para os desenhos não "escorregarem" com a câmera.
function comPosicaoDoModelo(sh) {
  sh.vertexShader = sh.vertexShader
    .replace("#include <common>", "#include <common>\nvarying vec3 vPosObj;")
    .replace("#include <begin_vertex>", "#include <begin_vertex>\nvPosObj = position;");
}

// Olho: íris amarela com raios, pupila de gato que segue o olhar, dois brilhos, contorno escuro e
// pálpebra de pelo que desce ao piscar. Dentro do olho, quase nada de cor difusa e muita emissão.
const OLHO_GLSL_DECL = `
varying vec3 vPosObj;
uniform vec3 uCentro[2]; uniform vec3 uEixoX[2]; uniform vec3 uEixoY[2]; uniform vec2 uRaio[2]; uniform vec2 uFrente[2];
uniform vec3 uCor; uniform float uBrilho; uniform float uPupila; uniform vec2 uOlhar; uniform float uFechar;`;
const OLHO_GLSL_COR = `
  bool primeiro = distance(vPosObj, uCentro[0]) < distance(vPosObj, uCentro[1]);
  vec3 relOlho = vPosObj - (primeiro ? uCentro[0] : uCentro[1]);
  vec2 raioOlho = primeiro ? uRaio[0] : uRaio[1];
  vec2 d = vec2(dot(relOlho, primeiro ? uEixoX[0] : uEixoX[1]) / raioOlho.x, dot(relOlho, primeiro ? uEixoY[0] : uEixoY[1]) / raioOlho.y);
  float r = length(d);
  vec3 iris = mix(mix(uCor, vec3(1.0, 0.97, 0.75), 0.5), uCor, smoothstep(0.0, 0.5, r));
  iris = mix(iris, uCor * vec3(0.9, 0.45, 0.18), smoothstep(0.55, 0.92, r));
  iris *= 0.9 + 0.1 * sin(atan(d.y, d.x) * 26.0 + r * 9.0);
  // A pupila olha para a frente da cabeça (os olhos ficam nos lados) e segue o olhar.
  vec2 q = d - (primeiro ? uFrente[0] : uFrente[1]) - uOlhar * 0.35;
  float meia = uPupila * 0.42 * sqrt(max(0.0, 1.0 - pow(q.y / 0.8, 2.0)));
  float pupila = (1.0 - smoothstep(meia - 0.03, meia + 0.03, abs(q.x))) * (1.0 - smoothstep(0.72, 0.8, abs(q.y)));
  iris = mix(iris, vec3(0.0), pupila);
  vec3 corOlho = iris * uBrilho;
  float b1 = 1.0 - smoothstep(0.13, 0.19, length(d - vec2(-0.3, 0.36)));
  float b2 = 1.0 - smoothstep(0.05, 0.09, length(d - vec2(0.28, -0.3)));
  corOlho = mix(corOlho, vec3(3.0), max(b1, b2 * 0.85));
  float fechado = smoothstep(-0.03, 0.03, d.y - (mix(1.15, -1.15, uFechar) - 0.18 * d.x * d.x)) * (1.0 - step(1.25, r));
  float dentro = (1.0 - smoothstep(0.97, 1.03, r)) * (1.0 - fechado);
  float contorno = smoothstep(0.95, 1.02, r) * (1.0 - smoothstep(1.08, 1.18, r)) * (1.0 - fechado);
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.008), max(dentro, contorno));`;

// Gema: losango com quatro facetas, brilho no canto e borda escura.
const GEMA_GLSL_DECL = `
varying vec3 vPosObj;
uniform vec3 uCentro; uniform vec3 uEixoX; uniform vec3 uEixoY; uniform vec2 uRaio; uniform vec3 uCor;`;
const GEMA_GLSL_COR = `
  vec3 relGema = vPosObj - uCentro;
  vec2 d = vec2(dot(relGema, uEixoX) / uRaio.x, dot(relGema, uEixoY) / uRaio.y);
  float losango = abs(d.x) + abs(d.y);
  float faceta = d.x < 0.0 ? (d.y > 0.0 ? 1.35 : 0.95) : (d.y > 0.0 ? 1.05 : 0.6);
  vec3 corOlho = uCor * faceta * (1.15 - 0.35 * losango);
  corOlho = mix(corOlho, vec3(1.0, 0.85, 1.0), (1.0 - smoothstep(0.07, 0.2, length(d - vec2(-0.22, 0.38)))) * 0.85);
  float dentro = 1.0 - smoothstep(0.93, 1.0, losango);
  float contorno = smoothstep(0.9, 1.0, losango) * (1.0 - smoothstep(1.04, 1.12, losango));
  diffuseColor.rgb = mix(diffuseColor.rgb, uCor * 0.08, max(dentro, contorno));`;

function desenhoNoPelo(m, aro, uniforms, decl, cor, chave) {
  m.vertexColors = false;
  return adicionarAro(m, aro, (sh) => {
    Object.assign(sh.uniforms, uniforms);
    comPosicaoDoModelo(sh);
    sh.fragmentShader = sh.fragmentShader
      .replace("#include <common>", "#include <common>\n" + decl)
      .replace("#include <color_fragment>", "#include <color_fragment>\n" + cor)
      .replace("#include <roughnessmap_fragment>", "#include <roughnessmap_fragment>\nroughnessFactor = mix(roughnessFactor, 0.2, dentro);")
      .replace("#include <emissivemap_fragment>", "#include <emissivemap_fragment>\ntotalEmissiveRadiance += corOlho * dentro;");
  }, chave);
}

// A cauda: base preta que vira fogo (cores e força das chamas dos ajustes), com brilho que sobe.
function adicionarFogo(m, comum, corPelo) {
  m.vertexColors = false;
  m.needsUpdate = true;
  m.customProgramCacheKey = () => "fogo";
  m.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, {
      uTempo: comum.uTempo, uIntensidade: comum.uIntensidade, uCor1: comum.uCor1, uCor2: comum.uCor2, uCor3: comum.uCor3, uCorPelo: corPelo,
    });
    comPosicaoDoModelo(sh);
    sh.vertexShader = sh.vertexShader
      .replace("#include <common>", "#include <common>\nattribute float _chama;\nvarying float vChama;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvChama = _chama;");
    sh.fragmentShader = sh.fragmentShader
      .replace("#include <common>", "#include <common>\nuniform float uTempo; uniform float uIntensidade;\n" +
        "uniform vec3 uCor1; uniform vec3 uCor2; uniform vec3 uCor3; uniform vec3 uCorPelo;\nvarying float vChama;\nvarying vec3 vPosObj;\n" + GLSL_RUIDO)
      .replace("#include <color_fragment>", `
        float tremor = fbm(vec2(vPosObj.x * 7.0 + vPosObj.z * 5.0, vPosObj.y * 6.0 - uTempo * 2.2));
        float calor = clamp(vChama + (tremor - 0.5) * 0.4 * vChama, 0.0, 1.0);
        vec3 fogo = mix(uCor3, uCor2, smoothstep(0.15, 0.55, calor));
        fogo = mix(fogo, uCor1, smoothstep(0.72, 1.0, calor));
        diffuseColor.rgb = mix(uCorPelo, fogo, smoothstep(0.0, 0.3, vChama));`)
      .replace("#include <emissivemap_fragment>", `#include <emissivemap_fragment>
        totalEmissiveRadiance = fogo * smoothstep(0.05, 0.6, calor) * (0.55 + 0.9 * tremor) * uIntensidade * 1.3;`);
  };
}

export function vestirModelo(raiz, mascote) {
  const materiais = {};
  const olhos = {
    uCentro: { value: [v3(), v3()] }, uEixoX: { value: [v3(1, 0, 0), v3(1, 0, 0)] }, uEixoY: { value: [v3(0, 1, 0), v3(0, 1, 0)] },
    uRaio: { value: [new THREE.Vector2(1, 1), new THREE.Vector2(1, 1)] }, uCor: { value: new THREE.Color() }, uBrilho: { value: 1.5 },
    uPupila: { value: 0.3 }, uOlhar: { value: new THREE.Vector2() }, uFechar: { value: 0 },
    uFrente: { value: [new THREE.Vector2(), new THREE.Vector2()] },
  };
  const gema = {
    uCentro: { value: v3() }, uEixoX: { value: v3(1, 0, 0) }, uEixoY: { value: v3(0, 1, 0) }, uRaio: { value: new THREE.Vector2(1, 1) },
    uCor: { value: new THREE.Color() },
  };
  const corPelo = { value: new THREE.Color() };
  raiz.traverse((o) => {
    if (!o.isMesh || !o.material) return;
    const m = o.material, nome = m.name, dados = m.userData || {};
    if (nome === "olhos" && dados.olhos && dados.olhos.length >= 2) {
      dados.olhos.slice(0, 2).forEach((olho, i) => {
        olhos.uCentro.value[i].fromArray(olho.centro);
        olhos.uEixoX.value[i].fromArray(olho.eixoX);
        olhos.uEixoY.value[i].fromArray(olho.eixoY);
        // Um pouco menor que a região do olho, para a íris não encostar na borda da malha.
        olhos.uRaio.value[i].set(olho.raioX * 0.93, olho.raioY * 0.93);
      });
      // Frente da cabeça = média das normais dos olhos, no plano horizontal; em cada olho, vira um
      // deslocamento da pupila nos eixos dele.
      const frente = v3().fromArray(dados.olhos[0].normal).add(v3().fromArray(dados.olhos[1].normal)).setY(0).normalize();
      dados.olhos.slice(0, 2).forEach((olho, i) => {
        olhos.uFrente.value[i].set(frente.dot(v3().fromArray(olho.eixoX)), frente.dot(v3().fromArray(olho.eixoY))).multiplyScalar(0.8);
      });
      desenhoNoPelo(m, mascote.aro, olhos, OLHO_GLSL_DECL, OLHO_GLSL_COR, "olhos");
    } else if (nome === "gema" && dados.gema) {
      const g = dados.gema;
      gema.uCentro.value.fromArray(g.centro);
      gema.uEixoX.value.fromArray(g.eixoX);
      gema.uEixoY.value.fromArray(g.eixoY);
      gema.uRaio.value.set(g.raioX, g.raioY);
      desenhoNoPelo(m, mascote.aro, gema, GEMA_GLSL_DECL, GEMA_GLSL_COR, "gema");
    } else if (nome === "chama") {
      adicionarFogo(m, mascote.comum, corPelo);
    } else if (["pelo", "roxo", "chifres", "garras"].includes(nome)) {
      adicionarAro(m, mascote.aro);
    }
    if (nome) materiais[nome] = m;
  });

  // Esqueleto do arquivo (mesmos nomes do modelo de código), marcadores de contato com o chão e os
  // pontos das chamas (`chama_*`, com tamanho em extras).
  const ossos = {}, contatos = {}, pontosDeChama = [];
  raiz.traverse((o) => {
    if (o.isBone) ossos[o.name] = o;
    else if (/^(sola|solaT)[ED]$|^(assento|barriga|peitoBaixo)$/.test(o.name)) contatos[o.name] = o;
    else if (o.name.startsWith("chama_")) pontosDeChama.push(o);
  });
  const animado = Boolean(ossos.raiz && ossos.cabeca && ossos.cauda0);
  const chamas = new Chamas(mascote.comum, mascote.geo.chama);
  raiz.add(chamas.grupo);
  const v = v3();
  let chaveDasChamas = "";

  function criarChamas(aj) {
    chamas.limpar();
    const ligado = (nome) => (/ombro/.test(nome) ? aj.chamasOmbros : /peito/.test(nome) ? aj.chamasPeito
      : /costas|dorso/.test(nome) ? aj.chamasCostas : aj.chamasPatas);
    for (const p of pontosDeChama) {
      if (!ligado(p.name)) continue;
      const e = p.userData || {};
      chamas.labareda(p, v3(), e.larg || 0.3, e.alt || 0.45, e.adiante ?? 0.08);
      if (e.taxa) chamas.emissor(p, v3(0, 0.03, 0), v3(e.larg * 0.35, 0.05, e.larg * 0.35), e.taxa, e.tam || 0.15, 0.7);
    }
    const costas = pontosDeChama.find((p) => p.name === "chama_costas");
    if (costas) chamas.luz(costas, v3(0, 0.1, 0), aj.chamasOmbros || aj.chamasCostas ? 0.8 : 0);
    if (ossos.cauda7) {
      chamas.luz(ossos.cauda7, v3(0, 0.1, 0), aj.chamasCauda ? 0.9 : 0, 1.8);
      if (aj.chamasCauda) {
        for (const k of [5, 6, 7]) chamas.emissor(ossos["cauda" + k], v3(0, 0.05, 0), v3(0.12, 0.08, 0.12), 10 + k * 3, 0.17, 0.75);
      }
    }
    if (contatos.peitoBaixo) chamas.luz(contatos.peitoBaixo, v3(0, -0.12, 0.1), aj.chamasPatas ? 0.5 : 0, 1.2);
  }

  function correcao(nomes) {
    let menor = Infinity;
    for (const nome of nomes) {
      const c = contatos[nome];
      if (!c) continue;
      raiz.worldToLocal(c.getWorldPosition(v));
      menor = Math.min(menor, v.y);
    }
    return Number.isFinite(menor) ? -menor : 0;
  }

  return {
    materiais,
    ossos,
    animado,
    aplicar(aj) {
      corPelo.value.set(aj.corPelo);
      // Olhos e gema são desenhados sobre o pelo: o material deles tem a cor e a aspereza do pelo.
      for (const nome of ["pelo", "olhos", "gema"]) {
        if (!materiais[nome]) continue;
        materiais[nome].color.set(aj.corPelo);
        materiais[nome].roughness = 0.85;
        materiais[nome].emissive.set(0x000000);
      }
      if (materiais.roxo) materiais.roxo.color.set(aj.corOrelhaDentro);
      if (materiais.chifres) materiais.chifres.color.set(aj.corChifres);
      if (materiais.garras) {
        materiais.garras.color.set(aj.corGema).multiplyScalar(0.6);
        materiais.garras.emissive.set(aj.corGema);
        materiais.garras.emissiveIntensity = 0.35;
      }
      gema.uCor.value.set(aj.corGema);
      olhos.uCor.value.set(aj.corOlhos);
      olhos.uBrilho.value = aj.brilhoOlhos;
      olhos.uPupila.value = aj.pupila;
      const chave = [aj.chamasOmbros, aj.chamasPeito, aj.chamasCostas, aj.chamasPatas, aj.chamasCauda].join();
      if (chave !== chaveDasChamas) {
        chaveDasChamas = chave;
        criarChamas(aj);
      }
    },
    // A cada quadro, depois de mascote.atualizar: a pose do modelo de código (que roda escondido) vai
    // osso a osso para o modelo do arquivo, as patas encostam no chão, e os olhos piscam e olham junto.
    atualizar(aj, dt) {
      olhos.uFechar.value = Math.max(mascote.fechamento || 0, aj.palpebra * 0.45);
      olhos.uOlhar.value.set(limitar(mascote.olhar.x, -1, 1), limitar(mascote.olhar.y, -1, 1));
      const pose = mascote.poseAtual;
      if (animado && pose && mascote.modo !== "pose") {
        for (const nome in ossos) {
          const fonte = mascote.ossos[nome];
          if (nome !== "raiz" && fonte) ossos[nome].quaternion.copy(fonte.quaternion);
        }
        ossos.raiz.position.set(0, 0, 0);
        raiz.updateMatrixWorld(true);
        let altura = correcao(pose.apoio);
        if (pose.apoioAnterior) altura = misturar(correcao(pose.apoioAnterior), altura, pose.peso);
        ossos.raiz.position.y = altura + pose.y;
      }
      raiz.updateMatrixWorld(true);
      const parado = mascote.modo === "pose" || !pose;
      chamas.atualizar(dt, raiz, aj.chamas * (parado ? 1 : pose.chamas), aj.alturaChamas, parado ? ZERO3 : pose.vento);
    },
  };
}

// ------------------------------------------------------------------ partículas
class Particulas {
  constructor(comum, max = 1000) {
    this.max = max;
    this.proxima = 0;
    this.pos = new Float32Array(max * 3);
    this.vel = new Float32Array(max * 3);
    this.vida = new Float32Array(max).fill(1);
    this.tam = new Float32Array(max);
    this.base = new Float32Array(max);
    this.idade = new Float32Array(max);
    this.duracao = new Float32Array(max).fill(1);
    const semente = new Float32Array(max).map(() => Math.random());
    const geo = new THREE.BufferGeometry();
    this.atrPos = new THREE.BufferAttribute(this.pos, 3).setUsage(THREE.DynamicDrawUsage);
    this.atrVida = new THREE.BufferAttribute(this.vida, 1).setUsage(THREE.DynamicDrawUsage);
    this.atrTam = new THREE.BufferAttribute(this.tam, 1).setUsage(THREE.DynamicDrawUsage);
    geo.setAttribute("position", this.atrPos);
    geo.setAttribute("aVida", this.atrVida);
    geo.setAttribute("aTam", this.atrTam);
    geo.setAttribute("aSemente", new THREE.BufferAttribute(semente, 1));
    this.material = new THREE.ShaderMaterial({
      uniforms: {
        uEscala: { value: 600 }, uTempo: comum.uTempo, uIntensidade: comum.uIntensidade, uCorte: comum.uCorte,
        uCor1: comum.uCor1, uCor2: comum.uCor2, uCor3: comum.uCor3,
      },
      vertexShader: PARTICULA_VERT, fragmentShader: PARTICULA_FRAG,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    this.pontos = new THREE.Points(geo, this.material);
    this.pontos.frustumCulled = false;
    const tamanho = new THREE.Vector2();
    // O tamanho em pixels depende da tela e da câmera de cada desenho (painel principal e miniatura).
    this.pontos.onBeforeRender = (renderer, _cena, camera) => {
      renderer.getDrawingBufferSize(tamanho);
      const fov = camera.isPerspectiveCamera ? camera.fov : 40;
      this.material.uniforms.uEscala.value = tamanho.y / (2 * Math.tan(THREE.MathUtils.degToRad(fov) / 2));
    };
  }

  emitir(origem, raio, tam, duracao, subida, vento) {
    const i = this.proxima;
    this.proxima = (i + 1) % this.max;
    let x, y, z;
    do { x = Math.random() * 2 - 1; y = Math.random() * 2 - 1; z = Math.random() * 2 - 1; } while (x * x + y * y + z * z > 1);
    this.pos[i * 3] = origem.x + x * raio.x;
    this.pos[i * 3 + 1] = origem.y + y * raio.y;
    this.pos[i * 3 + 2] = origem.z + z * raio.z;
    this.vel[i * 3] = x * 0.08 + vento[0] * 0.8;
    this.vel[i * 3 + 1] = (0.35 + Math.random() * 0.3) * subida + vento[1];
    this.vel[i * 3 + 2] = z * 0.08 + vento[2] * 0.8;
    this.idade[i] = 0;
    this.duracao[i] = duracao * (0.7 + Math.random() * 0.6);
    this.base[i] = tam * (0.7 + Math.random() * 0.6);
    this.vida[i] = 0;
  }

  atualizar(dt) {
    for (let i = 0; i < this.max; i++) {
      if (this.vida[i] >= 1) { this.tam[i] = 0; continue; }
      this.idade[i] += dt;
      const v = this.idade[i] / this.duracao[i];
      if (v >= 1) { this.vida[i] = 1; this.tam[i] = 0; continue; }
      this.vel[i * 3 + 1] += 0.5 * dt;
      this.pos[i * 3] += this.vel[i * 3] * dt;
      this.pos[i * 3 + 1] += this.vel[i * 3 + 1] * dt;
      this.pos[i * 3 + 2] += this.vel[i * 3 + 2] * dt;
      this.vida[i] = v;
      this.tam[i] = this.base[i] * (1 - 0.55 * v);
    }
    this.atrPos.needsUpdate = this.atrVida.needsUpdate = this.atrTam.needsUpdate = true;
  }
}

// ------------------------------------------------------------------ chamas presas a ossos
// Labaredas, partículas e luzes de fogo presas a ossos (ou a qualquer Object3D). Elas vivem no espaço
// local do `dono` (o grupo da mascote de código ou a raiz do modelo do arquivo) e sobem sempre para cima,
// mesmo com o osso girando. A cor, a força e o vento vêm de `comum` (os mesmos para tudo).
class Chamas {
  constructor(comum, geoChama) {
    this.comum = comum;
    this.geoChama = geoChama;
    this.grupo = new THREE.Group();
    this.grupo.name = "chamas";
    this.particulas = new Particulas(comum);
    this.grupo.add(this.particulas.pontos);
    this.labaredas = [];
    this.emissores = [];
    this.luzes = [];
    this._v = v3();
    this._inv = new THREE.Matrix4();
  }

  limpar() {
    for (const l of this.labaredas) {
      l.malha.material.dispose();
      this.grupo.remove(l.malha);
    }
    for (const z of this.luzes) this.grupo.remove(z.luz);
    this.labaredas = [];
    this.emissores = [];
    this.luzes = [];
  }

  labareda(osso, local, larg, alt, adiante = 0.05) {
    const c = this.comum;
    const uniforms = {
      uTamanho: { value: new THREE.Vector2(larg, alt) }, uSemente: { value: Math.random() }, uAdiante: { value: adiante },
      uTempo: c.uTempo, uVento: c.uVento, uIntensidade: c.uIntensidade, uCorte: c.uCorte, uCor1: c.uCor1, uCor2: c.uCor2, uCor3: c.uCor3,
    };
    const material = new THREE.ShaderMaterial({
      uniforms, vertexShader: LABAREDA_VERT, fragmentShader: LABAREDA_FRAG,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    const malha = new THREE.Mesh(this.geoChama, material);
    malha.frustumCulled = false;
    this.grupo.add(malha);
    this.labaredas.push({ osso, local: local.clone(), larg, alt, malha, uniforms, semente: uniforms.uSemente.value });
  }

  emissor(osso, local, raio, taxa, tam, vida) {
    this.emissores.push({ osso, local: local.clone(), raio: raio.clone(), taxa, tam, vida, acumulado: Math.random() });
  }

  // As luzes ficam sempre criadas (força 0 = apagada): mudar o número de luzes recompila os materiais.
  luz(osso, local, forca, alcance = 1.6) {
    const luz = new THREE.PointLight(0xffffff, 0, alcance, 2);
    this.grupo.add(luz);
    this.luzes.push({ osso, local: local.clone(), forca, luz });
  }

  atualizar(dt, dono, intensidade, altura, vento) {
    const t = this.comum.uTempo.value;
    this.comum.uIntensidade.value = intensidade;
    this.comum.uVento.value.set(vento[0], vento[1], vento[2]);
    const inv = this._inv.copy(dono.matrixWorld).invert();
    const local = (osso, p) => osso.localToWorld(this._v.copy(p)).applyMatrix4(inv);
    const forca = Math.min(intensidade, 1.6);
    for (const l of this.labaredas) {
      l.malha.position.copy(local(l.osso, l.local));
      const tremor = 0.9 + 0.12 * Math.sin(t * 7.3 + l.semente * 20) + 0.06 * Math.sin(t * 13.1 + l.semente * 7);
      l.uniforms.uTamanho.value.set(l.larg * (0.75 + 0.25 * forca), l.alt * altura * tremor * (0.6 + 0.4 * forca));
      l.malha.visible = intensidade > 0.01;
    }
    for (const e of this.emissores) {
      e.acumulado += e.taxa * intensidade * dt;
      if (e.acumulado < 1) continue;
      const origem = local(e.osso, e.local).clone();
      while (e.acumulado >= 1) {
        e.acumulado -= 1;
        this.particulas.emitir(origem, e.raio, e.tam, e.vida, altura, vento);
      }
    }
    this.particulas.atualizar(dt);
    const pisca = 0.85 + 0.15 * Math.sin(t * 9.1) * Math.sin(t * 5.3);
    for (const z of this.luzes) {
      z.luz.position.copy(local(z.osso, z.local));
      z.luz.color.copy(this.comum.uCor2.value);
      z.luz.intensity = z.forca * intensidade * pisca;
    }
  }
}

// ------------------------------------------------------------------ poses
function novaPose(p) {
  return { mandibula: 0, fechar: 0, chamas: 1, vento: [0, 0, 0], olhar: 1, apoio: SOLAS, y: 0, olhosX: 0, ...p };
}

function misturarPoses(a, b, w) {
  const r = {};
  for (const nome of new Set([...Object.keys(a.r), ...Object.keys(b.r)])) {
    const x = a.r[nome] || ZERO3, y = b.r[nome] || ZERO3;
    r[nome] = [misturar(x[0], y[0], w), misturar(x[1], y[1], w), misturar(x[2], y[2], w)];
  }
  const lista = (p, q) => p.map((v, i) => misturar(v, q[i], w));
  const num = (chave) => misturar(a[chave], b[chave], w);
  return {
    r, caudaCurva: lista(a.caudaCurva, b.caudaCurva), caudaLado: lista(a.caudaLado, b.caudaLado), vento: lista(a.vento, b.vento),
    mandibula: num("mandibula"), fechar: num("fechar"), chamas: num("chamas"), olhar: num("olhar"), y: num("y"), olhosX: num("olhosX"),
    apoio: b.apoio, apoioAnterior: a.apoio, peso: w,
  };
}

// ------------------------------------------------------------------ a mascote
export class Mascote3D {
  constructor(ajustes = {}) {
    this.aj = { ...AJUSTES_PADRAO, ...ajustes };
    this.grupo = new THREE.Group();
    this.grupo.name = "mascote";
    this.velocidade = 1;
    this.alvoOlhar = null; // ponto do mundo para onde ela olha (o mouse, no laboratório)
    this.aoReconstruir = null;
    this.modo = "parado";
    this.modoAnterior = null;
    this.transicao = 1;
    this.tempo = 0;
    this.proximaPiscada = 2;
    this.piscando = 0;
    this.olhar = new THREE.Vector2();
    this.fechamento = 0; // olho: 0 aberto, 1 fechado (piscada ou pose)
    this._v = v3();
    this._v2 = v3();
    this._q1 = new THREE.Quaternion();
    this._q2 = new THREE.Quaternion();
    this._inv = new THREE.Matrix4();
    this.texPelo = texturaPelo();
    this.comum = {
      // uCorte: transparência mínima do fogo (a janela da tela usa um corte para não deixar borrão na cor-chave).
      uTempo: { value: 0 }, uIntensidade: { value: 1 }, uVento: { value: v3() }, uCorte: { value: 0 },
      uCor1: { value: new THREE.Color() }, uCor2: { value: new THREE.Color() }, uCor3: { value: new THREE.Color() },
    };
    this.mat = this._criarMateriais();
    this.geo = this._criarGeometriasFixas();
    this.chamas = new Chamas(this.comum, this.geo.chama);
    this.poseAtual = null; // a última pose aplicada (o modelo do arquivo copia dela)
    this.construir();
    this.aplicarAparencia();
    this.atualizar(0);
  }

  // Troca os ajustes; refaz a malha só se algum ajuste de forma mudou.
  definirAjustes(novos) {
    const antes = this.aj;
    this.aj = { ...AJUSTES_PADRAO, ...novos };
    const mudouForma = [...CHAVES_DE_FORMA].some((chave) => antes[chave] !== this.aj[chave]);
    if (mudouForma) this.construir();
    this.aplicarAparencia();
  }

  definirModo(modo) {
    if (modo === this.modo) return;
    if (this.modo === "pose") {
      this.modoAnterior = null;
      this.transicao = 1;
    } else {
      this.modoAnterior = this.modo;
      this.transicao = 0;
    }
    this.modo = modo;
  }

  // Volta todos os ossos à posição de descanso (para posar do zero).
  zerarPose() {
    for (const osso of this.listaOssos) osso.rotation.set(0, 0, 0);
    this.ossos.raiz.position.set(0, 0, 0);
    this.grupo.updateMatrixWorld(true);
    this.ossos.raiz.position.y = this._correcao(SOLAS);
  }

  _criarMateriais() {
    this.aro = { cor: { value: new THREE.Color() }, forca: { value: 0.5 } };
    return {
      pelo: materialComAro({ roughness: 0.82, metalness: 0, bumpMap: this.texPelo, bumpScale: 0.6 }, this.aro),
      orelhaDentro: materialComAro({ roughness: 0.9, bumpMap: this.texPelo, bumpScale: 0.4 }, this.aro),
      chifre: materialComAro({ roughness: 0.38, metalness: 0.1 }, this.aro),
      nariz: new THREE.MeshPhysicalMaterial({ color: 0x0b0710, roughness: 0.28, clearcoat: 1, clearcoatRoughness: 0.2 }),
      boca: new THREE.MeshBasicMaterial({ color: 0x050307 }),
      bocaDentro: new THREE.MeshStandardMaterial({ color: 0x3b0f24, roughness: 0.55 }),
      gema: new THREE.MeshPhysicalMaterial({ roughness: 0.12, metalness: 0.05, clearcoat: 1, flatShading: true, emissiveIntensity: 0.9 }),
      garra: new THREE.MeshPhysicalMaterial({ roughness: 0.3, clearcoat: 0.6, emissiveIntensity: 0.35 }),
      olho: new THREE.ShaderMaterial({
        uniforms: { uCor: { value: new THREE.Color() }, uBrilho: { value: 1.5 }, uPupila: { value: 0.3 } },
        vertexShader: OLHO_VERT, fragmentShader: OLHO_FRAG,
      }),
    };
  }

  _criarGeometriasFixas() {
    const orelha = new THREE.ConeGeometry(0.4, 1, 24, 1);
    orelha.translate(0, 0.5, 0).scale(1, 1, 0.45);
    const orelhaDentro = new THREE.ConeGeometry(0.27, 0.8, 24, 1);
    orelhaDentro.translate(0, 0.4, 0).scale(1, 1, 0.3);
    const chama = new THREE.PlaneGeometry(1, 1, 1, 6);
    chama.translate(0, 0.5, 0);
    return {
      esfera: new THREE.SphereGeometry(1, 40, 28),
      palpebra: new THREE.SphereGeometry(1.07, 32, 12, 0, Math.PI * 2, 0, Math.PI / 2),
      garra: new THREE.ConeGeometry(0.014, 0.05, 8),
      gema: new THREE.OctahedronGeometry(1, 0),
      orelha, orelhaDentro, chama,
      mechaReta: geometriaMecha(0.15),
      mechaCurva: geometriaMecha(0.45),
    };
  }

  aplicarAparencia() {
    const aj = this.aj, m = this.mat;
    m.pelo.color.set(aj.corPelo);
    m.orelhaDentro.color.set(aj.corOrelhaDentro);
    m.chifre.color.set(aj.corChifres);
    this.aro.cor.value.set(aj.corBrilho);
    this.aro.forca.value = aj.forcaBrilho;
    m.gema.color.set(aj.corGema);
    m.gema.emissive.set(aj.corGema);
    m.garra.color.set(aj.corGema).multiplyScalar(0.6);
    m.garra.emissive.set(aj.corGema);
    m.olho.uniforms.uCor.value.set(aj.corOlhos);
    m.olho.uniforms.uBrilho.value = aj.brilhoOlhos;
    m.olho.uniforms.uPupila.value = aj.pupila;
    this.comum.uCor1.value.set(aj.corChamaNucleo);
    this.comum.uCor2.value.set(aj.corChamaMeio);
    this.comum.uCor3.value.set(aj.corChamaBorda);
  }

  _limpar() {
    const fixas = new Set(Object.values(this.geo));
    this.grupo.traverse((o) => {
      if (o === this.chamas.particulas.pontos) return;
      if (o.geometry && !fixas.has(o.geometry)) o.geometry.dispose();
      if (o.material && o.material.isShaderMaterial && o.material !== this.mat.olho) o.material.dispose();
    });
    if (this.esqueleto) this.esqueleto.dispose();
    this.grupo.clear();
  }

  construir() {
    this._limpar();
    const aj = this.aj, P = aj.pernas, L = aj.corpo, H = aj.cabeca, pel = aj.pelagem;
    const pelo = this.mat.pelo;
    this.ossos = {};
    this.listaOssos = [];
    this.contatos = {};
    this.olhos = [];
    this.malhasPele = [];
    const tufos = new Map();

    const alvo = (pai) => (typeof pai === "string" ? this.ossos[pai] : pai);
    const osso = (nome, pai, x, y, z) => {
      const b = new THREE.Bone();
      b.name = nome;
      b.position.set(x, y, z);
      alvo(pai).add(b);
      this.ossos[nome] = b;
      this.listaOssos.push(b);
      return b;
    };
    const marcador = (nome, pai, x, y, z) => {
      const m = new THREE.Object3D();
      m.position.set(x, y, z);
      alvo(pai).add(m);
      this.contatos[nome] = m;
    };
    const elipsoide = (pai, mat, x, y, z, rx, ry, rz, rot) => {
      const m = new THREE.Mesh(this.geo.esfera, mat);
      m.position.set(x, y, z);
      m.scale.set(rx, ry, rz);
      if (rot) m.rotation.set(rot[0], rot[1], rot[2]);
      m.castShadow = m.receiveShadow = true;
      alvo(pai).add(m);
      return m;
    };
    const tufo = (pai, base, dir, dobra, comp, larg, curvo = true) => {
      if (pel <= 0.01) return;
      const o = alvo(pai);
      if (!tufos.has(o)) tufos.set(o, new Acumulador());
      tufos.get(o).adicionar(curvo ? this.geo.mechaCurva : this.geo.mechaReta, matrizMecha(base, dir, dobra, comp * pel, larg * Math.sqrt(pel)));
    };

    // ---------------------------------------------------------- esqueleto
    const raiz = new THREE.Bone();
    raiz.name = "raiz";
    this.grupo.add(raiz);
    this.ossos.raiz = raiz;
    this.listaOssos.push(raiz);

    const a1 = 0.3 * P, a2 = 0.27 * P, t1 = 0.3 * P, t2 = 0.27 * P, t3 = 0.19 * P;
    const yOmbro = 0.085 + a1 + a2;
    const yQuadril = 0.075 + t1 * 0.8 + t2 * 0.75 + t3 * 0.97;
    const dy = (yOmbro + 0.11 - (yQuadril + 0.07)) / 2;
    osso("corpo", "raiz", 0, yQuadril + 0.07, -0.4 * L);
    osso("coluna", "corpo", 0, dy, 0.34 * L);
    osso("peito", "coluna", 0, dy, 0.34 * L);
    osso("pescoco", "peito", 0, 0.13, 0.13);
    osso("pescoco2", "pescoco", 0, 0.14, 0.06);
    osso("cabeca", "pescoco2", 0, 0.12, 0.03);
    for (const [lado, s] of [["E", 1], ["D", -1]]) {
      osso("ombro" + lado, "peito", s * 0.15, -0.11, 0.06);
      osso("cotovelo" + lado, "ombro" + lado, 0, -a1, -0.03);
      osso("pulso" + lado, "cotovelo" + lado, 0, -a2, 0.03);
      osso("coxa" + lado, "corpo", s * 0.15, -0.07, -0.04);
      osso("joelho" + lado, "coxa" + lado, 0, -t1 * 0.8, t1 * 0.6);
      osso("jarrete" + lado, "joelho" + lado, 0, -t2 * 0.75, -t2 * 0.65);
      osso("pataT" + lado, "jarrete" + lado, 0, -t3 * 0.97, t3 * 0.15);
    }
    const lc = (1.3 * aj.cauda) / (N_CAUDA - 1);
    const angulos = [-8, 8, 26, 46, 66, 82, 95].map((g) => (g * Math.PI) / 180);
    osso("cauda0", "corpo", 0, 0.09, -0.27);
    for (let k = 1; k < N_CAUDA; k++) {
      const th = angulos[k - 1];
      osso("cauda" + k, "cauda" + (k - 1), 0, Math.sin(th) * lc, -Math.cos(th) * lc);
    }
    // A cabeça é desenhada em unidades do crânio (raio 1); o grupo escala tudo junto.
    const cranio = new THREE.Group();
    cranio.position.set(0, 0.13, 0.07);
    cranio.scale.setScalar(0.38 * H);
    this.ossos.cabeca.add(cranio);
    osso("mandibula", cranio, 0, -0.4, 0.35);
    const gruposOrelha = {};
    for (const [lado, s] of [["E", 1], ["D", -1]]) {
      const b = osso("orelha" + lado, cranio, s * 0.56, 0.58, -0.12);
      const g = new THREE.Group();
      g.rotation.set(-0.1, s * 0.3, -s * 0.36);
      b.add(g);
      gruposOrelha[lado] = g;
    }

    this.grupo.updateMatrixWorld(true);
    const pos = (nome) => this.grupo.worldToLocal(this.ossos[nome].getWorldPosition(v3()));
    const idx = (nome) => this.listaOssos.indexOf(this.ossos[nome]);
    const no = (nome, dx, dy2, dz, rx, ry) => ({ p: pos(nome).add(v3(dx, dy2, dz)), rx, ry, osso: idx(nome) });
    const pele = (nos, opcoes) => {
      const m = new THREE.SkinnedMesh(construirTubo(nos, opcoes), pelo);
      m.castShadow = m.receiveShadow = true;
      m.frustumCulled = false;
      this.grupo.add(m);
      this.malhasPele.push(m);
    };

    // ---------------------------------------------------------- tronco e pescoço
    pele([
      no("corpo", 0, 0, -0.13, 0.25, 0.26),
      no("corpo", 0, 0, 0.04, 0.27, 0.28),
      no("coluna", 0, 0, 0, 0.255, 0.27),
      no("peito", 0, -0.01, -0.03, 0.285, 0.325),
      no("peito", 0, 0.04, 0.11, 0.255, 0.3),
      no("pescoco", 0, 0, 0, 0.205, 0.215),
      no("pescoco2", 0, 0, 0, 0.185, 0.195),
      no("cabeca", 0, 0.05, 0.03, 0.17, 0.17),
    ], { aneis: 56, lados: 28, tampaInicio: 1, tampaFim: 1, escalaUV: 0.16 });
    marcador("assento", "corpo", 0, -0.25, -0.12);
    marcador("barriga", "coluna", 0, -0.26, 0);
    marcador("peitoBaixo", "peito", 0, -0.31, 0.05);

    for (let fila = 0; fila < 3; fila++) {
      for (let j = -2; j <= 2; j++) {
        tufo("peito", v3(j * 0.075, 0.06 - fila * 0.1, 0.27 - fila * 0.035 - Math.abs(j) * 0.035),
          v3(j * 0.18, -0.75, 0.5), v3(0, -0.2, 1), 0.17 - fila * 0.02, 0.1);
      }
    }
    for (const s of [1, -1]) tufo("pescoco", v3(s * 0.17, 0.02, -0.04), v3(s * 0.55, -0.15, -0.8), v3(s, 0, 0), 0.16, 0.1);
    tufo("pescoco", v3(0, 0.16, -0.1), v3(0, 0.35, -0.95), v3(0, 1, 0), 0.15, 0.1);
    tufo("pescoco2", v3(0, 0.15, -0.08), v3(0, 0.3, -1), v3(0, 1, 0), 0.14, 0.1);
    for (const z of [-0.12, 0.04, 0.2]) tufo("coluna", v3(0, 0.24, z), v3(0, 0.45, -0.9), v3(0, 1, 0), 0.11, 0.09);
    tufo("corpo", v3(0, 0.22, -0.08), v3(0, 0.4, -0.9), v3(0, 1, 0), 0.11, 0.09);

    // ---------------------------------------------------------- patas
    for (const [lado, s] of [["E", 1], ["D", -1]]) {
      const ombro = "ombro" + lado, cotovelo = "cotovelo" + lado, pulso = "pulso" + lado;
      pele([
        no(ombro, 0, 0.12, 0, 0.13, 0.15), no(ombro, 0, 0, 0, 0.13, 0.15), no(cotovelo, 0, 0, 0, 0.095, 0.105),
        no(pulso, 0, 0.015, 0, 0.075, 0.08), no(pulso, 0, -0.035, 0.01, 0.078, 0.082),
      ], { aneis: 30, lados: 18, tampaInicio: 1, tampaFim: 1 });
      tufo(cotovelo, v3(0, 0.01, -0.07), v3(0, -0.35, -0.95), v3(0, -1, 0), 0.11, 0.07);
      tufo(pulso, v3(0, 0.07, -0.05), v3(0, -0.2, -1), v3(0, -1, 0), 0.08, 0.06);
      this._pata(pulso, elipsoide, -0.042, 0.045);
      marcador("sola" + lado, pulso, 0, -0.085, 0.04);

      const coxa = "coxa" + lado, joelho = "joelho" + lado, jarrete = "jarrete" + lado, pata = "pataT" + lado;
      pele([
        no(coxa, 0, 0.1, -0.02, 0.15, 0.18), no(coxa, 0, -0.02, 0.02, 0.15, 0.17), no(joelho, 0, 0, 0, 0.105, 0.115),
        no(jarrete, 0, 0, 0, 0.075, 0.08), no(pata, 0, 0.015, 0, 0.07, 0.074), no(pata, 0, -0.03, 0.01, 0.072, 0.076),
      ], { aneis: 34, lados: 18, tampaInicio: 1, tampaFim: 1 });
      elipsoide(coxa, pelo, s * 0.01, -0.1 * P, 0.05 * P, 0.135, 0.2, 0.16, [-0.64, 0, 0]);
      tufo(coxa, v3(s * 0.03, -0.14, -0.13), v3(s * 0.2, -0.45, -0.85), v3(0, -1, 0), 0.12, 0.08);
      tufo(jarrete, v3(0, 0.0, -0.06), v3(0, -0.1, -1), v3(0, -1, 0), 0.08, 0.06);
      this._pata(pata, elipsoide, -0.035, 0.05);
      marcador("solaT" + lado, pata, 0, -0.075, 0.04);
    }

    // ---------------------------------------------------------- cauda
    const raios = [0.1, 0.13, 0.17, 0.21, 0.23, 0.21, 0.17, 0.1].map((r) => r * aj.caudaVolume);
    const nosCauda = [{ p: pos("cauda0").add(v3(0, -0.04, 0.12)), rx: 0.09, ry: 0.09, osso: idx("corpo") }];
    for (let k = 0; k < N_CAUDA; k++) nosCauda.push({ p: pos("cauda" + k), rx: raios[k], ry: raios[k], osso: idx("cauda" + k) });
    const dirFim = pos("cauda7").sub(pos("cauda6")).normalize();
    nosCauda.push({ p: pos("cauda7").addScaledVector(dirFim, lc * 0.7), rx: 0.035, ry: 0.035, osso: idx("cauda7") });
    pele(nosCauda, { aneis: 64, lados: 20, tampaFim: 1.4, escalaUV: 0.14 });
    this.eixosCauda = [];
    for (let k = 0; k < N_CAUDA; k++) {
      const d = k < N_CAUDA - 1 ? pos("cauda" + (k + 1)).sub(pos("cauda" + k)).normalize() : dirFim.clone();
      this.eixosCauda.push(new THREE.Vector3().crossVectors(d, EIXO_X).normalize());
      if (k === 0) continue;
      const e2 = new THREE.Vector3().crossVectors(d, EIXO_X).normalize();
      const quantos = k >= 2 && k <= 5 ? 5 : 4;
      for (let j = 0; j < quantos; j++) {
        const a = (j / quantos) * Math.PI * 2 + k * 0.45;
        const radial = EIXO_X.clone().multiplyScalar(Math.cos(a)).addScaledVector(e2, Math.sin(a));
        tufo("cauda" + k, radial.clone().multiplyScalar(raios[k] * 0.6), d.clone().multiplyScalar(0.95).addScaledVector(radial, 0.3),
          radial, (0.22 + 0.12 * Math.sin((Math.PI * k) / 7)) * aj.caudaVolume, 0.16 * aj.caudaVolume);
      }
    }

    // ---------------------------------------------------------- cabeça
    this._cabeca(cranio, gruposOrelha, elipsoide, tufo);

    for (const [o, acumulador] of tufos) {
      const m = new THREE.Mesh(acumulador.geometria(), pelo);
      m.castShadow = m.receiveShadow = true;
      o.add(m);
    }

    // ---------------------------------------------------------- chamas
    this.chamas.limpar();
    this._criarChamas();
    this.grupo.add(this.chamas.grupo);

    this.grupo.updateMatrixWorld(true);
    this.esqueleto = new THREE.Skeleton(this.listaOssos);
    for (const m of this.malhasPele) m.bind(this.esqueleto);
    if (this.aoReconstruir) this.aoReconstruir();
  }

  // Pata com três dedos e garras; o osso fica no pulso (ou no tornozelo, nas de trás).
  _pata(nome, elipsoide, y, z) {
    const osso = this.ossos[nome], pelo = this.mat.pelo;
    elipsoide(osso, pelo, 0, y, z, 0.095, 0.055, 0.12);
    for (const dx of [-0.046, 0, 0.046]) {
      const frente = dx === 0 ? 0.012 : 0;
      elipsoide(osso, pelo, dx, y - 0.008, z + 0.08 + frente, 0.034, 0.03, 0.038);
      const garra = new THREE.Mesh(this.geo.garra, this.mat.garra);
      garra.position.set(dx, y - 0.02, z + 0.117 + frente);
      garra.rotation.x = Math.PI / 2 + 0.6;
      osso.add(garra);
    }
  }

  _cabeca(cranio, gruposOrelha, elipsoide, tufo) {
    const aj = this.aj, pelo = this.mat.pelo;
    elipsoide(cranio, pelo, 0, 0, 0, 1.0, 0.9, 0.98);
    for (const s of [1, -1]) elipsoide(cranio, pelo, s * 0.48, -0.3, 0.1, 0.58, 0.48, 0.58);
    // Focinho de raposa: uma base larga e uma ponta mais fina, que cresce com o ajuste.
    const f = aj.focinho, fc = 0.52 + 0.42 * f, frz = 0.38 * f;
    elipsoide(cranio, pelo, 0, -0.24, 0.5, 0.44, 0.34, 0.46);
    elipsoide(cranio, pelo, 0, -0.3, fc, 0.27, 0.22, frz);
    elipsoide(cranio, this.mat.nariz, 0, -0.2, fc + frz * 0.93, 0.115, 0.075, 0.085);
    const naSuperficie = (cy, rx, ry, cz, rz, x, y) => cz + rz * Math.sqrt(Math.max(0, 1 - (x / rx) ** 2 - ((y - cy) / ry) ** 2));
    const naBoca = (x, y) => v3(x, y, Math.max(naSuperficie(-0.3, 0.27, 0.22, fc, frz, x, y), naSuperficie(-0.24, 0.44, 0.34, 0.5, 0.46, x, y)) + 0.008);
    const sorriso = new THREE.CatmullRomCurve3([naBoca(-0.3, -0.34), naBoca(-0.17, -0.41), naBoca(-0.07, -0.42), naBoca(0, -0.4),
      naBoca(0.07, -0.42), naBoca(0.17, -0.41), naBoca(0.3, -0.34)]);
    cranio.add(new THREE.Mesh(new THREE.TubeGeometry(sorriso, 32, 0.014, 6), this.mat.boca));
    cranio.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.LineCurve3(naBoca(0, -0.27), naBoca(0, -0.4)), 4, 0.013, 6), this.mat.boca));

    const mandibula = this.ossos.mandibula;
    elipsoide(mandibula, pelo, 0, -0.06, fc - 0.1 - 0.35, 0.24, 0.11, frz * 0.95);
    elipsoide(mandibula, this.mat.bocaDentro, 0, 0.04, fc - 0.1 - 0.35, 0.2, 0.09, frz * 0.8);

    // Olhos: globo com a íris (gira para olhar) e pálpebra de pelo (gira para piscar).
    const ro = 0.29 * aj.olhos;
    for (const s of [1, -1]) {
      const x = 0.2 + 0.55 * ro, y = 0.1, profundidade = ro * 0.55;
      const superficie = 0.98 * Math.sqrt(Math.max(0.05, 1 - x * x - (y / 0.9) ** 2));
      const g = new THREE.Group();
      g.position.set(s * x, y, superficie - profundidade * 0.55);
      g.rotation.set(-0.05, s * 0.18, s * 0.12, "YXZ");
      g.scale.set(ro * 1.08, ro, profundidade);
      cranio.add(g);
      const globo = new THREE.Mesh(this.geo.esfera, this.mat.olho);
      const palpebra = new THREE.Mesh(this.geo.palpebra, pelo);
      g.add(globo, palpebra);
      this.olhos.push({ globo, palpebra });
    }

    const gema = new THREE.Mesh(this.geo.gema, this.mat.gema);
    gema.position.set(0, 0.5, 0.79);
    gema.rotation.x = -0.6;
    gema.scale.set(0.09, 0.14, 0.045);
    gema.visible = !!aj.gema;
    cranio.add(gema);

    const c = aj.chifres;
    if (c > 0.05) {
      for (const s of [1, -1]) {
        const base = v3(s * 0.28, 0.66, 0.5);
        const raio = 0.11 * Math.sqrt(c);
        const nos = [
          { p: base.clone(), rx: raio, ry: raio },
          { p: base.clone().add(v3(s * 0.03 * c, 0.22 * c, -0.03 * c)), rx: raio * 0.8, ry: raio * 0.8 },
          { p: base.clone().add(v3(s * 0.07 * c, 0.38 * c, -0.14 * c)), rx: raio * 0.52, ry: raio * 0.52 },
          { p: base.clone().add(v3(s * 0.09 * c, 0.45 * c, -0.32 * c)), rx: raio * 0.18, ry: raio * 0.18 },
        ];
        const chifre = new THREE.Mesh(construirTubo(nos, { aneis: 18, lados: 12, tampaInicio: 1, tampaFim: 1.2 }), this.mat.chifre);
        chifre.castShadow = true;
        cranio.add(chifre);
      }
    }

    for (const [lado, s] of [["E", 1], ["D", -1]]) {
      const g = gruposOrelha[lado], alt = 1.05 * aj.orelhas;
      const fora = new THREE.Mesh(this.geo.orelha, pelo);
      fora.scale.set(1, alt, 1);
      fora.castShadow = true;
      const dentro = new THREE.Mesh(this.geo.orelhaDentro, this.mat.orelhaDentro);
      dentro.scale.set(1, alt, 1);
      dentro.position.z = 0.11;
      dentro.rotation.x = -0.06;
      g.add(fora, dentro);
      for (const x of [-0.09, 0.09]) tufo(g, v3(x, 0.08, 0.12), v3(x * 2, 1, 0.35), v3(0, 0, 1), 0.4, 0.2);
      // Bochechas espetadas, para trás e para fora.
      tufo(cranio, v3(s * 0.84, -0.3, 0.02), v3(s * 0.9, -0.3, -0.35), v3(0, 0.3, -1), 0.6, 0.32);
      tufo(cranio, v3(s * 0.8, -0.1, -0.14), v3(s * 0.8, 0.12, -0.6), v3(0, 0.3, -1), 0.5, 0.28);
      tufo(cranio, v3(s * 0.72, -0.52, 0.04), v3(s * 0.6, -0.75, -0.3), v3(0, 0.2, -1), 0.46, 0.28);
      tufo(cranio, v3(s * 0.58, -0.62, 0.22), v3(s * 0.35, -0.9, -0.1), v3(0, 0, -1), 0.28, 0.2);
    }
    tufo(cranio, v3(0, 0.84, -0.05), v3(0, 0.5, -0.9), v3(0, 1, 0), 0.26, 0.24);
  }

  _criarChamas() {
    const aj = this.aj, ch = this.chamas;
    const labareda = (nome, x, y, z, larg, alt, adiante = 0.05) => ch.labareda(this.ossos[nome], v3(x, y, z), larg, alt, adiante);
    const emissor = (nome, x, y, z, rx, ry, rz, taxa, tam, vida) => ch.emissor(this.ossos[nome], v3(x, y, z), v3(rx, ry, rz), taxa, tam, vida);
    ch.luz(this.ossos.peito, v3(0, 0.32, 0.1), aj.chamasOmbros ? 0.8 : 0);
    ch.luz(this.ossos.cauda7, v3(0, 0.2, 0), aj.chamasCauda ? 1.0 : 0, 1.8);
    ch.luz(this.ossos.peito, v3(0, -0.45, 0.12), aj.chamasPatas ? 0.5 : 0, 1.2);
    if (aj.chamasOmbros) {
      for (const s of [1, -1]) {
        labareda("peito", s * 0.18, 0.18, 0.02, 0.5, 0.85, 0.14);
        labareda("pescoco", s * 0.15, 0.06, -0.04, 0.38, 0.6, 0.1);
      }
      labareda("peito", 0, 0.26, -0.08, 0.55, 0.95, 0.02);
      emissor("peito", 0, 0.2, 0, 0.24, 0.07, 0.18, 55, 0.22, 0.8);
    }
    if (aj.chamasCostas) {
      labareda("coluna", 0, 0.24, 0, 0.42, 0.55, 0.02);
      labareda("corpo", 0, 0.22, 0.05, 0.36, 0.45, 0);
      emissor("coluna", 0, 0.24, 0, 0.16, 0.04, 0.24, 24, 0.16, 0.6);
    }
    if (aj.chamasPeito) {
      for (const s of [1, -1]) labareda("peito", s * 0.26, -0.06, 0.18, 0.34, 0.6, 0.1);
    }
    if (aj.chamasPatas) {
      for (const lado of ["E", "D"]) {
        labareda("pulso" + lado, 0, 0.02, 0.02, 0.32, 0.42, 0.12);
        labareda("cotovelo" + lado, 0, -0.12, 0, 0.26, 0.34, 0.1);
        labareda("pataT" + lado, 0, 0.02, 0.02, 0.3, 0.38, 0.1);
        for (const nome of ["pulso" + lado, "pataT" + lado]) emissor(nome, 0, 0.02, 0.02, 0.08, 0.04, 0.08, 12, 0.13, 0.5);
      }
    }
    if (aj.chamasCauda) {
      const v = aj.caudaVolume;
      for (const k of [2, 3, 4, 5, 6]) labareda("cauda" + k, 0, 0.14 * v, 0, (0.36 + k * 0.04) * v, (0.5 + k * 0.08) * v, 0.04);
      labareda("cauda7", 0, 0, 0, 0.7 * v, 1.25 * v, 0);
      for (const k of [3, 4, 5, 6, 7]) emissor("cauda" + k, 0, 0.08 * v, 0, 0.14 * v, 0.08 * v, 0.14 * v, 14 + k * 4, 0.18 * v, 0.75);
    }
  }

  // ---------------------------------------------------------------- animação
  atualizar(dt) {
    dt = limitar(dt, 0, 0.05);
    this.comum.uTempo.value += dt;
    this.tempo += dt * this.velocidade;
    if (this.modo === "pose") {
      this._piscar(dt, 0);
      this.grupo.updateMatrixWorld(true);
      this.chamas.atualizar(dt, this.grupo, this.aj.chamas, this.aj.alturaChamas, ZERO3);
      return;
    }
    this.transicao = Math.min(1, this.transicao + dt / 0.45);
    let pose = this._pose(this.modo, this.tempo);
    if (this.modoAnterior && this.transicao < 1) {
      pose = misturarPoses(this._pose(this.modoAnterior, this.tempo), pose, suave01(this.transicao));
    }
    this.poseAtual = pose;
    this._aplicar(pose, dt);
    // As patas (ou a barriga, deitada) encostam no chão.
    const raiz = this.ossos.raiz;
    raiz.position.set(0, 0, 0);
    this.grupo.updateMatrixWorld(true);
    let correcao = this._correcao(pose.apoio);
    if (pose.apoioAnterior) correcao = misturar(this._correcao(pose.apoioAnterior), correcao, pose.peso);
    raiz.position.y = correcao + pose.y;
    this.grupo.updateMatrixWorld(true);
    this.chamas.atualizar(dt, this.grupo, this.aj.chamas * pose.chamas, this.aj.alturaChamas, pose.vento);
  }

  _correcao(nomes) {
    let menor = Infinity;
    for (const nome of nomes) {
      const c = this.contatos[nome];
      if (!c) continue;
      this.grupo.worldToLocal(c.getWorldPosition(this._v));
      menor = Math.min(menor, this._v.y);
    }
    return Number.isFinite(menor) ? -menor : 0;
  }

  _aplicar(pose, dt) {
    const o = this.ossos;
    for (const nome of ARTICULADOS) {
      const r = pose.r[nome] || ZERO3;
      o[nome].rotation.set(r[0], r[1], r[2]);
    }
    for (let k = 0; k < N_CAUDA; k++) {
      this._q1.setFromAxisAngle(EIXO_X, pose.caudaCurva[k]);
      this._q2.setFromAxisAngle(this.eixosCauda[k], pose.caudaLado[k]);
      o["cauda" + k].quaternion.copy(this._q2).multiply(this._q1);
    }
    o.mandibula.rotation.set(pose.mandibula, 0, 0);

    // Olhar para o alvo (o mouse): pescoço, cabeça e olhos dividem o giro.
    let yaw = 0, pitch = 0;
    if (this.alvoOlhar && pose.olhar > 0.01) {
      const cabeca = this.grupo.worldToLocal(o.cabeca.getWorldPosition(this._v));
      const alvo = this.grupo.worldToLocal(this._v2.copy(this.alvoOlhar)).sub(cabeca);
      yaw = limitar(Math.atan2(alvo.x, alvo.z), -1, 1) * pose.olhar;
      pitch = limitar(Math.atan2(alvo.y, Math.hypot(alvo.x, alvo.z)), -0.55, 0.55) * pose.olhar;
    }
    const k = 1 - Math.exp(-dt * 7);
    this.olhar.x += (yaw - this.olhar.x) * k;
    this.olhar.y += (pitch - this.olhar.y) * k;
    o.pescoco2.rotation.y += this.olhar.x * 0.35;
    o.cabeca.rotation.y += this.olhar.x * 0.45;
    o.pescoco.rotation.x -= this.olhar.y * 0.25;
    o.cabeca.rotation.x -= this.olhar.y * 0.45;
    for (const olho of this.olhos) {
      olho.globo.rotation.set(limitar(-this.olhar.y * 0.4 + pose.olhosX, -0.5, 0.5), limitar(this.olhar.x * 0.4, -0.45, 0.45), 0);
    }
    this._piscar(dt, pose.fechar);
  }

  _piscar(dt, fechar) {
    this.proximaPiscada -= dt;
    if (this.proximaPiscada <= 0) {
      this.piscando = 1e-4;
      this.proximaPiscada = 2.2 + Math.random() * 3.8;
    }
    let piscada = 0;
    if (this.piscando > 0) {
      this.piscando += dt;
      const u = this.piscando / 0.18;
      if (u >= 1) this.piscando = 0;
      else piscada = Math.sin(u * Math.PI);
    }
    // -90° esconde a pálpebra atrás do olho; +90° fecha o olho.
    const aberto = -Math.PI / 2 + this.aj.palpebra * 1.25;
    this.fechamento = Math.max(fechar, piscada);
    const angulo = misturar(aberto, Math.PI / 2 - 0.03, this.fechamento);
    for (const olho of this.olhos) olho.palpebra.rotation.set(angulo, 0, 0);
  }

  _pose(modo, t) {
    switch (modo) {
      case "andar": return this._poseAndar(t, false);
      case "correr": return this._poseAndar(t, true);
      case "sentar": return this._poseSentar(t);
      case "deitar": return this._poseDeitar(t);
      case "brincar": return this._poseBrincar(t);
      case "falar": return this._poseFalar(t);
      case "pensar": return this._posePensar(t);
      case "uivar": return this._poseUivar(t);
      default: return this._poseParado(t);
    }
  }

  _poseParado(t) {
    const r = {};
    const respira = Math.sin(t * 2.0);
    const yaw = 0.28 * Math.sin(t * 0.37) + 0.1 * Math.sin(t * 0.93 + 1);
    r.corpo = [0, 0, 0.015 * Math.sin(t * 0.6)];
    r.peito = [0.02 * respira, 0, 0];
    r.pescoco = [-0.04 + 0.015 * respira, 0, 0];
    r.pescoco2 = [0, yaw * 0.4, 0];
    r.cabeca = [0.05 * Math.sin(t * 0.51), yaw * 0.6, 0.08 * Math.sin(t * 0.29)];
    const tw1 = contracao(t, 4.3, 0), tw2 = contracao(t, 5.9, 2.2);
    r.orelhaE = [-0.05 - 0.3 * tw1, 0, -0.12 * tw1];
    r.orelhaD = [-0.05 - 0.3 * tw2, 0, 0.12 * tw2];
    const caudaCurva = [], caudaLado = [];
    for (let k = 0; k < N_CAUDA; k++) {
      caudaCurva.push(0.04 * Math.sin(t * 1.3 - k * 0.5));
      caudaLado.push(0.07 * Math.sin(t * 1.0 - k * 0.45));
    }
    return novaPose({ r, caudaCurva, caudaLado });
  }

  _poseAndar(t, correr) {
    const fase = t * (correr ? 2.3 : 1.4), w = fase * Math.PI * 2;
    const A = correr ? 0.62 : 0.34, B = correr ? 1.25 : 0.85, apoio = correr ? 0.42 : 0.62;
    // Andando, uma pata de cada vez; correndo, galope (as da frente juntas, depois as de trás).
    const fases = correr ? { E: [0, 0.52], D: [0.1, 0.62] } : { E: [0.25, 0], D: [0.75, 0.5] };
    const r = {};
    const flexao = correr ? 0.13 * Math.sin(w) : 0;
    r.corpo = [correr ? 0.06 * Math.sin(w + Math.PI) : 0, 0, correr ? 0 : 0.03 * Math.sin(w)];
    r.coluna = [flexao, 0, 0];
    r.peito = [flexao * 0.5, 0, 0];
    const cadeia = r.corpo[0] + r.coluna[0] + r.peito[0];
    for (const lado of ["E", "D"]) {
      const [fd, ft] = fases[lado];
      let [ang, lev] = passo(fase + fd, apoio, A);
      r["ombro" + lado] = [ang, 0, 0];
      r["cotovelo" + lado] = [B * lev, 0, 0];
      r["pulso" + lado] = [-(cadeia + ang + B * lev) + 0.8 * lev, 0, 0];
      [ang, lev] = passo(fase + ft, apoio, A);
      const joelho = B * 0.9 * lev, jarrete = -B * 1.05 * lev;
      r["coxa" + lado] = [ang, 0, 0];
      r["joelho" + lado] = [joelho, 0, 0];
      r["jarrete" + lado] = [jarrete, 0, 0];
      r["pataT" + lado] = [-(r.corpo[0] + ang + joelho + jarrete) - 0.35 * lev, 0, 0];
    }
    r.pescoco = [correr ? 0.22 : 0.02, 0, 0];
    r.pescoco2 = [0, 0, 0];
    r.cabeca = [(correr ? -0.12 : 0) + 0.035 * Math.sin(2 * w) - cadeia, 0, 0];
    const orelhaX = correr ? -0.65 : -0.08 + 0.04 * Math.sin(2 * w), orelhaZ = correr ? 0.2 : 0;
    r.orelhaE = [orelhaX, 0, -orelhaZ];
    r.orelhaD = [orelhaX, 0, orelhaZ];
    const caudaCurva = [], caudaLado = [];
    for (let k = 0; k < N_CAUDA; k++) {
      caudaCurva.push(correr ? -0.12 + 0.05 * Math.sin(w - k * 0.6) : -0.03 + 0.04 * Math.sin(2 * w - k * 0.5));
      caudaLado.push(correr ? 0.08 * Math.sin(w - k * 0.5) : 0.12 * Math.sin(w - k * 0.55));
    }
    return novaPose({ r, caudaCurva, caudaLado, vento: correr ? [0, -0.03, -0.5] : [0, 0, -0.14], chamas: correr ? 1.2 : 1 });
  }

  _poseSentar(t) {
    const p = this._poseParado(t), r = p.r;
    const inclinacao = -0.55;
    r.corpo = [inclinacao, 0, 0];
    r.coluna = [-0.05, 0, 0];
    r.peito = [-0.05 + 0.02 * Math.sin(t * 2.0), 0, 0];
    const cadeia = inclinacao - 0.1;
    for (const [lado, s] of [["E", 1], ["D", -1]]) {
      r["ombro" + lado] = [-cadeia, 0, 0];
      r["cotovelo" + lado] = [0, 0, 0];
      r["pulso" + lado] = [0, 0, 0];
      r["coxa" + lado] = [-0.15, 0, s * 0.12];
      r["joelho" + lado] = [1.5, 0, 0];
      r["jarrete" + lado] = [-2.0, 0, 0];
      r["pataT" + lado] = [-(inclinacao - 0.15 + 1.5 - 2.0), 0, 0];
    }
    r.pescoco[0] += 0.32;
    r.pescoco2[0] += 0.15;
    r.cabeca[0] += 0.22;
    const curva = [-0.5, -0.32, -0.25, -0.2, -0.12, -0.05, 0.05, 0.1], lado = [0, 0.22, 0.25, 0.28, 0.28, 0.25, 0.2, 0.1];
    p.caudaCurva = p.caudaCurva.map((c, k) => c + curva[k]);
    p.caudaLado = p.caudaLado.map((c, k) => c * 0.5 + lado[k]);
    p.apoio = ["solaE", "solaD", "assento"];
    return p;
  }

  _poseDeitar(t) {
    const r = {};
    const respira = Math.sin(t * 1.1);
    r.corpo = [0, 0, 0];
    r.coluna = [0.02 * respira, 0, 0];
    r.peito = [0.015 * respira, 0, 0];
    for (const [lado, s] of [["E", 1], ["D", -1]]) {
      r["ombro" + lado] = [-1.38, 0, 0];
      r["cotovelo" + lado] = [0.12, 0, 0];
      r["pulso" + lado] = [1.26, 0, 0];
      r["coxa" + lado] = [-1.15, 0, s * 0.25];
      r["joelho" + lado] = [2.25, 0, 0];
      r["jarrete" + lado] = [-1.55, 0, 0];
      r["pataT" + lado] = [-(-1.15 + 2.25 - 1.55), 0, 0];
    }
    r.pescoco = [0.5, 0, 0];
    r.pescoco2 = [0.32, 0.12, 0];
    r.cabeca = [0.18 + 0.02 * respira, 0.18, 0.12];
    r.orelhaE = [-0.45, 0, -0.25];
    r.orelhaD = [-0.45, 0, 0.25];
    const curva = [-0.55, -0.35, -0.2, -0.15, -0.1, -0.1, -0.05, 0], lado = [0.15, 0.35, 0.4, 0.42, 0.4, 0.35, 0.3, 0.2];
    const caudaCurva = curva.map((c, k) => c + 0.02 * Math.sin(t * 0.8 - k * 0.4));
    const caudaLado = lado.map((c, k) => c + 0.03 * Math.sin(t * 0.6 - k * 0.4));
    return novaPose({ r, caudaCurva, caudaLado, fechar: 1, chamas: 0.45, olhar: 0, apoio: [...SOLAS, "barriga", "peitoBaixo", "assento"] });
  }

  _poseBrincar(t) {
    const r = {};
    const inclinacao = 0.36, pulo = Math.max(0, Math.sin(t * 4.2)) ** 2;
    r.corpo = [inclinacao, 0, 0.05 * Math.sin(t * 7)];
    r.coluna = [0.05, 0, 0];
    r.peito = [0.05, 0, 0];
    const cadeia = inclinacao + 0.1;
    for (const lado of ["E", "D"]) {
      const ombro = -cadeia - 0.55, cotovelo = -1.0;
      r["ombro" + lado] = [ombro, 0, 0];
      r["cotovelo" + lado] = [cotovelo, 0, 0];
      r["pulso" + lado] = [-(cadeia + ombro + cotovelo), 0, 0];
      r["coxa" + lado] = [-inclinacao + 0.05, 0, 0];
      r["joelho" + lado] = [0.1, 0, 0];
      r["jarrete" + lado] = [-0.1, 0, 0];
      r["pataT" + lado] = [-0.05, 0, 0];
    }
    r.pescoco = [-0.35, 0, 0];
    r.pescoco2 = [-0.15, 0.1 * Math.sin(t * 3), 0];
    r.cabeca = [-0.05, 0, 0.18 * Math.sin(t * 2.5)];
    r.orelhaE = [0.12, 0, 0];
    r.orelhaD = [0.12, 0, 0];
    const caudaCurva = [], caudaLado = [];
    for (let k = 0; k < N_CAUDA; k++) {
      caudaCurva.push(0.1);
      caudaLado.push(0.3 * Math.sin(t * 15 - k * 0.55));
    }
    return novaPose({ r, caudaCurva, caudaLado, mandibula: 0.16 + 0.04 * Math.sin(t * 6), y: pulo * 0.06, chamas: 1.15 });
  }

  _poseFalar(t) {
    const p = this._poseParado(t);
    const boca = Math.max(0, Math.sin(t * 12.5)) * (0.55 + 0.45 * Math.sin(t * 3.1));
    p.mandibula = 0.04 + 0.26 * boca;
    p.r.cabeca[0] += 0.04 * Math.sin(t * 6.2);
    p.r.cabeca[2] += 0.05 * Math.sin(t * 1.7);
    p.r.orelhaE[0] += 0.08;
    p.r.orelhaD[0] += 0.08;
    return p;
  }

  _posePensar(t) {
    const p = this._poseParado(t * 0.6);
    p.r.cabeca = [-0.18, 0.28, 0.32];
    p.r.pescoco2 = [-0.05, 0.12, 0];
    p.r.orelhaD = [-0.45, 0, 0.25];
    p.olhosX = -0.3;
    p.olhar = 0.3;
    p.chamas = 0.8;
    p.caudaLado = p.caudaLado.map((c) => c * 0.5);
    return p;
  }

  _poseUivar(t) {
    const p = this._poseSentar(t * 0.5), r = p.r;
    r.pescoco[0] -= 0.3;
    r.pescoco2 = [r.pescoco2[0] - 0.3, 0, 0];
    r.cabeca = [r.cabeca[0] - 0.4, 0, 0.05 * Math.sin(t * 2)];
    r.orelhaE = [-0.4, 0, -0.1];
    r.orelhaD = [-0.4, 0, 0.1];
    p.mandibula = 0.42 + 0.05 * Math.sin(t * 5);
    p.fechar = 0.6;
    p.olhar = 0;
    p.chamas = 1.6;
    return p;
  }
}
