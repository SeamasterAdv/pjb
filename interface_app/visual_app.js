/* O visual em rede no app do celular (o mesmo do painel do PC, servido pelo app_celular.py em /visual/).
 *
 * Uma esfera só (o iPhone limita os contextos de WebGL): fica no visor do início e vai para o topo da conversa. Ela
 * segue os estados que o app.js põe no <body> (pensando, falando, ouvindo, offline) e vibra com a resposta que toca
 * aqui (o envelope do áudio, calculado do arquivo, acompanha o tocador). A montanha só existe enquanto você grava:
 * mede o mesmo microfone da gravação (Web Audio, sem sair do celular). Missão concluída: data-vitoria = dourado.
 */
import { EsferaEmRede, MontanhaDePontos, envelopeDoAudio, medidorDeAudio } from "/visual/visual_rede.js";

// Menos pontos que no PC: a bateria e a GPU do celular agradecem, e na tela pequena não faz diferença.
const NO_CELULAR = {
  esfera: { poeira: 3600, nos: 220, distancia: 0.34, tamanhoPoeira: 1.3, tamanhoNos: 2.2 },
  montanha: { pontos: 18000, tamanho: 1.3 },
};
const $ = (id) => document.getElementById(id);

const esfera = new EsferaEmRede($("esfera-inicio"), NO_CELULAR.esfera);
let montanha = null; // criada na primeira gravação (com o quadro já visível, para saber o tamanho)
let fala = null; // {tocador, envelope}
let microfone = null; // medidor do Web Audio

function estadoDoCorpo() {
  const classes = document.body.classList;
  return ["pensando", "falando", "ouvindo"].find((estado) => classes.contains(estado)) || "repouso";
}

function acompanhar() {
  const estado = estadoDoCorpo(), dourado = "vitoria" in document.body.dataset;
  if (dourado && !esfera.dourado) { // a esfera se abre um instante quando a missão termina
    esfera.definirEstado("vitoria");
    setTimeout(() => esfera.definirEstado(estadoDoCorpo()), 1400);
  } else {
    esfera.definirEstado(estado);
  }
  esfera.definirDourado(dourado);
  if (montanha) {
    montanha.definirEstado(estado);
    montanha.definirDourado(dourado);
  }
}
new MutationObserver(acompanhar).observe(document.body, { attributes: true, attributeFilter: ["class", "data-vitoria"] });
acompanhar();

window.visualApp = {
  esfera, // para ajustar pelo console
  // A esfera vai para a tela aberta: pequena na conversa, grande no início.
  tela(nome) { esfera.mover($(nome === "conversa" ? "esfera-conversa" : "esfera-inicio")); },

  // A resposta vai tocar: decodifica o mesmo arquivo para saber o nível a cada instante.
  async falar(tocador, arquivo, contexto) {
    fala = { tocador, envelope: null };
    if (!contexto) return;
    try {
      const atual = fala;
      const buffer = await contexto.decodeAudioData(await arquivo.arrayBuffer());
      atual.envelope = envelopeDoAudio(buffer);
    } catch (erro) { /* sem o envelope, a esfera só fica no estado "falando" */ }
  },

  // Você começou a gravar: a montanha mede o mesmo microfone.
  ouvir(fluxo, contexto) {
    if (!contexto || !fluxo) return;
    if (!montanha) {
      montanha = new MontanhaDePontos($("montanha-gravando"), NO_CELULAR.montanha);
      acompanhar();
    }
    this.pararDeOuvir();
    microfone = medidorDeAudio(contexto, contexto.createMediaStreamSource(fluxo));
  },

  pararDeOuvir() {
    if (microfone) microfone.desligar();
    microfone = null;
  },
};

let ultimoDb = 0;
function quadro(agora) {
  if (fala && fala.envelope && !fala.tocador.paused) {
    const { passo, niveis } = fala.envelope;
    const nivel = niveis[Math.min(niveis.length - 1, Math.floor(fala.tocador.currentTime / passo))] || 0;
    esfera.voz(nivel, [nivel, nivel * 0.65, nivel * 0.3]);
  } else {
    esfera.voz(0);
  }
  if (montanha) {
    const medida = microfone ? microfone.medir() : null;
    montanha.voz(medida ? medida.nivel : 0, medida ? medida.bandas : null);
    if (medida && agora - ultimoDb > 120) {
      ultimoDb = agora;
      $("gravando-db").textContent = medida.nivel > 0 ? medida.db.toFixed(1) : "-∞";
    }
  }
  requestAnimationFrame(quadro);
}
requestAnimationFrame(quadro);
