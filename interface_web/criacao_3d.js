/* O modelo 3D que o Claude descreveu (criacao_visual.py) montado com o Three.js no centro do painel, no lugar da esfera.
 *
 * A cena chega como JSON já conferido pelo Python (formas, medidas, cores e materiais conhecidos): `montarModelo` a
 * transforma em malhas, e o `VisualizadorDeModelo` a mostra num estúdio (luz suave, reflexos e a sombra no chão), girando
 * sozinha; dá para arrastar para girar e usar a roda do mouse para aproximar. `exportarGLB` devolve o modelo em .glb,
 * que o painel grava ao lado do .json (abre no Visualizador 3D do Windows, no Blender, na web).
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFExporter } from "three/addons/exporters/GLTFExporter.js";

const GRAU = Math.PI / 180;
const medida = (parte, nome, padrao) => (Number.isFinite(parte[nome]) && parte[nome] > 0 ? parte[nome] : padrao);

function caixa(parte) {
  const largura = medida(parte, "largura", 1), altura = medida(parte, "altura", 1), profundidade = medida(parte, "profundidade", 1);
  const chanfro = Math.min(parte.chanfro || 0, largura / 2.2, altura / 2.2, profundidade / 2.2);
  if (!(chanfro > 0)) return new THREE.BoxGeometry(largura, altura, profundidade);
  // Quinas arredondadas: o retângulo menor, extrudado com bisel do tamanho do chanfro.
  const w = largura / 2 - chanfro, h = altura / 2 - chanfro;
  const forma = new THREE.Shape([new THREE.Vector2(-w, -h), new THREE.Vector2(w, -h), new THREE.Vector2(w, h), new THREE.Vector2(-w, h)]);
  const geometria = new THREE.ExtrudeGeometry(forma, {
    depth: Math.max(0.0001, profundidade - 2 * chanfro), bevelEnabled: true, bevelSize: chanfro, bevelThickness: chanfro, bevelSegments: 4,
  });
  geometria.center();
  return geometria;
}

function extrusao(parte) {
  const forma = new THREE.Shape(parte.pontos.map(([x, y]) => new THREE.Vector2(x, y)));
  const profundidade = medida(parte, "profundidade", 0.2), chanfro = Math.min(parte.chanfro || 0, profundidade / 2);
  const geometria = new THREE.ExtrudeGeometry(forma, {
    depth: Math.max(0.0001, profundidade - 2 * chanfro), bevelEnabled: chanfro > 0, bevelSize: chanfro, bevelThickness: chanfro,
    bevelSegments: 3, curveSegments: 16,
  });
  geometria.translate(0, 0, -profundidade / 2 + chanfro);
  return geometria;
}

const GEOMETRIAS = {
  caixa,
  extrusao,
  esfera: (p) => new THREE.SphereGeometry(medida(p, "raio", 0.5), 48, 32),
  cilindro: (p) => new THREE.CylinderGeometry(medida(p, "raio_topo", medida(p, "raio", 0.5)), medida(p, "raio_base", medida(p, "raio", 0.5)),
                                              medida(p, "altura", 1), 48),
  cone: (p) => new THREE.ConeGeometry(medida(p, "raio", 0.5), medida(p, "altura", 1), 48),
  capsula: (p) => new THREE.CapsuleGeometry(medida(p, "raio", 0.3), medida(p, "comprimento", 1), 8, 24),
  toro: (p) => new THREE.TorusGeometry(medida(p, "raio", 0.5), medida(p, "tubo", 0.15), 24, 72, medida(p, "arco", 360) * GRAU),
  plano: (p) => new THREE.PlaneGeometry(medida(p, "largura", 1), medida(p, "altura", 1)),
  disco: (p) => new THREE.CircleGeometry(medida(p, "raio", 0.5), 48),
  anel: (p) => new THREE.RingGeometry(medida(p, "raio_interno", 0.3), medida(p, "raio_externo", 0.5), 64),
  tetraedro: (p) => new THREE.TetrahedronGeometry(medida(p, "raio", 0.5)),
  octaedro: (p) => new THREE.OctahedronGeometry(medida(p, "raio", 0.5)),
  dodecaedro: (p) => new THREE.DodecahedronGeometry(medida(p, "raio", 0.5)),
  icosaedro: (p) => new THREE.IcosahedronGeometry(medida(p, "raio", 0.5)),
  torno: (p) => new THREE.LatheGeometry(p.pontos.map(([r, y]) => new THREE.Vector2(Math.max(0, r), y)), 64),
  tubo: (p) => {
    const curva = new THREE.CatmullRomCurve3(p.pontos.map(([x, y, z]) => new THREE.Vector3(x, y, z)));
    return new THREE.TubeGeometry(curva, Math.max(32, p.pontos.length * 12), medida(p, "raio", 0.05), 12, false);
  },
};
const SUPERFICIES_ABERTAS = new Set(["plano", "disco", "anel", "torno"]); // vistas dos dois lados

function material(parte) {
  const cor = new THREE.Color(parte.cor || "#c8c8c8");
  const opacidade = parte.material === "vidro" ? Math.min(parte.opacidade ?? 1, 0.35) : (parte.opacidade ?? 1);
  const comum = { color: cor, transparent: opacidade < 1, opacity: opacidade, side: SUPERFICIES_ABERTAS.has(parte.forma) ? THREE.DoubleSide : THREE.FrontSide };
  switch (parte.material) {
    case "metal": return new THREE.MeshStandardMaterial({ ...comum, metalness: 1, roughness: 0.28 });
    case "brilhante": return new THREE.MeshPhysicalMaterial({ ...comum, roughness: 0.22, clearcoat: 0.7, clearcoatRoughness: 0.15 });
    case "vidro": return new THREE.MeshPhysicalMaterial({ ...comum, roughness: 0.04, metalness: 0, depthWrite: false });
    case "emissivo": return new THREE.MeshStandardMaterial({ ...comum, emissive: cor, emissiveIntensity: 1.6, roughness: 0.5 });
    default: return new THREE.MeshStandardMaterial({ ...comum, roughness: 0.78, metalness: 0 });
  }
}

function posicionar(objeto, parte) {
  objeto.name = parte.nome || parte.forma;
  objeto.position.fromArray(parte.posicao || [0, 0, 0]);
  const [x, y, z] = parte.rotacao || [0, 0, 0];
  objeto.rotation.set(x * GRAU, y * GRAU, z * GRAU);
  objeto.scale.fromArray(parte.escala || [1, 1, 1]);
  if (parte.gira) objeto.userData.gira = parte.gira.map((v) => v * GRAU);
  return objeto;
}

function montarParte(parte) {
  if (parte.forma === "grupo") {
    const grupo = new THREE.Group();
    for (const filha of parte.partes || []) {
      const objeto = montarParte(filha);
      if (objeto) grupo.add(objeto);
    }
    return posicionar(grupo, parte);
  }
  const fazer = GEOMETRIAS[parte.forma];
  if (!fazer) return null;
  let geometria;
  try { geometria = fazer(parte); } catch (erro) { return null; } // um contorno impossível não derruba o resto
  const malha = new THREE.Mesh(geometria, material(parte));
  malha.castShadow = parte.material !== "vidro";
  malha.receiveShadow = true;
  return posicionar(malha, parte);
}

// A cena inteira num grupo, apoiada no chão (o ponto mais baixo em y = 0) e centrada em x e z.
export function montarModelo(dados) {
  const modelo = new THREE.Group();
  modelo.name = dados.titulo || "criacao";
  for (const parte of dados.partes || []) {
    const objeto = montarParte(parte);
    if (objeto) modelo.add(objeto);
  }
  const caixaToda = new THREE.Box3().setFromObject(modelo);
  if (!caixaToda.isEmpty()) {
    const centro = caixaToda.getCenter(new THREE.Vector3());
    for (const filho of modelo.children) filho.position.sub(new THREE.Vector3(centro.x, caixaToda.min.y, centro.z));
  }
  return modelo;
}

// Um estúdio para os reflexos (metal e verniz ficam pretos sem ambiente): caixa cinza com painéis de luz.
function ambienteDeEstudio(renderizador) {
  const estudio = new THREE.Scene();
  const sala = new THREE.Mesh(new THREE.BoxGeometry(10, 10, 10), new THREE.MeshBasicMaterial({ color: 0x3a3a3a, side: THREE.BackSide }));
  estudio.add(sala);
  for (const [x, y, z, w, h, forca] of [[0, 4.9, 0, 6, 6, 3], [-4.9, 1, 2, 3, 5, 1.6], [4.9, 1.5, -1, 2, 4, 1.2], [0, 1, 4.9, 4, 3, 0.8]]) {
    const painel = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: new THREE.Color(forca, forca, forca) }));
    painel.position.set(x, y, z);
    painel.lookAt(0, 0, 0);
    estudio.add(painel);
  }
  const pmrem = new THREE.PMREMGenerator(renderizador);
  const textura = pmrem.fromScene(estudio, 0.04).texture;
  pmrem.dispose();
  estudio.traverse((objeto) => { if (objeto.isMesh) { objeto.geometry.dispose(); objeto.material.dispose(); } });
  return textura;
}

export class VisualizadorDeModelo {
  constructor(recipiente, dados) {
    this.recipiente = recipiente;
    this.dados = dados;
    this.renderizador = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderizador.setClearColor(0x000000, 0);
    this.renderizador.shadowMap.enabled = true;
    this.renderizador.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderizador.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderizador.domElement.style.cssText = "width:100%;height:100%;display:block;cursor:grab";
    recipiente.appendChild(this.renderizador.domElement);

    this.cena = new THREE.Scene();
    this.cena.environment = ambienteDeEstudio(this.renderizador);
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.01, 500);
    this.cena.add(new THREE.HemisphereLight(0xffffff, 0x222222, 0.6));
    this.sol = new THREE.DirectionalLight(0xffffff, 2.2);
    this.sol.castShadow = true;
    this.sol.shadow.mapSize.set(2048, 2048);
    this.sol.shadow.bias = -0.0005;
    this.cena.add(this.sol, this.sol.target);

    this.modelo = montarModelo(dados);
    this.cena.add(this.modelo);
    this.girando = [];
    this.modelo.traverse((objeto) => { if (objeto.userData.gira) this.girando.push(objeto); });

    const tamanho = new THREE.Box3().setFromObject(this.modelo).getSize(new THREE.Vector3());
    this.raio = Math.max(0.5, tamanho.length() / 2);
    this.altura = tamanho.y;
    // O chão só recebe a sombra (o fundo continua o preto do painel).
    this.chao = new THREE.Mesh(new THREE.CircleGeometry(this.raio * 3, 64), new THREE.ShadowMaterial({ opacity: 0.45 }));
    this.chao.rotation.x = -Math.PI / 2;
    this.chao.receiveShadow = true;
    this.cena.add(this.chao);
    const sombra = this.sol.shadow.camera;
    sombra.left = sombra.bottom = -this.raio * 1.6;
    sombra.right = sombra.top = this.raio * 1.6;
    sombra.near = 0.01; sombra.far = this.raio * 8;
    this.sol.position.set(this.raio * 1.4, this.raio * 2.6, this.raio * 1.8);
    this.sol.target.position.set(0, this.altura / 2, 0);

    this.controles = new OrbitControls(this.camera, this.renderizador.domElement);
    this.controles.enableDamping = true;
    this.controles.autoRotate = dados.girar_sozinho !== false;
    this.controles.autoRotateSpeed = 1.4;
    this.controles.enablePan = false;
    this.controles.minDistance = this.raio * 0.6;
    this.controles.maxDistance = this.raio * 8;
    this.controles.target.set(0, this.altura / 2, 0);
    this.controles.addEventListener("start", () => { this.renderizador.domElement.style.cursor = "grabbing"; });
    this.controles.addEventListener("end", () => { this.renderizador.domElement.style.cursor = "grab"; });

    this.observador = new ResizeObserver(() => this.ajustarTamanho());
    this.observador.observe(recipiente);
    this.ajustarTamanho(true);
    this.relogio = performance.now();
    this.animar = this.animar.bind(this);
    this.quadro = requestAnimationFrame(this.animar);
  }

  ajustarTamanho(enquadrar = false) {
    const largura = this.recipiente.clientWidth || 1, altura = this.recipiente.clientHeight || 1;
    this.renderizador.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderizador.setSize(largura, altura, false);
    this.camera.aspect = largura / altura;
    this.camera.updateProjectionMatrix();
    if (enquadrar) {
      // O modelo inteiro no quadro, visto um pouco de cima e de lado.
      const meiaAbertura = (this.camera.fov / 2) * GRAU;
      const distancia = (this.raio / Math.sin(Math.min(meiaAbertura, Math.atan(Math.tan(meiaAbertura) * this.camera.aspect)))) * 1.08;
      const direcao = new THREE.Vector3(0.75, 0.42, 1).normalize();
      this.camera.position.copy(this.controles.target).addScaledVector(direcao, distancia);
      this.camera.near = distancia / 100;
      this.camera.far = distancia * 20;
      this.camera.updateProjectionMatrix();
    }
  }

  animar(agora) {
    const passo = Math.min(0.1, (agora - this.relogio) / 1000);
    this.relogio = agora;
    for (const objeto of this.girando) {
      const [x, y, z] = objeto.userData.gira;
      objeto.rotation.x += x * passo; objeto.rotation.y += y * passo; objeto.rotation.z += z * passo;
    }
    this.controles.update();
    this.renderizador.render(this.cena, this.camera);
    this.quadro = requestAnimationFrame(this.animar);
  }

  // O modelo em .glb (sem o chão de sombra), para gravar ao lado do .json.
  exportarGLB() {
    return new Promise((resolver, recusar) => new GLTFExporter().parse(this.modelo, resolver, recusar, { binary: true }));
  }

  descartar() {
    cancelAnimationFrame(this.quadro);
    this.observador.disconnect();
    this.controles.dispose();
    this.cena.traverse((objeto) => {
      if (objeto.geometry) objeto.geometry.dispose();
      if (objeto.material) objeto.material.dispose();
    });
    if (this.cena.environment) this.cena.environment.dispose();
    this.renderizador.dispose();
    this.renderizador.domElement.remove();
  }
}
