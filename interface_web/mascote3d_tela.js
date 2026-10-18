/* A mascote 3D na janela que anda pela tela (mascote.html).
 *
 * Abre o modelo pintado e com esqueleto (modelos/mascote.glb) e o anima com o modelo de código, que
 * roda escondido só para dar as poses (mascote3d.js). caminhante.js continua cuidando do balão, dos
 * eventos e da pose que o Python manda; aqui cada estado vira uma animação:
 *   andando → andar · dormindo → deitar · ouvindo → sentar · pensando → pensar
 *   falando → falar · vitória → brincar · resto → parado (olhando para quem está na frente).
 *
 * O fundo da janela some pela cor-chave do Windows: o canvas é transparente e o fogo tem um corte de
 * transparência, para não sobrar borrão escuro em volta. Se algo falhar (sem WebGL, sem o modelo), fica a
 * mascote desenhada de antes (mascote.js).
 */
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { Mascote3D, AJUSTES_PADRAO, vestirModelo } from "./mascote3d.js";

const MODELO = "modelos/mascote.glb";
const AJUSTES = "modelos/mascote_ajustes.json"; // os ajustes escolhidos no laboratório (opcional)
const ALTURA_MODELO = 1.7;
// A janela anda a 55 px/s (mascote_tela.py); neste tamanho, o passo do "andar" cobre isso com 1,3×.
const VELOCIDADE_ANDANDO = 1.3;

function modoDoEstado(estado, pose) {
  if (estado === "ouvindo") return "sentar";
  if (estado === "pensando") return "pensar";
  if (estado === "falando") return "falar";
  if (estado === "vitoria") return "brincar";
  if (pose.modo === "dormindo") return "deitar";
  if (pose.modo === "andando") return "andar";
  return "parado";
}

async function lerAjustes() {
  try {
    const resposta = await fetch(AJUSTES, { cache: "no-store" });
    if (resposta.ok) return { ...AJUSTES_PADRAO, ...(await resposta.json()) };
  } catch {
    // sem arquivo de ajustes: fica o padrão
  }
  return { ...AJUSTES_PADRAO };
}

async function montar(caixa) {
  const canvas = document.createElement("canvas");
  canvas.className = "tela-3d";
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, premultipliedAlpha: true });
  // Desenha em resolução maior que a janela (supersampling): bordas e pelo mais lisos nesse tamanho pequeno.
  renderer.setPixelRatio(Math.min(Math.max(2, devicePixelRatio * 1.5), 3));
  renderer.setClearColor(0x000000, 0);
  // O Neutral mantém o roxo e o amarelo saturados (o ACES puxa o roxo para o azul e apaga as cores).
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.toneMappingExposure = 1.15;

  const cena = new THREE.Scene();
  cena.add(new THREE.HemisphereLight(0xcfd0e0, 0x2a2630, 1.1));
  const principal = new THREE.DirectionalLight(0xfff4ea, 2.4);
  principal.position.set(4, 5, 3);
  const contorno = new THREE.DirectionalLight(0xb090ff, 1.8);
  contorno.position.set(-3, 3, -4);
  const preenchimento = new THREE.DirectionalLight(0x9fb7ff, 0.7); // do lado da câmera: rosto menos escuro
  preenchimento.position.set(5, 1.5, 2);
  cena.add(principal, contorno, preenchimento);

  const [ajustes, gltf] = await Promise.all([lerAjustes(), new GLTFLoader().loadAsync(MODELO)]);
  // Sem o pós-processamento de brilho do laboratório, fogo e olhos parecem mais apagados: compensa aqui.
  ajustes.chamas = Math.min(2, ajustes.chamas * 1.6);
  ajustes.brilhoOlhos = Math.min(3, ajustes.brilhoOlhos * 1.35);
  const mascote = new Mascote3D(ajustes); // não entra na cena: só dá as poses
  mascote.comum.uCorte.value = 0.22;
  const suporte = new THREE.Group();
  suporte.add(gltf.scene);
  cena.add(suporte);
  gltf.scene.traverse((o) => { if (o.isMesh) o.frustumCulled = false; });
  const vestimenta = vestirModelo(gltf.scene, mascote);
  vestimenta.aplicar(mascote.aj);
  if (!vestimenta.animado) throw new Error("o modelo não tem o esqueleto da mascote");

  // De pé no chão, com a altura de referência, centrado.
  suporte.updateMatrixWorld(true);
  const caixaModelo = new THREE.Box3().setFromObject(suporte, true);
  const tamanho = caixaModelo.getSize(new THREE.Vector3()), centro = caixaModelo.getCenter(new THREE.Vector3());
  const escala = ALTURA_MODELO / tamanho.y;
  suporte.scale.setScalar(escala);
  suporte.position.set(-centro.x * escala, -caixaModelo.min.y * escala, -centro.z * escala);

  // Câmera de lado, um pouco à frente e acima: a cabeça fica à esquerda (o CSS espelha para andar à direita).
  const camera = new THREE.PerspectiveCamera(24, 1, 0.1, 50);
  const alvo = new THREE.Vector3(0, 0.82, 0);
  camera.position.copy(alvo).add(new THREE.Vector3(5.2, 0.9, 1.6));
  camera.lookAt(alvo);

  function redimensionar() {
    const { width, height } = caixa.getBoundingClientRect();
    renderer.setSize(width, height, false);
    camera.aspect = width / Math.max(1, height);
    camera.updateProjectionMatrix();
  }

  caixa.replaceChildren(canvas);
  caixa.classList.add("em-3d");
  document.body.classList.add("tem-3d");
  redimensionar();
  new ResizeObserver(redimensionar).observe(caixa);

  let modo = "";
  function definir(estado, pose) {
    const novo = modoDoEstado(estado, pose);
    if (novo !== modo) {
      modo = novo;
      mascote.definirModo(novo);
      mascote.velocidade = novo === "andar" ? VELOCIDADE_ANDANDO : 1;
    }
    // Olha para quem está na frente, menos andando (olha o caminho) e dormindo.
    mascote.alvoOlhar = novo === "andar" || novo === "deitar" ? null : camera.position;
  }

  const relogio = new THREE.Clock();
  (function quadro() {
    const dt = relogio.getDelta();
    mascote.atualizar(dt);
    vestimenta.atualizar(mascote.aj, dt);
    renderer.render(cena, camera);
    requestAnimationFrame(quadro);
  })();
  return { definir, mascote, vestimenta, camera };
}

const caixa = document.getElementById("mascote");
montar(caixa)
  .then((mascote3d) => {
    window.mascote3d = mascote3d;
    // Estado e pose atuais (variáveis de caminhante.js), para começar já certo.
    mascote3d.definir(typeof estado === "string" ? estado : "repouso", typeof pose === "object" ? pose : { modo: "parada" });
  })
  .catch((erro) => console.warn("Mascote 3D indisponível; fica a desenhada:", erro));
