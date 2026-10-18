/* Laboratório da mascote 3D: a cena (luz de estúdio, sombra, brilho), os controles de aparência,
 * corpo, chamas e pose, as versões salvas e a prévia no tamanho real que ela terá na tela.
 * Também abre um modelo .glb (feito a partir da imagem de referência) no lugar do modelo de código.
 * Os ajustes ficam no localStorage; `window.mascoteLab` dá acesso a eles pelo console. */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import { Mascote3D, AJUSTES_PADRAO, PRESETS, MODOS, ARTICULACOES, vestirModelo } from "./mascote3d.js";

const CHAVE_AJUSTES = "beatriz.mascote3d.ajustes";
const CHAVE_VERSOES = "beatriz.mascote3d.versoes";
const CHAVE_PREFERENCIAS = "beatriz.mascote3d.preferencias";

function ler(chave, padrao) {
  try {
    const valor = localStorage.getItem(chave);
    return valor ? JSON.parse(valor) : padrao;
  } catch {
    return padrao;
  }
}
function gravar(chave, valor) {
  try {
    localStorage.setItem(chave, JSON.stringify(valor));
    return true;
  } catch {
    return false;
  }
}

let ajustes = { ...AJUSTES_PADRAO, ...ler(CHAVE_AJUSTES, {}) };
const preferencias = { aba: "aparencia", modo: "parado", camera: "tres-quartos", fundo: "estudio", mini: true, ...ler(CHAVE_PREFERENCIAS, {}) };

// ------------------------------------------------------------------ cena
const palco = document.getElementById("palco");
const tela = document.getElementById("tela");
const renderer = new THREE.WebGLRenderer({ canvas: tela, antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.NeutralToneMapping;
renderer.toneMappingExposure = 1.05;

const cena = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(32, 1, 0.05, 60);
const orbita = new OrbitControls(camera, tela);
orbita.enableDamping = true;
orbita.minDistance = 1.0;
orbita.maxDistance = 10;
orbita.maxPolarAngle = Math.PI * 0.53;
orbita.autoRotateSpeed = 1.2;

cena.add(new THREE.HemisphereLight(0xcfd0e0, 0x2a2630, 1.0));
const luzPrincipal = new THREE.DirectionalLight(0xfff4ea, 2.4);
luzPrincipal.position.set(3, 5, 4);
luzPrincipal.castShadow = true;
luzPrincipal.shadow.mapSize.set(2048, 2048);
Object.assign(luzPrincipal.shadow.camera, { left: -2.6, right: 2.6, top: 2.6, bottom: -2.6, near: 0.5, far: 14 });
luzPrincipal.shadow.bias = -0.0004;
luzPrincipal.shadow.normalBias = 0.02;
const luzContorno = new THREE.DirectionalLight(0xb090ff, 1.8);
luzContorno.position.set(-3, 3, -4);
const luzPreenchimento = new THREE.DirectionalLight(0x9fb7ff, 0.6);
luzPreenchimento.position.set(-4, 1, 3);
cena.add(luzPrincipal, luzContorno, luzPreenchimento);

const chao = new THREE.Mesh(new THREE.CircleGeometry(9, 64), new THREE.ShadowMaterial({ opacity: 0.32 }));
chao.rotation.x = -Math.PI / 2;
chao.receiveShadow = true;
cena.add(chao);

function texturaFundo(desenhar, largura = 32, altura = 512) {
  const c = document.createElement("canvas");
  c.width = largura;
  c.height = altura;
  desenhar(c.getContext("2d"), largura, altura);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
const gradiente = (cores) => (g, l, a) => {
  const grad = g.createLinearGradient(0, 0, 0, a);
  cores.forEach(([pos, cor]) => grad.addColorStop(pos, cor));
  g.fillStyle = grad;
  g.fillRect(0, 0, l, a);
};
function desenharAreaDeTrabalho(g, l, a, barra) {
  const grad = g.createLinearGradient(0, 0, l, a);
  grad.addColorStop(0, "#1d3d7a");
  grad.addColorStop(0.55, "#3c3a8f");
  grad.addColorStop(1, "#6a3f9a");
  g.fillStyle = grad;
  g.fillRect(0, 0, l, a);
  if (!barra) return;
  g.fillStyle = "#1f1e26";
  g.fillRect(0, a - barra, l, barra);
  const icone = barra * 0.55;
  for (let i = 0; i < 6; i++) {
    g.fillStyle = ["#4f8cff", "#f2b732", "#e5e5e5", "#3ccf8e", "#a45cff", "#ff6b6b"][i];
    const x = l / 2 - 3 * icone * 1.5 + i * icone * 1.5;
    g.beginPath();
    g.roundRect(x, a - barra + (barra - icone) / 2, icone, icone, icone * 0.22);
    g.fill();
  }
}
const FUNDOS = {
  estudio: texturaFundo(gradiente([[0, "#5c5b64"], [0.55, "#8e8d96"], [0.75, "#a7a6ae"], [1, "#9b9aa2"]])),
  escuro: texturaFundo(gradiente([[0, "#1a1228"], [1, "#07050c"]])),
  area: texturaFundo((g, l, a) => desenharAreaDeTrabalho(g, l, a, 0), 512, 512),
};
// A miniatura imita a borda de baixo da tela: papel de parede e barra de tarefas (48 px).
const fundoMini = texturaFundo((g, l, a) => desenharAreaDeTrabalho(g, l, a, 48 * 2), 600, 400);

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(cena, camera));
const brilho = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.55, 0.55, 0.85);
composer.addPass(brilho);
composer.addPass(new OutputPass());

// Miniatura: outra tela, câmera de lado, sem sombra, no tamanho que a mascote terá (~110 px de altura).
const telaMini = document.getElementById("tela-mini");
const rendererMini = new THREE.WebGLRenderer({ canvas: telaMini, antialias: true });
rendererMini.setPixelRatio(Math.min(devicePixelRatio, 2));
rendererMini.setSize(300, 200, false);
rendererMini.toneMapping = THREE.NeutralToneMapping;
rendererMini.toneMappingExposure = 1.05;
const cameraMini = new THREE.PerspectiveCamera(20, 300 / 200, 0.5, 40);
cameraMini.position.set(9, 0.83, -0.22);
cameraMini.lookAt(0, 0.83, -0.22);
const composerMini = new EffectComposer(rendererMini);
composerMini.addPass(new RenderPass(cena, cameraMini));
composerMini.addPass(new UnrealBloomPass(new THREE.Vector2(300, 200), 0.5, 0.4, 0.85));
composerMini.addPass(new OutputPass());

// ------------------------------------------------------------------ modelo de arquivo (.glb)
// Um modelo feito a partir da imagem (Meshy, Tripo, Blender...). Fica num suporte que o põe de pé no
// chão, centrado e com a mesma altura do modelo de código; o giro corrige para onde ele olha.
const CAMINHO_MODELO = "modelos/mascote.glb";
const ALTURA_MODELO = 1.7;
const carregador = new GLTFLoader();
const draco = new DRACOLoader();
draco.setDecoderPath("https://www.gstatic.com/draco/versioned/decoders/1.5.7/");
carregador.setDRACOLoader(draco);
carregador.setMeshoptDecoder(MeshoptDecoder);
const modelo = { suporte: null, pivo: null, nome: "", triangulos: 0, ossos: [], repouso: new Map(), ajudante: null, mixer: null, clipes: [], acao: null, giro: 0, vestimenta: null };
let usarModelo = true;
const modeloAtivo = () => usarModelo && modelo.suporte !== null;
// Modelo com o esqueleto da mascote (mesmos nomes de ossos): as animações do modelo de código o movem.
const modeloAnimado = () => modeloAtivo() && Boolean(modelo.vestimenta && modelo.vestimenta.animado);

function carregarModelo(fonte, nome) {
  avisar(`Abrindo ${nome}…`);
  const pronto = (gltf) => {
    montarModelo(gltf, nome);
    avisar(`${nome} aberto.`, 3000);
  };
  const falhou = (erro) => {
    console.error(erro);
    avisar(`Não consegui abrir ${nome}: ${erro.message || erro}`, 8000);
  };
  if (typeof fonte === "string") carregador.load(fonte, pronto, undefined, falhou);
  else carregador.parse(fonte, "", pronto, falhou);
}

function tirarModelo() {
  if (!modelo.suporte) return;
  gizmo.detach();
  ossoEscolhido = null;
  if (modelo.mixer) modelo.mixer.stopAllAction();
  cena.remove(modelo.suporte);
  modelo.suporte.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
    for (const m of [].concat(o.material || [])) {
      for (const valor of Object.values(m)) if (valor && valor.isTexture) valor.dispose();
      m.dispose();
    }
  });
  if (modelo.ajudante) {
    cena.remove(modelo.ajudante);
    modelo.ajudante.dispose();
  }
  Object.assign(modelo, { suporte: null, pivo: null, nome: "", triangulos: 0, ossos: [], repouso: new Map(), ajudante: null, mixer: null, clipes: [], acao: null, giro: 0, vestimenta: null });
}

function montarModelo(gltf, nome) {
  tirarModelo();
  const pivo = new THREE.Group();
  const suporte = new THREE.Group();
  pivo.add(gltf.scene);
  suporte.add(pivo);
  let triangulos = 0;
  gltf.scene.traverse((o) => {
    if (o.isMesh) {
      o.castShadow = o.receiveShadow = true;
      o.frustumCulled = false;
      const g = o.geometry;
      // Malha sem normais (ex.: prévia do Meshy) sairia facetada: calcula normais suaves.
      if (!g.attributes.normal) {
        g.computeVertexNormals();
        for (const m of [].concat(o.material)) m.flatShading = false;
      }
      triangulos += (g.index ? g.index.count : g.attributes.position.count) / 3;
    }
    if (o.isBone) {
      modelo.ossos.push(o);
      modelo.repouso.set(o, o.quaternion.clone());
    }
  });
  Object.assign(modelo, { suporte, pivo, nome, triangulos: Math.round(triangulos), clipes: gltf.animations || [] });
  // Modelo pintado: olhos por shader, cauda em chamas e as cores dos ajustes em cada parte.
  modelo.vestimenta = vestirModelo(gltf.scene, mascote);
  modelo.vestimenta.aplicar(ajustes);
  if (modelo.clipes.length) modelo.mixer = new THREE.AnimationMixer(gltf.scene);
  cena.add(suporte);
  if (modelo.ossos.length) {
    modelo.ajudante = new THREE.SkeletonHelper(gltf.scene);
    cena.add(modelo.ajudante);
  }
  usarModelo = true;
  encaixarModelo();
  atualizarEsqueletos();
  if (modelo.clipes.length) tocarAnimacaoDoModelo(0);
  mostrarAba(preferencias.aba === "pose" || preferencias.aba === "modelo" ? preferencias.aba : "modelo");
}

function encaixarModelo() {
  const { suporte, pivo } = modelo;
  pivo.rotation.y = (modelo.giro * Math.PI) / 2;
  suporte.scale.setScalar(1);
  suporte.position.set(0, 0, 0);
  suporte.updateMatrixWorld(true);
  const caixa = new THREE.Box3().setFromObject(suporte, true);
  const tamanho = caixa.getSize(new THREE.Vector3()), centro = caixa.getCenter(new THREE.Vector3());
  const escala = ALTURA_MODELO / Math.max(tamanho.y, 1e-6);
  suporte.scale.setScalar(escala);
  suporte.position.set(-centro.x * escala, -caixa.min.y * escala, -centro.z * escala);
}

function tocarAnimacaoDoModelo(i) {
  if (!modelo.mixer) return;
  gizmo.detach();
  ossoEscolhido = null;
  const nova = modelo.mixer.clipAction(modelo.clipes[i]);
  if (modelo.acao && modelo.acao !== nova) modelo.acao.fadeOut(0.3);
  nova.reset().fadeIn(0.3).play();
  modelo.acao = nova;
}
function pararAnimacaoDoModelo() {
  if (!modelo.mixer) return;
  modelo.mixer.stopAllAction();
  modelo.acao = null;
}

// Arrastar um .glb para qualquer lugar da página abre o modelo.
addEventListener("dragover", (e) => {
  e.preventDefault();
  palco.classList.add("soltando");
});
addEventListener("dragleave", (e) => { if (!e.relatedTarget) palco.classList.remove("soltando"); });
addEventListener("drop", async (e) => {
  e.preventDefault();
  palco.classList.remove("soltando");
  const arquivo = [...e.dataTransfer.files].find((f) => /\.(glb|gltf)$/i.test(f.name));
  if (!arquivo) {
    avisar("Arraste um arquivo .glb (ou .gltf com tudo embutido).", 5000);
    return;
  }
  carregarModelo(await arquivo.arrayBuffer(), arquivo.name);
});

// ------------------------------------------------------------------ mascote
const mascote = new Mascote3D(ajustes);
cena.add(mascote.grupo);
let esqueleto = null;
const verEsqueleto = document.getElementById("ver-esqueleto");
function recriarEsqueleto() {
  if (esqueleto) {
    cena.remove(esqueleto);
    esqueleto.dispose();
  }
  esqueleto = new THREE.SkeletonHelper(mascote.grupo);
  const cores = esqueleto.geometry.getAttribute("color");
  for (let i = 0; i < cores.count; i++) cores.setXYZ(i, 0.82, 0.82, 0.86);
  cores.needsUpdate = true;
  cena.add(esqueleto);
  atualizarEsqueletos();
}
// Mostra o esqueleto (e o modelo) que está em uso: o feito em código ou o do arquivo.
function atualizarEsqueletos() {
  const doArquivo = modeloAtivo();
  mascote.grupo.visible = !doArquivo;
  if (modelo.suporte) modelo.suporte.visible = doArquivo;
  if (esqueleto) esqueleto.visible = !doArquivo && verEsqueleto.checked;
  if (modelo.ajudante) modelo.ajudante.visible = doArquivo && verEsqueleto.checked;
  document.getElementById("modos").hidden = doArquivo && !modeloAnimado();
}

const gizmo = new TransformControls(camera, tela);
gizmo.setMode("rotate");
gizmo.setSpace("local");
gizmo.setSize(0.75);
const ajudanteGizmo = gizmo.getHelper();
cena.add(ajudanteGizmo);
gizmo.addEventListener("dragging-changed", (e) => { orbita.enabled = !e.value; });
gizmo.addEventListener("objectChange", () => atualizarControlesDoOsso());
let ossoEscolhido = null;

mascote.aoReconstruir = () => {
  recriarEsqueleto();
  if (ossoEscolhido && !modeloAtivo()) {
    gizmo.detach();
    ossoEscolhido = null;
    if (preferencias.aba === "pose") mostrarAba("pose");
  }
};
mascote.aoReconstruir();

// Ajustes mexidos na mesma fração de segundo viram uma reconstrução só (no próximo quadro).
let ajustesPendentes = false;
function definir(chave, valor) {
  ajustes[chave] = valor;
  ajustesPendentes = true;
  salvarAjustes();
}
// Guarda só o que difere do padrão, para melhorias no padrão continuarem chegando no resto.
function salvarAjustes() {
  const mudados = Object.fromEntries(Object.entries(ajustes).filter(([chave, valor]) => AJUSTES_PADRAO[chave] !== valor));
  const ok = gravar(CHAVE_AJUSTES, mudados);
  avisar(ok ? "Salvo automaticamente." : "Não deu para salvar neste navegador.");
}
let avisoTemporario = null;
function avisar(texto, ms = 0) {
  const status = document.getElementById("status");
  status.textContent = texto;
  clearTimeout(avisoTemporario);
  if (ms) avisoTemporario = setTimeout(() => { status.textContent = "Salvo automaticamente."; }, ms);
}
function substituirAjustes(novos) {
  ajustes = { ...AJUSTES_PADRAO, ...novos };
  ajustesPendentes = true;
  salvarAjustes();
  mostrarAba(preferencias.aba);
}

// ------------------------------------------------------------------ câmera e modos
const CAMERAS = {
  "tres-quartos": { nome: "3/4", pos: [3.4, 1.7, 3.0], alvo: [0, 0.8, -0.15] },
  lado: { nome: "Lado", pos: [5.4, 1.0, -0.2], alvo: [0, 0.8, -0.2] },
  frente: { nome: "Frente", pos: [0.3, 1.25, 4.6], alvo: [0, 0.9, 0] },
  tras: { nome: "Trás", pos: [-2.2, 1.9, -4.2], alvo: [0, 0.9, -0.3] },
  rosto: { nome: "Rosto", pos: [1.5, 1.55, 2.35], alvo: [0, 1.25, 0.35] },
};
function irParaCamera(id) {
  const c = CAMERAS[id] || CAMERAS["tres-quartos"];
  camera.position.set(...c.pos);
  orbita.target.set(...c.alvo);
  orbita.update();
  preferencias.camera = id;
  gravar(CHAVE_PREFERENCIAS, preferencias);
  marcarAtivo("cameras", id);
}
function marcarAtivo(idGrupo, valor) {
  for (const b of document.getElementById(idGrupo).children) b.classList.toggle("ativo", b.dataset.valor === valor);
}
function chip(texto, valor, aoClicar) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "chip";
  b.textContent = texto;
  b.dataset.valor = valor;
  b.addEventListener("click", aoClicar);
  return b;
}
for (const m of MODOS) document.getElementById("modos").append(chip(m.nome, m.id, () => escolherModo(m.id)));
for (const [id, c] of Object.entries(CAMERAS)) document.getElementById("cameras").append(chip(c.nome, id, () => irParaCamera(id)));

function escolherModo(id) {
  if (id !== "pose") {
    gizmo.detach();
    ossoEscolhido = null;
    preferencias.modo = id;
    gravar(CHAVE_PREFERENCIAS, preferencias);
    if (preferencias.aba === "pose") mostrarAba("pose");
  }
  mascote.definirModo(id);
  marcarAtivo("modos", id);
}

// ------------------------------------------------------------------ barra de baixo
const girar = document.getElementById("girar");
girar.addEventListener("change", () => { orbita.autoRotate = girar.checked; });
verEsqueleto.addEventListener("change", atualizarEsqueletos);
const olharMouse = document.getElementById("olhar-mouse");
const verMini = document.getElementById("ver-mini");
const miniatura = document.getElementById("miniatura");
verMini.checked = preferencias.mini;
miniatura.hidden = !preferencias.mini;
verMini.addEventListener("change", () => {
  preferencias.mini = verMini.checked;
  miniatura.hidden = !verMini.checked;
  gravar(CHAVE_PREFERENCIAS, preferencias);
});
const seletorFundo = document.getElementById("fundo");
seletorFundo.value = preferencias.fundo;
seletorFundo.addEventListener("change", () => {
  preferencias.fundo = seletorFundo.value;
  gravar(CHAVE_PREFERENCIAS, preferencias);
});
document.getElementById("velocidade").addEventListener("input", (e) => { mascote.velocidade = Number(e.target.value); });

// O mouse vira o alvo do olhar: um ponto na frente da câmera, na altura da cabeça.
const raio = new THREE.Raycaster(), ponteiro = new THREE.Vector2(), plano = new THREE.Plane();
let alvoMouse = null;
tela.addEventListener("pointermove", (e) => {
  const caixa = tela.getBoundingClientRect();
  ponteiro.set(((e.clientX - caixa.left) / caixa.width) * 2 - 1, -((e.clientY - caixa.top) / caixa.height) * 2 + 1);
  raio.setFromCamera(ponteiro, camera);
  const cabeca = mascote.ossos.cabeca.getWorldPosition(new THREE.Vector3());
  plano.setFromNormalAndCoplanarPoint(camera.getWorldDirection(new THREE.Vector3()).negate(), cabeca);
  alvoMouse = raio.ray.intersectPlane(plano, new THREE.Vector3());
});
tela.addEventListener("pointerleave", () => { alvoMouse = null; });

// ------------------------------------------------------------------ painel de ajustes
const CONTROLES = {
  aparencia: [
    { titulo: "Cores", itens: [
      ["corPelo", "Pelo", "cor"], ["corBrilho", "Contorno do pelo", "cor"], ["forcaBrilho", "Força do contorno", 0, 2, 0.05],
      ["corOlhos", "Olhos", "cor"], ["brilhoOlhos", "Brilho dos olhos", 0.3, 3, 0.05],
      ["corGema", "Gema e garras", "cor"], ["corChifres", "Chifres", "cor"], ["corOrelhaDentro", "Dentro das orelhas", "cor"],
    ] },
    { titulo: "Rosto", itens: [
      ["cabeca", "Tamanho da cabeça", 0.75, 1.35, 0.01], ["olhos", "Tamanho dos olhos", 0.7, 1.45, 0.01],
      ["pupila", "Pupila (fina ↔ redonda)", 0.1, 1, 0.01], ["palpebra", "Olhar (aberto ↔ semicerrado)", 0, 0.7, 0.01],
      ["orelhas", "Orelhas", 0.6, 1.5, 0.01], ["focinho", "Focinho", 0.6, 1.5, 0.01],
      ["chifres", "Chifres (0 = sem)", 0, 1.8, 0.01], ["gema", "Gema na testa", "bool"],
    ] },
  ],
  corpo: [
    { titulo: "Proporções", itens: [
      ["corpo", "Comprimento do corpo", 0.75, 1.35, 0.01], ["pernas", "Altura das pernas", 0.7, 1.4, 0.01],
      ["cauda", "Comprimento da cauda", 0.5, 1.6, 0.01], ["caudaVolume", "Volume da cauda", 0.6, 1.6, 0.01],
      ["pelagem", "Tufos de pelo (0 = liso)", 0, 1.8, 0.01],
    ] },
  ],
  chamas: [
    { titulo: "Fogo", itens: [
      ["chamas", "Intensidade (0 = apagado)", 0, 2, 0.01], ["alturaChamas", "Altura das labaredas", 0.4, 2, 0.01],
      ["corChamaNucleo", "Cor do centro", "cor"], ["corChamaMeio", "Cor do meio", "cor"], ["corChamaBorda", "Cor das pontas", "cor"],
    ] },
    { titulo: "Onde tem fogo", itens: [
      ["chamasOmbros", "Ombros", "bool"], ["chamasCostas", "Costas", "bool"], ["chamasPeito", "Peito e pescoço", "bool"],
      ["chamasPatas", "Patas", "bool"], ["chamasCauda", "Cauda", "bool"],
    ] },
  ],
};
const ABAS = [["modelo", "Modelo"], ["aparencia", "Visual"], ["corpo", "Corpo"], ["chamas", "Chamas"], ["pose", "Pose"], ["versoes", "Versões"]];

function el(tag, atributos = {}, ...filhos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(atributos)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  e.append(...filhos.filter((f) => f !== null && f !== undefined));
  return e;
}

function formatar(numero) { return Number(numero).toFixed(2).replace(".", ","); }

function linhaControle([chave, rotulo, a, b, passo]) {
  const id = "ctl-" + chave;
  if (a === "cor") {
    return el("div", { class: "controle" }, el("label", { class: "rotulo", for: id }, rotulo),
      el("input", { type: "color", id, value: ajustes[chave], oninput: (e) => definir(chave, e.target.value) }));
  }
  if (a === "bool") {
    const caixa = el("input", { type: "checkbox", id, onchange: (e) => definir(chave, e.target.checked) });
    caixa.checked = !!ajustes[chave];
    return el("div", { class: "controle" }, el("label", { class: "rotulo", for: id }, rotulo), caixa);
  }
  const valor = el("span", { class: "valor" }, formatar(ajustes[chave]));
  return el("div", { class: "controle" }, el("label", { class: "rotulo", for: id }, rotulo), valor,
    el("input", { type: "range", id, min: a, max: b, step: passo, value: ajustes[chave],
      oninput: (e) => { valor.textContent = formatar(e.target.value); definir(chave, Number(e.target.value)); } }));
}

function abaDeControles(id) {
  const partes = [];
  for (const secao of CONTROLES[id]) partes.push(el("h2", {}, secao.titulo), ...secao.itens.map(linhaControle));
  return partes;
}

// ---------------------------------------------------------------- modelo
function abaModelo() {
  const entrada = el("input", { type: "file", accept: ".glb,.gltf", hidden: "", onchange: async (e) => {
    const arquivo = e.target.files[0];
    if (arquivo) carregarModelo(await arquivo.arrayBuffer(), arquivo.name);
  } });
  const partes = [el("h2", {}, "Modelo do arquivo"),
    el("button", { type: "button", class: "soltar", onclick: () => entrada.click() },
      el("strong", {}, "Arraste o arquivo .glb para cá"),
      el("span", {}, "ou clique para escolher. Salvo como interface_web/modelos/mascote.glb, ele abre sozinho."), entrada)];
  if (modelo.suporte) {
    const detalhes = [`${modelo.triangulos.toLocaleString("pt-BR")} triângulos`, `${modelo.ossos.length} ossos`, `${modelo.clipes.length} animações`];
    partes.push(el("div", { class: "cartao" }, el("span", { class: "sem-imagem" }),
      el("div", {}, el("div", { class: "titulo" }, modelo.nome), el("div", { class: "detalhe" }, detalhes.join(" · "))),
      el("div", { class: "acoes" }, el("button", { type: "button", class: "botao discreto perigo", onclick: () => {
        tirarModelo();
        atualizarEsqueletos();
        mostrarAba("modelo");
      } }, "Tirar"))));
    partes.push(el("div", { class: "linha-botoes" },
      el("button", { type: "button", class: "botao", onclick: () => {
        usarModelo = !usarModelo;
        gizmo.detach();
        ossoEscolhido = null;
        atualizarEsqueletos();
        mostrarAba("modelo");
      } }, usarModelo ? "Ver o modelo de código" : "Ver o modelo do arquivo"),
      el("button", { type: "button", class: "botao", onclick: () => {
        modelo.giro = (modelo.giro + 1) % 4;
        encaixarModelo();
      } }, "Girar 90°")));
    if (!modelo.ossos.length) {
      partes.push(el("p", { class: "dica" }, "Este modelo veio sem esqueleto: ele aparece parado. Me avise que eu encaixo os ossos e as animações nele."));
    }
    if (modelo.clipes.length) {
      partes.push(el("h2", {}, "Animações do arquivo"));
      const chips = el("div", { class: "grupo-ossos" });
      modelo.clipes.forEach((clipe, i) => chips.append(chip(clipe.name || `Animação ${i + 1}`, String(i), () => {
        tocarAnimacaoDoModelo(i);
        mostrarAba("modelo");
      })));
      chips.append(chip("Parar", "parar", () => pararAnimacaoDoModelo()));
      for (const b of chips.children) b.classList.toggle("ativo", modelo.acao !== null && modelo.clipes[Number(b.dataset.valor)] === modelo.acao.getClip());
      partes.push(chips);
    }
  } else {
    partes.push(el("p", { class: "dica" }, "Nenhum modelo aberto ainda. Enquanto isso, a cena mostra o modelo feito em código."));
  }
  partes.push(el("h2", {}, "Como gerar a partir da sua imagem"), el("ol", { class: "passos" },
    el("li", {}, "Use só a parte de cima da imagem (sem a tela do Blender), com o bicho inteiro e de lado."),
    el("li", {}, "Se der, use uma versão sem as chamas: aqui elas ficam animadas. Se vierem no modelo, ficam paradas como parte do corpo."),
    el("li", {}, "No Meshy ou no Tripo, faça Imagem → 3D. Se sair torto, gere de novo."),
    el("li", {}, "Faça o rig como quadrúpede (no Tripo, rode antes o Rig Check, que é grátis)."),
    el("li", {}, "Exporte em GLB e arraste para cá, ou salve em interface_web/modelos/mascote.glb.")));
  return partes;
}

// ---------------------------------------------------------------- pose
const graus = (rad) => Math.round(THREE.MathUtils.radToDeg(rad));
let controlesDoOsso = null;
function atualizarControlesDoOsso() {
  if (!controlesDoOsso || !ossoEscolhido) return;
  const r = ossoEscolhido.objeto.rotation;
  for (const eixo of ["x", "y", "z"]) {
    controlesDoOsso[eixo].entrada.value = graus(r[eixo]);
    controlesDoOsso[eixo].valor.textContent = graus(r[eixo]) + "°";
  }
}
// Um osso do modelo de código (pelo nome) ou do modelo do arquivo (pela posição na lista).
function escolherOsso(nome, rotulo) {
  if (modeloAnimado()) {
    if (mascote.modo !== "pose") escolherModo("pose");
    ossoEscolhido = { nome, rotulo, objeto: modelo.vestimenta.ossos[nome], repouso: new THREE.Quaternion() };
  } else if (modeloAtivo()) {
    pararAnimacaoDoModelo();
    const objeto = modelo.ossos[nome];
    ossoEscolhido = { nome, rotulo, objeto, repouso: modelo.repouso.get(objeto) };
  } else {
    if (mascote.modo !== "pose") escolherModo("pose");
    ossoEscolhido = { nome, rotulo, objeto: mascote.ossos[nome], repouso: new THREE.Quaternion() };
  }
  gizmo.attach(ossoEscolhido.objeto);
  verEsqueleto.checked = true;
  atualizarEsqueletos();
  mostrarAba("pose");
}
function gruposDeOssos() {
  if (!modeloAtivo()) return ARTICULACOES;
  if (modeloAnimado()) {
    const existe = modelo.vestimenta.ossos;
    return ARTICULACOES.map((g) => ({ ...g, ossos: g.ossos.filter(([nome]) => existe[nome]) })).filter((g) => g.ossos.length);
  }
  if (!modelo.ossos.length) return [];
  return [{ grupo: "Ossos do arquivo", ossos: modelo.ossos.map((o, i) => [i, o.name || `Osso ${i + 1}`]) }];
}
function abaPose() {
  const grupos = gruposDeOssos();
  if (!grupos.length) {
    return [el("p", { class: "dica" }, "Este modelo veio sem esqueleto, então ainda não dá para posar. Com o arquivo em mãos, eu encaixo os ossos nele.")];
  }
  const partes = [el("p", { class: "dica" },
    "Escolha uma articulação e gire pelos anéis coloridos na cena ou pelos controles abaixo. A animação pausa enquanto você posa.")];
  for (const grupo of grupos) {
    partes.push(el("h2", {}, grupo.grupo));
    const chips = el("div", { class: "grupo-ossos" });
    for (const [nome, rotulo] of grupo.ossos) {
      const b = chip(rotulo, String(nome), () => escolherOsso(nome, `${grupo.grupo}: ${rotulo}`));
      b.classList.toggle("ativo", ossoEscolhido !== null && nome === ossoEscolhido.nome);
      chips.append(b);
    }
    partes.push(chips);
  }
  if (ossoEscolhido) {
    controlesDoOsso = {};
    const caixa = el("div", { class: "osso-escolhido" }, el("strong", {}, ossoEscolhido.rotulo));
    for (const [eixo, rotulo] of [["x", "Frente e trás (X)"], ["y", "Lados (Y)"], ["z", "Inclinar (Z)"]]) {
      const valor = el("span", { class: "valor" }, "0°");
      const entrada = el("input", { type: "range", min: -180, max: 180, step: 1, value: 0, "aria-label": rotulo,
        oninput: (e) => {
          ossoEscolhido.objeto.rotation[eixo] = THREE.MathUtils.degToRad(Number(e.target.value));
          valor.textContent = e.target.value + "°";
        } });
      controlesDoOsso[eixo] = { entrada, valor };
      caixa.append(el("div", { class: "controle" }, el("span", { class: "rotulo" }, rotulo), valor, entrada));
    }
    caixa.append(el("div", { class: "linha-botoes" }, el("button", { type: "button", class: "botao", onclick: () => {
      ossoEscolhido.objeto.quaternion.copy(ossoEscolhido.repouso);
      atualizarControlesDoOsso();
    } }, "Zerar esta articulação")));
    partes.push(caixa);
    queueMicrotask(atualizarControlesDoOsso);
  } else {
    controlesDoOsso = null;
  }
  partes.push(el("div", { class: "linha-botoes" },
    el("button", { type: "button", class: "botao", onclick: () => {
      if (modeloAnimado()) {
        if (mascote.modo !== "pose") escolherModo("pose");
        for (const osso of Object.values(modelo.vestimenta.ossos)) osso.quaternion.identity();
        modelo.vestimenta.ossos.raiz.position.set(0, 0, 0);
      } else if (modeloAtivo()) {
        pararAnimacaoDoModelo();
        for (const [osso, q] of modelo.repouso) osso.quaternion.copy(q);
      } else {
        if (mascote.modo !== "pose") escolherModo("pose");
        mascote.zerarPose();
      }
      atualizarControlesDoOsso();
    } }, "Zerar a pose toda"),
    modeloAtivo() && !modeloAnimado() ? null : el("button", { type: "button", class: "botao principal",
      onclick: () => escolherModo(preferencias.modo === "pose" ? "parado" : preferencias.modo) }, "Voltar a animar")));
  return partes;
}

// ---------------------------------------------------------------- versões
function miniaturaDaCena() {
  const c = document.createElement("canvas");
  c.width = 160;
  c.height = 120;
  const g = c.getContext("2d");
  const l = tela.width, a = tela.height, escala = Math.max(160 / l, 120 / a);
  g.drawImage(tela, (160 - l * escala) / 2, (120 - a * escala) / 2, l * escala, a * escala);
  return c.toDataURL("image/jpeg", 0.8);
}
function abaVersoes() {
  const partes = [el("h2", {}, "Estilos prontos")];
  const prontos = el("div", { class: "cartoes" });
  for (const p of PRESETS) {
    prontos.append(el("div", { class: "cartao" }, el("span", { class: "sem-imagem" }),
      el("div", {}, el("div", { class: "titulo" }, p.nome), el("div", { class: "detalhe" }, p.descricao)),
      el("div", { class: "acoes" }, el("button", { type: "button", class: "botao", onclick: () => {
        substituirAjustes(p.ajustes);
        avisar(`Estilo "${p.nome}" aplicado.`, 2500);
      } }, "Usar"))));
  }
  partes.push(prontos, el("h2", {}, "Suas versões"));
  const nome = el("input", { type: "text", placeholder: "Nome da versão (ex.: olhos maiores)", maxlength: 60, "aria-label": "Nome da versão" });
  partes.push(el("div", { class: "salvar" }, nome, el("button", { type: "button", class: "botao principal", onclick: () => {
    const versoes = ler(CHAVE_VERSOES, []);
    versoes.unshift({ nome: nome.value.trim() || `Versão ${versoes.length + 1}`, quando: new Date().toISOString(), ajustes: { ...ajustes }, imagem: miniaturaDaCena() });
    if (!gravar(CHAVE_VERSOES, versoes.slice(0, 30))) avisar("Não deu para salvar a versão (espaço cheio?).", 4000);
    else avisar("Versão salva.", 2500);
    mostrarAba("versoes");
  } }, "Salvar")));
  const versoes = ler(CHAVE_VERSOES, []);
  const lista = el("div", { class: "cartoes" });
  if (!versoes.length) lista.append(el("p", { class: "dica" }, "Nenhuma versão salva ainda. Salve a atual para poder voltar a ela ou comparar."));
  versoes.forEach((v, i) => {
    const data = new Date(v.quando).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
    lista.append(el("div", { class: "cartao" }, v.imagem ? el("img", { src: v.imagem, alt: "" }) : el("span", { class: "sem-imagem" }),
      el("div", {}, el("div", { class: "titulo" }, v.nome), el("div", { class: "detalhe" }, data)),
      el("div", { class: "acoes" },
        el("button", { type: "button", class: "botao", onclick: () => { substituirAjustes(v.ajustes); avisar(`"${v.nome}" carregada.`, 2500); } }, "Carregar"),
        el("button", { type: "button", class: "botao discreto perigo", onclick: () => {
          const todas = ler(CHAVE_VERSOES, []);
          todas.splice(i, 1);
          gravar(CHAVE_VERSOES, todas);
          mostrarAba("versoes");
        } }, "Apagar"))));
  });
  partes.push(lista, el("h2", {}, "Levar para o projeto"));
  partes.push(el("div", { class: "linha-botoes" },
    el("button", { type: "button", class: "botao", onclick: async () => {
      try {
        await navigator.clipboard.writeText(JSON.stringify(ajustes, null, 2));
        avisar("Ajustes copiados.", 2500);
      } catch {
        avisar("Não deu para copiar; use o arquivo.", 3500);
      }
    } }, "Copiar ajustes"),
    el("button", { type: "button", class: "botao", onclick: () => {
      const blob = new Blob([JSON.stringify(ajustes, null, 2)], { type: "application/json" });
      const a = el("a", { href: URL.createObjectURL(blob), download: "mascote3d_ajustes.json" });
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    } }, "Baixar arquivo")));
  const colar = el("textarea", { placeholder: "{ \"corOlhos\": \"#ffc81a\", ... }", "aria-label": "Ajustes em JSON" });
  partes.push(el("details", {}, el("summary", {}, "Colar ajustes (JSON)"), colar,
    el("div", { class: "linha-botoes" }, el("button", { type: "button", class: "botao", onclick: () => {
      try {
        substituirAjustes(JSON.parse(colar.value));
        avisar("Ajustes aplicados.", 2500);
      } catch {
        avisar("Esse texto não é um JSON válido.", 3500);
      }
    } }, "Aplicar"))));
  return partes;
}

function mostrarAba(id) {
  preferencias.aba = id;
  gravar(CHAVE_PREFERENCIAS, preferencias);
  for (const b of document.getElementById("abas").children) b.setAttribute("aria-selected", String(b.dataset.valor === id));
  const conteudo = document.getElementById("conteudo");
  const abas = { modelo: abaModelo, pose: abaPose, versoes: abaVersoes };
  const partes = abas[id] ? abas[id]() : abaDeControles(id);
  conteudo.replaceChildren(...partes);
}
for (const [id, nome] of ABAS) {
  document.getElementById("abas").append(el("button", { type: "button", class: "aba", role: "tab", "data-valor": id, onclick: () => mostrarAba(id) }, nome));
}
document.getElementById("restaurar").addEventListener("click", () => {
  substituirAjustes({});
  avisar("Padrão restaurado.", 2500);
});

// ------------------------------------------------------------------ laço
function redimensionar() {
  const { width, height } = palco.getBoundingClientRect();
  renderer.setSize(width, height, false);
  composer.setSize(width, height);
  camera.aspect = width / Math.max(1, height);
  camera.updateProjectionMatrix();
}
new ResizeObserver(redimensionar).observe(palco);
redimensionar();

const relogio = new THREE.Clock();
let primeiroQuadro = true;
function quadro() {
  const dt = relogio.getDelta();
  if (ajustesPendentes) {
    ajustesPendentes = false;
    mascote.definirAjustes(ajustes);
    if (modelo.vestimenta) modelo.vestimenta.aplicar(ajustes);
  }
  orbita.update();
  if (modelo.mixer) modelo.mixer.update(dt * mascote.velocidade);
  mascote.alvoOlhar = olharMouse.checked ? alvoMouse : null;
  mascote.atualizar(dt);
  if (modelo.vestimenta) modelo.vestimenta.atualizar(ajustes, dt);
  cena.background = FUNDOS[preferencias.fundo] || FUNDOS.estudio;
  chao.visible = true;
  composer.render();
  if (!miniatura.hidden) {
    const ajudantes = [esqueleto, modelo.ajudante, ajudanteGizmo].filter(Boolean);
    const visiveis = ajudantes.map((a) => a.visible);
    cena.background = fundoMini;
    chao.visible = false;
    for (const a of ajudantes) a.visible = false;
    composerMini.render();
    ajudantes.forEach((a, i) => { a.visible = visiveis[i]; });
  }
  if (primeiroQuadro) {
    primeiroQuadro = false;
    document.getElementById("carregando").hidden = true;
  }
  requestAnimationFrame(quadro);
}

irParaCamera(preferencias.camera);
escolherModo(preferencias.modo);
mostrarAba(preferencias.aba);
window.mascoteLab = {
  mascote, camera, orbita,
  ajustes: () => ({ ...ajustes }),
  definir: (novos) => substituirAjustes({ ...ajustes, ...novos }),
  modo: escolherModo,
  irParaCamera,
  modelo,
  abrirModelo: carregarModelo,
};
requestAnimationFrame(quadro);

// Se o modelo já estiver salvo no projeto, abre sozinho.
fetch(CAMINHO_MODELO, { method: "HEAD" })
  .then((resposta) => { if (resposta.ok) carregarModelo(CAMINHO_MODELO, "mascote.glb"); })
  .catch(() => {});
