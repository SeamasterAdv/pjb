/* Visual da Beatriz em rede de pontos (em teste no laboratorio_visual.html).
 *
 * EsferaEmRede: a voz dela. Uma casca de poeira (milhares de pontos, deformados no shader) e uma rede
 * de nós ligados por linhas (plexus), que se deformam juntas pela mesma soma de ondas: o nível da voz
 * aumenta as ondas, e os graves, médios e agudos pesam ondas diferentes.
 *
 * MontanhaDePontos: a voz de quem fala com ela. Uma nuvem de pontos com um relevo fixo (picos e
 * cristas) que sobe com o nível do microfone e treme com as faixas do espectro (graves no meio).
 *
 * Os dois recebem o som de fora (voz(nivel, bandas) a cada quadro), então servem tanto para o áudio
 * do navegador (laboratório e app do celular) quanto para os níveis que o Python mandar (painel e login).
 *
 * O Three.js vem por caminho relativo (e não pelo importmap), para o app do celular, que não aceita script
 * embutido (CSP), servir este arquivo em /visual/ com o Three.js ao lado.
 */
import * as THREE from "./vendor/three/build/three.module.min.js";

const PIXEL = () => Math.min(devicePixelRatio || 1, 2);
export const COR_DOURADA = "#f5c04a"; // o branco vira dourado quando uma tarefa é cumprida (estado vitoria)
const aproximar = (atual, alvo, velocidade, dt) => atual + (alvo - atual) * Math.min(1, dt * velocidade);

// A cor anda até o dourado em ~0,5 s na vitória e volta ao branco devagar (~2 s).
function andarCor(atual, base, dourada, vitoria, dt) {
  atual.lerp(vitoria ? dourada : base, Math.min(1, dt * (vitoria ? 4 : 1.2)));
}

// Ponto redondo e suave, somado ao fundo (onde há muitos pontos juntos, fica mais claro).
const FRAGMENTO_PONTO = `
uniform vec3 uCor;
varying float vAlfa;
void main() {
  float d = length(gl_PointCoord - 0.5);
  if (d > 0.5) discard;
  gl_FragColor = vec4(uCor, vAlfa * smoothstep(0.5, 0.12, d));
}`;

/* ------------------------------------------------------------------ a esfera */

// Ondas que deformam a esfera: direção (kx, ky, kz), velocidade e fase. A mesma conta roda no
// JavaScript (nós e linhas da rede) e no shader (a poeira), para as duas camadas andarem juntas.
const ONDAS = [
  [1.7, 0.9, 1.3, 0.9, 0.0],
  [-1.1, 2.3, 0.7, -1.3, 1.0],
  [2.9, -1.6, 2.2, 2.1, 2.0],
  [-4.3, 3.1, -3.4, -3.1, 3.0],
];
const _n = (v) => v.toFixed(3);
const GLSL_DEFORMAR = `
uniform vec4 uPesos;
uniform float uEscala;
uniform float uFase;
float deformar(vec3 d) {
  return ${ONDAS.map(([x, y, z, v, f], i) =>
    `uPesos.${"xyzw"[i]} * sin(dot(d, vec3(${_n(x)}, ${_n(y)}, ${_n(z)})) * uEscala + ${_n(v)} * uFase + ${_n(f)})`).join("\n       + ")};
}`;

function deformar(x, y, z, pesos, escala, fase) {
  let soma = 0;
  for (let i = 0; i < ONDAS.length; i++) {
    const [kx, ky, kz, v, f] = ONDAS[i];
    soma += pesos[i] * Math.sin((x * kx + y * ky + z * kz) * escala + v * fase + f);
  }
  return soma;
}

// Como a esfera fica em cada estado; a voz soma por cima (em qualquer estado).
export const ESTADOS_ESFERA = {
  repouso: { pesos: [0.035, 0.02, 0.006, 0], giro: 0.07, raio: 1.0, linhas: 1.0, brilho: 1.0, ritmo: 0.6 },
  ouvindo: { pesos: [0.016, 0.012, 0.01, 0.004], giro: 0.035, raio: 0.93, linhas: 1.35, brilho: 1.1, ritmo: 0.9 },
  pensando: { pesos: [0.05, 0.045, 0.03, 0.012], giro: 0.55, raio: 0.97, linhas: 1.5, brilho: 1.15, ritmo: 2.4 },
  falando: { pesos: [0.03, 0.02, 0.01, 0.003], giro: 0.12, raio: 1.0, linhas: 1.2, brilho: 1.1, ritmo: 1.0 },
  vitoria: { pesos: [0.06, 0.03, 0.02, 0.01], giro: 0.3, raio: 1.12, linhas: 1.8, brilho: 1.5, ritmo: 1.6 },
};

export const AJUSTES_ESFERA = {
  poeira: 7000, nos: 380, agrupamento: 0.35, distancia: 0.28, alfaLinhas: 0.5, tamanhoPoeira: 1.5,
  tamanhoNos: 2.6, brilho: 1.0, reacao: 1.0, escalaOndas: 1.0, giro: 1.0, espessura: 0.06,
};
const RECONSTROEM_A_ESFERA = ["poeira", "nos", "agrupamento", "distancia"];

function direcaoAleatoria() {
  const u = Math.random() * 2 - 1, t = Math.random() * Math.PI * 2, s = Math.sqrt(1 - u * u);
  return [s * Math.cos(t), u, s * Math.sin(t)];
}

export class EsferaEmRede {
  constructor(recipiente, ajustes = {}) {
    this.recipiente = recipiente;
    this.ajustes = { ...AJUSTES_ESFERA, ...ajustes };
    this.corBase = new THREE.Color("#ffffff");
    this.corDourada = new THREE.Color(COR_DOURADA);
    this.cor = this.corBase.clone();
    this.estado = "repouso";
    this.nivel = 0;
    this.bandas = [0, 0, 0];
    this.nivelSuave = 0;
    this.bandasSuaves = [0, 0, 0];
    const inicial = ESTADOS_ESFERA.repouso;
    this.atual = { ...inicial, pesos: [...inicial.pesos] };
    this.fase = 0;
    this.tempo = 0;

    this.renderizador = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderizador.setClearColor(0x000000, 0);
    this.renderizador.domElement.style.cssText = "width:100%;height:100%;display:block";
    recipiente.appendChild(this.renderizador.domElement);
    this.cena = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(32, 1, 0.1, 50);
    this.inclinacao = new THREE.Group();
    this.inclinacao.rotation.x = 0.3;
    this.inclinacao.rotation.z = -0.12;
    this.giro = new THREE.Group();
    this.inclinacao.add(this.giro);
    this.cena.add(this.inclinacao);

    this.montar();
    this.observador = new ResizeObserver(() => this.ajustarTamanho());
    this.observador.observe(recipiente);
    this.ajustarTamanho();
    this.relogio = performance.now();
    this.animar = this.animar.bind(this);
    requestAnimationFrame(this.animar);
  }

  definirEstado(estado) { if (ESTADOS_ESFERA[estado]) this.estado = estado; }

  // Passa a desenhar em outro recipiente (o app usa uma esfera só, que vai do início para a conversa).
  mover(recipiente) {
    if (!recipiente || recipiente === this.recipiente) return;
    this.observador.unobserve(this.recipiente);
    this.recipiente = recipiente;
    recipiente.appendChild(this.renderizador.domElement);
    this.observador.observe(recipiente);
    this.ajustarTamanho();
  }

  // Missão cumprida: fica dourada em qualquer estado.
  definirDourado(dourado) { this.dourado = dourado; }

  // nivel de 0 a 1; bandas = [graves, médios, agudos], de 0 a 1.
  voz(nivel, bandas) {
    this.nivel = nivel;
    this.bandas = bandas || [0, 0, 0];
  }

  definirCor(cor) {
    this.corBase.set(cor);
    if (!this.dourado && this.estado !== "vitoria") this.cor.copy(this.corBase);
  }

  aplicarAjustes(parciais) {
    const reconstruir = Object.keys(parciais).some((chave) => RECONSTROEM_A_ESFERA.includes(chave) && parciais[chave] !== this.ajustes[chave]);
    Object.assign(this.ajustes, parciais);
    if (reconstruir) this.montar();
  }

  montar() {
    for (const objeto of [this.poeira, this.nosPontos, this.linhas]) {
      if (!objeto) continue;
      this.giro.remove(objeto);
      objeto.geometry.dispose();
      objeto.material.dispose();
    }
    this.montarPoeira();
    this.montarRede();
  }

  montarPoeira() {
    const total = Math.round(this.ajustes.poeira);
    const posicoes = new Float32Array(total * 3), aleatorios = new Float32Array(total);
    for (let i = 0; i < total; i++) {
      let d;
      if (Math.random() < 0.72) d = direcaoAleatoria();
      else { // por latitude e longitude: junta nos polos, como no vídeo de referência
        const lat = (Math.random() - 0.5) * Math.PI, lon = Math.random() * Math.PI * 2;
        d = [Math.cos(lat) * Math.cos(lon), Math.sin(lat), Math.cos(lat) * Math.sin(lon)];
      }
      posicoes.set(d, i * 3);
      aleatorios[i] = Math.random();
    }
    const geometria = new THREE.BufferGeometry();
    geometria.setAttribute("position", new THREE.BufferAttribute(posicoes, 3));
    geometria.setAttribute("aAleatorio", new THREE.BufferAttribute(aleatorios, 1));
    const material = new THREE.ShaderMaterial({
      uniforms: {
        uPesos: { value: new THREE.Vector4() }, uEscala: { value: 1 }, uFase: { value: 0 }, uTempo: { value: 0 },
        uRaio: { value: 1 }, uTamanho: { value: 1.5 }, uBrilho: { value: 1 }, uPixel: { value: PIXEL() },
        uEspessura: { value: 0.06 }, uTremor: { value: 0 }, uCor: { value: this.cor.clone() },
      },
      vertexShader: `
        ${GLSL_DEFORMAR}
        uniform float uTempo, uRaio, uTamanho, uBrilho, uPixel, uEspessura, uTremor;
        attribute float aAleatorio;
        varying float vAlfa;
        void main() {
          vec3 d = normalize(position);
          float tremor = uTremor * sin(uTempo * 31.0 + aAleatorio * 97.0);
          float r = uRaio * (1.0 + deformar(d) + tremor) * (1.0 + (aAleatorio - 0.5) * uEspessura);
          vec4 mv = modelViewMatrix * vec4(d * r, 1.0);
          gl_Position = projectionMatrix * mv;
          gl_PointSize = uTamanho * uPixel * (0.55 + aAleatorio * 0.9);
          float frente = smoothstep(-0.7, 0.9, normalize(normalMatrix * d).z);
          vAlfa = uBrilho * (0.22 + 0.7 * frente) * (0.4 + aAleatorio * 0.6);
        }`,
      fragmentShader: FRAGMENTO_PONTO,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    this.poeira = new THREE.Points(geometria, material);
    this.giro.add(this.poeira);
  }

  montarRede() {
    const total = Math.round(this.ajustes.nos);
    // Parte dos nós se junta em volta de alguns centros: dá os "novelos" e os vazios da rede.
    const centros = Array.from({ length: 7 }, direcaoAleatoria);
    this.direcoes = new Float32Array(total * 3);
    this.raios = new Float32Array(total);
    for (let i = 0; i < total; i++) {
      let d = direcaoAleatoria();
      if (Math.random() < this.ajustes.agrupamento) {
        const c = centros[i % centros.length], espalhar = 0.35 + Math.random() * 0.25;
        d = d.map((v, k) => c[k] + v * espalhar * Math.random());
        const tamanho = Math.hypot(...d);
        d = d.map((v) => v / tamanho);
      }
      this.direcoes.set(d, i * 3);
      this.raios[i] = 0.93 + Math.random() * 0.12;
    }
    // Os pares possíveis saem da posição de descanso (com folga, para linhas novas surgirem quando a
    // esfera se deforma); a cada quadro só aparecem os que estão perto o bastante.
    const limite = this.ajustes.distancia * 1.35, pares = [];
    for (let a = 0; a < total; a++) {
      for (let b = a + 1; b < total; b++) {
        const dx = this.direcoes[a * 3] * this.raios[a] - this.direcoes[b * 3] * this.raios[b];
        const dy = this.direcoes[a * 3 + 1] * this.raios[a] - this.direcoes[b * 3 + 1] * this.raios[b];
        const dz = this.direcoes[a * 3 + 2] * this.raios[a] - this.direcoes[b * 3 + 2] * this.raios[b];
        if (dx * dx + dy * dy + dz * dz < limite * limite) pares.push(a, b);
      }
    }
    this.pares = new Uint16Array(pares);
    this.posNos = new Float32Array(total * 3);
    this.frente = new Float32Array(total);

    const geoNos = new THREE.BufferGeometry();
    geoNos.setAttribute("position", new THREE.BufferAttribute(this.posNos, 3));
    this.brilhoNos = new Float32Array(total);
    geoNos.setAttribute("aBrilho", new THREE.BufferAttribute(this.brilhoNos, 1));
    this.nosPontos = new THREE.Points(geoNos, new THREE.ShaderMaterial({
      uniforms: { uTamanho: { value: 2.6 }, uPixel: { value: PIXEL() }, uCor: { value: this.cor.clone() } },
      vertexShader: `
        uniform float uTamanho, uPixel;
        attribute float aBrilho;
        varying float vAlfa;
        void main() {
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          gl_PointSize = uTamanho * uPixel;
          vAlfa = aBrilho;
        }`,
      fragmentShader: FRAGMENTO_PONTO,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    }));

    const quantosPares = this.pares.length / 2;
    this.posLinhas = new Float32Array(quantosPares * 6);
    this.corLinhas = new Float32Array(quantosPares * 6);
    const geoLinhas = new THREE.BufferGeometry();
    geoLinhas.setAttribute("position", new THREE.BufferAttribute(this.posLinhas, 3));
    geoLinhas.setAttribute("color", new THREE.BufferAttribute(this.corLinhas, 3));
    this.linhas = new THREE.LineSegments(geoLinhas, new THREE.LineBasicMaterial({
      vertexColors: true, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    }));
    this.giro.add(this.linhas, this.nosPontos);
  }

  ajustarTamanho() {
    const largura = this.recipiente.clientWidth || 1, altura = this.recipiente.clientHeight || 1;
    this.renderizador.setPixelRatio(PIXEL());
    this.renderizador.setSize(largura, altura, false);
    this.camera.aspect = largura / altura;
    // Cabe a esfera (com a deformação) no lado menor do recipiente.
    this.camera.position.set(0, 0, Math.max(4.6, 4.6 / this.camera.aspect));
    this.camera.updateProjectionMatrix();
  }

  animar(agora) {
    requestAnimationFrame(this.animar);
    const dt = Math.min(0.05, (agora - this.relogio) / 1000);
    this.relogio = agora;
    if (document.hidden || this.recipiente.clientWidth === 0) return; // aba escondida ou tela fechada: não desenha
    this.atualizar(dt);
    this.renderizador.render(this.cena, this.camera);
  }

  atualizar(dt) {
    const alvo = ESTADOS_ESFERA[this.estado], atual = this.atual, ajustes = this.ajustes;
    // A voz sobe rápido e desce devagar, como o ponteiro de um medidor.
    this.nivelSuave = aproximar(this.nivelSuave, this.nivel, this.nivel > this.nivelSuave ? 22 : 7, dt);
    this.bandasSuaves = this.bandasSuaves.map((b, i) => aproximar(b, this.bandas[i] || 0, (this.bandas[i] || 0) > b ? 22 : 7, dt));
    const nivel = this.nivelSuave, [g, m, a] = this.bandasSuaves, r = ajustes.reacao;
    const pesosAlvo = [alvo.pesos[0] + g * 0.2 * r, alvo.pesos[1] + m * 0.14 * r,
                       alvo.pesos[2] + (m * 0.06 + a * 0.05) * r, alvo.pesos[3] + a * 0.07 * r];
    for (let i = 0; i < 4; i++) atual.pesos[i] = aproximar(atual.pesos[i], pesosAlvo[i], 12, dt);
    atual.raio = aproximar(atual.raio, alvo.raio + nivel * 0.05 * r, 6, dt);
    for (const chave of ["giro", "linhas", "brilho"]) atual[chave] = aproximar(atual[chave], alvo[chave], 2.5, dt);
    this.fase += dt * (alvo.ritmo + nivel * 2.5 * r);
    this.tempo += dt;
    this.giro.rotation.y += dt * atual.giro * ajustes.giro;
    andarCor(this.cor, this.corBase, this.corDourada, this.dourado || this.estado === "vitoria", dt);

    const u = this.poeira.material.uniforms;
    u.uCor.value.copy(this.cor);
    this.nosPontos.material.uniforms.uCor.value.copy(this.cor);
    u.uPesos.value.set(...atual.pesos);
    u.uEscala.value = ajustes.escalaOndas;
    u.uFase.value = this.fase;
    u.uTempo.value = this.tempo;
    u.uRaio.value = atual.raio;
    u.uTamanho.value = ajustes.tamanhoPoeira;
    u.uBrilho.value = ajustes.brilho * atual.brilho;
    u.uEspessura.value = ajustes.espessura;
    u.uTremor.value = nivel * 0.012 * r;
    u.uPixel.value = PIXEL();
    this.nosPontos.material.uniforms.uTamanho.value = ajustes.tamanhoNos;
    this.nosPontos.material.uniforms.uPixel.value = PIXEL();
    this.atualizarRede();
  }

  atualizarRede() {
    const { direcoes, raios, posNos, frente, brilhoNos, pares, posLinhas, corLinhas } = this;
    const atual = this.atual, ajustes = this.ajustes;
    this.cena.updateMatrixWorld();
    const e = this.giro.matrixWorld.elements; // só rotação: a 3ª linha dá a profundidade
    const total = raios.length;
    for (let i = 0; i < total; i++) {
      const x = direcoes[i * 3], y = direcoes[i * 3 + 1], z = direcoes[i * 3 + 2];
      const raio = atual.raio * raios[i] * (1 + deformar(x, y, z, atual.pesos, ajustes.escalaOndas, this.fase));
      posNos[i * 3] = x * raio; posNos[i * 3 + 1] = y * raio; posNos[i * 3 + 2] = z * raio;
      const profundidade = e[2] * x + e[6] * y + e[10] * z; // -1 atrás, 1 na frente
      frente[i] = 0.18 + 0.82 * Math.max(0, Math.min(1, (profundidade + 0.7) / 1.6));
      brilhoNos[i] = Math.min(1, frente[i] * ajustes.brilho * atual.brilho);
    }
    const distancia = ajustes.distancia * atual.raio, alfa = ajustes.alfaLinhas * atual.linhas;
    for (let k = 0, quantos = pares.length / 2; k < quantos; k++) {
      const a = pares[k * 2] * 3, b = pares[k * 2 + 1] * 3, o = k * 6;
      const dx = posNos[a] - posNos[b], dy = posNos[a + 1] - posNos[b + 1], dz = posNos[a + 2] - posNos[b + 2];
      const perto = 1 - Math.sqrt(dx * dx + dy * dy + dz * dz) / distancia;
      posLinhas[o] = posNos[a]; posLinhas[o + 1] = posNos[a + 1]; posLinhas[o + 2] = posNos[a + 2];
      posLinhas[o + 3] = posNos[b]; posLinhas[o + 4] = posNos[b + 1]; posLinhas[o + 5] = posNos[b + 2];
      const forca = perto > 0 ? alfa * Math.pow(perto, 1.3) : 0;
      const ca = forca * frente[a / 3], cb = forca * frente[b / 3];
      corLinhas[o] = ca * this.cor.r; corLinhas[o + 1] = ca * this.cor.g; corLinhas[o + 2] = ca * this.cor.b;
      corLinhas[o + 3] = cb * this.cor.r; corLinhas[o + 4] = cb * this.cor.g; corLinhas[o + 5] = cb * this.cor.b;
    }
    this.nosPontos.geometry.attributes.position.needsUpdate = true;
    this.nosPontos.geometry.attributes.aBrilho.needsUpdate = true;
    this.linhas.geometry.attributes.position.needsUpdate = true;
    this.linhas.geometry.attributes.color.needsUpdate = true;
  }
}

/* ------------------------------------------------------------------ a montanha */

// Ruído de valor em 2D e cristas (o ruído dobrado em |…|): dão as arestas da montanha.
function _hash(i, j) { const s = Math.sin(i * 127.1 + j * 311.7) * 43758.5453; return s - Math.floor(s); }
function _ruido(x, y) {
  const i = Math.floor(x), j = Math.floor(y), fx = x - i, fy = y - j;
  const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
  const a = _hash(i, j), b = _hash(i + 1, j), c = _hash(i, j + 1), d = _hash(i + 1, j + 1);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}
function _cristas(x, y) {
  let soma = 0, amplitude = 0.5, frequencia = 1;
  for (let oitava = 0; oitava < 4; oitava++) {
    const n = 1 - Math.abs(_ruido(x * frequencia, y * frequencia) * 2 - 1);
    soma += n * n * amplitude;
    amplitude *= 0.5; frequencia *= 2.1;
  }
  return soma / 0.9375;
}

// Picos: x, z, altura, largura em x, largura em z. O do meio é o maior (ali ficam os graves).
const PICOS = [[0, -2.2, 1.0, 1.3, 1.1], [-2.7, -3.3, 0.72, 1.1, 0.9], [2.9, -3.1, 0.66, 1.0, 0.9],
               [-5.6, -4.3, 0.52, 1.4, 1.1], [5.9, -4.1, 0.56, 1.3, 1.1], [-8.6, -5.2, 0.4, 1.6, 1.2],
               [8.7, -5.3, 0.38, 1.6, 1.2], [1.3, -1.0, 0.3, 0.7, 0.55], [-1.5, -1.2, 0.26, 0.7, 0.55]];
const LARGURA_MONTANHA = 22, FUNDO_MONTANHA = -9, FRENTE_MONTANHA = 2;
export const BANDAS_MONTANHA = 16;

export const AJUSTES_MONTANHA = {
  pontos: 42000, aspereza: 0.6, altura: 2.3, base: 0.3, ganho: 1.1, tamanho: 1.4, brilho: 1.0, tremor: 1.0,
};
const RECONSTROEM_A_MONTANHA = ["pontos", "aspereza"];

export const ESTADOS_MONTANHA = {
  repouso: { brilho: 0.8, base: 1.0 }, ouvindo: { brilho: 1.15, base: 1.25 }, pensando: { brilho: 0.75, base: 0.9 },
  falando: { brilho: 0.6, base: 0.8 }, vitoria: { brilho: 1.0, base: 1.0 },
};

function formaDaMontanha(x, z, aspereza) {
  let g = 0;
  for (const [px, pz, altura, sx, sz] of PICOS) {
    g = Math.max(g, altura * Math.exp(-0.5 * (((x - px) / sx) ** 2 + ((z - pz) / sz) ** 2)));
  }
  const crista = _cristas(x * 1.1 + 7.3, z * 1.1 - 2.1);
  const pontas = g * g * aspereza * 0.55 * _cristas(x * 2.7 + 1.7, z * 2.7 + 4.2); // picos serrilhados no alto
  return Math.max(0, g * ((1 - aspereza) + aspereza * crista) + pontas + 0.025 * _ruido(x * 2.3, z * 2.3));
}

export class MontanhaDePontos {
  constructor(recipiente, ajustes = {}) {
    this.recipiente = recipiente;
    this.ajustes = { ...AJUSTES_MONTANHA, ...ajustes };
    this.corBase = new THREE.Color("#ffffff");
    this.corDourada = new THREE.Color(COR_DOURADA);
    this.cor = this.corBase.clone();
    this.estado = "repouso";
    this.nivel = 0;
    this.nivelSuave = 0;
    this.bandas = new Float32Array(BANDAS_MONTANHA);
    this.bandasSuaves = new Float32Array(BANDAS_MONTANHA);
    this.brilhoEstado = ESTADOS_MONTANHA.repouso.brilho;
    this.baseEstado = ESTADOS_MONTANHA.repouso.base;
    this.tempo = 0;

    this.renderizador = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderizador.setClearColor(0x000000, 0);
    this.renderizador.domElement.style.cssText = "width:100%;height:100%;display:block";
    recipiente.appendChild(this.renderizador.domElement);
    this.cena = new THREE.Scene();
    // Enquadrada para um quadro largo e baixo (como um gráfico): a base fica perto da borda de baixo.
    this.camera = new THREE.PerspectiveCamera(22, 1, 0.1, 60);
    this.camera.position.set(0, 0.7, 7.2);
    this.camera.lookAt(0, 1.05, -2.6);

    this.montar();
    this.observador = new ResizeObserver(() => this.ajustarTamanho());
    this.observador.observe(recipiente);
    this.ajustarTamanho();
    this.relogio = performance.now();
    this.animar = this.animar.bind(this);
    requestAnimationFrame(this.animar);
  }

  definirEstado(estado) { if (ESTADOS_MONTANHA[estado]) this.estado = estado; }

  definirDourado(dourado) { this.dourado = dourado; }

  // nivel de 0 a 1; bandas: BANDAS_MONTANHA valores de 0 a 1, do grave ao agudo.
  voz(nivel, bandas) {
    this.nivel = nivel;
    for (let i = 0; i < BANDAS_MONTANHA; i++) this.bandas[i] = bandas ? bandas[i] || 0 : 0;
  }

  definirCor(cor) {
    this.corBase.set(cor);
    if (!this.dourado && this.estado !== "vitoria") this.cor.copy(this.corBase);
  }

  aplicarAjustes(parciais) {
    const reconstruir = Object.keys(parciais).some((chave) => RECONSTROEM_A_MONTANHA.includes(chave) && parciais[chave] !== this.ajustes[chave]);
    Object.assign(this.ajustes, parciais);
    if (reconstruir) this.montar();
  }

  montar() {
    if (this.pontos) {
      this.cena.remove(this.pontos);
      this.pontos.geometry.dispose();
      this.pontos.material.dispose();
    }
    const total = Math.round(this.ajustes.pontos), aspereza = this.ajustes.aspereza;
    const posicoes = new Float32Array(total * 3), formas = new Float32Array(total);
    const bandas = new Float32Array(total), fases = new Float32Array(total);
    // Mais pontos onde a montanha é alta (como na referência): sorteia e aceita pela altura.
    for (let i = 0; i < total;) {
      const x = (Math.random() - 0.5) * LARGURA_MONTANHA;
      const z = FUNDO_MONTANHA + Math.random() * (FRENTE_MONTANHA - FUNDO_MONTANHA);
      const forma = formaDaMontanha(x, z, aspereza);
      if (Math.random() > 0.08 + 0.92 * Math.min(1, forma * 1.6)) continue;
      posicoes[i * 3] = x; posicoes[i * 3 + 2] = z;
      formas[i] = forma;
      bandas[i] = Math.min(1, Math.pow(Math.abs(x) / (LARGURA_MONTANHA * 0.3), 0.75)); // graves no meio
      fases[i] = Math.random();
      i++;
    }
    const geometria = new THREE.BufferGeometry();
    geometria.setAttribute("position", new THREE.BufferAttribute(posicoes, 3));
    geometria.setAttribute("aForma", new THREE.BufferAttribute(formas, 1));
    geometria.setAttribute("aBanda", new THREE.BufferAttribute(bandas, 1));
    geometria.setAttribute("aFase", new THREE.BufferAttribute(fases, 1));
    const material = new THREE.ShaderMaterial({
      uniforms: {
        uNivel: { value: 0 }, uTempo: { value: 0 }, uBase: { value: 0.14 }, uGanho: { value: 1 }, uAltura: { value: 1 },
        uTamanho: { value: 1.4 }, uBrilho: { value: 1 }, uPixel: { value: PIXEL() }, uTremor: { value: 1 },
        uBandas: { value: this.bandasSuaves }, uCor: { value: this.cor.clone() },
      },
      vertexShader: `
        uniform float uNivel, uTempo, uBase, uGanho, uAltura, uTamanho, uBrilho, uPixel, uTremor;
        uniform float uBandas[${BANDAS_MONTANHA}];
        attribute float aForma, aBanda, aFase;
        varying float vAlfa;
        float banda(float t) {
          float f = clamp(t, 0.0, 1.0) * ${BANDAS_MONTANHA - 1}.0;
          int i = int(floor(f));
          return mix(uBandas[i], uBandas[min(i + 1, ${BANDAS_MONTANHA - 1})], fract(f));
        }
        void main() {
          float sobe = uBase + uGanho * (uNivel * 0.7 + banda(aBanda) * 0.55);
          float treme = uTremor * uNivel * 0.07 * sin(uTempo * 24.0 + aFase * 6.2831);
          float h = aForma * (sobe + treme);
          vec4 mv = modelViewMatrix * vec4(position.x, h * uAltura, position.z, 1.0);
          gl_Position = projectionMatrix * mv;
          gl_PointSize = uTamanho * uPixel * clamp(7.0 / -mv.z, 0.55, 1.5);
          float alto = smoothstep(0.0, 0.85, h);
          float perto = smoothstep(17.0, 5.0, -mv.z);
          vAlfa = uBrilho * (0.24 + 0.76 * alto) * (0.35 + 0.65 * perto);
        }`,
      fragmentShader: FRAGMENTO_PONTO,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    this.pontos = new THREE.Points(geometria, material);
    this.cena.add(this.pontos);
  }

  ajustarTamanho() {
    const largura = this.recipiente.clientWidth || 1, altura = this.recipiente.clientHeight || 1;
    this.renderizador.setPixelRatio(PIXEL());
    this.renderizador.setSize(largura, altura, false);
    this.camera.aspect = largura / altura;
    this.camera.updateProjectionMatrix();
  }

  animar(agora) {
    requestAnimationFrame(this.animar);
    const dt = Math.min(0.05, (agora - this.relogio) / 1000);
    this.relogio = agora;
    if (document.hidden || this.recipiente.clientWidth === 0) return; // aba escondida ou tela fechada: não desenha
    this.atualizar(dt);
    this.renderizador.render(this.cena, this.camera);
  }

  atualizar(dt) {
    const ajustes = this.ajustes, estado = ESTADOS_MONTANHA[this.estado];
    this.nivelSuave = aproximar(this.nivelSuave, this.nivel, this.nivel > this.nivelSuave ? 20 : 4, dt);
    for (let i = 0; i < BANDAS_MONTANHA; i++) {
      this.bandasSuaves[i] = aproximar(this.bandasSuaves[i], this.bandas[i], this.bandas[i] > this.bandasSuaves[i] ? 20 : 5, dt);
    }
    this.brilhoEstado = aproximar(this.brilhoEstado, estado.brilho, 3, dt);
    this.baseEstado = aproximar(this.baseEstado, estado.base, 3, dt);
    this.tempo += dt;
    andarCor(this.cor, this.corBase, this.corDourada, this.dourado || this.estado === "vitoria", dt);
    const u = this.pontos.material.uniforms;
    u.uCor.value.copy(this.cor);
    u.uNivel.value = this.nivelSuave;
    u.uTempo.value = this.tempo;
    u.uBase.value = ajustes.base * this.baseEstado;
    u.uGanho.value = ajustes.ganho;
    u.uAltura.value = ajustes.altura;
    u.uTamanho.value = ajustes.tamanho;
    u.uBrilho.value = ajustes.brilho * this.brilhoEstado;
    u.uTremor.value = ajustes.tremor;
    u.uPixel.value = PIXEL();
  }
}

/* ------------------------------------------------------------------ som do navegador */

// Nível (0 a 1, de -52 a -12 dB, como o niveis_voz.py) e BANDAS_MONTANHA faixas de 90 Hz a 5 kHz de uma fonte do
// Web Audio (o microfone, um <audio>). O analisador vai a um ganho zero no destino: o Safari só processa o que chega lá.
export function medidorDeAudio(contexto, fonte) {
  const analisador = contexto.createAnalyser();
  analisador.fftSize = 2048;
  analisador.smoothingTimeConstant = 0.55;
  analisador.minDecibels = -90;
  analisador.maxDecibels = -25;
  fonte.connect(analisador);
  const mudo = contexto.createGain();
  mudo.gain.value = 0;
  analisador.connect(mudo).connect(contexto.destination);
  const tempo = new Float32Array(analisador.fftSize), frequencias = new Uint8Array(analisador.frequencyBinCount);
  const hzPorFaixa = contexto.sampleRate / analisador.fftSize;
  const limites = Array.from({ length: BANDAS_MONTANHA + 1 },
    (_, i) => Math.round((90 * Math.pow(5000 / 90, i / BANDAS_MONTANHA)) / hzPorFaixa));
  return {
    analisador,
    medir() {
      analisador.getFloatTimeDomainData(tempo);
      let soma = 0;
      for (const v of tempo) soma += v * v;
      const db = 20 * Math.log10(Math.sqrt(soma / tempo.length) + 1e-9);
      analisador.getByteFrequencyData(frequencias);
      const bandas = [];
      for (let i = 0; i < BANDAS_MONTANHA; i++) {
        let total = 0, quantas = 0;
        for (let k = limites[i]; k <= Math.max(limites[i], limites[i + 1] - 1); k++) { total += frequencias[k]; quantas++; }
        bandas.push(Math.pow(total / quantas / 255, 1.4));
      }
      return { nivel: Math.max(0, Math.min(1, (db + 52) / 40)), db, bandas, tres: tresBandas(bandas) };
    },
    desligar() { try { fonte.disconnect(analisador); analisador.disconnect(); mudo.disconnect(); } catch (erro) { /* já solto */ } },
  };
}

// O nível de um áudio já decodificado (AudioBuffer) a cada `passo` segundos, para acompanhar a fala enquanto ela toca
// sem passar o som pelo Web Audio (no iPhone, um contexto suspenso deixaria a fala muda).
export function envelopeDoAudio(buffer, passo = 1 / 30) {
  const dados = buffer.getChannelData(0), tamanho = Math.max(32, Math.round(buffer.sampleRate * passo)), niveis = [];
  for (let inicio = 0; inicio < dados.length; inicio += tamanho) {
    let soma = 0;
    const fim = Math.min(dados.length, inicio + tamanho);
    for (let i = inicio; i < fim; i++) soma += dados[i] * dados[i];
    const db = 20 * Math.log10(Math.sqrt(soma / (fim - inicio)) + 1e-9);
    niveis.push(Math.max(0, Math.min(1, (db + 52) / 40)));
  }
  return { passo, niveis };
}

/* ------------------------------------------------------------------ som de mentira */

export function tresBandas(bandas) {
  const media = (de, ate) => bandas.slice(de, ate).reduce((soma, v) => soma + v, 0) / (ate - de);
  return [media(0, 5), media(5, 11), media(11, BANDAS_MONTANHA)];
}

// Fala simulada (laboratório, modo demonstração e enquanto o Python não manda os níveis de verdade):
// sílabas de 0,12 a 0,25 s em palavras de 1 a 4 sílabas, com pausas; o espectro tem o tom da voz
// (banda tomDaVoz) e dois formantes que mudam a cada sílaba. medir() devolve null quando acaba.
export function falaSimulada(duracao, tomDaVoz) {
  const silabas = [];
  let t = 0.15;
  while (t < duracao) {
    const quantas = 1 + Math.floor(Math.random() * 4);
    for (let i = 0; i < quantas && t < duracao; i++) {
      const d = 0.12 + Math.random() * 0.13;
      silabas.push({ inicio: t, fim: t + d, forca: 0.45 + Math.random() * 0.55, abertura: Math.random() });
      t += d;
    }
    t += Math.random() < 0.15 ? 0.35 + Math.random() * 0.3 : 0.05 + Math.random() * 0.15;
  }
  const comeco = performance.now() / 1000, fim = t;
  return {
    medir() {
      const agora = performance.now() / 1000 - comeco;
      if (agora > fim + 0.25) return null;
      const silaba = silabas.find((s) => agora >= s.inicio && agora < s.fim);
      let nivel = 0, abertura = 0, consoante = 0;
      if (silaba) {
        const p = (agora - silaba.inicio) / (silaba.fim - silaba.inicio);
        nivel = silaba.forca * Math.pow(Math.sin(Math.PI * p), 0.6);
        abertura = silaba.abertura;
        consoante = p < 0.22 ? 1 : 0;
      }
      const bandas = Array.from({ length: BANDAS_MONTANHA }, (_, i) => {
        const forma = Math.exp(-((i - tomDaVoz) ** 2) / 2) + 0.7 * Math.exp(-((i - 5 - abertura * 2) ** 2) / 3)
                    + 0.45 * Math.exp(-((i - 9 - abertura * 2) ** 2) / 4) + consoante * 0.6 * (i / BANDAS_MONTANHA);
        return Math.min(1, nivel * forma * (0.8 + Math.random() * 0.4));
      });
      return { nivel, db: nivel > 0 ? -52 + nivel * 40 : -Infinity, bandas, tres: tresBandas(bandas) };
    },
  };
}
